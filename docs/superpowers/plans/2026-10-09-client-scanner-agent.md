# Client Scanner Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scan from a Wi-Fi eSCL scanner on the client's network into the existing Origami scan flow, through a small agent the browser launches on demand and that connects out to the server over the existing Cloudflare tunnel.

**Architecture:** A Go agent (`agent/`) finds eSCL scanners with mDNS and opens an outbound WebSocket to `/api/agent/ws`. The backend keeps connected agents in an in-memory `AgentHub`, and a new `EsclRemoteBackend` speaks eSCL through the hub. A `CompositeBackend` routes `agent:` device ids to the remote backend and everything else to the existing `ScanimageBackend`, so scan sessions, storage and compile are unchanged. The frontend replaces the native scanner `<select>` with a listbox that has a "Search local scanners" item, launches the agent through the `origami-agent://` URL scheme and polls the device list while the popover stays open.

**Tech Stack:** FastAPI + Starlette WebSockets, SQLModel, Pillow, pytest; Go 1.22+ with `github.com/coder/websocket`, `github.com/libp2p/zeroconf/v2`, `golang.org/x/sys`; React 19 + TanStack Query + vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-09-client-scanner-agent-design.md`

## Global Constraints

- Server scanning (`ScanimageBackend`, `FakeScannerBackend` test override through `get_scanner`) keeps working unchanged.
- Remote device id format: `agent:<client_id>:<scanner_uuid>`. `client_id` matches `^[A-Za-z0-9-]{8,64}$`.
- Launch token: random, single use, TTL 120 seconds.
- Agent idle exit: 30 minutes without an `escl` request. Agent gives up reconnecting after 60 seconds.
- Agent and server send WebSocket pings; agent pings every 30 seconds.
- Binary frame = 16-byte ASCII request id + at most 256 KB (262144 bytes) of data.
- Scan timeout: `SCAN_TIMEOUT_SECONDS` (120 s). Preview: 75 dpi, `Gray`.
- Stored scan pages are PNG (`page_NNN.png`), whatever format the scanner sends.
- The agent only executes eSCL requests for scanners it discovered, on paths under that scanner's eSCL root. The server never sees scanner IPs or URLs.
- Device search in the UI: poll `/api/scan/devices` every 1 s for at most 15 s; "Agent not responding" after 10 s without an agent.
- `localStorage` keys: `origami.clientId`, `origami.agentInstalled`. Every access in `try/catch`.
- No admin rights needed on the client; URL handler registered for the current user only.
- Persisted text (code comments, commits, README) in plain English.
- No server address is compiled into the agent. The server URL comes only from the `PUBLIC_URL` env var (fallback: request base URL) and reaches the agent through the launch link. Examples use `https://origami.example.com`.

### Deviations from the spec (decided while planning, tell the user at handoff)

1. **eSCL paths are relative to the scanner's eSCL root** (`ScanJobs`, `ScanJobs/12/NextDocument`). The agent joins them with the root it discovered. The server never needs to know the root.
2. **Resume token:** the launch token is single use, so after connecting the server sends `{"type": "welcome", "resume_token": ...}` (same TTL, rotated on every connect). The agent reconnects with it after a network drop.
3. **Server pinning (trust on first use):** the agent stores the first server URL it connects to in its user config dir and refuses launch URLs for any other server. `origami-agent --reset` clears it. This stops another website from pointing the agent at a foreign server.
4. **Single instance** uses a localhost TCP port (`127.0.0.1:47811`) for both the lock and the hand-off, instead of a lock file plus a separate socket.
5. **Launch from the browser** uses `window.location.href = url`, not a hidden iframe: Chrome blocks external-protocol launches from iframes without user activation. The page does not navigate for external schemes. The launch URL is prefetched when the popover opens, so the click handler can navigate synchronously.
6. **Another user's device id** is answered as `ScannerOffline` (503), not 404: the hub is keyed by `(user_id, client_id)`, so the device is simply not found for that user.
7. **`/api/scan/devices`** also returns `agent_connected` (boolean) when `client_id` is given, so the UI can tell "no agent" from "agent, no scanners".

## Review Focus

1. **Agent disconnects mid-scan:** the pending request fails at once with `ScannerOffline`, not after 120 s. Test in Task 3.
2. **Same browser launches the agent again while one is connected:** the old socket is closed, its pending requests fail, the new one serves. Test in Task 3.
3. **Scanner answers `503` on `NextDocument` while warming up:** the backend retries until the document arrives or the timeout ends. Test in Task 5.
4. **Scanner returns an absolute `Location` (`http://192.168.1.20/eSCL/ScanJobs/7`):** the agent rewrites it to `ScanJobs/7`. Test in Task 6.
5. **`localStorage` throws (private mode, blocked storage):** client id still works for the page lifetime, search shows the install panel, nothing crashes. Test in Task 10.

---

## File Structure

```
backend/app/services/scanner.py        # MODIFY: per-device locks, CompositeBackend
backend/app/services/escl.py           # CREATE: eSCL XML helpers, image->PNG, EsclRemoteBackend
backend/app/services/agent_hub.py      # CREATE: tokens, connected agents, request/response bridge
backend/app/api/agent.py               # CREATE: /api/agent/launch, /api/agent/ws, /api/agent/download/{platform}
backend/app/api/scan.py                # MODIFY: get_scan_backend dependency, client_id/device params
backend/app/config.py                  # MODIFY: public_url, agent_dist_dir
backend/app/main.py                    # MODIFY: include agent router
backend/tests/test_scanner.py          # MODIFY
backend/tests/test_scan_api.py         # MODIFY
backend/tests/test_escl.py             # CREATE
backend/tests/test_agent_hub.py        # CREATE
backend/tests/test_agent_api.py        # CREATE
backend/tests/test_remote_scan.py      # CREATE

agent/go.mod
agent/protocol.go        agent/protocol_test.go      # message types, framing
agent/escl.go            agent/escl_test.go          # restricted eSCL executor
agent/discovery.go       agent/discovery_test.go     # mDNS browse, record -> Scanner
agent/conn.go            agent/conn_test.go          # WebSocket session, idle exit, reconnect
agent/launch.go          agent/launch_test.go        # URL parsing, server pinning, single instance
agent/main.go                                        # entry (non-darwin)
agent/main_darwin.go     agent/url_darwin.m          # Apple Event URL handling (cgo)
agent/register_linux.go  agent/register_windows.go  agent/register_darwin.go
agent/register_test.go
agent/macos/Info.plist
agent/build.sh

frontend/src/lib/localScan.ts          frontend/src/lib/localScan.test.ts
frontend/src/lib/agentLaunch.ts
frontend/src/hooks/useLocalScanSearch.ts
frontend/src/components/scan/DeviceListbox.tsx      frontend/src/components/scan/DeviceListbox.test.tsx
frontend/src/components/scan/AgentInstallPanel.tsx
frontend/src/components/scan/ScanToolbar.tsx         # MODIFY
frontend/src/pages/ScanPage.tsx                      # MODIFY
frontend/src/lib/types.ts                            # MODIFY
frontend/src/lib/scanDevices.ts, scanDevices.test.ts # DELETE (replaced by localScan.groupDevices)

README.md, .gitignore                                # MODIFY
```

---

### Task 1: Per-device scan locks

**Files:**
- Modify: `backend/app/services/scanner.py` (the `_scan_lock`, `scan_locked`, `preview_locked` block near the end)
- Modify: `backend/app/api/scan.py:73-78` (`scan_status`)
- Test: `backend/tests/test_scanner.py`, `backend/tests/test_scan_api.py`

**Interfaces:**
- Produces: `device_lock(device: str | None) -> threading.Lock`, `device_busy(device: str | None) -> bool`, `scan_locked(backend, dpi=300, mode="Color", device=None) -> bytes`, `preview_locked(backend, device=None) -> bytes` (signatures unchanged). `_scan_lock` is removed.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_scanner.py`, replace `test_scan_locked_rejects_concurrent_use` with:

```python
def test_scan_locked_rejects_concurrent_use_of_same_device():
    backend = FakeScannerBackend()
    lock = scanner.device_lock("fake:0")
    assert lock.acquire()
    try:
        with pytest.raises(ScannerBusy):
            scan_locked(backend, device="fake:0")
    finally:
        lock.release()
    assert scan_locked(backend, device="fake:0").startswith(b"\x89PNG")


def test_scan_locked_allows_other_device_while_one_is_busy():
    backend = FakeScannerBackend()
    lock = scanner.device_lock("fake:0")
    assert lock.acquire()
    try:
        assert scan_locked(backend, device="agent:abcdefgh:uuid-1").startswith(b"\x89PNG")
        assert scanner.device_busy("fake:0")
        assert not scanner.device_busy("agent:abcdefgh:uuid-1")
    finally:
        lock.release()


def test_default_device_shares_one_lock():
    assert scanner.device_lock(None) is scanner.device_lock("")
