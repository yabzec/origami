# Origami — Chat Redesign: Conversation, Markdown, Document Preflight, File Picker

**Date:** 2026-10-03
**Status:** Approved by user (brainstorming session), pending written-spec review
**Build order:** third, after `2026-10-03-office-docs-folder-picker-design.md` and `2026-10-03-job-retries-notifications-design.md` (it touches `llm.py` and the `llm_stub` fixture, which both earlier plans change).

## 1. Overview

The chat becomes a real multi-turn conversation with markdown answers. The documents it may draw from are chosen explicitly:

- **Preflight.** A cheap local search shortlists up to 30 candidate documents. A short LLM call then picks which of them the user means. Passages are retrieved **only** from the chosen documents.
- **User control.** The files in context show as chips. The user can remove any chip and can add files with a search picker.

Goals agreed with the user: relevance, privacy (send the LLM only content from clearly related documents), and token cost that stays flat as the archive grows.

## 2. Current state (verified)

- `rag.retrieve` fuses semantic and keyword chunk search over the **whole** archive and sends the top `rag_top_k` (8) chunks of about 1,000 characters each. Whole files are never sent.
- Single-turn: `POST /api/chat {question}`, with no history.
- `ChatPage.tsx` renders plain text. `citations.ts` already maps `[n]` to sources.
- SSE events: `meta {grounded, sources}`, `delta {text}`, `done`, `error`.

## 3. Backend

### 3.1 Document shortlist — `app/services/chat_context.py`
`shortlist_documents(session, query: str, limit: int = 30) -> list[Candidate]`
- No LLM call except one embedding of `query`.
- Semantic: pgvector cosine search over chunks whose `source in (summary, metadata)`. Keyword: the existing keyword search over the same sources. Both run with a pool of 60 chunks.
- Fuse with `rrf_fuse` (existing). Group by document, keeping the best rank per document. Take the first `limit` documents.
- `Candidate = {id: str, title: str, document_date: str (ISO), doc_type: str, summary_line: str}`, where `summary_line` is the first 200 characters of `summary` or `description`, flattened to one line.
- `query` is built by the endpoint: the last user message, plus the previous user message when present, joined with a newline. That lets follow-ups ("and the one from March?") match.
- Implementation detail: extend `semantic_search` / `keyword_search` in `services/search.py` with an optional `sources: list[str] | None` filter, and with `document_ids: list[uuid] | None` (used in 3.3). Defaults keep today's behaviour.

### 3.2 Preflight — `llm.select_documents`
`select_documents(question: str, history: list[dict], candidates: list[dict]) -> list[str]`
- Runs only when `candidates` is non-empty.
- Prompt (text model, `LLM_MODEL`): a numbered candidate list (`id | title | date | type | summary_line`), the last 4 history messages, and the question. Instruction: return ONLY JSON `{"document_ids": [...]}` with the ids of documents the user is asking about or that are needed to answer; return `[]` if none apply.
- Parsing: extract the first `{...}` block, `json.loads`, keep string ids that are in the candidate set (order preserved, de-duplicated), and cap at 10. Any parse failure or exception → `[]`, logged. The chat must never fail because of the preflight.

### 3.3 Retrieval restricted to context — `rag.retrieve`
`retrieve(session, query, document_ids: list[uuid]) -> (sources, grounded)`
- Same hybrid chunk search, filtered to `document_ids` (all chunk sources), top `rag_top_k`.
- Empty `document_ids` → `([], False)` with no search.
- `grounded` is computed as today: best similarity ≥ `rag_relevance_floor`.

### 3.4 Chat endpoint — `POST /api/chat`
Body:

```json
{
  "messages": [{"role": "user" | "assistant", "content": "..."}],
  "pinned_ids": ["<uuid>", "..."],
  "excluded_ids": ["<uuid>", "..."]
}
```

- `messages` has at least 1 entry and the last one must be `role=user` (else 422 `validation_error`). At most 40 entries are accepted (422 beyond that).
- History trimming: keep the last 12 messages before the final user message, then drop the oldest until their total content is ≤ 6,000 characters. The final user message is always kept.
- Flow:
  1. `candidates = shortlist_documents(query)`, excluding `excluded_ids` and `pinned_ids` (pinned ones are already in).
  2. `auto_ids = select_documents(question, trimmed_history, candidates)`.
  3. `context_ids = pinned_ids ∪ auto_ids` (pinned first, order preserved). Unknown or deleted pinned ids are ignored silently.
  4. `sources, grounded = retrieve(query_for_chunks=question, document_ids=context_ids)`.
  5. Messages for the LLM: system prompt, then the trimmed history (as role messages), then the final user message with the sources block prepended (same format as today).
- SSE `meta` event: `{grounded, sources, auto_documents: [{id, title, document_date}]}` (auto picks only; the client already knows its pinned files). Then `delta`*, `done`. Errors keep the existing `error` event.
- System prompt update: format answers in **Markdown** (lists, tables, bold where useful); cite sources inline as `[n]`; answer in the user's language; say so explicitly when the sources do not contain the answer; use the conversation history for follow-up questions.

