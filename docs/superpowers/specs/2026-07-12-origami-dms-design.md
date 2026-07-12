# Origami — Document Management System: Design Spec

**Date:** 2026-07-12
**Status:** Approved by user (brainstorming session)

## 1. Overview

Origami is a self-hosted Document Management System for a single user, running on a home server and exposed externally through a Cloudflare tunnel. It captures documents from a flatbed scanner (page by page), accepts file uploads (PDF, text formats, images, video), OCRs content in Italian, stores searchable PDFs, and provides keyword, semantic, and RAG-based Q&A search over document contents.

### Goals

- Scan multi-page documents from a server-connected flatbed scanner with per-page preview/retake, compiled into a single searchable PDF. OCR language (Italian, English, or both) is selected before scanning or uploading.
- Ingest uploaded files: PDF, plain text/markdown/docx, images, and video (storage + tagging only, no content extraction).
- Every document type is searchable. Scans are indexed via OCR text; every uploaded document except video additionally gets an LLM-generated semantic description (vision model for images, summary of extracted text for PDFs/text files), indexed alongside the content for keyword, semantic, and RAG search.
- Organize documents into nested virtual folders (exactly one folder per document in the DB) with many-to-many tags. Folder structure lives only in the database and frontend; physical storage on disk is a single flat directory.
- Search: keyword (Postgres FTS), semantic (pgvector), hybrid (RRF fusion), plus a RAG chat that answers questions with citations and explicitly signals when an answer is not grounded in the stored documents.
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
VISION_MODEL=gemini/gemini-2.5-flash # used for image descriptions; local swap needs a multimodal model (e.g. ollama/llava)
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_DIM=1536
GEMINI_API_KEY=...
# OLLAMA_API_BASE=...                # when using local models
DEFAULT_OCR_LANGUAGES=ita+eng        # per-document override at scan/upload time
```

### Auth

JWT bearer tokens, bcrypt password hashing (passlib), ~30-day expiry. Single `users` table. Every endpoint except `POST /api/auth/login` requires a valid token. Users are created only via `python -m app.cli create-user` — there is no signup endpoint.

### LLM abstraction

LiteLLM behind a single wrapper module `services/llm.py` exposing `embed(texts)`, `complete(prompt, stream=...)`, and `describe(text_or_image)` (summary/description generation; uses `VISION_MODEL` for images). No other module imports litellm. Provider swap (Gemini ↔ Ollama) is purely env-var driven. Changing to an embedding model with a different dimension requires a migration plus `python -m app.cli reembed`.

## 3. Data model

```
users:      id, username, password_hash, created_at

folders:    id, name, parent_id (nullable self-FK → nesting), created_at
            UNIQUE(parent_id, name)

tags:       id, name UNIQUE, color

documents:  id (uuid), title, description,          -- description = user-written
            summary TEXT,                            -- LLM-generated semantic description
            folder_id FK → folders (nullable = root),
            doc_type ENUM(scan|pdf|text|image|video),
            ocr_languages VARCHAR (e.g. 'ita', 'eng', 'ita+eng'),
            status ENUM(pending|processing|ready|failed),
            error_message, original_filename, file_path (relative to STORAGE_PATH),
            page_count, file_size, created_at, updated_at

document_tags: document_id + tag_id (M:N join, PK on pair)

chunks:     id, document_id FK ON DELETE CASCADE, chunk_index, page_number,
            source ENUM(content|summary|metadata),   -- what this chunk was built from
            content TEXT,
            embedding VECTOR(EMBEDDING_DIM),
            content_tsv TSVECTOR GENERATED ('simple' config; language-agnostic since documents mix Italian and English)
            Indexes: HNSW (cosine) on embedding, GIN on content_tsv

jobs:       id, type ENUM(process_document), payload JSONB,
            status ENUM(queued|running|done|failed),
            attempts, max_attempts (3), run_at, last_error,
            created_at, updated_at

