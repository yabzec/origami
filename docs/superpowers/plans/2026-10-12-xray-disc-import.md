# X-ray Disc Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import a hospital X-ray disc (IHE PDI: DICOM files, README, report PDF) from the browser into Origami as one document per study: an image PDF built from the DICOM files plus the original report as an attachment, stored together in a bundle directory.

**Architecture:**
- **Upload:** the browser filters the picked disc folder and uploads the useful files in batches to a staging dir (`tmp/disc_imports/<id>/`). `services/dicom_parse.py` builds a preview: studies, images, report candidates and proposed metadata.
- **Commit:** creates pending documents (`extra_metadata` marks them as studies) and queues `process_document` with the chosen images and reports.
- **Worker:** renders the DICOM files (`dicom_render.py`), builds the PDF (`study_pdf.py`) and writes either a flat file or a bundle dir with attachments (`tree_sync.write_bundle`). It indexes only the report text.
- **Notes:** a new `notes` field and a `refresh_metadata` job keep the metadata chunk current after edits.

**Tech Stack:**
- Backend: Python 3.13, FastAPI, SQLModel, Postgres + pgvector, pydicom 3, pylibjpeg (+libjpeg, +openjpeg), numpy, Pillow, pypdf, pytest on real Postgres.
- Frontend: React 19, TanStack Query, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-12-xray-disc-import-design.md`

## Global Constraints

**Process**
- Work on branch `feat/xray-disc-import` (already created; the spec commit is on it). Merge to local `main` with `--no-ff` at the end. Never push unless the user asks.
- Commands:
  - Backend tests: `cd /opt/origami/backend && uv run pytest`
  - Frontend tests: `cd /opt/origami/frontend && npx vitest run`
  - Typecheck: `npx tsc -b`
  - Lint: `npx oxlint`
- Code, comments, commits and README in normal English prose, matching the surrounding style.

**Data**
- No real patient data anywhere in the repo or tests. DICOM fixtures are generated in tests with pydicom (`ROSSI^MARIO`, `Ospedale Test`). Never copy files from `/home/matteo/Scaricati/disc` into the repo.

**Import behaviour**
- Import dialog defaults: OCR **off**, translation **off**, AI summary **on**. Only the import dialog uses these; normal upload defaults do not change.
- AI summary of a study document: the input is the report text plus the study metadata text. Never vision on X-ray images.
- Bundle dir only when a document has at least one attachment. A study without a report is a flat PDF.
- Attachment file name: `Report.pdf`; duplicates get ` (n)`.

**Rendering**
- Image pages: JPEG quality 92, 150 dpi, long side capped at 4096 px.
- Cover page: A4 at 150 dpi (1240×1754).
- Thumbnails: 256 px JPEG, quality 80.

**Upload and staging**
- Client per-file cap: 90 MB. Upload batches: at most 50 MB and 200 files each. Cloudflare rejects request bodies over 100 MB.
- Staging TTL: 24 hours.
- Staging subdirs `.thumbs/` and `.extracted/` are internal. Client paths with a segment starting with `.` are rejected.

## Spec clarifications (decided while planning)

**Staging and upload**
- Staging has two steps: `POST /api/disc-imports` creates an empty import, then `POST /api/disc-imports/{id}/files` receives batches (Cloudflare 100 MB body limit).
  - Every `/files` call re-parses the staging dir and returns the full preview.
  - `GET /api/disc-imports/{id}` returns the current preview.
- The folder picker puts the picked folder's own name first in `webkitRelativePath`; the client strips that first segment.
- The small JPEG copies under `IHE_PDI/<subdirs>/` are always uploaded, so there is no second folder pick.
  - The server uses them only for a study whose DICOM images are all undecodable.
  - It matches them by path: `IHE_PDI/<rest>.jpg` ↔ `DICOM/<rest>`.
- Loose files from the manual pickers are staged under `manual/`.
  - Loose images join the only study when there is exactly one; otherwise they form a synthetic study with key `manual`.
  - PDFs under `manual/` are preselected reports.

**Data model and storage**
- A document is a "study" when `extra_metadata` is not null. The pipeline routes on that, not on attachments.
- Bundle dir name clash: a sibling bundle dir, a sibling Origami folder (by disk name), or anything already on disk with that name. A flat file `X.pdf` does not clash with a dir `X`.

**Report text**
- A report is OCR'd only when OCR is on and the report has no usable text. Only the OCR text is indexed; the stored attachment is always the original, and no derived OCR file is kept.
- A report without text and with OCR off is only warned about in the preview. The job records nothing.

**Cover page**
- The cover page is a raster image, so its text is not extractable. Search reaches the same data through the metadata chunk. Tests check page count and page size, not cover text.

**Dialog**
- Patient tag: the preview returns `patient_tag` (display name) and `patient_tag_id` (an existing tag with that name, case-insensitive, or null). When it is null and the user keeps "Tag …" ticked, the dialog creates the tag with `POST /api/tags` before commit.
- No toast system exists: after commit the dialog shows a success view with a link per document.
- Each study card has its own folder picker.

**Backend jobs and APIs**
- `refresh_metadata` re-queues itself 60 s later while the document is `pending` or `processing`.
- `POST /api/documents/upload` gains a `notes` form field. `UploadDialog` does not change.
- `delete_documents` returns 4-tuples `(file_rel, preview, doc_id, attachment_rels)`. All three callers change.

## Review Focus

1. A study title with `/` ("Rx gamba … Destro/a") must give a safe bundle dir name (`Destro_a`), and a later rename must still find and move the dir. → Task 3 test `test_bundle_title_with_slash_and_rename`.
2. A document renamed to "Report" whose attachment is `Report.pdf`: the main file must become `Report (2).pdf`, not crash with `FileExistsError`. → Task 3 test `test_main_file_never_overwrites_attachment`.
3. A staging path such as `../x`, `/etc/x`, `C:/x` or `.thumbs/x` must get 400 `invalid_path` with nothing written outside the staging dir. → Task 8 test `test_files_rejects_unsafe_paths`.
4. A disc picked at its root, where every path starts with the picked folder's name, must still be recognised: `README.TXT` kept, `VIEWERS/` dropped. → Task 10 test `strips the picked folder name before filtering`.
5. A worker retry after the bundle is already written (embedding provider down on the first try) must not create `Title (2)/` or duplicate attachment rows. → Task 9 test `test_retry_does_not_rebuild_bundle`.

---

### Task 1: Dependencies, schema and models

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (through `uv add`)
- Create: `backend/alembic/versions/a3f9c2d71b48_disc_import_attachments.py`
- Modify: `backend/app/models/document.py`, `backend/app/models/chunk.py`, `backend/app/models/__init__.py`
- Create: `backend/app/models/disc_import.py`
- Test: `backend/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `Document.notes: str` (default `""`), `Document.extra_metadata: dict | None` (JSONB)
  - `class AttachmentRole(StrEnum): report = "report"`
  - `class DocumentAttachment` (table `document_attachments`): `id: int | None`, `document_id: uuid.UUID`, `role: str`, `filename: str`, `file_path: str`, `file_size: int`, `page_count: int | None`, `created_at: datetime`
  - `Chunk.attachment_id: int | None`
  - `class DiscImportStatus(StrEnum): staging, committed, cancelled`
  - `class DiscImport` (table `disc_imports`): `id: uuid.UUID`, `user_id: int`, `status: str`, `preview: dict | None` (JSONB), `created_at: datetime`
  - All exported from `app.models`.

- [ ] **Step 1: Add the DICOM dependencies**

```bash
cd /opt/origami/backend && uv add "pydicom>=3.0" "pylibjpeg>=2.1" "pylibjpeg-libjpeg>=2.4" "pylibjpeg-openjpeg>=2.6" "numpy>=2.0"
```

Expected: `pyproject.toml` lists the five packages and `uv.lock` is updated. Check with `uv run python -c "import pydicom, libjpeg, openjpeg, numpy"`.

- [ ] **Step 2: Write the failing schema tests**

Append to `backend/tests/test_schema.py`:

```python
def test_disc_import_schema(engine):
    inspector = inspect(engine)
    assert {"document_attachments", "disc_imports"} <= set(inspector.get_table_names())
    doc_cols = {c["name"] for c in inspector.get_columns("documents")}
    assert {"notes", "extra_metadata"} <= doc_cols
    assert "attachment_id" in {c["name"] for c in inspector.get_columns("chunks")}


def test_attachments_and_their_chunks_cascade_with_document(session):
    from sqlmodel import select

    from app.models import Chunk, ChunkSource, DocType, Document, DocumentAttachment

    doc = Document(title="Study", doc_type=DocType.pdf, extra_metadata={"modality": "DX"})
    session.add(doc)
    session.commit()
    att = DocumentAttachment(
        document_id=doc.id, role="report", filename="Report.pdf", file_path="Study/Report.pdf", file_size=1
    )
    session.add(att)
    session.commit()
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content, content="x", attachment_id=att.id))
    session.commit()
    session.refresh(doc)
    assert doc.notes == ""
    assert doc.extra_metadata == {"modality": "DX"}

    session.delete(doc)
    session.commit()
    assert session.exec(select(DocumentAttachment)).all() == []
    assert session.exec(select(Chunk)).all() == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_schema.py -v`
Expected: FAIL. The tables are missing, or `ImportError: cannot import name 'DocumentAttachment'`.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/a3f9c2d71b48_disc_import_attachments.py`:

```python
"""document notes, study metadata, attachments and disc import staging

Revision ID: a3f9c2d71b48
Revises: c8e2f5a1d746
Create Date: 2026-10-12 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a3f9c2d71b48"
down_revision: Union[str, Sequence[str], None] = "c8e2f5a1d746"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("notes", sa.String(), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("extra_metadata", postgresql.JSONB(), nullable=True))
    op.create_table(
        "document_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_attachments_document_id", "document_attachments", ["document_id"])
    op.add_column(
        "chunks",
        sa.Column(
            "attachment_id",
            sa.Integer(),
            sa.ForeignKey("document_attachments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_table(
        "disc_imports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("preview", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("disc_imports")
    op.drop_column("chunks", "attachment_id")
    op.drop_index("ix_document_attachments_document_id", table_name="document_attachments")
    op.drop_table("document_attachments")
    op.drop_column("documents", "extra_metadata")
    op.drop_column("documents", "notes")
```

- [ ] **Step 5: Add the models**

In `backend/app/models/document.py`:

1. Extend the imports:

```python
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
```

2. In `Document`, after `description: str = ""`, add:

```python
    notes: str = ""  # free text from the user; part of the metadata chunk
```

3. After `file_size: int | None = None`, add:

```python
    # study data of an imported X-ray disc (patient, modality, series, ...); None for other documents
    extra_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
```

4. At the end of the file, add:

```python
class AttachmentRole(StrEnum):
    report = "report"


class DocumentAttachment(SQLModel, table=True):
    """Extra file stored next to the main file, inside the document's bundle directory."""

    __tablename__ = "document_attachments"

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    role: str  # AttachmentRole
    filename: str  # name inside the bundle directory
    file_path: str  # relative to STORAGE_PATH
    file_size: int
    page_count: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
```

In `backend/app/models/chunk.py`, add to `Chunk` after `content: str`:

```python
    attachment_id: int | None = Field(default=None, foreign_key="document_attachments.id")
```

Create `backend/app/models/disc_import.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class DiscImportStatus(StrEnum):
    staging = "staging"
    committed = "committed"
    cancelled = "cancelled"


class DiscImport(SQLModel, table=True):
    """Files of one X-ray disc staged under tmp/disc_imports/<id>/ until commit."""

    __tablename__ = "disc_imports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    status: str = DiscImportStatus.staging
    preview: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=utcnow)
```

Replace `backend/app/models/__init__.py` with:

```python
from app.models.chunk import Chunk, ChunkSource
from app.models.disc_import import DiscImport, DiscImportStatus
from app.models.document import (
    AttachmentRole,
    Document,
    DocumentAttachment,
    DocumentTag,
    DocStatus,
    DocType,
    TranslationStatus,
)
from app.models.embedding_meta import EmbeddingMeta
from app.models.folder import Folder
from app.models.job import Job, JobStatus
from app.models.scan import ScanPage, ScanSession, ScanSessionStatus
from app.models.tag import Tag
from app.models.translation import TranslationSegment
from app.models.user import User

__all__ = [
    "AttachmentRole", "Chunk", "ChunkSource", "DiscImport", "DiscImportStatus", "Document",
    "DocumentAttachment", "DocumentTag", "DocStatus", "DocType", "EmbeddingMeta", "Folder", "Job",
    "JobStatus", "ScanPage", "ScanSession", "ScanSessionStatus", "Tag", "TranslationSegment",
    "TranslationStatus", "User",
]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /opt/origami/backend && uv run pytest tests/test_schema.py tests/test_migrations.py -v`
Expected: PASS. `test_migrations.py` downgrades and upgrades across this revision.

- [ ] **Step 7: Run the whole backend suite**

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: all pass. The `serialize` output now carries `notes` and `extra_metadata` through `model_dump()`.

- [ ] **Step 8: Commit**

```bash
cd /opt/origami && git add backend/pyproject.toml backend/uv.lock backend/alembic/versions/a3f9c2d71b48_disc_import_attachments.py backend/app/models backend/tests/test_schema.py
git commit -m "feat: notes, study metadata, document attachments and disc import tables"
```

---

### Task 2: Notes field and metadata chunk refresh

**Files:**
- Create: `backend/app/services/study_metadata.py`
- Modify: `backend/app/api/documents.py` (`DocumentPatch`, `update_document`, new `queue_metadata_refresh`)
- Modify: `backend/app/api/uploads.py` (`create_pending_document`, `upload_document`)
- Modify: `backend/app/worker/pipeline.py` (`_ensure_metadata_chunk`, new `refresh_metadata` job)
- Test: `backend/tests/test_notes.py`

**Interfaces:**
- Consumes: `Document.notes`, `Document.extra_metadata` (Task 1).
- Produces:
  - `app.services.study_metadata`:
    - `FIELDS: list[tuple[str, str]]`
    - `metadata_rows(meta: dict) -> list[tuple[str, str]]`
    - `metadata_text(meta: dict) -> str`
    - `cover_rows(meta: dict, study_date: date) -> list[tuple[str, str]]`
  - `create_pending_document(..., notes: str = "", extra_metadata: dict | None = None)`
  - `queue_metadata_refresh(session, doc) -> None` in `app.api.documents` (no commit)
  - job type `refresh_metadata` with payload `{"document_id": str}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notes.py`:

```python
from datetime import date

from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job, JobStatus
from app.services.study_metadata import cover_rows, metadata_text
from app.worker import pipeline


META = {
    "patient_name": "Mario Rossi",
    "modality": "DX",
    "institution": "Ospedale Test",
    "series": [{"description": "AP", "images": 2}, {"description": "Lateral", "images": 1}],
}


def make_ready_doc(session, **kw):
    doc = Document(title=kw.pop("title", "Visita"), doc_type=DocType.pdf, status=DocStatus.ready, **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_metadata_text_lists_known_fields_in_order():
    assert metadata_text(META) == (
        "Patient: Mario Rossi\nModality: DX\nInstitution: Ospedale Test\nSeries: AP (2); Lateral (1)"
    )
    assert metadata_text({}) == ""


def test_cover_rows_start_with_study_date():
    rows = cover_rows(META, date(2026, 8, 28))
    assert rows[0] == ("Study date", "2026-08-28")
    assert ("Patient", "Mario Rossi") in rows


def test_patch_notes_queues_metadata_refresh(auth_client, session, storage):
    doc = make_ready_doc(session)
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.metadata, content="Visita"))
    session.commit()

    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"notes": "Controllo a 3 mesi"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Controllo a 3 mesi"
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).all() == []
    job = session.exec(select(Job).where(Job.type == "refresh_metadata")).one()
    assert job.payload == {"document_id": str(doc.id)}


def test_patch_without_metadata_fields_queues_nothing(auth_client, session, storage):
    doc = make_ready_doc(session)
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2026-01-02"})
    assert resp.status_code == 200
    assert session.exec(select(Job).where(Job.type == "refresh_metadata")).all() == []


def test_refresh_metadata_rebuilds_chunk_with_notes_and_study_data(session, llm_stub):
    doc = make_ready_doc(session, notes="Controllo a 3 mesi", extra_metadata=META)
    pipeline.refresh_metadata(session, {"document_id": str(doc.id)})
    chunk = session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).one()
    assert chunk.content.startswith("Visita\n\nControllo a 3 mesi\n\nPatient: Mario Rossi")
    assert chunk.embedding is not None


def test_refresh_metadata_waits_while_processing(session, llm_stub):
    doc = make_ready_doc(session)
    doc.status = DocStatus.processing
    session.commit()
    pipeline.refresh_metadata(session, {"document_id": str(doc.id)})
    assert session.exec(select(Chunk)).all() == []
    job = session.exec(select(Job).where(Job.type == "refresh_metadata")).one()
    assert job.status == JobStatus.queued


def test_upload_accepts_notes(auth_client, session, storage):
    resp = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.pdf", b"%PDF-1.7 fake", "application/pdf")},
        data={"notes": "Da portare alla visita"},
    )
    assert resp.status_code == 201
    assert resp.json()["notes"] == "Da portare alla visita"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_notes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.study_metadata'`.

- [ ] **Step 3: Write `study_metadata.py`**

Create `backend/app/services/study_metadata.py`:

```python
"""Labels and plain text for the study data of imported X-ray discs (cover page, search, summary)."""

from datetime import date

# Same keys and labels as STUDY_FIELDS in frontend/src/lib/documentView.ts.
FIELDS: list[tuple[str, str]] = [
    ("patient_name", "Patient"),
    ("birth_date", "Birth date"),
    ("sex", "Sex"),
    ("patient_id", "Patient ID"),
    ("study_description", "Study"),
    ("modality", "Modality"),
    ("body_part", "Body part"),
    ("institution", "Institution"),
    ("accession_number", "Accession number"),
    ("device", "Device"),
]


def metadata_rows(meta: dict) -> list[tuple[str, str]]:
    rows = [(label, str(meta[key])) for key, label in FIELDS if meta.get(key)]
    series = meta.get("series") or []
    if series:
        rows.append(("Series", "; ".join(f"{s['description']} ({s['images']})" for s in series)))
    return rows


def metadata_text(meta: dict) -> str:
    return "\n".join(f"{label}: {value}" for label, value in metadata_rows(meta))


def cover_rows(meta: dict, study_date: date) -> list[tuple[str, str]]:
    return [("Study date", study_date.isoformat()), *metadata_rows(meta)]
```

- [ ] **Step 4: Include notes and study data in the metadata chunk, add the job**

In `backend/app/worker/pipeline.py`, add the import:

```python
from app.services.study_metadata import metadata_text
```

Replace `_ensure_metadata_chunk` with:

```python
def _metadata_content(doc: Document) -> str:
    parts = [doc.title]
    if doc.description and doc.description != doc.summary:
        parts.append(doc.description)  # an AI description is already embedded as the summary chunk
    if doc.notes.strip():
        parts.append(doc.notes.strip())
    if doc.extra_metadata:
        parts.append(metadata_text(doc.extra_metadata))
    return "\n\n".join(parts)


def _ensure_metadata_chunk(session: Session, doc: Document) -> None:
    if _has_chunks(session, doc, ChunkSource.metadata):
        return
    session.add(
        Chunk(
            document_id=doc.id,
            chunk_index=_next_chunk_index(session, doc),
            source=ChunkSource.metadata,
            content=_metadata_content(doc),
        )
    )
    session.commit()


METADATA_REFRESH_DELAY = timedelta(seconds=60)


@register("refresh_metadata")
def refresh_metadata(session: Session, payload: dict) -> None:
    """Rebuild and embed the metadata chunk after the user edited title, description or notes."""
    from app.services.jobs import enqueue

    doc = session.get(Document, payload["document_id"])
    if doc is None:
        return
    if doc.status in (DocStatus.pending, DocStatus.processing):
        # the running pipeline may already be past its metadata step: look again later
        enqueue(
            session, "refresh_metadata", {"document_id": str(doc.id)},
            run_at=datetime.now(timezone.utc) + METADATA_REFRESH_DELAY,
        )
        return
    _ensure_metadata_chunk(session, doc)
    _embed_pending_chunks(session, doc)
```

- [ ] **Step 5: Accept notes in PATCH and upload**

In `backend/app/api/documents.py`:

1. Change the sqlalchemy import to `from sqlalchemy import delete, func`.
2. Add `notes: str | None = None` to `DocumentPatch`.
3. Below `DocumentPatch`, add:

```python
METADATA_FIELDS = {"title", "description", "notes"}


def queue_metadata_refresh(session: Session, doc: Document) -> None:
    """Drop the metadata chunk and queue its rebuild (no commit), so edits become searchable."""
    session.exec(
        delete(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.metadata)
    )
    session.add(
        Job(type="refresh_metadata", payload={"document_id": str(doc.id)}, run_at=datetime.now(timezone.utc))
    )
```

