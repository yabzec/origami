# Document Properties and Origin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the read-only X-ray `extra_metadata` dict with an editable, ordered key/value `properties` list on every document, and add an explicit `origin` column (`file` / `scan` / `xray_disc`).

**Architecture:** One new Alembic migration adds `documents.origin`, backfills it, renames `extra_metadata` to `properties` and converts the one existing dict row to the list form in Python. Backend code routes X-ray studies on `origin` instead of the presence of metadata; `PATCH /api/documents/{id}` accepts normalized properties and refreshes the metadata chunk only when they change. The frontend gets a `PropertiesPanel` component (view/edit, own mutation, max-height scrolling frame) on every document page.

**Tech Stack:** FastAPI + SQLModel + Alembic + PostgreSQL JSONB (backend, `uv`), React + TypeScript + TanStack Query + Vitest + Tailwind (frontend).

**Spec:** `docs/superpowers/specs/2026-10-12-document-properties-design.md` (builds on `docs/superpowers/specs/2026-10-12-xray-disc-import-design.md`).

## Global Constraints

- Branch `feat/xray-disc-import` in `/opt/origami`. Never push, never touch `main`, never restart the `origami` service.
- Never edit migration `a3f9c2d71b48` (already applied in production). The new migration revises it.
- Never run Alembic against the production DB. Tests use `origami_test` (the test fixtures already point there).
- Migrations must not import app code; the label map lives inside the migration file.
- `origin` values: exactly `file`, `scan`, `xray_disc`. `class DocumentOrigin(StrEnum)` exported from `app.models`.
- `properties` is JSONB NOT NULL, server default `'[]'::jsonb`, an ordered list of `{"key": str, "value": str}`.
- PATCH rules: strip key and value; drop rows where both are empty; empty key with non-empty value → 422 `validation_error` "Every value needs a key"; duplicate keys (case-insensitive, after strip) → 422 `validation_error` "Duplicate key: X"; max 100 rows; key ≤ 100 chars; value ≤ 2000 chars.
- Labels (same as `FIELDS` in `app/services/study_metadata.py`): patient_name Patient, birth_date Birth date, sex Sex, patient_id Patient ID, study_description Study, modality Modality, body_part Body part, institution Institution, accession_number Accession number, device Device; series → Series (`"AP (2); Lateral (2)"`).
- Panel frame classes: `max-h-64 overflow-y-auto` with border and rounded corners, in view and edit mode. Empty state text "No properties".
- Suites: `cd /opt/origami/backend && uv run pytest -q`; `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint && npm run build`. Pre-existing noise: a starlette/httpx warning, 3 oxlint warnings.
- Commit messages: Conventional Commits, normal English.

## Review Focus

1. `PATCH` with `"properties": null` — a reasonable client sends null to mean "unchanged"; the column is NOT NULL, so it must be ignored, not a 500. Test in Task 2.
2. A non-X-ray document that has user properties and is re-processed must still go through the normal PDF/OCR path, never `_extract_study`. Test in Task 1.
3. Saving the same properties again (or the same rows with extra whitespace / trailing empty rows) must not queue a `refresh_metadata` job. Test in Task 2.
4. Migration on a dict with empty values, unknown keys and `series` must keep label order, skip empties, keep unknown keys as raw labels; a scan-type row gets `origin='scan'`; other rows get `[]` and `file`. Test in Task 1.
5. Editing in the panel, then pressing Cancel, must restore the server rows exactly (including after Add row / delete). Test in Task 4.

---

## File Structure

Backend:
- Create `backend/alembic/versions/e4b7c19a2d58_document_properties_origin.py` — new migration.
- Modify `backend/app/models/document.py` — `DocumentOrigin`, `origin`, `properties`; drop `extra_metadata`.
- Modify `backend/app/models/__init__.py` — export `DocumentOrigin`.
- Modify `backend/app/services/study_metadata.py` — `study_properties`, `properties_text`, `cover_rows`.
- Modify `backend/app/services/dicom_parse.py` — preview studies expose `properties`.
- Modify `backend/app/api/uploads.py` — `create_pending_document(properties=, origin=)`.
- Modify `backend/app/api/disc_imports.py`, `backend/app/api/scan.py` — pass origin/properties.
- Modify `backend/app/worker/pipeline.py` — route on origin; summary/metadata text from properties.
- Modify `backend/app/api/documents.py` — `PropertyRow`, PATCH normalization.
- Tests: `tests/test_migrations.py`, `tests/test_schema.py`, `tests/test_notes.py`, `tests/test_study_pipeline.py`, `tests/test_dicom_parse.py`, `tests/test_disc_imports_api.py`, `tests/test_attachments_api.py`, `tests/test_bundles.py`, `tests/test_scan_compile.py`, `tests/test_uploads.py`, new `tests/test_properties.py`.

Frontend:
- Modify `frontend/src/lib/types.ts`, `frontend/src/lib/documentView.ts` (+ test).
- Create `frontend/src/lib/properties.ts` (+ `properties.test.ts`).
- Create `frontend/src/components/PropertiesPanel.tsx` (+ `PropertiesPanel.test.tsx`).
- Modify `frontend/src/pages/DocumentPage.tsx`, fixtures in `DocumentCard.test.tsx`, `discImport.test.ts`, `DiscImportDialog.test.tsx`.
- Modify `README.md`.

