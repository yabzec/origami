# Origami

Self-hosted document management system: scan, upload, OCR, semantic search, and RAG chat over your own documents.

## Installation

### System dependencies

```bash
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu
```

Required for OCR (Italian + English). Also needed on the host:

- `poppler-utils` (`pdftoppm`) — PDF rasterization for OCR fallback. Usually preinstalled; otherwise `sudo apt install poppler-utils`.
- `sane-utils` (`scanimage`) — flatbed scanner access. Usually preinstalled; otherwise `sudo apt install sane-utils`.
- `fonts-dejavu` — only needed to run the OCR test suite (renders test fixture images). Usually preinstalled; otherwise `sudo apt install fonts-dejavu`.

Verify:

```bash
tesseract --list-langs   # must include ita, eng and deu
pdftoppm -v
scanimage --version
```

### Backend

```bash
cd backend
cp ../.env.example ../.env   # edit STORAGE_PATH, GEMINI_API_KEY, JWT_SECRET
# optional: PRIMARY_LANGUAGE (ISO 639-1, default "it") - language for AI summaries and translations
uv sync
docker compose up -d db
uv run alembic upgrade head
uv run python -m app.cli create-user <username>
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
```

Run the background worker (separate process, required for uploads/scans to process):

```bash
uv run python -m app.worker
```

Run tests (real Postgres, no mocks — `docker compose up -d db` must be running):

```bash
uv run pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server on :5173, proxies /api to :8000
npm test           # vitest
npm run build      # outputs frontend/dist
```

In production, build the frontend and run only the backend: FastAPI serves
`frontend/dist` automatically when it exists, so the Cloudflare tunnel needs
just the one backend port.

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
