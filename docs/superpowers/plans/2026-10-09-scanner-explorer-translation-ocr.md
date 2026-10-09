# Scanner, Explorer, Translation and OCR Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the ten follow-ups in the spec: scanner folder auto-select, page reorder, per-stage processing switches, inline tags; explorer file-system browse, date filter, bulk move/delete; re-translate only, TPM-safe resumable translation; auto-discovered OCR languages.

**Architecture:** The backend (FastAPI + SQLModel + Postgres job queue) gets new columns (`summary_enabled`, `translation_enabled`), a `translation_segments` table, local language detection (`lingua`), a token-budget throttle in `llm.translate`, and new endpoints (`/api/ocr/languages`, `/api/documents/bulk/*`, `/api/documents/{id}/retranslate`). The React frontend gets new shared components (`ProcessingOptions`, `TagInput`, multi-select `OcrLanguageSelect`, `BulkActionBar`, `FolderTiles`) and pure helper modules in `src/lib/` that carry the logic and the unit tests.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, litellm, pytesseract, lingua-language-detector, pytest; React 19, TypeScript, TanStack Query 5, react-router 8, Tailwind 4, @dnd-kit, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-09-scanner-explorer-translation-ocr-design.md`

## Global Constraints

- Backend tests: run from `backend/` with `uv run pytest`. They need Postgres: `docker compose up -d db` from the repo root.
- Frontend tests: run from `frontend/` with `npm test`. Type check with `npx tsc -b`. Lint with `npm run lint`.
- Every API error uses `api_error(status, code, message)` from `app.api.deps`. The response body is `{"error": {"code", "message", "detail"}}`.
- New Alembic revisions chain from the current head `e5a1c7d93b20`.
- New boolean columns use server default `true`, so existing rows keep today's behaviour.
- `PRIMARY_LANGUAGE` and `detected_language` are ISO 639-1 codes (`it`, `de`, ...).
- `LLM_TPM_LIMIT` default `0` = no throttle. `TRANSLATION_SEGMENT_CHARS` default `6000`.
- Bulk endpoints accept at most 500 ids.
- `GET /api/documents` with `date_from > date_to` returns 422.
- Unknown OCR language returns 422 with message `Unknown OCR language: <code>` and code `unknown_ocr_language`.
- UI copy is English. Commit messages follow Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Do not add pagination, folder bulk actions, AI tag suggestions, or throttling for summary/embedding calls (spec section 8).

## Review Focus

1. **One translation call bigger than the whole TPM budget.** `TokenBudget.acquire` must not wait forever. It caps the request at the limit and proceeds when the window is empty. Pinned in Task 9.
2. **Folder tile counts after a bulk move or delete.** The tiles must show the new counts without a reload. The bulk hooks invalidate `["folders"]` as well as `["documents"]`. Pinned in Task 16.
3. **Re-process with a stored language that is no longer installed.** The Document page sends the document's stored `ocr_languages`. The backend must answer 422 `Unknown OCR language: xyz`, and the page must show that message. Pinned in Task 1 (backend) and Task 14 (UI error text).
4. **Re-translate of a document translated before this change.** Old translation chunks (one per overlapping content chunk) must be deleted and replaced by page-based chunks. Pinned in Task 10.
5. **Selection holding ids that are gone after a bulk delete.** After a bulk delete the selection must be empty, and "N selected" must not count deleted ids. Pinned in Task 17.

---

## File Map

**Backend — create**
- `backend/app/api/ocr.py`: `GET /api/ocr/languages`, plus `check_ocr_languages()` used by other routers.
- `backend/app/services/ocr_language_names.py`: Tesseract code to English name map.
- `backend/app/services/language.py`: `detect_language(text)` with lingua.
- `backend/app/models/translation.py`: `TranslationSegment` model.
- `backend/alembic/versions/f2b9d4c61a87_processing_flags.py`
- `backend/alembic/versions/a6c3e8f15d29_translation_segments.py`
- Tests: `tests/test_ocr_languages.py`, `tests/test_language.py`, `tests/test_processing_flags.py`, `tests/test_browse_api.py`, `tests/test_bulk.py`, `tests/test_translation_segments.py`, `tests/test_throttle.py`, `tests/test_retranslate.py`.

**Backend — modify**
- `app/services/ocr.py`: `available_languages()`, `unknown_languages()`.
- `app/main.py`: register the `ocr` router.
- `app/api/uploads.py`, `app/api/scan.py`, `app/api/documents.py`: language validation, processing flags, bulk, retranslate, root/date filters, `translatable`.
- `app/api/folders.py`: `document_count`.
- `app/services/search.py`: date filters.
- `app/services/chunking.py`: `page_texts_from_chunks()`, `segment_pages()`.
- `app/services/llm.py`: summary-only `describe()`, `TokenBudget`, 429 retries.
- `app/config.py`: `llm_tpm_limit`, `translation_segment_chars`.
- `app/models/document.py`, `app/models/__init__.py`.
- `app/worker/pipeline.py`: flags, language detection, per-page resumable translation.
- `tests/conftest.py`: `llm_stub` fakes `detect_language`, `describe` returns `str`.
- `pyproject.toml`: `lingua-language-detector`.
- `README.md`: OCR packages, `LLM_TPM_LIMIT`.

**Frontend — create**
- `src/hooks/useOcrLanguages.ts`
- `src/lib/processing.ts`, `src/components/ProcessingOptions.tsx`
- `src/lib/tagInput.ts`, `src/components/TagInput.tsx`
- `src/lib/scanReorder.ts`
- `src/lib/browseParams.ts`
- `src/lib/selection.ts`, `src/hooks/useSelection.ts`
- `src/components/FolderTiles.tsx`, `src/components/BulkActionBar.tsx`
- Tests next to each new lib/component.

**Frontend — modify**
- `src/lib/types.ts`, `src/lib/ocrLanguages.ts`, `src/components/OcrLanguageSelect.tsx`
- `src/components/FolderPicker.tsx` (+ test)
- `src/components/scan/PageCarousel.tsx`, `ScanSidebar.tsx`, `ScanToolbar.tsx`, `src/pages/ScanPage.tsx`, `src/lib/scanWizard.ts`
- `src/components/UploadDialog.tsx`, `src/lib/upload.ts`
- `src/pages/DocumentPage.tsx`, `src/lib/translation.ts`
- `src/pages/BrowsePage.tsx`, `src/components/DocumentCard.tsx`, `src/components/FolderTree.tsx`, `src/components/Layout.tsx`, `src/hooks/useDocuments.ts`, `src/lib/sorting.ts`
- `src/pages/SearchPage.tsx`
- `package.json` (`@dnd-kit/*`)

---

## Task 1: OCR language discovery and validation (backend)

**Files:**
- Modify: `backend/app/services/ocr.py`
- Create: `backend/app/services/ocr_language_names.py`
- Create: `backend/app/api/ocr.py`
- Modify: `backend/app/main.py`, `backend/app/api/uploads.py`, `backend/app/api/scan.py`, `backend/app/api/documents.py`
- Modify: `README.md`
- Test: `backend/tests/test_ocr_languages.py`

**Interfaces:**
- Produces: `app.services.ocr.available_languages() -> list[str]`, `app.services.ocr.reset_language_cache() -> None`, `app.services.ocr.unknown_languages(value: str) -> list[str]`, `app.api.ocr.check_ocr_languages(value: str | None) -> None` (raises 422), endpoint `GET /api/ocr/languages -> {"languages": [{"code": str, "name": str}], "default": str}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ocr_languages.py`:

```python
import pytest

from app.services import ocr


@pytest.fixture
def installed(monkeypatch):
    """Pretend Tesseract has exactly these languages installed."""
    def apply(codes):
        monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda config="": list(codes))
        ocr.reset_language_cache()
    yield apply
    ocr.reset_language_cache()


def test_available_languages_drops_osd_and_equ(installed):
    installed(["eng", "osd", "ita", "equ", "chi_sim"])
    assert ocr.available_languages() == ["chi_sim", "eng", "ita"]


def test_available_languages_is_cached(installed, monkeypatch):
    installed(["eng"])
    assert ocr.available_languages() == ["eng"]
    monkeypatch.setattr(ocr.pytesseract, "get_languages", lambda config="": ["deu"])
    assert ocr.available_languages() == ["eng"]  # cached for 5 minutes
    ocr.reset_language_cache()
    assert ocr.available_languages() == ["deu"]


def test_unknown_languages(installed):
    installed(["eng", "ita"])
    assert ocr.unknown_languages("ita+eng") == []
    assert ocr.unknown_languages("ita+xyz") == ["xyz"]
    assert ocr.unknown_languages("") == [""]


def test_languages_endpoint(auth_client, installed, monkeypatch):
    installed(["eng", "ita", "zzz"])
    monkeypatch.setenv("DEFAULT_OCR_LANGUAGES", "ita+deu+eng")
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        body = auth_client.get("/api/ocr/languages").json()
    finally:
        get_settings.cache_clear()
    assert body["languages"] == [
        {"code": "eng", "name": "English"},
        {"code": "ita", "name": "Italian"},
        {"code": "zzz", "name": "zzz"},
    ]
    assert body["default"] == "ita+eng"  # deu is not installed


def test_languages_endpoint_default_falls_back_to_first_installed(auth_client, installed, monkeypatch):
    installed(["fra"])
    from app.config import get_settings
    monkeypatch.setenv("DEFAULT_OCR_LANGUAGES", "ita+eng")
    get_settings.cache_clear()
    try:
        body = auth_client.get("/api/ocr/languages").json()
    finally:
        get_settings.cache_clear()
    assert body["default"] == "fra"


def test_languages_endpoint_nothing_installed(auth_client, installed):
    installed([])
    assert auth_client.get("/api/ocr/languages").json() == {"languages": [], "default": ""}


def _assert_unknown(resp, code="xyz"):
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_ocr_language"
    assert resp.json()["error"]["message"] == f"Unknown OCR language: {code}"


def test_upload_rejects_unknown_language(auth_client, storage, installed):
    installed(["eng"])
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        data={"ocr_languages": "eng+xyz"},
    )
    _assert_unknown(resp)


def test_scan_session_and_compile_reject_unknown_language(auth_client, fake_scanner, storage, installed):
    installed(["eng"])
    _assert_unknown(auth_client.post("/api/scan/sessions", json={"ocr_languages": "xyz"}))
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    _assert_unknown(
        auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "T", "ocr_languages": "xyz"})
    )


def test_reprocess_rejects_uninstalled_stored_language(auth_client, session, installed):
    from tests.helpers import seed_document

    installed(["eng"])
    doc = seed_document(session, "Doc", [{"content": "x", "page_number": 1}], doc_type="pdf")
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    _assert_unknown(resp, "ita")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_ocr_languages.py -v`
Expected: FAIL with `AttributeError: module 'app.services.ocr' has no attribute 'reset_language_cache'`.

- [ ] **Step 3: Implement the service functions**

Append to `backend/app/services/ocr.py` (add `import time` at the top):

```python
LANGUAGE_CACHE_SECONDS = 300
_IGNORED_LANGUAGES = {"osd", "equ"}  # orientation/script detection and equations: not text languages
_language_cache: tuple[float, list[str]] | None = None


def available_languages() -> list[str]:
    """Installed Tesseract languages, sorted; cached so new packages show up within 5 minutes."""
    global _language_cache
    now = time.monotonic()
    if _language_cache is None or now - _language_cache[0] > LANGUAGE_CACHE_SECONDS:
        codes = sorted(set(pytesseract.get_languages(config="")) - _IGNORED_LANGUAGES)
        _language_cache = (now, codes)
    return list(_language_cache[1])


def reset_language_cache() -> None:
    global _language_cache
    _language_cache = None


def unknown_languages(value: str) -> list[str]:
    """Codes of a Tesseract `lang` string (`ita+eng`) that are not installed."""
    installed = set(available_languages())
    return [code for code in value.split("+") if code not in installed]
```

Create `backend/app/services/ocr_language_names.py`:

```python
"""English names for common Tesseract language codes; unmapped codes are shown as the code."""

OCR_LANGUAGE_NAMES = {
    "afr": "Afrikaans", "ara": "Arabic", "bul": "Bulgarian", "cat": "Catalan", "ces": "Czech",
    "chi_sim": "Chinese (Simplified)", "chi_tra": "Chinese (Traditional)", "dan": "Danish",
    "deu": "German", "ell": "Greek", "eng": "English", "est": "Estonian", "fin": "Finnish",
    "fra": "French", "heb": "Hebrew", "hin": "Hindi", "hrv": "Croatian", "hun": "Hungarian",
    "ind": "Indonesian", "ita": "Italian", "jpn": "Japanese", "kor": "Korean", "lat": "Latin",
    "lav": "Latvian", "lit": "Lithuanian", "nld": "Dutch", "nor": "Norwegian", "pol": "Polish",
    "por": "Portuguese", "ron": "Romanian", "rus": "Russian", "slk": "Slovak", "slv": "Slovenian",
    "spa": "Spanish", "srp": "Serbian", "swe": "Swedish", "tur": "Turkish", "ukr": "Ukrainian",
    "vie": "Vietnamese",
}


def ocr_language_name(code: str) -> str:
    return OCR_LANGUAGE_NAMES.get(code, code)
```

- [ ] **Step 4: Add the router and the check helper**

Create `backend/app/api/ocr.py`:

```python
from fastapi import APIRouter, Depends

from app.api.deps import api_error, get_current_user
from app.config import get_settings
from app.services.ocr import available_languages, unknown_languages
from app.services.ocr_language_names import ocr_language_name

router = APIRouter(prefix="/api/ocr", tags=["ocr"], dependencies=[Depends(get_current_user)])


def check_ocr_languages(value: str | None) -> None:
    """422 when a client-supplied Tesseract language string names an uninstalled language."""
    if value is None:
        return
    unknown = unknown_languages(value)
    if unknown:
        raise api_error(422, "unknown_ocr_language", f"Unknown OCR language: {unknown[0]}")


@router.get("/languages")
def list_ocr_languages() -> dict:
    codes = available_languages()
    configured = [c for c in get_settings().default_ocr_languages.split("+") if c in codes]
    default = "+".join(configured) or (codes[0] if codes else "")
    return {
        "languages": [{"code": c, "name": ocr_language_name(c)} for c in codes],
        "default": default,
    }
```

In `backend/app/main.py`, add `ocr` to the `from app.api import ...` line and add `app.include_router(ocr.router)` after `app.include_router(folders.router)`.

- [ ] **Step 5: Call the check in every endpoint that accepts `ocr_languages`**

`backend/app/api/uploads.py`: add `from app.api.ocr import check_ocr_languages`, and at the top of `upload_document` (before the extension check) add:

```python
    check_ocr_languages(ocr_languages)
```

`backend/app/api/scan.py`: add `from app.api.ocr import check_ocr_languages`. First line of `create_session`: `check_ocr_languages(body.ocr_languages)`. In `compile_session`, right after `scan_session = get_session_or_404(db, session_id)`: `check_ocr_languages(body.ocr_languages)`.

`backend/app/api/documents.py`: add `from app.api.ocr import check_ocr_languages`. In `reprocess_document`, right after `doc = get_doc_or_404(session, document_id)`: `check_ocr_languages(body.ocr_languages)`.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_ocr_languages.py tests/test_uploads.py tests/test_scan_api.py tests/test_scan_compile.py tests/test_reprocess.py -v`
Expected: PASS. Existing tests use `eng`, `ita`, `deu`, which the README requires to be installed.

- [ ] **Step 7: Update the README**

In `README.md`, replace the line `sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu` with:

```
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu  # add any tesseract-ocr-<lang>
```

Replace "Required for OCR (Italian, English and German)." with "Required for OCR. Install any `tesseract-ocr-<lang>` packages you need: the app discovers installed languages automatically (within 5 minutes, no restart). The test suite needs `ita`, `eng` and `deu`." Keep the `tesseract --list-langs` line.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/ocr.py backend/app/services/ocr_language_names.py backend/app/api/ocr.py backend/app/main.py backend/app/api/uploads.py backend/app/api/scan.py backend/app/api/documents.py backend/tests/test_ocr_languages.py README.md
git commit -m "feat: discover installed OCR languages and validate requests against them"
```

---

## Task 2: Processing flags (`summary_enabled`, `translation_enabled`)

**Files:**
- Modify: `backend/app/models/document.py`
- Create: `backend/alembic/versions/f2b9d4c61a87_processing_flags.py`
- Modify: `backend/app/api/uploads.py`, `backend/app/api/scan.py`, `backend/app/api/documents.py`
- Modify: `backend/app/worker/pipeline.py`
- Test: `backend/tests/test_processing_flags.py`, `backend/tests/test_schema.py`

**Interfaces:**
- Produces: `Document.summary_enabled: bool`, `Document.translation_enabled: bool`. Request fields `summary_enabled` / `translation_enabled` (bool, default `True`) on upload (form), compile (JSON), reprocess (JSON). `create_pending_document(..., summary_enabled: bool = True, translation_enabled: bool = True)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_processing_flags.py`:

```python
from sqlmodel import select

from app.models import ChunkSource, DocType, Job
from app.worker import pipeline
from tests.test_pipeline import chunks_by_source, make_doc, pipeline_storage, run  # noqa: F401


def _text_doc(session, storage, **kwargs):
    doc = make_doc(session, doc_type=DocType.text, title="Brief", **kwargs)
    rel, _ = storage.store_file(doc.id, ".md", b"Erster Absatz.\n\nZweiter Absatz.")
    doc.file_path = rel
    session.commit()
    return doc


def test_summary_disabled_skips_summary(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage, summary_enabled=False))
    assert llm_stub["describe"] == []
    assert doc.summary is None
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.summary not in by_source
    assert [c.content for c in by_source[ChunkSource.metadata]] == ["Brief"]


def test_translation_disabled_schedules_nothing(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    doc = run(session, _text_doc(session, pipeline_storage, translation_enabled=False))
    assert doc.detected_language == "de"
    assert doc.translation_status is None
    assert session.exec(select(Job).where(Job.type == "translate_document")).all() == []


def test_upload_stores_flags(auth_client, storage):
    body = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.md", b"x", "text/markdown")},
        data={"summary_enabled": "false", "translation_enabled": "false"},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, False)


def test_upload_flags_default_true(auth_client, storage):
    body = auth_client.post(
        "/api/documents/upload", files={"file": ("a.md", b"x", "text/markdown")}
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (True, True)


def test_compile_stores_flags(auth_client, fake_scanner, storage):
    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    body = auth_client.post(
        f"/api/scan/sessions/{sid}/compile",
        json={"title": "T", "summary_enabled": False, "translation_enabled": True},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, True)


def test_reprocess_stores_flags(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Doc", [{"content": "x", "page_number": 1}], doc_type="pdf")
    body = auth_client.post(
        f"/api/documents/{doc.id}/reprocess",
        json={"ocr_languages": "eng", "summary_enabled": False, "translation_enabled": False},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, False)
```

Append to `backend/tests/test_schema.py`:

```python
def test_document_processing_flag_columns(engine):
    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    for name in ("summary_enabled", "translation_enabled"):
        assert cols[name]["nullable"] is False
        assert cols[name]["default"] == "true"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_processing_flags.py tests/test_schema.py -v`
Expected: FAIL (`TypeError: 'summary_enabled' is an invalid keyword argument for Document` or `KeyError: 'summary_enabled'`).

- [ ] **Step 3: Add the model fields and the migration**

In `backend/app/models/document.py`, after `ocr_enabled: bool = True` add:

```python
    summary_enabled: bool = True  # False: the pipeline skips the AI summary
    translation_enabled: bool = True  # False: no translate_document job is scheduled
```

Create `backend/alembic/versions/f2b9d4c61a87_processing_flags.py`:

```python
"""documents.summary_enabled / translation_enabled

Revision ID: f2b9d4c61a87
Revises: e5a1c7d93b20
Create Date: 2026-10-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b9d4c61a87"
down_revision: Union[str, Sequence[str], None] = "e5a1c7d93b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents", sa.Column("summary_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "documents", sa.Column("translation_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "translation_enabled")
    op.drop_column("documents", "summary_enabled")
```

- [ ] **Step 4: Accept the flags in the API**

`backend/app/api/uploads.py`, `create_pending_document`: add keyword parameters `summary_enabled: bool = True, translation_enabled: bool = True` after `ocr_enabled`, and pass `summary_enabled=summary_enabled, translation_enabled=translation_enabled` to `Document(...)`. In `upload_document`, add form parameters after `ocr_enabled`:

```python
    summary_enabled: bool = Form(default=True),
    translation_enabled: bool = Form(default=True),
```

and pass them to `create_pending_document`.

`backend/app/api/scan.py`, `CompileRequest`: add `summary_enabled: bool = True` and `translation_enabled: bool = True`. In `compile_session`, pass `summary_enabled=body.summary_enabled, translation_enabled=body.translation_enabled` to `create_pending_document`.

`backend/app/api/documents.py`, `ReprocessRequest`: add `summary_enabled: bool = True` and `translation_enabled: bool = True`. In `reprocess_document`, next to `doc.ocr_enabled = body.ocr_enabled`:

```python
    doc.summary_enabled = body.summary_enabled
    doc.translation_enabled = body.translation_enabled
```

- [ ] **Step 5: Respect the flags in the pipeline**

In `backend/app/worker/pipeline.py`:

In `_ensure_summary`, change the first line to:

```python
    if doc.doc_type == DocType.video or doc.summary is not None or not doc.summary_enabled:
        return
```

In `_needs_translation`, add as the first line:

```python
    if not doc.translation_enabled:
        return False
```

`_ensure_metadata_chunk` already builds `content = doc.title` when there is no description, so no change is needed there. `test_summary_disabled_skips_summary` pins it.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_processing_flags.py tests/test_schema.py tests/test_pipeline.py tests/test_uploads.py tests/test_scan_compile.py tests/test_reprocess.py tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/document.py backend/alembic/versions/f2b9d4c61a87_processing_flags.py backend/app/api/uploads.py backend/app/api/scan.py backend/app/api/documents.py backend/app/worker/pipeline.py backend/tests/test_processing_flags.py backend/tests/test_schema.py
git commit -m "feat: per-document switches for AI summary and translation"
```

---

## Task 3: Local language detection; summary-only `describe()`

**Files:**
- Modify: `backend/pyproject.toml` (via `uv add`)
- Create: `backend/app/services/language.py`
- Modify: `backend/app/services/llm.py`
- Modify: `backend/app/worker/pipeline.py`
- Modify: `backend/tests/conftest.py`, `backend/tests/test_llm.py`, `backend/tests/test_pipeline.py`
- Test: `backend/tests/test_language.py`

**Interfaces:**
- Produces: `app.services.language.detect_language(text: str | None) -> str | None` (ISO 639-1 lowercase). `app.services.llm.describe(text=None, image_path=None) -> str` (summary text, `""` when empty). `app.services.llm.parse_summary(raw: str | None) -> str`. In the pipeline it is imported as `detect_language` (the `llm_stub` fixture patches `app.worker.pipeline.detect_language`).
- Removes: `llm.Description`, `llm.parse_description`, `llm._normalize_language`.

- [ ] **Step 1: Add the dependency**

Run: `cd backend && uv add lingua-language-detector`
Expected: `pyproject.toml` lists `lingua-language-detector>=2...` and `uv.lock` updates.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_language.py`:

```python
from app.services.language import detect_language


def test_detects_italian_english_german():
    assert detect_language("Gentile cliente, in allegato trova la fattura del mese di marzo.") == "it"
    assert detect_language("Dear customer, please find attached the invoice for March.") == "en"
    assert detect_language("Sehr geehrte Damen und Herren, anbei die Rechnung für März.") == "de"


def test_empty_or_tiny_text_is_none():
    assert detect_language("") is None
    assert detect_language(None) is None
    assert detect_language("   \n ") is None
    assert detect_language("12 34 / 56") is None


def test_only_looks_at_the_first_5000_chars():
    text = "Gentile cliente, ecco la fattura del mese. " * 200 + "Dear customer " * 2000
    assert detect_language(text) == "it"
```

In `backend/tests/test_llm.py`, replace the describe/parse tests so they expect a plain string:

```python
def test_describe_text(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        litellm, "completion", _completion_returning('{"summary": " Una fattura del 2026. "}', captured)
    )
    assert llm.describe(text="FATTURA n. 42 del 2026...") == "Una fattura del 2026."
    assert "FATTURA n. 42" in captured["content"]
    assert "Italian" in captured["content"]  # summary requested in the primary language
    assert '"language"' not in captured["content"]  # detection is local now
    assert captured["model"] == "gemini/gemini-2.5-flash"


def test_describe_image(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}
    monkeypatch.setattr(litellm, "completion", _completion_returning('{"summary": "Uno scontrino."}', captured))
    assert llm.describe(image_path=img) == "Uno scontrino."
    kinds = [p["type"] for p in captured["content"]]
    assert kinds == ["text", "image_url"]
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_parse_summary_handles_fenced_json():
    assert llm.parse_summary('```json\n{"summary": "Contratto di affitto."}\n```') == "Contratto di affitto."


def test_parse_summary_falls_back_to_raw_text():
    assert llm.parse_summary("  Just prose, no JSON.  ") == "Just prose, no JSON."
    assert llm.parse_summary('{"summary": ""}') == '{"summary": ""}'


def test_parse_summary_rejects_null_summary():
    raw = '{"summary": null}'
    assert llm.parse_summary(raw) == raw


def test_parse_summary_extracts_json_from_prose():
    assert llm.parse_summary('Here:\n{"summary": "Una fattura."}\nHope it helps!') == "Una fattura."


def test_parse_summary_none_content():
    assert llm.parse_summary(None) == ""
```

Delete these old tests from `test_llm.py`: `test_parse_description_handles_fenced_json`, `test_parse_description_falls_back_to_raw_text`, `test_parse_description_rejects_null_summary`, `test_parse_description_unknown_language_is_none`, `test_parse_description_normalizes_region_language`, `test_parse_description_extracts_json_from_prose`, `test_parse_description_none_content`. Search the file for any other `Description(` or `.language` uses (for example in the vision credential tests around lines 227-270) and change their fake replies to `'{"summary": "..."}'` and their assertions to compare against the plain string.

In `backend/tests/conftest.py`, inside `llm_stub`, replace the `Description` import and `fake_describe`, and add a `detect_language` fake:

```python
    def fake_describe(text=None, image_path=None):
        calls["describe"].append({"text": text, "image_path": image_path})
        return "Descrizione generata."

    def fake_detect_language(text):
        calls["detect"].append(text)
        return calls["language"] if text and text.strip() else None
```

Add `"detect": []` to the `calls` dict. Next to the other `monkeypatch.setattr` lines add:

```python
    monkeypatch.setattr("app.worker.pipeline.detect_language", fake_detect_language)
```

Delete the line `from app.services.llm import Description`. Update the docstring: `calls["language"]` is now what language detection returns for non-empty text.

In `backend/tests/test_pipeline.py`, change the comment in `test_unknown_language_skips_translation` from `# e.g. describe() got non-JSON and fell back to raw text` to `# language detection was not confident`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_language.py tests/test_llm.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.services.language'`, `AttributeError: ... 'parse_summary'`).

- [ ] **Step 4: Implement `detect_language`**

Create `backend/app/services/language.py`:

```python
"""Local (no-LLM) language detection for extracted document text."""

from functools import lru_cache

from lingua import Language, LanguageDetector, LanguageDetectorBuilder

DETECT_INPUT_CHARS = 5000
MIN_LETTERS = 20  # below this, detection is noise

# Languages a personal archive in Europe realistically holds; a closed set keeps memory small
# and accuracy high. Extend when needed.
SUPPORTED = (
    Language.ITALIAN, Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.SPANISH,
    Language.PORTUGUESE, Language.DUTCH, Language.POLISH, Language.ROMANIAN, Language.CROATIAN,
    Language.SLOVENE, Language.CZECH, Language.SWEDISH, Language.DANISH, Language.BOKMAL,
    Language.FINNISH, Language.HUNGARIAN, Language.GREEK, Language.RUSSIAN, Language.UKRAINIAN,
    Language.TURKISH, Language.ARABIC, Language.CHINESE, Language.JAPANESE,
)


@lru_cache
def _detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_languages(*SUPPORTED).with_minimum_relative_distance(0.1).build()


def detect_language(text: str | None) -> str | None:
    """ISO 639-1 code of the text's language, or None when empty or not confident."""
    sample = (text or "")[:DETECT_INPUT_CHARS]
    if sum(ch.isalpha() for ch in sample) < MIN_LETTERS:
        return None
    language = _detector().detect_language_of(sample)
    return language.iso_code_639_1.name.lower() if language is not None else None
```

If `Language.BOKMAL` or `Language.SLOVENE` fail to import in the installed lingua version, run `uv run python -c "from lingua import Language; print([l.name for l in Language.all()])"` and use the names it prints.

- [ ] **Step 5: Make `describe()` summary-only**

In `backend/app/services/llm.py`:

Replace `class Description(NamedTuple)`, `_describe_prompt`, `parse_description` and `_normalize_language` with:

```python
def _describe_prompt(target_language: str) -> str:
    return (
        "You are indexing a document for a searchable personal archive. "
        'Reply with ONLY a JSON object: {"summary": "..."}. '
        f'"summary": 2-4 sentences written in {language_name(target_language)} describing '
        "what the document is, its purpose, and key entities (dates, amounts, names, "
        "organizations)."
    )


def parse_summary(raw: str | None) -> str:
    """Summary from the describe() JSON reply; falls back to the raw text."""
    raw = raw or ""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        text = text[first : last + 1]  # tolerate prose around the JSON object
    try:
        summary = json.loads(text)["summary"]
    except (ValueError, KeyError, TypeError):
        return raw.strip()
    if not isinstance(summary, str) or not summary.strip():
        return raw.strip()
    return summary.strip()
```

Change the signature of `describe` to `-> str` and its last line to `return parse_summary(resp.choices[0].message.content)`. Remove `NamedTuple` from the `typing` import and remove `re` from the imports only if nothing else in the file uses it (`parse_document_ids` uses `re`, so keep it).

- [ ] **Step 6: Detect the language in the pipeline**

In `backend/app/worker/pipeline.py`:

Add the import `from app.services.language import detect_language`.

Add this function after `_content_chunks`:

```python
def _ensure_language(session: Session, doc: Document) -> None:
    """Local detection on the extracted text; translation depends on this, not on the summary."""
    if doc.detected_language is not None:
        return
    text = "\n\n".join(c.content for c in _content_chunks(session, doc))
    language = detect_language(text)
    if language is not None:
        doc.detected_language = language
        session.commit()
```

In `process_document`, call it right after `_ensure_content_chunks(session, doc, pages)`:

```python
        _ensure_language(session, doc)
```

In `_ensure_summary`: rename `result` to `summary` throughout. Replace `if not result.summary:` with `if not summary:`, `doc.summary = result.summary` with `doc.summary = summary`, delete the line `doc.detected_language = result.language`, and change `content=result.summary` and `.values(description=result.summary)` to use `summary`.

- [ ] **Step 7: Run the whole backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS. If a pipeline test that sends an image to vision (no extracted text) asserts a `detected_language`, update it to expect `None`. That is the spec's intended behaviour (spec 2.3: images processed without OCR have no text, so no language).

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/services/language.py backend/app/services/llm.py backend/app/worker/pipeline.py backend/tests/conftest.py backend/tests/test_llm.py backend/tests/test_pipeline.py backend/tests/test_language.py
git commit -m "feat: detect document language locally with lingua instead of the summary call"
```

---

## Task 4: Browse API — root filter, date range, folder document counts, `translatable`

**Files:**
- Modify: `backend/app/api/documents.py`
- Modify: `backend/app/api/folders.py`
- Test: `backend/tests/test_browse_api.py`

**Interfaces:**
- Produces: `GET /api/documents?folder_id=root|<int>&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`. `GET /api/folders` items gain `document_count: int`. Serialized documents gain `translatable: bool` (`detected_language` set and different from `PRIMARY_LANGUAGE`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_browse_api.py`:

```python
from datetime import date

from app.models import Document, DocStatus, DocType


def make(session, title, **kwargs):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, **kwargs)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def titles(resp):
    assert resp.status_code == 200, resp.text
    return sorted(d["title"] for d in resp.json())


def test_root_filter_lists_only_documents_without_folder(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    make(session, "Loose")
    make(session, "Filed", folder_id=folder_id)
    assert titles(auth_client.get("/api/documents", params={"folder_id": "root"})) == ["Loose"]
    assert titles(auth_client.get("/api/documents", params={"folder_id": folder_id})) == ["Filed"]
    assert titles(auth_client.get("/api/documents")) == ["Filed", "Loose"]


def test_bad_folder_id_is_422(auth_client):
    assert auth_client.get("/api/documents", params={"folder_id": "abc"}).status_code == 422


def test_date_range_is_inclusive(auth_client, session):
    make(session, "Jan", document_date=date(2026, 1, 31))
    make(session, "Feb", document_date=date(2026, 2, 1))
    make(session, "Mar", document_date=date(2026, 3, 1))
    get = lambda **p: titles(auth_client.get("/api/documents", params=p))  # noqa: E731
    assert get(date_from="2026-02-01") == ["Feb", "Mar"]
    assert get(date_to="2026-02-01") == ["Feb", "Jan"]
    assert get(date_from="2026-02-01", date_to="2026-02-01") == ["Feb"]


def test_inverted_date_range_is_422(auth_client):
    resp = auth_client.get("/api/documents", params={"date_from": "2026-03-01", "date_to": "2026-02-01"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_date_range"


def test_folders_include_direct_document_counts(auth_client, session):
    parent = auth_client.post("/api/folders", json={"name": "P"}).json()["id"]
    child = auth_client.post("/api/folders", json={"name": "C", "parent_id": parent}).json()["id"]
    make(session, "a", folder_id=parent)
    make(session, "b", folder_id=child)
    make(session, "c", folder_id=child)
    counts = {f["name"]: f["document_count"] for f in auth_client.get("/api/folders").json()}
    assert counts == {"P": 1, "C": 2}


def test_translatable_flag(auth_client, session):
    german = make(session, "de", detected_language="de")
    italian = make(session, "it", detected_language="it")
    unknown = make(session, "none")
    flags = {d["title"]: d["translatable"] for d in auth_client.get("/api/documents").json()}
    assert flags == {"de": True, "it": False, "none": False}
    assert auth_client.get(f"/api/documents/{german.id}").json()["translatable"] is True
    assert italian and unknown
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_browse_api.py -v`
Expected: FAIL (`folder_id=root` gives 422 from int parsing, `KeyError: 'document_count'`, `KeyError: 'translatable'`).

- [ ] **Step 3: Implement the filters**

In `backend/app/api/documents.py`, replace the `list_documents` signature and the folder filter:

```python
@router.get("")
def list_documents(
    folder_id: str | None = None,
    tag_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: DocumentSort = "date_desc",
    session: Session = Depends(get_session),
) -> list[dict]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise api_error(422, "invalid_date_range", "date_from must not be after date_to")
    query = select(Document)
    if folder_id == "root":
        query = query.where(Document.folder_id.is_(None))
    elif folder_id is not None:
        if not folder_id.isdigit():
            raise api_error(422, "validation_error", "folder_id must be an integer or 'root'")
        query = query.where(Document.folder_id == int(folder_id))
    if date_from is not None:
        query = query.where(Document.document_date >= date_from)
    if date_to is not None:
        query = query.where(Document.document_date <= date_to)
```

The `doc_type`, `status`, `tag_id`, sort and return lines stay unchanged.

In `serialize`, add the `translatable` key:

```python
    return {
        **doc.model_dump(),
        "translatable": bool(doc.detected_language) and doc.detected_language != get_primary_language(),
        "tags": [t.model_dump() for t in doc_tags(session, doc)],
        "active_job": active_jobs.get(str(doc.id)),
    }
```

- [ ] **Step 4: Add folder counts**

In `backend/app/api/folders.py`, add `from sqlalchemy import func` and replace `list_folders`:

```python
@router.get("")
def list_folders(session: Session = Depends(get_session)) -> list[dict]:
    counts = dict(
        session.exec(
            select(Document.folder_id, func.count())
            .where(Document.folder_id.is_not(None))
            .group_by(Document.folder_id)
        ).all()
    )
    return [{**f.model_dump(), "document_count": counts.get(f.id, 0)} for f in session.exec(select(Folder))]
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/test_browse_api.py tests/test_documents.py tests/test_folders.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/documents.py backend/app/api/folders.py backend/tests/test_browse_api.py
git commit -m "feat: root and date-range document filters, folder document counts"
```

---

## Task 5: Search date filters (backend)

**Files:**
- Modify: `backend/app/services/search.py`
- Test: `backend/tests/test_search_core.py`

**Interfaces:**
- Produces: `SearchFilters.date_from: date | None`, `SearchFilters.date_to: date | None` (inclusive, on `document_date`). Inverted range raises a pydantic validation error, so `POST /api/search` returns 422.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_search_core.py`:

```python
def test_allowed_document_ids_date_range(session):
    from datetime import date

    from app.services.search import SearchFilters, allowed_document_ids
    from tests.helpers import seed_document

    early = seed_document(session, "early", [], document_date=date(2026, 1, 10))
    late = seed_document(session, "late", [], document_date=date(2026, 5, 10))
    ids = allowed_document_ids(session, SearchFilters(date_from=date(2026, 2, 1)))
    assert ids == [late.id]
    ids = allowed_document_ids(session, SearchFilters(date_to=date(2026, 1, 10)))
    assert ids == [early.id]


def test_search_filters_reject_inverted_range():
    from datetime import date

    import pytest
    from pydantic import ValidationError

    from app.services.search import SearchFilters

    with pytest.raises(ValidationError):
        SearchFilters(date_from=date(2026, 3, 1), date_to=date(2026, 2, 1))
```

Append to `backend/tests/test_search_api.py`:

```python
def test_search_inverted_date_range_is_422(auth_client):
    resp = auth_client.post(
        "/api/search",
        json={"query": "x", "filters": {"date_from": "2026-03-01", "date_to": "2026-02-01"}},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_search_core.py tests/test_search_api.py -v`
Expected: FAIL (date filters are ignored, and the inverted range is accepted).

- [ ] **Step 3: Implement**

In `backend/app/services/search.py`: add `from datetime import date` and `from pydantic import BaseModel, model_validator`. Replace `SearchFilters` and the head of `allowed_document_ids`:

```python
class SearchFilters(BaseModel):
    folder_id: int | None = None
    tag_ids: list[int] = []
    doc_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _range_in_order(self) -> "SearchFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self
```

```python
def allowed_document_ids(
    session: Session, filters: SearchFilters
) -> list[uuid.UUID] | None:
    """None = unfiltered; [] = filters exclude everything."""
    if (
        filters.folder_id is None
        and not filters.tag_ids
        and filters.doc_type is None
        and filters.date_from is None
        and filters.date_to is None
    ):
        return None
    query = select(Document.id)
    if filters.date_from is not None:
        query = query.where(Document.document_date >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(Document.document_date <= filters.date_to)
```

Keep the rest of the function (folder, doc_type and tag conditions) as it is.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_search_core.py tests/test_search_api.py tests/test_rag.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/search.py backend/tests/test_search_core.py backend/tests/test_search_api.py
git commit -m "feat: date range filter for search"
```

---

## Task 6: Bulk move and bulk delete endpoints

**Files:**
- Modify: `backend/app/api/documents.py`
- Test: `backend/tests/test_bulk.py`

**Interfaces:**
- Produces: `POST /api/documents/bulk/move` with body `{ids: [uuid], folder_id: int | null}` returns `{moved: int, missing: [str]}`. `POST /api/documents/bulk/delete` with body `{ids: [uuid]}` returns `{deleted: int, missing: [str]}`. 1–500 ids, otherwise 422.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_bulk.py`:

```python
import uuid

from sqlmodel import select

from app.models import Document, DocStatus, DocType, Job, JobStatus


def make(session, title="d", **kwargs):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, **kwargs)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_bulk_move(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    a, b = make(session), make(session)
    ghost = str(uuid.uuid4())
    body = auth_client.post(
        "/api/documents/bulk/move", json={"ids": [str(a.id), str(b.id), ghost], "folder_id": folder_id}
    ).json()
    assert body == {"moved": 2, "missing": [ghost]}
    session.expire_all()
    assert {session.get(Document, a.id).folder_id, session.get(Document, b.id).folder_id} == {folder_id}


def test_bulk_move_to_root(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    a = make(session, folder_id=folder_id)
    auth_client.post("/api/documents/bulk/move", json={"ids": [str(a.id)], "folder_id": None})
    session.expire_all()
    assert session.get(Document, a.id).folder_id is None


def test_bulk_move_missing_folder_changes_nothing(auth_client, session):
    a = make(session)
    resp = auth_client.post("/api/documents/bulk/move", json={"ids": [str(a.id)], "folder_id": 999})
    assert resp.status_code == 404
    session.expire_all()
    assert session.get(Document, a.id).folder_id is None


def test_bulk_delete_removes_rows_files_and_queued_jobs(auth_client, session, storage):
    a, b = make(session), make(session)
    rel, _ = storage.store_file(a.id, ".pdf", b"%PDF")
    a.file_path = rel
    session.add(Job(type="process_document", payload={"document_id": str(a.id)}))
    session.commit()
    ghost = str(uuid.uuid4())
    body = auth_client.post("/api/documents/bulk/delete", json={"ids": [str(a.id), str(b.id), ghost]}).json()
    assert body == {"deleted": 2, "missing": [ghost]}
    session.expire_all()
    assert session.exec(select(Document)).all() == []
    assert not storage.abs_path(rel).exists()
    assert session.exec(select(Job)).one().status == JobStatus.cancelled


def test_bulk_limits(auth_client):
    assert auth_client.post("/api/documents/bulk/delete", json={"ids": []}).status_code == 422
    ids = [str(uuid.uuid4()) for _ in range(501)]
    assert auth_client.post("/api/documents/bulk/delete", json={"ids": ids}).status_code == 422
    assert auth_client.post("/api/documents/bulk/move", json={"ids": ids, "folder_id": None}).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_bulk.py -v`
Expected: FAIL (405 or 422: the route does not exist).

- [ ] **Step 3: Implement**

In `backend/app/api/documents.py`, change `from pydantic import BaseModel` to `from pydantic import BaseModel, Field`. Add the models and a shared delete helper, and refactor `delete_document` to use the helper:

```python
BULK_MAX = 500


class BulkIds(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_MAX)


class BulkMove(BulkIds):
    folder_id: int | None


def _found_and_missing(session: Session, ids: list[uuid.UUID]) -> tuple[list[Document], list[str]]:
    docs = list(session.exec(select(Document).where(Document.id.in_(ids))))
    found = {d.id for d in docs}
    return docs, [str(i) for i in ids if i not in found]


def _delete_documents(session: Session, docs: list[Document]) -> list[str | None]:
    """Cancel queued jobs and delete rows (no commit); returns the file paths to remove after commit."""
    rel_paths: list[str | None] = []
    for doc in docs:
        rel_paths += [doc.file_path, doc.preview_path]
        _cancel_queued_jobs(session, doc)
        session.delete(doc)  # chunks, document_tags and translation segments cascade via FK
    return rel_paths


@router.post("/bulk/move")
def bulk_move(body: BulkMove, session: Session = Depends(get_session)) -> dict:
    if body.folder_id is not None and session.get(Folder, body.folder_id) is None:
        raise api_error(404, "not_found", "Folder not found")
    docs, missing = _found_and_missing(session, body.ids)
    now = datetime.now(timezone.utc)
    for doc in docs:
        doc.folder_id = body.folder_id
        doc.updated_at = now
    session.commit()
    return {"moved": len(docs), "missing": missing}


@router.post("/bulk/delete")
def bulk_delete(
    body: BulkIds,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    docs, missing = _found_and_missing(session, body.ids)
    rel_paths = _delete_documents(session, docs)
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)
    return {"deleted": len(docs), "missing": missing}
```

Replace the body of `delete_document` with:

```python
    doc = get_doc_or_404(session, document_id)
    rel_paths = _delete_documents(session, [doc])
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)
```

Place both `bulk_*` routes **above** `@router.get("/{document_id}")`, so the route order is clear when the file is read top to bottom. They are POST routes and do not clash with the GET routes either way. `storage.delete_document_file(None)` is already called today for a missing `preview_path`, so it accepts `None`.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_bulk.py tests/test_documents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/documents.py backend/tests/test_bulk.py
git commit -m "feat: bulk move and bulk delete endpoints"
```

---

## Task 7: Page reconstruction and segmenting helpers

**Files:**
- Modify: `backend/app/services/chunking.py`
- Test: `backend/tests/test_chunking.py`

**Interfaces:**
- Produces: `page_texts_from_chunks(chunks: list[tuple[int | None, str]], overlap: int = 200) -> list[tuple[int | None, str]]` (inverse of `chunk_pages`). `segment_pages(pages: list[tuple[int | None, str]], max_chars: int) -> list[tuple[int | None, str]]` (no overlap, empty pages dropped).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_chunking.py`:

```python
from app.services.chunking import chunk_pages, page_texts_from_chunks, segment_pages


def _roundtrip(pages, size=100, overlap=20):
    chunks = [(c["page_number"], c["content"]) for c in chunk_pages(pages, size=size, overlap=overlap)]
    return page_texts_from_chunks(chunks, overlap=overlap)


def test_page_texts_roundtrip_paragraphs():
    paragraphs = [f"Paragraph number {i} has some words in it." for i in range(12)]
    page = "\n\n".join(paragraphs)
    assert _roundtrip([(1, page), (2, "Short page.")]) == [(1, page), (2, "Short page.")]


def test_page_texts_roundtrip_hard_split_paragraph():
    long_paragraph = "".join(f"w{i:03d} " for i in range(90)).strip()  # ~450 chars, no blank lines
    page = f"Intro line.\n\n{long_paragraph}\n\nOutro line."
    assert _roundtrip([(3, page)]) == [(3, page)]


def test_page_texts_roundtrip_overlap_starting_with_space():
    # the overlap tail of a chunk can begin with whitespace, which chunk_pages strips
    page = "\n\n".join("a" * 79 + " " + "b" * 15 for _ in range(6))
    assert _roundtrip([(1, page)]) == [(1, page)]


def test_page_texts_keeps_chunk_when_overlap_does_not_match():
    assert page_texts_from_chunks([(1, "first"), (1, "unrelated")]) == [(1, "first\n\nunrelated")]


def test_page_texts_none_page_numbers_group_together():
    assert page_texts_from_chunks([(None, "a"), (None, "b")], overlap=0) == [(None, "a\n\nb")]


def test_segment_pages_splits_long_pages_without_overlap():
    page = "\n\n".join(["x" * 40] * 5)  # 5 paragraphs of 40 chars
    segments = segment_pages([(1, page), (2, "   "), (3, "short")], max_chars=100)
    assert segments == [
        (1, "x" * 40 + "\n\n" + "x" * 40),
        (1, "x" * 40 + "\n\n" + "x" * 40),
        (1, "x" * 40),
        (3, "short"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: FAIL (`ImportError: cannot import name 'page_texts_from_chunks'`).

- [ ] **Step 3: Implement**

Append to `backend/app/services/chunking.py`:

```python
def page_texts_from_chunks(
    chunks: list[tuple[int | None, str]], overlap: int = 200
) -> list[tuple[int | None, str]]:
    """Rebuild page texts from chunk_pages() output by removing the overlap between chunks.

    When a chunk does not start with the previous chunk's tail, it is kept whole after a
    paragraph break: duplicating a little text is better than losing any.
    """
    pages: list[list] = []  # [page_number, text, previous chunk]
    for page_number, content in chunks:
        if not pages or pages[-1][0] != page_number:
            pages.append([page_number, content, content])
            continue
        entry = pages[-1]
        tail = entry[2][-overlap:] if overlap else ""
        for candidate in (tail, tail.lstrip()):
            if candidate and content.startswith(candidate):
                entry[1] += content[len(candidate):]
                break
        else:
            entry[1] += "\n\n" + content
        entry[2] = content
    return [(page_number, text) for page_number, text, _ in pages]


def segment_pages(
    pages: list[tuple[int | None, str]], max_chars: int
) -> list[tuple[int | None, str]]:
    """Translation units: one per page, long pages split on paragraphs, no overlap."""
    return [
        (page_number, piece)
        for page_number, text in pages
        for piece in _split_text(text, max_chars, 0)
    ]
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_chunking.py -v`
Expected: PASS. If `test_page_texts_roundtrip_overlap_starting_with_space` fails, print `chunk_pages(...)` for that input and check which prefix the next chunk starts with. The candidates loop must cover it, and the fix belongs in `page_texts_from_chunks`, not in `chunk_pages`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/chunking.py backend/tests/test_chunking.py
git commit -m "feat: rebuild page texts from overlapping chunks and split them into segments"
```

---

## Task 8: Resumable page-based translation (`translation_segments`)

**Files:**
- Create: `backend/app/models/translation.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/a6c3e8f15d29_translation_segments.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/worker/pipeline.py`
- Modify: `backend/app/api/documents.py` (reprocess deletes segments)
- Test: `backend/tests/test_translation_segments.py`, `backend/tests/test_schema.py`

**Interfaces:**
- Consumes: `page_texts_from_chunks`, `segment_pages` (Task 7). `llm_translate(text, target) -> str` (the same name in the pipeline).
- Produces: `TranslationSegment(document_id, segment_index, page_number, source_hash, text)`. Settings `translation_segment_chars: int = 6000` and `llm_tpm_limit: int = 0`. `pipeline.translation_segment_chars() -> int`. `pipeline.delete_translation_segments(session, doc_id) -> None` (no commit).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_translation_segments.py`:

```python
import pytest
from sqlmodel import select

from app.models import ChunkSource, TranslationSegment
from app.worker import pipeline
from tests.helpers import seed_document
from tests.test_pipeline import chunks_by_source, pipeline_storage  # noqa: F401


def _german(session, pages=3):
    specs = [{"content": f"Seite {n} Text.", "page_number": n} for n in range(1, pages + 1)]
    return seed_document(
        session, "Brief", specs, detected_language="de", translation_status="pending"
    )


def translate(session, doc, **extra):
    pipeline.translate_document(session, {"document_id": str(doc.id), **extra})
    session.refresh(doc)
    return doc


def test_one_call_per_page_and_chunks_per_page(session, pipeline_storage, llm_stub):
    doc = translate(session, _german(session))
    assert doc.translation_status == "done"
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 1 Text.", "Seite 2 Text.", "Seite 3 Text."]
    translated = sorted(chunks_by_source(session, doc)[ChunkSource.translation], key=lambda c: c.chunk_index)
    assert [(c.page_number, c.content) for c in translated] == [
        (1, "[it] Seite 1 Text."), (2, "[it] Seite 2 Text."), (3, "[it] Seite 3 Text."),
    ]
    assert session.exec(select(TranslationSegment)).all() == []  # cleaned up when done


def test_overlap_is_not_translated_twice(session, pipeline_storage, llm_stub):
    first = "A" * 900
    second = first[-200:] + "\n\nB" * 1
    doc = seed_document(
        session, "Brief",
        [{"content": first, "page_number": 1}, {"content": second, "page_number": 1}],
        detected_language="de", translation_status="pending",
    )
    translate(session, doc)
    assert [t for t, _ in llm_stub["translate"]] == [first + "\n\nB"]


def test_failure_keeps_finished_segments_and_retry_resumes(session, pipeline_storage, llm_stub, monkeypatch):
    doc = _german(session)
    real = pipeline.llm_translate
    def flaky(text, target):
        if text.startswith("Seite 3"):
            raise RuntimeError("429")
        return real(text, target)
    monkeypatch.setattr(pipeline, "llm_translate", flaky)
    with pytest.raises(RuntimeError):
        translate(session, doc, _final_attempt=False)
    stored = session.exec(select(TranslationSegment).order_by(TranslationSegment.segment_index)).all()
    assert [s.page_number for s in stored] == [1, 2]

    monkeypatch.setattr(pipeline, "llm_translate", real)
    llm_stub["translate"].clear()
    doc = translate(session, doc)
    assert doc.translation_status == "done"
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 3 Text."]  # pages 1-2 reused


def test_stale_segment_is_translated_again(session, pipeline_storage, llm_stub):
    doc = _german(session, pages=1)
    session.add(TranslationSegment(
        document_id=doc.id, segment_index=0, page_number=1, source_hash="old", text="stale",
    ))
    session.commit()
    doc = translate(session, doc)
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 1 Text."]
    assert chunks_by_source(session, doc)[ChunkSource.translation][0].content == "[it] Seite 1 Text."


def test_segment_size_respects_tpm_cap(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRANSLATION_SEGMENT_CHARS", "6000")
    monkeypatch.setenv("LLM_TPM_LIMIT", "2000")
    get_settings.cache_clear()
    try:
        assert pipeline.translation_segment_chars() == 2800  # 2000 * 0.4 * 3.5
    finally:
        get_settings.cache_clear()


def test_reprocess_deletes_segments(auth_client, session):
    doc = _german(session, pages=1)
    doc.translation_status = "failed"
    doc.doc_type = "pdf"
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "eng"}).status_code == 200
    assert session.exec(select(TranslationSegment)).all() == []


def test_document_delete_cascades_segments(auth_client, session, storage):
    doc = _german(session, pages=1)
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert session.exec(select(TranslationSegment)).all() == []
```

Append to `backend/tests/test_schema.py`:

```python
def test_translation_segments_table(engine):
    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("translation_segments")}
    assert {"document_id", "segment_index", "page_number", "source_hash", "text"} <= cols
    fks = inspector.get_foreign_keys("translation_segments")
    assert fks[0]["referred_table"] == "documents"
    assert fks[0]["options"].get("ondelete") == "CASCADE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_translation_segments.py tests/test_schema.py -v`
Expected: FAIL (`ImportError: cannot import name 'TranslationSegment'`).

- [ ] **Step 3: Add the model, migration and settings**

Create `backend/app/models/translation.py`:

```python
import uuid

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class TranslationSegment(SQLModel, table=True):
    """A translated source segment (page or part of a page), kept until the translation completes."""

    __tablename__ = "translation_segments"
    __table_args__ = (UniqueConstraint("document_id", "segment_index"),)

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    segment_index: int
    page_number: int | None = None
    source_hash: str  # sha256 hex of the source text; a mismatch means the source changed
    text: str
```

In `backend/app/models/__init__.py`, add `from app.models.translation import TranslationSegment` and add `"TranslationSegment"` to `__all__`.

Create `backend/alembic/versions/a6c3e8f15d29_translation_segments.py`:

```python
"""translation_segments for resumable page-based translation

Revision ID: a6c3e8f15d29
Revises: f2b9d4c61a87
Create Date: 2026-10-09 10:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6c3e8f15d29"
down_revision: Union[str, Sequence[str], None] = "f2b9d4c61a87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "translation_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("document_id", "segment_index"),
    )
    op.create_index("ix_translation_segments_document_id", "translation_segments", ["document_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_translation_segments_document_id", table_name="translation_segments")
    op.drop_table("translation_segments")
```

In `backend/app/config.py`, in `Settings` after `primary_language`:

```python
    llm_tpm_limit: int = 0  # provider tokens-per-minute limit for translation; 0 = no throttle
    translation_segment_chars: int = 6000  # max source characters per translation call
```

- [ ] **Step 4: Rewrite the translation step in the pipeline**

In `backend/app/worker/pipeline.py`:

Add the imports `import hashlib`, `from sqlalchemy import delete` (next to `func, update`), `TranslationSegment` in the `app.models` import, and `page_texts_from_chunks, segment_pages` in the chunking import.

Add these helpers above `_insert_translation_chunks`:

```python
CHARS_PER_TOKEN = 3.5
SEGMENT_SHARE_OF_TPM = 0.4  # input share of one minute's budget; output roughly doubles it


def translation_segment_chars() -> int:
    settings = get_settings()
    limit = settings.translation_segment_chars
    if settings.llm_tpm_limit > 0:
        limit = min(limit, int(settings.llm_tpm_limit * SEGMENT_SHARE_OF_TPM * CHARS_PER_TOKEN))
    return max(limit, 200)


def _source_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def delete_translation_segments(session: Session, doc_id) -> None:
    """Drop saved translation progress (no commit)."""
    session.exec(delete(TranslationSegment).where(TranslationSegment.document_id == doc_id))


def _translate_segments(
    session: Session, doc: Document, content_chunks: list[Chunk]
) -> list[tuple[int | None, str]]:
    """Translate page segments, committing each one so a retry resumes where this run stopped."""
    target = get_primary_language()
    pages = page_texts_from_chunks([(c.page_number, c.content) for c in content_chunks])
    stored = {
        s.segment_index: s
        for s in session.exec(select(TranslationSegment).where(TranslationSegment.document_id == doc.id))
    }
    translated: list[tuple[int | None, str]] = []
    for index, (page_number, source) in enumerate(segment_pages(pages, translation_segment_chars())):
        digest = _source_hash(source)
        row = stored.get(index)
        if row is None or row.source_hash != digest:
            text = llm_translate(source, target)
            row = row or TranslationSegment(document_id=doc.id, segment_index=index, source_hash=digest, text="")
            row.page_number, row.source_hash, row.text = page_number, digest, text
            session.add(row)
            session.commit()
        translated.append((row.page_number, row.text))
    return translated


def _merge_pages(segments: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    merged: list[tuple[int | None, str]] = []
    for page_number, text in segments:
        if merged and merged[-1][0] == page_number:
            merged[-1] = (page_number, f"{merged[-1][1]}\n\n{text}")
        else:
            merged.append((page_number, text))
    return merged
```

Replace `_insert_translation_chunks` with:

```python
def _insert_translation_chunks(session: Session, doc: Document) -> bool:
    """Translate page segments (resumable), then insert the translation chunks in one commit.

    Returns False without writing chunks when the document was re-processed meanwhile
    (translation status reset or content chunks replaced): the new run schedules its own job.
    """
    content_chunks = _content_chunks(session, doc)
    source_ids = [c.id for c in content_chunks]
    translated = _merge_pages(_translate_segments(session, doc, content_chunks))
    # row lock (released by the commit below) serializes with reprocess_document, which takes
    # the same lock before touching chunks; never held across the LLM calls above
    session.refresh(doc, with_for_update=True)
    current_ids = [c.id for c in _content_chunks(session, doc)]
    if not source_ids or current_ids != source_ids or doc.translation_status != TranslationStatus.pending:
        session.rollback()  # release the row lock
        log.info("Document %s changed during translation; result discarded", doc.id)
        return False
    next_index = _next_chunk_index(session, doc)
    for offset, chunk in enumerate(chunk_pages(translated)):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index + offset,
                page_number=chunk["page_number"],
                source=ChunkSource.translation,
                content=chunk["content"],
            )
        )
    delete_translation_segments(session, doc.id)
    session.commit()
    return True
```

In `translate_document`, after the `if not doc.detected_language ...` block, add:

```python
    if not doc.translation_enabled:
        if doc.translation_status == TranslationStatus.pending:
            _finish_translation(session, doc, None)
        return
```

- [ ] **Step 5: Reprocess deletes saved progress**

In `backend/app/api/documents.py`, in `reprocess_document`, right after the `for chunk in session.exec(...): session.delete(chunk)` loop, add:

```python
    delete_translation_segments(session, doc.id)
```

with the import `from app.worker.pipeline import delete_translation_segments`. If importing the worker module from the API creates an import cycle, move `delete_translation_segments` to `app/models/translation.py` as a plain function and import it from there in both places.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/test_translation_segments.py tests/test_pipeline.py tests/test_reprocess.py tests/test_schema.py tests/test_migrations.py -v`
Expected: PASS. Existing tests such as `test_translate_document_adds_embedded_translation_chunks` (one short page, so one chunk) and the lock and discard tests must still pass unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/translation.py backend/app/models/__init__.py backend/alembic/versions/a6c3e8f15d29_translation_segments.py backend/app/config.py backend/app/worker/pipeline.py backend/app/api/documents.py backend/tests/test_translation_segments.py backend/tests/test_schema.py
git commit -m "feat: translate page by page and resume from saved segments after a failure"
```

---

## Task 9: Token-budget throttle and 429 retries in `llm.translate`

**Files:**
- Modify: `backend/app/services/llm.py`
- Modify: `README.md`
- Test: `backend/tests/test_throttle.py`

**Interfaces:**
- Consumes: `Settings.llm_tpm_limit` (Task 8).
- Produces: `llm.TokenBudget(limit_per_minute: int, clock: Callable[[], float], sleep: Callable[[float], None])` with `.acquire(tokens: int) -> list | None` (returns the mutable `[timestamp, tokens]` entry, or `None` when unlimited). `llm.rate_limit_wait(exc: Exception) -> float`. Module hooks `llm._clock` and `llm._sleep`. `llm.RATE_LIMIT_RETRIES = 5`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_throttle.py`:

```python
from types import SimpleNamespace

import httpx
import litellm
import pytest

from app.services import llm


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_budget_under_limit_does_not_wait():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(400)
    budget.acquire(500)
    assert t.sleeps == []


def test_budget_over_limit_waits_for_window():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(800)
    t.now = 10.0
    budget.acquire(500)
    assert t.now >= 60.0  # waited until the first entry aged out


def test_budget_request_bigger_than_limit_does_not_hang():
    t = FakeTime()
    budget = llm.TokenBudget(1000, t.clock, t.sleep)
    budget.acquire(5000)  # capped to the limit, empty window: proceeds
    assert t.sleeps == []


def test_unlimited_budget_returns_none():
    t = FakeTime()
    assert llm.TokenBudget(0, t.clock, t.sleep).acquire(10**9) is None


def _rate_limit(headers=None, message="Rate limit reached"):
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "http://x"))
    return litellm.RateLimitError(message=message, llm_provider="groq", model="m", response=response)


def test_rate_limit_wait_from_header_message_or_default():
    assert llm.rate_limit_wait(_rate_limit({"retry-after": "7"})) == 7.0
    assert llm.rate_limit_wait(_rate_limit(message="Please try again in 7.5s.")) == 7.5
    assert llm.rate_limit_wait(_rate_limit(message="Please try again in 1m2.5s.")) == 62.5
    assert llm.rate_limit_wait(_rate_limit()) == 60.0


def _ok(content="Ciao"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(total_tokens=42))


@pytest.fixture
def fake_time(monkeypatch):
    t = FakeTime()
    monkeypatch.setattr(llm, "_clock", t.clock)
    monkeypatch.setattr(llm, "_sleep", t.sleep)
    llm.reset_translate_budget()
    yield t
    llm.reset_translate_budget()


def test_translate_retries_on_rate_limit(monkeypatch, fake_time):
    replies = iter([_rate_limit({"retry-after": "3"}), _ok("Fattura")])

    def completion(**kwargs):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(litellm, "completion", completion)
    assert llm.translate("Rechnung", "it") == "Fattura"
    assert fake_time.sleeps == [3.0]


def test_translate_gives_up_after_retries(monkeypatch, fake_time):
    calls = []

    def completion(**kwargs):
        calls.append(1)
        raise _rate_limit({"retry-after": "1"})

    monkeypatch.setattr(litellm, "completion", completion)
    with pytest.raises(litellm.RateLimitError):
        llm.translate("Rechnung", "it")
    assert len(calls) == llm.RATE_LIMIT_RETRIES + 1


def test_translate_throttles_to_tpm_limit(monkeypatch, fake_time):
    from app.config import get_settings

    monkeypatch.setenv("LLM_TPM_LIMIT", "100")
    get_settings.cache_clear()
    monkeypatch.setattr(litellm, "token_counter", lambda model, text: 40)  # estimate 80 per call
    monkeypatch.setattr(litellm, "completion", lambda **kw: _ok())
    try:
        llm.translate("a", "it")
        llm.translate("b", "it")  # 42 actual + 80 estimate > 100: waits
    finally:
        get_settings.cache_clear()
    assert fake_time.sleeps and fake_time.now >= 60.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_throttle.py -v`
Expected: FAIL (`AttributeError: module 'app.services.llm' has no attribute 'TokenBudget'`).

- [ ] **Step 3: Implement**

In `backend/app/services/llm.py`, add the imports `import math`, `import threading`, `import time`, `from collections import deque` and `from typing import Callable`, then add above `translate`:

```python
WINDOW_SECONDS = 60.0
RATE_LIMIT_RETRIES = 5
DEFAULT_RATE_LIMIT_WAIT = 60.0
CHARS_PER_TOKEN = 3.5

_clock: Callable[[], float] = time.monotonic  # replaced in tests
_sleep: Callable[[float], None] = time.sleep


class TokenBudget:
    """Sliding 60 s token window for one process. limit <= 0 disables throttling."""

    def __init__(self, limit_per_minute: int, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.limit = limit_per_minute
        self._clock, self._sleep = clock, sleep
        self._events: deque[list] = deque()  # [timestamp, tokens]; tokens corrected after the call
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> list | None:
        if self.limit <= 0:
            return None
        tokens = min(tokens, self.limit)  # one oversized call must not wait forever
        while True:
            with self._lock:
                now = self._clock()
                while self._events and now - self._events[0][0] >= WINDOW_SECONDS:
                    self._events.popleft()
                if sum(e[1] for e in self._events) + tokens <= self.limit:
                    entry = [now, tokens]
                    self._events.append(entry)
                    return entry
                wait = self._events[0][0] + WINDOW_SECONDS - now
            self._sleep(max(wait, 0.05))


_translate_budget: TokenBudget | None = None


def reset_translate_budget() -> None:
    global _translate_budget
    _translate_budget = None


def _budget() -> TokenBudget:
    global _translate_budget
    limit = get_settings().llm_tpm_limit
    if _translate_budget is None or _translate_budget.limit != limit:
        _translate_budget = TokenBudget(limit, lambda: _clock(), lambda s: _sleep(s))
    return _translate_budget


def _estimate_tokens(model: str, text: str) -> int:
    """Input tokens times two: the translation is about as long as the source."""
    try:
        tokens = litellm.token_counter(model=model, text=text)
    except Exception:
        tokens = len(text) / CHARS_PER_TOKEN
    return math.ceil(tokens * 2)


def rate_limit_wait(exc: Exception) -> float:
    """Seconds to wait after a 429: retry-after header, then the provider's message, then 60 s."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        pass
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return DEFAULT_RATE_LIMIT_WAIT
```

Replace `translate` with:

```python
def translate(text: str, target_language: str) -> str:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = (
        f"Translate the following text into {language_name(target_language)}. Preserve line "
        "breaks, numbers, names, and dates. Output only the translation, with no comments."
    )
    content = f"{prompt}\n\n---\n\n{text}"
    budget = _budget()
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        entry = budget.acquire(_estimate_tokens(settings.llm_model, content))
        try:
            resp = litellm.completion(
                model=settings.llm_model, messages=[{"role": "user", "content": content}], **kw
            )
        except litellm.RateLimitError as exc:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            wait = rate_limit_wait(exc)
            log.warning("Translation rate-limited; retrying in %.1fs (%d/%d)", wait, attempt + 1, RATE_LIMIT_RETRIES)
            _sleep(wait)
            continue
        actual = getattr(getattr(resp, "usage", None), "total_tokens", None)
        if entry is not None and isinstance(actual, int):
            entry[1] = actual
        return resp.choices[0].message.content.strip()
    raise AssertionError("unreachable")
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_throttle.py tests/test_llm.py -v`
Expected: PASS. The existing `test_translate` builds a fake `completion(model, messages)`, which still matches this call. Its response has no `usage`, so `actual` is `None` and the estimate is kept.

- [ ] **Step 5: Document the setting**

In `README.md`, in the section that documents AI provider environment variables (search for `LLM_API_KEY`), add:

```
- `LLM_TPM_LIMIT` — your provider's tokens-per-minute limit (for example `8000` on a free tier). Translation waits to stay under it and retries 429 responses. `0` (default) disables the throttle. The budget is per worker process.
- `TRANSLATION_SEGMENT_CHARS` — maximum characters sent in one translation call (default `6000`; capped automatically to fit `LLM_TPM_LIMIT`).
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/llm.py backend/tests/test_throttle.py README.md
git commit -m "feat: keep translation under the provider TPM limit and retry 429 responses"
```

---

## Task 10: Re-translate endpoint

**Files:**
- Modify: `backend/app/api/documents.py`
- Test: `backend/tests/test_retranslate.py`

**Interfaces:**
- Consumes: `delete_translation_segments` (Task 8). `serialize` with `translatable` (Task 4).
- Produces: `POST /api/documents/{id}/retranslate` returns the serialized document. 409 codes: `document_busy`, `translation_busy`, `nothing_to_translate`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_retranslate.py`:

```python
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, Job, TranslationSegment
from tests.helpers import seed_document


def _doc(session, **kwargs):
    defaults = dict(detected_language="de", translation_status="done", translation_enabled=False)
    defaults.update(kwargs)
    return seed_document(
        session, "Brief",
        [
            {"content": "Seite eins", "page_number": 1},
            {"content": "old overlap translation 1", "page_number": 1, "source": ChunkSource.translation},
            {"content": "old overlap translation 2", "page_number": 1, "source": ChunkSource.translation},
        ],
        **defaults,
    )


def test_retranslate_resets_translation_and_queues_job(auth_client, session):
    doc = _doc(session)
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    resp = auth_client.post(f"/api/documents/{doc.id}/retranslate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["translation_status"] == "pending"
    assert body["translation_enabled"] is True
    assert body["status"] == "ready"
    sources = [c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))]
    assert sources == [ChunkSource.content]  # legacy translation chunks removed, content kept
    assert session.exec(select(TranslationSegment)).all() == []
    job = session.exec(select(Job)).one()
    assert (job.type, job.payload) == ("translate_document", {"document_id": str(doc.id)})


def _code(resp):
    assert resp.status_code == 409
    return resp.json()["error"]["code"]


def test_retranslate_conflicts(auth_client, session):
    busy = _doc(session, status=DocStatus.processing)
    assert _code(auth_client.post(f"/api/documents/{busy.id}/retranslate")) == "document_busy"
    pending = _doc(session, translation_status="pending")
    assert _code(auth_client.post(f"/api/documents/{pending.id}/retranslate")) == "translation_busy"
    unknown = _doc(session, detected_language=None)
    assert _code(auth_client.post(f"/api/documents/{unknown.id}/retranslate")) == "nothing_to_translate"
    italian = _doc(session, detected_language="it")
    assert _code(auth_client.post(f"/api/documents/{italian.id}/retranslate")) == "nothing_to_translate"
    assert session.exec(select(Job)).all() == []


def test_retranslate_then_worker_translates(auth_client, session, llm_stub):
    from app.worker import pipeline

    doc = _doc(session)
    auth_client.post(f"/api/documents/{doc.id}/retranslate")
    pipeline.translate_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.translation_status == "done"
    contents = [
        c.content
        for c in session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.translation)
        )
    ]
    assert contents == ["[it] Seite eins"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_retranslate.py -v`
Expected: FAIL (404 or 405: the route does not exist).

- [ ] **Step 3: Implement**

In `backend/app/api/documents.py`, add `TranslationStatus` to the `app.models` import and add after `reprocess_document`:

```python
@router.post("/{document_id}/retranslate")
def retranslate_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    doc = get_doc_or_404(session, document_id)
    session.refresh(doc, with_for_update=True)  # same lock as reprocess and translate_document
    if doc.status in (DocStatus.pending, DocStatus.processing):
        raise api_error(409, "document_busy", "Document is still being processed")
    if doc.translation_status == TranslationStatus.pending:
        raise api_error(409, "translation_busy", "A translation is already in progress")
    if not doc.detected_language or doc.detected_language == get_primary_language():
        raise api_error(409, "nothing_to_translate", "The document is already in the primary language")
    _cancel_queued_jobs(session, doc)
    for chunk in session.exec(
        select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.translation)
    ):
        session.delete(chunk)
    delete_translation_segments(session, doc.id)
    doc.translation_enabled = True
    doc.translation_status = TranslationStatus.pending
    doc.updated_at = datetime.now(timezone.utc)
    session.add(
        Job(type="translate_document", payload={"document_id": str(doc.id)}, run_at=datetime.now(timezone.utc))
    )
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)
```

- [ ] **Step 4: Run the whole backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/documents.py backend/tests/test_retranslate.py
git commit -m "feat: re-translate a document without re-processing it"
```

---
## Task 11: Shared frontend types and FolderPicker auto-select

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/components/FolderPicker.tsx`
- Test: `frontend/src/components/FolderPicker.test.tsx`

**Interfaces:**
- Produces (types): `Folder.document_count: number`. `Document.summary_enabled: boolean`, `Document.translation_enabled: boolean`, `Document.translatable: boolean`. `OcrLanguage { code: string; name: string }`. `OcrLanguagesResponse { languages: OcrLanguage[]; default: string }`. `BulkResult { moved?: number; deleted?: number; missing: string[] }`.

- [ ] **Step 1: Update the types**

In `frontend/src/lib/types.ts`:

```ts
export interface Folder {
  id: number;
  name: string;
  parent_id: number | null;
  created_at: string;
  document_count: number;
}
```

In `Document`, after `ocr_enabled: boolean;` add:

```ts
  summary_enabled: boolean;
  translation_enabled: boolean;
  translatable: boolean;
```

Append:

```ts
export interface OcrLanguage {
  code: string;
  name: string;
}

export interface OcrLanguagesResponse {
  languages: OcrLanguage[];
  default: string;
}

export interface BulkResult {
  moved?: number;
  deleted?: number;
  missing: string[];
}
```

Then run `cd frontend && npx tsc -b`. Fix every test fixture that builds a `Folder` literal by adding `document_count: 0` (for example `FolderPicker.test.tsx` and `lib/folderTree.test.ts`).

- [ ] **Step 2: Write the failing test**

In `frontend/src/components/FolderPicker.test.tsx`, add `document_count: 0` to each `FOLDERS` entry and replace the first test with:

```tsx
it("drills into folders with children and closes on a folder without children", async () => {
  const { trigger } = renderPicker();
  expect(trigger).toHaveTextContent("(root)");
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("button", { name: "Bollette" }));
  expect(trigger).toHaveTextContent("Bollette");
  expect(screen.getByRole("dialog", { name: "Choose folder" })).toBeInTheDocument(); // has children: stays open
  await userEvent.click(screen.getByRole("button", { name: "2026" }));
  expect(trigger).toHaveTextContent("Bollette / 2026");
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument(); // leaf: closed
});

it("Back and Done still work while browsing", async () => {
  const { trigger } = renderPicker();
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("button", { name: "Bollette" }));
  await userEvent.click(screen.getByRole("button", { name: /back/i }));
  expect(screen.getByRole("button", { name: "Assicurazioni" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/FolderPicker.test.tsx`
Expected: FAIL (the dialog is still open after clicking "2026").

- [ ] **Step 4: Implement**

In `frontend/src/components/FolderPicker.tsx`, replace the folder item `onClick`:

```tsx
                  onClick={() => {
                    onChange(f.id);
                    if (childrenOf(folders, f.id).length > 0) setLevel(f.id);
                    else setOpen(false); // nothing below: the choice is final
                  }}
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/components/FolderPicker.test.tsx && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/components/FolderPicker.tsx frontend/src/components/FolderPicker.test.tsx frontend/src/lib/folderTree.test.ts
git commit -m "feat: folder picker selects and closes on a folder without subfolders"
```

---

## Task 12: OCR language multi-select from the server

**Files:**
- Create: `frontend/src/hooks/useOcrLanguages.ts`
- Modify: `frontend/src/lib/ocrLanguages.ts`, `frontend/src/lib/ocrLanguages.test.ts`
- Modify: `frontend/src/components/OcrLanguageSelect.tsx`
- Test: `frontend/src/components/OcrLanguageSelect.test.tsx`

**Interfaces:**
- Consumes: `OcrLanguage`, `OcrLanguagesResponse` (Task 11). `GET /api/ocr/languages` (Task 1).
- Produces: `useOcrLanguages()` (TanStack query, key `["ocr-languages"]`). `splitLanguages(value: string): string[]`. `toggleLanguage(value: string, code: string): string` (keeps order; never removes the last one). `languagesLabel(value: string, languages: OcrLanguage[]): string`. `<OcrLanguageSelect id? value onChange(value: string) disabled? />`.
- Removes: `OCR_LANGUAGES`, `DEFAULT_OCR_LANGUAGES`. Callers are rewired in Task 14. Until then, `npx tsc -b` reports errors in `ScanPage`, `ScanToolbar`, `UploadDialog` and `DocumentPage`. That is expected; Task 14 fixes them, and this task's commit only has to pass its own tests.

- [ ] **Step 1: Write the failing tests**

Replace `frontend/src/lib/ocrLanguages.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { languagesLabel, splitLanguages, toggleLanguage } from "./ocrLanguages";

const LANGS = [
  { code: "eng", name: "English" },
  { code: "ita", name: "Italian" },
];

describe("ocrLanguages helpers", () => {
  it("splits a Tesseract language string", () => {
    expect(splitLanguages("ita+eng")).toEqual(["ita", "eng"]);
    expect(splitLanguages("")).toEqual([]);
  });

  it("appends in click order and removes, but never the last one", () => {
    expect(toggleLanguage("ita", "eng")).toBe("ita+eng");
    expect(toggleLanguage("eng", "ita")).toBe("eng+ita");
    expect(toggleLanguage("ita+eng", "ita")).toBe("eng");
    expect(toggleLanguage("eng", "eng")).toBe("eng");
    expect(toggleLanguage("", "deu")).toBe("deu");
  });

  it("labels with names, falling back to codes", () => {
    expect(languagesLabel("ita+eng", LANGS)).toBe("Italian + English");
    expect(languagesLabel("ita+xyz", LANGS)).toBe("Italian + xyz");
    expect(languagesLabel("", LANGS)).toBe("No language");
  });
});
```

Create `frontend/src/components/OcrLanguageSelect.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OcrLanguageSelect } from "./OcrLanguageSelect";

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          languages: [
            { code: "deu", name: "German" },
            { code: "eng", name: "English" },
            { code: "ita", name: "Italian" },
          ],
          default: "ita+eng",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onValue }: { onValue: (v: string) => void }) {
  const [value, setValue] = useState("ita");
  return (
    <OcrLanguageSelect
      id="lang"
      value={value}
      onChange={(v) => {
        setValue(v);
        onValue(v);
      }}
    />
  );
}

it("checks languages in click order and keeps at least one", async () => {
  const onValue = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness onValue={onValue} />
    </QueryClientProvider>,
  );
  const trigger = await screen.findByRole("button", { name: /Italian/ });
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("checkbox", { name: "German" }));
  expect(onValue).toHaveBeenLastCalledWith("ita+deu");
  await userEvent.click(screen.getByRole("checkbox", { name: "Italian" }));
  expect(onValue).toHaveBeenLastCalledWith("deu");
  expect(screen.getByRole("checkbox", { name: "German" })).toBeDisabled(); // the only one left
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/ocrLanguages.test.ts src/components/OcrLanguageSelect.test.tsx`
Expected: FAIL (`splitLanguages` is not exported).

- [ ] **Step 3: Implement the helpers and the hook**

Replace `frontend/src/lib/ocrLanguages.ts`:

```ts
import type { OcrLanguage } from "./types";

/** `ita+eng` → ["ita", "eng"]; Tesseract treats the first one as primary. */
export function splitLanguages(value: string): string[] {
  return value.split("+").filter(Boolean);
}

