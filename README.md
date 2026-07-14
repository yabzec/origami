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
cp ../.env.example ../.env   # edit STORAGE_PATH, GEMINI_API_KEY, JWT_SECRET
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