scan_sessions: id, status ENUM(active|compiling|done|cancelled), created_at
scan_pages:    id, session_id FK, page_number, image_path
```

Notes:

- Folders are purely virtual: they exist only in the database and drive the frontend tree. Exactly one folder per document (single FK); many tags via join table. Moving a document between folders never touches the filesystem.
- Every document — including video — gets one metadata chunk built from title + user description, so all documents surface in semantic search without special-casing. Video gets only that chunk. Uploaded non-video documents additionally get summary chunks (see §5).
- Physical storage is a single flat directory: `$STORAGE_PATH/files/`, filenames keyed by document id (`{document_id}.pdf`, `{document_id}.mp4`, ...). Scanned page images are retained inside the compiled PDF (they are its visual layer), so no separate image assets persist after compilation. In-progress scan pages live under `$STORAGE_PATH/tmp/scan_sessions/{session_id}/` until compiled or purged. The DB stores relative paths only.

## 4. Scanning

### Integration

`python-sane` in `services/scanner.py`, behind an interface so a `scanimage` subprocess implementation can be swapped in if the device misbehaves with python-sane. SANE calls run in a thread executor (they block). A process-wide asyncio lock serializes scanner access; a 120 s watchdog aborts hung scans.

### Flow (flatbed, page by page)

1. `POST /api/scan/sessions` with `{ocr_languages}` (ita / eng / ita+eng, default from env) → create session. Language is chosen up front, before any page is scanned.
2. `POST /api/scan/sessions/{id}/pages` → scan one page (default 300 DPI color; DPI/mode optional params) → PNG saved to `$STORAGE_PATH/tmp/scan_sessions/{session_id}/` → returns page metadata + preview URL.
3. UI loop: preview → scan next / retake (`DELETE .../pages/{n}`) / reorder / finish.
4. `POST /api/scan/sessions/{id}/compile` with `{title, folder_id, tag_ids}` → creates the document (status = pending) carrying the session's `ocr_languages`, enqueues `process_document`, returns immediately. Page images stay in the session temp dir until the worker compiles the PDF into `$STORAGE_PATH/files/`, then the temp dir is removed.
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

Single job type `process_document`, dispatched by `doc_type`. OCR always uses the document's `ocr_languages` (pytesseract `lang='ita'`, `'eng'`, or `'ita+eng'`), chosen at scan/upload time:

| doc_type | Content extraction | LLM summary |
|---|---|---|
| scan | Per page: pytesseract via `image_to_pdf_or_hocr` → merge pages with pypdf into one searchable PDF (original images preserved as the visual layer) → extract per-page text | No (OCR text is the index) |
| pdf | pypdf text extraction; if the text layer is empty or garbage (heuristic: < 50 chars/page average) → rasterize with pdf2image → OCR → rebuild searchable PDF | Yes — summary of extracted text |
| text | Read plain text / markdown; python-docx for docx. Stored as-is, no PDF conversion | Yes — summary of content |
| image | Vision-model description via `describe()`; OCR additionally attempted and kept if it yields text | Yes — the vision description is the summary |
| video | None; status → ready immediately | No — metadata chunk only |

**LLM summary step (uploads except video):** `describe()` generates a semantic description, saved to `documents.summary` and indexed as chunk rows with `source = summary` — so it participates in keyword, semantic, and RAG search exactly like content text.

Common tail (all types; video gets metadata chunk only): chunk text (~1000 chars, 200 overlap, split on paragraph boundaries, page numbers tracked; `source = content`) + summary chunks (`source = summary`) + metadata chunk (`source = metadata`) → embed via `services/llm.py` (batched) → insert `chunks` rows → document status = ready.

### Failure handling

- Job exceptions → retry with exponential backoff, max 3 attempts → document status = failed with `error_message`; UI shows a failed badge and a "retry processing" action that re-enqueues.
- Pipeline stages persist intermediate results (extracted text saved on the document before embedding), so an embedding-API outage does not lose OCR work; retry resumes from the embedding stage.
- On backend startup, any document stuck in `processing` with no live job is re-enqueued.

## 6. Search & RAG

### Search — `POST /api/search`

Modes: `semantic | keyword | hybrid` (default hybrid).

- **Semantic:** embed query → pgvector cosine top-K over chunks (content, summary, and metadata chunks alike).
- **Keyword:** `websearch_to_tsquery('simple', q)` against `content_tsv`, ranked with `ts_rank`. The `simple` config is language-agnostic — documents mix Italian and English, so per-language stemming would misindex half the corpus; the semantic side of hybrid search compensates for the lost stemming.
- **Hybrid:** Reciprocal Rank Fusion of the two ranked lists (no tuning parameters).
- Filters: folder (subtree), tags, doc_type. Results grouped by document with highlighted snippets and page numbers.

### RAG chat — `POST /api/chat` (SSE streaming)

Retrieve top-8 hybrid chunks → prompt the LLM with numbered sources → answer in the user's language with `[n]` citations → response includes the source list linking to document + page. Stateless single-turn in v1.

**Grounding signal:** the user must always know whether an answer comes from their documents.

- If retrieval returns no chunk above a relevance floor, the response is flagged `grounded: false` and the UI shows a clear banner ("Answer not based on your documents") before a general-knowledge reply.
- The system prompt additionally instructs the model to state explicitly, inside the answer, when it draws on knowledge outside the provided sources — covering the mixed case where retrieval found something but the answer goes beyond it.
- Every response carries `grounded` + the list of sources actually cited; the frontend renders the banner and the source panel from these fields.

## 7. Frontend

React + Vite + TypeScript, shadcn/ui, TanStack Query, react-router. Pages:

- **Login.**
- **Dashboard / Browse** — folder tree sidebar, document grid/list, tag chips and filtering, drag-and-drop upload, live status badges (processing/failed) via polling.
- **Scan wizard** — OCR language selector up front (ita/eng/ita+eng), scanner status indicator, page-by-page loop with thumbnails, retake/reorder, compile form (title, folder, tags). Upload dialog gets the same language selector.
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
| Keyword search | Postgres FTS, `simple` config | Mixed ita/eng corpus; stemming for one language would break the other |
| Disk layout | Single flat `files/` dir, id-keyed names | Folders are virtual (DB-only); moving documents never touches disk |
| Upload indexing | LLM summary chunks (`source=summary`) | Makes images and poor-text uploads semantically searchable |
| RAG grounding | `grounded` flag + relevance floor + prompt rule | User always sees when an answer is not from their documents |
| Hybrid ranking | Reciprocal Rank Fusion | Robust, parameter-free |
| ORM | SQLModel + Alembic | Requested SQLAlchemy family + typed models |
| Auth | JWT bearer, CLI-created single user | External exposure via tunnel; no signup surface |
