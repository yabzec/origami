import io
import subprocess
import threading
from itertools import cycle
from typing import Protocol

from PIL import Image, ImageDraw

SCAN_TIMEOUT_SECONDS = 120
PROBE_TIMEOUT_SECONDS = 10


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
    def scan(self, dpi: int, mode: str) -> bytes: ...
    def available(self) -> bool: ...


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

    def scan(self, dpi: int, mode: str) -> bytes:
        try:
            result = subprocess.run(
                ["scanimage", "--format=png", f"--resolution={dpi}", f"--mode={mode}"],
                capture_output=True,
                timeout=SCAN_TIMEOUT_SECONDS,
            )
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


class FakeScannerBackend:
    """Test double: renders labelled PNGs or raises a scripted error."""

    def __init__(self, pages: list[str] | None = None, error: ScannerError | None = None):
        self._labels = cycle(pages or ["SCAN"])
        self._error = error

    def scan(self, dpi: int, mode: str) -> bytes:
        if self._error is not None:
            raise self._error
        img = Image.new("RGB", (600, 200), "white")
        ImageDraw.Draw(img).text((20, 80), next(self._labels), fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def available(self) -> bool:
        return self._error is None or not isinstance(self._error, ScannerOffline)


_scan_lock = threading.Lock()


def scan_locked(backend: ScannerBackend, dpi: int = 300, mode: str = "Color") -> bytes:
    if not _scan_lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.scan(dpi=dpi, mode=mode)
    finally:
        _scan_lock.release()


_default_backend: ScannerBackend = ScanimageBackend()


def get_scanner() -> ScannerBackend:
    return _default_backend
