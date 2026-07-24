import threading

import pytest

from app.services import scanner
from app.services.scanner import (
    FakeScannerBackend,
    ScannerBusy,
    ScannerJam,
    ScannerOffline,
    ScannerTimeout,
    ScanimageBackend,
    scan_locked,
)


def test_fake_backend_returns_png():
    backend = FakeScannerBackend(pages=["PAGINA UNO"])
    data = backend.scan(dpi=300, mode="Color")
    assert data.startswith(b"\x89PNG")


def test_fake_backend_scripted_error():
    backend = FakeScannerBackend(error=ScannerJam())
    with pytest.raises(ScannerJam):
        backend.scan(dpi=300, mode="Color")


def test_scan_locked_rejects_concurrent_use():
    backend = FakeScannerBackend()
    acquired = scanner._scan_lock.acquire()
    assert acquired
    try:
        with pytest.raises(ScannerBusy):
            scan_locked(backend)
    finally:
        scanner._scan_lock.release()
    # once released, scanning works again
    assert scan_locked(backend).startswith(b"\x89PNG")


def test_scanimage_stderr_mapping():
    m = ScanimageBackend._map_error
    assert isinstance(m("no SANE devices found", 1), ScannerOffline)
    assert isinstance(m("sane_start: Device busy", 1), ScannerBusy)
    assert isinstance(m("sane_start: Document feeder jammed", 1), ScannerJam)
    assert isinstance(m("sane_start: Cover open", 1), type(m("cover open", 1)))
    assert m("anything else", 1).code == "scanner_error"
    # Narrow offline detection: "no such device" maps to offline
    assert isinstance(m("open of device foo failed: no such device", 1), ScannerOffline)
    # But "invalid argument" should NOT map to offline (it's a bad parameter, not device missing)
    assert not isinstance(m("scanimage: sane_start: Invalid argument (bad --resolution)", 1), ScannerOffline)
    assert m("scanimage: sane_start: Invalid argument (bad --resolution)", 1).code == "scanner_error"


def test_scanimage_timeout_maps(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="scanimage", timeout=120)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ScannerTimeout):
        ScanimageBackend().scan(dpi=300, mode="Color")


def test_parse_scanimage_devices():
    from app.services.scanner import parse_scanimage_devices

    out = (
        "device `net:192.168.1.9:pixma:MG5700' is a CANON MG5700 flatbed scanner\n"
        "device 'epson2:libusb:001:004' is a Epson Perfection V39 flatbed scanner\n"
        "garbage line without the pattern\n"
    )
    devices = parse_scanimage_devices(out)
    assert devices == [
        {"id": "net:192.168.1.9:pixma:MG5700", "name": "CANON MG5700 flatbed scanner"},
        {"id": "epson2:libusb:001:004", "name": "Epson Perfection V39 flatbed scanner"},
    ]


def test_parse_scanimage_devices_empty():
    from app.services.scanner import parse_scanimage_devices

    assert parse_scanimage_devices("No scanners were identified.\n") == []


def test_fake_backend_list_devices():
    from app.services.scanner import FakeScannerBackend

    backend = FakeScannerBackend(devices=[{"id": "fake:0", "name": "Fake Scanner"}])
    assert backend.list_devices() == [{"id": "fake:0", "name": "Fake Scanner"}]


def test_scanimage_scan_includes_device(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().scan(dpi=300, mode="Color", device="epson2:libusb:001:004")
    assert "-d" in captured["argv"]
    assert "epson2:libusb:001:004" in captured["argv"]


def test_scanimage_scan_omits_device_when_none(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().scan(dpi=300, mode="Color")
    assert "-d" not in captured["argv"]


def test_scanimage_preview_uses_fast_params(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["timeout"] = kwargs.get("timeout")

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().preview()
    assert "--resolution=75" in captured["argv"]
    assert "--mode=Gray" in captured["argv"]
    assert captured["timeout"] == 30
