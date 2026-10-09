# Origami — Client Scanner Agent: Design Spec

**Date:** 2026-10-09
**Status:** Approved by user (brainstorming session)

## 1. Overview

Today scanning is server-side only: `ScanimageBackend` (`backend/app/services/scanner.py`) runs `scanimage` on the Origami host. The user wants to scan from a Wi-Fi scanner that sits on the client's network, while the server is on a different network and is reached only through the Cloudflare tunnel at `https://origami.yabzec.uk`.

The browser cannot reach the scanner (no CORS headers on the scanner, mixed content, Chrome Local Network Access), and the server cannot reach the client LAN. A small agent on the client PC bridges the gap: the browser launches it on demand through a custom URL scheme, the agent finds eSCL (AirScan) scanners on the LAN with mDNS, and opens an outbound WebSocket to the server through the existing tunnel. The server drives the scanner over that socket with a new `ScannerBackend` implementation, so the scan session flow (pages, preview, reorder, compile, processing) is unchanged.

### Goals

- Scan from a Wi-Fi eSCL scanner on the client LAN into the existing scan page flow.
- No ports opened on the client, no VPN, no per-client Cloudflare configuration: only the existing `origami.yabzec.uk` tunnel.
- Light client setup: download one file, run it once. After that, the browser starts the agent when needed.
- In the scanner dropdown, a "Search local scanners" item starts the agent and the found scanners appear in the open dropdown without closing it.
- Agent for Windows, macOS and Linux.
- Server-side scanning keeps working as before.

### Non-goals

- USB scanners on the client (no TWAIN/WIA/SANE on the client side).
- ADF multi-page feed: one page per Scan click, as today.
- Agent autostart, tray icon, auto-update.
- Code signing. First run shows the Windows SmartScreen / macOS Gatekeeper warning; the install panel explains how to pass it.
- Manual scanner address entry when mDNS discovery fails.
- Server-sent events for device updates (polling is enough).

## 2. Components

```
agent/                         # new, Go module
├── main.go                    # entry: --register, origami-agent:// URL handling
├── register_{windows,darwin,linux}.go
├── discovery.go               # mDNS _uscan._tcp / _uscans._tcp
├── escl.go                    # restricted eSCL request executor
├── conn.go                    # WebSocket client, framing, ping, idle exit
├── macos/Info.plist           # CFBundleURLTypes for origami-agent
└── build.sh                   # cross-compile into agent/dist/ (git-ignored)

backend/app/services/agent_hub.py      # new: connected agents, request/response over WebSocket
backend/app/services/escl.py           # new: EsclRemoteBackend
backend/app/services/scanner.py        # CompositeBackend, per-device locks
backend/app/api/agent.py               # new: launch token, WebSocket, downloads
backend/app/api/scan.py                # client_id on devices/status

frontend/src/components/scan/DeviceListbox.tsx   # new: replaces native <select>
frontend/src/components/scan/AgentInstallPanel.tsx
frontend/src/lib/localScan.ts                    # new: pure state/OS-detection logic
```

### 2.1 `origami-agent` (Go)

- Single binary per platform; macOS ships as a zipped `Origami Agent.app`.
- `--register` (also the default when run with no arguments): registers the `origami-agent://` URL handler for the current user only, no admin rights:
  - Windows: `HKCU\Software\Classes\origami-agent` with `shell\open\command` = `"<exe path>" "%1"`.
  - Linux: `~/.local/share/applications/origami-agent.desktop` with `Exec=<path> %u` and `MimeType=x-scheme-handler/origami-agent;`, then `xdg-mime default origami-agent.desktop x-scheme-handler/origami-agent`.
  - macOS: the `Info.plist` declares the scheme; LaunchServices registers it when the user opens the app once. macOS delivers the URL as an Apple Event, not in `argv`, so the binary needs a small cgo shim with an `NSAppleEventManager` `kAEGetURL` handler. The macOS build therefore runs on a Mac (or a macOS CI runner), not by cross-compiling from Linux.
- Launch with `origami-agent://connect?server=<url>&token=<t>`: connects, discovers, serves requests.
- Single instance per user (lock file in the user cache dir plus a local IPC socket). A second launch hands the new server URL and token to the running instance, which reconnects with the new token and re-runs discovery, then the second process exits.
- Exits after 30 minutes without an `escl` request, or when the socket cannot be restored within 60 seconds.

### 2.2 Restricted eSCL executor