### 3.5 Picker search
The file picker reuses `GET /api/search?q=<text>` (existing; returns documents with titles). No new endpoint.

## 4. Frontend

### 4.1 State — `src/lib/chatReducer.ts` (pure)
State:

```ts
{
  messages: {id, role: "user" | "assistant", content, sources?, grounded?, error?}[];
  streaming: boolean;
  pinned: DocRef[];
  auto: DocRef[];
  excluded: string[];
}
```

where `DocRef = {id, title, document_date}`.

Actions:
- `SEND {text}`: appends the user message and an empty assistant message, sets `streaming`.
- `META {sources, grounded, autoDocuments}`: sets them on the last assistant message and merges `autoDocuments` into `auto` (skipping ids that are pinned or excluded).
- `DELTA {text}`
- `DONE`
- `ERROR {message}`
- `ABORT`: ends streaming and keeps the partial text.
- `PIN {doc}`: adds to pinned, removes from auto and excluded.
- `REMOVE {id}`: removes from pinned or auto. An auto removal also adds the id to `excluded`.
- `NEW_CHAT`: back to the initial state.

### 4.2 Components — `src/components/chat/`
- `MessageList`: user bubbles right-aligned, assistant left-aligned. Auto-scrolls to the bottom on new content unless the user scrolled up. A typing indicator shows while streaming and the assistant text is still empty. Under each assistant message: a sources list (title, page, link) and, when `grounded === false`, the existing "not found in your documents" banner.
- `Markdown`: `react-markdown` + `remark-gfm`, with raw HTML disabled (the default). Links open in the same app for internal paths. `[n]` citations are turned into links to `/documents/<id>` using the message's sources (the existing `citations.ts` logic adapted to produce markdown links before rendering).
- `ChatInput`: auto-growing textarea (max 8 rows). Enter sends, Shift+Enter adds a newline. Disabled while streaming. A **Stop** button aborts the fetch through `AbortController` and dispatches `ABORT`.
- `ContextBar`: chips for `pinned` (normal) and `auto` (with an "auto" badge), each showing title + date and an ×. The "+ Add file" button opens `FilePicker`.
- `FilePicker`: a popover with a search input debounced at 300 ms (pure helper `debounce` or a `useDebouncedValue` hook). It calls `/api/search?q=` and lists up to 10 documents (type icon if available, title, date). Clicking pins the document and closes. Esc or a click outside closes.
- Header: a "New chat" button (asks for confirmation if there are messages).
- `ChatPage.tsx` wires the reducer, the SSE stream (existing `sse.ts`), and the request body: the last 40 messages as `{role, content}`, `pinned_ids` and `excluded_ids`. Assistant messages with an error are not sent.
- New dependencies: `react-markdown`, `remark-gfm`.

## 5. Testing
- **Backend** (real Postgres; `llm.py` mocked; `llm_stub` gains `select_documents`, which records its arguments and returns scripted ids):
  - `shortlist_documents`: ranks documents by summary/metadata match, caps at `limit`, one entry per document, and the summary line is ≤ 200 characters.
  - `select_documents`: valid JSON → candidate ids only, de-duplicated and capped at 10. Fenced JSON or prose-wrapped JSON is parsed. Junk or an exception → `[]` (monkeypatch `litellm.completion`, as in the existing tests).
  - `retrieve` with `document_ids`: only chunks of those documents. Empty ids → no sources, not grounded.
  - Endpoint:
    - pinned + auto − excluded → context
    - retrieval restricted (a seeded document outside the context never appears in `sources`)
    - history trimmed to the limits
    - `meta.auto_documents` present
    - last message not from the user → 422
    - preflight failure still streams an answer.
- **Frontend** (vitest):
  - `chatReducer`, all actions, including that `REMOVE` of an auto chip adds the id to `excluded` and that a later `META` never re-adds an excluded id.
  - Citation-to-markdown transform.
  - Debounce helper.
  - Request-body builder (last 40 messages, errored assistant messages skipped).
- **Manual:** a follow-up question resolves through history; pin a file and ask about it; remove an auto chip and see it stay out; markdown tables and lists render; Stop aborts mid-answer.

## 6. Out of scope
- Saving conversations (decided: later, maybe, after real use).
- Per-message context editing (the selection applies to the whole conversation).
- Token usage display.

## 7. Decisions log
| Decision | Choice | Why |
|---|---|---|
| Preflight scope | Two-stage: local shortlist (30) → LLM pick | Flat token cost as the archive grows; AI judgment on which file is meant |
| Retrieval | Only inside chosen + pinned documents | Relevance and privacy |
| Context editing | Chips; remove auto picks (sticky exclusion) and pin via search | User control requested |
| Persistence | Session only (browser memory) | User: decide later after real use |
| History sent | Last 12 messages, ≤ 6,000 characters | Bounded tokens; enough for follow-ups |
| Preflight failure | Treated as no auto picks; chat continues | Chat must not fail on a helper call |
| Markdown | react-markdown + remark-gfm, no raw HTML | Safe rendering of LLM output |
