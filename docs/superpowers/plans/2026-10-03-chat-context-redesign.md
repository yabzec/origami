# Chat Redesign — Conversation, Markdown, Document Preflight, File Picker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the chat into a multi-turn conversation with Markdown answers whose passages come **only** from documents chosen explicitly: a local shortlist (30 documents, no LLM call except one embedding), a short LLM preflight that picks the documents the user means, plus files the user pins with a search picker. Auto picks show as removable chips; a removed chip stays excluded for the rest of the conversation.

**Architecture:** Backend: `services/search.py` gains a `sources` chunk filter. The new `services/chat_context.py::shortlist_documents` fuses semantic and keyword search over summary/metadata chunks into document candidates. `services/llm.py::select_documents` is the preflight, and it never raises. `services/rag.py::retrieve` searches only inside the given document ids. The new `rag.stream_chat` orchestrates the flow: history trim, shortlist, preflight, pinned ∪ auto context, restricted retrieval, LLM stream. `POST /api/chat` takes `{messages, pinned_ids, excluded_ids}` and adds `auto_documents` to the SSE `meta` event. Frontend: a pure reducer (`lib/chatReducer.ts`) holds the conversation and the context chips. A pure request builder (`lib/chatRequest.ts`) builds the request body. `components/chat/*` render it: Markdown via `react-markdown` + `remark-gfm`, with `[n]` turned into document links before rendering. `ChatPage.tsx` wires the reducer, the existing SSE parser and an `AbortController`.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Postgres + pgvector, litellm, pytest on real Postgres; React 19, TypeScript 6, Vite 8, TanStack Query 5, react-router 8, vitest 4, oxlint, `react-markdown` 10, `remark-gfm` 4.

**Spec:** `docs/superpowers/specs/2026-10-03-chat-context-redesign-design.md`

## Global Constraints

