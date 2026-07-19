# Origami systemd Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `systemctl start origami` brings up the whole stack — db container (idempotent), pg_isready wait, migrations, then API on 127.0.0.1:8124 + worker from the host venv.

**Architecture:** One systemd system unit (`deploy/origami.service`) executes one launcher script (`deploy/origami.sh`). The script ensures the Postgres container is healthy, migrates, launches both host processes, and exits non-zero if either dies so systemd restarts the whole stack. No app containerization — the backend stays on the host (scanner keeps native SANE/USB access).

**Tech Stack:** bash, systemd, docker compose (db only), existing `backend/.venv`.

**Spec:** `docs/superpowers/specs/2026-07-19-systemd-service-design.md`

## Global Constraints

- API binds `127.0.0.1:8124` (production service). Dev workflow (`uv run uvicorn ...` on 8000 + Vite proxy) stays untouched and can run simultaneously.
- Venv binaries invoked directly (`.venv/bin/python`, `.venv/bin/uvicorn`) — no reliance on `uv` being on systemd's PATH.
- The launcher runs backend commands with CWD `backend/` so pydantic-settings' `env_file="../.env"` resolves to the repo-root `.env`, same as dev.
- `systemctl stop origami` leaves the db container running (data safety); this is documented, not "fixed".
- Unit: `User=mcolpo`, `SupplementaryGroups=docker` (docker-socket access without group membership), `Restart=on-failure`, `RestartSec=5`, `WantedBy=multi-user.target`, absolute repo paths baked in.
- **Host constraint:** implementation subagents on this machine have neither sudo nor docker-socket access. Automatable verification is limited to `bash -n`, `shellcheck` (if installed), `systemd-analyze verify`, and executable-bit checks. The real end-to-end smoke (Task 3) is a HUMAN step — an agent must not attempt it, only present it.
- Git hygiene: stage specific files only, never `git add -A`/`.` (untracked `graphify-out/` must never be committed). Conventional Commits.

---

### Task 1: Launcher script + systemd unit

**Files:**
- Create: `deploy/origami.sh` (mode 755)
- Create: `deploy/origami.service`

**Interfaces:**
- Consumes: existing `docker-compose.yml` (service `db`, user `origami`), `backend/.venv`, `backend/alembic.ini`, repo-root `.env`.
- Produces: `deploy/origami.sh` (exit 0 only while both children run; exit 1 on db timeout, migration failure, or child death) and `deploy/origami.service` (installable unit pointing at the script). Task 2's README references both paths and the install commands verbatim.

- [ ] **Step 1: Write the launcher script**

`deploy/origami.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$REPO_DIR/docker-compose.yml")

echo "origami: ensuring db container is up"
"${COMPOSE[@]}" up -d db

echo "origami: waiting for postgres to become ready"
deadline=$((SECONDS + 60))
until "${COMPOSE[@]}" exec -T db pg_isready -U origami -d origami >/dev/null 2>&1; do
  if ((SECONDS >= deadline)); then
    echo "origami: postgres not ready after 60s, giving up" >&2
    exit 1
  fi
  sleep 1
done

cd "$REPO_DIR/backend"

echo "origami: applying migrations"
.venv/bin/python -m alembic upgrade head

echo "origami: starting api (127.0.0.1:8124) and worker"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8124 &
API_PID=$!
.venv/bin/python -m app.worker &
WORKER_PID=$!

shutdown() {
  kill "$API_PID" "$WORKER_PID" 2>/dev/null || true
  wait "$API_PID" "$WORKER_PID" 2>/dev/null || true
}
trap 'shutdown; exit 0' TERM INT

# Block until either child exits; then stop the survivor and fail so
# systemd (Restart=on-failure) restarts the whole stack.
set +e
wait -n
set -e
echo "origami: a child process exited, shutting down" >&2
shutdown
exit 1
```

Make it executable: `chmod +x deploy/origami.sh`

- [ ] **Step 2: Verify script syntax and mode**

Run: `bash -n deploy/origami.sh && test -x deploy/origami.sh && echo OK`
Expected: `OK`

Run: `command -v shellcheck >/dev/null && shellcheck deploy/origami.sh || echo "shellcheck not installed, skipped"`
Expected: no findings, or the skipped message. Fix any shellcheck findings (don't suppress).

- [ ] **Step 3: Write the unit file**

`deploy/origami.service`:

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

- [ ] **Step 4: Verify the unit**

Run: `systemd-analyze verify deploy/origami.service 2>&1 | grep -v -i "user\|group" || true` — then eyeball the full output.
Expected: no errors beyond possibly user/group-resolution warnings from running unprivileged (those are acceptable and expected; anything about syntax, unknown directives, or missing ExecStart is a real failure to fix).

- [ ] **Step 5: Commit**

```bash
git add deploy/origami.sh deploy/origami.service
git commit -m "feat: systemd service and launcher for one-command startup"
```

---

### Task 2: README operations documentation

**Files:**
- Modify: `README.md` (after the existing Frontend section)

**Interfaces:**
- Consumes: Task 1's file paths and unit semantics — commands below must match them exactly.
- Produces: user-facing run/operate/create-user documentation.

- [ ] **Step 1: Add the sections**

Append to `README.md` after the Frontend section:

````markdown
## Run as a systemd service

One command starts everything (db container, migrations, API on
`127.0.0.1:8124`, worker). Prerequisites, once:

- `.env` configured (copy from `.env.example`)
- `cd backend && uv sync`
- `cd frontend && npm install && npm run build` (the backend serves `frontend/dist`)
- docker available (the unit runs with the `docker` supplementary group)

Install and start:

```bash
sudo cp deploy/origami.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now origami
```

Operate:

```bash
systemctl status origami
journalctl -u origami -f      # logs (api + worker + launcher)
sudo systemctl restart origami
sudo systemctl stop origami   # stops api+worker; the db container stays up
docker compose stop db        # stop the db too, when you really want to
```

The app is served at `http://127.0.0.1:8124` — front it with cloudflared
(or edit `--host` in `deploy/origami.sh` to expose it on the LAN). If the
repo moves, update `WorkingDirectory` and `ExecStart` in the unit file and
re-run the install commands.

## Create a user

There is no signup endpoint by design. With the db up (service running, or
just `docker compose up -d db`):

```bash
cd backend
uv run python -m app.cli create-user <username>
```

You'll be prompted for the password twice.

## Storage

Documents live in the host folder pointed to by `STORAGE_PATH` in `.env`
(created automatically). It is a plain directory — back it up together with
the `pgdata` docker volume.
````

- [ ] **Step 2: Sanity-check consistency**

Verify every command in the new sections against the actual files: unit path `deploy/origami.service`, script port `8124`, `create-user` spelling matches `backend/app/cli.py`. Render check: ` ```bash ` fences balanced.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: systemd service operations, user creation, storage notes"
```

---

### Task 3: End-to-end smoke — HUMAN ONLY

**Do not execute this task as an agent** (requires sudo + docker socket). Present these commands to the human and wait for their confirmation:

```bash
sudo cp deploy/origami.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start origami
systemctl status origami                      # expect: active (running)
curl -s http://127.0.0.1:8124/api/health      # expect: {"status":"ok"}
cd backend && uv run python -m app.cli create-user marco  # if no user yet
sudo systemctl stop origami
docker compose ps                             # expect: db still running
```

Optional: `sudo systemctl enable origami` for start-at-boot.

---

## Exit criteria

- `bash -n` + executable bit + `systemd-analyze verify` clean (modulo unprivileged user-resolution warnings).
- README sections match the shipped files exactly.
- Human smoke (Task 3) passes: start → health OK → stop → db still up.