---

### Task 1: Backend data model, migration and origin routing

**Files:**
- Create: `backend/alembic/versions/e4b7c19a2d58_document_properties_origin.py`
- Modify: `backend/app/models/document.py`, `backend/app/models/__init__.py`, `backend/app/services/study_metadata.py`, `backend/app/services/dicom_parse.py:213-230`, `backend/app/api/uploads.py:55-90`, `backend/app/api/disc_imports.py:255-272`, `backend/app/api/scan.py:246-260`, `backend/app/worker/pipeline.py` (lines ~32, ~101, ~174, ~360, ~585)
- Test: `backend/tests/test_migrations.py`, `test_schema.py`, `test_notes.py`, `test_study_pipeline.py`, `test_dicom_parse.py`, `test_disc_imports_api.py`, `test_attachments_api.py`, `test_bundles.py`, `test_scan_compile.py`, `test_uploads.py`

**Interfaces:**
- Produces:
  - `app.models.DocumentOrigin(StrEnum)`: `file`, `scan`, `xray_disc`.
  - `Document.origin: str` (default `DocumentOrigin.file`), `Document.properties: list[dict[str, str]]` (default `[]`).
  - `app.services.study_metadata.study_properties(fields: dict, series: list[dict]) -> list[dict[str, str]]`
  - `app.services.study_metadata.properties_text(props: list[dict]) -> str`
  - `app.services.study_metadata.cover_rows(props: list[dict], study_date: date) -> list[tuple[str, str]]`
  - `create_pending_document(..., properties: list[dict[str, str]] | None = None, origin: str = DocumentOrigin.file, ...)`
  - Preview study dicts carry `"properties"` (list form) instead of `"extra_metadata"`.
  - Serialized documents carry `origin` and `properties`.

- [ ] **Step 1: Write the failing migration test**

Append to `backend/tests/test_migrations.py`:

```python
BEFORE_PROPERTIES = "a3f9c2d71b48"


def test_properties_and_origin_backfilled(engine):
    cfg = _cfg()
    command.downgrade(cfg, BEFORE_PROPERTIES)
    try:
        with engine.begin() as conn:
            insert = text(
                "INSERT INTO documents (id, title, description, notes, doc_type, ocr_languages, ocr_enabled, "
                "summary_enabled, translation_enabled, translation_language, document_date, status, "
                "extra_metadata, created_at, updated_at) VALUES (gen_random_uuid(), :title, '', '', :doc_type, "
                "'ita', true, true, false, 'it', '2026-01-01', 'ready', CAST(:meta AS jsonb), now(), now())"
            )
            meta = (
                '{"modality": "DX", "patient_name": "Mario Rossi", "sex": "", "custom": "x", '
                '"series": [{"description": "AP", "images": 2}, {"description": "Lateral", "images": 2}]}'
            )
            conn.execute(insert, {"title": "PropStudy", "doc_type": "pdf", "meta": meta})
            conn.execute(insert, {"title": "PropScan", "doc_type": "scan", "meta": None})
            conn.execute(insert, {"title": "PropFile", "doc_type": "pdf", "meta": None})
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = {
                title: (origin, props)
                for title, origin, props in conn.execute(
                    text("SELECT title, origin, properties FROM documents WHERE title LIKE 'Prop%'")
                ).all()
            }
        assert rows["PropStudy"] == (
            "xray_disc",
            [
                {"key": "Patient", "value": "Mario Rossi"},
                {"key": "Modality", "value": "DX"},
                {"key": "Series", "value": "AP (2); Lateral (2)"},
                {"key": "custom", "value": "x"},
            ],
        )
        assert rows["PropScan"] == ("scan", [])
        assert rows["PropFile"] == ("file", [])
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM documents WHERE title LIKE 'Prop%'"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /opt/origami/backend && uv run pytest tests/test_migrations.py::test_properties_and_origin_backfilled -q`
Expected: FAIL (column `origin` does not exist).

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/e4b7c19a2d58_document_properties_origin.py`:

```python
"""document properties (ordered key/value list) and origin

Revision ID: e4b7c19a2d58
Revises: a3f9c2d71b48
Create Date: 2026-10-12 21:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e4b7c19a2d58"
down_revision: Union[str, Sequence[str], None] = "a3f9c2d71b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Copy of app.services.study_metadata.FIELDS at the time of this migration (no app imports here).
LABELS = [
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

documents = sa.table(
    "documents",
    sa.column("id", sa.Uuid()),
    sa.column("origin", sa.String()),
    sa.column("properties", postgresql.JSONB()),
    sa.column("extra_metadata", postgresql.JSONB()),
)


def _as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _to_rows(meta: dict) -> list[dict]:
    rows = [{"key": label, "value": _as_text(meta[key])} for key, label in LABELS if meta.get(key)]
    series = meta.get("series") or []
    if series:
        rows.append(
            {"key": "Series", "value": "; ".join(f"{s['description']} ({s['images']})" for s in series)}
        )
    known = {key for key, _ in LABELS} | {"series"}
    rows += [{"key": key, "value": _as_text(value)} for key, value in meta.items() if key not in known and value]
    return rows


def upgrade() -> None:
    op.add_column("documents", sa.Column("origin", sa.String(), nullable=False, server_default="file"))
    op.execute("UPDATE documents SET origin = 'scan' WHERE doc_type = 'scan'")
    op.execute("UPDATE documents SET origin = 'xray_disc' WHERE extra_metadata IS NOT NULL")
    op.alter_column("documents", "extra_metadata", new_column_name="properties")
    conn = op.get_bind()
    for doc_id, meta in conn.execute(
        sa.select(documents.c.id, documents.c.properties).where(documents.c.properties.is_not(None))
    ).all():
        rows = _to_rows(meta) if isinstance(meta, dict) else []
        conn.execute(documents.update().where(documents.c.id == doc_id).values(properties=rows))
    op.execute("UPDATE documents SET properties = '[]'::jsonb WHERE properties IS NULL")
    op.alter_column(
        "documents", "properties", nullable=False, server_default=sa.text("'[]'::jsonb")
    )


def downgrade() -> None:
    op.alter_column(
        "documents", "properties", new_column_name="extra_metadata", nullable=True, server_default=None
    )
    conn = op.get_bind()
    # best effort: X-ray studies get a dict keyed by label; other documents lose their properties
    for doc_id, origin, rows in conn.execute(
        sa.select(documents.c.id, documents.c.origin, documents.c.extra_metadata)
    ).all():
        meta = {row["key"]: row["value"] for row in rows or []} if origin == "xray_disc" else None
        conn.execute(
            documents.update().where(documents.c.id == doc_id).values(
                extra_metadata=sa.null() if meta is None else meta
            )
        )
    op.drop_column("documents", "origin")
```

Note: the `documents` table helper declares both `properties` and `extra_metadata` because the column has a different name in upgrade and downgrade. In downgrade, `sa.null()` writes SQL NULL (a plain `None` through `JSONB()` would store JSON `null`).

- [ ] **Step 4: Update the model**

In `backend/app/models/document.py`: add `text` to the `sqlalchemy` import (`from sqlalchemy import Column, text`), add after `DocStatus`:

```python
class DocumentOrigin(StrEnum):
    file = "file"  # uploaded file
    scan = "scan"  # compiled from a scan session
    xray_disc = "xray_disc"  # imported X-ray disc study
```

Replace the `extra_metadata` field (and its comment) with:

```python
    origin: str = DocumentOrigin.file  # DocumentOrigin
    # ordered key/value rows ({"key", "value"}), editable on every document; searchable
    properties: list[dict[str, str]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
```

Drop the now-unused `Any` import if nothing else uses it. In `backend/app/models/__init__.py` import and export `DocumentOrigin` (add to the import list and `__all__`, alphabetical after `DocumentAttachment`).

- [ ] **Step 5: Replace the study metadata helpers**

Replace everything after `FIELDS` in `backend/app/services/study_metadata.py` (and update the module docstring and the `FIELDS` comment — the frontend copy `STUDY_FIELDS` is removed in Task 3):

```python
"""Labels and plain text for document properties (X-ray study data, cover page, search, summary)."""

from datetime import date

# Labels of the study fields read from an X-ray disc, in display order.
FIELDS: list[tuple[str, str]] = [ ...unchanged... ]


def study_properties(fields: dict, series: list[dict]) -> list[dict[str, str]]:
    rows = [{"key": label, "value": str(fields[key])} for key, label in FIELDS if fields.get(key)]
    if series:
        rows.append({"key": "Series", "value": "; ".join(f"{s['description']} ({s['images']})" for s in series)})
    return rows


def properties_text(props: list[dict]) -> str:
    return "\n".join(f"{p['key']}: {p['value']}" for p in props)


def cover_rows(props: list[dict], study_date: date) -> list[tuple[str, str]]:
    return [("Study date", study_date.isoformat()), *((p["key"], p["value"]) for p in props)]
```

- [ ] **Step 6: Update callers**

`backend/app/services/dicom_parse.py` `_finish_study`: import `from app.services.study_metadata import study_properties`; replace the `extra` lines and the `"extra_metadata": extra,` entry:

```python
    shown = {k: v for k, v in fields.items() if k != "study_date"}
    if description:
        shown["study_description"] = description
    ...
        "properties": study_properties(shown, _series(images)),
```

`backend/app/api/uploads.py` `create_pending_document`: replace parameter `extra_metadata: dict | None = None,` with

```python
    properties: list[dict[str, str]] | None = None,
    origin: str = DocumentOrigin.file,
```

and in the `Document(...)` call replace `extra_metadata=extra_metadata,` with `properties=properties or [],` and `origin=origin,`. Import `DocumentOrigin` from `app.models`.

`backend/app/api/disc_imports.py` commit: replace `extra_metadata=study["extra_metadata"],` with `properties=study["properties"],` and `origin=DocumentOrigin.xray_disc,` (import `DocumentOrigin`).

`backend/app/api/scan.py` compile: add `origin=DocumentOrigin.scan,` to the `create_pending_document(...)` call (import `DocumentOrigin`).

`backend/app/worker/pipeline.py`:
- import: `from app.services.study_metadata import cover_rows, properties_text`; add `DocumentOrigin` to the `app.models` import.
- `_extract_content`: `if doc.origin == DocumentOrigin.xray_disc or "disc_import_id" in payload:`
- `_build_study_files`: `cover_rows(doc.properties, doc.document_date)`
- `_ensure_summary`:
  ```python
      if doc.origin == DocumentOrigin.xray_disc:
          # X-ray study: summarize the report and the study data, never the images through vision
          text = "\n\n".join(filter(None, [properties_text(doc.properties), text]))
  ```
- `_metadata_content`: `if doc.properties: parts.append(properties_text(doc.properties))`

Run `grep -rn extra_metadata backend/app` — it must print nothing.

- [ ] **Step 7: Update existing tests**

- `tests/test_schema.py::test_disc_import_schema`: `assert {"notes", "properties", "origin"} <= doc_cols`. In `test_attachments_and_their_chunks_cascade_with_document` build the document with `properties=[{"key": "Modality", "value": "DX"}]` and assert `doc.properties == [{"key": "Modality", "value": "DX"}]` and `doc.origin == "file"`.
- `tests/test_attachments_api.py`, `tests/test_bundles.py`: replace `extra_metadata={}` with `origin=DocumentOrigin.xray_disc` (import it).
- `tests/test_study_pipeline.py`: replace `META` with
  `PROPS = [{"key": "Patient", "value": "Mario Rossi"}, {"key": "Modality", "value": "DX"}, {"key": "Series", "value": "AP (2)"}]`
  and in `make_study_doc` use `properties=PROPS, origin=DocumentOrigin.xray_disc`. Existing assertions on `"Patient: Mario Rossi"` stay.
- `tests/test_notes.py`: `META` becomes the same `PROPS` list as above plus `{"key": "Institution", "value": "Ospedale Test"}` placed before Series; replace the two helper tests:
  ```python
  def test_study_properties_lists_known_fields_in_order():
      fields = {"modality": "DX", "patient_name": "Mario Rossi", "sex": "", "institution": "Ospedale Test"}
      series = [{"description": "AP", "images": 2}, {"description": "Lateral", "images": 1}]
      assert study_properties(fields, series) == [
          {"key": "Patient", "value": "Mario Rossi"},
          {"key": "Modality", "value": "DX"},
          {"key": "Institution", "value": "Ospedale Test"},
          {"key": "Series", "value": "AP (2); Lateral (1)"},
      ]
      assert study_properties({}, []) == []


  def test_properties_text_and_cover_rows():
      props = [{"key": "Patient", "value": "Mario Rossi"}, {"key": "Colore", "value": "blu"}]
      assert properties_text(props) == "Patient: Mario Rossi\nColore: blu"
      assert properties_text([]) == ""
      assert cover_rows(props, date(2026, 8, 28)) == [
          ("Study date", "2026-08-28"), ("Patient", "Mario Rossi"), ("Colore", "blu"),
      ]
  ```
  `test_refresh_metadata_rebuilds_chunk_with_notes_and_study_data`: `make_ready_doc(session, notes=..., properties=META)` (a plain `file`-origin document — properties are searchable on every document); the `startswith("Visita\n\nControllo a 3 mesi\n\nPatient: Mario Rossi")` assertion stays.
- `tests/test_dicom_parse.py`: replace the `meta = study["extra_metadata"]` block with
  ```python
      props = {p["key"]: p["value"] for p in study["properties"]}
      assert props["Patient ID"] == "P-1"  # DICOM wins over the htm value
      assert props["Birth date"] == "1980-01-01"
      assert props["Series"] == "AP (1); Lateral (1)"
      assert [p["key"] for p in study["properties"]][-1] == "Series"
  ```
  and the second test: `assert {p["key"]: p["value"] for p in study["properties"]}["Patient ID"] == "P-77"`.
- `tests/test_disc_imports_api.py::test_commit_creates_documents_and_jobs`: replace the `extra_metadata` assertion with
  ```python
      assert doc_json["origin"] == "xray_disc"
      assert {"key": "Patient", "value": "Mario Rossi"} in doc_json["properties"]
      assert "extra_metadata" not in doc_json
  ```

- [ ] **Step 8: Add origin tests**

`tests/test_scan_compile.py::test_compile_creates_document_and_job`: add `assert body["origin"] == "scan"`.
`tests/test_uploads.py::test_upload_pdf_creates_pending_document_and_job`: add `assert body["origin"] == "file"` and `assert body["properties"] == []`.

Append to `tests/test_study_pipeline.py` (Review Focus 2):

```python
def test_properties_on_uploaded_pdf_do_not_route_to_study(session, pipeline_storage, llm_stub):
    pipeline_storage.abs_path("Plain.pdf").parent.mkdir(parents=True, exist_ok=True)
    make_text_pdf(pipeline_storage.abs_path("Plain.pdf"), ["Fattura numero 12 del fornitore"])
    doc = Document(
        title="Plain", doc_type=DocType.pdf, file_path="Plain.pdf", ocr_enabled=False,
        properties=[{"key": "Fornitore", "value": "ACME"}],
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    doc = run(session, doc, {"document_id": str(doc.id)})
    assert doc.status == DocStatus.ready
    assert doc.origin == "file"
    assert session.exec(select(DocumentAttachment)).all() == []
    content = session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()
    assert content and all(c.attachment_id is None for c in content)
    metadata = session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).one()
    assert "Fornitore: ACME" in metadata.content
```

- [ ] **Step 9: Run the full backend suite**

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: all pass (pre-existing starlette/httpx warning only).

- [ ] **Step 10: Commit**

```bash
git add backend
git commit -m "feat: document origin column and ordered properties replacing extra_metadata"
```

---

### Task 2: PATCH document properties

**Files:**
- Modify: `backend/app/api/documents.py:38-47` and `update_document`
- Test: `backend/tests/test_properties.py` (new)

**Interfaces:**
- Consumes: `Document.properties` from Task 1; existing `queue_metadata_refresh`, `api_error`.
- Produces: `PATCH /api/documents/{id}` accepts `{"properties": [{"key": str, "value": str}, ...]}`; returns the serialized document with normalized `properties`. Errors: 422 `{"error": {"code": "validation_error", "message": "Every value needs a key" | "Duplicate key: <key>" | "Request validation failed"}}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_properties.py`:

```python
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job


def make_doc(session, **kw):
    doc = Document(title="Fattura", doc_type=DocType.pdf, status=DocStatus.ready, **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def refresh_jobs(session):
    return session.exec(select(Job).where(Job.type == "refresh_metadata")).all()


def patch(client, doc, properties):
    return client.patch(f"/api/documents/{doc.id}", json={"properties": properties})


def test_patch_properties_normalizes_and_refreshes(auth_client, session, storage):
    doc = make_doc(session)
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.metadata, content="Fattura"))
    session.commit()
    resp = patch(auth_client, doc, [
        {"key": "  Fornitore ", "value": " ACME  "},
        {"key": "", "value": "   "},
        {"key": "Importo", "value": ""},
    ])
    assert resp.status_code == 200
    assert resp.json()["properties"] == [{"key": "Fornitore", "value": "ACME"}, {"key": "Importo", "value": ""}]
    session.refresh(doc)
    assert doc.properties == [{"key": "Fornitore", "value": "ACME"}, {"key": "Importo", "value": ""}]
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).all() == []
    assert len(refresh_jobs(session)) == 1


def test_patch_same_properties_queues_nothing(auth_client, session, storage):
    doc = make_doc(session, properties=[{"key": "Fornitore", "value": "ACME"}])
    resp = patch(auth_client, doc, [{"key": "Fornitore ", "value": " ACME"}, {"key": "", "value": ""}])
    assert resp.status_code == 200
    assert refresh_jobs(session) == []


def test_patch_properties_keeps_order(auth_client, session, storage):
    doc = make_doc(session)
    rows = [{"key": k, "value": str(i)} for i, k in enumerate(["Zeta", "Alfa", "Mu"])]
    assert patch(auth_client, doc, rows).json()["properties"] == rows


def test_patch_properties_null_is_ignored(auth_client, session, storage):
    doc = make_doc(session, properties=[{"key": "A", "value": "1"}])
    resp = patch(auth_client, doc, None)
    assert resp.status_code == 200
    assert resp.json()["properties"] == [{"key": "A", "value": "1"}]
    assert refresh_jobs(session) == []


def test_patch_value_without_key_422(auth_client, session, storage):
    doc = make_doc(session)
    resp = patch(auth_client, doc, [{"key": "  ", "value": "ACME"}])
    assert resp.status_code == 422
    assert resp.json()["error"] == {"code": "validation_error", "message": "Every value needs a key", "detail": None}


def test_patch_duplicate_keys_422(auth_client, session, storage):
    doc = make_doc(session)
    resp = patch(auth_client, doc, [{"key": "Colore", "value": "blu"}, {"key": " colore", "value": "rosso"}])
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "Duplicate key: colore"
    session.refresh(doc)
    assert doc.properties == []


def test_patch_properties_limits_422(auth_client, session, storage):
    doc = make_doc(session)
    assert patch(auth_client, doc, [{"key": "k" * 101, "value": ""}]).status_code == 422
    assert patch(auth_client, doc, [{"key": "k", "value": "v" * 2001}]).status_code == 422
    rows = [{"key": f"k{i}", "value": ""} for i in range(101)]
    assert patch(auth_client, doc, rows).status_code == 422
    assert patch(auth_client, doc, [{"key": "k" * 100, "value": "v" * 2000}]).status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /opt/origami/backend && uv run pytest tests/test_properties.py -q`
Expected: FAIL (properties ignored by the PATCH model; `properties` not in response changes).

- [ ] **Step 3: Implement**

In `backend/app/api/documents.py`: change imports to `from typing import Annotated, Literal` and `from pydantic import BaseModel, Field, StringConstraints`. Replace `DocumentPatch` and `METADATA_FIELDS`:

```python
class PropertyRow(BaseModel):
    key: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
    value: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class DocumentPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    folder_id: int | None = None
    tag_ids: list[int] | None = None
    document_date: date | None = None
    notes: str | None = None
    properties: list[PropertyRow] | None = Field(default=None, max_length=100)


METADATA_FIELDS = {"title", "description", "notes", "properties"}


def normalize_properties(rows: list[dict]) -> list[dict[str, str]]:
    """Drop empty rows; every value needs a key; keys are unique ignoring case. Rows arrive stripped."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not row["key"] and not row["value"]:
            continue
        if not row["key"]:
            raise api_error(422, "validation_error", "Every value needs a key")
        if row["key"].casefold() in seen:
            raise api_error(422, "validation_error", f"Duplicate key: {row['key']}")
        seen.add(row["key"].casefold())
        result.append({"key": row["key"], "value": row["value"]})
    return result
