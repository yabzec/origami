# Inline PDF, Scan Redesign, German OCR, Re-process, AI Translation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render PDFs inline with a separate download, rebuild the scan page as a single-screen session, add German OCR, allow re-processing a document with another OCR language, and store a searchable AI translation (plus Italian summary) for non-Italian documents.

**Architecture:** Backend (FastAPI + SQLModel + Postgres/pgvector, worker job `process_document`) gains three document columns, a `translation` chunk source, a structured `describe()` result carrying the detected language, a `translate()` LLM call, and a `reprocess` endpoint that re-enqueues the pipeline with `force_ocr`. Frontend (React 19 + react-router 8 + TanStack Query + Tailwind) moves to a data router so the new scan page can block navigation, splits the scan UI into four presentational components, and extends the document page (download, date, translation toggle, re-process).

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, LiteLLM, Tesseract (pytesseract), pytest against real Postgres; React 19, TypeScript, Vite, react-router 8, TanStack Query 5, vitest + Testing Library, oxlint.

**Spec:** `docs/superpowers/specs/2026-10-02-scan-redesign-translation-design.md`

## Global Constraints

- Backend tests run against real Postgres (`origami_test`, recreated per session by `tests/conftest.py`). Only `app/services/llm.py` may be mocked (project rule). `app/services/llm.py` is the only module that imports `litellm`.
- Run backend tests from `backend/`: `uv run pytest`. Run frontend tests from `frontend/`: `npx vitest run`; lint `npm run lint`; type-check + build `npm run build`.
- Baseline before this plan: backend 143 passed, frontend 38 passed. Every task ends with the full suite of the side it touched green.
- Target language: setting `primary_language` (env `PRIMARY_LANGUAGE`, ISO 639-1, default `it`), read **only** through `app.config.get_primary_language()`.
- OCR language options exactly: `ita+eng` Italian + English (default), `ita` Italian, `eng` English, `deu` German, `ita+deu` Italian + German.
- Re-process: 409 `document_busy` when status is `pending` or `processing`.
- Translation failure is non-fatal: `translation_status = "failed"`, document still ends `ready`, no partial translation chunks.
- Leave-guard confirm text: `You have unsaved scanned pages. Leave and discard them?`
- Re-process confirm text: `Re-run OCR and AI processing? Extracted text, summary and translation will be replaced.`
- Commit messages: Conventional Commits, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Do not stage `deploy/origami.service` or `deploy/origami.sh` (user's uncommitted work).

## Deviation from the spec (flagged for review)

- The current pipeline skips the summary for `scan` documents (`_ensure_summary` returns early for `DocType.scan`). Language detection rides on the summary call, so scans — the main German use case — would never be translated. **Task 6 enables the summary for scans that have OCR text.** No-OCR scans still get no summary (they have no content).
- Re-processing a **born-digital PDF** with OCR enabled rasterizes it (the stored PDF is replaced by an image + OCR text layer), because the system cannot tell an earlier-OCR'd PDF from a native one. The re-process confirm on PDFs says so (Task 14).

## Review Focus

1. **AI returns non-JSON or fenced JSON for the summary** — summary must still be stored (raw text), no crash, no translation attempted. Test in Task 5 (`parse_description`) and Task 6 (pipeline with raw summary).
2. **German selected but `tesseract-ocr-deu` missing on the host** — document ends `failed` with Tesseract's message, not a stuck `processing`. Existing failure path covers it; Task 2 test is skipped when `deu` is absent and the plan's Task 2 Step 1 installs the package.
3. **Re-process clicked twice quickly / while processing** — second call returns 409, no duplicate jobs. Test in Task 8.
4. **React StrictMode double-mount on the scan page** — exactly one session created on mount. Guarded by a ref in Task 13; verified manually in Task 15 (dev mode, check `scan_sessions` count).
5. **Clearing the date field** (empty input) — `PATCH` with `document_date: null` must not violate `NOT NULL`; value is ignored. Test in Task 4.

---

## File Structure

Backend
- `backend/app/api/files.py` — `download` query param (Task 1).
- `backend/app/config.py` — `primary_language`, `get_primary_language()` (Task 3).
- `backend/app/models/document.py`, `backend/app/models/chunk.py`, `backend/app/models/__init__.py` — new columns, `TranslationStatus`, `ChunkSource.translation` (Task 3).
- `backend/alembic/versions/b7c4e2a91d05_document_date_language_translation.py` — migration (Task 3).
- `backend/app/api/documents.py` — `document_date` patch (Task 4), text `variant` (Task 7), `reprocess` (Task 8).
- `backend/app/api/uploads.py` — `document_date`, `description` in `create_pending_document` and upload form (Task 4, Task 9).
- `backend/app/services/llm.py` — `Description`, `parse_description`, JSON describe prompt, `translate` (Task 5).
- `backend/app/worker/pipeline.py` — language + translation step, scan summaries (Task 6), `force_ocr` (Task 8).
- `backend/app/api/scan.py`, `backend/app/services/scanner.py` — per-page device, richer compile body (Task 9).
- Tests: `test_files_api.py`, `test_ocr.py`, `test_schema.py`, new `test_migrations.py`, `test_documents.py`, `test_uploads.py`, `test_llm.py`, `test_pipeline.py`, new `test_reprocess.py`, `test_scan_api.py`, `conftest.py`.

Frontend
- `src/lib/api.ts` — `fileUrl` download option (Task 1).
- `src/lib/ocrLanguages.ts`, `src/components/OcrLanguageSelect.tsx` — shared language list (Task 2).
- `src/lib/types.ts`, `src/lib/dates.ts`, `src/lib/upload.ts`, `src/components/UploadDialog.tsx` — new fields, date helpers (Task 10).
- `src/App.tsx`, `src/main.tsx` — data router (Task 11).
- `src/lib/scanWizard.ts` — new phases, selection, `shouldBlockLeave` (Task 12).
- `src/hooks/useLeaveGuard.ts`, `src/components/scan/{ScanToolbar,ScanPreview,PageCarousel,ScanSidebar}.tsx`, `src/pages/ScanPage.tsx` — scan page (Task 13).
- `src/lib/translation.ts`, `src/pages/DocumentPage.tsx`, `src/components/DocumentCard.tsx` — document page (Task 14).
- `README.md` — `tesseract-ocr-deu`, `PRIMARY_LANGUAGE` (Task 2, Task 3).

---

### Task 1: Inline PDF preview + download parameter

**Files:**
- Modify: `backend/app/api/files.py`
- Modify: `frontend/src/lib/api.ts:54-55`
- Modify: `frontend/src/pages/DocumentPage.tsx` (header row)
- Test: `backend/tests/test_files_api.py`, `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `GET /api/documents/{id}/file?download=1` → `Content-Disposition: attachment`; without it → `inline`. Frontend `fileUrl(documentId: string, opts?: { download?: boolean }): string`.

- [ ] **Step 1: Write the failing backend tests** — append to `backend/tests/test_files_api.py`:

```python
def test_file_is_inline_by_default(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline")


def test_file_is_attachment_when_download_requested(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?download=1")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_files_api.py -v` → `test_file_is_inline_by_default` fails (`attachment; filename=...`).

- [ ] **Step 3: Implement** — replace the endpoint in `backend/app/api/files.py`:

```python
@router.get("/{document_id}/file")
def document_file(
    document_id: uuid.UUID,
    download: bool = False,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    doc = get_doc_or_404(session, document_id)
    if not doc.file_path:
        raise api_error(404, "no_file", "Document has no stored file")
    path = storage.abs_path(doc.file_path)
    if not path.is_file():
        raise api_error(404, "no_file", "Stored file is missing on disk")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    # inline lets the <iframe> render PDFs; attachment only for the explicit Download button
    return FileResponse(
        path,
        media_type=media_type,
        filename=doc.original_filename or path.name,
        content_disposition_type="attachment" if download else "inline",
    )
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_files_api.py -v`.

- [ ] **Step 5: Write the failing frontend test** — in `frontend/src/lib/api.test.ts`, add inside `describe("api client")`:

```ts
  it("fileUrl adds download=1 when requested", () => {
    setToken("tok");
    expect(fileUrl("doc-1", { download: true })).toBe("/api/documents/doc-1/file?token=tok&download=1");
  });
```

- [ ] **Step 6: Run, expect FAIL** — `cd frontend && npx vitest run src/lib/api.test.ts`.

- [ ] **Step 7: Implement** — replace `fileUrl` in `frontend/src/lib/api.ts`:

```ts
export const fileUrl = (documentId: string, opts: { download?: boolean } = {}): string =>
  `/api/documents/${documentId}/file?token=${getToken() ?? ""}${opts.download ? "&download=1" : ""}`;
```

In `frontend/src/pages/DocumentPage.tsx`, change the import to `import { api, fileUrl } from "@/lib/api";` (already present) and replace the header row:

```tsx
        <div className="mb-3 flex items-center gap-3">
          <h2 className="flex-1 truncate text-lg font-semibold">{doc.title}</h2>
          {doc.file_path && (
            <a
              href={fileUrl(doc.id, { download: true })}
              className="inline-flex h-8 items-center rounded-md border border-zinc-300 px-3 text-sm hover:bg-zinc-100"
            >
              Download
            </a>
          )}
          <Badge variant={STATUS_VARIANTS[doc.status]}>{doc.status}</Badge>
        </div>
```

- [ ] **Step 8: Run, expect PASS** — `npx vitest run && npm run lint && npm run build`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/files.py backend/tests/test_files_api.py frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/pages/DocumentPage.tsx
git commit -m "fix: render PDFs inline and add explicit download

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: German OCR language

**Files:**
- Create: `frontend/src/lib/ocrLanguages.ts`, `frontend/src/lib/ocrLanguages.test.ts`, `frontend/src/components/OcrLanguageSelect.tsx`
- Modify: `frontend/src/components/UploadDialog.tsx:30,100-110`, `frontend/src/pages/ScanPage.tsx:185-195`
- Modify: `README.md:10,22`
- Test: `backend/tests/test_ocr.py`

**Interfaces:**
- Produces: `OCR_LANGUAGES: readonly { value: string; label: string }[]`, `DEFAULT_OCR_LANGUAGES = "ita+eng"`, component `OcrLanguageSelect(props: SelectHTMLAttributes<HTMLSelectElement>)`.

- [ ] **Step 1: Install the German traineddata (human action — needs sudo)**

Ask the user to run: `! sudo apt install -y tesseract-ocr-deu`
Verify: `tesseract --list-langs` lists `deu`.

- [ ] **Step 2: Write the backend test** — append to `backend/tests/test_ocr.py`:

```python
import pytesseract
import pytest

from app.services.ocr import ocr_image
from tests.helpers import make_text_image


@pytest.mark.skipif("deu" not in pytesseract.get_languages(config=""), reason="tesseract-ocr-deu not installed")
def test_ocr_image_german(tmp_path):
    img = make_text_image(tmp_path / "de.png", "RECHNUNG STRASSE", size=(1600, 400))
    _, text = ocr_image(img, "deu")
    assert "RECHNUNG" in text.upper()
```

(If `test_ocr.py` already imports `pytest`, `ocr_image`, or `make_text_image`, do not duplicate the imports.)

- [ ] **Step 3: Run** — `cd backend && uv run pytest tests/test_ocr.py -v` → PASS (or SKIP if Step 1 not done yet; do not proceed to commit until it passes).

- [ ] **Step 4: Write the failing frontend test** — `frontend/src/lib/ocrLanguages.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_OCR_LANGUAGES, OCR_LANGUAGES } from "./ocrLanguages";

describe("OCR_LANGUAGES", () => {
  it("offers the five supported combinations, default first", () => {
    expect(OCR_LANGUAGES.map((l) => l.value)).toEqual(["ita+eng", "ita", "eng", "deu", "ita+deu"]);
    expect(OCR_LANGUAGES[0].value).toBe(DEFAULT_OCR_LANGUAGES);
  });
});
```

- [ ] **Step 5: Run, expect FAIL** — `cd frontend && npx vitest run src/lib/ocrLanguages.test.ts` (module not found).

- [ ] **Step 6: Implement** — `frontend/src/lib/ocrLanguages.ts`:

```ts
export const DEFAULT_OCR_LANGUAGES = "ita+eng";

export const OCR_LANGUAGES = [
  { value: "ita+eng", label: "Italian + English" },
  { value: "ita", label: "Italian" },
  { value: "eng", label: "English" },
  { value: "deu", label: "German" },
  { value: "ita+deu", label: "Italian + German" },
] as const;
```

`frontend/src/components/OcrLanguageSelect.tsx`:

```tsx
import type { SelectHTMLAttributes } from "react";
import { Select } from "@/components/ui/select";
import { OCR_LANGUAGES } from "@/lib/ocrLanguages";

export function OcrLanguageSelect(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Select {...props}>
      {OCR_LANGUAGES.map((l) => (
        <option key={l.value} value={l.value}>
          {l.label}
        </option>
      ))}
    </Select>
  );
}
```

`UploadDialog.tsx`: `useState("ita+eng")` → `useState(DEFAULT_OCR_LANGUAGES)`; replace the `<Select id="up-lang" ...>…</Select>` block with:

```tsx
            <OcrLanguageSelect id="up-lang" value={languages} onChange={(e) => setLanguages(e.target.value)} />
```

Add imports `import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";` and `import { DEFAULT_OCR_LANGUAGES } from "@/lib/ocrLanguages";`. Remove the `Select` import if no longer used (it is still used for the folder select — keep it).

`ScanPage.tsx` (old page, replaced in Task 13 — this keeps German usable meanwhile): replace the `<Select id="scan-lang" …>…</Select>` block with:

```tsx
              <OcrLanguageSelect
                id="scan-lang"
                value={state.languages}
                onChange={(e) => dispatch({ type: "SET_LANGUAGES", languages: e.target.value })}
              />
```

and add the `OcrLanguageSelect` import.

`README.md`: line 10 → `sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu`; line 22 → `tesseract --list-langs   # must include ita, eng and deu`.

- [ ] **Step 7: Run, expect PASS** — `npx vitest run && npm run lint && npm run build`; `cd ../backend && uv run pytest -q`.

- [ ] **Step 8: Commit**

```bash
git add README.md backend/tests/test_ocr.py frontend/src/lib/ocrLanguages.ts frontend/src/lib/ocrLanguages.test.ts frontend/src/components/OcrLanguageSelect.tsx frontend/src/components/UploadDialog.tsx frontend/src/pages/ScanPage.tsx
git commit -m "feat: add German OCR language option

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Data model, migration, primary-language setting

**Files:**
- Create: `backend/alembic/versions/b7c4e2a91d05_document_date_language_translation.py`, `backend/tests/test_migrations.py`
- Modify: `backend/app/models/document.py`, `backend/app/models/chunk.py:12-15`, `backend/app/models/__init__.py`, `backend/app/config.py`, `.env.example`, `README.md`
- Test: `backend/tests/test_schema.py`, `backend/tests/test_documents.py`

**Interfaces:**
- Produces: `Document.document_date: date`, `Document.detected_language: str | None`, `Document.translation_status: str | None`; `TranslationStatus(StrEnum)` with `done`, `failed` (exported from `app.models`); `ChunkSource.translation = "translation"`; `Settings.primary_language: str = "it"`; `app.config.get_primary_language() -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_schema.py`:

```python
def test_document_language_columns(engine):
    from sqlalchemy import inspect

    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    assert {"document_date", "detected_language", "translation_status"} <= set(cols)
    assert cols["document_date"]["nullable"] is False
```

Create `backend/tests/test_migrations.py`:

```python
from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import text

TEST_URL = "postgresql+psycopg://origami:origami@localhost:5432/origami_test"
PREVIOUS = "616180658622"


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    return cfg


def test_document_date_backfilled_from_created_at(engine):
    cfg = _cfg()
    command.downgrade(cfg, PREVIOUS)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO documents (id, title, description, doc_type, ocr_languages, "
                "ocr_enabled, status, created_at, updated_at) VALUES (gen_random_uuid(), "
                "'MigrationOld', '', 'pdf', 'ita', true, 'ready', "
                "'2020-05-17 10:00:00', '2020-05-17 10:00:00')"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            value = conn.execute(
                text("SELECT document_date FROM documents WHERE title = 'MigrationOld'")
            ).scalar_one()
        assert value == date(2020, 5, 17)
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title = 'MigrationOld'"))
```

Append to `backend/tests/test_documents.py`:

```python
def test_new_document_defaults_and_serialization(auth_client, session):
    from datetime import date

    doc = seed_document(session, "Fresh", [])
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["document_date"] == date.today().isoformat()
    assert body["detected_language"] is None
    assert body["translation_status"] is None
```

(Use the `seed_document` import already at the top of `test_documents.py`; add `from tests.helpers import seed_document` if missing.)

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_schema.py tests/test_migrations.py tests/test_documents.py -v`.

- [ ] **Step 3: Implement the model** — `backend/app/models/document.py`: add `from datetime import date, datetime` (replace the `datetime` import), add the enum and fields:

```python
class TranslationStatus(StrEnum):
    done = "done"
    failed = "failed"
```

Inside `Document`, after `ocr_enabled`:

```python
    document_date: date = Field(default_factory=lambda: utcnow().date())
    detected_language: str | None = None  # ISO 639-1, set by the summary step
    translation_status: str | None = None  # TranslationStatus; None = not needed / not yet processed
```

`backend/app/models/chunk.py` — `ChunkSource` gains `translation = "translation"` after `metadata`.

`backend/app/models/__init__.py`: import and export `TranslationStatus`:

```python
from app.models.document import Document, DocumentTag, DocStatus, DocType, TranslationStatus
```

and add `"TranslationStatus"` to `__all__`.

- [ ] **Step 4: Implement the migration** — `backend/alembic/versions/b7c4e2a91d05_document_date_language_translation.py`:

```python
"""document date, detected language, translation status

Revision ID: b7c4e2a91d05
Revises: 616180658622
Create Date: 2026-10-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c4e2a91d05"
down_revision: Union[str, Sequence[str], None] = "616180658622"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("document_date", sa.Date(), nullable=True))
    op.execute("UPDATE documents SET document_date = created_at::date")
    op.alter_column(
        "documents",
        "document_date",
        nullable=False,
        server_default=sa.text("CURRENT_DATE"),
    )
    op.add_column("documents", sa.Column("detected_language", sa.String(), nullable=True))
    op.add_column("documents", sa.Column("translation_status", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "translation_status")
    op.drop_column("documents", "detected_language")
    op.drop_column("documents", "document_date")
```

- [ ] **Step 5: Implement the setting** — `backend/app/config.py`: add `primary_language: str = "it"` to `Settings` (after `default_ocr_languages`) and, after `get_settings`:

```python
def get_primary_language() -> str:
    """Target language (ISO 639-1) for summaries and translations.

    Single lookup point: a future per-user profile setting replaces this body.
    """
    return get_settings().primary_language
```

`.env.example`: add a line `PRIMARY_LANGUAGE=it  # ISO 639-1 language for AI summaries and translations`. `README.md`: in the configuration/env section, add the same variable with one line of explanation (if the README has no env table, add it under the `.env` setup instructions).

- [ ] **Step 6: Run, expect PASS** — `uv run pytest -q` (whole backend suite; migration test must leave DB at head).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models backend/app/config.py backend/alembic/versions/b7c4e2a91d05_document_date_language_translation.py backend/tests/test_schema.py backend/tests/test_migrations.py backend/tests/test_documents.py .env.example README.md
git commit -m "feat: add document date, detected language and translation columns

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Editable document date (PATCH + upload)

**Files:**
- Modify: `backend/app/api/documents.py` (`DocumentPatch`, `update_document`)
- Modify: `backend/app/api/uploads.py` (`create_pending_document`, `upload_document`)
- Test: `backend/tests/test_documents.py`, `backend/tests/test_uploads.py`

**Interfaces:**
- Consumes: `Document.document_date` (Task 3).
- Produces: `create_pending_document(session, *, title, doc_type, ocr_languages, ocr_enabled=True, folder_id, tag_ids, original_filename, description: str = "", document_date: date | None = None) -> Document`. `PATCH` body field `document_date: "YYYY-MM-DD" | null` (null ignored). Upload form field `document_date`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_documents.py`:

```python
def test_patch_document_date(auth_client, session):
    doc = seed_document(session, "Dated", [])
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"


def test_patch_null_document_date_is_ignored(auth_client, session):
    doc = seed_document(session, "Dated", [])
    auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": None, "title": "T2"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"
    assert resp.json()["title"] == "T2"
```

Append to `backend/tests/test_uploads.py` (follow the file's existing upload call style; this is the shape):

```python
def test_upload_with_document_date(auth_client, session, storage):
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("old.pdf", b"%PDF-1.4 x", "application/pdf")},
        data={"document_date": "2018-12-01"},
    )
    assert resp.status_code == 201
    assert resp.json()["document_date"] == "2018-12-01"
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_documents.py tests/test_uploads.py -v`.

- [ ] **Step 3: Implement** — `backend/app/api/documents.py`: add `from datetime import date, datetime, timezone`; `DocumentPatch` gains `document_date: date | None = None`. In `update_document`, right after `tag_ids = fields.pop("tag_ids", None)`:

```python
    if fields.get("document_date", ...) is None:
        fields.pop("document_date", None)  # column is NOT NULL; an empty date input means "unchanged"
```

`backend/app/api/uploads.py`: add `from datetime import date`. Extend `create_pending_document` signature with `description: str = ""` and `document_date: date | None = None`, and build the document as:

```python
    doc = Document(
        title=title,
        description=description,
        doc_type=doc_type,
        ocr_languages=ocr_languages,
        ocr_enabled=ocr_enabled,
        folder_id=folder_id,
        original_filename=original_filename,
    )
    if document_date is not None:
        doc.document_date = document_date
```

`upload_document` gains `document_date: date | None = Form(default=None)` and passes `document_date=document_date`.

- [ ] **Step 4: Run, expect PASS** — `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/documents.py backend/app/api/uploads.py backend/tests/test_documents.py backend/tests/test_uploads.py
git commit -m "feat: editable document date on patch and upload

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: LLM — structured describe (summary + language) and translate

**Files:**
- Modify: `backend/app/services/llm.py`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `get_primary_language()` (Task 3).
- Produces: `class Description(NamedTuple): summary: str; language: str | None`; `parse_description(raw: str) -> Description`; `describe(text: str | None = None, image_path: Path | None = None) -> Description`; `translate(text: str, target_language: str) -> str`; `language_name(code: str) -> str`.

- [ ] **Step 1: Write the failing tests** — in `backend/tests/test_llm.py`, replace `test_describe_text` and `test_describe_image` with the versions below and add the new tests:

```python
def _completion_returning(content, captured):
    def fake_completion(model, messages):
        captured["model"] = model
        captured["content"] = messages[0]["content"]
        msg = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    return fake_completion


def test_describe_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        litellm,
        "completion",
        _completion_returning('{"summary": " Una fattura del 2026. ", "language": "DE"}', captured),
    )
    result = llm.describe(text="FATTURA n. 42 del 2026...")
    assert result == llm.Description("Una fattura del 2026.", "de")
    assert "FATTURA n. 42" in captured["content"]
    assert "Italian" in captured["content"]  # summary requested in the primary language
    assert captured["model"] == "gemini/gemini-2.5-flash"


def test_describe_image(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}
    monkeypatch.setattr(
        litellm, "completion", _completion_returning('{"summary": "Uno scontrino.", "language": "it"}', captured)
    )
    result = llm.describe(image_path=img)
    assert result == llm.Description("Uno scontrino.", "it")
    kinds = [p["type"] for p in captured["content"]]
    assert kinds == ["text", "image_url"]
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_description_handles_fenced_json():
    raw = '```json\n{"summary": "Contratto di affitto.", "language": "it"}\n```'
    assert llm.parse_description(raw) == llm.Description("Contratto di affitto.", "it")


def test_parse_description_falls_back_to_raw_text():
    assert llm.parse_description("  Just prose, no JSON.  ") == llm.Description("Just prose, no JSON.", None)
    assert llm.parse_description('{"summary": ""}') == llm.Description('{"summary": ""}', None)
    assert llm.parse_description('["not", "an", "object"]').language is None


def test_translate(monkeypatch):
    captured = {}
    monkeypatch.setattr(litellm, "completion", _completion_returning("  Fattura numero 5  ", captured))
    assert llm.translate("Rechnung Nummer 5", "it") == "Fattura numero 5"
    assert "Italian" in captured["content"]
    assert "Rechnung Nummer 5" in captured["content"]
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_llm.py -v`.

- [ ] **Step 3: Implement** — `backend/app/services/llm.py`: replace `DESCRIBE_PROMPT` and `describe`; add imports `import json` and `from typing import NamedTuple`; change `from app.config import get_settings` to `from app.config import get_primary_language, get_settings`.

```python
LANGUAGE_NAMES = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


class Description(NamedTuple):
    summary: str
    language: str | None  # ISO 639-1 of the document's own language; None if unknown


def _describe_prompt(target_language: str) -> str:
    return (
        "You are indexing a document for a searchable personal archive. "
        'Reply with ONLY a JSON object: {"summary": "...", "language": "xx"}. '
        f'"summary": 2-4 sentences written in {language_name(target_language)} describing '
        "what the document is, its purpose, and key entities (dates, amounts, names, "
        'organizations). "language": the ISO 639-1 code of the document\'s own language '
        '(for example "it", "en", "de").'
    )


def parse_description(raw: str) -> Description:
    """Parse the describe() JSON reply; fall back to the raw text with no language."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
        summary = str(data["summary"]).strip()
        language = data.get("language")
    except (ValueError, KeyError, TypeError, AttributeError):
        return Description(raw.strip(), None)
    if not summary:
        return Description(raw.strip(), None)
    if isinstance(language, str) and language.strip():
        return Description(summary, language.strip().lower()[:2])
    return Description(summary, None)


def describe(text: str | None = None, image_path: Path | None = None) -> Description:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = _describe_prompt(get_primary_language())
    if image_path is not None:
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content: str | list = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]
        model = settings.vision_model
    else:
        content = f"{prompt}\n\n---\n\n{(text or '')[:8000]}"
        model = settings.llm_model
    resp = litellm.completion(model=model, messages=[{"role": "user", "content": content}], **kw)
    return parse_description(resp.choices[0].message.content)


def translate(text: str, target_language: str) -> str:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = (
        f"Translate the following text into {language_name(target_language)}. Preserve line "
        "breaks, numbers, names, and dates. Output only the translation, with no comments."
    )
    resp = litellm.completion(
        model=settings.llm_model,
        messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{text}"}],
        **kw,
    )
    return resp.choices[0].message.content.strip()
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_llm.py -v` → PASS. Pipeline tests now fail because `describe()` returns a `Description` the pipeline does not use yet; Task 6 fixes that. **Do not commit here** — Tasks 5 and 6 share one commit (Task 6 Step 6) so `main` never has a red suite.

---

### Task 6: Pipeline — detected language, translation chunks, scan summaries

**Files:**
- Modify: `backend/app/worker/pipeline.py`
- Modify: `backend/tests/conftest.py` (`llm_stub`)
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Description`, `translate` (Task 5); `get_primary_language()`, `TranslationStatus`, `ChunkSource.translation` (Task 3).
- Produces: pipeline sets `doc.detected_language`, `doc.translation_status`; one `translation` chunk per `content` chunk (same `page_number`), embedded. `llm_stub` fixture: `calls["language"]` (default `"it"`) controls the detected language; `calls["translate"]` records `(text, target)`; `calls["translate_error"]` (default `None`) makes translate raise it.

- [ ] **Step 1: Update the stub** — in `backend/tests/conftest.py`, replace `llm_stub`:

```python
@pytest.fixture
def llm_stub(monkeypatch):
    """Stub the ONLY sanctioned mock boundary: app.services.llm."""
    from app.services.llm import Description

    calls = {"embed": [], "describe": [], "translate": [], "language": "it", "translate_error": None}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1] * 1536 for _ in texts]

    def fake_describe(text=None, image_path=None):
        calls["describe"].append({"text": text, "image_path": image_path})
        return Description("Descrizione generata.", calls["language"])

    def fake_translate(text, target_language):
        calls["translate"].append((text, target_language))
        if calls["translate_error"] is not None:
            raise calls["translate_error"]
        return f"[{target_language}] {text}"

    monkeypatch.setattr("app.worker.pipeline.llm_embed", fake_embed)
    monkeypatch.setattr("app.worker.pipeline.llm_describe", fake_describe)
    monkeypatch.setattr("app.worker.pipeline.llm_translate", fake_translate)
    return calls