```

In `backend/tests/test_scan_api.py`, replace `test_preview_busy_returns_409` with:

```python
def test_preview_busy_returns_409(auth_client, fake_scanner, storage):
    from app.services import scanner as scanner_module

    lock = scanner_module.device_lock(None)
    lock.acquire()
    try:
        resp = auth_client.post("/api/scan/preview", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "scanner_busy"
        assert auth_client.get("/api/scan/status").json()["busy"] is True
    finally:
        lock.release()


def test_status_busy_is_per_device(auth_client, fake_scanner, storage):
    from app.services import scanner as scanner_module

    lock = scanner_module.device_lock("fake:1")
    lock.acquire()
    try:
        assert auth_client.get("/api/scan/status", params={"device": "fake:1"}).json()["busy"] is True
        assert auth_client.get("/api/scan/status", params={"device": "fake:0"}).json()["busy"] is False
    finally:
        lock.release()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scanner.py tests/test_scan_api.py -k "locked or busy or default_device" -v`
Expected: FAIL with `AttributeError: module 'app.services.scanner' has no attribute 'device_lock'`

- [ ] **Step 3: Implement**

In `backend/app/services/scanner.py`, replace the block from `_scan_lock = threading.Lock()` through the end of `preview_locked` with:

```python
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
```

In `backend/app/api/scan.py`, change the import line to
`from app.services.scanner import ScannerBackend, device_busy, get_scanner, preview_locked, scan_locked`,
remove `from app.services import scanner as scanner_module`, and replace `scan_status` with:

```python
@router.get("/status")
def scan_status(
    device: str | None = None, backend: ScannerBackend = Depends(get_scanner)
) -> dict:
    return {"available": backend.available(), "busy": device_busy(device)}
```

- [ ] **Step 4: Run the backend suite**

Run: `cd backend && uv run pytest tests/test_scanner.py tests/test_scan_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scanner.py backend/app/api/scan.py backend/tests/test_scanner.py backend/tests/test_scan_api.py
git commit -m "feat: one scan lock per device"
```

---

### Task 2: eSCL helpers

Pure functions: build the `ScanSettings` XML, parse `ScannerCapabilities`, parse `ScannerStatus` into an exception, convert any image to PNG.

**Files:**
- Create: `backend/app/services/escl.py`
- Test: `backend/tests/test_escl.py`

**Interfaces:**
- Consumes: `ScannerError`, `ScannerJam`, `CoverOpen`, `ScannerBusy` from `app.services.scanner`.
- Produces:
  - `@dataclass(frozen=True) class Capabilities: max_width: int; max_height: int; resolutions: list[int]; color_modes: list[str]; formats: list[str]`
  - `parse_capabilities(xml: bytes) -> Capabilities`
  - `pick_resolution(caps: Capabilities, dpi: int) -> int`
  - `pick_format(caps: Capabilities) -> str` (`"image/png"` if offered, else `"image/jpeg"`, else first format)
  - `color_mode(mode: str) -> str` (`"Color"` → `"RGB24"`, anything else → `"Grayscale8"`)
  - `scan_settings_xml(caps: Capabilities, dpi: int, mode: str) -> str`
  - `status_error(xml: bytes) -> ScannerError`
  - `to_png(data: bytes) -> bytes`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_escl.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_escl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.escl'`

- [ ] **Step 3: Implement**

Create `backend/app/services/escl.py`:

```python
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
    return [(el.text or "").strip() for el in root.iter(f"{{*}}{tag}") if (el.text or "").strip()]


def parse_capabilities(xml: bytes) -> Capabilities:
    root = ET.fromstring(xml)
    platen = next(root.iter("{*}PlatenInputCaps"), root)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_escl.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/escl.py backend/tests/test_escl.py
git commit -m "feat: eSCL capability, settings and status helpers"
```

---

### Task 3: Agent hub

In-memory registry of connected agents, launch/resume tokens, and a sync `request()` that runs an eSCL call over the agent's WebSocket.

**Files:**
- Create: `backend/app/services/agent_hub.py`
- Test: `backend/tests/test_agent_hub.py`

**Interfaces:**
- Produces:
  - `class AgentOffline(Exception)`, `class AgentTimeout(Exception)`
  - `@dataclass class EsclResponse: status: int; content_type: str; headers: dict[str, str]; body: bytes`
  - `class AgentHub` with:
    - `issue_token(user_id: int, client_id: str, now: float | None = None) -> str`
    - `consume_token(token: str, now: float | None = None) -> tuple[int, str] | None`
    - `async serve(ws: WebSocket, user_id: int, client_id: str) -> None` (accept already done by caller; runs until the socket closes)
    - `connected(user_id: int, client_id: str) -> bool`
    - `scanners(user_id: int, client_id: str) -> list[dict]` (each `{"uuid", "name"}`)
    - `request(user_id, client_id, scanner_uuid, method, path, body=None, timeout=120.0) -> EsclResponse` (sync; call from a worker thread, never from the event loop)
  - `TOKEN_TTL_SECONDS = 120`, `ID_LEN = 16`
  - `get_agent_hub() -> AgentHub` (module singleton; FastAPI dependency)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_agent_hub.py`. The hub is exercised through a tiny FastAPI app so the WebSocket is real; requests run in a thread while the test thread plays the agent.

```python
import threading

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.services.agent_hub import AgentHub, AgentOffline, AgentTimeout


def make_app(hub: AgentHub) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws/{user_id}/{client_id}")
    async def ws(websocket: WebSocket, user_id: int, client_id: str):
        await websocket.accept()
        await hub.serve(websocket, user_id, client_id)

    return app


def run_request(hub, *args, **kwargs):
    box = {}

    def target():
        try:
            box["result"] = hub.request(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the test
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_tokens_single_use_and_expire():
    hub = AgentHub()
    t = hub.issue_token(1, "client-abc", now=1000.0)
    assert hub.consume_token(t, now=1001.0) == (1, "client-abc")
    assert hub.consume_token(t, now=1001.0) is None
    old = hub.issue_token(1, "client-abc", now=1000.0)
    assert hub.consume_token(old, now=1121.0) is None


def test_devices_and_round_trip_with_chunks():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome" and welcome["resume_token"]
        ws.send_json({"type": "hello", "version": "0.1.0", "os": "linux"})
        ws.send_json({"type": "devices", "devices": [{"uuid": "u1", "name": "HP"}]})
        ws.send_json({"type": "noop"})  # hub ignores unknown types
        # devices are visible once processed
        for _ in range(50):
            if hub.scanners(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        assert hub.connected(1, "client-abc")
        assert hub.scanners(1, "client-abc") == [{"uuid": "u1", "name": "HP"}]

        t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=5)
        msg = ws.receive_json()
        assert msg["type"] == "escl"
        assert (msg["scanner_uuid"], msg["method"], msg["path"]) == ("u1", "GET", "ScannerCapabilities")
        rid = msg["id"]
        assert len(rid) == 16
        ws.send_json({"type": "escl_response", "id": rid, "status": 200,
                      "content_type": "text/xml", "headers": {"Location": "ScanJobs/1"}, "length": 6})
        ws.send_bytes(rid.encode() + b"abc")
        ws.send_bytes(rid.encode() + b"def")
        ws.send_json({"type": "end", "id": rid})
        t.join(5)
        res = box["result"]
        assert (res.status, res.content_type, res.body) == (200, "text/xml", b"abcdef")
        assert res.headers == {"Location": "ScanJobs/1"}


def test_request_without_agent_is_offline():
    with pytest.raises(AgentOffline):
        AgentHub().request(1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=1)


def test_disconnect_fails_pending_request_at_once():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client:
        with client.websocket_connect("/ws/1/client-abc") as ws:
            ws.receive_json()
            ws.send_json({"type": "devices", "devices": [{"uuid": "u1", "name": "HP"}]})
            for _ in range(50):
                if hub.connected(1, "client-abc"):
                    break
                threading.Event().wait(0.02)
            t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=60)
            ws.receive_json()  # the escl message
        t.join(5)
        assert not t.is_alive()
        assert isinstance(box["error"], AgentOffline)
        assert not hub.connected(1, "client-abc")


def test_request_timeout():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client, client.websocket_connect("/ws/1/client-abc") as ws:
        ws.receive_json()
        for _ in range(50):
            if hub.connected(1, "client-abc"):
                break
            threading.Event().wait(0.02)
        t, box = run_request(hub, 1, "client-abc", "u1", "GET", "ScannerCapabilities", timeout=0.3)
        ws.receive_json()
        t.join(5)
        assert isinstance(box["error"], AgentTimeout)


def test_second_agent_replaces_first():
    hub = AgentHub()
    with TestClient(make_app(hub)) as client:
        with client.websocket_connect("/ws/1/client-abc") as first:
            first.receive_json()
            first.send_json({"type": "devices", "devices": [{"uuid": "old", "name": "Old"}]})
            for _ in range(50):
                if hub.scanners(1, "client-abc"):
                    break
                threading.Event().wait(0.02)
            t, box = run_request(hub, 1, "client-abc", "old", "GET", "ScannerCapabilities", timeout=60)
            first.receive_json()
            with client.websocket_connect("/ws/1/client-abc") as second:
                second.receive_json()
                second.send_json({"type": "devices", "devices": [{"uuid": "new", "name": "New"}]})
                t.join(5)
                assert isinstance(box["error"], AgentOffline)
                for _ in range(50):
                    if hub.scanners(1, "client-abc") == [{"uuid": "new", "name": "New"}]:
                        break
                    threading.Event().wait(0.02)
                assert hub.scanners(1, "client-abc") == [{"uuid": "new", "name": "New"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agent_hub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.agent_hub'`

- [ ] **Step 3: Implement**

Create `backend/app/services/agent_hub.py`:

```python
"""Connected client scanner agents.

The API runs as a single uvicorn process, so this in-memory registry is
the source of truth. Scan endpoints are sync and run in the threadpool;
`request()` hands the call to the event loop that owns the agent's socket.
"""

import asyncio
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 120
ID_LEN = 16
AGENT_VERSION = "0.1.0"


class AgentOffline(Exception):
    pass


class AgentTimeout(Exception):
    pass


@dataclass
class EsclResponse:
    status: int
    content_type: str
    headers: dict[str, str]
    body: bytes


@dataclass
class _Pending:
    future: asyncio.Future
    meta: dict | None = None
    chunks: list[bytes] = field(default_factory=list)


@dataclass
class _Agent:
    ws: WebSocket
    loop: asyncio.AbstractEventLoop
    scanners: list[dict] = field(default_factory=list)
    pending: dict[str, _Pending] = field(default_factory=dict)


class AgentHub:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[int, str, float]] = {}
        self._agents: dict[tuple[int, str], _Agent] = {}
        self._lock = threading.Lock()

    # tokens ---------------------------------------------------------------

    def issue_token(self, user_id: int, client_id: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens = {t: v for t, v in self._tokens.items() if v[2] > now}
            self._tokens[token] = (user_id, client_id, now + TOKEN_TTL_SECONDS)
        return token

    def consume_token(self, token: str, now: float | None = None) -> tuple[int, str] | None:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None or entry[2] <= now:
            return None
        return entry[0], entry[1]

    # registry -------------------------------------------------------------

    def connected(self, user_id: int, client_id: str) -> bool:
        with self._lock:
            return (user_id, client_id) in self._agents

    def scanners(self, user_id: int, client_id: str) -> list[dict]:
        with self._lock:
            agent = self._agents.get((user_id, client_id))
            return list(agent.scanners) if agent else []

    async def serve(self, ws: WebSocket, user_id: int, client_id: str) -> None:
        key = (user_id, client_id)
        agent = _Agent(ws=ws, loop=asyncio.get_running_loop())
        with self._lock:
            old = self._agents.get(key)
            self._agents[key] = agent
        if old is not None:
            self._fail_pending(old)
            try:
                await old.ws.close(code=4000)
            except RuntimeError:
                pass  # already closed
        await ws.send_json({"type": "welcome", "resume_token": self.issue_token(user_id, client_id)})
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    self._on_chunk(agent, msg["bytes"])
                elif msg.get("text") is not None:
                    self._on_message(agent, json.loads(msg["text"]))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            with self._lock:
                if self._agents.get(key) is agent:
                    del self._agents[key]
            self._fail_pending(agent)

    def _on_message(self, agent: _Agent, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            if msg.get("version") != AGENT_VERSION:
                log.warning("agent version %s, server expects %s", msg.get("version"), AGENT_VERSION)
        elif kind == "devices":
            agent.scanners = [
                {"uuid": str(d["uuid"]), "name": str(d.get("name") or d["uuid"])}
                for d in msg.get("devices") or []
            ]
        elif kind == "escl_response":
            pending = agent.pending.get(msg.get("id", ""))
            if pending is not None:
                pending.meta = msg
        elif kind == "end":
            pending = agent.pending.pop(msg.get("id", ""), None)
            if pending is not None and not pending.future.done():
                meta = pending.meta or {}
                pending.future.set_result(
                    EsclResponse(
                        status=int(meta.get("status", 502)),
                        content_type=str(meta.get("content_type", "")),
                        headers=dict(meta.get("headers") or {}),
                        body=b"".join(pending.chunks),
                    )
                )

    def _on_chunk(self, agent: _Agent, data: bytes) -> None:
        pending = agent.pending.get(data[:ID_LEN].decode(errors="replace"))
        if pending is not None:
            pending.chunks.append(data[ID_LEN:])

    @staticmethod
    def _fail_pending(agent: _Agent) -> None:
        def fail() -> None:
            for pending in agent.pending.values():
                if not pending.future.done():
                    pending.future.set_exception(AgentOffline())
            agent.pending.clear()

        try:
            if asyncio.get_running_loop() is agent.loop:
                fail()
                return
        except RuntimeError:
            pass
        agent.loop.call_soon_threadsafe(fail)

    # requests -------------------------------------------------------------

    def request(
        self,
        user_id: int,
        client_id: str,
        scanner_uuid: str,
        method: str,
        path: str,
        body: str | None = None,
        timeout: float = 120.0,
    ) -> EsclResponse:
        with self._lock:
            agent = self._agents.get((user_id, client_id))
        if agent is None:
            raise AgentOffline()
        rid = secrets.token_hex(ID_LEN // 2)

        async def call() -> EsclResponse:
            future = agent.loop.create_future()
            agent.pending[rid] = _Pending(future=future)
            await agent.ws.send_json(
                {"type": "escl", "id": rid, "scanner_uuid": scanner_uuid,
                 "method": method, "path": path, "body": body or ""}
            )
            try:
                return await asyncio.wait_for(future, timeout)
            finally:
                agent.pending.pop(rid, None)

        try:
            return asyncio.run_coroutine_threadsafe(call(), agent.loop).result(timeout + 5)
        except (asyncio.TimeoutError, TimeoutError):
            raise AgentTimeout()
        except RuntimeError:
            raise AgentOffline()  # socket closed while sending


_hub = AgentHub()


def get_agent_hub() -> AgentHub:
    return _hub
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_agent_hub.py -v`
Expected: all PASS. If `test_disconnect_fails_pending_request_at_once` hangs, check that `serve()`'s `finally` runs `_fail_pending` on the agent loop (it is called from inside the loop, so `fail()` runs directly).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_hub.py backend/tests/test_agent_hub.py
git commit -m "feat: in-memory hub for client scanner agents"
```

---

### Task 4: Agent API (launch, WebSocket, downloads)

**Files:**
- Create: `backend/app/api/agent.py`
- Modify: `backend/app/config.py` (add two settings after `soffice_path`)
- Modify: `backend/app/main.py` (import and include `agent.router`)
- Modify: `backend/tests/conftest.py` (add `agent_hub` fixture)
- Test: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `AgentHub`, `get_agent_hub` (Task 3); `get_current_user`, `get_current_user_flexible`, `api_error` from `app.api.deps`; `decode_token`.
- Produces:
  - `POST /api/agent/launch` body `{"client_id": str}` → `{"url": "origami-agent://connect?server=<urlencoded>&token=<t>"}`; invalid `client_id` → 422 `invalid_client_id`.
  - `WS /api/agent/ws?token=<t>` → closes with code 4401 on a bad token.
  - `GET /api/agent/download/{platform}` (`?token=` allowed) → file; unknown platform → 404 `unknown_platform`; missing file → 404 `agent_not_built`.
  - `CLIENT_ID_RE` (compiled regex) and `AGENT_FILES: dict[str, str]` in `app.api.agent`.
  - Settings `public_url: str = ""`, `agent_dist_dir: Path = Path("../agent/dist")`.
  - Test fixture `agent_hub` → fresh `AgentHub` installed as the `get_agent_hub` override.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/conftest.py`, after the `fake_scanner` fixture:

```python
@pytest.fixture
def agent_hub(client):
    from app.main import app as main_app
    from app.services.agent_hub import AgentHub, get_agent_hub

    hub = AgentHub()
    main_app.dependency_overrides[get_agent_hub] = lambda: hub
    yield hub
    main_app.dependency_overrides.pop(get_agent_hub, None)
```

Create `backend/tests/test_agent_api.py`:

```python
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.websockets import WebSocketDisconnect

CLIENT = "0f8e2a4c-1111-4222-8333-444455556666"


def launch(auth_client, client_id=CLIENT):
    return auth_client.post("/api/agent/launch", json={"client_id": client_id})


def test_launch_returns_agent_url(auth_client, agent_hub, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PUBLIC_URL", "https://origami.example.org")
    get_settings.cache_clear()
    try:
        resp = launch(auth_client)
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200
    url = urlparse(resp.json()["url"])
    assert url.scheme == "origami-agent" and url.netloc == "connect"
    query = parse_qs(url.query)
    assert query["server"] == ["https://origami.example.org"]
    assert agent_hub.consume_token(query["token"][0]) is not None


def test_launch_falls_back_to_request_base_url(auth_client, agent_hub):
    query = parse_qs(urlparse(launch(auth_client).json()["url"]).query)
    assert query["server"] == ["http://testserver"]


@pytest.mark.parametrize("bad", ["short", "has:colon-xxxxxxxx", "x" * 65])
def test_launch_rejects_bad_client_id(auth_client, agent_hub, bad):
    resp = launch(auth_client, bad)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_client_id"


def test_launch_requires_login(client, agent_hub):
    assert client.post("/api/agent/launch", json={"client_id": CLIENT}).status_code == 401


def test_ws_with_valid_token_registers_agent(auth_client, agent_hub, user):
    token = parse_qs(urlparse(launch(auth_client).json()["url"]).query)["token"][0]
    with auth_client.websocket_connect(f"/api/agent/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "welcome"
        assert agent_hub.connected(user.id, CLIENT)


def test_ws_rejects_bad_token(auth_client, agent_hub):
    with pytest.raises(WebSocketDisconnect) as exc:
        with auth_client.websocket_connect("/api/agent/ws?token=nope") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_resume_token_reconnects(auth_client, agent_hub, user):
    token = parse_qs(urlparse(launch(auth_client).json()["url"]).query)["token"][0]
    with auth_client.websocket_connect(f"/api/agent/ws?token={token}") as ws:
        resume = ws.receive_json()["resume_token"]
    with auth_client.websocket_connect(f"/api/agent/ws?token={resume}") as ws:
        assert ws.receive_json()["type"] == "welcome"
        assert agent_hub.connected(user.id, CLIENT)


def test_download_serves_built_file(auth_client, tmp_path, monkeypatch):
    from app.config import get_settings

    (tmp_path / "origami-agent-linux-amd64").write_bytes(b"\x7fELF-binary")
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        ok = auth_client.get("/api/agent/download/linux-amd64")
        missing = auth_client.get("/api/agent/download/windows-amd64")
        unknown = auth_client.get("/api/agent/download/amiga")
    finally:
        get_settings.cache_clear()
    assert ok.status_code == 200 and ok.content == b"\x7fELF-binary"
    assert 'filename="origami-agent-linux-amd64"' in ok.headers["content-disposition"]
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "agent_not_built"
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_platform"


def test_download_accepts_query_token(client, user, tmp_path, monkeypatch):
    from app.config import get_settings
    from app.services.auth import create_access_token

    (tmp_path / "origami-agent-linux-amd64").write_bytes(b"bin")
    monkeypatch.setenv("AGENT_DIST_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        resp = client.get(f"/api/agent/download/linux-amd64?token={create_access_token(user.id)}")
    finally:
        get_settings.cache_clear()
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_agent_api.py -v`
Expected: FAIL (404 on `/api/agent/launch`, fixture import ok)

- [ ] **Step 3: Implement**

In `backend/app/config.py`, after `soffice_path`, add:

```python
    public_url: str = ""  # e.g. https://origami.example.com — public server URL handed to the client scanner agent; empty → request base URL
    agent_dist_dir: Path = Path("../agent/dist")  # built agent binaries served by /api/agent/download
```

Create `backend/app/api/agent.py`:

```python
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import api_error, get_current_user, get_current_user_flexible
from app.config import get_settings
from app.models import User
from app.services.agent_hub import AgentHub, get_agent_hub

router = APIRouter(prefix="/api/agent", tags=["agent"])

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
AGENT_FILES = {
    "windows-amd64": "origami-agent-windows-amd64.exe",
    "linux-amd64": "origami-agent-linux-amd64",
    "linux-arm64": "origami-agent-linux-arm64",
    "darwin-arm64": "origami-agent-darwin-arm64.zip",
    "darwin-amd64": "origami-agent-darwin-amd64.zip",
}


class LaunchRequest(BaseModel):
    client_id: str


@router.post("/launch")
def launch(
    body: LaunchRequest,
    request: Request,
    user: User = Depends(get_current_user),
    hub: AgentHub = Depends(get_agent_hub),
) -> dict:
    if not CLIENT_ID_RE.match(body.client_id):
        raise api_error(422, "invalid_client_id", "client_id must be 8-64 letters, digits or dashes")
    server = get_settings().public_url or str(request.base_url)
    query = urlencode({"server": server.rstrip("/"), "token": hub.issue_token(user.id, body.client_id)})
    return {"url": f"origami-agent://connect?{query}"}


@router.websocket("/ws")
async def agent_socket(websocket: WebSocket, token: str = "", hub: AgentHub = Depends(get_agent_hub)) -> None:
    owner = hub.consume_token(token)
    await websocket.accept()
    if owner is None:
        await websocket.close(code=4401)
        return
    await hub.serve(websocket, owner[0], owner[1])


@router.get("/download/{platform}")
def download(platform: str, user: User = Depends(get_current_user_flexible)) -> FileResponse:
    name = AGENT_FILES.get(platform)
    if name is None:
        raise api_error(404, "unknown_platform", f"No agent build for {platform}")
    path = get_settings().agent_dist_dir / name
    if not path.is_file():
        raise api_error(404, "agent_not_built", "The scanner agent has not been built on the server")
    return FileResponse(path, filename=name, media_type="application/octet-stream")
```

In `.env.example`, after `APP_BASE_URL=`, add:

```
# Public URL clients use to reach Origami; handed to the client scanner agent. Empty = request base URL.
PUBLIC_URL=
# Folder with the built scanner agent files (agent/build.sh output), relative to backend/.
AGENT_DIST_DIR=../agent/dist
```

In `backend/app/main.py`, change the import to
`from app.api import agent, auth, chat, documents, files, folders, ocr, scan, search, tags, uploads`
and add `app.include_router(agent.router)` before `app.include_router(auth.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_agent_api.py tests/test_agent_hub.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add .env.example backend/app/api/agent.py backend/app/config.py backend/app/main.py backend/tests/conftest.py backend/tests/test_agent_api.py
git commit -m "feat: agent launch token, WebSocket and download endpoints"
```

---

### Task 5: Remote backend, composite routing, scan API wiring

**Files:**
- Modify: `backend/app/services/escl.py` (append `EsclRemoteBackend`)
- Modify: `backend/app/services/scanner.py` (append `CompositeBackend`)
- Modify: `backend/app/api/scan.py` (new `get_scan_backend` dependency; `status`, `devices`, `preview`, `scan_page` use it)
- Test: `backend/tests/test_remote_scan.py`

**Interfaces:**
- Consumes: `AgentHub.request/scanners/connected`, `AgentOffline`, `AgentTimeout`, `EsclResponse` (Task 3); helpers from Task 2; `device_busy` (Task 1); `CLIENT_ID_RE` (Task 4).
- Produces:
  - `AGENT_PREFIX = "agent:"`, `parse_agent_device(device: str) -> tuple[str, str]` in `app.services.escl` (raises `ScannerOffline` on a malformed id).
  - `class EsclRemoteBackend(hub, user_id: int, sleep=time.sleep)` with `devices(client_id) -> list[dict]`, `scan(dpi, mode, device) -> bytes`, `preview(device) -> bytes`, `available(device) -> bool`.
  - `class CompositeBackend(local: ScannerBackend, remote: EsclRemoteBackend, client_id: str | None)` implementing `ScannerBackend` (`available(device=None)` gains an optional device argument).
  - `GET /api/scan/devices?client_id=` → `{"devices", "default", "agent_connected"}`.
  - `GET /api/scan/status?client_id=&device=`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_remote_scan.py`. A `FakeHub` scripts eSCL replies, so no socket is needed:

```python
import io

import pytest
from PIL import Image

from app.services.agent_hub import AgentOffline, AgentTimeout, EsclResponse
from app.services.escl import EsclRemoteBackend, parse_agent_device
from app.services.scanner import ScannerBusy, ScannerOffline, ScannerTimeout
from tests.test_escl import CAPS

CLIENT = "0f8e2a4c-1111-4222-8333-444455556666"
DEVICE = f"agent:{CLIENT}:u1"


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), "white").save(buf, "JPEG")
    return buf.getvalue()


class FakeHub:
    def __init__(self, user_id=1, scanners=None):
        self.user_id = user_id
        self._scanners = scanners if scanners is not None else [{"uuid": "u1", "name": "HP Envy"}]
        self.calls = []
        self.next_document = [EsclResponse(200, "image/jpeg", {}, jpeg())]
        self.post_status = 201
        self.error = None

    def connected(self, user_id, client_id):
        return user_id == self.user_id and client_id == CLIENT

    def scanners(self, user_id, client_id):
        return list(self._scanners) if self.connected(user_id, client_id) else []

    def request(self, user_id, client_id, scanner_uuid, method, path, body=None, timeout=120.0):
        self.calls.append((method, path, body))
        if self.error:
            raise self.error
        if not self.connected(user_id, client_id):
            raise AgentOffline()
        if path == "ScannerCapabilities":
            return EsclResponse(200, "text/xml", {}, CAPS)
        if method == "POST" and path == "ScanJobs":
            return EsclResponse(self.post_status, "", {"Location": "ScanJobs/7"}, b"")
        if path == "ScanJobs/7/NextDocument":
            return self.next_document.pop(0)
        if path == "ScannerStatus":
            return EsclResponse(200, "text/xml", {}, b"<State>Processing</State>")
        return EsclResponse(404, "", {}, b"")


def test_parse_agent_device():
    assert parse_agent_device(DEVICE) == (CLIENT, "u1")
    assert parse_agent_device(f"agent:{CLIENT}:a:b") == (CLIENT, "a:b")
    with pytest.raises(ScannerOffline):
        parse_agent_device("agent:bad")


def test_devices_and_available():
    remote = EsclRemoteBackend(FakeHub(), user_id=1)
    assert remote.devices(CLIENT) == [{"id": DEVICE, "name": "HP Envy"}]
    assert remote.available(DEVICE)
    assert not remote.available(f"agent:{CLIENT}:gone")


def test_scan_runs_job_and_returns_png():
    hub = FakeHub()
    png = EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)
    assert png.startswith(b"\x89PNG")
    methods = [(m, p) for m, p, _ in hub.calls]
    assert methods == [("GET", "ScannerCapabilities"), ("POST", "ScanJobs"), ("GET", "ScanJobs/7/NextDocument")]
    assert "<scan:ColorMode>RGB24</scan:ColorMode>" in hub.calls[1][2]


