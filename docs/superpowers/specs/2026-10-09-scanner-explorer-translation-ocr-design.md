# Origami — Scanner, Explorer, Translation and OCR Improvements

**Date:** 2026-10-09
**Status:** Approved by user (brainstorming session), pending written-spec review

## 1. Overview

Ten follow-ups grouped in four areas:

**Scanner page**
1. The folder picker selects the folder and closes when you click a folder that has no subfolders.
2. Scanned pages can be reordered before saving.
3. Each processing stage can be switched off on its own: OCR (already possible), AI summary, translation.
4. You can create tags inline while scanning.

**Explorer (Browse page)**
5. Select several documents, then move or delete them together.
6. Filter documents by document date (from/to).
7. The main area shows subfolders as well as documents, like a file manager.

**Document page**
8. Re-translate a document without re-processing it.
9. Translation stays under the LLM provider's tokens-per-minute (TPM) limit, and a failed translation resumes instead of restarting.

**OCR**
10. The available OCR languages are discovered from the Tesseract installation on the server.

Decisions taken during brainstorming:
- The translation provider stays the configured LLM. It gets per-page segments, a token-budget throttle and 429 retries. Argos and DeepL were rejected.
- "Skip AI" is a group of separate checkboxes (OCR, summary, translation), not one switch.
- Language detection moves from the summary LLM call to a local library, so translation no longer depends on the summary.
- Tags: the missing feature is inline creation. AI tag suggestions are out of scope.
- Date filter: `document_date`, from/to range, inclusive.
- Browse root behaves like a filesystem root. A separate "All documents" view keeps today's flat list.
- Bulk actions apply to documents only, not folders.

## 2. Scanner page

### 2.1 Folder picker auto-select
`frontend/src/components/FolderPicker.tsx`:
- Click a folder **with no children**: call `onChange(id)` and close the popover.
- Click a folder **with children**: same as today. Select it and drill into its level.
- The Document page uses the same component, so it gets the same behaviour.

### 2.2 Page reorder
- Add `@dnd-kit/core` and `@dnd-kit/sortable` to the frontend.
- `components/scan/PageCarousel.tsx` becomes a horizontal sortable list. Keyboard sorting comes from the dnd-kit keyboard sensor. The selected page also gets ← and → buttons to move it one place, for touch use.
- On drop:
  1. Dispatch the existing `PAGES_REORDERED` action straight away (optimistic update).
  2. Call `POST /api/scan/sessions/{id}/reorder` with the new page id order. The backend endpoint already exists.
  3. If the call fails, restore the previous order and show the error in the wizard error area.
- After reorder, pages keep their ids. Only `page_number` changes. Thumbnails do not reload.

### 2.3 Processing options
New shared component `components/ProcessingOptions.tsx` with three checkboxes. All three are on by default.
- **OCR**, plus the OCR language select. This is the existing control, moved into the group.
- **AI summary**
- **Translation**

The component is used in the Scan sidebar, the Upload dialog, and the Document page re-process section.

**Backend**
- `Document` gets two columns: `summary_enabled: bool = True` and `translation_enabled: bool = True`. They come with an Alembic migration whose server default is `true`, so existing rows keep today's behaviour.
- These endpoints accept `summary_enabled` and `translation_enabled` (optional, default `true`) and store them on the document:
  - scan compile (`POST /api/scan/sessions/{id}/compile`)
  - upload (`POST /api/documents/upload`, as form fields)
  - reprocess (`POST /api/documents/{id}/reprocess`)
- Pipeline (`backend/app/worker/pipeline.py`):
  - `_ensure_summary` runs only when `doc.summary_enabled`.
  - `_ensure_metadata_chunk` keeps working when there is no summary: title, description and tags only.
  - `_schedule_translation` (through `_needs_translation`) does nothing when `not doc.translation_enabled`. In that case `translation_status` stays `None`.

**Language detection**
- Add `lingua-language-detector` to the backend dependencies.
- New `services/language.py` provides `detect_language(text) -> str | None`, which returns an ISO 639-1 code. It looks at the first ~5000 characters of the extracted content. It returns `None` when the text is empty or the confidence is low.
- The pipeline sets `doc.detected_language` from `detect_language` right after content extraction. This happens for every document, whatever `summary_enabled` is set to.
- The summary prompt and `llm.describe` stop returning a language. Their return value is just the summary text.
- `PRIMARY_LANGUAGE` comparisons stay as they are, because both values are ISO 639-1 codes.
- Images processed without OCR have no text, so `detected_language` stays `None` and no translation is scheduled. This matches today: those documents have no content chunks to translate.

