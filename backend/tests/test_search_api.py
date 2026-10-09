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


def test_search_inverted_date_range_is_422(auth_client):
    resp = auth_client.post(
        "/api/search",
        json={"query": "x", "filters": {"date_from": "2026-03-01", "date_to": "2026-02-01"}},
    )
    assert resp.status_code == 422


def test_search_fetches_content_flags_in_one_query(auth_client, session, embed_stub, monkeypatch):
    from app.api import documents, search as search_api

    seed_corpus(session)
    calls = []
    real = documents.docs_with_content

    def counting(s, ids):
        calls.append(list(ids))
        return real(s, ids)

    monkeypatch.setattr(documents, "docs_with_content", counting)
    monkeypatch.setattr(search_api, "docs_with_content", counting, raising=False)
    resp = auth_client.post("/api/search", json={"query": "bolletta luce"})
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 2
    assert len(calls) == 1 and len(calls[0]) == 2
    assert all(r["document"]["has_text"] for r in resp.json()["results"])
