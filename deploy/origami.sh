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