```

In `update_document`, right after the `document_date` pop:

```python
    if "properties" in fields:
        if fields["properties"] is None:
            fields.pop("properties")  # column is NOT NULL; null means "unchanged"
        else:
            fields["properties"] = normalize_properties(fields["properties"])
```

The existing `metadata_changed` comparison (`getattr(doc, key) != value`) then covers properties: list-of-dict equality is order-sensitive, which is what we want.

- [ ] **Step 4: Run tests**

Run: `cd /opt/origami/backend && uv run pytest tests/test_properties.py tests/test_notes.py tests/test_documents.py -q`
Expected: PASS.

- [ ] **Step 5: Full backend suite and commit**

Run: `cd /opt/origami/backend && uv run pytest -q` — all pass.

```bash
git add backend
git commit -m "feat: edit document properties through PATCH with validation"
```

---

### Task 3: Frontend types, documentView and properties helpers

**Files:**
- Modify: `frontend/src/lib/types.ts:63,187`, `frontend/src/lib/documentView.ts`, `frontend/src/lib/documentView.test.ts`, fixtures `frontend/src/components/DocumentCard.test.tsx:17`, `frontend/src/lib/discImport.test.ts:27`, `frontend/src/components/DiscImportDialog.test.tsx:34`
- Modify (temporarily): `frontend/src/pages/DocumentPage.tsx` (only the two `extra_metadata` uses + `studyRows` import, so `tsc` passes; the panel arrives in Task 4)
- Create: `frontend/src/lib/properties.ts`, `frontend/src/lib/properties.test.ts`

**Interfaces:**
- Produces:
  - `types.ts`: `export interface Property { key: string; value: string }`; `export type DocumentOrigin = "file" | "scan" | "xray_disc"`; `Document.origin: DocumentOrigin`; `Document.properties: Property[]`; `DiscStudy.properties: Property[]`.
  - `documentView.ts`: `viewTabs(doc: Pick<Document, "attachments" | "origin">): ViewTab[]`. `STUDY_FIELDS` and `studyRows` removed.
  - `properties.ts`: `normalizeProperties(rows: Property[]): { rows: Property[]; error: string | null }`; `emptyRow(): Property`.

- [ ] **Step 1: Write failing tests**

Create `frontend/src/lib/properties.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { emptyRow, normalizeProperties } from "./properties";