```

- [ ] **Step 2: Write the failing tests** — append to `backend/tests/test_pipeline.py`:

```python
def _text_doc(session, pipeline_storage, body="Erster Absatz.\n\nZweiter Absatz."):
    doc = make_doc(session, doc_type=DocType.text, title="Brief")
    rel, _ = pipeline_storage.store_file(doc.id, ".md", body.encode())
    doc.file_path = rel
    session.commit()
    return doc


def test_italian_document_is_not_translated(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.detected_language == "it"
    assert doc.translation_status is None
    assert llm_stub["translate"] == []
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_german_document_gets_translation_chunks(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.status == DocStatus.ready
    assert doc.detected_language == "de"
    assert doc.translation_status == "done"
    by_source = chunks_by_source(session, doc)
    content = sorted(by_source[ChunkSource.content], key=lambda c: c.chunk_index)
    translated = sorted(by_source[ChunkSource.translation], key=lambda c: c.chunk_index)
    assert len(translated) == len(content)
    assert [t.page_number for t in translated] == [c.page_number for c in content]
    assert translated[0].content.startswith("[it] ")
    assert all(t.embedding is not None for t in translated)


def test_translation_failure_is_not_fatal(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    llm_stub["translate_error"] = RuntimeError("provider down")
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.status == DocStatus.ready
    assert doc.translation_status == "failed"
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_unknown_language_skips_translation(session, pipeline_storage, llm_stub):
    llm_stub["language"] = None  # e.g. describe() got non-JSON and fell back to raw text
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.status == DocStatus.ready
    assert doc.summary == "Descrizione generata."
    assert doc.detected_language is None
    assert llm_stub["translate"] == []


def test_scan_with_ocr_text_gets_summary_and_language(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    from app.worker.runner import run_once

    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    fake_scanner._labels = iter(["RECHNUNG NUMMER 123 FUER HERRN MUELLER"] * 2)
    llm_stub["language"] = "de"
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "Rechnung"}).json()["id"]

    assert run_once(engine) is True
    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    if ChunkSource.content in chunks_by_source(session, doc):
        assert doc.summary == "Descrizione generata."
        assert doc.detected_language == "de"
        assert doc.translation_status == "done"
```

(The `if` guard keeps the test honest if the tiny default PIL font defeats Tesseract on some hosts; on this host the label is OCR-readable. If it is not, render the label with `make_text_image` by giving `FakeScannerBackend` a font — do not delete the assertions.)

- [ ] **Step 3: Run, expect FAIL** — `uv run pytest tests/test_pipeline.py -v`.

- [ ] **Step 4: Implement** — `backend/app/worker/pipeline.py`:

Imports:

```python
from app.config import get_primary_language, get_settings
from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, TranslationStatus
from app.services.llm import describe as llm_describe
from app.services.llm import embed as llm_embed
from app.services.llm import translate as llm_translate
```

`process_document` — add the translation step after the summary:

```python
        pages = _extract_content(session, doc, storage, payload)
        _ensure_content_chunks(session, doc, pages)
        _ensure_summary(session, doc, storage)
        _ensure_translation(session, doc)
        _ensure_metadata_chunk(session, doc)
        _embed_pending_chunks(session, doc)
```

Add a helper used by summary and translation:

```python
def _content_chunks(session: Session, doc: Document) -> list[Chunk]:
    return list(
        session.exec(
            select(Chunk)
            .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
            .order_by(Chunk.chunk_index)
        ).all()
    )
```

Replace `_ensure_summary`:

```python
def _ensure_summary(session: Session, doc: Document, storage: Storage) -> None:
    if doc.doc_type == DocType.video or doc.summary is not None:
        return
    text = "\n\n".join(c.content for c in _content_chunks(session, doc)).strip()
    if doc.doc_type == DocType.image and len(text) < IMAGE_SUMMARY_TEXT_THRESHOLD:
        # No usable extracted text (a photo, or no-OCR) — send the file to vision.
        result = llm_describe(image_path=storage.abs_path(doc.file_path))
    elif text:
        result = llm_describe(text=text[:SUMMARY_INPUT_CHARS])
    else:
        return  # nothing to summarize (e.g. a no-OCR scan or an empty PDF)
    doc.summary = result.summary
    doc.detected_language = result.language
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.summary,
            content=result.summary,
        )
    )
    session.commit()
