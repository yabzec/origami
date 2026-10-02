# AI Description, Office Documents, Folder Picker, Browse Sorting and Icons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copy the AI summary into the description with an "AI generated" label, give `.doc/.docx/.odt/.rtf` uploads a real PDF preview through headless LibreOffice, replace the flat folder `<select>` with a drill-down picker, add an order-by dropdown and SVG type icons to Browse, and simplify the scan carousel buttons.

**Architecture:** Backend (FastAPI + SQLModel + Postgres, worker job `process_document`) gains a data-only migration (summary → description), a `documents.preview_path` column, a `services/convert.py` wrapper around `soffice --convert-to pdf`, an office branch in the pipeline's text extraction, `?preview=1` on the file endpoint and a `sort` query param on `GET /api/documents`. Frontend (React 19 + react-router 8 + TanStack Query + Tailwind) adds pure helpers (`folderTree`, `docIcons`, `description`, `sorting`), a `FolderPicker` popover used in three places, a `DocTypeIcon` SVG component, and small edits to the document, browse and scan pages.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, LibreOffice (`soffice`, headless), pypdf, python-docx, pytest against real Postgres; React 19, TypeScript, Vite, react-router 8, TanStack Query 5, vitest + Testing Library, oxlint.

**Spec:** `docs/superpowers/specs/2026-10-03-office-docs-folder-picker-design.md`

## Global Constraints

- Backend tests run against real Postgres (`origami_test`, recreated per session by `backend/tests/conftest.py`, which runs `alembic upgrade head`). Only `app/services/llm.py` may be mocked (project rule; `llm_stub` fixture). LibreOffice tests use the real `/usr/bin/soffice`; failures are forced through the setting `SOFFICE_PATH` (env + `get_settings.cache_clear()`), never by mocking `subprocess`.
- Run backend tests from `backend/`: `uv run pytest -q`. Run frontend from `frontend/`: `npx vitest run`, `npm run lint`, `npm run build` (`tsc -b` with `noUnusedLocals` — remove imports that become unused).
- Baseline before this plan: backend 185 passed, frontend 51 passed. Every task ends with the full suite of the side it touched green.
- Alembic: current head `b7c4e2a91d05`. New revisions: `c3d81f0a6b27` (data-only, revises `b7c4e2a91d05`), then `d94a2b7e5c13` (`preview_path`, revises `c3d81f0a6b27`).
- Migration SQL, verbatim: upgrade `UPDATE documents SET description = summary WHERE (description IS NULL OR btrim(description) = '') AND summary IS NOT NULL AND btrim(summary) <> ''`; downgrade `UPDATE documents SET description = '' WHERE description = summary`.
- `OFFICE_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}`; all are `DocType.text`. Preview stored as `files/{id}.preview.pdf`; `file_path` stays the original upload. Conversion timeout 120 s; stderr trimmed to 500 characters. Setting `soffice_path` (env `SOFFICE_PATH`, default `"soffice"`).
- Conversion failure: `.docx` falls back to `extract_docx` (single page, no preview, warning logged); `.doc`, `.odt`, `.rtf` re-raise so the document ends `failed`.
- `sort` values exactly: `date_desc` (default), `date_asc`, `added_desc`, `title_asc`; anything else → 422. Labels exactly: "Document date (newest)", "Document date (oldest)", "Date added (newest)", "Title A–Z".
- UI copy exactly: label `AI generated`; picker closed state `(root)` and path joined with ` / `; picker entries `(root)`, `↑ Back`, `No subfolders`, `Done`.
- Icon colors: pdf `text-red-600`, word `text-blue-600`, text `text-zinc-500`, image `text-green-600`, video `text-purple-600`; 24px on cards; each SVG has a `<title>`.
- Scan × button: `type="button"`, `aria-label="Delete page"`, `text-base`, ~24px hit area (`h-6 w-6`), red, disabled while busy. The backend reorder endpoint stays.
- Commit messages: Conventional Commits, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never stage `deploy/origami.service` or `deploy/origami.sh` (user's uncommitted work). Do not edit `.env.example` (open in the user's editor; a swap file exists).

## Decisions taken while planning (flagged for review)

- **Re-process also deletes the metadata chunk.** The spec says existing metadata chunks are rebuilt only by re-process, but `reprocess_document` currently keeps the metadata chunk. Task 1 adds `ChunkSource.metadata` to the deleted sources so the pipeline rebuilds it with the new title-only rule; `test_reprocess_resets_and_enqueues` is updated accordingly.
- **Pure helpers live in `src/lib/`.** `iconKind` goes in `src/lib/docIcons.ts` and `isAiDescription` in `src/lib/description.ts` (not inside the `.tsx` components) so oxlint's `react/only-export-components` does not add warnings. `DocTypeIcon.tsx` imports `iconKind`.
- **The document page follows server-side description changes.** Today the form hydrates once per document id; an upload opened while processing would keep `""` and a later Save would erase the AI description. Task 7 adds `nextDescription(current, previousServer, nextServer)` so an unedited field follows the server value.
- **`FolderPicker` loads folders itself** (`useFolders()`), keeping the spec's props (`value`, `onChange`, `id`). `ScanSidebar` loses its `folders` prop.
- **Sort in the URL is preserved when switching folders**, and an unknown `?sort=` falls back to `date_desc` on the client (no 422 page from a stale bookmark). The `sort` param is only sent to the API when it is not the default.
- **Upload `accept` list:** the Browse file input has no `accept` attribute, so nothing changes there.
- **Bad-file conversion test** uses a truncated `.docx` zip: LibreOffice imports arbitrary garbage bytes as plain text and exits 0, but a truncated zip makes it exit 1 with "source file could not be loaded".
- **`preview_path` column type** is `sa.String()` (unbounded `VARCHAR`, same storage semantics as `TEXT` in Postgres) to match how SQLModel declares the other `str | None` columns.
- **Timeouts kill the whole process group** (`start_new_session=True` + `os.killpg`) so a hung `soffice.bin` child does not outlive the worker job. `office_to_pdf` takes an optional `timeout` argument (default 120 s) so the timeout path is testable without mocks.

## Review Focus

1. **Description opened while the document is still processing, then saved after the AI filled it** — Save must not erase the AI description. Test: `nextDescription` in Task 6; wiring in Task 7.
2. **LibreOffice missing on the host or not on the service `PATH`** — conversion raises `ConversionError("Office conversion could not start…")`; `.docx` still gets text, other formats end `failed` with that message (no stuck `processing`). Test in Task 2 (missing binary) and Task 3 (fallback / failed).
3. **Two office uploads converted at the same time** — both succeed (fresh LibreOffice profile per call). Test in Task 2.
4. **Pressing Esc in the folder picker inside the Upload dialog** — closes only the picker, not the dialog. Test in Task 8.
5. **Stale or hand-edited `?sort=` in a bookmark, and switching folders after choosing a sort** — falls back to the default sort without an error; the chosen sort survives folder navigation. Test in Task 9 (`parseSort`, `browseSearch`).

---

## File Structure

Backend
- `backend/app/worker/pipeline.py` — summary → description, title-only metadata chunk (Task 1); office branch `_extract_office` (Task 3).
- `backend/app/api/documents.py` — re-process clears AI description and metadata chunk (Task 1); `delete_document` removes the preview (Task 4); `sort` on list (Task 5).
- `backend/alembic/versions/c3d81f0a6b27_ai_summary_as_description.py` — data-only migration (Task 1).
- `backend/app/config.py` — `soffice_path` (Task 2).
- `backend/app/services/convert.py` — new: `OFFICE_EXTENSIONS`, `ConversionError`, `office_to_pdf` (Task 2).
- `backend/app/models/document.py` — `preview_path` (Task 3).
- `backend/alembic/versions/d94a2b7e5c13_document_preview_path.py` — column migration (Task 3).
- `backend/app/services/storage.py` — `store_preview` (Task 3).
- `backend/app/api/uploads.py` — `.doc`, `.odt`, `.rtf` in `EXTENSION_MAP` (Task 3).
- `backend/app/api/files.py` — `preview` query param (Task 4).
- Tests: `test_pipeline.py`, `test_reprocess.py`, `test_migrations.py` (Task 1); new `test_convert.py`, `helpers.py`, `conftest.py` (Task 2); `test_schema.py`, `test_storage.py`, `test_uploads.py`, `test_pipeline.py` (Task 3); `test_files_api.py`, `test_documents.py` (Task 4, Task 5).
- `README.md` — LibreOffice host requirement (Task 2).

Frontend
- `src/lib/folderTree.ts` — `folderPath`, `childrenOf`, `folderLabel` (Task 6).
- `src/lib/docIcons.ts` — new: `IconKind`, `iconKind` (Task 6).
- `src/lib/description.ts` — new: `isAiDescription`, `nextDescription` (Task 6).
- `src/lib/types.ts`, `src/lib/viewer.ts`, `src/lib/api.ts`, `src/pages/DocumentPage.tsx` — `preview_path`, `viewerKind(doc)`, `fileUrl` preview, AI label (Task 7).
- `src/components/FolderPicker.tsx` — new popover (Task 8); used by `src/components/UploadDialog.tsx`, `src/components/scan/ScanSidebar.tsx`, `src/pages/DocumentPage.tsx`, with `src/pages/ScanPage.tsx` dropping the `folders` prop (Task 8).
- `src/lib/sorting.ts` — new: sort options, `parseSort`, `browseSearch` (Task 9).
- `src/hooks/useDocuments.ts`, `src/pages/BrowsePage.tsx`, `src/components/Layout.tsx` — sort wiring (Task 9).
- `src/components/DocTypeIcon.tsx` — new SVG icons; `src/components/DocumentCard.tsx` uses it (Task 9).
- `src/components/scan/PageCarousel.tsx`, `src/pages/ScanPage.tsx` — carousel buttons (Task 10).

