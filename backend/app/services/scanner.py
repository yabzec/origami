import io
import re
import subprocess
import threading
from itertools import cycle
from typing import Protocol

from PIL import Image, ImageDraw

SCAN_TIMEOUT_SECONDS = 120
PROBE_TIMEOUT_SECONDS = 10
PREVIEW_TIMEOUT_SECONDS = 30
PREVIEW_RESOLUTION = 75
PREVIEW_MODE = "Gray"

_DEVICE_RE = re.compile(r"^device\s+[`'\"](?P<id>[^`'\"]+)['\"]?\s+is a\s+(?P<name>.+?)\s*$")


def parse_scanimage_devices(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        match = _DEVICE_RE.match(line.strip())
        if match:
            devices.append({"id": match.group("id"), "name": match.group("name")})
    return devices


class ScannerError(Exception):
    code = "scanner_error"
    http_status = 500

    def __init__(self, message: str = ""):
        self.message = message or self.__class__.__doc__ or self.code
        super().__init__(self.message)


class ScannerOffline(ScannerError):
    """Scanner not found - check power and USB connection."""
    code = "scanner_offline"
    http_status = 503


class ScannerBusy(ScannerError):
    """Scanner is busy with another operation."""
    code = "scanner_busy"
    http_status = 409


class ScannerJam(ScannerError):
    """Paper jam detected."""
    code = "scanner_jam"
    http_status = 422


class CoverOpen(ScannerError):
    """Scanner cover is open."""
    code = "cover_open"
    http_status = 422


class ScannerTimeout(ScannerError):
    """Scan timed out - try power-cycling the scanner."""
    code = "scanner_timeout"
    http_status = 504


class ScannerBackend(Protocol):
    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes: ...
    def preview(self, device: str | None = None) -> bytes: ...
    def available(self, device: str | None = None) -> bool: ...
    def list_devices(self) -> list[dict]: ...


class ScanimageBackend:
    """Drives a SANE scanner via the scanimage CLI (sanctioned python-sane swap)."""

    @staticmethod
    def _map_error(stderr: str, returncode: int) -> ScannerError:
        lowered = stderr.lower()
        if "no sane devices" in lowered or "no such device" in lowered:
            return ScannerOffline(stderr.strip())
        if "device busy" in lowered:
            return ScannerBusy(stderr.strip())
        if "jam" in lowered:
            return ScannerJam(stderr.strip())
        if "cover open" in lowered:
            return CoverOpen(stderr.strip())
        return ScannerError(stderr.strip() or f"scanimage exited {returncode}")

    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        argv = ["scanimage", "--format=png", f"--resolution={dpi}", f"--mode={mode}"]
        if device:
            argv += ["-d", device]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=SCAN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ScannerTimeout()
        if result.returncode != 0:
            raise self._map_error(result.stderr.decode(errors="replace"), result.returncode)
        return result.stdout

    def preview(self, device: str | None = None) -> bytes:
        argv = [
            "scanimage",
            "--format=png",
            f"--resolution={PREVIEW_RESOLUTION}",
            f"--mode={PREVIEW_MODE}",
        ]
        if device:
            argv += ["-d", device]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=PREVIEW_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ScannerTimeout()
        if result.returncode != 0:
            raise self._map_error(result.stderr.decode(errors="replace"), result.returncode)
        return result.stdout

    def available(self) -> bool:
        try:
            result = subprocess.run(
                ["scanimage", "-L"], capture_output=True, timeout=PROBE_TIMEOUT_SECONDS
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        return result.returncode == 0 and b"device" in result.stdout.lower()

    def list_devices(self) -> list[dict]:
        try:
            result = subprocess.run(
                ["scanimage", "-L"], capture_output=True, timeout=PROBE_TIMEOUT_SECONDS
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        return parse_scanimage_devices(result.stdout.decode(errors="replace"))


class FakeScannerBackend:
    """Test double: renders labelled PNGs or raises a scripted error."""

    def __init__(
        self,
        pages: list[str] | None = None,
        error: ScannerError | None = None,
        devices: list[dict] | None = None,
    ):
        self._labels = cycle(pages or ["SCAN"])
        self._error = error
        self._devices = devices if devices is not None else [{"id": "fake:0", "name": "Fake Scanner"}]
        self.last_device: str | None = None

    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        self.last_device = device
        if self._error is not None:
            raise self._error
        img = Image.new("RGB", (600, 200), "white")
        ImageDraw.Draw(img).text((20, 80), next(self._labels), fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def preview(self, device: str | None = None) -> bytes:
        return self.scan(dpi=PREVIEW_RESOLUTION, mode=PREVIEW_MODE, device=device)

    def available(self) -> bool:
        return self._error is None or not isinstance(self._error, ScannerOffline)

    def list_devices(self) -> list[dict]:
        return list(self._devices)


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def device_lock(device: str | None) -> threading.Lock:
    """One lock per device id; None and "" both mean the server's default scanner."""
    key = device or ""
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def device_busy(device: str | None) -> bool:
    return device_lock(device).locked()


def scan_locked(
    backend: ScannerBackend, dpi: int = 300, mode: str = "Color", device: str | None = None
) -> bytes:
    lock = device_lock(device)
    if not lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.scan(dpi=dpi, mode=mode, device=device)
    finally:
        lock.release()


def preview_locked(backend: ScannerBackend, device: str | None = None) -> bytes:
    lock = device_lock(device)
    if not lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.preview(device=device)
    finally:
        lock.release()


_default_backend: ScannerBackend = ScanimageBackend()


def get_scanner() -> ScannerBackend:
    return _default_backend


REMOTE_PREFIX = "agent:"


class CompositeBackend:
    """Server scanners plus the scanners of the requesting browser's agent."""

    def __init__(self, local: ScannerBackend, remote, client_id: str | None):
        self._local = local
        self._remote = remote
        self._client_id = client_id

    def _pick(self, device: str | None):
        return self._remote if device and device.startswith(REMOTE_PREFIX) else self._local

    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        return self._pick(device).scan(dpi=dpi, mode=mode, device=device)

    def preview(self, device: str | None = None) -> bytes:
        return self._pick(device).preview(device=device)

    def available(self, device: str | None = None) -> bool:
        if device and device.startswith(REMOTE_PREFIX):
            try:
                return self._remote.available(device)
            except ScannerError:
                return False
        return self._local.available()

    def list_devices(self) -> list[dict]:
        remote = self._remote.devices(self._client_id) if self._client_id else []
        return self._local.list_devices() + remote