```

(Scans are no longer excluded — see "Deviation from the spec".)

Add `_ensure_translation`:

```python
def _ensure_translation(session: Session, doc: Document) -> None:
    target = get_primary_language()
    if not doc.detected_language or doc.detected_language == target:
        return
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return
    content_chunks = _content_chunks(session, doc)
    if not content_chunks:
        return
    try:
        # translate everything first so a failure never leaves partial translation chunks
        translated = [(c, llm_translate(c.content, target)) for c in content_chunks]
    except Exception:
        log.exception("Translation failed for document %s", doc.id)
        doc.translation_status = TranslationStatus.failed
        session.commit()
        return
    next_index = _next_chunk_index(session, doc)
    for offset, (chunk, text) in enumerate(translated):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index + offset,
                page_number=chunk.page_number,
                source=ChunkSource.translation,
                content=text,
            )
        )
    doc.translation_status = TranslationStatus.done
    session.commit()
```

- [ ] **Step 5: Run, expect PASS** — `uv run pytest -q` (whole backend suite, including the older image-summary tests which still pass because `describe(text=...)`/`describe(image_path=...)` routing is unchanged).

- [ ] **Step 6: Commit (Tasks 5 + 6)**

```bash
git add backend/app/services/llm.py backend/tests/test_llm.py backend/app/worker/pipeline.py backend/tests/conftest.py backend/tests/test_pipeline.py
git commit -m "feat: detect document language and store searchable translation

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Document text endpoint — translation variant