describe("normalizeProperties", () => {
  it("trims and drops empty rows, keeping order", () => {
    expect(
      normalizeProperties([
        { key: " Zeta ", value: " 1 " },
        { key: "", value: "  " },
        { key: "Alfa", value: "" },
      ]),
    ).toEqual({ rows: [{ key: "Zeta", value: "1" }, { key: "Alfa", value: "" }], error: null });
  });

  it("needs a key for every value", () => {
    expect(normalizeProperties([{ key: " ", value: "ACME" }]).error).toBe("Every value needs a key");
  });

  it("rejects duplicate keys ignoring case", () => {
    expect(normalizeProperties([{ key: "Colore", value: "blu" }, { key: "colore ", value: "rosso" }]).error).toBe(
      "Duplicate key: colore",
    );
  });

  it("accepts an empty list", () => {
    expect(normalizeProperties([])).toEqual({ rows: [], error: null });
  });
});

it("emptyRow is a fresh blank row", () => {
  const a = emptyRow();
  expect(a).toEqual({ key: "", value: "" });
  expect(emptyRow()).not.toBe(a);
});
```

In `frontend/src/lib/documentView.test.ts`: change the import to `import { activeViewTab, documentLink, viewTabs } from "./documentView";`, replace every `extra_metadata: null` with `origin: "file" as const` and every `extra_metadata: {}` with `origin: "xray_disc" as const`, and delete the whole `describe("studyRows", ...)` block. Add:

```ts
  it("labels the main tab Document for scans and uploads", () => {
    expect(viewTabs({ attachments: [], origin: "scan" })[0].label).toBe("Document");
  });