/** Add at the end or remove; the last remaining language cannot be removed. */
export function toggleLanguage(value: string, code: string): string {
  const current = splitLanguages(value);
  if (!current.includes(code)) return [...current, code].join("+");
  if (current.length === 1) return value;
  return current.filter((c) => c !== code).join("+");
}

export function languagesLabel(value: string, languages: OcrLanguage[]): string {
  const names = new Map(languages.map((l) => [l.code, l.name]));
  const codes = splitLanguages(value);
  return codes.length ? codes.map((c) => names.get(c) ?? c).join(" + ") : "No language";
}
```

Create `frontend/src/hooks/useOcrLanguages.ts`:

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { OcrLanguagesResponse } from "@/lib/types";

export function useOcrLanguages() {
  return useQuery({
    queryKey: ["ocr-languages"],
    queryFn: () => api.get<OcrLanguagesResponse>("/api/ocr/languages"),
    staleTime: 5 * 60_000, // the server re-reads Tesseract every 5 minutes
  });
}
```

- [ ] **Step 4: Implement the component**

Replace `frontend/src/components/OcrLanguageSelect.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { useOcrLanguages } from "@/hooks/useOcrLanguages";
import { languagesLabel, splitLanguages, toggleLanguage } from "@/lib/ocrLanguages";

export function OcrLanguageSelect({
  id,
  value,
  onChange,
  disabled,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { data } = useOcrLanguages();
  const languages = data?.languages ?? [];
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = splitLanguages(value);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // keep an enclosing Dialog open
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  if (data && languages.length === 0)
    return <p className="text-sm text-red-600">No OCR languages installed</p>;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        id={id}
        disabled={disabled}
        onClick={() => setOpen(!open)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm disabled:opacity-50"
      >
        <span className="truncate">{languagesLabel(value, languages)}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="OCR languages"
          className="absolute top-full right-0 left-0 z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          {languages.map((l) => {
            const checked = selected.includes(l.code);
            return (
              <label key={l.code} className="flex items-center gap-2 px-1 py-0.5 text-sm">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={checked && selected.length === 1}
                  onChange={() => onChange(toggleLanguage(value, l.code))}
                />
                <span className="flex-1">{l.name}</span>
                {checked && <span className="text-xs text-zinc-400">{selected.indexOf(l.code) + 1}</span>}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/lib/ocrLanguages.test.ts src/components/OcrLanguageSelect.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useOcrLanguages.ts frontend/src/lib/ocrLanguages.ts frontend/src/lib/ocrLanguages.test.ts frontend/src/components/OcrLanguageSelect.tsx frontend/src/components/OcrLanguageSelect.test.tsx
git commit -m "feat: OCR language multi-select fed by the server's installed languages"
```