---

### Task 1: AI summary becomes the description

**Files:**
- Modify: `backend/app/worker/pipeline.py:202-274` (`_ensure_summary`, `_ensure_metadata_chunk`)
- Modify: `backend/app/api/documents.py:188-199` (`reprocess_document`)
- Create: `backend/alembic/versions/c3d81f0a6b27_ai_summary_as_description.py`
- Test: `backend/tests/test_pipeline.py`, `backend/tests/test_reprocess.py`, `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: after the pipeline, `doc.description == doc.summary` when the description was empty/whitespace; metadata chunk content is `doc.title` when `description` is empty or equals `summary`, else `f"{title}\n\n{description}"`. `POST /api/documents/{id}/reprocess` returns `description == ""` when it equalled the summary, and deletes content, summary, translation **and metadata** chunks. Alembic revision `c3d81f0a6b27` (revises `b7c4e2a91d05`).

- [ ] **Step 1: Write the failing pipeline tests** — append to `backend/tests/test_pipeline.py`:

```python
def test_summary_fills_empty_description(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.description == "Descrizione generata."
    metadata = chunks_by_source(session, doc)[ChunkSource.metadata]
    assert [c.content for c in metadata] == ["Brief"]  # summary has its own chunk; not embedded twice


def test_summary_fills_whitespace_description(session, pipeline_storage, llm_stub):
    doc = _text_doc(session, pipeline_storage)
    doc.description = "   "
    session.commit()
    doc = run(session, doc)
    assert doc.description == "Descrizione generata."


def test_summary_keeps_user_description(session, pipeline_storage, llm_stub):
    doc = _text_doc(session, pipeline_storage)
    doc.description = "Lettera del notaio"
    session.commit()
    doc = run(session, doc)
    assert doc.summary == "Descrizione generata."
    assert doc.description == "Lettera del notaio"
    metadata = chunks_by_source(session, doc)[ChunkSource.metadata]
    assert [c.content for c in metadata] == ["Brief\n\nLettera del notaio"]
```

- [ ] **Step 2: Write the failing re-process tests** — in `backend/tests/test_reprocess.py`, change the last two lines of `test_reprocess_resets_and_enqueues`:

```python
    sources = {c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))}
    assert sources == set()  # metadata is rebuilt by the pipeline too
    job = session.exec(select(Job).where(Job.type == "process_document")).one()
    assert job.payload == {"document_id": str(doc.id), "force_ocr": True}
```

and add after `test_reprocess_video_is_rejected`:

```python
def test_reprocess_clears_ai_description(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf, description="riassunto")  # == summary
    body = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).json()
    assert body["description"] == ""


def test_reprocess_keeps_edited_description(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf, description="Bolletta luce di marzo")
    body = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).json()
    assert body["description"] == "Bolletta luce di marzo"
```

- [ ] **Step 3: Write the failing migration test** — append to `backend/tests/test_migrations.py`:

```python
BEFORE_BACKFILL = "b7c4e2a91d05"
DESCRIPTION_BACKFILL = "c3d81f0a6b27"


def _backfill_descriptions(engine) -> dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT title, description FROM documents WHERE title LIKE 'Backfill%'")
        ).all()
    return {title: description for title, description in rows}


def test_summary_backfills_empty_description(engine):
    cfg = _cfg()
    command.downgrade(cfg, BEFORE_BACKFILL)
    try:
        with engine.begin() as conn:
            for title, description, summary in [
                ("BackfillEmpty", "", "Riassunto A."),
                ("BackfillBlank", "   ", "Riassunto B."),
                ("BackfillUser", "Mia nota", "Riassunto C."),
                ("BackfillNoSummary", "", None),
            ]:
                conn.execute(
                    text(
                        "INSERT INTO documents (id, title, description, summary, doc_type, "
                        "ocr_languages, ocr_enabled, status, created_at, updated_at) VALUES "
                        "(gen_random_uuid(), :title, :description, :summary, 'pdf', 'ita', true, "
                        "'ready', now(), now())"
                    ),
                    {"title": title, "description": description, "summary": summary},
                )
        command.upgrade(cfg, DESCRIPTION_BACKFILL)
        assert _backfill_descriptions(engine) == {
            "BackfillEmpty": "Riassunto A.",
            "BackfillBlank": "Riassunto B.",
            "BackfillUser": "Mia nota",
            "BackfillNoSummary": "",
        }
        command.downgrade(cfg, BEFORE_BACKFILL)
        assert _backfill_descriptions(engine) == {
            "BackfillEmpty": "",
            "BackfillBlank": "",
            "BackfillUser": "Mia nota",
            "BackfillNoSummary": "",
        }
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title LIKE 'Backfill%'"))
```

- [ ] **Step 4: Run, expect FAIL** — `cd backend && uv run pytest tests/test_pipeline.py tests/test_reprocess.py tests/test_migrations.py -q` → the three pipeline tests fail (description stays `""` / metadata has the description), the re-process tests fail (`{metadata}` != `set()`, description kept), the migration test fails (`Can't locate revision identified by 'c3d81f0a6b27'`).

- [ ] **Step 5: Implement the pipeline** — in `backend/app/worker/pipeline.py`, replace the tail of `_ensure_summary` (from `doc.summary = result.summary` to the end of the function):

```python
    doc.summary = result.summary
    doc.detected_language = result.language
    if not (doc.description or "").strip():
        doc.description = result.summary  # AI text; the UI labels it until the user edits it
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

and replace `_ensure_metadata_chunk`:

```python
def _ensure_metadata_chunk(session: Session, doc: Document) -> None:
    if _has_chunks(session, doc, ChunkSource.metadata):
        return
    if not doc.description or doc.description == doc.summary:
        content = doc.title  # an AI description is already embedded as the summary chunk
    else:
        content = f"{doc.title}\n\n{doc.description}"
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.metadata,
            content=content,
        )
    )
    session.commit()
