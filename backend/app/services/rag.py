import uuid
from dataclasses import dataclass
from typing import Iterator

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Chunk, Document
from app.services.llm import complete as llm_complete
from app.services.llm import embed as llm_embed
from app.services.search import QUERY_MAX_CHARS, keyword_search, rrf_fuse, semantic_search

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
