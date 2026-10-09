# Origami — Storage Tree, Batch Upload and Translation Language

**Date:** 2026-10-09
**Status:** Approved by user (brainstorming session), pending written-spec review

## 1. Overview

Three changes:

1. **Storage mirrors the explorer tree.** `STORAGE_PATH` holds the documents as real files at the paths the app shows: `<folder>/<subfolder>/<document title>.<ext>`. Today files are stored flat as `files/<uuid>.<ext>`.
2. **Batch upload.** Upload several files or a whole folder in one action. Each file gets its own title (the file name) and document date (the file's last-modified date). Folder, tags and processing options are shared by all files.
3. **Per-document translation language.** Each document has one translation target language, chosen from the installed OCR languages. Default OCR languages and the default translation language come from `.env`, for scan and for upload.

Decisions taken during brainstorming:

- The database stays the source of truth. The app writes to disk; changes made directly on disk are not picked up (no two-way sync).
- Real files at the tree paths (approach A). Symlink trees and periodic hardlink exports were rejected.
- `STORAGE_PATH` contains only the document tree. Derived files and temporary scan files live in sibling directories of `STORAGE_PATH`.
- Folder upload recreates the uploaded folder's subfolders inside the target folder.
- Batch document date = `File.lastModified`. Browsers do not expose file creation time. No date override in the batch dialog.
- One translation target per document (single-select), not several targets at once.
- Translation is skipped when the locally detected document language equals the target, even if translation is switched on. OCR languages are not used for this decision.
- Targets outside the 24 languages lingua detects are accepted; such documents are always translated (accepted detection gap).

## 2. Disk layout

```
/data/
  storage/                   STORAGE_PATH: explorer tree only
    Home/Bills/2025/Invoice ACME.pdf
    Home/Bills/2025/Invoice ACME (2).pdf
    Loose document.docx      document without a folder = tree root
  derived/                   DERIVED_PATH, default <STORAGE_PATH>/../derived
    <uuid>.ocr.pdf           searchable companion PDF of an OCR'd image
    <uuid>.preview.pdf       LibreOffice preview of an office document
  tmp/                       TMP_PATH, default <STORAGE_PATH>/../tmp
    scan_sessions/<id>/...
```

- New settings `derived_path: Path | None` and `tmp_path: Path | None`. Empty means the sibling of `STORAGE_PATH` with that name. Explicit values allow another disk.
- `Document.file_path` is relative to `STORAGE_PATH`, without a `files/` prefix (e.g. `Home/Bills/2025/Invoice ACME.pdf`).
- `Document.preview_path` is relative to `DERIVED_PATH` (e.g. `<uuid>.preview.pdf`).
- The image companion PDF goes to `DERIVED_PATH/<uuid>.ocr.pdf`. It stays untracked in the database and unserved, as today; it is deleted with the document.
- Derived files are keyed by document id and never move on rename.
- No hidden or app-specific files inside `STORAGE_PATH`.
- Download keeps returning `original_filename` as the download name.

## 3. Path service and storage operations

### 3.1 `app/services/tree_paths.py` (pure, no I/O except the uniqueness probe)

- `folder_rel_dir(session, folder_id) -> PurePosixPath`: path of the folder chain from the root (`Home/Bills/2025`); `None` folder gives the empty path.
- `safe_name(name) -> str`: replaces `/ \ : * ? " < > |` and control characters with `_`, strips leading/trailing dots and spaces, truncates to 200 UTF-8 bytes without splitting a character, maps an empty result to `Untitled`. Used for folder names and document titles on disk only; the app keeps the original names.
- `unique_name(dir_abs, stem, ext, taken) -> str`: returns `stem.ext`, or `stem (2).ext`, `stem (3).ext`, … when the name exists on disk or in `taken` (paths of other documents in the same directory from the database).
- `document_rel_path(session, doc, ext) -> str`: `folder_rel_dir / unique_name(safe_name(title), ext)`. A document's current own path never counts as taken.

### 3.2 `Storage` (`app/services/storage.py`)

`Storage` gets three roots: `root` (tree), `derived_dir`, `tmp_dir`. The uuid-named writers (`store_file`, `store_fileobj`, `store_preview`) are replaced by:

- `place_document(doc, data | fileobj, ext) -> (rel, size)`: writes the document file at its tree path. Writes `<name>.part` in the target directory first, then `os.replace`. Creates missing directories. An OCR'd PDF replacing the original keeps the same path. When the document already has a file at another path (the extension changed), the old file is removed after the replace.
- `move_document(old_rel, new_rel)`: `os.rename` inside `STORAGE_PATH`, creating the target directory. Missing source: log a warning and return (legacy damage); the database is still updated.
- `move_folder_dir(old_rel, new_rel)`: one `os.rename` of the directory. Missing source: create the target directory instead.
- `remove_folder_dir(rel)`: `rmdir` of an empty directory; ignores a missing directory.
- `store_derived(doc_id, suffix, data) -> str`: writes `DERIVED_PATH/<uuid><suffix>` (same `.part` + replace pattern) and returns the name relative to `DERIVED_PATH`.
- `abs_path(rel)` (tree) and `derived_abs(rel)` (derived) replace today's single `abs_path`.
- `delete_document_files(doc)`: unlinks the tree file, the preview and `<uuid>.ocr.pdf`.
- Scan session directories move under `tmp_dir/scan_sessions/`.

### 3.3 When paths change

All operations below lock the affected document rows with `SELECT … FOR UPDATE` before computing paths. The worker takes the same lock before it writes a document file, so a rename and a worker write never interleave.

| Trigger | Disk action | Database action |
|---|---|---|
| Upload | `place_document` | insert with `file_path` |
| Document title edit (`PATCH /api/documents/{id}`) | `move_document` to the new unique name | `file_path` |
| Document folder change (`PATCH`, `POST /bulk/move`) | `move_document` per document | `folder_id`, `file_path` |
| Folder create | `mkdir` | insert |
| Folder rename or move (`PATCH /api/folders/{id}`) | `move_folder_dir` once | one `UPDATE documents SET file_path = :new_prefix || substr(file_path, :n)` for all documents under the old prefix |
| Folder delete (only empty folders, as today) | `remove_folder_dir` | delete |
| Document delete, bulk delete | `delete_document_files` after commit | delete |
| Worker writes an OCR'd or compiled PDF | `place_document` under the row lock | `file_path`, `file_size` |

Sibling folders already have unique names in the database, so a folder rename or move never needs a suffix. Document titles may repeat; only the disk names differ.

### 3.4 Failure handling

- Order: lock rows → compute new paths → rename on disk → commit.
- Disk rename fails: roll back the transaction. Target exists (race): API 409 `storage_conflict`. Any other `OSError`: API 500 `storage_error`, message includes the path.
- Commit fails after the rename: rename back, then re-raise. If the rename back fails, log `CRITICAL` with both paths; `migrate-storage --check` reports the drift.
- Bulk move: each document is moved and recorded; on a failure, already-moved documents of this request are renamed back and the transaction rolls back (all or nothing, as today).
- Leftover `*.part` files (worker crash) are removed by the hourly `sweep_scan_sessions` job when older than one hour, and by `migrate-storage --check --fix`.

## 4. Batch upload

### 4.1 Frontend

- **BrowsePage:** the Upload button becomes a menu with **Files…** (`<input type="file" multiple>`) and **Folder…** (`<input type="file" webkitdirectory>`). Dropping files or folders on the Browse page also works (`DataTransferItem.webkitGetAsEntry`, recursive read).
- **One file selected:** today's `UploadDialog` (editable title and date), plus the translation target select from section 5.
- **Two or more files, or a folder:** new `BatchUploadDialog`:
  - File list: relative path and size. Unsupported extensions and hidden files (`.DS_Store`, `Thumbs.db`, names starting with `.`) are listed as skipped with the reason.
  - Shared fields: target folder (default: the current Browse folder), tags, `ProcessingOptions`.
  - No title and no date field.
- **Per-file values:**
  - Title: file name without the extension.
  - Document date: `File.lastModified` as a local calendar date.
  - Folder: the target folder plus the file's subpath from `webkitRelativePath` (or the drop entry path), without the file name. `Bills/2025/a.pdf` uploaded into `Home` lands in `Home/Bills/2025`.
- The extension list moves to one TS module shared by the dialog filter and tests; it matches `EXTENSION_MAP` on the backend.
- **Upload queue** (`lib/batchUpload.ts` + `useBatchUpload` hook):
  - Resolves every distinct subpath once through `ensure-path`, caching folder ids.
  - Sends files with the existing `POST /api/documents/upload`, three at a time.
  - Per-file state: queued, uploading (percent), done, failed (server message).
  - A totals bar and a **Retry failed** button. A failed file never stops the batch.
  - Closing the dialog during a batch asks for confirmation (`useLeaveGuard`).
  - The Browse list refreshes when the batch ends.

### 4.2 Backend

- New `POST /api/folders/ensure-path`, body `{"parent_id": int | null, "segments": ["Bills", "2025"]}`. Returns `{"folder_id": int}` for the leaf. Reuses a sibling with the same name, creates missing folders (and their directories), idempotent. Empty segment list returns `parent_id`. A concurrent create of the same sibling is handled by catching the unique-constraint error and re-reading.
- `POST /api/documents/upload` stays one file per request and gains `translation_language` (section 5).

## 5. Translation language and defaults

### 5.1 Configuration

- `DEFAULT_OCR_LANGUAGES` (existing): default for upload, batch upload and scan. The scan page stops forcing Italian (`scanProcessing()` uses the server default).
- `DEFAULT_TRANSLATION_LANGUAGE` (new, ISO 639-1): default translation target. Empty means `PRIMARY_LANGUAGE`.
- `PRIMARY_LANGUAGE` keeps one role: the language of AI summaries.

### 5.2 Target list

- New mapping in `app/services/ocr_language_names.py` from Tesseract codes to ISO 639-1 (`ita→it`, `eng→en`, `deu→de`, `chi_sim→zh`, `chi_tra→zh`, …).
- Translation targets = installed Tesseract languages that have an ISO 639-1 code, deduplicated, sorted by name. Codes without one (`osd`, `equ`, script packs such as `Latin`) are left out.
- `default_translation_language()`: the env default if it is in the list, else the first entry, with a logged warning (same pattern as `default_ocr_languages()`).
- `check_translation_language(value)`: 422 `unknown_translation_language` for a code not in the list.

### 5.3 API

- `GET /api/ocr/languages` adds `translation_languages: [{"code": "it", "name": "Italian"}]` and `translation_default`.
- `translation_language` is accepted (optional, default from env) on: upload, scan compile, re-process. Re-translate takes an optional body `{"translation_language": "en"}`; changing it re-translates to the new target.
- `GET /api/documents/{id}` and `/text` return the document's `translation_language` (today `/text` returns `PRIMARY_LANGUAGE`).

### 5.4 Model and pipeline

- New column `documents.translation_language` (`varchar(8)`, not null). The Alembic migration backfills existing rows with the current `PRIMARY_LANGUAGE`, so no existing translation looks stale.
- Every use of `get_primary_language()` in the translation path reads `doc.translation_language` instead: `_needs_translation`, `_translate_segments`, `translate_document`, `retranslate_document`, `/text`. Summaries keep `get_primary_language()`.
- Skip rule: `detected_language == doc.translation_language` → no translation job, even with translation on. Re-translate returns 409 `nothing_to_translate` in that case.
- The segment hash already includes the target, so a target change invalidates saved segments.

### 5.5 UI

- `ProcessingOptions` shows a single-select **Translate to** under the Translation checkbox, only when Translation is on. Default from `translation_default`.
- The document page shows "Translated to <language>" and a target select in the re-translate section.

## 6. Migration and rollout

1. Alembic migration: add and backfill `translation_language`. No file changes.
2. Deploy the code. At startup the API and the worker check for the old layout (`STORAGE_PATH/files/` containing uuid-named files). If found, they refuse to start and log: `Old storage layout found; run: python -m app.cli migrate-storage`.
3. `python -m app.cli migrate-storage [--dry-run] [--check] [--fix]`:
   - Moves each `files/<uuid>.<ext>` document file to its tree path and commits per document (resumable, idempotent; a rerun is a no-op).
   - Moves `files/<uuid>.preview.pdf` to `DERIVED_PATH` and rewrites `preview_path`.
   - Moves image companions `files/<uuid>.pdf` (where the document's own file is not that PDF) to `DERIVED_PATH/<uuid>.ocr.pdf`.
   - Moves `STORAGE_PATH/tmp/` to `TMP_PATH`.
   - Removes the empty `files/` and `tmp/` directories.
   - `--dry-run` prints the planned moves. `--check` reports database/disk drift (missing files, files without a document, `*.part` leftovers); `--fix` also removes `*.part` leftovers.
4. README and `.env.example`: new layout, `DERIVED_PATH`, `TMP_PATH`, `DEFAULT_TRANSLATION_LANGUAGE`, batch and folder upload, the migration command.

## 7. Testing

Backend (real Postgres, existing style, no mocks of the filesystem — `tmp_path` roots):

- `test_tree_paths.py`: `safe_name` (reserved characters, dots, length, empty), `unique_name` suffixes, folder chain paths.
- `test_storage.py`: place, move, folder directory move, derived and tmp siblings, `.part` + replace, delete of all document files.
- `test_documents.py`, `test_bulk.py`, `test_folders.py`: title edit, folder move, bulk move, folder rename and move all move files on disk; rollback on a simulated rename failure; rename back on a simulated commit failure; empty folder delete removes the directory.
- `test_folders.py`: `ensure-path` create, reuse, idempotency, empty segments.
- `test_uploads.py`: tree path on upload, collision suffix, `translation_language` validation and default.
- `test_pipeline.py`, `test_retranslate.py`: target read from the document, skip when detected language equals the target, target change re-translates, worker writes under the lock.
- `test_ocr_languages.py`: translation list mapping, dedup, env default fallback.
- `test_cli.py`: `migrate-storage` dry run, run, rerun no-op, `--check` drift report, startup refusal on the old layout.
- `test_migrations.py`: backfill of `translation_language`.

Frontend (vitest):

- Batch file filtering (extensions, hidden files), title/date/subpath derivation.
- `ensure-path` caching, queue concurrency of three, per-file states, retry of failed files.
- `scanProcessing()` uses the server OCR default.
- `ProcessingOptions` translation target select: visible only with Translation on, default from the server.