```

inside `describe("viewTabs", ...)`.

- [ ] **Step 2: Run to verify failure**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/properties.test.ts src/lib/documentView.test.ts`
Expected: FAIL (module `./properties` not found; `origin` unused → "Images" check fails).

- [ ] **Step 3: Implement**

`frontend/src/lib/properties.ts`:

```ts
import type { Property } from "./types";

/** Same rules as the backend PATCH: trim, drop empty rows, a key for every value, unique keys ignoring case. */
export function normalizeProperties(rows: Property[]): { rows: Property[]; error: string | null } {
  const result: Property[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    const value = row.value.trim();
    if (!key && !value) continue;
    if (!key) return { rows: result, error: "Every value needs a key" };
    if (seen.has(key.toLowerCase())) return { rows: result, error: `Duplicate key: ${key}` };
    seen.add(key.toLowerCase());
    result.push({ key, value });
  }
  return { rows: result, error: null };
}

export function emptyRow(): Property {
  return { key: "", value: "" };
}
```

`types.ts`: add near `Attachment`:

```ts
export interface Property {
  key: string;
  value: string;
}

export type DocumentOrigin = "file" | "scan" | "xray_disc";
```

In `Document` replace the `extra_metadata` line with:

```ts
  origin: DocumentOrigin; // file = upload, scan = scan compile, xray_disc = disc import
  properties: Property[];
```