---

## Task 13: Inline tag input

**Files:**
- Create: `frontend/src/lib/tagInput.ts`, `frontend/src/lib/tagInput.test.ts`
- Create: `frontend/src/components/TagInput.tsx`, `frontend/src/components/TagInput.test.tsx`

**Interfaces:**
- Consumes: `useTags`, `useCreateTag` (existing, `src/hooks/useTags.ts`).
- Produces: `matchingTags(tags: Tag[], selectedIds: number[], query: string): Tag[]`. `exactTag(tags: Tag[], query: string): Tag | undefined` (case-insensitive, trimmed). `nextTagColor(tags: Tag[]): string`. `TAG_COLORS: readonly string[]`. `<TagInput id? value: number[] onChange(ids: number[]) />`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/tagInput.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { exactTag, matchingTags, nextTagColor, TAG_COLORS } from "./tagInput";

const TAGS = [
  { id: 1, name: "Casa", color: "#111111" },
  { id: 2, name: "Auto", color: "#222222" },
  { id: 3, name: "Casalinghi", color: "#333333" },
];

describe("tagInput helpers", () => {
  it("matches by substring, case-insensitive, excluding selected tags", () => {
    expect(matchingTags(TAGS, [1], "cas").map((t) => t.id)).toEqual([3]);
    expect(matchingTags(TAGS, [], "").map((t) => t.id)).toEqual([2, 1, 3]); // sorted by name
  });

  it("finds an exact name ignoring case and spaces", () => {
    expect(exactTag(TAGS, "  casa ")?.id).toBe(1);
    expect(exactTag(TAGS, "cas")).toBeUndefined();
  });

  it("cycles through the palette", () => {
    expect(nextTagColor([])).toBe(TAG_COLORS[0]);
    expect(nextTagColor(TAGS)).toBe(TAG_COLORS[3 % TAG_COLORS.length]);
  });
});
```

Create `frontend/src/components/TagInput.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TagInput } from "./TagInput";

