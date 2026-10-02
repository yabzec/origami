# Origami — AI Description, Office Documents, Folder Picker, Browse Sorting and Icons

**Date:** 2026-10-03
**Status:** Approved by user (brainstorming session), pending written-spec review

## 1. Overview

Five follow-ups after the scan-redesign release:

1. **AI summary becomes the description.** The summary prefills the Description field. A small "AI generated" label shows until the user edits the text.
2. **Scan carousel buttons.** Remove the ← and → buttons. Make × larger.
3. **Folder picker.** A drill-down tree picker replaces the flat folder `<select>`.
4. **Type icons.** SVG icons per type replace the emoji on browse cards. Scans use the PDF icon.
5. **Browse sorting.** An order-by dropdown, defaulting to document date (newest first).

Added during brainstorming: **office documents** (`.doc`, `.docx`, `.odt`, `.rtf`) are converted to PDF with headless LibreOffice, so they get a real visual preview. Download still returns the original file. There are no existing office documents, so no backfill is needed.

## 2. AI summary → description

### Backend
- `_ensure_summary` (pipeline): after storing a non-empty summary, set `doc.description = summary` when `doc.description` is empty or whitespace.
- `_ensure_metadata_chunk`: build the content from the title only when `description == summary`, so the summary text is not embedded twice. Otherwise use `title + "\n\n" + description`, as today.
- `reprocess_document`: if `doc.description == doc.summary` (the description is still AI text), set `description = ""` before clearing the summary. The new summary then refills it. Edited descriptions are kept.
- Data-only Alembic migration (revises `b7c4e2a91d05`):
  - upgrade: `UPDATE documents SET description = summary WHERE (description IS NULL OR btrim(description) = '') AND summary IS NOT NULL AND btrim(summary) <> ''`
  - downgrade: `UPDATE documents SET description = '' WHERE description = summary`
- Existing metadata chunks keep their content. Only re-process rebuilds them; that is acceptable.

### Frontend (document page)
- Remove the grey summary box from the sidebar.
- Under the Description textarea, show a small muted label "AI generated" when the current textarea value equals `doc.summary` and `doc.summary` is non-empty. The value is compared live, so typing hides the label immediately. Saving keeps the equality rule.
- The Text tab keeps its "Summary:" block.

## 3. Office documents

### Formats and storage
- `EXTENSION_MAP` adds `.doc`, `.odt`, `.rtf` (all `DocType.text`). `.docx` is already `text`.
- New column `documents.preview_path TEXT NULL`: a relative path to a PDF used only for viewing. It is set for converted office documents and `NULL` for everything else.
- `file_path` stays the original upload, so Download returns `.docx`, `.odt` and the rest unchanged.

### Conversion service — `backend/app/services/convert.py`
- `OFFICE_EXTENSIONS = {".doc", ".docx", ".odt", ".rtf"}`.
- `office_to_pdf(src: Path) -> bytes` runs `soffice --headless --norestore -env:UserInstallation=file://<tmpdir>/profile --convert-to pdf --outdir <tmpdir> <src>`:
  - It uses a fresh temp directory per call, so concurrent runs do not collide on LibreOffice's profile lock.
  - Timeout 120 s. A non-zero exit, a timeout, or a missing output file raises `ConversionError` with a short message, including stderr trimmed to 500 characters.
- The soffice binary path comes from setting `soffice_path` (env `SOFFICE_PATH`, default `"soffice"`).

### Pipeline — text branch
For `doc_type == text` with a suffix in `OFFICE_EXTENSIONS`:
1. Try `office_to_pdf`. On success:
   - store the PDF as `files/{id}.preview.pdf` (new `Storage.store_preview(document_id, data)`)
   - set `preview_path` and `page_count`
   - content = `extract_pdf_text(preview)`, which gives per-page text and page numbers.
2. On `ConversionError`:
   - `.docx` falls back to `extract_docx` (single page, no preview). Log a warning.
   - The other formats re-raise, so the document ends `failed` with the conversion message.
3. Retry or re-process: if `preview_path` already exists on disk, reuse it instead of converting again.

`.txt` and `.md` are unchanged.

### File endpoint
- `GET /api/documents/{id}/file?preview=1` serves `preview_path` (media type `application/pdf`, `inline`) when it is set and the file exists. Otherwise it serves the normal file, the same as without the parameter. `download=1` always serves the original `file_path`.
- `delete_document` also deletes `preview_path`.

### Frontend
- `viewerKind` gains a document argument: a text document with `preview_path` → `"pdf"`, rendered with `fileUrl(id, { preview: true })`. Without a preview, text stays on the text view.
- `fileUrl(documentId, { download?, preview? })` adds `&preview=1`.
- The upload input's `accept` list (if present) adds the new extensions.

## 4. Scan carousel
- Remove the ← and → buttons, together with `onMove`/`movePage` and the reorder API call from the scan page. The backend reorder endpoint stays; it is unused by this UI.
- The × button: `type="button"`, `aria-label="Delete page"`, `text-base` with a padded hit area of about 24px, red, disabled while busy.

## 5. Folder picker — `frontend/src/components/FolderPicker.tsx`