The agent only executes requests that:

- target a scanner `uuid` it discovered itself (the server never sees or sends IP addresses or URLs), and
- use a path under that scanner's eSCL root (`rs` TXT record, default `eSCL`), with method `GET`, `POST` or `DELETE`.

Anything else gets `escl_response` with status 403 and is not sent. A compromised server cannot use the agent as a general proxy into the client LAN. TLS certificate checks are skipped for `_uscans._tcp` scanners, which use self-signed certificates.

### 2.3 Agent hub (`agent_hub.py`)

- In-memory registry keyed by `(user_id, client_id)`: socket, agent version, OS, discovered scanners. The API is a single uvicorn process, so in-memory state is correct.
- A new agent for the same key replaces and closes the old one.
- `request(key, scanner_uuid, method, path, body, timeout) -> (status, content_type, bytes)`: assigns a correlation id, sends the `escl` message, collects binary chunks until `end`, resolves a future.
- Scan endpoints are sync `def` functions running in the threadpool. The hub keeps a reference to the event loop and sync callers use `asyncio.run_coroutine_threadsafe(...).result(timeout)`.
- One-time launch tokens: `token -> (user_id, client_id, expires_at)`, random 32 bytes, TTL 2 minutes, removed on first use.

### 2.4 `EsclRemoteBackend` and routing

- Implements the existing `ScannerBackend` protocol for one `(user_id, client_id)`.
- `list_devices()`: the hub's scanner list for that key, as `{"id": "agent:<client_id>:<scanner_uuid>", "name": ...}`. The id is stable across agent restarts, so a stored `ScanSession.device` stays valid.
- `scan(dpi, mode)`: `GET ScannerCapabilities` (cached per scanner), `POST ScanJobs` with `InputSource` Platen, resolution clamped to the nearest supported value, `ColorMode` `RGB24` for `Color` and `Grayscale8` for `Gray`, `DocumentFormatExt` `image/png` when supported, otherwise `image/jpeg`. Read the job URL from the `Location` header, `GET <job>/NextDocument`, convert the result to PNG with PIL. The stored page is `page_NNN.png`, exactly as today.
- `preview()`: same as `scan` with 75 dpi grayscale.
- `available()`: true when the agent is connected and has at least one scanner.
- `CompositeBackend` wraps `ScanimageBackend` and the remote backend for the requesting client: `list_devices` returns server scanners first, then agent scanners; `scan` and `preview` route by device id prefix (`agent:` goes to the remote backend). A device id with a `client_id` other than the request's is rejected with 404 `not_found`.
- The single global `_scan_lock` becomes one lock per device id, so a remote scan does not block the server scanner. `/api/scan/status` and `/api/scan/devices` take optional `client_id` and `device` query parameters; `status` reports `busy` for the given device.

### 2.5 Error mapping

| Condition | Exception |
|---|---|
| Agent not connected, or scanner no longer discovered | `ScannerOffline` |
| eSCL HTTP 503, or job state busy | `ScannerBusy` |
| No response within `SCAN_TIMEOUT_SECONDS` (120 s) | `ScannerTimeout` |
| eSCL state `CoverOpen` / jam | `CoverOpen` / `ScannerJam` |
| Agent 403 or any other failure | `ScannerError` |

The existing error handlers and UI messages apply unchanged.

## 3. Protocol

### 3.1 Launch and auth

1. Browser: `POST /api/agent/launch` with `{"client_id": ...}` (normal JWT auth). Response: `{"url": "origami-agent://connect?server=https://origami.yabzec.uk&token=<t>"}`. The server URL comes from a new `PUBLIC_URL` setting, falling back to the request's base URL.
2. Browser opens the URL in a hidden iframe; the page does not navigate. The browser asks "Open Origami Agent?" the first time; the user can tick "always allow".
3. Agent connects to `wss://<server>/api/agent/ws?token=<t>`. The server consumes the token; an invalid or expired token closes the socket with code 4401.

### 3.2 Messages

JSON text frames, plus binary frames for response bodies.

