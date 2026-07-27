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

### Environment and AI models

Three AI paths, configured independently:

**Chat / text (remote):** Set `LLM_MODEL` (a LiteLLM model string; see `.env.example`) and `LLM_API_KEY`.

**Embeddings (remote — Cloudflare Workers AI):**
- Both document indexing and search queries use `@cf/baai/bge-m3`, reached through LiteLLM's OpenAI-compatible path. Set `EMBEDDING_API_KEY` to a Cloudflare API token with the *Workers AI* permission, and `EMBEDDING_API_BASE` to `https://api.cloudflare.com/client/v4/accounts/<account_id>/ai/v1`.
- **Cost:** Cloudflare's free tier is 10,000 Neurons/day, resetting 00:00 UTC. bge-m3 bills 1,075 Neurons per million input tokens, so the free pool is roughly **9.3M input tokens/day** — on the order of 37,000 chunks or hundreds of thousands of search queries. On the Workers *Free* plan, exceeding the pool makes requests fail outright until the reset; the Paid plan bills overage at $0.011/1,000 Neurons.
- **Indexing and querying must use the same model.** Vector search compares the query vector against stored chunk vectors; if the two came from different models the comparison is meaningless and search silently returns noise. Never point indexing and search at different providers.
- **Changing `EMBEDDING_MODEL`** to a model with a different vector size requires a new Alembic migration to match `EMBEDDING_DIM` in `app/models/chunk.py` (a code constant, not an env var) plus a full re-embed of every chunk. Not a config-only switch.

**Image description (local):**
- `vikhyatk/moondream2` runs locally on CPU; no API key needed. Document images never leave the machine.
- **First-run download:** ~3.7GB from HuggingFace, cached under `~/.cache/huggingface`; relocate with `HF_HOME`. The first image description is slow while this downloads.
- **RAM:** nothing is resident. The model is loaded per call and released, costing a transient ~3.7GB spike in the worker only while an image is being described (peak observed ~4.4GB, returning to ~1GB after). The API process never imports torch at all. Only images with little or no extractable text reach this path, so it is rare in practice.
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
