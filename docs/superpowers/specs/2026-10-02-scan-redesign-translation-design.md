# Origami — Inline PDF, Scan Page Redesign, German OCR, Re-process, AI Translation

**Date:** 2026-10-02
**Status:** Approved by user (brainstorming session), pending written-spec review

## 1. Overview

Five requested changes plus two additions agreed during brainstorming:

1. **PDF preview** — PDFs render inline in the document page instead of downloading; add a separate Download button.
2. **Scan page redesign** — the page opens directly into a new scan session, with a toolbar / big preview / carousel / sidebar layout, and a leave-page guard.
3. **German OCR** — add `deu` as an OCR language.
4. **Re-process** — change OCR language (and OCR toggle) on an existing document and re-run OCR + AI processing.
5. **AI translation** — documents not in the primary language (Italian) get an AI translation, stored alongside the original, searchable, and shown via an Original / Italiano toggle.
6. **Editable document date** — a date that matches the original paper document, not the scan/upload date.
7. **Summaries in the primary language** — summaries are always written in Italian.

### Target language setting

A single setting `primary_language` (env `PRIMARY_LANGUAGE`, ISO 639-1, default `it`) drives both summaries and translations. A future profile page will replace this global lookup with a per-user value; all code reads the target language through one helper (`get_primary_language()`), so that future change touches only that helper.

## 2. Root cause: PDF downloads instead of previewing

`backend/app/api/files.py` returns `FileResponse(..., filename=...)`. Starlette then sets `Content-Disposition: attachment`, so the browser downloads the file instead of rendering it in the `<iframe>`.

**Fix:** `GET /api/documents/{id}/file` takes an optional query parameter `download: bool = False`.
- `download=false` (default) → `content_disposition_type="inline"` with the filename (the iframe renders the PDF).
- `download=true` → `content_disposition_type="attachment"`.

Frontend: `fileUrl(documentId, opts?: { download?: boolean })` appends `&download=1` when requested.

## 3. Data model (one Alembic migration)

- `documents.document_date DATE NOT NULL` — the date of the original paper document. Server default `CURRENT_DATE`; existing rows backfilled with `created_at::date`. New documents default to today when no date is given.
- `documents.detected_language TEXT NULL` — ISO 639-1 code returned by the AI (e.g. `de`), `NULL` until processed.
- `documents.translation_status TEXT NULL` — `NULL` (not needed / not yet processed), `done`, or `failed`.
- `ChunkSource.translation = "translation"` — new chunk source. `chunks.source` is a plain string column, so no DB enum change is needed.

`created_at` stays unchanged and keeps driving cleanup and default ordering.

## 4. OCR languages

- Supported options (shared frontend constant `OCR_LANGUAGES` in `frontend/src/lib/ocrLanguages.ts`, used by upload dialog, scan page, document page):
  - `ita+eng` Italian + English (default)
  - `ita` Italian
  - `eng` English
  - `deu` German
  - `ita+deu` Italian + German
- Host requirement: `tesseract-ocr-deu`. README install line and `tesseract --list-langs` check updated to include `deu`.

## 5. Pipeline: summary, language detection, translation

### Summary + language detection (one AI call)
- `llm.describe` returns a structured result `{summary: str, language: str}`. The prompt asks for JSON with the summary written in the primary language (Italian) and the ISO 639-1 code of the document's own language.
- Parsing is defensive: invalid JSON → use the raw text as summary and `language = NULL` (no translation attempted).
- Image vision path uses the same prompt/shape.
- `doc.summary` and `doc.detected_language` are set from the result.

### Translation
Runs after the summary step, inside `process_document`:
- Skip when `detected_language` is `NULL`, equals the primary language, or the document has no `content` chunks.
- Otherwise call `llm.translate(text, target_language)` once per content chunk (chunks are already size-bounded by `chunk_pages`), and store one `translation` chunk per content chunk with the same `page_number`. Translation chunks are embedded with the rest in `_embed_pending_chunks`, so search and RAG find them.
- Any exception during translation → delete partial translation chunks, set `translation_status = failed`, log, and continue; the document still ends `ready`. Success → `translation_status = done`.
- Idempotent on retry: skip when translation chunks already exist and `translation_status = done`.