- **Build order:** third, after `2026-10-03-office-docs-folder-picker.md` and `2026-10-03-job-retries-notifications.md`. Both are merged on `main` (`llm.py` already has vision credentials and `list_models`). Where a step edits an existing function, read the current function first and merge the shown change into it. Never paste over code that this plan does not show in full.
- **Baseline on `main`:** backend **272 passed** (no warnings), frontend **89 passed**. Lint has 3 pre-existing `react/only-export-components` warnings and no new ones may appear. `npm run build` runs `tsc -b` with `noUnusedLocals` / `noUnusedParameters`.
- **Commands:** backend `cd /opt/origami/backend && uv run pytest -q`. Frontend `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`. Every task ends with the suite of the side it touched green, at the count stated in the task.
- **Mock boundaries (backend):** only `app/services/llm.py` may be mocked. That means the `llm_stub` fixture, the names it imports into other modules (`rag.llm_embed`, `rag.llm_complete`, `rag.llm_select_documents`, `chat_context.llm_embed`), and `litellm.completion` monkeypatched for `llm.py`'s own tests. `smtplib.SMTP` is mocked too (autouse `smtp_stub`). Everything else runs for real on Postgres (`origami_test` is recreated by `conftest.py`, which also blanks `Settings.model_config["env_file"]`).
- **No Alembic migration.** No table or column changes: `chunks.source` and the generated `chunks.content_tsv` already exist, and conversations are not persisted (spec §6).
- **No new environment settings.** All limits are module constants with these exact values:
  - Shortlist: `SHORTLIST_LIMIT = 30`, `SHORTLIST_POOL = 60` chunks per search, `SHORTLIST_SOURCES = ["summary", "metadata"]`, `SUMMARY_LINE_CHARS = 200`.
  - Preflight: `SELECT_HISTORY_MESSAGES = 4`, `SELECT_MAX_DOCUMENTS = 10`.
  - History: `HISTORY_MAX_MESSAGES = 12`, `HISTORY_MAX_CHARS = 6000`; endpoint accepts `MAX_MESSAGES = 40`.
  - Query truncation (this plan's own rule, see the ambiguities section): `QUERY_MAX_CHARS = 2000`.
  - Frontend: `MAX_REQUEST_MESSAGES = 40`, picker debounce `300` ms, picker limit `10`, textarea max 8 rows.
- **SSE contract:** `meta {grounded, sources, auto_documents: [{id, title, document_date}]}`, then `delta {text}`*, then `done`. Failures send the existing `error {code: "chat_failed", message: "Answer generation failed"}`. Payload dicts never contain a `type` key.
- **New npm dependencies:** `react-markdown` and `remark-gfm`. Checked on `main`: neither is in `frontend/package.json` nor in `frontend/node_modules`. Registry latest is `react-markdown@10.1.0` and `remark-gfm@4.0.1`. Install with `cd /opt/origami/frontend && npm install react-markdown remark-gfm` (Task 6) and commit `package.json` and `package-lock.json`.
- **Pure helpers live in `src/lib/` (or hooks in `src/hooks/`), never exported from `.tsx` files.** That way oxlint's `react/only-export-components` adds no warnings. `.tsx` files under `src/components/chat/` export exactly one component each.
- **UI copy (verbatim):**
  - Banner: `Answer not based on your documents.` (the existing copy).
  - Errors: `Chat request failed`, `Connection lost mid-answer`.
  - Buttons: `New chat`, `Send`, `Stop`, `+ Add file`.
  - Chip close button label: `Remove <title>`.
  - Badge: `auto`.
  - Confirmation: `Start a new chat? The current conversation will be cleared.`
  - Picker texts: `Type to search your documents`, `Searching…`, `Search failed`, `No documents found`.
  - Empty assistant message: `No answer.`
  - Typing indicator: `role="status"` named `Assistant is typing`.
- **Git:** never stage `deploy/origami.service`, `deploy/origami.sh`, `.env` or `.env.example.save` (the user's uncommitted work). Always `git add` explicit paths. Commit messages follow Conventional Commits and end with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Spec ambiguities resolved here (flagged for review)

1. **Picker endpoint.** The spec says `GET /api/search?q=<text>`, but the existing endpoint is `POST /api/search` with body `{query, mode, filters, limit}` (`app/api/search.py`). The picker posts `{"query": <text>, "mode": "hybrid", "limit": 10}`. There is still no new endpoint. Hybrid costs one embedding per debounced query, and keyword-only search would miss partial words and fuzzy titles.
2. **`document_ids` filter on search.** `semantic_search` / `keyword_search` already take `doc_ids: list[uuid.UUID] | None` (None = unfiltered). The plan reuses it instead of adding a second `document_ids` parameter. Only `sources: list[str] | None = None` is new.
3. **Exclusions are applied before the 30-document cut.** `shortlist_documents(session, query, limit=30, *, exclude_ids=())` gets a keyword-only `exclude_ids`, so excluded and pinned documents do not use up shortlist slots. The endpoint passes pinned ∪ excluded.
4. **`retrieve` signature in two steps.** Task 3 adds `document_ids: list[uuid.UUID] | None = None`, where None means the whole archive. This keeps the old single-turn endpoint green for one task. Task 4 makes the parameter required, deletes `stream_answer` (replaced by `stream_chat`) and updates the existing `test_rag.py` calls.
5. **Query length cap (spec silent).** Shortlist and retrieval queries are cut to `QUERY_MAX_CHARS = 2000` characters before embedding and keyword search. A pasted 20,000-character message would otherwise exceed the embedding model's input limit and fail the whole chat. The full text is still sent to the answer LLM.
6. **Message validation.** Every message needs `content` with `min_length=1` (422 otherwise). For that reason the request builder skips assistant messages with an empty text (Stop pressed before the first token) as well as those with an error.
7. **Id types.** `pinned_ids` / `excluded_ids` are `list[uuid.UUID]`. A malformed id gives 422. A well-formed id of a deleted or unknown document is ignored silently (spec 3.4.3), and stream_chat filters pinned ids to existing documents.
8. **Preflight id matching.** Returned ids are compared after `strip().lower()` (candidate ids are lowercase `str(uuid)`). "First `{...}` block" means the non-greedy regex `\{.*?\}` (DOTALL). As defense in depth, `stream_chat` also keeps only candidate ids, de-duplicates them, and catches any exception from the preflight.
9. **`llm_stub` gains `select_documents` in Task 4, not Task 2.** The stub must patch the name where the chat flow imports it (`app.services.rag.llm_select_documents`). That name only exists once Task 4 adds the import, and patching a missing attribute would break every `llm_stub` test. The stub does not stub embed/complete for the chat. Chat tests keep a `chat_llm` fixture with basis-vector embeddings so that grounding is controllable.
10. **Reducer actions carry message ids.**
    - `SEND {text, userId, assistantId}`; `META`, `DELTA`, `DONE` and `ERROR` carry `messageId`. They apply only while streaming and only to the last assistant message with that id, so stale events from a stopped stream cannot leak into the next reply.
    - Ids come from a counter, `nextMessageId()`, not from `crypto.randomUUID()`, which is missing on plain-HTTP LAN origins.
11. **Request builder signature.** It is `buildChatRequest(state, text)` and appends the new user message itself, because `dispatch` does not update `state` synchronously.
12. **Shortlist query order.** Chronological: `"<previous user message>\n<last user message>"`.
13. **History trimming edge.** A history message longer than 6,000 characters is dropped by the drop-oldest rule. The final user message is never trimmed or truncated in the answer prompt.
14. **`auto_documents` titles and dates** come from the shortlist candidates (`document_date` in ISO `YYYY-MM-DD`). The shortlist does not filter by document status, which matches `/api/search`.
15. **Banner copy.** The spec calls it the existing "not found in your documents" banner. The existing copy, `Answer not based on your documents.`, is kept.
16. **`splitCitations` is removed** in Task 8, together with its 2 tests. Its only caller was the old `ChatPage`, and `citationsToMarkdown` replaces it. `[n]` with no matching source stays plain text. `[n](…)` links, `[n]:` definitions and code spans or fences are left untouched.
17. **ChatInput while streaming.** The textarea is disabled as the spec says, the Send button is replaced by Stop, and focus returns to the textarea when streaming ends. An assistant message that ends empty with no error (stopped before any text) renders `No answer.`.

## Review Focus

1. **The preflight LLM returns ids outside the shortlist**: hallucinated UUIDs, `"not-a-uuid"`, duplicates, an excluded document, or ids in upper case with spaces. Expected: only shortlist candidates are used, each once, at most 10, and the chat does not crash. Tests: Task 2 (`test_select_documents_keeps_only_candidate_ids_once_in_order`) and Task 4 (`test_chat_drops_preflight_ids_outside_shortlist`, `test_chat_context_is_pinned_plus_auto_minus_excluded`).
2. **Stop mid-answer, then ask again right away.** Expected: the partial text stays, the next reply streams into a new bubble, no late delta of the stopped stream is appended to it, and the partial answer is sent as history. Tests: Task 5 (`ignores events for a stale message id after Stop and a new send`) and Task 8 (`Stop keeps the partial answer and the next question gets its own reply`).
3. **A pinned file is deleted in another tab, then the user asks again.** The client still sends its id. Expected: the id is ignored, the answer streams, and there is no 500 or error event. Test: Task 4 (`test_chat_unknown_pinned_id_ignored`).
4. **A very long pasted message** (for example a 27,000-character email body). Expected: the chat still answers. Embedding and keyword queries are cut to 2,000 characters, so the embedding provider does not reject the input. Tests: Task 1 (`test_shortlist_truncates_long_query`) and Task 3 (`test_retrieve_truncates_long_query`).
5. **LLM output with raw HTML or `javascript:` links** (`<img onerror=…>`, `<script>`, `[clic](javascript:alert(1))`). Expected: no HTML element is created, unsafe links become plain text, and external links open in a new tab with `rel="noopener noreferrer"`. Tests: Task 6 (`never renders raw HTML from the model`, `neutralises javascript: links and opens external links in a new tab`).

Also covered (spec-implied, outside the top five):
- All candidates excluded: Task 4, `test_chat_skips_preflight_when_all_candidates_excluded`.
- `[n]` beyond the number of sources: Task 6, `leaves [n] without a matching source as plain text`.
- Adjacent `[1][2]`: Task 6.

---

## File Structure

Backend
- `backend/app/services/search.py`: `QUERY_MAX_CHARS`, plus a `sources` filter on `semantic_search` / `keyword_search` (Task 1).
- `backend/app/services/chat_context.py` (new): `Candidate`, `summary_line`, `shortlist_documents` (Task 1).
- `backend/app/services/llm.py`: `parse_document_ids`, `select_documents` (Task 2).
- `backend/app/services/rag.py`: `retrieve` restricted to document ids (Task 3). Then `SYSTEM_PROMPT`, `trim_history`, `shortlist_query`, `build_messages(history)` and `stream_chat`, with `stream_answer` removed (Task 4).
- `backend/app/api/chat.py`: new request body and validation (Task 4).
- Tests:
  - `backend/tests/test_search_core.py` (Task 1).
  - `backend/tests/test_chat_context.py`, new (Task 1).
  - `backend/tests/test_llm.py` (Task 2).
  - `backend/tests/test_rag.py` (Tasks 3, 4).
  - `backend/tests/test_chat_api.py` (Task 4).
  - `backend/tests/conftest.py`, the `llm_stub` fixture (Task 4).

Frontend
- `frontend/src/lib/types.ts`: `DocRef`, and `auto_documents` on the `meta` `ChatEvent` (Task 5).
- `frontend/src/lib/chatReducer.ts` (+ test): state, actions, `nextMessageId` (Task 5).
- `frontend/src/lib/chatRequest.ts` (+ test): `buildChatRequest` (Task 5).
- `frontend/src/hooks/useDebouncedValue.ts` (+ test) (Task 5).
- `frontend/src/lib/citations.ts` (+ test): `citationsToMarkdown`, `isInternalHref`, `isCitationLabel` (Task 6); `splitCitations` removed (Task 8).
- `frontend/src/components/chat/Markdown.tsx` (+ test) (Task 6).
- `frontend/package.json`, `frontend/package-lock.json`: the new dependencies (Task 6).
- `frontend/src/lib/scroll.ts` (+ test): `isNearBottom` (Task 7).
- `frontend/src/components/chat/MessageList.tsx`, `ChatInput.tsx`, `ContextBar.tsx`, `FilePicker.tsx` (+ tests) (Task 7).
- `frontend/src/pages/ChatPage.tsx` (rewritten) + `ChatPage.test.tsx`, new (Task 8).

---

### Task 1: Search `sources` filter and document shortlist

**Files:**
- Modify: `backend/app/services/search.py` (`RRF_K` block at the top, `semantic_search`, `keyword_search`)
- Create: `backend/app/services/chat_context.py`
- Test: `backend/tests/test_search_core.py`, `backend/tests/test_chat_context.py` (new)

**Interfaces:**
- Consumes: existing `semantic_search`, `keyword_search`, `rrf_fuse` (`app/services/search.py`), `llm.embed`, `tests.helpers.seed_document(session, title, chunk_specs, **doc_kwargs)` and `basis_vector(index)`.
- Produces:
  - `app.services.search.QUERY_MAX_CHARS: int = 2000`.
  - `semantic_search(session, query_vector, limit=20, doc_ids=None, sources: list[str] | None = None) -> list[SemanticHit]`.
  - `keyword_search(session, query, limit=20, doc_ids=None, sources: list[str] | None = None) -> list[KeywordHit]`.
  - `app.services.chat_context.Candidate` (TypedDict `{id: str, title: str, document_date: str, doc_type: str, summary_line: str}`).
  - `summary_line(doc: Document) -> str`.
  - `shortlist_documents(session, query: str, limit: int = 30, *, exclude_ids: Iterable[uuid.UUID] = ()) -> list[Candidate]`.
  - Constants `SHORTLIST_LIMIT = 30`, `SHORTLIST_POOL = 60`, `SHORTLIST_SOURCES`, `SUMMARY_LINE_CHARS = 200`.
  - `chat_context.llm_embed`, the name tests patch.

- [ ] **Step 0: Confirm the baseline**

Run: `cd /opt/origami/backend && uv run pytest -q` → `272 passed`. Run: `cd /opt/origami/frontend && npx vitest run` → `89 passed`. Run `git status` → only `deploy/origami.service`, `deploy/origami.sh` (modified) and `.env.example.save` (untracked). If a count differs, stop and report it.

- [ ] **Step 1: Write the failing search-filter tests**

In `backend/tests/test_search_core.py`, change the imports at the top:

```python
import pytest
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocType, DocumentTag, Tag
```

(the `from app.services.search import (...)` block and the `tests.helpers` import stay as they are). Append:

```python
def test_semantic_search_sources_filter(session):
    doc = seed_document(
        session, "A",
        [
            {"content": "corpo", "embedding": basis_vector(0)},
            {"content": "riassunto", "embedding": basis_vector(0), "source": ChunkSource.summary},
            {"content": "A", "embedding": basis_vector(0), "source": ChunkSource.metadata},
        ],
    )
    content_chunk = session.exec(
        select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
    ).one()

    hits = semantic_search(
        session, basis_vector(0), sources=[ChunkSource.summary.value, ChunkSource.metadata.value]
    )
    assert len(hits) == 2
    assert content_chunk.id not in {h.chunk_id for h in hits}
    assert len(semantic_search(session, basis_vector(0))) == 3  # default: every source


def test_keyword_search_sources_filter(session):
    seed_document(
        session, "Bolletta",
        [
            {"content": "bolletta nel testo"},
            {"content": "bolletta nel riassunto", "source": ChunkSource.summary},
        ],
    )
    hits = keyword_search(session, "bolletta", sources=[ChunkSource.summary.value])
    assert len(hits) == 1
    assert "riassunto" in hits[0].snippet
    assert len(keyword_search(session, "bolletta")) == 2  # default: every source
```

- [ ] **Step 2: Write the failing shortlist tests**

Create `backend/tests/test_chat_context.py`:

```python
import pytest

from app.models import ChunkSource
from app.services import chat_context
from app.services.chat_context import shortlist_documents
from app.services.search import QUERY_MAX_CHARS
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def embed_calls(monkeypatch):
    """Query embedding = basis_vector(0)."""
    calls = []

    def fake_embed(texts):
        calls.append(list(texts))
        return [basis_vector(0) for _ in texts]

    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
    return calls


def summary_doc(session, title, vector, summary=None, **kwargs):
    text = summary or f"Riassunto di {title}"
    return seed_document(
        session, title,
        [{"content": text, "embedding": vector, "source": ChunkSource.summary}],
        summary=text, **kwargs,
    )


def test_shortlist_ranks_documents_by_summary_and_metadata(session, embed_calls):
    best = summary_doc(session, "Bolletta luce", basis_vector(0), "Bolletta della luce di marzo")
    second = seed_document(
        session, "Contratto",
        [{"content": "Contratto affitto", "embedding": basis_vector(1), "source": ChunkSource.metadata}],
    )
    content_only = seed_document(
        session, "Solo contenuto", [{"content": "bolletta bolletta", "embedding": basis_vector(0)}]
    )

    result = shortlist_documents(session, "bolletta")

    assert [c["id"] for c in result] == [str(best.id), str(second.id)]
    assert str(content_only.id) not in {c["id"] for c in result}
    assert result[0] == {
        "id": str(best.id),
        "title": "Bolletta luce",
        "document_date": best.document_date.isoformat(),
        "doc_type": "text",
        "summary_line": "Bolletta della luce di marzo",
    }
    assert embed_calls == [["bolletta"]]


def test_shortlist_one_entry_per_document_and_limit(session, embed_calls):
    doc = seed_document(
        session, "Doppio",
        [
            {"content": "riassunto doppio", "embedding": basis_vector(0), "source": ChunkSource.summary},
            {"content": "Doppio riassunto", "embedding": basis_vector(0), "source": ChunkSource.metadata},
        ],
    )
    for i in range(4):
        summary_doc(session, f"Doc {i}", basis_vector(i + 1))

    ids = [c["id"] for c in shortlist_documents(session, "riassunto")]
    assert ids[0] == str(doc.id)
    assert len(ids) == len(set(ids)) == 5
    assert len(shortlist_documents(session, "riassunto", limit=3)) == 3


def test_shortlist_summary_line_is_flat_and_capped(session, embed_calls):
    summary_doc(session, "Lungo", basis_vector(0), "Prima riga.\n\nSeconda   riga " + "x" * 400)
    [candidate] = shortlist_documents(session, "lungo")
    assert candidate["summary_line"].startswith("Prima riga. Seconda riga x")
    assert len(candidate["summary_line"]) == 200
    assert "\n" not in candidate["summary_line"]


def test_shortlist_falls_back_to_description(session, embed_calls):
    seed_document(
        session, "Manuale",
        [{"content": "Manuale\n\nIstruzioni lavatrice", "embedding": basis_vector(0),
          "source": ChunkSource.metadata}],
        description="Istruzioni\nlavatrice",
    )
    [candidate] = shortlist_documents(session, "lavatrice")
    assert candidate["summary_line"] == "Istruzioni lavatrice"


def test_shortlist_skips_excluded_ids(session, embed_calls):
    excluded = summary_doc(session, "Escluso", basis_vector(0))
    kept = summary_doc(session, "Tenuto", basis_vector(1))
    result = shortlist_documents(session, "riassunto", exclude_ids={excluded.id})
    assert [c["id"] for c in result] == [str(kept.id)]


def test_shortlist_truncates_long_query(session, embed_calls):
    summary_doc(session, "Bolletta", basis_vector(0))
    shortlist_documents(session, "bolletta " * 3000)
    assert len(embed_calls[0][0]) == QUERY_MAX_CHARS


def test_shortlist_empty_archive_returns_empty(session, embed_calls):
    assert shortlist_documents(session, "qualcosa") == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_search_core.py tests/test_chat_context.py`
Expected: FAIL. The search-core tests fail with `TypeError: semantic_search() got an unexpected keyword argument 'sources'` and `TypeError: keyword_search() got an unexpected keyword argument 'sources'`. `test_chat_context.py` errors at collection with `ModuleNotFoundError: No module named 'app.services.chat_context'`.

- [ ] **Step 4: Add the `sources` filter and `QUERY_MAX_CHARS` to `search.py`**

In `backend/app/services/search.py`, below `RRF_K = 60`, add:

```python
QUERY_MAX_CHARS = 2000  # embedding / keyword query cap: a pasted email must not exceed the embed input limit
```

Replace `semantic_search` with:

```python
def semantic_search(
    session: Session,
    query_vector: list[float],
    limit: int = 20,
    doc_ids: list[uuid.UUID] | None = None,
    sources: list[str] | None = None,
) -> list[SemanticHit]:
    distance = Chunk.embedding.cosine_distance(query_vector)
    query = select(Chunk.id, distance.label("distance")).where(Chunk.embedding.is_not(None))
    if doc_ids is not None:
        query = query.where(Chunk.document_id.in_(doc_ids))
    if sources is not None:
        query = query.where(Chunk.source.in_([str(s) for s in sources]))
    query = query.order_by(distance).limit(limit)
    return [
        SemanticHit(chunk_id=chunk_id, similarity=1.0 - dist)
        for chunk_id, dist in session.exec(query)
    ]
```

Keep `KEYWORD_SQL_BASE` unchanged (its `{doc_filter}` placeholder now receives every filter line). Replace `keyword_search` with:

```python
def keyword_search(
    session: Session,
    query: str,
    limit: int = 20,
    doc_ids: list[uuid.UUID] | None = None,
    sources: list[str] | None = None,
) -> list[KeywordHit]:
    filters: list[str] = []
    params: dict = {"query": query, "limit": limit}
    if doc_ids is not None:
        filters.append("AND c.document_id = ANY(:doc_ids)")
        params["doc_ids"] = doc_ids
    if sources is not None:
        filters.append("AND c.source = ANY(:sources)")
        params["sources"] = [str(s) for s in sources]
    sql = KEYWORD_SQL_BASE.format(doc_filter="\n".join(filters))
    rows = session.execute(text(sql), params)
    return [KeywordHit(chunk_id=r[0], rank=r[1], snippet=r[2]) for r in rows]
```

`str(s)` turns `ChunkSource` members into plain strings before psycopg sees them.

- [ ] **Step 5: Create `chat_context.py`**

Create `backend/app/services/chat_context.py`:

```python
"""Document shortlist for the chat preflight: local search only (one query embedding)."""

import uuid
from collections.abc import Iterable
from typing import TypedDict

from sqlmodel import Session, select

from app.models import Chunk, ChunkSource, Document
from app.services.llm import embed as llm_embed
from app.services.search import QUERY_MAX_CHARS, keyword_search, rrf_fuse, semantic_search

SHORTLIST_LIMIT = 30
SHORTLIST_POOL = 60  # chunks fetched by each of the semantic and keyword searches
SHORTLIST_SOURCES = [ChunkSource.summary.value, ChunkSource.metadata.value]
SUMMARY_LINE_CHARS = 200


class Candidate(TypedDict):
    id: str
    title: str
    document_date: str  # ISO YYYY-MM-DD
    doc_type: str
    summary_line: str


def summary_line(doc: Document) -> str:
    """First 200 characters of the summary (or description), flattened to one line."""
    text = doc.summary or doc.description or ""
    return " ".join(text.split())[:SUMMARY_LINE_CHARS]


def shortlist_documents(
    session: Session,
    query: str,
    limit: int = SHORTLIST_LIMIT,
    *,
    exclude_ids: Iterable[uuid.UUID] = (),
) -> list[Candidate]:
    """Up to `limit` documents whose summary/metadata chunks match `query`, best first."""
    query = query[:QUERY_MAX_CHARS]
    query_vector = llm_embed([query])[0]
    semantic_hits = semantic_search(
        session, query_vector, limit=SHORTLIST_POOL, sources=SHORTLIST_SOURCES
    )
    keyword_hits = keyword_search(session, query, limit=SHORTLIST_POOL, sources=SHORTLIST_SOURCES)
    ordered = rrf_fuse(
        [[h.chunk_id for h in semantic_hits], [h.chunk_id for h in keyword_hits]]
    )
    if not ordered:
        return []

    chunk_documents = dict(
        session.exec(select(Chunk.id, Chunk.document_id).where(Chunk.id.in_(ordered))).all()
    )
    excluded = set(exclude_ids)
    document_ids: list[uuid.UUID] = []
    for chunk_id in ordered:  # best rank first, so the first chunk seen is the document's best
        document_id = chunk_documents[chunk_id]
        if document_id in excluded or document_id in document_ids:
            continue
        document_ids.append(document_id)
        if len(document_ids) >= limit:
            break
    if not document_ids:
        return []

    documents = {
        d.id: d for d in session.exec(select(Document).where(Document.id.in_(document_ids)))
    }
    return [
        Candidate(
            id=str(doc.id),
            title=doc.title,
            document_date=doc.document_date.isoformat(),
            doc_type=doc.doc_type,
            summary_line=summary_line(doc),
        )
        for doc in (documents[i] for i in document_ids)
    ]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_search_core.py tests/test_chat_context.py`
Expected: PASS (9 new tests among them).

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: `281 passed` (272 + 9), no warnings.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami
git add backend/app/services/search.py backend/app/services/chat_context.py backend/tests/test_search_core.py backend/tests/test_chat_context.py
git commit -m "feat: shortlist chat candidate documents from summary and metadata chunks

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Preflight — `llm.select_documents`

**Files:**
- Modify: `backend/app/services/llm.py` (imports at the top; new functions after `complete`)
- Test: `backend/tests/test_llm.py` (append at the end)

**Interfaces:**
- Consumes: `llm._kw`, `llm.get_settings`, `litellm.completion`. The candidate dict shape is `Candidate` from Task 1, but `llm.py` must not import `chat_context` (to avoid an import cycle), so it takes plain `list[dict]`.
- Produces:
  - `llm.select_documents(question: str, history: list[dict], candidates: list[dict]) -> list[str]`. It never raises and returns `[]` on any failure or when `candidates` is empty, without making a call in that case.
  - `llm.parse_document_ids(raw: str | None, candidate_ids: list[str]) -> list[str]`, which raises `ValueError` on a malformed reply.
  - `SELECT_HISTORY_MESSAGES = 4`, `SELECT_MAX_DOCUMENTS = 10`, logger `origami.llm`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_llm.py`. The module already imports `SimpleNamespace`, `litellm`, `llm`, `pytest`, and `json` as `jsonlib`; the autouse `pinned_settings` fixture gives `llm_model="gemini/gemini-2.5-flash"` and empty keys, so `**kw` is empty.

```python
SELECT_IDS = [f"00000000-0000-4000-8000-{i:012d}" for i in range(12)]


def _candidates(count=3):
    return [
        {
            "id": SELECT_IDS[i],
            "title": f"Documento {i}",
            "document_date": "2026-03-15",
            "doc_type": "pdf",
            "summary_line": f"Riassunto {i}",
        }
        for i in range(count)
    ]


def _select_completion(reply, captured):
    def fake_completion(model, messages, **kw):
        captured.append({"model": model, "messages": messages, **kw})
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    return fake_completion


def test_select_documents_keeps_only_candidate_ids_once_in_order(monkeypatch):
    reply = jsonlib.dumps(
        {"document_ids": [f"  {SELECT_IDS[2].upper()} ", "ghost-id", 7, SELECT_IDS[0],
                          SELECT_IDS[2], SELECT_IDS[11]]}
    )
    monkeypatch.setattr(litellm, "completion", _select_completion(reply, []))
    assert llm.select_documents("quale bolletta?", [], _candidates()) == [SELECT_IDS[2], SELECT_IDS[0]]


def test_select_documents_caps_at_ten(monkeypatch):
    reply = jsonlib.dumps({"document_ids": SELECT_IDS})
    monkeypatch.setattr(litellm, "completion", _select_completion(reply, []))
    assert llm.select_documents("tutto", [], _candidates(12)) == SELECT_IDS[:10]


def test_select_documents_parses_fenced_json(monkeypatch):
    fence = "`" * 3
    reply = f'{fence}json\n{{"document_ids": ["{SELECT_IDS[1]}"]}}\n{fence}'
    monkeypatch.setattr(litellm, "completion", _select_completion(reply, []))
    assert llm.select_documents("q", [], _candidates()) == [SELECT_IDS[1]]


def test_select_documents_parses_prose_wrapped_json(monkeypatch):
    reply = f'Ecco i documenti: {{"document_ids": ["{SELECT_IDS[0]}"]}} spero aiuti.'
    monkeypatch.setattr(litellm, "completion", _select_completion(reply, []))
    assert llm.select_documents("q", [], _candidates()) == [SELECT_IDS[0]]


def test_select_documents_junk_returns_empty(monkeypatch):
    for reply in ["Non lo so.", '{"document_ids": "x"}', "{not json}", None]:
        monkeypatch.setattr(litellm, "completion", _select_completion(reply, []))
        assert llm.select_documents("q", [], _candidates()) == [], reply
```

```python
def test_select_documents_exception_returns_empty(monkeypatch, caplog):
    monkeypatch.setattr(litellm, "completion", _select_completion(RuntimeError("provider down"), []))
    assert llm.select_documents("q", [], _candidates()) == []
    assert "document preflight failed" in caplog.text


def test_select_documents_skips_call_without_candidates(monkeypatch):
    captured = []
    monkeypatch.setattr(litellm, "completion", _select_completion('{"document_ids": []}', captured))
    assert llm.select_documents("q", [{"role": "user", "content": "x"}], []) == []
    assert captured == []


def test_select_documents_prompt_lists_candidates_and_last_four_messages(monkeypatch):
    captured = []
    monkeypatch.setattr(litellm, "completion", _select_completion('{"document_ids": []}', captured))
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"storico-{i}"} for i in range(6)
    ]
    assert llm.select_documents("quale bolletta?", history, _candidates(2)) == []
    [call] = captured
    assert call["model"] == "gemini/gemini-2.5-flash"
    prompt = call["messages"][0]["content"]
    assert f"1. {SELECT_IDS[0]} | Documento 0 | 2026-03-15 | pdf | Riassunto 0" in prompt
    assert f"2. {SELECT_IDS[1]} | Documento 1 | 2026-03-15 | pdf | Riassunto 1" in prompt
    assert "storico-1" not in prompt
    assert "user: storico-2" in prompt and "assistant: storico-5" in prompt
    assert '{"document_ids": [' in prompt
    assert prompt.rstrip().endswith("Question: quale bolletta?")
