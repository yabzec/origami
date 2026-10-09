from datetime import date

from app.models import Chunk, ChunkSource, Document, DocStatus, DocType


def make(session, title, **kwargs):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, **kwargs)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def titles(resp):
    assert resp.status_code == 200, resp.text
    return sorted(d["title"] for d in resp.json())


def test_root_filter_lists_only_documents_without_folder(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    make(session, "Loose")
    make(session, "Filed", folder_id=folder_id)
    assert titles(auth_client.get("/api/documents", params={"folder_id": "root"})) == ["Loose"]
    assert titles(auth_client.get("/api/documents", params={"folder_id": folder_id})) == ["Filed"]
    assert titles(auth_client.get("/api/documents")) == ["Filed", "Loose"]


def test_bad_folder_id_is_422(auth_client):
    assert auth_client.get("/api/documents", params={"folder_id": "abc"}).status_code == 422


def test_date_range_is_inclusive(auth_client, session):
    make(session, "Jan", document_date=date(2026, 1, 31))
    make(session, "Feb", document_date=date(2026, 2, 1))
    make(session, "Mar", document_date=date(2026, 3, 1))
    get = lambda **p: titles(auth_client.get("/api/documents", params=p))  # noqa: E731
    assert get(date_from="2026-02-01") == ["Feb", "Mar"]
    assert get(date_to="2026-02-01") == ["Feb", "Jan"]
    assert get(date_from="2026-02-01", date_to="2026-02-01") == ["Feb"]


def test_inverted_date_range_is_422(auth_client):
    resp = auth_client.get("/api/documents", params={"date_from": "2026-03-01", "date_to": "2026-02-01"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_date_range"


def test_folders_include_direct_document_counts(auth_client, session):
    parent = auth_client.post("/api/folders", json={"name": "P"}).json()["id"]
    child = auth_client.post("/api/folders", json={"name": "C", "parent_id": parent}).json()["id"]
    make(session, "a", folder_id=parent)
    make(session, "b", folder_id=child)
    make(session, "c", folder_id=child)
    counts = {f["name"]: f["document_count"] for f in auth_client.get("/api/folders").json()}
    assert counts == {"P": 1, "C": 2}


def test_translatable_flag(auth_client, session):
    german = make(session, "de", detected_language="de")
    session.add(Chunk(document_id=german.id, chunk_index=0, source=ChunkSource.content, content="Brief"))
    session.commit()
    italian = make(session, "it", detected_language="it")
    unknown = make(session, "none")
    flags = {d["title"]: d["translatable"] for d in auth_client.get("/api/documents").json()}
    assert flags == {"de": True, "it": False, "none": False}
    assert auth_client.get(f"/api/documents/{german.id}").json()["translatable"] is True
    assert italian and unknown