def test_capabilities_are_cached():
    hub = FakeHub()
    remote = EsclRemoteBackend(hub, user_id=1)
    remote.scan(dpi=300, mode="Color", device=DEVICE)
    hub.next_document = [EsclResponse(200, "image/jpeg", {}, jpeg())]
    remote.preview(device=DEVICE)
    assert [p for _, p, _ in hub.calls].count("ScannerCapabilities") == 1
    assert "<scan:ColorMode>Grayscale8</scan:ColorMode>" in hub.calls[-2][2]


def test_next_document_503_is_retried():
    hub = FakeHub()
    hub.next_document = [
        EsclResponse(503, "", {}, b""),
        EsclResponse(503, "", {}, b""),
        EsclResponse(200, "image/jpeg", {}, jpeg()),
    ]
    sleeps = []
    png = EsclRemoteBackend(hub, user_id=1, sleep=sleeps.append).scan(dpi=300, mode="Color", device=DEVICE)
    assert png.startswith(b"\x89PNG")
    assert len(sleeps) == 2


def test_busy_scanner_on_post():
    hub = FakeHub()
    hub.post_status = 503
    with pytest.raises(ScannerBusy):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_agent_errors_map_to_scanner_errors():
    hub = FakeHub()
    hub.error = AgentOffline()
    with pytest.raises(ScannerOffline):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)
    hub.error = AgentTimeout()
    with pytest.raises(ScannerTimeout):
        EsclRemoteBackend(hub, user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


def test_other_users_device_is_offline():
    with pytest.raises(ScannerOffline):
        EsclRemoteBackend(FakeHub(user_id=2), user_id=1).scan(dpi=300, mode="Color", device=DEVICE)


# --- API wiring ----------------------------------------------------------


@pytest.fixture
def fake_hub(client, user):
    from app.main import app as main_app
    from app.services.agent_hub import get_agent_hub

    hub = FakeHub(user_id=user.id)
    main_app.dependency_overrides[get_agent_hub] = lambda: hub
    yield hub
    main_app.dependency_overrides.pop(get_agent_hub, None)


def test_devices_lists_server_then_agent(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/devices", params={"client_id": CLIENT}).json()
    assert [d["id"] for d in data["devices"]] == ["fake:0", DEVICE]
    assert data["default"] == "fake:0"
    assert data["agent_connected"] is True


def test_devices_without_client_id_is_server_only(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/devices").json()
    assert [d["id"] for d in data["devices"]] == ["fake:0"]
    assert data["agent_connected"] is False


def test_page_scan_routes_agent_device(auth_client, fake_scanner, fake_hub, storage):
    sid = auth_client.post("/api/scan/sessions", json={"device": DEVICE}).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert resp.status_code == 201
    stored = storage.scan_session_dir(sid) / "page_001.png"
    assert stored.read_bytes().startswith(b"\x89PNG")
    assert fake_scanner.last_device is None  # the server scanner was not used


def test_status_for_agent_device(auth_client, fake_scanner, fake_hub):
    data = auth_client.get("/api/scan/status", params={"client_id": CLIENT, "device": DEVICE}).json()
    assert data == {"available": True, "busy": False}
    gone = auth_client.get("/api/scan/status", params={"client_id": CLIENT, "device": f"agent:{CLIENT}:x"}).json()
    assert gone["available"] is False


def test_preview_agent_device_offline_is_503(auth_client, fake_scanner, fake_hub):
    fake_hub.error = AgentOffline()
    resp = auth_client.post("/api/scan/preview", json={"device": DEVICE})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "scanner_offline"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_remote_scan.py -v`
Expected: FAIL with `ImportError: cannot import name 'EsclRemoteBackend'`

- [ ] **Step 3: Implement the remote backend**

Append to `backend/app/services/escl.py` (and add `import time` plus the extra imports at the top):

```python
import time

from app.services.agent_hub import AgentHub, AgentOffline, AgentTimeout, EsclResponse
from app.services.scanner import (
    PREVIEW_MODE,
    PREVIEW_RESOLUTION,
    SCAN_TIMEOUT_SECONDS,
    ScannerOffline,
    ScannerTimeout,
)

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
            self._caps[device] = parse_capabilities(res.body)
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
                return to_png(doc.body)
            if doc.status != 503 or time.monotonic() >= deadline:
                break
            self._sleep(NEXT_DOCUMENT_RETRY_SECONDS)
        if doc.status == 503:
            raise ScannerTimeout()
        status = self._call(client_id, uuid, "GET", "ScannerStatus")
        raise status_error(status.body)
```

Note: `escl.py` now imports from `agent_hub`, and `scanner.py` must not import `escl.py` at module level (circular). `CompositeBackend` takes the remote backend as a constructor argument and only checks the id prefix string.

- [ ] **Step 4: Implement the composite backend**

Append to `backend/app/services/scanner.py`:

```python
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
```

- [ ] **Step 5: Wire the scan API**

In `backend/app/api/scan.py`:

Add imports:

```python
from app.api.agent import CLIENT_ID_RE
from app.models import User
from app.services.agent_hub import AgentHub, get_agent_hub
from app.services.escl import EsclRemoteBackend
from app.services.scanner import CompositeBackend
```

and `get_current_user` is already imported from `app.api.deps`. Add the dependency below `session_pages`:

```python
def get_scan_backend(
    client_id: str | None = None,
    user: User = Depends(get_current_user),
    local: ScannerBackend = Depends(get_scanner),
    hub: AgentHub = Depends(get_agent_hub),
) -> CompositeBackend:
    if client_id is not None and not CLIENT_ID_RE.match(client_id):
        client_id = None
    return CompositeBackend(local, EsclRemoteBackend(hub, user.id), client_id)
```

Replace `scan_status` and `scan_devices`:

```python
@router.get("/status")
def scan_status(
    device: str | None = None, backend: CompositeBackend = Depends(get_scan_backend)
) -> dict:
    return {"available": backend.available(device), "busy": device_busy(device)}


@router.get("/devices")
def scan_devices(
    client_id: str | None = None,
    backend: CompositeBackend = Depends(get_scan_backend),
    user: User = Depends(get_current_user),
    hub: AgentHub = Depends(get_agent_hub),
) -> dict:
    devices = backend.list_devices()
    connected = bool(client_id) and hub.connected(user.id, client_id)
    return {"devices": devices, "default": devices[0]["id"] if devices else None, "agent_connected": connected}
```

In `scan_preview` and `scan_page`, change the parameter `backend: ScannerBackend = Depends(get_scanner)` to `backend: CompositeBackend = Depends(get_scan_backend)`. In `scan_page`, pass the resolved device to the lock as well (already does: `device=body.device or scan_session.device`).

`FakeScannerBackend.available()` takes no device; `CompositeBackend.available` calls `self._local.available()` without arguments, so the fake keeps working.

- [ ] **Step 6: Run the whole backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all PASS (existing scan tests go through `CompositeBackend` with the fake local scanner).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/escl.py backend/app/services/scanner.py backend/app/api/scan.py backend/tests/test_remote_scan.py
git commit -m "feat: scan through eSCL scanners on the client's network"
```

---

### Task 6: Go agent — module, protocol, restricted eSCL executor

**Files:**
- Create: `agent/go.mod`, `agent/protocol.go`, `agent/protocol_test.go`, `agent/escl.go`, `agent/escl_test.go`

**Interfaces:**
- Produces (package `main`):
  - `const Version = "0.1.0"` (must equal backend `AGENT_VERSION`), `const ChunkSize = 262144`, `const IDLen = 16`
  - `type Incoming struct { Type, ID, ScannerUUID, Method, Path, Body, ResumeToken string }` (JSON tags `type`, `id`, `scanner_uuid`, `method`, `path`, `body`, `resume_token`)
  - `type Device struct { UUID string \`json:"uuid"\`; Name string \`json:"name"\` }`
  - `func helloMsg() map[string]any`, `func devicesMsg(d []Device) map[string]any`, `func responseMsg(id string, r Result) map[string]any`, `func endMsg(id string) map[string]any`
  - `func chunks(id string, body []byte) [][]byte` (each = id bytes + ≤ ChunkSize data; empty body → no chunks)
  - `type Scanner struct { UUID, Name, BaseURL string }` (`BaseURL` = `scheme://host:port/<root>`, no trailing slash)
  - `type Result struct { Status int; ContentType string; Headers map[string]string; Body []byte }`
  - `type Executor struct { Client *http.Client; Lookup func(uuid string) (Scanner, bool) }`, `func (e *Executor) Do(ctx context.Context, uuid, method, path, body string) Result`
  - `func validPath(p string) bool`, `func relativeLocation(baseURL, loc string) string`, `func newHTTPClient() *http.Client`

- [ ] **Step 1: Install Go and create the module**

Go is not installed on this machine. Install Go 1.22 or newer (e.g. `sudo snap install go --classic`, or the tarball from go.dev). Check: `go version`.

```bash
mkdir -p agent && cd agent
go mod init github.com/yabzec/origami/agent
```

Edit `agent/go.mod` so the `go` line reads `go 1.22`.

- [ ] **Step 2: Write the failing tests**

Create `agent/protocol_test.go`:

```go
package main

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestChunksSplitAndPrefix(t *testing.T) {
	id := "0123456789abcdef"
	body := bytes.Repeat([]byte("x"), ChunkSize*2+10)
	got := chunks(id, body)
	if len(got) != 3 {
		t.Fatalf("want 3 chunks, got %d", len(got))
	}
	var joined []byte
	for _, c := range got {
		if string(c[:IDLen]) != id {
			t.Fatalf("chunk without id prefix")
		}
		if len(c)-IDLen > ChunkSize {
			t.Fatalf("chunk too big: %d", len(c)-IDLen)
		}
		joined = append(joined, c[IDLen:]...)
	}
	if !bytes.Equal(joined, body) {
		t.Fatal("chunks do not rebuild the body")
	}
	if len(chunks(id, nil)) != 0 {
		t.Fatal("empty body must give no chunks")
	}
}

func TestDevicesMsgSendsEmptyList(t *testing.T) {
	raw, _ := json.Marshal(devicesMsg(nil))
	if string(raw) != `{"devices":[],"type":"devices"}` {
		t.Fatalf("got %s", raw)
	}
}

func TestResponseMsg(t *testing.T) {
	raw, _ := json.Marshal(responseMsg("id1", Result{Status: 201, ContentType: "text/xml",
		Headers: map[string]string{"Location": "ScanJobs/1"}, Body: []byte("abc")}))
	var m map[string]any
	_ = json.Unmarshal(raw, &m)
	if m["type"] != "escl_response" || m["status"].(float64) != 201 || m["length"].(float64) != 3 {
		t.Fatalf("bad message %s", raw)
	}
}
```

Create `agent/escl_test.go`:

```go
package main

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

func fakeScanner(t *testing.T) (*httptest.Server, *[]string) {
	var hits []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		hits = append(hits, r.Method+" "+r.URL.Path+" "+string(body))
		switch r.URL.Path {
		case "/eSCL/ScanJobs":
			w.Header().Set("Location", "http://"+r.Host+"/eSCL/ScanJobs/7")
			w.WriteHeader(http.StatusCreated)
		case "/eSCL/ScanJobs/7/NextDocument":
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte("JPEGDATA"))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv, &hits
}

func executorFor(srv *httptest.Server) *Executor {
	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: srv.URL + "/eSCL"}
	return &Executor{Client: srv.Client(), Lookup: func(uuid string) (Scanner, bool) {
		return sc, uuid == sc.UUID
	}}
}

func TestDoPostsAndRewritesLocation(t *testing.T) {
	srv, hits := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "POST", "ScanJobs", "<xml/>")
	if res.Status != 201 {
		t.Fatalf("status %d", res.Status)
	}
	if res.Headers["Location"] != "ScanJobs/7" {
		t.Fatalf("location %q", res.Headers["Location"])
	}
	if (*hits)[0] != "POST /eSCL/ScanJobs <xml/>" {
		t.Fatalf("hit %q", (*hits)[0])
	}
}

func TestDoReturnsBody(t *testing.T) {
	srv, _ := fakeScanner(t)
	res := executorFor(srv).Do(context.Background(), "u1", "GET", "ScanJobs/7/NextDocument", "")
	if res.Status != 200 || string(res.Body) != "JPEGDATA" || res.ContentType != "image/jpeg" {
		t.Fatalf("bad result %+v", res)
	}
}

func TestDoRejectsWithoutCallingScanner(t *testing.T) {
	srv, hits := fakeScanner(t)
	ex := executorFor(srv)
	cases := []struct{ uuid, method, path string }{
		{"other", "GET", "ScannerCapabilities"},
		{"u1", "PUT", "ScanJobs"},
		{"u1", "GET", "/etc/passwd"},
		{"u1", "GET", "../admin"},
		{"u1", "GET", "ScanJobs/../../admin"},
		{"u1", "GET", "http://10.0.0.1/x"},
		{"u1", "GET", ""},
	}
	for _, c := range cases {
		if res := ex.Do(context.Background(), c.uuid, c.method, c.path, ""); res.Status != 403 {
			t.Errorf("%+v: want 403, got %d", c, res.Status)
		}
	}
	if len(*hits) != 0 {
		t.Fatalf("scanner was called: %v", *hits)
	}
}

func TestRelativeLocation(t *testing.T) {
	base := "http://192.168.1.20:80/eSCL"
	cases := map[string]string{
		"http://192.168.1.20/eSCL/ScanJobs/7": "ScanJobs/7",
		"/eSCL/ScanJobs/7":                    "ScanJobs/7",
		"ScanJobs/7":                          "ScanJobs/7",
		"https://192.168.1.20:443/eSCL/ScanJobs/abc-1/": "ScanJobs/abc-1",
	}
	for in, want := range cases {
		if got := relativeLocation(base, in); got != want {
			t.Errorf("%s: got %q want %q", in, got, want)
		}
	}
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd agent && go test ./...`
Expected: FAIL with `undefined: chunks` (and other undefined names)

- [ ] **Step 4: Implement**

Create `agent/protocol.go`:

```go
package main

// Version must match AGENT_VERSION in backend/app/services/agent_hub.py.
const Version = "0.1.0"

// ChunkSize is the largest body slice sent in one binary frame.
const ChunkSize = 262144

// IDLen is the length of the ASCII request id that prefixes every binary frame.
const IDLen = 16

// Incoming is any JSON message the server sends.
type Incoming struct {
	Type        string `json:"type"`
	ID          string `json:"id"`
	ScannerUUID string `json:"scanner_uuid"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	Body        string `json:"body"`
	ResumeToken string `json:"resume_token"`
}

type Device struct {
	UUID string `json:"uuid"`
	Name string `json:"name"`
}

func helloMsg() map[string]any {
	return map[string]any{"type": "hello", "version": Version, "os": osName()}
}

func devicesMsg(d []Device) map[string]any {
	if d == nil {
		d = []Device{}
	}
	return map[string]any{"type": "devices", "devices": d}
}

func responseMsg(id string, r Result) map[string]any {
	headers := r.Headers
	if headers == nil {
		headers = map[string]string{}
	}
	return map[string]any{
		"type": "escl_response", "id": id, "status": r.Status,
		"content_type": r.ContentType, "headers": headers, "length": len(r.Body),
	}
}

func endMsg(id string) map[string]any {
	return map[string]any{"type": "end", "id": id}
}

func chunks(id string, body []byte) [][]byte {
	var out [][]byte
	for start := 0; start < len(body); start += ChunkSize {
		end := min(start+ChunkSize, len(body))
		frame := make([]byte, 0, IDLen+end-start)
		frame = append(frame, id...)
		frame = append(frame, body[start:end]...)
		out = append(out, frame)
	}
	return out
}
```

Create `agent/escl.go`:

```go
package main

import (
	"context"
	"crypto/tls"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"runtime"
	"strings"
	"time"
)

const maxBody = 200 << 20 // 200 MB: far above a 600 dpi colour page

var safePath = regexp.MustCompile(`^[A-Za-z0-9._\-/]+$`)

// Scanner is an eSCL scanner found on the local network. Its address never leaves the agent.
type Scanner struct {
	UUID    string
	Name    string
	BaseURL string // scheme://host:port/<eSCL root>, no trailing slash
}

type Result struct {
	Status      int
	ContentType string
	Headers     map[string]string
	Body        []byte
}

// Executor runs eSCL requests, but only for discovered scanners and only under their eSCL root.
type Executor struct {
	Client *http.Client
	Lookup func(uuid string) (Scanner, bool)
}

func osName() string { return runtime.GOOS + "/" + runtime.GOARCH }

func newHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 130 * time.Second,
		Transport: &http.Transport{
			// _uscans._tcp scanners use self-signed certificates.
			TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, //nolint:gosec
		},
	}
}

