# Origami Phase 2 — Ingestion Pipeline + Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Documents get INTO Origami — file upload and flatbed scanning both produce stored files plus a background pipeline run (OCR, text extraction, LLM summary, chunking, embeddings) that ends with a `ready`, searchable document.

**Architecture:** New service modules (`llm`, `chunking`, `ocr`, `extract`, `scanner`) each own one concern; a `process_document` job handler in `worker/pipeline.py` orchestrates them with idempotent stages so a retried job resumes instead of redoing completed work. Scanner access goes through a backend interface (default: `scanimage` subprocess) behind a process-wide lock. All heavy work runs in the Phase 1 worker; API endpoints only store files, create rows, and enqueue.

**Tech Stack:** LiteLLM (Gemini default, Ollama-swappable via env), pytesseract + Tesseract (`ita`/`eng`), pypdf, pdf2image (poppler), Pillow, python-docx, `scanimage` (SANE CLI), FastAPI multipart uploads.

**Spec:** `docs/superpowers/specs/2026-07-12-origami-dms-design.md` (§4 Scanning, §5 Ingestion, §2 LLM abstraction)

## System Prerequisites (verify before Task 1)

- `tesseract --version` works and `tesseract --list-langs` includes `ita` and `eng`. If missing: `sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng` (requires the human — a subagent must report BLOCKED, not attempt sudo).
- `pdftoppm -v` works (poppler — already present).
- `scanimage --version` works (sane-backends CLI — already present). A physical scanner is NOT required for any test.
- `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` exists (used to render OCR test fixtures — already present).

## Global Constraints

- Everything from Phase 1 still binds: real Postgres in tests (never mocked), error shape `{"error": {code, message, detail}}` via `api_error()` + the Phase 1 exception handlers, all routes under `/api/` behind JWT (`get_current_user`), Conventional Commits, pristine test output (no warnings).
- **The ONLY permitted mock boundary is `app/services/llm.py`** — tests monkeypatch `app.services.llm.embed` / `app.services.llm.describe` (or `litellm.*` inside `test_llm.py` itself). OCR, filesystem, DB, and pipeline logic run for real in tests.
- OCR languages come from the document's `ocr_languages` column (`ita`, `eng`, or `ita+eng`), chosen at scan-session creation or upload time; default from `Settings.default_ocr_languages`.
- Physical storage stays flat: final artifacts in `$STORAGE_PATH/files/` keyed by document id; in-progress scan pages under `$STORAGE_PATH/tmp/scan_sessions/{session_id}/`; DB stores paths relative to `$STORAGE_PATH`.
- Chunking: ~1000 chars, 200 overlap, paragraph-boundary splits, page numbers tracked; chunks never span pages. Chunk `source` is `content` / `summary` / `metadata` per the spec.
- LLM summary (`describe`) applies to uploaded pdf/text/image documents; scans index by OCR text only; video gets only the metadata chunk.
- Pipeline stages must be idempotent: a retried job skips stages whose output already exists (file already compiled, chunks already inserted, summary already set) and resumes at the first incomplete stage — an embedding-API outage must not redo OCR.
- Scanner error mapping (spec §4): offline→503 `scanner_offline`, busy→409 `scanner_busy`, jam→422 `scanner_jam`, cover open→422 `cover_open`, timeout (120 s watchdog)→504 `scanner_timeout`.
- **Sanctioned deviation from spec:** the default real scanner backend is a `scanimage` subprocess wrapper, not `python-sane` (python-sane requires a native build against libsane-dev; `scanimage` is already installed; the spec explicitly allows this swap). The backend interface keeps a python-sane implementation possible later.
- Git hygiene: stage specific files only, never `git add -A`/`.` (untracked `graphify-out/` must never be committed).
- Run tests with `cd backend && uv run pytest ...`; if `uv` is not on PATH use `.venv/bin/python -m pytest ...`.

## Interfaces inherited from Phase 1 (already on master)

- `app.services.storage.Storage`: `files_dir`, `tmp_scans_dir`, `abs_path(rel) -> Path`, `store_file(document_id, ext, data: bytes) -> (rel_path, size)`, `delete_document_file(rel)`, `scan_session_dir(session_id) -> Path`, `remove_scan_session_dir(session_id)`; `get_storage()` factory (dependency-overridable, NOT cached).
- `app.services.jobs`: `enqueue(session, job_type, payload, run_at=None) -> Job`, `claim_next`, `complete`, `fail`.
- `app.worker.runner`: `HANDLERS`, `register(job_type)`, `run_once(engine) -> bool`, `recover(engine)`, `main_loop(engine, poll_seconds=1.0)`; `app/worker/__main__.py` calls `main_loop(engine)`.
- `app.api.deps`: `api_error(status, code, message, detail=None) -> HTTPException`, `get_current_user`.
- `app.api.documents`: `serialize(session, doc) -> dict`, `get_doc_or_404(session, document_id)`.
- `app.api.error_handlers.register_error_handlers(app)` — flattens every error to top-level `{"error": {...}}` (including 422 validation).
- Models: `Document(id: UUID, title, description, summary, folder_id, doc_type, ocr_languages, status, error_message, original_filename, file_path, page_count, file_size, ...)`, `DocType(scan|pdf|text|image|video)`, `DocStatus(pending|processing|ready|failed)`, `Chunk(document_id, chunk_index, page_number, source, content, embedding)`, `ChunkSource(content|summary|metadata)`, `ScanSession(id, status, ocr_languages)`, `ScanSessionStatus(active|compiling|done|cancelled)`, `ScanPage(id, session_id, page_number, image_path)`, `Job`/`JobStatus`.
- Test fixtures (`backend/tests/conftest.py`): `engine`, `session`, `client`, `user`, `auth_client`, `storage` (tmp-path `Storage` wired into `dependency_overrides[get_storage]`).
- `Settings` (`app/config.py`): `llm_model`, `vision_model`, `embedding_model`, `embedding_dim`, `gemini_api_key`, `default_ocr_languages`, `storage_path`.

---

### Task 1: Phase 2 dependencies + LLM service

**Files:**
- Modify: `backend/pyproject.toml` (add dependencies)
- Create: `backend/app/services/llm.py`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings` (`embedding_model`, `embedding_dim`, `llm_model`, `vision_model`).
- Produces: `app.services.llm.embed(texts: list[str]) -> list[list[float]]` and `app.services.llm.describe(text: str | None = None, image_path: Path | None = None) -> str`. Every later task that needs LLM calls goes through these two functions and nothing else; tests everywhere else monkeypatch them.

- [ ] **Step 1: Verify system prerequisites**

Run: `tesseract --list-langs`
Expected: list including `ita` and `eng`. If tesseract is missing, STOP and report BLOCKED (human must `sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng`).

- [ ] **Step 2: Add dependencies**

In `backend/pyproject.toml`, extend `[project].dependencies` with:

```toml
    "litellm>=1.60",
    "pytesseract>=0.3.13",
    "pillow>=10.4",
    "pypdf>=5.1",
    "pdf2image>=1.17",
    "python-docx>=1.1",
    "python-multipart>=0.0.12",
```

Run: `cd backend && uv sync`
Expected: resolves and installs without errors.

- [ ] **Step 3: Write failing tests**

`backend/tests/test_llm.py`:

```python
from types import SimpleNamespace

import litellm

from app.services import llm


def test_embed_returns_vectors_in_input_order(monkeypatch):
    def fake_embedding(model, input, dimensions):
        assert model == "gemini/gemini-embedding-001"
        assert dimensions == 1536
        data = [{"index": i, "embedding": [float(i)] * 3} for i in range(len(input))]
        return SimpleNamespace(data=list(reversed(data)))  # out of order on purpose

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    vectors = llm.embed(["a", "b"])
    assert vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


def test_describe_text(monkeypatch):
    captured = {}

    def fake_completion(model, messages):
        captured["model"] = model
        captured["content"] = messages[0]["content"]
        msg = SimpleNamespace(content="  Una fattura del 2026.  ")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = llm.describe(text="FATTURA n. 42 del 2026...")
    assert result == "Una fattura del 2026."
    assert "FATTURA n. 42" in captured["content"]
    assert captured["model"] == "gemini/gemini-2.5-flash"


