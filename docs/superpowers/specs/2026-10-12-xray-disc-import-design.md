# Origami — X-ray Disc Import: Design Spec

**Date:** 2026-10-12
**Status:** Approved by user (brainstorming session)

## 1. Overview

Hospitals hand out X-ray studies on a CD/DVD that follows the IHE PDI profile: full-resolution DICOM files under `DICOM/`, a `DICOMDIR` index, down-scaled JPEG copies with HTML pages under `IHE_PDI/`, a vendor `README.TXT` with cover data, often the signed radiology report as a PDF under `REPORT/`, and a Windows viewer (`VIEWERS/`, `*.exe`, `*.dll`).

The user wants to import such a disc into Origami from the client PC: pick the disc mount point in the browser, and get one document per study with the correct metadata, a PDF of the images, and the radiology report, without OCR or translation and with the AI summary on by default.

### Goals

- "Import X-ray disc" in the browse page: the user picks the disc root folder; only useful files are uploaded.
- One Origami document per DICOM study. In the explorer it is one entry; when opened it shows two views: **Images** (a generated PDF: cover page plus one page per image) and **Report** (the original report PDF, byte-identical).
- On disk, a document with attachments lives in its own sub-directory ("bundle dir") named after the document.
- Metadata is pre-filled from DICOM tags (with `README.TXT` and `IHE_PDI/Study000.htm` as fallback) and is editable before import.
- Images come from the DICOM files (full resolution, 12–16 bit) rather than the JPEG copies, which are reduced to at most 1024 px and marked as not suitable for diagnosis.
- Import defaults: OCR off, translation off, AI summary on.
- A new generic `notes` field on every document.
- When the selected folder holds no DICOM study, the same dialog asks the user to add the image and report files by hand.

### Non-goals

- Diagnostic-quality viewing (no DICOM viewer, no window/level controls, no 16-bit export). The PDF is for reference; the cover page says so.
- Reading the disc through the client agent. The browser folder picker is enough.
- Verifying the report's digital signature (`.p7m`, `.tsr`). The report PDF is stored unchanged, so verification stays possible outside Origami.
- Vision-based AI description of X-ray images.
- Attachments for other document kinds. The data model is generic, but the only producer is this import.
- Storing the raw DICOM files.

## 2. Storage and data model

### 2.1 Layout on disk

Documents without attachments stay flat, as today. A document with at least one attachment lives in a bundle dir:

```
<Folder>/
  Rx gamba (tibia e perone) Destro_a — 2026-08-28/      ← bundle dir = safe_name(title)
    Rx gamba (tibia e perone) Destro_a — 2026-08-28.pdf  ← Document.file_path (main file)
    Referto.pdf                                           ← attachment
```

`Document.file_path` points to the main file inside the bundle dir, so the existing file endpoint, preview, search and chat code is unchanged.

### 2.2 Database

New table `document_attachments` (Alembic migration):

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `document_id` | UUID FK `documents.id`, `ON DELETE CASCADE`, indexed | |
| `role` | str | `report` (only value for now) |
| `filename` | str | name inside the bundle dir, e.g. `Referto.pdf`; a second report becomes `Referto (2).pdf` |
| `file_path` | str | relative to `STORAGE_PATH` |
| `file_size` | int | |
| `page_count` | int nullable | |
| `created_at` | datetime | |

New columns on `documents`:

- `notes: str` (default `""`): free text from the user.
- `extra_metadata: JSON` (nullable): structured study data (patient name, birth date, sex, patient ID, accession number, institution, modality, body part, series/view list, device). Shown on the document page as a read-only key/value list.

New column on `chunks`: `attachment_id: int | None` (FK `document_attachments.id`, `ON DELETE CASCADE`). Content chunks extracted from an attachment carry it, so a search hit can open the right tab.

### 2.3 Tree rules (`tree_paths.py`, `tree_sync.py`, `storage.py`)