func validPath(p string) bool {
	return p != "" && !strings.HasPrefix(p, "/") && !strings.Contains(p, "..") && safePath.MatchString(p)
}

func forbidden() Result { return Result{Status: http.StatusForbidden} }

func (e *Executor) Do(ctx context.Context, uuid, method, path, body string) Result {
	if method != http.MethodGet && method != http.MethodPost && method != http.MethodDelete {
		return forbidden()
	}
	sc, ok := e.Lookup(uuid)
	if !ok || !validPath(path) {
		return forbidden()
	}
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, sc.BaseURL+"/"+path, reader)
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	if body != "" {
		req.Header.Set("Content-Type", "text/xml")
	}
	resp, err := e.Client.Do(req)
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return Result{Status: http.StatusBadGateway}
	}
	headers := map[string]string{}
	if loc := resp.Header.Get("Location"); loc != "" {
		headers["Location"] = relativeLocation(sc.BaseURL, loc)
	}
	return Result{Status: resp.StatusCode, ContentType: resp.Header.Get("Content-Type"), Headers: headers, Body: data}
}

// relativeLocation turns a job URL from the scanner into a path relative to the eSCL root.
func relativeLocation(baseURL, loc string) string {
	u, err := url.Parse(loc)
	if err != nil {
		return ""
	}
	p := u.Path
	if b, err := url.Parse(baseURL); err == nil {
		root := strings.TrimSuffix(b.Path, "/") + "/"
		p = strings.TrimPrefix(p, root)
	}
	return strings.Trim(p, "/")
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd agent && go vet ./... && go test ./...`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/go.mod agent/protocol.go agent/protocol_test.go agent/escl.go agent/escl_test.go
git commit -m "feat(agent): protocol framing and restricted eSCL executor"
```

---

### Task 7: Go agent — discovery and connection loop

**Files:**
- Create: `agent/discovery.go`, `agent/discovery_test.go`, `agent/conn.go`, `agent/conn_test.go`

**Interfaces:**
- Consumes: Task 6 names.
- Produces:
  - `func scannerFromRecord(instance string, txt []string, ipv4, ipv6 []net.IP, port int, secure bool) (Scanner, bool)`
  - `type Browser func(ctx context.Context, timeout time.Duration) []Scanner`; `func browseMDNS(ctx context.Context, timeout time.Duration) []Scanner`
  - `type Agent struct` with `func NewAgent(server, token string, browse Browser) *Agent`, `func (a *Agent) Run(ctx context.Context) error`, `func (a *Agent) Relaunch(server, token string)`
  - Constants `IdleTimeout = 30 * time.Minute`, `ReconnectWindow = 60 * time.Second`, `PingInterval = 30 * time.Second`, `RediscoverInterval = 60 * time.Second`
  - `func wsURL(server, token string) (string, error)`

- [ ] **Step 1: Add dependencies**

```bash
cd agent
go get github.com/coder/websocket@v1.8.12 github.com/libp2p/zeroconf/v2@v2.2.0
```

- [ ] **Step 2: Write the failing tests**

Create `agent/discovery_test.go`:

```go
package main

import (
	"net"
	"testing"
)

func TestScannerFromRecord(t *testing.T) {
	sc, ok := scannerFromRecord("HP Envy 6000 [A1B2]",
		[]string{"txtvers=1", "ty=HP ENVY 6000", "uuid=1234-abcd", "rs=/eSCL"},
		[]net.IP{net.ParseIP("192.168.1.20")}, nil, 80, false)
	if !ok {
		t.Fatal("not ok")
	}
	want := Scanner{UUID: "1234-abcd", Name: "HP ENVY 6000", BaseURL: "http://192.168.1.20:80/eSCL"}
	if sc != want {
		t.Fatalf("got %+v", sc)
	}
}

func TestScannerFromRecordDefaultsAndIPv6(t *testing.T) {
	sc, ok := scannerFromRecord("Brother", nil, nil, []net.IP{net.ParseIP("fe80::1")}, 443, true)
	if !ok {
		t.Fatal("not ok")
	}
	if sc.UUID != "Brother" || sc.Name != "Brother" || sc.BaseURL != "https://[fe80::1]:443/eSCL" {
		t.Fatalf("got %+v", sc)
	}
}

func TestScannerFromRecordNeedsAddress(t *testing.T) {
	if _, ok := scannerFromRecord("x", nil, nil, nil, 80, false); ok {
		t.Fatal("record without address must be skipped")
	}
}
```

Create `agent/conn_test.go` (a fake server built with `coder/websocket` plays the backend):

```go
package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestWsURL(t *testing.T) {
	got, _ := wsURL("https://origami.example.org", "a b")
	if got != "wss://origami.example.org/api/agent/ws?token=a+b" {
		t.Fatalf("got %s", got)
	}
	got, _ = wsURL("http://localhost:8000/", "t")
	if got != "ws://localhost:8000/api/agent/ws?token=t" {
		t.Fatalf("got %s", got)
	}
}

func TestAgentSessionServesEsclRequest(t *testing.T) {
	scannerSrv, _ := fakeScanner(t)
	tokens := make(chan string, 4)
	results := make(chan []any, 1)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tokens <- r.URL.Query().Get("token")
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		ctx := r.Context()
		_ = writeJSON(ctx, c, map[string]any{"type": "welcome", "resume_token": "resume-1"})
		var got []any
		for len(got) < 2 { // hello + devices
			_, data, err := c.Read(ctx)
			if err != nil {
				return
			}
			var m map[string]any
			_ = json.Unmarshal(data, &m)
			got = append(got, m["type"])
		}
		_ = writeJSON(ctx, c, map[string]any{"type": "escl", "id": "0123456789abcdef",
			"scanner_uuid": "u1", "method": "GET", "path": "ScanJobs/7/NextDocument"})
		for {
			typ, data, err := c.Read(ctx)
			if err != nil {
				return
			}
			if typ == websocket.MessageBinary {
				got = append(got, string(data))
				continue
			}
			var m map[string]any
			_ = json.Unmarshal(data, &m)
			got = append(got, m["type"])
			if m["type"] == "end" {
				results <- got
				return
			}
		}
	}))
	defer srv.Close()

	sc := Scanner{UUID: "u1", Name: "HP", BaseURL: scannerSrv.URL + "/eSCL"}
	browse := func(ctx context.Context, d time.Duration) []Scanner { return []Scanner{sc} }
	a := NewAgent(srv.URL, "launch-1", browse)
	a.exec.Client = scannerSrv.Client()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go func() { _ = a.Run(ctx) }()

	select {
	case got := <-results:
		joined := strings.Join(toStrings(got), ",")
		want := "hello,devices,escl_response,0123456789abcdefJPEGDATA,end"
		if joined != want {
			t.Fatalf("got %s want %s", joined, want)
		}
	case <-ctx.Done():
		t.Fatal("timeout")
	}
	if <-tokens != "launch-1" {
		t.Fatal("first connect must use the launch token")
	}
	// after the server closes, the agent reconnects with the resume token
	select {
	case tok := <-tokens:
		if tok != "resume-1" {
			t.Fatalf("reconnect used %q", tok)
		}
	case <-ctx.Done():
		t.Fatal("no reconnect")
	}
}

func toStrings(xs []any) []string {
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i], _ = x.(string)
	}
	return out
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd agent && go test ./...`
Expected: FAIL with `undefined: scannerFromRecord`, `undefined: wsURL`, `undefined: NewAgent`

- [ ] **Step 4: Implement discovery**

Create `agent/discovery.go`:

```go
package main

import (
	"context"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/libp2p/zeroconf/v2"
)

// Browser finds eSCL scanners; tests replace it.
type Browser func(ctx context.Context, timeout time.Duration) []Scanner

func scannerFromRecord(instance string, txt []string, ipv4, ipv6 []net.IP, port int, secure bool) (Scanner, bool) {
	fields := map[string]string{}
	for _, kv := range txt {
		if k, v, ok := strings.Cut(kv, "="); ok {
			fields[strings.ToLower(k)] = v
		}
	}
	var host string
	switch {
	case len(ipv4) > 0:
		host = ipv4[0].String()
	case len(ipv6) > 0:
		host = "[" + ipv6[0].String() + "]"
	default:
		return Scanner{}, false
	}
	scheme := "http"
	if secure {
		scheme = "https"
	}
	root := strings.Trim(fields["rs"], "/")
	if root == "" {
		root = "eSCL"
	}
	uuid := fields["uuid"]
	if uuid == "" {
		uuid = instance
	}
	name := fields["ty"]
	if name == "" {
		name = instance
	}
	return Scanner{UUID: uuid, Name: name, BaseURL: fmt.Sprintf("%s://%s:%d/%s", scheme, host, port, root)}, true
}

func browseMDNS(ctx context.Context, timeout time.Duration) []Scanner {
	var (
		mu   sync.Mutex
		out  []Scanner
		seen = map[string]bool{}
		wg   sync.WaitGroup
	)
	for _, svc := range []string{"_uscan._tcp", "_uscans._tcp"} {
		secure := svc == "_uscans._tcp"
		entries := make(chan *zeroconf.ServiceEntry, 8)
		bctx, cancel := context.WithTimeout(ctx, timeout)
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-bctx.Done(): // do not rely on Browse closing the channel
					return
				case e, open := <-entries:
					if !open {
						return
					}
					sc, ok := scannerFromRecord(e.Instance, e.Text, e.AddrIPv4, e.AddrIPv6, e.Port, secure)
					mu.Lock()
					if ok && !seen[sc.UUID] {
						seen[sc.UUID] = true
						out = append(out, sc)
					}
					mu.Unlock()
				}
			}
		}()
		go func() {
			defer cancel()
			_ = zeroconf.Browse(bctx, svc, "local.", entries) // closes entries when bctx ends
		}()
	}
	wg.Wait()
	return out
}
```

- [ ] **Step 5: Implement the connection loop**

Create `agent/conn.go`:

```go
package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
)

const (
	IdleTimeout        = 30 * time.Minute
	ReconnectWindow    = 60 * time.Second
	PingInterval       = 30 * time.Second
	RediscoverInterval = 60 * time.Second
	browseTimeout      = 3 * time.Second
	retryDelay         = 3 * time.Second
)

var errIdle = errors.New("idle timeout")

type Agent struct {
	mu           sync.Mutex
	server       string
	token        string
	scanners     map[string]Scanner
	lastActivity time.Time
	browse       Browser
	exec         *Executor
	relaunch     chan struct{}
}

func NewAgent(server, token string, browse Browser) *Agent {
	a := &Agent{server: server, token: token, scanners: map[string]Scanner{},
		lastActivity: time.Now(), browse: browse, relaunch: make(chan struct{}, 1)}
	a.exec = &Executor{Client: newHTTPClient(), Lookup: a.lookup}
	return a
}

func (a *Agent) lookup(uuid string) (Scanner, bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	sc, ok := a.scanners[uuid]
	return sc, ok
}

// Relaunch switches to a new server/token (from a second launch) and reconnects.
func (a *Agent) Relaunch(server, token string) {
	a.mu.Lock()
	a.server, a.token, a.lastActivity = server, token, time.Now()
	a.mu.Unlock()
	select {
	case a.relaunch <- struct{}{}:
	default:
	}
}

func wsURL(server, token string) (string, error) {
	u, err := url.Parse(strings.TrimSuffix(server, "/"))
	if err != nil {
		return "", err
	}
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	case "http":
		u.Scheme = "ws"
	}
	u.Path += "/api/agent/ws"
	u.RawQuery = url.Values{"token": {token}}.Encode()
	return u.String(), nil
}

// Run serves until idle for IdleTimeout, or until no connection for ReconnectWindow.
func (a *Agent) Run(ctx context.Context) error {
	lostAt := time.Time{}
	for {
		connCtx, cancel := context.WithCancel(ctx)
		go func() {
			select {
			case <-a.relaunch:
				cancel()
			case <-connCtx.Done():
			}
		}()
		connected, err := a.session(connCtx)
		cancel()
		if errors.Is(err, errIdle) || ctx.Err() != nil {
			return err
		}
		if connected {
			lostAt = time.Now()
		} else if lostAt.IsZero() {
			lostAt = time.Now()
		}
		if time.Since(lostAt) > ReconnectWindow {
			return errors.New("server unreachable")
		}
		log.Printf("connection lost: %v; retrying", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(retryDelay):
		}
	}
}

func writeJSON(ctx context.Context, c *websocket.Conn, v any) error {
	data, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return c.Write(ctx, websocket.MessageText, data)
}

// session runs one WebSocket connection. connected reports whether the dial worked.
func (a *Agent) session(ctx context.Context) (connected bool, err error) {
	a.mu.Lock()
	target, err := wsURL(a.server, a.token)
	a.mu.Unlock()
	if err != nil {
		return false, err
	}
	c, _, err := websocket.Dial(ctx, target, nil)
	if err != nil {
		return false, err
	}
	defer c.CloseNow()
	c.SetReadLimit(1 << 20)
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)

	if err := writeJSON(ctx, c, helloMsg()); err != nil {
		return true, err
	}
	go a.discoverLoop(ctx, c)
	go a.pingLoop(ctx, c)
	go a.idleLoop(ctx, cancel)

	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			if cause := context.Cause(ctx); errors.Is(cause, errIdle) {
				_ = c.Close(websocket.StatusNormalClosure, "idle")
				return true, errIdle
			}
			return true, err
		}
		var msg Incoming
		if json.Unmarshal(data, &msg) != nil {
			continue
		}
		switch msg.Type {
		case "welcome":
			a.mu.Lock()
			a.token = msg.ResumeToken
			a.mu.Unlock()
		case "escl":
			a.mu.Lock()
			a.lastActivity = time.Now()
			a.mu.Unlock()
			go a.handle(ctx, c, msg)
		}
	}
}

func (a *Agent) handle(ctx context.Context, c *websocket.Conn, msg Incoming) {
	res := a.exec.Do(ctx, msg.ScannerUUID, msg.Method, msg.Path, msg.Body)
	if writeJSON(ctx, c, responseMsg(msg.ID, res)) != nil {
		return
	}
	for _, frame := range chunks(msg.ID, res.Body) {
		if c.Write(ctx, websocket.MessageBinary, frame) != nil {
			return
		}
	}
	_ = writeJSON(ctx, c, endMsg(msg.ID))
}

func (a *Agent) discoverLoop(ctx context.Context, c *websocket.Conn) {
	var last string
	for {
		found := a.browse(ctx, browseTimeout)
		devices := make([]Device, 0, len(found))
		next := map[string]Scanner{}
		for _, sc := range found {
			next[sc.UUID] = sc
			devices = append(devices, Device{UUID: sc.UUID, Name: sc.Name})
		}
		sort.Slice(devices, func(i, j int) bool { return devices[i].UUID < devices[j].UUID })
		a.mu.Lock()
		a.scanners = next
		a.mu.Unlock()
		key, _ := json.Marshal(devices)
		if string(key) != last {
			if writeJSON(ctx, c, devicesMsg(devices)) != nil {
				return
			}
			last = string(key)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(RediscoverInterval):
		}
	}
}

func (a *Agent) pingLoop(ctx context.Context, c *websocket.Conn) {
	t := time.NewTicker(PingInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			pctx, cancel := context.WithTimeout(ctx, 10*time.Second)
			err := c.Ping(pctx)
			cancel()
			if err != nil {
				_ = c.Close(websocket.StatusGoingAway, "ping failed")
				return
			}
		}
	}
}

func (a *Agent) idleLoop(ctx context.Context, cancel context.CancelCauseFunc) {
	t := time.NewTicker(time.Minute)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			a.mu.Lock()
			idle := time.Since(a.lastActivity) > IdleTimeout
			a.mu.Unlock()
			if idle {
				cancel(errIdle)
				return
			}
		}
	}
}
```

Note: `coder/websocket` needs a concurrent reader for `Ping` to receive the pong; `session` always has `c.Read` running, so pings work.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd agent && go vet ./... && go test -race ./...`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add agent/go.mod agent/go.sum agent/discovery.go agent/discovery_test.go agent/conn.go agent/conn_test.go
git commit -m "feat(agent): mDNS discovery and WebSocket session"
```

---

### Task 8: Go agent — launch URL, server pinning, single instance, registration

**Files:**
- Create: `agent/launch.go`, `agent/launch_test.go`, `agent/main.go`, `agent/register_linux.go`, `agent/register_windows.go`, `agent/register_darwin.go`, `agent/register_test.go`

**Interfaces:**
- Consumes: `NewAgent`, `Agent.Run`, `Agent.Relaunch`, `browseMDNS` (Task 7).
- Produces:
  - `type LaunchParams struct { Server, Token string }`, `func parseLaunchURL(raw string) (LaunchParams, error)`
  - `func checkPinned(path, server string) error` (writes the pin on first use; error `errForeignServer` if a different server is pinned)
  - `func pinPath() (string, error)` (`<user config dir>/origami-agent/server`)
  - `const HandoffAddr = "127.0.0.1:47811"`
  - `func listenSingleInstance() (net.Listener, error)`, `func handOff(raw string) error`, `func serveHandoffs(l net.Listener, onURL func(string))`
  - `func launch(raw string)` (shared entry for every OS)
  - `func register() (string, error)` per OS (returns a message for the user); Linux helper `desktopEntry(exe string) string`; Windows helper `windowsEntries(exe string) []regEntry` with `type regEntry struct { Key, Name, Value string }`
  - `func notify(msg string)` per OS

- [ ] **Step 1: Write the failing tests**

Create `agent/launch_test.go`:

```go
package main

import (
	"bufio"
	"errors"
	"net"
	"path/filepath"
	"testing"
)

func TestParseLaunchURL(t *testing.T) {
	p, err := parseLaunchURL("origami-agent://connect?server=https%3A%2F%2Forigami.example.org&token=abc")
	if err != nil || p.Server != "https://origami.example.org" || p.Token != "abc" {
		t.Fatalf("got %+v %v", p, err)
	}
	for _, bad := range []string{
		"origami-agent://other?server=https%3A%2F%2Fx&token=a",
		"origami-agent://connect?server=ftp%3A%2F%2Fx&token=a",
		"origami-agent://connect?server=http%3A%2F%2Fexample.org&token=a", // plain http only for localhost
		"origami-agent://connect?server=https%3A%2F%2Fx",
		"https://connect?server=https%3A%2F%2Fx&token=a",
	} {
		if _, err := parseLaunchURL(bad); err == nil {
			t.Errorf("%s: want error", bad)
		}
	}
	if _, err := parseLaunchURL("origami-agent://connect?server=http%3A%2F%2Flocalhost%3A8000&token=a"); err != nil {
		t.Errorf("localhost http must be allowed: %v", err)
	}
}

func TestCheckPinned(t *testing.T) {
	path := filepath.Join(t.TempDir(), "origami-agent", "server")
	if err := checkPinned(path, "https://a.example"); err != nil {
		t.Fatal(err)
	}
	if err := checkPinned(path, "https://a.example/"); err != nil {
		t.Fatalf("same server with slash: %v", err)
	}
	if err := checkPinned(path, "https://evil.example"); !errors.Is(err, errForeignServer) {
		t.Fatalf("want errForeignServer, got %v", err)
	}
}

func TestHandoff(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	got := make(chan string, 1)
	go serveHandoffs(l, func(u string) { got <- u })
	c, err := net.Dial("tcp", l.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	w := bufio.NewWriter(c)
	_, _ = w.WriteString("origami-agent://connect?x=1\n")
	_ = w.Flush()
	_ = c.Close()
	if u := <-got; u != "origami-agent://connect?x=1" {
		t.Fatalf("got %q", u)
	}
}
```

Create `agent/register_test.go`:

```go
package main

import (
	"strings"
	"testing"
)

func TestDesktopEntry(t *testing.T) {
	d := desktopEntry("/home/u/Downloads/origami-agent-linux-amd64")
	for _, want := range []string{
		"Exec=\"/home/u/Downloads/origami-agent-linux-amd64\" %u",
		"MimeType=x-scheme-handler/origami-agent;",
		"NoDisplay=true",
	} {
		if !strings.Contains(d, want) {
			t.Errorf("missing %q in\n%s", want, d)
		}
	}
}

func TestWindowsEntries(t *testing.T) {
	got := windowsEntries(`C:\Users\u\Downloads\origami-agent.exe`)
	want := []regEntry{
		{`Software\Classes\origami-agent`, "", "URL:Origami Agent"},
		{`Software\Classes\origami-agent`, "URL Protocol", ""},
		{`Software\Classes\origami-agent\shell\open\command`, "", `"C:\Users\u\Downloads\origami-agent.exe" "%1"`},
	}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("entry %d: got %+v want %+v", i, got[i], want[i])
		}
	}
}
```

`desktopEntry` and `windowsEntries` are pure and live in `launch.go` (no build tags) so both tests run on every OS.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd agent && go test ./...`
Expected: FAIL with `undefined: parseLaunchURL`, `undefined: desktopEntry`, ...

- [ ] **Step 3: Implement `launch.go`**

Create `agent/launch.go`:

```go
package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const HandoffAddr = "127.0.0.1:47811"

var errForeignServer = errors.New("this agent is paired with another Origami server; run it with --reset to change")

type LaunchParams struct{ Server, Token string }

type regEntry struct{ Key, Name, Value string }

func parseLaunchURL(raw string) (LaunchParams, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "origami-agent" || u.Host != "connect" {
		return LaunchParams{}, fmt.Errorf("not an origami-agent connect link")
	}
	q := u.Query()
	server, token := strings.TrimSuffix(q.Get("server"), "/"), q.Get("token")
	s, err := url.Parse(server)
	if err != nil || token == "" || s.Host == "" {
		return LaunchParams{}, fmt.Errorf("link is missing server or token")
	}
	local := s.Hostname() == "localhost" || s.Hostname() == "127.0.0.1"
	if s.Scheme != "https" && !(s.Scheme == "http" && local) {
		return LaunchParams{}, fmt.Errorf("server must use https")
	}
	return LaunchParams{Server: server, Token: token}, nil
}

func pinPath() (string, error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "origami-agent", "server"), nil
}