### Prompts
`llm.translate` prompt: translate faithfully into the target language, preserve line breaks, numbers, names, and dates; output only the translation.

## 6. Re-process

`POST /api/documents/{id}/reprocess`, body `{ ocr_languages: str, ocr_enabled: bool = true }`.
- 409 `document_busy` when status is `pending` or `processing`.
- Updates `ocr_languages` / `ocr_enabled`; deletes `content`, `summary`, and `translation` chunks; clears `summary`, `detected_language`, `translation_status`; keeps the `metadata` chunk; sets status `pending`; enqueues `process_document` with `{document_id, force_ocr: true}`.
- `force_ocr` in `_extract_content`:
  - **pdf / scan** with `ocr_enabled` → `pdf_to_searchable_pdf(stored_pdf, languages)` (rasterize + OCR); replaces the stored PDF. Scan session images no longer exist after compile, so the stored PDF is the source.
  - **pdf / scan** with `ocr_enabled = false` → native text layer only (`extract_pdf_text`).
  - **image** → `ocr_image` again (or no text when OCR disabled).
  - **text / docx** → normal extraction (OCR not applicable); summary and translation still refresh.
- Returns the serialized document.

## 7. API changes summary

- `GET /api/documents/{id}/file?download=1` (§2).
- `GET /api/documents/{id}/text?variant=content|translation` — default `content`. Response also returns `detected_language` and `translation_status`.
- `PATCH /api/documents/{id}` accepts `document_date` (ISO date).
- `POST /api/documents/upload` accepts optional `document_date` form field.
- `POST /api/documents/{id}/reprocess` (§6).
- `POST /api/scan/sessions/{id}/pages` body accepts `device` (overrides session device; the chooser is editable at any time).
- `POST /api/scan/sessions/{id}/compile` body adds `description`, `document_date`, `ocr_languages`, `ocr_enabled`. Session values are the fallback when omitted.
- Document serialization includes `document_date`, `detected_language`, `translation_status`.

## 8. Scan page redesign

### Lifecycle
- On mount the page creates a session automatically (`POST /api/scan/sessions`). There is no setup phase.
- Reducer phases: `starting` → `ready` ⇄ `scanning` → `compiling` → `done`. Session-start failure shows the error with a Retry button.
- `done` shows a link to the new document and a "Scan another" button (creates a new session).
- Sidebar "Discard": asks for confirmation when pages exist, cancels the session, starts a new one.
- Every visit starts a fresh session; abandoned sessions are removed by the existing 24h sweep.

### Leave guard
Active when the session has ≥ 1 page and phase is not `done` (pure helper `shouldBlockLeave(state)`).
- Browser close / reload / external URL: `beforeunload` handler → the browser's native "Leave site?" dialog (browsers do not allow custom text).
- In-app navigation: react-router `useBlocker` → `window.confirm("You have unsaved scanned pages. Leave and discard them?")`. Confirm → `DELETE` session, then proceed.
- `useBlocker` requires a data router: `frontend/src/main.tsx` moves from `<BrowserRouter>` to `createBrowserRouter` + `<RouterProvider>` with the same routes and layout.

### Layout (desktop; stacks vertically on narrow screens)
```
┌──────────────────────────────────────────────┬──────────────┐
│     [● Scanner ok] [Scanner ▾] [OCR lang ▾] [☑ OCR]         │
├──────────────────────────────────────────────┤ Title        │
│                                              │ Description  │
│   BIG PREVIEW (selected page, last scanned,  │ Date         │
│   or fast preview)                           │ Folder       │
│                                              │ Tags         │
├──────────────────────────────────────────────┤──────────────│
│ [p1][p2][p3*][p4]   carousel (scroll-x)      │ [Preview]    │
│                                              │ [Scan next]  │
│                                              │ [Finish/Save]│
│                                              │ [Discard]    │
└──────────────────────────────────────────────┴──────────────┘
```
- **Toolbar (top right):** status pill (polls `/api/scan/status` every 10s); scanner chooser (0 devices → red "No scanner detected", 1 → name text, >1 → dropdown); OCR language select (hidden when OCR unchecked); Run OCR checkbox.
- **Big preview:** shows the selected page. A new scan auto-selects the new page. "Preview" (fast 75 dpi gray) renders here labelled "Preview — not saved" until the next selection or scan.
- **Carousel:** horizontal scroll of thumbnails; selected page highlighted; per-thumbnail move left/right and delete.
- **Sidebar:** title, description, date (default today), folder, tags; buttons Preview, Scan next page (label "Scan first page" when empty), Finish & save (disabled until title is set and ≥ 1 page), Discard. The compile dialog is removed.
- Preview / scan buttons disabled while the scanner is busy (`scanning` phase or preview in flight).