**Files:**
- Modify: `backend/app/api/documents.py` (`document_text`)
- Test: `backend/tests/test_documents.py`

**Interfaces:**
- Consumes: `ChunkSource.translation`, `get_primary_language()`.
- Produces: `GET /api/documents/{id}/text?variant=content|translation` (default `content`; anything else → 422) returning `{summary, variant, detected_language, translation_status, translation_language, chunks: [{chunk_index, page_number, content}]}`.

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_documents.py`:

```python
def test_document_text_translation_variant(auth_client, session):
    from app.models import ChunkSource

    doc = seed_document(
        session,
        "Brief",
        [
            {"content": "Hallo Welt", "page_number": 1},
            {"content": "Ciao mondo", "page_number": 1, "source": ChunkSource.translation},
        ],
        detected_language="de",
        translation_status="done",
    )
    original = auth_client.get(f"/api/documents/{doc.id}/text").json()
    assert [c["content"] for c in original["chunks"]] == ["Hallo Welt"]
    assert original["variant"] == "content"
    assert original["detected_language"] == "de"
    assert original["translation_status"] == "done"
    assert original["translation_language"] == "it"

    translated = auth_client.get(f"/api/documents/{doc.id}/text?variant=translation").json()
    assert [c["content"] for c in translated["chunks"]] == ["Ciao mondo"]
    assert auth_client.get(f"/api/documents/{doc.id}/text?variant=bogus").status_code == 422
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_documents.py -v`.

- [ ] **Step 3: Implement** — `backend/app/api/documents.py`: add `from typing import Literal` and `from app.config import get_primary_language`; replace `document_text`:

```python
@router.get("/{document_id}/text")
def document_text(
    document_id: uuid.UUID,
    variant: Literal["content", "translation"] = "content",
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    source = ChunkSource.translation if variant == "translation" else ChunkSource.content
    chunks = session.exec(
        select(Chunk)
        .where(Chunk.document_id == doc.id, Chunk.source == source)
        .order_by(Chunk.chunk_index)
    ).all()
    return {
        "summary": doc.summary,
        "variant": variant,
        "detected_language": doc.detected_language,
        "translation_status": doc.translation_status,
        "translation_language": get_primary_language(),
        "chunks": [
            {"chunk_index": c.chunk_index, "page_number": c.page_number, "content": c.content}
            for c in chunks
        ],
    }
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/documents.py backend/tests/test_documents.py
git commit -m "feat: serve translated text variant

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Re-process endpoint + forced OCR in the pipeline

**Files:**
- Modify: `backend/app/api/documents.py` (new endpoint)
- Modify: `backend/app/worker/pipeline.py` (`_extract_content`, new `_reocr_pdf`)
- Create: `backend/tests/test_reprocess.py`

**Interfaces:**
- Consumes: `enqueue(session, type, payload)` from `app.services.jobs`; `pdf_to_searchable_pdf`, `extract_pdf_text`.
- Produces: `POST /api/documents/{id}/reprocess` body `{ocr_languages: str, ocr_enabled: bool = true}` → serialized document with `status: "pending"`; enqueues `process_document` with payload `{"document_id": "<uuid>", "force_ocr": true}`. Errors: 409 `document_busy`, 422 `not_reprocessable` (video).

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_reprocess.py`:

```python
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Job
from app.services.ocr import ocr_image
from app.worker import pipeline
from tests.helpers import make_text_image, seed_document


def _ready_doc(session, **kwargs):
    return seed_document(
        session,
        "Doc",
        [
            {"content": "testo", "page_number": 1},
            {"content": "riassunto", "source": ChunkSource.summary},
            {"content": "traduzione", "page_number": 1, "source": ChunkSource.translation},
            {"content": "Doc", "source": ChunkSource.metadata},
        ],
        summary="riassunto",
        detected_language="de",
        translation_status="done",
        **kwargs,
    )


def test_reprocess_resets_and_enqueues(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "deu"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["ocr_languages"] == "deu"
    assert body["summary"] is None
    assert body["detected_language"] is None
    assert body["translation_status"] is None

    sources = {c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))}
    assert sources == {ChunkSource.metadata}
    job = session.exec(select(Job).where(Job.type == "process_document")).one()
    assert job.payload == {"document_id": str(doc.id), "force_ocr": True}


def test_reprocess_busy_returns_409(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf, status=DocStatus.processing)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "document_busy"


def test_reprocess_twice_second_is_409(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf)
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).status_code == 200
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).status_code == 409
    assert len(session.exec(select(Job).where(Job.type == "process_document")).all()) == 1


def test_reprocess_video_is_rejected(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.video)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_reprocessable"


def test_force_ocr_reocrs_pdf_with_existing_text_layer(session, tmp_path, monkeypatch, llm_stub):
    from app.services.storage import Storage

    storage = Storage(tmp_path / "store")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    img = make_text_image(tmp_path / "p.png", "FATTURA 2026")
    pdf_bytes, _ = ocr_image(img, "eng")  # already has a text layer → normal path would not OCR

    doc = seed_document(session, "Pdf", [], doc_type=DocType.pdf, status=DocStatus.pending, ocr_languages="ita")
    rel, _ = storage.store_file(doc.id, ".pdf", pdf_bytes)
    doc.file_path = rel
    session.commit()

    calls = []
    real = pipeline.pdf_to_searchable_pdf
    monkeypatch.setattr(
        pipeline, "pdf_to_searchable_pdf", lambda path, langs: calls.append(langs) or real(path, langs)
    )
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert calls == ["ita"]
    assert doc.status == DocStatus.ready
    content = " ".join(
        c.content for c in session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
        )
    )
    assert "FATTURA" in content.upper()


def test_force_ocr_scan_uses_stored_pdf_without_session(session, tmp_path, monkeypatch, llm_stub):
    from app.services.storage import Storage

    storage = Storage(tmp_path / "store")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    img = make_text_image(tmp_path / "s.png", "VERBALE 9")
    pdf_bytes, _ = ocr_image(img, "eng")
    doc = seed_document(session, "Scan", [], doc_type=DocType.scan, status=DocStatus.pending)
    rel, _ = storage.store_file(doc.id, ".pdf", pdf_bytes)
    doc.file_path = rel
    session.commit()

    # payload has no scan_session_id: the scan branch must not be entered
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 1
```

(The `pdf_to_searchable_pdf` wrapper is a pass-through spy — it calls the real function — so the "only llm mocked" rule holds.)

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_reprocess.py -v`.

- [ ] **Step 3: Implement the endpoint** — `backend/app/api/documents.py`: add imports `from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, DocumentTag, Folder, Tag` and `from app.services.jobs import enqueue`; add:

```python
class ReprocessRequest(BaseModel):
    ocr_languages: str
    ocr_enabled: bool = True


@router.post("/{document_id}/reprocess")
def reprocess_document(
    document_id: uuid.UUID,
    body: ReprocessRequest,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    if doc.doc_type == DocType.video:
        raise api_error(422, "not_reprocessable", "Videos have no text to re-process")
    for chunk in session.exec(
        select(Chunk).where(
            Chunk.document_id == doc.id,
            Chunk.source.in_([ChunkSource.content, ChunkSource.summary, ChunkSource.translation]),
        )
    ):
        session.delete(chunk)
    doc.ocr_languages = body.ocr_languages
    doc.ocr_enabled = body.ocr_enabled
    doc.summary = None
    doc.detected_language = None
    doc.translation_status = None
    doc.error_message = None
    doc.status = DocStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    session.commit()
    enqueue(session, "process_document", {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    return serialize(session, doc)
```

(`enqueue` commits the job itself.)

- [ ] **Step 4: Implement forced OCR** — `backend/app/worker/pipeline.py`, in `_extract_content` right after the video/has-chunks guard:

```python
    if payload.get("force_ocr") and doc.doc_type in (DocType.pdf, DocType.scan):
        return _reocr_pdf(session, doc, storage)
```

and add:

```python
def _reocr_pdf(session: Session, doc: Document, storage: Storage) -> list[tuple[int | None, str]]:
    """Re-process: OCR the stored PDF again (scan page images are gone after compile)."""
    path = storage.abs_path(doc.file_path)
    if doc.ocr_enabled:
        pdf_bytes, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)
        rel, size = storage.store_file(doc.id, ".pdf", pdf_bytes)
        doc.file_path = rel
        doc.file_size = size
    else:
        pages = extract_pdf_text(path)
    doc.page_count = len(pages)
    session.commit()
    return pages
```

(Images need no change: their content chunks were deleted, so the normal image branch OCRs again with the new language. Text/docx re-extract normally.)

- [ ] **Step 5: Run, expect PASS** — `uv run pytest -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/documents.py backend/app/worker/pipeline.py backend/tests/test_reprocess.py
git commit -m "feat: re-process documents with a new OCR language

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Scan API — per-page device and full compile body

**Files:**
- Modify: `backend/app/api/scan.py` (`PageScanRequest`, `CompileRequest`, `scan_page`, `compile_session`)
- Modify: `backend/app/services/scanner.py` (`FakeScannerBackend.scan` records device)
- Test: `backend/tests/test_scan_api.py`

**Interfaces:**
- Consumes: `create_pending_document(..., description=, document_date=)` (Task 4).
- Produces: `POST /api/scan/sessions/{id}/pages` body `{dpi?, mode?, device?: str | null}`; `POST /api/scan/sessions/{id}/compile` body `{title, folder_id?, tag_ids?, description?: str = "", document_date?: date | null, ocr_languages?: str | null, ocr_enabled?: bool | null}`. `FakeScannerBackend.last_device: str | None`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_scan_api.py`:

```python
def test_page_scan_uses_request_device(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, device="fake:0").json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={"device": "fake:1"})
    assert fake_scanner.last_device == "fake:1"
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert fake_scanner.last_device == "fake:0"  # falls back to the session device


def test_compile_applies_form_fields(auth_client, fake_scanner, storage, session):
    from app.models import Document

    sid = new_session(auth_client, ocr_languages="ita+eng", ocr_enabled=True).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    resp = auth_client.post(
        f"/api/scan/sessions/{sid}/compile",
        json={
            "title": "Brief",
            "description": "Lettera dalla Germania",
            "document_date": "2021-07-09",
            "ocr_languages": "deu",
            "ocr_enabled": False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["description"] == "Lettera dalla Germania"
    assert body["document_date"] == "2021-07-09"
    assert body["ocr_languages"] == "deu"
    assert body["ocr_enabled"] is False


def test_compile_falls_back_to_session_settings(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, ocr_languages="ita", ocr_enabled=False).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    body = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "X"}).json()
    assert body["ocr_languages"] == "ita"
    assert body["ocr_enabled"] is False
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_scan_api.py -v`.

