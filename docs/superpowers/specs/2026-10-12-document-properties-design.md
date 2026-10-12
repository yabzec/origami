# Origami — Editable Document Properties and Origin: Design Spec

**Date:** 2026-10-12
**Status:** Approved by user (in chat, during the X-ray disc import session). Builds on `2026-10-12-xray-disc-import-design.md`; work continues on branch `feat/xray-disc-import`.

Approved by the user. Production DB already has migration a3f9c2d71b48 applied (documents.extra_metadata JSONB exists, one X-ray study row has a dict in it) — so this needs a NEW Alembic migration, never edit a3f9c2d71b48.

## Data model
- `documents.origin`: VARCHAR NOT NULL, server_default 'file'. Values: `file` (uploads), `scan` (documents created by the scan compile flow, backend/app/api/scan.py) and `xray_disc` (disc imports). Model: `class DocumentOrigin(StrEnum): file = "file"; scan = "scan"; xray_disc = "xray_disc"`; scan compile passes origin=DocumentOrigin.scan; `Document.origin: str = DocumentOrigin.file`. Export from app.models.
- Rename `documents.extra_metadata` → `documents.properties`, JSONB NOT NULL server_default '[]': an ORDERED LIST of `{"key": str, "value": str}` objects (a JSON object would lose row order). Model: `properties: list[dict[str, str]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")))` (match how the repo declares JSONB elsewhere).
- New migration (revises a3f9c2d71b48): add origin; set origin='scan' where doc_type='scan'; set origin='xray_disc' where extra_metadata IS NOT NULL; convert each non-null extra_metadata dict to the list form using the labels below (in label order; skip empty values; `series` → one row `{"key":"Series","value":"AP (2); Lateral (2)"}`; any unknown key kept with its raw key as label); NULL → []; then rename column to properties, set NOT NULL + default '[]'. Downgrade: reverse best-effort (list → dict keyed by label is acceptable; drop origin). Do the row conversion in Python inside the migration (select, transform, update) — keep the label map local to the migration file (migrations must not import app code).
- Labels (same as app/services/study_metadata.py FIELDS): patient_name Patient, birth_date Birth date, sex Sex, patient_id Patient ID, study_description Study, modality Modality, body_part Body part, institution Institution, accession_number Accession number, device Device; series → Series.

## Backend behaviour
- app/services/study_metadata.py: keep FIELDS; replace dict-based helpers with: `study_properties(fields: dict, series: list[dict]) -> list[dict]` (labels in FIELDS order, skip empty, append Series row if any) ; `properties_text(props: list[dict]) -> str` ("Key: Value" per line) ; `cover_rows(props, study_date) -> list[tuple[str,str]]` = [("Study date", iso)] + [(p["key"], p["value"])...]. Update every caller.
- dicom_parse.py: preview studies expose `properties` (list form, via study_properties) instead of `extra_metadata`. Same content.
- disc_imports.py commit: `create_pending_document(..., properties=study["properties"], origin=DocumentOrigin.xray_disc)` (rename the extra_metadata parameter to properties; add origin param defaulting to file).
- pipeline.py: study routing uses `doc.origin == DocumentOrigin.xray_disc or "disc_import_id" in payload` (NOT properties). Study summary input = properties_text(doc.properties) + report text (metadata first, as now). Metadata chunk (`_metadata_content`) includes properties_text for ALL documents with properties.
- documents.py PATCH: accept `properties: list[PropertyRow] | None` where PropertyRow = {key: str, value: str}. Normalize: strip both; drop rows where key and value are both empty; key empty but value non-empty → 422 validation_error; duplicate keys (case-insensitive, after strip) → 422; max 100 rows; key ≤ 100 chars; value ≤ 2000 chars. `properties` joins METADATA_FIELDS (refresh only when the normalized value actually changed — reuse the existing changed-value comparison).
- Serialized documents expose `origin` and `properties` (model_dump does it; drop extra_metadata everywhere).

## Frontend
- types.ts: `export interface Property { key: string; value: string }`; Document gets `origin: "file" | "scan" | "xray_disc"`, `properties: Property[]`; remove extra_metadata; DiscStudy gets `properties: Property[]` instead of extra_metadata. Fix fixtures (DocumentCard.test.tsx etc.).
- documentView.ts: viewTabs main label "Images" when `origin === "xray_disc"`, else "Document" (Pick origin instead of extra_metadata); remove STUDY_FIELDS/studyRows (labels now live in the data). Update tests.
- New pure helpers in `src/lib/properties.ts` (+ tests): `normalizeProperties(rows): { rows: Property[]; error: string | null }` mirroring the backend rules (trim, drop empty rows, error "Every value needs a key" / "Duplicate key: X"); `emptyRow()`.
- New component `src/components/PropertiesPanel.tsx` (+ test), used in DocumentPage where the study-details panel is now (between Tags and Save), shown for EVERY document:
  - View mode: heading "Properties" with an "Edit" button; `<dl>` key/value rows inside a frame with max height and internal scroll (`max-h-64 overflow-y-auto`, border, rounded); empty state "No properties".
  - Edit mode: a table Key | Value of text inputs, a × button per row to delete, "Add row" button, Save / Cancel. The table body is inside the same max-height scrolling frame. Save → `api.patch(/api/documents/{id}, { properties })` via its own mutation (not the main Save button), invalidates ["document", id] and ["documents"]; validation errors from normalizeProperties shown inline, server ApiError message shown too. Cancel restores the server value.
- DocumentPage: replace the studyRows panel with <PropertiesPanel doc={doc} />; reprocess pdfNote guard uses `doc.origin !== "xray_disc"` instead of extra_metadata.
- README: in the X-ray section, mention the study data is stored as editable Properties; in Features, one line: "Properties: a free key/value table on every document (searchable)".

## Tests (TDD where practical)
Backend: scan compile creates origin "scan"; migration backfills origin='scan' for doc_type scan; migration upgrade converts a dict row to the ordered list and sets origin (test in tests/test_migrations.py style: downgrade to a3f9c2d71b48, insert a row with extra_metadata dict, upgrade head, assert properties list + origin; always restore head); PATCH properties normalize/422 cases/refresh-on-change; pipeline routes on origin (a document with properties but origin file goes through the normal pdf path); update all existing tests that used extra_metadata. Frontend: properties.ts rules; PropertiesPanel view/edit/add/delete/save/cancel/validation; documentView changes.

## Process notes
Suites: `cd /opt/origami/backend && uv run pytest -q`; `cd /opt/origami/frontend && npx vitest run && npx tsc -b && npx oxlint && npm run build` (pre-existing: starlette/httpx warning, 3 oxlint warnings). Commits: backend, frontend, docs (or fewer logical commits). Never push, never touch main or the running service, never run alembic against the production DB (tests use origami_test).
