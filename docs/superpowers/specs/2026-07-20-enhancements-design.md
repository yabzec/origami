# Origami — Enhancements: External Access, No-OCR, Scanner UX, AI Pipeline

**Date:** 2026-07-20
**Status:** Approved by user (brainstorming session)

## 1. Overview

The six requested enhancements, consolidated into five work areas (the OCR toggle spans both upload and scan). Several are already partly built — the spec is explicit about the *actual delta*, not a rebuild.

1. **External connectivity** — make the app usable from another machine (LAN IP + cloudflare tunnel) in dev.
2. **No-OCR toggle** — let uploads/scans skip the Tesseract pipeline.
3. **Scanner UX** — device discovery + a fast throwaway preview, bolted onto the existing scan wizard.
4. **AI token optimization** — close the one gap where an image is sent to the vision model even when OCR text exists.
5. **Multi-provider AI config** — generalize the (already LiteLLM-based) integration to any provider via explicit env keys.

### What is already implemented (verified, not re-done)

- The pipeline already extracts native text from PDFs (pypdf, OCR only when the text layer is poor via `pdf_needs_ocr`), DOCX (python-docx), and text files, and feeds **only text** to summarization, embeddings, and RAG. The sole exception is the image summary path (see §5).
- `app/services/llm.py` already uses **LiteLLM**. Provider switching (Groq/OpenAI/Gemini/Ollama) already works by changing the model string; §6 only generalizes the *config surface*, it is not a refactor.

## 2. External connectivity (#1)

The dev flow is Vite dev server accessed by LAN IP and via a cloudflare tunnel. With the existing `/api` proxy the browser is **same-origin** with the Vite/tunnel origin (Vite forwards to the backend server-side), so CORS is not the real blocker — Vite's host allow-list is (it returns "Blocked request. This host is not allowed" for unknown hosts, which the user already hit and patched by hand).