### 2.4 Inline tags
New component `components/TagInput.tsx`. It replaces the tag checkbox lists in `ScanSidebar.tsx`, `UploadDialog.tsx` and `DocumentPage.tsx`.
- Selected tags show as coloured chips, each with a remove button.
- A text input filters a dropdown of the existing tags that are not selected yet. Arrow keys and Enter pick one.
- When the typed name matches no existing tag (case-insensitive), the dropdown offers "Create “name”". Enter or a click calls `POST /api/tags` with the next colour from the existing TagManager palette, then selects the new tag and invalidates the tags query.
- A 409 for a duplicate name (race) is handled by refetching the tags and selecting the existing tag.
- No backend change is needed.

## 3. Explorer (Browse page)

### 3.1 File-system browse
Views and URLs:

| URL | View |
|---|---|
| `/` | Root: top-level folders, then documents with no folder |
| `/?folder=<id>` | Folder: its direct subfolders, then its direct documents |
| `/?all=1` | All documents, flat list (today's "All" behaviour) |

- The sidebar `FolderTree` gets an "All documents" entry above the tree, linking to `/?all=1`. The "root" entry links to `/`.
- Root and folder views show:
  - **Breadcrumb**: `Root / A / B`, built with `folderPath` from `lib/folderTree.ts`. Every segment is a link.
  - **Folders section**: tiles for the direct children (`childrenOf`), showing the folder icon, the name and the document count. A single click opens the folder. Hidden when there are no children.
  - **Documents section**: the existing `DocumentCard` grid, showing direct documents only.
- Backend:
  - `GET /api/documents?folder_id=root` filters `folder_id IS NULL`.
  - `GET /api/folders` adds `document_count` to each folder (direct documents only, one grouped count query).
- Tag, type and date filters apply to the documents section only. Folder tiles always show.

### 3.2 Date filter
- Add From and To `<input type="date">` fields to the filter row, plus a clear button.
- The values go in the URL as `?from=YYYY-MM-DD&to=YYYY-MM-DD`.
- `GET /api/documents` accepts `date_from` and `date_to`. Both are optional and inclusive, and they apply to `document_date`. While either one is set, documents with a null `document_date` are excluded. If `date_from > date_to`, the endpoint returns 422.
- Search: `SearchFilters` (`backend/app/services/search.py`) gets `date_from` and `date_to` with the same meaning. The search endpoint and the search page filter row expose them.
- The tag and type filters also move from local `useState` into the URL (`?tag=`, `?type=`), so reload and back/forward keep every filter.

### 3.3 Multi-select and bulk actions
**Selection**
- Selection state lives in a small hook, `useSelection(visibleIds)`, with pure helpers in `lib/selection.ts`:
  - toggle one id
  - shift-click range over the visible order
  - select all visible
  - clear
- Each `DocumentCard` gets a checkbox. It shows on hover, and always while anything is selected.
- Clicking the card body still opens the document. Esc clears the selection.
- The selection clears when the folder, the view or any filter changes.

**Action bar**
- A sticky bar shows while the selection is not empty. It has "N selected", **Select all**, **Move…**, **Delete** and **Clear**.
- **Move…** opens `FolderPicker`, with root allowed. **Delete** opens a confirm dialog with the count.
- Folder tiles have no checkbox. Bulk actions apply to documents only.

**Backend** (`backend/app/api/documents.py`)
- `POST /api/documents/bulk/move` with body `{ids: [uuid], folder_id: uuid | null}`:
  - Returns 404 if the folder does not exist.
  - Updates all existing ids in one transaction.
  - Returns `{moved: n, missing: [ids]}`.
- `POST /api/documents/bulk/delete` with body `{ids: [uuid]}`:
  - Reuses the single-delete logic, refactored into one helper: cancel queued jobs, delete the rows, collect the file paths.
  - Runs all database work in one transaction, then removes the files after commit.
  - Returns `{deleted: n, missing: [ids]}`.
- Both endpoints accept at most 500 ids per request. More returns 422.

## 4. Document page: translation

### 4.1 Re-translate only
`POST /api/documents/{id}/retranslate`:
- Takes the same row lock as `reprocess_document`.
- Returns 409 with a clear message when:
  - the document is `pending` or `processing`
  - `translation_status == pending`
  - `detected_language` is null
  - `detected_language == PRIMARY_LANGUAGE`
- Otherwise:
  1. Delete the translation chunks and the `translation_segments` rows.
  2. Set `translation_enabled = true` and `translation_status = pending`.
  3. Queue `translate_document`.
  4. Commit and return the document.
- No OCR, summary or content re-embedding runs.

Frontend: a "Re-translate" button next to the translation status on the Document page. It is enabled under the same conditions. The existing polling of `translation_status` shows progress.

### 4.2 Translation by page
Source text:
- Page texts are rebuilt from the content chunks. New helper `page_texts_from_chunks(chunks) -> list[tuple[int | None, str]]` in `services/chunking.py`:
  1. Group the chunks by `page_number`, in `chunk_index` order.
  2. Remove the overlap: when a chunk starts with the last `overlap` characters of the previous chunk (whitespace-tolerant), drop that prefix and the paragraph separator.
  3. If the overlap does not match, keep the whole chunk. Some duplication is acceptable there; losing text is not.
- This works for existing documents too, so nothing new is stored at extraction time.

Segments:
- Each page is one segment.
- A page longer than the maximum segment size is split on paragraph boundaries (`\n\n`, with a hard split as fallback), with no overlap.
- Maximum segment size is `TRANSLATION_SEGMENT_CHARS` (default 6000). When `LLM_TPM_LIMIT` is set, it is also capped at about 40% of the limit in characters, about 3.5 characters per token. The input plus output of one call must fit in one minute's budget.

Resume:
- New table `translation_segments`:
  - `document_id` (FK, `ON DELETE CASCADE`)
  - `segment_index`
  - `page_number`
  - `source_hash` (sha256 of the source segment)
  - `text`
  - unique (`document_id`, `segment_index`)
- `translate_document` flow:
  1. Build the segments from the current content chunks and record their ids, as today.
  2. For each segment whose stored row does not have a matching `source_hash`, translate it and commit the row right away.
  3. After every segment is translated, take the row lock and check the content chunks are unchanged and `translation_status` is still `pending` (same check as today).
  4. Build the translation chunks with `chunk_pages(translated_pages)` and insert them.
  5. Delete the segments, commit, embed, and set the status to `done`.
- A job retry after a failure keeps the segments already committed.
- `reprocess_document` and `retranslate` delete the segments. Document delete removes them through the cascade.
- Side effect: the Translation text tab no longer shows duplicated overlap text.

### 4.3 Throttle and rate-limit retries
In `backend/app/services/llm.py`, for `translate`:
- New setting `LLM_TPM_LIMIT: int = 0`. The value 0 means no throttle.
- A module-level sliding window records `(timestamp, tokens)` for the last 60 seconds, guarded by a lock.
- Before each call:
  - Estimate the tokens as `token_counter(prompt) * 2`, counting input and roughly equal output. Fall back to `len(text) / 3.5` if the counter fails.
  - If `used + estimate > limit`, sleep until enough entries age out.
- After the call, replace the estimate with the actual `usage.total_tokens` when the response provides it.
- On `litellm.RateLimitError`:
  - Sleep for the `retry-after` header value when present, otherwise 60 s.
  - Retry the same segment, up to 5 times.
  - After that, re-raise. The normal job retry then resumes from the stored segments.
- The clock and sleep are injectable, for tests.
- Limit: the budget is per process. There is one worker process today. With more workers, each one has its own budget. This is documented in the README.

## 5. OCR language discovery

**Backend**
- `services/ocr.py` gets `available_languages() -> list[str]`:
  - It calls `pytesseract.get_languages(config="")` and drops `osd` and `equ`.
  - The result is cached for 5 minutes, so newly installed packages show up without a restart.
- New module `services/ocr_language_names.py`: a static map from Tesseract codes to English names. Unmapped codes show as the code.
- New endpoint `GET /api/ocr/languages` returns `{languages: [{code, name}], default: "ita+eng"}`. `default` is `DEFAULT_OCR_LANGUAGES` with uninstalled codes removed. If nothing is left, it is the first installed language.
- `validate_ocr_languages(value: str)` splits the value on `+` and checks every code against `available_languages()`. On failure it raises HTTP 422 `Unknown OCR language: <code>`. Every endpoint that accepts `ocr_languages` calls it:
  - upload
  - scan session create
  - scan compile
  - reprocess

**Frontend**
- Delete the hardcoded options in `lib/ocrLanguages.ts`. Keep only the join and split helpers, if they are still used.
- New `hooks/useOcrLanguages.ts` uses TanStack Query with a long `staleTime`.
- `components/OcrLanguageSelect.tsx` becomes a multi-select:
  - a button showing the current value (`ita+eng`)
  - a popover with one checkbox per installed language
- The order in which languages are checked is the order in the `+` string. The first one is Tesseract's primary language.
- At least one language must stay selected while OCR is on.
- The default comes from the server `default`.
- Used by `ScanToolbar`, `UploadDialog` and `DocumentPage`.
- README: replace the fixed `tesseract-ocr-ita/eng/deu` requirement with "install any `tesseract-ocr-<lang>` packages; they are discovered automatically".

## 6. Error handling summary

| Case | Behaviour |
|---|---|
| Reorder API fails | Order rolls back and the error shows in the scan wizard |
| Inline tag create conflicts | Refetch the tags and select the existing tag |
| Bulk move to a missing folder | 404, nothing changes |
| Bulk ids not found | Listed in `missing`, the others are processed |
| `date_from > date_to` | 422 |
| Retranslate in an invalid state | 409 with a reason |
| Provider 429 | Wait (`retry-after` or 60 s), up to 5 retries, then the job retry resumes from the segments |
| Unknown OCR language | 422 `Unknown OCR language: <code>` |
| No languages installed | `/api/ocr/languages` returns an empty list. The UI shows "No OCR languages installed" and OCR cannot be ticked |

## 7. Testing

**Backend (pytest, `backend/tests/`)**
- Processing flags:
  - `summary_enabled=false` skips the summary
  - `translation_enabled=false` schedules no translation
  - the metadata chunk is built without a summary
- `detect_language` returns the right code for Italian, English and German samples, and `None` for empty text.
- Browse:
  - `folder_id=root`
  - `date_from` and `date_to`, inclusive, with null dates excluded
  - 422 on an inverted range
  - `document_count` on folders
  - search date filters
- Bulk move and delete: success, missing ids, missing folder, the 500-id limit, and files removed after commit.
- Retranslate: each 409 case, and the success path queues a job and deletes the chunks and segments.
- `page_texts_from_chunks` gives back the original page text for chunks made by `chunk_pages`, including a hard-split paragraph.
- Translation resume:
  - a failure on segment 3 keeps segments 1–2, and the retry translates only segment 3 onward
  - a stale `source_hash` is translated again
- Throttle, with a fake clock and fake `token_counter`: it waits when over budget and does not wait when under budget. The 429 retry honours `retry-after` and re-raises after 5 tries.
- OCR languages: endpoint output with `get_languages` monkeypatched, the default filtered to installed languages, and a 422 on an unknown code for each endpoint.

**Frontend (vitest)**
- `FolderPicker` closes and selects on a folder with no children, and drills into a folder with children.
- Scan reorder: optimistic reducer update, the API call, and rollback on failure.
- `TagInput`: filter, select, create on Enter, and the duplicate fallback.
- `lib/selection.ts`: toggle, shift range, select all, and clear when the view changes.
- URL filter parsing for `folder`, `all`, `tag`, `type`, `from` and `to`.
- `OcrLanguageSelect` keeps the order of selection and needs at least one language.

## 8. Out of scope
- AI tag suggestions.
- Selecting, moving or deleting folders in bulk.
- Pagination of the document list.
- Throttling the summary and embedding calls. Only translation is throttled, but the throttle helper is written so these calls could use it later.
- A translation provider other than the configured LLM.

## 9. Implementation notes (changes made during implementation)

These decisions were taken while building and reviewing the feature. Where they differ from the sections above, this section describes the shipped behaviour.

- **Tag input keys (2.4):** Enter selects an exact name match, otherwise it creates the typed name. If the user moved the highlight with the arrow keys since the last keystroke, Enter picks the highlighted suggestion instead.
- **Page reorder (2.2):** a page is dragged by a separate handle on its thumbnail; clicking the thumbnail (or pressing Enter on it) still selects the page. Only one reorder is saved at a time, and a failed save is ignored if the scan session was discarded meanwhile.
- **OCR language order (5):** the order number next to a checked language is hidden from screen readers.
- **Processing options (2.3, 5):** once the installed languages are known, codes that are no longer installed are dropped from the form (falling back to the server default). With no language installed, OCR is switched off in the request. The server does not validate `ocr_languages` when OCR is off, and its own fallback default only uses installed languages.
- **Selection (3.3):** the card checkbox stays in the tab order and is always visible on small screens. Esc does not clear the selection when it is closing a dialog. Uploads also refresh the folder tile counts.
- **Re-translate (4.1):** also requires the document to be `ready` and to have extracted text; `translatable` has the same condition. A translation job for a document without extracted text clears the pending status.
- **Throttle (4.3):** when the token budget or a 429 needs a wait longer than 5 seconds, the translation job is re-queued at that time (saved segments are kept, no attempt is used), so the single worker keeps processing other documents. After a 429 the rejected request no longer counts against the budget. The saved-segment hash includes the target language.
- **Known limits:** a provider that keeps answering with long waits makes translation re-queue indefinitely (it never reaches `failed`). Search results compute `translatable` with one small query per foreign-language hit.