4. In `update_document`, on the line after `doc.updated_at = datetime.now(timezone.utc)` (still inside the `with` block), add:

```python
        if METADATA_FIELDS & fields.keys():
            queue_metadata_refresh(session, doc)
```

In `backend/app/api/uploads.py`:

1. Add two keyword parameters to `create_pending_document`, after `document_date: date | None = None`:

```python
    notes: str = "",
    extra_metadata: dict | None = None,
```

2. Pass them to the `Document(...)` constructor: `notes=notes, extra_metadata=extra_metadata,`.
3. In `upload_document`, add the form field `notes: str = Form(default=""),` after `document_date`, and pass `notes=notes` to `create_pending_document`.

- [ ] **Step 6: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_notes.py tests/test_documents.py tests/test_pipeline.py tests/test_uploads.py -v`
Expected: PASS. If an existing PATCH test asserted an exact job list, update it to allow the new `refresh_metadata` job: a title edit now queues one by design.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami && git add backend/app/services/study_metadata.py backend/app/api/documents.py backend/app/api/uploads.py backend/app/worker/pipeline.py backend/tests/test_notes.py backend/tests/test_documents.py
git commit -m "feat: document notes; metadata chunk with notes and study data, rebuilt after edits"
```

---

### Task 3: Bundle directories in the storage tree

**Files:**
- Modify: `backend/app/services/tree_paths.py`
- Modify: `backend/app/services/tree_sync.py`
- Modify: `backend/app/services/storage.py` (`delete_document_files`)
- Modify: `backend/app/services/storage_check.py`
- Modify: `backend/app/api/folders.py` (`reparent_folder`)
- Modify: `backend/app/api/documents.py` (`delete_documents` and its two callers)
- Modify: `backend/app/api/bulk.py` (`bulk_delete_items` caller)
- Test: `backend/tests/test_bundles.py`

**Interfaces:**
- Consumes: `DocumentAttachment` (Task 1).
- Produces:
  - `tree_paths.has_attachments(session, document_id) -> bool`
  - `tree_paths.bundle_dir_names(session, folder_id: int | None, exclude_id=None) -> set[str]`
  - `tree_paths.bundle_rel_dir(session, storage, doc) -> str`
  - `tree_sync.AttachmentSource` dataclass: `role: str`, `filename: str`, `source: Path`, `page_count: int | None = None`
  - `tree_sync.write_bundle(session, storage, doc, ext: str, data: bytes, attachments: list[AttachmentSource]) -> None` (commits)
  - `relocate_document` moves bundle dirs; `disk_name_taken` counts bundle dirs
  - `delete_documents(...) -> list[tuple[str | None, str | None, uuid.UUID, list[str]]]`
  - `Storage.delete_document_files(file_rel, preview, document_id, attachments: Sequence[str] = ())`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_bundles.py`:

```python
import pytest
from sqlmodel import select

from app.models import DocType, Document, DocumentAttachment
from app.services.storage_check import check_storage
from app.services.tree_sync import AttachmentSource, write_bundle


@pytest.fixture
def report_file(tmp_path):
    path = tmp_path / "staged-report.pdf"
    path.write_bytes(b"%PDF-1.4 report")
    return path


def make_bundle(session, storage, report_file, title="Study", folder_id=None, reports=1):
    doc = Document(title=title, doc_type=DocType.pdf, folder_id=folder_id, extra_metadata={})
    session.add(doc)
    session.commit()
    session.refresh(doc)
    sources = [AttachmentSource(role="report", filename="Report.pdf", source=report_file, page_count=1)] * reports
    write_bundle(session, storage, doc, ".pdf", b"%PDF-1.4 images", sources)
    session.refresh(doc)
    return doc


def attachments(session, doc):
    return session.exec(
        select(DocumentAttachment).where(DocumentAttachment.document_id == doc.id).order_by(DocumentAttachment.id)
    ).all()


def test_write_bundle_creates_dir_with_main_file_and_attachment(session, storage, report_file):
    doc = make_bundle(session, storage, report_file)
    assert doc.file_path == "Study/Study.pdf"
    assert storage.abs_path("Study/Study.pdf").read_bytes() == b"%PDF-1.4 images"
    [att] = attachments(session, doc)
    assert (att.filename, att.file_path, att.page_count) == ("Report.pdf", "Study/Report.pdf", 1)
    assert storage.abs_path("Study/Report.pdf").read_bytes() == b"%PDF-1.4 report"


def test_two_reports_get_distinct_names(session, storage, report_file):
    doc = make_bundle(session, storage, report_file, reports=2)
    assert [a.filename for a in attachments(session, doc)] == ["Report.pdf", "Report (2).pdf"]


def test_bundle_avoids_sibling_folder_name(auth_client, session, storage, report_file):
    assert auth_client.post("/api/folders", json={"name": "Study"}).status_code == 201
    doc = make_bundle(session, storage, report_file)
    assert doc.file_path == "Study (2)/Study (2).pdf"


def test_main_file_never_overwrites_attachment(session, storage, report_file):
    doc = make_bundle(session, storage, report_file, title="Report")
    assert doc.file_path == "Report/Report (2).pdf"
    assert storage.abs_path("Report/Report.pdf").read_bytes() == b"%PDF-1.4 report"


def test_bundle_title_with_slash_and_rename(auth_client, session, storage, report_file):
    doc = make_bundle(session, storage, report_file, title="Rx gamba Destro/a")
    assert doc.file_path == "Rx gamba Destro_a/Rx gamba Destro_a.pdf"
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "Rx tibia"})
    assert resp.status_code == 200
    session.refresh(doc)
    assert doc.file_path == "Rx tibia/Rx tibia.pdf"
    assert [a.file_path for a in attachments(session, doc)] == ["Rx tibia/Report.pdf"]
    assert storage.abs_path("Rx tibia/Report.pdf").exists()
    assert not storage.abs_path("Rx gamba Destro_a").exists()


def test_move_bundle_to_folder(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    doc = make_bundle(session, storage, report_file)
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"folder_id": folder_id})
    assert resp.status_code == 200
    session.refresh(doc)
    assert doc.file_path == "Salute/Study/Study.pdf"
    assert attachments(session, doc)[0].file_path == "Salute/Study/Report.pdf"
    assert storage.abs_path("Salute/Study/Report.pdf").exists()


