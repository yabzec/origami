"""eSCL (AirScan) protocol helpers and the remote scanner backend.

eSCL is HTTP + XML. Element names are matched with the `{*}` namespace
wildcard because vendors disagree on prefixes.
"""

import io
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from app.services.agent_hub import AgentHub, AgentOffline, AgentTimeout, EsclResponse
from app.services.scanner import (
    PREVIEW_MODE,
    PREVIEW_RESOLUTION,
    SCAN_TIMEOUT_SECONDS,
    CoverOpen,
    ScannerBusy,
    ScannerError,
    ScannerJam,
    ScannerOffline,
    ScannerTimeout,
)


@dataclass(frozen=True)
class Capabilities:
    max_width: int
    max_height: int
    resolutions: list[int]
    color_modes: list[str]
    formats: list[str]


def _texts(root: ET.Element, tag: str) -> list[str]:
    return [(el.text or "").strip() for el in root.iterfind(f".//{{*}}{tag}") if (el.text or "").strip()]


def parse_capabilities(xml: bytes) -> Capabilities:
    root = ET.fromstring(xml)
    platen = root.find(".//{*}PlatenInputCaps")
    if platen is None:
        platen = root
    resolutions = sorted({int(x) for x in _texts(platen, "XResolution")})
    formats = list(dict.fromkeys(_texts(platen, "DocumentFormatExt") + _texts(platen, "DocumentFormat")))
    return Capabilities(
        max_width=int((_texts(platen, "MaxWidth") or ["2550"])[0]),
        max_height=int((_texts(platen, "MaxHeight") or ["3508"])[0]),
        resolutions=resolutions or [300],
        color_modes=list(dict.fromkeys(_texts(platen, "ColorMode"))),
        formats=formats,
    )


def pick_resolution(caps: Capabilities, dpi: int) -> int:
    # nearest supported value; on a tie the higher one (more detail for OCR)
    return min(caps.resolutions, key=lambda r: (abs(r - dpi), -r))


def pick_format(caps: Capabilities) -> str:
    for fmt in ("image/png", "image/jpeg"):
        if fmt in caps.formats:
            return fmt
    return caps.formats[0] if caps.formats else "image/jpeg"


def color_mode(mode: str) -> str:
    return "RGB24" if mode == "Color" else "Grayscale8"


def scan_settings_xml(caps: Capabilities, dpi: int, mode: str) -> str:
    res = pick_resolution(caps, dpi)
    fmt = pick_format(caps)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>{caps.max_width}</pwg:Width>
      <pwg:Height>{caps.max_height}</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>{color_mode(mode)}</scan:ColorMode>
  <scan:XResolution>{res}</scan:XResolution>
  <scan:YResolution>{res}</scan:YResolution>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>
  <scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>
</scan:ScanSettings>"""


def status_error(xml: bytes) -> ScannerError:
    """Best exception for a failed job, from a ScannerStatus document."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ScannerError("Scanner reported an error")
    words = " ".join(_texts(root, "State") + _texts(root, "StateReason") + _texts(root, "AdfState")).lower()
    if "jam" in words:
        return ScannerJam()
    if "coveropen" in words or "cover open" in words:
        return CoverOpen()
    if "processing" in words:
        return ScannerBusy()
    return ScannerError(f"Scanner reported: {words or 'unknown state'}")


def to_png(data: bytes) -> bytes:
    if data.startswith(b"\x89PNG"):
        return data
    with Image.open(io.BytesIO(data)) as img:
        if img.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()


AGENT_PREFIX = "agent:"
NEXT_DOCUMENT_RETRY_SECONDS = 1.0


def parse_agent_device(device: str) -> tuple[str, str]:
    parts = device.split(":", 2)
    if len(parts) != 3 or parts[0] + ":" != AGENT_PREFIX or not parts[1] or not parts[2]:
        raise ScannerOffline(f"Unknown scanner {device}")
    return parts[1], parts[2]


class EsclRemoteBackend:
    """Drives eSCL scanners on a client's network through that client's agent."""

    def __init__(self, hub: AgentHub, user_id: int, sleep=time.sleep):
        self._hub = hub
        self._user_id = user_id
        self._sleep = sleep
        self._caps: dict[str, Capabilities] = {}

    def devices(self, client_id: str) -> list[dict]:
        return [
            {"id": f"{AGENT_PREFIX}{client_id}:{s['uuid']}", "name": s["name"]}
            for s in self._hub.scanners(self._user_id, client_id)
        ]

    def available(self, device: str | None = None) -> bool:
        if not device:
            return False
        client_id, uuid = parse_agent_device(device)
        return any(s["uuid"] == uuid for s in self._hub.scanners(self._user_id, client_id))

    def list_devices(self) -> list[dict]:
        return []  # needs a client_id; CompositeBackend calls devices() instead

    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        return self._run_job(device or "", dpi, mode)

    def preview(self, device: str | None = None) -> bytes:
        return self._run_job(device or "", PREVIEW_RESOLUTION, PREVIEW_MODE)

    def _call(self, client_id: str, uuid: str, method: str, path: str, body: str | None = None,
              timeout: float = SCAN_TIMEOUT_SECONDS) -> EsclResponse:
        try:
            return self._hub.request(self._user_id, client_id, uuid, method, path, body, timeout)
        except AgentOffline:
            raise ScannerOffline("The scanner agent on this computer is not connected")
        except AgentTimeout:
            raise ScannerTimeout()

    def _capabilities(self, device: str, client_id: str, uuid: str) -> Capabilities:
        if device not in self._caps:
            res = self._call(client_id, uuid, "GET", "ScannerCapabilities")
            if res.status != 200:
                raise ScannerError(f"ScannerCapabilities returned {res.status}")
            try:
                self._caps[device] = parse_capabilities(res.body)
            except (ET.ParseError, ValueError):
                raise ScannerError("Scanner returned invalid capabilities")
        return self._caps[device]

    def _run_job(self, device: str, dpi: int, mode: str) -> bytes:
        client_id, uuid = parse_agent_device(device)
        if not self._hub.connected(self._user_id, client_id):
            raise ScannerOffline("The scanner agent on this computer is not connected")
        caps = self._capabilities(device, client_id, uuid)
        job = self._call(client_id, uuid, "POST", "ScanJobs", scan_settings_xml(caps, dpi, mode))
        if job.status == 503:
            raise ScannerBusy()
        location = job.headers.get("Location") or job.headers.get("location")
        if job.status not in (200, 201) or not location:
            raise ScannerError(f"ScanJobs returned {job.status}")
        deadline = time.monotonic() + SCAN_TIMEOUT_SECONDS
        while True:
            doc = self._call(client_id, uuid, "GET", f"{location.strip('/')}/NextDocument")
            if doc.status == 200 and doc.body:
                try:
                    return to_png(doc.body)
                except (UnidentifiedImageError, OSError):
                    raise ScannerError("Scanner returned an unreadable image")
            if doc.status != 503 or time.monotonic() >= deadline:
                break
            self._sleep(NEXT_DOCUMENT_RETRY_SECONDS)
        if doc.status == 503:
            raise ScannerTimeout()
        status = self._call(client_id, uuid, "GET", "ScannerStatus")
        raise status_error(status.body)