```

The final set is 8 tests: keeps_only…, caps_at_ten, fenced, prose, junk (single test), exception, skips_call, prompt.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_llm.py -k select_documents`
Expected: FAIL, 8 tests with `AttributeError: module 'app.services.llm' has no attribute 'select_documents'`.

- [ ] **Step 3: Implement**

In `backend/app/services/llm.py`, add `import logging` to the stdlib imports (alphabetical: after `import json`). Below `LANGUAGE_NAMES = {...}`, add:

```python
log = logging.getLogger("origami.llm")
```

After the `complete` function (before `OPENAI_STYLE_MODEL_URLS`), add:

```python
SELECT_HISTORY_MESSAGES = 4
SELECT_MAX_DOCUMENTS = 10


def _select_prompt(question: str, history: list[dict], candidates: list[dict]) -> str:
    lines = "\n".join(
        f"{n}. {c['id']} | {c['title']} | {c['document_date']} | {c['doc_type']} | {c['summary_line']}"
        for n, c in enumerate(candidates, start=1)
    )
    recent = history[-SELECT_HISTORY_MESSAGES:]
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in recent) or "(no earlier messages)"
    return (
        "You pick which documents of a personal archive a chat question refers to.\n"
        'Reply with ONLY a JSON object: {"document_ids": ["<id>", ...]}, listing the ids of the '
        "documents the user is asking about or that are needed to answer. "
        'Reply {"document_ids": []} if none apply.\n\n'
        f"Candidate documents (id | title | date | type | summary):\n{lines}\n\n"
        f"Recent conversation:\n{conversation}\n\n"
        f"Question: {question}"
    )


def parse_document_ids(raw: str | None, candidate_ids: list[str]) -> list[str]:
    """Candidate ids from the first {...} block of the reply, in order, de-duplicated, max 10."""
    match = re.search(r"\{.*?\}", raw or "", re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the preflight reply")
    data = json.loads(match.group(0))
    ids = data.get("document_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        raise ValueError("document_ids is not a list")
    allowed = {cid.lower(): cid for cid in candidate_ids}
    selected: list[str] = []
    for item in ids:
        if not isinstance(item, str):
            continue
        cid = allowed.get(item.strip().lower())
        if cid is not None and cid not in selected:
            selected.append(cid)
            if len(selected) == SELECT_MAX_DOCUMENTS:
                break
    return selected


def select_documents(question: str, history: list[dict], candidates: list[dict]) -> list[str]:
    """Chat preflight: ids of the candidates the question is about. Never raises: [] on failure."""
    if not candidates:
        return []
    try:
        settings = get_settings()
        kw = _kw(settings.llm_api_key, settings.llm_api_base)
        resp = litellm.completion(
            model=settings.llm_model,
            messages=[{"role": "user", "content": _select_prompt(question, history, candidates)}],
            **kw,
        )
        return parse_document_ids(resp.choices[0].message.content, [c["id"] for c in candidates])
    except Exception:
        log.exception("document preflight failed")
        return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_llm.py`
Expected: PASS.

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: `289 passed` (281 + 8).

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add backend/app/services/llm.py backend/tests/test_llm.py
git commit -m "feat: add LLM preflight that picks the documents a chat question is about

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Retrieval restricted to context documents

**Files:**
- Modify: `backend/app/services/rag.py` (imports, `retrieve`)
- Test: `backend/tests/test_rag.py`

**Interfaces:**
- Consumes: `semantic_search(..., doc_ids=...)`, `keyword_search(..., doc_ids=...)` and `QUERY_MAX_CHARS` from Task 1.
- Produces: `rag.retrieve(session, query: str, document_ids: list[uuid.UUID] | None = None) -> tuple[list[RetrievedChunk], bool]`.
  - `[]` → `([], False)` without any embed or search.
  - A list → hybrid search over all chunk sources of those documents only.
  - `None` → the whole archive. This is temporary, for the old `stream_answer`, and Task 4 removes it.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_rag.py`, add these imports below `import pytest`:

```python
from app.models import ChunkSource
from app.services.search import QUERY_MAX_CHARS
```

Append:

```python
def test_retrieve_restricted_to_document_ids(session, rag_llm):
    inside = seed_document(
        session, "Dentro",
        [
            {"content": "Riassunto dentro.", "embedding": basis_vector(0), "source": ChunkSource.summary},
            {"content": "Testo dentro.", "embedding": basis_vector(0), "page_number": 1},
        ],
    )
    seed_document(session, "Fuori", [{"content": "Testo fuori.", "embedding": basis_vector(0)}])

    sources, grounded = rag.retrieve(session, "testo", [inside.id])
    assert grounded is True
    assert {s.document_id for s in sources} == {inside.id}
    assert len(sources) == 2  # every chunk source of a context document is searchable


def test_retrieve_empty_document_ids_skips_search(session, rag_llm):
    seed_document(session, "Doc", [{"content": "Contenuto.", "embedding": basis_vector(0)}])
    assert rag.retrieve(session, "qualsiasi", []) == ([], False)
    assert rag_llm["embed"] == []


def test_retrieve_grounded_only_by_context_documents(session, rag_llm):
    seed_document(session, "Fuori", [{"content": "molto simile", "embedding": basis_vector(0)}])
    inside = seed_document(session, "Dentro", [{"content": "poco simile", "embedding": basis_vector(9)}])
    sources, grounded = rag.retrieve(session, "domanda", [inside.id])
    assert [s.title for s in sources] == ["Dentro"]
    assert grounded is False


def test_retrieve_truncates_long_query(session, rag_llm):
    doc = seed_document(session, "Bolletta", [{"content": "bolletta", "embedding": basis_vector(0)}])
    rag.retrieve(session, "bolletta " * 3000, [doc.id])
    assert len(rag_llm["embed"][0][0]) == QUERY_MAX_CHARS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_rag.py`
Expected: FAIL with `TypeError: retrieve() takes 2 positional arguments but 3 were given` (4 tests).

- [ ] **Step 3: Implement**

In `backend/app/services/rag.py`, change the search import to:

```python
from app.services.search import QUERY_MAX_CHARS, keyword_search, rrf_fuse, semantic_search
```

Read the current `retrieve` first. Replace its signature and the lines before `ordered = rrf_fuse(...)` so that the function starts like this; everything from `ordered = rrf_fuse(` to the end stays unchanged:

```python
def retrieve(
    session: Session, query: str, document_ids: list[uuid.UUID] | None = None
) -> tuple[list[RetrievedChunk], bool]:
    """Hybrid chunk search inside `document_ids` (all chunk sources); [] → no search.

    None searches the whole archive (kept only for the single-turn stream_answer).
    """
    if document_ids is not None and not document_ids:
        return [], False
    settings = get_settings()
    query = query[:QUERY_MAX_CHARS]
    doc_ids = list(document_ids) if document_ids is not None else None
    query_vector = llm_embed([query])[0]
    semantic_hits = semantic_search(session, query_vector, limit=CANDIDATE_POOL, doc_ids=doc_ids)
    keyword_hits = keyword_search(session, query, limit=CANDIDATE_POOL, doc_ids=doc_ids)
```

(the old body used the name `question`; it appears only in these first lines, so nothing else in the function changes).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_rag.py tests/test_chat_api.py`
Expected: PASS (the old endpoint tests still pass through `stream_answer` → `retrieve(session, question)`).

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: `293 passed` (289 + 4).

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add backend/app/services/rag.py backend/tests/test_rag.py
git commit -m "feat: restrict RAG retrieval to a set of context documents

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Conversation chat endpoint

**Files:**
- Modify: `backend/app/services/rag.py` (whole file shown below)
- Modify: `backend/app/api/chat.py` (whole file shown below)
- Modify: `backend/tests/conftest.py` (`llm_stub` fixture)
- Test: `backend/tests/test_rag.py`, `backend/tests/test_chat_api.py` (rewritten)

**Interfaces:**
- Consumes: `shortlist_documents(..., exclude_ids=...)` and `Candidate` (Task 1), `llm.select_documents` (Task 2), `retrieve(session, query, document_ids)` (Task 3).
- Produces:
  - `rag.SYSTEM_PROMPT` (now asks for Markdown and mentions history).
  - `rag.trim_history(history: list[dict]) -> list[dict]`.
  - `rag.shortlist_query(messages: list[dict]) -> str`.
  - `rag.build_messages(question: str, sources: list[RetrievedChunk], history: list[dict] | None = None) -> list[dict]`.
  - `rag.stream_chat(session, messages: list[dict], pinned_ids: list[uuid.UUID], excluded_ids: list[uuid.UUID]) -> Iterator[tuple[str, dict]]`.
  - `rag.retrieve(session, query, document_ids: list[uuid.UUID])`, with the parameter now required.
  - `rag.llm_select_documents`, the patch point. `stream_answer` is removed.
  - API: `POST /api/chat` body `{messages: [{role: "user"|"assistant", content: str(min 1)}] (1..40, last must be user), pinned_ids: uuid[] = [], excluded_ids: uuid[] = []}`. The SSE `meta` event gains `auto_documents: [{id, title, document_date}]`.
  - `llm_stub` gains `calls["select"]` (a list of `{question, history, candidates}`), `calls["select_ids"]` (scripted return; None → `[]`) and `calls["select_error"]` (an exception to raise).

- [ ] **Step 1: Extend `llm_stub`**

In `backend/tests/conftest.py`, read the current `llm_stub` fixture first. Then:
- Change its docstring to `"""Stub the LLM mock boundary: app.services.llm (the other one is smtplib.SMTP). select_documents is scripted via calls["select_ids"] / calls["select_error"]."""`.
- Change the `calls = {...}` line to:

```python
    calls = {
        "embed": [], "describe": [], "translate": [], "language": "it", "translate_error": None,
        "select": [], "select_ids": None, "select_error": None,
    }
```

- Add this function and patch before `return calls`:

```python
    def fake_select_documents(question, history, candidates):
        calls["select"].append(
            {"question": question, "history": [dict(m) for m in history],
             "candidates": [dict(c) for c in candidates]}
        )
        if calls["select_error"] is not None:
            raise calls["select_error"]
        return list(calls["select_ids"] or [])

    monkeypatch.setattr("app.services.rag.llm_select_documents", fake_select_documents)
```

(This patch fails until Step 4 adds the import to `rag.py`, so Steps 1–3 are verified together in Step 5.)

- [ ] **Step 2: Update and extend `test_rag.py`**

In `backend/tests/test_rag.py`:

a) Change the imports block at the top to:

```python
import uuid

import pytest

from app.models import ChunkSource
from app.services import chat_context, rag
from app.services.search import QUERY_MAX_CHARS
from tests.helpers import basis_vector, seed_document
```

b) In the `rag_llm` fixture, add after `monkeypatch.setattr(rag, "llm_embed", fake_embed)`:

```python
    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
```

c) Pass context ids to the four existing calls that relied on the whole archive:
- `test_retrieve_grounded_when_similar`: `rag.retrieve(session, "quanto ho pagato la bolletta di marzo?", [doc.id])`.
- `test_retrieve_not_grounded_when_dissimilar`: bind the seeded document (`doc = seed_document(...)`) and call `rag.retrieve(session, "chi ha vinto il mondiale 2006?", [doc.id])`.
- `test_retrieve_not_grounded_when_empty`: `rag.retrieve(session, "qualsiasi cosa", [uuid.uuid4()])` (a context id with no chunks).
- `test_build_messages_numbers_sources`: bind `doc = seed_document(...)` and call `rag.retrieve(session, "quanto pago di affitto?", [doc.id])`.

d) Replace `test_stream_answer_event_sequence` entirely with:

```python
def test_stream_chat_event_sequence(session, rag_llm, llm_stub):
    doc = seed_document(
        session, "Doc",
        [{"content": "Contenuto rilevante.", "embedding": basis_vector(0), "source": ChunkSource.summary}],
        summary="Contenuto rilevante.",
    )
    llm_stub["select_ids"] = [str(doc.id)]
    events = list(rag.stream_chat(session, [{"role": "user", "content": "domanda?"}], [], []))
    assert [kind for kind, _ in events] == ["meta", "delta", "delta", "done"]
    meta = events[0][1]
    assert meta["grounded"] is True
    assert meta["sources"][0]["n"] == 1
    assert meta["sources"][0]["document_id"] == str(doc.id)
    assert meta["auto_documents"] == [
        {"id": str(doc.id), "title": "Doc", "document_date": doc.document_date.isoformat()}
    ]
    assert events[1][1]["text"] == "Ecco "
```

e) Append:

```python
def test_trim_history_keeps_last_twelve():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)
    ]
    assert rag.trim_history(history) == history[-12:]


def test_trim_history_drops_oldest_over_char_budget():
    history = [
        {"role": "user", "content": "a" * 2500},
        {"role": "assistant", "content": "b" * 2500},
        {"role": "user", "content": "c" * 2500},
    ]
    assert rag.trim_history(history) == history[1:]
    assert rag.trim_history([{"role": "user", "content": "x" * 7000}]) == []


def test_shortlist_query_joins_previous_user_message():
    messages = [
        {"role": "user", "content": "prima"},
        {"role": "assistant", "content": "risposta"},
        {"role": "user", "content": "seconda"},
    ]
    assert rag.shortlist_query(messages) == "prima\nseconda"
    assert rag.shortlist_query([{"role": "user", "content": "sola"}]) == "sola"


def test_build_messages_inserts_history_before_question():
    history = [{"role": "user", "content": "prima"}, {"role": "assistant", "content": "risposta"}]
    messages = rag.build_messages("seconda?", [], history)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1] == {"role": "user", "content": "prima"}
    assert messages[2] == {"role": "assistant", "content": "risposta"}
    assert messages[-1]["content"].endswith("Question: seconda?")


def test_system_prompt_asks_for_markdown_citations_and_history():
    assert "Markdown" in rag.SYSTEM_PROMPT
    assert "[n]" in rag.SYSTEM_PROMPT
    assert "conversation history" in rag.SYSTEM_PROMPT
```

- [ ] **Step 3: Rewrite `test_chat_api.py`**

Replace `backend/tests/test_chat_api.py` with:

```python
import json
import uuid

import pytest

from app.models import ChunkSource
from app.services import chat_context, rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def chat_llm(monkeypatch, llm_stub):
    """Query embedding = basis_vector(0) for shortlist and retrieval; scripted answer stream."""
    calls = {"embed": [], "complete": []}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [basis_vector(0) for _ in texts]

    def fake_complete(messages, stream=False):
        calls["complete"].append(messages)
        return iter(["Ecco ", "la risposta [1]."])

    monkeypatch.setattr(rag, "llm_embed", fake_embed)
    monkeypatch.setattr(chat_context, "llm_embed", fake_embed)
    monkeypatch.setattr(rag, "llm_complete", fake_complete)
    return calls


def parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def body(*contents, pinned=(), excluded=()):
    """Alternating user/assistant messages starting with the user; odd count ends with the user."""
    return {
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": c}
            for i, c in enumerate(contents)
        ],
        "pinned_ids": [str(i) for i in pinned],
        "excluded_ids": [str(i) for i in excluded],
    }


def seed_doc(session, title, vector, content="Testo del documento."):
    """A document reachable by the shortlist (summary chunk) and by retrieval (content chunk)."""
    return seed_document(
        session, title,
        [
            {"content": f"Riassunto di {title}", "embedding": vector, "source": ChunkSource.summary},
            {"content": content, "embedding": vector, "page_number": 1},
        ],
        summary=f"Riassunto di {title}",
    )


def source_doc_ids(meta: dict) -> set[str]:
    return {s["document_id"] for s in meta["sources"]}


def test_chat_streams_meta_deltas_done(auth_client, session, chat_llm, llm_stub):
    doc = seed_doc(session, "Bolletta", basis_vector(0), "Bolletta di marzo: 42 euro.")
    llm_stub["select_ids"] = [str(doc.id)]

    resp = auth_client.post("/api/chat", json=body("quanto ho pagato?"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    meta = events[0]
    assert meta["grounded"] is True
    assert meta["sources"][0]["title"] == "Bolletta"
    assert source_doc_ids(meta) == {str(doc.id)}
    assert meta["auto_documents"] == [
        {"id": str(doc.id), "title": "Bolletta", "document_date": doc.document_date.isoformat()}
    ]
    assert events[1]["text"] == "Ecco "


def test_chat_ungrounded_flag(auth_client, session, chat_llm):
    events = parse_sse(auth_client.post("/api/chat", json=body("chi ha vinto il mondiale?")).text)
    assert events[0]["type"] == "meta"
    assert events[0]["grounded"] is False
    assert events[0]["sources"] == []
    assert events[0]["auto_documents"] == []
    assert events[-1]["type"] == "done"


def test_chat_invalid_messages_422(auth_client):
    for payload in ({"messages": []}, {"messages": [{"role": "user", "content": ""}]}):
        resp = auth_client.post("/api/chat", json=payload)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json=body("x")).status_code == 401


def test_chat_emits_error_event_on_failure(auth_client, monkeypatch, chat_llm):
    def boom(texts):
        raise RuntimeError("embedding down")

    monkeypatch.setattr(chat_context, "llm_embed", boom)
    resp = auth_client.post("/api/chat", json=body("ciao"))
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "chat_failed"


def test_chat_last_message_not_user_422(auth_client):
    resp = auth_client.post("/api/chat", json=body("domanda", "risposta"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_chat_more_than_40_messages_422(auth_client):
    resp = auth_client.post("/api/chat", json=body(*[f"m{i}" for i in range(41)]))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_chat_context_is_pinned_plus_auto_minus_excluded(auth_client, session, chat_llm, llm_stub):
    pinned = seed_doc(session, "Fissato", basis_vector(1))
    auto = seed_doc(session, "Automatico", basis_vector(0))
    excluded = seed_doc(session, "Escluso", basis_vector(0))
    llm_stub["select_ids"] = [str(excluded.id), str(auto.id)]  # the preflight even names the excluded one

    resp = auth_client.post(
        "/api/chat", json=body("domanda", pinned=[pinned.id], excluded=[excluded.id])
    )
    meta = parse_sse(resp.text)[0]

    candidate_ids = {c["id"] for c in llm_stub["select"][0]["candidates"]}
    assert str(auto.id) in candidate_ids
    assert str(pinned.id) not in candidate_ids and str(excluded.id) not in candidate_ids
    assert source_doc_ids(meta) == {str(pinned.id), str(auto.id)}
    assert [d["id"] for d in meta["auto_documents"]] == [str(auto.id)]


def test_chat_retrieval_restricted_to_context(auth_client, session, chat_llm, llm_stub):
    outside = seed_doc(session, "Fuori", basis_vector(0), "bolletta luce marzo")
    pinned = seed_doc(session, "Contratto", basis_vector(5), "contratto di affitto")
    llm_stub["select_ids"] = []

    meta = parse_sse(
        auth_client.post("/api/chat", json=body("bolletta luce", pinned=[pinned.id])).text
    )[0]

    assert str(outside.id) in {c["id"] for c in llm_stub["select"][0]["candidates"]}
    assert meta["sources"] and source_doc_ids(meta) == {str(pinned.id)}
    assert meta["grounded"] is False  # only the context's similarity counts


def test_chat_history_trimmed_to_twelve_messages(auth_client, session, chat_llm, llm_stub):
    seed_doc(session, "Doc", basis_vector(0))
    contents = [f"m{i}" for i in range(20)] + ["domanda finale"]

    auth_client.post("/api/chat", json=body(*contents))

    expected = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(8, 20)
    ]
    sent = chat_llm["complete"][0]
    assert sent[0]["role"] == "system"
    assert sent[1:-1] == expected
    assert sent[-1]["role"] == "user"
    assert sent[-1]["content"].endswith("Question: domanda finale")
    assert llm_stub["select"][0]["history"] == expected
    assert llm_stub["select"][0]["question"] == "domanda finale"


def test_chat_preflight_failure_still_streams_answer(auth_client, session, chat_llm, llm_stub):
    seed_doc(session, "Doc", basis_vector(0))
    llm_stub["select_error"] = RuntimeError("provider down")

    events = parse_sse(auth_client.post("/api/chat", json=body("domanda")).text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    assert events[0]["auto_documents"] == []
    assert events[0]["sources"] == []


def test_chat_unknown_pinned_id_ignored(auth_client, session, chat_llm, llm_stub):
    real = seed_doc(session, "Reale", basis_vector(0))
    deleted_id = uuid.uuid4()

    resp = auth_client.post("/api/chat", json=body("domanda", pinned=[deleted_id, real.id]))
    events = parse_sse(resp.text)
    assert resp.status_code == 200
    assert events[-1]["type"] == "done"
    assert source_doc_ids(events[0]) == {str(real.id)}


def test_chat_drops_preflight_ids_outside_shortlist(auth_client, session, chat_llm, llm_stub):
    doc = seed_doc(session, "Bolletta", basis_vector(0))
    llm_stub["select_ids"] = ["not-a-uuid", str(uuid.uuid4()), str(doc.id), str(doc.id)]

    events = parse_sse(auth_client.post("/api/chat", json=body("domanda")).text)
    assert events[-1]["type"] == "done"
    assert [d["id"] for d in events[0]["auto_documents"]] == [str(doc.id)]
    assert source_doc_ids(events[0]) == {str(doc.id)}


def test_chat_skips_preflight_when_all_candidates_excluded(auth_client, session, chat_llm, llm_stub):
    pinned = seed_doc(session, "Fissato", basis_vector(0))
    excluded = seed_doc(session, "Escluso", basis_vector(1))

    events = parse_sse(
        auth_client.post(
            "/api/chat", json=body("domanda", pinned=[pinned.id], excluded=[excluded.id])
        ).text
    )
    assert llm_stub["select"] == []
    assert events[0]["auto_documents"] == []
    assert source_doc_ids(events[0]) == {str(pinned.id)}
    assert events[-1]["type"] == "done"


def test_chat_shortlist_query_includes_previous_user_message(auth_client, chat_llm):
    auth_client.post("/api/chat", json=body("prima domanda", "risposta", "e quella di marzo?"))
    assert chat_llm["embed"][0] == ["prima domanda\ne quella di marzo?"]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_rag.py tests/test_chat_api.py`
Expected: FAIL. Every test using `llm_stub` errors with `AttributeError: <module 'app.services.rag' ...> has no attribute 'llm_select_documents'`. The rest fail with `AttributeError: module 'app.services.rag' has no attribute 'trim_history'` / `'shortlist_query'`, an assertion on `SYSTEM_PROMPT`, or a 422 for the new body shape.

- [ ] **Step 5: Rewrite `rag.py`**

Read the current `backend/app/services/rag.py` first (Task 3 changed `retrieve`). Replace the whole file with the version below, which keeps `RetrievedChunk` and the retrieval and source-numbering code unchanged:

```python
import logging
import uuid
from dataclasses import dataclass
from typing import Iterator

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, Document
from app.services.chat_context import Candidate, shortlist_documents
from app.services.llm import complete as llm_complete
from app.services.llm import embed as llm_embed
from app.services.llm import select_documents as llm_select_documents
from app.services.search import QUERY_MAX_CHARS, keyword_search, rrf_fuse, semantic_search

log = logging.getLogger("origami.chat")

SYSTEM_PROMPT = (
    "You are Origami, a personal document archive assistant. Answer the user's "
    "question using the numbered sources provided. Format the answer in Markdown "
    "(lists, tables and bold text where they help). Cite the sources inline with "
    "[n] after each claim they support. Answer in the same language as the "
    "question. If the sources do not contain the answer, say so explicitly and "
    "clearly state when you are drawing on general knowledge instead of the "
    "user's documents. Use the conversation history to understand follow-up questions."
)

CANDIDATE_POOL = 20
HISTORY_MAX_MESSAGES = 12
HISTORY_MAX_CHARS = 6000


@dataclass
class RetrievedChunk:
    n: int
    chunk_id: int
    document_id: uuid.UUID
    title: str
    page_number: int | None
    content: str


def retrieve(
    session: Session, query: str, document_ids: list[uuid.UUID]
) -> tuple[list[RetrievedChunk], bool]:
    """Hybrid chunk search inside `document_ids` (all chunk sources); [] → no search."""
    if not document_ids:
        return [], False
    settings = get_settings()
    query = query[:QUERY_MAX_CHARS]
    doc_ids = list(document_ids)
    query_vector = llm_embed([query])[0]
    semantic_hits = semantic_search(session, query_vector, limit=CANDIDATE_POOL, doc_ids=doc_ids)
    keyword_hits = keyword_search(session, query, limit=CANDIDATE_POOL, doc_ids=doc_ids)
    ordered = rrf_fuse(
        [[h.chunk_id for h in semantic_hits], [h.chunk_id for h in keyword_hits]]
    )[: settings.rag_top_k]
    if not ordered:
        return [], False

    chunks = {c.id: c for c in session.exec(select(Chunk).where(Chunk.id.in_(ordered)))}
    titles: dict[uuid.UUID, str] = {}
    sources: list[RetrievedChunk] = []
    for n, chunk_id in enumerate(ordered, start=1):
        chunk = chunks[chunk_id]
        if chunk.document_id not in titles:
            titles[chunk.document_id] = session.get(Document, chunk.document_id).title
        sources.append(
            RetrievedChunk(
                n=n,
                chunk_id=chunk_id,
                document_id=chunk.document_id,
                title=titles[chunk.document_id],
                page_number=chunk.page_number,
                content=chunk.content,
            )
        )
    best_similarity = max((h.similarity for h in semantic_hits), default=0.0)
    grounded = best_similarity >= settings.rag_relevance_floor
    return sources, grounded


def trim_history(history: list[dict]) -> list[dict]:
    """Last 12 messages, then drop the oldest until their content totals ≤ 6,000 characters."""
    kept = list(history[-HISTORY_MAX_MESSAGES:])
    while kept and sum(len(m["content"]) for m in kept) > HISTORY_MAX_CHARS:
        kept.pop(0)
    return kept


def shortlist_query(messages: list[dict]) -> str:
    """The previous and the last user message, so follow-ups ("and in March?") still match."""
    user_texts = [m["content"] for m in messages if m["role"] == "user"]
    return "\n".join(user_texts[-2:])


def build_messages(
    question: str, sources: list[RetrievedChunk], history: list[dict] | None = None
) -> list[dict]:
    if sources:
        blocks = []
        for source in sources:
            page = f" (p. {source.page_number})" if source.page_number else ""
            blocks.append(f"[{source.n}] {source.title}{page}\n{source.content}")
        sources_text = "Sources:\n\n" + "\n\n".join(blocks)
    else:
        sources_text = "No relevant documents were found in the archive."
    user_content = f"{sources_text}\n\nQuestion: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        *({"role": m["role"], "content": m["content"]} for m in history or []),
        {"role": "user", "content": user_content},
    ]


def _existing_document_ids(session: Session, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """`ids` in order, de-duplicated, without unknown or deleted documents."""
    unique = list(dict.fromkeys(ids))
    if not unique:
        return []
    found = set(session.exec(select(Document.id).where(Document.id.in_(unique))))
    return [i for i in unique if i in found]


def _auto_picks(question: str, history: list[dict], candidates: list[Candidate]) -> list[Candidate]:
    """Preflight picks, restricted to the shortlist, de-duplicated; any failure → []."""
    if not candidates:
        return []
    try:
        picked = llm_select_documents(question, history, candidates)
    except Exception:
        log.exception("document preflight failed")
        return []
    by_id = {c["id"]: c for c in candidates}
    chosen: list[Candidate] = []
    for document_id in picked:
        candidate = by_id.get(document_id)
        if candidate is not None and candidate not in chosen:
            chosen.append(candidate)
    return chosen


def stream_chat(
    session: Session,
    messages: list[dict],
    pinned_ids: list[uuid.UUID],
    excluded_ids: list[uuid.UUID],
) -> Iterator[tuple[str, dict]]:
    question = messages[-1]["content"]
    history = trim_history(messages[:-1])
    pinned = _existing_document_ids(session, pinned_ids)
    candidates = shortlist_documents(
        session, shortlist_query(messages), exclude_ids=set(pinned_ids) | set(excluded_ids)
    )
    auto = _auto_picks(question, history, candidates)
    context_ids = pinned + [uuid.UUID(c["id"]) for c in auto]
    sources, grounded = retrieve(session, question, context_ids)
    yield (
        "meta",
        {
            "grounded": grounded,
            "sources": [
                {
                    "n": s.n,
                    "chunk_id": s.chunk_id,
                    "document_id": str(s.document_id),
                    "title": s.title,
                    "page_number": s.page_number,
                }
                for s in sources
            ],
            "auto_documents": [
                {"id": c["id"], "title": c["title"], "document_date": c["document_date"]}
                for c in auto
            ],
        },
    )
    for delta in llm_complete(build_messages(question, sources, history), stream=True):
        yield ("delta", {"text": delta})
    yield ("done", {})
```

