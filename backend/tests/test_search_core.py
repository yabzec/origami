import pytest
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocType, DocumentTag, Tag
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
