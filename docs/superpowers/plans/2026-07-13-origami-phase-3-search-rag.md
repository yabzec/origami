# Origami Phase 3 — Hybrid Search + RAG Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Documents get FOUND — keyword, semantic, and hybrid (RRF) search over the chunks Phase 2 populated, plus a streaming RAG chat endpoint that answers questions with `[n]` citations and an explicit grounded/not-grounded signal.

**Architecture:** Two new service modules: `search.py` (semantic via pgvector cosine, keyword via Postgres FTS `websearch_to_tsquery` + `ts_headline`, Reciprocal Rank Fusion, folder-subtree/tag/doc_type filters) and `rag.py` (hybrid retrieval → numbered-source prompt → streamed completion with a relevance-floor grounding flag). Two new routers: `POST /api/search` (grouped-by-document results with snippets) and `POST /api/chat` (SSE stream: one `meta` event with grounded+sources, then `delta` token events, then `done`). `llm.py` gains `complete()` with streaming — still the only mock boundary.

**Tech Stack:** pgvector cosine distance (SQLAlchemy comparator), Postgres FTS (`simple` config, matching the Phase 1 `content_tsv` generated column), LiteLLM streaming completions, FastAPI `StreamingResponse` SSE.

**Spec:** `docs/superpowers/specs/2026-07-12-origami-dms-design.md` §6 (Search & RAG)

## Global Constraints

