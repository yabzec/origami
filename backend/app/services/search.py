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