def test_describe_image(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}

    def fake_completion(model, messages):
        captured["model"] = model
        captured["parts"] = messages[0]["content"]
        msg = SimpleNamespace(content="A receipt photo.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = llm.describe(image_path=img)
    assert result == "A receipt photo."
    kinds = [p["type"] for p in captured["parts"]]
    assert kinds == ["text", "image_url"]
    assert captured["parts"][1]["image_url"]["url"].startswith("data:image/png;base64,")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: FAIL (ImportError: app.services.llm)

- [ ] **Step 5: Implement**

`backend/app/services/llm.py`:

```python
import base64
from pathlib import Path

import litellm

from app.config import get_settings

DESCRIBE_PROMPT = (
    "Describe this document for a searchable personal archive. In 2-4 sentences, "
    "in the document's own language, summarize what it is, its purpose, and key "
    "entities (dates, amounts, names, organizations)."
)


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    resp = litellm.embedding(
        model=settings.embedding_model, input=texts, dimensions=settings.embedding_dim
    )
    data = sorted(resp.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def describe(text: str | None = None, image_path: Path | None = None) -> str:
    settings = get_settings()
    if image_path is not None:
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content: str | list = [
            {"type": "text", "text": DESCRIBE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]
        model = settings.vision_model
    else:
        content = f"{DESCRIBE_PROMPT}\n\n---\n\n{(text or '')[:8000]}"
        model = settings.llm_model
    resp = litellm.completion(model=model, messages=[{"role": "user", "content": content}])
    return resp.choices[0].message.content.strip()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: 3 PASS. If importing litellm emits deprecation warnings from its own dependencies, add a narrowly-scoped `filterwarnings` entry to `backend/pyproject.toml` with a comment (same pattern as the existing starlette entry) — do not blanket-ignore.

- [ ] **Step 7: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/services/llm.py backend/tests/test_llm.py
git commit -m "feat: LiteLLM service wrapper with embed and describe"
```

---

### Task 2: Chunking service

**Files:**
- Create: `backend/app/services/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Interfaces:**
- Consumes: nothing (pure function module).
- Produces: `app.services.chunking.chunk_pages(pages: list[tuple[int | None, str]], size: int = 1000, overlap: int = 200) -> list[dict]` where each dict is `{"content": str, "page_number": int | None}`. Chunks never span pages. The pipeline (Task 6) assigns `chunk_index` itself from list position.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_chunking.py`:

```python
from app.services.chunking import chunk_pages


def test_empty_and_blank_pages_produce_nothing():
    assert chunk_pages([]) == []
    assert chunk_pages([(1, ""), (2, "   \n\n  ")]) == []


def test_short_page_is_single_chunk():
    chunks = chunk_pages([(1, "Breve testo di prova.")])
    assert chunks == [{"content": "Breve testo di prova.", "page_number": 1}]


def test_long_text_splits_with_overlap():
    paragraphs = [f"Paragrafo {i}. " + ("parola " * 40).strip() for i in range(10)]
    text = "\n\n".join(paragraphs)
    chunks = chunk_pages([(1, text)], size=500, overlap=100)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c["content"]) <= 500 + 100 + 2
        assert c["page_number"] == 1
    # overlap: each later chunk starts with the tail of the previous one
    tail = chunks[0]["content"][-100:]
    assert chunks[1]["content"].startswith(tail[: len(tail) // 2]) or tail in chunks[1]["content"]


def test_oversized_single_paragraph_is_hard_split():
    text = "x" * 2500
    chunks = chunk_pages([(None, text)], size=1000, overlap=200)
    assert len(chunks) >= 3
    assert all(len(c["content"]) <= 1200 for c in chunks)
    assert all(c["page_number"] is None for c in chunks)


def test_chunks_never_span_pages():
    chunks = chunk_pages([(1, "Pagina uno."), (2, "Pagina due.")])
    assert [c["page_number"] for c in chunks] == [1, 2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/services/chunking.py`:

```python
def chunk_pages(
    pages: list[tuple[int | None, str]], size: int = 1000, overlap: int = 200
) -> list[dict]:
    """Split page texts into ~size-char chunks on paragraph boundaries.

    Chunks never span pages, so every chunk carries an exact page_number.
    Consecutive chunks from the same page share `overlap` trailing/leading chars.
    """
    chunks: list[dict] = []
    for page_number, text in pages:
        for piece in _split_text(text, size, overlap):
            chunks.append({"content": piece, "page_number": page_number})
    return chunks


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    current = ""

    def flush() -> str:
        """Push current piece; return the overlap tail to seed the next one."""
        nonlocal current
        if not current:
            return ""
        pieces.append(current)
        tail = current[-overlap:] if overlap else ""
        current = ""
        return tail

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= size:
            current = candidate
            continue
        tail = flush()
        # hard-split paragraphs that alone exceed size
        while len(para) > size:
            pieces.append(f"{tail}\n\n{para[:size]}".strip() if tail else para[:size])
            para = para[size - overlap if overlap else size :] if overlap else para[size:]
            tail = ""
        current = f"{tail}\n\n{para}".strip() if tail else para
    if current:
        pieces.append(current)
    return pieces
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: 5 PASS. If the overlap/hard-split arithmetic makes an assertion fail, fix the implementation (the tests define the contract), not the test.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chunking.py backend/tests/test_chunking.py
git commit -m "feat: paragraph-aware page chunking with overlap"
```

---

### Task 3: Upload endpoint

**Files:**
- Modify: `backend/app/services/storage.py` (add `store_fileobj`)
- Create: `backend/app/api/uploads.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_uploads.py`, extend `backend/tests/test_storage.py`

**Interfaces:**
- Consumes: `Storage`/`get_storage`, `enqueue`, `api_error`, `get_current_user`, `serialize`, `Document`, `DocumentTag`, `Folder`, `Tag`, `DocType`, `DocStatus`, `Settings.default_ocr_languages`.
- Produces:
  - `Storage.store_fileobj(document_id: UUID, ext: str, fileobj: BinaryIO) -> tuple[str, int]` — streams to `files/{id}{ext}` without loading into memory; returns `(relative_path, size)`.
  - `app.api.uploads.EXTENSION_MAP: dict[str, DocType]` — `.pdf`→pdf; `.txt`/`.md`→text; `.docx`→text; `.png`/`.jpg`/`.jpeg`/`.tif`/`.tiff`/`.webp`→image; `.mp4`/`.mkv`/`.mov`/`.avi`/`.webm`→video.
  - `app.api.uploads.create_pending_document(session, *, title, doc_type, ocr_languages, folder_id, tag_ids, original_filename) -> Document` — validates folder/tags (404 via `api_error`), inserts document (status pending) + tag links, commits. Task 9's compile endpoint reuses this.
  - `POST /api/documents/upload` — multipart form: `file` (required), `title` (optional, default = filename stem), `folder_id` (optional int), `tag_ids` (optional comma-separated ints, e.g. `"1,3"`), `ocr_languages` (optional, default from settings). Returns 201 with the serialized document (status `pending`); stores the original file; enqueues `process_document` with payload `{"document_id": str(doc.id)}`. 422 `unsupported_type` for unknown extensions.

- [ ] **Step 1: Write failing storage test**

Append to `backend/tests/test_storage.py`:

```python
import io


def test_store_fileobj_streams_and_sizes(tmp_path):
    import uuid as uuid_mod

    from app.services.storage import Storage

    storage = Storage(tmp_path)
    doc_id = uuid_mod.uuid4()
    rel, size = storage.store_fileobj(doc_id, ".mp4", io.BytesIO(b"0123456789"))
    assert rel == f"files/{doc_id}.mp4"
    assert size == 10
    assert storage.abs_path(rel).read_bytes() == b"0123456789"
```

Run: `cd backend && uv run pytest tests/test_storage.py -v` — Expected: new test FAILS (AttributeError).

- [ ] **Step 2: Implement `store_fileobj`**

Add to the `Storage` class in `backend/app/services/storage.py`:

```python
    def store_fileobj(self, document_id: uuid.UUID, ext: str, fileobj) -> tuple[str, int]:
        name = f"{document_id}{ext}"
        path = self.files_dir / name
        with path.open("wb") as out:
            shutil.copyfileobj(fileobj, out)
        return f"files/{name}", path.stat().st_size
```

Run: `cd backend && uv run pytest tests/test_storage.py -v` — Expected: all PASS.

- [ ] **Step 3: Write failing upload tests**

`backend/tests/test_uploads.py`:

```python
from sqlmodel import select

from app.models import DocStatus, DocType, Job, Tag


def upload(client, filename, content=b"data", **form):
    return client.post(
        "/api/documents/upload",
        files={"file": (filename, content, "application/octet-stream")},
        data=form,
    )


def test_upload_pdf_creates_pending_document_and_job(auth_client, session, storage):
    resp = upload(auth_client, "bolletta.pdf", b"%PDF-1.7 fake")
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.pdf
    assert body["status"] == DocStatus.pending
    assert body["title"] == "bolletta"
    assert body["original_filename"] == "bolletta.pdf"
    assert storage.abs_path(body["file_path"]).read_bytes() == b"%PDF-1.7 fake"

    job = session.exec(select(Job)).one()
    assert job.type == "process_document"
    assert job.payload == {"document_id": body["id"]}


def test_upload_respects_form_fields(auth_client, session, storage):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    t1 = Tag(name="casa")
    session.add(t1)
    session.commit()

    resp = upload(
        auth_client, "foto.jpg", b"\xff\xd8fake",
        title="Foto contatore", folder_id=str(folder_id),
        tag_ids=str(t1.id), ocr_languages="eng",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.image
    assert body["title"] == "Foto contatore"
    assert body["folder_id"] == folder_id
    assert [t["id"] for t in body["tags"]] == [t1.id]
    assert body["ocr_languages"] == "eng"


def test_upload_video_and_text_types(auth_client, session, storage):
    assert upload(auth_client, "video.mp4").json()["doc_type"] == DocType.video
    assert upload(auth_client, "note.md").json()["doc_type"] == DocType.text
    assert upload(auth_client, "doc.docx").json()["doc_type"] == DocType.text


def test_upload_unsupported_extension_422(auth_client, storage):
    resp = upload(auth_client, "archive.zip")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unsupported_type"


def test_upload_bad_folder_404(auth_client, storage):
    resp = upload(auth_client, "a.pdf", folder_id="999999")
    assert resp.status_code == 404


def test_upload_requires_auth(client, storage):
    resp = upload(client, "a.pdf")
    assert resp.status_code == 401
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_uploads.py -v`
Expected: FAIL (404 — route missing)

- [ ] **Step 5: Implement the upload router**

`backend/app/api/uploads.py`:

```python
from pathlib import Path

from fastapi import APIRouter, Depends, Form, UploadFile
from sqlmodel import Session

from app.api.deps import api_error, get_current_user
from app.api.documents import serialize
from app.config import get_settings
from app.db import get_session
from app.models import DocType, Document, DocumentTag, Folder, Tag
from app.services.jobs import enqueue
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents", tags=["uploads"], dependencies=[Depends(get_current_user)]
)

EXTENSION_MAP: dict[str, DocType] = {
    ".pdf": DocType.pdf,
    ".txt": DocType.text,
    ".md": DocType.text,
    ".docx": DocType.text,
    ".png": DocType.image,
    ".jpg": DocType.image,
    ".jpeg": DocType.image,
    ".tif": DocType.image,
    ".tiff": DocType.image,
    ".webp": DocType.image,
    ".mp4": DocType.video,
    ".mkv": DocType.video,
    ".mov": DocType.video,
    ".avi": DocType.video,
    ".webm": DocType.video,
}


def create_pending_document(
    session: Session,
    *,
    title: str,
    doc_type: str,
    ocr_languages: str,
    folder_id: int | None,
    tag_ids: list[int],
    original_filename: str | None,
) -> Document:
    """Insert a pending document with validated folder/tags. Reused by scan compile."""
    if folder_id is not None and session.get(Folder, folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    for tag_id in tag_ids:
        if session.get(Tag, tag_id) is None:
            raise api_error(404, "not_found", f"Tag {tag_id} not found")
    doc = Document(
        title=title,
        doc_type=doc_type,
        ocr_languages=ocr_languages,
        folder_id=folder_id,
        original_filename=original_filename,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    for tag_id in tag_ids:
        session.add(DocumentTag(document_id=doc.id, tag_id=tag_id))
    session.commit()
    return doc


def parse_tag_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        return [int(part) for part in raw.split(",") if part.strip()]
    except ValueError:
        raise api_error(422, "validation_error", "tag_ids must be comma-separated integers")


@router.post("/upload", status_code=201)
def upload_document(
    file: UploadFile,
    title: str | None = Form(default=None),
    folder_id: int | None = Form(default=None),
    tag_ids: str | None = Form(default=None),
    ocr_languages: str | None = Form(default=None),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    doc_type = EXTENSION_MAP.get(ext)
    if doc_type is None:
        raise api_error(422, "unsupported_type", f"Unsupported file extension {ext!r}")

    doc = create_pending_document(
        session,
        title=title or Path(file.filename).stem,
        doc_type=doc_type,
        ocr_languages=ocr_languages or get_settings().default_ocr_languages,
        folder_id=folder_id,
        tag_ids=parse_tag_ids(tag_ids),
        original_filename=file.filename,
    )
    rel, size = storage.store_fileobj(doc.id, ext, file.file)
    doc.file_path = rel
    doc.file_size = size
    session.commit()

    enqueue(session, "process_document", {"document_id": str(doc.id)})
    session.refresh(doc)
    return serialize(session, doc)
```

In `backend/app/main.py`, add `uploads` to the router imports and `app.include_router(uploads.router)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_uploads.py tests/test_storage.py -v`
Expected: all PASS.

- [ ] **Step 7: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/api/uploads.py backend/app/services/storage.py backend/app/main.py backend/tests/test_uploads.py backend/tests/test_storage.py
git commit -m "feat: multipart upload endpoint with type detection and job enqueue"
```

---

### Task 4: OCR service (real Tesseract)

**Files:**
- Create: `backend/app/services/ocr.py`
- Create: `backend/tests/helpers.py`
- Test: `backend/tests/test_ocr.py`

**Interfaces:**
- Consumes: pytesseract, pypdf, pdf2image, Pillow.
- Produces:
  - `tests/helpers.make_text_image(path: Path, text: str = "FATTURA 2026", size: tuple[int, int] = (1200, 400)) -> Path` — renders `text` in large DejaVuSans-Bold onto a white PNG; used by every OCR/pipeline test.
  - `app.services.ocr.ocr_image(image_path: Path, languages: str) -> tuple[bytes, str]` — (single-page searchable PDF bytes, extracted text).
  - `app.services.ocr.images_to_searchable_pdf(image_paths: list[Path], languages: str) -> tuple[bytes, list[tuple[int, str]]]` — merged multi-page searchable PDF + per-page (1-based page_number, text).
  - `app.services.ocr.pdf_to_searchable_pdf(pdf_path: Path, languages: str, dpi: int = 300) -> tuple[bytes, list[tuple[int, str]]]` — rasterize (pdf2image) then OCR each page; for uploaded PDFs with no usable text layer.

- [ ] **Step 1: Write the fixture helper**

`backend/tests/helpers.py`:

```python
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def make_text_image(
    path: Path, text: str = "FATTURA 2026", size: tuple[int, int] = (1200, 400)
) -> Path:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.text((60, size[1] // 3), text, fill="black", font=ImageFont.truetype(FONT, 72))
    img.save(path)
    return path
```

- [ ] **Step 2: Write failing tests**

`backend/tests/test_ocr.py`:

```python
import io

from pypdf import PdfReader

from app.services.ocr import images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
from tests.helpers import make_text_image


def test_ocr_image_produces_searchable_pdf_and_text(tmp_path):
    img = make_text_image(tmp_path / "page.png", "FATTURA 2026")
    pdf_bytes, text = ocr_image(img, "ita+eng")
    assert "FATTURA" in text.upper()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    assert "FATTURA" in reader.pages[0].extract_text().upper()


def test_images_to_searchable_pdf_merges_pages_in_order(tmp_path):
    p1 = make_text_image(tmp_path / "p1.png", "PAGINA UNO")
    p2 = make_text_image(tmp_path / "p2.png", "PAGINA DUE")
    pdf_bytes, pages = images_to_searchable_pdf([p1, p2], "ita+eng")
    assert [n for n, _ in pages] == [1, 2]
    assert "UNO" in pages[0][1].upper()
    assert "DUE" in pages[1][1].upper()
    assert len(PdfReader(io.BytesIO(pdf_bytes)).pages) == 2


def test_pdf_to_searchable_pdf_roundtrip(tmp_path):
    img = make_text_image(tmp_path / "scan.png", "RICEVUTA 99")
    source_pdf_bytes, _ = ocr_image(img, "ita+eng")
    source = tmp_path / "source.pdf"
    source.write_bytes(source_pdf_bytes)

    pdf_bytes, pages = pdf_to_searchable_pdf(source, "ita+eng", dpi=150)
    assert len(pages) == 1
    assert "RICEVUTA" in pages[0][1].upper()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_ocr.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 4: Implement**

`backend/app/services/ocr.py`:

```python
import io
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from pypdf import PdfReader, PdfWriter


def _ocr_pil_image(image: Image.Image, languages: str) -> tuple[bytes, str]:
    pdf_page = pytesseract.image_to_pdf_or_hocr(image, lang=languages, extension="pdf")
    text = pytesseract.image_to_string(image, lang=languages)
    return pdf_page, text.strip()


def ocr_image(image_path: Path, languages: str) -> tuple[bytes, str]:
    with Image.open(image_path) as image:
        return _ocr_pil_image(image, languages)


def _merge(pdf_pages: list[bytes]) -> bytes:
    writer = PdfWriter()
    for page_bytes in pdf_pages:
        writer.append(PdfReader(io.BytesIO(page_bytes)))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def images_to_searchable_pdf(
    image_paths: list[Path], languages: str
) -> tuple[bytes, list[tuple[int, str]]]:
    pdf_pages: list[bytes] = []
    texts: list[tuple[int, str]] = []
    for number, path in enumerate(image_paths, start=1):
        page_pdf, text = ocr_image(path, languages)
        pdf_pages.append(page_pdf)
        texts.append((number, text))
    return _merge(pdf_pages), texts


def pdf_to_searchable_pdf(
    pdf_path: Path, languages: str, dpi: int = 300
) -> tuple[bytes, list[tuple[int, str]]]:
    images = convert_from_path(pdf_path, dpi=dpi)
    pdf_pages: list[bytes] = []
    texts: list[tuple[int, str]] = []
    for number, image in enumerate(images, start=1):
        page_pdf, text = _ocr_pil_image(image, languages)
        pdf_pages.append(page_pdf)
        texts.append((number, text))
    return _merge(pdf_pages), texts
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_ocr.py -v`
Expected: 3 PASS (a few seconds — real Tesseract).

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/services/ocr.py backend/tests/test_ocr.py backend/tests/helpers.py
git commit -m "feat: tesseract OCR service producing searchable PDFs"
```

---

### Task 5: Text extractors

**Files:**
- Create: `backend/app/services/extract.py`
- Test: `backend/tests/test_extract.py`

**Interfaces:**
- Consumes: pypdf, python-docx; `tests/helpers.make_text_image` + `ocr_image` (to build a PDF fixture with a real text layer).
- Produces:
  - `app.services.extract.extract_pdf_text(pdf_path: Path) -> list[tuple[int, str]]` — (1-based page_number, text) via pypdf.
  - `app.services.extract.pdf_needs_ocr(pages: list[tuple[int, str]]) -> bool` — True when average extracted chars/page < 50 (spec heuristic).
  - `app.services.extract.extract_text_file(path: Path) -> str` — reads `.txt`/`.md` as UTF-8 (`errors="replace"`).
  - `app.services.extract.extract_docx(path: Path) -> str` — paragraphs joined with `\n\n`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_extract.py`:

```python
import docx as docx_lib

from app.services.extract import (
    extract_docx,
    extract_pdf_text,
    extract_text_file,
    pdf_needs_ocr,
)
from app.services.ocr import ocr_image
from tests.helpers import make_text_image


def test_extract_pdf_text_reads_text_layer(tmp_path):
    img = make_text_image(tmp_path / "p.png", "CONTRATTO AFFITTO")
    pdf_bytes, _ = ocr_image(img, "ita+eng")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(pdf_bytes)

    pages = extract_pdf_text(pdf)
    assert len(pages) == 1
    assert pages[0][0] == 1
    assert "CONTRATTO" in pages[0][1].upper()


def test_pdf_needs_ocr_heuristic():
    assert pdf_needs_ocr([(1, ""), (2, "ab")]) is True
    assert pdf_needs_ocr([(1, "x" * 200)]) is False
    assert pdf_needs_ocr([]) is True


def test_extract_text_file(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# Titolo\n\nContenuto della nota.", encoding="utf-8")
    assert "Contenuto della nota." in extract_text_file(f)


def test_extract_docx(tmp_path):
    path = tmp_path / "doc.docx"
    d = docx_lib.Document()
    d.add_paragraph("Primo paragrafo.")
    d.add_paragraph("Secondo paragrafo.")
    d.save(path)
    text = extract_docx(path)
    assert "Primo paragrafo." in text
    assert "\n\n" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_extract.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/services/extract.py`:

```python
from pathlib import Path

import docx
from pypdf import PdfReader

MIN_CHARS_PER_PAGE = 50


def extract_pdf_text(pdf_path: Path) -> list[tuple[int, str]]:
    reader = PdfReader(pdf_path)
    return [
        (number, (page.extract_text() or "").strip())
        for number, page in enumerate(reader.pages, start=1)
    ]


def pdf_needs_ocr(pages: list[tuple[int, str]]) -> bool:
    if not pages:
        return True
    average = sum(len(text) for _, text in pages) / len(pages)
    return average < MIN_CHARS_PER_PAGE


def extract_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_docx(path: Path) -> str:
    document = docx.Document(path)
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_extract.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/extract.py backend/tests/test_extract.py
git commit -m "feat: pdf/text/docx extractors with needs-ocr heuristic"
```

---

### Task 6: Ingestion pipeline handler (upload types)

**Files:**
- Create: `backend/app/worker/pipeline.py`
- Modify: `backend/app/worker/__main__.py` (import pipeline so its handler registers)
- Modify: `backend/tests/conftest.py` (add `llm_stub` fixture)
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `register`, `Storage` (constructed from settings inside the handler — the worker has no FastAPI dependency injection), `llm.embed`/`llm.describe`, `chunking.chunk_pages`, `ocr.*`, `extract.*`, models.
- Produces:
  - `app.worker.pipeline.process_document(session: Session, payload: dict) -> None` registered as `HANDLERS["process_document"]`. Payload: `{"document_id": str}` (Task 9 adds `"scan_session_id"` for scans; this task raises a clear error for `doc_type == scan` until then).
  - Stage order and idempotence contract (each stage checks its own completion marker and skips):
    1. status → `processing` (always safe to repeat)
    2. **content extraction** per doc_type — marker: any `Chunk` with `source == content` exists (skip), or doc_type `video` (no content stage). For `pdf` when `pdf_needs_ocr` → rebuild the stored file as a searchable PDF (overwrite via `storage.store_file`) and update `page_count`. For `image` → OCR to a companion searchable PDF stored at `files/{id}.pdf` (original image stays `file_path`); OCR text becomes the content. For `text` → whole file, single pseudo-page `None`.
    3. **summary** (uploads except video/scan) — marker: `doc.summary is not None`. `describe(image_path=...)` for image (vision), `describe(text=first 8000 chars)` for pdf/text. Saved to `doc.summary` + one `Chunk(source=summary)`.
    4. **metadata chunk** — marker: `Chunk` with `source == metadata` exists. Content: `title` + `"\n\n"` + `description` (if any).
    5. **embedding** — all `Chunk` rows with `embedding IS NULL`, batched through one `llm.embed` call, then updated.
    6. status → `ready`, `error_message = None`.
  - On any exception: set `doc.status = failed` + `error_message = str(exc)[:2000]`, commit, then **re-raise** (so the Phase 1 runner's retry/backoff still applies; a successful retry overwrites `failed` back to `processing` → `ready`).
  - `tests/conftest.llm_stub` fixture — monkeypatches `app.worker.pipeline.llm_embed`/`llm_describe` references (see implementation: pipeline imports them as names) returning deterministic values and recording calls.

- [ ] **Step 1: Add the llm_stub fixture**

Append to `backend/tests/conftest.py`:

```python
@pytest.fixture
def llm_stub(monkeypatch):
    """Stub the ONLY sanctioned mock boundary: app.services.llm."""
    calls = {"embed": [], "describe": []}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1] * 1536 for _ in texts]

    def fake_describe(text=None, image_path=None):
        calls["describe"].append({"text": text, "image_path": image_path})
        return "Descrizione generata."

    monkeypatch.setattr("app.worker.pipeline.llm_embed", fake_embed)
    monkeypatch.setattr("app.worker.pipeline.llm_describe", fake_describe)
    return calls
```

- [ ] **Step 2: Write failing tests**

`backend/tests/test_pipeline.py`:

```python
import uuid

import pytest
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document
from app.services.ocr import ocr_image
from app.services.storage import Storage
from app.worker import pipeline
from tests.helpers import make_text_image


@pytest.fixture
def pipeline_storage(tmp_path, monkeypatch):
    s = Storage(tmp_path)
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: s)
    return s


def make_doc(session, **kwargs):
    doc = Document(
        title=kwargs.pop("title", "Doc"),
        doc_type=kwargs.pop("doc_type", DocType.text),
        ocr_languages=kwargs.pop("ocr_languages", "ita+eng"),
        **kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def run(session, doc):
    pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    return doc


def chunks_by_source(session, doc):
    rows = session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()
    out = {}
    for c in rows:
        out.setdefault(c.source, []).append(c)
    return out


def test_text_document_full_pipeline(session, pipeline_storage, llm_stub):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, size = pipeline_storage.store_file(doc.id, ".md", "Contenuto importante.\n\nAltro testo.".encode())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.summary == "Descrizione generata."
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.content in by_source
    assert ChunkSource.summary in by_source
    assert ChunkSource.metadata in by_source
    all_chunks = [c for group in by_source.values() for c in group]
    assert all(c.embedding is not None for c in all_chunks)
    assert len(llm_stub["embed"]) == 1  # one batched call


def test_image_document_ocr_and_vision(session, pipeline_storage, llm_stub, tmp_path):
    img = make_text_image(tmp_path / "src.png", "SCONTRINO 12")
    doc = make_doc(session, doc_type=DocType.image, title="Scontrino")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    # vision describe was called with the image path
    assert llm_stub["describe"][0]["image_path"] is not None
    # companion searchable PDF exists alongside the original
    assert pipeline_storage.abs_path(f"files/{doc.id}.pdf").exists()
    content = " ".join(c.content for c in chunks_by_source(session, doc)[ChunkSource.content])
    assert "SCONTRINO" in content.upper()


def test_pdf_without_text_layer_gets_ocr(session, pipeline_storage, llm_stub, tmp_path):
    # a PDF with no text layer: image-only via PIL save
    from PIL import Image

    img_path = make_text_image(tmp_path / "p.png", "PREVENTIVO 77")
    pdf_path = tmp_path / "raw.pdf"
    Image.open(img_path).save(pdf_path, "PDF")

    doc = make_doc(session, doc_type=DocType.pdf, title="Preventivo")
    rel, _ = pipeline_storage.store_file(doc.id, ".pdf", pdf_path.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 1
    content = " ".join(c.content for c in chunks_by_source(session, doc)[ChunkSource.content])
    assert "PREVENTIVO" in content.upper()


def test_video_document_metadata_only(session, pipeline_storage, llm_stub):
    doc = make_doc(session, doc_type=DocType.video, title="Video vacanze", description="Mare 2026")
    rel, _ = pipeline_storage.store_file(doc.id, ".mp4", b"fake video")
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.summary is None
    assert llm_stub["describe"] == []
    by_source = chunks_by_source(session, doc)
    assert list(by_source) == [ChunkSource.metadata]
    assert "Video vacanze" in by_source[ChunkSource.metadata][0].content


def test_pipeline_resumes_after_embedding_failure(session, pipeline_storage, llm_stub, monkeypatch):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, _ = pipeline_storage.store_file(doc.id, ".txt", b"Testo di prova.")
    doc.file_path = rel
    session.commit()

    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "embedding API down" in doc.error_message
    describe_calls_after_first_run = len(llm_stub["describe"])

    # retry with embedding working again: must NOT redo extraction/summary
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: [[0.2] * 1536 for _ in texts])
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert len(llm_stub["describe"]) == describe_calls_after_first_run  # summary not regenerated


def test_unknown_document_id_raises(session, pipeline_storage):
    with pytest.raises(ValueError):
        pipeline.process_document(session, {"document_id": str(uuid.uuid4())})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL (ImportError: app.worker.pipeline)

- [ ] **Step 4: Implement**

`backend/app/worker/pipeline.py`:

```python
import logging
from pathlib import Path

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document
from app.services.chunking import chunk_pages
from app.services.extract import (
    extract_docx,
    extract_pdf_text,
    extract_text_file,
    pdf_needs_ocr,
)
from app.services.llm import describe as llm_describe
from app.services.llm import embed as llm_embed
from app.services.ocr import ocr_image, pdf_to_searchable_pdf
from app.services.storage import Storage
from app.worker.runner import register

log = logging.getLogger("origami.pipeline")

SUMMARY_INPUT_CHARS = 8000


def get_pipeline_storage() -> Storage:
    """Worker-side storage factory (no FastAPI DI in the worker process)."""
    return Storage(get_settings().storage_path)


@register("process_document")
def process_document(session: Session, payload: dict) -> None:
    doc = session.get(Document, payload["document_id"])
    if doc is None:
        raise ValueError(f"Document {payload['document_id']} not found")
    storage = get_pipeline_storage()
    try:
        doc.status = DocStatus.processing
        session.commit()

        pages = _extract_content(session, doc, storage, payload)
        _ensure_content_chunks(session, doc, pages)
        _ensure_summary(session, doc, storage)
        _ensure_metadata_chunk(session, doc)
        _embed_pending_chunks(session, doc)

        doc.status = DocStatus.ready
        doc.error_message = None
        session.commit()
    except Exception as exc:
        session.rollback()
        doc.status = DocStatus.failed
        doc.error_message = str(exc)[:2000]
        session.commit()
        raise


def _has_chunks(session: Session, doc: Document, source: str) -> bool:
    return (
        session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == source)
        ).first()
        is not None
    )


def _extract_content(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    """Return page texts; skip (return []) if content chunks already exist."""
    if doc.doc_type == DocType.video or _has_chunks(session, doc, ChunkSource.content):
        return []
    path = storage.abs_path(doc.file_path)

    if doc.doc_type == DocType.text:
        text = extract_docx(path) if path.suffix == ".docx" else extract_text_file(path)
        return [(None, text)]

    if doc.doc_type == DocType.image:
        pdf_bytes, text = ocr_image(path, doc.ocr_languages)
        # companion searchable PDF alongside the original image
        storage.store_file(doc.id, ".pdf", pdf_bytes)
        return [(1, text)]

    if doc.doc_type == DocType.pdf:
        pages = extract_pdf_text(path)
        if pdf_needs_ocr(pages):
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
            doc.file_path = rel
            doc.file_size = size
        doc.page_count = len(pages)
        session.commit()
        return pages

    if doc.doc_type == DocType.scan:
        return _extract_scan(session, doc, storage, payload)  # Task 9

    raise ValueError(f"Unknown doc_type {doc.doc_type!r}")


def _extract_scan(session, doc, storage, payload):  # implemented in Task 9
    raise NotImplementedError("scan compilation lands in the scan-compile task")


def _ensure_content_chunks(
    session: Session, doc: Document, pages: list[tuple[int | None, str]]
) -> None:
    if not pages or _has_chunks(session, doc, ChunkSource.content):
        return
    next_index = 0
    for chunk in chunk_pages(pages):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index,
                page_number=chunk["page_number"],
                source=ChunkSource.content,
                content=chunk["content"],
            )
        )
        next_index += 1
    session.commit()


def _ensure_summary(session: Session, doc: Document, storage: Storage) -> None:
    if doc.doc_type in (DocType.video, DocType.scan) or doc.summary is not None:
        return
    if doc.doc_type == DocType.image:
        summary = llm_describe(image_path=storage.abs_path(doc.file_path))
    else:
        content_chunks = session.exec(
            select(Chunk)
            .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
            .order_by(Chunk.chunk_index)
        ).all()
        text = "\n\n".join(c.content for c in content_chunks)[:SUMMARY_INPUT_CHARS]
        if not text.strip():
            return
        summary = llm_describe(text=text)
    doc.summary = summary
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.summary,
            content=summary,
        )
    )
    session.commit()