// checkPinned trusts the first server it sees and refuses any other afterwards.
func checkPinned(path, server string) error {
	server = strings.TrimSuffix(server, "/")
	data, err := os.ReadFile(path)
	if err == nil {
		if strings.TrimSpace(string(data)) != server {
			return errForeignServer
		}
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(server+"\n"), 0o600)
}

func listenSingleInstance() (net.Listener, error) { return net.Listen("tcp", HandoffAddr) }

func handOff(raw string) error {
	c, err := net.DialTimeout("tcp", HandoffAddr, 2*time.Second)
	if err != nil {
		return err
	}
	defer c.Close()
	_, err = fmt.Fprintln(c, raw)
	return err
}

func serveHandoffs(l net.Listener, onURL func(string)) {
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		go func(c net.Conn) {
			defer c.Close()
			_ = c.SetReadDeadline(time.Now().Add(5 * time.Second))
			if line, err := bufio.NewReader(c).ReadString('\n'); err == nil {
				onURL(strings.TrimSpace(line))
			}
		}(c)
	}
}

func validatedLaunch(raw string) (LaunchParams, error) {
	p, err := parseLaunchURL(raw)
	if err != nil {
		return p, err
	}
	path, err := pinPath()
	if err != nil {
		return p, err
	}
	return p, checkPinned(path, p.Server)
}