- [ ] **Step 3: Implement** — `backend/app/services/scanner.py`, `FakeScannerBackend.__init__`: add `self.last_device: str | None = None`; first line of `FakeScannerBackend.scan`: `self.last_device = device`.

`backend/app/api/scan.py`: add `from datetime import date`. Models:

```python
class PageScanRequest(BaseModel):
    dpi: int = 300
    mode: str = "Color"
    device: str | None = None


class CompileRequest(BaseModel):
    title: str
    folder_id: int | None = None
    tag_ids: list[int] = []
    description: str = ""
    document_date: date | None = None
    ocr_languages: str | None = None
    ocr_enabled: bool | None = None
```

In `scan_page`: `png = scan_locked(backend, dpi=body.dpi, mode=body.mode, device=body.device or scan_session.device)`.

In `compile_session`, the `create_pending_document` call becomes:

```python
    doc = create_pending_document(
        db,
        title=body.title,
        description=body.description,
        document_date=body.document_date,
        doc_type=DocType.scan,
        ocr_languages=body.ocr_languages or scan_session.ocr_languages,
        ocr_enabled=scan_session.ocr_enabled if body.ocr_enabled is None else body.ocr_enabled,
        folder_id=body.folder_id,
        tag_ids=body.tag_ids,
        original_filename=None,
    )
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/scan.py backend/app/services/scanner.py backend/tests/test_scan_api.py
git commit -m "feat: per-page scanner device and full compile form

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Frontend types, date helpers, upload date

**Files:**
- Create: `frontend/src/lib/dates.ts`, `frontend/src/lib/dates.test.ts`
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/upload.ts`, `frontend/src/lib/upload.test.ts`, `frontend/src/components/UploadDialog.tsx`

**Interfaces:**
- Produces: `todayIso(now?: Date): string` (local date `YYYY-MM-DD`), `formatDate(iso: string): string` (`DD/MM/YYYY`); `Document` gains `ocr_enabled: boolean`, `document_date: string`, `detected_language: string | null`, `translation_status: "done" | "failed" | null`; `DocumentText` gains `variant`, `detected_language`, `translation_status`, `translation_language`; `UploadFields.documentDate?: string`.

- [ ] **Step 1: Write the failing tests** — `frontend/src/lib/dates.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatDate, todayIso } from "./dates";

describe("dates", () => {
  it("todayIso uses the local calendar date", () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
  });

  it("formatDate renders DD/MM/YYYY and passes through junk", () => {
    expect(formatDate("2019-03-04")).toBe("04/03/2019");
    expect(formatDate("garbage")).toBe("garbage");
  });
});
```

Append to `frontend/src/lib/upload.test.ts` inside `describe("buildUploadForm")`:

```ts
  it("sends document_date when set", () => {
    const form = buildUploadForm(file, { documentDate: "2018-12-01" });
    expect(form.get("document_date")).toBe("2018-12-01");
    expect(buildUploadForm(file, {}).has("document_date")).toBe(false);
  });
```

- [ ] **Step 2: Run, expect FAIL** — `npx vitest run src/lib/dates.test.ts src/lib/upload.test.ts`.

- [ ] **Step 3: Implement** — `frontend/src/lib/dates.ts`:

```ts
const pad = (n: number) => String(n).padStart(2, "0");

export function todayIso(now: Date = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y}` : iso;
}
```

`frontend/src/lib/types.ts` — in `Document` after `ocr_languages`:

```ts
  ocr_enabled: boolean;
  document_date: string;
  detected_language: string | null;
  translation_status: "done" | "failed" | null;
```

Replace `DocumentText`:

```ts
export interface DocumentText {
  summary: string | null;
  variant: "content" | "translation";
  detected_language: string | null;
  translation_status: "done" | "failed" | null;
  translation_language: string;
  chunks: { chunk_index: number; page_number: number | null; content: string }[];
}
```

`frontend/src/lib/upload.ts`: `UploadFields` gains `documentDate?: string;`; in `buildUploadForm` before `return form;`: `if (fields.documentDate) form.append("document_date", fields.documentDate);`.

`frontend/src/components/UploadDialog.tsx`: add `const [documentDate, setDocumentDate] = useState(todayIso);` (import `todayIso` from `@/lib/dates`), pass `documentDate` into `buildUploadForm`, and add after the title field:

```tsx
        <div>
          <Label htmlFor="up-date">Document date</Label>
          <Input id="up-date" type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </div>
```

- [ ] **Step 4: Run, expect PASS** — `npx vitest run && npm run lint && npm run build` (the build may surface test fixtures typed as `Document` that miss the new fields — add them there).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/dates.ts frontend/src/lib/dates.test.ts frontend/src/lib/types.ts frontend/src/lib/upload.ts frontend/src/lib/upload.test.ts frontend/src/components/UploadDialog.tsx
git commit -m "feat: document date on upload and new document fields

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Data router (prerequisite for `useBlocker`)

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/main.tsx`

**Interfaces:**
- Produces: `App` renders `<RouterProvider router={router} />`; same paths: `/login`, `/`, `/documents/:id`, `/scan`, `/search`, `/chat`, with `RequireAuth` → `Layout` nesting unchanged.

- [ ] **Step 1: Implement** — `frontend/src/App.tsx`:

```tsx
import { createBrowserRouter, RouterProvider } from "react-router";
import { RequireAuth } from "@/auth";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/LoginPage";
import { BrowsePage } from "@/pages/BrowsePage";
import { DocumentPage } from "@/pages/DocumentPage";
import { ScanPage } from "@/pages/ScanPage";
import { SearchPage } from "@/pages/SearchPage";
import { ChatPage } from "@/pages/ChatPage";

// Data router: required by useBlocker (scan page leave guard).
const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { path: "/", element: <BrowsePage /> },
          { path: "/documents/:id", element: <DocumentPage /> },
          { path: "/scan", element: <ScanPage /> },
          { path: "/search", element: <SearchPage /> },
          { path: "/chat", element: <ChatPage /> },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
```

`frontend/src/main.tsx`: remove the `BrowserRouter` import and wrapper so the tree is `<StrictMode><QueryClientProvider><AuthProvider><App /></AuthProvider></QueryClientProvider></StrictMode>`.

- [ ] **Step 2: Verify** — `npx vitest run && npm run lint && npm run build`. Then `npm run dev` with the backend running and click through login, browse, a document, scan, search, chat; reload on `/documents/<id>` (deep link works). Expected: identical behavior to before.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx frontend/src/main.tsx
git commit -m "refactor: switch to data router for navigation blocking

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Scan state machine rewrite

**Files:**
- Modify: `frontend/src/lib/scanWizard.ts`, `frontend/src/lib/scanWizard.test.ts`

**Interfaces:**
- Produces:
  - `type ScanPhase = "starting" | "ready" | "scanning" | "compiling" | "done"`
  - `interface ScanState { phase; sessionId: number | null; pages: ScanPageInfo[]; selectedPageId: number | null; error: { code: string; message: string } | null; document: Document | null }`
  - `initialScanState` with `phase: "starting"`.
  - Actions: `SESSION_STARTED {sessionId}`, `SESSION_FAILED {code, message}`, `SCAN_STARTED`, `PAGE_SCANNED {page}`, `SCAN_FAILED {code, message}`, `SELECT_PAGE {pageId}`, `PAGE_DELETED {pageId}`, `PAGES_REORDERED {pages}`, `COMPILE_STARTED`, `COMPILED {document}`, `COMPILE_FAILED {code, message}`, `DISMISS_ERROR`, `RESET`. (`SET_LANGUAGES` removed — language lives in page state.)
  - `shouldBlockLeave(state: ScanState): boolean`. `SCANNER_MESSAGES`, `scannerMessage` unchanged.

- [ ] **Step 1: Write the failing tests** — replace the `describe("scanWizardReducer")` block in `scanWizard.test.ts` (keep the `scannerMessage` block) and update the import to include `shouldBlockLeave`:

```ts
describe("scanWizardReducer", () => {
  it("starts in 'starting' and walks the happy path", () => {
    expect(initialScanState.phase).toBe("starting");
    let state = scanWizardReducer(initialScanState, { type: "SESSION_STARTED", sessionId: 5 });
    expect(state.phase).toBe("ready");
    expect(state.sessionId).toBe(5);

    state = scanWizardReducer(state, { type: "SCAN_STARTED" });
    expect(state.phase).toBe("scanning");
    state = scanWizardReducer(state, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(state.phase).toBe("ready");
    expect(state.selectedPageId).toBe(1);

    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILED", document: { id: "d1" } as Document }], state);
    expect(state.phase).toBe("done");
    expect(state.document?.id).toBe("d1");
  });

  it("session start failure stays in starting with the error", () => {
    const state = scanWizardReducer(initialScanState, { type: "SESSION_FAILED", code: "x", message: "down" });
    expect(state.phase).toBe("starting");
    expect(state.error?.message).toBe("down");
    expect(scanWizardReducer(state, { type: "DISMISS_ERROR" }).error).toBeNull();
  });

  it("ignores SCAN_STARTED outside ready", () => {
    expect(scanWizardReducer(initialScanState, { type: "SCAN_STARTED" }).phase).toBe("starting");
  });

  it("each new page becomes the selection; SELECT_PAGE changes it", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
    ]);
    expect(state.selectedPageId).toBe(11);
    state = scanWizardReducer(state, { type: "SELECT_PAGE", pageId: 10 });
    expect(state.selectedPageId).toBe(10);
  });

  it("scan failure keeps pages and returns to ready with the error", () => {
    const state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "SCAN_STARTED" },
      { type: "SCAN_FAILED", code: "scanner_jam", message: "jam" },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);
    expect(state.error?.code).toBe("scanner_jam");
  });

  it("PAGE_DELETED renumbers and moves the selection to the last page when needed", () => {
    const withPages = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
      { type: "PAGE_SCANNED", page: page(12, 3) },
    ]);
    const deletedSelected = scanWizardReducer(withPages, { type: "PAGE_DELETED", pageId: 12 });
    expect(deletedSelected.pages).toEqual([page(10, 1), page(11, 2)]);
    expect(deletedSelected.selectedPageId).toBe(11);

    const selectedFirst = scanWizardReducer(withPages, { type: "SELECT_PAGE", pageId: 10 });
    const deletedOther = scanWizardReducer(selectedFirst, { type: "PAGE_DELETED", pageId: 11 });
    expect(deletedOther.selectedPageId).toBe(10);
    expect(deletedOther.pages).toEqual([page(10, 1), page(12, 2)]);

    const empty = reduceAll(
      [{ type: "PAGE_DELETED", pageId: 10 }, { type: "PAGE_DELETED", pageId: 11 }, { type: "PAGE_DELETED", pageId: 12 }],
      withPages,
    );
    expect(empty.selectedPageId).toBeNull();
  });

  it("PAGES_REORDERED replaces the list; COMPILE_FAILED returns to ready", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "PAGE_SCANNED", page: page(2, 2) },
      { type: "PAGES_REORDERED", pages: [page(2, 1), page(1, 2)] },
    ]);
    expect(state.pages[0].id).toBe(2);
    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILE_FAILED", code: "no_pages", message: "x" }], state);
    expect(state.phase).toBe("ready");
    expect(state.error?.code).toBe("no_pages");
  });

  it("RESET returns to the initial state (a new session will start)", () => {
    const state = reduceAll([{ type: "SESSION_STARTED", sessionId: 1 }, { type: "RESET" }]);
    expect(state).toEqual(initialScanState);
  });
});

describe("shouldBlockLeave", () => {
  it("blocks only with unsaved pages", () => {
    const ready = scanWizardReducer(initialScanState, { type: "SESSION_STARTED", sessionId: 1 });
    expect(shouldBlockLeave(ready)).toBe(false);
    const withPage = scanWizardReducer(ready, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(shouldBlockLeave(withPage)).toBe(true);
    const done = scanWizardReducer(withPage, { type: "COMPILED", document: { id: "d" } as Document });
    expect(shouldBlockLeave(done)).toBe(false);
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `npx vitest run src/lib/scanWizard.test.ts`.

- [ ] **Step 3: Implement** — replace everything above `SCANNER_MESSAGES` in `frontend/src/lib/scanWizard.ts`:

```ts
import type { Document, ScanPageInfo } from "./types";