- [ ] **Step 6: Rewrite `api/chat.py`**

Replace `backend/app/api/chat.py` with:

```python
import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from app.api.deps import get_current_user
from app.db import get_session
from app.services.rag import stream_chat

router = APIRouter(
    prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)]
)

log = logging.getLogger("origami.chat")

MAX_MESSAGES = 40


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    pinned_ids: list[uuid.UUID] = []
    excluded_ids: list[uuid.UUID] = []

    @model_validator(mode="after")
    def last_message_from_user(self) -> "ChatRequest":
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    messages = [m.model_dump() for m in body.messages]

    def event_stream():
        # NOTE: stream_chat payload dicts must never contain a "type" key —
        # it would be clobbered by the event type merged in here.
        try:
            for event_type, payload in stream_chat(
                session, messages, body.pinned_ids, body.excluded_ids
            ):
                yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"
        except Exception:
            log.exception("chat stream failed")
            yield f"data: {json.dumps({'type': 'error', 'code': 'chat_failed', 'message': 'Answer generation failed'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd /opt/origami/backend && uv run pytest -q tests/test_rag.py tests/test_chat_api.py`
Expected: PASS. `test_rag.py` has 6 + 4 (Task 3) + 5 = 15 tests and `test_chat_api.py` has 15.

Run: `cd /opt/origami/backend && uv run pytest -q`
Expected: `308 passed`. That is 293 + 5 (`test_rag.py`; `test_stream_answer_event_sequence` was replaced, not added) + 10 (`test_chat_api.py` goes from 5 to 15). No warnings.

Run: `cd /opt/origami/backend && grep -rn "stream_answer" app tests` → no output.

- [ ] **Step 8: Commit**

```bash
cd /opt/origami
git add backend/app/services/rag.py backend/app/api/chat.py backend/tests/conftest.py backend/tests/test_rag.py backend/tests/test_chat_api.py
git commit -m "feat: multi-turn chat with document preflight, pinned and excluded context

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Frontend state — chat reducer, request builder, debounce hook

**Files:**
- Modify: `frontend/src/lib/types.ts` (append `DocRef`; `meta` variant of `ChatEvent`)
- Create: `frontend/src/lib/chatReducer.ts`, `frontend/src/lib/chatReducer.test.ts`
- Create: `frontend/src/lib/chatRequest.ts`, `frontend/src/lib/chatRequest.test.ts`
- Create: `frontend/src/hooks/useDebouncedValue.ts`, `frontend/src/hooks/useDebouncedValue.test.ts`

**Interfaces:**
- Consumes: the `POST /api/chat` body and the `meta.auto_documents` field (Task 4); `ChatSource` (existing in `types.ts`).
- Produces:
  - `types.ts`: `interface DocRef { id: string; title: string; document_date: string }`; `ChatEvent` meta = `{ type: "meta"; grounded: boolean; sources: ChatSource[]; auto_documents: DocRef[] }`.
  - `chatReducer.ts`:
    - `interface ChatMessage { id: string; role: "user" | "assistant"; content: string; sources?: ChatSource[]; grounded?: boolean; error?: string }`.
    - `interface ChatState { messages: ChatMessage[]; streaming: boolean; pinned: DocRef[]; auto: DocRef[]; excluded: string[] }`.
    - `type ChatAction`, `initialChatState: ChatState`, `chatReducer(state, action): ChatState`, `nextMessageId(): string`.
    - Actions: `SEND {text, userId, assistantId}`, `META {messageId, sources, grounded, autoDocuments}`, `DELTA {messageId, text}`, `DONE {messageId}`, `ERROR {messageId, message}`, `ABORT`, `PIN {doc}`, `REMOVE {id}`, `NEW_CHAT`.
  - `chatRequest.ts`: `MAX_REQUEST_MESSAGES = 40`, `interface ChatRequestBody { messages: {role, content}[]; pinned_ids: string[]; excluded_ids: string[] }`, `buildChatRequest(state: Pick<ChatState, "messages" | "pinned" | "excluded">, text: string): ChatRequestBody`.
  - `useDebouncedValue<T>(value: T, delayMs: number): T`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/chatReducer.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState, nextMessageId, type ChatState } from "./chatReducer";
import type { ChatSource, DocRef } from "./types";

const BOLLETTA: DocRef = { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" };
const CONTRATTO: DocRef = { id: "doc-2", title: "Contratto", document_date: "2026-01-10" };
const SOURCE: ChatSource = { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 };

function send(state: ChatState = initialChatState, text = "Domanda?", userId = "u1", assistantId = "a1"): ChatState {
  return chatReducer(state, { type: "SEND", text, userId, assistantId });
}

describe("chatReducer", () => {
  it("SEND appends the user message and an empty assistant message and starts streaming", () => {
    const state = send();
    expect(state.streaming).toBe(true);
    expect(state.messages).toEqual([
      { id: "u1", role: "user", content: "Domanda?" },
      { id: "a1", role: "assistant", content: "" },
    ]);
  });

  it("SEND is ignored while streaming", () => {
    const state = send();
    expect(send(state, "Altra", "u2", "a2")).toBe(state);
  });

  it("META sets sources and grounded on the assistant message and adds auto documents", () => {
    const state = chatReducer(send(), {
      type: "META",
      messageId: "a1",
      sources: [SOURCE],
      grounded: false,
      autoDocuments: [BOLLETTA],
    });
    expect(state.messages[1]).toEqual({ id: "a1", role: "assistant", content: "", sources: [SOURCE], grounded: false });
    expect(state.auto).toEqual([BOLLETTA]);
  });

  it("META skips documents that are pinned, excluded or already auto", () => {
    const start: ChatState = { ...send(), pinned: [CONTRATTO], auto: [BOLLETTA], excluded: ["doc-3"] };
    const doc3: DocRef = { id: "doc-3", title: "Escluso", document_date: "2026-02-01" };
    const doc4: DocRef = { id: "doc-4", title: "Nuovo", document_date: "2026-02-02" };
    const state = chatReducer(start, {
      type: "META",
      messageId: "a1",
      sources: [],
      grounded: true,
      autoDocuments: [CONTRATTO, BOLLETTA, doc3, doc4, doc4],
    });
    expect(state.auto).toEqual([BOLLETTA, doc4]);
    expect(state.pinned).toEqual([CONTRATTO]);
  });

  it("DELTA appends text to the streaming assistant message", () => {
    let state = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Ciao " });
    state = chatReducer(state, { type: "DELTA", messageId: "a1", text: "mondo" });
    expect(state.messages[1].content).toBe("Ciao mondo");
    expect(state.streaming).toBe(true);
  });

  it("DONE ends streaming", () => {
    const state = chatReducer(send(), { type: "DONE", messageId: "a1" });
    expect(state.streaming).toBe(false);
  });

  it("ERROR stores the message on the assistant reply and ends streaming", () => {
    const state = chatReducer(send(), { type: "ERROR", messageId: "a1", message: "Chat request failed" });
    expect(state.streaming).toBe(false);
    expect(state.messages[1].error).toBe("Chat request failed");
  });

  it("ABORT ends streaming and keeps the partial text", () => {
    const partial = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Risposta parz" });
    const state = chatReducer(partial, { type: "ABORT" });
    expect(state.streaming).toBe(false);
    expect(state.messages[1].content).toBe("Risposta parz");
  });

  it("ignores events for a stale message id after Stop and a new send", () => {
    let state = chatReducer(send(), { type: "DELTA", messageId: "a1", text: "Prima" });
    state = chatReducer(state, { type: "ABORT" });
    expect(chatReducer(state, { type: "DELTA", messageId: "a1", text: " tardi" })).toBe(state);
    state = send(state, "Seconda?", "u2", "a2");
    state = chatReducer(state, { type: "DELTA", messageId: "a1", text: " tardi" });
    state = chatReducer(state, { type: "DONE", messageId: "a1" });
    state = chatReducer(state, { type: "DELTA", messageId: "a2", text: "Nuova" });
    expect(state.messages.map((m) => m.content)).toEqual(["Domanda?", "Prima", "Seconda?", "Nuova"]);
    expect(state.streaming).toBe(true);
  });

  it("PIN adds the document once and removes it from auto and excluded", () => {
    const start: ChatState = { ...initialChatState, auto: [BOLLETTA], excluded: ["doc-1"] };
    const state = chatReducer(start, { type: "PIN", doc: BOLLETTA });
    expect(state.pinned).toEqual([BOLLETTA]);
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual([]);
    expect(chatReducer(state, { type: "PIN", doc: BOLLETTA })).toBe(state);
  });

  it("REMOVE of a pinned document does not exclude it", () => {
    const state = chatReducer({ ...initialChatState, pinned: [CONTRATTO] }, { type: "REMOVE", id: "doc-2" });
    expect(state.pinned).toEqual([]);
    expect(state.excluded).toEqual([]);
  });

  it("REMOVE of an auto document excludes it and a later META never re-adds it", () => {
    let state = chatReducer({ ...initialChatState, auto: [BOLLETTA] }, { type: "REMOVE", id: "doc-1" });
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual(["doc-1"]);
    state = send(state);
    state = chatReducer(state, { type: "META", messageId: "a1", sources: [], grounded: true, autoDocuments: [BOLLETTA] });
    expect(state.auto).toEqual([]);
    expect(state.excluded).toEqual(["doc-1"]);
  });

  it("NEW_CHAT resets to the initial state", () => {
    const busy: ChatState = { ...send(), pinned: [CONTRATTO], auto: [BOLLETTA], excluded: ["doc-3"] };
    expect(chatReducer(busy, { type: "NEW_CHAT" })).toEqual(initialChatState);
  });

  it("nextMessageId returns unique ids", () => {
    const ids = new Set(Array.from({ length: 50 }, () => nextMessageId()));
    expect(ids.size).toBe(50);
  });
});
```

Create `frontend/src/lib/chatRequest.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import type { ChatMessage } from "./chatReducer";
import { buildChatRequest } from "./chatRequest";

const msg = (id: string, role: ChatMessage["role"], content: string, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id,
  role,
  content,
  ...extra,
});

describe("buildChatRequest", () => {
  it("appends the new question and sends pinned and excluded ids", () => {
    const body = buildChatRequest(
      {
        messages: [msg("u1", "user", "Prima?"), msg("a1", "assistant", "Risposta.")],
        pinned: [{ id: "doc-2", title: "Contratto", document_date: "2026-01-10" }],
        excluded: ["doc-1"],
      },
      "Seconda?",
    );
    expect(body).toEqual({
      messages: [
        { role: "user", content: "Prima?" },
        { role: "assistant", content: "Risposta." },
        { role: "user", content: "Seconda?" },
      ],
      pinned_ids: ["doc-2"],
      excluded_ids: ["doc-1"],
    });
  });

  it("skips assistant messages that errored or have no text", () => {
    const body = buildChatRequest(
      {
        messages: [
          msg("u1", "user", "prima"),
          msg("a1", "assistant", "", { error: "Chat request failed" }),
          msg("u2", "user", "seconda"),
          msg("a2", "assistant", ""),
          msg("u3", "user", "terza"),
          msg("a3", "assistant", "mezza risposta", { error: "Connection lost mid-answer" }),
          msg("u4", "user", "quarta"),
          msg("a4", "assistant", "risposta"),
        ],
        pinned: [],
        excluded: [],
      },
      "nuova",
    );
    expect(body.messages).toEqual([
      { role: "user", content: "prima" },
      { role: "user", content: "seconda" },
      { role: "user", content: "terza" },
      { role: "user", content: "quarta" },
      { role: "assistant", content: "risposta" },
      { role: "user", content: "nuova" },
    ]);
  });

  it("sends at most the last 40 messages", () => {
    const messages = Array.from({ length: 50 }, (_, i) => msg(`m${i}`, i % 2 === 0 ? "user" : "assistant", `m${i}`));
    const body = buildChatRequest({ messages, pinned: [], excluded: [] }, "ultima");
    expect(body.messages).toHaveLength(40);
    expect(body.messages[0].content).toBe("m11");
    expect(body.messages[39]).toEqual({ role: "user", content: "ultima" });
  });
});
```

Create `frontend/src/hooks/useDebouncedValue.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useDebouncedValue } from "./useDebouncedValue";

describe("useDebouncedValue", () => {
  afterEach(() => vi.useRealTimers());

  it("publishes the value only after the delay, restarting on every change", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 300), {
      initialProps: { value: "a" },
    });
    expect(result.current).toBe("a");
    rerender({ value: "ab" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    rerender({ value: "abc" });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(result.current).toBe("a");
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current).toBe("abc");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/chatReducer.test.ts src/lib/chatRequest.test.ts src/hooks/useDebouncedValue.test.ts`
Expected: FAIL. The 3 files fail to load with `Failed to resolve import "./chatReducer"`, `"./chatRequest"` and `"./useDebouncedValue"`.

