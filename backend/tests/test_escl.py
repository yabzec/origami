import io

from PIL import Image

from app.services.escl import (
    color_mode,
    parse_capabilities,
    pick_format,
    pick_resolution,
    scan_settings_xml,
    status_error,
    to_png,
)
from app.services.scanner import CoverOpen, ScannerBusy, ScannerError, ScannerJam

CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.63</pwg:Version>
  <scan:Platen>
    <scan:PlatenInputCaps>
      <scan:MinWidth>16</scan:MinWidth>
      <scan:MaxWidth>2550</scan:MaxWidth>
      <scan:MinHeight>16</scan:MinHeight>
      <scan:MaxHeight>3508</scan:MaxHeight>
      <scan:SettingProfiles>
        <scan:SettingProfile>
          <scan:ColorModes>
            <scan:ColorMode>RGB24</scan:ColorMode>
            <scan:ColorMode>Grayscale8</scan:ColorMode>
          </scan:ColorModes>
          <scan:DocumentFormats>
            <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
            <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
            <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
          </scan:DocumentFormats>
          <scan:SupportedResolutions>
            <scan:DiscreteResolutions>
              <scan:DiscreteResolution><scan:XResolution>100</scan:XResolution><scan:YResolution>100</scan:YResolution></scan:DiscreteResolution>
              <scan:DiscreteResolution><scan:XResolution>200</scan:XResolution><scan:YResolution>200</scan:YResolution></scan:DiscreteResolution>
              <scan:DiscreteResolution><scan:XResolution>600</scan:XResolution><scan:YResolution>600</scan:YResolution></scan:DiscreteResolution>
            </scan:DiscreteResolutions>
          </scan:SupportedResolutions>
        </scan:SettingProfile>
      </scan:SettingProfiles>
    </scan:PlatenInputCaps>
  </scan:Platen>
</scan:ScannerCapabilities>"""


def status(state: str, extra: str = "") -> bytes:
    return f"""<?xml version="1.0"?>
<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
    xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:State>{state}</pwg:State>{extra}
</scan:ScannerStatus>""".encode()


def test_parse_capabilities():
    caps = parse_capabilities(CAPS)
    assert (caps.max_width, caps.max_height) == (2550, 3508)
    assert caps.resolutions == [100, 200, 600]
    assert caps.color_modes == ["RGB24", "Grayscale8"]
    assert "image/jpeg" in caps.formats


def test_pick_resolution_nearest_and_tie_goes_up():
    caps = parse_capabilities(CAPS)
    assert pick_resolution(caps, 300) == 200  # |300-200|=100 < |300-600|=300
    assert pick_resolution(caps, 75) == 100
    assert pick_resolution(caps, 400) == 600  # tie 200/600 → higher


def test_pick_format_prefers_png_then_jpeg():
    caps = parse_capabilities(CAPS)
    assert pick_format(caps) == "image/jpeg"
    png_caps = parse_capabilities(CAPS.replace(b"application/pdf", b"image/png"))
    assert pick_format(png_caps) == "image/png"


def test_color_mode():
    assert color_mode("Color") == "RGB24"
    assert color_mode("Gray") == "Grayscale8"


def test_scan_settings_xml_contains_choices():
    xml = scan_settings_xml(parse_capabilities(CAPS), dpi=300, mode="Gray")
    assert "<scan:XResolution>200</scan:XResolution>" in xml
    assert "<scan:ColorMode>Grayscale8</scan:ColorMode>" in xml
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in xml
    assert "<pwg:Width>2550</pwg:Width>" in xml
    assert "<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>" in xml


def test_status_error_mapping():
    assert isinstance(status_error(status("Processing")), ScannerBusy)
    assert isinstance(status_error(status("Stopped", "<scan:AdfState>ScannerAdfJam</scan:AdfState>")), ScannerJam)
    assert isinstance(status_error(status("Stopped", "<pwg:StateReasons><pwg:StateReason>CoverOpen</pwg:StateReason></pwg:StateReasons>")), CoverOpen)
    err = status_error(status("Idle"))
    assert type(err) is ScannerError
    assert type(status_error(b"not xml")) is ScannerError


def test_to_png_converts_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(buf, "JPEG")
    out = to_png(buf.getvalue())
    assert out.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(out)).size == (20, 10)


def test_to_png_keeps_png():
    buf = io.BytesIO()
    Image.new("L", (5, 5)).save(buf, "PNG")
    assert to_png(buf.getvalue()) == buf.getvalue()