let tags = [{ id: 1, name: "Casa", color: "#111111" }];
const fetchMock = vi.fn();
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  tags = [{ id: 1, name: "Casa", color: "#111111" }];
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === "/api/tags" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      const created = { id: 2, name: body.name, color: body.color };
      tags = [...tags, created];
      return json(201, created);
    }
    return json(200, tags);
  });
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onIds }: { onIds: (ids: number[]) => void }) {
  const [ids, setIds] = useState<number[]>([]);
  return (
    <TagInput
      id="tags"
      value={ids}
      onChange={(next) => {
        setIds(next);
        onIds(next);
      }}
    />
  );
}

function renderInput() {
  const onIds = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <label htmlFor="tags">Tags</label>
      <Harness onIds={onIds} />
    </QueryClientProvider>,
  );
  return { input: screen.getByLabelText("Tags"), onIds };
}

it("selects an existing tag from the suggestions", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "ca");
  await userEvent.click(await screen.findByRole("option", { name: "Casa" }));
  expect(onIds).toHaveBeenLastCalledWith([1]);
  expect(screen.getByText("Casa")).toBeInTheDocument(); // chip
});

it("creates a new tag on Enter and selects it", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "Bollette{Enter}");
  await waitFor(() => expect(onIds).toHaveBeenLastCalledWith([2]));
  expect(fetchMock).toHaveBeenCalledWith("/api/tags", expect.objectContaining({ method: "POST" }));
});