def _ensure_metadata_chunk(session: Session, doc: Document) -> None:
    if _has_chunks(session, doc, ChunkSource.metadata):
        return
    content = doc.title if not doc.description else f"{doc.title}\n\n{doc.description}"
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.metadata,
            content=content,
        )
    )
    session.commit()


def _next_chunk_index(session: Session, doc: Document) -> int:
    rows = session.exec(select(Chunk.chunk_index).where(Chunk.document_id == doc.id)).all()
    return (max(rows) + 1) if rows else 0


def _embed_pending_chunks(session: Session, doc: Document) -> None:
    pending = session.exec(
        select(Chunk)
        .where(Chunk.document_id == doc.id, Chunk.embedding.is_(None))
        .order_by(Chunk.chunk_index)
    ).all()
    if not pending:
        return
    vectors = llm_embed([c.content for c in pending])
    for chunk, vector in zip(pending, vectors):
        chunk.embedding = vector
        session.add(chunk)
    session.commit()
```

Update `backend/app/worker/__main__.py`:

```python
import app.worker.pipeline  # noqa: F401  (registers the process_document handler)
from app.db import engine
from app.worker.runner import main_loop

main_loop(engine)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: 6 PASS (OCR tests take a few seconds).

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/worker/pipeline.py backend/app/worker/__main__.py backend/tests/test_pipeline.py backend/tests/conftest.py
git commit -m "feat: idempotent process_document pipeline for uploaded documents"
```

---

### Task 7: Scanner service

**Files:**
- Create: `backend/app/services/scanner.py`
- Test: `backend/tests/test_scanner.py`

**Interfaces:**
- Consumes: `subprocess`, `threading`; Pillow (FakeBackend generates PNGs).
- Produces:
  - Exceptions: `ScannerError(code: str, message: str, http_status: int)` base, with subclasses `ScannerOffline` (503/`scanner_offline`), `ScannerBusy` (409/`scanner_busy`), `ScannerJam` (422/`scanner_jam`), `CoverOpen` (422/`cover_open`), `ScannerTimeout` (504/`scanner_timeout`).
  - `ScannerBackend` protocol: `scan(dpi: int, mode: str) -> bytes` (PNG bytes) and `available() -> bool`.
  - `ScanimageBackend` — runs `scanimage --format=png --resolution {dpi} --mode {mode}` with a 120 s timeout, maps stderr to the exceptions above; `available()` probes `scanimage -L` (10 s timeout).
  - `FakeScannerBackend(pages: list[str] | None = None, error: ScannerError | None = None)` — `scan()` raises `error` if set, else returns a generated PNG (rendered text per call, cycling `pages`); used by all API tests.
  - `scan_locked(backend, dpi=300, mode="Color") -> bytes` — module-level `threading.Lock`; if already held raises `ScannerBusy` immediately (non-blocking).
  - `get_scanner() -> ScannerBackend` — module-level singleton (default `ScanimageBackend()`), replaceable via FastAPI `dependency_overrides`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_scanner.py`:

```python
import threading

import pytest

from app.services import scanner
from app.services.scanner import (
    FakeScannerBackend,
    ScannerBusy,
    ScannerJam,
    ScannerOffline,
    ScannerTimeout,
    ScanimageBackend,
    scan_locked,
)


def test_fake_backend_returns_png():
    backend = FakeScannerBackend(pages=["PAGINA UNO"])
    data = backend.scan(dpi=300, mode="Color")
    assert data.startswith(b"\x89PNG")


def test_fake_backend_scripted_error():
    backend = FakeScannerBackend(error=ScannerJam())
    with pytest.raises(ScannerJam):
        backend.scan(dpi=300, mode="Color")


def test_scan_locked_rejects_concurrent_use():
    backend = FakeScannerBackend()
    acquired = scanner._scan_lock.acquire()
    assert acquired
    try:
        with pytest.raises(ScannerBusy):
            scan_locked(backend)
    finally:
        scanner._scan_lock.release()
    # once released, scanning works again
    assert scan_locked(backend).startswith(b"\x89PNG")


def test_scanimage_stderr_mapping():
    m = ScanimageBackend._map_error
    assert isinstance(m("no SANE devices found", 1), ScannerOffline)
    assert isinstance(m("sane_start: Device busy", 1), ScannerBusy)
    assert isinstance(m("sane_start: Document feeder jammed", 1), ScannerJam)
    assert isinstance(m("sane_start: Cover open", 1), type(m("cover open", 1)))
    assert m("anything else", 1).code == "scanner_error"


def test_scanimage_timeout_maps(monkeypatch):
    import subprocess

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="scanimage", timeout=120)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ScannerTimeout):
        ScanimageBackend().scan(dpi=300, mode="Color")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scanner.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/services/scanner.py`:

```python
import io
import subprocess
import threading
from itertools import cycle
from typing import Protocol

from PIL import Image, ImageDraw

SCAN_TIMEOUT_SECONDS = 120
PROBE_TIMEOUT_SECONDS = 10


class ScannerError(Exception):
    code = "scanner_error"
    http_status = 500

    def __init__(self, message: str = ""):
        self.message = message or self.__class__.__doc__ or self.code
        super().__init__(self.message)


class ScannerOffline(ScannerError):
    """Scanner not found - check power and USB connection."""
    code = "scanner_offline"
    http_status = 503


class ScannerBusy(ScannerError):
    """Scanner is busy with another operation."""
    code = "scanner_busy"
    http_status = 409


class ScannerJam(ScannerError):
    """Paper jam detected."""
    code = "scanner_jam"
    http_status = 422


class CoverOpen(ScannerError):
    """Scanner cover is open."""
    code = "cover_open"
    http_status = 422


class ScannerTimeout(ScannerError):
    """Scan timed out - try power-cycling the scanner."""
    code = "scanner_timeout"
    http_status = 504


class ScannerBackend(Protocol):
    def scan(self, dpi: int, mode: str) -> bytes: ...
    def available(self) -> bool: ...


class ScanimageBackend:
    """Drives a SANE scanner via the scanimage CLI (sanctioned python-sane swap)."""

    @staticmethod
    def _map_error(stderr: str, returncode: int) -> ScannerError:
        lowered = stderr.lower()
        if "no sane devices" in lowered or "invalid argument" in lowered:
            return ScannerOffline(stderr.strip())
        if "device busy" in lowered:
            return ScannerBusy(stderr.strip())
        if "jam" in lowered:
            return ScannerJam(stderr.strip())
        if "cover open" in lowered:
            return CoverOpen(stderr.strip())
        return ScannerError(stderr.strip() or f"scanimage exited {returncode}")

    def scan(self, dpi: int, mode: str) -> bytes:
        try:
            result = subprocess.run(
                ["scanimage", "--format=png", f"--resolution={dpi}", f"--mode={mode}"],
                capture_output=True,
                timeout=SCAN_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ScannerTimeout()
        if result.returncode != 0:
            raise self._map_error(result.stderr.decode(errors="replace"), result.returncode)
        return result.stdout

    def available(self) -> bool:
        try:
            result = subprocess.run(
                ["scanimage", "-L"], capture_output=True, timeout=PROBE_TIMEOUT_SECONDS
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        return result.returncode == 0 and b"device" in result.stdout.lower()


class FakeScannerBackend:
    """Test double: renders labelled PNGs or raises a scripted error."""

    def __init__(self, pages: list[str] | None = None, error: ScannerError | None = None):
        self._labels = cycle(pages or ["SCAN"])
        self._error = error

    def scan(self, dpi: int, mode: str) -> bytes:
        if self._error is not None:
            raise self._error
        img = Image.new("RGB", (600, 200), "white")
        ImageDraw.Draw(img).text((20, 80), next(self._labels), fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()

    def available(self) -> bool:
        return self._error is None or not isinstance(self._error, ScannerOffline)


_scan_lock = threading.Lock()


def scan_locked(backend: ScannerBackend, dpi: int = 300, mode: str = "Color") -> bytes:
    if not _scan_lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.scan(dpi=dpi, mode=mode)
    finally:
        _scan_lock.release()


_default_backend: ScannerBackend = ScanimageBackend()


def get_scanner() -> ScannerBackend:
    return _default_backend
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scanner.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scanner.py backend/tests/test_scanner.py
git commit -m "feat: scanner backend interface with scanimage impl and fake"
```