- Agent → `{"type": "hello", "version": "...", "os": "..."}`. The server logs a version mismatch; nothing is enforced.
- Agent → `{"type": "devices", "devices": [{"uuid": "...", "name": "..."}]}`: after the first mDNS browse (about 3 s) and whenever a scanner appears or disappears.
- Server → `{"type": "escl", "id": "...", "scanner_uuid": "...", "method": "POST", "path": "/eSCL/ScanJobs", "body": "<xml>"}`.
- Agent → `{"type": "escl_response", "id": "...", "status": 201, "headers": {"Location": "..."}, "content_type": "...", "length": 123}`, then binary frames (16-byte request id followed by at most 256 KB of data), then `{"type": "end", "id": "..."}`. The `Location` header is rewritten by the agent to a path relative to the scanner, so the server never sees addresses.
- WebSocket ping every 30 s in both directions; Cloudflare closes idle sockets after 100 s.

## 4. Frontend

### 4.1 Device listbox

- `DeviceListbox` replaces the native `<select>` in `ScanToolbar.tsx`: a popover listbox with keyboard (arrows, Enter, Esc) and touch support.
- Groups: "Server" (server scanners), "This computer's network" (agent scanners), then the "Search local scanners" item.
- Selecting "Search local scanners" does not close the popover. It shows its state in place: searching (spinner), "N found", "No scanners found", or "Agent not responding" with a reinstall link.
- While searching, React Query refetches `/api/scan/devices?client_id=<id>` every second, for at most 15 seconds. New scanners are inserted in the open list.

### 4.2 Install flow

`localStorage` keys (every access in `try/catch`):

- `origami.clientId`: random id, created on first use.
- `origami.agentInstalled`: set when the agent first connects for this client, not when the download is clicked.

Flow when the user selects "Search local scanners":

- Flag missing: the popover shows `AgentInstallPanel` right away: a download button for the detected OS, links for the other platforms, a short first-run note (Windows: "More info → Run anyway", then allow the firewall prompt on private networks; macOS: unzip, move to Applications, right-click → Open; Linux: `chmod +x`, run once), and an "Installed, search now" button that starts the search.
- Flag set: launch at once.
- Flag set but no agent after 10 seconds: "Agent not responding" with a reinstall link. The flag is kept, because the cause can be a dismissed browser prompt, not a missing install.

### 4.3 Pure logic

`lib/localScan.ts` holds the search state machine, device grouping and OS/architecture detection, unit-tested like `lib/scanDevices.ts`.

## 5. Distribution

- `agent/build.sh` builds `windows-amd64.exe`, `linux-amd64`, `linux-arm64`, and on macOS `darwin-arm64.zip` and `darwin-amd64.zip`, into `agent/dist/` (git-ignored).
- `GET /api/agent/download/{platform}` serves files from a new `AGENT_DIST_DIR` setting (default `agent/dist`), with `get_current_user_flexible` auth so a plain link works. A missing file returns 404 `agent_not_built` and the install panel says the agent has not been built on the server.
- Go is needed only on the machine that builds the agent. The README gets a "Client scanner agent" section: build, first run per OS, troubleshooting (mDNS blocked by firewall, scanner without eSCL).

## 6. Testing

- **Go:** eSCL executor against an `httptest` fake scanner; the allow-list rejects a non-eSCL path and an unknown scanner uuid; framing round trip with a large body; URL parsing; handler registration on Linux and Windows writes the right entries (temp home / fake registry interface); single-instance hand-off.
- **Backend (pytest):** launch token is single-use and expires; WebSocket session with a fake agent through `TestClient`; `EsclRemoteBackend` against canned `ScannerCapabilities` XML and a JPEG document, stored as PNG; `CompositeBackend` routing by id prefix; another client's device id is rejected; per-device locks let a server scan and a remote scan run together; error mapping (disconnect → `ScannerOffline`, 503 → `ScannerBusy`, timeout → `ScannerTimeout`).
- **Frontend (vitest):** `localScan.ts` state machine (flag missing → install panel, flag set → searching → found / none / not responding), grouping, OS detection.
- **Manual:** real Wi-Fi scanner, client on a different network from the server, through `origami.yabzec.uk`, on at least Windows and Linux.

## 7. Risks

- **mDNS blocked:** Windows Firewall or guest Wi-Fi isolation can block multicast. The agent reports zero scanners and the UI shows "No scanners found"; the README covers it.
- **Scanner without eSCL:** older models only work with vendor drivers. Out of scope; the README says how to check (AirPrint / Mopria Scan support).
- **macOS build** needs cgo and a Mac or macOS CI runner.
- **Large pages:** a 300 dpi colour page can be 20–30 MB as PNG. Chunked binary frames keep memory flat on the agent; the server holds one page in memory, as it does today with `scanimage` output.
