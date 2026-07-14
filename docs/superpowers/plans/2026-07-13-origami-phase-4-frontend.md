# Origami Phase 4 — React Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working web UI for Origami — login, browse/organize documents, upload, scan wizard, hybrid search, and RAG chat — served by the existing FastAPI backend in production.

**Architecture:** A Vite + React + TypeScript SPA in `frontend/` (strictly separated from `backend/`). TanStack Query owns all server state (with polling while documents process); react-router v7 owns navigation; a tiny `lib/api.ts` fetch wrapper owns auth headers and the `{"error": {...}}` envelope. All non-trivial logic lives in pure, unit-tested modules (`folderTree`, `scanWizard` reducer, `sse` parser, `snippets`, `citations`); components stay thin. Task 1 first adds three small backend endpoints the UI needs (file serving with token-query auth, extracted text, SSE error event); Task 9 mounts the built SPA into FastAPI.

**Tech Stack:** Vite 6, React 19, TypeScript, Tailwind CSS v4 (`@tailwindcss/vite`), TanStack Query v5, react-router v7, vitest + Testing Library (jsdom). Node 24 / npm 11 (verified on host).

**Spec:** `docs/superpowers/specs/2026-07-12-origami-dms-design.md` §7 (Frontend), §2 (single exposed port in prod).

## Global Constraints

- **Strict directory separation:** all frontend code under `frontend/`; the only backend files touched are those named in Tasks 1 and 9.
- Backend rules still bind for Tasks 1 and 9: real Postgres in tests, error envelope `{"error": {code, message, detail}}`, `app/services/llm.py` as the only backend mock boundary, pristine pytest output, Conventional Commits.
- **Frontend test convention:** vitest + Testing Library, files colocated as `src/**/*.test.ts(x)`, run with `npm test` (= `vitest run`) from `frontend/`. Mocking `fetch` in frontend tests is allowed and expected — the backend "never mock" rule applies to backend tests only. Frontend test output must also be pristine (no unhandled-rejection noise, no act() warnings).
- **Sanctioned deviation from spec §7:** UI primitives are hand-written shadcn-style components in `src/components/ui/` (same file layout and import paths the shadcn CLI would generate) instead of CLI-generated ones — the CLI is interactive and registry-dependent, unusable headlessly. They can be replaced by real shadcn components later without changing imports.
- **No `dangerouslySetInnerHTML` anywhere.** Search snippets contain `<b>` markers from `ts_headline`; they are rendered via the pure `splitHighlights()` parser into React elements.
- Auth: JWT in `localStorage` (`origami_token`), attached as `Authorization: Bearer` by the api client. Browser-native resource loads (`<iframe>`, `<video>`, `<img>` for document files) can't send headers → the Task 1 file endpoint also accepts `?token=`. Scan-page thumbnails are fetched as blobs with headers instead (small PNGs).
- API surface consumed (from Phases 1–3, all under JWT): `POST /api/auth/login` `{username,password}`→`{access_token}`; `GET /api/auth/me`; folders/tags CRUD; `GET /api/documents?folder_id&tag_id&doc_type&status` (+`GET/PATCH/DELETE /{id}`, PATCH body `{title?,description?,folder_id?,tag_ids?}`); `POST /api/documents/upload` (multipart: `file`, `title?`, `folder_id?`, `tag_ids?` comma-separated, `ocr_languages?`); scan API (`GET /api/scan/status`, `POST /api/scan/sessions`, `POST /api/scan/sessions/{id}/pages`, `GET /api/scan/pages/{id}/preview`, `DELETE /api/scan/pages/{id}`, `POST /api/scan/sessions/{id}/reorder`, `DELETE /api/scan/sessions/{id}`, `POST /api/scan/sessions/{id}/compile`); `POST /api/search` `{query,mode,filters:{folder_id,tag_ids,doc_type},limit}`; `POST /api/chat` `{question}` (SSE: `meta`/`delta`/`done`, + `error` added in Task 1).
- Scanner error codes surfaced to users with actionable copy: `scanner_offline`, `scanner_busy`, `scanner_jam`, `cover_open`, `scanner_timeout`.
- Git hygiene: stage specific files, never `git add -A`/`.` (untracked `graphify-out/` must never be committed; `frontend/node_modules` and `frontend/dist` get gitignored in Task 2).
- Backend tests: `cd backend && uv run pytest ...` (fallback `.venv/bin/python -m pytest`). Frontend: `cd frontend && npm test`.

---

### Task 1: Backend support endpoints

**Files:**
- Modify: `backend/app/api/deps.py` (add `get_current_user_flexible`)
- Create: `backend/app/api/files.py` (file-serving router)
- Modify: `backend/app/api/documents.py` (add `/text` endpoint)
- Modify: `backend/app/api/chat.py` (SSE `error` event)
- Modify: `backend/app/main.py` (include files router)
- Test: `backend/tests/test_files_api.py`, extend `backend/tests/test_documents.py`, extend `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `decode_token`/`User`/`api_error`/`bearer` (deps.py), `get_doc_or_404`/`serialize` (documents.py), `Storage`/`get_storage`, `Chunk`/`ChunkSource`, `rag.stream_answer`.
- Produces:
  - `app.api.deps.get_current_user_flexible` — like `get_current_user` but also accepts `?token=<jwt>` query param when the Authorization header is absent (needed for `<iframe>/<video>/<img>` loads). Header wins if both present.
  - `GET /api/documents/{id}/file` → `FileResponse` of the stored file, media type guessed from the extension, `Content-Disposition` filename from `original_filename`; 404 `no_file` if the document has no stored file yet; auth via `get_current_user_flexible`.
  - `GET /api/documents/{id}/text` → `{"summary": str | null, "chunks": [{"chunk_index", "page_number", "content"}]}` — content-source chunks ordered by `chunk_index` (normal JWT header auth).
  - Chat SSE: any exception mid-stream now yields a final `data: {"type": "error", "code": "chat_failed", "message": ...}` event instead of silently truncating; a comment documents that `stream_answer` payloads must never contain a `type` key.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_files_api.py`:

```python
from app.services.auth import create_access_token
from tests.helpers import seed_document


def stored_doc(session, storage, data=b"%PDF-1.7 x", ext=".pdf"):
    doc = seed_document(session, "Doc", [{"content": "c"}])
    rel, size = storage.store_file(doc.id, ext, data)
    doc.file_path = rel
    session.commit()
    return doc


def test_file_served_with_bearer_header(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.7 x"


def test_file_served_with_token_query_param(client, session, storage, user):
    doc = stored_doc(session, storage, data=b"vid", ext=".mp4")
    token = create_access_token(user.id)
    resp = client.get(f"/api/documents/{doc.id}/file?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"


def test_file_requires_auth(client, session, storage):
    doc = stored_doc(session, storage)
    assert client.get(f"/api/documents/{doc.id}/file").status_code == 401
    assert client.get(f"/api/documents/{doc.id}/file?token=garbage").status_code == 401


def test_file_404_when_no_file(auth_client, session, storage):
    doc = seed_document(session, "NoFile", [{"content": "c"}])
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "no_file"
```

Append to `backend/tests/test_documents.py`:

```python
def test_document_text_endpoint(auth_client, session):
    from app.models import Chunk, ChunkSource
    from tests.helpers import seed_document

    doc = seed_document(
        session, "Testo",
        [
            {"content": "Pagina uno.", "page_number": 1},
            {"content": "Pagina due.", "page_number": 2},
            {"content": "Riassunto.", "source": ChunkSource.summary},
        ],
    )
    doc.summary = "Riassunto."
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}/text").json()
    assert body["summary"] == "Riassunto."
    assert [c["content"] for c in body["chunks"]] == ["Pagina uno.", "Pagina due."]
    assert body["chunks"][0]["page_number"] == 1
```

Append to `backend/tests/test_chat_api.py`:

```python
def test_chat_emits_error_event_on_failure(auth_client, monkeypatch):
    from app.services import rag

    def boom(texts):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(rag, "llm_embed", boom)
    resp = auth_client.post("/api/chat", json={"question": "ciao"})
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "chat_failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_files_api.py tests/test_documents.py::test_document_text_endpoint tests/test_chat_api.py::test_chat_emits_error_event_on_failure -v`
Expected: FAIL (404s / missing routes; chat test fails because the exception propagates instead of yielding an error event)

- [ ] **Step 3: Implement**

Add to `backend/app/api/deps.py`:

```python
def get_current_user_flexible(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    token: str | None = None,
    session: Session = Depends(get_session),
) -> User:
    """Auth via Bearer header OR ?token= query param (browser-native resource loads)."""
    raw = creds.credentials if creds is not None else token
    if raw is None:
        raise api_error(401, "unauthorized", "Missing bearer token")
    try:
        payload = decode_token(raw)
    except pyjwt.InvalidTokenError:
        raise api_error(401, "unauthorized", "Invalid or expired token")
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise api_error(401, "unauthorized", "Unknown user")
    return user
```

`backend/app/api/files.py`:

```python
import mimetypes
import uuid

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


@router.get("/{document_id}/file")
def document_file(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> FileResponse:
    doc = get_doc_or_404(session, document_id)
    if not doc.file_path:
        raise api_error(404, "no_file", "Document has no stored file")
    path = storage.abs_path(doc.file_path)
    if not path.is_file():
        raise api_error(404, "no_file", "Stored file is missing on disk")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=doc.original_filename or path.name)
```

Add to `backend/app/api/documents.py` (after `get_document`):

```python
@router.get("/{document_id}/text")
def document_text(document_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    doc = get_doc_or_404(session, document_id)
    chunks = session.exec(
        select(Chunk)
        .where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
        .order_by(Chunk.chunk_index)
    ).all()
    return {
        "summary": doc.summary,
        "chunks": [
            {"chunk_index": c.chunk_index, "page_number": c.page_number, "content": c.content}
            for c in chunks
        ],
    }
```

(add `Chunk, ChunkSource` to the imports from `app.models`.)

Replace `event_stream` in `backend/app/api/chat.py`:

