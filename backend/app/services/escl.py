"""eSCL (AirScan) protocol helpers and the remote scanner backend.

eSCL is HTTP + XML. Element names are matched with the `{*}` namespace
wildcard because vendors disagree on prefixes.
"""

import io
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from PIL import Image

from app.services.scanner import CoverOpen, ScannerBusy, ScannerError, ScannerJam


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
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
