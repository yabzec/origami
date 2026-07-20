# Origami Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** External access from another machine, a no-OCR toggle, scanner device discovery + fast preview, image-summary token optimization, and provider-agnostic LLM config — all on the existing Origami app.

**Architecture:** Small, mostly-additive changes across existing modules. One Alembic migration adds three columns. The scanner service gains device listing + a preview method; the pipeline honors a new `ocr_enabled` flag; `llm.py` gains explicit api_key/api_base passthrough. Frontend adds two toggles, a device dropdown, and a preview button — none touching the tested scan reducer.

**Tech Stack:** FastAPI, SQLModel, Alembic, LiteLLM, pytesseract/pypdf/pdf2image/Pillow, scanimage CLI; React/Vite/TypeScript, TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-07-20-enhancements-design.md`

## Global Constraints

- Backend rules bind: real Postgres in tests (never mocked), **only `app/services/llm.py` is a mock boundary**, error envelope `{"error": {code, message, detail}}` via `api_error()`, all routes under `/api/` behind JWT, pristine pytest output, Conventional Commits.
- Frontend: vitest + Testing Library, colocated `*.test.ts(x)`, `fetch` mocking allowed, `npm test` = `vitest run`, pristine output. No `dangerouslySetInnerHTML`.
- No hardcoded IPs/hosts in the frontend — API client stays on relative `/api/...`; Vite `allowedHosts: true`.
- Preview scan strictly uses fast params `--resolution=75 --mode=Gray` (vs full `300`/`Color`).
- Image summary uses OCR/native **text** when combined content-chunk text is **≥ 40 characters**; only otherwise send the image file to the vision model.
- Multi-provider must be drop-in: passing `None` when a key/base is unset preserves LiteLLM's existing provider-env fallback (existing `GEMINI_API_KEY` setups keep working).
- Backend tests: `cd backend && uv run pytest ...` (fallback `.venv/bin/python -m pytest`). Frontend: `cd frontend && npm test` / `npx tsc -b`.
- Git hygiene: stage named files only, never `git add -A`/`.` (untracked `graphify-out/` must never be committed).

## Sequencing

Tasks are ordered so the blocking connectivity fix ships first: **§1 (Task 1)** → **§2 no-OCR (Tasks 2–6)** → **§3 scanner (Tasks 7–10)** → **§4 AI (Tasks 11–12)**.

---

### Task 1: External connectivity — CORS + Vite host

**Files:**
- Modify: `backend/app/config.py` (add `cors_origins`)
- Modify: `backend/app/main.py` (CORS middleware)
- Modify: `frontend/vite.config.ts` (host + allowedHosts)
- Test: `backend/tests/test_cors.py`

**Interfaces:**
- Produces: `Settings.cors_origins: str` (comma-separated, default `"*"`); `CORSMiddleware` on the app echoing `access-control-allow-origin`.

- [ ] **Step 1: Write the failing CORS test**

`backend/tests/test_cors.py`:

```python
def test_cors_header_present_on_health(client):
    resp = client.get("/api/health", headers={"Origin": "http://example.test"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_cors_preflight_allowed(client):
    resp = client.options(
        "/api/auth/login",
        headers={
            "Origin": "http://example.test",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
    assert resp.headers.get("access-control-allow-origin") == "*"
```

- [ ] **Step 2: Run it, expect failure**

Run: `cd backend && uv run pytest tests/test_cors.py -v`
Expected: FAIL (no `access-control-allow-origin` header).

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, add to `Settings` (after `rag_relevance_floor`):

```python
    cors_origins: str = "*"
```

- [ ] **Step 4: Add the middleware**

In `backend/app/main.py`, after `app = FastAPI(title="Origami")` and before `register_error_handlers(app)`:

```python
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

(Place the two imports with the existing top-of-file imports; keep the middleware call adjacent to app creation.)

- [ ] **Step 5: Run the test, expect pass**

Run: `cd backend && uv run pytest tests/test_cors.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Update Vite config**

Replace the `server` block in `frontend/vite.config.ts`:

```ts
  server: {
    host: true,
    allowedHosts: true,
    proxy: { "/api": "http://localhost:8000" },
  },
```

- [ ] **Step 7: Typecheck + full backend suite, then commit**

Run: `cd frontend && npx tsc -b` (expect clean) and `cd backend && uv run pytest` (expect all pass, 0 warnings).

```bash
git add backend/app/config.py backend/app/main.py backend/tests/test_cors.py frontend/vite.config.ts
git commit -m "feat: configurable CORS and external-host Vite config"
```

---

### Task 2: Migration + models (`ocr_enabled`, `device`)

**Files:**
- Modify: `backend/app/models/document.py` (add `ocr_enabled`)
- Modify: `backend/app/models/scan.py` (add `ocr_enabled`, `device`)
- Create: `backend/alembic/versions/<generated>_add_ocr_enabled_and_device.py`
- Test: extend `backend/tests/test_schema.py`

**Interfaces:**
- Produces: `Document.ocr_enabled: bool = True`; `ScanSession.ocr_enabled: bool = True`, `ScanSession.device: str | None = None`. Columns: `documents.ocr_enabled`, `scan_sessions.ocr_enabled` (both `BOOLEAN NOT NULL DEFAULT true`), `scan_sessions.device` (`VARCHAR NULL`). `device` is consumed by Task 8; added here since one migration owns schema.

- [ ] **Step 1: Write the failing schema test**

Append to `backend/tests/test_schema.py`:

```python
def test_ocr_enabled_and_device_columns(engine):
    from sqlalchemy import inspect

    inspector = inspect(engine)
    doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    assert "ocr_enabled" in doc_cols
    session_cols = {c["name"] for c in inspector.get_columns("scan_sessions")}
    assert {"ocr_enabled", "device"} <= session_cols
```

- [ ] **Step 2: Run it, expect failure**

Run: `cd backend && uv run pytest tests/test_schema.py::test_ocr_enabled_and_device_columns -v`
Expected: FAIL (columns absent).

- [ ] **Step 3: Edit the models**

`backend/app/models/document.py` — add after `ocr_languages`:

```python
    ocr_enabled: bool = True
```

`backend/app/models/scan.py` — in `ScanSession`, add after `ocr_languages`:

```python
    ocr_enabled: bool = True
    device: str | None = None
```

- [ ] **Step 4: Create the migration (manual, not autogenerate)**

Run: `cd backend && uv run alembic revision -m "add ocr_enabled and device"`

In the generated file, set the body:

```python
import sqlalchemy as sa
from alembic import op

def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "scan_sessions",
        sa.Column("ocr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("scan_sessions", sa.Column("device", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_sessions", "device")
    op.drop_column("scan_sessions", "ocr_enabled")
    op.drop_column("documents", "ocr_enabled")
```

Confirm `down_revision = '9f9c707d06be'` (the current head) is set by alembic. Keep `server_default=sa.true()` so existing rows backfill to true; the SQLModel default handles new inserts.

- [ ] **Step 5: Apply + run the test**

Run: `cd backend && uv run alembic upgrade head` (expect no error), then
`cd backend && uv run pytest tests/test_schema.py -v` (expect PASS — the `engine` fixture migrates a fresh test DB).

- [ ] **Step 6: Full suite, then commit**

Run: `cd backend && uv run pytest` (all pass, 0 warnings).

```bash
git add backend/app/models/document.py backend/app/models/scan.py backend/alembic/versions/ backend/tests/test_schema.py
git commit -m "feat: ocr_enabled and scanner device columns"
```

---

### Task 3: `ocr.images_to_pdf` helper (image-only PDF)

**Files:**
- Modify: `backend/app/services/ocr.py` (add `images_to_pdf`)
- Test: extend `backend/tests/test_ocr.py`

**Interfaces:**
- Consumes: `tests/helpers.make_text_image`.
- Produces: `app.services.ocr.images_to_pdf(image_paths: list[Path]) -> bytes` — a PDF embedding the images as pages, **no OCR text layer**. Used by Task 4's no-OCR scan branch.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_ocr.py`:

```python
def test_images_to_pdf_has_pages_but_no_text(tmp_path):
    import io

    from pypdf import PdfReader

    from app.services.ocr import images_to_pdf
    from tests.helpers import make_text_image

    p1 = make_text_image(tmp_path / "a.png", "PAGINA UNO")
    p2 = make_text_image(tmp_path / "b.png", "PAGINA DUE")
    pdf_bytes = images_to_pdf([p1, p2])
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 2
    # image-only: no extractable text layer
    assert (reader.pages[0].extract_text() or "").strip() == ""
```

- [ ] **Step 2: Run it, expect failure**

Run: `cd backend && uv run pytest tests/test_ocr.py::test_images_to_pdf_has_pages_but_no_text -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

Append to `backend/app/services/ocr.py`:

```python
def images_to_pdf(image_paths: list[Path]) -> bytes:
    """Embed images as PDF pages with no OCR text layer (no-OCR scans)."""
    images = [Image.open(p).convert("RGB") for p in image_paths]
    try:
        out = io.BytesIO()
        images[0].save(out, "PDF", save_all=True, append_images=images[1:])
        return out.getvalue()
    finally:
        for image in images:
            image.close()
```

- [ ] **Step 4: Run the test, expect pass**

Run: `cd backend && uv run pytest tests/test_ocr.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ocr.py backend/tests/test_ocr.py
git commit -m "feat: images_to_pdf helper for no-OCR scans"
```

---

### Task 4: Pipeline honors `ocr_enabled`

**Files:**
- Modify: `backend/app/worker/pipeline.py` (`_extract_content`, `_extract_scan`)
- Test: extend `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Document.ocr_enabled` (Task 2), `ocr.images_to_pdf` (Task 3).
- Produces: no-OCR behavior — image: no content, no companion PDF; pdf: native text only (no OCR fallback); scan: image-only PDF, no content. `_ensure_summary` is NOT changed here (its image branch already uses vision, which is correct for a no-OCR image; Task 11 refines it).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_pipeline.py` (reuse the file's existing `make_doc`, `run`, `chunks_by_source`, `pipeline_storage`, `llm_stub` helpers/fixtures):

```python
def test_no_ocr_image_skips_ocr_and_has_no_content(session, pipeline_storage, llm_stub, tmp_path):
    from PIL import Image as PILImage

    from app.models import ChunkSource, DocStatus, DocType
    from tests.helpers import make_text_image

    img = make_text_image(tmp_path / "src.png", "SCONTRINO 12")
    doc = make_doc(session, doc_type=DocType.image, title="Foto", ocr_enabled=False)
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.content not in by_source        # no OCR text
    assert not pipeline_storage.abs_path(f"files/{doc.id}.pdf").exists()  # no companion pdf
    assert llm_stub["describe"][0]["image_path"] is not None  # vision summary (photo path)


def test_no_ocr_pdf_uses_native_text_only(session, pipeline_storage, llm_stub, tmp_path):
    # image-only PDF (no text layer): with OCR it would be OCR'd; no-OCR must NOT OCR it.
    from PIL import Image as PILImage

    from app.models import ChunkSource, DocStatus, DocType
    from tests.helpers import make_text_image

    img = make_text_image(tmp_path / "p.png", "PREVENTIVO 77")
    raw_pdf = tmp_path / "raw.pdf"
    PILImage.open(img).convert("RGB").save(raw_pdf, "PDF")
    doc = make_doc(session, doc_type=DocType.pdf, title="Prev", ocr_enabled=False)
    rel, _ = pipeline_storage.store_file(doc.id, ".pdf", raw_pdf.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    # native extraction of an image-only pdf yields no usable text → no content chunks
    assert ChunkSource.content not in chunks_by_source(session, doc)
```

(The no-OCR *scan* branch of `_extract_scan` is implemented in this task but its end-to-end test lives in Task 5, where the scan API accepts `ocr_enabled` — so the test can go green in one task.)

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_pipeline.py -k "no_ocr" -v`
Expected: FAIL (`make_doc` doesn't accept `ocr_enabled`; pipeline still OCRs).

- [ ] **Step 3: Implement — replace `_extract_content` and `_extract_scan`**

In `backend/app/worker/pipeline.py`, replace the `_extract_content` body's per-type branches and `_extract_scan` with `ocr_enabled`-aware versions. Full replacement of both functions:

```python
def _extract_content(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    """Return page texts; skip (return []) if content chunks already exist."""
    if doc.doc_type == DocType.video or _has_chunks(session, doc, ChunkSource.content):
        return []

    if doc.doc_type == DocType.scan:
        return _extract_scan(session, doc, storage, payload)

    path = storage.abs_path(doc.file_path)

    if doc.doc_type == DocType.text:
        text = extract_docx(path) if path.suffix == ".docx" else extract_text_file(path)
        return [(None, text)]

    if doc.doc_type == DocType.image:
        if not doc.ocr_enabled:
            return []  # photo path: no OCR, no companion pdf; summary via vision
        pdf_bytes, text = ocr_image(path, doc.ocr_languages)
        storage.store_file(doc.id, ".pdf", pdf_bytes)  # companion searchable PDF
        return [(1, text)]

    if doc.doc_type == DocType.pdf:
        pages = extract_pdf_text(path)
        if doc.ocr_enabled and pdf_needs_ocr(pages):
            pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
            rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
            doc.file_path = rel
            doc.file_size = size
        doc.page_count = len(pages)
        session.commit()
        return pages

    raise ValueError(f"Unknown doc_type {doc.doc_type!r}")


def _extract_scan(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    from app.models import ScanPage, ScanSession, ScanSessionStatus

    session_id = payload["scan_session_id"]
    if doc.file_path and storage.abs_path(doc.file_path).exists():
        pages = extract_pdf_text(storage.abs_path(doc.file_path))
    else:
        page_rows = session.exec(
            select(ScanPage)
            .where(ScanPage.session_id == session_id)
            .order_by(ScanPage.page_number)
        ).all()
        image_paths = [storage.abs_path(p.image_path) for p in page_rows]
        if doc.ocr_enabled:
            pdf_bytes, pages = images_to_searchable_pdf(image_paths, doc.ocr_languages)
        else:
            pdf_bytes = images_to_pdf(image_paths)
            pages = []
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

Add `images_to_pdf` to the ocr import at the top of `pipeline.py`:

```python
from app.services.ocr import images_to_pdf, images_to_searchable_pdf, ocr_image, pdf_to_searchable_pdf
```

Extend the test helper `make_doc` in `test_pipeline.py` to pass through `ocr_enabled` (it already does `**kwargs` into `Document(...)`, so `ocr_enabled=False` flows through — confirm; if `make_doc` pops specific kwargs, add `ocr_enabled` to the passthrough).

- [ ] **Step 4: Run the no-OCR image + pdf tests, expect pass**

Run: `cd backend && uv run pytest tests/test_pipeline.py -k "no_ocr" -v`
Expected: PASS (both).

- [ ] **Step 5: Full pipeline suite, then commit**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v` (existing OCR-on tests still pass).

```bash
git add backend/app/worker/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: pipeline honors ocr_enabled for image/pdf/scan"
```

---

### Task 5: Thread `ocr_enabled` through upload + scan APIs

**Files:**
- Modify: `backend/app/api/uploads.py` (`create_pending_document`, `upload_document`)
- Modify: `backend/app/api/scan.py` (`SessionCreate`, `create_session`, `compile_session`)
- Test: extend `backend/tests/test_uploads.py`, `backend/tests/test_scan_compile.py`

**Interfaces:**
- Consumes: `Document.ocr_enabled`, `ScanSession.ocr_enabled` (Task 2).
- Produces: `create_pending_document(..., ocr_enabled: bool = True)`; `upload` accepts `ocr_enabled` form field; `POST /api/scan/sessions` accepts `ocr_enabled` (+ `device`, wired in Task 8); compile copies `scan_session.ocr_enabled` onto the document.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_uploads.py`:

```python
def test_upload_ocr_disabled(auth_client, session, storage):
    from app.models import Document

    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF-1.7 x", "application/octet-stream")},
        data={"ocr_enabled": "false"},
    )
    assert resp.status_code == 201
    doc = session.get(Document, resp.json()["id"])
    assert doc.ocr_enabled is False


def test_upload_ocr_enabled_default_true(auth_client, session, storage):
    from app.models import Document

    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("b.pdf", b"%PDF-1.7 x", "application/octet-stream")},
    )
    doc = session.get(Document, resp.json()["id"])
    assert doc.ocr_enabled is True
```

Append to `backend/tests/test_scan_compile.py`:

```python
def test_session_and_compiled_doc_carry_ocr_enabled(auth_client, fake_scanner, storage, session):
    from app.models import Document, ScanSession

    sid = auth_client.post("/api/scan/sessions", json={"ocr_enabled": False}).json()["id"]
    assert session.get(ScanSession, sid).ocr_enabled is False
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "X"}
    ).json()["id"]
    assert session.get(Document, doc_id).ocr_enabled is False
```

Append the no-OCR scan end-to-end test to `backend/tests/test_pipeline.py` (its `_extract_scan` no-OCR branch was implemented in Task 4; this exercises it through the now-wired scan API):

```python
def test_no_ocr_scan_builds_image_only_pdf(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    from app.models import ChunkSource, DocStatus, Document
    from app.worker import pipeline
    from app.worker.runner import run_once

    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    sid = auth_client.post(
        "/api/scan/sessions", json={"ocr_languages": "ita+eng", "ocr_enabled": False}
    ).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "ScanNoOcr"}
    ).json()["id"]

    assert run_once(engine) is True
    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert storage.abs_path(doc.file_path).exists()
    assert ChunkSource.content not in chunks_by_source(session, doc)
```

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_uploads.py::test_upload_ocr_disabled tests/test_scan_compile.py::test_session_and_compiled_doc_carry_ocr_enabled "tests/test_pipeline.py::test_no_ocr_scan_builds_image_only_pdf" -v`
Expected: FAIL (fields ignored / not stored).

- [ ] **Step 3: Edit `uploads.py`**

Add `ocr_enabled` param to `create_pending_document` (after `ocr_languages`):

```python
    ocr_enabled: bool = True,
```

and set it on the `Document(...)` construction:

```python
    doc = Document(
        title=title,
        doc_type=doc_type,
        ocr_languages=ocr_languages,
        ocr_enabled=ocr_enabled,
        folder_id=folder_id,
        original_filename=original_filename,
    )
```

Add the form field to `upload_document` (after `ocr_languages`):

```python
    ocr_enabled: bool = Form(default=True),
```

and pass it into the `create_pending_document(...)` call:

```python
        ocr_enabled=ocr_enabled,
```

- [ ] **Step 4: Edit `scan.py`**

Extend `SessionCreate`:

```python
class SessionCreate(BaseModel):
    ocr_languages: str | None = None
    ocr_enabled: bool = True
    device: str | None = None
```

Set both on session creation in `create_session`:

```python
    scan_session = ScanSession(
        ocr_languages=body.ocr_languages or get_settings().default_ocr_languages,
        ocr_enabled=body.ocr_enabled,
        device=body.device,
    )
```

In `compile_session`, pass the session's flag into `create_pending_document`:

```python
    doc = create_pending_document(
        db,
        title=body.title,
        doc_type=DocType.scan,
        ocr_languages=scan_session.ocr_languages,
        ocr_enabled=scan_session.ocr_enabled,
        folder_id=body.folder_id,
        tag_ids=body.tag_ids,
        original_filename=None,
    )
```

(`device` is stored now but only *used* by scanning in Task 8 — harmless until then.)

- [ ] **Step 5: Run tests, expect pass; then the deferred Task 4 scan test**

Run: `cd backend && uv run pytest tests/test_uploads.py tests/test_scan_compile.py "tests/test_pipeline.py::test_no_ocr_scan_builds_image_only_pdf" -v`
Expected: all PASS (the Task 4 scan-no-ocr test now works end-to-end).

- [ ] **Step 6: Full suite, then commit**

Run: `cd backend && uv run pytest` (all pass, 0 warnings).

```bash
git add backend/app/api/uploads.py backend/app/api/scan.py backend/tests/test_uploads.py backend/tests/test_scan_compile.py
git commit -m "feat: thread ocr_enabled through upload and scan APIs"
```

---

### Task 6: Frontend no-OCR toggle

**Files:**
- Modify: `frontend/src/lib/upload.ts` (`UploadFields`, `buildUploadForm`)
- Modify: `frontend/src/components/UploadDialog.tsx`
- Test: extend `frontend/src/lib/upload.test.ts`

**Interfaces:**
- Consumes: upload endpoint's `ocr_enabled` form field (Task 5).
- Produces: `UploadFields.ocrEnabled?: boolean`; `buildUploadForm` appends `ocr_enabled` only when explicitly `false` (default-true stays implicit). `UploadDialog` "Run OCR" checkbox.

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/lib/upload.test.ts`:

```ts
it("appends ocr_enabled=false only when OCR is disabled", () => {
  const file = new File([new Uint8Array([1])], "a.pdf", { type: "application/pdf" });
  expect(buildUploadForm(file, {}).has("ocr_enabled")).toBe(false);
  expect(buildUploadForm(file, { ocrEnabled: true }).has("ocr_enabled")).toBe(false);
  expect(buildUploadForm(file, { ocrEnabled: false }).get("ocr_enabled")).toBe("false");
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `cd frontend && npm test -- upload.test`
Expected: FAIL.

- [ ] **Step 3: Edit `upload.ts`**

Add to `UploadFields`:

```ts
  ocrEnabled?: boolean;
```

Append inside `buildUploadForm` (before `return form;`):

```ts
  if (fields.ocrEnabled === false) form.append("ocr_enabled", "false");
```

- [ ] **Step 4: Run it, expect pass**

Run: `cd frontend && npm test -- upload.test`
Expected: PASS.

- [ ] **Step 5: Edit `UploadDialog.tsx`**

Add state (after the `languages` state):

```tsx
  const [ocrEnabled, setOcrEnabled] = useState(true);
```

Pass it into `buildUploadForm` in `submit`:

```tsx
      buildUploadForm(file, {
        title: title || fileStem(file.name),
        folderId,
        tagIds,
        ocrLanguages: languages,
        ocrEnabled,
      }),
```

Replace the "OCR language" `<div>` block with a toggle that gates the language selector:

```tsx
        <div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={ocrEnabled} onChange={(e) => setOcrEnabled(e.target.checked)} />
            Run OCR (extract text)
          </label>
        </div>
        {ocrEnabled && (
          <div>
            <Label htmlFor="up-lang">OCR language</Label>
            <Select id="up-lang" value={languages} onChange={(e) => setLanguages(e.target.value)}>
              <option value="ita+eng">Italian + English</option>
              <option value="ita">Italian</option>
              <option value="eng">English</option>
            </Select>
          </div>
        )}
```

- [ ] **Step 6: Full frontend suite + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b` (all pass, clean).

```bash
git add frontend/src/lib/upload.ts frontend/src/lib/upload.test.ts frontend/src/components/UploadDialog.tsx
git commit -m "feat: no-OCR toggle in upload dialog"
```

---

### Task 7: Scanner device discovery

**Files:**
- Modify: `backend/app/services/scanner.py` (`parse_scanimage_devices`, `list_devices` on both backends)
- Modify: `backend/app/api/scan.py` (`GET /api/scan/devices`)
- Test: extend `backend/tests/test_scanner.py`, `backend/tests/test_scan_api.py`

**Interfaces:**
- Produces: `app.services.scanner.parse_scanimage_devices(output: str) -> list[dict]` (`[{"id","name"}]`); `ScannerBackend.list_devices() -> list[dict]`; `ScanimageBackend.list_devices()` runs `scanimage -L`; `FakeScannerBackend.list_devices()` returns a scripted list (constructor arg `devices`); `GET /api/scan/devices` → `{"devices": [...], "default": <first id | null>}`.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_scanner.py`:

```python
def test_parse_scanimage_devices():
    from app.services.scanner import parse_scanimage_devices

    out = (
        "device `net:192.168.1.9:pixma:MG5700' is a CANON MG5700 flatbed scanner\n"
        "device 'epson2:libusb:001:004' is a Epson Perfection V39 flatbed scanner\n"
        "garbage line without the pattern\n"
    )
    devices = parse_scanimage_devices(out)
    assert devices == [
        {"id": "net:192.168.1.9:pixma:MG5700", "name": "CANON MG5700 flatbed scanner"},
        {"id": "epson2:libusb:001:004", "name": "Epson Perfection V39 flatbed scanner"},
    ]


def test_parse_scanimage_devices_empty():
    from app.services.scanner import parse_scanimage_devices

    assert parse_scanimage_devices("No scanners were identified.\n") == []


def test_fake_backend_list_devices():
    from app.services.scanner import FakeScannerBackend

    backend = FakeScannerBackend(devices=[{"id": "fake:0", "name": "Fake Scanner"}])
    assert backend.list_devices() == [{"id": "fake:0", "name": "Fake Scanner"}]
```

Append to `backend/tests/test_scan_api.py`:

```python
def test_devices_endpoint(auth_client, fake_scanner, storage):
    resp = auth_client.get("/api/scan/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["devices"], list)
    assert "default" in body
```

Note: the `fake_scanner` conftest fixture creates `FakeScannerBackend()`; extend it (see Step 3) to seed one device so `default` is that device's id.

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_scanner.py -k devices tests/test_scan_api.py::test_devices_endpoint -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `scanner.py`**

Add the pure parser (module level; `scanimage -L` lines look like ``device `id' is a NAME`` — quotes may be backtick+apostrophe or straight):

```python
import re

_DEVICE_RE = re.compile(r"^device\s+[`'\"](?P<id>[^`'\"]+)['\"]?\s+is a\s+(?P<name>.+?)\s*$")


def parse_scanimage_devices(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        match = _DEVICE_RE.match(line.strip())
        if match:
            devices.append({"id": match.group("id"), "name": match.group("name")})
    return devices
```

Add `list_devices` to the `ScannerBackend` Protocol:

```python
    def list_devices(self) -> list[dict]: ...
```

Add to `ScanimageBackend`:

```python
    def list_devices(self) -> list[dict]:
        try:
            result = subprocess.run(
                ["scanimage", "-L"], capture_output=True, timeout=PROBE_TIMEOUT_SECONDS
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        return parse_scanimage_devices(result.stdout.decode(errors="replace"))
```

Extend `FakeScannerBackend.__init__` to accept `devices` and add `list_devices`:

```python
    def __init__(
        self,
        pages: list[str] | None = None,
        error: ScannerError | None = None,
        devices: list[dict] | None = None,
    ):
        self._labels = cycle(pages or ["SCAN"])
        self._error = error
        self._devices = devices if devices is not None else [{"id": "fake:0", "name": "Fake Scanner"}]

    def list_devices(self) -> list[dict]:
        return list(self._devices)
```

- [ ] **Step 4: Add the endpoint + seed the fixture**

In `backend/app/api/scan.py`, add:

```python
@router.get("/devices")
def scan_devices(backend: ScannerBackend = Depends(get_scanner)) -> dict:
    devices = backend.list_devices()
    return {"devices": devices, "default": devices[0]["id"] if devices else None}
```

The `fake_scanner` fixture in `backend/tests/conftest.py` already constructs `FakeScannerBackend()`; the new default `devices` (one fake device) makes `default` non-null. No fixture change needed unless it passed explicit args — confirm.

- [ ] **Step 5: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/test_scanner.py tests/test_scan_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `cd backend && uv run pytest`.

```bash
git add backend/app/services/scanner.py backend/app/api/scan.py backend/tests/test_scanner.py backend/tests/test_scan_api.py
git commit -m "feat: scanner device discovery endpoint"
```

---

### Task 8: Device threading into scans

**Files:**
- Modify: `backend/app/services/scanner.py` (`scan`, `scan_locked` device param)
- Modify: `backend/app/api/scan.py` (`scan_page` passes session device)
- Test: extend `backend/tests/test_scanner.py`

**Interfaces:**
- Consumes: `ScanSession.device` (Task 2), `SessionCreate.device` (Task 5).
- Produces: `ScanimageBackend.scan(dpi, mode, device=None)` (adds `-d <id>`), `scan_locked(backend, dpi=300, mode="Color", device=None)`, `FakeScannerBackend.scan(dpi, mode, device=None)`.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_scanner.py`:

```python
def test_scanimage_scan_includes_device(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().scan(dpi=300, mode="Color", device="epson2:libusb:001:004")
    assert "-d" in captured["argv"]
    assert "epson2:libusb:001:004" in captured["argv"]


def test_scanimage_scan_omits_device_when_none(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().scan(dpi=300, mode="Color")
    assert "-d" not in captured["argv"]
```

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_scanner.py -k "includes_device or omits_device" -v`
Expected: FAIL (`scan` has no `device` param).

- [ ] **Step 3: Implement**

Replace `ScanimageBackend.scan`:

```python
    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        argv = ["scanimage", "--format=png", f"--resolution={dpi}", f"--mode={mode}"]
        if device:
            argv += ["-d", device]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=SCAN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ScannerTimeout()
        if result.returncode != 0:
            raise self._map_error(result.stderr.decode(errors="replace"), result.returncode)
        return result.stdout
```

Update the Protocol and Fake:

```python
class ScannerBackend(Protocol):
    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes: ...
    def available(self) -> bool: ...
    def list_devices(self) -> list[dict]: ...
```

```python
    def scan(self, dpi: int, mode: str, device: str | None = None) -> bytes:
        if self._error is not None:
            raise self._error
        img = Image.new("RGB", (600, 200), "white")
        ImageDraw.Draw(img).text((20, 80), next(self._labels), fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
```

Update `scan_locked`:

```python
def scan_locked(
    backend: ScannerBackend, dpi: int = 300, mode: str = "Color", device: str | None = None
) -> bytes:
    if not _scan_lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.scan(dpi=dpi, mode=mode, device=device)
    finally:
        _scan_lock.release()
```

In `scan.py` `scan_page`, pass the session device:

```python
    png = scan_locked(backend, dpi=body.dpi, mode=body.mode, device=scan_session.device)
```

- [ ] **Step 4: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/test_scanner.py tests/test_scan_api.py -v`
Expected: all PASS (existing scan-page tests still pass; Fake ignores device).

- [ ] **Step 5: Full suite, then commit**

Run: `cd backend && uv run pytest`.

```bash
git add backend/app/services/scanner.py backend/app/api/scan.py backend/tests/test_scanner.py
git commit -m "feat: thread scanner device selection into scans"
```

---

### Task 9: Fast scan preview endpoint

**Files:**
- Modify: `backend/app/services/scanner.py` (`preview` on both backends)
- Modify: `backend/app/api/scan.py` (`POST /api/scan/preview`)
- Test: extend `backend/tests/test_scanner.py`, `backend/tests/test_scan_api.py`

**Interfaces:**
- Produces: `ScanimageBackend.preview(device=None) -> bytes` (fast params `--resolution=75 --mode=Gray`, 30s timeout); `FakeScannerBackend.preview(device=None) -> bytes`; `POST /api/scan/preview` (body `{device?}`) → `Response(content=<png>, media_type="image/png")`, under `_scan_lock` (409 `scanner_busy` if held).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_scanner.py`:

```python
def test_scanimage_preview_uses_fast_params(monkeypatch):
    import subprocess

    from app.services.scanner import ScanimageBackend

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["timeout"] = kwargs.get("timeout")

        class R:
            returncode = 0
            stdout = b"\x89PNG fake"
            stderr = b""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ScanimageBackend().preview()
    assert "--resolution=75" in captured["argv"]
    assert "--mode=Gray" in captured["argv"]
    assert captured["timeout"] == 30
```

Append to `backend/tests/test_scan_api.py`:

```python
def test_preview_endpoint_returns_png(auth_client, fake_scanner, storage):
    resp = auth_client.post("/api/scan/preview", json={})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_preview_busy_returns_409(auth_client, fake_scanner, storage):
    from app.services import scanner as scanner_module

    scanner_module._scan_lock.acquire()
    try:
        resp = auth_client.post("/api/scan/preview", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "scanner_busy"
    finally:
        scanner_module._scan_lock.release()
```

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_scanner.py -k preview tests/test_scan_api.py -k preview -v`
Expected: FAIL.

- [ ] **Step 3: Implement `preview` in `scanner.py`**

Add module constants near the top (after `PROBE_TIMEOUT_SECONDS`):

```python
PREVIEW_TIMEOUT_SECONDS = 30
PREVIEW_RESOLUTION = 75
PREVIEW_MODE = "Gray"
```

Add to the Protocol: `def preview(self, device: str | None = None) -> bytes: ...`

Add to `ScanimageBackend`:

```python
    def preview(self, device: str | None = None) -> bytes:
        argv = [
            "scanimage",
            "--format=png",
            f"--resolution={PREVIEW_RESOLUTION}",
            f"--mode={PREVIEW_MODE}",
        ]
        if device:
            argv += ["-d", device]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=PREVIEW_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ScannerTimeout()
        if result.returncode != 0:
            raise self._map_error(result.stderr.decode(errors="replace"), result.returncode)
        return result.stdout
```

Add to `FakeScannerBackend`:

```python
    def preview(self, device: str | None = None) -> bytes:
        return self.scan(dpi=PREVIEW_RESOLUTION, mode=PREVIEW_MODE, device=device)
```

Add a lock-guarded helper next to `scan_locked`:

```python
def preview_locked(backend: ScannerBackend, device: str | None = None) -> bytes:
    if not _scan_lock.acquire(blocking=False):
        raise ScannerBusy("Another scan is in progress")
    try:
        return backend.preview(device=device)
    finally:
        _scan_lock.release()
```

- [ ] **Step 4: Add the endpoint in `scan.py`**

Add the import and a request model + route:

```python
from fastapi import Response
from app.services.scanner import preview_locked


class PreviewRequest(BaseModel):
    device: str | None = None


@router.post("/preview")
def scan_preview(
    body: PreviewRequest, backend: ScannerBackend = Depends(get_scanner)
) -> Response:
    png = preview_locked(backend, device=body.device)
    return Response(content=png, media_type="image/png")
```

(Add `Response` to the existing `fastapi` import line; add `preview_locked` to the scanner import.)

- [ ] **Step 5: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/test_scanner.py tests/test_scan_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `cd backend && uv run pytest`.

```bash
git add backend/app/services/scanner.py backend/app/api/scan.py backend/tests/test_scanner.py backend/tests/test_scan_api.py
git commit -m "feat: fast scan preview endpoint"
```

---

### Task 10: Frontend scanner UI — device dropdown + preview + no-OCR

**Files:**
- Modify: `frontend/src/lib/types.ts` (`ScanDevice`)
- Modify: `frontend/src/pages/ScanPage.tsx`
- Test: `frontend/src/lib/scanDevices.test.ts` (+ small pure helper `frontend/src/lib/scanDevices.ts`)

**Interfaces:**
- Consumes: `GET /api/scan/devices`, `POST /api/scan/preview`, session `ocr_enabled`/`device` (Tasks 5,7,9).
- Produces: `lib/scanDevices.ts` `deviceLabel(devices, id)` + `scanDeviceHint(devices): "none" | "single" | "multiple"` (pure, tested); `ScanPage` fetches devices, renders none/single/dropdown, a "Preview" button (throwaway image in component state), and a "Run OCR" checkbox — all in the setup phase, none touching `scanWizardReducer`.

- [ ] **Step 1: Write the failing pure-helper test**

`frontend/src/lib/scanDevices.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { scanDeviceHint } from "./scanDevices";

describe("scanDeviceHint", () => {
  it("classifies device count", () => {
    expect(scanDeviceHint([])).toBe("none");
    expect(scanDeviceHint([{ id: "a", name: "A" }])).toBe("single");
    expect(scanDeviceHint([{ id: "a", name: "A" }, { id: "b", name: "B" }])).toBe("multiple");
  });
});
```

- [ ] **Step 2: Run it, expect failure**

Run: `cd frontend && npm test -- scanDevices.test`
Expected: FAIL.

- [ ] **Step 3: Implement the helper + type**

`frontend/src/lib/scanDevices.ts`:

```ts
import type { ScanDevice } from "./types";

export function scanDeviceHint(devices: ScanDevice[]): "none" | "single" | "multiple" {
  if (devices.length === 0) return "none";
  return devices.length === 1 ? "single" : "multiple";
}
```

Add to `frontend/src/lib/types.ts`:

```ts
export interface ScanDevice {
  id: string;
  name: string;
}
```

Run: `cd frontend && npm test -- scanDevices.test` → PASS.

- [ ] **Step 4: Wire the ScanPage UI**

Read `frontend/src/pages/ScanPage.tsx` first. Make these additive changes (the wizard reducer is untouched — all new state is component-local `useState`/`useQuery`):

1. Imports: add `useQuery` usage for devices, `scanDeviceHint` + `ScanDevice`, `getToken` (already imported for preview blobs if present — else add from `@/lib/api`).
2. Devices query + local selection state (near the existing status query):

```tsx
  const { data: deviceData } = useQuery({
    queryKey: ["scan-devices"],
    queryFn: () => api.get<{ devices: ScanDevice[]; default: string | null }>("/api/scan/devices"),
  });
  const devices = deviceData?.devices ?? [];
  const [device, setDevice] = useState<string | null>(null);
  const chosenDevice = device ?? deviceData?.default ?? null;

  const [ocrEnabled, setOcrEnabled] = useState(true);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
```

3. Session creation body — include the new fields. Find where `POST /api/scan/sessions` is called and change the body to:

```tsx
      { ocr_languages: languages, ocr_enabled: ocrEnabled, device: chosenDevice }
```

(reuse the existing `languages` state variable already in ScanPage.)

4. In the **setup phase** JSX (before "Start scan session"), add device display + OCR toggle + preview:

```tsx
        {scanDeviceHint(devices) === "none" && (
          <p className="text-sm text-red-600">No scanner detected — check power and USB.</p>
        )}
        {scanDeviceHint(devices) === "single" && (
          <p className="text-sm text-zinc-600">Scanner: {devices[0].name}</p>
        )}
        {scanDeviceHint(devices) === "multiple" && (
          <div>
            <Label htmlFor="scan-device">Scanner</Label>
            <Select
              id="scan-device"
              value={chosenDevice ?? ""}
              onChange={(e) => setDevice(e.target.value || null)}
            >
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={ocrEnabled} onChange={(e) => setOcrEnabled(e.target.checked)} />
          Run OCR (extract text)
        </label>
        <div>
          <Button
            variant="outline"
            disabled={previewing}
            onClick={async () => {
              setPreviewing(true);
              try {
                const resp = await fetch("/api/scan/preview", {
                  method: "POST",
                  headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
                  body: JSON.stringify({ device: chosenDevice }),
                });
                if (resp.ok) {
                  if (previewUrl) URL.revokeObjectURL(previewUrl);
                  setPreviewUrl(URL.createObjectURL(await resp.blob()));
                }
              } finally {
                setPreviewing(false);
              }
            }}
          >
            {previewing ? "Previewing…" : "Preview"}
          </Button>
          {previewUrl && (
            <div className="mt-2">
              <img src={previewUrl} alt="scan preview" className="max-h-64 rounded border" />
              <button
                className="mt-1 block text-xs text-zinc-500"
                onClick={() => {
                  URL.revokeObjectURL(previewUrl);
                  setPreviewUrl(null);
                }}
              >
                clear preview
              </button>
            </div>
          )}
        </div>
```

Ensure `Label` and `Select` are imported in ScanPage (they are used by the compile dialog already — confirm; add if missing). Ensure `api` and `getToken` are imported from `@/lib/api`.

- [ ] **Step 5: Full frontend suite + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b` (all pass, clean).

```bash
git add frontend/src/lib/types.ts frontend/src/lib/scanDevices.ts frontend/src/lib/scanDevices.test.ts frontend/src/pages/ScanPage.tsx
git commit -m "feat: scanner device dropdown, preview, and no-OCR toggle in scan UI"
```

---

### Task 11: Image summary from OCR text (#5)

**Files:**
- Modify: `backend/app/worker/pipeline.py` (`_ensure_summary` image branch)
- Test: extend `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Chunk` content chunks (produced by OCR when `ocr_enabled`).
- Produces: image summary uses the **text model** when combined `source=content` text is **≥ 40 chars**; otherwise the vision model (`describe(image_path=...)`).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_pipeline.py`:

```python
IMAGE_TEXT_THRESHOLD = 40


def test_image_summary_uses_text_when_ocr_text_present(session, pipeline_storage, llm_stub, tmp_path):
    from app.models import ChunkSource, DocType
    from tests.helpers import make_text_image

    # rendered image whose OCR yields well over 40 chars
    img = make_text_image(
        tmp_path / "rich.png",
        "FATTURA NUMERO 12345 DEL 2026 IMPORTO 42 EURO CLIENTE ACME SRL",
    )
    doc = make_doc(session, doc_type=DocType.image, title="Fattura")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.summary is not None
    # text path used: describe called with text=, image_path None
    assert llm_stub["describe"][0]["text"] is not None
    assert llm_stub["describe"][0]["image_path"] is None


def test_image_summary_falls_back_to_vision_when_little_text(session, pipeline_storage, llm_stub, tmp_path):
    from app.models import Chunk, ChunkSource, DocType

    # image doc with a tiny content chunk (< 40 chars), simulating a near-textless photo
    doc = make_doc(session, doc_type=DocType.image, title="Foto")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", b"\x89PNG fake")
    doc.file_path = rel
    session.add(
        Chunk(document_id=doc.id, chunk_index=0, page_number=1, source=ChunkSource.content, content="ciao")
    )
    session.commit()

    from app.worker import pipeline

    pipeline._ensure_summary(session, doc, pipeline_storage)
    assert llm_stub["describe"][-1]["image_path"] is not None  # vision fallback
```

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_pipeline.py -k image_summary -v`
Expected: FAIL (current image branch always uses vision).

- [ ] **Step 3: Implement**

In `backend/app/worker/pipeline.py`, add the constant near `SUMMARY_INPUT_CHARS`:

```python
IMAGE_SUMMARY_TEXT_THRESHOLD = 40
```

Replace the image branch of `_ensure_summary` (the `if doc.doc_type == DocType.image:` block) so it prefers OCR text:

```python
    if doc.doc_type == DocType.image:
        content_chunks = session.exec(
            select(Chunk)
            .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
            .order_by(Chunk.chunk_index)
        ).all()
        ocr_text = "\n\n".join(c.content for c in content_chunks).strip()
        if len(ocr_text) >= IMAGE_SUMMARY_TEXT_THRESHOLD:
            summary = llm_describe(text=ocr_text[:SUMMARY_INPUT_CHARS])
        else:
            # No usable extracted text (a photo, or no-OCR) — send the file to vision.
            summary = llm_describe(image_path=storage.abs_path(doc.file_path))
    else:
```

(Keep the existing `else:` branch that handles pdf/text/scan summarization from content chunks; only the image branch changes.)

- [ ] **Step 4: Run tests, expect pass**

Run: `cd backend && uv run pytest tests/test_pipeline.py -v`
Expected: all PASS (including the earlier no-OCR image test, which has no content chunks → <40 → vision, still correct).

- [ ] **Step 5: Commit**

```bash
git add backend/app/worker/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: summarize images from OCR text when available"
```

---

### Task 12: Provider-agnostic LLM config (#6)

**Files:**
- Modify: `backend/app/config.py` (api key/base fields)
- Modify: `backend/app/services/llm.py` (pass api_key/api_base)
- Modify: `.env.example` (documented provider block)
- Test: extend `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings`.
- Produces: `embed`/`describe`/`complete` pass `api_key`/`api_base` to litellm (embedding falls back to LLM key/base when its own is unset; `None` when all unset).

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_llm.py`:

```python
def test_complete_passes_api_key_and_base(monkeypatch):
    import litellm

    from app.config import get_settings
    from app.services import llm

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:11434")

    captured = {}

    def fake_completion(model, messages, stream=False, api_key=None, api_base=None):
        captured["api_key"] = api_key
        captured["api_base"] = api_base
        msg = type("M", (), {"content": "ok"})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    llm.complete([{"role": "user", "content": "hi"}])
    assert captured["api_key"] == "sk-test"
    assert captured["api_base"] == "http://localhost:11434"
    get_settings.cache_clear()


def test_embed_falls_back_to_llm_key(monkeypatch):
    import litellm

    from app.config import get_settings
    from app.services import llm

    get_settings.cache_clear()
    monkeypatch.setenv("LLM_API_KEY", "sk-shared")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    captured = {}

    def fake_embedding(model, input, dimensions, api_key=None, api_base=None):
        captured["api_key"] = api_key
        return type("R", (), {"data": [{"index": 0, "embedding": [0.1] * dimensions}]})()

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    llm.embed(["hello"])
    assert captured["api_key"] == "sk-shared"
    get_settings.cache_clear()
```

- [ ] **Step 2: Run them, expect failure**

Run: `cd backend && uv run pytest tests/test_llm.py -k "api_key or fall" -v`
Expected: FAIL (litellm called without api_key/api_base kwargs).

- [ ] **Step 3: Add config fields**

In `backend/app/config.py`, add to `Settings` (after `gemini_api_key`):

```python
    llm_api_key: str = ""
    llm_api_base: str = ""
    embedding_api_key: str = ""
    embedding_api_base: str = ""
```

- [ ] **Step 4: Pass them in `llm.py`**

Add a helper and thread it through all three calls:

```python
def _kw(key: str, base: str) -> dict:
    kw: dict = {}
    if key:
        kw["api_key"] = key
    if base:
        kw["api_base"] = base
    return kw
```

`embed`:

```python
def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    kw = _kw(
        settings.embedding_api_key or settings.llm_api_key,
        settings.embedding_api_base or settings.llm_api_base,
    )
    resp = litellm.embedding(
        model=settings.embedding_model, input=texts, dimensions=settings.embedding_dim, **kw
    )
    data = sorted(resp.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]
```

`describe` — build `kw = _kw(settings.llm_api_key, settings.llm_api_base)` and pass `**kw` to `litellm.completion(...)`. `complete` — same: `kw = _kw(settings.llm_api_key, settings.llm_api_base)` and `litellm.completion(model=..., messages=..., stream=stream, **kw)`.

(Passing nothing when unset preserves LiteLLM's provider-env fallback — the drop-in requirement. The tests pass explicit kwargs, so the fake signatures include `api_key`/`api_base`.)

- [ ] **Step 5: Update `.env.example`**

Replace the LLM block in `.env.example` with a documented, provider-agnostic version:

```env
# --- LLM provider (LiteLLM model strings select the provider) ---
# Set the model strings + one API key. Examples:
#   Gemini:  LLM_MODEL=gemini/gemini-2.5-flash        LLM_API_KEY=<google key>
#   Groq:    LLM_MODEL=groq/llama-3.3-70b-versatile   LLM_API_KEY=<groq key>
#   OpenAI:  LLM_MODEL=openai/gpt-4o-mini             LLM_API_KEY=<openai key>
#   Ollama:  LLM_MODEL=ollama/llama3.1  LLM_API_BASE=http://localhost:11434  (no key)
LLM_MODEL=gemini/gemini-2.5-flash
VISION_MODEL=gemini/gemini-2.5-flash
LLM_API_KEY=
LLM_API_BASE=
# Embeddings often need a different provider (e.g. Groq has none) — falls back to LLM_API_* if unset.
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_DIM=1536
EMBEDDING_API_KEY=
EMBEDDING_API_BASE=
# Legacy: still honored by LiteLLM if set instead of LLM_API_KEY.
GEMINI_API_KEY=
```

- [ ] **Step 6: Run tests, then full suite, then commit**

Run: `cd backend && uv run pytest tests/test_llm.py -v` then `cd backend && uv run pytest`
Expected: all PASS, 0 warnings.

```bash
git add backend/app/config.py backend/app/services/llm.py .env.example backend/tests/test_llm.py
git commit -m "feat: provider-agnostic LLM api key/base config"
```

---

## Exit criteria

- Full backend suite green (0 warnings) against real Postgres; only `llm.py` mocked.
- Full frontend suite green (`npm test`), `npx tsc -b` clean, `npm run build` succeeds.
- Manual smoke (human): from an external machine via the tunnel — upload works; toggle "No OCR" and confirm a photo skips OCR; scanner dropdown shows detected devices; Preview returns a fast image; a text-native PDF summary spends no vision tokens; switching `LLM_MODEL`/`LLM_API_KEY` to a non-Gemini provider keeps search/summary/chat working.