```python
import logging

log = logging.getLogger("origami.chat")


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    def event_stream():
        # NOTE: stream_answer payload dicts must never contain a "type" key —
        # it would be clobbered by the event type merged in here.
        try:
            for event_type, payload in stream_answer(session, body.question):
                yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"
        except Exception:
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'code': 'chat_failed', 'message': 'Answer generation failed'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

In `backend/app/main.py`, add `files` to the router imports and `app.include_router(files.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_files_api.py tests/test_documents.py tests/test_chat_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Run full backend suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/api/deps.py backend/app/api/files.py backend/app/api/documents.py backend/app/api/chat.py backend/app/main.py backend/tests/test_files_api.py backend/tests/test_documents.py backend/tests/test_chat_api.py
git commit -m "feat: file serving with token auth, text endpoint, chat SSE error event"
```

---

### Task 2: Frontend scaffold — Vite, Tailwind, api client, auth, router

**Files:**
- Create: `frontend/` via Vite template, then: `frontend/vite.config.ts`, `frontend/src/index.css`, `frontend/src/test-setup.ts`, `frontend/src/lib/utils.ts`, `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/auth.tsx`, `frontend/src/components/ui/button.tsx`, `input.tsx`, `label.tsx`, `textarea.tsx`, `select.tsx`, `badge.tsx`, `dialog.tsx`, `frontend/src/pages/LoginPage.tsx`, `frontend/src/App.tsx`, `frontend/src/main.tsx`
- Modify: `.gitignore` (frontend entries), `frontend/tsconfig.app.json` (path alias)
- Test: `frontend/src/lib/api.test.ts`, `frontend/src/pages/LoginPage.test.tsx`

**Interfaces:**
- Produces (everything later tasks import):
  - `lib/api.ts`: `getToken()/setToken(t)/clearToken()`, `class ApiError extends Error {status, code, detail}`, `api.get/post/patch/del/postForm`, `fileUrl(documentId: string): string` (appends `?token=`).
  - `lib/types.ts`: `Document`, `Folder`, `Tag`, `DocumentText`, `ScanStatus`, `ScanPageInfo`, `SearchResult`, `SearchResponse`, `ChatEvent`, `ChatSource`.
  - `lib/utils.ts`: `cn(...classes)` class joiner.
  - `auth.tsx`: `AuthProvider`, `useAuth() -> {user, loading, login(u,p), logout()}`, `RequireAuth` route wrapper (redirects to `/login`).
  - UI primitives with shadcn-style APIs: `Button {variant: "default"|"outline"|"ghost"|"destructive", size?: "sm"}`, `Input`, `Label`, `Textarea`, `Select` (styled native), `Badge {variant}`, `Dialog {open, onClose, title, children}`.
  - Routes registered in `App.tsx`: `/login`, and under `RequireAuth`+`Layout` (Layout arrives in Task 3 — until then a passthrough placeholder `<Outlet/>` component): `/` (Browse), `/documents/:id`, `/scan`, `/search`, `/chat` — pages are placeholder stubs (`<div>...</div>`) replaced by later tasks.

- [ ] **Step 1: Scaffold the Vite app**

```bash
cd /path/to/repo  # repo root
npm create vite@latest frontend -- --template react-ts
cd frontend && npm install
npm install @tanstack/react-query react-router tailwindcss @tailwindcss/vite
npm install -D vitest jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom
```

Append to the repo-root `.gitignore`:

```gitignore
frontend/node_modules/
frontend/dist/
```

Delete the template's `frontend/src/App.css` and `frontend/src/assets/` (unused). Replace `frontend/src/index.css` with:

```css
@import "tailwindcss";
```

- [ ] **Step 2: Configure Vite, vitest, and the path alias**

`frontend/vite.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
```

`frontend/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

In `frontend/tsconfig.app.json`, add inside `compilerOptions`:

```json
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] },
    "types": ["vitest/globals"]
```

In `frontend/package.json` scripts, set: `"test": "vitest run"`.

- [ ] **Step 3: Write failing api-client tests**

`frontend/src/lib/api.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, clearToken, fileUrl, getToken, setToken } from "./api";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  clearToken();
});
afterEach(() => vi.unstubAllGlobals());

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  it("attaches the bearer token", async () => {
    setToken("tok123");
    fetchMock.mockResolvedValue(jsonResponse(200, { ok: true }));
    await api.get("/api/auth/me");
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer tok123");
  });

  it("parses the flattened error envelope into ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { error: { code: "duplicate_folder", message: "exists", detail: null } }),
    );
    const err = await api.post("/api/folders", { name: "x" }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("duplicate_folder");
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.del("/api/tags/1")).resolves.toBeUndefined();
  });

  it("token storage round-trips", () => {
    setToken("abc");
    expect(getToken()).toBe("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });

  it("fileUrl embeds the token", () => {
    setToken("tok");
    expect(fileUrl("doc-1")).toBe("/api/documents/doc-1/file?token=tok");
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL (module `./api` missing).

- [ ] **Step 4: Implement utils, types, api client**

`frontend/src/lib/utils.ts`:

```ts
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}
```

`frontend/src/lib/types.ts`:

```ts
export interface Tag {
  id: number;
  name: string;
  color: string;
}

export interface Folder {
  id: number;
  name: string;
  parent_id: number | null;
  created_at: string;
}

export type DocType = "scan" | "pdf" | "text" | "image" | "video";
export type DocStatus = "pending" | "processing" | "ready" | "failed";

export interface Document {
  id: string;
  title: string;
  description: string;
  summary: string | null;
  folder_id: number | null;
  doc_type: DocType;
  ocr_languages: string;
  status: DocStatus;
  error_message: string | null;
  original_filename: string | null;
  file_path: string | null;
  page_count: number | null;
  file_size: number | null;
  created_at: string;
  updated_at: string;
  tags: Tag[];
}

export interface DocumentText {
  summary: string | null;
  chunks: { chunk_index: number; page_number: number | null; content: string }[];
}

export interface ScanStatus {
  available: boolean;
  busy: boolean;
}

export interface ScanPageInfo {
  id: number;
  page_number: number;
}

export interface SearchSnippet {
  chunk_id: number;
  page_number: number | null;
  source: string;
  text: string;
  similarity: number | null;
}

export interface SearchResult {
  document: Document;
  score: number;
  snippets: SearchSnippet[];
}

export interface SearchResponse {
  mode: string;
  results: SearchResult[];
}

export interface ChatSource {
  n: number;
  chunk_id: number;
  document_id: string;
  title: string;
  page_number: number | null;
}

export type ChatEvent =
  | { type: "meta"; grounded: boolean; sources: ChatSource[] }
  | { type: "delta"; text: string }
  | { type: "done" }
  | { type: "error"; code: string; message: string };