- [ ] **Step 3: Implement**

In `frontend/src/lib/types.ts`, add above `export type ChatEvent`:

```ts
export interface DocRef {
  id: string;
  title: string;
  document_date: string;
}
```

and change the `meta` line of `ChatEvent` to:

```ts
  | { type: "meta"; grounded: boolean; sources: ChatSource[]; auto_documents: DocRef[] }
```

Create `frontend/src/lib/chatReducer.ts`:

```ts
import type { ChatSource, DocRef } from "./types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  grounded?: boolean;
  error?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  pinned: DocRef[];
  auto: DocRef[];
  excluded: string[];
}

export type ChatAction =
  | { type: "SEND"; text: string; userId: string; assistantId: string }
  | { type: "META"; messageId: string; sources: ChatSource[]; grounded: boolean; autoDocuments: DocRef[] }
  | { type: "DELTA"; messageId: string; text: string }
  | { type: "DONE"; messageId: string }
  | { type: "ERROR"; messageId: string; message: string }
  | { type: "ABORT" }
  | { type: "PIN"; doc: DocRef }
  | { type: "REMOVE"; id: string }
  | { type: "NEW_CHAT" };

export const initialChatState: ChatState = { messages: [], streaming: false, pinned: [], auto: [], excluded: [] };

let messageSeq = 0;

/** Unique per page load; crypto.randomUUID() is unavailable on plain-HTTP LAN origins. */
export function nextMessageId(): string {
  messageSeq += 1;
  return `msg-${Date.now().toString(36)}-${messageSeq}`;
}

/** Apply `update` to the streaming assistant reply `messageId`; stale or late events are ignored. */
function updateReply(
  state: ChatState,
  messageId: string,
  update: (message: ChatMessage) => Partial<ChatMessage>,
  streaming = true,
): ChatState {
  const last = state.messages[state.messages.length - 1];
  if (!state.streaming || !last || last.role !== "assistant" || last.id !== messageId) return state;
  return { ...state, streaming, messages: [...state.messages.slice(0, -1), { ...last, ...update(last) }] };
}

function mergeAuto(state: ChatState, docs: DocRef[]): DocRef[] {
  const taken = new Set([...state.pinned.map((d) => d.id), ...state.auto.map((d) => d.id), ...state.excluded]);
  const added: DocRef[] = [];
  for (const doc of docs) {
    if (taken.has(doc.id)) continue;
    taken.add(doc.id);
    added.push(doc);
  }
  return added.length > 0 ? [...state.auto, ...added] : state.auto;
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "SEND":
      if (state.streaming) return state;
      return {
        ...state,
        streaming: true,
        messages: [
          ...state.messages,
          { id: action.userId, role: "user", content: action.text },
          { id: action.assistantId, role: "assistant", content: "" },
        ],
      };
    case "META": {
      const next = updateReply(state, action.messageId, () => ({ sources: action.sources, grounded: action.grounded }));
      return next === state ? state : { ...next, auto: mergeAuto(next, action.autoDocuments) };
    }
    case "DELTA":
      return updateReply(state, action.messageId, (m) => ({ content: m.content + action.text }));
    case "DONE":
      return updateReply(state, action.messageId, () => ({}), false);
    case "ERROR":
      return updateReply(state, action.messageId, () => ({ error: action.message }), false);
    case "ABORT":
      return state.streaming ? { ...state, streaming: false } : state;
    case "PIN":
      if (state.pinned.some((d) => d.id === action.doc.id)) return state;
      return {
        ...state,
        pinned: [...state.pinned, action.doc],
        auto: state.auto.filter((d) => d.id !== action.doc.id),
        excluded: state.excluded.filter((id) => id !== action.doc.id),
      };
    case "REMOVE":
      if (state.pinned.some((d) => d.id === action.id)) {
        return { ...state, pinned: state.pinned.filter((d) => d.id !== action.id) };
      }
      if (state.auto.some((d) => d.id === action.id)) {
        return {
          ...state,
          auto: state.auto.filter((d) => d.id !== action.id),
          excluded: state.excluded.includes(action.id) ? state.excluded : [...state.excluded, action.id],
        };
      }
      return state;
    case "NEW_CHAT":
      return initialChatState;
  }
}
```

Create `frontend/src/lib/chatRequest.ts`:

```ts
import type { ChatState } from "./chatReducer";

export const MAX_REQUEST_MESSAGES = 40;

export interface ChatRequestBody {
  messages: { role: "user" | "assistant"; content: string }[];
  pinned_ids: string[];
  excluded_ids: string[];
}

/** Body for POST /api/chat: history plus the new question, without failed or empty replies. */
export function buildChatRequest(
  state: Pick<ChatState, "messages" | "pinned" | "excluded">,
  text: string,
): ChatRequestBody {
  const history = state.messages
    .filter((m) => m.role === "user" || (!m.error && m.content.trim() !== ""))
    .map(({ role, content }) => ({ role, content }));
  return {
    messages: [...history, { role: "user" as const, content: text }].slice(-MAX_REQUEST_MESSAGES),
    pinned_ids: state.pinned.map((d) => d.id),
    excluded_ids: [...state.excluded],
  };
}
```

Create `frontend/src/hooks/useDebouncedValue.ts`:

```ts
import { useEffect, useState } from "react";

/** `value`, published only after it stopped changing for `delayMs`. */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`
Expected: vitest `107 passed` (89 + 14 reducer + 3 request + 1 debounce). Lint shows only the 3 pre-existing warnings. The build succeeds: `ChatPage.tsx` still compiles, because it only reads `event.grounded` / `event.sources` from `meta`.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add frontend/src/lib/types.ts frontend/src/lib/chatReducer.ts frontend/src/lib/chatReducer.test.ts frontend/src/lib/chatRequest.ts frontend/src/lib/chatRequest.test.ts frontend/src/hooks/useDebouncedValue.ts frontend/src/hooks/useDebouncedValue.test.ts
git commit -m "feat: add chat conversation reducer, request builder and debounce hook

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Markdown rendering with citation links

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (via `npm install`)
- Modify: `frontend/src/lib/citations.ts`, `frontend/src/lib/citations.test.ts`
- Create: `frontend/src/components/chat/Markdown.tsx`, `frontend/src/components/chat/Markdown.test.tsx`

**Interfaces:**
- Consumes: `ChatSource` (`types.ts`).
- Produces:
  - `citations.ts`: `citationsToMarkdown(text: string, sources: ChatSource[]): string`, `isInternalHref(href: string): boolean`, `isCitationLabel(children: unknown): boolean`. `splitCitations` stays until Task 8.
  - `<Markdown text: string sources?: ChatSource[] />`. It renders GFM, makes no raw HTML, keeps the default `urlTransform` (unsafe URLs → empty → rendered as plain text), turns internal paths into react-router `Link`s and opens external links in a new tab.

- [ ] **Step 1: Install the dependencies**

Run: `cd /opt/origami/frontend && grep -E '"(react-markdown|remark-gfm)"' package.json; ls node_modules | grep -E '^(react-markdown|remark-gfm)$'`
Expected: no output (verified on `main`: not present).

Run: `cd /opt/origami/frontend && npm install react-markdown remark-gfm`
Expected: `package.json` `dependencies` gains `"react-markdown": "^10.1.0"` and `"remark-gfm": "^4.0.1"` (or the then-current 10.x / 4.x), and `package-lock.json` is updated.

- [ ] **Step 2: Write the failing tests**

In `frontend/src/lib/citations.test.ts`, change the import line to:

```ts
import { citationsToMarkdown, isCitationLabel, isInternalHref, splitCitations } from "./citations";
import type { ChatSource } from "./types";
```

and append:

```ts
const SOURCES: ChatSource[] = [
  { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 },
  { n: 2, chunk_id: 9, document_id: "doc-2", title: "Contratto", page_number: null },
];

describe("citationsToMarkdown", () => {
  it("turns [n] into links to the cited document, adjacent ones included", () => {
    expect(citationsToMarkdown("Totale 42 euro [1]. Vedi [1][2].", SOURCES)).toBe(
      "Totale 42 euro [\\[1\\]](/documents/doc-1). Vedi [\\[1\\]](/documents/doc-1)[\\[2\\]](/documents/doc-2).",
    );
  });

  it("leaves [n] without a matching source as plain text", () => {
    expect(citationsToMarkdown("Secondo [3] e [0].", SOURCES)).toBe("Secondo [3] e [0].");
  });

  it("does not touch existing markdown links, reference definitions or code", () => {
    const fence = "`".repeat(3);
    const text = `Link [1](https://example.com), nota [2]: testo, codice \`arr[1]\` e\n${fence}\nx[2]\n${fence}`;
    expect(citationsToMarkdown(text, SOURCES)).toBe(text);
  });

  it("returns the text unchanged without sources", () => {
    expect(citationsToMarkdown("Risposta [1].", [])).toBe("Risposta [1].");
  });

  it("recognises app paths and citation labels", () => {
    expect(isInternalHref("/documents/doc-1")).toBe(true);
    expect(isInternalHref("//evil.example/x")).toBe(false);
    expect(isInternalHref("https://example.com")).toBe(false);
    expect(isCitationLabel("[12]")).toBe(true);
    expect(isCitationLabel("Contratto")).toBe(false);
    expect(isCitationLabel(["[1]"])).toBe(false);
  });
});
```

Create `frontend/src/components/chat/Markdown.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { ChatSource } from "@/lib/types";
import { Markdown } from "./Markdown";

const SOURCES: ChatSource[] = [{ n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 }];

function renderMarkdown(text: string, sources: ChatSource[] = SOURCES) {
  return render(
    <MemoryRouter>
      <Markdown text={text} sources={sources} />
    </MemoryRouter>,
  );
}

it("renders GFM tables and lists", () => {
  renderMarkdown("| Mese | Euro |\n|---|---|\n| Marzo | 42 |\n\n- uno\n- **due**");
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "42" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByText("due").tagName).toBe("STRONG");
});

it("renders citation links to the document page", () => {
  renderMarkdown("Hai pagato 42 euro [1].");
  expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "/documents/doc-1");
});

it("never renders raw HTML from the model", () => {
  const { container } = renderMarkdown('Testo <img src="x" onerror="alert(1)"> e <script>alert(1)</script> fine');
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect(container.querySelector("[onerror]")).toBeNull();
});