- **`frontend/vite.config.ts`** (dev only): `server.host = true` (bind 0.0.0.0), `server.allowedHosts = true` (accept any Host — LAN IP, `*.trycloudflare.com`, custom tunnel domains — with no hardcoded host, superseding the user's manual per-host edit), keep the existing `/api` proxy to `http://localhost:8000`.
- **API client** (`frontend/src/lib/api.ts`): unchanged — already uses **relative** `/api/...` paths. Relative *is* the "dynamic base URL": it targets whichever origin served the page (IP, tunnel, or prod FastAPI). No hardcoded host anywhere (satisfies the no-hardcoded-IP constraint).
- **Backend CORS** (`backend/app/main.py`): add Starlette `CORSMiddleware`. Belt-and-suspenders for the same-origin-proxy path and a genuine fix for any future cross-origin split (built SPA served separately + API called directly). Allowed origins from a new env `CORS_ORIGINS` (comma-separated; default `*`). `allow_credentials=False` (auth is a Bearer header, not cookies), so `*` is legal and safe for this single-user app. `allow_methods=["*"]`, `allow_headers=["*"]`.

If a specific upload error survives these, capture the browser network-tab error and chase the real cause — but this covers the whole external-access failure class.

## 3. No-OCR toggle (#2)

### Data model
- Add `documents.ocr_enabled BOOLEAN NOT NULL DEFAULT true` and `scan_sessions.ocr_enabled BOOLEAN NOT NULL DEFAULT true` (one Alembic migration; existing rows default true). A new explicit column, **not** an overloaded sentinel in `ocr_languages`.

### Pipeline behavior when `ocr_enabled = False`
- **image** → skip Tesseract entirely; no content text, no companion searchable PDF; summary via vision (`describe(image_path=...)`) — the photo path.
- **pdf** → use **only** the native text layer (`extract_pdf_text`); never fall back to `pdf_to_searchable_pdf`, even if the layer is empty. Empty layer → document stored with only the metadata chunk (no summary).
- **scan** → compile pages into an **image-only** PDF via a new `ocr.images_to_pdf(image_paths) -> bytes` helper (embeds the page images, no OCR text layer); no content chunks; no summary (metadata chunk only).
- **text / docx** → unaffected (OCR never applied to these).

In all cases the document is still stored, embedded, and searchable on whatever text exists — at minimum the metadata chunk (title + description).

### API
- `POST /api/documents/upload`: accept an `ocr_enabled` form field (default `true`); pass to `create_pending_document`.
- `POST /api/scan/sessions`: accept `ocr_enabled` in the body (default `true`), store on the session; `compile` copies it onto the created document.

### Frontend
- `UploadDialog` and `ScanPage`: a "Run OCR" checkbox (default checked). When unchecked, the OCR-language selector is hidden/disabled and the request sends `ocr_enabled=false`.

## 4. Scanner UX (#3 discovery + #4 preview)

### Device discovery (#3)
- `ScanimageBackend.list_devices() -> list[dict]` runs `scanimage -L` (10s timeout) and parses each `device '<id>' is a <name>` line into `{"id": <id>, "name": <name>}`. Pure parser split out as `parse_scanimage_devices(output: str) -> list[dict]`, unit-tested against real sample output. `FakeScannerBackend.list_devices()` returns a scripted list.
- `GET /api/scan/devices` (JWT) → `{"devices": [{"id","name"}...], "default": <first id | null>}`.
- `ScanPage` fetches devices on mount (query key `["scan-devices"]`): **0** → "no scanner detected"; **1** → show the name before Start; **>1** → a dropdown. The chosen id is sent when creating the session.

### Device threading
- Add `scan_sessions.device TEXT NULL` (null = system default) to the same migration as §3's `ocr_enabled`.
- `ScanimageBackend.scan(dpi, mode, device=None)` and `scan_locked(backend, dpi, mode, device=None)` gain an optional `device`; when set, append `-d <id>` to the `scanimage` argv. `FakeScannerBackend.scan` accepts and ignores it.
- `POST /api/scan/sessions` accepts `device` in the body and stores it; `POST /api/scan/sessions/{id}/pages` reads the session's device and passes it to `scan_locked`.

### Fast preview (#4)
- `POST /api/scan/preview` (JWT, body `{device?}`) → runs `scanimage` with **fast params `--resolution=75 --mode=Gray`** (vs full `300`/`Color`; ~16× fewer pixels + grayscale, strictly faster — satisfies the fast-preview constraint), 30s timeout, held under the same `_scan_lock` (409 `scanner_busy` if a scan/preview is in progress). Returns the PNG bytes directly as a `Response(media_type="image/png")`; **not** saved as a session page. New backend method `ScanimageBackend.preview(device=None) -> bytes` (fast params baked in) so preview params live in one place; `FakeScannerBackend.preview` returns a generated PNG.
- `ScanPage`: a "Preview" button available in the wizard's ready state → fetches the preview as a blob and shows it in a throwaway panel (with a "clear" affordance). Kept in **component state**, never the `scanWizardReducer` — the tested scan state machine is untouched.

## 5. AI token optimization (#5)

Everything except the image summary path already sends only extracted text. The delta:

- `_ensure_summary` for **images**: read the image's `source=content` chunks (produced by OCR when `ocr_enabled`). If their combined text is **≥ 40 characters**, summarize from that text via the text model (`describe(text=...)`) — the cheap path, same as PDFs. Otherwise (a photo with little/no text, or `ocr_enabled=False` so no content chunks exist) fall back to `describe(image_path=...)` (vision, file sent).
- Add a one-line invariant comment at the `describe(image_path=...)` call site: files reach the LLM only when no usable text was extracted.

This makes the rule explicit and enforced: raw text (native or OCR) for summary/RAG whenever available; the file only for genuine no-text images.

## 6. Multi-provider AI config (#6)

LiteLLM is already the provider abstraction. This generalizes the config surface only.

### New env (`backend/app/config.py` + `.env.example`)
- `LLM_API_KEY` (default `""`) and `LLM_API_BASE` (default `""`, for Ollama / OpenAI-compatible endpoints).
- `EMBEDDING_API_KEY` and `EMBEDDING_API_BASE` (default `""`; fall back to the `LLM_*` values when unset — because e.g. Groq has no embeddings, so chat and embeddings often use two different providers).
- `LLM_MODEL` / `VISION_MODEL` / `EMBEDDING_MODEL` stay as LiteLLM model strings — that string *is* the provider selector: `groq/llama-3.3-70b-versatile`, `openai/gpt-4o-mini`, `gemini/gemini-2.5-flash`, `ollama/llava`.
- `GEMINI_API_KEY` retained for backward compatibility (LiteLLM still reads it), but `.env.example` documents `LLM_API_KEY` as the generic key.

### `llm.py`
- `embed()` passes `api_key` / `api_base` resolved as `embedding_api_key or llm_api_key or None` and `embedding_api_base or llm_api_base or None`.
- `describe()` / `complete()` pass `api_key=llm_api_key or None`, `api_base=llm_api_base or None`.
- Passing `None` when unset preserves LiteLLM's own provider-env fallback, so existing `GEMINI_API_KEY`-based setups keep working unchanged (drop-in, per the constraint).

### `.env.example`
- A documented block with a commented example config per provider (Groq, OpenAI, Gemini, Ollama) showing the model strings + which key/base to set.

## 7. Testing

- **Backend** (real Postgres; only `app/services/llm.py` mocked, per project rule):
  - No-OCR branches: image (no content chunk, vision summary), pdf (native-only, no OCR fallback), scan (image-only PDF, no content), verifying no OCR is invoked.
  - `parse_scanimage_devices` — pure, against real `scanimage -L` sample strings (0/1/many, and a malformed line ignored).
  - `GET /api/scan/devices`, `POST /api/scan/preview` via `FakeScannerBackend` (preview returns image bytes, not a persisted page; busy-lock → 409).
  - Image summary: OCR text ≥ 40 chars → text model (no vision call); < 40 chars → vision call. Asserted via the `llm` mock.
  - `llm.py` passes `api_key`/`api_base` to litellm (monkeypatch litellm, assert kwargs), including the embedding fallback-to-LLM-key behavior.
  - CORS: a request with an `Origin` header gets the `access-control-allow-origin` response header.
- **Frontend** (vitest + Testing Library; `fetch` mocked):
  - No-OCR toggle in `UploadDialog` and `ScanPage` (sends `ocr_enabled=false`, hides the language selector).
  - Device-selection logic (pure): 0 → message, 1 → name, >1 → dropdown.
  - Preview button flow (mock fetch → blob → image shown, then cleared).
- **Not unit-tested**: `vite.config.ts` (config; human-verified by external access).

## 8. Sequencing

One spec; the implementation plan runs in this order so the blocking item ships first:

1. **§2 connectivity** (Vite host/allowedHosts + CORS) — unblocks external use immediately.
2. **§3 no-OCR** (migration + pipeline + toggles).
3. **§4 scanner** (device discovery + threading + preview).
4. **§5–6 AI pipeline** (image-summary text path + provider config).

The plan may be split into separate per-area plans if incremental shipping is preferred — decided at writing-plans time.

## 9. Decisions log

| Decision | Choice | Why |
|---|---|---|
| #1 root cause | Vite `allowedHosts`/`host`, not CORS | Same-origin via `/api` proxy; user already hit the host block |
| #1 base URL | Keep relative `/api` | No hardcoded host; targets page origin |
| #1 CORS | Add anyway, `CORS_ORIGINS` env, default `*`, no credentials | Honors ask, safe for Bearer-auth single-user, future-proofs cross-origin |
| No-OCR signal | New `ocr_enabled` boolean column | Explicit, no sentinel parsing |
| No-OCR scan | Image-only PDF (`images_to_pdf`) | Preserve the visual document without a text layer |
| Preview params | `--resolution=75 --mode=Gray`, 30s timeout | Strictly faster than 300/Color |
| Preview state | Component-local, not the reducer | Don't touch the tested scan state machine |
| Image summary threshold | OCR text ≥ 40 chars → text model, else vision | User-chosen; "text if available, file only for photos" |
| Provider config | Generalize env keys, pass to litellm explicitly; separate embedding key/base with fallback | LiteLLM already abstracts providers; Groq lacks embeddings |
| Backward compat | Keep `GEMINI_API_KEY`; `None` when unset | Drop-in, existing setups unaffected |