export type ScanPhase = "starting" | "ready" | "scanning" | "compiling" | "done";

export interface ScanState {
  phase: ScanPhase;
  sessionId: number | null;
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  error: { code: string; message: string } | null;
  document: Document | null;
}

export const initialScanState: ScanState = {
  phase: "starting",
  sessionId: null,
  pages: [],
  selectedPageId: null,
  error: null,
  document: null,
};

export type ScanAction =
  | { type: "SESSION_STARTED"; sessionId: number }
  | { type: "SESSION_FAILED"; code: string; message: string }
  | { type: "SCAN_STARTED" }
  | { type: "PAGE_SCANNED"; page: ScanPageInfo }
  | { type: "SCAN_FAILED"; code: string; message: string }
  | { type: "SELECT_PAGE"; pageId: number }
  | { type: "PAGE_DELETED"; pageId: number }
  | { type: "PAGES_REORDERED"; pages: ScanPageInfo[] }
  | { type: "COMPILE_STARTED" }
  | { type: "COMPILED"; document: Document }
  | { type: "COMPILE_FAILED"; code: string; message: string }
  | { type: "DISMISS_ERROR" }
  | { type: "RESET" };

export function scanWizardReducer(state: ScanState, action: ScanAction): ScanState {
  switch (action.type) {
    case "SESSION_STARTED":
      return { ...initialScanState, phase: "ready", sessionId: action.sessionId };
    case "SESSION_FAILED":
      return { ...state, phase: "starting", error: { code: action.code, message: action.message } };
    case "SCAN_STARTED":
      return state.phase === "ready" ? { ...state, phase: "scanning", error: null } : state;
    case "PAGE_SCANNED":
      return { ...state, phase: "ready", pages: [...state.pages, action.page], selectedPageId: action.page.id };
    case "SCAN_FAILED":
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "SELECT_PAGE":
      return { ...state, selectedPageId: action.pageId };
    case "PAGE_DELETED": {
      const remaining = state.pages
        .filter((p) => p.id !== action.pageId)
        .map((p, index) => ({ ...p, page_number: index + 1 }));
      const selectedPageId =
        state.selectedPageId === action.pageId ? (remaining.at(-1)?.id ?? null) : state.selectedPageId;
      return { ...state, pages: remaining, selectedPageId };
    }
    case "PAGES_REORDERED":
      return { ...state, pages: action.pages };
    case "COMPILE_STARTED":
      return { ...state, phase: "compiling", error: null };
    case "COMPILED":
      return { ...state, phase: "done", document: action.document };
    case "COMPILE_FAILED":
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "DISMISS_ERROR":
      return { ...state, error: null };
    case "RESET":
      return initialScanState;
  }
}

/** Unsaved scanned pages exist: leaving the page would discard them. */
export function shouldBlockLeave(state: ScanState): boolean {
  return state.pages.length > 0 && state.phase !== "done";
}
```

- [ ] **Step 4: Run** — `npx vitest run src/lib/scanWizard.test.ts` → PASS. `npm run build` now fails in the old `ScanPage.tsx` (`SET_LANGUAGES`, `state.languages`, `"setup"`); Task 13 replaces that file. Commit Task 12 together with Task 13 (single commit at the end of Task 13), so `main` never has a broken build.

---

### Task 13: Scan page — components, leave guard, page wiring

**Files:**
- Create: `frontend/src/hooks/useLeaveGuard.ts`, `frontend/src/components/scan/ScanToolbar.tsx`, `frontend/src/components/scan/ScanPreview.tsx`, `frontend/src/components/scan/PageCarousel.tsx`, `frontend/src/components/scan/ScanSidebar.tsx`
- Replace: `frontend/src/pages/ScanPage.tsx`

**Interfaces:**
- Consumes: reducer + `shouldBlockLeave` (Task 12); `OcrLanguageSelect`, `DEFAULT_OCR_LANGUAGES` (Task 2); `todayIso` (Task 10); scan API from Task 9; `usePreviewImage(pageId)`, `scanDeviceHint(devices)`, `useFolders`, `useTags`, `cn`.
- Produces: `useLeaveGuard(active: boolean, message: string, onLeave: () => void): void`; `ScanFormFields { title; description; documentDate; folderId: number | null; tagIds: number[] }`; `emptyScanForm(): ScanFormFields`.

- [ ] **Step 1: Leave guard hook** — `frontend/src/hooks/useLeaveGuard.ts`:

```ts
import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

/** Confirm before leaving: in-app navigation via useBlocker, reload/close via beforeunload. */
export function useLeaveGuard(active: boolean, message: string, onLeave: () => void): void {
  const onLeaveRef = useRef(onLeave);
  onLeaveRef.current = onLeave;

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => active && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm(message)) {
      onLeaveRef.current();
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker, message]);

  useEffect(() => {
    if (!active) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = ""; // browsers show their own fixed "Leave site?" text
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [active]);
}
```

- [ ] **Step 2: Toolbar** — `frontend/src/components/scan/ScanToolbar.tsx`:

```tsx
import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";
import { Select } from "@/components/ui/select";
import { scanDeviceHint } from "@/lib/scanDevices";
import type { ScanDevice, ScanStatus } from "@/lib/types";

function StatusPill({ status }: { status: ScanStatus | undefined }) {
  if (!status) return <span className="rounded-full bg-zinc-100 px-3 py-1 text-zinc-500">Checking scanner…</span>;
  if (!status.available)
    return <span className="rounded-full bg-red-50 px-3 py-1 text-red-700">● Scanner offline</span>;
  return (
    <span className="rounded-full bg-green-50 px-3 py-1 text-green-700">
      ● Scanner ready{status.busy ? " (busy)" : ""}
    </span>
  );
}