---

### Task 8: Scan API — sessions, pages, previews, errors

**Files:**
- Create: `backend/app/api/scan.py`
- Modify: `backend/app/api/error_handlers.py` (ScannerError handler)
- Modify: `backend/app/main.py` (include router)
- Modify: `backend/tests/conftest.py` (add `fake_scanner` fixture)
- Test: `backend/tests/test_scan_api.py`

**Interfaces:**
- Consumes: `scanner` service (Task 7), `Storage`/`get_storage`, `ScanSession`/`ScanPage`/`ScanSessionStatus` models, `api_error`, `get_current_user`, `Settings.default_ocr_languages`.
- Produces (all under JWT):
  - `GET /api/scan/status` → `{"available": bool, "busy": bool}` (busy = scan lock currently held).
  - `POST /api/scan/sessions` `{ocr_languages?}` → 201 session `{id, status, ocr_languages, created_at}`.
  - `POST /api/scan/sessions/{id}/pages` `{dpi?: int = 300, mode?: str = "Color"}` → 201 `{id, page_number, preview_url}`; scans one page via `scan_locked(get_scanner(), ...)`, saves PNG at `tmp/scan_sessions/{sid}/page_{n:03d}.png` (path stored relative in `ScanPage.image_path`), 409 `session_not_active` if session isn't `active`.
  - `GET /api/scan/pages/{page_id}/preview` → PNG `FileResponse`.
  - `DELETE /api/scan/pages/{page_id}` → 204; deletes file + row and renumbers later pages down by one (files are NOT renamed — `image_path` stays authoritative; only `page_number` shifts).
  - `POST /api/scan/sessions/{id}/reorder` `{page_ids: [int, ...]}` → 200; must be exactly the session's page ids; reassigns `page_number` 1..N in given order; 422 `invalid_order` otherwise.
  - `DELETE /api/scan/sessions/{id}` → 204; sets status `cancelled`, deletes page rows, removes temp dir.
  - `app.api.scan.get_session_or_404(db, session_id) -> ScanSession` (Task 9 reuses).
  - ScannerError exception handler in `error_handlers.py`: any `ScannerError` → `JSONResponse(status_code=err.http_status, content={"error": {"code": err.code, "message": err.message, "detail": None}})`.
  - `tests/conftest.fake_scanner` fixture: overrides `get_scanner` with a `FakeScannerBackend()`, yields the backend (tests can swap `backend._error`).

- [ ] **Step 1: Add the fake_scanner fixture**

Append to `backend/tests/conftest.py`:

```python
@pytest.fixture
def fake_scanner(client):
    from app.main import app as main_app
    from app.services.scanner import FakeScannerBackend, get_scanner

    backend = FakeScannerBackend()
    main_app.dependency_overrides[get_scanner] = lambda: backend
    yield backend
    main_app.dependency_overrides.pop(get_scanner, None)
```

- [ ] **Step 2: Write failing tests**

`backend/tests/test_scan_api.py`:

```python
from app.services.scanner import ScannerOffline


def new_session(auth_client, **body):
    return auth_client.post("/api/scan/sessions", json=body)


def test_create_session_default_languages(auth_client, fake_scanner, storage):
    resp = new_session(auth_client)
    assert resp.status_code == 201
    assert resp.json()["ocr_languages"] == "ita+eng"
    assert resp.json()["status"] == "active"


def test_scan_pages_and_preview(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, ocr_languages="ita").json()["id"]
    p1 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p2 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    assert (p1["page_number"], p2["page_number"]) == (1, 2)

    preview = auth_client.get(p1["preview_url"])
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG")


def test_scanner_offline_maps_to_503(auth_client, fake_scanner, storage):
    fake_scanner._error = ScannerOffline()
    sid = new_session(auth_client).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "scanner_offline"


def test_delete_page_renumbers(auth_client, fake_scanner, storage):
    sid = new_session(auth_client).json()["id"]
    p1 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p2 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p3 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()

    assert auth_client.delete(f"/api/scan/pages/{p2['id']}").status_code == 204
    remaining = {p3["id"]: 2, p1["id"]: 1}
    for page_id, expected_number in remaining.items():
        preview = auth_client.get(f"/api/scan/pages/{page_id}/preview")
        assert preview.status_code == 200
    # renumbering verified through reorder round-trip below


def test_reorder_pages(auth_client, fake_scanner, storage):
    sid = new_session(auth_client).json()["id"]
    ids = [
        auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()["id"]
        for _ in range(3)
    ]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/reorder", json={"page_ids": ids[::-1]})
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()["pages"]] == ids[::-1]

    bad = auth_client.post(f"/api/scan/sessions/{sid}/reorder", json={"page_ids": ids[:2]})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_order"


def test_cancel_session_purges(auth_client, fake_scanner, storage, session):
    from app.models import ScanPage
    from sqlmodel import select

    sid = new_session(auth_client).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert (storage.tmp_scans_dir / str(sid)).exists()

    assert auth_client.delete(f"/api/scan/sessions/{sid}").status_code == 204
    assert not (storage.tmp_scans_dir / str(sid)).exists()
    assert session.exec(select(ScanPage).where(ScanPage.session_id == sid)).all() == []


def test_status_endpoint(auth_client, fake_scanner, storage):
    resp = auth_client.get("/api/scan/status")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "busy": False}


def test_scan_requires_auth(client, fake_scanner, storage):
    assert client.get("/api/scan/status").status_code == 401
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scan_api.py -v`
Expected: FAIL (404s)

- [ ] **Step 4: Implement**

Add to `backend/app/api/error_handlers.py` (inside `register_error_handlers(app)`):

```python
    from app.services.scanner import ScannerError

    @app.exception_handler(ScannerError)
    async def scanner_error_handler(request, exc: ScannerError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "detail": None}},
        )
```

`backend/app/api/scan.py`:

```python
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.config import get_settings
from app.db import get_session
from app.models import ScanPage, ScanSession, ScanSessionStatus
from app.services.scanner import ScannerBackend, get_scanner, scan_locked
from app.services import scanner as scanner_module
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/scan", tags=["scan"], dependencies=[Depends(get_current_user)]
)


class SessionCreate(BaseModel):
    ocr_languages: str | None = None


class PageScanRequest(BaseModel):
    dpi: int = 300
    mode: str = "Color"


class ReorderRequest(BaseModel):
    page_ids: list[int]


def get_session_or_404(db: Session, session_id: int) -> ScanSession:
    scan_session = db.get(ScanSession, session_id)
    if scan_session is None:
        raise api_error(404, "not_found", f"Scan session {session_id} not found")
    return scan_session


def session_pages(db: Session, session_id: int) -> list[ScanPage]:
    return list(
        db.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        )
    )


@router.get("/status")
def scan_status(backend: ScannerBackend = Depends(get_scanner)) -> dict:
    return {
        "available": backend.available(),
        "busy": scanner_module._scan_lock.locked(),
    }


@router.post("/sessions", status_code=201)
def create_session(
    body: SessionCreate, db: Session = Depends(get_session)
) -> ScanSession:
    scan_session = ScanSession(
        ocr_languages=body.ocr_languages or get_settings().default_ocr_languages
    )
    db.add(scan_session)
    db.commit()
    db.refresh(scan_session)
    return scan_session


@router.post("/sessions/{session_id}/pages", status_code=201)
def scan_page(
    session_id: int,
    body: PageScanRequest,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    backend: ScannerBackend = Depends(get_scanner),
) -> dict:
    scan_session = get_session_or_404(db, session_id)
    if scan_session.status != ScanSessionStatus.active:
        raise api_error(409, "session_not_active", "Scan session is not active")

    png = scan_locked(backend, dpi=body.dpi, mode=body.mode)

    number = len(session_pages(db, session_id)) + 1
    filename = f"page_{number:03d}.png"
    (storage.scan_session_dir(session_id) / filename).write_bytes(png)
    page = ScanPage(
        session_id=session_id,
        page_number=number,
        image_path=f"tmp/scan_sessions/{session_id}/{filename}",
    )
    db.add(page)
    db.commit()
    db.refresh(page)
    return {
        "id": page.id,
        "page_number": page.page_number,
        "preview_url": f"/api/scan/pages/{page.id}/preview",
    }


@router.get("/pages/{page_id}/preview")
def page_preview(
    page_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    page = db.get(ScanPage, page_id)
    if page is None:
        raise api_error(404, "not_found", f"Scan page {page_id} not found")
    return FileResponse(storage.abs_path(page.image_path), media_type="image/png")


@router.delete("/pages/{page_id}", status_code=204)
def delete_page(
    page_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    page = db.get(ScanPage, page_id)
    if page is None:
        raise api_error(404, "not_found", f"Scan page {page_id} not found")
    storage.abs_path(page.image_path).unlink(missing_ok=True)
    session_id, removed_number = page.session_id, page.page_number
    db.delete(page)
    db.commit()
    for later in session_pages(db, session_id):
        if later.page_number > removed_number:
            later.page_number -= 1
            db.add(later)
    db.commit()


@router.post("/sessions/{session_id}/reorder")
def reorder_pages(
    session_id: int, body: ReorderRequest, db: Session = Depends(get_session)
) -> dict:
    get_session_or_404(db, session_id)
    pages = session_pages(db, session_id)
    if sorted(body.page_ids) != sorted(p.id for p in pages):
        raise api_error(422, "invalid_order", "page_ids must be exactly the session's pages")
    by_id = {p.id: p for p in pages}
    # two-phase renumber to dodge any (session, page_number) collisions mid-update
    for offset, page_id in enumerate(body.page_ids):
        by_id[page_id].page_number = 1000 + offset
        db.add(by_id[page_id])
    db.commit()
    for offset, page_id in enumerate(body.page_ids, start=1):
        by_id[page_id].page_number = offset
        db.add(by_id[page_id])
    db.commit()
    return {"pages": [{"id": p.id, "page_number": p.page_number} for p in session_pages(db, session_id)]}


@router.delete("/sessions/{session_id}", status_code=204)
def cancel_session(
    session_id: int,
    db: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    scan_session = get_session_or_404(db, session_id)
    for page in session_pages(db, session_id):
        db.delete(page)
    scan_session.status = ScanSessionStatus.cancelled
    db.commit()
    storage.remove_scan_session_dir(session_id)
```

In `backend/app/main.py`, add `scan` to the router imports and `app.include_router(scan.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scan_api.py -v`
Expected: 8 PASS.

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/api/scan.py backend/app/api/error_handlers.py backend/app/main.py backend/tests/test_scan_api.py backend/tests/conftest.py
git commit -m "feat: scan session API with previews, reorder, and scanner error mapping"
```

---

### Task 9: Scan compile + pipeline scan branch + session sweep

**Files:**
- Modify: `backend/app/api/scan.py` (compile endpoint)
- Modify: `backend/app/worker/pipeline.py` (replace `_extract_scan` stub; add sweep handler)
- Modify: `backend/app/worker/__main__.py` (schedule sweep on startup)
- Test: `backend/tests/test_scan_compile.py`

**Interfaces:**
- Consumes: `create_pending_document` (Task 3), `images_to_searchable_pdf` (Task 4), `enqueue`, `llm_stub` fixture, `fake_scanner` fixture, `run_once`.
- Produces:
  - `POST /api/scan/sessions/{id}/compile` `{title: str, folder_id?: int, tag_ids?: [int]}` → 201 serialized document (status `pending`, doc_type `scan`, `ocr_languages` from the session); session status → `compiling`; enqueues `process_document` with `{"document_id": ..., "scan_session_id": ...}`. 409 `session_not_active` if not `active`; 422 `no_pages` if the session has zero pages.
  - Pipeline `_extract_scan`: loads the session's pages in `page_number` order, `images_to_searchable_pdf` with `doc.ocr_languages`, stores the PDF (`store_file(doc.id, ".pdf", ...)` → sets `file_path`/`file_size`/`page_count`), marks the scan session `done`, removes the temp dir, returns page texts. Idempotent: if `doc.file_path` is already set (retry after partial failure), re-reads nothing and returns `[]` only when content chunks exist; if the temp dir is gone but the PDF exists, extract text from the stored PDF instead of re-OCRing.
  - `sweep_scan_sessions` handler registered in `pipeline.py`: purges sessions with status `done`/`cancelled` (rows + temp dirs) and cancels+purges `active`/`compiling` sessions older than 24 h with no pending/running compile job; re-enqueues itself with `run_at = now + 1h`. `ensure_sweep_scheduled(engine)` enqueues one if none queued/running; called from `__main__.py`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_scan_compile.py`:

