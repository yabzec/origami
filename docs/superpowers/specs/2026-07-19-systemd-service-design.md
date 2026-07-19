# Origami — systemd Service: Design Spec

**Date:** 2026-07-19
**Status:** Approved by user (brainstorming session)

## 1. Overview

Run the whole Origami stack with `systemctl start origami`: one systemd unit executes a launcher script that ensures the Postgres container is up and healthy, applies migrations, then runs the API and worker from the host venv. This implements the "venv + systemd later" deployment path deferred in the original design spec (§2), keeping the backend on the host so the scanner retains native SANE/USB access.

### Goals

- `systemctl start origami` brings up everything: db container (started only if not already running), migration to head, API, worker.
- `systemctl enable origami` makes it start at boot.
- Single service, single launcher — the structure the user chose (option B) over per-process units.
- Documented user-creation and operations flow in the README.

### Non-goals

- No app containerization: no Dockerfile, no compose changes, no storage volume in compose (storage stays a plain host folder at `STORAGE_PATH`, auto-created by the `Storage` class — the compose-volume idea from the original request is moot in this architecture).
- No per-process supervision: if either the API or the worker dies, the whole service restarts (accepted all-or-nothing tradeoff of the single-unit choice).
- The service does not build the frontend; building (`npm run build`) is a documented manual prerequisite. The backend already serves `frontend/dist` when present (Phase 4).
- Dev workflow unchanged: `uv run uvicorn app.main:app` (port 8000) + Vite dev server keep working exactly as before, independent of the service.

## 2. Files

```
deploy/
├── origami.service   # systemd system unit; installed to /etc/systemd/system/
└── origami.sh        # launcher script, executable, stays in the repo
README.md             # new "Run as a systemd service" + "Create a user" sections
```

## 3. Launcher — `deploy/origami.sh`

Bash, `set -euo pipefail`. Derives the repo root from its own path (`REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"`) so it works from any CWD.

1. **DB up (idempotent):** `docker compose -f "$REPO_DIR/docker-compose.yml" up -d db` — starts the container if down, no-op if already running.
2. **Wait for healthy:** poll `docker compose … exec -T db pg_isready -U origami` every 1 s, 60 s timeout. On timeout: clear error message to stderr, exit 1 (systemd marks the start failed).
3. **Migrate:** `cd "$REPO_DIR/backend"` then `.venv/bin/python -m alembic upgrade head`. Venv binaries are invoked directly — no dependency on `uv` being on systemd's PATH. CWD is `backend/` so pydantic-settings' `env_file="../.env"` resolves to the repo-root `.env` exactly as in dev.
4. **Launch both processes** (still with CWD `backend/`):
   - `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8124 &`
   - `.venv/bin/python -m app.worker &`
5. **Supervise:** `trap` on TERM/INT kills both children; `wait -n` returns when either child exits, after which the script kills the survivor and exits non-zero so systemd's `Restart=on-failure` restarts the whole stack.

Port **8124**, bound to **127.0.0.1** — cloudflared on the same host reaches it; nothing is exposed on the LAN. (Dev uvicorn keeps its default 8000; the two can run simultaneously.)

## 4. Unit — `deploy/origami.service`

```ini
[Unit]
Description=Origami DMS (API + worker; ensures db container is up)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=exec
User=mcolpo
SupplementaryGroups=docker
WorkingDirectory=/home/mcolpo/Developer/Private/origami
ExecStart=/home/mcolpo/Developer/Private/origami/deploy/origami.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- **System unit** (not user unit): starts at boot via `enable`, no login/linger required.
- `SupplementaryGroups=docker` grants the service docker-socket access even though the account is not in the docker group (the exact permission wall observed on this host).
- Absolute repo path baked in; the README notes to edit both `WorkingDirectory` and `ExecStart` if the repo ever moves.
- Default `KillMode=control-group` means `systemctl stop` TERMs the script and both children; the script's trap is belt-and-braces for manual runs.

### Stop semantics

`systemctl stop origami` stops the API and worker but **leaves the db container running** (cheap, holds the data, may be shared). `docker compose stop db` stops it explicitly when wanted.

## 5. README changes

- **"Run as a systemd service"** section: prerequisites (`.env` configured, `cd backend && uv sync` done, `cd frontend && npm install && npm run build` done, docker present), install commands (`sudo cp deploy/origami.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now origami`), logs (`journalctl -u origami -f`), the app URL (`http://127.0.0.1:8124`, fronted by cloudflared), stop semantics note, and the repo-path-edit note.
- **"Create a user"** section: `cd backend && uv run python -m app.cli create-user <name>` — works whenever the db is up (service running or just `docker compose up -d db`); interactive password prompt; no signup endpoint exists by design.
- Storage note: files live in the host folder pointed to by `STORAGE_PATH` (auto-created); nothing docker-related about it.

## 6. Error handling

| Failure | Behavior |
|---|---|
| docker daemon down / socket denied | `docker compose up` fails → script exits non-zero → unit start fails with the docker error in `journalctl` |
| db never becomes healthy | 60 s timeout → explicit message → exit 1 → unit failed (systemd retries per `Restart=on-failure`/`RestartSec=5`) |
| migration fails | script exits at step 3 (`set -e`), API/worker never start |
| API or worker crashes later | `wait -n` returns → survivor killed → non-zero exit → systemd restarts the whole stack |
| `systemctl stop` | TERM to the cgroup: script trap + systemd kill both children cleanly; db container stays up |

## 7. Verification

- Automatable in-repo: `bash -n deploy/origami.sh` (syntax), `systemd-analyze verify deploy/origami.service` (unit lint; may emit ignorable user-resolution warnings when run unprivileged).
- Real smoke is a **human step** (requires sudo + docker socket, which implementation subagents on this host lack): install unit → `systemctl start origami` → `curl http://127.0.0.1:8124/api/health` → create a user → log in via the UI → `systemctl stop origami` → confirm db container still up.

## 8. Decisions log

| Decision | Choice | Why |
|---|---|---|
| App containerization | None — host venv + systemd | Scanner keeps native USB access; original spec's deferred plan |
| Unit structure | Single unit + launcher script (option B) | User preference: literal `systemctl start origami.service`; accepts all-or-nothing restarts |
| Unit type | System unit, `User=mcolpo`, `SupplementaryGroups=docker` | Boot-time start without linger; solves docker-socket access declaratively |
| Port | 127.0.0.1:8124 | User choice; localhost-only for cloudflared fronting; coexists with dev's 8000 |
| Stop semantics | db container left running | Data safety, cheap, possibly shared |
| Storage | Plain host folder via `STORAGE_PATH` | No docker involvement; original compose-volume ask is moot |