it("selects an existing tag on Enter instead of creating a duplicate", async () => {
  const { input, onIds } = renderInput();
  await screen.findByRole("textbox");
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await userEvent.type(input, "casa{Enter}");
  expect(onIds).toHaveBeenLastCalledWith([1]);
  expect(fetchMock).not.toHaveBeenCalledWith("/api/tags", expect.objectContaining({ method: "POST" }));
});

it("removes a chip", async () => {
  const { input, onIds } = renderInput();
  await userEvent.type(input, "casa{Enter}");
  await userEvent.click(screen.getByRole("button", { name: "Remove Casa" }));
  expect(onIds).toHaveBeenLastCalledWith([]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/tagInput.test.ts src/components/TagInput.test.tsx`
Expected: FAIL (the modules do not exist).

- [ ] **Step 3: Implement the helpers**

Create `frontend/src/lib/tagInput.ts`:

```ts
import type { Tag } from "./types";

export const TAG_COLORS = [
  "#2563eb", "#16a34a", "#dc2626", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#4b5563",
] as const;

const norm = (s: string) => s.trim().toLocaleLowerCase();

export function matchingTags(tags: Tag[], selectedIds: number[], query: string): Tag[] {
  const q = norm(query);
  return tags
    .filter((t) => !selectedIds.includes(t.id) && norm(t.name).includes(q))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function exactTag(tags: Tag[], query: string): Tag | undefined {
  const q = norm(query);
  return q ? tags.find((t) => norm(t.name) === q) : undefined;
}

export function nextTagColor(tags: Tag[]): string {
  return TAG_COLORS[tags.length % TAG_COLORS.length];
}
```

- [ ] **Step 4: Implement the component**

Create `frontend/src/components/TagInput.tsx`:

```tsx
import { useState, type KeyboardEvent } from "react";
import { useCreateTag, useTags } from "@/hooks/useTags";
import { ApiError } from "@/lib/api";
import { exactTag, matchingTags, nextTagColor } from "@/lib/tagInput";
import { cn } from "@/lib/utils";

export function TagInput({
  id,
  value,
  onChange,
}: {
  id?: string;
  value: number[];
  onChange: (ids: number[]) => void;
}) {
  const { data, refetch } = useTags();
  const tags = data ?? [];
  const create = useCreateTag();
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const selected = value.map((tid) => tags.find((t) => t.id === tid)).filter((t) => t !== undefined);
  const suggestions = matchingTags(tags, value, query);
  const exact = exactTag(tags, query);
  const canCreate = query.trim() !== "" && exact === undefined;

  const add = (tagId: number) => {
    if (!value.includes(tagId)) onChange([...value, tagId]);
    setQuery("");
    setActive(0);
    setError(null);
  };

  const createTag = () => {
    const name = query.trim();
    create.mutate(
      { name, color: nextTagColor(tags) },
      {
        onSuccess: (tag) => add(tag.id),
        onError: async (err) => {
          if (err instanceof ApiError && err.status === 409) {
            const existing = exactTag((await refetch()).data ?? [], name); // created elsewhere meanwhile
            if (existing) return add(existing.id);
          }
          setError(err instanceof ApiError ? err.message : "Could not create tag");
        },
      },
    );
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? Math.min(active + 1, suggestions.length - 1) : Math.max(active - 1, 0);
      setActive(next);
    } else if (e.key === "Enter") {
      e.preventDefault();
      // exact name → select it; otherwise create the typed name; arrows + Enter on an empty box pick
      if (exact) add(exact.id);
      else if (canCreate) createTag();
      else if (suggestions[active]) add(suggestions[active].id);
    } else if (e.key === "Backspace" && query === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <div className="relative">
      <div className="flex min-h-9 flex-wrap items-center gap-1 rounded-md border border-zinc-300 bg-white px-1 py-1">
        {selected.map((tag) => (
          <span
            key={tag.id}
            className="flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
            style={{ backgroundColor: `${tag.color}22`, color: tag.color }}
          >
            {tag.name}
            <button
              type="button"
              aria-label={`Remove ${tag.name}`}
              onClick={() => onChange(value.filter((x) => x !== tag.id))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)} // let option clicks land first
          onKeyDown={onKeyDown}
          placeholder={selected.length ? "" : "Add tags…"}
          className="min-w-24 flex-1 px-1 text-sm outline-none"
        />
      </div>
      {focused && (suggestions.length > 0 || canCreate) && (
        <ul role="listbox" className="absolute z-20 mt-1 max-h-48 w-full overflow-y-auto rounded-md border border-zinc-200 bg-white p-1 shadow-lg">
          {suggestions.map((tag, index) => (
            <li
              key={tag.id}
              role="option"
              aria-selected={index === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => add(tag.id)}
              className={cn("cursor-pointer rounded px-2 py-1 text-sm", index === active && "bg-zinc-100")}
            >
              {tag.name}
            </li>
          ))}
          {canCreate && (
            <li
              role="option"
              aria-selected={false}
              onMouseDown={(e) => e.preventDefault()}
              onClick={createTag}
              className="cursor-pointer rounded px-2 py-1 text-sm text-zinc-600 hover:bg-zinc-100"
            >
              Create “{query.trim()}”
            </li>
          )}
        </ul>
      )}
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}
```

Enter selects an exact name match, otherwise creates the typed name. Pick a partial match with the mouse.

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npx vitest run src/lib/tagInput.test.ts src/components/TagInput.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/tagInput.ts frontend/src/lib/tagInput.test.ts frontend/src/components/TagInput.tsx frontend/src/components/TagInput.test.tsx
git commit -m "feat: tag input that creates new tags inline"
```

---

## Task 14: Processing options group; wire OCR, tags and flags into Scan, Upload and Document

**Files:**
- Create: `frontend/src/lib/processing.ts`, `frontend/src/lib/processing.test.ts`
- Create: `frontend/src/components/ProcessingOptions.tsx`
- Modify: `frontend/src/lib/upload.ts`, `frontend/src/lib/upload.test.ts`
- Modify: `frontend/src/components/scan/ScanToolbar.tsx`, `frontend/src/components/scan/ScanSidebar.tsx`, `frontend/src/pages/ScanPage.tsx`
- Modify: `frontend/src/components/UploadDialog.tsx`
- Modify: `frontend/src/pages/DocumentPage.tsx`

**Interfaces:**
- Consumes: `OcrLanguageSelect` and `useOcrLanguages` (Task 12), `TagInput` (Task 13).
- Produces: `ProcessingValues { ocrEnabled: boolean; ocrLanguages: string; summaryEnabled: boolean; translationEnabled: boolean }`. `defaultProcessing(): ProcessingValues` (`ocrLanguages: ""` = use the server default). `processingFromDocument(doc: Document): ProcessingValues`. `processingPayload(v: ProcessingValues): { ocr_enabled: boolean; ocr_languages: string | null; summary_enabled: boolean; translation_enabled: boolean }`. `<ProcessingOptions idPrefix value onChange />`. `UploadFields.summaryEnabled?`, `UploadFields.translationEnabled?`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/processing.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { defaultProcessing, processingFromDocument, processingPayload } from "./processing";
import type { Document } from "./types";

describe("processing", () => {
  it("defaults everything on with the server's default languages", () => {
    expect(defaultProcessing()).toEqual({
      ocrEnabled: true,
      ocrLanguages: "",
      summaryEnabled: true,
      translationEnabled: true,
    });
  });

  it("maps to the API payload; empty languages become null", () => {
    expect(processingPayload({ ...defaultProcessing(), summaryEnabled: false })).toEqual({
      ocr_enabled: true,
      ocr_languages: null,
      summary_enabled: false,
      translation_enabled: true,
    });
  });

  it("reads a document's stored settings", () => {
    const doc = {
      ocr_enabled: false,
      ocr_languages: "deu",
      summary_enabled: true,
      translation_enabled: false,
    } as Document;
    expect(processingFromDocument(doc)).toEqual({
      ocrEnabled: false,
      ocrLanguages: "deu",
      summaryEnabled: true,
      translationEnabled: false,
    });
  });
});
```

In `frontend/src/lib/upload.test.ts`, add:

```ts
it("sends disabled summary and translation flags", () => {
  const form = buildUploadForm(new File(["x"], "a.pdf"), { summaryEnabled: false, translationEnabled: false });
  expect(form.get("summary_enabled")).toBe("false");
  expect(form.get("translation_enabled")).toBe("false");
  const defaults = buildUploadForm(new File(["x"], "a.pdf"), {});
  expect(defaults.get("summary_enabled")).toBeNull();
});
```

(Match the file's existing `describe`/`it` imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/processing.test.ts src/lib/upload.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement the helpers**

Create `frontend/src/lib/processing.ts`:

```ts
import type { Document } from "./types";

export interface ProcessingValues {
  ocrEnabled: boolean;
  ocrLanguages: string; // "" = not chosen yet: use the server default
  summaryEnabled: boolean;
  translationEnabled: boolean;
}

export function defaultProcessing(): ProcessingValues {
  return { ocrEnabled: true, ocrLanguages: "", summaryEnabled: true, translationEnabled: true };
}

export function processingFromDocument(doc: Document): ProcessingValues {
  return {
    ocrEnabled: doc.ocr_enabled,
    ocrLanguages: doc.ocr_languages,
    summaryEnabled: doc.summary_enabled,
    translationEnabled: doc.translation_enabled,
  };
}

export function processingPayload(v: ProcessingValues) {
  return {
    ocr_enabled: v.ocrEnabled,
    ocr_languages: v.ocrLanguages || null,
    summary_enabled: v.summaryEnabled,
    translation_enabled: v.translationEnabled,
  };
}
```

In `frontend/src/lib/upload.ts`, add `summaryEnabled?: boolean; translationEnabled?: boolean;` to `UploadFields`, and in `buildUploadForm` after the `ocrEnabled` line:

```ts
  if (fields.summaryEnabled === false) form.append("summary_enabled", "false");
  if (fields.translationEnabled === false) form.append("translation_enabled", "false");
```

- [ ] **Step 4: Implement `ProcessingOptions`**

Create `frontend/src/components/ProcessingOptions.tsx`:

```tsx
import { useEffect } from "react";
import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";
import { useOcrLanguages } from "@/hooks/useOcrLanguages";
import type { ProcessingValues } from "@/lib/processing";

export function ProcessingOptions({
  idPrefix,
  value,
  onChange,
}: {
  idPrefix: string;
  value: ProcessingValues;
  onChange: (value: ProcessingValues) => void;
}) {
  const { data } = useOcrLanguages();
  const noLanguages = data !== undefined && data.languages.length === 0;

  useEffect(() => {
    // fill the server default once it arrives, unless the user (or the document) already chose
    if (data && value.ocrLanguages === "" && data.default) onChange({ ...value, ocrLanguages: data.default });
  }, [data, value, onChange]);

  const set = (patch: Partial<ProcessingValues>) => onChange({ ...value, ...patch });

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Processing</legend>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.ocrEnabled && !noLanguages}
          disabled={noLanguages}
          onChange={(e) => set({ ocrEnabled: e.target.checked })}
        />
        OCR (extract text)
      </label>
      {value.ocrEnabled && (
        <OcrLanguageSelect
          id={`${idPrefix}-ocr-lang`}
          value={value.ocrLanguages}
          onChange={(ocrLanguages) => set({ ocrLanguages })}
        />
      )}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value.summaryEnabled} onChange={(e) => set({ summaryEnabled: e.target.checked })} />
        AI summary
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={value.translationEnabled}
          onChange={(e) => set({ translationEnabled: e.target.checked })}
        />
        Translation
      </label>
    </fieldset>
  );
}
```

Callers must pass a stable `onChange` (a `useState` setter), so the effect does not loop.

- [ ] **Step 5: Wire the Scan page**

`frontend/src/components/scan/ScanToolbar.tsx`: remove the `languages`, `onLanguagesChange`, `ocrEnabled` and `onOcrEnabledChange` props, the "Run OCR" label and the `OcrLanguageSelect`, and the `OcrLanguageSelect` import. The toolbar keeps the status pill and the device picker.

`frontend/src/components/scan/ScanSidebar.tsx`:
- Remove the `tags: Tag[]` prop and the `Tag` import.
- Add props `processing: ProcessingValues; onProcessingChange: (v: ProcessingValues) => void;`.
- Replace the whole `{tags.length > 0 && (...)}` block with:

```tsx
      <div>
        <Label htmlFor="scan-tags">Tags</Label>
        <TagInput id="scan-tags" value={fields.tagIds} onChange={(tagIds) => onChange({ tagIds })} />
      </div>
      <ProcessingOptions idPrefix="scan" value={processing} onChange={onProcessingChange} />
```

with the imports `TagInput`, `ProcessingOptions` and `type ProcessingValues`.

`frontend/src/pages/ScanPage.tsx`:
- Remove `useTags`, the `DEFAULT_OCR_LANGUAGES` import, and the `languages`/`ocrEnabled` state.
- Add `const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);`.
- Session creation body: `{ ocr_languages: processing.ocrLanguages || null, ocr_enabled: processing.ocrEnabled, device: chosenDevice }`.
- Compile body: replace the `ocr_languages` and `ocr_enabled` lines with `...processingPayload(processing),`.
- `<ScanToolbar ...>`: drop the removed props.
- `<ScanSidebar ...>`: drop `tags`, add `processing={processing} onProcessingChange={setProcessing}`.
- In `startOver`, keep `processing` as it is (same document batch), and reset only `fields`.

- [ ] **Step 6: Wire the Upload dialog**

`frontend/src/components/UploadDialog.tsx`:
- Remove `useTags`, `OcrLanguageSelect`, `DEFAULT_OCR_LANGUAGES`, and the `languages`/`ocrEnabled` state.
- Add `const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);`.
- Replace the Tags block with `<Label htmlFor="up-tags">Tags</Label><TagInput id="up-tags" value={tagIds} onChange={setTagIds} />` (wrapped in a `<div>`).
- Replace the OCR checkbox and the language blocks with `<ProcessingOptions idPrefix="up" value={processing} onChange={setProcessing} />`.
- `buildUploadForm` fields: `ocrLanguages: processing.ocrLanguages || undefined, ocrEnabled: processing.ocrEnabled, summaryEnabled: processing.summaryEnabled, translationEnabled: processing.translationEnabled`.

- [ ] **Step 7: Wire the Document page**

`frontend/src/pages/DocumentPage.tsx`:
- Remove the `DEFAULT_OCR_LANGUAGES` import, the `ocrLanguages`/`ocrEnabled` state, `OcrLanguageSelect`, and `useTags` with the `tags` variable.
- Add `const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);`. In the hydration effect, replace the two `setOcr*` calls with `setProcessing(processingFromDocument(doc));`.
- Replace the Tags checkbox block with:

```tsx
        <div>
          <Label htmlFor="d-tags">Tags</Label>
          <TagInput id="d-tags" value={tagIds} onChange={setTagIds} />
        </div>
```

- The reprocess mutation body becomes `processingPayload(processing)`. The backend `ReprocessRequest.ocr_languages` is required, so after `processingFromDocument` the value is never empty for an existing document.
- In `confirmReprocess`, replace `ocrEnabled` with `processing.ocrEnabled`.
- Replace the OCR section heading and controls (from `<p className="text-sm font-medium">OCR</p>` through the `OcrLanguageSelect` block) with `<ProcessingOptions idPrefix="d" value={processing} onChange={setProcessing} />`. Keep the Re-process button and its error line. The error line already shows `reprocess.error.message`, which is how the 422 `Unknown OCR language: xyz` reaches the user (Review Focus 3).

- [ ] **Step 8: Type check, lint, test**

Run: `cd frontend && npx tsc -b && npm run lint && npm test`
Expected: PASS, no type errors (the errors expected after Task 12 are gone).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/processing.ts frontend/src/lib/processing.test.ts frontend/src/components/ProcessingOptions.tsx frontend/src/lib/upload.ts frontend/src/lib/upload.test.ts frontend/src/components/scan/ScanToolbar.tsx frontend/src/components/scan/ScanSidebar.tsx frontend/src/pages/ScanPage.tsx frontend/src/components/UploadDialog.tsx frontend/src/pages/DocumentPage.tsx
git commit -m "feat: processing options (OCR, AI summary, translation) and inline tags in scan, upload and document"
```

---

## Task 15: Scan page reorder

**Files:**
- Modify: `frontend/package.json` (via npm)
- Modify: `frontend/src/lib/scanWizard.ts`, `frontend/src/lib/scanWizard.test.ts`
- Create: `frontend/src/lib/scanReorder.ts`, `frontend/src/lib/scanReorder.test.ts`
- Modify: `frontend/src/components/scan/PageCarousel.tsx`
- Create: `frontend/src/components/scan/PageCarousel.test.tsx`
- Modify: `frontend/src/pages/ScanPage.tsx`

**Interfaces:**
- Produces: `movePage(pages: ScanPageInfo[], pageId: number, toIndex: number): ScanPageInfo[]` (renumbers `page_number` from 1). `applyReorder(args: { sessionId: number; previous: ScanPageInfo[]; next: ScanPageInfo[]; dispatch: (a: ScanAction) => void; post?: (path: string, body: unknown) => Promise<unknown> }): Promise<void>`. `PageCarousel` gains the prop `onMove: (pageId: number, toIndex: number) => void`.

- [ ] **Step 1: Add the dependency**

Run: `cd frontend && npm install @dnd-kit/core@6.3.1 @dnd-kit/sortable@10.0.0 @dnd-kit/utilities@3.2.2`
Expected: `package.json` lists the three packages.

- [ ] **Step 2: Write the failing tests**

Append to `frontend/src/lib/scanWizard.test.ts`:

```ts
describe("movePage", () => {
  const pages = [
    { id: 10, page_number: 1 },
    { id: 11, page_number: 2 },
    { id: 12, page_number: 3 },
  ];
  it("moves a page and renumbers", () => {
    expect(movePage(pages, 12, 0)).toEqual([
      { id: 12, page_number: 1 },
      { id: 10, page_number: 2 },
      { id: 11, page_number: 3 },
    ]);
  });
  it("clamps the target index and ignores unknown ids", () => {
    expect(movePage(pages, 10, 99).map((p) => p.id)).toEqual([11, 12, 10]);
    expect(movePage(pages, 99, 0)).toBe(pages);
  });
});
```

(Add `movePage` to that file's import from `./scanWizard`.)

Create `frontend/src/lib/scanReorder.test.ts`:

```ts
import { expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { applyReorder } from "./scanReorder";

const previous = [
  { id: 1, page_number: 1 },
  { id: 2, page_number: 2 },
];
const next = [
  { id: 2, page_number: 1 },
  { id: 1, page_number: 2 },
];

it("applies the new order first, then saves it", async () => {
  const dispatch = vi.fn();
  const post = vi.fn().mockResolvedValue({});
  await applyReorder({ sessionId: 7, previous, next, dispatch, post });
  expect(dispatch).toHaveBeenCalledTimes(1);
  expect(dispatch).toHaveBeenCalledWith({ type: "PAGES_REORDERED", pages: next });
  expect(post).toHaveBeenCalledWith("/api/scan/sessions/7/reorder", { page_ids: [2, 1] });
});

it("rolls back and reports the error when saving fails", async () => {
  const dispatch = vi.fn();
  const post = vi.fn().mockRejectedValue(new ApiError(422, "invalid_order", "bad order"));
  await applyReorder({ sessionId: 7, previous, next, dispatch, post });
  expect(dispatch.mock.calls.map((c) => c[0])).toEqual([
    { type: "PAGES_REORDERED", pages: next },
    { type: "PAGES_REORDERED", pages: previous },
    { type: "SCAN_FAILED", code: "invalid_order", message: "bad order" },
  ]);
});
```

Create `frontend/src/components/scan/PageCarousel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PageCarousel } from "./PageCarousel";

vi.mock("@/hooks/usePreviewImage", () => ({ usePreviewImage: () => null }));

const pages = [
  { id: 1, page_number: 1 },
  { id: 2, page_number: 2 },
  { id: 3, page_number: 3 },
];

it("moves the selected page with the arrow buttons", async () => {
  const onMove = vi.fn();
  render(
    <PageCarousel pages={pages} selectedPageId={2} disabled={false} onSelect={vi.fn()} onDelete={vi.fn()} onMove={onMove} />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Move page 2 left" }));
  expect(onMove).toHaveBeenLastCalledWith(2, 0);
  await userEvent.click(screen.getByRole("button", { name: "Move page 2 right" }));
  expect(onMove).toHaveBeenLastCalledWith(2, 2);
});

it("hides the arrows on unselected pages and disables them at the ends", () => {
  render(
    <PageCarousel pages={pages} selectedPageId={1} disabled={false} onSelect={vi.fn()} onDelete={vi.fn()} onMove={vi.fn()} />,
  );
  expect(screen.getByRole("button", { name: "Move page 1 left" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Move page 2 left" })).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/scanWizard.test.ts src/lib/scanReorder.test.ts src/components/scan/PageCarousel.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Implement `movePage` and `applyReorder`**

Append to `frontend/src/lib/scanWizard.ts`:

```ts
/** New page order with `pageId` at `toIndex` (clamped), renumbered from 1. */
export function movePage(pages: ScanPageInfo[], pageId: number, toIndex: number): ScanPageInfo[] {
  const from = pages.findIndex((p) => p.id === pageId);
  if (from === -1) return pages;
  const rest = pages.filter((p) => p.id !== pageId);
  const target = Math.max(0, Math.min(toIndex, rest.length));
  rest.splice(target, 0, pages[from]);
  return rest.map((p, index) => ({ ...p, page_number: index + 1 }));
}
```

Create `frontend/src/lib/scanReorder.ts`:

```ts
import { api, ApiError } from "./api";
import type { ScanAction } from "./scanWizard";
import type { ScanPageInfo } from "./types";

/** Optimistic reorder: show the new order at once, roll back if the server rejects it. */
export async function applyReorder({
  sessionId,
  previous,
  next,
  dispatch,
  post = api.post,
}: {
  sessionId: number;
  previous: ScanPageInfo[];
  next: ScanPageInfo[];
  dispatch: (action: ScanAction) => void;
  post?: (path: string, body: unknown) => Promise<unknown>;
}): Promise<void> {
  dispatch({ type: "PAGES_REORDERED", pages: next });
  try {
    await post(`/api/scan/sessions/${sessionId}/reorder`, { page_ids: next.map((p) => p.id) });
  } catch (err) {
    dispatch({ type: "PAGES_REORDERED", pages: previous });
    dispatch({
      type: "SCAN_FAILED",
      code: err instanceof ApiError ? err.code : "unknown",
      message: err instanceof ApiError ? err.message : "Could not reorder pages",
    });
  }
}
```

- [ ] **Step 5: Implement the sortable carousel**

Replace `frontend/src/components/scan/PageCarousel.tsx`:

```tsx
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  horizontalListSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  index,
  count,
  selected,
  disabled,
  onSelect,
  onDelete,
  onMove,
}: {
  page: ScanPageInfo;
  index: number;
  count: number;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onMove: (toIndex: number) => void;
}) {
  const url = usePreviewImage(page.id);
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: page.id,
    disabled,
  });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "w-28 shrink-0 rounded border bg-white p-1",
        selected ? "border-zinc-900 ring-2 ring-zinc-900" : "border-zinc-200",
        isDragging && "opacity-60",
      )}
    >
      <button
        type="button"
        className="block w-full cursor-grab active:cursor-grabbing"
        onClick={onSelect}
        title={`Show page ${page.page_number} (drag to reorder)`}
        {...attributes}
        {...listeners}
      >
        {url ? (
          <img src={url} alt={`Page ${page.page_number}`} className="h-32 w-full rounded object-cover" />
        ) : (
          <div className="flex h-32 items-center justify-center text-zinc-300">…</div>
        )}
      </button>
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        {selected ? (
          <button
            type="button"
            aria-label={`Move page ${page.page_number} left`}
            disabled={disabled || index === 0}
            onClick={() => onMove(index - 1)}
            className="h-6 w-6 rounded hover:bg-zinc-100 disabled:opacity-30"
          >
            ←
          </button>
        ) : (
          <span className="w-6" />
        )}
        <span>p. {page.page_number}</span>
        {selected && (
          <button
            type="button"
            aria-label={`Move page ${page.page_number} right`}
            disabled={disabled || index === count - 1}
            onClick={() => onMove(index + 1)}
            className="h-6 w-6 rounded hover:bg-zinc-100 disabled:opacity-30"
          >
            →
          </button>
        )}
        <button
          type="button"
          disabled={disabled}
          onClick={onDelete}
          aria-label="Delete page"
          title="Delete page"
          className="flex h-6 w-6 items-center justify-center rounded text-base leading-none text-red-600 hover:bg-red-50 disabled:opacity-40"
        >
          ×
        </button>
      </div>
    </div>
  );
}

