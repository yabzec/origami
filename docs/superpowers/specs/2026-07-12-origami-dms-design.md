# Origami — Document Management System: Design Spec

**Date:** 2026-07-12
**Status:** Approved by user (brainstorming session)

## 1. Overview

Origami is a self-hosted Document Management System for a single user, running on a home server and exposed externally through a Cloudflare tunnel. It captures documents from a flatbed scanner (page by page), accepts file uploads (PDF, text formats, images, video), OCRs content in Italian, stores searchable PDFs, and provides keyword, semantic, and RAG-based Q&A search over document contents.

### Goals

- Scan multi-page documents from a server-connected flatbed scanner with per-page preview/retake, compiled into a single searchable PDF.
- Ingest uploaded files: PDF, plain text/markdown/docx, images (OCR'd), and video (storage + tagging only, no content extraction).
- Organize documents into nested folders (exactly one folder per document) with many-to-many tags.
- Search: keyword (Postgres FTS, Italian), semantic (pgvector), hybrid (RRF fusion), plus a RAG chat that answers questions with citations.
- All heavy processing (OCR, text extraction, embedding) happens in a background worker; API responses never block on it.

### Non-goals (v1)

- Multi-user accounts, roles, sharing. Single user only; account created via CLI.
- Signup flow of any kind. Login only.
- Automatic transcription or description of video content.
- Multi-turn chat memory (RAG chat is stateless single-turn in v1).
- ADF/batch scanning (flatbed only).

## 2. Architecture

### Runtime processes (3)

1. **PostgreSQL 17 + pgvector** — Docker Compose (the only containerized component).
2. **FastAPI backend** — runs directly on the host (venv; systemd later) for direct SANE/USB scanner access. Serves the built frontend via `StaticFiles` so one port is exposed through the Cloudflare tunnel.
3. **Worker** — `python -m app.worker`, same codebase, consumes a Postgres-backed job queue (`SELECT ... FOR UPDATE SKIP LOCKED`, ~1 s poll). No Redis, no Celery.

During development the frontend runs on the Vite dev server with a proxy to the backend.

### Repository layout

```
origami/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, routers, startup recovery
│   │   ├── config.py        # pydantic-settings, reads .env
│   │   ├── models/          # SQLModel entities
│   │   ├── api/             # routers: auth, documents, folders, tags, scan, search, chat
│   │   ├── services/        # scanner, ocr, ingestion, embeddings, rag, storage, llm
│   │   ├── worker/          # queue consumer + pipeline dispatch
│   │   └── cli.py           # create-user, requeue-failed, reembed
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml       # uv-managed, Python 3.12+
├── frontend/                # React + Vite + TS + shadcn/ui + TanStack Query + react-router
├── docker-compose.yml       # postgres + pgvector only
├── docs/
│   ├── architecture.md      # imported by CLAUDE.md
│   ├── workflow.md
│   ├── coding-style.md
│   └── superpowers/specs/
└── CLAUDE.md                # < 200 lines, imports the three docs above
```

### Configuration (.env)

```
DATABASE_URL=postgresql+psycopg://...
STORAGE_PATH=/path/to/storage        # files stored under $STORAGE_PATH/files/
JWT_SECRET=...
LLM_MODEL=gemini/gemini-2.5-flash    # swap to ollama/<model> for local
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_DIM=1536
GEMINI_API_KEY=...
# OLLAMA_API_BASE=...                # when using local models
```

### Auth

JWT bearer tokens, bcrypt password hashing (passlib), ~30-day expiry. Single `users` table. Every endpoint except `POST /api/auth/login` requires a valid token. Users are created only via `python -m app.cli create-user` — there is no signup endpoint.

### LLM abstraction

LiteLLM behind a single wrapper module `services/llm.py` exposing `embed(texts)` and `complete(prompt, stream=...)`. No other module imports litellm. Provider swap (Gemini ↔ Ollama) is purely env-var driven. Changing to an embedding model with a different dimension requires a migration plus `python -m app.cli reembed`.

## 3. Data model

```
users:      id, username, password_hash, created_at

folders:    id, name, parent_id (nullable self-FK → nesting), created_at
            UNIQUE(parent_id, name)

tags:       id, name UNIQUE, color

documents:  id (uuid), title, description,
            folder_id FK → folders (nullable = root),
            doc_type ENUM(scan|pdf|text|image|video),
            status ENUM(pending|processing|ready|failed),
            error_message, original_filename, file_path (relative to STORAGE_PATH),
            page_count, file_size, created_at, updated_at

document_tags: document_id + tag_id (M:N join, PK on pair)

chunks:     id, document_id FK ON DELETE CASCADE, chunk_index, page_number,
            content TEXT,
            embedding VECTOR(EMBEDDING_DIM),
            content_tsv TSVECTOR GENERATED ('italian' config)
            Indexes: HNSW (cosine) on embedding, GIN on content_tsv

jobs:       id, type ENUM(process_document), payload JSONB,
            status ENUM(queued|running|done|failed),
            attempts, max_attempts (3), run_at, last_error,
            created_at, updated_at

scan_sessions: id, status ENUM(active|compiling|done|cancelled), created_at
scan_pages:    id, session_id FK, page_number, image_path
```

Notes:

- Exactly one folder per document (single FK); many tags via join table.
- Every document — including video — gets one metadata chunk built from title + description, so all documents surface in semantic search without special-casing. Video gets only that chunk.
- File storage layout: `$STORAGE_PATH/files/{document_id}/` containing original page images or the uploaded original, plus the compiled searchable PDF. The DB stores relative paths only.

## 4. Scanning

### Integration

`python-sane` in `services/scanner.py`, behind an interface so a `scanimage` subprocess implementation can be swapped in if the device misbehaves with python-sane. SANE calls run in a thread executor (they block). A process-wide asyncio lock serializes scanner access; a 120 s watchdog aborts hung scans.

### Flow (flatbed, page by page)

1. `POST /api/scan/sessions` → create session.
2. `POST /api/scan/sessions/{id}/pages` → scan one page (default 300 DPI color; DPI/mode optional params) → PNG saved to a temp scan directory → returns page metadata + preview URL.
3. UI loop: preview → scan next / retake (`DELETE .../pages/{n}`) / reorder / finish.
4. `POST /api/scan/sessions/{id}/compile` with `{title, folder_id, tag_ids}` → creates the document (status = pending), moves images to `$STORAGE_PATH/files/{doc_id}/pages/`, enqueues `process_document`, returns immediately.
5. Worker sweep purges cancelled/abandoned sessions older than 24 h.

`GET /api/scan/status` reports scanner presence and lock state so the UI shows live scanner availability.

### Scanner error handling

All scanner endpoints return a structured error shape `{error: {code, message, detail}}`:

| Condition | Detection | Response |
|---|---|---|
| No scanner / offline | sane init/open fails or empty device list | 503 `scanner_offline` |
| Busy (app-level) | scan lock held | 409 `scanner_busy` |
| Busy (OS-level) | SANE "Device busy" | 409 `scanner_busy` |
| Paper jam | SANE jammed status | 422 `scanner_jam` |
| Cover open | SANE cover_open status | 422 `cover_open` |
| Cancelled | user cancel | scan aborted, page discarded |
| Hang | 120 s watchdog | 504 `scanner_timeout` |

## 5. Ingestion pipeline (worker)

Single job type `process_document`, dispatched by `doc_type`:

| doc_type | Pipeline |
|---|---|
| scan | Per page: pytesseract `lang='ita'` via `image_to_pdf_or_hocr` → merge pages with pypdf into one searchable PDF (original images preserved as the visual layer) → extract per-page text |
| pdf | pypdf text extraction; if the text layer is empty or garbage (heuristic: < 50 chars/page average) → rasterize with pdf2image → OCR ita → rebuild searchable PDF |
| text | Read plain text / markdown; python-docx for docx. Stored as-is, no PDF conversion |
| image | OCR ita → single-page searchable PDF + original kept |
| video | No content processing; status → ready immediately. Metadata chunk only |

Common tail (all types; video gets metadata chunk only): chunk text (~1000 chars, 200 overlap, split on paragraph boundaries, page numbers tracked) → embed via `services/llm.py` (batched) → insert `chunks` rows → document status = ready.

### Failure handling

- Job exceptions → retry with exponential backoff, max 3 attempts → document status = failed with `error_message`; UI shows a failed badge and a "retry processing" action that re-enqueues.
- Pipeline stages persist intermediate results (extracted text saved on the document before embedding), so an embedding-API outage does not lose OCR work; retry resumes from the embedding stage.
- On backend startup, any document stuck in `processing` with no live job is re-enqueued.

## 6. Search & RAG

### Search — `POST /api/search`

Modes: `semantic | keyword | hybrid` (default hybrid).

- **Semantic:** embed query → pgvector cosine top-K over chunks.
- **Keyword:** `websearch_to_tsquery('italian', q)` against `content_tsv`, ranked with `ts_rank`.
- **Hybrid:** Reciprocal Rank Fusion of the two ranked lists (no tuning parameters).
- Filters: folder (subtree), tags, doc_type. Results grouped by document with highlighted snippets and page numbers.

### RAG chat — `POST /api/chat` (SSE streaming)

Retrieve top-8 hybrid chunks → prompt the LLM with numbered sources → answer in the user's language with `[n]` citations → response includes the source list linking to document + page. Stateless single-turn in v1.

## 7. Frontend

React + Vite + TypeScript, shadcn/ui, TanStack Query, react-router. Pages:

- **Login.**
- **Dashboard / Browse** — folder tree sidebar, document grid/list, tag chips and filtering, drag-and-drop upload, live status badges (processing/failed) via polling.
- **Scan wizard** — scanner status indicator, page-by-page loop with thumbnails, retake/reorder, compile form (title, folder, tags).
- **Document view** — embedded viewer (PDF/image/video), metadata and tag editing, extracted-text tab.
- **Search** — mode toggle, filters, snippet results.
- **Chat** — RAG Q&A with cited sources.

## 8. Testing

- **Backend:** pytest against a real Postgres (docker compose test database — the DB is never mocked). Scanner service tested via a `FakeSaneDevice` implementing the scanner interface. Pipeline tests use small fixture images containing Italian text. LiteLLM is mocked only at the `services/llm.py` boundary.
- **Frontend:** vitest + Testing Library for the scan wizard state machine and search flows.

## 9. Documentation plan

Rewrite `CLAUDE.md` (< 200 lines) importing `docs/architecture.md`, `docs/workflow.md` (dev commands, migrations, graphify update), and `docs/coding-style.md`. CLAUDE.md changes require asking the user first, per their global rules.

## 10. Decisions log

| Decision | Choice | Why |
|---|---|---|
| Deployment | Backend on host, Postgres in Docker | Direct SANE/USB access; avoids device passthrough |
| Background jobs | Postgres-backed queue + worker process | Durable and retryable without Redis/Celery |
| LLM abstraction | LiteLLM | Thin, env-var provider swap (Gemini ↔ Ollama) |
| Embeddings | Chunk-level, pgvector HNSW | Long documents need chunk retrieval for RAG |
| Keyword search | Postgres FTS, italian config | Same store, no extra engine |
| Hybrid ranking | Reciprocal Rank Fusion | Robust, parameter-free |
| ORM | SQLModel + Alembic | Requested SQLAlchemy family + typed models |
| Auth | JWT bearer, CLI-created single user | External exposure via tunnel; no signup surface |
