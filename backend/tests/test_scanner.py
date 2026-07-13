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


def test_scanimage_timeout_maps(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="scanimage", timeout=120)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ScannerTimeout):
        ScanimageBackend().scan(dpi=300, mode="Color")