// launch is the shared entry for an origami-agent:// link on every OS.
func launch(raw string) {
	p, err := validatedLaunch(raw)
	if err != nil {
		notify("Origami Agent: " + err.Error())
		os.Exit(1)
	}
	l, err := listenSingleInstance()
	if err != nil {
		if handOff(raw) == nil {
			os.Exit(0) // the running agent takes over
		}
		notify("Origami Agent: cannot start: " + err.Error())
		os.Exit(1)
	}
	a := NewAgent(p.Server, p.Token, browseMDNS)
	go serveHandoffs(l, func(next string) {
		if q, err := validatedLaunch(next); err == nil {
			a.Relaunch(q.Server, q.Token)
		} else {
			log.Printf("ignored launch: %v", err)
		}
	})
	if err := a.Run(context.Background()); err != nil {
		log.Printf("agent stopped: %v", err)
	}
	os.Exit(0)
}

func reset() error {
	path, err := pinPath()
	if err != nil {
		return err
	}
	if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

func desktopEntry(exe string) string {
	return "[Desktop Entry]\n" +
		"Type=Application\n" +
		"Name=Origami Agent\n" +
		"Exec=\"" + exe + "\" %u\n" +
		"MimeType=x-scheme-handler/origami-agent;\n" +
		"NoDisplay=true\n" +
		"Terminal=false\n"
}

func windowsEntries(exe string) []regEntry {
	const base = `Software\Classes\origami-agent`
	return []regEntry{
		{base, "", "URL:Origami Agent"},
		{base, "URL Protocol", ""},
		{base + `\shell\open\command`, "", `"` + exe + `" "%1"`},
	}
}
```

- [ ] **Step 4: Implement entry point and registration**

Create `agent/main.go`:

```go
//go:build !darwin

package main

import (
	"fmt"
	"os"
	"strings"
)

func main() {
	args := os.Args[1:]
	switch {
	case len(args) == 1 && strings.HasPrefix(args[0], "origami-agent:"):
		launch(args[0])
	case len(args) == 1 && args[0] == "--version":
		fmt.Println(Version)
	case len(args) == 1 && args[0] == "--reset":
		if err := reset(); err != nil {
			notify("Origami Agent: " + err.Error())
			os.Exit(1)
		}
		notify("Origami Agent: server pairing cleared.")
	default: // double-click or --register
		msg, err := register()
		if err != nil {
			notify("Origami Agent: registration failed: " + err.Error())
			os.Exit(1)
		}
		notify(msg)
	}
}
```

Create `agent/register_linux.go`:

```go
//go:build linux

package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func register() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	if exe, err = filepath.EvalSymlinks(exe); err != nil {
		return "", err
	}
	dataHome := os.Getenv("XDG_DATA_HOME")
	if dataHome == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dataHome = filepath.Join(home, ".local", "share")
	}
	appDir := filepath.Join(dataHome, "applications")
	if err := os.MkdirAll(appDir, 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(filepath.Join(appDir, "origami-agent.desktop"), []byte(desktopEntry(exe)), 0o644); err != nil {
		return "", err
	}
	if out, err := exec.Command("xdg-mime", "default", "origami-agent.desktop", "x-scheme-handler/origami-agent").CombinedOutput(); err != nil {
		return "", fmt.Errorf("xdg-mime: %v: %s", err, out)
	}
	_ = exec.Command("update-desktop-database", appDir).Run() // optional on most desktops
	return "Origami Agent is installed. Keep this file where it is (" + exe + "), then go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	fmt.Println(msg)
	_ = exec.Command("notify-send", "Origami Agent", msg).Run()
}
```

Create `agent/register_windows.go`:

```go
//go:build windows

package main

import (
	"os"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func register() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	for _, e := range windowsEntries(exe) {
		k, _, err := registry.CreateKey(registry.CURRENT_USER, e.Key, registry.SET_VALUE)
		if err != nil {
			return "", err
		}
		err = k.SetStringValue(e.Name, e.Value)
		k.Close()
		if err != nil {
			return "", err
		}
	}
	return "Origami Agent is installed. Keep this file where it is, then go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	title, _ := windows.UTF16PtrFromString("Origami Agent")
	text, _ := windows.UTF16PtrFromString(msg)
	_, _ = windows.MessageBox(0, text, title, 0x40) // MB_OK | MB_ICONINFORMATION
}
```

Create `agent/register_darwin.go`:

```go
//go:build darwin

package main

import (
	"fmt"
	"os/exec"
)

// On macOS LaunchServices registers the scheme from Info.plist when the app is opened once.
func register() (string, error) {
	return "Origami Agent is installed. Go back to Origami and search for local scanners.", nil
}

func notify(msg string) {
	fmt.Println(msg)
	script := fmt.Sprintf("display notification %q with title %q", msg, "Origami Agent")
	_ = exec.Command("osascript", "-e", script).Run()
}
```

Then add `golang.org/x/sys`: `cd agent && go get golang.org/x/sys@v0.25.0 && go mod tidy`.

- [ ] **Step 5: Run tests and cross-compile checks**

Run:
```bash
cd agent && go vet ./... && go test ./...
GOOS=windows GOARCH=amd64 go vet ./...
GOOS=linux GOARCH=arm64 go build -o /dev/null .
```
Expected: tests PASS; Windows vet and arm64 build succeed. (`main_darwin.go` comes in Task 9, so do not build darwin yet.)

- [ ] **Step 6: Manual check on Linux**

```bash
cd agent && go build -o /tmp/origami-agent . && /tmp/origami-agent --register
xdg-mime query default x-scheme-handler/origami-agent
```
Expected: prints `origami-agent.desktop`.

- [ ] **Step 7: Commit**

```bash
git add agent/
git commit -m "feat(agent): launch links, server pinning, single instance and URL handler registration"
```

---

### Task 9: Go agent — macOS URL events, build script

**Files:**
- Create: `agent/main_darwin.go`, `agent/url_darwin.m`, `agent/macos/Info.plist`, `agent/build.sh`
- Modify: `.gitignore` (add `agent/dist/`)

**Interfaces:**
- Consumes: `launch`, `Agent.Relaunch` via the hand-off listener (Task 8).
- Produces: `agent/build.sh` writing `agent/dist/origami-agent-windows-amd64.exe`, `agent/dist/origami-agent-linux-amd64`, `agent/dist/origami-agent-linux-arm64`, and on macOS `agent/dist/origami-agent-darwin-arm64.zip`, `agent/dist/origami-agent-darwin-amd64.zip` (names match `AGENT_FILES` in `backend/app/api/agent.py`).

- [ ] **Step 1: Write the macOS entry**

Create `agent/url_darwin.m`:

```objc
#import <Cocoa/Cocoa.h>
#include "_cgo_export.h"

@interface OrigamiURLHandler : NSObject
@end

@implementation OrigamiURLHandler
- (void)handle:(NSAppleEventDescriptor *)event withReply:(NSAppleEventDescriptor *)reply {
    NSString *url = [[event paramDescriptorForKeyword:keyDirectObject] stringValue];
    if (url != nil) {
        goHandleURL((char *)[url UTF8String]);
    }
}
@end

void runApp(void) {
    [NSApplication sharedApplication];
    OrigamiURLHandler *handler = [OrigamiURLHandler new];
    [[NSAppleEventManager sharedAppleEventManager]
        setEventHandler:handler
            andSelector:@selector(handle:withReply:)
          forEventClass:kInternetEventClass
             andEventID:kAEGetURL];
    [NSApp run];
}
```

Create `agent/main_darwin.go`:

```go
//go:build darwin

package main

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework Cocoa
void runApp(void);
*/
import "C"

import (
	"fmt"
	"os"
	"runtime"
	"strings"
	"time"
)

var urls = make(chan string, 4)

func init() { runtime.LockOSThread() } // Cocoa must run on the main thread

//export goHandleURL
func goHandleURL(u *C.char) { urls <- C.GoString(u) }

func main() {
	args := os.Args[1:]
	if len(args) == 1 && args[0] == "--version" {
		fmt.Println(Version)
		return
	}
	if len(args) == 1 && args[0] == "--reset" {
		if err := reset(); err != nil {
			notify("Origami Agent: " + err.Error())
			os.Exit(1)
		}
		notify("Origami Agent: server pairing cleared.")
		return
	}
	go func() {
		select {
		case u := <-urls:
			go func() { // later links reach this running app as Apple Events
				for next := range urls {
					_ = handOff(next)
				}
			}()
			if strings.HasPrefix(u, "origami-agent:") {
				launch(u) // exits the process when the agent stops
			}
			os.Exit(0)
		case <-time.After(3 * time.Second): // opened by double-click: registration only
			msg, _ := register()
			notify(msg)
			os.Exit(0)
		}
	}()
	C.runApp()
}
```

Create `agent/macos/Info.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Origami Agent</string>
  <key>CFBundleIdentifier</key><string>uk.yabzec.origami.agent</string>
  <key>CFBundleExecutable</key><string>origami-agent</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>Origami Agent</string>
      <key>CFBundleURLSchemes</key><array><string>origami-agent</string></array>
    </dict>
  </array>
</dict>
</plist>
```

- [ ] **Step 2: Write the build script**

Create `agent/build.sh`:

```bash
#!/usr/bin/env bash
# Builds the client scanner agent into agent/dist/ (served by /api/agent/download).
# Windows and Linux cross-compile anywhere; the macOS build needs a Mac (cgo + Cocoa).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p dist

build() { # goos goarch output [extra ldflags]
  echo "building $3"
  CGO_ENABLED=0 GOOS="$1" GOARCH="$2" go build -trimpath -ldflags "-s -w ${4:-}" -o "dist/$3" .
}

build windows amd64 origami-agent-windows-amd64.exe "-H=windowsgui"
build linux amd64 origami-agent-linux-amd64
build linux arm64 origami-agent-linux-arm64

if [[ "$(uname -s)" == "Darwin" ]]; then
  for arch in arm64 amd64; do
    app="dist/mac-$arch/Origami Agent.app"
    rm -rf "dist/mac-$arch"
    mkdir -p "$app/Contents/MacOS"
    cp macos/Info.plist "$app/Contents/Info.plist"
    echo "building origami-agent-darwin-$arch.zip"
    CGO_ENABLED=1 GOOS=darwin GOARCH="$arch" go build -trimpath -ldflags "-s -w" -o "$app/Contents/MacOS/origami-agent" .
    (cd "dist/mac-$arch" && rm -f "../origami-agent-darwin-$arch.zip" && zip -qr "../origami-agent-darwin-$arch.zip" "Origami Agent.app")
    rm -rf "dist/mac-$arch"
  done
else
  echo "skipping macOS: build on a Mac to produce origami-agent-darwin-*.zip"