- **Bundle dir name:** `safe_name(title)`, with ` (n)` added on a clash. A clash is a sibling bundle dir, a sibling flat file with the same stem, or a sibling Origami folder with the same disk name. The main file inside is `<bundle dir name><ext>`.
- **Folder create or rename:** `disk_name_taken` also returns true when a sibling bundle dir has the same name. The API reports this as the existing name-conflict error.
- **Document rename:** the bundle dir is renamed and the main file inside follows. Attachment file names do not change. All moves go through the existing `MoveLog`, so they are undone on failure.
- **Document move to another folder:** the whole bundle dir moves (`move_dir`).
- **Document delete (single, bulk, recursive folder delete):** the whole bundle dir is removed after commit, with the existing post-commit cleanup tolerance for disk errors.
- **`check-storage`:** attachment `file_path` values count as referenced files. A bundle dir with no matching document is reported through its unreferenced files, as today.

### 2.4 API

- Document payloads (`GET /api/documents`, `GET /api/documents/{id}`) gain `notes`, `extra_metadata` and `attachments: [{id, role, filename, file_size, page_count}]`.
- `GET /api/documents/{id}/attachments/{attachment_id}/file`: inline by default, `?download=1` for download; same auth as `/file` (`get_current_user_flexible`, `?token=`).
- `PATCH /api/documents/{id}` accepts `notes`. A change to `title`, `description` or `notes` deletes the metadata chunk and enqueues a light `refresh_metadata` job that rebuilds and embeds it. This keeps notes searchable after edits.
- `POST /api/documents/upload` gains a `notes` form field, for symmetry.

## 3. Reading the disc on the client