```python
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models import (
    Chunk,
    ChunkSource,
    DocStatus,
    DocType,
    Document,
    Job,
    JobStatus,
    ScanSession,
    ScanSessionStatus,
)
from app.worker import pipeline
from app.worker.runner import run_once


def scanned_session(auth_client, pages=2):
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "ita+eng"}).json()["id"]
    for _ in range(pages):
        auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    return sid


def test_compile_creates_document_and_job(auth_client, fake_scanner, storage, session):
    sid = scanned_session(auth_client)
    resp = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "Bolletta"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.scan
    assert body["status"] == DocStatus.pending
    assert body["ocr_languages"] == "ita+eng"

    assert session.get(ScanSession, sid).status == ScanSessionStatus.compiling
    job = session.exec(select(Job)).one()
    assert job.payload == {"document_id": body["id"], "scan_session_id": sid}


def test_compile_empty_session_422(auth_client, fake_scanner, storage):
    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "X"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_pages"


def test_worker_compiles_scan_end_to_end(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    fake_scanner._labels = iter(["PAGINA UNO", "PAGINA DUE"])  # deterministic page text
    sid = scanned_session(auth_client, pages=2)
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "Documento"}
    ).json()["id"]

    assert run_once(engine) is True

    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 2
    assert doc.file_path == f"files/{doc.id}.pdf"
    assert storage.abs_path(doc.file_path).exists()
    assert doc.summary is None  # scans get no LLM summary
    session.expire_all()
    assert session.get(ScanSession, sid).status == ScanSessionStatus.done
    assert not (storage.tmp_scans_dir / str(sid)).exists()
    sources = {c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))}
    assert sources == {ChunkSource.content, ChunkSource.metadata}


def test_sweep_purges_old_sessions(session, storage, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    old = ScanSession(
        status=ScanSessionStatus.active,
        created_at=datetime.now(timezone.utc) - timedelta(hours=30),
    )
    done = ScanSession(status=ScanSessionStatus.done)
    session.add(old)
    session.add(done)
    session.commit()
    storage.scan_session_dir(old.id)

    pipeline.sweep_scan_sessions(session, {})

    assert session.get(ScanSession, old.id) is None
    assert session.get(ScanSession, done.id) is None
    assert not (storage.tmp_scans_dir / str(old.id)).exists()
    # sweep re-scheduled itself
    from app.models import Job as JobModel

    jobs = session.exec(select(JobModel).where(JobModel.type == "sweep_scan_sessions")).all()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.queued


def test_ensure_sweep_scheduled_is_idempotent(session, engine):
    pipeline.ensure_sweep_scheduled(engine)
    pipeline.ensure_sweep_scheduled(engine)
    jobs = session.exec(select(Job).where(Job.type == "sweep_scan_sessions")).all()
    assert len(jobs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scan_compile.py -v`
Expected: FAIL (404 on compile route / AttributeError on sweep)

- [ ] **Step 3: Implement the compile endpoint**

Add to `backend/app/api/scan.py`:

```python
from app.api.documents import serialize
from app.api.uploads import create_pending_document
from app.models import DocType
from app.services.jobs import enqueue


class CompileRequest(BaseModel):
    title: str
    folder_id: int | None = None
    tag_ids: list[int] = []


@router.post("/sessions/{session_id}/compile", status_code=201)
def compile_session(
    session_id: int, body: CompileRequest, db: Session = Depends(get_session)
) -> dict:
    scan_session = get_session_or_404(db, session_id)
    if scan_session.status != ScanSessionStatus.active:
        raise api_error(409, "session_not_active", "Scan session is not active")
    if not session_pages(db, session_id):
        raise api_error(422, "no_pages", "Scan session has no pages to compile")

    doc = create_pending_document(
        db,
        title=body.title,
        doc_type=DocType.scan,
        ocr_languages=scan_session.ocr_languages,
        folder_id=body.folder_id,
        tag_ids=body.tag_ids,
        original_filename=None,
    )
    scan_session.status = ScanSessionStatus.compiling
    db.commit()
    enqueue(
        db,
        "process_document",
        {"document_id": str(doc.id), "scan_session_id": session_id},
    )
    return serialize(db, doc)
```

- [ ] **Step 4: Implement the scan pipeline branch and sweep**

In `backend/app/worker/pipeline.py`, replace the `_extract_scan` stub with:

```python
def _extract_scan(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    from app.models import ScanPage, ScanSession, ScanSessionStatus

    session_id = payload["scan_session_id"]
    if doc.file_path and storage.abs_path(doc.file_path).exists():
        # retry after the PDF was already built: recover text from the stored PDF
        pages = extract_pdf_text(storage.abs_path(doc.file_path))
    else:
        page_rows = session.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        ).all()
        image_paths = [storage.abs_path(p.image_path) for p in page_rows]
        pdf_bytes, pages = images_to_searchable_pdf(image_paths, doc.ocr_languages)
        rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
        doc.file_path = rel
        doc.file_size = size
    doc.page_count = len(pages)
    scan_session = session.get(ScanSession, session_id)
    if scan_session is not None:
        scan_session.status = ScanSessionStatus.done
    session.commit()
    storage.remove_scan_session_dir(session_id)
    return pages
```

Add the missing imports at the top of `pipeline.py`: `from app.services.ocr import images_to_searchable_pdf` (extend the existing ocr import line) and `from datetime import datetime, timedelta, timezone`.

Add the sweep handler and scheduler to `pipeline.py`:

```python
SWEEP_INTERVAL = timedelta(hours=1)
SESSION_MAX_AGE = timedelta(hours=24)


@register("sweep_scan_sessions")
def sweep_scan_sessions(session: Session, payload: dict) -> None:
    from app.models import ScanPage, ScanSession, ScanSessionStatus
    from app.services.jobs import enqueue

    storage = get_pipeline_storage()
    cutoff = datetime.now(timezone.utc) - SESSION_MAX_AGE
    for scan_session in session.exec(select(ScanSession)).all():
        finished = scan_session.status in (
            ScanSessionStatus.done,
            ScanSessionStatus.cancelled,
        )
        created = scan_session.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        abandoned = not finished and created < cutoff
        if not (finished or abandoned):
            continue
        for page in session.exec(
            select(ScanPage).where(ScanPage.session_id == scan_session.id)
        ).all():
            session.delete(page)
        session.delete(scan_session)
        session.commit()
        storage.remove_scan_session_dir(scan_session.id)

    enqueue(
        session,
        "sweep_scan_sessions",
        {},
        run_at=datetime.now(timezone.utc) + SWEEP_INTERVAL,
    )


def ensure_sweep_scheduled(engine) -> None:
    from app.models import Job, JobStatus
    from app.services.jobs import enqueue

    with Session(engine) as session:
        existing = session.exec(
            select(Job).where(
                Job.type == "sweep_scan_sessions",
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
        ).first()
        if existing is None:
            enqueue(session, "sweep_scan_sessions", {})
```

Update `backend/app/worker/__main__.py`:

```python
import app.worker.pipeline  # noqa: F401  (registers handlers)
from app.db import engine
from app.worker.pipeline import ensure_sweep_scheduled
from app.worker.runner import main_loop

ensure_sweep_scheduled(engine)
main_loop(engine)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scan_compile.py -v`
Expected: 5 PASS. Note `test_sweep_purges_old_sessions` asserts exactly 1 queued sweep job — the handler enqueues its successor; nothing else should.

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/api/scan.py backend/app/worker/pipeline.py backend/app/worker/__main__.py backend/tests/test_scan_compile.py
git commit -m "feat: scan compile endpoint, worker scan branch, session sweep job"
```

---

## Phase 2 exit criteria

- `uv run pytest` fully green, 0 warnings, against real Postgres + real Tesseract.
- Manual smoke (human, optional if no scanner attached): `POST /api/documents/upload` with a real PDF → document reaches `ready` with chunks + summary once the worker runs; `GET /api/scan/status` reports scanner availability truthfully.
- Worker startup (`python -m app.worker`) registers `process_document` + `sweep_scan_sessions` and schedules exactly one sweep.
- Phase 3 (search + RAG) builds on: populated `chunks.embedding` vectors, `content_tsv` generated column, `llm.embed`/`llm.describe`/`litellm.completion` via `services/llm.py`.
