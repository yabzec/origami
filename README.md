# Origami

Self-hosted document management system: scan, upload, OCR, semantic search, and RAG chat over your own documents.

## Installation

### System dependencies

```bash
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng
```

Required for OCR (Italian + English). Also needed on the host:

- `poppler-utils` (`pdftoppm`) — PDF rasterization for OCR fallback. Usually preinstalled; otherwise `sudo apt install poppler-utils`.
- `sane-utils` (`scanimage`) — flatbed scanner access. Usually preinstalled; otherwise `sudo apt install sane-utils`.
- `fonts-dejavu` — only needed to run the OCR test suite (renders test fixture images). Usually preinstalled; otherwise `sudo apt install fonts-dejavu`.

Verify:

```bash
tesseract --list-langs   # must include ita and eng
pdftoppm -v
scanimage --version
```

### Backend

```bash
cd backend
cp ../.env.example ../.env   # edit STORAGE_PATH, LLM_API_KEY, JWT_SECRET
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

### Environment and Local Models

**Chat and Text LLM (remote):** Only the chat endpoint requires an API key and provider configuration. Set `LLM_MODEL` (a LiteLLM model string; see `.env.example` for examples) and `LLM_API_KEY`. Embedding and image description do not use this — they run entirely locally.

**Embedding and Image Description (local):**
- Embedding (`BAAI/bge-m3`) and image description (`vikhyatk/moondream2`) run locally on CPU; no API key is needed.
- **First-run model download:** ~6GB of weights from HuggingFace (~2.3GB embedding + ~3.7GB vision) are downloaded on first use and cached under `~/.cache/huggingface`. Relocate the cache by setting `HF_HOME`. The first ingestion after a fresh install is slow because of this download; the first image description is slower still while the vision model loads.
- **RAM:** Expect ~2.3GB resident in each of the two processes (`uvicorn` and the worker both embed concurrently), plus a transient ~3.7GB spike in the worker while an image is being described. Total steady-state ~4.6GB, peak ~8.3GB. This has been tested on a 16GB system running the Postgres container alongside; less than 16GB is not recommended.
- **Changing embedding models:** The `EMBEDDING_MODEL_NAME` setting is not a simple config change. Switching models requires a new Alembic migration to match the new embedding dimension, followed by a full re-ingestion of all documents. The default is `BAAI/bge-m3` (1024-dim); if you need to change it, plan for downtime.
- **Vision model pinning:** `VISION_MODEL_REVISION` is pinned to a specific commit because moondream2 executes arbitrary code from its HuggingFace repository (`trust_remote_code=True`). Do not unpin it casually — always verify the commit before updating.

**Upgrading an existing install:** this branch's migration (`887ee519199f_local_embedding_dim`) wipes all chunks and summaries and resets every document to `pending` — it runs automatically on `alembic upgrade head`, which `deploy/origami.sh` also runs on every service start/restart. Nothing re-enqueues that work automatically, so after upgrading, rebuild the search index by running:

```bash
cd backend && uv run python -m scripts.reingest_pending
```

Skipping this step leaves the search index silently empty.

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