- **Picker:** the browser folder picker (`<input webkitdirectory>`), already used for batch upload. It works on Windows, macOS and Linux with no install. The user selects the disc root (`D:\`, `/media/<user>/<label>`, `/Volumes/<label>`).
- **Client-side filter** (`frontend/src/lib/discImport.ts`, pure and unit-tested), applied to `webkitRelativePath`, case-insensitive:
  - Keep: every file under a `DICOM/` directory; `DICOMDIR`; `README.TXT`; `IHE_PDI/*.htm`; every `*.pdf`; every file with no extension anywhere outside the skip list (DICOM files are often extension-less).
  - Skip: anything under `VIEWERS/`, `COMMON/` or `IHE_PDI/` subdirectories (JPEG copies, icons), and `*.exe`, `*.dll`, `*.inf`, `*.ini`, `*.bak`, `*.bmp`, `*.gif`, `Thumbs.db`.
  - Also skip any single file over 200 MB.
  - The JPEG copies are uploaded only by the fallback path (§4.4).
- **Upload:** one multipart request with all kept files and their relative paths, using the existing XHR upload with progress.

## 4. Server: staging, parsing, commit

New modules: `backend/app/api/disc_imports.py`, `backend/app/services/dicom_parse.py`, `backend/app/services/dicom_render.py`, `backend/app/services/study_pdf.py`.

### 4.1 Flow

1. **Stage:** `POST /api/disc-imports` (multipart: `files[]` and `paths[]`).
   - The server writes the files under `tmp/disc-imports/<import_id>/` with their relative paths. Each path is normalised and must stay inside the staging dir.
   - It parses the files and returns a preview (§4.2).
   - The import id is a random UUID bound to the user, held in a new `disc_imports` table (`id`, `user_id`, `created_at`, `state`).
2. **Add files:** `POST /api/disc-imports/{id}/files`. Same multipart shape; used by the fallback and by "Add files". The server re-parses and returns a new preview.
3. **Thumbnails:** `GET /api/disc-imports/{id}/images/{image_key}/thumb` returns a 256 px JPEG rendered with the same pipeline as §5.
4. **Report preview:** `GET /api/disc-imports/{id}/reports/{report_key}/file` returns the staged PDF inline.
5. **Commit:** `POST /api/disc-imports/{id}/commit` with one entry per selected study:
   - `title`, `document_date`, `notes`, `folder_id`, `tag_ids` (a new patient tag is created by the dialog through the existing tags API before commit)
   - `image_keys` (ordered), `report_keys`
   - `ocr_enabled`, `ocr_languages`, `summary_enabled`, `translation_enabled`, `translation_language`

   For each study, the server creates the document (status `pending`, `doc_type` `pdf`, `extra_metadata` from the preview) and enqueues `process_document` with payload `{"disc_import_id": ..., "study": {...}}`. The worker builds the PDF (§5) and writes the bundle.
6. **Cancel:** `DELETE /api/disc-imports/{id}` removes the staging dir.

**Cleanup:**
- The worker deletes the staging dir after the last study job of an import has written its files.
- The periodic cleanup that already removes `*.part` files also removes staging dirs older than 24 hours.

### 4.2 Parsing (`dicom_parse.py`)

- **Images:** every staged file whose bytes 128–131 are `DICM`, read with `pydicom.dcmread(stop_before_pixels=True)`.
  - Group by `StudyInstanceUID`.
  - Inside a study, order by `SeriesNumber`, then `InstanceNumber`, then file path.
  - Encapsulated PDF objects (SOP Class `1.2.840.10008.5.1.4.1.1.104.1`) are extracted to staged PDFs and become report candidates for their study.
  - Structured reports and other non-image objects are ignored.
- **Readability:** each image gets `readable: bool`. A file is readable when its transfer syntax is in the set the installed decoders handle; otherwise the preview shows it greyed out with "format not supported".
- **Report candidates:** every staged `*.pdf` that is not a viewer manual.
  - Viewer manuals are PDFs in the disc root whose name contains `help`, `manual` or `guida`, or PDFs under `VIEWERS/`.
  - Candidates under a `REPORT/` directory, and encapsulated PDFs, are preselected.
  - With one study, all candidates attach to it. With several studies, an encapsulated PDF attaches to its own study; other candidates are unassigned and the user assigns them in the preview.
- **Vendor fallbacks:**
  - `README.TXT`: `Key: value` lines, accepting the Italian keys `Paziente`, `Data di nascita`, `Sesso`, `Data studio`, `Descrizione studio`, `Modalita`, and the English `Patient Name`.
  - `IHE_PDI/Study000.htm`: tag-stripped `Label:value` pairs (`Identificativo paziente`, `Accession Number`, `Data dell'esame`).
  - Both parsers are best effort: unknown formats produce no values and no error.
  - They fill only fields the DICOM tags leave empty, and with several studies they apply only when exactly one study exists.
- **Preview response:** per study: proposed metadata (§4.3), `extra_metadata`, images (`key`, series description, view, readable), report candidates (`key`, filename, page count, preselected), warnings. Top level: `no_study: bool` and unassigned reports.

### 4.3 Metadata mapping

| Field | Source, first non-empty wins | Notes |
|---|---|---|
| `title` | `StudyDescription`, else README `Descrizione studio`, else first `SeriesDescription`, else `"X-ray"` + modality; then ` — YYYY-MM-DD` when a date exists | A leading or embedded procedure code like `- 88.27.3` (regex `\s*-?\s*\d{2}\.\d{2}(\.\d+)?\s*`) is removed |
| `document_date` | `StudyDate`, `SeriesDate`, `ContentDate`, `AcquisitionDate`, README `Data studio`, htm `Data dell'esame`, today | |
| `description` | left empty, so the AI summary fills it (existing behaviour); with summary off, `"<study description> · <institution>"` | |
| `notes` | empty, user input | |
| tags | suggested new or existing tag with the patient name in title case (`PatientName` `ROSSI^MARIO` → `Mario Rossi`) | editable; a new tag is created by the dialog before commit |
| `extra_metadata` | `PatientName`, `PatientBirthDate`, `PatientSex`, `PatientID`, `AccessionNumber`, `InstitutionName`, `Modality`, `BodyPartExamined`, series list (`SeriesDescription` / `ViewPosition`, image count), `Manufacturer` + `ManufacturerModelName` | plus README/htm fallbacks |

### 4.4 Fallback: no DICOM study

- When parsing finds no DICOM image, the preview has `no_study: true`. The dialog shows "No X-ray study found on this disc. Add the files manually." with two pickers: **Images** (multiple, DICOM or JPEG/PNG) and **Report** (PDF, optional).
- Files go through `POST /api/disc-imports/{id}/files`.
- Loose JPEG/PNG files form one synthetic study ordered by filename, with empty metadata the user fills in.
- When DICOM files exist but none is readable, the server builds the images from the IHE_PDI JPEG copies instead.
  - The client uploads them on demand: the dialog asks the user to re-pick the same disc folder; that pass keeps only `IHE_PDI/**/*.jpg`.
  - The preview warns that these images are reduced in resolution.

## 5. Image PDF (`dicom_render.py`, `study_pdf.py`)

**New dependencies:** `pydicom`, `pylibjpeg`, `pylibjpeg-libjpeg` (JPEG baseline, extended and lossless, including transfer syntax `1.2.840.10008.1.2.4.70`), `pylibjpeg-openjpeg` (JPEG 2000), `numpy`. All are pip wheels; no system packages.

**Rendering one DICOM image to 8-bit grayscale:**
1. Decode `pixel_array`. Each frame of a multi-frame image becomes its own page.
2. Apply the modality LUT (`apply_modality_lut`).
3. Apply the VOI LUT or window (`apply_voi_lut`) when the file has one. Otherwise clip to the 0.5–99.5 percentile window.
4. Invert when `PhotometricInterpretation` is `MONOCHROME1`.
5. Scale to 0–255 `uint8`. Colour images (`RGB`, `YBR_*`) are converted to RGB and kept in colour.
6. Keep full resolution. Cap the long side at 4096 px, so the PDF stays reasonable for very large detectors.

**PDF assembly:**
- **Cover page** (A4, drawn with Pillow at 150 dpi, then saved as a PDF page):
  - title, patient, birth date, sex, patient ID
  - study date, description, modality, body part, institution, accession number
  - series list
  - notes
  - footer: "Images converted for reference. Not for diagnostic use."
- **Image pages:** one image per page. The page size follows the image aspect ratio at 150 dpi. The image is encoded as JPEG quality 92. A 1-line caption strip under the image gives series description / view, study date and `image n/N`.
- **Merge** with pypdf, as `ocr._merge` does today.
- **Size:** the sample disc (four images of about 2500×3000) gives a PDF of about 2–4 MB.
- **`page_count`** = 1 + number of image pages.

## 6. Worker pipeline

`process_document` gains a `disc_import` branch in `_extract_content`, used when the job payload has `disc_import_id`:

1. Render the images (§5) and write the bundle inside `disk_transaction`: the main PDF, then every selected report copied byte-for-byte as an attachment row.
2. **Content chunks:**
   - Report text through pypdf, chunked as for PDFs, with `attachment_id` set.
   - If `ocr_enabled` is on, scanned reports (`pdf_needs_ocr`) get OCR through `pdf_to_searchable_pdf`. The OCR'd copy is a derived file; the stored attachment stays original.
   - If OCR is off and the report has no text, record the warning `report_has_no_text` on the job; the preview had already warned before commit (§7).
   - The image PDF itself is not OCR'd, even with OCR on, because it has no text worth reading. The cover data reaches search through the metadata chunk.
3. **Summary** (`_ensure_summary`): for a document with `extra_metadata`, the input is the report text plus a plain-text rendering of `extra_metadata`, never vision on the X-ray images. With no report text, the summary uses metadata only.
4. **Metadata chunk** (`_ensure_metadata_chunk`): title, description when it is not the summary, notes, and a plain-text rendering of `extra_metadata`. Applies to all documents, so notes on ordinary documents are searchable too.
5. Language detection, embeddings and translation scheduling are unchanged. Translation is skipped because the import default is off.

`reprocess` on a bundle document keeps the bundle files and re-runs text, summary and metadata. It does not rebuild the PDF, because the staging files are gone.

## 7. Frontend

- **Entry point:** a new item "Import X-ray disc" in the BrowsePage upload menu. It opens the folder picker; the current folder is the default destination.
- **`DiscImportDialog`** (new component; pure logic in `lib/discImport.ts`):
  1. **Upload step:** file count, bytes and progress bar. On error: message and **Retry**.
  2. **Preview step**, one card per study:
     - an include checkbox
     - thumbnails with include checkboxes (unreadable ones greyed out)
     - report candidates with checkbox, filename and a "View" link
     - title, document date, notes, tags (patient tag prefilled), and the FolderPicker
     - the `ProcessingOptions` component with import defaults `ocr_enabled=false`, `translation_enabled=false`, `summary_enabled=true`
     - a warning row for each preview warning, e.g. a report without text: "This report has no text. Enable OCR to make it searchable."
     - an "Add files" button
  3. **Fallback view** (`no_study`): the manual pickers from §4.4. **Import** stays disabled until there is at least one image.
  4. **Commit:** toast with a link per created document. Cancel or close calls `DELETE /api/disc-imports/{id}`.
- **DocumentPage:**
  - When `attachments` is not empty: tabs **Images | Report** (one tab per attachment, labelled by role, numbered when there are more than one), a `?tab=` query parameter, and a download button per tab.
  - The `extra_metadata` key/value list sits in the side panel.
  - A `notes` textarea for every document, saved through PATCH.
- **Search and chat hits:** a hit on a chunk with `attachment_id` links to `?tab=<attachment_id>`.
- **Defaults:** the import defaults apply only in this dialog. The normal upload and batch upload defaults do not change.

## 8. Error handling

| Condition | Behaviour |
|---|---|
| Path escapes the staging dir (`..`, absolute) | 400 `invalid_path`; staging dir removed |
| Import id unknown, or another user's | 404 `not_found` |
| Staging dir older than 24 h at commit | 410 `import_expired`; dialog asks to start again |
| DICOM file fails to parse | skipped, listed as a warning |
| Every image of a selected study unreadable | Import disabled for that study, with the JPEG fallback offered (§4.4) |
| Render or bundle write fails in the worker | the job fails through existing retries; `disk_transaction` removes partial files; document `failed` with error shown on the page |
| Folder or bundle name clash | resolved with ` (n)` (§2.3) |

## 9. Testing

**Backend (pytest).** Fixtures are synthetic DICOM files generated in tests with pydicom: uncompressed, lossless JPEG, `MONOCHROME1`, multi-frame, no window tags, two studies, encapsulated PDF. Also a vendor `README.TXT`, a `Study000.htm`, and a one-page text PDF. No real patient data is used in the repo.
- **Parsing:** grouping and ordering, readability flag, report candidates and preselection, help PDF excluded, README and htm fallbacks, title code stripping, patient tag formatting.
- **Rendering:** VOI window, percentile fallback, inversion, colour passthrough, 4096 px cap, multi-frame pages.
- **Study PDF:** page count and the cover page text.
- **API:** stage, add files, thumbnails, commit, cancel, expiry, path traversal rejected, other user's import rejected.
- **Tree rules:**
  - bundle created on commit
  - rename renames the dir and the main file
  - move moves the dir
  - delete removes the dir
  - folder name clash with a bundle dir in both directions
  - `check-storage` with attachments
- **Pipeline:**
  - report chunks carry `attachment_id`
  - summary input is report text plus metadata, never vision
  - metadata chunk includes notes and `extra_metadata`
  - a PATCH of notes rebuilds the metadata chunk
  - OCR off skips the image PDF and the text report
- **Attachment endpoint:** inline, download and auth.

**Frontend (vitest):**
- the `discImport.ts` filter: keeps DICOM, extension-less files, README, htm and PDF; drops VIEWERS, COMMON, exe, dll and the JPEG copies
- dialog defaults
- the fallback view
- document page tabs and `?tab=`
- notes editing

**Manual:**
- the sample disc copy on the dev machine
- a real CD mount on Windows and on Linux through `origami.yabzec.uk`
- open, rename, move and delete the resulting bundle, then run `check-storage`

## 10. Risks

- **Unsupported compression:** rare transfer syntaxes (JPEG-LS, RLE without decoder) are covered only partly by the pylibjpeg plugins. Such files are reported as unreadable, and the JPEG fallback exists.
- **Vendor README formats** vary. The parsers are best effort and DICOM tags remain the primary source.
- **Large discs** (CT/MR with hundreds of slices): out of scope for the X-ray use case. The page cap is not enforced, but the preview shows the image count so the user can deselect series.
- **Sensitive data:** studies contain health data. Nothing new leaves the server, except the AI summary call that already exists for every document. Users who use a remote LLM should know the report text is sent to it, as with any document today.