```

`frontend/src/lib/api.ts`:

```ts
const TOKEN_KEY = "origami_token";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  form?: FormData,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const resp = await fetch(path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (resp.status === 204) return undefined as T;
  const data = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = (data as { error?: { code?: string; message?: string; detail?: unknown } })?.error;
    throw new ApiError(resp.status, err?.code ?? "unknown_error", err?.message ?? resp.statusText, err?.detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
  postForm: <T>(path: string, form: FormData) => request<T>("POST", path, undefined, form),
};

export const fileUrl = (documentId: string): string =>
  `/api/documents/${documentId}/file?token=${getToken() ?? ""}`;
```

Run: `cd frontend && npm test` — Expected: api tests PASS.

- [ ] **Step 5: UI primitives**

`frontend/src/components/ui/button.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

const variants = {
  default: "bg-zinc-900 text-white hover:bg-zinc-700",
  outline: "border border-zinc-300 hover:bg-zinc-100",
  ghost: "hover:bg-zinc-100",
  destructive: "bg-red-600 text-white hover:bg-red-500",
};

export function Button({
  variant = "default",
  size,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof variants; size?: "sm" }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none",
        size === "sm" ? "h-8 px-3 text-sm" : "h-9 px-4 text-sm",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/input.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { InputHTMLAttributes } from "react";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-md border border-zinc-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400",
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/label.tsx`:

```tsx
import type { LabelHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Label({ className, ...props }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("mb-1 block text-sm font-medium text-zinc-700", className)} {...props} />;
}
```

`frontend/src/components/ui/textarea.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { TextareaHTMLAttributes } from "react";

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded-md border border-zinc-300 bg-white p-3 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400",
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/select.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { SelectHTMLAttributes } from "react";

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 w-full rounded-md border border-zinc-300 bg-white px-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400",
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/badge.tsx`:

```tsx
import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

const variants = {
  default: "bg-zinc-100 text-zinc-800",
  green: "bg-green-100 text-green-800",
  amber: "bg-amber-100 text-amber-800",
  blue: "bg-blue-100 text-blue-800",
  red: "bg-red-100 text-red-800",
};

export function Badge({
  variant = "default",
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: keyof typeof variants }) {
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", variants[variant], className)}
      {...props}
    />
  );
}
```

`frontend/src/components/ui/dialog.tsx`:

```tsx
import { useEffect, type ReactNode } from "react";

export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        role="dialog"
        aria-label={title}
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Write failing login test**

`frontend/src/pages/LoginPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "@/auth";
import { clearToken, getToken } from "@/lib/api";
import { LoginPage } from "./LoginPage";

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  clearToken();
});
afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

it("logs in and stores the token", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/auth/login") return json(200, { access_token: "tok42", token_type: "bearer" });
    if (url === "/api/auth/me") return json(200, { id: 1, username: "marco" });
    return json(404, { error: { code: "not_found", message: "no" } });
  });

  render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText(/username/i), "marco");
  await userEvent.type(screen.getByLabelText(/password/i), "secret");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText(/redirecting/i)).toBeInTheDocument();
  expect(getToken()).toBe("tok42");
});

it("shows the error message on bad credentials", async () => {
  fetchMock.mockResolvedValue(json(401, { error: { code: "invalid_credentials", message: "Wrong username or password" } }));

  render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText(/username/i), "marco");
  await userEvent.type(screen.getByLabelText(/password/i), "wrong");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText(/wrong username or password/i)).toBeInTheDocument();
  expect(getToken()).toBeNull();
});
```

Run: `cd frontend && npm test` — Expected: login tests FAIL (missing modules).

- [ ] **Step 7: Implement auth context and LoginPage**

`frontend/src/auth.tsx`:

```tsx
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, Outlet } from "react-router";
import { api, ApiError, clearToken, getToken, setToken } from "@/lib/api";

interface User {
  id: number;
  username: string;
}

interface AuthValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return;
    api
      .get<User>("/api/auth/me")
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) clearToken();
      })
      .finally(() => setLoading(false));
  }, []);

  const login = async (username: string, password: string) => {
    const { access_token } = await api.post<{ access_token: string }>("/api/auth/login", {
      username,
      password,
    });
    setToken(access_token);
    setUser(await api.get<User>("/api/auth/me"));
  };

  const logout = () => {
    clearToken();
    setUser(null);
  };

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth outside AuthProvider");
  return value;
}

export function RequireAuth() {
  const { user, loading } = useAuth();
  if (loading) return <div className="p-8 text-zinc-500">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Outlet />;
}
```

`frontend/src/pages/LoginPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router";
import { useAuth } from "@/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api";

export function LoginPage() {
  const { user, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  if (user || done) {
    return (
      <div className="p-8 text-zinc-500">
        Redirecting…
        <Navigate to="/" replace />
      </div>
    );
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50">
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4 rounded-lg bg-white p-8 shadow">
        <h1 className="text-xl font-semibold">Origami</h1>
        <div>
          <Label htmlFor="username">Username</Label>
          <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </div>
  );
}
```

- [ ] **Step 8: Router skeleton**

`frontend/src/App.tsx`:

```tsx
import { Route, Routes } from "react-router";
import { RequireAuth } from "@/auth";
import { LoginPage } from "@/pages/LoginPage";

// Placeholder pages — replaced by Tasks 3-8.
const BrowsePage = () => <div className="p-8">Browse</div>;
const DocumentPage = () => <div className="p-8">Document</div>;
const ScanPage = () => <div className="p-8">Scan</div>;
const SearchPage = () => <div className="p-8">Search</div>;
const ChatPage = () => <div className="p-8">Chat</div>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route path="/" element={<BrowsePage />} />
        <Route path="/documents/:id" element={<DocumentPage />} />
        <Route path="/scan" element={<ScanPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/chat" element={<ChatPage />} />
      </Route>
    </Routes>
  );
}
```

`frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/auth";
import App from "@/App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
```

- [ ] **Step 9: Run all frontend tests + typecheck + build, then commit**

Run: `cd frontend && npm test && npx tsc -b && npm run build`
Expected: all tests PASS, no type errors, build succeeds.

```bash
git add .gitignore frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/tsconfig*.json frontend/index.html frontend/src frontend/public frontend/eslint.config.js frontend/.gitignore
git commit -m "feat: frontend scaffold with api client, auth, and router skeleton"
```

(If the Vite template generated files not listed here, stage them explicitly by name after checking `git status` — never `git add -A`.)

---

### Task 3: Layout, folder tree, tags

**Files:**
- Create: `frontend/src/lib/folderTree.ts`, `frontend/src/hooks/useFolders.ts`, `frontend/src/hooks/useTags.ts`, `frontend/src/components/Layout.tsx`, `frontend/src/components/FolderTree.tsx`, `frontend/src/components/TagManager.tsx`
- Modify: `frontend/src/App.tsx` (wrap protected routes in `Layout`)
- Test: `frontend/src/lib/folderTree.test.ts`

**Interfaces:**
- Consumes: `api`, `Folder`/`Tag` types, UI primitives.
- Produces:
  - `lib/folderTree.ts`: `interface FolderNode extends Folder { children: FolderNode[] }`, `buildFolderTree(folders: Folder[]): FolderNode[]` — roots sorted by name, children recursively sorted; folders with a missing parent become roots.
  - `hooks/useFolders.ts`: `useFolders()` (query key `["folders"]`), `useCreateFolder()`, `useRenameFolder()`, `useDeleteFolder()` mutations (invalidate `["folders"]`).
  - `hooks/useTags.ts`: `useTags()` (key `["tags"]`), `useCreateTag()`, `useDeleteTag()` (invalidate `["tags"]` and `["documents"]`).
  - `components/FolderTree.tsx`: `<FolderTree selectedId onSelect={(id: number | null) => void} />` — renders the tree with expand/collapse, an "All documents" root option (`null`), per-folder new-subfolder/rename/delete actions (via `window.prompt`/`window.confirm`, showing `ApiError.message` in `window.alert` on 409s).
  - `components/TagManager.tsx`: list of tags with color dots, create form (name + color input), delete buttons.
  - `components/Layout.tsx`: sidebar (nav links to Browse/Scan/Search/Chat, `FolderTree`, `TagManager`, logout button) + `<Outlet context={...}>`. Folder selection lives in the URL: `Layout` reads/writes `?folder=<id>` via `useSearchParams` and navigates to `/` on selection, so BrowsePage (Task 4) just reads the param.

- [ ] **Step 1: Write failing tests**

`frontend/src/lib/folderTree.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildFolderTree } from "./folderTree";
import type { Folder } from "./types";

const folder = (id: number, name: string, parent_id: number | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-01-01",
});

describe("buildFolderTree", () => {
  it("nests children under parents", () => {
    const tree = buildFolderTree([folder(1, "root"), folder(2, "child", 1), folder(3, "grand", 2)]);
    expect(tree).toHaveLength(1);
    expect(tree[0].children[0].name).toBe("child");
    expect(tree[0].children[0].children[0].name).toBe("grand");
  });

  it("sorts siblings alphabetically at every level", () => {
    const tree = buildFolderTree([folder(1, "b"), folder(2, "a"), folder(3, "z", 1), folder(4, "c", 1)]);
    expect(tree.map((n) => n.name)).toEqual(["a", "b"]);
    expect(tree[1].children.map((n) => n.name)).toEqual(["c", "z"]);
  });

  it("treats folders with unknown parents as roots", () => {
    const tree = buildFolderTree([folder(5, "orphan", 999)]);
    expect(tree.map((n) => n.name)).toEqual(["orphan"]);
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL (module missing).

- [ ] **Step 2: Implement `buildFolderTree`**

`frontend/src/lib/folderTree.ts`:

```ts
import type { Folder } from "./types";

export interface FolderNode extends Folder {
  children: FolderNode[];
}

export function buildFolderTree(folders: Folder[]): FolderNode[] {
  const nodes = new Map<number, FolderNode>();
  folders.forEach((f) => nodes.set(f.id, { ...f, children: [] }));
  const roots: FolderNode[] = [];
  nodes.forEach((node) => {
    if (node.parent_id !== null && nodes.has(node.parent_id)) {
      nodes.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  const sortRec = (list: FolderNode[]) => {
    list.sort((a, b) => a.name.localeCompare(b.name));
    list.forEach((n) => sortRec(n.children));
  };
  sortRec(roots);
  return roots;
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: Query hooks**

`frontend/src/hooks/useFolders.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Folder } from "@/lib/types";

export function useFolders() {
  return useQuery({ queryKey: ["folders"], queryFn: () => api.get<Folder[]>("/api/folders") });
}

function useInvalidateFolders() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["folders"] });
}

export function useCreateFolder() {
  const invalidate = useInvalidateFolders();
  return useMutation({
    mutationFn: (body: { name: string; parent_id: number | null }) => api.post<Folder>("/api/folders", body),
    onSuccess: invalidate,
  });
}

export function useRenameFolder() {
  const invalidate = useInvalidateFolders();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => api.patch<Folder>(`/api/folders/${id}`, { name }),
    onSuccess: invalidate,
  });
}

export function useDeleteFolder() {
  const invalidate = useInvalidateFolders();
  return useMutation({
    mutationFn: (id: number) => api.del(`/api/folders/${id}`),
    onSuccess: invalidate,
  });
}
```

`frontend/src/hooks/useTags.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Tag } from "@/lib/types";

export function useTags() {
  return useQuery({ queryKey: ["tags"], queryFn: () => api.get<Tag[]>("/api/tags") });
}

export function useCreateTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; color: string }) => api.post<Tag>("/api/tags", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tags"] }),
  });
}

export function useDeleteTag() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/api/tags/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tags"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}
```

- [ ] **Step 4: FolderTree, TagManager, Layout**

`frontend/src/components/FolderTree.tsx`:

```tsx
import { useState } from "react";
import { useCreateFolder, useDeleteFolder, useFolders, useRenameFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";
import { buildFolderTree, type FolderNode } from "@/lib/folderTree";
import { cn } from "@/lib/utils";

function report(err: unknown) {
  window.alert(err instanceof ApiError ? err.message : "Operation failed");
}

function Node({
  node,
  depth,
  selectedId,
  onSelect,
}: {
  node: FolderNode;
  depth: number;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const [open, setOpen] = useState(true);
  const create = useCreateFolder();
  const rename = useRenameFolder();
  const remove = useDeleteFolder();

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded px-2 py-1 text-sm hover:bg-zinc-100",
          selectedId === node.id && "bg-zinc-200 font-medium",
        )}
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <button onClick={() => setOpen(!open)} className="w-4 text-zinc-400" aria-label="toggle">
          {node.children.length > 0 ? (open ? "▾" : "▸") : "·"}
        </button>
        <button className="flex-1 truncate text-left" onClick={() => onSelect(node.id)}>
          {node.name}
        </button>
        <span className="hidden gap-1 group-hover:flex">
          <button
            title="New subfolder"
            onClick={() => {
              const name = window.prompt("Subfolder name");
              if (name) create.mutate({ name, parent_id: node.id }, { onError: report });
            }}
          >
            +
          </button>
          <button
            title="Rename"
            onClick={() => {
              const name = window.prompt("New name", node.name);
              if (name && name !== node.name) rename.mutate({ id: node.id, name }, { onError: report });
            }}
          >
            ✎
          </button>
          <button
            title="Delete"
            onClick={() => {
              if (window.confirm(`Delete folder "${node.name}"?`))
                remove.mutate(node.id, {
                  onError: report,
                  onSuccess: () => selectedId === node.id && onSelect(null),
                });
            }}
          >
            ×
          </button>
        </span>
      </div>
      {open &&
        node.children.map((child) => (
          <Node key={child.id} node={child} depth={depth + 1} selectedId={selectedId} onSelect={onSelect} />
        ))}
    </div>
  );
}

export function FolderTree({
  selectedId,
  onSelect,
}: {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const { data: folders } = useFolders();
  const create = useCreateFolder();
  const tree = buildFolderTree(folders ?? []);

  return (
    <div>
      <div className="mb-1 flex items-center justify-between px-2">
        <span className="text-xs font-semibold uppercase text-zinc-400">Folders</span>
        <button
          title="New folder"
          className="text-zinc-400 hover:text-zinc-700"
          onClick={() => {
            const name = window.prompt("Folder name");
            if (name) create.mutate({ name, parent_id: null }, { onError: report });
          }}
        >
          +
        </button>
      </div>
      <button
        className={cn(
          "w-full rounded px-2 py-1 text-left text-sm hover:bg-zinc-100",
          selectedId === null && "bg-zinc-200 font-medium",
        )}
        onClick={() => onSelect(null)}
      >
        All documents
      </button>
      {tree.map((node) => (
        <Node key={node.id} node={node} depth={0} selectedId={selectedId} onSelect={onSelect} />
      ))}
    </div>
  );
}
```

`frontend/src/components/TagManager.tsx`:

```tsx
import { useState } from "react";
import { useCreateTag, useDeleteTag, useTags } from "@/hooks/useTags";

export function TagManager() {
  const { data: tags } = useTags();
  const create = useCreateTag();
  const remove = useDeleteTag();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#888888");

  return (
    <div className="mt-6">
      <span className="px-2 text-xs font-semibold uppercase text-zinc-400">Tags</span>
      <ul className="mt-1">
        {(tags ?? []).map((tag) => (
          <li key={tag.id} className="group flex items-center gap-2 px-2 py-1 text-sm">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: tag.color }} />
            <span className="flex-1 truncate">{tag.name}</span>
            <button
              className="hidden text-zinc-400 hover:text-red-600 group-hover:block"
              onClick={() => window.confirm(`Delete tag "${tag.name}"?`) && remove.mutate(tag.id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <form
        className="mt-1 flex items-center gap-1 px-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!name.trim()) return;
          create.mutate({ name: name.trim(), color }, { onSuccess: () => setName("") });
        }}
      >
        <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="h-6 w-6" />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New tag"
          className="w-full rounded border border-zinc-200 px-1 py-0.5 text-sm"
        />
      </form>
    </div>
  );
}
```

`frontend/src/components/Layout.tsx`:

```tsx
import { NavLink, Outlet, useNavigate, useSearchParams } from "react-router";
import { useAuth } from "@/auth";
import { FolderTree } from "@/components/FolderTree";
import { TagManager } from "@/components/TagManager";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Browse" },
  { to: "/scan", label: "Scan" },
  { to: "/search", label: "Search" },
  { to: "/chat", label: "Chat" },
];

export function Layout() {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const selectedFolder = searchParams.get("folder") ? Number(searchParams.get("folder")) : null;

  const selectFolder = (id: number | null) => {
    navigate(id === null ? "/" : `/?folder=${id}`);
  };

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-64 flex-col border-r border-zinc-200 bg-zinc-50 p-3">
        <h1 className="mb-4 px-2 text-lg font-bold">Origami</h1>
        <nav className="mb-4 space-y-0.5">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn("block rounded px-2 py-1 text-sm hover:bg-zinc-100", isActive && "bg-zinc-200 font-medium")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex-1 overflow-y-auto">
          <FolderTree selectedId={selectedFolder} onSelect={selectFolder} />
          <TagManager />
        </div>
        <button onClick={logout} className="mt-4 px-2 text-left text-sm text-zinc-500 hover:text-zinc-800">
          Log out {user ? `(${user.username})` : ""}
        </button>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
```

Update `frontend/src/App.tsx` — wrap the protected routes:

```tsx
import { Route, Routes } from "react-router";
import { RequireAuth } from "@/auth";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/LoginPage";

const BrowsePage = () => <div className="p-8">Browse</div>;
const DocumentPage = () => <div className="p-8">Document</div>;
const ScanPage = () => <div className="p-8">Scan</div>;
const SearchPage = () => <div className="p-8">Search</div>;
const ChatPage = () => <div className="p-8">Chat</div>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<BrowsePage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
```

- [ ] **Step 5: Run tests + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b`
Expected: all PASS, no type errors.

```bash
git add frontend/src/lib/folderTree.ts frontend/src/lib/folderTree.test.ts frontend/src/hooks/useFolders.ts frontend/src/hooks/useTags.ts frontend/src/components/Layout.tsx frontend/src/components/FolderTree.tsx frontend/src/components/TagManager.tsx frontend/src/App.tsx
git commit -m "feat: app layout with folder tree and tag manager"
```

---

### Task 4: Browse page — document grid, filters, upload

**Files:**
- Create: `frontend/src/lib/upload.ts`, `frontend/src/hooks/useDocuments.ts`, `frontend/src/components/DocumentCard.tsx`, `frontend/src/components/UploadDialog.tsx`, `frontend/src/pages/BrowsePage.tsx`
- Modify: `frontend/src/App.tsx` (use real BrowsePage)
- Test: `frontend/src/lib/upload.test.ts`, `frontend/src/hooks/useDocuments.test.ts`

**Interfaces:**
- Consumes: `api.postForm`, `Document`/`Tag` types, `useTags`/`useFolders`, UI primitives, `?folder=` search param set by Layout.
- Produces:
  - `lib/upload.ts`: `interface UploadFields { title?: string; folderId?: number | null; tagIds?: number[]; ocrLanguages?: string }`, `buildUploadForm(file: File, fields: UploadFields): FormData` — appends `file` always; `title`/`folder_id`/`ocr_languages` only when set; `tag_ids` joined with commas only when non-empty. `fileStem(name: string): string`.
  - `hooks/useDocuments.ts`: `interface DocumentFilters { folderId: number | null; tagId: number | null; docType: string | null }`, `documentsQueryString(filters): string`, `documentsPollInterval(docs?: Document[]): number | false` (4000 while any doc is `pending`/`processing`, else `false`), `useDocuments(filters)` (key `["documents", filters]`, `refetchInterval` wired to `documentsPollInterval`), `useUploadDocument()` (postForm + invalidate `["documents"]`), `useDeleteDocument()`.
  - `components/DocumentCard.tsx`: card linking to `/documents/{id}` — title, doc_type, status `Badge` (ready→green, processing→blue, pending→amber, failed→red with `error_message` in `title` attr), tag chips, page count / size.
  - `components/UploadDialog.tsx`: `<UploadDialog file, open, onClose>` — fields: title (defaults to file stem), folder select, tag checkboxes, OCR language select (`ita+eng` / `ita` / `eng`); submit → `useUploadDocument`.
  - `pages/BrowsePage.tsx`: reads `?folder=`, tag/doc_type filter selects, document grid, hidden file input + full-page drag-and-drop that opens `UploadDialog`, per-card delete (confirm).
  - Status badge variants exported as `STATUS_VARIANTS` from `DocumentCard.tsx` for reuse.

- [ ] **Step 1: Write failing tests**

`frontend/src/lib/upload.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildUploadForm, fileStem } from "./upload";

describe("buildUploadForm", () => {
  const file = new File([new Uint8Array([1])], "bolletta marzo.pdf", { type: "application/pdf" });

  it("always includes the file, omits unset fields", () => {
    const form = buildUploadForm(file, {});
    expect(form.get("file")).toBe(file);
    expect(form.has("title")).toBe(false);
    expect(form.has("folder_id")).toBe(false);
    expect(form.has("tag_ids")).toBe(false);
    expect(form.has("ocr_languages")).toBe(false);
  });

  it("serializes all fields", () => {
    const form = buildUploadForm(file, {
      title: "Bolletta",
      folderId: 7,
      tagIds: [1, 3],
      ocrLanguages: "ita",
    });
    expect(form.get("title")).toBe("Bolletta");
    expect(form.get("folder_id")).toBe("7");
    expect(form.get("tag_ids")).toBe("1,3");
    expect(form.get("ocr_languages")).toBe("ita");
  });

  it("omits empty tag list and null folder", () => {
    const form = buildUploadForm(file, { folderId: null, tagIds: [] });
    expect(form.has("folder_id")).toBe(false);
    expect(form.has("tag_ids")).toBe(false);
  });
});

describe("fileStem", () => {
  it("strips the extension", () => {
    expect(fileStem("bolletta marzo.pdf")).toBe("bolletta marzo");
    expect(fileStem("archive.tar.gz")).toBe("archive.tar");
    expect(fileStem("noext")).toBe("noext");
  });
});
```

`frontend/src/hooks/useDocuments.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { documentsPollInterval, documentsQueryString } from "./useDocuments";
import type { Document } from "@/lib/types";

const doc = (status: Document["status"]): Document =>
  ({ status }) as Document;

describe("documentsPollInterval", () => {
  it("polls while any document is processing or pending", () => {
    expect(documentsPollInterval([doc("ready"), doc("processing")])).toBe(4000);
    expect(documentsPollInterval([doc("pending")])).toBe(4000);
  });
  it("stops when everything settled", () => {
    expect(documentsPollInterval([doc("ready"), doc("failed")])).toBe(false);
    expect(documentsPollInterval([])).toBe(false);
    expect(documentsPollInterval(undefined)).toBe(false);
  });
});

describe("documentsQueryString", () => {
  it("includes only active filters", () => {
    expect(documentsQueryString({ folderId: null, tagId: null, docType: null })).toBe("");
    expect(documentsQueryString({ folderId: 3, tagId: 2, docType: "pdf" })).toBe(
      "?folder_id=3&tag_id=2&doc_type=pdf",
    );
  });
});
```

Run: `cd frontend && npm test` — Expected: new tests FAIL.

- [ ] **Step 2: Implement `lib/upload.ts` and `hooks/useDocuments.ts`**

`frontend/src/lib/upload.ts`:

```ts
export interface UploadFields {
  title?: string;
  folderId?: number | null;
  tagIds?: number[];
  ocrLanguages?: string;
}

export function fileStem(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

export function buildUploadForm(file: File, fields: UploadFields): FormData {
  const form = new FormData();
  form.append("file", file);
  if (fields.title) form.append("title", fields.title);
  if (fields.folderId !== null && fields.folderId !== undefined)
    form.append("folder_id", String(fields.folderId));
  if (fields.tagIds && fields.tagIds.length > 0) form.append("tag_ids", fields.tagIds.join(","));
  if (fields.ocrLanguages) form.append("ocr_languages", fields.ocrLanguages);
  return form;
}
```

`frontend/src/hooks/useDocuments.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Document } from "@/lib/types";

export interface DocumentFilters {
  folderId: number | null;
  tagId: number | null;
  docType: string | null;
}

export function documentsQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  if (filters.folderId !== null) params.set("folder_id", String(filters.folderId));
  if (filters.tagId !== null) params.set("tag_id", String(filters.tagId));
  if (filters.docType !== null) params.set("doc_type", filters.docType);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export function documentsPollInterval(docs?: Document[]): number | false {
  return docs?.some((d) => d.status === "pending" || d.status === "processing") ? 4000 : false;
}

export function useDocuments(filters: DocumentFilters) {
  return useQuery({
    queryKey: ["documents", filters],
    queryFn: () => api.get<Document[]>(`/api/documents${documentsQueryString(filters)}`),
    refetchInterval: (query) => documentsPollInterval(query.state.data),
  });
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: FormData) => api.postForm<Document>("/api/documents/upload", form),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.del(`/api/documents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: DocumentCard, UploadDialog, BrowsePage**

`frontend/src/components/DocumentCard.tsx`:

```tsx
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import type { Document } from "@/lib/types";

export const STATUS_VARIANTS = {
  ready: "green",
  processing: "blue",
  pending: "amber",
  failed: "red",
} as const;

const TYPE_ICONS: Record<Document["doc_type"], string> = {
  scan: "🖨",
  pdf: "📄",
  text: "📝",
  image: "🖼",
  video: "🎬",
};

export function DocumentCard({ doc, onDelete }: { doc: Document; onDelete: (id: string) => void }) {
  return (
    <div className="group relative rounded-lg border border-zinc-200 bg-white p-4 hover:shadow">
      <Link to={`/documents/${doc.id}`} className="block">
        <div className="mb-2 flex items-center gap-2">
          <span>{TYPE_ICONS[doc.doc_type]}</span>
          <span className="flex-1 truncate font-medium">{doc.title}</span>
          <Badge variant={STATUS_VARIANTS[doc.status]} title={doc.error_message ?? undefined}>
            {doc.status}
          </Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {doc.tags.map((tag) => (
            <span
              key={tag.id}
              className="rounded-full px-2 py-0.5 text-xs"
              style={{ backgroundColor: `${tag.color}22`, color: tag.color }}
            >
              {tag.name}
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {doc.page_count ? `${doc.page_count} pages · ` : ""}
          {doc.file_size ? `${Math.round(doc.file_size / 1024)} KB` : ""}
        </p>
      </Link>
      <button
        className="absolute right-2 bottom-2 hidden text-zinc-300 hover:text-red-600 group-hover:block"
        title="Delete"
        onClick={() => window.confirm(`Delete "${doc.title}"?`) && onDelete(doc.id)}
      >
        🗑
      </button>
    </div>
  );
}
```

`frontend/src/components/UploadDialog.tsx`:

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { useUploadDocument } from "@/hooks/useDocuments";
import { ApiError } from "@/lib/api";
import { buildUploadForm, fileStem } from "@/lib/upload";

export function UploadDialog({ file, open, onClose }: { file: File | null; open: boolean; onClose: () => void }) {
  const { data: folders } = useFolders();
  const { data: tags } = useTags();
  const upload = useUploadDocument();
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [languages, setLanguages] = useState("ita+eng");
  const [error, setError] = useState<string | null>(null);

  if (!file) return null;

  const submit = () => {
    setError(null);
    upload.mutate(
      buildUploadForm(file, { title: title || fileStem(file.name), folderId, tagIds, ocrLanguages: languages }),
      {
        onSuccess: () => {
          setTitle("");
          setTagIds([]);
          onClose();
        },
        onError: (err) => setError(err instanceof ApiError ? err.message : "Upload failed"),
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} title={`Upload ${file.name}`}>
      <div className="space-y-3">
        <div>
          <Label htmlFor="up-title">Title</Label>
          <Input id="up-title" value={title} placeholder={fileStem(file.name)} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="up-folder">Folder</Label>
          <Select
            id="up-folder"
            value={folderId ?? ""}
            onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">(root)</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label>Tags</Label>
          <div className="flex flex-wrap gap-2">
            {(tags ?? []).map((tag) => (
              <label key={tag.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={tagIds.includes(tag.id)}
                  onChange={(e) =>
                    setTagIds(e.target.checked ? [...tagIds, tag.id] : tagIds.filter((id) => id !== tag.id))
                  }
                />
                {tag.name}
              </label>
            ))}
          </div>
        </div>
        <div>
          <Label htmlFor="up-lang">OCR language</Label>
          <Select id="up-lang" value={languages} onChange={(e) => setLanguages(e.target.value)}>
            <option value="ita+eng">Italian + English</option>
            <option value="ita">Italian</option>
            <option value="eng">English</option>
          </Select>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
```

`frontend/src/pages/BrowsePage.tsx`:

```tsx
import { useRef, useState, type DragEvent } from "react";
import { useSearchParams } from "react-router";
import { DocumentCard } from "@/components/DocumentCard";
import { UploadDialog } from "@/components/UploadDialog";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { useDeleteDocument, useDocuments } from "@/hooks/useDocuments";
import { useTags } from "@/hooks/useTags";

const DOC_TYPES = ["scan", "pdf", "text", "image", "video"];

export function BrowsePage() {
  const [searchParams] = useSearchParams();
  const folderId = searchParams.get("folder") ? Number(searchParams.get("folder")) : null;
  const [tagId, setTagId] = useState<number | null>(null);
  const [docType, setDocType] = useState<string | null>(null);
  const { data: docs, isLoading } = useDocuments({ folderId, tagId, docType });
  const { data: tags } = useTags();
  const deleteDoc = useDeleteDocument();

  const fileInput = useRef<HTMLInputElement>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) setPendingFile(file);
  };

  return (
    <div
      className="relative min-h-full p-6"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center border-4 border-dashed border-zinc-400 bg-white/80 text-lg text-zinc-600">
          Drop to upload
        </div>
      )}
      <div className="mb-4 flex items-center gap-3">
        <h2 className="flex-1 text-lg font-semibold">Documents</h2>
        <Select
          className="w-40"
          value={tagId ?? ""}
          onChange={(e) => setTagId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All tags</option>
          {(tags ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <Select className="w-32" value={docType ?? ""} onChange={(e) => setDocType(e.target.value || null)}>
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Button onClick={() => fileInput.current?.click()}>Upload</Button>
        <input
          ref={fileInput}
          type="file"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) setPendingFile(file);
            e.target.value = "";
          }}
        />
      </div>
      {isLoading && <p className="text-zinc-400">Loading…</p>}
      {docs && docs.length === 0 && <p className="text-zinc-400">No documents here yet — upload or scan one.</p>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(docs ?? []).map((doc) => (
          <DocumentCard key={doc.id} doc={doc} onDelete={(id) => deleteDoc.mutate(id)} />
        ))}
      </div>
      <UploadDialog file={pendingFile} open={pendingFile !== null} onClose={() => setPendingFile(null)} />
    </div>
  );
}
```

In `frontend/src/App.tsx`: remove the `BrowsePage` placeholder and `import { BrowsePage } from "@/pages/BrowsePage";`.

- [ ] **Step 4: Run tests + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b`
Expected: all PASS.

```bash
git add frontend/src/lib/upload.ts frontend/src/lib/upload.test.ts frontend/src/hooks/useDocuments.ts frontend/src/hooks/useDocuments.test.ts frontend/src/components/DocumentCard.tsx frontend/src/components/UploadDialog.tsx frontend/src/pages/BrowsePage.tsx frontend/src/App.tsx
git commit -m "feat: browse page with filters, polling status badges, and upload"
```

---

### Task 5: Document view page

**Files:**
- Create: `frontend/src/lib/viewer.ts`, `frontend/src/pages/DocumentPage.tsx`
- Modify: `frontend/src/App.tsx` (use real DocumentPage)
- Test: `frontend/src/lib/viewer.test.ts`

**Interfaces:**
- Consumes: `fileUrl`, `api`, `Document`/`DocumentText` types, `useFolders`/`useTags`, `STATUS_VARIANTS`, UI primitives.
- Produces:
  - `lib/viewer.ts`: `viewerKind(docType: DocType): "pdf" | "image" | "video" | "text"` — scan/pdf→pdf, image→image, video→video, text→text.
  - `pages/DocumentPage.tsx`: loads the document (key `["document", id]`, poll 4s while pending/processing); viewer per kind (`<iframe src={fileUrl(id)}>` for pdf, `<img>` for image, `<video controls>` for video, extracted text for text docs); tabs Preview / Text (Text fetches `["document-text", id]` → `/api/documents/{id}/text`, shows summary block + per-page text); metadata panel (title/description inputs, folder select, tag checkboxes, Save → PATCH + invalidate); failed banner with `error_message`; Delete (confirm → DELETE → navigate `/`).

- [ ] **Step 1: Write failing test**

`frontend/src/lib/viewer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { viewerKind } from "./viewer";

describe("viewerKind", () => {
  it("maps every doc type", () => {
    expect(viewerKind("scan")).toBe("pdf");
    expect(viewerKind("pdf")).toBe("pdf");
    expect(viewerKind("image")).toBe("image");
    expect(viewerKind("video")).toBe("video");
    expect(viewerKind("text")).toBe("text");
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `viewer.ts`**

`frontend/src/lib/viewer.ts`:

```ts
import type { DocType } from "./types";

export function viewerKind(docType: DocType): "pdf" | "image" | "video" | "text" {
  switch (docType) {
    case "scan":
    case "pdf":
      return "pdf";
    case "image":
      return "image";
    case "video":
      return "video";
    case "text":
      return "text";
  }
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: Implement DocumentPage**

`frontend/src/pages/DocumentPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { STATUS_VARIANTS } from "@/components/DocumentCard";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { api, fileUrl } from "@/lib/api";
import type { Document, DocumentText } from "@/lib/types";
import { viewerKind } from "@/lib/viewer";

function Viewer({ doc }: { doc: Document }) {
  const kind = viewerKind(doc.doc_type);
  if (doc.status !== "ready" && kind !== "video")
    return <div className="flex h-96 items-center justify-center text-zinc-400">Processing…</div>;
  const src = fileUrl(doc.id);
  if (kind === "pdf") return <iframe title="preview" src={src} className="h-[75vh] w-full rounded border" />;
  if (kind === "image") return <img src={src} alt={doc.title} className="max-h-[75vh] rounded border" />;
  if (kind === "video") return <video controls src={src} className="max-h-[75vh] w-full rounded border" />;
  return <TextView documentId={doc.id} />;
}

function TextView({ documentId }: { documentId: string }) {
  const { data } = useQuery({
    queryKey: ["document-text", documentId],
    queryFn: () => api.get<DocumentText>(`/api/documents/${documentId}/text`),
  });
  if (!data) return <p className="text-zinc-400">Loading…</p>;
  return (
    <div className="space-y-4">
      {data.summary && (
        <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
          <span className="font-medium">Summary: </span>
          {data.summary}
        </div>
      )}
      {data.chunks.map((chunk) => (
        <div key={chunk.chunk_index}>
          {chunk.page_number != null && (
            <p className="mb-1 text-xs font-semibold text-zinc-400">Page {chunk.page_number}</p>
          )}
          <p className="whitespace-pre-wrap text-sm">{chunk.content}</p>
        </div>
      ))}
      {data.chunks.length === 0 && <p className="text-zinc-400">No extracted text.</p>}
    </div>
  );
}

export function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: doc } = useQuery({
    queryKey: ["document", id],
    queryFn: () => api.get<Document>(`/api/documents/${id}`),
    refetchInterval: (q) =>
      q.state.data && ["pending", "processing"].includes(q.state.data.status) ? 4000 : false,
  });
  const { data: folders } = useFolders();
  const { data: tags } = useTags();

  const [tab, setTab] = useState<"preview" | "text">("preview");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);

  useEffect(() => {
    if (doc) {
      setTitle(doc.title);
      setDescription(doc.description);
      setFolderId(doc.folder_id);
      setTagIds(doc.tags.map((t) => t.id));
    }
  }, [doc]);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Document>(`/api/documents/${id}`, {
        title,
        description,
        folder_id: folderId,
        tag_ids: tagIds,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/documents/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      navigate("/");
    },
  });

  if (!doc) return <div className="p-8 text-zinc-400">Loading…</div>;

  return (
    <div className="flex gap-6 p-6">
      <div className="flex-1">
        <div className="mb-3 flex items-center gap-3">
          <h2 className="flex-1 truncate text-lg font-semibold">{doc.title}</h2>
          <Badge variant={STATUS_VARIANTS[doc.status]}>{doc.status}</Badge>
        </div>
        {doc.status === "failed" && (
          <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            Processing failed: {doc.error_message}
          </div>
        )}
        <div className="mb-3 flex gap-2 border-b border-zinc-200">
          {(["preview", "text"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={
                tab === t ? "border-b-2 border-zinc-800 px-3 py-1 font-medium" : "px-3 py-1 text-zinc-500"
              }
            >
              {t === "preview" ? "Preview" : "Text"}
            </button>
          ))}
        </div>
        {tab === "preview" ? <Viewer doc={doc} /> : <TextView documentId={doc.id} />}
      </div>
      <aside className="w-72 space-y-3">
        <div>
          <Label htmlFor="d-title">Title</Label>
          <Input id="d-title" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="d-desc">Description</Label>
          <Textarea id="d-desc" rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="d-folder">Folder</Label>
          <Select
            id="d-folder"
            value={folderId ?? ""}
            onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">(root)</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label>Tags</Label>
          <div className="flex flex-wrap gap-2">
            {(tags ?? []).map((tag) => (
              <label key={tag.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={tagIds.includes(tag.id)}
                  onChange={(e) =>
                    setTagIds(e.target.checked ? [...tagIds, tag.id] : tagIds.filter((x) => x !== tag.id))
                  }
                />
                {tag.name}
              </label>
            ))}
          </div>
        </div>
        {doc.summary && (
          <div className="rounded border border-zinc-200 bg-zinc-50 p-2 text-xs text-zinc-600">{doc.summary}</div>
        )}
        <Button className="w-full" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        <Button
          variant="destructive"
          className="w-full"
          onClick={() => window.confirm(`Delete "${doc.title}"?`) && remove.mutate()}
        >
          Delete
        </Button>
      </aside>
    </div>
  );
}
```

In `frontend/src/App.tsx`: replace the `DocumentPage` placeholder with `import { DocumentPage } from "@/pages/DocumentPage";`.

- [ ] **Step 4: Run tests + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b`
Expected: all PASS.

```bash
git add frontend/src/lib/viewer.ts frontend/src/lib/viewer.test.ts frontend/src/pages/DocumentPage.tsx frontend/src/App.tsx
git commit -m "feat: document view with typed viewer, text tab, and metadata editing"
```

---

### Task 6: Scan wizard

**Files:**
- Create: `frontend/src/lib/scanWizard.ts`, `frontend/src/hooks/usePreviewImage.ts`, `frontend/src/pages/ScanPage.tsx`
- Modify: `frontend/src/App.tsx` (use real ScanPage)
- Test: `frontend/src/lib/scanWizard.test.ts`

**Interfaces:**
- Consumes: `api`, `getToken`, `ScanStatus`/`ScanPageInfo`/`Document` types, `useFolders`/`useTags`, UI primitives.
- Produces:
  - `lib/scanWizard.ts`:
    - `type ScanPhase = "setup" | "ready" | "scanning" | "compiling" | "done"`
    - `interface ScanState { phase: ScanPhase; sessionId: number | null; languages: string; pages: ScanPageInfo[]; error: { code: string; message: string } | null; document: Document | null }`
    - `initialScanState: ScanState` (phase `setup`, languages `ita+eng`)
    - `type ScanAction = {type:"SET_LANGUAGES";languages:string} | {type:"SESSION_STARTED";sessionId:number} | {type:"SCAN_STARTED"} | {type:"PAGE_SCANNED";page:ScanPageInfo} | {type:"SCAN_FAILED";code:string;message:string} | {type:"PAGE_DELETED";pageId:number} | {type:"PAGES_REORDERED";pages:ScanPageInfo[]} | {type:"COMPILE_STARTED"} | {type:"COMPILED";document:Document} | {type:"COMPILE_FAILED";code:string;message:string} | {type:"DISMISS_ERROR"} | {type:"RESET"}`
    - `scanWizardReducer(state, action): ScanState` — SCAN_STARTED only valid from `ready` (otherwise no-op); SCAN_FAILED returns to `ready` keeping pages; PAGE_DELETED renumbers remaining pages sequentially; COMPILE_FAILED returns to `ready`.
    - `SCANNER_MESSAGES: Record<string, string>` + `scannerMessage(code: string, fallback: string): string` — friendly copy for `scanner_offline`/`scanner_busy`/`scanner_jam`/`cover_open`/`scanner_timeout`.
  - `hooks/usePreviewImage.ts`: `usePreviewImage(pageId: number): string | null` — fetches `/api/scan/pages/{id}/preview` with the auth header, returns an object URL, revokes it on unmount.
  - `pages/ScanPage.tsx`: scanner status banner (query `["scan-status"]`, refetch 10 s), wizard UI (language select on setup; scan/retake/reorder/finish; thumbnails; compile form title/folder/tags; done state links to the document; cancel deletes the session and resets).

- [ ] **Step 1: Write failing reducer tests**

`frontend/src/lib/scanWizard.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  initialScanState,
  scannerMessage,
  scanWizardReducer,
  type ScanState,
} from "./scanWizard";
import type { Document } from "./types";

const page = (id: number, page_number: number) => ({ id, page_number });

function reduceAll(actions: Parameters<typeof scanWizardReducer>[1][], from = initialScanState): ScanState {
  return actions.reduce(scanWizardReducer, from);
}

describe("scanWizardReducer", () => {
  it("walks the happy path: setup → ready → scanning → ready → compiling → done", () => {
    let state = reduceAll([
      { type: "SET_LANGUAGES", languages: "ita" },
      { type: "SESSION_STARTED", sessionId: 5 },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.languages).toBe("ita");

    state = scanWizardReducer(state, { type: "SCAN_STARTED" });
    expect(state.phase).toBe("scanning");
    state = scanWizardReducer(state, { type: "PAGE_SCANNED", page: page(1, 1) });
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);

    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILED", document: { id: "d1" } as Document }], state);
    expect(state.phase).toBe("done");
    expect(state.document?.id).toBe("d1");
  });

  it("ignores SCAN_STARTED outside ready", () => {
    expect(scanWizardReducer(initialScanState, { type: "SCAN_STARTED" }).phase).toBe("setup");
  });

  it("scan failure keeps pages and returns to ready with the error", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "SCAN_STARTED" },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "SCAN_STARTED" },
      { type: "SCAN_FAILED", code: "scanner_jam", message: "jam" },
    ]);
    expect(state.phase).toBe("ready");
    expect(state.pages).toHaveLength(1);
    expect(state.error?.code).toBe("scanner_jam");
    state = scanWizardReducer(state, { type: "DISMISS_ERROR" });
    expect(state.error).toBeNull();
  });

  it("PAGE_DELETED renumbers the remaining pages", () => {
    const withPages = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(10, 1) },
      { type: "PAGE_SCANNED", page: page(11, 2) },
      { type: "PAGE_SCANNED", page: page(12, 3) },
    ]);
    const state = scanWizardReducer(withPages, { type: "PAGE_DELETED", pageId: 11 });
    expect(state.pages).toEqual([page(10, 1), page(12, 2)]);
  });

  it("PAGES_REORDERED replaces the list; COMPILE_FAILED returns to ready", () => {
    let state = reduceAll([
      { type: "SESSION_STARTED", sessionId: 1 },
      { type: "PAGE_SCANNED", page: page(1, 1) },
      { type: "PAGE_SCANNED", page: page(2, 2) },
      { type: "PAGES_REORDERED", pages: [page(2, 1), page(1, 2)] },
    ]);
    expect(state.pages[0].id).toBe(2);
    state = reduceAll([{ type: "COMPILE_STARTED" }, { type: "COMPILE_FAILED", code: "no_pages", message: "x" }], state);
    expect(state.phase).toBe("ready");
    expect(state.error?.code).toBe("no_pages");
  });

  it("RESET returns to the initial state", () => {
    const state = reduceAll([{ type: "SESSION_STARTED", sessionId: 1 }, { type: "RESET" }]);
    expect(state).toEqual(initialScanState);
  });
});

describe("scannerMessage", () => {
  it("maps known codes and falls back otherwise", () => {
    expect(scannerMessage("scanner_offline", "x")).toMatch(/power|USB/i);
    expect(scannerMessage("weird_code", "fallback text")).toBe("fallback text");
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL.

- [ ] **Step 2: Implement the reducer**

`frontend/src/lib/scanWizard.ts`:

```ts
import type { Document, ScanPageInfo } from "./types";

export type ScanPhase = "setup" | "ready" | "scanning" | "compiling" | "done";

export interface ScanState {
  phase: ScanPhase;
  sessionId: number | null;
  languages: string;
  pages: ScanPageInfo[];
  error: { code: string; message: string } | null;
  document: Document | null;
}

export const initialScanState: ScanState = {
  phase: "setup",
  sessionId: null,
  languages: "ita+eng",
  pages: [],
  error: null,
  document: null,
};

export type ScanAction =
  | { type: "SET_LANGUAGES"; languages: string }
  | { type: "SESSION_STARTED"; sessionId: number }
  | { type: "SCAN_STARTED" }
  | { type: "PAGE_SCANNED"; page: ScanPageInfo }
  | { type: "SCAN_FAILED"; code: string; message: string }
  | { type: "PAGE_DELETED"; pageId: number }
  | { type: "PAGES_REORDERED"; pages: ScanPageInfo[] }
  | { type: "COMPILE_STARTED" }
  | { type: "COMPILED"; document: Document }
  | { type: "COMPILE_FAILED"; code: string; message: string }
  | { type: "DISMISS_ERROR" }
  | { type: "RESET" };

export function scanWizardReducer(state: ScanState, action: ScanAction): ScanState {
  switch (action.type) {
    case "SET_LANGUAGES":
      return { ...state, languages: action.languages };
    case "SESSION_STARTED":
      return { ...state, phase: "ready", sessionId: action.sessionId, pages: [], error: null };
    case "SCAN_STARTED":
      return state.phase === "ready" ? { ...state, phase: "scanning", error: null } : state;
    case "PAGE_SCANNED":
      return { ...state, phase: "ready", pages: [...state.pages, action.page] };
    case "SCAN_FAILED":
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "PAGE_DELETED": {
      const remaining = state.pages.filter((p) => p.id !== action.pageId);
      return {
        ...state,
        pages: remaining.map((p, index) => ({ ...p, page_number: index + 1 })),
      };
    }
    case "PAGES_REORDERED":
      return { ...state, pages: action.pages };
    case "COMPILE_STARTED":
      return { ...state, phase: "compiling", error: null };
    case "COMPILED":
      return { ...state, phase: "done", document: action.document };
    case "COMPILE_FAILED":
      return { ...state, phase: "ready", error: { code: action.code, message: action.message } };
    case "DISMISS_ERROR":
      return { ...state, error: null };
    case "RESET":
      return initialScanState;
  }
}

export const SCANNER_MESSAGES: Record<string, string> = {
  scanner_offline: "Scanner not found — check power and USB connection.",
  scanner_busy: "The scanner is busy with another operation. Try again in a moment.",
  scanner_jam: "Paper jam detected — clear the scanner and retry.",
  cover_open: "The scanner cover is open — close it and retry.",
  scanner_timeout: "The scan timed out — try power-cycling the scanner.",
};

export function scannerMessage(code: string, fallback: string): string {
  return SCANNER_MESSAGES[code] ?? fallback;
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: Preview hook and ScanPage**

`frontend/src/hooks/usePreviewImage.ts`:

```ts
import { useEffect, useState } from "react";
import { getToken } from "@/lib/api";

export function usePreviewImage(pageId: number): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    fetch(`/api/scan/pages/${pageId}/preview`, {
      headers: { Authorization: `Bearer ${getToken() ?? ""}` },
    })
      .then((resp) => (resp.ok ? resp.blob() : Promise.reject(new Error("preview failed"))))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) setUrl(objectUrl);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [pageId]);

  return url;
}
```

`frontend/src/pages/ScanPage.tsx`:

```tsx
import { useReducer, useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { usePreviewImage } from "@/hooks/usePreviewImage";
import { api, ApiError } from "@/lib/api";
import {
  initialScanState,
  scannerMessage,
  scanWizardReducer,
} from "@/lib/scanWizard";
import type { Document, ScanPageInfo, ScanStatus } from "@/lib/types";

function Thumbnail({
  page,
  onDelete,
  onMove,
  isFirst,
  isLast,
}: {
  page: ScanPageInfo;
  onDelete: () => void;
  onMove: (direction: -1 | 1) => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const url = usePreviewImage(page.id);
  return (
    <div className="w-36 rounded border border-zinc-200 bg-white p-2">
      {url ? (
        <img src={url} alt={`Page ${page.page_number}`} className="h-40 w-full rounded object-cover" />
      ) : (
        <div className="flex h-40 items-center justify-center text-zinc-300">…</div>
      )}
      <div className="mt-1 flex items-center justify-between text-xs text-zinc-500">
        <span>p. {page.page_number}</span>
        <span className="flex gap-1">
          <button disabled={isFirst} onClick={() => onMove(-1)} title="Move up/left">
            ←
          </button>
          <button disabled={isLast} onClick={() => onMove(1)} title="Move down/right">
            →
          </button>
          <button onClick={onDelete} title="Delete page" className="text-red-500">
            ×
          </button>
        </span>
      </div>
    </div>
  );
}

export function ScanPage() {
  const [state, dispatch] = useReducer(scanWizardReducer, initialScanState);
  const { data: status } = useQuery({
    queryKey: ["scan-status"],
    queryFn: () => api.get<ScanStatus>("/api/scan/status"),
    refetchInterval: 10_000,
  });
  const { data: folders } = useFolders();
  const { data: tags } = useTags();
  const [compileOpen, setCompileOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagIds, setTagIds] = useState<number[]>([]);

  const fail = (kind: "SCAN_FAILED" | "COMPILE_FAILED") => (err: unknown) => {
    const code = err instanceof ApiError ? err.code : "unknown";
    const message = err instanceof ApiError ? err.message : "Unexpected error";
    dispatch({ type: kind, code, message });
  };

  const startSession = async () => {
    try {
      const session = await api.post<{ id: number }>("/api/scan/sessions", {
        ocr_languages: state.languages,
      });
      dispatch({ type: "SESSION_STARTED", sessionId: session.id });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const scanPage = async () => {
    dispatch({ type: "SCAN_STARTED" });
    try {
      const page = await api.post<ScanPageInfo & { preview_url: string }>(
        `/api/scan/sessions/${state.sessionId}/pages`,
        {},
      );
      dispatch({ type: "PAGE_SCANNED", page: { id: page.id, page_number: page.page_number } });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const deletePage = async (pageId: number) => {
    try {
      await api.del(`/api/scan/pages/${pageId}`);
      dispatch({ type: "PAGE_DELETED", pageId });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const movePage = async (index: number, direction: -1 | 1) => {
    const order = state.pages.map((p) => p.id);
    const target = index + direction;
    [order[index], order[target]] = [order[target], order[index]];
    try {
      const resp = await api.post<{ pages: ScanPageInfo[] }>(
        `/api/scan/sessions/${state.sessionId}/reorder`,
        { page_ids: order },
      );
      dispatch({ type: "PAGES_REORDERED", pages: resp.pages });
    } catch (err) {
      fail("SCAN_FAILED")(err);
    }
  };

  const compile = async () => {
    setCompileOpen(false);
    dispatch({ type: "COMPILE_STARTED" });
    try {
      const doc = await api.post<Document>(`/api/scan/sessions/${state.sessionId}/compile`, {
        title,
        folder_id: folderId,
        tag_ids: tagIds,
      });
      dispatch({ type: "COMPILED", document: doc });
    } catch (err) {
      fail("COMPILE_FAILED")(err);
    }
  };

  const cancel = async () => {
    if (state.sessionId !== null) await api.del(`/api/scan/sessions/${state.sessionId}`).catch(() => {});
    dispatch({ type: "RESET" });
  };

  return (
    <div className="p-6">
      <h2 className="mb-2 text-lg font-semibold">Scan</h2>
      {status && (
        <p className="mb-4 text-sm">
          Scanner:{" "}
          {status.available ? (
            <span className="text-green-700">available{status.busy ? " (busy)" : ""}</span>
          ) : (
            <span className="text-red-600">not detected — check power and USB</span>
          )}
        </p>
      )}
      {state.error && (
        <div className="mb-4 flex items-center justify-between rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <span>{scannerMessage(state.error.code, state.error.message)}</span>
          <button onClick={() => dispatch({ type: "DISMISS_ERROR" })}>×</button>
        </div>
      )}

      {state.phase === "setup" && (
        <div className="max-w-sm space-y-3">
          <div>
            <Label htmlFor="scan-lang">OCR language</Label>
            <Select
              id="scan-lang"
              value={state.languages}
              onChange={(e) => dispatch({ type: "SET_LANGUAGES", languages: e.target.value })}
            >
              <option value="ita+eng">Italian + English</option>
              <option value="ita">Italian</option>
              <option value="eng">English</option>
            </Select>
          </div>
          <Button onClick={startSession}>Start scan session</Button>
        </div>
      )}

      {(state.phase === "ready" || state.phase === "scanning" || state.phase === "compiling") && (
        <div>
          <div className="mb-4 flex flex-wrap gap-3">
            {state.pages.map((p, index) => (
              <Thumbnail
                key={p.id}
                page={p}
                isFirst={index === 0}
                isLast={index === state.pages.length - 1}
                onDelete={() => deletePage(p.id)}
                onMove={(direction) => movePage(index, direction)}
              />
            ))}
            {state.pages.length === 0 && <p className="text-zinc-400">No pages yet — scan the first one.</p>}
          </div>
          <div className="flex gap-2">
            <Button onClick={scanPage} disabled={state.phase !== "ready"}>
              {state.phase === "scanning" ? "Scanning…" : state.pages.length === 0 ? "Scan first page" : "Scan next page"}
            </Button>
            <Button
              variant="outline"
              disabled={state.pages.length === 0 || state.phase !== "ready"}
              onClick={() => {
                setTitle("");
                setCompileOpen(true);
              }}
            >
              Finish & compile
            </Button>
            <Button variant="ghost" onClick={cancel}>
              Cancel
            </Button>
          </div>
          {state.phase === "compiling" && <p className="mt-3 text-sm text-zinc-500">Compiling document…</p>}
        </div>
      )}

      {state.phase === "done" && state.document && (
        <div className="space-y-3">
          <p>
            Document created:{" "}
            <Link className="font-medium underline" to={`/documents/${state.document.id}`}>
              {state.document.title}
            </Link>{" "}
            (processing in the background)
          </p>
          <Button onClick={() => dispatch({ type: "RESET" })}>Scan another document</Button>
        </div>
      )}

      <Dialog open={compileOpen} onClose={() => setCompileOpen(false)} title="Compile document">
        <div className="space-y-3">
          <div>
            <Label htmlFor="c-title">Title</Label>
            <Input id="c-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="c-folder">Folder</Label>
            <Select
              id="c-folder"
              value={folderId ?? ""}
              onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">(root)</option>
              {(folders ?? []).map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Tags</Label>
            <div className="flex flex-wrap gap-2">
              {(tags ?? []).map((tag) => (
                <label key={tag.id} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={tagIds.includes(tag.id)}
                    onChange={(e) =>
                      setTagIds(e.target.checked ? [...tagIds, tag.id] : tagIds.filter((x) => x !== tag.id))
                    }
                  />
                  {tag.name}
                </label>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCompileOpen(false)}>
              Back
            </Button>
            <Button onClick={compile} disabled={!title.trim()}>
              Compile
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
```

In `frontend/src/App.tsx`: replace the `ScanPage` placeholder with `import { ScanPage } from "@/pages/ScanPage";`.

- [ ] **Step 4: Run tests + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b`
Expected: all PASS.

```bash
git add frontend/src/lib/scanWizard.ts frontend/src/lib/scanWizard.test.ts frontend/src/hooks/usePreviewImage.ts frontend/src/pages/ScanPage.tsx frontend/src/App.tsx
git commit -m "feat: scan wizard with tested state machine and page thumbnails"
```

---

### Task 7: Search page

**Files:**
- Create: `frontend/src/lib/snippets.ts`, `frontend/src/pages/SearchPage.tsx`
- Modify: `frontend/src/App.tsx` (use real SearchPage)
- Test: `frontend/src/lib/snippets.test.ts`, `frontend/src/pages/SearchPage.test.tsx`

**Interfaces:**
- Consumes: `api`, `SearchResponse`/`SearchResult` types, `useFolders`/`useTags`, `STATUS_VARIANTS`, UI primitives.
- Produces:
  - `lib/snippets.ts`: `interface SnippetPart { text: string; highlighted: boolean }`, `splitHighlights(text: string): SnippetPart[]` — parses `ts_headline`'s `<b>…</b>` markers into parts; no other HTML is interpreted (rendered as literal text). Rendering NEVER uses `dangerouslySetInnerHTML`.
  - `pages/SearchPage.tsx`: query input, mode select (`hybrid`/`semantic`/`keyword`), folder/tag/type filter selects, submit → `api.post<SearchResponse>("/api/search", {...})` via `useMutation`; results as document rows with `<mark>`-rendered snippets (+ page numbers) linking to `/documents/{id}`.

- [ ] **Step 1: Write failing tests**

`frontend/src/lib/snippets.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { splitHighlights } from "./snippets";

describe("splitHighlights", () => {
  it("passes plain text through", () => {
    expect(splitHighlights("nothing special")).toEqual([{ text: "nothing special", highlighted: false }]);
  });

  it("marks single and multiple highlights", () => {
    expect(splitHighlights("la <b>bolletta</b> della <b>luce</b>")).toEqual([
      { text: "la ", highlighted: false },
      { text: "bolletta", highlighted: true },
      { text: " della ", highlighted: false },
      { text: "luce", highlighted: true },
    ]);
  });

  it("does not interpret other HTML", () => {
    expect(splitHighlights("<script>x</script>")).toEqual([{ text: "<script>x</script>", highlighted: false }]);
  });

  it("drops empty segments", () => {
    expect(splitHighlights("<b>solo</b>")).toEqual([{ text: "solo", highlighted: true }]);
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `snippets.ts`**

`frontend/src/lib/snippets.ts`:

```ts
export interface SnippetPart {
  text: string;
  highlighted: boolean;
}

export function splitHighlights(text: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  const pattern = /<b>(.*?)<\/b>/gs;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index! > cursor) parts.push({ text: text.slice(cursor, match.index), highlighted: false });
    if (match[1]) parts.push({ text: match[1], highlighted: true });
    cursor = match.index! + match[0].length;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), highlighted: false });
  return parts;
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: Implement SearchPage**

`frontend/src/pages/SearchPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { STATUS_VARIANTS } from "@/components/DocumentCard";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { api } from "@/lib/api";
import { splitHighlights } from "@/lib/snippets";
import type { SearchResponse } from "@/lib/types";

function Snippet({ text }: { text: string }) {
  return (
    <>
      {splitHighlights(text).map((part, index) =>
        part.highlighted ? (
          <mark key={index} className="rounded bg-yellow-200 px-0.5">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagId, setTagId] = useState<number | null>(null);
  const [docType, setDocType] = useState<string | null>(null);
  const { data: folders } = useFolders();
  const { data: tags } = useTags();

  const search = useMutation({
    mutationFn: () =>
      api.post<SearchResponse>("/api/search", {
        query,
        mode,
        filters: {
          folder_id: folderId,
          tag_ids: tagId !== null ? [tagId] : [],
          doc_type: docType,
        },
        limit: 10,
      }),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim()) search.mutate();
  };

  return (
    <div className="p-6">
      <h2 className="mb-4 text-lg font-semibold">Search</h2>
      <form onSubmit={onSubmit} className="mb-6 flex flex-wrap items-center gap-2">
        <Input
          className="max-w-md"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search your documents…"
        />
        <Select className="w-32" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          <option value="hybrid">Hybrid</option>
          <option value="semantic">Semantic</option>
          <option value="keyword">Keyword</option>
        </Select>
        <Select
          className="w-36"
          value={folderId ?? ""}
          onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All folders</option>
          {(folders ?? []).map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </Select>
        <Select
          className="w-32"
          value={tagId ?? ""}
          onChange={(e) => setTagId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All tags</option>
          {(tags ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <Select className="w-28" value={docType ?? ""} onChange={(e) => setDocType(e.target.value || null)}>
          <option value="">All types</option>
          {["scan", "pdf", "text", "image", "video"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Button type="submit" disabled={search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      {search.data && search.data.results.length === 0 && <p className="text-zinc-400">No results.</p>}
      <div className="space-y-4">
        {search.data?.results.map((result) => (
          <div key={result.document.id} className="rounded-lg border border-zinc-200 bg-white p-4">
            <div className="mb-1 flex items-center gap-2">
              <Link to={`/documents/${result.document.id}`} className="font-medium underline">
                {result.document.title}
              </Link>
              <Badge variant={STATUS_VARIANTS[result.document.status]}>{result.document.doc_type}</Badge>
            </div>
            <ul className="space-y-1">
              {result.snippets.map((snippet) => (
                <li key={snippet.chunk_id} className="text-sm text-zinc-600">
                  {snippet.page_number != null && (
                    <span className="mr-1 text-xs text-zinc-400">p. {snippet.page_number}</span>
                  )}
                  <Snippet text={snippet.text} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
```

In `frontend/src/App.tsx`: replace the `SearchPage` placeholder with `import { SearchPage } from "@/pages/SearchPage";`.

- [ ] **Step 4: Write the search-flow component test (spec §8 requires Testing Library coverage of the search flow)**

`frontend/src/pages/SearchPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SearchPage } from "./SearchPage";

const fetchMock = vi.fn();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SearchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("submits a search and renders highlighted snippet results", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/folders" || url === "/api/tags") return json(200, []);
    if (url === "/api/search")
      return json(200, {
        mode: "hybrid",
        results: [
          {
            document: {
              id: "doc-1",
              title: "Bolletta marzo",
              status: "ready",
              doc_type: "pdf",
              tags: [],
            },
            score: 1,
            snippets: [
              { chunk_id: 9, page_number: 2, source: "content", text: "la <b>bolletta</b> di marzo", similarity: null },
            ],
          },
        ],
      });
    return json(404, { error: { code: "not_found", message: "no" } });
  });

  renderPage();
  await userEvent.type(screen.getByPlaceholderText(/search your documents/i), "bolletta");
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));

  expect(await screen.findByRole("link", { name: "Bolletta marzo" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByText("bolletta").tagName).toBe("MARK");
  expect(screen.getByText(/p\. 2/)).toBeInTheDocument();

  const searchCall = fetchMock.mock.calls.find(([url]) => url === "/api/search")!;
  expect(JSON.parse(searchCall[1].body).mode).toBe("hybrid");
});

it("shows the empty state when nothing matches", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/search") return json(200, { mode: "hybrid", results: [] });
    return json(200, []);
  });
  renderPage();
  await userEvent.type(screen.getByPlaceholderText(/search your documents/i), "niente");
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));
  expect(await screen.findByText(/no results/i)).toBeInTheDocument();
});
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 5: Run tests + typecheck, then commit**

Run: `cd frontend && npm test && npx tsc -b`
Expected: all PASS.

```bash
git add frontend/src/lib/snippets.ts frontend/src/lib/snippets.test.ts frontend/src/pages/SearchPage.tsx frontend/src/pages/SearchPage.test.tsx frontend/src/App.tsx
git commit -m "feat: search page with mode toggle, filters, and safe snippet highlighting"
```

---

### Task 8: Chat page

**Files:**
- Create: `frontend/src/lib/sse.ts`, `frontend/src/lib/citations.ts`, `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/App.tsx` (use real ChatPage)
- Test: `frontend/src/lib/sse.test.ts`, `frontend/src/lib/citations.test.ts`

**Interfaces:**
- Consumes: `getToken`, `ChatEvent`/`ChatSource` types, UI primitives.
- Produces:
  - `lib/sse.ts`: `parseSSEStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent>` — buffers bytes, splits on `\n\n`, parses `data: <json>` frames; tolerates frames split across chunks.
  - `lib/citations.ts`: `type CitationPart = { kind: "text"; text: string } | { kind: "citation"; n: number }`, `splitCitations(answer: string): CitationPart[]` — splits `[n]` markers out of answer text.
  - `pages/ChatPage.tsx`: question form; on submit streams `POST /api/chat` (fetch + `parseSSEStream`); renders: not-grounded banner ("Answer not based on your documents") when `meta.grounded === false`, streaming answer with citation superscripts linking to `#source-{n}`, sources panel (`[n] title (p. X)` → `/documents/{id}`), error event as an inline error box. Single-turn: a new question replaces the previous answer.

- [ ] **Step 1: Write failing tests**

`frontend/src/lib/sse.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parseSSEStream } from "./sse";
import type { ChatEvent } from "./types";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const event of parseSSEStream(stream)) events.push(event);
  return events;
}

describe("parseSSEStream", () => {
  it("parses a full event sequence", async () => {
    const events = await collect(
      streamOf(
        'data: {"type":"meta","grounded":true,"sources":[]}\n\n',
        'data: {"type":"delta","text":"Ciao"}\n\n',
        'data: {"type":"done"}\n\n',
      ),
    );
    expect(events.map((e) => e.type)).toEqual(["meta", "delta", "done"]);
  });

  it("handles frames split across chunks", async () => {
    const events = await collect(
      streamOf('data: {"type":"delta","te', 'xt":"spez', 'zato"}\n\ndata: {"type":"done"}\n\n'),
    );
    expect(events).toEqual([{ type: "delta", text: "spezzato" }, { type: "done" }]);
  });

  it("handles multiple frames in one chunk", async () => {
    const events = await collect(streamOf('data: {"type":"delta","text":"a"}\n\ndata: {"type":"delta","text":"b"}\n\n'));
    expect(events).toHaveLength(2);
  });
});
```

`frontend/src/lib/citations.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("passes plain text through", () => {
    expect(splitCitations("nessuna citazione")).toEqual([{ kind: "text", text: "nessuna citazione" }]);
  });

  it("extracts citation markers", () => {
    expect(splitCitations("La bolletta è di 42 euro [1] pagata a marzo [2].")).toEqual([
      { kind: "text", text: "La bolletta è di 42 euro " },
      { kind: "citation", n: 1 },
      { kind: "text", text: " pagata a marzo " },
      { kind: "citation", n: 2 },
      { kind: "text", text: "." },
    ]);
  });
});
```

Run: `cd frontend && npm test` — Expected: FAIL.

- [ ] **Step 2: Implement `sse.ts` and `citations.ts`**

`frontend/src/lib/sse.ts`:

```ts
import type { ChatEvent } from "./types";

export async function* parseSSEStream(stream: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 2);
        if (frame.startsWith("data: ")) yield JSON.parse(frame.slice(6)) as ChatEvent;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
```

`frontend/src/lib/citations.ts`:

```ts
export type CitationPart = { kind: "text"; text: string } | { kind: "citation"; n: number };

export function splitCitations(answer: string): CitationPart[] {
  const parts: CitationPart[] = [];
  const pattern = /\[(\d+)\]/g;
  let cursor = 0;
  for (const match of answer.matchAll(pattern)) {
    if (match.index! > cursor) parts.push({ kind: "text", text: answer.slice(cursor, match.index) });
    parts.push({ kind: "citation", n: Number(match[1]) });
    cursor = match.index! + match[0].length;
  }
  if (cursor < answer.length) parts.push({ kind: "text", text: answer.slice(cursor) });
  return parts;
}
```

Run: `cd frontend && npm test` — Expected: PASS.

- [ ] **Step 3: Implement ChatPage**

`frontend/src/pages/ChatPage.tsx`:

```tsx
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { getToken } from "@/lib/api";
import { splitCitations } from "@/lib/citations";
import { parseSSEStream } from "@/lib/sse";
import type { ChatSource } from "@/lib/types";

interface ChatResult {
  grounded: boolean | null;
  sources: ChatSource[];
  answer: string;
  error: string | null;
  streaming: boolean;
}

const emptyResult: ChatResult = { grounded: null, sources: [], answer: "", error: null, streaming: false };

function Answer({ text }: { text: string }) {
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed">
      {splitCitations(text).map((part, index) =>
        part.kind === "text" ? (
          <span key={index}>{part.text}</span>
        ) : (
          <a key={index} href={`#source-${part.n}`} className="align-super text-xs font-semibold text-blue-700">
            [{part.n}]
          </a>
        ),
      )}
    </p>
  );
}

export function ChatPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ChatResult>(emptyResult);

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim() || result.streaming) return;
    setResult({ ...emptyResult, streaming: true });
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${getToken() ?? ""}`,
        },
        body: JSON.stringify({ question }),
      });
      if (!resp.ok || !resp.body) {
        setResult((r) => ({ ...r, streaming: false, error: "Chat request failed" }));
        return;
      }
      for await (const event of parseSSEStream(resp.body)) {
        if (event.type === "meta") {
          setResult((r) => ({ ...r, grounded: event.grounded, sources: event.sources }));
        } else if (event.type === "delta") {
          setResult((r) => ({ ...r, answer: r.answer + event.text }));
        } else if (event.type === "error") {
          setResult((r) => ({ ...r, error: event.message, streaming: false }));
        } else if (event.type === "done") {
          setResult((r) => ({ ...r, streaming: false }));
        }
      }
      setResult((r) => ({ ...r, streaming: false }));
    } catch {
      setResult((r) => ({ ...r, streaming: false, error: "Connection lost mid-answer" }));
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h2 className="mb-4 text-lg font-semibold">Ask your documents</h2>
      <form onSubmit={ask} className="mb-6 space-y-2">
        <Textarea
          rows={2}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Quanto ho pagato la bolletta di marzo?"
        />
        <Button type="submit" disabled={result.streaming || !question.trim()}>
          {result.streaming ? "Answering…" : "Ask"}
        </Button>
      </form>

      {result.grounded === false && (
        <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          Answer not based on your documents.
        </div>
      )}
      {result.error && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{result.error}</div>
      )}
      {result.answer && (
        <div className="rounded-lg border border-zinc-200 bg-white p-4">
          <Answer text={result.answer} />
          {result.streaming && <span className="animate-pulse text-zinc-400">▍</span>}
        </div>
      )}
      {result.sources.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-semibold text-zinc-500">Sources</h3>
          <ol className="space-y-1 text-sm">
            {result.sources.map((source) => (
              <li key={source.n} id={`source-${source.n}`}>
                [{source.n}]{" "}
                <Link to={`/documents/${source.document_id}`} className="underline">
                  {source.title}
                </Link>
                {source.page_number != null && <span className="text-zinc-400"> (p. {source.page_number})</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
```

In `frontend/src/App.tsx`: replace the `ChatPage` placeholder with `import { ChatPage } from "@/pages/ChatPage";` (all placeholders are now gone).

- [ ] **Step 4: Run tests + typecheck + build, then commit**

Run: `cd frontend && npm test && npx tsc -b && npm run build`
Expected: all PASS, build succeeds.

```bash
git add frontend/src/lib/sse.ts frontend/src/lib/sse.test.ts frontend/src/lib/citations.ts frontend/src/lib/citations.test.ts frontend/src/pages/ChatPage.tsx frontend/src/App.tsx
git commit -m "feat: RAG chat page with SSE streaming, grounding banner, and citations"
```

---

### Task 9: Production static serving + docs

**Files:**
- Create: `backend/app/api/spa.py`
- Modify: `backend/app/main.py` (mount when `frontend/dist` exists)
- Modify: `README.md` (Frontend section)
- Test: `backend/tests/test_spa.py`

**Interfaces:**
- Consumes: FastAPI `StaticFiles`/`FileResponse`.
- Produces: `app.api.spa.register_spa(app: FastAPI, dist: Path) -> None` — mounts `dist/assets` at `/assets` and adds a catch-all GET route that serves real files from `dist` when they exist, `dist/index.html` otherwise (SPA fallback), and returns 404 for unknown `/api/...` paths (API routes are registered first, so only unknown API paths reach the catch-all). `main.py` calls it only when `frontend/dist/index.html` exists, AFTER all API routers.

- [ ] **Step 1: Write failing test**

`backend/tests/test_spa.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.spa import register_spa


def make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>origami spa</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (dist / "favicon.ico").write_bytes(b"icon")
    return dist


def test_spa_serves_index_and_assets(tmp_path):
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    register_spa(app, make_dist(tmp_path))
    client = TestClient(app)

    assert "origami spa" in client.get("/").text
    assert "origami spa" in client.get("/documents/abc").text  # SPA fallback
    assert client.get("/favicon.ico").content == b"icon"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/nope").status_code == 404
```

Run: `cd backend && uv run pytest tests/test_spa.py -v` — Expected: FAIL (ImportError).

- [ ] **Step 2: Implement**

`backend/app/api/spa.py`:

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def register_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built frontend. Call AFTER all API routers are registered."""
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="spa-assets")
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
```

Add to the END of `backend/app/main.py` (after all `include_router` calls):

```python
from pathlib import Path

from app.api.spa import register_spa

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    register_spa(app, FRONTEND_DIST)
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_spa.py -v`
Expected: PASS. Then the full backend suite: `cd backend && uv run pytest` — all pass, 0 warnings.

- [ ] **Step 4: Update README**

Add to `README.md` after the Backend section:

```markdown
### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server on :5173, proxies /api to :8000
npm test           # vitest
npm run build      # outputs frontend/dist
```

In production, build the frontend and run only the backend: FastAPI serves
`frontend/dist` automatically when it exists, so the Cloudflare tunnel needs
just the one backend port.
```

- [ ] **Step 5: End-to-end smoke, then commit**

Run: `cd frontend && npm run build && cd ../backend && uv run pytest tests/test_spa.py -v`
Expected: build succeeds; test passes. (Optionally start `uvicorn app.main:app` and confirm `/` serves the app.)

```bash
git add backend/app/api/spa.py backend/app/main.py backend/tests/test_spa.py README.md
git commit -m "feat: serve built frontend from FastAPI with SPA fallback"
```

---

## Phase 4 exit criteria

- Backend suite fully green, 0 warnings (including the new file/text/SSE-error/SPA tests).
- Frontend suite fully green (`npm test`), `npx tsc -b` clean, `npm run build` succeeds.
- Manual smoke (human): `npm run dev` + backend + worker running → login, create a folder, upload a PDF, watch the status badge flip to ready, open it (preview + text tab), scan a document if the scanner is attached, search for content, ask the chat a question and see cited sources / grounding banner.
- Production mode: `npm run build` then backend alone serves the whole app on one port.