### Components
`ScanPage.tsx` keeps state and API wiring. New presentational components in `frontend/src/components/scan/`: `ScanToolbar`, `ScanPreview`, `PageCarousel`, `ScanSidebar`.

## 9. Document page

- **Preview tab:** inline PDF (fixed by §2). Header gets a **Download** button (`fileUrl(id, { download: true })`).
- **Text tab:** `Original | Italiano` toggle, shown only when `translation_status = done`. Shows a "Detected: DE" badge when `detected_language` is set. `translation_status = failed` → note "Translation failed — re-process to retry".
- **Sidebar:** Date input (`document_date`), saved with Save.
- **Sidebar "OCR" section:** language select + Run OCR checkbox + **Re-process** button. Confirm: "Re-run OCR and AI processing? Extracted text, summary and translation will be replaced." The existing 4s status polling tracks progress; the text query is invalidated when status returns to `ready`.
- Browse cards show `document_date` instead of `created_at`.

## 10. Upload dialog

Uses `OCR_LANGUAGES`. Adds an optional Date field (default today) sent as `document_date`.

## 11. Testing

**Backend** (real Postgres; only `app/services/llm.py` mocked, per project rule):
- File endpoint: `Content-Disposition` is `inline` by default and `attachment` with `download=1`.
- Pipeline: describe returns `it` → no translation chunks; returns `de` → one translation chunk per content chunk, embedded, `translation_status = done`; translate raises → document `ready`, `translation_status = failed`, no partial chunks; invalid JSON from describe → raw summary, no translation.
- Reprocess: 409 while `processing`; content/summary/translation chunks replaced, metadata kept; `force_ocr` takes the OCR path for pdf, scan, and image.
- Compile: description, date, languages, and OCR flag from the body are applied; page scan passes the request `device` to the backend.
- Migration: `document_date` backfilled from `created_at`.
- `PATCH` with `document_date`; upload with `document_date`.

**Frontend** (vitest + Testing Library):
- Scan reducer: new phases and transitions (`starting`, auto-select on `PAGE_SCANNED`, select page, reset to new session).
- `shouldBlockLeave` pure helper.
- `fileUrl` download parameter.
- Document text toggle visible only when the translation exists.

**Manual:** real scanner flow end to end; German document → German OCR, Italian summary, Italian translation; leave guard on reload and sidebar navigation.

## 12. Out of scope

- Profile page and per-user primary language (future; prepared by `get_primary_language()`).
- Translation into languages other than the primary language.
- Resuming an unsaved scan session after leaving the page.

## 13. Decisions log

| Decision | Choice | Why |
|---|---|---|
| PDF download cause | `FileResponse(filename=)` → `attachment` | Verified in `files.py`; fix with `inline` + `?download=1` |
| Document date | New `document_date` column, editable, default today | Match original paper date; keep `created_at` for cleanup/sort |
| Language detection | AI, in the summary call | Works for native PDF/DOCX and mixed `ita+eng`; no extra call |
| Translation storage | `translation` chunks, embedded | Italian search/RAG finds foreign documents |
| Translation failure | Non-fatal, `translation_status = failed` | Translation is secondary to the document |
| Summary language | Always primary language (Italian) | User choice; future per-user setting |
| Scan session on leave | Always fresh session + leave guard | User choice; guard prevents accidental loss |
| In-app leave guard | `createBrowserRouter` + `useBlocker` | `useBlocker` needs a data router |
| Re-process source for scans | Stored PDF, rasterized | Scan page images are deleted after compile |
| Language/OCR at scan | Sent at compile, editable until Finish | Toolbar is editable at any time |