fi
```

Run `chmod +x agent/build.sh` and add `agent/dist/` to the repo-root `.gitignore`.

- [ ] **Step 3: Build**

Run: `agent/build.sh && ls -l agent/dist`
Expected: three files on Linux (`origami-agent-windows-amd64.exe`, `origami-agent-linux-amd64`, `origami-agent-linux-arm64`) and the "skipping macOS" line.

- [ ] **Step 4: Commit**

```bash
git add agent/main_darwin.go agent/url_darwin.m agent/macos/Info.plist agent/build.sh .gitignore
git commit -m "feat(agent): macOS URL events and cross-platform build script"
```

---

### Task 10: Frontend — local scan logic

**Files:**
- Create: `frontend/src/lib/localScan.ts`, `frontend/src/lib/localScan.test.ts`, `frontend/src/lib/agentLaunch.ts`
- Modify: `frontend/src/lib/types.ts` (`ScanDevicesResponse`)

**Interfaces:**
- Produces (`lib/localScan.ts`):
  - `CLIENT_ID_KEY = "origami.clientId"`, `AGENT_INSTALLED_KEY = "origami.agentInstalled"`
  - `getClientId(storage?: Storage): string` (falls back to a module-level id when storage throws)
  - `isAgentInstalled(storage?: Storage): boolean`, `markAgentInstalled(storage?: Storage): void`
  - `type SearchPhase = "idle" | "install" | "searching" | "found" | "none" | "unresponsive"`
  - `interface SearchState { phase: SearchPhase; startedAt: number | null; found: number }`
  - `initialSearch: SearchState`
  - `startSearch(installed: boolean, now: number): SearchState`
  - `searchTick(state: SearchState, input: { now: number; agentConnected: boolean; localCount: number }): SearchState`
  - `SEARCH_WINDOW_MS = 15000`, `AGENT_WAIT_MS = 10000`
  - `isLocalDevice(id: string): boolean`, `groupDevices(devices: ScanDevice[]): { server: ScanDevice[]; local: ScanDevice[] }`
  - `type AgentPlatform = "windows-amd64" | "darwin-arm64" | "darwin-amd64" | "linux-amd64" | "linux-arm64"`
  - `AGENT_PLATFORMS: { id: AgentPlatform; label: string }[]`
  - `detectPlatform(userAgent: string): AgentPlatform | null`
  - `downloadUrl(platform: AgentPlatform, token: string | null): string`
- Produces (`lib/agentLaunch.ts`): `openAgentUrl(url: string): void`
- Produces (`lib/types.ts`): `interface ScanDevicesResponse { devices: ScanDevice[]; default: string | null; agent_connected: boolean }`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/localScan.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  AGENT_INSTALLED_KEY,
  CLIENT_ID_KEY,
  detectPlatform,
  downloadUrl,
  getClientId,
  groupDevices,
  initialSearch,
  isAgentInstalled,
  markAgentInstalled,
  searchTick,
  startSearch,
} from "./localScan";

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
    clear: () => data.clear(),
    key: (i) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}

const throwing = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
} as unknown as Storage;

describe("client id and install flag", () => {
  it("creates the client id once and keeps it", () => {
    const s = memoryStorage();
    const id = getClientId(s);
    expect(id).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(getClientId(s)).toBe(id);
    expect(s.getItem(CLIENT_ID_KEY)).toBe(id);
  });

  it("works when storage throws", () => {
    const id = getClientId(throwing);
    expect(id).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(getClientId(throwing)).toBe(id); // same id for the page lifetime
    expect(isAgentInstalled(throwing)).toBe(false);
    expect(() => markAgentInstalled(throwing)).not.toThrow();
  });

  it("stores the install flag", () => {
    const s = memoryStorage();
    expect(isAgentInstalled(s)).toBe(false);
    markAgentInstalled(s);
    expect(isAgentInstalled(s)).toBe(true);
    expect(s.getItem(AGENT_INSTALLED_KEY)).toBe("1");
  });
});

describe("search state machine", () => {
  it("asks to install when the flag is missing", () => {
    expect(startSearch(false, 0).phase).toBe("install");
  });

  it("finds scanners", () => {
    const s = startSearch(true, 0);
    expect(s.phase).toBe("searching");
    expect(searchTick(s, { now: 2000, agentConnected: true, localCount: 0 }).phase).toBe("searching");
    const found = searchTick(s, { now: 4000, agentConnected: true, localCount: 2 });
    expect(found).toMatchObject({ phase: "found", found: 2 });
  });

  it("reports none after the window when the agent is connected", () => {
    const s = startSearch(true, 0);
    expect(searchTick(s, { now: 14999, agentConnected: true, localCount: 0 }).phase).toBe("searching");
    expect(searchTick(s, { now: 15000, agentConnected: true, localCount: 0 }).phase).toBe("none");
  });

  it("reports an unresponsive agent after 10 s", () => {
    const s = startSearch(true, 0);
    expect(searchTick(s, { now: 9999, agentConnected: false, localCount: 0 }).phase).toBe("searching");
    expect(searchTick(s, { now: 10000, agentConnected: false, localCount: 0 }).phase).toBe("unresponsive");
  });

  it("ignores ticks when not searching", () => {
    expect(searchTick(initialSearch, { now: 99999, agentConnected: false, localCount: 3 })).toBe(initialSearch);
  });
});

describe("devices and platforms", () => {
  it("groups server and local devices", () => {
    const g = groupDevices([
      { id: "airscan:e0:HP", name: "HP" },
      { id: "agent:abcdefgh:u1", name: "Brother" },
    ]);
    expect(g.server.map((d) => d.name)).toEqual(["HP"]);
    expect(g.local.map((d) => d.name)).toEqual(["Brother"]);
  });

  it("detects the platform from the user agent", () => {
    expect(detectPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows-amd64");
    expect(detectPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")).toBe("darwin-arm64");
    expect(detectPlatform("Mozilla/5.0 (X11; Linux x86_64)")).toBe("linux-amd64");
    expect(detectPlatform("Mozilla/5.0 (X11; Linux aarch64)")).toBe("linux-arm64");
    expect(detectPlatform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)")).toBeNull();
    expect(detectPlatform("Mozilla/5.0 (Linux; Android 14)")).toBeNull();
  });

  it("builds the download url", () => {
    expect(downloadUrl("linux-amd64", "t k")).toBe("/api/agent/download/linux-amd64?token=t%20k");
    expect(downloadUrl("linux-amd64", null)).toBe("/api/agent/download/linux-amd64");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/localScan.test.ts`
Expected: FAIL with `Failed to resolve import "./localScan"`

- [ ] **Step 3: Implement**

Create `frontend/src/lib/localScan.ts`:

```ts
import type { ScanDevice } from "./types";

export const CLIENT_ID_KEY = "origami.clientId";
export const AGENT_INSTALLED_KEY = "origami.agentInstalled";
export const SEARCH_WINDOW_MS = 15_000;
export const AGENT_WAIT_MS = 10_000;

function defaultStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

let fallbackClientId: string | null = null;

function newClientId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Stable id for this browser; lives for the page when storage is unavailable. */
export function getClientId(storage: Storage | undefined = defaultStorage()): string {
  try {
    const saved = storage?.getItem(CLIENT_ID_KEY);
    if (saved) return saved;
    const id = fallbackClientId ?? newClientId();
    storage?.setItem(CLIENT_ID_KEY, id);
    fallbackClientId = id;
    return id;
  } catch {
    fallbackClientId ??= newClientId();
    return fallbackClientId;
  }
}

export function isAgentInstalled(storage: Storage | undefined = defaultStorage()): boolean {
  try {
    return storage?.getItem(AGENT_INSTALLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function markAgentInstalled(storage: Storage | undefined = defaultStorage()): void {
  try {
    storage?.setItem(AGENT_INSTALLED_KEY, "1");
  } catch {
    // storage blocked: the install panel shows again next time
  }
}

export type SearchPhase = "idle" | "install" | "searching" | "found" | "none" | "unresponsive";

export interface SearchState {
  phase: SearchPhase;
  startedAt: number | null;
  found: number;
}

export const initialSearch: SearchState = { phase: "idle", startedAt: null, found: 0 };

export function startSearch(installed: boolean, now: number): SearchState {
  return installed ? { phase: "searching", startedAt: now, found: 0 } : { phase: "install", startedAt: null, found: 0 };
}

export function searchTick(
  state: SearchState,
  input: { now: number; agentConnected: boolean; localCount: number },
): SearchState {
  if (state.phase !== "searching" || state.startedAt === null) return state;
  const elapsed = input.now - state.startedAt;
  if (input.localCount > 0) return { ...state, phase: "found", found: input.localCount };
  if (!input.agentConnected && elapsed >= AGENT_WAIT_MS) return { ...state, phase: "unresponsive" };
  if (input.agentConnected && elapsed >= SEARCH_WINDOW_MS) return { ...state, phase: "none" };
  return state;
}

export function isLocalDevice(id: string): boolean {
  return id.startsWith("agent:");
}

export function groupDevices(devices: ScanDevice[]): { server: ScanDevice[]; local: ScanDevice[] } {
  return {
    server: devices.filter((d) => !isLocalDevice(d.id)),
    local: devices.filter((d) => isLocalDevice(d.id)),
  };
}

export type AgentPlatform = "windows-amd64" | "darwin-arm64" | "darwin-amd64" | "linux-amd64" | "linux-arm64";

export const AGENT_PLATFORMS: { id: AgentPlatform; label: string }[] = [
  { id: "windows-amd64", label: "Windows" },
  { id: "darwin-arm64", label: "macOS (Apple silicon)" },
  { id: "darwin-amd64", label: "macOS (Intel)" },
  { id: "linux-amd64", label: "Linux (x86-64)" },
  { id: "linux-arm64", label: "Linux (ARM64)" },
];

/** Best guess from the user agent. Macs default to Apple silicon: browsers report "Intel" on both. */
export function detectPlatform(userAgent: string): AgentPlatform | null {
  const ua = userAgent.toLowerCase();
  if (/iphone|ipad|android/.test(ua)) return null;
  if (ua.includes("windows")) return "windows-amd64";
  if (ua.includes("macintosh") || ua.includes("mac os x")) return "darwin-arm64";
  if (ua.includes("linux")) return /aarch64|arm64/.test(ua) ? "linux-arm64" : "linux-amd64";
  return null;
}

export function downloadUrl(platform: AgentPlatform, token: string | null): string {
  const base = `/api/agent/download/${platform}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}
