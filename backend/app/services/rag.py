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