export function ScanToolbar({
  status,
  devices,
  device,
  onDeviceChange,
  languages,
  onLanguagesChange,
  ocrEnabled,
  onOcrEnabledChange,
}: {
  status: ScanStatus | undefined;
  devices: ScanDevice[];
  device: string | null;
  onDeviceChange: (device: string | null) => void;
  languages: string;
  onLanguagesChange: (languages: string) => void;
  ocrEnabled: boolean;
  onOcrEnabledChange: (enabled: boolean) => void;
}) {
  const hint = scanDeviceHint(devices);
  return (
    <div className="flex flex-wrap items-center justify-end gap-3 text-sm">
      <StatusPill status={status} />
      {hint === "none" && <span className="text-red-600">No scanner detected — check power and USB.</span>}
      {hint === "single" && <span className="text-zinc-600">{devices[0].name}</span>}
      {hint === "multiple" && (
        <Select
          aria-label="Scanner"
          className="w-56"
          value={device ?? ""}
          onChange={(e) => onDeviceChange(e.target.value || null)}
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </Select>
      )}
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={ocrEnabled} onChange={(e) => onOcrEnabledChange(e.target.checked)} />
        Run OCR
      </label>
      {ocrEnabled && (
        <OcrLanguageSelect
          aria-label="OCR language"
          className="w-48"
          value={languages}
          onChange={(e) => onLanguagesChange(e.target.value)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Big preview** — `frontend/src/components/scan/ScanPreview.tsx`:

```tsx
import { usePreviewImage } from "@/hooks/usePreviewImage";

function PageImage({ pageId }: { pageId: number }) {
  const url = usePreviewImage(pageId);
  if (!url) return <span className="text-zinc-300">…</span>;
  return <img src={url} alt="Selected page" className="max-h-full max-w-full object-contain" />;
}

export function ScanPreview({
  pageId,
  previewUrl,
  scanning,
}: {
  pageId: number | null;
  previewUrl: string | null;
  scanning: boolean;
}) {
  return (
    <div className="relative flex h-[60vh] items-center justify-center overflow-hidden rounded-lg border border-zinc-200 bg-zinc-50 p-2">
      {previewUrl ? (
        <>
          <img src={previewUrl} alt="Scanner preview" className="max-h-full max-w-full object-contain" />
          <span className="absolute top-2 left-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
            Preview — not saved
          </span>
        </>
      ) : pageId !== null ? (
        <PageImage key={pageId} pageId={pageId} />
      ) : (
        <p className="text-zinc-400">No pages yet — place a page on the scanner and press “Scan first page”.</p>
      )}
      {scanning && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-zinc-600">Scanning…</div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Carousel** — `frontend/src/components/scan/PageCarousel.tsx`:

```tsx
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  selected,
  isFirst,
  isLast,
  disabled,
  onSelect,
  onMove,
  onDelete,
}: {
  page: ScanPageInfo;
  selected: boolean;
  isFirst: boolean;
  isLast: boolean;
  disabled: boolean;
  onSelect: () => void;
  onMove: (direction: -1 | 1) => void;
  onDelete: () => void;
}) {
  const url = usePreviewImage(page.id);
  return (
    <div
      className={cn(
        "w-28 shrink-0 rounded border bg-white p-1",
        selected ? "border-zinc-900 ring-2 ring-zinc-900" : "border-zinc-200",
      )}
    >
      <button type="button" className="block w-full" onClick={onSelect} title={`Show page ${page.page_number}`}>
        {url ? (
          <img src={url} alt={`Page ${page.page_number}`} className="h-32 w-full rounded object-cover" />
        ) : (
          <div className="flex h-32 items-center justify-center text-zinc-300">…</div>
        )}
      </button>
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        <span>p. {page.page_number}</span>
        <span className="flex gap-1">
          <button disabled={disabled || isFirst} onClick={() => onMove(-1)} title="Move left">
            ←
          </button>
          <button disabled={disabled || isLast} onClick={() => onMove(1)} title="Move right">
            →
          </button>
          <button disabled={disabled} onClick={onDelete} title="Delete page" className="text-red-500">
            ×
          </button>
        </span>
      </div>
    </div>
  );
}

export function PageCarousel({
  pages,
  selectedPageId,
  disabled,
  onSelect,
  onMove,
  onDelete,
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onMove: (index: number, direction: -1 | 1) => void;
  onDelete: (pageId: number) => void;
}) {
  if (pages.length === 0) return null;
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {pages.map((page, index) => (
        <Thumb
          key={page.id}
          page={page}
          selected={page.id === selectedPageId}
          isFirst={index === 0}
          isLast={index === pages.length - 1}
          disabled={disabled}
          onSelect={() => onSelect(page.id)}
          onMove={(direction) => onMove(index, direction)}
          onDelete={() => onDelete(page.id)}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Sidebar** — `frontend/src/components/scan/ScanSidebar.tsx`:

```tsx
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { todayIso } from "@/lib/dates";
import type { ScanPhase } from "@/lib/scanWizard";
import type { Folder, Tag } from "@/lib/types";

export interface ScanFormFields {
  title: string;
  description: string;
  documentDate: string;
  folderId: number | null;
  tagIds: number[];
}

export function emptyScanForm(): ScanFormFields {
  return { title: "", description: "", documentDate: todayIso(), folderId: null, tagIds: [] };
}

export function ScanSidebar({
  fields,
  onChange,
  folders,
  tags,
  phase,
  pageCount,
  previewing,
  onPreview,
  onScan,
  onFinish,
  onDiscard,
}: {
  fields: ScanFormFields;
  onChange: (patch: Partial<ScanFormFields>) => void;
  folders: Folder[];
  tags: Tag[];
  phase: ScanPhase;
  pageCount: number;
  previewing: boolean;
  onPreview: () => void;
  onScan: () => void;
  onFinish: () => void;
  onDiscard: () => void;
}) {
  const busy = phase !== "ready" || previewing;
  return (
    <aside className="w-full space-y-3 lg:w-72">
      <div>
        <Label htmlFor="scan-title">Title</Label>
        <Input id="scan-title" value={fields.title} onChange={(e) => onChange({ title: e.target.value })} />
      </div>
      <div>
        <Label htmlFor="scan-desc">Description</Label>
        <Textarea
          id="scan-desc"
          rows={3}
          value={fields.description}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="scan-date">Document date</Label>
        <Input
          id="scan-date"
          type="date"
          value={fields.documentDate}
          onChange={(e) => onChange({ documentDate: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="scan-folder">Folder</Label>
        <Select
          id="scan-folder"
          value={fields.folderId ?? ""}
          onChange={(e) => onChange({ folderId: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">(root)</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </Select>
      </div>
      {tags.length > 0 && (
        <div>
          <Label>Tags</Label>
          <div className="flex flex-wrap gap-2">
            {tags.map((tag) => (
              <label key={tag.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={fields.tagIds.includes(tag.id)}
                  onChange={(e) =>
                    onChange({
                      tagIds: e.target.checked
                        ? [...fields.tagIds, tag.id]
                        : fields.tagIds.filter((x) => x !== tag.id),
                    })
                  }
                />
                {tag.name}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="space-y-2 border-t border-zinc-200 pt-3">
        <Button variant="outline" className="w-full" disabled={busy} onClick={onPreview}>
          {previewing ? "Previewing…" : "Preview"}
        </Button>
        <Button className="w-full" disabled={busy} onClick={onScan}>
          {phase === "scanning" ? "Scanning…" : pageCount === 0 ? "Scan first page" : "Scan next page"}
        </Button>
        <Button className="w-full" disabled={busy || pageCount === 0 || !fields.title.trim()} onClick={onFinish}>
          {phase === "compiling" ? "Saving…" : "Finish & save"}
        </Button>
        <Button
          variant="ghost"
          className="w-full"
          disabled={phase === "scanning" || phase === "compiling"}
          onClick={onDiscard}
        >
          Discard
        </Button>
      </div>
    </aside>
  );
}
```

- [ ] **Step 6: Page** — replace `frontend/src/pages/ScanPage.tsx`:

```tsx
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { PageCarousel } from "@/components/scan/PageCarousel";
import { ScanPreview } from "@/components/scan/ScanPreview";
import { emptyScanForm, ScanSidebar, type ScanFormFields } from "@/components/scan/ScanSidebar";
import { ScanToolbar } from "@/components/scan/ScanToolbar";
import { useFolders } from "@/hooks/useFolders";
import { useLeaveGuard } from "@/hooks/useLeaveGuard";
import { useTags } from "@/hooks/useTags";
import { api, ApiError, getToken } from "@/lib/api";
import { DEFAULT_OCR_LANGUAGES } from "@/lib/ocrLanguages";
import { initialScanState, scannerMessage, scanWizardReducer, shouldBlockLeave } from "@/lib/scanWizard";
import type { Document, ScanDevice, ScanPageInfo, ScanStatus } from "@/lib/types";

const LEAVE_MESSAGE = "You have unsaved scanned pages. Leave and discard them?";

function errorInfo(err: unknown): { code: string; message: string } {
  return err instanceof ApiError
    ? { code: err.code, message: err.message }
    : { code: "unknown", message: "Unexpected error" };
}

export function ScanPage() {
  const [state, dispatch] = useReducer(scanWizardReducer, initialScanState);
  const { data: status } = useQuery({
    queryKey: ["scan-status"],
    queryFn: () => api.get<ScanStatus>("/api/scan/status"),
    refetchInterval: 10_000,
  });
  const { data: deviceData } = useQuery({
    queryKey: ["scan-devices"],
    queryFn: () => api.get<{ devices: ScanDevice[]; default: string | null }>("/api/scan/devices"),
  });
  const { data: folders } = useFolders();
  const { data: tags } = useTags();

  const [device, setDevice] = useState<string | null>(null);
  const chosenDevice = device ?? deviceData?.default ?? null;
  const [languages, setLanguages] = useState(DEFAULT_OCR_LANGUAGES);
  const [ocrEnabled, setOcrEnabled] = useState(true);
  const [fields, setFields] = useState<ScanFormFields>(emptyScanForm);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const startInFlight = useRef(false); // StrictMode re-runs effects: create exactly one session

  const clearPreview = useCallback(() => {
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }, []);

  useEffect(() => {
    if (state.phase !== "starting" || state.error !== null || startInFlight.current) return;
    startInFlight.current = true;
    api
      .post<{ id: number }>("/api/scan/sessions", {
        ocr_languages: languages,
        ocr_enabled: ocrEnabled,
        device: chosenDevice,
      })
      .then((session) => dispatch({ type: "SESSION_STARTED", sessionId: session.id }))
      .catch((err) => dispatch({ type: "SESSION_FAILED", ...errorInfo(err) }))
      .finally(() => {
        startInFlight.current = false;
      });
    // languages/ocrEnabled/device are only defaults here; compile and page scans send the current values
  }, [state.phase, state.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const leaveSession = useCallback(() => {
    if (state.sessionId !== null) api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
  }, [state.sessionId]);
  useLeaveGuard(shouldBlockLeave(state), LEAVE_MESSAGE, leaveSession);

  const preview = async () => {
    setPreviewing(true);
    try {
      const resp = await fetch("/api/scan/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
        body: JSON.stringify({ device: chosenDevice }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => null);
        dispatch({
          type: "SCAN_FAILED",
          code: data?.error?.code ?? "unknown",
          message: data?.error?.message ?? "Preview failed",
        });
        return;
      }
      const url = URL.createObjectURL(await resp.blob());
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return url;
      });
    } finally {
      setPreviewing(false);
    }
  };

  const scanPage = async () => {
    clearPreview();
    dispatch({ type: "SCAN_STARTED" });
    try {
      const page = await api.post<ScanPageInfo>(`/api/scan/sessions/${state.sessionId}/pages`, {
        device: chosenDevice,
      });
      dispatch({ type: "PAGE_SCANNED", page: { id: page.id, page_number: page.page_number } });
    } catch (err) {
      dispatch({ type: "SCAN_FAILED", ...errorInfo(err) });
    }
  };

  const selectPage = (pageId: number) => {
    clearPreview();
    dispatch({ type: "SELECT_PAGE", pageId });
  };

  const deletePage = async (pageId: number) => {
    try {
      await api.del(`/api/scan/pages/${pageId}`);
      dispatch({ type: "PAGE_DELETED", pageId });
    } catch (err) {
      dispatch({ type: "SCAN_FAILED", ...errorInfo(err) });
    }
  };

  const movePage = async (index: number, direction: -1 | 1) => {
    const order = state.pages.map((p) => p.id);
    const target = index + direction;
    [order[index], order[target]] = [order[target], order[index]];
    try {
      const resp = await api.post<{ pages: ScanPageInfo[] }>(`/api/scan/sessions/${state.sessionId}/reorder`, {
        page_ids: order,
      });
      dispatch({ type: "PAGES_REORDERED", pages: resp.pages });
    } catch (err) {
      dispatch({ type: "SCAN_FAILED", ...errorInfo(err) });
    }
  };

  const finish = async () => {
    dispatch({ type: "COMPILE_STARTED" });
    try {
      const doc = await api.post<Document>(`/api/scan/sessions/${state.sessionId}/compile`, {
        title: fields.title.trim(),
        description: fields.description,
        document_date: fields.documentDate || null,
        folder_id: fields.folderId,
        tag_ids: fields.tagIds,
        ocr_languages: languages,
        ocr_enabled: ocrEnabled,
      });
      clearPreview();
      dispatch({ type: "COMPILED", document: doc });
    } catch (err) {
      dispatch({ type: "COMPILE_FAILED", ...errorInfo(err) });
    }
  };

  const startOver = () => {
    clearPreview();
    setFields(emptyScanForm());
    dispatch({ type: "RESET" }); // phase "starting" → effect creates a new session
  };

  const discard = async () => {
    if (state.pages.length > 0 && !window.confirm("Discard all scanned pages?")) return;
    if (state.sessionId !== null) await api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
    startOver();
  };

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">Scan</h2>
        <div className="ml-auto">
          <ScanToolbar
            status={status}
            devices={deviceData?.devices ?? []}
            device={chosenDevice}
            onDeviceChange={setDevice}
            languages={languages}
            onLanguagesChange={setLanguages}
            ocrEnabled={ocrEnabled}
            onOcrEnabledChange={setOcrEnabled}
          />
        </div>
      </div>

      {state.error && (
        <div className="flex items-center justify-between rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <span>{scannerMessage(state.error.code, state.error.message)}</span>
          <button onClick={() => dispatch({ type: "DISMISS_ERROR" })}>
            {state.phase === "starting" ? "Retry" : "×"}
          </button>
        </div>
      )}

      {state.phase === "done" && state.document ? (
        <div className="space-y-3">
          <p>
            Document created:{" "}
            <Link className="font-medium underline" to={`/documents/${state.document.id}`}>
              {state.document.title}
            </Link>{" "}
            (processing in the background)
          </p>
          <Button onClick={startOver}>Scan another document</Button>
        </div>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row">
          <div className="min-w-0 flex-1 space-y-3">
            <ScanPreview
              pageId={state.selectedPageId}
              previewUrl={previewUrl}
              scanning={state.phase === "scanning"}
            />
            <PageCarousel
              pages={state.pages}
              selectedPageId={state.selectedPageId}
              disabled={state.phase !== "ready"}
              onSelect={selectPage}
              onMove={movePage}
              onDelete={deletePage}
            />
            {state.phase === "starting" && !state.error && (
              <p className="text-sm text-zinc-500">Starting scan session…</p>
            )}
          </div>
          <ScanSidebar
            fields={fields}
            onChange={(patch) => setFields((prev) => ({ ...prev, ...patch }))}
            folders={folders ?? []}
            tags={tags ?? []}
            phase={state.phase}
            pageCount={state.pages.length}
            previewing={previewing}
            onPreview={preview}
            onScan={scanPage}
            onFinish={finish}
            onDiscard={discard}
          />
        </div>
      )}
    </div>
  );
}
```

(If oxlint reports the `eslint-disable-line` comment as unused because `exhaustive-deps` is not enabled, drop the directive and keep the explanatory comment.)

- [ ] **Step 7: Verify** — `npx vitest run && npm run lint && npm run build` all green.

- [ ] **Step 8: Manual check (dev server, backend + worker running, scanner or `FakeScannerBackend`)**
  - Open `/scan`: exactly one new row in `scan_sessions` (`psql ... -c "select id,status from scan_sessions order by id desc limit 3"`), even in dev StrictMode.
  - Scan two pages: newest page shows big; click first thumbnail → big preview switches; ← → × work.
  - Preview: amber "Preview — not saved" label; scanning or selecting clears it.
  - With one page, click "Browse" in the sidebar → confirm dialog with the exact leave text; Cancel stays; OK leaves and the session row becomes `cancelled`. Reload with pages → browser "Leave site?" dialog.
  - Finish & save disabled until a title is typed; after save, the document link appears and "Scan another document" starts a fresh session.

- [ ] **Step 9: Commit (Tasks 12 + 13)**

```bash
git add frontend/src/lib/scanWizard.ts frontend/src/lib/scanWizard.test.ts frontend/src/hooks/useLeaveGuard.ts frontend/src/components/scan frontend/src/pages/ScanPage.tsx
git commit -m "feat: single-screen scan page with carousel and leave guard

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Document page — date, translation toggle, re-process; card date

**Files:**
- Create: `frontend/src/lib/translation.ts`, `frontend/src/lib/translation.test.ts`
- Modify: `frontend/src/pages/DocumentPage.tsx`, `frontend/src/components/DocumentCard.tsx`

**Interfaces:**
- Consumes: `fileUrl` (Task 1); `DocumentText` / `Document` fields (Task 10); `OcrLanguageSelect`, `DEFAULT_OCR_LANGUAGES` (Task 2); `formatDate` (Task 10); endpoints from Tasks 4, 7, 8.
- Produces: `type TextVariant = "content" | "translation"`; `textVariants(doc: Pick<Document, "translation_status">): TextVariant[]`; `languageLabel(code: string): string`.

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/translation.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { languageLabel, textVariants } from "./translation";

describe("textVariants", () => {
  it("offers the translation only when it is done", () => {
    expect(textVariants({ translation_status: "done" })).toEqual(["content", "translation"]);
    expect(textVariants({ translation_status: "failed" })).toEqual(["content"]);
    expect(textVariants({ translation_status: null })).toEqual(["content"]);
  });
});

describe("languageLabel", () => {
  it("names known languages and upper-cases unknown codes", () => {
    expect(languageLabel("it")).toBe("Italiano");
    expect(languageLabel("de")).toBe("Deutsch");
    expect(languageLabel("pt")).toBe("PT");
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `npx vitest run src/lib/translation.test.ts`.

- [ ] **Step 3: Implement helpers** — `frontend/src/lib/translation.ts`:

```ts
import type { Document } from "./types";

export type TextVariant = "content" | "translation";

export function textVariants(doc: Pick<Document, "translation_status">): TextVariant[] {
  return doc.translation_status === "done" ? ["content", "translation"] : ["content"];
}

const LANGUAGE_LABELS: Record<string, string> = {
  it: "Italiano",
  en: "English",
  de: "Deutsch",
  fr: "Français",
  es: "Español",
};

export function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] ?? code.toUpperCase();
}
```

- [ ] **Step 4: Document page** — in `frontend/src/pages/DocumentPage.tsx`:

Imports to add: `OcrLanguageSelect` from `@/components/OcrLanguageSelect`, `DEFAULT_OCR_LANGUAGES` from `@/lib/ocrLanguages`, `languageLabel, textVariants, type TextVariant` from `@/lib/translation`.

`Viewer`: the text branch becomes `return <TextView doc={doc} />;`.

Replace `TextView`:

```tsx
function TextView({ doc }: { doc: Document }) {
  const variants = textVariants(doc);
  const [variant, setVariant] = useState<TextVariant>("content");
  const active = variants.includes(variant) ? variant : "content";
  const { data } = useQuery({
    queryKey: ["document-text", doc.id, active],
    queryFn: () => api.get<DocumentText>(`/api/documents/${doc.id}/text?variant=${active}`),
  });
  if (!data) return <p className="text-zinc-400">Loading…</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {data.detected_language && <Badge variant="blue">Detected: {data.detected_language.toUpperCase()}</Badge>}
        {variants.length > 1 && (
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-300 text-sm">
            {variants.map((v) => (
              <button
                key={v}
                onClick={() => setVariant(v)}
                className={active === v ? "bg-zinc-900 px-3 py-1 text-white" : "px-3 py-1 hover:bg-zinc-100"}
              >
                {v === "content" ? "Original" : languageLabel(data.translation_language)}
              </button>
            ))}
          </div>
        )}
        {data.translation_status === "failed" && (
          <span className="text-xs text-amber-700">Translation failed — re-process to retry.</span>
        )}
      </div>
      {data.summary && (
        <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
          <span className="font-medium">Summary: </span>
          {data.summary}
        </div>
      )}
      {data.chunks.map((chunk) => (
        <div key={chunk.chunk_index}>
          {chunk.page_number != null && (
            <p className="mb-1 text-xs font-semibold text-zinc-400">Page {chunk.page_number}</p>
          )}
          <p className="whitespace-pre-wrap text-sm">{chunk.content}</p>
        </div>
      ))}
      {data.chunks.length === 0 && <p className="text-zinc-400">No extracted text.</p>}
    </div>
  );
}
```

The tab switch line becomes `{tab === "preview" ? <Viewer doc={doc} /> : <TextView doc={doc} />}`.

In `DocumentPage`, add state and hydration:

```tsx
  const [documentDate, setDocumentDate] = useState("");
  const [ocrLanguages, setOcrLanguages] = useState(DEFAULT_OCR_LANGUAGES);
  const [ocrEnabled, setOcrEnabled] = useState(true);
  const lastStatus = useRef<string | null>(null);
```

Inside the existing hydration `if` block add:

```tsx
      setDocumentDate(doc.document_date);
      setOcrLanguages(doc.ocr_languages);
      setOcrEnabled(doc.ocr_enabled);
```

Add, after the hydration effect:

```tsx
  useEffect(() => {
    if (!doc) return;
    // re-process finished: refetch extracted/translated text
    if (lastStatus.current && lastStatus.current !== "ready" && doc.status === "ready")
      qc.invalidateQueries({ queryKey: ["document-text", doc.id] });
    lastStatus.current = doc.status;
  }, [doc, qc]);
```

`save` mutation body adds `document_date: documentDate || null,`.

Add the re-process mutation after `remove`:

```tsx
  const reprocess = useMutation({
    mutationFn: () =>
      api.post<Document>(`/api/documents/${id}/reprocess`, {
        ocr_languages: ocrLanguages,
        ocr_enabled: ocrEnabled,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["document", id], updated);
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const confirmReprocess = () => {
    const base = "Re-run OCR and AI processing? Extracted text, summary and translation will be replaced.";
    const pdfNote =
      doc && (doc.doc_type === "pdf" || doc.doc_type === "scan") && ocrEnabled
        ? " The PDF is rebuilt from page images."
        : "";
    if (window.confirm(base + pdfNote)) reprocess.mutate();
  };
```

(`setQueryData` with `status: "pending"` makes the existing `refetchInterval` resume polling.)

In the sidebar, after the Description field:

```tsx
        <div>
          <Label htmlFor="d-date">Document date</Label>
          <Input id="d-date" type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </div>
```

After the Delete button, add the OCR section (hidden for video):

```tsx
        {doc.doc_type !== "video" && (
          <div className="space-y-2 border-t border-zinc-200 pt-3">
            <p className="text-sm font-medium">OCR</p>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={ocrEnabled} onChange={(e) => setOcrEnabled(e.target.checked)} />
              Run OCR
            </label>
            {ocrEnabled && (
              <OcrLanguageSelect
                aria-label="OCR language"
                value={ocrLanguages}
                onChange={(e) => setOcrLanguages(e.target.value)}
              />
            )}
            <Button
              variant="outline"
              className="w-full"
              disabled={reprocess.isPending || doc.status === "pending" || doc.status === "processing"}
              onClick={confirmReprocess}
            >
              {reprocess.isPending ? "Starting…" : "Re-process"}
            </Button>
            {reprocess.isError && (
              <p className="text-xs text-red-600">
                {reprocess.error instanceof ApiError ? reprocess.error.message : "Re-process failed"}
              </p>
            )}
          </div>
        )}
```

Import `ApiError` alongside `api, fileUrl` from `@/lib/api`.

- [ ] **Step 5: Card date** — `frontend/src/components/DocumentCard.tsx`: import `formatDate` from `@/lib/dates`; replace the size line:

```tsx
        <p className="mt-2 text-xs text-zinc-400">
          {formatDate(doc.document_date)}
          {doc.page_count ? ` · ${doc.page_count} pages` : ""}
          {doc.file_size ? ` · ${Math.round(doc.file_size / 1024)} KB` : ""}
        </p>
```

- [ ] **Step 6: Verify** — `npx vitest run && npm run lint && npm run build`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/translation.ts frontend/src/lib/translation.test.ts frontend/src/pages/DocumentPage.tsx frontend/src/components/DocumentCard.tsx
git commit -m "feat: document date, translation toggle and re-process on document page

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: End-to-end verification

**Files:** none (verification only; fix-forward commits if something fails).

- [ ] **Step 1: Full suites** — `cd backend && uv run pytest -q` (expect ≥ 143 + new tests, 0 failures); `cd ../frontend && npx vitest run && npm run lint && npm run build`.

- [ ] **Step 2: Apply migration to the dev DB** — `cd backend && uv run alembic upgrade head`; `uv run alembic current` shows `b7c4e2a91d05 (head)`.

- [ ] **Step 3: Restart services** — restart the API and worker the way this host runs them (systemd unit `origami` from `deploy/`, or the README's `uvicorn` + `python -m app.worker` commands).

- [ ] **Step 4: Manual scenarios**
  1. Open any PDF document → renders in the Preview tab, no download. Download button saves the file with the original name.
  2. Scan page checks from Task 13 Step 8.
  3. Scan (or upload) a German document with OCR `deu` → status `ready`; Text tab shows "Detected: DE", toggle Original | Italiano; summary in Italian; search for an Italian word that appears only in the translation finds the document.
  4. On an English-OCR'd German scan, choose `deu` → Re-process → status goes pending → ready; text improves; translation present.
  5. Edit document date, Save, reload → date persists; Browse card shows `DD/MM/YYYY`.
  6. Upload with a past document date → card shows that date.

- [ ] **Step 5: Report** — summarize results to the user with exact test counts; list anything that failed or was skipped (e.g. German OCR test skipped if `tesseract-ocr-deu` still missing).