Props: `value: number | null`, `onChange(id: number | null)`, `id?: string` (for the label).

- **Closed:** a button showing the selected path joined with ` / ` (for example `Bollette / 2026`), or `(root)` when `null`.
- **Open:** a popover anchored below the button.
  - The header holds a breadcrumb of the level being browsed, plus a "↑ Back" link when not at the top.
  - The list holds the folders of the current level, sorted by name. At the top level, a first entry "(root)" sets `null`.
  - Clicking a folder calls `onChange(folder.id)` and enters it: the list shows that folder's children. Repeat to go deeper; every click updates the selection.
  - A level with no children shows "No subfolders".
  - The popover closes on Esc, on a click outside, or with a "Done" button. On opening, it browses the level of the currently selected folder (so its siblings are visible) — top level when `null`.
- Pure helpers in `frontend/src/lib/folderTree.ts`:
  - `folderPath(folders, id) -> Folder[]` returns root-to-node and is cycle-safe.
  - `childrenOf(folders, parentId | null) -> Folder[]` returns the folders sorted by name.
- It replaces the folder `<Select>` in `UploadDialog`, `ScanSidebar` and `DocumentPage`. The sidebar `FolderTree` (browse navigation) is unchanged.

## 6. Browse page

### Sorting
- Backend: `GET /api/documents` gains `sort: Literal["date_desc", "date_asc", "added_desc", "title_asc"] = "date_desc"`:
  - `date_desc` → `document_date desc, created_at desc`
  - `date_asc` → `document_date asc, created_at asc`
  - `added_desc` → `created_at desc`
  - `title_asc` → `lower(title) asc, created_at desc`
  - Any other value → 422.
- Frontend: an order-by `<select>` with the labels "Document date (newest)", "Document date (oldest)", "Date added (newest)", "Title A–Z". It is stored in the URL query `?sort=` (absent = `date_desc`) and passed through `documentsQueryString`.

### Type icons — `frontend/src/components/DocTypeIcon.tsx`
- `iconKind(doc) -> "pdf" | "word" | "text" | "image" | "video"` (pure, unit-tested):
  - `pdf` and `scan` → `pdf`
  - `text` whose `original_filename` extension is `.doc`, `.docx`, `.odt` or `.rtf` → `word`; other text → `text`
  - `image` → `image`, `video` → `video`
- Inline SVG line icons, 24px on cards, colored per kind: pdf red-600, word blue-600, text zinc-500, image green-600, video purple-600. Each has a `title` for accessibility.
- `DocumentCard` uses `DocTypeIcon` instead of `TYPE_ICONS`.

## 7. Testing
- **Backend** (real Postgres, only `llm.py` mocked):
  - The pipeline fills an empty description from the summary, keeps a user description, and the metadata chunk is title-only when the two are equal.
  - Re-process clears an AI description and keeps an edited one.
  - Migration test: the data backfill sets description from summary, and the downgrade reverts it.
  - `office_to_pdf` with real soffice on a generated `.docx` (python-docx) and `.odt`: returns bytes starting with `%PDF`. A bad file raises `ConversionError`.
  - Pipeline on a `.docx` upload: `preview_path` is set, there are content chunks with page numbers, and `page_count` is set. Forced conversion failure (`SOFFICE_PATH=/bin/false`) on `.docx` → fallback text with no preview. The same on `.odt` → `failed`.
  - The file endpoint with `preview=1` returns the PDF, and `download=1` returns the original. Upload accepts `.odt`, `.doc` and `.rtf`.
  - Sorting: each `sort` value orders correctly, and a bad value gives 422.
- **Frontend** (vitest):
  - `folderPath` and `childrenOf`, including the cycle guard.
  - `iconKind`.
  - `viewerKind` with `preview_path`.
  - `fileUrl` with `preview`.
  - `documentsQueryString` with `sort`.
  - The "AI generated" label visibility helper `isAiDescription(description, summary)`.
- **Manual:** upload `.docx` and `.odt` files → PDF preview, Download returns the original. Folder picker drill-down in all three places. Sort persists on reload. The label disappears while typing.

## 8. Out of scope
- Server-side thumbnails or previews in cards (icons only).
- Inline folder creation in the picker (the sidebar `FolderTree` still creates folders).
- Spreadsheet or presentation formats (`.xlsx`, `.ods`, `.pptx`).

## 9. Decisions log
| Decision | Choice | Why |
|---|---|---|
| Summary placement | Copy into description, "AI generated" label | User choice |
| AI-label state | Derived (`description == summary`), no column | No schema change; editing breaks equality |
| Re-process of AI description | Cleared, refilled by new summary | Keeps AI text fresh, never overwrites user text |
| Office preview | LibreOffice → PDF, original kept for download | Real layout through the existing PDF viewer; soffice already installed |
| Preview storage | New `preview_path` column | Keeps `file_path` = original, so download semantics stay simple |
| Conversion failure | `.docx` falls back to python-docx; others fail | `.docx` was parseable before; no regression |
| Sort options | Document date both ways, date added, title | User choice (A) |
| Scan icon | PDF icon | User: a scan is a PDF |
| Word icon | By original file extension | `doc_type` is `text` for all text formats |