In `DiscStudy` replace `extra_metadata: Record<string, unknown>;` with `properties: Property[];`.

`documentView.ts`: `export function viewTabs(doc: Pick<Document, "attachments" | "origin">): ViewTab[]` with main label `doc.origin === "xray_disc" ? "Images" : "Document"`; delete `STUDY_FIELDS` and `studyRows`.

Fixtures: `DocumentCard.test.tsx` replace `extra_metadata: null,` with `origin: "file",` and `properties: [],`. `discImport.test.ts` and `DiscImportDialog.test.tsx`: replace `extra_metadata: {},` with `properties: [],`.

`DocumentPage.tsx` (minimal, Task 4 replaces the panel): import line becomes `import { activeViewTab, viewTabs, type ViewTab } from "@/lib/documentView";`; in `confirmReprocess` replace `!doc.extra_metadata` with `doc.origin !== "xray_disc"`; replace `const details = studyRows(doc.extra_metadata);` with `const details: [string, string][] = doc.properties.map((p) => [p.key, p.value]);` (the existing `<dl>` keeps working until Task 4).

Run `grep -rn "extra_metadata\|studyRows\|STUDY_FIELDS" frontend/src` — must print nothing.

- [ ] **Step 4: Run frontend suites**

Run: `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint`
Expected: all pass (3 pre-existing oxlint warnings).

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: frontend document origin and properties types, normalize helper"
```

---

### Task 4: PropertiesPanel on the document page, README

**Files:**
- Create: `frontend/src/components/PropertiesPanel.tsx`, `frontend/src/components/PropertiesPanel.test.tsx`
- Modify: `frontend/src/pages/DocumentPage.tsx` (aside panel between Tags and Save), `README.md`

**Interfaces:**
- Consumes: `Property`, `Document` (types.ts); `normalizeProperties`, `emptyRow` (properties.ts); `api.patch`, `ApiError` (`@/lib/api`); `Button`, `Input` (`@/components/ui/*`).
- Produces: `export function PropertiesPanel({ doc }: { doc: Pick<Document, "id" | "properties"> }): JSX.Element`.

- [ ] **Step 1: Write the failing component test**

Create `frontend/src/components/PropertiesPanel.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { PropertiesPanel } from "./PropertiesPanel";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, patch: vi.fn() } };
});

const PROPS = [
  { key: "Patient", value: "Mario Rossi" },
  { key: "Modality", value: "DX" },
];

function renderPanel(properties = PROPS) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <PropertiesPanel doc={{ id: "d1", properties }} />
    </QueryClientProvider>,
  );
  return { invalidate };
}

afterEach(() => vi.mocked(api.patch).mockReset());

describe("PropertiesPanel", () => {
  it("shows the rows in a scrolling frame", () => {
    renderPanel();
    const frame = screen.getByTestId("properties-frame");
    expect(frame).toHaveClass("max-h-64", "overflow-y-auto");
    expect(within(frame).getByText("Patient")).toBeInTheDocument();
    expect(within(frame).getByText("Mario Rossi")).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    renderPanel([]);
    expect(screen.getByText("No properties")).toBeInTheDocument();
  });

  it("edits, adds and deletes rows, then saves the normalized list", async () => {
    vi.mocked(api.patch).mockResolvedValueOnce({});
    const { invalidate } = renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("properties-frame")).toHaveClass("max-h-64", "overflow-y-auto");
    await userEvent.click(screen.getByRole("button", { name: "Delete row Modality" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    const keys = screen.getAllByRole("textbox", { name: "Key" });
    const values = screen.getAllByRole("textbox", { name: "Value" });
    await userEvent.type(keys[1], " Colore ");
    await userEvent.type(values[1], "blu");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(api.patch).toHaveBeenCalledWith("/api/documents/d1", {
      properties: [{ key: "Patient", value: "Mario Rossi" }, { key: "Colore", value: "blu" }],
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["document", "d1"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["documents"] });
    expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("blocks save with an inline validation error", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await userEvent.type(screen.getAllByRole("textbox", { name: "Key" })[2], "patient");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText("Duplicate key: patient")).toBeInTheDocument();
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("shows the server error message", async () => {
    vi.mocked(api.patch).mockRejectedValueOnce(new ApiError(422, "validation_error", "Request validation failed"));
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Request validation failed")).toBeInTheDocument();
  });

  it("cancel restores the server rows", async () => {
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    await userEvent.clear(screen.getAllByRole("textbox", { name: "Value" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Delete row Modality" }));
    await userEvent.click(screen.getByRole("button", { name: "Add row" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Mario Rossi")).toBeInTheDocument();
    expect(screen.getByText("DX")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getAllByRole("textbox", { name: "Key" })).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /opt/origami/frontend && npx vitest run src/components/PropertiesPanel.test.tsx`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/PropertiesPanel.tsx`:

```tsx
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { emptyRow, normalizeProperties } from "@/lib/properties";
import type { Document, Property } from "@/lib/types";

const FRAME = "max-h-64 overflow-y-auto rounded border border-zinc-200";

export function PropertiesPanel({ doc }: { doc: Pick<Document, "id" | "properties"> }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<Property[] | null>(null); // null = view mode
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (properties: Property[]) => api.patch(`/api/documents/${doc.id}`, { properties }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", doc.id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      setRows(null);
    },
  });

  const startEdit = () => {
    setRows(doc.properties.length > 0 ? doc.properties.map((p) => ({ ...p })) : [emptyRow()]);
    setError(null);
    save.reset();
  };
  const cancel = () => {
    setRows(null);
    setError(null);
    save.reset();
  };
  const update = (index: number, change: Partial<Property>) =>
    setRows((current) => current!.map((row, i) => (i === index ? { ...row, ...change } : row)));
  const submit = () => {
    const result = normalizeProperties(rows!);
    setError(result.error);
    if (!result.error) save.mutate(result.rows);
  };

  if (rows === null) {
    return (
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-medium">Properties</span>
          <Button variant="outline" size="sm" onClick={startEdit}>
            Edit
          </Button>
        </div>
        <div data-testid="properties-frame" className={`${FRAME} bg-zinc-50 p-3 text-xs`}>
          {doc.properties.length === 0 ? (
            <p className="text-zinc-400">No properties</p>
          ) : (
            <dl className="space-y-1">
              {doc.properties.map((p) => (
                <div key={p.key} className="flex gap-2">
                  <dt className="w-24 shrink-0 break-words font-medium text-zinc-500">{p.key}</dt>
                  <dd className="min-w-0 break-words">{p.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <span className="mb-1 block text-sm font-medium">Properties</span>
      <div data-testid="properties-frame" className={FRAME}>
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-500">
            <tr>
              <th className="px-1 py-1 font-medium">Key</th>
              <th className="px-1 py-1 font-medium">Value</th>
              <th className="w-6" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td className="p-1">
                  <Input aria-label="Key" className="h-7 text-xs" value={row.key} onChange={(e) => update(i, { key: e.target.value })} />
                </td>
                <td className="p-1">
                  <Input aria-label="Value" className="h-7 text-xs" value={row.value} onChange={(e) => update(i, { value: e.target.value })} />
                </td>
                <td className="p-1 text-center">
                  <button
                    type="button"
                    aria-label={`Delete row ${row.key || i + 1}`}
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}
                    className="cursor-pointer text-zinc-400 hover:text-red-600"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(error || save.isError) && (
        <p className="mt-1 text-xs text-red-600">
          {error ?? (save.error instanceof ApiError ? save.error.message : "Save failed")}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        <Button variant="outline" size="sm" onClick={() => setRows([...rows, emptyRow()])}>
          Add row
        </Button>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={cancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
```

`Button` supports `size="sm"` and `variant="outline"`. In `DocumentPage` there is also a main "Save" button; the panel's Save is scoped inside the panel (the test renders the panel alone, so the name is unique there).

Note on the test "Delete row Modality": the delete button's label uses the row's current key, so it reads "Delete row Modality" for the second server row.

- [ ] **Step 4: Run the component test**

Run: `cd /opt/origami/frontend && npx vitest run src/components/PropertiesPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Wire into DocumentPage**

In `frontend/src/pages/DocumentPage.tsx`: `import { PropertiesPanel } from "@/components/PropertiesPanel";`; delete the `const details ...` line; replace the whole `{details.length > 0 && (<dl ...>...</dl>)}` block (between the Tags `<div>` and the Save `<Button>`) with `<PropertiesPanel doc={doc} />`. The panel is shown for every document.

- [ ] **Step 6: README**

In `README.md` Features, after the **Notes** line add:

```markdown
- **Properties:** a free key/value table on every document (searchable). Edit it on the document page with **Edit**; rows keep their order.
```

In the X-ray disc import section, in **Result.**, after the "Images and Report tabs" bullet add:

```markdown
- The study data read from the disc (patient, modality, series, …) is stored as the document's **Properties**, which you can edit like on any other document.
```

- [ ] **Step 7: Full frontend suite**

Run: `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint && npm run build`
Expected: all pass (3 pre-existing oxlint warnings).

- [ ] **Step 8: Commit**

```bash
git add frontend README.md
git commit -m "feat: editable Properties panel on the document page"
```

---

## Final verification

- `cd /opt/origami/backend && uv run pytest -q` and the full frontend command both pass.
- `grep -rn extra_metadata backend/app frontend/src` prints nothing (only the old migration `a3f9c2d71b48` and the new migration mention it).
- Do not restart the service or merge; report to the user and ask.