- Everything from Phases 1–2 still binds: real Postgres in tests (never mocked), error shape `{"error": {code, message, detail}}` via `api_error()` + exception handlers, all routes under `/api/` behind JWT, Conventional Commits, pristine test output (no warnings).
- **The ONLY permitted mock boundary is `app/services/llm.py`** — tests monkeypatch the `llm_embed`/`llm_complete` aliases imported into `search.py`/`rag.py` (same pattern as Phase 2's `pipeline.llm_embed`). FTS queries, pgvector queries, RRF, grouping, and SSE framing all run for real.
- Search modes: `semantic | keyword | hybrid`, default `hybrid` (spec §6). Keyword uses `websearch_to_tsquery('simple', ...)` — the `simple` config, matching the mixed-ita/eng `content_tsv` column from Phase 1.
- Hybrid ranking is Reciprocal Rank Fusion with k=60, no tuning parameters (spec Decisions log).
- Filters: folder (whole subtree), tags, doc_type. Results grouped by document with highlighted snippets and page numbers.
- RAG chat: retrieve top-`RAG_TOP_K` (default 8) hybrid chunks → numbered sources → answer in the user's language with `[n]` citations → response carries the source list. Stateless single-turn (spec §6).
- Grounding signal (spec §6): if no retrieved chunk clears the relevance floor (`RAG_RELEVANCE_FLOOR`, cosine similarity, default 0.35), the response is flagged `grounded: false`; the system prompt additionally instructs the model to state in-answer when it draws on knowledge outside the sources. Every response carries `grounded` + the source list.
- Deterministic tests: semantic-search tests seed chunks with hand-constructed basis vectors (dimension 1536) and stub the query embedding, so cosine ranking is exact; keyword/FTS tests run fully real.
- Git hygiene: stage specific files only, never `git add -A`/`.` (untracked `graphify-out/` must never be committed).
- Run tests with `cd backend && uv run pytest ...`; if `uv` is not on PATH use `.venv/bin/python -m pytest ...`.

## Interfaces inherited from Phases 1–2 (already on master)

- `app.services.llm`: `embed(texts: list[str]) -> list[list[float]]`, `describe(...)`. Settings: `llm_model`, `embedding_model`, `embedding_dim=1536`.
- Models: `Chunk(id, document_id: UUID, chunk_index, page_number, source, content, embedding: Vector(1536) nullable)` — plus the migration-only `content_tsv` generated tsvector column (`'simple'` config, GIN-indexed) and an HNSW cosine index on `embedding`. `Document`, `Folder(parent_id self-FK)`, `Tag`, `DocumentTag`, `DocType`, `DocStatus`, `ChunkSource`.
- `app.api.deps`: `api_error(...)`, `get_current_user`. `app.api.documents.serialize(session, doc) -> dict`.
- `app.api.error_handlers.register_error_handlers(app)` — flattened `{"error": {...}}` everywhere.
- Test fixtures: `engine`, `session`, `client`, `user`, `auth_client`, `storage`; `tests/helpers.py` (`make_text_image`).
- pgvector's SQLAlchemy `Vector` column supports `Chunk.embedding.cosine_distance(vec)` (cosine distance = 1 − cosine similarity).

---

### Task 1: `llm.complete()` with streaming

**Files:**
- Modify: `backend/app/services/llm.py`
- Test: extend `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings.llm_model`, litellm.
- Produces: `app.services.llm.complete(messages: list[dict], stream: bool = False) -> str | Iterator[str]` — non-stream returns the full assistant text; stream returns an iterator of non-empty text deltas. RAG (Task 4) imports it as `llm_complete`.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_llm.py`:

```python
def test_complete_non_stream(monkeypatch):
    def fake_completion(model, messages, stream=False):
        assert stream is False
        assert model == "gemini/gemini-2.5-flash"
        msg = SimpleNamespace(content="Risposta completa.")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert llm.complete([{"role": "user", "content": "ciao"}]) == "Risposta completa."


def test_complete_stream_yields_deltas(monkeypatch):
    def fake_completion(model, messages, stream=False):
        assert stream is True

        def chunks():
            for piece in ["Ecco ", None, "la risposta.", ""]:
                delta = SimpleNamespace(content=piece)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        return chunks()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    deltas = list(llm.complete([{"role": "user", "content": "ciao"}], stream=True))
    assert deltas == ["Ecco ", "la risposta."]  # None/empty deltas filtered out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: 2 new FAIL (AttributeError: complete), 3 existing PASS

- [ ] **Step 3: Implement**

Append to `backend/app/services/llm.py`:

```python
def complete(messages: list[dict], stream: bool = False):
    settings = get_settings()
    resp = litellm.completion(model=settings.llm_model, messages=messages, stream=stream)
    if not stream:
        return resp.choices[0].message.content

    def deltas():
        for chunk in resp:
            piece = chunk.choices[0].delta.content
            if piece:
                yield piece

    return deltas()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_llm.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/llm.py backend/tests/test_llm.py
git commit -m "feat: streaming complete() on the LLM service"
```

---

### Task 2: Search service core — semantic, keyword, RRF, filters

**Files:**
- Create: `backend/app/services/search.py`
- Modify: `backend/tests/helpers.py` (add `basis_vector` + `seed_document` helpers)
- Test: `backend/tests/test_search_core.py`

**Interfaces:**
- Consumes: `Chunk`/`Document`/`DocumentTag`/`Folder` models, `content_tsv` column (raw SQL), pgvector `cosine_distance`, `llm.embed` (imported as `llm_embed` — Task 3 uses it; this task's functions take a ready query vector and do NOT call the LLM).
- Produces (all in `app.services.search`):
  - `SearchFilters(BaseModel)`: `folder_id: int | None = None`, `tag_ids: list[int] = []`, `doc_type: str | None = None`.
  - `descendant_folder_ids(session, folder_id: int) -> list[int]` — the folder and its whole subtree.
  - `allowed_document_ids(session, filters: SearchFilters) -> list[uuid.UUID] | None` — `None` means "no filtering"; an empty list means "filters matched nothing".
  - `SemanticHit` dataclass: `chunk_id: int`, `similarity: float` (= 1 − cosine distance).
  - `KeywordHit` dataclass: `chunk_id: int`, `rank: float`, `snippet: str` (ts_headline output, `<b>…</b>` highlights).
  - `semantic_search(session, query_vector: list[float], limit: int = 20, doc_ids: list[uuid.UUID] | None = None) -> list[SemanticHit]` — ordered best-first, skips NULL-embedding chunks.
  - `keyword_search(session, query: str, limit: int = 20, doc_ids: list[uuid.UUID] | None = None) -> list[KeywordHit]` — ordered by `ts_rank` desc.
  - `rrf_fuse(rankings: list[list[int]], k: int = 60) -> list[int]` — RRF-fused chunk ids, best first, ties broken by chunk id.
  - Module-level alias `from app.services.llm import embed as llm_embed` (Task 3's `search()` and tests' monkeypatching rely on this exact name).

- [ ] **Step 1: Add test helpers**

Append to `backend/tests/helpers.py`:

```python
import uuid


def basis_vector(index: int, dim: int = 1536) -> list[float]:
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


def seed_document(session, title: str, chunk_specs: list[dict], **doc_kwargs):
    """Insert a ready Document plus chunks. Each spec: {content, embedding?, page_number?, source?}."""
    from app.models import Chunk, ChunkSource, DocStatus, DocType, Document

    doc = Document(
        title=title,
        doc_type=doc_kwargs.pop("doc_type", DocType.text),
        status=doc_kwargs.pop("status", DocStatus.ready),
        **doc_kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    for index, spec in enumerate(chunk_specs):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=index,
                page_number=spec.get("page_number"),
                source=spec.get("source", ChunkSource.content),
                content=spec["content"],
                embedding=spec.get("embedding"),
            )
        )
    session.commit()
    return doc
```

- [ ] **Step 2: Write failing tests**

`backend/tests/test_search_core.py`:

```python
import pytest

from app.models import DocType, DocumentTag, Tag
from app.services.search import (
    SearchFilters,
    allowed_document_ids,
    descendant_folder_ids,
    keyword_search,
    rrf_fuse,
    semantic_search,
)
from tests.helpers import basis_vector, seed_document


def test_semantic_search_orders_by_cosine_similarity(session):
    doc_a = seed_document(session, "A", [{"content": "alpha", "embedding": basis_vector(0)}])
    doc_b = seed_document(session, "B", [{"content": "beta", "embedding": basis_vector(1)}])
    seed_document(session, "no-embedding", [{"content": "skipped"}])

    hits = semantic_search(session, basis_vector(0))
    assert len(hits) == 2  # NULL-embedding chunk skipped
    assert hits[0].similarity == pytest.approx(1.0)
    assert hits[1].similarity == pytest.approx(0.0)
    first_chunk_docs = {h.chunk_id for h in hits}
    assert len(first_chunk_docs) == 2


def test_semantic_search_respects_doc_filter(session):
    doc_a = seed_document(session, "A", [{"content": "alpha", "embedding": basis_vector(0)}])
    doc_b = seed_document(session, "B", [{"content": "beta", "embedding": basis_vector(1)}])

    hits = semantic_search(session, basis_vector(0), doc_ids=[doc_b.id])
    assert len(hits) == 1


def test_keyword_search_ranks_and_highlights(session):
    seed_document(session, "Bolletta", [{"content": "La bolletta della luce di marzo.", "page_number": 2}])
    seed_document(session, "Altro", [{"content": "Un contratto di affitto."}])

    hits = keyword_search(session, "bolletta luce")
    assert len(hits) == 1
    assert "<b>" in hits[0].snippet
    assert hits[0].rank > 0


def test_keyword_search_empty_on_no_match(session):
    seed_document(session, "Doc", [{"content": "contenuto qualunque"}])
    assert keyword_search(session, "zzzzimprobabile") == []


def test_rrf_fuse_combines_rankings():
    fused = rrf_fuse([[1, 2, 3], [3, 1, 4]])
    assert fused[0] == 1  # ranks 1st and 2nd
    assert 3 in fused and 4 in fused and 2 in fused
    assert rrf_fuse([[], []]) == []


def test_descendant_folder_ids_walks_subtree(auth_client, session):
    root = auth_client.post("/api/folders", json={"name": "root"}).json()["id"]
    child = auth_client.post("/api/folders", json={"name": "c", "parent_id": root}).json()["id"]
    grandchild = auth_client.post("/api/folders", json={"name": "g", "parent_id": child}).json()["id"]
    auth_client.post("/api/folders", json={"name": "other"})

    assert set(descendant_folder_ids(session, root)) == {root, child, grandchild}


def test_allowed_document_ids_filters(session, auth_client):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    tag = Tag(name="fiscale")
    session.add(tag)
    session.commit()

    doc_in = seed_document(session, "in", [{"content": "x"}], folder_id=folder_id)
    doc_tagged = seed_document(session, "tagged", [{"content": "y"}])
    session.add(DocumentTag(document_id=doc_tagged.id, tag_id=tag.id))
    seed_document(session, "video", [{"content": "z"}], doc_type=DocType.video)
    session.commit()

    assert allowed_document_ids(session, SearchFilters()) is None
    assert allowed_document_ids(session, SearchFilters(folder_id=folder_id)) == [doc_in.id]
    assert allowed_document_ids(session, SearchFilters(tag_ids=[tag.id])) == [doc_tagged.id]
    by_type = allowed_document_ids(session, SearchFilters(doc_type=DocType.video))
    assert len(by_type) == 1
    assert allowed_document_ids(session, SearchFilters(folder_id=folder_id, tag_ids=[tag.id])) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_search_core.py -v`
Expected: FAIL (ImportError: app.services.search)

- [ ] **Step 4: Implement**

`backend/app/services/search.py`:

```python
import uuid
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from app.models import Chunk, Document, DocumentTag, Folder
from app.services.llm import embed as llm_embed  # noqa: F401  (used by search(); monkeypatched in tests)

RRF_K = 60


class SearchFilters(BaseModel):
    folder_id: int | None = None
    tag_ids: list[int] = []
    doc_type: str | None = None


@dataclass
class SemanticHit:
    chunk_id: int
    similarity: float


@dataclass
class KeywordHit:
    chunk_id: int
    rank: float
    snippet: str


def descendant_folder_ids(session: Session, folder_id: int) -> list[int]:
    ids = [folder_id]
    frontier = [folder_id]
    while frontier:
        children = list(session.exec(select(Folder.id).where(Folder.parent_id.in_(frontier))))
        frontier = children
        ids.extend(children)
    return ids


def allowed_document_ids(
    session: Session, filters: SearchFilters
) -> list[uuid.UUID] | None:
    """None = unfiltered; [] = filters exclude everything."""
    if filters.folder_id is None and not filters.tag_ids and filters.doc_type is None:
        return None
    query = select(Document.id)
    if filters.folder_id is not None:
        query = query.where(Document.folder_id.in_(descendant_folder_ids(session, filters.folder_id)))
    if filters.doc_type is not None:
        query = query.where(Document.doc_type == filters.doc_type)
    if filters.tag_ids:
        query = query.join(DocumentTag, DocumentTag.document_id == Document.id).where(
            DocumentTag.tag_id.in_(filters.tag_ids)
        )
    return list(session.exec(query.distinct()))


def semantic_search(
    session: Session,
    query_vector: list[float],
    limit: int = 20,
    doc_ids: list[uuid.UUID] | None = None,
) -> list[SemanticHit]:
    distance = Chunk.embedding.cosine_distance(query_vector)
    query = select(Chunk.id, distance.label("distance")).where(Chunk.embedding.is_not(None))
    if doc_ids is not None:
        query = query.where(Chunk.document_id.in_(doc_ids))
    query = query.order_by(distance).limit(limit)
    return [
        SemanticHit(chunk_id=chunk_id, similarity=1.0 - dist)
        for chunk_id, dist in session.exec(query)
    ]


KEYWORD_SQL_BASE = """
SELECT c.id,
       ts_rank(c.content_tsv, q) AS rank,
       ts_headline('simple', c.content, q, 'MaxFragments=2, MaxWords=25, MinWords=5') AS snippet
FROM chunks c, websearch_to_tsquery('simple', :query) q
WHERE c.content_tsv @@ q
{doc_filter}
ORDER BY rank DESC, c.id
LIMIT :limit
"""


def keyword_search(
    session: Session,
    query: str,
    limit: int = 20,
    doc_ids: list[uuid.UUID] | None = None,
) -> list[KeywordHit]:
    doc_filter = "AND c.document_id = ANY(:doc_ids)" if doc_ids is not None else ""
    params: dict = {"query": query, "limit": limit}
    if doc_ids is not None:
        params["doc_ids"] = doc_ids
    rows = session.execute(text(KEYWORD_SQL_BASE.format(doc_filter=doc_filter)), params)
    return [KeywordHit(chunk_id=r[0], rank=r[1], snippet=r[2]) for r in rows]


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_search_core.py -v`
Expected: 7 PASS

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/services/search.py backend/tests/test_search_core.py backend/tests/helpers.py
git commit -m "feat: semantic, keyword, and RRF search primitives with filters"
```

---

### Task 3: `search()` grouping + `POST /api/search`

**Files:**
- Modify: `backend/app/services/search.py` (add `search()`)
- Create: `backend/app/api/search.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_search_api.py`

**Interfaces:**
- Consumes: Task 2 primitives, `llm_embed` alias, `serialize` from `app.api.documents`.
- Produces:
  - `app.services.search.search(session, query: str, mode: str = "hybrid", filters: SearchFilters | None = None, limit: int = 10) -> list[dict]` — each dict: `{"document_id": UUID, "score": float, "snippets": [{"chunk_id", "page_number", "source", "text", "similarity"}]}` grouped by document in rank order, max 3 snippets per document, at most `limit` documents. `text` is the ts_headline snippet when the chunk matched by keyword, else the first 250 chars of the chunk. `score` = 1/(position+1) of the document's best chunk in the fused/mode ranking.
  - `POST /api/search` (JWT): body `{"query": str (min_length 1), "mode": "semantic"|"keyword"|"hybrid" = "hybrid", "filters": SearchFilters = {}, "limit": int = 10}` → `{"mode": ..., "results": [{"document": <serialized doc>, "score": ..., "snippets": [...]}]}`. 422 (validation) for empty query or bad mode.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_search_api.py`:

```python
import pytest

from app.services import search as search_module
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def embed_stub(monkeypatch):
    """Query embedding = basis_vector(0): matches the 'alpha' chunk exactly."""
    monkeypatch.setattr(search_module, "llm_embed", lambda texts: [basis_vector(0)])


def seed_corpus(session):
    doc_sem = seed_document(
        session, "Semantico",
        [{"content": "alpha content", "embedding": basis_vector(0), "page_number": 1}],
    )
    doc_kw = seed_document(
        session, "Bolletta luce",
        [{"content": "La bolletta della luce di marzo.", "embedding": basis_vector(5), "page_number": 3}],
    )
    return doc_sem, doc_kw


def test_hybrid_search_groups_by_document(auth_client, session, embed_stub):
    doc_sem, doc_kw = seed_corpus(session)
    resp = auth_client.post("/api/search", json={"query": "bolletta luce"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "hybrid"
    ids = [r["document"]["id"] for r in body["results"]]
    assert str(doc_sem.id) in ids and str(doc_kw.id) in ids
    kw_result = next(r for r in body["results"] if r["document"]["id"] == str(doc_kw.id))
    assert "<b>" in kw_result["snippets"][0]["text"]
    assert kw_result["snippets"][0]["page_number"] == 3


def test_semantic_mode(auth_client, session, embed_stub):
    doc_sem, _ = seed_corpus(session)
    resp = auth_client.post("/api/search", json={"query": "qualcosa", "mode": "semantic"})
    results = resp.json()["results"]
    assert results[0]["document"]["id"] == str(doc_sem.id)
    assert results[0]["snippets"][0]["similarity"] == pytest.approx(1.0)


def test_keyword_mode_no_llm_call(auth_client, session, monkeypatch):
    seed_corpus(session)

    def boom(texts):
        raise AssertionError("keyword mode must not embed")

    monkeypatch.setattr(search_module, "llm_embed", boom)
    resp = auth_client.post("/api/search", json={"query": "bolletta", "mode": "keyword"})
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


def test_filters_narrow_results(auth_client, session, embed_stub):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    seed_document(
        session, "Dentro",
        [{"content": "La bolletta della luce.", "embedding": basis_vector(1)}],
        folder_id=folder_id,
    )
    seed_corpus(session)

    resp = auth_client.post(
        "/api/search",
        json={"query": "bolletta", "mode": "keyword", "filters": {"folder_id": folder_id}},
    )
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["document"]["title"] == "Dentro"


def test_empty_query_422(auth_client):
    resp = auth_client.post("/api/search", json={"query": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_bad_mode_422(auth_client):
    resp = auth_client.post("/api/search", json={"query": "x", "mode": "telepathic"})
    assert resp.status_code == 422


def test_search_requires_auth(client):
    assert client.post("/api/search", json={"query": "x"}).status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_search_api.py -v`
Expected: FAIL (404s — route missing)

- [ ] **Step 3: Implement `search()`**

Append to `backend/app/services/search.py`:

```python
def search(
    session: Session,
    query: str,
    mode: str = "hybrid",
    filters: SearchFilters | None = None,
    limit: int = 10,
) -> list[dict]:
    filters = filters or SearchFilters()
    doc_ids = allowed_document_ids(session, filters)
    if doc_ids is not None and not doc_ids:
        return []

    fetch = max(limit * 5, 20)
    semantic_hits: list[SemanticHit] = []
    keyword_hits: list[KeywordHit] = []
    if mode in ("semantic", "hybrid"):
        query_vector = llm_embed([query])[0]
        semantic_hits = semantic_search(session, query_vector, fetch, doc_ids)
    if mode in ("keyword", "hybrid"):
        keyword_hits = keyword_search(session, query, fetch, doc_ids)

    if mode == "semantic":
        ordered = [h.chunk_id for h in semantic_hits]
    elif mode == "keyword":
        ordered = [h.chunk_id for h in keyword_hits]
    else:
        ordered = rrf_fuse(
            [[h.chunk_id for h in semantic_hits], [h.chunk_id for h in keyword_hits]]
        )
    if not ordered:
        return []

    similarities = {h.chunk_id: h.similarity for h in semantic_hits}
    snippets = {h.chunk_id: h.snippet for h in keyword_hits}
    chunks = {
        c.id: c for c in session.exec(select(Chunk).where(Chunk.id.in_(ordered)))
    }

    results: list[dict] = []
    by_document: dict = {}
    for position, chunk_id in enumerate(ordered):
        chunk = chunks[chunk_id]
        entry = by_document.get(chunk.document_id)
        if entry is None:
            if len(results) >= limit:
                continue
            entry = {
                "document_id": chunk.document_id,
                "score": 1.0 / (position + 1),
                "snippets": [],
            }
            by_document[chunk.document_id] = entry
            results.append(entry)
        if len(entry["snippets"]) < 3:
            entry["snippets"].append(
                {
                    "chunk_id": chunk_id,
                    "page_number": chunk.page_number,
                    "source": chunk.source,
                    "text": snippets.get(chunk_id) or chunk.content[:250],
                    "similarity": similarities.get(chunk_id),
                }
            )
    return results
```

- [ ] **Step 4: Implement the router**

`backend/app/api/search.py`:

```python
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.api.documents import serialize
from app.db import get_session
from app.models import Document
from app.services.search import SearchFilters, search

router = APIRouter(
    prefix="/api/search", tags=["search"], dependencies=[Depends(get_current_user)]
)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: Literal["semantic", "keyword", "hybrid"] = "hybrid"
    filters: SearchFilters = SearchFilters()
    limit: int = Field(default=10, ge=1, le=50)


@router.post("")
def run_search(body: SearchRequest, session: Session = Depends(get_session)) -> dict:
    grouped = search(session, body.query, body.mode, body.filters, body.limit)
    results = []
    for entry in grouped:
        doc = session.get(Document, entry["document_id"])
        results.append(
            {
                "document": serialize(session, doc),
                "score": entry["score"],
                "snippets": entry["snippets"],
            }
        )
    return {"mode": body.mode, "results": results}
```

In `backend/app/main.py`, add `search` to the router imports and `app.include_router(search.router)`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_search_api.py -v`
Expected: 7 PASS

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/services/search.py backend/app/api/search.py backend/app/main.py backend/tests/test_search_api.py
git commit -m "feat: hybrid search endpoint with document grouping and snippets"
```

---

### Task 4: RAG service — retrieval, grounding, prompt, streaming

**Files:**
- Create: `backend/app/services/rag.py`
- Modify: `backend/app/config.py` (add `rag_top_k`, `rag_relevance_floor`)
- Modify: `.env.example` (document the two new vars)
- Test: `backend/tests/test_rag.py`

**Interfaces:**
- Consumes: `semantic_search`/`keyword_search`/`rrf_fuse` from Task 2, `llm.embed`/`llm.complete` (imported as `llm_embed`/`llm_complete` module aliases — the monkeypatch surface), `Chunk`/`Document`.
- Produces (all in `app.services.rag`):
  - Settings: `rag_top_k: int = 8`, `rag_relevance_floor: float = 0.35` on `Settings`.
  - `RetrievedChunk` dataclass: `n: int` (1-based citation number), `chunk_id: int`, `document_id: uuid.UUID`, `title: str`, `page_number: int | None`, `content: str`.
  - `retrieve(session, question: str) -> tuple[list[RetrievedChunk], bool]` — hybrid top-`rag_top_k`; `grounded` = at least one chunk retrieved AND best semantic similarity ≥ `rag_relevance_floor`.
  - `build_messages(question: str, sources: list[RetrievedChunk]) -> list[dict]` — system prompt (cite `[n]`, answer in the question's language, state explicitly when drawing on knowledge outside the sources) + user message with numbered sources (title + page) and the question. With zero sources the user message says no relevant documents were found.
  - `stream_answer(session, question: str) -> Iterator[tuple[str, dict]]` — yields `("meta", {"grounded": bool, "sources": [{"n", "chunk_id", "document_id"(str), "title", "page_number"}]})` first, then `("delta", {"text": str})` per token, then `("done", {})`.

- [ ] **Step 1: Add settings**

In `backend/app/config.py`, add to `Settings`:

```python
    rag_top_k: int = 8
    rag_relevance_floor: float = 0.35
```

Append to `.env.example`:

```env
RAG_TOP_K=8
RAG_RELEVANCE_FLOOR=0.35
```

- [ ] **Step 2: Write failing tests**

`backend/tests/test_rag.py`:

```python
import pytest

from app.services import rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def rag_llm(monkeypatch):
    calls = {"embed": [], "complete": []}

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [basis_vector(0)]

    def fake_complete(messages, stream=False):
        calls["complete"].append(messages)
        assert stream is True
        return iter(["Ecco ", "la risposta [1]."])

    monkeypatch.setattr(rag, "llm_embed", fake_embed)
    monkeypatch.setattr(rag, "llm_complete", fake_complete)
    return calls


def test_retrieve_grounded_when_similar(session, rag_llm):
    doc = seed_document(
        session, "Bolletta marzo",
        [{"content": "La bolletta della luce di marzo: 42 euro.",
          "embedding": basis_vector(0), "page_number": 2}],
    )
    sources, grounded = rag.retrieve(session, "quanto ho pagato la bolletta di marzo?")
    assert grounded is True
    assert sources[0].n == 1
    assert sources[0].title == "Bolletta marzo"
    assert sources[0].page_number == 2
    assert sources[0].document_id == doc.id


def test_retrieve_not_grounded_when_dissimilar(session, rag_llm):
    seed_document(
        session, "Estraneo",
        [{"content": "Curiosamente irrilevante.", "embedding": basis_vector(9)}],
    )
    sources, grounded = rag.retrieve(session, "chi ha vinto il mondiale 2006?")
    assert grounded is False  # best similarity 0.0 < floor


def test_retrieve_not_grounded_when_empty(session, rag_llm):
    sources, grounded = rag.retrieve(session, "qualsiasi cosa")
    assert sources == []
    assert grounded is False


def test_build_messages_numbers_sources(session, rag_llm):
    seed_document(
        session, "Contratto",
        [{"content": "Canone mensile 800 euro.", "embedding": basis_vector(0), "page_number": 5}],
    )
    sources, _ = rag.retrieve(session, "quanto pago di affitto?")
    messages = rag.build_messages("quanto pago di affitto?", sources)
    assert messages[0]["role"] == "system"
    assert "[n]" in messages[0]["content"] or "[1]" in messages[1]["content"]
    user = messages[1]["content"]
    assert "[1] Contratto (p. 5)" in user
    assert "Canone mensile 800 euro." in user
    assert "quanto pago di affitto?" in user


def test_build_messages_no_sources():
    messages = rag.build_messages("domanda", [])
    assert "No relevant documents" in messages[1]["content"]


def test_stream_answer_event_sequence(session, rag_llm):
    seed_document(
        session, "Doc",
        [{"content": "Contenuto rilevante.", "embedding": basis_vector(0)}],
    )
    events = list(rag.stream_answer(session, "domanda?"))
    kinds = [kind for kind, _ in events]
    assert kinds == ["meta", "delta", "delta", "done"]
    meta = events[0][1]
    assert meta["grounded"] is True
    assert meta["sources"][0]["n"] == 1
    assert isinstance(meta["sources"][0]["document_id"], str)
    assert events[1][1]["text"] == "Ecco "
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_rag.py -v`
Expected: FAIL (ImportError: app.services.rag)

- [ ] **Step 4: Implement**

`backend/app/services/rag.py`:

```python
import uuid
from dataclasses import dataclass
from typing import Iterator

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, Document
from app.services.llm import complete as llm_complete
from app.services.llm import embed as llm_embed
from app.services.search import keyword_search, rrf_fuse, semantic_search

SYSTEM_PROMPT = (
    "You are Origami, a personal document archive assistant. Answer the user's "
    "question using the numbered sources provided. Cite the sources inline with "
    "[n] after each claim they support. Answer in the same language as the "
    "question. If the sources do not contain the answer, say so explicitly and "
    "clearly state when you are drawing on general knowledge instead of the "
    "user's documents."
)

CANDIDATE_POOL = 20


@dataclass
class RetrievedChunk:
    n: int
    chunk_id: int
    document_id: uuid.UUID
    title: str
    page_number: int | None
    content: str


def retrieve(session: Session, question: str) -> tuple[list[RetrievedChunk], bool]:
    settings = get_settings()
    query_vector = llm_embed([question])[0]
    semantic_hits = semantic_search(session, query_vector, limit=CANDIDATE_POOL)
    keyword_hits = keyword_search(session, question, limit=CANDIDATE_POOL)
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


def build_messages(question: str, sources: list[RetrievedChunk]) -> list[dict]:
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
        {"role": "user", "content": user_content},
    ]


def stream_answer(session: Session, question: str) -> Iterator[tuple[str, dict]]:
    sources, grounded = retrieve(session, question)
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
        },
    )
    for delta in llm_complete(build_messages(question, sources), stream=True):
        yield ("delta", {"text": delta})
    yield ("done", {})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_rag.py -v`
Expected: 6 PASS

- [ ] **Step 6: Run full suite, then commit**

Run: `cd backend && uv run pytest`
Expected: all pass, 0 warnings.

```bash
git add backend/app/services/rag.py backend/app/config.py .env.example backend/tests/test_rag.py
git commit -m "feat: RAG retrieval with grounding floor and streaming answer generator"
```

---

### Task 5: `POST /api/chat` SSE endpoint

**Files:**
- Create: `backend/app/api/chat.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `rag.stream_answer`, `get_current_user`, `get_session`.
- Produces: `POST /api/chat` (JWT): body `{"question": str (min_length 1)}` → `StreamingResponse`, `media_type="text/event-stream"`. Each event is one line `data: <json>\n\n` where the JSON is `{"type": "meta"|"delta"|"done", ...payload}` exactly as produced by `stream_answer` (spec §6: meta carries `grounded` + `sources`; deltas carry `text`).

- [ ] **Step 1: Write failing tests**

`backend/tests/test_chat_api.py`:

```python
import json

import pytest

from app.services import rag
from tests.helpers import basis_vector, seed_document


@pytest.fixture
def chat_llm(monkeypatch):
    monkeypatch.setattr(rag, "llm_embed", lambda texts: [basis_vector(0)])
    monkeypatch.setattr(
        rag, "llm_complete", lambda messages, stream=False: iter(["Ecco ", "la risposta [1]."])
    )


def parse_sse(text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def test_chat_streams_meta_deltas_done(auth_client, session, chat_llm):
    seed_document(
        session, "Bolletta",
        [{"content": "Bolletta di marzo: 42 euro.", "embedding": basis_vector(0), "page_number": 1}],
    )
    resp = auth_client.post("/api/chat", json={"question": "quanto ho pagato?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    assert [e["type"] for e in events] == ["meta", "delta", "delta", "done"]
    assert events[0]["grounded"] is True
    assert events[0]["sources"][0]["title"] == "Bolletta"
    assert events[1]["text"] == "Ecco "


def test_chat_ungrounded_flag(auth_client, session, chat_llm):
    resp = auth_client.post("/api/chat", json={"question": "chi ha vinto il mondiale?"})
    events = parse_sse(resp.text)
    assert events[0]["type"] == "meta"
    assert events[0]["grounded"] is False
    assert events[0]["sources"] == []


def test_chat_empty_question_422(auth_client):
    resp = auth_client.post("/api/chat", json={"question": ""})
    assert resp.status_code == 422


def test_chat_requires_auth(client):
    assert client.post("/api/chat", json={"question": "x"}).status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_chat_api.py -v`
Expected: FAIL (404s)

- [ ] **Step 3: Implement**

`backend/app/api/chat.py`:

```python
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import get_current_user
from app.db import get_session
from app.services.rag import stream_answer

router = APIRouter(
    prefix="/api/chat", tags=["chat"], dependencies=[Depends(get_current_user)]
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("")
def chat(body: ChatRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    def event_stream():
        for event_type, payload in stream_answer(session, body.question):
            yield f"data: {json.dumps({'type': event_type, **payload})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

In `backend/app/main.py`, add `chat` to the router imports and `app.include_router(chat.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_chat_api.py -v`
Expected: 4 PASS

- [ ] **Step 5: Run the FULL suite (Phase 3 is complete), then commit**

Run: `cd backend && uv run pytest -v`
Expected: all pass, 0 warnings.

```bash
git add backend/app/api/chat.py backend/app/main.py backend/tests/test_chat_api.py
git commit -m "feat: SSE chat endpoint with grounded flag and cited sources"
```

---

## Phase 3 exit criteria

- Full suite green, 0 warnings, against real Postgres (FTS + pgvector queries real; only `llm_embed`/`llm_complete` stubbed).
- `POST /api/search` returns grouped, snippet-highlighted results in all three modes with folder/tag/doc_type filters.
- `POST /api/chat` streams `meta` (grounded + sources) → `delta`* → `done`, with `grounded: false` when nothing clears the relevance floor.
- Manual smoke (human, needs `GEMINI_API_KEY`): upload a real PDF, wait for the worker, search for its content, ask the chat a question about it — answer cites `[1]` and the meta event lists the document.
- Phase 4 (frontend) consumes: `/api/search` response shape and the SSE event protocol defined here.