export function PageCarousel({
  pages,
  selectedPageId,
  disabled,
  onSelect,
  onDelete,
  onMove,
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onDelete: (pageId: number) => void;
  onMove: (pageId: number, toIndex: number) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), // a click still selects
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  if (pages.length === 0) return null;

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    onMove(Number(active.id), pages.findIndex((p) => p.id === over.id));
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={pages.map((p) => p.id)} strategy={horizontalListSortingStrategy}>
        <div className="flex gap-3 overflow-x-auto pb-2">
          {pages.map((page, index) => (
            <Thumb
              key={page.id}
              page={page}
              index={index}
              count={pages.length}
              selected={page.id === selectedPageId}
              disabled={disabled}
              onSelect={() => onSelect(page.id)}
              onDelete={() => onDelete(page.id)}
              onMove={(toIndex) => onMove(page.id, toIndex)}
            />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
```

- [ ] **Step 6: Wire the Scan page**

In `frontend/src/pages/ScanPage.tsx`, import `movePage` from `@/lib/scanWizard` and `applyReorder` from `@/lib/scanReorder`, then add next to `deletePage`:

```tsx
  const movePageTo = (pageId: number, toIndex: number) => {
    if (state.sessionId === null) return;
    const next = movePage(state.pages, pageId, toIndex);
    if (next === state.pages) return;
    void applyReorder({ sessionId: state.sessionId, previous: state.pages, next, dispatch });
  };
```

and pass `onMove={movePageTo}` to `<PageCarousel>`.

- [ ] **Step 7: Run the tests**

Run: `cd frontend && npx tsc -b && npx vitest run src/lib/scanWizard.test.ts src/lib/scanReorder.test.ts src/components/scan/PageCarousel.test.tsx`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/scanWizard.ts frontend/src/lib/scanWizard.test.ts frontend/src/lib/scanReorder.ts frontend/src/lib/scanReorder.test.ts frontend/src/components/scan/PageCarousel.tsx frontend/src/components/scan/PageCarousel.test.tsx frontend/src/pages/ScanPage.tsx
git commit -m "feat: reorder scanned pages before saving (drag or arrow buttons)"
```

---

## Task 16: Browse URL state, file-system view and date filter

**Files:**
- Create: `frontend/src/lib/browseParams.ts`, `frontend/src/lib/browseParams.test.ts`
- Modify: `frontend/src/lib/sorting.ts`, `frontend/src/lib/sorting.test.ts` (remove `browseSearch`)
- Modify: `frontend/src/hooks/useDocuments.ts`, `frontend/src/hooks/useDocuments.test.ts`
- Create: `frontend/src/components/FolderTiles.tsx`, `frontend/src/components/FolderTiles.test.tsx`
- Modify: `frontend/src/components/FolderTree.tsx`, `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/pages/BrowsePage.tsx`

**Interfaces:**
- Consumes: `Folder.document_count` (Task 11). `childrenOf`, `folderPath` (`lib/folderTree.ts`). Backend `folder_id=root`, `date_from`, `date_to` (Task 4).
- Produces: `BrowseParams { all: boolean; folderId: number | null; sort: DocumentSort; tagId: number | null; docType: string | null; dateFrom: string | null; dateTo: string | null }`. `DEFAULT_BROWSE: BrowseParams`. `parseBrowseParams(sp: URLSearchParams): BrowseParams`. `browseQuery(p: BrowseParams): string` (`""` or `?...`). `browseViewKey(p: BrowseParams): string` (changes whenever the view or a filter changes; used to clear the selection in Task 17). `invalidDateRange(p: BrowseParams): boolean`. `DocumentFilters.folder: number | "root" | null`, plus `dateFrom?` and `dateTo?`. `useBulkMove()`, `useBulkDelete()`. `<FolderTiles folders parentId onOpen(id) />`. `<Breadcrumb folders folderId onNavigate(id | null) />` (exported from `FolderTiles.tsx`).

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/browseParams.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { browseQuery, browseViewKey, DEFAULT_BROWSE, invalidDateRange, parseBrowseParams } from "./browseParams";

const parse = (qs: string) => parseBrowseParams(new URLSearchParams(qs));

describe("browseParams", () => {
  it("parses every key, ignoring junk", () => {
    expect(parse("")).toEqual(DEFAULT_BROWSE);
    expect(parse("folder=3&tag=2&type=pdf&from=2026-01-01&to=2026-02-01&sort=title_asc")).toEqual({
      all: false,
      folderId: 3,
      sort: "title_asc",
      tagId: 2,
      docType: "pdf",
      dateFrom: "2026-01-01",
      dateTo: "2026-02-01",
    });
    expect(parse("all=1").all).toBe(true);
    expect(parse("folder=abc&tag=x&from=yesterday")).toEqual(DEFAULT_BROWSE);
  });

  it("round-trips through the query string, omitting defaults", () => {
    expect(browseQuery(DEFAULT_BROWSE)).toBe("");
    const p = { ...DEFAULT_BROWSE, folderId: 3, dateFrom: "2026-01-01", sort: "title_asc" as const };
    expect(browseQuery(p)).toBe("?folder=3&from=2026-01-01&sort=title_asc");
    expect(parse(browseQuery(p).slice(1))).toEqual(p);
    expect(browseQuery({ ...DEFAULT_BROWSE, all: true, folderId: 5 })).toBe("?all=1"); // all ignores folder
  });

  it("view key changes with view and filters, not with sort", () => {
    const base = browseViewKey(DEFAULT_BROWSE);
    expect(browseViewKey({ ...DEFAULT_BROWSE, sort: "title_asc" })).toBe(base);
    expect(browseViewKey({ ...DEFAULT_BROWSE, folderId: 1 })).not.toBe(base);
    expect(browseViewKey({ ...DEFAULT_BROWSE, dateTo: "2026-01-01" })).not.toBe(base);
  });

  it("flags an inverted range", () => {
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-02-01", dateTo: "2026-01-01" })).toBe(true);
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-01-01", dateTo: "2026-01-01" })).toBe(false);
    expect(invalidDateRange({ ...DEFAULT_BROWSE, dateFrom: "2026-01-01" })).toBe(false);
  });
});
```

Replace the `documentsQueryString` tests in `frontend/src/hooks/useDocuments.test.ts`:

```ts
describe("documentsQueryString", () => {
  const none = { folder: null, tagId: null, docType: null } as const;
  it("includes only active filters", () => {
    expect(documentsQueryString(none)).toBe("");
    expect(documentsQueryString({ ...none, folder: 3, tagId: 2, docType: "pdf" })).toBe(
      "?folder_id=3&tag_id=2&doc_type=pdf",
    );
  });
  it("sends root and dates", () => {
    expect(documentsQueryString({ ...none, folder: "root", dateFrom: "2026-01-01", dateTo: "2026-01-31" })).toBe(
      "?folder_id=root&date_from=2026-01-01&date_to=2026-01-31",
    );
  });
  it("passes a non-default sort", () => {
    expect(documentsQueryString({ ...none, sort: "title_asc" })).toBe("?sort=title_asc");
    expect(documentsQueryString({ ...none, folder: 3, sort: "date_desc" })).toBe("?folder_id=3");
  });
});
```

Create `frontend/src/components/FolderTiles.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { Breadcrumb, FolderTiles } from "./FolderTiles";

const FOLDERS = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "", document_count: 4 },
  { id: 2, name: "2026", parent_id: 1, created_at: "", document_count: 1 },
  { id: 3, name: "Auto", parent_id: null, created_at: "", document_count: 0 },
];

it("shows direct subfolders with counts and opens on click", async () => {
  const onOpen = vi.fn();
  render(<FolderTiles folders={FOLDERS} parentId={null} onOpen={onOpen} />);
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["📁Auto0 documents", "📁Bollette4 documents"]);
  await userEvent.click(screen.getByRole("button", { name: /Bollette/ }));
  expect(onOpen).toHaveBeenCalledWith(1);
});

it("renders nothing when there are no subfolders", () => {
  const { container } = render(<FolderTiles folders={FOLDERS} parentId={2} onOpen={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

it("breadcrumb links every level", async () => {
  const onNavigate = vi.fn();
  render(<Breadcrumb folders={FOLDERS} folderId={2} onNavigate={onNavigate} />);
  await userEvent.click(screen.getByRole("button", { name: "Root" }));
  expect(onNavigate).toHaveBeenLastCalledWith(null);
  await userEvent.click(screen.getByRole("button", { name: "Bollette" }));
  expect(onNavigate).toHaveBeenLastCalledWith(1);
  expect(screen.getByText("2026")).toHaveAttribute("aria-current", "page");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/browseParams.test.ts src/hooks/useDocuments.test.ts src/components/FolderTiles.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement `browseParams`**

Create `frontend/src/lib/browseParams.ts`:

```ts
import { DEFAULT_SORT, parseSort, type DocumentSort } from "./sorting";

export interface BrowseParams {
  all: boolean; // flat list of every document (today's "All documents")
  folderId: number | null; // null with all=false = root
  sort: DocumentSort;
  tagId: number | null;
  docType: string | null;
  dateFrom: string | null; // YYYY-MM-DD
  dateTo: string | null;
}

export const DEFAULT_BROWSE: BrowseParams = {
  all: false,
  folderId: null,
  sort: DEFAULT_SORT,
  tagId: null,
  docType: null,
  dateFrom: null,
  dateTo: null,
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const int = (raw: string | null) => (raw && /^\d+$/.test(raw) ? Number(raw) : null);
const isoDate = (raw: string | null) => (raw && ISO_DATE.test(raw) ? raw : null);

export function parseBrowseParams(sp: URLSearchParams): BrowseParams {
  return {
    all: sp.get("all") === "1",
    folderId: int(sp.get("folder")),
    sort: parseSort(sp.get("sort")),
    tagId: int(sp.get("tag")),
    docType: sp.get("type") || null,
    dateFrom: isoDate(sp.get("from")),
    dateTo: isoDate(sp.get("to")),
  };
}

export function browseQuery(p: BrowseParams): string {
  const params = new URLSearchParams();
  if (p.all) params.set("all", "1");
  else if (p.folderId !== null) params.set("folder", String(p.folderId));
  if (p.tagId !== null) params.set("tag", String(p.tagId));
  if (p.docType) params.set("type", p.docType);
  if (p.dateFrom) params.set("from", p.dateFrom);
  if (p.dateTo) params.set("to", p.dateTo);
  if (p.sort !== DEFAULT_SORT) params.set("sort", p.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Identity of what is listed (view + filters, not order): selection resets when it changes. */
export function browseViewKey(p: BrowseParams): string {
  return browseQuery({ ...p, sort: DEFAULT_SORT });
}

export function invalidDateRange(p: BrowseParams): boolean {
  return p.dateFrom !== null && p.dateTo !== null && p.dateFrom > p.dateTo;
}
```

In `frontend/src/lib/sorting.ts`, delete `browseSearch`. In `sorting.test.ts`, delete its `describe("browseSearch", ...)` block and the import.

- [ ] **Step 4: Update `useDocuments` and add the bulk hooks**

In `frontend/src/hooks/useDocuments.ts`:

```ts
export interface DocumentFilters {
  folder: number | "root" | null; // null = every folder
  tagId: number | null;
  docType: string | null;
  dateFrom?: string | null;
  dateTo?: string | null;
  sort?: DocumentSort;
}

export function documentsQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.folder !== null) params.set("folder_id", String(filters.folder));
  if (filters.tagId !== null) params.set("tag_id", String(filters.tagId));
  if (filters.docType !== null) params.set("doc_type", filters.docType);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  if (filters.sort && filters.sort !== DEFAULT_SORT) params.set("sort", filters.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
```

Change `useDocuments(filters)` to `useDocuments(filters: DocumentFilters, enabled = true)` and pass `enabled` to `useQuery`. Append:

```ts
function useInvalidateListing() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["folders"] }); // folder tiles show document counts
  };
}

export function useBulkMove() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (body: { ids: string[]; folder_id: number | null }) =>
      api.post<BulkResult>("/api/documents/bulk/move", body),
    onSuccess: invalidate,
  });
}

export function useBulkDelete() {
  const invalidate = useInvalidateListing();
  return useMutation({
    mutationFn: (ids: string[]) => api.post<BulkResult>("/api/documents/bulk/delete", { ids }),
    onSuccess: invalidate,
  });
}
```

Also make `useDeleteDocument` use `useInvalidateListing()` for its `onSuccess`, so single delete updates the counts too. Import `BulkResult` from `@/lib/types`.

- [ ] **Step 5: Implement `FolderTiles` and `Breadcrumb`**

Create `frontend/src/components/FolderTiles.tsx`:

```tsx
import { Fragment } from "react";
import { childrenOf, folderPath } from "@/lib/folderTree";
import type { Folder } from "@/lib/types";

export function FolderTiles({
  folders,
  parentId,
  onOpen,
}: {
  folders: Folder[];
  parentId: number | null;
  onOpen: (id: number) => void;
}) {
  const children = childrenOf(folders, parentId);
  if (children.length === 0) return null;
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
      {children.map((f) => (
        <button
          key={f.id}
          type="button"
          onClick={() => onOpen(f.id)}
          className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white p-3 text-left hover:shadow"
        >
          <span aria-hidden="true" className="text-xl">
            📁
          </span>
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">{f.name}</span>
            <span className="block text-xs text-zinc-400">
              {f.document_count} {f.document_count === 1 ? "document" : "documents"}
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}

export function Breadcrumb({
  folders,
  folderId,
  onNavigate,
}: {
  folders: Folder[];
  folderId: number | null;
  onNavigate: (id: number | null) => void;
}) {
  const path = folderPath(folders, folderId);
  const crumb = "text-sm text-zinc-500 hover:text-zinc-900 hover:underline";
  return (
    <nav aria-label="Breadcrumb" className="mb-3 flex flex-wrap items-center gap-1">
      {path.length === 0 ? (
        <span aria-current="page" className="text-sm font-medium">
          Root
        </span>
      ) : (
        <button type="button" className={crumb} onClick={() => onNavigate(null)}>
          Root
        </button>
      )}
      {path.map((f, index) => (
        <Fragment key={f.id}>
          <span className="text-zinc-300">/</span>
          {index === path.length - 1 ? (
            <span aria-current="page" className="text-sm font-medium">
              {f.name}
            </span>
          ) : (
            <button type="button" className={crumb} onClick={() => onNavigate(f.id)}>
              {f.name}
            </button>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
```

- [ ] **Step 6: Sidebar entries**

`frontend/src/components/FolderTree.tsx`: change the `FolderTree` props to `{ selectedId: number | null; allSelected: boolean; onSelect: (id: number | null) => void; onSelectAll: () => void }`. Replace the single "All documents" button with two buttons:

```tsx
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          allSelected && "bg-zinc-200 font-medium",
        )}
        onClick={onSelectAll}
      >
        All documents
      </button>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          !allSelected && selectedId === null && "bg-zinc-200 font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        Root
      </button>
```

`frontend/src/components/Layout.tsx`: replace the `browseSearch`/`parseSort` imports with `browseQuery, DEFAULT_BROWSE, parseBrowseParams` from `@/lib/browseParams`. Then:

```tsx
  const current = parseBrowseParams(searchParams);
  const selectFolder = (id: number | null) =>
    navigate(`/${browseQuery({ ...DEFAULT_BROWSE, folderId: id, sort: current.sort })}`);
  const selectAll = () => navigate(`/${browseQuery({ ...DEFAULT_BROWSE, all: true, sort: current.sort })}`);
```

and render `<FolderTree selectedId={current.all ? null : current.folderId} allSelected={current.all} onSelect={selectFolder} onSelectAll={selectAll} />`. Delete the now-unused `selectedFolder` variable. The Layout reads the search params on every route. On `/scan`, for example, nothing is highlighted except Root. That is acceptable, because the folder tree only ever navigates to `/`.

- [ ] **Step 7: Rewrite the Browse page header and listing**

In `frontend/src/pages/BrowsePage.tsx`:

```tsx
  const [searchParams, setSearchParams] = useSearchParams();
  const params = parseBrowseParams(searchParams);
  const update = (patch: Partial<BrowseParams>) =>
    setSearchParams(new URLSearchParams(browseQuery({ ...params, ...patch }).slice(1)));
  const badRange = invalidDateRange(params);
  const { data: folders } = useFolders();
  const { data: docs, isLoading, isError, error } = useDocuments(
    {
      folder: params.all ? null : (params.folderId ?? "root"),
      tagId: params.tagId,
      docType: params.docType,
      dateFrom: params.dateFrom,
      dateTo: params.dateTo,
      sort: params.sort,
    },
    !badRange,
  );
```

- Remove the `tagId`/`docType` `useState`s. The tag select uses `value={params.tagId ?? ""}` and `onChange={(e) => update({ tagId: e.target.value ? Number(e.target.value) : null })}`. The type select uses `update({ docType: e.target.value || null })`. The sort select uses `update({ sort: parseSort(e.target.value) })`.
- Add after the type select:

```tsx
        <Input
          type="date"
          aria-label="From date"
          className="w-40"
          value={params.dateFrom ?? ""}
          onChange={(e) => update({ dateFrom: e.target.value || null })}
        />
        <Input
          type="date"
          aria-label="To date"
          className="w-40"
          value={params.dateTo ?? ""}
          onChange={(e) => update({ dateTo: e.target.value || null })}
        />
        {(params.dateFrom || params.dateTo) && (
          <Button variant="ghost" onClick={() => update({ dateFrom: null, dateTo: null })}>
            Clear dates
          </Button>
        )}
```

- Wrap the header row in `flex flex-wrap` so the extra inputs fit on narrow screens.
- The heading reads `{params.all ? "All documents" : "Documents"}`.
- Below the header, before the loading line:

```tsx
      {badRange && <p className="mb-3 text-sm text-red-600">“From” date is after “To” date.</p>}
      {isError && (
        <p className="mb-3 text-sm text-red-600">{error instanceof ApiError ? error.message : "Could not load documents"}</p>
      )}
      {!params.all && (
        <>
          <Breadcrumb folders={folders ?? []} folderId={params.folderId} onNavigate={(id) => update({ folderId: id })} />
          <FolderTiles folders={folders ?? []} parentId={params.folderId} onOpen={(id) => update({ folderId: id })} />
        </>
      )}
```

- `UploadDialog initialFolderId={params.all ? null : params.folderId}`.
- Change the empty text to show only when there are also no folder tiles: `docs && docs.length === 0 && (params.all || childrenOf(folders ?? [], params.folderId).length === 0)`.

Imports: `Input`, `ApiError`, `useFolders`, `childrenOf`, `Breadcrumb`, `FolderTiles`, and `browseQuery, invalidDateRange, parseBrowseParams, type BrowseParams` from `@/lib/browseParams`. Remove the `useState` import if nothing else uses it (the file still uses it for `pendingFile`/`dragging`).

- [ ] **Step 8: Run all frontend checks**

Run: `cd frontend && npx tsc -b && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/browseParams.ts frontend/src/lib/browseParams.test.ts frontend/src/lib/sorting.ts frontend/src/lib/sorting.test.ts frontend/src/hooks/useDocuments.ts frontend/src/hooks/useDocuments.test.ts frontend/src/components/FolderTiles.tsx frontend/src/components/FolderTiles.test.tsx frontend/src/components/FolderTree.tsx frontend/src/components/Layout.tsx frontend/src/pages/BrowsePage.tsx
git commit -m "feat: file-system style browsing with breadcrumb, folder tiles and date filter"
```

---

## Task 17: Multi-select and bulk actions in Browse

**Files:**
- Create: `frontend/src/lib/selection.ts`, `frontend/src/lib/selection.test.ts`
- Create: `frontend/src/hooks/useSelection.ts`, `frontend/src/hooks/useSelection.test.ts`
- Create: `frontend/src/components/BulkActionBar.tsx`, `frontend/src/components/BulkActionBar.test.tsx`
- Modify: `frontend/src/components/DocumentCard.tsx`
- Modify: `frontend/src/pages/BrowsePage.tsx`

**Interfaces:**
- Consumes: `useBulkMove`, `useBulkDelete`, `browseViewKey` (Task 16). `FolderPicker`, `Dialog`.
- Produces: `toggleId(sel: ReadonlySet<string>, id: string): Set<string>`. `rangeSelect(sel: ReadonlySet<string>, order: string[], anchor: string | null, id: string): Set<string>`. `useSelection(order: string[], resetKey: string): { selected: Set<string>; toggle(id: string, shift: boolean): void; selectAll(): void; clear(): void }`. `<BulkActionBar count onSelectAll onMove(folderId) onDelete onClear busy? />`. `DocumentCard` props `selected?: boolean; selecting?: boolean; onToggleSelect?: (id: string, shift: boolean) => void`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/selection.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { rangeSelect, toggleId } from "./selection";

const ORDER = ["a", "b", "c", "d", "e"];

describe("selection", () => {
  it("toggles one id without mutating the input", () => {
    const empty = new Set<string>();
    const one = toggleId(empty, "a");
    expect([...one]).toEqual(["a"]);
    expect(empty.size).toBe(0);
    expect([...toggleId(one, "a")]).toEqual([]);
  });

  it("adds the range between anchor and id, both directions", () => {
    expect([...rangeSelect(new Set(["b"]), ORDER, "b", "d")].sort()).toEqual(["b", "c", "d"]);
    expect([...rangeSelect(new Set(), ORDER, "d", "b")].sort()).toEqual(["b", "c", "d"]);
  });

  it("falls back to a toggle without a usable anchor", () => {
    expect([...rangeSelect(new Set(), ORDER, null, "c")]).toEqual(["c"]);
    expect([...rangeSelect(new Set(), ORDER, "zz", "c")]).toEqual(["c"]);
  });
});
```

Create `frontend/src/hooks/useSelection.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useSelection } from "./useSelection";

it("selects, ranges, clears on view change and drops ids that left the list", () => {
  const { result, rerender } = renderHook(({ order, key }) => useSelection(order, key), {
    initialProps: { order: ["a", "b", "c"], key: "v1" },
  });
  act(() => result.current.toggle("a", false));
  act(() => result.current.toggle("c", true));
  expect([...result.current.selected].sort()).toEqual(["a", "b", "c"]);

  rerender({ order: ["a", "c"], key: "v1" }); // "b" was deleted
  expect([...result.current.selected].sort()).toEqual(["a", "c"]);

  rerender({ order: ["a", "c"], key: "v2" }); // folder or filter changed
  expect(result.current.selected.size).toBe(0);

  act(() => result.current.selectAll());
  expect(result.current.selected.size).toBe(2);
  act(() => result.current.clear());
  expect(result.current.selected.size).toBe(0);
});
```

Create `frontend/src/components/BulkActionBar.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BulkActionBar } from "./BulkActionBar";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderBar(overrides = {}) {
  const props = { count: 3, onSelectAll: vi.fn(), onMove: vi.fn(), onDelete: vi.fn(), onClear: vi.fn(), ...overrides };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <BulkActionBar {...props} />
    </QueryClientProvider>,
  );
  return props;
}

it("confirms before deleting", async () => {
  const props = renderBar();
  expect(screen.getByText("3 selected")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.getByRole("dialog", { name: "Delete 3 documents?" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Delete 3" }));
  expect(props.onDelete).toHaveBeenCalled();
});

it("moves to the root by default", async () => {
  const props = renderBar();
  await userEvent.click(screen.getByRole("button", { name: "Move…" }));
  await userEvent.click(screen.getByRole("button", { name: "Move here" }));
  expect(props.onMove).toHaveBeenCalledWith(null);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/selection.test.ts src/hooks/useSelection.test.ts src/components/BulkActionBar.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the selection logic**

Create `frontend/src/lib/selection.ts`:

```ts
export function toggleId(sel: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(sel);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Shift-click: add everything between the anchor and `id` (in list order). */
export function rangeSelect(
  sel: ReadonlySet<string>,
  order: string[],
  anchor: string | null,
  id: string,
): Set<string> {
  const from = anchor === null ? -1 : order.indexOf(anchor);
  const to = order.indexOf(id);
  if (from === -1 || to === -1) return toggleId(sel, id);
  const [lo, hi] = from < to ? [from, to] : [to, from];
  return new Set([...sel, ...order.slice(lo, hi + 1)]);
}
```

Create `frontend/src/hooks/useSelection.ts`:

```ts
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { rangeSelect, toggleId } from "@/lib/selection";

/** Selected document ids; cleared when `resetKey` (view + filters) changes. Callbacks are stable. */
export function useSelection(order: string[], resetKey: string) {
  const [raw, setRaw] = useState<Set<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const orderRef = useRef(order);
  orderRef.current = order;

  useEffect(() => {
    setRaw(new Set());
    anchor.current = null;
  }, [resetKey]);

  // ids no longer listed (deleted, moved away) never count as selected
  const orderKey = order.join(",");
  const selected = useMemo(() => {
    const visible = new Set(orderRef.current);
    return new Set([...raw].filter((id) => visible.has(id)));
  }, [raw, orderKey]);

  const toggle = useCallback((id: string, shift: boolean) => {
    setRaw((prev) => (shift ? rangeSelect(prev, orderRef.current, anchor.current, id) : toggleId(prev, id)));
    anchor.current = id;
  }, []);
  const selectAll = useCallback(() => setRaw(new Set(orderRef.current)), []);
  const clear = useCallback(() => {
    setRaw(new Set());
    anchor.current = null;
  }, []);

  return { selected, toggle, selectAll, clear };
}
```

If `npm run lint` flags the ref write during render, move `orderRef.current = order;` into a `useLayoutEffect(() => { orderRef.current = order; })` with no dependency list.

- [ ] **Step 4: Implement the action bar**

Create `frontend/src/components/BulkActionBar.tsx`:

```tsx
import { useState } from "react";
import { FolderPicker } from "@/components/FolderPicker";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

export function BulkActionBar({
  count,
  onSelectAll,
  onMove,
  onDelete,
  onClear,
  busy = false,
}: {
  count: number;
  onSelectAll: () => void;
  onMove: (folderId: number | null) => void;
  onDelete: () => void;
  onClear: () => void;
  busy?: boolean;
}) {
  const [dialog, setDialog] = useState<"move" | "delete" | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const noun = count === 1 ? "document" : "documents";

  return (
    <>
      <div className="sticky top-0 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-white p-2 shadow">
        <span className="px-2 text-sm font-medium">{count} selected</span>
        <Button variant="ghost" onClick={onSelectAll}>
          Select all
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => setDialog("move")}>
          Move…
        </Button>
        <Button variant="destructive" disabled={busy} onClick={() => setDialog("delete")}>
          Delete
        </Button>
        <Button variant="ghost" className="ml-auto" onClick={onClear}>
          Clear
        </Button>
      </div>
      <Dialog open={dialog === "move"} onClose={() => setDialog(null)} title={`Move ${count} ${noun}`}>
        <div className="space-y-3">
          <FolderPicker value={target} onChange={setTarget} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                onMove(target);
                setDialog(null);
              }}
            >
              Move here
            </Button>
          </div>
        </div>
      </Dialog>
      <Dialog open={dialog === "delete"} onClose={() => setDialog(null)} title={`Delete ${count} ${noun}?`}>
        <p className="mb-4 text-sm text-zinc-600">The files and their extracted text are removed permanently.</p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDialog(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onDelete();
              setDialog(null);
            }}
          >
            Delete {count}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 5: Selectable cards**

In `frontend/src/components/DocumentCard.tsx`, extend the props and add the checkbox as the first child of the outer `div`:

```tsx
export function DocumentCard({
  doc,
  onDelete,
  selected = false,
  selecting = false,
  onToggleSelect,
}: {
  doc: Document;
  onDelete: (id: string) => void;
  selected?: boolean;
  selecting?: boolean;
  onToggleSelect?: (id: string, shift: boolean) => void;
}) {
  return (
    <div className={cn("group relative rounded-lg border bg-white p-4 hover:shadow", selected ? "border-zinc-900 ring-1 ring-zinc-900" : "border-zinc-200")}>
      {onToggleSelect && (
        <input
          type="checkbox"
          aria-label={`Select ${doc.title}`}
          checked={selected}
          onChange={() => {}}
          onClick={(e) => onToggleSelect(doc.id, e.shiftKey)}
          className={cn("absolute top-2 left-2 z-10 h-4 w-4", selecting || selected ? "block" : "hidden group-hover:block")}
        />
      )}
```

Import `cn` from `@/lib/utils`. Add `pl-4` to the title row (`mb-2 flex items-center gap-2 pl-4`) so the checkbox does not cover the icon.

- [ ] **Step 6: Wire the Browse page**

In `frontend/src/pages/BrowsePage.tsx`:

```tsx
  const order = (docs ?? []).map((d) => d.id);
  const selection = useSelection(order, browseViewKey(params));
  const bulkMove = useBulkMove();
  const bulkDelete = useBulkDelete();
  const [bulkError, setBulkError] = useState<string | null>(null);
  const selectedIds = [...selection.selected];
  const reportBulk = (err: unknown) => setBulkError(err instanceof ApiError ? err.message : "Bulk action failed");

  const { clear } = selection;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && clear();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clear]);
```

Render, above the folder tiles:

```tsx
      {selection.selected.size > 0 && (
        <BulkActionBar
          count={selection.selected.size}
          busy={bulkMove.isPending || bulkDelete.isPending}
          onSelectAll={selection.selectAll}
          onClear={selection.clear}
          onMove={(folderId) =>
            bulkMove.mutate({ ids: selectedIds, folder_id: folderId }, { onSuccess: selection.clear, onError: reportBulk })
          }
          onDelete={() => bulkDelete.mutate(selectedIds, { onSuccess: selection.clear, onError: reportBulk })}
        />
      )}
      {bulkError && <p className="mb-3 text-sm text-red-600">{bulkError}</p>}
```

Pass to each card: `selected={selection.selected.has(doc.id)} selecting={selection.selected.size > 0} onToggleSelect={selection.toggle}`.

- [ ] **Step 7: Run all frontend checks**

Run: `cd frontend && npx tsc -b && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/selection.ts frontend/src/lib/selection.test.ts frontend/src/hooks/useSelection.ts frontend/src/hooks/useSelection.test.ts frontend/src/components/BulkActionBar.tsx frontend/src/components/BulkActionBar.test.tsx frontend/src/components/DocumentCard.tsx frontend/src/pages/BrowsePage.tsx
git commit -m "feat: multi-select documents to move or delete them together"
```

---

## Task 18: Search date filters (UI)

**Files:**
- Modify: `frontend/src/pages/SearchPage.tsx`
- Test: `frontend/src/pages/SearchPage.test.tsx`

**Interfaces:**
- Consumes: `SearchFilters.date_from` / `date_to` (Task 5).

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/pages/SearchPage.test.tsx`:

```tsx
it("sends the date range with the search", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/folders" || url === "/api/tags") return json(200, []);
    return json(200, { mode: "hybrid", results: [] });
  });
  renderPage();
  await userEvent.type(screen.getByPlaceholderText("Search your documents…"), "bolletta");
  await userEvent.type(screen.getByLabelText("From date"), "2026-01-01");
  await userEvent.type(screen.getByLabelText("To date"), "2026-03-31");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  const call = fetchMock.mock.calls.find(([url]) => url === "/api/search");
  expect(JSON.parse(call![1].body).filters).toMatchObject({ date_from: "2026-01-01", date_to: "2026-03-31" });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/SearchPage.test.tsx`
Expected: FAIL (`Unable to find a label with the text of: From date`).

- [ ] **Step 3: Implement**

In `frontend/src/pages/SearchPage.tsx`, add the state `const [dateFrom, setDateFrom] = useState("");` and `const [dateTo, setDateTo] = useState("");`. Add `date_from: dateFrom || null, date_to: dateTo || null` to `filters`. Add before the Search button:

```tsx
        <Input type="date" aria-label="From date" className="w-40" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        <Input type="date" aria-label="To date" className="w-40" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
```

The existing error box already shows the 422 message for an inverted range.

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npx vitest run src/pages/SearchPage.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/SearchPage.tsx frontend/src/pages/SearchPage.test.tsx
git commit -m "feat: date range filter on the search page"
```

---

## Task 19: Re-translate button on the Document page

**Files:**
- Modify: `frontend/src/lib/translation.ts`, `frontend/src/lib/translation.test.ts`
- Modify: `frontend/src/pages/DocumentPage.tsx`

**Interfaces:**
- Consumes: `POST /api/documents/{id}/retranslate` (Task 10). `Document.translatable` (Task 11).
- Produces: `canRetranslate(doc: Pick<Document, "status" | "translatable" | "translation_status">): boolean`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/translation.test.ts`, change the expected failed note to `"Translation failed — notification sent. Re-translate to retry."` and add:

```ts
describe("canRetranslate", () => {
  const base = { status: "ready", translatable: true, translation_status: "done" } as const;
  it("allows a ready, translatable document with no translation running", () => {
    expect(canRetranslate(base)).toBe(true);
    expect(canRetranslate({ ...base, translation_status: null })).toBe(true);
    expect(canRetranslate({ ...base, translation_status: "failed" })).toBe(true);
  });
  it("refuses while busy or when nothing needs translating", () => {
    expect(canRetranslate({ ...base, translation_status: "pending" })).toBe(false);
    expect(canRetranslate({ ...base, status: "processing" })).toBe(false);
    expect(canRetranslate({ ...base, translatable: false })).toBe(false);
  });
});
```

(Add `canRetranslate` to the import.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/translation.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement the helper**

In `frontend/src/lib/translation.ts`, change the failed note to `"Translation failed — notification sent. Re-translate to retry."` and append:

```ts
export function canRetranslate(
  doc: Pick<Document, "status" | "translatable" | "translation_status">,
): boolean {
  return doc.status === "ready" && doc.translatable && doc.translation_status !== "pending";
}
```

- [ ] **Step 4: Add the button**

In `frontend/src/pages/DocumentPage.tsx`, add the mutation:

```tsx
  const retranslate = useMutation({
    mutationFn: () => api.post<Document>(`/api/documents/${id}/retranslate`),
    onSuccess: (updated) => {
      qc.setQueryData(["document", id], updated);
      qc.invalidateQueries({ queryKey: ["document-text", id] });
    },
  });
```

In the sidebar, after the Re-process error line (inside the same `doc.doc_type !== "video"` block), add:

```tsx
            {doc.translatable && (
              <>
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={retranslate.isPending || !canRetranslate(doc)}
                  onClick={() => retranslate.mutate()}
                >
                  {retranslate.isPending ? "Starting…" : "Re-translate"}
                </Button>
                {retranslate.isError && (
                  <p className="text-xs text-red-600">
                    {retranslate.error instanceof ApiError ? retranslate.error.message : "Re-translate failed"}
                  </p>
                )}
              </>
            )}
```

Import `canRetranslate` from `@/lib/translation`. The existing effect that refetches the text when `translation_status` goes from `pending` to `done` already covers progress.

- [ ] **Step 5: Run all frontend checks**

Run: `cd frontend && npx tsc -b && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/translation.ts frontend/src/lib/translation.test.ts frontend/src/pages/DocumentPage.tsx
git commit -m "feat: re-translate button on the document page"
```

---

## Task 20: Final verification

**Files:** none new.

- [ ] **Step 1: Full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Full frontend checks and build**

Run: `cd frontend && npx tsc -b && npm run lint && npm test && npm run build`
Expected: all pass. The build writes `frontend/dist`.

- [ ] **Step 3: Migration round trip**

Run: `cd backend && uv run alembic downgrade e5a1c7d93b20 && uv run alembic upgrade head`
Expected: both succeed against the dev database (`docker compose up -d db`).

- [ ] **Step 4: Manual smoke test**

Start the API and worker as in the README, then in the browser:
1. Scan: pick a folder without subfolders (the popover closes). Scan 3 pages, drag page 3 to the front, uncheck "AI summary", type a new tag and press Enter, then save. The document has no summary, the new tag, and page order 3-1-2.
2. Browse: Root shows folder tiles and loose documents. Open a folder (the breadcrumb updates). Select 2 cards (shift-click a range), Move… to another folder (the counts on the tiles update), then select one card and Delete.
3. Set From/To dates. Reload: the filters survive.
4. Open a German document and click Re-translate. The status goes to pending and then done, and the translation text has no repeated overlap.
5. With `LLM_TPM_LIMIT=8000`, translate a long document. The worker log shows throttle waits and no failed jobs.
6. Upload: the OCR language list shows the server's installed languages.

- [ ] **Step 5: Commit any fixes found during the smoke test**

```bash
git add -A
git commit -m "fix: issues found during smoke test"
```

Skip this step if nothing changed.