```

- [ ] **Step 6: Implement re-process** — in `backend/app/api/documents.py`, inside `reprocess_document`, replace the block from `for chunk in session.exec(` through `doc.summary = None`:

```python
    for chunk in session.exec(
        select(Chunk).where(
            Chunk.document_id == doc.id,
            Chunk.source.in_(
                [ChunkSource.content, ChunkSource.summary, ChunkSource.translation, ChunkSource.metadata]
            ),
        )
    ):
        session.delete(chunk)
    doc.ocr_languages = body.ocr_languages
    doc.ocr_enabled = body.ocr_enabled
    if doc.summary and doc.description == doc.summary:
        doc.description = ""  # still the AI text: the new summary refills it; edited text is kept
    doc.summary = None
```

(the following lines `doc.detected_language = None` … stay unchanged).

- [ ] **Step 7: Create the migration** — `backend/alembic/versions/c3d81f0a6b27_ai_summary_as_description.py`:

```python
"""AI summary becomes the description (data only)

Revision ID: c3d81f0a6b27
Revises: b7c4e2a91d05
Create Date: 2026-10-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "c3d81f0a6b27"
down_revision: Union[str, Sequence[str], None] = "b7c4e2a91d05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Copy the AI summary into empty descriptions."""
    op.execute(
        "UPDATE documents SET description = summary "
        "WHERE (description IS NULL OR btrim(description) = '') "
        "AND summary IS NOT NULL AND btrim(summary) <> ''"
    )


def downgrade() -> None:
    """Clear descriptions that are still the AI summary."""
    op.execute("UPDATE documents SET description = '' WHERE description = summary")
```

- [ ] **Step 8: Run, expect PASS** — `uv run pytest tests/test_pipeline.py tests/test_reprocess.py tests/test_migrations.py -q`, then the full suite `uv run pytest -q` (expect 185 + 6 = 191 passed).

- [ ] **Step 9: Commit**

```bash
git add backend/app/worker/pipeline.py backend/app/api/documents.py backend/alembic/versions/c3d81f0a6b27_ai_summary_as_description.py backend/tests/test_pipeline.py backend/tests/test_reprocess.py backend/tests/test_migrations.py
git commit -m "feat: use the AI summary as the default description

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: LibreOffice conversion service

**Files:**
- Modify: `backend/app/config.py` (Settings)
- Create: `backend/app/services/convert.py`
- Modify: `backend/tests/helpers.py` (append), `backend/tests/conftest.py` (append)
- Create: `backend/tests/test_convert.py`
- Modify: `README.md:13-23`

**Interfaces:**
- Consumes: `get_settings()`.
- Produces: `Settings.soffice_path: str = "soffice"`; `app.services.convert.OFFICE_EXTENSIONS: set[str]`; `class ConversionError(Exception)`; `office_to_pdf(src: Path, timeout: float = CONVERT_TIMEOUT_SECONDS) -> bytes` (`CONVERT_TIMEOUT_SECONDS = 120.0`). Test helpers `make_docx(path: Path, pages: list[str]) -> Path` (page break between entries) and `make_odt(path: Path, text: str) -> Path`. Fixture `break_soffice` → callable `(path: str = "/bin/false") -> None` that points `SOFFICE_PATH` at `path` for the rest of the test.

- [ ] **Step 1: Add test helpers** — append to `backend/tests/helpers.py`:

```python
import zipfile

_ODT_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2"><office:body><office:text><text:p>{text}</text:p></office:text></office:body></office:document-content>"""

_ODT_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>"""


def make_docx(path: Path, pages: list[str]) -> Path:
    """A .docx with one paragraph per entry and a hard page break between entries."""
    import docx

    document = docx.Document()
    for index, text in enumerate(pages):
        if index:
            document.add_page_break()
        document.add_paragraph(text)
    document.save(path)
    return path


def make_odt(path: Path, text: str) -> Path:
    """A minimal single-paragraph OpenDocument text file (mimetype entry first, stored)."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("content.xml", _ODT_CONTENT.format(text=text), compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", _ODT_MANIFEST, compress_type=zipfile.ZIP_DEFLATED)
    return path
```

- [ ] **Step 2: Add the `break_soffice` fixture** — append to `backend/tests/conftest.py`:

```python
@pytest.fixture
def break_soffice(monkeypatch):
    """Call to point SOFFICE_PATH elsewhere (default /bin/false, a real binary that exits 1)."""
    from app.config import get_settings

    def _break(path: str = "/bin/false") -> None:
        monkeypatch.setenv("SOFFICE_PATH", path)
        get_settings.cache_clear()

    yield _break
    get_settings.cache_clear()  # monkeypatch restores the env afterwards; next call re-reads it
```

- [ ] **Step 3: Write the failing tests** — create `backend/tests/test_convert.py`:

```python
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.convert import OFFICE_EXTENSIONS, ConversionError, office_to_pdf
from tests.helpers import make_docx, make_odt


def test_office_extensions():
    assert OFFICE_EXTENSIONS == {".doc", ".docx", ".odt", ".rtf"}


def test_docx_converts_to_pdf(tmp_path):
    pdf = office_to_pdf(make_docx(tmp_path / "a.docx", ["CONTRATTO DI LOCAZIONE"]))
    assert pdf.startswith(b"%PDF")


def test_odt_converts_to_pdf(tmp_path):
    pdf = office_to_pdf(make_odt(tmp_path / "a.odt", "VERBALE ASSEMBLEA"))
    assert pdf.startswith(b"%PDF")


def test_corrupt_file_raises(tmp_path):
    good = make_docx(tmp_path / "good.docx", ["testo"])
    bad = tmp_path / "bad.docx"
    bad.write_bytes(good.read_bytes()[:300])  # truncated zip: LibreOffice cannot load it
    with pytest.raises(ConversionError, match="Office conversion failed"):
        office_to_pdf(bad)


def test_failing_binary_raises(tmp_path, break_soffice):
    break_soffice()  # /bin/false exits 1
    with pytest.raises(ConversionError, match=r"exit 1"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]))


def test_missing_binary_raises(tmp_path, break_soffice):
    break_soffice("/nonexistent/soffice")
    with pytest.raises(ConversionError, match="could not start"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]))


def test_timeout_raises(tmp_path, break_soffice):
    slow = tmp_path / "slow-soffice"
    slow.write_text("#!/bin/sh\nsleep 10\n")
    slow.chmod(0o755)
    break_soffice(str(slow))
    with pytest.raises(ConversionError, match="timed out"):
        office_to_pdf(make_docx(tmp_path / "a.docx", ["testo"]), timeout=0.5)


def test_concurrent_conversions_do_not_collide(tmp_path):
    sources = [make_docx(tmp_path / f"d{i}.docx", [f"DOCUMENTO {i}"]) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(office_to_pdf, sources))
    assert all(pdf.startswith(b"%PDF") for pdf in results)
```

- [ ] **Step 4: Run, expect FAIL** — `cd backend && uv run pytest tests/test_convert.py -q` → `ModuleNotFoundError: No module named 'app.services.convert'`.

- [ ] **Step 5: Add the setting** — in `backend/app/config.py`, add after `cors_origins: str = "*"`:

```python
    soffice_path: str = "soffice"  # LibreOffice binary used to convert office documents to PDF
```

- [ ] **Step 6: Implement the service** — create `backend/app/services/convert.py`:

```python
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from app.config import get_settings

OFFICE_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}
CONVERT_TIMEOUT_SECONDS = 120.0
STDERR_LIMIT = 500


class ConversionError(Exception):
    """LibreOffice could not turn the file into a PDF."""


def office_to_pdf(src: Path, timeout: float = CONVERT_TIMEOUT_SECONDS) -> bytes:
    """Convert an office document to PDF bytes with headless LibreOffice.

    Every call uses its own temp dir and LibreOffice profile, so concurrent conversions
    never fight over the profile lock.
    """
    with tempfile.TemporaryDirectory(prefix="origami-soffice-", ignore_cleanup_errors=True) as tmp:
        workdir = Path(tmp)
        cmd = [
            get_settings().soffice_path,
            "--headless",
            "--norestore",
            f"-env:UserInstallation={(workdir / 'profile').as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(src),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,  # own process group: a timeout kills soffice.bin too
            )
        except OSError as exc:
            raise ConversionError(f"Office conversion could not start: {exc}") from exc
        try:
            _, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ConversionError(f"Office conversion timed out after {timeout:g}s") from None
        detail = (stderr or "").strip()[:STDERR_LIMIT]
        if proc.returncode != 0:
            raise ConversionError(f"Office conversion failed (exit {proc.returncode}): {detail}")
        output = workdir / f"{src.stem}.pdf"
        if not output.is_file():
            raise ConversionError(f"Office conversion produced no PDF: {detail}")
        return output.read_bytes()
```

- [ ] **Step 7: Run, expect PASS** — `uv run pytest tests/test_convert.py -q` (8 passed), then `uv run pytest -q` (expect 199 passed).

- [ ] **Step 8: Document the host requirement** — in `README.md`, add after the `sane-utils` bullet:

```markdown
- `libreoffice-writer` (`soffice`) — converts `.doc`, `.docx`, `.odt` and `.rtf` uploads to PDF for the preview. Usually installed with LibreOffice; otherwise `sudo apt install libreoffice-writer`. Set `SOFFICE_PATH` if the binary is not on the service's `PATH`.
```

and add `soffice --version` as the last line inside the "Verify:" code block (after `scanimage --version`).

- [ ] **Step 9: Commit**

```bash
git add backend/app/config.py backend/app/services/convert.py backend/tests/helpers.py backend/tests/conftest.py backend/tests/test_convert.py README.md
git commit -m "feat: add LibreOffice office-to-PDF conversion service

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Office documents in the pipeline

**Files:**
- Modify: `backend/app/models/document.py:47` (after `file_path`)
- Create: `backend/alembic/versions/d94a2b7e5c13_document_preview_path.py`
- Modify: `backend/app/services/storage.py` (after `store_fileobj`)
- Modify: `backend/app/api/uploads.py:19-35` (`EXTENSION_MAP`)
- Modify: `backend/app/worker/pipeline.py:9-14,89-93` (imports, text branch) and add `_extract_office`
- Test: `backend/tests/test_schema.py`, `backend/tests/test_storage.py`, `backend/tests/test_uploads.py`, `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `office_to_pdf`, `ConversionError`, `OFFICE_EXTENSIONS` (Task 2); `make_docx`, `make_odt`, `break_soffice` (Task 2).
- Produces: `Document.preview_path: str | None` (serialized by the API as `preview_path`); Alembic revision `d94a2b7e5c13` (revises `c3d81f0a6b27`); `Storage.store_preview(document_id: uuid.UUID, data: bytes) -> str` returning `"files/{id}.preview.pdf"`; `EXTENSION_MAP` maps `.doc`, `.odt`, `.rtf` to `DocType.text`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_schema.py`:

```python
def test_document_preview_path_column(engine):
    cols = {c["name"]: c for c in inspect(engine).get_columns("documents")}
    assert cols["preview_path"]["nullable"] is True
```

Append to `backend/tests/test_storage.py`:

```python
def test_store_preview_writes_pdf_next_to_original(tmp_path):
    storage = Storage(tmp_path)
    doc_id = uuid.uuid4()
    rel = storage.store_preview(doc_id, b"%PDF-1.7")
    assert rel == f"files/{doc_id}.preview.pdf"
    assert storage.abs_path(rel).read_bytes() == b"%PDF-1.7"
```

Append to `backend/tests/test_uploads.py`:

```python
def test_upload_office_formats_are_text(auth_client, session, storage):
    for name in ("lettera.odt", "vecchio.doc", "nota.rtf", "contratto.docx"):
        assert upload(auth_client, name).json()["doc_type"] == DocType.text
```

Append to `backend/tests/test_pipeline.py`:

```python
def _office_doc(session, pipeline_storage, tmp_path, ext):
    from tests.helpers import make_docx, make_odt

    if ext == ".docx":
        src = make_docx(tmp_path / "src.docx", ["CONTRATTO DI LOCAZIONE", "Seconda pagina"])
    else:
        src = make_odt(tmp_path / "src.odt", "VERBALE ASSEMBLEA")
    doc = make_doc(session, doc_type=DocType.text, title="Contratto", original_filename=f"contratto{ext}")
    rel, _ = pipeline_storage.store_file(doc.id, ext, src.read_bytes())
    doc.file_path = rel
    session.commit()
    return doc


def test_docx_gets_pdf_preview_and_page_numbers(session, pipeline_storage, llm_stub, tmp_path):
    doc = _office_doc(session, pipeline_storage, tmp_path, ".docx")
    original = doc.file_path
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.file_path == original  # download keeps the .docx
    assert doc.preview_path == f"files/{doc.id}.preview.pdf"
    assert pipeline_storage.abs_path(doc.preview_path).read_bytes().startswith(b"%PDF")
    assert doc.page_count == 2
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert {c.page_number for c in content} == {1, 2}
    assert "CONTRATTO" in " ".join(c.content for c in content)


def test_docx_conversion_failure_falls_back_to_text(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    break_soffice()
    doc = run(session, _office_doc(session, pipeline_storage, tmp_path, ".docx"))
    assert doc.status == DocStatus.ready
    assert doc.preview_path is None
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert [c.page_number for c in content] == [None]
    assert "CONTRATTO" in content[0].content


def test_odt_conversion_failure_fails_document(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    from app.services.convert import ConversionError

    break_soffice()
    doc = _office_doc(session, pipeline_storage, tmp_path, ".odt")
    with pytest.raises(ConversionError):
        pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "Office conversion failed" in doc.error_message
    assert doc.preview_path is None


def test_reprocess_reuses_existing_preview(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    doc = run(session, _office_doc(session, pipeline_storage, tmp_path, ".odt"))
    assert doc.preview_path is not None
    for chunk in session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all():
        session.delete(chunk)
    doc.summary = None
    session.commit()

    break_soffice()  # a second conversion would now fail the .odt
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert [c.page_number for c in content] == [1]
    assert "VERBALE" in content[0].content
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_schema.py tests/test_storage.py tests/test_uploads.py tests/test_pipeline.py -q` → `KeyError: 'preview_path'`, `AttributeError: ... 'store_preview'`, `unsupported_type` for `.odt`, and the office pipeline tests fail because `Document` has no `preview_path` (the `.docx` is read with python-docx, the `.odt` as plain text).

- [ ] **Step 3: Add the model field** — in `backend/app/models/document.py`, add after `file_path: str | None = None  # relative to STORAGE_PATH`:

```python
    preview_path: str | None = None  # relative PDF used only for viewing (converted office documents)
```

- [ ] **Step 4: Create the migration** — `backend/alembic/versions/d94a2b7e5c13_document_preview_path.py`:

```python
"""documents.preview_path for converted office documents

Revision ID: d94a2b7e5c13
Revises: c3d81f0a6b27
Create Date: 2026-10-03 10:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d94a2b7e5c13"
down_revision: Union[str, Sequence[str], None] = "c3d81f0a6b27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("documents", sa.Column("preview_path", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("documents", "preview_path")
```

- [ ] **Step 5: Add `store_preview`** — in `backend/app/services/storage.py`, add after `store_fileobj`:

```python
    def store_preview(self, document_id: uuid.UUID, data: bytes) -> str:
        """Viewing-only PDF next to the original upload; returns its relative path."""
        name = f"{document_id}.preview.pdf"
        (self.files_dir / name).write_bytes(data)
        return f"files/{name}"
```

- [ ] **Step 6: Accept the new extensions** — in `backend/app/api/uploads.py`, replace the text entries of `EXTENSION_MAP`:

```python
    ".txt": DocType.text,
    ".md": DocType.text,
    ".doc": DocType.text,
    ".docx": DocType.text,
    ".odt": DocType.text,
    ".rtf": DocType.text,
```

- [ ] **Step 7: Implement the pipeline branch** — in `backend/app/worker/pipeline.py`, add the import after the `app.config` import line:

```python
from app.services.convert import OFFICE_EXTENSIONS, ConversionError, office_to_pdf
```

replace the text branch inside `_extract_content`:

```python
    if doc.doc_type == DocType.text:
        if path.suffix in OFFICE_EXTENSIONS:
            return _extract_office(session, doc, storage, path)
        return [(None, extract_text_file(path))]
```

and add after `_reocr_pdf`:

```python
def _extract_office(
    session: Session, doc: Document, storage: Storage, path: Path
) -> list[tuple[int | None, str]]:
    """Office files are viewed and indexed through a LibreOffice PDF; the original stays the download."""
    preview = storage.abs_path(doc.preview_path) if doc.preview_path else None
    if preview is None or not preview.is_file():
        try:
            pdf_bytes = office_to_pdf(path)
        except ConversionError:
            if path.suffix != ".docx":
                raise  # no other parser for .doc/.odt/.rtf: the document ends failed
            log.warning("Office conversion failed for %s; using python-docx text", doc.id, exc_info=True)
            doc.preview_path = None
            session.commit()
            return [(None, extract_docx(path))]
        doc.preview_path = storage.store_preview(doc.id, pdf_bytes)
        preview = storage.abs_path(doc.preview_path)
    pages = extract_pdf_text(preview)
    doc.page_count = len(pages)
    session.commit()
    return pages
```

Add `from pathlib import Path` to the top imports of `pipeline.py` (after `from datetime import ...`).

- [ ] **Step 8: Run, expect PASS** — `uv run pytest tests/test_schema.py tests/test_storage.py tests/test_uploads.py tests/test_pipeline.py -q`, then `uv run pytest -q` (expect 199 + 7 = 206 passed). `uv run alembic heads` shows `d94a2b7e5c13 (head)`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/document.py backend/alembic/versions/d94a2b7e5c13_document_preview_path.py backend/app/services/storage.py backend/app/api/uploads.py backend/app/worker/pipeline.py backend/tests/test_schema.py backend/tests/test_storage.py backend/tests/test_uploads.py backend/tests/test_pipeline.py
git commit -m "feat: convert office documents to a PDF preview in the pipeline

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Preview file endpoint and delete cleanup

**Files:**
- Modify: `backend/app/api/files.py`
- Modify: `backend/app/api/documents.py:149-159` (`delete_document`)
- Test: `backend/tests/test_files_api.py`, `backend/tests/test_documents.py`

**Interfaces:**
- Consumes: `Document.preview_path`, `Storage.store_preview` (Task 3).
- Produces: `GET /api/documents/{id}/file?preview=1` → the preview PDF (`application/pdf`, `inline`) when `preview_path` is set and on disk, else the normal file. `download=1` always serves the original `file_path` as attachment. `DELETE /api/documents/{id}` removes `file_path` and `preview_path`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_files_api.py`:

```python
def office_doc(session, storage):
    doc = seed_document(session, "Contratto", [{"content": "c"}], original_filename="contratto.docx")
    rel, _ = storage.store_file(doc.id, ".docx", b"PK original docx")
    doc.file_path = rel
    doc.preview_path = storage.store_preview(doc.id, b"%PDF-1.7 preview")
    session.commit()
    return doc


def test_preview_param_serves_preview_pdf_inline(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 preview"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")


def test_without_preview_param_serves_original(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.content == b"PK original docx"


def test_download_always_serves_original(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?download=1&preview=1")
    assert resp.status_code == 200
    assert resp.content == b"PK original docx"
    assert resp.headers["content-disposition"].startswith("attachment")
    assert "contratto.docx" in resp.headers["content-disposition"]


def test_preview_param_without_preview_serves_file(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 x"


def test_preview_missing_on_disk_falls_back_to_file(auth_client, session, storage):
    doc = office_doc(session, storage)
    storage.abs_path(doc.preview_path).unlink()
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"PK original docx"
```

Append to `backend/tests/test_documents.py`:

```python
def test_delete_removes_preview(auth_client, session, storage):
    doc = make_document(session, doc_type=DocType.text)
    rel, _ = storage.store_file(doc.id, ".docx", b"PK")
    doc.file_path = rel
    doc.preview_path = storage.store_preview(doc.id, b"%PDF")
    session.commit()
    original, preview = storage.abs_path(rel), storage.abs_path(doc.preview_path)

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not original.exists()
    assert not preview.exists()
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_files_api.py tests/test_documents.py -q` → `test_preview_param_serves_preview_pdf_inline` gets the `.docx` bytes; `test_delete_removes_preview` finds the preview still on disk.

- [ ] **Step 3: Implement the endpoint** — replace `backend/app/api/files.py` from the imports through the end of the file:

```python
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.deps import api_error, get_current_user_flexible
from app.api.documents import get_doc_or_404
from app.db import get_session
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents",
    tags=["files"],
    dependencies=[Depends(get_current_user_flexible)],
)

NO_CACHE = {"Cache-Control": "no-cache"}  # re-process may replace the file at the same path


@router.get("/{document_id}/file")
def document_file(
    document_id: uuid.UUID,
    download: bool = False,
    preview: bool = False,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    doc = get_doc_or_404(session, document_id)
    if preview and not download and doc.preview_path:
        preview_file = storage.abs_path(doc.preview_path)
        if preview_file.is_file():
            return FileResponse(
                preview_file,
                media_type="application/pdf",
                filename=f"{Path(doc.original_filename or preview_file.name).stem}.pdf",
                content_disposition_type="inline",
                headers=NO_CACHE,
            )
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
        headers=NO_CACHE,
    )
```

- [ ] **Step 4: Implement delete** — in `backend/app/api/documents.py`, replace `delete_document`'s body:

```python
    doc = get_doc_or_404(session, document_id)
    rel_paths = [doc.file_path, doc.preview_path]
    session.delete(doc)  # chunks and document_tags cascade via FK
    session.commit()
    for rel in rel_paths:
        storage.delete_document_file(rel)
```

- [ ] **Step 5: Run, expect PASS** — `uv run pytest tests/test_files_api.py tests/test_documents.py -q`, then `uv run pytest -q` (expect 212 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/files.py backend/app/api/documents.py backend/tests/test_files_api.py backend/tests/test_documents.py
git commit -m "feat: serve office previews and delete them with the document

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Browse sorting API

**Files:**
- Modify: `backend/app/api/documents.py:1-10,59-79` (imports, `list_documents`)
- Test: `backend/tests/test_documents.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `GET /api/documents?sort=date_desc|date_asc|added_desc|title_asc` (default `date_desc`); other values → 422.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_documents.py`:

```python
def _sort_fixture(session):
    from datetime import date, datetime, timezone

    utc = timezone.utc
    a = make_document(session, title="Beta", document_date=date(2026, 1, 10), created_at=datetime(2026, 3, 1, tzinfo=utc))
    b = make_document(session, title="alpha", document_date=date(2026, 2, 1), created_at=datetime(2026, 1, 1, tzinfo=utc))
    c = make_document(session, title="gamma", document_date=date(2026, 2, 1), created_at=datetime(2026, 2, 1, tzinfo=utc))
    return a, b, c


def _titles(auth_client, **params):
    resp = auth_client.get("/api/documents", params=params)
    assert resp.status_code == 200
    return [d["title"] for d in resp.json()]


def test_list_sort_orders(auth_client, session):
    _sort_fixture(session)
    # date ties (alpha/gamma share 2026-02-01) break on created_at
    assert _titles(auth_client) == ["gamma", "alpha", "Beta"]  # default date_desc
    assert _titles(auth_client, sort="date_desc") == ["gamma", "alpha", "Beta"]
    assert _titles(auth_client, sort="date_asc") == ["Beta", "alpha", "gamma"]
    assert _titles(auth_client, sort="added_desc") == ["Beta", "gamma", "alpha"]
    assert _titles(auth_client, sort="title_asc") == ["alpha", "Beta", "gamma"]  # case-insensitive


def test_list_bad_sort_is_422(auth_client, session):
    assert auth_client.get("/api/documents", params={"sort": "size"}).status_code == 422
```

- [ ] **Step 2: Run, expect FAIL** — `cd backend && uv run pytest tests/test_documents.py -q` → `test_list_sort_orders` gets created-at order; `test_list_bad_sort_is_422` gets 200.

- [ ] **Step 3: Implement** — in `backend/app/api/documents.py`, add `from sqlalchemy import func` after `from pydantic import BaseModel`, add above `list_documents`:

```python
DocumentSort = Literal["date_desc", "date_asc", "added_desc", "title_asc"]

SORT_ORDER = {
    "date_desc": (Document.document_date.desc(), Document.created_at.desc()),
    "date_asc": (Document.document_date.asc(), Document.created_at.asc()),
    "added_desc": (Document.created_at.desc(),),
    "title_asc": (func.lower(Document.title).asc(), Document.created_at.desc()),
}
```

and replace `list_documents`:

```python
@router.get("")
def list_documents(
    folder_id: int | None = None,
    tag_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    sort: DocumentSort = "date_desc",
    session: Session = Depends(get_session),
) -> list[dict]:
    query = select(Document)
    if folder_id is not None:
        query = query.where(Document.folder_id == folder_id)
    if doc_type is not None:
        query = query.where(Document.doc_type == doc_type)
    if status is not None:
        query = query.where(Document.status == status)
    if tag_id is not None:
        query = query.join(DocumentTag, DocumentTag.document_id == Document.id).where(
            DocumentTag.tag_id == tag_id
        )
    query = query.order_by(*SORT_ORDER[sort])
    return [serialize(session, d) for d in session.exec(query)]
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/test_documents.py -q`, then `uv run pytest -q` (expect 214 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/documents.py backend/tests/test_documents.py
git commit -m "feat: sort the document list by date, date added or title

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Frontend pure helpers

**Files:**
- Modify: `frontend/src/lib/folderTree.ts` (append), `frontend/src/lib/folderTree.test.ts` (append)
- Create: `frontend/src/lib/docIcons.ts`, `frontend/src/lib/docIcons.test.ts`
- Create: `frontend/src/lib/description.ts`, `frontend/src/lib/description.test.ts`

**Interfaces:**
- Consumes: `Folder`, `Document`, `DocType` from `src/lib/types.ts`.
- Produces: `folderPath(folders: Folder[], id: number | null): Folder[]` (root-to-node, cycle-safe); `childrenOf(folders: Folder[], parentId: number | null): Folder[]` (sorted by name; top level includes folders whose parent is unknown, as `buildFolderTree` does); `folderLabel(folders: Folder[], id: number | null): string` (`"(root)"`, path joined with `" / "`, or `"…"` while unknown). `type IconKind = "pdf" | "word" | "text" | "image" | "video"`; `iconKind(doc: Pick<Document, "doc_type" | "original_filename">): IconKind`. `isAiDescription(description: string, summary: string | null): boolean`; `nextDescription(current: string, previousServer: string | null, nextServer: string): string`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/lib/folderTree.test.ts` (and change the import line to `import { buildFolderTree, childrenOf, folderLabel, folderPath } from "./folderTree";`):

```ts
const nested = [folder(1, "Bollette"), folder(2, "2026", 1), folder(3, "Gennaio", 2), folder(4, "Assicurazioni")];

describe("folderPath", () => {
  it("returns the folders from the root to the node", () => {
    expect(folderPath(nested, 3).map((f) => f.name)).toEqual(["Bollette", "2026", "Gennaio"]);
  });

  it("is empty for null and unknown ids", () => {
    expect(folderPath(nested, null)).toEqual([]);
    expect(folderPath(nested, 99)).toEqual([]);
  });

  it("stops on a parent cycle", () => {
    const cyclic = [folder(1, "a", 2), folder(2, "b", 1)];
    expect(folderPath(cyclic, 1).map((f) => f.name)).toEqual(["b", "a"]);
  });
});

describe("childrenOf", () => {
  it("lists the top level sorted by name, including orphans", () => {
    expect(childrenOf([...nested, folder(5, "Zeta", 999)], null).map((f) => f.name)).toEqual([
      "Assicurazioni",
      "Bollette",
      "Zeta",
    ]);
  });

  it("lists the children of a folder", () => {
    expect(childrenOf(nested, 1).map((f) => f.name)).toEqual(["2026"]);
    expect(childrenOf(nested, 3)).toEqual([]);
  });
});

describe("folderLabel", () => {
  it("names the root, joins the path and marks unknown folders", () => {
    expect(folderLabel(nested, null)).toBe("(root)");
    expect(folderLabel(nested, 2)).toBe("Bollette / 2026");
    expect(folderLabel([], 2)).toBe("…");
  });
});
```

Create `frontend/src/lib/docIcons.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { iconKind } from "./docIcons";
import type { DocType } from "./types";

const doc = (doc_type: DocType, original_filename: string | null = null) => ({ doc_type, original_filename });

describe("iconKind", () => {
  it("uses the PDF icon for PDFs and scans", () => {
    expect(iconKind(doc("pdf", "a.pdf"))).toBe("pdf");
    expect(iconKind(doc("scan"))).toBe("pdf");
  });

  it("uses the Word icon for office text files, case-insensitively", () => {
    for (const name of ["a.doc", "a.docx", "a.odt", "a.rtf", "Contratto.DOCX"]) {
      expect(iconKind(doc("text", name))).toBe("word");
    }
  });

  it("uses the text icon for other text files", () => {
    expect(iconKind(doc("text", "note.md"))).toBe("text");
    expect(iconKind(doc("text", "readme.txt"))).toBe("text");
    expect(iconKind(doc("text", null))).toBe("text");
  });

  it("maps images and videos", () => {
    expect(iconKind(doc("image", "a.jpg"))).toBe("image");
    expect(iconKind(doc("video", "a.mp4"))).toBe("video");
  });
});
```

Create `frontend/src/lib/description.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { isAiDescription, nextDescription } from "./description";

describe("isAiDescription", () => {
  it("is true only while the text equals a non-empty summary", () => {
    expect(isAiDescription("Una bolletta.", "Una bolletta.")).toBe(true);
    expect(isAiDescription("Una bolletta!", "Una bolletta.")).toBe(false);
    expect(isAiDescription("", null)).toBe(false);
    expect(isAiDescription("", "")).toBe(false);
    expect(isAiDescription("  ", "  ")).toBe(false);
  });
});

describe("nextDescription", () => {
  it("follows the server while the field is unedited", () => {
    expect(nextDescription("", "", "Riassunto AI.")).toBe("Riassunto AI.");
  });

  it("keeps the user's edits", () => {
    expect(nextDescription("Mia nota", "", "Riassunto AI.")).toBe("Mia nota");
  });

  it("keeps the field before the first server value is known", () => {
    expect(nextDescription("Bozza", null, "Riassunto AI.")).toBe("Bozza");
  });
});
```

- [ ] **Step 2: Run, expect FAIL** — `cd frontend && npx vitest run src/lib` → missing exports / modules `./docIcons`, `./description`.

- [ ] **Step 3: Implement**

Append to `frontend/src/lib/folderTree.ts`:

```ts
export function folderPath(folders: Folder[], id: number | null): Folder[] {
  const byId = new Map(folders.map((f) => [f.id, f]));
  const path: Folder[] = [];
  const seen = new Set<number>();
  let current = id === null ? undefined : byId.get(id);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    path.unshift(current);
    current = current.parent_id === null ? undefined : byId.get(current.parent_id);
  }
  return path;
}

export function childrenOf(folders: Folder[], parentId: number | null): Folder[] {
  const ids = new Set(folders.map((f) => f.id));
  return folders
    .filter((f) =>
      parentId === null ? f.parent_id === null || !ids.has(f.parent_id) : f.parent_id === parentId,
    )
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function folderLabel(folders: Folder[], id: number | null): string {
  if (id === null) return "(root)";
  const path = folderPath(folders, id);
  return path.length > 0 ? path.map((f) => f.name).join(" / ") : "…";
}
```

Create `frontend/src/lib/docIcons.ts`:

```ts
import type { Document } from "./types";

export type IconKind = "pdf" | "word" | "text" | "image" | "video";

const WORD_EXTENSIONS = [".doc", ".docx", ".odt", ".rtf"];

export function iconKind(doc: Pick<Document, "doc_type" | "original_filename">): IconKind {
  switch (doc.doc_type) {
    case "pdf":
    case "scan":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text": {
      const name = (doc.original_filename ?? "").toLowerCase();
      return WORD_EXTENSIONS.some((ext) => name.endsWith(ext)) ? "word" : "text";
    }
  }
}
```

Create `frontend/src/lib/description.ts`:

```ts
/** The description is still the AI summary (derived; no stored flag). */
export function isAiDescription(description: string, summary: string | null): boolean {
  return summary !== null && summary.trim() !== "" && description === summary;
}

/** Value for the description field after the server value changed (pipeline or re-process). */
export function nextDescription(current: string, previousServer: string | null, nextServer: string): string {
  return current === previousServer ? nextServer : current;
}
```

- [ ] **Step 4: Run, expect PASS** — `npx vitest run && npm run lint && npm run build` (expect 51 + 14 = 65 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/folderTree.ts frontend/src/lib/folderTree.test.ts frontend/src/lib/docIcons.ts frontend/src/lib/docIcons.test.ts frontend/src/lib/description.ts frontend/src/lib/description.test.ts
git commit -m "feat: add folder path, icon kind and AI description helpers

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Document page — office preview and AI description label

**Files:**
- Modify: `frontend/src/lib/types.ts:112-134` (`Document`)
- Modify: `frontend/src/lib/viewer.ts`, `frontend/src/lib/viewer.test.ts`
- Modify: `frontend/src/lib/api.ts:54-55` (`fileUrl`), `frontend/src/lib/api.test.ts`
- Modify: `frontend/src/pages/DocumentPage.tsx:14-29,103-116,210-213,250-252`

**Interfaces:**
- Consumes: `isAiDescription`, `nextDescription` (Task 6); API field `preview_path` and `?preview=1` (Tasks 3–4).
- Produces: `Document.preview_path: string | null`; `viewerKind(doc: Pick<Document, "doc_type" | "preview_path">): "pdf" | "image" | "video" | "text"`; `fileUrl(documentId: string, opts?: { download?: boolean; preview?: boolean }): string`.

- [ ] **Step 1: Write the failing tests** — replace `frontend/src/lib/viewer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { viewerKind } from "./viewer";
import type { DocType } from "./types";

const doc = (doc_type: DocType, preview_path: string | null = null) => ({ doc_type, preview_path });

describe("viewerKind", () => {
  it("maps every doc type", () => {
    expect(viewerKind(doc("scan"))).toBe("pdf");
    expect(viewerKind(doc("pdf"))).toBe("pdf");
    expect(viewerKind(doc("image"))).toBe("image");
    expect(viewerKind(doc("video"))).toBe("video");
    expect(viewerKind(doc("text"))).toBe("text");
  });

  it("shows a text document with a preview as PDF", () => {
    expect(viewerKind(doc("text", "files/x.preview.pdf"))).toBe("pdf");
  });
});
```

Add inside `describe("api client")` in `frontend/src/lib/api.test.ts`:

```ts
  it("fileUrl adds preview=1 when requested", () => {
    setToken("tok");
    expect(fileUrl("doc-1", { preview: true })).toBe("/api/documents/doc-1/file?token=tok&preview=1");
  });
```

- [ ] **Step 2: Run, expect FAIL** — `cd frontend && npx vitest run src/lib/viewer.test.ts src/lib/api.test.ts` → `viewerKind` returns `undefined` for an object; `fileUrl` ignores `preview`.

- [ ] **Step 3: Implement the libs**

In `frontend/src/lib/types.ts`, add to `Document` after `file_path: string | null;`:

```ts
  preview_path: string | null;
```

Replace `frontend/src/lib/viewer.ts`:

```ts
import type { Document } from "./types";

export function viewerKind(doc: Pick<Document, "doc_type" | "preview_path">): "pdf" | "image" | "video" | "text" {
  switch (doc.doc_type) {
    case "scan":
    case "pdf":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text":
      return doc.preview_path ? "pdf" : "text"; // converted office documents
  }
}
```

Replace `fileUrl` in `frontend/src/lib/api.ts`:

```ts
export const fileUrl = (documentId: string, opts: { download?: boolean; preview?: boolean } = {}): string =>
  `/api/documents/${documentId}/file?token=${getToken() ?? ""}` +
  `${opts.download ? "&download=1" : ""}${opts.preview ? "&preview=1" : ""}`;
```

- [ ] **Step 4: Update the document page** — in `frontend/src/pages/DocumentPage.tsx`:

Add the import after the `@/lib/api` import:

```tsx
import { isAiDescription, nextDescription } from "@/lib/description";
```

Replace the first three lines of `Viewer`'s body (through `const src = …`):

```tsx
  const kind = viewerKind(doc);
  if (doc.status !== "ready" && kind !== "video")
    return <div className="flex h-96 items-center justify-center text-zinc-400">Processing…</div>;
  const src = fileUrl(doc.id, { preview: doc.preview_path !== null });
```

Replace the hydration block (`const hydratedForDocId = …` and its `useEffect`):

```tsx
  const hydratedForDocId = useRef<string | null>(null);
  const serverDescription = useRef<string | null>(null);

  useEffect(() => {
    if (!doc) return;
    if (hydratedForDocId.current !== doc.id) {
      setTitle(doc.title);
      setDescription(doc.description);
      setFolderId(doc.folder_id);
      setTagIds(doc.tags.map((t) => t.id));
      setDocumentDate(doc.document_date);
      setOcrLanguages(doc.ocr_languages);
      setOcrEnabled(doc.ocr_enabled);
      hydratedForDocId.current = doc.id;
    } else if (serverDescription.current !== doc.description) {
      // pipeline filled (or re-process cleared) the description: follow it unless the user edited the field
      const previous = serverDescription.current;
      setDescription((current) => nextDescription(current, previous, doc.description));
    }
    serverDescription.current = doc.description;
  }, [doc]);
```

Replace the Description field:

```tsx
        <div>
          <Label htmlFor="d-desc">Description</Label>
          <Textarea id="d-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
          {isAiDescription(description, doc.summary) && <p className="mt-1 text-xs text-zinc-400">AI generated</p>}
        </div>
```

Delete the sidebar summary box:

```tsx
        {doc.summary && (
          <div className="rounded border border-zinc-200 bg-zinc-50 p-2 text-xs text-zinc-600">{doc.summary}</div>
        )}
```

(The Text tab's "Summary:" block in `TextView` stays.)

- [ ] **Step 5: Run, expect PASS** — `npx vitest run && npm run lint && npm run build` (expect 67 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/viewer.ts frontend/src/lib/viewer.test.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts frontend/src/pages/DocumentPage.tsx
git commit -m "feat: office PDF preview and AI generated description label

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Folder picker and its three call sites

**Files:**
- Create: `frontend/src/components/FolderPicker.tsx`, `frontend/src/components/FolderPicker.test.tsx`
- Modify: `frontend/src/components/UploadDialog.tsx:1-14,30,77-91`
- Modify: `frontend/src/components/scan/ScanSidebar.tsx:1-8,22-46,72-86`
- Modify: `frontend/src/pages/ScanPage.tsx:9,36,257`
- Modify: `frontend/src/pages/DocumentPage.tsx` (imports, `useFolders`, Folder field)

**Interfaces:**
- Consumes: `folderPath`, `childrenOf`, `folderLabel` (Task 6); `useFolders()` (`src/hooks/useFolders.ts`).
- Produces: `FolderPicker({ value, onChange, id }: { value: number | null; onChange: (id: number | null) => void; id?: string })`. `ScanSidebar` no longer takes `folders`.

- [ ] **Step 1: Write the failing component test** — create `frontend/src/components/FolderPicker.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Dialog } from "@/components/ui/dialog";
import type { Folder } from "@/lib/types";
import { FolderPicker } from "./FolderPicker";

const FOLDERS: Folder[] = [
  { id: 1, name: "Bollette", parent_id: null, created_at: "2026-01-01" },
  { id: 2, name: "2026", parent_id: 1, created_at: "2026-01-01" },
  { id: 3, name: "Assicurazioni", parent_id: null, created_at: "2026-01-01" },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () => new Response(JSON.stringify(FOLDERS), { status: 200, headers: { "Content-Type": "application/json" } }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ initial, onDialogClose }: { initial: number | null; onDialogClose: () => void }) {
  const [value, setValue] = useState<number | null>(initial);
  return (
    <Dialog open onClose={onDialogClose} title="Upload">
      <label htmlFor="pick">Folder</label>
      <FolderPicker id="pick" value={value} onChange={setValue} />
    </Dialog>
  );
}

function renderPicker(initial: number | null = null, onDialogClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness initial={initial} onDialogClose={onDialogClose} />
    </QueryClientProvider>,
  );
  return { trigger: screen.getByLabelText("Folder"), onDialogClose };
}

it("drills down and selects the folder at every click", async () => {
  const { trigger } = renderPicker();
  expect(trigger).toHaveTextContent("(root)");
  await userEvent.click(trigger);
  await userEvent.click(await screen.findByRole("button", { name: "Bollette" }));
  expect(trigger).toHaveTextContent("Bollette");
  await userEvent.click(screen.getByRole("button", { name: "2026" }));
  expect(trigger).toHaveTextContent("Bollette / 2026");
  expect(screen.getByText("No subfolders")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /back/i }));
  expect(screen.getByRole("button", { name: "2026" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument();
});

it("opens at the selected folder's level and Esc closes only the picker", async () => {
  const { trigger, onDialogClose } = renderPicker(2);
  await waitFor(() => expect(trigger).toHaveTextContent("Bollette / 2026"));
  await userEvent.click(trigger);
  expect(screen.getByRole("button", { name: "2026" })).toBeInTheDocument(); // sibling level of the selection
  expect(screen.queryByRole("button", { name: "(root)" })).not.toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Choose folder" })).not.toBeInTheDocument();
  expect(onDialogClose).not.toHaveBeenCalled();
});

it("selects the root from the top level", async () => {
  const { trigger } = renderPicker(3);
  await waitFor(() => expect(trigger).toHaveTextContent("Assicurazioni"));
  await userEvent.click(trigger);
  await userEvent.click(screen.getByRole("button", { name: "(root)" }));
  expect(trigger).toHaveTextContent("(root)");
});
```

- [ ] **Step 2: Run, expect FAIL** — `cd frontend && npx vitest run src/components/FolderPicker.test.tsx` → cannot resolve `./FolderPicker`.

- [ ] **Step 3: Implement the picker** — create `frontend/src/components/FolderPicker.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { useFolders } from "@/hooks/useFolders";
import { childrenOf, folderLabel, folderPath } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

const itemClass = "flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm hover:bg-zinc-100";

export function FolderPicker({
  value,
  onChange,
  id,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  id?: string;
}) {
  const { data } = useFolders();
  const folders = data ?? [];
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<number | null>(null); // parent whose children are listed
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // capture phase on document: an enclosing Dialog (window listener) stays open
      setOpen(false);
    };
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const toggle = () => {
    if (open) {
      setOpen(false);
      return;
    }
    // browse the selected folder's level so its siblings are visible
    const selectedPath = folderPath(folders, value);
    setLevel(selectedPath.length >= 2 ? selectedPath[selectedPath.length - 2].id : null);
    setOpen(true);
  };

  const crumbs = folderPath(folders, level);
  const entries = childrenOf(folders, level);
  const back = () => setLevel(crumbs.length >= 2 ? crumbs[crumbs.length - 2].id : null);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        id={id}
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex h-9 w-full items-center justify-between rounded-md border border-zinc-300 bg-white px-2 text-left text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400"
      >
        <span className="truncate">{folderLabel(folders, value)}</span>
        <span aria-hidden="true" className="ml-2 text-zinc-400">
          ▾
        </span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Choose folder"
          className="absolute top-full right-0 left-0 z-20 mt-1 rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          <div className="mb-1 flex items-center gap-2 border-b border-zinc-100 pb-1 text-xs text-zinc-500">
            {level !== null && (
              <button type="button" onClick={back} className="shrink-0 text-zinc-700 hover:underline">
                ↑ Back
              </button>
            )}
            <span className="truncate">{["Top level", ...crumbs.map((f) => f.name)].join(" / ")}</span>
          </div>
          <ul className="max-h-60 overflow-y-auto">
            {level === null && (
              <li>
                <button
                  type="button"
                  onClick={() => onChange(null)}
                  className={cn(itemClass, value === null && "bg-zinc-100 font-medium")}
                >
                  (root)
                </button>
              </li>
            )}
            {entries.map((f) => (
              <li key={f.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(f.id);
                    setLevel(f.id);
                  }}
                  className={cn(itemClass, value === f.id && "bg-zinc-100 font-medium")}
                >
                  <span className="truncate">{f.name}</span>
                  {childrenOf(folders, f.id).length > 0 && (
                    <span aria-hidden="true" className="text-zinc-400">
                      ›
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
          {entries.length === 0 && <p className="px-2 py-1 text-sm text-zinc-400">No subfolders</p>}
          <div className="mt-1 flex justify-end border-t border-zinc-100 pt-1">
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded px-2 py-1 text-sm font-medium hover:bg-zinc-100"
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run, expect PASS** — `npx vitest run src/components/FolderPicker.test.tsx` (3 passed).

- [ ] **Step 5: Use it in the Upload dialog** — in `frontend/src/components/UploadDialog.tsx`: delete the imports `import { Select } from "@/components/ui/select";` and `import { useFolders } from "@/hooks/useFolders";`, add `import { FolderPicker } from "@/components/FolderPicker";`, delete `const { data: folders } = useFolders();`, and replace the folder field:

```tsx
        <div>
          <Label htmlFor="up-folder">Folder</Label>
          <FolderPicker id="up-folder" value={folderId} onChange={setFolderId} />
        </div>
```

- [ ] **Step 6: Use it in the scan sidebar** — in `frontend/src/components/scan/ScanSidebar.tsx`: replace `import { Select } from "@/components/ui/select";` with `import { FolderPicker } from "@/components/FolderPicker";`, change `import type { Folder, Tag } from "@/lib/types";` to `import type { Tag } from "@/lib/types";`, remove `folders,` from the destructured props and `folders: Folder[];` from the props type, and replace the folder field:

```tsx
      <div>
        <Label htmlFor="scan-folder">Folder</Label>
        <FolderPicker id="scan-folder" value={fields.folderId} onChange={(folderId) => onChange({ folderId })} />
      </div>
```

In `frontend/src/pages/ScanPage.tsx`: delete `import { useFolders } from "@/hooks/useFolders";`, delete `const { data: folders } = useFolders();`, and delete the `folders={folders ?? []}` line from `<ScanSidebar … />`.

- [ ] **Step 7: Use it on the document page** — in `frontend/src/pages/DocumentPage.tsx`: delete `import { Select } from "@/components/ui/select";` and `import { useFolders } from "@/hooks/useFolders";`, add `import { FolderPicker } from "@/components/FolderPicker";`, delete `const { data: folders } = useFolders();`, and replace the folder field:

```tsx
        <div>
          <Label htmlFor="d-folder">Folder</Label>
          <FolderPicker id="d-folder" value={folderId} onChange={setFolderId} />
        </div>
```

- [ ] **Step 8: Run, expect PASS** — `npx vitest run && npm run lint && npm run build` (expect 70 tests; build must not report unused imports).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/FolderPicker.tsx frontend/src/components/FolderPicker.test.tsx frontend/src/components/UploadDialog.tsx frontend/src/components/scan/ScanSidebar.tsx frontend/src/pages/ScanPage.tsx frontend/src/pages/DocumentPage.tsx
git commit -m "feat: drill-down folder picker for upload, scan and document pages

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Browse sort dropdown and type icons

**Files:**
- Create: `frontend/src/lib/sorting.ts`, `frontend/src/lib/sorting.test.ts`
- Modify: `frontend/src/hooks/useDocuments.ts:5-18`, `frontend/src/hooks/useDocuments.test.ts`
- Modify: `frontend/src/pages/BrowsePage.tsx`
- Modify: `frontend/src/components/Layout.tsx:1-23`
- Create: `frontend/src/components/DocTypeIcon.tsx`
- Modify: `frontend/src/components/DocumentCard.tsx:1-26`

**Interfaces:**
- Consumes: `iconKind`, `IconKind` (Task 6); API `sort` (Task 5).
- Produces: `type DocumentSort = "date_desc" | "date_asc" | "added_desc" | "title_asc"`; `DEFAULT_SORT`; `SORT_OPTIONS: readonly { value: DocumentSort; label: string }[]`; `parseSort(raw: string | null): DocumentSort`; `browseSearch(folderId: number | null, sort: DocumentSort): string`; `DocumentFilters.sort?: DocumentSort`; `DocTypeIcon({ doc, className }: { doc: Pick<Document, "doc_type" | "original_filename">; className?: string })`.

- [ ] **Step 1: Write the failing tests** — create `frontend/src/lib/sorting.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { browseSearch, parseSort, SORT_OPTIONS } from "./sorting";

describe("parseSort", () => {
  it("accepts known values and falls back to document date (newest)", () => {
    expect(parseSort("title_asc")).toBe("title_asc");
    expect(parseSort(null)).toBe("date_desc");
    expect(parseSort("bogus")).toBe("date_desc");
  });
});

describe("browseSearch", () => {
  it("keeps the folder and a non-default sort", () => {
    expect(browseSearch(null, "date_desc")).toBe("");
    expect(browseSearch(3, "date_desc")).toBe("?folder=3");
    expect(browseSearch(null, "added_desc")).toBe("?sort=added_desc");
    expect(browseSearch(3, "title_asc")).toBe("?folder=3&sort=title_asc");
  });
});

describe("SORT_OPTIONS", () => {
  it("lists the four orders with their labels", () => {
    expect(SORT_OPTIONS.map((o) => o.label)).toEqual([
      "Document date (newest)",
      "Document date (oldest)",
      "Date added (newest)",
      "Title A–Z",
    ]);
  });
});
```

Add inside `describe("documentsQueryString")` in `frontend/src/hooks/useDocuments.test.ts`:

```ts
  it("passes a non-default sort", () => {
    expect(documentsQueryString({ folderId: null, tagId: null, docType: null, sort: "title_asc" })).toBe(
      "?sort=title_asc",
    );
    expect(documentsQueryString({ folderId: 3, tagId: null, docType: null, sort: "date_desc" })).toBe("?folder_id=3");
  });
```

- [ ] **Step 2: Run, expect FAIL** — `cd frontend && npx vitest run src/lib/sorting.test.ts src/hooks/useDocuments.test.ts` → cannot resolve `./sorting`; the query string ignores `sort` (also a type error on `sort` that vitest does not check).

- [ ] **Step 3: Implement sorting** — create `frontend/src/lib/sorting.ts`:

```ts
export type DocumentSort = "date_desc" | "date_asc" | "added_desc" | "title_asc";

export const DEFAULT_SORT: DocumentSort = "date_desc";

export const SORT_OPTIONS: readonly { value: DocumentSort; label: string }[] = [
  { value: "date_desc", label: "Document date (newest)" },
  { value: "date_asc", label: "Document date (oldest)" },
  { value: "added_desc", label: "Date added (newest)" },
  { value: "title_asc", label: "Title A–Z" },
];

/** Sort from the URL; unknown values (old bookmarks, typos) fall back to the default. */
export function parseSort(raw: string | null): DocumentSort {
  return SORT_OPTIONS.find((o) => o.value === raw)?.value ?? DEFAULT_SORT;
}

/** Browse page query string: `?folder=` and `?sort=` (omitted when default). */
export function browseSearch(folderId: number | null, sort: DocumentSort): string {
  const params = new URLSearchParams();
  if (folderId !== null) params.set("folder", String(folderId));
  if (sort !== DEFAULT_SORT) params.set("sort", sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
```

In `frontend/src/hooks/useDocuments.ts`, add `import { DEFAULT_SORT, type DocumentSort } from "@/lib/sorting";` and replace `DocumentFilters` and `documentsQueryString`:

```ts
export interface DocumentFilters {
  folderId: number | null;
  tagId: number | null;
  docType: string | null;
  sort?: DocumentSort;
}

export function documentsQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.folderId !== null) params.set("folder_id", String(filters.folderId));
  if (filters.tagId !== null) params.set("tag_id", String(filters.tagId));
  if (filters.docType !== null) params.set("doc_type", filters.docType);
  if (filters.sort && filters.sort !== DEFAULT_SORT) params.set("sort", filters.sort);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
```

- [ ] **Step 4: Run, expect PASS** — `npx vitest run src/lib/sorting.test.ts src/hooks/useDocuments.test.ts`.

- [ ] **Step 5: Wire the Browse page** — in `frontend/src/pages/BrowsePage.tsx`, add `import { browseSearch, parseSort, SORT_OPTIONS } from "@/lib/sorting";`, replace the first lines of `BrowsePage`:

```tsx
  const [searchParams, setSearchParams] = useSearchParams();
  const folderId = searchParams.get("folder") ? Number(searchParams.get("folder")) : null;
  const sort = parseSort(searchParams.get("sort"));
  const [tagId, setTagId] = useState<number | null>(null);
  const [docType, setDocType] = useState<string | null>(null);
  const { data: docs, isLoading } = useDocuments({ folderId, tagId, docType, sort });
```

and insert the order-by select right after the `<h2 className="flex-1 text-lg font-semibold">Documents</h2>` line:

```tsx
        <Select
          className="w-52"
          aria-label="Order by"
          value={sort}
          onChange={(e) => setSearchParams(new URLSearchParams(browseSearch(folderId, parseSort(e.target.value))))}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
```

- [ ] **Step 6: Keep the sort when switching folders** — in `frontend/src/components/Layout.tsx`, add `import { browseSearch, parseSort } from "@/lib/sorting";` and replace `selectFolder`:

```tsx
  const selectFolder = (id: number | null) => {
    navigate(`/${browseSearch(id, parseSort(searchParams.get("sort")))}`);
  };
```

- [ ] **Step 7: Create the icon component** — `frontend/src/components/DocTypeIcon.tsx`:

```tsx
import type { ReactNode } from "react";
import { iconKind, type IconKind } from "@/lib/docIcons";
import { cn } from "@/lib/utils";
import type { Document } from "@/lib/types";

const PAGE = (
  <>
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
    <path d="M14 3v5h5" />
  </>
);

const ICONS: Record<IconKind, { color: string; title: string; body: ReactNode }> = {
  pdf: {
    color: "text-red-600",
    title: "PDF",
    body: (
      <>
        {PAGE}
        <text x="12" y="17.5" textAnchor="middle" fontSize="5.5" fontWeight="700" fill="currentColor" stroke="none">
          PDF
        </text>
      </>
    ),
  },
  word: {
    color: "text-blue-600",
    title: "Word document",
    body: (
      <>
        {PAGE}
        <path d="m8 12 1.5 6 2.5-4.5 2.5 4.5 1.5-6" />
      </>
    ),
  },
  text: {
    color: "text-zinc-500",
    title: "Text",
    body: (
      <>
        {PAGE}
        <path d="M9 13h6M9 17h6M9 9h2" />
      </>
    ),
  },
  image: {
    color: "text-green-600",
    title: "Image",
    body: (
      <>
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <circle cx="8.5" cy="9.5" r="1.5" />
        <path d="m21 16-5-5-9 9" />
      </>
    ),
  },
  video: {
    color: "text-purple-600",
    title: "Video",
    body: (
      <>
        <rect x="2" y="6" width="14" height="12" rx="2" />
        <path d="m16 10 6-3v10l-6-3z" />
      </>
    ),
  },
};

export function DocTypeIcon({
  doc,
  className,
}: {
  doc: Pick<Document, "doc_type" | "original_filename">;
  className?: string;
}) {
  const { color, title, body } = ICONS[iconKind(doc)];
  return (
    <svg
      viewBox="0 0 24 24"
      width={24}
      height={24}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label={title}
      className={cn("shrink-0", color, className)}
    >
      <title>{title}</title>
      {body}
    </svg>
  );
}
```

- [ ] **Step 8: Use it on cards** — in `frontend/src/components/DocumentCard.tsx`, add `import { DocTypeIcon } from "@/components/DocTypeIcon";`, delete the whole `TYPE_ICONS` constant, and replace `<span>{TYPE_ICONS[doc.doc_type]}</span>` with:

```tsx
          <DocTypeIcon doc={doc} />
```

- [ ] **Step 9: Run, expect PASS** — `npx vitest run && npm run lint && npm run build` (expect 74 tests).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/lib/sorting.ts frontend/src/lib/sorting.test.ts frontend/src/hooks/useDocuments.ts frontend/src/hooks/useDocuments.test.ts frontend/src/pages/BrowsePage.tsx frontend/src/components/Layout.tsx frontend/src/components/DocTypeIcon.tsx frontend/src/components/DocumentCard.tsx
git commit -m "feat: browse order-by dropdown and SVG document type icons

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Scan carousel buttons

**Files:**
- Modify: `frontend/src/components/scan/PageCarousel.tsx`
- Modify: `frontend/src/pages/ScanPage.tsx:147-162,242-249`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PageCarousel({ pages, selectedPageId, disabled, onSelect, onDelete })` (no `onMove`). The reducer action `PAGES_REORDERED` in `src/lib/scanWizard.ts` stays (still covered by its tests; unused by the UI, like the backend reorder endpoint).

- [ ] **Step 1: Replace the carousel** — `frontend/src/components/scan/PageCarousel.tsx`:

```tsx
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { cn } from "@/lib/utils";
import type { ScanPageInfo } from "@/lib/types";

function Thumb({
  page,
  selected,
  disabled,
  onSelect,
  onDelete,
}: {
  page: ScanPageInfo;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
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
}: {
  pages: ScanPageInfo[];
  selectedPageId: number | null;
  disabled: boolean;
  onSelect: (pageId: number) => void;
  onDelete: (pageId: number) => void;
}) {
  if (pages.length === 0) return null;
  return (
    <div className="flex gap-3 overflow-x-auto pb-2">
      {pages.map((page) => (
        <Thumb
          key={page.id}
          page={page}
          selected={page.id === selectedPageId}
          disabled={disabled}
          onSelect={() => onSelect(page.id)}
          onDelete={() => onDelete(page.id)}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Drop the reorder call from the scan page** — in `frontend/src/pages/ScanPage.tsx`, delete the whole `const movePage = async (index: number, direction: -1 | 1) => { … };` function and the `onMove={movePage}` line from `<PageCarousel … />`. (`ScanPageInfo` stays imported: `scanPage` still uses it.)

- [ ] **Step 3: Run, expect PASS** — `cd frontend && npx vitest run && npm run lint && npm run build` (74 tests; no unused-symbol errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/scan/PageCarousel.tsx frontend/src/pages/ScanPage.tsx
git commit -m "feat: drop scan page reorder arrows and enlarge the delete button

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: End-to-end verification

**Files:** none (verification only; fix-forward commits if something fails).

- [ ] **Step 1: Full suites** — `cd backend && uv run pytest -q` (expect 214 passed, 0 failures); `cd ../frontend && npx vitest run && npm run lint && npm run build` (expect 74 passed; lint shows only the three pre-existing `only-export-components` warnings).

- [ ] **Step 2: Alembic head** — `cd backend && uv run alembic heads` shows `d94a2b7e5c13 (head)` (single head).

- [ ] **Step 3: Rebuild and restart** — the frontend build from Step 1 already wrote `frontend/dist`. Ask the user to run `! sudo systemctl restart origami` (the unit runs `deploy/origami.sh`, which applies migrations with `alembic upgrade head` before starting the API and worker). Afterwards, `cd backend && uv run alembic current` shows `d94a2b7e5c13 (head)`, and existing documents with a summary and an empty description now show the summary as description.

- [ ] **Step 4: Manual scenarios**
  1. Upload a `.docx` with two pages and an `.odt` → status `ready`; Preview tab shows the PDF rendering with the original layout; Text tab shows "Page 1 / Page 2"; Download saves the original `.docx` / `.odt` with its name.
  2. Upload a `.txt` → Preview still shows the text view.
  3. Open a newly uploaded document while it is processing; wait for `ready` → the Description field fills with the summary and shows "AI generated"; type one character → the label disappears immediately; Save, reload → the edited text persists, no label.
  4. Re-process a document whose description is still AI text → the description empties, then refills with the new summary. Re-process one with an edited description → the edit stays.
  5. Folder picker in the Upload dialog, the Scan sidebar and the document sidebar: drill into a nested folder (every click selects; closed button shows `Parent / Child`), "↑ Back", "(root)", "No subfolders", Done, click outside, and Esc — in the Upload dialog Esc closes only the picker.
  6. Browse: pick "Title A–Z", reload → order and dropdown persist (`?sort=title_asc`); click another folder in the sidebar → sort is kept; edit the URL to `?sort=bogus` → page loads with "Document date (newest)".
  7. Browse cards: PDF and scan show the red PDF icon, `.docx`/`.odt` the blue Word icon, `.md` the grey text icon, images green, videos purple; hovering an icon shows its title.
  8. Scan page: thumbnails have no ← / →; × is larger, red, disabled while scanning or saving, and deletes the page.

- [ ] **Step 5: Report** — summarize results to the user with exact test counts; list anything that failed or was skipped.