def test_folder_rename_moves_attachment_paths(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    doc = make_bundle(session, storage, report_file, folder_id=folder_id)
    assert auth_client.patch(f"/api/folders/{folder_id}", json={"name": "Medico"}).status_code == 200
    session.expire_all()
    assert session.get(Document, doc.id).file_path == "Medico/Study/Study.pdf"
    assert attachments(session, doc)[0].file_path == "Medico/Study/Report.pdf"
    assert storage.abs_path("Medico/Study/Report.pdf").exists()


def test_new_folder_cannot_take_bundle_dir_name(auth_client, session, storage, report_file):
    make_bundle(session, storage, report_file)
    resp = auth_client.post("/api/folders", json={"name": "Study"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"


def test_delete_removes_bundle_dir(auth_client, session, storage, report_file):
    doc = make_bundle(session, storage, report_file)
    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not storage.abs_path("Study").exists()
    assert session.exec(select(DocumentAttachment)).all() == []


def test_bulk_folder_delete_removes_bundle(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    make_bundle(session, storage, report_file, folder_id=folder_id)
    resp = auth_client.post("/api/bulk/delete", json={"folder_ids": [folder_id], "document_ids": []})
    assert resp.status_code == 200
    assert not storage.abs_path("Salute").exists()


def test_check_storage_counts_attachments(session, storage, report_file):
    make_bundle(session, storage, report_file)
    report = check_storage(session, storage)
    assert report.ok, (report.missing, report.unreferenced)


def test_flat_documents_unchanged(auth_client, session, storage):
    resp = auth_client.post(
        "/api/documents/upload", files={"file": ("Study.pdf", b"%PDF", "application/pdf")}
    )
    assert resp.json()["file_path"] == "Study.pdf"
```

Before writing the tests, read `backend/app/api/bulk.py` and use its exact path and body for the bulk delete endpoint. The test above assumes `POST /api/bulk/delete` with `folder_ids` and `document_ids`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_bundles.py -v`
Expected: FAIL with `ImportError: cannot import name 'AttachmentSource'`.

- [ ] **Step 3: Path rules in `tree_paths.py`**

Change the models import to `from app.models import Document, DocumentAttachment, Folder` and append:

```python
def has_attachments(session: Session, document_id) -> bool:
    return (
        session.exec(select(DocumentAttachment.id).where(DocumentAttachment.document_id == document_id)).first()
        is not None
    )


def bundle_dir_names(session: Session, folder_id: int | None, exclude_id=None) -> set[str]:
    """Disk names of the bundle directories (documents with attachments) directly inside a folder."""
    same_folder = Document.folder_id.is_(None) if folder_id is None else Document.folder_id == folder_id
    query = select(Document.file_path).where(
        same_folder,
        Document.file_path.is_not(None),
        Document.id.in_(select(DocumentAttachment.document_id)),
    )
    if exclude_id is not None:
        query = query.where(Document.id != exclude_id)
    return {PurePosixPath(p).parent.name for p in session.exec(query)}


def bundle_rel_dir(session: Session, storage, doc: Document) -> str:
    """Target bundle directory of a document with attachments, for its current title and folder."""
    dir_rel = folder_rel_dir(session, doc.folder_id)
    same_parent = Folder.parent_id.is_(None) if doc.folder_id is None else Folder.parent_id == doc.folder_id
    taken = bundle_dir_names(session, doc.folder_id, exclude_id=doc.id)
    taken |= {safe_name(f.name) for f in session.exec(select(Folder).where(same_parent))}
    own = None
    if doc.file_path and has_attachments(session, doc.id):
        current = PurePosixPath(doc.file_path).parent
        if str(current.parent) == (dir_rel or "."):
            own = current.name
    name = unique_name(storage.abs_path(dir_rel), safe_name(doc.title), "", taken, own)
    return join_rel(dir_rel, name)
```

- [ ] **Step 4: Bundle writes and moves in `tree_sync.py`**

Change the imports at the top:

```python
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy import text
from sqlmodel import Session, select

from app.models import Document, DocumentAttachment, Folder
from app.services.storage import Storage
from app.services.tree_paths import (
    bundle_dir_names,
    bundle_rel_dir,
    document_rel_path,
    has_attachments,
    join_rel,
    safe_name,
    unique_name,
)
```

Replace `relocate_document` with:

```python
def relocate_document(session: Session, storage: Storage, moves: MoveLog, doc: Document) -> None:
    """Move the document file (or its bundle directory) to the path its current title and folder give."""
    if not doc.file_path:
        return
    session.flush()  # earlier relocations in this transaction count as taken names
    if has_attachments(session, doc.id):
        _relocate_bundle(session, storage, moves, doc)
        return
    new = document_rel_path(session, storage, doc, PurePosixPath(doc.file_path).suffix)
    if new != doc.file_path:
        moves.move_file(doc.file_path, new)
        doc.file_path = new


def _doc_attachments(session: Session, doc: Document) -> list[DocumentAttachment]:
    return list(
        session.exec(
            select(DocumentAttachment)
            .where(DocumentAttachment.document_id == doc.id)
            .order_by(DocumentAttachment.id)
        )
    )


def _relocate_bundle(session: Session, storage: Storage, moves: MoveLog, doc: Document) -> None:
    old_dir = str(PurePosixPath(doc.file_path).parent)
    new_dir = bundle_rel_dir(session, storage, doc)
    attachments = _doc_attachments(session, doc)
    if new_dir != old_dir:
        moves.move_dir(old_dir, new_dir)
        for attachment in attachments:
            attachment.file_path = join_rel(new_dir, attachment.filename)
    old_name = PurePosixPath(doc.file_path).name
    name = unique_name(
        storage.abs_path(new_dir),
        PurePosixPath(new_dir).name,
        PurePosixPath(doc.file_path).suffix,
        {a.filename for a in attachments},
        own=old_name,
    )
    if name != old_name:
        moves.move_file(join_rel(new_dir, old_name), join_rel(new_dir, name))
    doc.file_path = join_rel(new_dir, name)
```

After `write_document_file`, add:

```python
@dataclass
class AttachmentSource:
    role: str  # AttachmentRole
    filename: str  # wanted name; " (n)" is added on a clash
    source: Path
    page_count: int | None = None


def write_bundle(
    session: Session,
    storage: Storage,
    doc: Document,
    ext: str,
    data: bytes,
    attachments: list[AttachmentSource],
) -> None:
    """Write a new bundle directory (main file plus attachments) for a document, then commit."""
    lock_tree(session)  # same lock order as write_document_file
    session.flush()
    session.refresh(doc, with_for_update=True)
    bundle = bundle_rel_dir(session, storage, doc)
    bundle_abs = storage.abs_path(bundle)
    names: list[str] = []
    for attachment in attachments:
        wanted = PurePosixPath(attachment.filename)
        names.append(unique_name(bundle_abs, wanted.stem, wanted.suffix.lower(), set(names)))
    main = unique_name(bundle_abs, PurePosixPath(bundle).name, ext.lower(), set(names))
    written: list[str] = []
    try:
        rel, size = storage.write_file(join_rel(bundle, main), data)
        written.append(rel)
        doc.file_path, doc.file_size = rel, size
        for attachment, name in zip(attachments, names):
            with attachment.source.open("rb") as src:
                att_rel, att_size = storage.write_file(join_rel(bundle, name), src)
            written.append(att_rel)
            session.add(
                DocumentAttachment(
                    document_id=doc.id,
                    role=attachment.role,
                    filename=name,
                    file_path=att_rel,
                    file_size=att_size,
                    page_count=attachment.page_count,
                )
            )
        session.commit()
    except BaseException:
        session.rollback()
        for rel in written:
            storage.delete_file(rel)
        storage.remove_dir(bundle)
        raise
```

In `disk_name_taken`, before the final `return any(...)`, add:

```python
    if target in bundle_dir_names(session, parent_id):
        return True
```

- [ ] **Step 5: Folder renames move attachment paths**

In `backend/app/api/folders.py`, change the models import to `from app.models import Document, DocumentAttachment, Folder`. In `reparent_folder`, after the existing `session.execute(update(Document)...)` call (inside `if new_dir != old_dir:`), add:

```python
        att_under_old = func.starts_with(DocumentAttachment.file_path, old_dir + "/")
        session.execute(
            update(DocumentAttachment)
            .where(att_under_old)
            .values(
                file_path=func.concat(new_dir, func.substr(DocumentAttachment.file_path, len(old_dir) + 1))
            )
            .execution_options(synchronize_session=False)
        )
```

- [ ] **Step 6: Deletes remove attachments and the bundle dir**

In `backend/app/services/storage.py`:

1. Add `from collections.abc import Sequence` and change the pathlib import to `from pathlib import Path, PurePosixPath`.
2. Replace `delete_document_files` with:

```python
    def delete_document_files(
        self,
        file_rel: str | None,
        preview: str | None,
        document_id: uuid.UUID,
        attachments: Sequence[str] = (),
    ) -> None:
        self.delete_file(file_rel)
        for rel in attachments:
            self.delete_file(rel)
        if attachments and file_rel:
            self.remove_dir(str(PurePosixPath(file_rel).parent))  # the bundle directory
        self.delete_derived(preview)
        self.delete_derived(companion_name(document_id))
```

In `backend/app/api/documents.py`:

1. Add `DocumentAttachment` to the models import.
2. Replace `delete_documents` with:

```python
def delete_documents(session: Session, docs: list[Document]) -> list[tuple]:
    """Cancel queued jobs and delete rows (no commit); returns the files to remove after commit."""
    files: list[tuple] = []
    for doc in docs:
        attachments = list(
            session.exec(select(DocumentAttachment.file_path).where(DocumentAttachment.document_id == doc.id))
        )
        files.append((doc.file_path, doc.preview_path, doc.id, attachments))
        _cancel_queued_jobs(session, doc)
        session.delete(doc)  # chunks, attachments, document_tags and translation segments cascade via FK
    return files
```

3. In `bulk_delete` and `delete_document`, change the loops to:

```python
    for file_rel, preview, doc_id, attachments in files:
        storage.delete_document_files(file_rel, preview, doc_id, attachments)
```

In `backend/app/api/bulk.py` (`bulk_delete_items`), change the loop the same way:

```python
    for file_rel, preview, doc_id, attachments in files:
        try:
            storage.delete_document_files(file_rel, preview, doc_id, attachments)
        except Exception:  # the DB delete has committed: keep cleaning up the rest
            log.warning("Could not delete files of document %s", doc_id, exc_info=True)
```

- [ ] **Step 7: `check-storage` knows attachments**

In `backend/app/services/storage_check.py`, change the import to `from app.models import Document, DocumentAttachment`. In `check_storage`, after the `paths = {...}` line, add:

```python
    paths |= set(session.exec(select(DocumentAttachment.file_path)))
```

- [ ] **Step 8: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_bundles.py tests/test_tree_sync.py tests/test_tree_paths.py tests/test_folders.py tests/test_bulk.py tests/test_bulk_items.py tests/test_storage.py tests/test_storage_check.py tests/test_documents.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd /opt/origami && git add backend/app/services/tree_paths.py backend/app/services/tree_sync.py backend/app/services/storage.py backend/app/services/storage_check.py backend/app/api/folders.py backend/app/api/documents.py backend/app/api/bulk.py backend/tests/test_bundles.py
git commit -m "feat: bundle directories for documents with attachments: write, rename, move, delete, check"
```

---

### Task 4: Attachments in the API, search and chat

**Files:**
- Modify: `backend/app/api/documents.py` (`serialize`, `list_documents`, new `attachments_for`)
- Modify: `backend/app/api/files.py` (new attachment endpoint)
- Modify: `backend/app/services/search.py` (snippet `attachment_id`)
- Modify: `backend/app/services/rag.py` (`RetrievedChunk.attachment_id`, chat source field)
- Modify: `backend/tests/helpers.py` (`seed_document` passes `attachment_id`)
- Test: `backend/tests/test_attachments_api.py`

**Interfaces:**
- Consumes: `DocumentAttachment`, `Chunk.attachment_id` (Task 1).
- Produces:
  - Document JSON gains `attachments: [{id, role, filename, file_size, page_count}]`.
  - `GET /api/documents/{document_id}/attachments/{attachment_id}/file?download=&token=`
  - Search snippets gain `attachment_id: int | None`.
  - Chat `meta.sources[]` gain `attachment_id: int | None`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/helpers.py`, inside `seed_document`'s `Chunk(...)` call, add `attachment_id=spec.get("attachment_id"),`.

Create `backend/tests/test_attachments_api.py`:

```python
from app.models import DocType, Document, DocumentAttachment
from app.services import rag
from app.services.auth import create_access_token
from app.services.search import search
from tests.helpers import basis_vector, seed_document


def make_doc_with_report(session, storage):
    doc = Document(title="Study", doc_type=DocType.pdf, file_path="Study/Study.pdf", extra_metadata={})
    session.add(doc)
    session.commit()
    storage.write_file("Study/Study.pdf", b"%PDF images")
    storage.write_file("Study/Report.pdf", b"%PDF report")
    att = DocumentAttachment(
        document_id=doc.id, role="report", filename="Report.pdf", file_path="Study/Report.pdf",
        file_size=11, page_count=1,
    )
    session.add(att)
    session.commit()
    session.refresh(doc)
    session.refresh(att)
    return doc, att


def test_document_json_lists_attachments(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["attachments"] == [
        {"id": att.id, "role": "report", "filename": "Report.pdf", "file_size": 11, "page_count": 1}
    ]
    listed = auth_client.get("/api/documents").json()
    assert listed[0]["attachments"][0]["id"] == att.id


def test_attachment_file_inline_and_download(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file")
    assert resp.status_code == 200
    assert resp.content == b"%PDF report"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    resp = auth_client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file?download=1")
    assert resp.headers["content-disposition"].startswith("attachment")


def test_attachment_file_with_token_query(client, user, session, storage):
    doc, att = make_doc_with_report(session, storage)
    token = create_access_token(user.id)
    resp = client.get(f"/api/documents/{doc.id}/attachments/{att.id}/file?token={token}")
    assert resp.status_code == 200


def test_attachment_of_other_document_is_404(auth_client, session, storage):
    doc, att = make_doc_with_report(session, storage)
    other = Document(title="Other", doc_type=DocType.pdf)
    session.add(other)
    session.commit()
    resp = auth_client.get(f"/api/documents/{other.id}/attachments/{att.id}/file")
    assert resp.status_code == 404


def test_search_snippet_carries_attachment_id(session, storage):
    doc, att = make_doc_with_report(session, storage)
    seed_document(session, "Unrelated", [{"content": "bolletta della luce"}])
    from app.models import Chunk, ChunkSource

    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content,
                      content="Frattura saldata della tibia destra.", attachment_id=att.id))
    session.commit()
    [result] = search(session, "tibia", mode="keyword")
    assert result["snippets"][0]["attachment_id"] == att.id


def test_retrieve_carries_attachment_id(session, storage, monkeypatch):
    doc, att = make_doc_with_report(session, storage)
    from app.models import Chunk, ChunkSource

    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.content, content="tibia",
                      attachment_id=att.id, embedding=basis_vector(0)))
    session.commit()
    monkeypatch.setattr(rag, "llm_embed", lambda texts, role="document": [basis_vector(0)])
    sources, _ = rag.retrieve(session, "tibia", [doc.id])
    assert sources[0].attachment_id == att.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_attachments_api.py -v`
Expected: FAIL with `KeyError: 'attachments'` and 404s.

- [ ] **Step 3: Serialize attachments**

In `backend/app/api/documents.py`, below `docs_with_content`, add:

```python
def attachments_for(session: Session, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    """Attachments of the given documents, fetched in one query."""
    out: dict[uuid.UUID, list[dict]] = {}
    if not doc_ids:
        return out
    for att in session.exec(
        select(DocumentAttachment)
        .where(DocumentAttachment.document_id.in_(doc_ids))
        .order_by(DocumentAttachment.id)
    ):
        out.setdefault(att.document_id, []).append(
            {"id": att.id, "role": att.role, "filename": att.filename,
             "file_size": att.file_size, "page_count": att.page_count}
        )
    return out
```

Change `serialize`:
- add the parameter `attachments: dict[uuid.UUID, list[dict]] | None = None`
- at the top of the body, add `if attachments is None: attachments = attachments_for(session, [doc.id])`
- add `"attachments": attachments.get(doc.id, []),` to the returned dict

In `list_documents`, pass `attachments_for(session, ids)` as a fourth positional argument:

```python
    attachments = attachments_for(session, ids)
    return [serialize(session, d, active_jobs, with_content, attachments) for d in docs]
```

- [ ] **Step 4: Attachment file endpoint**

In `backend/app/api/files.py`, add `from app.models import DocumentAttachment` and append:

```python
@router.get("/{document_id}/attachments/{attachment_id}/file")
def attachment_file(
    document_id: uuid.UUID,
    attachment_id: int,
    download: bool = False,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    att = session.get(DocumentAttachment, attachment_id)
    if att is None or att.document_id != document_id:
        raise api_error(404, "not_found", f"Attachment {attachment_id} not found")
    path = storage.abs_path(att.file_path)
    if not path.is_file():
        raise api_error(404, "no_file", "Stored file is missing on disk")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        filename=att.filename,
        content_disposition_type="attachment" if download else "inline",
        headers=NO_CACHE,
    )
```

- [ ] **Step 5: Attachment ids in search and chat**

In `backend/app/services/search.py`, add `"attachment_id": chunk.attachment_id,` to the snippet dict, after `"page_number"`.

In `backend/app/services/rag.py`:
- add `attachment_id: int | None = None` as the last field of `RetrievedChunk`
- pass `attachment_id=chunk.attachment_id,` in `retrieve`
- add `"attachment_id": s.attachment_id,` to each `meta` source dict in `stream_chat`

- [ ] **Step 6: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_attachments_api.py tests/test_search_core.py tests/test_search_api.py tests/test_rag.py tests/test_chat_api.py tests/test_files_api.py -v`
Expected: PASS. If a test compares a snippet or source dict exactly, add `"attachment_id": None` to the expected value.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami && git add backend/app/api/documents.py backend/app/api/files.py backend/app/services/search.py backend/app/services/rag.py backend/tests/helpers.py backend/tests/test_attachments_api.py backend/tests
git commit -m "feat: attachments in document JSON, attachment file endpoint, attachment ids in search and chat"
```

---

### Task 5: DICOM rendering

**Files:**
- Create: `backend/app/services/dicom_render.py`
- Modify: `backend/tests/helpers.py` (`make_dicom`, `make_text_pdf`)
- Test: `backend/tests/test_dicom_render.py`

**Interfaces:**
- Produces:
  - `dicom_render.MAX_SIDE = 4096`, `THUMB_SIDE = 256`
  - `is_decodable(transfer_syntax: str) -> bool`
  - `render_dicom(path: Path) -> list[Image.Image]` (mode `"L"` or `"RGB"`, one per frame)
  - `render_image(path: Path) -> list[Image.Image]`
  - `render(path: Path, kind: str) -> list[Image.Image]`, where `kind` is `"dicom"` or `"image"`
  - `thumbnail_jpeg(path: Path, kind: str) -> bytes`
  - Test helpers:
    - `make_dicom(path, *, study_uid=..., series_number=1, instance_number=1, rows=64, cols=48, photometric="MONOCHROME2", window=None, frames=1, color=False, rle=False, **tags) -> Path`
    - `make_text_pdf(path, lines: list[str]) -> Path`

- [ ] **Step 1: Add the test helpers**

Append to `backend/tests/helpers.py`:

```python
DX_STORAGE = "1.2.840.10008.5.1.4.1.1.1.1"


def make_dicom(
    path: Path,
    *,
    study_uid: str = "1.2.826.0.1.1",
    series_number: int = 1,
    instance_number: int = 1,
    rows: int = 64,
    cols: int = 48,
    photometric: str = "MONOCHROME2",
    window: tuple[float, float] | None = None,
    frames: int = 1,
    color: bool = False,
    rle: bool = False,
    **tags,
) -> Path:
    """A synthetic DX image: a left-to-right 12-bit gradient (or flat red RGB). `Tag=None` removes a tag."""
    import numpy as np
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, RLELossless, generate_uid

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = DX_STORAGE
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = DX_STORAGE
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = f"{study_uid}.{series_number}"
    ds.SeriesNumber = series_number
    ds.InstanceNumber = instance_number
    ds.Modality = "DX"
    ds.PatientName = "ROSSI^MARIO"
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "M"
    ds.PatientID = "P-1"
    ds.AccessionNumber = "A-1"
    ds.StudyDate = "20260828"
    ds.StudyDescription = "Rx gamba (tibia e perone) - 88.27.3 Destro/a"
    ds.SeriesDescription = "AP"
    ds.InstitutionName = "Ospedale Test"
    ds.BodyPartExamined = "TIBIA FIBULA"
    ds.Rows, ds.Columns = rows, cols
    if color:
        ds.SamplesPerPixel, ds.PhotometricInterpretation, ds.PlanarConfiguration = 3, "RGB", 0
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
        frame = np.zeros((rows, cols, 3), dtype=np.uint8)
        frame[..., 0] = 200
    else:
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, photometric
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
        frame = np.tile(np.linspace(0, 4095, cols).astype(np.uint16), (rows, 1))
    data = np.stack([frame] * frames) if frames > 1 else frame
    if frames > 1:
        ds.NumberOfFrames = frames
    if window is not None:
        ds.WindowCenter, ds.WindowWidth = window
    for key, value in tags.items():
        if value is None:
            if key in ds:
                delattr(ds, key)
        else:
            setattr(ds, key, value)
    ds.PixelData = data.tobytes()
    if rle:
        ds.compress(RLELossless)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)
    return path


def make_text_pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF with a real text layer (Helvetica); no lines gives a page with no text."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_dicom_render.py`:

```python
from PIL import Image

from app.services.dicom_render import (
    MAX_SIDE,
    THUMB_SIDE,
    is_decodable,
    render,
    render_dicom,
    render_image,
    thumbnail_jpeg,
)
from tests.helpers import make_dicom


def test_monochrome2_without_window_uses_full_gradient(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a"))
    assert image.mode == "L"
    assert image.size == (48, 64)
    assert image.getpixel((0, 0)) == 0
    assert image.getpixel((47, 0)) == 255


def test_monochrome1_is_inverted(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", photometric="MONOCHROME1"))
    assert image.getpixel((0, 0)) == 255
    assert image.getpixel((47, 0)) == 0


def test_window_from_file_is_applied(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", window=(1000, 200)))
    assert image.getpixel((5, 0)) == 0  # value ~436: below the window
    assert image.getpixel((30, 0)) == 255  # value ~2614: above the window
    assert 0 < image.getpixel((11, 0)) < 255  # value ~958: inside the window


def test_every_frame_becomes_an_image(tmp_path):
    assert len(render_dicom(make_dicom(tmp_path / "a", frames=3))) == 3


def test_color_image_stays_rgb(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", color=True))
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (200, 0, 0)


def test_long_side_is_capped(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", rows=5000, cols=10))
    assert max(image.size) == MAX_SIDE


def test_rle_compressed_file_decodes(tmp_path):
    [image] = render_dicom(make_dicom(tmp_path / "a", rle=True))
    assert image.size == (48, 64)


def test_decoder_availability():
    assert is_decodable("1.2.840.10008.1.2.4.70")  # JPEG lossless, used by hospital discs
    assert is_decodable("1.2.840.10008.1.2.4.90")  # JPEG 2000 lossless
    assert not is_decodable("1.2.3.4")


def test_plain_image_and_dispatch(tmp_path):
    path = tmp_path / "copy.jpg"
    Image.new("RGB", (30, 20), "white").save(path)
    [image] = render_image(path)
    assert image.size == (30, 20)
    assert len(render(path, "image")) == 1
    assert len(render(make_dicom(tmp_path / "d"), "dicom")) == 1


def test_thumbnail_is_small_jpeg(tmp_path):
    data = thumbnail_jpeg(make_dicom(tmp_path / "a", rows=1000, cols=800), "dicom")
    assert data[:2] == b"\xff\xd8"
    from io import BytesIO

    assert max(Image.open(BytesIO(data)).size) == THUMB_SIDE
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_dicom_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.dicom_render'`.

- [ ] **Step 4: Write `dicom_render.py`**

Create `backend/app/services/dicom_render.py`:

```python
"""Turn DICOM pixel data into 8-bit images for the study PDF and the import preview."""

import io
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image
from pydicom.pixels import apply_modality_lut, apply_voi_lut, get_decoder

MAX_SIDE = 4096
THUMB_SIDE = 256
LOW_PERCENTILE, HIGH_PERCENTILE = 0.5, 99.5


def is_decodable(transfer_syntax: str) -> bool:
    """True when an installed pixel decoder (pydicom, pylibjpeg plugins) handles the syntax."""
    try:
        return get_decoder(transfer_syntax).is_available
    except NotImplementedError:
        return False


def _has_window(ds) -> bool:
    return ("WindowCenter" in ds and "WindowWidth" in ds) or "VOILUTSequence" in ds


def _to_uint8(values: np.ndarray, full_range: bool) -> np.ndarray:
    """Stretch to 0-255: the whole range after a VOI window, else the 0.5-99.5 percentile window."""
    values = values.astype(np.float64)
    if full_range:
        low, high = float(values.min()), float(values.max())
    else:
        low, high = (float(v) for v in np.percentile(values, [LOW_PERCENTILE, HIGH_PERCENTILE]))
    if high <= low:
        return np.zeros(values.shape, dtype=np.uint8)
    return ((np.clip(values, low, high) - low) / (high - low) * 255.0).round().astype(np.uint8)


def _gray(frame: np.ndarray, ds) -> Image.Image:
    values = apply_modality_lut(frame, ds)
    windowed = _has_window(ds)
    if windowed:
        values = apply_voi_lut(values, ds)
    pixels = _to_uint8(values, full_range=windowed)
    if ds.get("PhotometricInterpretation") == "MONOCHROME1":
        pixels = 255 - pixels  # MONOCHROME1: low values are white
    return Image.fromarray(pixels)


def _color(frame: np.ndarray) -> Image.Image:
    # pydicom already converts YBR colour spaces to RGB in pixel_array
    if frame.dtype != np.uint8:
        frame = _to_uint8(frame, full_range=True)
    return Image.fromarray(frame).convert("RGB")


def _cap(image: Image.Image) -> Image.Image:
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def render_dicom(path: Path) -> list[Image.Image]:
    """One 8-bit image per frame (grayscale "L" or "RGB"), at most MAX_SIDE on the long side."""
    ds = pydicom.dcmread(path)
    pixels = ds.pixel_array
    if int(ds.get("NumberOfFrames") or 1) == 1:
        pixels = pixels[np.newaxis, ...]
    color = int(ds.get("SamplesPerPixel") or 1) == 3
    return [_cap(_color(frame) if color else _gray(frame, ds)) for frame in pixels]


def render_image(path: Path) -> list[Image.Image]:
    with Image.open(path) as img:
        image = img.convert("L" if img.mode == "L" else "RGB")
    return [_cap(image)]


def render(path: Path, kind: str) -> list[Image.Image]:
    return render_dicom(path) if kind == "dicom" else render_image(path)


def thumbnail_jpeg(path: Path, kind: str) -> bytes:
    image = render(path, kind)[0]
    image.thumbnail((THUMB_SIDE, THUMB_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()
```

- [ ] **Step 5: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_dicom_render.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add backend/app/services/dicom_render.py backend/tests/helpers.py backend/tests/test_dicom_render.py
git commit -m "feat: render DICOM images to 8-bit with modality/VOI LUT, MONOCHROME1 inversion and size cap"
```

---

### Task 6: Study PDF

**Files:**
- Create: `backend/app/services/study_pdf.py`
- Test: `backend/tests/test_study_pdf.py`

**Interfaces:**
- Consumes: nothing new. Callers pass `cover_rows(...)` from Task 2.
- Produces:
  - `study_pdf.PageImage` dataclass (`image: Image.Image`, `caption: str`)
  - `cover_page(title: str, rows: list[tuple[str, str]], notes: str) -> Image.Image`
  - `image_page(item: PageImage) -> Image.Image`
  - `build_study_pdf(title: str, rows: list[tuple[str, str]], notes: str, images: list[PageImage]) -> bytes`
  - constants `DPI = 150`, `A4 = (1240, 1754)`, `JPEG_QUALITY = 92`, `DISCLAIMER`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_study_pdf.py`:

```python
from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from app.services.study_pdf import A4, PageImage, build_study_pdf, cover_page, image_page


def test_pdf_has_cover_plus_one_page_per_image():
    images = [
        PageImage(Image.new("L", (600, 900), 128), "AP · 2026-08-28 · image 1/2"),
        PageImage(Image.new("RGB", (900, 600), "red"), "Lateral · 2026-08-28 · image 2/2"),
    ]
    reader = PdfReader(BytesIO(build_study_pdf("Rx gamba — 2026-08-28", [("Modality", "DX")], "note", images)))
    assert len(reader.pages) == 3
    cover = reader.pages[0].mediabox
    assert (round(float(cover.width)), round(float(cover.height))) == (595, 842)  # A4 in points
    portrait = reader.pages[1].mediabox
    assert float(portrait.height) > float(portrait.width)
    assert reader.pages[1].images[0].name.endswith(".jpg")  # JPEG-encoded, not lossless


def test_image_page_keeps_image_and_adds_caption_strip():
    page = image_page(PageImage(Image.new("L", (1200, 800), 0), "AP"))
    assert page.mode == "L"
    assert page.size[0] == 1200 and page.size[1] > 800
    assert page.getpixel((600, 400)) == 0
    assert page.getpixel((1199, page.size[1] - 1)) == 255  # white strip


def test_cover_page_survives_long_values_and_notes():
    rows = [("Study", "word " * 200), ("Series", "AP (2); Lateral (2)")]
    page = cover_page("A very long title " * 10, rows, "note line\n" * 200)
    assert page.size == A4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_study_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.study_pdf'`.

- [ ] **Step 3: Write `study_pdf.py`**

Create `backend/app/services/study_pdf.py`:

```python
"""Image PDF of one study: a cover page with the study data, then one page per image."""

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

DPI = 150
A4 = (1240, 1754)  # A4 portrait at 150 dpi
MARGIN = 100
LABEL_WIDTH = 330
JPEG_QUALITY = 92
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DISCLAIMER = "Images converted for reference. Not for diagnostic use."


@dataclass
class PageImage:
    image: Image.Image
    caption: str


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)
    except OSError:
        return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if not line or draw.textlength(candidate, font=font) <= width:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def cover_page(title: str, rows: list[tuple[str, str]], notes: str) -> Image.Image:
    """A4 page: title, `label  value` rows, notes; text past the bottom margin is dropped."""
    page = Image.new("RGB", A4, "white")
    draw = ImageDraw.Draw(page)
    width = A4[0] - 2 * MARGIN
    bottom = A4[1] - MARGIN - 60
    title_font, label_font, body_font = _font(40, bold=True), _font(24, bold=True), _font(24)
    y = MARGIN
    for line in _wrap(draw, title, title_font, width):
        draw.text((MARGIN, y), line, font=title_font, fill="black")
        y += 52
    y += 30
    for label, value in rows:
        if not value or y > bottom:
            continue
        draw.text((MARGIN, y), label, font=label_font, fill="black")
        for line in _wrap(draw, value, body_font, width - LABEL_WIDTH):
            if y > bottom:
                break
            draw.text((MARGIN + LABEL_WIDTH, y), line, font=body_font, fill="black")
            y += 34
        y += 8
    if notes.strip() and y < bottom:
        y += 30
        draw.text((MARGIN, y), "Notes", font=label_font, fill="black")
        y += 40
        for line in _wrap(draw, notes.strip(), body_font, width):
            if y > bottom:
                break
            draw.text((MARGIN, y), line, font=body_font, fill="black")
            y += 34
    draw.text((MARGIN, A4[1] - MARGIN), DISCLAIMER, font=_font(20), fill="#555555")
    return page


def image_page(item: PageImage) -> Image.Image:
    """The image at full size with a caption strip below; the page size follows the image."""
    width, height = item.image.size
    font_size = max(16, width // 60)
    strip = font_size * 2
    mode = "RGB" if item.image.mode == "RGB" else "L"
    page = Image.new(mode, (width, height + strip), "white")
    page.paste(item.image.convert(mode), (0, 0))
    ImageDraw.Draw(page).text(
        (font_size // 2, height + font_size // 2), item.caption, font=_font(font_size), fill="black"
    )
    return page


def build_study_pdf(title: str, rows: list[tuple[str, str]], notes: str, images: list[PageImage]) -> bytes:
    pages = [cover_page(title, rows, notes), *(image_page(item) for item in images)]
    out = io.BytesIO()
    # Pillow stores "L"/"RGB" pages as JPEG (DCTDecode); quality applies to every page
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=DPI, quality=JPEG_QUALITY)
    return out.getvalue()
```

- [ ] **Step 4: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_study_pdf.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add backend/app/services/study_pdf.py backend/tests/test_study_pdf.py
git commit -m "feat: study PDF with cover page and captioned JPEG image pages"
```

---

### Task 7: Disc parsing and preview

**Files:**
- Create: `backend/app/services/dicom_parse.py`
- Test: `backend/tests/test_dicom_parse.py`

**Interfaces:**
- Consumes:
  - `dicom_render.is_decodable` (Task 5)
  - `extract.extract_pdf_text`, `extract.pdf_needs_ocr` (existing)
  - `tests.helpers.make_dicom`, `make_text_pdf` (Task 5)
- Produces (in `app.services.dicom_parse`):
  - `MANUAL_DIR = "manual"`, `EXTRACTED_DIR = ".extracted"`, `REPORT_FILENAME = "Report.pdf"`, `JPEG_COPY_WARNING`
  - Helpers: `file_key(value: str) -> str`, `is_dicom(path) -> bool`, `clean_title(str) -> str`, `patient_display_name(str) -> str`, `parse_date(object) -> date | None`
  - Vendor files: `parse_readme(str) -> dict[str, str]`, `parse_study_htm(str) -> dict[str, str]`, `is_viewer_manual(rel: str) -> bool`
  - `build_preview(root: Path) -> dict` with keys `no_study`, `studies`, `reports`, `warnings`:
    - study: `{key, title, document_date: "YYYY-MM-DD" | None, description_fallback, patient_tag: str | None, extra_metadata: dict, images: [image]}`
    - image: `{key, path, kind: "dicom" | "image", label, readable: bool, frames: int}`
    - report: `{key, path, filename, page_count, has_text, study_key: str | None, preselected: bool}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_dicom_parse.py`:

```python
from datetime import date

import pytest
from PIL import Image

from app.services import dicom_parse
from app.services.dicom_parse import (
    JPEG_COPY_WARNING,
    build_preview,
    clean_title,
    file_key,
    is_viewer_manual,
    parse_date,
    parse_readme,
    parse_study_htm,
    patient_display_name,
)
from tests.helpers import make_dicom, make_text_pdf

REPORT_TEXT = [
    "Referto: frattura pressoche saldata con callo osseo al terzo medio della tibia destra,",
    "stabilizzata con chiodo endomidollare e viti, senza segni di mobilizzazione.",
]
README = """Patient Name : ROSSI MARIO
--- Cover Data ---
Paziente: ROSSI MARIO
Data di nascita: 19800101
Sesso: M
Data studio: 
Descrizione studio: Rx gamba (tibia e perone) - 88.27.3 Destro/a
Modalita: DX
"""
STUDY_HTM = """<html><body><table>
<tr><td>Identificativo paziente:</td><td>P-77</td></tr>
<tr><td>Accession Number:</td><td>ACC-9</td></tr>
<tr><td>Data dell'esame:</td><td>28/08/2026</td></tr>
</table></body></html>"""


def make_disc(root, **dicom_tags):
    make_dicom(root / "DICOM/1/2/3/img2", series_number=2, instance_number=1, SeriesDescription="Lateral", **dicom_tags)
    make_dicom(root / "DICOM/1/2/3/img1", series_number=1, instance_number=1, **dicom_tags)
    (root / "README.TXT").write_text(README)
    (root / "IHE_PDI").mkdir()
    (root / "IHE_PDI/Study000.htm").write_text(STUDY_HTM)
    make_text_pdf(root / "REPORT/8499735.pdf", REPORT_TEXT)
    make_text_pdf(root / "help.pdf", ["Viewer manual"])
    return root


def test_helpers():
    assert clean_title("Rx gamba (tibia e perone) - 88.27.3 Destro/a") == "Rx gamba (tibia e perone) Destro/a"
    assert clean_title("Rx torace") == "Rx torace"
    assert patient_display_name("ROSSI^MARIO") == "Mario Rossi"
    assert patient_display_name("ROSSI MARIO") == "Rossi Mario"
    assert parse_date("20260828") == date(2026, 8, 28)
    assert parse_date("28/08/2026") == date(2026, 8, 28)
    assert parse_date("2026-08-28") == date(2026, 8, 28)
    assert parse_date("20261345") is None
    assert parse_date("") is None
    assert is_viewer_manual("help.pdf")
    assert is_viewer_manual("VIEWERS/lang/guide.pdf")
    assert not is_viewer_manual("REPORT/8499735.pdf")
    assert len(file_key("DICOM/a")) == 16


def test_vendor_parsers():
    readme = parse_readme(README)
    assert readme["patient_name"] == "ROSSI MARIO"
    assert readme["study_description"].startswith("Rx gamba")
    assert "study_date" not in readme  # empty value is skipped
    assert parse_study_htm(STUDY_HTM) == {
        "patient_id": "P-77", "accession_number": "ACC-9", "study_date": "28/08/2026",
    }
    assert parse_readme("garbage without keys") == {}


def test_standard_disc_preview(tmp_path):
    preview = build_preview(make_disc(tmp_path))
    assert preview["no_study"] is False
    assert preview["warnings"] == []
    [study] = preview["studies"]
    assert study["title"] == "Rx gamba (tibia e perone) Destro/a — 2026-08-28"
    assert study["document_date"] == "2026-08-28"
    assert study["patient_tag"] == "Mario Rossi"
    assert study["description_fallback"] == "Rx gamba (tibia e perone) Destro/a · Ospedale Test"
    assert [i["label"] for i in study["images"]] == ["AP", "Lateral"]  # series order, not file order
    assert all(i["readable"] and i["kind"] == "dicom" for i in study["images"])
    meta = study["extra_metadata"]
    assert meta["patient_id"] == "P-1"  # DICOM wins over the htm value
    assert meta["birth_date"] == "1980-01-01"
    assert meta["series"] == [{"description": "AP", "images": 1}, {"description": "Lateral", "images": 1}]
    [report] = preview["reports"]  # help.pdf is excluded
    assert report["path"] == "REPORT/8499735.pdf"
    assert report["has_text"] and report["preselected"] and report["page_count"] == 1
    assert report["study_key"] == study["key"]


def test_vendor_files_fill_missing_tags(tmp_path):
    preview = build_preview(make_disc(tmp_path, StudyDate=None, PatientID=None, StudyDescription=None))
    [study] = preview["studies"]
    assert study["document_date"] == "2026-08-28"  # from Study000.htm
    assert study["extra_metadata"]["patient_id"] == "P-77"
    assert study["title"].startswith("Rx gamba (tibia e perone) Destro/a")  # from README


def test_two_studies_keep_reports_unassigned(tmp_path):
    make_dicom(tmp_path / "DICOM/a", study_uid="1.2.826.0.1.1")
    make_dicom(tmp_path / "DICOM/b", study_uid="1.2.826.0.1.2", StudyDescription="Rx torace")
    make_text_pdf(tmp_path / "REPORT/r.pdf", REPORT_TEXT)
    preview = build_preview(tmp_path)
    assert len(preview["studies"]) == 2
    assert preview["reports"][0]["study_key"] is None


def test_undecodable_study_uses_jpeg_copies(tmp_path, monkeypatch):
    make_disc(tmp_path)
    for name in ("img1", "img2"):
        target = tmp_path / f"IHE_PDI/1/2/3/{name}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (20, 30)).save(target)
    Image.new("RGB", (5, 5)).save(tmp_path / "IHE_PDI/Left.jpg")  # page decoration, ignored
    monkeypatch.setattr(dicom_parse, "is_decodable", lambda uid: False)
    preview = build_preview(tmp_path)
    [study] = preview["studies"]
    assert [i["path"] for i in study["images"]] == ["IHE_PDI/1/2/3/img1.jpg", "IHE_PDI/1/2/3/img2.jpg"]
    assert all(i["kind"] == "image" and i["readable"] for i in study["images"])
    assert preview["warnings"] == [JPEG_COPY_WARNING]


def test_undecodable_without_copies_is_marked_unreadable(tmp_path, monkeypatch):
    make_dicom(tmp_path / "DICOM/a")
    monkeypatch.setattr(dicom_parse, "is_decodable", lambda uid: False)
    [study] = build_preview(tmp_path)["studies"]
    assert study["images"][0]["readable"] is False


def test_no_dicom_is_no_study(tmp_path):
    (tmp_path / "README.TXT").write_text(README)
    preview = build_preview(tmp_path)
    assert preview["no_study"] is True
    assert preview["studies"] == []


def test_manual_files_form_a_study(tmp_path):
    Image.new("RGB", (20, 30)).save(tmp_path / "manual-b.png")
    (tmp_path / "manual").mkdir()
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/b.png")
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/a.jpg")
    make_text_pdf(tmp_path / "manual/referto.pdf", [])
    preview = build_preview(tmp_path)
    [study] = preview["studies"]
    assert study["key"] == "manual"
    assert [i["path"] for i in study["images"]] == ["manual/a.jpg", "manual/b.png"]
    assert study["title"] == "X-ray"
    assert study["document_date"] is None
    [report] = preview["reports"]
    assert report["preselected"] and report["has_text"] is False
    assert report["study_key"] == "manual"


def test_manual_images_join_the_only_study(tmp_path):
    make_dicom(tmp_path / "DICOM/a")
    (tmp_path / "manual").mkdir()
    Image.new("RGB", (20, 30)).save(tmp_path / "manual/extra.jpg")
    [study] = build_preview(tmp_path)["studies"]
    assert [i["path"] for i in study["images"]] == ["DICOM/a", "manual/extra.jpg"]


def test_encapsulated_pdf_becomes_report(tmp_path):
    pdf = make_text_pdf(tmp_path / "src.pdf", REPORT_TEXT).read_bytes()
    (tmp_path / "src.pdf").unlink()
    make_dicom(tmp_path / "DICOM/a")
    make_dicom(
        tmp_path / "DICOM/r",
        SOPClassUID="1.2.840.10008.5.1.4.1.1.104.1",
        EncapsulatedDocument=pdf,
        MIMETypeOfEncapsulatedDocument="application/pdf",
    )
    preview = build_preview(tmp_path)
    [report] = preview["reports"]
    assert report["path"].startswith(".extracted/")
    assert report["preselected"] and report["has_text"]
    assert len(preview["studies"][0]["images"]) == 1  # the PDF object is not an image


def test_broken_dicom_is_a_warning(tmp_path):
    (tmp_path / "DICOM").mkdir()
    (tmp_path / "DICOM/bad").write_bytes(b"\0" * 128 + b"DICM" + b"\xff" * 10)
    preview = build_preview(tmp_path)
    assert preview["no_study"] is True
    assert preview["warnings"] == ["DICOM/bad: not a readable DICOM file"]
```

Check the encapsulated-PDF fixture: `make_dicom` sets `Rows`, so the test also checks that the encapsulated-PDF branch comes before the image branch.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_dicom_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.dicom_parse'`.

- [ ] **Step 3: Write `dicom_parse.py`**

Create `backend/app/services/dicom_parse.py`:

```python
"""Find the studies, images and reports in a staged disc (or loose files) and propose metadata."""

import hashlib
import logging
import re
from datetime import date
from html import unescape
from pathlib import Path, PurePosixPath

import pydicom

from app.services.dicom_render import is_decodable
from app.services.extract import extract_pdf_text, pdf_needs_ocr

log = logging.getLogger("origami.disc")

ENCAPSULATED_PDF = "1.2.840.10008.5.1.4.1.1.104.1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MANUAL_DIR = "manual"
EXTRACTED_DIR = ".extracted"
REPORT_FILENAME = "Report.pdf"
VIEWER_MANUAL_WORDS = ("help", "manual", "guida")
PROCEDURE_CODE = re.compile(r"\s*-?\s*\b\d{2}\.\d{2}(?:\.\d+)?\b")
JPEG_COPY_WARNING = "The original images could not be decoded: the reduced copies from the disc are used."

README_KEYS = {
    "paziente": "patient_name",
    "patient name": "patient_name",
    "data di nascita": "birth_date",
    "sesso": "sex",
    "data studio": "study_date",
    "descrizione studio": "study_description",
    "modalita": "modality",
    "modalità": "modality",
}
HTM_KEYS = {
    "identificativo paziente": "patient_id",
    "accession number": "accession_number",
    "data dell'esame": "study_date",
}
FIELD_KEYS = (
    "patient_name", "birth_date", "sex", "patient_id", "accession_number", "institution",
    "modality", "body_part", "study_description", "device", "study_date",
)


def file_key(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:16]


def is_dicom(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            f.seek(128)
            return f.read(4) == b"DICM"
    except OSError:
        return False


def clean_title(description: str) -> str:
    """Drop procedure codes: `Rx gamba - 88.27.3 Destro/a` → `Rx gamba Destro/a`."""
    return " ".join(PROCEDURE_CODE.sub(" ", description).split())


def patient_display_name(raw: str) -> str:
    """DICOM `ROSSI^MARIO` → `Mario Rossi`; other formats keep their order, capitalized."""
    raw = raw.strip()
    if "^" in raw:
        family, _, rest = raw.partition("^")
        raw = f"{rest.split('^')[0]} {family}"
    return " ".join(part.capitalize() for part in raw.split())


def parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    for pattern, order in (
        (r"(\d{4})(\d{2})(\d{2})", "ymd"),
        (r"(\d{4})-(\d{2})-(\d{2})", "ymd"),
        (r"(\d{2})/(\d{2})/(\d{4})", "dmy"),
    ):
        match = re.fullmatch(pattern, text)
        if match:
            a, b, c = (int(g) for g in match.groups())
            try:
                return date(a, b, c) if order == "ymd" else date(c, b, a)
            except ValueError:
                return None
    return None


def parse_readme(text: str) -> dict[str, str]:
    """`Key: value` lines of a vendor README (best effort; unknown formats give {})."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        field = README_KEYS.get(key.strip().lower())
        if sep and field and value.strip() and field not in out:
            out[field] = value.strip()
    return out


def parse_study_htm(html: str) -> dict[str, str]:
    """`Label:value` pairs of an IHE PDI study page; the value may sit in the next element."""
    text = unescape(re.sub(r"<[^>]+>", "\n", html))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    out: dict[str, str] = {}
    for index, line in enumerate(lines):
        key, sep, value = line.partition(":")
        field = HTM_KEYS.get(key.strip().lower())
        if not sep or not field or field in out:
            continue
        value = value.strip()
        if not value and index + 1 < len(lines) and ":" not in lines[index + 1]:
            value = lines[index + 1]
        if value:
            out[field] = value
    return out


def is_viewer_manual(rel: str) -> bool:
    path = PurePosixPath(rel.lower())
    if "viewers" in path.parts[:-1]:
        return True
    return len(path.parts) == 1 and any(word in path.stem for word in VIEWER_MANUAL_WORDS)


def _text(ds, keyword: str) -> str:
    return str(ds.get(keyword) or "").strip()


def _first_date(ds) -> date | None:
    for keyword in ("StudyDate", "SeriesDate", "ContentDate", "AcquisitionDate"):
        value = parse_date(ds.get(keyword))
        if value:
            return value
    return None


def _study_fields(ds) -> dict:
    birth = parse_date(ds.get("PatientBirthDate"))
    first = _first_date(ds)
    return {
        "patient_name": patient_display_name(_text(ds, "PatientName")),
        "birth_date": birth.isoformat() if birth else "",
        "sex": _text(ds, "PatientSex"),
        "patient_id": _text(ds, "PatientID"),
        "accession_number": _text(ds, "AccessionNumber"),
        "institution": _text(ds, "InstitutionName"),
        "modality": _text(ds, "Modality"),
        "body_part": _text(ds, "BodyPartExamined"),
        "study_description": _text(ds, "StudyDescription"),
        "device": " ".join(filter(None, [_text(ds, "Manufacturer"), _text(ds, "ManufacturerModelName")])),
        "study_date": first.isoformat() if first else "",
    }


def _image_label(ds, rel: str) -> str:
    series, view = _text(ds, "SeriesDescription"), _text(ds, "ViewPosition")
    if view and series and (view.casefold() in series.casefold() or series.casefold() in view.casefold()):
        view = ""  # "Lateral" and "LATERALE" say the same thing
    return " · ".join(filter(None, [series, view])) or PurePosixPath(rel).name


def _report(root: Path, rel: str, *, study_key: str | None, preselected: bool) -> dict | None:
    try:
        pages = extract_pdf_text(root / rel)
    except Exception:
        log.warning("Unreadable PDF %s", rel, exc_info=True)
        return None
    return {
        "key": file_key(rel),
        "path": rel,
        "filename": PurePosixPath(rel).name,
        "page_count": len(pages),
        "has_text": not pdf_needs_ocr(pages),
        "study_key": study_key,
        "preselected": preselected,
    }


def _extract_encapsulated(root: Path, path: Path, rel: str) -> str | None:
    """Write the PDF inside an Encapsulated PDF object to .extracted/ (once) and return its path."""
    target_rel = f"{EXTRACTED_DIR}/{file_key(rel)}.pdf"
    target = root / target_rel
    if not target.exists():
        data = pydicom.dcmread(path).get("EncapsulatedDocument")
        if not data:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
    return target_rel


def _fill_from_vendor(fields: dict, readme: dict, htm: dict) -> None:
    for key, value in {**htm, **readme}.items():
        if fields.get(key):
            continue  # DICOM tags win
        if key in ("birth_date", "study_date"):
            parsed = parse_date(value)
            fields[key] = parsed.isoformat() if parsed else ""
        elif key == "patient_name":
            fields[key] = patient_display_name(value)
        else:
            fields[key] = value


def _series(images: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for image in images:
        counts[image["label"]] = counts.get(image["label"], 0) + image["frames"]
    return [{"description": label, "images": n} for label, n in counts.items()]


def _finish_study(key: str, fields: dict, images: list[dict]) -> dict:
    first_series = images[0]["label"] if images and images[0]["kind"] == "dicom" else ""
    description = clean_title(fields["study_description"])
    base = description or clean_title(first_series) or f"X-ray {fields['modality']}".strip()
    extra = {k: v for k, v in fields.items() if v and k != "study_date"}
    if description:
        extra["study_description"] = description
    extra["series"] = _series(images)
    return {
        "key": key,
        "title": f"{base} — {fields['study_date']}" if fields["study_date"] else base,
        "document_date": fields["study_date"] or None,
        "description_fallback": " · ".join(filter(None, [description, fields["institution"]])),
        "patient_tag": fields["patient_name"] or None,
        "extra_metadata": extra,
        "images": images,
    }


def build_preview(root: Path) -> dict:
    """Studies (one per StudyInstanceUID), report candidates and warnings for a staging dir."""
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.relative_to(root).parts[0].startswith(".")
    )
    groups: dict[str, list[tuple[tuple, dict, object]]] = {}
    reports: list[dict] = []
    warnings: list[str] = []
    jpeg_copies: dict[str, str] = {}
    loose: list[str] = []
    readme: dict = {}
    htm: dict = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        parts = PurePosixPath(rel.lower()).parts
        suffix = path.suffix.lower()
        if is_dicom(path):
            try:
                ds = pydicom.dcmread(path, stop_before_pixels=True)
            except Exception:
                warnings.append(f"{rel}: not a readable DICOM file")
                continue
            uid = _text(ds, "StudyInstanceUID") or "unknown"
            if _text(ds, "SOPClassUID") == ENCAPSULATED_PDF:
                pdf_rel = _extract_encapsulated(root, path, rel)
                report = pdf_rel and _report(root, pdf_rel, study_key=file_key(uid), preselected=True)
                if report:
                    reports.append(report)
                continue
            if "Rows" not in ds:
                continue  # structured report, presentation state, ...: no pixels
            image = {
                "key": file_key(rel),
                "path": rel,
                "kind": "dicom",
                "label": _image_label(ds, rel),
                "readable": is_decodable(str(ds.file_meta.TransferSyntaxUID)),
                "frames": int(ds.get("NumberOfFrames") or 1),
            }
            order = (int(ds.get("SeriesNumber") or 0), int(ds.get("InstanceNumber") or 0), rel)
            groups.setdefault(uid, []).append((order, image, ds))
        elif parts == ("readme.txt",):
            readme = parse_readme(path.read_text(errors="replace"))
        elif parts[-1] == "study000.htm":
            htm = parse_study_htm(path.read_text(errors="replace"))
        elif suffix == ".pdf":
            if not is_viewer_manual(rel):
                preselected = "report" in parts[:-1] or parts[0] == MANUAL_DIR
                report = _report(root, rel, study_key=None, preselected=preselected)
                if report:
                    reports.append(report)
        elif suffix in IMAGE_EXTENSIONS:
            if parts[0] == MANUAL_DIR:
                loose.append(rel)
            elif parts[0] == "ihe_pdi" and len(parts) > 2:
                jpeg_copies["/".join(parts[1:])[: -len(suffix)]] = rel  # IHE_PDI/<rest>.jpg ↔ DICOM/<rest>

    studies: list[tuple[str, dict, list[dict]]] = []
    for uid, entries in groups.items():
        entries.sort(key=lambda e: e[0])
        images = [image for _, image, _ in entries]
        if not any(image["readable"] for image in images):
            copies = [jpeg_copies.get("/".join(PurePosixPath(i["path"].lower()).parts[1:])) for i in images]
            if any(copies):
                images = [
                    {**i, "path": c, "kind": "image", "readable": True, "frames": 1, "key": file_key(c)}
                    for i, c in zip(images, copies)
                    if c
                ]
                warnings.append(JPEG_COPY_WARNING)
        studies.append((file_key(uid), _study_fields(entries[0][2]), images))

    loose_images = [
        {"key": file_key(rel), "path": rel, "kind": "image", "label": PurePosixPath(rel).name,
         "readable": True, "frames": 1}
        for rel in loose
    ]
    if loose_images:
        if len(studies) == 1:
            studies[0][2].extend(loose_images)
        else:
            studies.append(("manual", {key: "" for key in FIELD_KEYS}, loose_images))

    if len(studies) == 1:
        _fill_from_vendor(studies[0][1], readme, htm)
        for report in reports:
            report["study_key"] = studies[0][0]

    return {
        "no_study": not studies,
        "studies": [_finish_study(key, fields, images) for key, fields, images in studies],
        "reports": reports,
        "warnings": list(dict.fromkeys(warnings)),
    }
```

- [ ] **Step 4: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_dicom_parse.py -v`
Expected: PASS.

For `test_manual_files_form_a_study`: the loose `manual-b.png` at the root is not under `manual/`, so it is ignored. With no study, the `manual` study has no fields and the title is `"X-ray"`. Its report is preselected (under `manual/`), and since there is exactly one study the report is assigned to it.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add backend/app/services/dicom_parse.py backend/tests/test_dicom_parse.py
git commit -m "feat: parse staged X-ray discs into studies, images, report candidates and proposed metadata"
```

---

### Task 8: Disc import API

**Files:**
- Modify: `backend/app/services/storage.py` (disc import dirs)
- Create: `backend/app/api/disc_imports.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_disc_imports_api.py`

**Interfaces:**
- Consumes:
  - `DiscImport`, `DiscImportStatus` (Task 1)
  - `create_pending_document(..., notes, extra_metadata)` (Task 2)
  - `build_preview` (Task 7), `thumbnail_jpeg` (Task 5)
- Produces:
  - `Storage.disc_imports_dir` (property), `disc_import_dir(import_id) -> Path` (creates it), `disc_import_path(import_id) -> Path` (no mkdir), `remove_disc_import_dir(import_id)`
  - Endpoints:
    - `POST /api/disc-imports` → `{"id"}` (201)
    - `POST /api/disc-imports/{id}/files` (multipart `files[]`, `paths[]`) → preview
    - `GET /api/disc-imports/{id}` → preview
    - `GET /api/disc-imports/{id}/images/{key}/thumb` → `image/jpeg` (`?token=` auth)
    - `GET /api/disc-imports/{id}/reports/{key}/file` → `application/pdf` inline (`?token=` auth)
    - `DELETE /api/disc-imports/{id}` → 204
    - `POST /api/disc-imports/{id}/commit` → `{"documents": [document JSON]}`
  - Preview response = `build_preview` output, plus `id`, plus `patient_tag_id: int | None` on each study.
  - The `process_document` payload for a study:

    ```
    {"document_id", "disc_import_id",
     "images": [{"path", "kind", "label"}],
     "reports": [{"path"}]}
    ```

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_disc_imports_api.py`:

```python
import io
from datetime import datetime, timedelta, timezone

from PIL import Image
from sqlmodel import select

from app.models import DiscImport, DiscImportStatus, Document, Job, Tag, User
from app.services.auth import create_access_token, hash_password
from tests.helpers import make_dicom, make_text_pdf

REPORT = ["Referto: frattura saldata della tibia destra con callo osseo, nessuna mobilizzazione dei mezzi."]


def disc_files(tmp_path):
    """(path inside the disc, bytes) of a small standard disc."""
    a = make_dicom(tmp_path / "src/a", series_number=1).read_bytes()
    b = make_dicom(tmp_path / "src/b", series_number=2, SeriesDescription="Lateral").read_bytes()
    report = make_text_pdf(tmp_path / "src/r.pdf", REPORT).read_bytes()
    return [("DICOM/1/a", a), ("DICOM/1/b", b), ("REPORT/r.pdf", report), ("README.TXT", b"Sesso: M\n")]


def create(client) -> str:
    resp = client.post("/api/disc-imports")
    assert resp.status_code == 201
    return resp.json()["id"]


def send(client, import_id, files):
    return client.post(
        f"/api/disc-imports/{import_id}/files",
        files=[("files", (path.rsplit("/", 1)[-1], data, "application/octet-stream")) for path, data in files],
        data={"paths": [path for path, _ in files]},
    )


def staged(client, tmp_path):
    import_id = create(client)
    resp = send(client, import_id, disc_files(tmp_path))
    assert resp.status_code == 200
    return import_id, resp.json()


def test_stage_files_returns_preview(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    assert preview["id"] == import_id
    [study] = preview["studies"]
    assert study["patient_tag"] == "Mario Rossi"
    assert study["patient_tag_id"] is None
    assert len(study["images"]) == 2
    assert preview["reports"][0]["preselected"] is True
    assert storage.disc_import_path(import_id).joinpath("DICOM/1/a").is_file()
    assert auth_client.get(f"/api/disc-imports/{import_id}").json()["studies"][0]["key"] == study["key"]


def test_preview_finds_existing_patient_tag(auth_client, session, storage, tmp_path):
    tag = Tag(name="mario rossi")
    session.add(tag)
    session.commit()
    _, preview = staged(auth_client, tmp_path)
    assert preview["studies"][0]["patient_tag_id"] == tag.id


def test_files_in_several_batches_accumulate(auth_client, session, storage, tmp_path):
    import_id = create(auth_client)
    files = disc_files(tmp_path)
    send(auth_client, import_id, files[:1])
    preview = send(auth_client, import_id, files[1:]).json()
    assert len(preview["studies"][0]["images"]) == 2


def test_files_rejects_unsafe_paths(auth_client, session, storage, tmp_path):
    for bad in ("../escape", "/etc/x", "C:/x", ".thumbs/x", "a/../../b", ""):
        import_id = create(auth_client)
        resp = send(auth_client, import_id, [(bad, b"x")])
        assert resp.status_code == 400, bad
        assert resp.json()["error"]["code"] == "invalid_path"
        assert not storage.disc_import_path(import_id).exists()
    assert not (storage.tmp_root / "escape").exists()


def test_files_and_paths_must_match(auth_client, session, storage):
    import_id = create(auth_client)
    resp = auth_client.post(
        f"/api/disc-imports/{import_id}/files",
        files=[("files", ("a", b"x", "application/octet-stream"))],
        data={"paths": ["a", "b"]},
    )
    assert resp.status_code == 422


def test_thumbnail_and_report_preview_accept_token(client, user, session, storage, tmp_path):
    from app.services.auth import create_access_token as token_for

    client.headers["Authorization"] = f"Bearer {token_for(user.id)}"
    import_id, preview = staged(client, tmp_path)
    del client.headers["Authorization"]
    token = token_for(user.id)
    image_key = preview["studies"][0]["images"][0]["key"]
    resp = client.get(f"/api/disc-imports/{import_id}/images/{image_key}/thumb?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(resp.content)).size) <= 256
    report_key = preview["reports"][0]["key"]
    resp = client.get(f"/api/disc-imports/{import_id}/reports/{report_key}/file?token={token}")
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF")
    assert client.get(f"/api/disc-imports/{import_id}/images/nope/thumb?token={token}").status_code == 404


def test_other_users_import_is_404(auth_client, session, storage):
    other = User(username="other", password_hash=hash_password("x"))
    session.add(other)
    session.commit()
    imp = DiscImport(user_id=other.id)
    session.add(imp)
    session.commit()
    assert auth_client.get(f"/api/disc-imports/{imp.id}").status_code == 404


def test_expired_import_is_410(auth_client, session, storage, user):
    imp = DiscImport(user_id=user.id, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
    session.add(imp)
    session.commit()
    resp = auth_client.get(f"/api/disc-imports/{imp.id}")
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "import_expired"


def test_cancel_removes_staging(auth_client, session, storage, tmp_path):
    import_id, _ = staged(auth_client, tmp_path)
    assert auth_client.delete(f"/api/disc-imports/{import_id}").status_code == 204
    assert not storage.disc_import_path(import_id).exists()
    assert session.get(DiscImport, import_id).status == DiscImportStatus.cancelled


def commit_body(preview, **over):
    study = preview["studies"][0]
    body = {
        "studies": [{
            "study_key": study["key"],
            "title": "Rx gamba destra",
            "document_date": "2026-08-28",
            "notes": "Controllo",
            "folder_id": None,
            "tag_ids": [],
            "image_keys": [i["key"] for i in study["images"]],
            "report_keys": [preview["reports"][0]["key"]],
        }],
        "ocr_enabled": False,
        "summary_enabled": True,
        "translation_enabled": False,
    }
    body.update(over)
    return body


def test_commit_creates_documents_and_jobs(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=commit_body(preview))
    assert resp.status_code == 200
    [doc_json] = resp.json()["documents"]
    assert doc_json["title"] == "Rx gamba destra"
    assert doc_json["notes"] == "Controllo"
    assert doc_json["doc_type"] == "pdf"
    assert doc_json["ocr_enabled"] is False and doc_json["summary_enabled"] is True
    assert doc_json["extra_metadata"]["patient_name"] == "Mario Rossi"
    assert doc_json["description"] == ""  # the AI summary fills it
    job = session.exec(select(Job)).one()
    assert job.payload["disc_import_id"] == import_id
    assert job.payload["images"][0] == {"path": "DICOM/1/a", "kind": "dicom", "label": "AP"}
    assert job.payload["reports"] == [{"path": "REPORT/r.pdf"}]
    assert session.get(DiscImport, import_id).status == DiscImportStatus.committed
    assert auth_client.post(f"/api/disc-imports/{import_id}/commit", json=commit_body(preview)).status_code == 409


def test_commit_without_summary_fills_description(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    resp = auth_client.post(
        f"/api/disc-imports/{import_id}/commit", json=commit_body(preview, summary_enabled=False)
    )
    assert resp.json()["documents"][0]["description"] == "Rx gamba (tibia e perone) Destro/a · Ospedale Test"


def test_commit_rejects_unknown_keys_before_creating_anything(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    body = commit_body(preview)
    body["studies"][0]["image_keys"] = ["nope"]
    resp = auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body)
    assert resp.status_code == 422
    assert session.exec(select(Document)).all() == []


def test_commit_rejects_unknown_folder(auth_client, session, storage, tmp_path):
    import_id, preview = staged(auth_client, tmp_path)
    body = commit_body(preview)
    body["studies"][0]["folder_id"] = 999
    assert auth_client.post(f"/api/disc-imports/{import_id}/commit", json=body).status_code == 404
    assert session.exec(select(Document)).all() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_disc_imports_api.py -v`
Expected: FAIL with 404 on `POST /api/disc-imports`.

- [ ] **Step 3: Storage helpers**

In `backend/app/services/storage.py`, after `remove_scan_session_dir`, add:

```python
    # --- disc imports ---
    @property
    def disc_imports_dir(self) -> Path:
        return self.tmp_root / "disc_imports"

    def disc_import_dir(self, import_id) -> Path:
        d = self.disc_imports_dir / str(import_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def disc_import_path(self, import_id) -> Path:
        """Staging dir of an import without creating it (the worker checks it still exists)."""
        return self.disc_imports_dir / str(import_id)

    def remove_disc_import_dir(self, import_id) -> None:
        shutil.rmtree(self.disc_imports_dir / str(import_id), ignore_errors=True)
```

- [ ] **Step 4: Write the router**

Create `backend/app/api/disc_imports.py`:

```python
"""Import an X-ray disc: stage its files, preview the studies, then create one document per study."""

import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user, get_current_user_flexible
from app.api.documents import serialize
from app.api.ocr import check_ocr_languages, check_translation_language, default_ocr_languages
from app.api.uploads import create_pending_document
from app.db import get_session
from app.models import DiscImport, DiscImportStatus, DocType, Folder, Tag, User
from app.services.dicom_parse import build_preview
from app.services.dicom_render import thumbnail_jpeg
from app.services.jobs import enqueue
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/api/disc-imports", tags=["disc-imports"])

IMPORT_MAX_AGE = timedelta(hours=24)
THUMBS_DIR = ".thumbs"
EMPTY_PREVIEW = {"no_study": True, "studies": [], "reports": [], "warnings": []}


class StudyCommit(BaseModel):
    study_key: str
    title: str = Field(min_length=1)
    document_date: date | None = None
    notes: str = ""
    folder_id: int | None = None
    tag_ids: list[int] = []
    image_keys: list[str] = Field(min_length=1)
    report_keys: list[str] = []


class CommitRequest(BaseModel):
    studies: list[StudyCommit] = Field(min_length=1)
    ocr_languages: str | None = None
    ocr_enabled: bool = False
    summary_enabled: bool = True
    translation_enabled: bool = False
    translation_language: str | None = None


def staged_path(root: Path, rel: str) -> Path:
    """Target inside the staging dir; ValueError for absolute, parent, drive or dot-prefixed paths."""
    rel = rel.replace("\\", "/")
    parts = PurePosixPath(rel).parts
    if (
        not parts
        or rel.startswith("/")
        or ":" in parts[0]
        or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
    ):
        raise ValueError(f"Invalid path {rel!r}")
    return root.joinpath(*parts)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_import(session: Session, import_id: uuid.UUID, user: User, *, staging: bool = True) -> DiscImport:
    imp = session.get(DiscImport, import_id)
    if imp is None or imp.user_id != user.id:
        raise api_error(404, "not_found", "Import not found")
    if _aware(imp.created_at) < datetime.now(timezone.utc) - IMPORT_MAX_AGE:
        raise api_error(410, "import_expired", "This import has expired; select the disc again")
    if staging and imp.status != DiscImportStatus.staging:
        raise api_error(409, "import_closed", "This import is already committed or cancelled")
    return imp


def preview_response(session: Session, imp: DiscImport) -> dict:
    preview = imp.preview or EMPTY_PREVIEW
    tags = {t.name.casefold(): t.id for t in session.exec(select(Tag))}
    studies = [
        {**s, "patient_tag_id": tags.get((s.get("patient_tag") or "").casefold())} for s in preview["studies"]
    ]
    return {**preview, "id": str(imp.id), "studies": studies}


def _find_image(imp: DiscImport, key: str) -> dict:
    for study in (imp.preview or EMPTY_PREVIEW)["studies"]:
        for image in study["images"]:
            if image["key"] == key:
                return image
    raise api_error(404, "not_found", "Image not found")


def _find_report(imp: DiscImport, key: str) -> dict:
    for report in (imp.preview or EMPTY_PREVIEW)["reports"]:
        if report["key"] == key:
            return report
    raise api_error(404, "not_found", "Report not found")


def _cancel(session: Session, storage: Storage, imp: DiscImport) -> None:
    imp.status = DiscImportStatus.cancelled
    session.commit()
    storage.remove_disc_import_dir(imp.id)


@router.post("", status_code=201)
def create_import(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    imp = DiscImport(user_id=user.id)
    session.add(imp)
    session.commit()
    storage.disc_import_dir(imp.id)
    return {"id": str(imp.id)}


@router.post("/{import_id}/files")
def add_files(
    import_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> dict:
    imp = get_import(session, import_id, user)
    if len(files) != len(paths):
        raise api_error(422, "validation_error", "Send one path per file")
    root = storage.disc_import_dir(imp.id)
    try:
        targets = [staged_path(root, rel) for rel in paths]
    except ValueError as exc:
        _cancel(session, storage, imp)
        raise api_error(400, "invalid_path", str(exc))
    for upload, target in zip(files, targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    imp.preview = build_preview(root)
    session.commit()
    return preview_response(session, imp)


@router.get("/{import_id}")
def get_preview(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    return preview_response(session, get_import(session, import_id, user, staging=False))


@router.get("/{import_id}/images/{key}/thumb")
def image_thumb(
    import_id: uuid.UUID,
    key: str,
    user: User = Depends(get_current_user_flexible),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    imp = get_import(session, import_id, user, staging=False)
    image = _find_image(imp, key)
    root = storage.disc_import_path(imp.id)
    cached = root / THUMBS_DIR / f"{key}.jpg"
    if not cached.is_file():
        try:
            data = thumbnail_jpeg(root / image["path"], image["kind"])
        except Exception:
            raise api_error(422, "unreadable_image", "This image cannot be decoded")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return FileResponse(cached, media_type="image/jpeg")


@router.get("/{import_id}/reports/{key}/file")
def report_file(
    import_id: uuid.UUID,
    key: str,
    user: User = Depends(get_current_user_flexible),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    imp = get_import(session, import_id, user, staging=False)
    report = _find_report(imp, key)
    return FileResponse(
        storage.disc_import_path(imp.id) / report["path"],
        media_type="application/pdf",
        filename=report["filename"],
        content_disposition_type="inline",
    )


@router.delete("/{import_id}", status_code=204)
def cancel_import(
    import_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    _cancel(session, storage, get_import(session, import_id, user))


@router.post("/{import_id}/commit")
def commit_import(
    import_id: uuid.UUID,
    body: CommitRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    imp = get_import(session, import_id, user)
    if body.ocr_enabled:
        check_ocr_languages(body.ocr_languages)
    check_translation_language(body.translation_language)
    preview = imp.preview or EMPTY_PREVIEW
    studies = {s["key"]: s for s in preview["studies"]}
    reports = {r["key"]: r for r in preview["reports"]}

    # validate everything first: a bad second study must not leave the first one created
    for item in body.studies:
        study = studies.get(item.study_key)
        if study is None:
            raise api_error(422, "validation_error", f"Unknown study {item.study_key!r}")
        readable = {i["key"] for i in study["images"] if i["readable"]}
        if not set(item.image_keys) <= readable:
            raise api_error(422, "validation_error", "Unknown or unreadable image selected")
        if not set(item.report_keys) <= reports.keys():
            raise api_error(422, "validation_error", "Unknown report selected")
        if item.folder_id is not None and session.get(Folder, item.folder_id) is None:
            raise api_error(404, "not_found", "Folder not found")
        for tag_id in item.tag_ids:
            if session.get(Tag, tag_id) is None:
                raise api_error(404, "not_found", f"Tag {tag_id} not found")

    documents = []
    for item in body.studies:
        study = studies[item.study_key]
        images = {i["key"]: i for i in study["images"]}
        doc = create_pending_document(
            session,
            title=item.title.strip(),
            doc_type=DocType.pdf,
            ocr_languages=body.ocr_languages or default_ocr_languages(),
            ocr_enabled=body.ocr_enabled,
            summary_enabled=body.summary_enabled,
            translation_enabled=body.translation_enabled,
            translation_language=body.translation_language,
            folder_id=item.folder_id,
            tag_ids=item.tag_ids,
            original_filename=None,
            description="" if body.summary_enabled else study["description_fallback"],
            document_date=item.document_date,
            notes=item.notes,
            extra_metadata=study["extra_metadata"],
        )
        payload = {
            "document_id": str(doc.id),
            "disc_import_id": str(imp.id),
            "images": [
                {"path": images[k]["path"], "kind": images[k]["kind"], "label": images[k]["label"]}
                for k in item.image_keys
            ],
            "reports": [{"path": reports[k]["path"]} for k in item.report_keys],
        }
        enqueue(session, "process_document", payload)
        documents.append(doc)
    imp.status = DiscImportStatus.committed
    session.commit()
    return {"documents": [serialize(session, d) for d in documents]}
```

In `backend/app/main.py`, add `disc_imports` to the `from app.api import ...` line and add `app.include_router(disc_imports.router)` after `chat.router`. Keep the routers in alphabetical order.

- [ ] **Step 5: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_disc_imports_api.py -v`
Expected: PASS.

If `test_files_rejects_unsafe_paths` fails for `""`: python-multipart drops empty form values, so `paths` ends up empty and the length check returns 422. In that case change the expectation for `""` to `in (400, 422)` and keep the others at 400.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami && git add backend/app/services/storage.py backend/app/api/disc_imports.py backend/app/main.py backend/tests/test_disc_imports_api.py
git commit -m "feat: disc import API: staging, preview, thumbnails, report preview, cancel and commit"
```

---

### Task 9: Worker: study documents, staging cleanup, sweep

**Files:**
- Modify: `backend/app/worker/pipeline.py`
- Test: `backend/tests/test_study_pipeline.py`

**Interfaces:**
- Consumes:
  - `render` (Task 5), `PageImage`, `build_study_pdf` (Task 6), `cover_rows`, `metadata_text` (Task 2)
  - `AttachmentSource`, `write_bundle` (Task 3), `REPORT_FILENAME` (Task 7)
  - `Storage.disc_import_path`, `remove_disc_import_dir` (Task 8)
  - The job payload from Task 8.
- Produces:
  - `_extract_content` routes study documents (`extra_metadata is not None` or `disc_import_id` in the payload) to `_extract_study`.
  - Summary input for study documents.
  - `sweep_scan_sessions` also removes disc imports older than 24 h.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_study_pipeline.py`:

```python
from datetime import date, datetime, timedelta, timezone

import pytest
from pypdf import PdfReader
from sqlmodel import select

from app.models import (
    Chunk, ChunkSource, DiscImport, DocStatus, DocType, Document, DocumentAttachment, Job, JobStatus,
)
from app.services.storage import Storage
from app.worker import pipeline
from tests.helpers import make_dicom, make_text_pdf

REPORT = ["Referto: frattura pressoche saldata con callo osseo al terzo medio della tibia destra."]
META = {"patient_name": "Mario Rossi", "modality": "DX", "series": [{"description": "AP", "images": 2}]}


@pytest.fixture
def pipeline_storage(tmp_path, monkeypatch):
    s = Storage(tmp_path / "storage")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: s)
    return s


@pytest.fixture
def disc(session, user, pipeline_storage):
    imp = DiscImport(user_id=user.id)
    session.add(imp)
    session.commit()
    root = pipeline_storage.disc_import_dir(imp.id)
    make_dicom(root / "DICOM/a", series_number=1)
    make_dicom(root / "DICOM/b", series_number=2, frames=2)
    make_text_pdf(root / "REPORT/r.pdf", REPORT)
    make_text_pdf(root / "REPORT/scan.pdf", [])
    return imp


def make_study_doc(session, **kw):
    doc = Document(
        title=kw.pop("title", "Rx gamba"), doc_type=DocType.pdf, extra_metadata=META,
        document_date=date(2026, 8, 28), ocr_enabled=kw.pop("ocr_enabled", False), **kw,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def payload(doc, imp, reports=("REPORT/r.pdf",)):
    return {
        "document_id": str(doc.id),
        "disc_import_id": str(imp.id),
        "images": [
            {"path": "DICOM/a", "kind": "dicom", "label": "AP"},
            {"path": "DICOM/b", "kind": "dicom", "label": "Lateral"},
        ],
        "reports": [{"path": p} for p in reports],
    }


def run(session, doc, data):
    pipeline.process_document(session, data)
    session.refresh(doc)
    return doc


def test_bundle_pdf_chunks_summary_and_cleanup(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc))
    assert doc.status == DocStatus.ready
    assert doc.file_path == "Rx gamba/Rx gamba.pdf"
    assert doc.page_count == 4  # cover + 1 + 2 frames
    assert doc.ocr_applied is False
    reader = PdfReader(pipeline_storage.abs_path(doc.file_path))
    assert len(reader.pages) == 4
    [att] = session.exec(select(DocumentAttachment)).all()
    assert (att.filename, att.file_path, att.page_count) == ("Report.pdf", "Rx gamba/Report.pdf", 1)
    content = session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()
    assert content and all(c.attachment_id == att.id for c in content)
    [call] = llm_stub["describe"]
    assert call["image_path"] is None
    assert "tibia destra" in call["text"] and "Patient: Mario Rossi" in call["text"]
    assert not pipeline_storage.disc_import_path(disc.id).exists()


def test_study_without_report_is_flat(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc, reports=()))
    assert doc.file_path == "Rx gamba.pdf"
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all() == []
    [call] = llm_stub["describe"]
    assert call["text"].startswith("Patient: Mario Rossi")


def test_textless_report_needs_ocr(session, pipeline_storage, disc, llm_stub, monkeypatch):
    calls = []

    def fake_ocr(path, languages):
        calls.append(path)
        return b"%PDF ocr", [(1, "Testo riconosciuto dal referto scansionato, abbastanza lungo.")]

    monkeypatch.setattr(pipeline, "pdf_to_searchable_pdf", fake_ocr)
    doc = make_study_doc(session)
    run(session, doc, payload(doc, disc, reports=("REPORT/scan.pdf",)))
    assert calls == []  # OCR off: no text, no OCR
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all() == []

    other = make_study_doc(session, title="Con OCR", ocr_enabled=True)
    imp2 = DiscImport(user_id=disc.user_id)
    session.add(imp2)
    session.commit()
    root = pipeline_storage.disc_import_dir(imp2.id)
    make_dicom(root / "DICOM/a")
    make_text_pdf(root / "REPORT/scan.pdf", [])
    run(session, other, payload(other, imp2, reports=("REPORT/scan.pdf",)) | {"images": [{"path": "DICOM/a", "kind": "dicom", "label": "AP"}]})
    assert len(calls) == 1
    [chunk] = session.exec(
        select(Chunk).where(Chunk.document_id == other.id, Chunk.source == ChunkSource.content)
    ).all()
    assert chunk.content.startswith("Testo riconosciuto")
    assert pipeline_storage.abs_path("Con OCR/Report.pdf").read_bytes().startswith(b"%PDF-1.4")  # original kept


def test_retry_does_not_rebuild_bundle(session, pipeline_storage, disc, llm_stub, monkeypatch):
    original_embed = pipeline.llm_embed
    failures = {"left": 1}

    def flaky_embed(texts, role="document"):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("embedding provider down")
        return original_embed(texts, role)

    monkeypatch.setattr(pipeline, "llm_embed", flaky_embed)
    doc = make_study_doc(session)
    data = payload(doc, disc) | {"_final_attempt": False}
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, data)
    session.refresh(doc)
    assert doc.status == DocStatus.pending
    doc = run(session, doc, payload(doc, disc))
    assert doc.status == DocStatus.ready
    assert doc.file_path == "Rx gamba/Rx gamba.pdf"
    assert len(session.exec(select(DocumentAttachment)).all()) == 1
    assert not pipeline_storage.abs_path("Rx gamba (2)").exists()


def test_other_pending_job_keeps_staging(session, pipeline_storage, disc, llm_stub):
    first, second = make_study_doc(session), make_study_doc(session, title="Second")
    session.add(Job(type="process_document", payload=payload(second, disc), run_at=datetime.now(timezone.utc)))
    session.commit()
    run(session, first, payload(first, disc))
    assert pipeline_storage.disc_import_path(disc.id).exists()


def test_reprocess_keeps_image_pdf(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc))
    before = pipeline_storage.abs_path(doc.file_path).read_bytes()
    for chunk in session.exec(select(Chunk).where(Chunk.document_id == doc.id)):
        session.delete(chunk)
    doc.summary = None
    session.commit()
    doc = run(session, doc, {"document_id": str(doc.id), "force_ocr": True})
    assert doc.status == DocStatus.ready
    assert pipeline_storage.abs_path(doc.file_path).read_bytes() == before
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()


def test_missing_staging_fails_with_clear_message(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    pipeline_storage.remove_disc_import_dir(disc.id)
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, payload(doc, disc))
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "import the disc again" in doc.error_message


def test_sweep_removes_old_imports(session, pipeline_storage, disc, llm_stub):
    disc.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    session.commit()
    pipeline.sweep_scan_sessions(session, {})
    assert session.get(DiscImport, disc.id) is None
    assert not pipeline_storage.disc_import_path(disc.id).exists()


def test_sweep_keeps_imports_with_jobs_in_flight(session, pipeline_storage, disc, llm_stub):
    disc.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    doc = make_study_doc(session)
    session.add(Job(type="process_document", payload=payload(doc, disc), run_at=datetime.now(timezone.utc)))
    session.commit()
    pipeline.sweep_scan_sessions(session, {})
    assert session.get(DiscImport, disc.id) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest tests/test_study_pipeline.py -v`
Expected: FAIL. The study documents go down the plain `pdf` branch and fail on `storage.abs_path(None)`.

- [ ] **Step 3: Implement the study branch**

In `backend/app/worker/pipeline.py`, extend the imports:

```python
from app.models import (
    AttachmentRole, Chunk, ChunkSource, DiscImport, DocStatus, DocType, Document, DocumentAttachment, Job,
    JobStatus, TranslationSegment, TranslationStatus,
)
from app.services.dicom_parse import REPORT_FILENAME
from app.services.dicom_render import render
from app.services.study_metadata import cover_rows, metadata_text
from app.services.study_pdf import PageImage, build_study_pdf
from app.services.tree_sync import AttachmentSource, write_bundle, write_document_file
```

`metadata_text` is already imported from Task 2; keep a single import line. The function-local `from app.models import ...` lines in `sweep_scan_sessions` and `ensure_sweep_scheduled` stay as they are.

In `_extract_content`, right after the first `if doc.doc_type == DocType.video or _has_chunks(...)` check, add:

```python
    if doc.extra_metadata is not None or "disc_import_id" in payload:
        return _extract_study(session, doc, storage, payload)
```

Add after `_extract_scan`:

```python
def _extract_study(
    session: Session, doc: Document, storage: Storage, payload: dict
) -> list[tuple[int | None, str]]:
    """Imported X-ray study: build the image PDF (and bundle) once; index only the report text."""
    if not (doc.file_path and storage.abs_path(doc.file_path).exists()):
        _build_study_files(session, doc, storage, payload)
    _release_disc_import(session, storage, payload)
    _insert_attachment_chunks(session, doc, storage)
    return []  # content chunks are written above, tagged with their attachment


def _build_study_files(session: Session, doc: Document, storage: Storage, payload: dict) -> None:
    root = storage.disc_import_path(payload.get("disc_import_id", "-"))
    if "disc_import_id" not in payload or not root.is_dir():
        raise RuntimeError("The staged disc files are no longer available; import the disc again")
    rendered = [
        (image, spec["label"])
        for spec in payload["images"]
        for image in render(root / spec["path"], spec["kind"])
    ]
    day = doc.document_date.isoformat()
    pages = [
        PageImage(image, " · ".join([label, day, f"image {n}/{len(rendered)}"]))
        for n, (image, label) in enumerate(rendered, start=1)
    ]
    pdf = build_study_pdf(doc.title, cover_rows(doc.extra_metadata or {}, doc.document_date), doc.notes, pages)
    reports = [
        AttachmentSource(
            role=AttachmentRole.report,
            filename=REPORT_FILENAME,
            source=root / report["path"],
            page_count=len(extract_pdf_text(root / report["path"])),
        )
        for report in payload["reports"]
    ]
    if reports:
        write_bundle(session, storage, doc, ".pdf", pdf, reports)
    else:
        write_document_file(session, storage, doc, ".pdf", pdf)
    doc.page_count = 1 + len(rendered)
    doc.ocr_applied = False
    session.commit()


def _release_disc_import(session: Session, storage: Storage, payload: dict) -> None:
    """Remove the staging dir once no other job of the same import still needs it."""
    import_id = payload.get("disc_import_id")
    if not import_id:
        return
    other = session.exec(
        select(Job.id).where(
            Job.type == "process_document",
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["disc_import_id"].astext == import_id,
            Job.payload["document_id"].astext != payload["document_id"],
        )
    ).first()
    if other is None:
        storage.remove_disc_import_dir(import_id)


def _insert_attachment_chunks(session: Session, doc: Document, storage: Storage) -> None:
    """Content chunks from the attachment PDFs; OCR only when enabled and the PDF has no text layer."""
    next_index = _next_chunk_index(session, doc)
    attachments = session.exec(
        select(DocumentAttachment)
        .where(DocumentAttachment.document_id == doc.id)
        .order_by(DocumentAttachment.id)
    ).all()
    for attachment in attachments:
        path = storage.abs_path(attachment.file_path)
        pages = extract_pdf_text(path)
        if pdf_needs_ocr(pages):
            if not doc.ocr_enabled:
                continue  # the import preview warned that this report has no text
            _, pages = pdf_to_searchable_pdf(path, doc.ocr_languages)  # text only: the original stays
        for chunk in chunk_pages(pages):
            session.add(
                Chunk(
                    document_id=doc.id,
                    attachment_id=attachment.id,
                    chunk_index=next_index,
                    page_number=chunk["page_number"],
                    source=ChunkSource.content,
                    content=chunk["content"],
                )
            )
            next_index += 1
    session.commit()
```

In `_ensure_summary`, after the `text = ...` line, add:

```python
    if doc.extra_metadata is not None:
        # X-ray study: summarize the report and the study data, never the images through vision
        text = "\n\n".join(filter(None, [text, metadata_text(doc.extra_metadata)]))
```

- [ ] **Step 4: Sweep old disc imports**

In `sweep_scan_sessions`, after the scan session loop and before `remove_part_files`, add:

```python
    imports_in_flight = {
        job.payload.get("disc_import_id") for job in in_flight_jobs if job.payload.get("disc_import_id")
    }
    for imp in session.exec(select(DiscImport)).all():
        created = imp.created_at if imp.created_at.tzinfo else imp.created_at.replace(tzinfo=timezone.utc)
        if created >= cutoff or str(imp.id) in imports_in_flight:
            continue
        import_id = imp.id
        session.delete(imp)
        session.commit()
        storage.remove_disc_import_dir(import_id)
```

- [ ] **Step 5: Run the tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_study_pipeline.py tests/test_pipeline.py tests/test_reprocess.py tests/test_scan_compile.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole backend suite**

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami && git add backend/app/worker/pipeline.py backend/tests/test_study_pipeline.py
git commit -m "feat: worker builds study PDF and bundle, indexes report text, summarizes report plus study data"
```

---

### Task 10: Frontend types, links and disc import logic

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts` (`attachmentUrl`, `discThumbUrl`, `discReportUrl`)
- Create: `frontend/src/lib/discImport.ts`, `frontend/src/lib/discImport.test.ts`
- Create: `frontend/src/lib/documentView.ts`, `frontend/src/lib/documentView.test.ts`
- Modify: `frontend/src/components/DocumentCard.test.tsx` (fixture fields)

**Interfaces:**
- Consumes: backend JSON from Tasks 4 and 8.
- Produces:
  - `types.ts`:
    - `Attachment`, `DiscImage`, `DiscReport`, `DiscStudy`, `DiscPreview`
    - `Document.notes`, `Document.extra_metadata`, `Document.attachments`
    - `SearchSnippet.attachment_id`, `ChatSource.attachment_id?`
  - `api.ts`:
    - `attachmentUrl(documentId: string, attachmentId: number, opts?: { download?: boolean }): string`
    - `discThumbUrl(importId: string, key: string): string`
    - `discReportUrl(importId: string, key: string): string`
  - `discImport.ts`:
    - constants `DISC_MAX_FILE_BYTES`, `DISC_BATCH_BYTES`, `DISC_BATCH_FILES`
    - `StagedFile` (`{ file: File; path: string }`)
    - file selection: `discPath(relativePath)`, `keepDiscFile(path, size)`, `discFiles(picked)`, `manualFiles(files)`
    - upload: `uploadBatches(files, maxBytes?, maxFiles?)`, `batchForm(batch)`
    - `discProcessing()`
    - drafts: `StudyDraft`, `draftFromStudy(study, reports, folderId)`, `mergeDrafts(previous, before, after, folderId)`, `reportsFor(study, reports)`
    - commit: `canImport(drafts)`, `commitBody(drafts, preview, patientTagIds, processing)`
  - `documentView.ts`:
    - `ViewTab`, `viewTabs(doc)`, `activeViewTab(tabs, param)`
    - `documentLink(documentId, attachmentId?)`
    - `STUDY_FIELDS`, `studyRows(meta)`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/discImport.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  batchForm,
  canImport,
  commitBody,
  discFiles,
  discPath,
  discProcessing,
  draftFromStudy,
  keepDiscFile,
  manualFiles,
  mergeDrafts,
  reportsFor,
  uploadBatches,
  DISC_MAX_FILE_BYTES,
  type StagedFile,
} from "./discImport";
import type { DiscPreview, DiscStudy } from "./types";

const file = (name: string, size = 1) => new File([new Uint8Array(size)], name);

const study = (over: Partial<DiscStudy> = {}): DiscStudy => ({
  key: "s1",
  title: "Rx gamba — 2026-08-28",
  document_date: "2026-08-28",
  description_fallback: "Rx gamba · Ospedale",
  patient_tag: "Mario Rossi",
  patient_tag_id: null,
  extra_metadata: {},
  images: [
    { key: "i1", path: "DICOM/a", kind: "dicom", label: "AP", readable: true, frames: 1 },
    { key: "i2", path: "DICOM/b", kind: "dicom", label: "Lateral", readable: true, frames: 1 },
    { key: "i3", path: "DICOM/c", kind: "dicom", label: "Odd", readable: false, frames: 1 },
  ],
  ...over,
});

const preview = (over: Partial<DiscPreview> = {}): DiscPreview => ({
  id: "imp",
  no_study: false,
  studies: [study()],
  reports: [
    { key: "r1", path: "REPORT/r.pdf", filename: "r.pdf", page_count: 1, has_text: true, study_key: "s1", preselected: true },
    { key: "r2", path: "other.pdf", filename: "other.pdf", page_count: 1, has_text: true, study_key: null, preselected: false },
  ],
  warnings: [],
  ...over,
});

describe("disc file selection", () => {
  it("strips the picked folder name before filtering", () => {
    expect(discPath("CDROM/README.TXT")).toBe("README.TXT");
    expect(discPath("README.TXT")).toBe("README.TXT");
    const kept = discFiles([
      { file: file("README.TXT"), relativePath: "CDROM/README.TXT" },
      { file: file("x.dll"), relativePath: "CDROM/VIEWERS/x.dll" },
      { file: file("a"), relativePath: "CDROM/DICOM/1/2/a" },
    ]).map((f) => f.path);
    expect(kept).toEqual(["README.TXT", "DICOM/1/2/a"]);
  });

  it("keeps DICOM, reports, vendor pages and JPEG copies; drops the viewer and decorations", () => {
    const keep = (path: string) => keepDiscFile(path, 10);
    expect(keep("DICOMDIR")).toBe(true);
    expect(keep("DICOM/12840113/56410336/89864915")).toBe(true);
    expect(keep("REPORT/8499735.pdf")).toBe(true);
    expect(keep("help.pdf")).toBe(true); // the server drops viewer manuals
    expect(keep("IHE_PDI/Study000.htm")).toBe(true);
    expect(keep("IHE_PDI/1/2/3/a.jpg")).toBe(true);
    expect(keep("IHE_PDI/Left.jpg")).toBe(false);
    expect(keep("IHE_PDI/1/2/000_001.htm")).toBe(false);
    expect(keep("REPORT/8499735.p7m")).toBe(false);
    expect(keep("REPORT/8499735.tsr")).toBe(false);
    expect(keep("VIEWERS/lang/it.pdf")).toBe(false);
    expect(keep("COMMON/LICENSE")).toBe(false);
    expect(keep("start.exe")).toBe(false);
    expect(keep("INDEX.BAK")).toBe(false);
    expect(keep("IHE_PDI/Thumbs.db")).toBe(false);
    expect(keep("scan.dcm")).toBe(true);
    expect(keepDiscFile("DICOM/huge", DISC_MAX_FILE_BYTES + 1)).toBe(false);
  });

  it("puts manual picks under manual/", () => {
    expect(manualFiles([file("a.jpg")]).map((f) => f.path)).toEqual(["manual/a.jpg"]);
  });
});

describe("upload batches", () => {
  const staged = (n: number, size: number): StagedFile[] =>
    Array.from({ length: n }, (_, i) => ({ file: file(`f${i}`, size), path: `DICOM/f${i}` }));

  it("splits by bytes and by count", () => {
    expect(uploadBatches(staged(5, 10), 25, 100).map((b) => b.length)).toEqual([2, 2, 1]);
    expect(uploadBatches(staged(5, 1), 1000, 2).map((b) => b.length)).toEqual([2, 2, 1]);
    expect(uploadBatches(staged(1, 99), 10, 10).map((b) => b.length)).toEqual([1]); // a big file goes alone
    expect(uploadBatches([], 10, 10)).toEqual([]);
  });

  it("sends one path per file", () => {
    const form = batchForm(staged(2, 1));
    expect(form.getAll("paths")).toEqual(["DICOM/f0", "DICOM/f1"]);
    expect(form.getAll("files")).toHaveLength(2);
  });
});

describe("drafts", () => {
  it("defaults: OCR and translation off, summary on", () => {
    const p = discProcessing();
    expect([p.ocrEnabled, p.translationEnabled, p.summaryEnabled]).toEqual([false, false, true]);
  });

  it("starts from the proposed metadata, readable images and preselected reports", () => {
    const d = draftFromStudy(study(), preview().reports, 7);
    expect(d).toMatchObject({
      key: "s1", include: true, title: "Rx gamba — 2026-08-28", documentDate: "2026-08-28",
      folderId: 7, tagIds: [], addPatientTag: true, imageKeys: ["i1", "i2"], reportKeys: ["r1"],
    });
    const tagged = draftFromStudy(study({ patient_tag_id: 4 }), [], null);
    expect([tagged.tagIds, tagged.addPatientTag]).toEqual([[4], false]);
  });

  it("offers own and unassigned reports", () => {
    expect(reportsFor(study(), preview().reports).map((r) => r.key)).toEqual(["r1", "r2"]);
    expect(reportsFor(study({ key: "s2" }), preview().reports).map((r) => r.key)).toEqual(["r2"]);
  });

  it("keeps edits and selects only newly added files after Add files", () => {
    const before = preview();
    const edited = { ...draftFromStudy(study(), before.reports, null), title: "Mine", imageKeys: ["i1"] };
    const after = preview({
      studies: [study({ images: [...study().images, { key: "i4", path: "manual/x.jpg", kind: "image", label: "x.jpg", readable: true, frames: 1 }] })],
      reports: [...before.reports, { key: "r3", path: "manual/m.pdf", filename: "m.pdf", page_count: 1, has_text: true, study_key: "s1", preselected: true }],
    });
    const [merged] = mergeDrafts([edited], before, after, null);
    expect(merged.title).toBe("Mine");
    expect(merged.imageKeys).toEqual(["i1", "i4"]);
    expect(merged.reportKeys).toEqual(["r1", "r3"]);
  });

  it("can import only with a titled study that has images", () => {
    const d = draftFromStudy(study(), [], null);
    expect(canImport([d])).toBe(true);
    expect(canImport([{ ...d, title: " " }])).toBe(false);
    expect(canImport([{ ...d, imageKeys: [] }])).toBe(false);
    expect(canImport([{ ...d, include: false }])).toBe(false);
  });

  it("builds the commit body in study image order with the patient tag", () => {
    const d = { ...draftFromStudy(study(), preview().reports, 3), imageKeys: ["i2", "i1"], notes: "n" };
    const body = commitBody([d], preview(), { s1: 9 }, discProcessing());
    expect(body.studies[0]).toEqual({
      study_key: "s1", title: "Rx gamba — 2026-08-28", document_date: "2026-08-28", notes: "n",
      folder_id: 3, tag_ids: [9], image_keys: ["i1", "i2"], report_keys: ["r1"],
    });
    expect(body.ocr_enabled).toBe(false);
    expect(body.summary_enabled).toBe(true);
  });
});
```

Create `frontend/src/lib/documentView.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { activeViewTab, documentLink, studyRows, viewTabs } from "./documentView";

const att = (id: number, role = "report") => ({ id, role, filename: "Report.pdf", file_size: 1, page_count: 1 });

describe("viewTabs", () => {
  it("plain documents have one tab", () => {
    expect(viewTabs({ attachments: [], extra_metadata: null }).map((t) => t.label)).toEqual(["Document"]);
  });

  it("studies show Images then one tab per attachment, numbered when repeated", () => {
    const tabs = viewTabs({ attachments: [att(5), att(6)], extra_metadata: {} });
    expect(tabs.map((t) => [t.key, t.label])).toEqual([
      ["main", "Images"], ["5", "Report 1"], ["6", "Report 2"],
    ]);
    expect(viewTabs({ attachments: [att(5)], extra_metadata: {} })[1].label).toBe("Report");
  });

  it("picks the tab from the query parameter, falling back to the first", () => {
    const tabs = viewTabs({ attachments: [att(5)], extra_metadata: {} });
    expect(activeViewTab(tabs, "5").key).toBe("5");
    expect(activeViewTab(tabs, "99").key).toBe("main");
    expect(activeViewTab(tabs, null).key).toBe("main");
  });
});

describe("documentLink", () => {
  it("adds the tab only for attachment hits", () => {
    expect(documentLink("d1")).toBe("/documents/d1");
    expect(documentLink("d1", null)).toBe("/documents/d1");
    expect(documentLink("d1", 5)).toBe("/documents/d1?tab=5");
  });
});

describe("studyRows", () => {
  it("lists known fields in order, then the series", () => {
    expect(
      studyRows({ modality: "DX", patient_name: "Mario Rossi", series: [{ description: "AP", images: 2 }], other: "x" }),
    ).toEqual([["Patient", "Mario Rossi"], ["Modality", "DX"], ["Series", "AP (2)"]]);
    expect(studyRows(null)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/discImport.test.ts src/lib/documentView.test.ts`
Expected: FAIL. The modules `./discImport` and `./documentView` are not found.

- [ ] **Step 3: Types and URL helpers**

In `frontend/src/lib/types.ts`:

1. Add before `Document`:

```ts
export interface Attachment {
  id: number;
  role: string;
  filename: string;
  file_size: number;
  page_count: number | null;
}
```

2. Add to `Document`, after `description: string;`:

```ts
  notes: string;
```

3. Add to `Document`, after `file_size: number | null;`:

```ts
  extra_metadata: Record<string, unknown> | null; // study data of an imported X-ray disc
  attachments: Attachment[];
```

4. Add `attachment_id: number | null;` to `SearchSnippet`, and `attachment_id?: number | null;` to `ChatSource`.

5. Append:

```ts
export interface DiscImage {
  key: string;
  path: string;
  kind: "dicom" | "image";
  label: string;
  readable: boolean;
  frames: number;
}

export interface DiscReport {
  key: string;
  path: string;
  filename: string;
  page_count: number;
  has_text: boolean;
  study_key: string | null;
  preselected: boolean;
}

export interface DiscStudy {
  key: string;
  title: string;
  document_date: string | null;
  description_fallback: string;
  patient_tag: string | null;
  patient_tag_id: number | null;
  extra_metadata: Record<string, unknown>;
  images: DiscImage[];
}

export interface DiscPreview {
  id: string;
  no_study: boolean;
  studies: DiscStudy[];
  reports: DiscReport[];
  warnings: string[];
}
```

In `frontend/src/lib/api.ts`, append:

```ts
export const attachmentUrl = (documentId: string, attachmentId: number, opts: { download?: boolean } = {}): string =>
  `/api/documents/${documentId}/attachments/${attachmentId}/file?token=${getToken() ?? ""}` +
  `${opts.download ? "&download=1" : ""}`;

export const discThumbUrl = (importId: string, key: string): string =>
  `/api/disc-imports/${importId}/images/${key}/thumb?token=${getToken() ?? ""}`;

export const discReportUrl = (importId: string, key: string): string =>
  `/api/disc-imports/${importId}/reports/${key}/file?token=${getToken() ?? ""}`;
```

In `frontend/src/components/DocumentCard.test.tsx`, add `notes: "", extra_metadata: null, attachments: [],` to the `doc` fixture object.

- [ ] **Step 4: Write `discImport.ts`**

Create `frontend/src/lib/discImport.ts`:

```ts
import type { PickedFile } from "./batchUpload";
import { defaultProcessing, processingPayload, type ProcessingValues } from "./processing";
import type { DiscPreview, DiscReport, DiscStudy } from "./types";

export const DISC_MAX_FILE_BYTES = 90 * 1024 * 1024; // Cloudflare rejects request bodies over 100 MB
export const DISC_BATCH_BYTES = 50 * 1024 * 1024;
export const DISC_BATCH_FILES = 200;

const SKIP_DIRS = new Set(["viewers", "common"]);
const SKIP_EXTENSIONS = new Set([".exe", ".dll", ".inf", ".ini", ".bak", ".bmp", ".gif", ".tsr", ".p7m"]);
const JPEG_EXTENSIONS = new Set([".jpg", ".jpeg"]);

export interface StagedFile {
  file: File;
  path: string; // path inside the disc, e.g. "DICOM/1/2/a" or "manual/a.jpg"
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/** Path inside the disc: the folder picker puts the picked folder's own name first. */
export function discPath(relativePath: string): string {
  const parts = relativePath.split("/").filter(Boolean);
  return (parts.length > 1 ? parts.slice(1) : parts).join("/");
}

/** Files worth uploading: DICOM, README, reports, the study page and the small JPEG copies. */
export function keepDiscFile(path: string, size: number): boolean {
  if (size > DISC_MAX_FILE_BYTES) return false;
  const parts = path.toLowerCase().split("/");
  const name = parts[parts.length - 1];
  const ext = extension(name);
  if (name === "thumbs.db" || SKIP_EXTENSIONS.has(ext)) return false;
  if (parts.slice(0, -1).some((p) => SKIP_DIRS.has(p))) return false;
  if (parts[0] === "ihe_pdi")
    return (ext === ".htm" && parts.length === 2) || (JPEG_EXTENSIONS.has(ext) && parts.length > 2);
  if (name === "dicomdir" || name === "readme.txt" || ext === ".pdf" || ext === ".dcm") return true;
  if (parts.slice(0, -1).includes("dicom")) return true;
  return ext === ""; // DICOM files often have no extension
}

export function discFiles(picked: PickedFile[]): StagedFile[] {
  return picked
    .map((p) => ({ file: p.file, path: discPath(p.relativePath) }))
    .filter((f) => keepDiscFile(f.path, f.file.size));
}

export function manualFiles(files: File[]): StagedFile[] {
  return files.map((file) => ({ file, path: `manual/${file.name}` }));
}

export function uploadBatches(
  files: StagedFile[],
  maxBytes = DISC_BATCH_BYTES,
  maxFiles = DISC_BATCH_FILES,
): StagedFile[][] {
  const batches: StagedFile[][] = [];
  let current: StagedFile[] = [];
  let bytes = 0;
  for (const f of files) {
    if (current.length > 0 && (bytes + f.file.size > maxBytes || current.length >= maxFiles)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(f);
    bytes += f.file.size;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export function batchForm(batch: StagedFile[]): FormData {
  const form = new FormData();
  for (const f of batch) {
    form.append("files", f.file);
    form.append("paths", f.path);
  }
  return form;
}

/** Import defaults: X-rays have no text worth OCR and are not translated; the report gets a summary. */
export function discProcessing(): ProcessingValues {
  return { ...defaultProcessing(), ocrEnabled: false, summaryEnabled: true, translationEnabled: false };
}

export interface StudyDraft {
  key: string;
  include: boolean;
  title: string;
  documentDate: string; // "" = server default (today)
  notes: string;
  folderId: number | null;
  tagIds: number[];
  addPatientTag: boolean; // create the suggested patient tag on import
  imageKeys: string[];
  reportKeys: string[];
}

export function reportsFor(study: DiscStudy, reports: DiscReport[]): DiscReport[] {
  return reports.filter((r) => r.study_key === study.key || r.study_key === null);
}

export function draftFromStudy(study: DiscStudy, reports: DiscReport[], folderId: number | null): StudyDraft {
  return {
    key: study.key,
    include: true,
    title: study.title,
    documentDate: study.document_date ?? "",
    notes: "",
    folderId,
    tagIds: study.patient_tag_id !== null ? [study.patient_tag_id] : [],
    addPatientTag: study.patient_tag !== null && study.patient_tag_id === null,
    imageKeys: study.images.filter((i) => i.readable).map((i) => i.key),
    reportKeys: reports.filter((r) => r.study_key === study.key && r.preselected).map((r) => r.key),
  };
}

/** After "Add files": keep the user's edits; only images and reports that are new get selected. */
export function mergeDrafts(
  previous: StudyDraft[],
  before: DiscPreview | null,
  after: DiscPreview,
  folderId: number | null,
): StudyDraft[] {
  const knownReports = new Set((before?.reports ?? []).map((r) => r.key));
  return after.studies.map((study) => {
    const fresh = draftFromStudy(study, after.reports, folderId);
    const old = previous.find((d) => d.key === study.key);
    const oldStudy = before?.studies.find((s) => s.key === study.key);
    if (!old || !oldStudy) return fresh;
    const knownImages = new Set(oldStudy.images.map((i) => i.key));
    return {
      ...old,
      imageKeys: [...old.imageKeys, ...fresh.imageKeys.filter((k) => !knownImages.has(k))],
      reportKeys: [...old.reportKeys, ...fresh.reportKeys.filter((k) => !knownReports.has(k))],
    };
  });
}

export function canImport(drafts: StudyDraft[]): boolean {
  const included = drafts.filter((d) => d.include);
  return included.length > 0 && included.every((d) => d.title.trim() !== "" && d.imageKeys.length > 0);
}

export function commitBody(
  drafts: StudyDraft[],
  preview: DiscPreview,
  patientTagIds: Record<string, number>,
  processing: ProcessingValues,
) {
  return {
    studies: drafts
      .filter((d) => d.include)
      .map((d) => {
        const study = preview.studies.find((s) => s.key === d.key);
        const order = study ? study.images.map((i) => i.key) : d.imageKeys;
        const extra = patientTagIds[d.key];
        return {
          study_key: d.key,
          title: d.title.trim(),
          document_date: d.documentDate || null,
          notes: d.notes,
          folder_id: d.folderId,
          tag_ids: extra !== undefined && !d.tagIds.includes(extra) ? [...d.tagIds, extra] : d.tagIds,
          image_keys: order.filter((k) => d.imageKeys.includes(k)),
          report_keys: d.reportKeys,
        };
      }),
    ...processingPayload(processing),
  };
}
```

- [ ] **Step 5: Write `documentView.ts`**

Create `frontend/src/lib/documentView.ts`:

```ts
import type { Attachment, Document } from "./types";

export interface ViewTab {
  key: string; // "main" or the attachment id
  label: string;
  attachment: Attachment | null;
}

const ROLE_LABELS: Record<string, string> = { report: "Report" };

export function viewTabs(doc: Pick<Document, "attachments" | "extra_metadata">): ViewTab[] {
  const tabs: ViewTab[] = [{ key: "main", label: doc.extra_metadata ? "Images" : "Document", attachment: null }];
  const seen: Record<string, number> = {};
  for (const attachment of doc.attachments) {
    const total = doc.attachments.filter((a) => a.role === attachment.role).length;
    seen[attachment.role] = (seen[attachment.role] ?? 0) + 1;
    const base = ROLE_LABELS[attachment.role] ?? attachment.role;
    tabs.push({
      key: String(attachment.id),
      label: total > 1 ? `${base} ${seen[attachment.role]}` : base,
      attachment,
    });
  }
  return tabs;
}

export function activeViewTab(tabs: ViewTab[], param: string | null): ViewTab {
  return tabs.find((t) => t.key === param) ?? tabs[0];
}

export function documentLink(documentId: string, attachmentId?: number | null): string {
  return `/documents/${documentId}${attachmentId != null ? `?tab=${attachmentId}` : ""}`;
}

/** Same keys and labels as FIELDS in backend/app/services/study_metadata.py. */
export const STUDY_FIELDS: [string, string][] = [
  ["patient_name", "Patient"],
  ["birth_date", "Birth date"],
  ["sex", "Sex"],
  ["patient_id", "Patient ID"],
  ["study_description", "Study"],
  ["modality", "Modality"],
  ["body_part", "Body part"],
  ["institution", "Institution"],
  ["accession_number", "Accession number"],
  ["device", "Device"],
];

export function studyRows(meta: Record<string, unknown> | null): [string, string][] {
  if (!meta) return [];
  const rows: [string, string][] = STUDY_FIELDS.filter(([key]) => typeof meta[key] === "string" && meta[key]).map(
    ([key, label]) => [label, meta[key] as string],
  );
  const series = Array.isArray(meta.series) ? (meta.series as { description: string; images: number }[]) : [];
  if (series.length > 0) rows.push(["Series", series.map((s) => `${s.description} (${s.images})`).join("; ")]);
  return rows;
}
```

- [ ] **Step 6: Run the tests, typecheck and lint**

Run: `cd /opt/origami/frontend && npx vitest run src/lib && npx tsc -b && npx oxlint`
Expected: PASS with no type errors. If `tsc` reports a `Document` fixture with missing fields in another test file, add `notes: "", extra_metadata: null, attachments: []` there too.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami && git add frontend/src/lib frontend/src/components/DocumentCard.test.tsx
git commit -m "feat: frontend types, attachment and disc import URLs, disc file filter and import drafts"
```

---

### Task 11: Import dialog and entry point

**Files:**
- Modify: `frontend/src/components/ui/dialog.tsx` (optional `className`)
- Create: `frontend/src/components/DiscStudyCard.tsx`
- Create: `frontend/src/components/DiscImportDialog.tsx`, `frontend/src/components/DiscImportDialog.test.tsx`
- Modify: `frontend/src/pages/BrowsePage.tsx`

**Interfaces:**
- Consumes: everything from Task 10, `api`, `useCreateTag`, `useTags`, `ProcessingOptions`, `FolderPicker`, `TagInput`.
- Produces:
  - `<DiscImportDialog picked={PickedFile[]} onClose={() => void} initialFolderId={number | null} />`
  - `<Dialog className?>`

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/components/DiscImportDialog.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { DiscPreview } from "@/lib/types";
import { DiscImportDialog } from "./DiscImportDialog";

vi.mock("@/hooks/useOcrLanguages", () => ({
  useOcrLanguages: () => ({
    data: { languages: [{ code: "ita", name: "Italian" }], default: "ita", translation_languages: [{ code: "it", name: "Italian" }], translation_default: "it" },
  }),
}));
vi.mock("@/hooks/useFolders", () => ({ useFolders: () => ({ data: [] }) }));
const { createTag } = vi.hoisted(() => ({ createTag: vi.fn() })); // vi.mock factories run before module code
vi.mock("@/hooks/useTags", () => ({
  useTags: () => ({ data: [], refetch: vi.fn() }),
  useCreateTag: () => ({ mutateAsync: createTag }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { get: vi.fn(), post: vi.fn(), upload: vi.fn(), del: vi.fn(() => Promise.resolve()) } };
});

const PREVIEW: DiscPreview = {
  id: "imp",
  no_study: false,
  studies: [
    {
      key: "s1",
      title: "Rx gamba — 2026-08-28",
      document_date: "2026-08-28",
      description_fallback: "",
      patient_tag: "Mario Rossi",
      patient_tag_id: null,
      extra_metadata: {},
      images: [{ key: "i1", path: "DICOM/a", kind: "dicom", label: "AP", readable: true, frames: 1 }],
    },
  ],
  reports: [{ key: "r1", path: "REPORT/r.pdf", filename: "r.pdf", page_count: 1, has_text: false, study_key: "s1", preselected: true }],
  warnings: [],
};

const picked = [
  { file: new File(["x"], "README.TXT"), relativePath: "CD/README.TXT" },
  { file: new File(["x"], "start.exe"), relativePath: "CD/start.exe" },
  { file: new File(["x"], "a"), relativePath: "CD/DICOM/a" },
];

function renderDialog() {
  const router = createMemoryRouter([
    { path: "/", element: <DiscImportDialog picked={picked} onClose={() => {}} initialFolderId={null} /> },
  ]);
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("DiscImportDialog", () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    vi.mocked(api.upload).mockReset();
    createTag.mockReset();
  });

  it("uploads the useful files and shows the proposed study with import defaults", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ id: "imp" });
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    renderDialog();
    expect(await screen.findByDisplayValue("Rx gamba — 2026-08-28")).toBeInTheDocument();
    const form = vi.mocked(api.upload).mock.calls[0][1] as FormData;
    expect(form.getAll("paths")).toEqual(["README.TXT", "DICOM/a"]);
    expect(screen.getByLabelText("OCR (extract text)")).not.toBeChecked();
    expect(screen.getByLabelText("AI summary")).toBeChecked();
    expect(screen.getByLabelText("Translation")).not.toBeChecked();
    expect(screen.getByText(/This report has no text/)).toBeInTheDocument();
  });

  it("asks for manual files when no study is found", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ id: "imp" });
    vi.mocked(api.upload).mockResolvedValueOnce({ ...PREVIEW, no_study: true, studies: [], reports: [] });
    renderDialog();
    expect(await screen.findByText(/No X-ray study found on this disc/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("creates the patient tag and commits the study", async () => {
    vi.mocked(api.post)
      .mockResolvedValueOnce({ id: "imp" })
      .mockResolvedValueOnce({ documents: [{ id: "d1", title: "Rx gamba — 2026-08-28" }] });
    vi.mocked(api.upload).mockResolvedValueOnce(PREVIEW);
    createTag.mockResolvedValueOnce({ id: 9, name: "Mario Rossi", color: "#888888" });
    renderDialog();
    await userEvent.click(await screen.findByRole("button", { name: "Import" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Rx gamba — 2026-08-28" })).toBeInTheDocument());
    expect(createTag).toHaveBeenCalledWith(expect.objectContaining({ name: "Mario Rossi" }));
    const [path, body] = vi.mocked(api.post).mock.calls[1] as [string, { studies: { tag_ids: number[]; image_keys: string[]; report_keys: string[] }[] }];
    expect(path).toBe("/api/disc-imports/imp/commit");
    expect(body.studies[0]).toMatchObject({ tag_ids: [9], image_keys: ["i1"], report_keys: ["r1"] });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /opt/origami/frontend && npx vitest run src/components/DiscImportDialog.test.tsx`
Expected: FAIL. The module `./DiscImportDialog` is not found.

- [ ] **Step 3: Let `Dialog` take a width class**

In `frontend/src/components/ui/dialog.tsx`:
- import `cn` from `@/lib/utils`
- add `className?: string` to the props (destructure `className`)
- change the panel class to `className={cn("w-full max-w-md rounded-lg bg-white p-6 shadow-xl", className)}`

Tailwind's later `max-w-*` wins only when it comes after `max-w-md` in the generated CSS. If it does not apply, use `!max-w-3xl` in the caller.

- [ ] **Step 4: Write `DiscStudyCard.tsx`**

Create `frontend/src/components/DiscStudyCard.tsx`:

```tsx
import { FolderPicker } from "@/components/FolderPicker";
import { TagInput } from "@/components/TagInput";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { discReportUrl, discThumbUrl } from "@/lib/api";
import type { StudyDraft } from "@/lib/discImport";
import type { DiscReport, DiscStudy } from "@/lib/types";

function toggle(keys: string[], key: string, on: boolean): string[] {
  return on ? [...keys.filter((k) => k !== key), key] : keys.filter((k) => k !== key);
}

export function DiscStudyCard({
  importId,
  study,
  reports,
  draft,
  onChange,
}: {
  importId: string;
  study: DiscStudy;
  reports: DiscReport[];
  draft: StudyDraft;
  onChange: (draft: StudyDraft) => void;
}) {
  const set = (patch: Partial<StudyDraft>) => onChange({ ...draft, ...patch });
  const id = (name: string) => `disc-${study.key}-${name}`;
  return (
    <section className="space-y-3 rounded-md border border-zinc-200 p-3">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={draft.include} onChange={(e) => set({ include: e.target.checked })} />
        Import this study ({study.images.length} images)
      </label>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor={id("title")}>Title</Label>
          <Input id={id("title")} value={draft.title} onChange={(e) => set({ title: e.target.value })} />
        </div>
        <div>
          <Label htmlFor={id("date")}>Document date</Label>
          <Input
            id={id("date")}
            type="date"
            value={draft.documentDate}
            onChange={(e) => set({ documentDate: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor={id("folder")}>Folder</Label>
          <FolderPicker id={id("folder")} value={draft.folderId} onChange={(folderId) => set({ folderId })} />
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor={id("tags")}>Tags</Label>
          <TagInput id={id("tags")} value={draft.tagIds} onChange={(tagIds) => set({ tagIds })} />
          {study.patient_tag && study.patient_tag_id === null && (
            <label className="mt-1 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.addPatientTag}
                onChange={(e) => set({ addPatientTag: e.target.checked })}
              />
              Tag “{study.patient_tag}”
            </label>
          )}
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor={id("notes")}>Notes</Label>
          <Textarea id={id("notes")} rows={2} value={draft.notes} onChange={(e) => set({ notes: e.target.value })} />
        </div>
      </div>
      <fieldset>
        <legend className="mb-1 text-sm font-medium">Images</legend>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {study.images.map((image) => (
            <label key={image.key} className={image.readable ? "text-xs" : "text-xs opacity-50"}>
              {image.readable ? (
                <img
                  src={discThumbUrl(importId, image.key)}
                  alt={image.label}
                  className="mb-1 h-28 w-full rounded border bg-black object-contain"
                />
              ) : (
                <div className="mb-1 flex h-28 items-center justify-center rounded border text-center">
                  Format not supported
                </div>
              )}
              <span className="flex items-center gap-1">
                <input
                  type="checkbox"
                  disabled={!image.readable}
                  checked={draft.imageKeys.includes(image.key)}
                  onChange={(e) => set({ imageKeys: toggle(draft.imageKeys, image.key, e.target.checked) })}
                />
                {image.label}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      {reports.length > 0 && (
        <fieldset className="space-y-1">
          <legend className="mb-1 text-sm font-medium">Reports</legend>
          {reports.map((report) => (
            <div key={report.key} className="text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={draft.reportKeys.includes(report.key)}
                  onChange={(e) => set({ reportKeys: toggle(draft.reportKeys, report.key, e.target.checked) })}
                />
                {report.filename} ({report.page_count} p.)
                <a href={discReportUrl(importId, report.key)} target="_blank" rel="noreferrer" className="underline">
                  View
                </a>
              </label>
              {!report.has_text && (
                <p className="pl-6 text-xs text-amber-700">
                  This report has no text. Enable OCR to make it searchable.
                </p>
              )}
            </div>
          ))}
        </fieldset>
      )}
    </section>
  );
}
```

- [ ] **Step 5: Write `DiscImportDialog.tsx`**

Create `frontend/src/components/DiscImportDialog.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { DiscStudyCard } from "@/components/DiscStudyCard";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { useCreateTag, useTags } from "@/hooks/useTags";
import { api, ApiError } from "@/lib/api";
import type { PickedFile } from "@/lib/batchUpload";
import {
  batchForm,
  canImport,
  commitBody,
  discFiles,
  discProcessing,
  manualFiles,
  mergeDrafts,
  reportsFor,
  uploadBatches,
  type StagedFile,
  type StudyDraft,
} from "@/lib/discImport";
import type { ProcessingValues } from "@/lib/processing";
import { nextTagColor } from "@/lib/tagInput";
import type { DiscPreview, Document } from "@/lib/types";

type Phase =
  | { kind: "uploading"; percent: number }
  | { kind: "preview" }
  | { kind: "importing" }
  | { kind: "done"; documents: Document[] }
  | { kind: "failed"; message: string };

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

function ManualPickers({ onAdd }: { onAdd: (files: StagedFile[]) => void }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div>
        <Label htmlFor="disc-manual-images">Images (DICOM, JPEG, PNG)</Label>
        <input
          id="disc-manual-images"
          type="file"
          multiple
          accept=".dcm,.jpg,.jpeg,.png,application/dicom"
          className="block text-sm"
          onChange={(e) => {
            onAdd(manualFiles(Array.from(e.target.files ?? [])));
            e.target.value = "";
          }}
        />
      </div>
      <div>
        <Label htmlFor="disc-manual-report">Report (PDF, optional)</Label>
        <input
          id="disc-manual-report"
          type="file"
          accept=".pdf,application/pdf"
          className="block text-sm"
          onChange={(e) => {
            onAdd(manualFiles(Array.from(e.target.files ?? [])));
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

export function DiscImportDialog({
  picked,
  onClose,
  initialFolderId,
}: {
  picked: PickedFile[];
  onClose: () => void;
  initialFolderId: number | null;
}) {
  const qc = useQueryClient();
  const { data: tags } = useTags();
  const createTag = useCreateTag();
  const [importId, setImportId] = useState<string | null>(null);
  const [preview, setPreview] = useState<DiscPreview | null>(null);
  const previewRef = useRef<DiscPreview | null>(null);
  const [drafts, setDrafts] = useState<StudyDraft[]>([]);
  const [processing, setProcessing] = useState<ProcessingValues>(discProcessing);
  const [phase, setPhase] = useState<Phase>({ kind: "uploading", percent: 0 });
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const started = useRef(false);
  const committed = useRef(false);

  const send = async (id: string, files: StagedFile[]): Promise<DiscPreview> => {
    const total = files.reduce((n, f) => n + f.file.size, 0) || 1;
    let done = 0;
    let latest: DiscPreview | null = null;
    setPhase({ kind: "uploading", percent: 0 });
    for (const batch of uploadBatches(files)) {
      const size = batch.reduce((n, f) => n + f.file.size, 0);
      latest = await api.upload<DiscPreview>(`/api/disc-imports/${id}/files`, batchForm(batch), (p) =>
        setPhase({ kind: "uploading", percent: Math.round(((done + (size * p) / 100) / total) * 100) }),
      );
      done += size;
    }
    return latest ?? api.get<DiscPreview>(`/api/disc-imports/${id}`);
  };

  const apply = (next: DiscPreview) => {
    setDrafts((current) => mergeDrafts(current, previewRef.current, next, initialFolderId));
    previewRef.current = next;
    setPreview(next);
    setPhase({ kind: "preview" });
  };

  const start = async () => {
    try {
      const { id } = await api.post<{ id: string }>("/api/disc-imports");
      setImportId(id);
      apply(await send(id, discFiles(picked)));
    } catch (err) {
      setPhase({ kind: "failed", message: message(err, "Upload failed") });
    }
  };

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice in development
    started.current = true;
    void start();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addFiles = async (files: StagedFile[]) => {
    if (!importId || files.length === 0) return;
    setAdding(false);
    try {
      apply(await send(importId, files));
    } catch (err) {
      setPhase({ kind: "preview" });
      setError(message(err, "Upload failed"));
    }
  };

  const close = () => {
    if (importId && !committed.current) api.del(`/api/disc-imports/${importId}`).catch(() => {});
    onClose();
  };

  const retry = () => {
    if (importId) api.del(`/api/disc-imports/${importId}`).catch(() => {});
    setImportId(null);
    setPreview(null);
    previewRef.current = null;
    setDrafts([]);
    void start();
  };

  const runImport = async () => {
    if (!importId || !preview) return;
    setError(null);
    setPhase({ kind: "importing" });
    try {
      const patientTagIds: Record<string, number> = {};
      const byName = new Map((tags ?? []).map((t) => [t.name.toLowerCase(), t.id]));
      for (const d of drafts.filter((x) => x.include && x.addPatientTag)) {
        const name = preview.studies.find((s) => s.key === d.key)?.patient_tag;
        if (!name) continue;
        let tagId = byName.get(name.toLowerCase());
        if (tagId === undefined) {
          tagId = (await createTag.mutateAsync({ name, color: nextTagColor(tags ?? []) })).id;
          byName.set(name.toLowerCase(), tagId);
        }
        patientTagIds[d.key] = tagId;
      }
      const result = await api.post<{ documents: Document[] }>(
        `/api/disc-imports/${importId}/commit`,
        commitBody(drafts, preview, patientTagIds, processing),
      );
      committed.current = true;
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["folders"] });
      setPhase({ kind: "done", documents: result.documents });
    } catch (err) {
      setPhase({ kind: "preview" });
      setError(message(err, "Import failed"));
    }
  };

  return (
    <Dialog open onClose={close} title="Import X-ray disc" className="max-h-[90vh] max-w-3xl overflow-y-auto">
      {phase.kind === "uploading" && (
        <div className="space-y-2">
          <p className="text-sm text-zinc-600">Uploading the disc files…</p>
          <div className="h-2 rounded bg-zinc-200">
            <div className="h-2 rounded bg-zinc-800" style={{ width: `${phase.percent}%` }} />
          </div>
        </div>
      )}
      {phase.kind === "failed" && (
        <div className="space-y-3">
          <p className="text-sm text-red-600">{phase.message}</p>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button onClick={retry}>Retry</Button>
          </div>
        </div>
      )}
      {phase.kind === "done" && (
        <div className="space-y-3">
          <p className="text-sm">Imported:</p>
          <ul className="list-disc pl-5 text-sm">
            {phase.documents.map((d) => (
              <li key={d.id}>
                <Link to={`/documents/${d.id}`} className="underline" onClick={onClose}>
                  {d.title}
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex justify-end">
            <Button onClick={onClose}>Close</Button>
          </div>
        </div>
      )}
      {(phase.kind === "preview" || phase.kind === "importing") && preview && importId && (
        <div className="space-y-4">
          {preview.warnings.map((w) => (
            <p key={w} className="rounded border border-amber-200 bg-amber-50 p-2 text-sm text-amber-800">
              {w}
            </p>
          ))}
          {preview.no_study ? (
            <div className="space-y-3">
              <p className="text-sm">No X-ray study found on this disc. Add the files manually.</p>
              <ManualPickers onAdd={addFiles} />
            </div>
          ) : (
            <>
              {preview.studies.map((study) => {
                const draft = drafts.find((d) => d.key === study.key);
                return draft ? (
                  <DiscStudyCard
                    key={study.key}
                    importId={importId}
                    study={study}
                    reports={reportsFor(study, preview.reports)}
                    draft={draft}
                    onChange={(next) => setDrafts((all) => all.map((d) => (d.key === next.key ? next : d)))}
                  />
                ) : null;
              })}
              {adding ? (
                <ManualPickers onAdd={addFiles} />
              ) : (
                <Button variant="outline" onClick={() => setAdding(true)}>
                  Add files
                </Button>
              )}
            </>
          )}
          <ProcessingOptions idPrefix="disc" value={processing} onChange={setProcessing} />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button onClick={runImport} disabled={phase.kind === "importing" || !canImport(drafts)}>
              {phase.kind === "importing" ? "Importing…" : "Import"}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
```

Check the label on the OCR checkbox in `ProcessingOptions` before relying on `getByLabelText("OCR (extract text)")`. The checkbox sits inside its `<label>`, so the accessible name is the label text.

- [ ] **Step 6: Entry point in the browse page**

In `frontend/src/pages/BrowsePage.tsx`:

1. Add `import { DiscImportDialog } from "@/components/DiscImportDialog";`.
2. Add state and ref next to the folder input ones:

```tsx
  const discInput = useRef<HTMLInputElement>(null);
  const [discPicked, setDiscPicked] = useState<PickedFile[] | null>(null);
```

3. In the `webkitdirectory` effect, add `discInput.current?.setAttribute("webkitdirectory", "");`.
4. Widen the menu from `w-36` to `w-44` and add a third item after "Folder…":

```tsx
              <button
                role="menuitem"
                className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
                onClick={() => {
                  setUploadMenu(false);
                  discInput.current?.click();
                }}
              >
                X-ray disc…
              </button>
```

5. After the folder `<input>`, add:

```tsx
        <input
          ref={discInput}
          type="file"
          hidden
          onChange={(e) => {
            const files = pickedFromInput(e.target.files ?? []);
            if (files.length > 0) setDiscPicked(files);
            e.target.value = "";
          }}
        />
```

6. Before the closing `</div>` of the page, after the `BatchUploadDialog` block, add:

```tsx
      {discPicked && (
        <DiscImportDialog
          picked={discPicked}
          onClose={() => setDiscPicked(null)}
          initialFolderId={params.all ? null : params.folderId}
        />
      )}
```

- [ ] **Step 7: Run the tests, typecheck and lint**

Run: `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd /opt/origami && git add frontend/src/components frontend/src/pages/BrowsePage.tsx
git commit -m "feat: X-ray disc import dialog with study cards, manual fallback and patient tag"
```

---

### Task 12: Document page tabs, notes, study details and hit links

**Files:**
- Modify: `frontend/src/pages/DocumentPage.tsx`
- Modify: `frontend/src/pages/SearchPage.tsx`
- Modify: `frontend/src/components/chat/MessageList.tsx`
- Test: covered by `documentView.test.ts` (Task 10), plus a manual check in Task 13.

**Interfaces:**
- Consumes: `viewTabs`, `activeViewTab`, `documentLink`, `studyRows` (Task 10); `attachmentUrl` (Task 10).

- [ ] **Step 1: Tabs over the viewer**

In `frontend/src/pages/DocumentPage.tsx`:

1. Change the router import to `import { useNavigate, useParams, useSearchParams } from "react-router";`.
2. Change the api import to `import { api, ApiError, attachmentUrl, fileUrl } from "@/lib/api";`.
3. Add `import { activeViewTab, studyRows, viewTabs, type ViewTab } from "@/lib/documentView";`.
4. Replace the `Viewer` component with:

```tsx
function Viewer({ doc, view }: { doc: Document; view: ViewTab }) {
  if (view.attachment)
    return <iframe title={view.label} src={attachmentUrl(doc.id, view.attachment.id)} className="h-[75vh] w-full rounded border" />;
  const kind = viewerKind(doc);
  if (doc.status !== "ready" && kind !== "video")
    return <div className="flex h-96 items-center justify-center text-zinc-400">Processing…</div>;
  const src = fileUrl(doc.id, { preview: doc.preview_path !== null });
  if (kind === "pdf") return <iframe title="preview" src={src} className="h-[75vh] w-full rounded border" />;
  if (kind === "image") return <img src={src} alt={doc.title} className="max-h-[75vh] rounded border" />;
  if (kind === "video") return <video controls src={src} className="max-h-[75vh] w-full rounded border" />;
  return <TextView doc={doc} />;
}
```

5. In `DocumentPage`, add `const [searchParams, setSearchParams] = useSearchParams();` and `const [notes, setNotes] = useState("");`.
6. In the hydrate branch of the `doc` effect, add `setNotes(doc.notes);`.
7. In `save`'s `mutationFn` body, add `notes,` after `title,`.
8. After `const retryMessage = ...`, add:

```tsx
  const tabs = viewTabs(doc);
  const view = activeViewTab(tabs, searchParams.get("tab"));
  const details = studyRows(doc.extra_metadata);
```

9. Change the Download link `href` to:

```tsx
              href={view.attachment ? attachmentUrl(doc.id, view.attachment.id, { download: true }) : fileUrl(doc.id, { download: true })}
```

10. Replace `{tab === "preview" ? <Viewer doc={doc} /> : <TextView doc={doc} />}` with:

```tsx
        {tab === "preview" && tabs.length > 1 && (
          <div className="mb-3 inline-flex overflow-hidden rounded-md border border-zinc-300 text-sm">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setSearchParams(t.key === "main" ? {} : { tab: t.key }, { replace: true })}
                className={view.key === t.key ? "bg-zinc-900 px-3 py-1 text-white" : "px-3 py-1 hover:bg-zinc-100"}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
        {tab === "preview" ? <Viewer doc={doc} view={view} /> : <TextView doc={doc} />}
```

- [ ] **Step 2: Notes and study details in the side panel**

In the `<aside>`, add this before the Title block:

```tsx
        {details.length > 0 && (
          <dl className="space-y-1 rounded border border-zinc-200 bg-zinc-50 p-3 text-xs">
            {details.map(([label, value]) => (
              <div key={label} className="flex gap-2">
                <dt className="w-24 shrink-0 font-medium text-zinc-500">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
        )}
```

After the Description block, add:

```tsx
        <div>
          <Label htmlFor="d-notes">Notes</Label>
          <Textarea id="d-notes" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
```

In `confirmReprocess`, change the `pdfNote` condition so that study documents do not promise a rebuild. Add `&& !doc.extra_metadata`:

```tsx
      doc && (doc.doc_type === "pdf" || doc.doc_type === "scan") && !doc.extra_metadata && processing.ocrEnabled && doc.ocr_applied !== false
```

- [ ] **Step 3: Search and chat links open the right tab**

In `frontend/src/pages/SearchPage.tsx`:
- add `import { documentLink } from "@/lib/documentView";`
- change the result title link to `to={documentLink(result.document.id, result.snippets.find((s) => s.attachment_id != null)?.attachment_id)}`

In `frontend/src/components/chat/MessageList.tsx`:
- add the same import
- change the source link to `to={documentLink(source.document_id, source.attachment_id)}`

- [ ] **Step 4: Run tests, typecheck, lint, build**

Run: `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint && npm run build`
Expected: PASS and a successful build.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami && git add frontend/src/pages frontend/src/components/chat/MessageList.tsx
git commit -m "feat: document page tabs for attachments, notes, study details; hits open the matching tab"
```

---

### Task 13: README, full verification, manual check, merge

**Files:**
- Modify: `README.md` (new section after "Client scanner agent", plus one line in "Features")

- [ ] **Step 1: Document the feature**

Add this to the `## Features` list in `README.md`:

```markdown
- Import an X-ray disc from the hospital (IHE PDI): pick the disc folder in the browser; each study becomes one document with an image PDF built from the DICOM files and the radiology report attached.
```

Add this section before `## Run as a systemd service`:

```markdown
## X-ray disc import

Upload → **X-ray disc…** opens a folder picker. Select the root of the disc (for example `D:\` or `/media/<user>/<label>`).

**Upload.** The browser uploads only the files that matter: `DICOM/`, `DICOMDIR`, `README.TXT`, the IHE PDI study page and its small JPEG copies, and PDFs. The Windows viewer and its libraries are skipped.

**Preview.** Origami shows one card per study:
- The title, date, patient tag and series come from the DICOM tags. `README.TXT` and `IHE_PDI/Study000.htm` fill gaps.
- Thumbnails of the images.
- The report PDFs found on the disc.

Edit what you need. The default processing is: OCR off, translation off, AI summary on.

**Result.**
- Each study becomes a PDF with a cover page and one page per image, built at full resolution from the DICOM files. The JPEG copies on the disc are reduced and are used only when the DICOM files cannot be decoded. The PDF is for reference, not for diagnosis.
- When the study has a report, the document is stored as a directory named after the document, holding the image PDF and `Report.pdf`, byte-identical to the disc copy.
- The document page shows the two as **Images** and **Report** tabs.
- The report text is searchable and feeds the AI summary. The images are never sent to a vision model.

**No DICOM study on the disc.** The dialog asks for the files instead: images (DICOM, JPEG or PNG) and an optional report PDF.

**Expiry.** Staged files are kept for 24 hours if an import is abandoned.
```

- [ ] **Step 2: Full test runs**

Run: `cd /opt/origami/backend && uv run pytest -q`
Run: `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint && npm run build`
Expected: everything passes. Fix any failure before going on.

- [ ] **Step 3: Apply the migration and restart the service**

Follow the deployment memory: code and `.env` changes need a restart. The systemd unit runs migrations through `origami.sh` at start. Check that file before restarting, and restart with the repo's documented command (see `README.md` "Run as a systemd service").

Run: `systemctl --user status origami 2>/dev/null || sudo systemctl status origami` to find the unit, then restart it and check the logs for `a3f9c2d71b48` and a clean start.

Ask the user before restarting the production service.

- [ ] **Step 4: Manual check with the real disc**

In the browser at the running instance:
1. Upload → X-ray disc… → select `/home/matteo/Scaricati/disc`.
2. Check the preview:
   - one study with 4 images (AP ×2, Lateral ×2) and thumbnails
   - title `Rx gamba (tibia e perone) - Destro/a — 2026-08-28` (edit it if wanted)
   - date 2026-08-28
   - report `8499735.pdf` preselected
   - OCR and translation off, summary on
3. Import.
4. Open the document:
   - the **Images** tab shows the cover and 4 pages
   - the **Report** tab shows the signed report
   - after processing, the description holds a summary of the report
   - the study details panel lists the modality and institution
5. Search `chiodo endomidollare`. The hit opens the document on the **Report** tab.
6. Rename the document, move it to another folder, then run `cd /opt/origami/backend && uv run python -m app.cli check-storage` and expect no problems.
7. Check on disk that the bundle dir holds the image PDF and `Report.pdf`.
8. Delete the test document if the user does not want to keep it. Ask first.

- [ ] **Step 5: Commit the README**

```bash
cd /opt/origami && git add README.md
git commit -m "docs: X-ray disc import"
```

- [ ] **Step 6: Merge to local main**

```bash
cd /opt/origami && git switch main && git merge --no-ff feat/xray-disc-import -m "Merge branch 'feat/xray-disc-import'"
```

Do not push. Tell the user the merge is local.