it("neutralises javascript: links and opens external links in a new tab", () => {
  const { container } = renderMarkdown("[clic](javascript:alert(1)) e [sito](https://example.com)");
  expect(screen.getByText("clic").closest("a")).toBeNull();
  expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  const external = screen.getByRole("link", { name: "sito" });
  expect(external).toHaveAttribute("href", "https://example.com");
  expect(external).toHaveAttribute("target", "_blank");
  expect(external).toHaveAttribute("rel", "noopener noreferrer");
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/citations.test.ts src/components/chat/Markdown.test.tsx`
Expected: FAIL. `citations.test.ts` fails with `citationsToMarkdown is not a function` (or a missing-export error). `Markdown.test.tsx` fails with `Failed to resolve import "./Markdown"`.

- [ ] **Step 4: Implement**

Replace `frontend/src/lib/citations.ts` with:

```ts
import type { ChatSource } from "./types";

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

// Fenced blocks and inline code spans: citations inside them are left alone.
const CODE = /(`{3}[\s\S]*?`{3}|`[^`\n]*`)/;
// [n] not followed by "(" (already a link) or ":" (reference definition).
const CITATION = /\[(\d+)\](?![(:])/g;

/** Rewrite [n] markers as markdown links to the cited document; unknown n stays plain text. */
export function citationsToMarkdown(text: string, sources: ChatSource[]): string {
  if (sources.length === 0) return text;
  const documentByNumber = new Map(sources.map((s) => [s.n, s.document_id]));
  return text
    .split(CODE)
    .map((part, index) =>
      index % 2 === 1
        ? part
        : part.replace(CITATION, (match, n: string) => {
            const documentId = documentByNumber.get(Number(n));
            return documentId ? `[\\[${n}\\]](/documents/${encodeURIComponent(documentId)})` : match;
          }),
    )
    .join("");
}

/** Same-app path (rendered with the router), not a protocol-relative URL. */
export function isInternalHref(href: string): boolean {
  return href.startsWith("/") && !href.startsWith("//");
}

/** Link text produced by citationsToMarkdown, e.g. "[3]". */
export function isCitationLabel(children: unknown): boolean {
  return typeof children === "string" && /^\[\d+\]$/.test(children);
}
```

Create `frontend/src/components/chat/Markdown.tsx`:

```tsx
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";
import { citationsToMarkdown, isCitationLabel, isInternalHref } from "@/lib/citations";
import type { ChatSource } from "@/lib/types";

// Raw HTML stays disabled (react-markdown default, no rehype-raw). The default urlTransform
// empties javascript:/data: URLs, which the link renderer below turns into plain text.
const components: Components = {
  a: ({ href, children }) => {
    if (!href) return <span>{children}</span>;
    if (isInternalHref(href)) {
      return (
        <Link
          to={href}
          className={
            isCitationLabel(children) ? "align-super text-xs font-semibold text-blue-700" : "text-blue-700 underline"
          }
        >
          {children}
        </Link>
      );
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-700 underline">
        {children}
      </a>
    );
  },
  p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  h1: ({ children }) => <h3 className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-3 mb-1 text-base font-semibold">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-3 mb-1 font-semibold">{children}</h4>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-zinc-300 pl-3 text-zinc-600">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border border-zinc-200 bg-zinc-50 px-2 py-1 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border border-zinc-200 px-2 py-1 align-top">{children}</td>,
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded bg-zinc-100 p-3 text-xs [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }) => <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
};

export function Markdown({ text, sources = [] }: { text: string; sources?: ChatSource[] }) {
  return (
    <div className="text-sm break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {citationsToMarkdown(text, sources)}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`
Expected: vitest `116 passed` (107 + 5 citations + 4 Markdown). Lint shows only the 3 pre-existing warnings. The build succeeds.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami
git add frontend/package.json frontend/package-lock.json frontend/src/lib/citations.ts frontend/src/lib/citations.test.ts frontend/src/components/chat/Markdown.tsx frontend/src/components/chat/Markdown.test.tsx
git commit -m "feat: render chat answers as safe Markdown with citation links

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Chat components — MessageList, ChatInput, ContextBar, FilePicker

**Files:**
- Create: `frontend/src/lib/scroll.ts`, `frontend/src/lib/scroll.test.ts`
- Create: `frontend/src/components/chat/MessageList.tsx`, `MessageList.test.tsx`
- Create: `frontend/src/components/chat/ChatInput.tsx`, `ChatInput.test.tsx`
- Create: `frontend/src/components/chat/ContextBar.tsx`, `ContextBar.test.tsx`
- Create: `frontend/src/components/chat/FilePicker.tsx`, `FilePicker.test.tsx`

**Interfaces:**
- Consumes:
  - `ChatMessage` (Task 5), `DocRef` (Task 5), `<Markdown>` (Task 6), `useDebouncedValue` (Task 5).
  - Existing: `DocTypeIcon` (`doc: Pick<Document, "doc_type" | "original_filename">`), `formatDate(iso)` (`lib/dates.ts`), `api.post` (`lib/api.ts`), `SearchResponse` (`types.ts`), and the `Button` UI component.
- Produces:
  - `isNearBottom(scrollTop: number, scrollHeight: number, clientHeight: number, threshold = 40): boolean`.
  - `<MessageList messages: ChatMessage[] streaming: boolean />`.
  - `<ChatInput streaming: boolean onSend: (text: string) => void onStop: () => void />`.
  - `<ContextBar pinned: DocRef[] auto: DocRef[] onPin: (doc: DocRef) => void onRemove: (id: string) => void />`.
  - `<FilePicker pinnedIds: string[] onPick: (doc: DocRef) => void />`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/scroll.test.ts`:

```ts
import { expect, it } from "vitest";
import { isNearBottom } from "./scroll";

it("treats positions within the threshold of the bottom as at the bottom", () => {
  expect(isNearBottom(560, 1000, 400)).toBe(true); // 40 px left
  expect(isNearBottom(500, 1000, 400)).toBe(false); // 100 px left: the user scrolled up
  expect(isNearBottom(0, 300, 400)).toBe(true); // content shorter than the view
});
```

Create `frontend/src/components/chat/MessageList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import type { ChatMessage } from "@/lib/chatReducer";
import { MessageList } from "./MessageList";

it("renders bubbles, sources, the not-grounded banner and the typing indicator", () => {
  const messages: ChatMessage[] = [
    { id: "u1", role: "user", content: "Domanda?" },
    {
      id: "a1",
      role: "assistant",
      content: "Non trovato.",
      grounded: false,
      sources: [{ n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 }],
    },
    { id: "u2", role: "user", content: "Altro?" },
    { id: "a2", role: "assistant", content: "" },
  ];
  render(
    <MemoryRouter>
      <MessageList messages={messages} streaming />
    </MemoryRouter>,
  );
  expect(screen.getByText("Domanda?")).toBeInTheDocument();
  expect(screen.getByText("Non trovato.")).toBeInTheDocument();
  expect(screen.getByText("Answer not based on your documents.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Bolletta marzo" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByText("(p. 2)")).toBeInTheDocument();
  expect(screen.getByRole("status", { name: "Assistant is typing" })).toBeInTheDocument();
});
```

Create `frontend/src/components/chat/ChatInput.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ChatInput } from "./ChatInput";

it("Enter sends the trimmed text and Shift+Enter adds a new line", async () => {
  const onSend = vi.fn();
  render(<ChatInput streaming={false} onSend={onSend} onStop={vi.fn()} />);
  const box = screen.getByRole("textbox", { name: "Message" });
  await userEvent.type(box, "riga uno{Shift>}{Enter}{/Shift}riga due");
  expect(box).toHaveValue("riga uno\nriga due");
  expect(onSend).not.toHaveBeenCalled();
  await userEvent.type(box, "  {Enter}");
  expect(onSend).toHaveBeenCalledWith("riga uno\nriga due");
  expect(box).toHaveValue("");
});

it("while streaming the input is disabled and Stop is offered", async () => {
  const onStop = vi.fn();
  render(<ChatInput streaming onSend={vi.fn()} onStop={onStop} />);
  expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  expect(onStop).toHaveBeenCalledOnce();
});
```

Create `frontend/src/components/chat/ContextBar.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { ContextBar } from "./ContextBar";

it("shows pinned and auto chips with dates and removes a chip on ×", async () => {
  const onRemove = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ContextBar
        pinned={[{ id: "p1", title: "Contratto", document_date: "2026-01-10" }]}
        auto={[{ id: "a1", title: "Bolletta marzo", document_date: "2026-03-15" }]}
        onPin={vi.fn()}
        onRemove={onRemove}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Contratto")).toBeInTheDocument();
  expect(screen.getByText("10/01/2026")).toBeInTheDocument();
  expect(screen.getByText("15/03/2026")).toBeInTheDocument();
  expect(screen.getAllByText("auto")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Remove Bolletta marzo" }));
  expect(onRemove).toHaveBeenCalledWith("a1");
  expect(screen.getByRole("button", { name: "+ Add file" })).toBeInTheDocument();
});
```

Create `frontend/src/components/chat/FilePicker.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FilePicker } from "./FilePicker";

const DOCS = [
  { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15", doc_type: "pdf", original_filename: "b.pdf" },
  { id: "doc-2", title: "Bolletta aprile", document_date: "2026-04-15", doc_type: "pdf", original_filename: "a.pdf" },
];

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () =>
      new Response(
        JSON.stringify({ mode: "hybrid", results: DOCS.map((document) => ({ document, score: 1, snippets: [] })) }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderPicker(onPick = vi.fn(), pinnedIds: string[] = []) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <p>outside</p>
      <FilePicker pinnedIds={pinnedIds} onPick={onPick} />
    </QueryClientProvider>,
  );
  return onPick;
}

it("searches after the debounce, hides pinned files and pins the clicked one", async () => {
  const onPick = renderPicker(vi.fn(), ["doc-2"]);
  await userEvent.click(screen.getByRole("button", { name: "+ Add file" }));
  await userEvent.type(screen.getByRole("textbox", { name: "Search files" }), "bolletta");
  const result = await screen.findByRole("button", { name: /Bolletta marzo/ });
  expect(screen.queryByRole("button", { name: /Bolletta aprile/ })).not.toBeInTheDocument();
  await userEvent.click(result);
  expect(onPick).toHaveBeenCalledWith({ id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" });
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
  const searches = fetchMock.mock.calls.filter(([url]) => url === "/api/search");
  expect(searches).toHaveLength(1);
  expect(JSON.parse(searches[0][1].body)).toEqual({ query: "bolletta", mode: "hybrid", limit: 10 });
});

it("closes on Escape and on a click outside", async () => {
  renderPicker();
  const trigger = screen.getByRole("button", { name: "+ Add file" });
  await userEvent.click(trigger);
  expect(screen.getByRole("dialog", { name: "Add file" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
  await userEvent.click(trigger);
  await userEvent.click(screen.getByText("outside"));
  expect(screen.queryByRole("dialog", { name: "Add file" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/scroll.test.ts src/components/chat`
Expected: FAIL. The 5 new files fail to resolve `./scroll`, `./MessageList`, `./ChatInput`, `./ContextBar` and `./FilePicker`. `Markdown.test.tsx` still passes.

- [ ] **Step 3: Implement**

Create `frontend/src/lib/scroll.ts`:

```ts
/** True when the scroll position is within `threshold` px of the bottom (auto-scroll stays on). */
export function isNearBottom(scrollTop: number, scrollHeight: number, clientHeight: number, threshold = 40): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
```

Create `frontend/src/components/chat/MessageList.tsx`:

```tsx
import { useEffect, useRef } from "react";
import { Link } from "react-router";
import { Markdown } from "@/components/chat/Markdown";
import type { ChatMessage } from "@/lib/chatReducer";
import { isNearBottom } from "@/lib/scroll";

function TypingIndicator() {
  return (
    <span role="status" aria-label="Assistant is typing" className="inline-flex gap-1 py-1">
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:150ms]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-zinc-400 [animation-delay:300ms]" />
    </span>
  );
}

function AssistantMessage({ message, typing }: { message: ChatMessage; typing: boolean }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-2">
        {message.grounded === false && (
          <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            Answer not based on your documents.
          </div>
        )}
        <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-3">
          {typing ? (
            <TypingIndicator />
          ) : message.content ? (
            <Markdown text={message.content} sources={message.sources} />
          ) : (
            !message.error && <p className="text-sm text-zinc-400">No answer.</p>
          )}
          {message.error && <p className="mt-2 text-sm text-red-700">{message.error}</p>}
        </div>
        {message.sources && message.sources.length > 0 && (
          <ol className="space-y-0.5 pl-1 text-xs text-zinc-500">
            {message.sources.map((source) => (
              <li key={source.n}>
                [{source.n}]{" "}
                <Link to={`/documents/${source.document_id}`} className="underline hover:text-zinc-800">
                  {source.title}
                </Link>
                {source.page_number != null && <span> (p. {source.page_number})</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

export function MessageList({ messages, streaming }: { messages: ChatMessage[]; streaming: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true); // follow new content unless the user scrolled up
  const countRef = useRef(messages.length);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length !== countRef.current) {
      countRef.current = messages.length;
      stickRef.current = true; // a new question always brings the view back down
    }
    if (stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = isNearBottom(el.scrollTop, el.scrollHeight, el.clientHeight);
  };

  return (
    <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-6 py-4">
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.length === 0 && (
          <p className="pt-8 text-center text-sm text-zinc-400">
            Ask a question about your documents. Origami picks the relevant files; you can add or remove them above.
          </p>
        )}
        {messages.map((message, index) =>
          message.role === "user" ? (
            <div key={message.id} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl bg-zinc-900 px-4 py-2 text-sm whitespace-pre-wrap text-white">
                {message.content}
              </div>
            </div>
          ) : (
            <AssistantMessage
              key={message.id}
              message={message}
              typing={streaming && index === messages.length - 1 && message.content === ""}
            />
          ),
        )}
      </div>
    </div>
  );
}
```

Create `frontend/src/components/chat/ChatInput.tsx`:

```tsx
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Button } from "@/components/ui/button";

const MAX_HEIGHT_PX = 8 * 20 + 24; // 8 rows of text-sm (20 px line height) + p-3 padding

export function ChatInput({
  streaming,
  onSend,
  onStop,
}: {
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [text]);

  useEffect(() => {
    if (!streaming) ref.current?.focus();
  }, [streaming]);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    onSend(trimmed);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="border-t border-zinc-200 px-6 py-3"
    >
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          ref={ref}
          aria-label="Message"
          rows={1}
          value={text}
          disabled={streaming}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about your documents… (Shift+Enter for a new line)"
          style={{ maxHeight: MAX_HEIGHT_PX }}
          className="w-full resize-none overflow-y-auto rounded-md border border-zinc-300 bg-white p-3 text-sm leading-5 focus:ring-2 focus:ring-zinc-400 focus:outline-none disabled:bg-zinc-50"
        />
        {streaming ? (
          <Button type="button" variant="outline" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="submit" disabled={!text.trim()}>
            Send
          </Button>
        )}
      </div>
    </form>
  );
}
```

(A plain `<textarea>` is used because the `Textarea` UI component's props type has no `ref`.)

Create `frontend/src/components/chat/FilePicker.tsx`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { DocTypeIcon } from "@/components/DocTypeIcon";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/dates";
import type { Document, DocRef, SearchResponse } from "@/lib/types";

const DEBOUNCE_MS = 300;
const RESULT_LIMIT = 10;

export function FilePicker({ pinnedIds, onPick }: { pinnedIds: string[]; onPick: (doc: DocRef) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim(), DEBOUNCE_MS);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // capture phase: an enclosing dialog stays open
      setOpen(false);
      setQuery("");
    };
    const onPointer = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  const search = useQuery({
    queryKey: ["file-picker", debounced],
    queryFn: () =>
      api.post<SearchResponse>("/api/search", { query: debounced, mode: "hybrid", limit: RESULT_LIMIT }),
    enabled: open && debounced.length > 0,
  });
  const docs = (search.data?.results ?? [])
    .map((r) => r.document)
    .filter((d) => !pinnedIds.includes(d.id))
    .slice(0, RESULT_LIMIT);

  const close = () => {
    setOpen(false);
    setQuery("");
  };
  const pick = (doc: Document) => {
    onPick({ id: doc.id, title: doc.title, document_date: doc.document_date });
    close();
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="rounded-full border border-dashed border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-100"
      >
        + Add file
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Add file"
          className="absolute top-full left-0 z-20 mt-1 w-80 rounded-md border border-zinc-200 bg-white p-2 shadow-lg"
        >
          <input
            autoFocus
            aria-label="Search files"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by title or content…"
            className="h-8 w-full rounded-md border border-zinc-300 px-2 text-sm focus:ring-2 focus:ring-zinc-400 focus:outline-none"
          />
          {debounced === "" ? (
            <p className="px-2 py-1 text-sm text-zinc-400">Type to search your documents</p>
          ) : search.isPending ? (
            <p className="px-2 py-1 text-sm text-zinc-400">Searching…</p>
          ) : search.isError ? (
            <p className="px-2 py-1 text-sm text-red-700">Search failed</p>
          ) : docs.length === 0 ? (
            <p className="px-2 py-1 text-sm text-zinc-400">No documents found</p>
          ) : (
            <ul className="mt-1 max-h-72 overflow-y-auto">
              {docs.map((doc) => (
                <li key={doc.id}>
                  <button
                    type="button"
                    onClick={() => pick(doc)}
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-zinc-100"
                  >
                    <DocTypeIcon doc={doc} className="h-4 w-4" />
                    <span className="flex-1 truncate">{doc.title}</span>
                    <span className="shrink-0 text-xs text-zinc-400">{formatDate(doc.document_date)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
```

Create `frontend/src/components/chat/ContextBar.tsx`:

```tsx
import { FilePicker } from "@/components/chat/FilePicker";
import { formatDate } from "@/lib/dates";
import type { DocRef } from "@/lib/types";
import { cn } from "@/lib/utils";

function Chip({ doc, auto = false, onRemove }: { doc: DocRef; auto?: boolean; onRemove: (id: string) => void }) {
  return (
    <span
      className={cn(
        "inline-flex max-w-xs items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
        auto ? "border-blue-200 bg-blue-50" : "border-zinc-300 bg-white",
      )}
    >
      <span className="truncate" title={doc.title}>
        {doc.title}
      </span>
      <span className="shrink-0 text-zinc-400">{formatDate(doc.document_date)}</span>
      {auto && (
        <span className="shrink-0 rounded bg-blue-100 px-1 text-[10px] font-semibold text-blue-700 uppercase">auto</span>
      )}
      <button
        type="button"
        aria-label={`Remove ${doc.title}`}
        onClick={() => onRemove(doc.id)}
        className="shrink-0 text-zinc-400 hover:text-zinc-800"
      >
        ×
      </button>
    </span>
  );
}

export function ContextBar({
  pinned,
  auto,
  onPin,
  onRemove,
}: {
  pinned: DocRef[];
  auto: DocRef[];
  onPin: (doc: DocRef) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="border-b border-zinc-200 px-6 py-2">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-zinc-500">Context:</span>
        {pinned.map((doc) => (
          <Chip key={doc.id} doc={doc} onRemove={onRemove} />
        ))}
        {auto.map((doc) => (
          <Chip key={doc.id} doc={doc} auto onRemove={onRemove} />
        ))}
        {pinned.length === 0 && auto.length === 0 && (
          <span className="text-xs text-zinc-400">Files are picked from your question</span>
        )}
        <FilePicker pinnedIds={pinned.map((d) => d.id)} onPick={onPin} />
      </div>
    </div>
  );
}
```

The "auto" text is lowercase in the DOM and shown in upper case by CSS, so `getAllByText("auto")` matches it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`
Expected: vitest `123 passed` (116 + 1 scroll + 1 MessageList + 2 ChatInput + 1 ContextBar + 2 FilePicker). Lint shows only the 3 pre-existing warnings. The build succeeds.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add frontend/src/lib/scroll.ts frontend/src/lib/scroll.test.ts frontend/src/components/chat/MessageList.tsx frontend/src/components/chat/MessageList.test.tsx frontend/src/components/chat/ChatInput.tsx frontend/src/components/chat/ChatInput.test.tsx frontend/src/components/chat/ContextBar.tsx frontend/src/components/chat/ContextBar.test.tsx frontend/src/components/chat/FilePicker.tsx frontend/src/components/chat/FilePicker.test.tsx
git commit -m "feat: add chat message list, input, context chips and file picker

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: ChatPage wiring

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx` (rewritten)
- Create: `frontend/src/pages/ChatPage.test.tsx`
- Modify: `frontend/src/lib/citations.ts`, `frontend/src/lib/citations.test.ts` (remove `splitCitations`)

**Interfaces:**
- Consumes:
  - `chatReducer`, `initialChatState`, `nextMessageId` (Task 5); `buildChatRequest` (Task 5).
  - `MessageList`, `ChatInput`, `ContextBar` (Task 7).
  - Existing `parseSSEStream` (`lib/sse.ts`) and `getToken` (`lib/api.ts`).
  - `POST /api/chat` (Task 4).
- Produces: `<ChatPage />`, route `/chat`, unchanged in `App.tsx`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/pages/ChatPage.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatPage } from "./ChatPage";

const fetchMock = vi.fn();
beforeEach(() => vi.stubGlobal("fetch", fetchMock));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const BOLLETTA = { id: "doc-1", title: "Bolletta marzo", document_date: "2026-03-15" };
const SOURCE = { n: 1, chunk_id: 7, document_id: "doc-1", title: "Bolletta marzo", page_number: 2 };

const frame = (event: unknown) => `data: ${JSON.stringify(event)}\n\n`;

function answer(text: string, auto = [BOLLETTA]) {
  const events = [
    { type: "meta", grounded: true, sources: [SOURCE], auto_documents: auto },
    { type: "delta", text },
    { type: "done" },
  ];
  return new Response(events.map(frame).join(""), { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function chatBodies() {
  return fetchMock.mock.calls.filter(([url]) => url === "/api/chat").map(([, init]) => JSON.parse(init.body));
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function ask(text: string) {
  await userEvent.type(screen.getByRole("textbox", { name: "Message" }), `${text}{Enter}`);
}

it("sends the question and renders the markdown answer, its citation and the auto chip", async () => {
  fetchMock.mockImplementation(async () => answer("Hai pagato **42 euro** [1]."));
  renderPage();
  await ask("Quanto ho pagato?");
  expect((await screen.findByText("42 euro")).tagName).toBe("STRONG");
  expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "/documents/doc-1");
  expect(screen.getByRole("button", { name: "Remove Bolletta marzo" })).toBeInTheDocument();
  expect(screen.getByText("auto")).toBeInTheDocument();
  expect(chatBodies()).toEqual([
    { messages: [{ role: "user", content: "Quanto ho pagato?" }], pinned_ids: [], excluded_ids: [] },
  ]);
});

it("sends the history on follow-ups and keeps a removed auto file excluded", async () => {
  fetchMock.mockImplementationOnce(async () => answer("Prima risposta [1].")).mockImplementationOnce(async () => answer("Seconda risposta."));
  renderPage();
  await ask("Quanto ho pagato?");
  await screen.findByText(/Prima risposta/);
  await userEvent.click(screen.getByRole("button", { name: "Remove Bolletta marzo" }));
  await ask("E a febbraio?");
  await screen.findByText("Seconda risposta.");
  expect(chatBodies()[1]).toEqual({
    messages: [
      { role: "user", content: "Quanto ho pagato?" },
      { role: "assistant", content: "Prima risposta [1]." },
      { role: "user", content: "E a febbraio?" },
    ],
    pinned_ids: [],
    excluded_ids: ["doc-1"],
  });
  expect(screen.queryByRole("button", { name: "Remove Bolletta marzo" })).not.toBeInTheDocument();
});

it("New chat asks for confirmation before clearing the conversation", async () => {
  fetchMock.mockImplementation(async () => answer("Risposta."));
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  renderPage();
  await ask("Domanda?");
  await screen.findByText("Risposta.");
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(screen.getByText("Risposta.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(confirm).toHaveBeenCalledWith("Start a new chat? The current conversation will be cleared.");
  expect(screen.queryByText("Risposta.")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove Bolletta marzo" })).not.toBeInTheDocument();
});

it("Stop keeps the partial answer and the next question gets its own reply", async () => {
  let calls = 0;
  fetchMock.mockImplementation(async (_url: string, init: RequestInit) => {
    calls += 1;
    if (calls > 1) return answer("Seconda risposta.", []);
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(frame({ type: "meta", grounded: true, sources: [], auto_documents: [] })));
        controller.enqueue(encoder.encode(frame({ type: "delta", text: "Risposta parz" })));
        init.signal?.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  });
  renderPage();
  await ask("prima");
  expect(await screen.findByText("Risposta parz")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop" }));
  await ask("seconda");
  expect(await screen.findByText("Seconda risposta.")).toBeInTheDocument();
  expect(screen.getByText("Risposta parz")).toBeInTheDocument();
  expect(screen.queryByText(/Connection lost/)).not.toBeInTheDocument();
  expect(chatBodies()[1].messages).toEqual([
    { role: "user", content: "prima" },
    { role: "assistant", content: "Risposta parz" },
    { role: "user", content: "seconda" },
  ]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /opt/origami/frontend && npx vitest run src/pages/ChatPage.test.tsx`
Expected: FAIL. The old page has no textbox named `Message` (`Unable to find an accessible element with the role "textbox" and name "Message"`).

- [ ] **Step 3: Rewrite `ChatPage.tsx`**

Replace `frontend/src/pages/ChatPage.tsx` with:

```tsx
import { useEffect, useReducer, useRef } from "react";
import { ChatInput } from "@/components/chat/ChatInput";
import { ContextBar } from "@/components/chat/ContextBar";
import { MessageList } from "@/components/chat/MessageList";
import { Button } from "@/components/ui/button";
import { getToken } from "@/lib/api";
import { chatReducer, initialChatState, nextMessageId } from "@/lib/chatReducer";
import { buildChatRequest } from "@/lib/chatRequest";
import { parseSSEStream } from "@/lib/sse";

const NEW_CHAT_CONFIRM = "Start a new chat? The current conversation will be cleared.";

export function ChatPage() {
  const [state, dispatch] = useReducer(chatReducer, initialChatState);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []); // leaving the page stops the stream

  const send = async (text: string) => {
    if (state.streaming) return;
    const body = buildChatRequest(state, text);
    const assistantId = nextMessageId();
    dispatch({ type: "SEND", text, userId: nextMessageId(), assistantId });
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        dispatch({ type: "ERROR", messageId: assistantId, message: "Chat request failed" });
        return;
      }
      for await (const event of parseSSEStream(resp.body)) {
        if (controller.signal.aborted) break;
        if (event.type === "meta") {
          dispatch({
            type: "META",
            messageId: assistantId,
            sources: event.sources,
            grounded: event.grounded,
            autoDocuments: event.auto_documents,
          });
        } else if (event.type === "delta") {
          dispatch({ type: "DELTA", messageId: assistantId, text: event.text });
        } else if (event.type === "error") {
          dispatch({ type: "ERROR", messageId: assistantId, message: event.message });
        } else if (event.type === "done") {
          dispatch({ type: "DONE", messageId: assistantId });
        }
      }
      dispatch({ type: "DONE", messageId: assistantId }); // stream closed without "done"; no-op otherwise
    } catch {
      if (!controller.signal.aborted) {
        dispatch({ type: "ERROR", messageId: assistantId, message: "Connection lost mid-answer" });
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  };

  const stop = () => {
    controllerRef.current?.abort();
    dispatch({ type: "ABORT" });
  };

  const newChat = () => {
    if (state.messages.length > 0 && !window.confirm(NEW_CHAT_CONFIRM)) return;
    controllerRef.current?.abort();
    dispatch({ type: "NEW_CHAT" });
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-zinc-200 px-6 py-3">
        <h2 className="text-lg font-semibold">Ask your documents</h2>
        <Button variant="outline" size="sm" onClick={newChat}>
          New chat
        </Button>
      </header>
      <ContextBar
        pinned={state.pinned}
        auto={state.auto}
        onPin={(doc) => dispatch({ type: "PIN", doc })}
        onRemove={(id) => dispatch({ type: "REMOVE", id })}
      />
      <MessageList messages={state.messages} streaming={state.streaming} />
      <ChatInput streaming={state.streaming} onSend={send} onStop={stop} />
    </div>
  );
}
```

- [ ] **Step 4: Remove the now-unused `splitCitations`**

In `frontend/src/lib/citations.ts`, delete the `CitationPart` type and the `splitCitations` function (the first block of the file, up to and including its closing `}`). In `frontend/src/lib/citations.test.ts`, delete the whole `describe("splitCitations", ...)` block and change the import to:

```ts
import { citationsToMarkdown, isCitationLabel, isInternalHref } from "./citations";
```

Run: `cd /opt/origami/frontend && grep -rn "splitCitations" src` → no output.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`
Expected: vitest `125 passed` (123 + 4 ChatPage − 2 `splitCitations`). Lint shows only the 3 pre-existing warnings. The build succeeds.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami
git add frontend/src/pages/ChatPage.tsx frontend/src/pages/ChatPage.test.tsx frontend/src/lib/citations.ts frontend/src/lib/citations.test.ts
git commit -m "feat: multi-turn chat page with context chips, file picker and stop

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Full verification and manual checks

**Files:**
- No code changes expected. If a manual check finds a bug, write a failing test first in the task that owns the code, fix it, and commit separately.

**Interfaces:**
- Consumes: everything above.
- Produces: a verified release; the user restarts the service.

- [ ] **Step 1: Full suites**

Run:

```bash
cd /opt/origami/backend && uv run pytest -q
cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build
```

Expected: backend `308 passed` with no warnings. Frontend `125 passed`, lint shows only the 3 pre-existing `only-export-components` warnings, and the build succeeds. `git status` shows only the user's `deploy/origami.service`, `deploy/origami.sh` (modified) and `.env.example.save` (untracked). `git log --oneline -8` shows the 8 task commits.

- [ ] **Step 2: Restart the service (human action, needs sudo)**

Ask the user to run `! sudo systemctl restart origami`. Then check `systemctl status origami --no-pager` → `active (running)`. There is no migration: `cd /opt/origami/backend && uv run alembic current` prints the same head as before this plan.

- [ ] **Step 3: Manual checks in the browser (human action)**

Open `/chat` and check:
1. **Follow-up through history:** ask "Quanto ho pagato la bolletta della luce?", then "E quella di marzo?". The second answer uses the context of the first, and the auto chips show the bill(s) with an `AUTO` badge and their dates.
2. **Pinning:** click `+ Add file` and type part of a title. Results appear after a short pause with type icon, title and date. Click one: it becomes a normal chip. Ask about it: its passages are cited (`[n]` links open `/documents/<id>`).
3. **Sticky exclusion:** remove an auto chip with ×. Ask another question that would match it. The chip does not come back and that document is not in the sources.
4. **Markdown:** ask "Fammi una tabella delle bollette con mese e importo". A table renders. A list question renders bullets, and bold text renders bold.
5. **Stop:** ask a long question and press `Stop` mid-answer. The partial text stays and the input is enabled again. Ask again: the new reply appears in a new bubble.
6. **New chat:** press `New chat`, confirm, and check that messages and chips are cleared.
7. **Privacy check:** in the service log (`journalctl -u origami -n 50 --no-pager`) there is no "chat stream failed". If the preflight provider errors, there is a "document preflight failed" entry and the chat still answers.

- [ ] **Step 4: Report**

Report the final test counts (backend 308, frontend 125) and the manual check results. Remind the user that `deploy/origami.service`, `deploy/origami.sh` and `.env.example.save` were left untouched.

---

## Self-review (done while writing)

- **Spec coverage:**
  - §3.1: Task 1.
  - §3.2: Task 2, with the stub in Task 4.
  - §3.3: Tasks 3 and 4.
  - §3.4:
    - Body, validation and 422: Task 4.
    - Trimming: `trim_history`, Task 4.
    - Flow steps 1–5: `stream_chat`, Task 4.
    - `meta.auto_documents`: Task 4.
    - System prompt: Task 4.
  - §3.5: FilePicker uses `POST /api/search` (Task 7; ambiguity 1).
  - §4.1: Task 5.
  - §4.2:
    - MessageList, ChatInput, ContextBar and FilePicker: Task 7.
    - Markdown: Task 6.
    - Header "New chat", ChatPage wiring and the request body: Task 8.
    - Dependencies: Task 6.
  - §5:
    - Backend tests: Tasks 1–4.
    - Frontend tests: reducer, request builder and debounce in Task 5; citation transform in Task 6.
    - Manual checks: Task 9.
- **Placeholders:** none. Each code step shows complete code or an exact merge instruction (Task 3 `retrieve` head, Task 4 `llm_stub`, the `test_rag.py` call edits, Task 8 `splitCitations` removal).
- **Type consistency:**
  - `shortlist_documents(..., exclude_ids=)`, `Candidate`, `select_documents(question, history, candidates)`, `retrieve(session, query, document_ids)` and `stream_chat(session, messages, pinned_ids, excluded_ids)` match across Tasks 1–4.
  - Frontend: `DocRef`, `ChatMessage`, `ChatState`, the action shapes with `messageId`, and `buildChatRequest(state, text)` match across Tasks 5–8. The `meta` event field is `auto_documents` on the wire and `autoDocuments` in the action.
- **Counts:**
  - Backend: 272 → 281 → 289 → 293 → 308.
  - Frontend: 89 → 107 → 116 → 123 → 125.