```

Create `frontend/src/lib/agentLaunch.ts`:

```ts
/** Hands an origami-agent:// link to the OS. The page does not navigate for external schemes. */
export function openAgentUrl(url: string): void {
  window.location.href = url;
}
```

In `frontend/src/lib/types.ts`, after `ScanDevice`, add:

```ts
export interface ScanDevicesResponse {
  devices: ScanDevice[];
  default: string | null;
  agent_connected: boolean;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/localScan.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/localScan.ts frontend/src/lib/localScan.test.ts frontend/src/lib/agentLaunch.ts frontend/src/lib/types.ts
git commit -m "feat: local scanner search state, client id and agent platforms"
```

---

### Task 11: Frontend — device listbox, install panel, scan page wiring

**Files:**
- Create: `frontend/src/hooks/useLocalScanSearch.ts`, `frontend/src/components/scan/DeviceListbox.tsx`, `frontend/src/components/scan/DeviceListbox.test.tsx`, `frontend/src/components/scan/AgentInstallPanel.tsx`
- Modify: `frontend/src/components/scan/ScanToolbar.tsx`, `frontend/src/pages/ScanPage.tsx:32-44`
- Delete: `frontend/src/lib/scanDevices.ts`, `frontend/src/lib/scanDevices.test.ts`

**Interfaces:**
- Consumes: everything from Task 10; `api`, `getToken` from `@/lib/api`.
- Produces:
  - `useLocalScanSearch(clientId: string, input: { agentConnected: boolean; localCount: number }) => { search: SearchState; start(): void; confirmInstalled(): void; prefetch(): void }`
  - `DeviceListbox` props: `{ devices: ScanDevice[]; value: string | null; onChange(id: string): void; search: SearchState; onSearch(): void; onOpen(): void; installPanel: ReactNode }`
  - `AgentInstallPanel` props: `{ onInstalled(): void }`
  - `ScanToolbar` props become `{ status; devices; device; onDeviceChange; search; onSearch; onOpen; onInstalled }`

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/components/scan/DeviceListbox.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { initialSearch, type SearchState } from "@/lib/localScan";
import type { ScanDevice } from "@/lib/types";
import { DeviceListbox } from "./DeviceListbox";

const server: ScanDevice = { id: "fake:0", name: "Server Epson" };
const local: ScanDevice = { id: "agent:abcdefgh:u1", name: "Home HP" };

function setup(devices: ScanDevice[], search: SearchState = initialSearch) {
  const onChange = vi.fn();
  const onSearch = vi.fn();
  const onOpen = vi.fn();
  const view = render(
    <DeviceListbox
      devices={devices}
      value="fake:0"
      onChange={onChange}
      search={search}
      onSearch={onSearch}
      onOpen={onOpen}
      installPanel={<p>Install panel</p>}
    />,
  );
  return { onChange, onSearch, onOpen, view };
}

describe("DeviceListbox", () => {
  it("shows the selected device and opens with groups", async () => {
    const { onOpen } = setup([server, local]);
    const button = screen.getByRole("button", { name: /scanner/i });
    expect(button).toHaveTextContent("Server Epson");
    await userEvent.click(button);
    expect(onOpen).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByText("Server")).toBeInTheDocument();
    expect(screen.getByText("This computer's network")).toBeInTheDocument();
  });

  it("selecting a device closes the list", async () => {
    const { onChange } = setup([server, local]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.click(screen.getByRole("option", { name: "Home HP" }));
    expect(onChange).toHaveBeenCalledWith("agent:abcdefgh:u1");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("search keeps the list open and new devices appear in place", async () => {
    const { onSearch, view } = setup([server]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.click(screen.getByRole("option", { name: /search local scanners/i }));
    expect(onSearch).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    view.rerender(
      <DeviceListbox
        devices={[server, local]}
        value="fake:0"
        onChange={() => {}}
        search={{ phase: "found", startedAt: 0, found: 1 }}
        onSearch={onSearch}
        onOpen={() => {}}
        installPanel={<p>Install panel</p>}
      />,
    );
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Home HP" })).toBeInTheDocument();
    expect(screen.getByText("1 found")).toBeInTheDocument();
  });

  it("shows the install panel inside the open list", async () => {
    setup([server], { phase: "install", startedAt: null, found: 0 });
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    expect(screen.getByText("Install panel")).toBeInTheDocument();
  });

  it("keyboard: arrows move, Enter on search keeps it open, Escape closes", async () => {
    const { onSearch, onChange } = setup([server, local]);
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(onSearch).toHaveBeenCalled();
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowUp}{Enter}");
    expect(onChange).toHaveBeenCalledWith("agent:abcdefgh:u1");
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("shows status text for unresponsive and none", async () => {
    const { view } = setup([server], { phase: "unresponsive", startedAt: 0, found: 0 });
    await userEvent.click(screen.getByRole("button", { name: /scanner/i }));
    expect(screen.getByText(/agent not responding/i)).toBeInTheDocument();
    view.rerender(
      <DeviceListbox devices={[server]} value="fake:0" onChange={() => {}}
        search={{ phase: "none", startedAt: 0, found: 0 }} onSearch={() => {}} onOpen={() => {}}
        installPanel={null} />,
    );
    expect(screen.getByText(/no scanners found/i)).toBeInTheDocument();
  });
});
```

Keyboard order in the test: the active index starts at the selected option (`fake:0`, index 0). Options in order: `fake:0`, `agent:...:u1`, search item. Two `ArrowDown` reach the search item; `ArrowUp` goes back to `Home HP`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/scan/DeviceListbox.test.tsx`
Expected: FAIL with `Failed to resolve import "./DeviceListbox"`

- [ ] **Step 3: Implement `DeviceListbox`**

Create `frontend/src/components/scan/DeviceListbox.tsx`:

```tsx
import { useEffect, useRef, useState, type ReactNode } from "react";
import { groupDevices, type SearchState } from "@/lib/localScan";
import type { ScanDevice } from "@/lib/types";

const SEARCH_ID = "__search__";

function searchLabel(search: SearchState): string | null {
  switch (search.phase) {
    case "searching":
      return "Searching…";
    case "found":
      return `${search.found} found`;
    case "none":
      return "No scanners found";
    case "unresponsive":
      return "Agent not responding";
    default:
      return null;
  }
}

export function DeviceListbox({
  devices,
  value,
  onChange,
  search,
  onSearch,
  onOpen,
  installPanel,
}: {
  devices: ScanDevice[];
  value: string | null;
  onChange: (id: string) => void;
  search: SearchState;
  onSearch: () => void;
  onOpen: () => void;
  installPanel: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const { server, local } = groupDevices(devices);
  const ids = [...server.map((d) => d.id), ...local.map((d) => d.id), SEARCH_ID];
  const selected = devices.find((d) => d.id === value);

  const openList = () => {
    setActive(Math.max(0, ids.indexOf(value ?? "")));
    setOpen(true);
    onOpen();
  };

  const choose = (id: string) => {
    if (id === SEARCH_ID) {
      onSearch(); // keeps the list open
      return;
    }
    onChange(id);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, ids.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        choose(ids[active]);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }); // re-bind every render: handlers read the current ids/active

  const option = (d: ScanDevice) => {
    const index = ids.indexOf(d.id);
    return (
      <li
        key={d.id}
        role="option"
        aria-selected={d.id === value}
        onMouseEnter={() => setActive(index)}
        onClick={() => choose(d.id)}
        className={`cursor-pointer rounded px-2 py-1.5 ${index === active ? "bg-zinc-100" : ""} ${d.id === value ? "font-medium" : ""}`}
      >
        {d.name}
      </li>
    );
  };

  const status = searchLabel(search);
  const searchIndex = ids.length - 1;

  return (
    <div ref={rootRef} className="relative w-64">
      <button
        type="button"
        aria-label={`Scanner: ${selected?.name ?? "none selected"}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openList())}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm"
      >
        <span className="truncate">{selected?.name ?? "No scanner selected"}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div className="absolute top-full right-0 z-20 mt-1 w-80 rounded-md border border-zinc-200 bg-white p-1 text-sm shadow-lg">
          <ul role="listbox" aria-label="Scanners">
            {server.length > 0 && (
              <li role="presentation" className="px-2 pt-1 text-xs font-semibold text-zinc-500">
                Server
              </li>
            )}
            {server.map(option)}
            {local.length > 0 && (
              <li role="presentation" className="px-2 pt-2 text-xs font-semibold text-zinc-500">
                This computer's network
              </li>
            )}
            {local.map(option)}
            <li
              role="option"
              aria-selected={false}
              onMouseEnter={() => setActive(searchIndex)}
              onClick={() => choose(SEARCH_ID)}
              className={`mt-1 flex cursor-pointer items-center justify-between rounded border-t border-zinc-100 px-2 py-1.5 text-brand-700 ${active === searchIndex ? "bg-zinc-100" : ""}`}
            >
              <span>Search local scanners</span>
              {status && (
                <span className="text-xs text-zinc-500">
                  {search.phase === "searching" && <span aria-hidden="true" className="mr-1 inline-block animate-spin">◌</span>}
                  {status}
                </span>
              )}
            </li>
          </ul>
          {(search.phase === "install" || search.phase === "unresponsive") && installPanel}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the component test**

Run: `cd frontend && npx vitest run src/components/scan/DeviceListbox.test.tsx`
Expected: PASS

- [ ] **Step 5: Implement the install panel**

Create `frontend/src/components/scan/AgentInstallPanel.tsx`:

```tsx
import { Button } from "@/components/ui/button";
import { getToken } from "@/lib/api";
import { AGENT_PLATFORMS, detectPlatform, downloadUrl, type AgentPlatform } from "@/lib/localScan";

const FIRST_RUN: Record<string, string> = {
  windows: "Run the file once. If Windows shows “Windows protected your PC”, choose More info → Run anyway. Allow it on private networks if the firewall asks.",
  darwin: "Unzip, move Origami Agent to Applications, then right-click it → Open once.",
  linux: "Make it executable (chmod +x origami-agent-linux-*) and run it once.",
};

export function AgentInstallPanel({ onInstalled }: { onInstalled: () => void }) {
  const detected = detectPlatform(navigator.userAgent);
  const token = getToken();
  const others = AGENT_PLATFORMS.filter((p) => p.id !== detected);
  const label = (id: AgentPlatform) => AGENT_PLATFORMS.find((p) => p.id === id)?.label ?? id;

  return (
    <div className="mt-1 space-y-2 border-t border-zinc-100 p-2 text-xs text-zinc-600">
      <p>To use a scanner on this computer's network, install the Origami Agent once.</p>
      {detected && (
        <>
          <a
            href={downloadUrl(detected, token)}
            className="inline-block rounded-md bg-brand-700 px-3 py-1.5 font-medium text-white hover:bg-brand-800"
          >
            Download for {label(detected)}
          </a>
          <p>{FIRST_RUN[detected.split("-")[0]]}</p>
        </>
      )}
      <p>
        Other systems:{" "}
        {others.map((p, i) => (
          <span key={p.id}>
            {i > 0 && " · "}
            <a className="underline" href={downloadUrl(p.id, token)}>
              {p.label}
            </a>
          </span>
        ))}
      </p>
      <Button type="button" size="sm" onClick={onInstalled}>
        Installed, search now
      </Button>
    </div>
  );
}
```

`Button` already supports `size="sm"` (`components/ui/button.tsx`). The brand palette has only 200/300/500/700/800 shades, so highlights use `bg-zinc-100`.

- [ ] **Step 6: Implement the search hook**

Create `frontend/src/hooks/useLocalScanSearch.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { openAgentUrl } from "@/lib/agentLaunch";
import {
  initialSearch,
  isAgentInstalled,
  markAgentInstalled,
  searchTick,
  startSearch,
  type SearchState,
} from "@/lib/localScan";

const PREFETCH_MAX_AGE_MS = 90_000; // launch tokens live 120 s on the server

export function useLocalScanSearch(clientId: string, input: { agentConnected: boolean; localCount: number }) {
  const [search, setSearch] = useState<SearchState>(initialSearch);
  const launchUrl = useRef<{ url: string; at: number } | null>(null);

  const fetchUrl = useCallback(async () => {
    const { url } = await api.post<{ url: string }>("/api/agent/launch", { client_id: clientId });
    launchUrl.current = { url, at: Date.now() };
    return url;
  }, [clientId]);

  const prefetch = useCallback(() => {
    if (isAgentInstalled()) fetchUrl().catch(() => {});
  }, [fetchUrl]);

  const launch = useCallback(() => {
    const cached = launchUrl.current;
    launchUrl.current = null; // single use
    setSearch(startSearch(true, Date.now()));
    if (cached && Date.now() - cached.at < PREFETCH_MAX_AGE_MS) {
      openAgentUrl(cached.url); // synchronous: keeps the click's user activation
      return;
    }
    fetchUrl()
      .then((url) => {
        launchUrl.current = null;
        openAgentUrl(url);
      })
      .catch(() => setSearch({ phase: "unresponsive", startedAt: Date.now(), found: 0 }));
  }, [fetchUrl]);

  const start = useCallback(() => {
    if (input.agentConnected) {
      setSearch(startSearch(true, Date.now())); // agent already running: just poll again
      return;
    }
    if (!isAgentInstalled()) {
      setSearch(startSearch(false, Date.now()));
      return;
    }
    launch();
  }, [input.agentConnected, launch]);

  const confirmInstalled = launch;

  useEffect(() => {
    if (input.agentConnected) markAgentInstalled();
  }, [input.agentConnected]);

  useEffect(() => {
    if (search.phase !== "searching") return;
    const id = setInterval(() => setSearch((s) => searchTick(s, { now: Date.now(), ...input })), 500);
    setSearch((s) => searchTick(s, { now: Date.now(), ...input }));
    return () => clearInterval(id);
  }, [search.phase, input.agentConnected, input.localCount]); // eslint-disable-line react-hooks/exhaustive-deps

  return { search, start, confirmInstalled, prefetch };
}
```

- [ ] **Step 7: Wire the toolbar and page**

Replace `frontend/src/components/scan/ScanToolbar.tsx` with:

```tsx
import { AgentInstallPanel } from "@/components/scan/AgentInstallPanel";
import { DeviceListbox } from "@/components/scan/DeviceListbox";
import type { SearchState } from "@/lib/localScan";
import type { ScanDevice, ScanStatus } from "@/lib/types";

function StatusPill({ status }: { status: ScanStatus | undefined }) {
  if (!status) return <span className="rounded-full bg-zinc-100 px-3 py-1 text-zinc-500">Checking scanner…</span>;
  if (!status.available)
    return <span className="rounded-full bg-red-50 px-3 py-1 text-red-700">● Scanner offline</span>;
  return (
    <span className="rounded-full bg-green-50 px-3 py-1 text-green-700">
      ● Scanner ready{status.busy ? " (busy)" : ""}
    </span>
  );
}

export function ScanToolbar({
  status,
  devices,
  device,
  onDeviceChange,
  search,
  onSearch,
  onOpen,
  onInstalled,
}: {
  status: ScanStatus | undefined;
  devices: ScanDevice[];
  device: string | null;
  onDeviceChange: (device: string | null) => void;
  search: SearchState;
  onSearch: () => void;
  onOpen: () => void;
  onInstalled: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
      <StatusPill status={status} />
      <DeviceListbox
        devices={devices}
        value={device}
        onChange={onDeviceChange}
        search={search}
        onSearch={onSearch}
        onOpen={onOpen}
        installPanel={<AgentInstallPanel onInstalled={onInstalled} />}
      />
    </div>
  );
}
```

In `frontend/src/pages/ScanPage.tsx`:

1. Imports: add `import { useMemo } from "react"` to the existing React import list, `import { useLocalScanSearch } from "@/hooks/useLocalScanSearch";`, `import { getClientId, isLocalDevice } from "@/lib/localScan";`, and add `ScanDevicesResponse` to the `@/lib/types` import (drop `ScanDevice` if unused).
2. Replace the two queries and the device state (lines 32-44) with:

```tsx
  const clientId = useMemo(() => getClientId(), []);
  const [device, setDevice] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const { data: deviceData } = useQuery({
    queryKey: ["scan-devices", clientId],
    queryFn: () =>
      api.get<ScanDevicesResponse>(`/api/scan/devices?client_id=${encodeURIComponent(clientId)}`),
    refetchInterval: searching ? 1000 : false,
  });
  const chosenDevice = device ?? deviceData?.default ?? null;
  const { data: status } = useQuery({
    queryKey: ["scan-status", clientId, chosenDevice],
    queryFn: () =>
      api.get<ScanStatus>(
        `/api/scan/status?client_id=${encodeURIComponent(clientId)}` +
          (chosenDevice ? `&device=${encodeURIComponent(chosenDevice)}` : ""),
      ),
    refetchInterval: 10_000,
  });
  const localCount = (deviceData?.devices ?? []).filter((d) => isLocalDevice(d.id)).length;
  const { search, start, confirmInstalled, prefetch } = useLocalScanSearch(clientId, {
    agentConnected: deviceData?.agent_connected ?? false,
    localCount,
  });
  useEffect(() => setSearching(search.phase === "searching"), [search.phase]);
```

   Keep `const [reordering, setReordering] = useState(false);` where it was.
3. Where `ScanToolbar` is rendered (around line 205), pass the new props:

```tsx
          <ScanToolbar
            status={status}
            devices={deviceData?.devices ?? []}
            device={chosenDevice}
            onDeviceChange={setDevice}
            search={search}
            onSearch={start}
            onOpen={prefetch}
            onInstalled={confirmInstalled}
          />
```

4. Delete `frontend/src/lib/scanDevices.ts` and `frontend/src/lib/scanDevices.test.ts` (`groupDevices` replaces `scanDeviceHint`). Run `grep -rn scanDevices frontend/src` to confirm no other user.

- [ ] **Step 8: Run frontend checks**

Run: `cd frontend && npm test && npm run lint && npx tsc -b`
Expected: all PASS, no type errors

- [ ] **Step 9: Commit**

```bash
git add -A frontend/src
git commit -m "feat: scanner listbox with local scanner search and agent install panel"
```

---

### Task 12: Docs and end-to-end check

**Files:**
- Modify: `README.md` (Features list; new "Client scanner agent" section after the prerequisites/scanner part)

**Interfaces:** none.

- [ ] **Step 1: Write the README section**

Add to the Features list, after the **Scan** bullet:

```markdown
- **Scan from your own network:** pick **Search local scanners** in the scanner menu to use a Wi-Fi scanner on the network of the computer you are using, even when the server is elsewhere. The browser starts the Origami Agent, which finds eSCL (AirScan) scanners and connects out to the server; no ports, no VPN.
```

Add a new section:

````markdown
## Client scanner agent

The agent lets a browser use Wi-Fi scanners on its own network. It runs only when the scan page asks for it, and stops after 30 minutes without a scan.

### Build

Needs Go 1.22+ on the build machine (not on the server or clients):

```bash
agent/build.sh
```

This writes Windows and Linux builds to `agent/dist/`. The macOS builds (`origami-agent-darwin-*.zip`) need a Mac: run the same script there and copy the zips into `agent/dist/` on the server. The server serves the files from `AGENT_DIST_DIR` (default `../agent/dist`, relative to `backend/`).

Set `PUBLIC_URL` in `.env` to the address clients use to reach Origami (e.g. `https://origami.example.com`). The agent has no built-in server address: it gets this URL from the launch link, so the same agent build works for any Origami install. When `PUBLIC_URL` is empty, the server uses the address of the incoming request, which is wrong behind a reverse proxy or tunnel that rewrites the host.

### First run on a client

On the scan page open the scanner menu, choose **Search local scanners** and download the agent for your system. Run it once:

- **Windows:** if SmartScreen says “Windows protected your PC”, choose **More info → Run anyway**. Allow it on private networks when the firewall asks.
- **macOS:** unzip, move **Origami Agent** to Applications, right-click → **Open** once.
- **Linux:** `chmod +x origami-agent-linux-*` and run it once. It needs `xdg-mime` (package `xdg-utils`).

Keep the file where it is: the browser starts it from that path. Then click **Installed, search now**. The browser asks once whether to open Origami Agent; tick “always allow”.

The agent pairs with the first Origami server that starts it and refuses others. To pair it with another server, run it with `--reset`.

### Troubleshooting

- **No scanners found:** the scanner must support eSCL/AirScan (look for AirPrint or Mopria Scan in its specs). Guest Wi-Fi with client isolation, or a firewall blocking multicast DNS (UDP 5353), hides the scanner.
- **Agent not responding:** the browser prompt may have been dismissed, or the agent was moved after the first run. Run it again once, then search again.
````

- [ ] **Step 2: Manual end-to-end check**

With the service running behind its public URL (for this install `https://origami.yabzec.uk`), `PUBLIC_URL` set to it and `agent/build.sh` run:

1. On a client on another network than the server, with a Wi-Fi eSCL scanner: open Scan, open the scanner menu, choose **Search local scanners**. Expected: install panel (first time).
2. Download, run once, click **Installed, search now**. Expected: browser prompt; then the scanner appears under "This computer's network" while the menu stays open, with "1 found".
3. Select it, Preview, Scan two pages, Save. Expected: document processes like a server scan.
4. Close the browser tab, reopen Scan, search again. Expected: no install panel; agent starts directly.
5. Turn the scanner off and Scan. Expected: "Scanner offline" error.
6. Repeat 1–3 on Windows.

- [ ] **Step 3: Full test run**

Run:
```bash
cd backend && uv run pytest -q
cd ../agent && go test ./...
cd ../frontend && npm test && npm run lint
```
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: client scanner agent"
```
