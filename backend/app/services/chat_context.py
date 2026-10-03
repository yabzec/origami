"""Document shortlist for the chat preflight: local search only (one query embedding)."""

import uuid
from collections.abc import Iterable
from typing import TypedDict

from sqlalchemy import text
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
    # The source filter is applied after the HNSW index returns ef_search neighbours, so on a large
    # archive it can leave too few summary/metadata hits; iterative scan keeps pulling until filled.
    session.exec(  # transaction-local
        text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true), set_config('hnsw.ef_search', '100', true)")
    )
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
