import uuid

from app.models import Document, DocStatus, DocType, DocumentTag, Tag


def make_document(session, **kwargs):
    doc = Document(
        title=kwargs.pop("title", "Doc"),
        doc_type=kwargs.pop("doc_type", DocType.pdf),
        status=kwargs.pop("status", DocStatus.ready),
        **kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_list_and_filters(auth_client, session):
    d1 = make_document(session, title="Bolletta")
    make_document(session, title="Video", doc_type=DocType.video)

    all_docs = auth_client.get("/api/documents").json()
    assert len(all_docs) == 2

    only_pdf = auth_client.get("/api/documents", params={"doc_type": "pdf"}).json()
    assert [d["id"] for d in only_pdf] == [str(d1.id)]


def test_get_includes_tags(auth_client, session):
    doc = make_document(session)
    tag = Tag(name="casa")
    session.add(tag)
    session.commit()
    session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert [t["name"] for t in body["tags"]] == ["casa"]


def test_patch_replaces_tags_and_moves_folder(auth_client, session):
    doc = make_document(session)
    t1, t2 = Tag(name="a"), Tag(name="b")
    session.add(t1); session.add(t2); session.commit()
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]

    body = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"title": "New", "folder_id": folder_id, "tag_ids": [t2.id]},
    ).json()
    assert body["title"] == "New"
    assert body["folder_id"] == folder_id
    assert [t["id"] for t in body["tags"]] == [t2.id]


def test_patch_replaces_existing_tag_set(auth_client, session):
    doc = make_document(session)
    t1, t2 = Tag(name="a"), Tag(name="b")
    session.add(t1); session.add(t2); session.commit()
    session.add(DocumentTag(document_id=doc.id, tag_id=t1.id))
    session.commit()

    body = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"tag_ids": [t2.id]},
    ).json()
    assert [t["id"] for t in body["tags"]] == [t2.id]


def test_patch_missing_folder_id_404(auth_client, session):
    doc = make_document(session)
    resp = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"folder_id": 999999},
    )
    assert resp.status_code == 404


def test_patch_missing_tag_id_404(auth_client, session):
    doc = make_document(session)
    resp = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"tag_ids": [999999]},
    )
    assert resp.status_code == 404


def test_delete_removes_file(auth_client, session, storage):
    doc = make_document(session)
    rel, _ = storage.store_file(doc.id, ".pdf", b"%PDF")
    doc.file_path = rel
    session.commit()

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not storage.abs_path(rel).exists()
    assert auth_client.get(f"/api/documents/{doc.id}").status_code == 404


def test_get_missing_404(auth_client):
    resp = auth_client.get(f"/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_document_text_endpoint(auth_client, session):
    from app.models import Chunk, ChunkSource
    from tests.helpers import seed_document

    doc = seed_document(
        session, "Testo",
        [
            {"content": "Pagina uno.", "page_number": 1},
            {"content": "Pagina due.", "page_number": 2},
            {"content": "Riassunto.", "source": ChunkSource.summary},
        ],
    )
    doc.summary = "Riassunto."
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}/text").json()
    assert body["summary"] == "Riassunto."
    assert [c["content"] for c in body["chunks"]] == ["Pagina uno.", "Pagina due."]
    assert body["chunks"][0]["page_number"] == 1


def test_new_document_defaults_and_serialization(auth_client, session):
    from app.models.user import utcnow
    from tests.helpers import seed_document

    doc = seed_document(session, "Fresh", [])
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["document_date"] == utcnow().date().isoformat()
    assert body["detected_language"] is None
    assert body["translation_status"] is None
    assert body["ocr_applied"] is None


def test_patch_document_date(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Dated", [])
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"


def test_patch_null_document_date_is_ignored(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Dated", [])
    auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": None, "title": "T2"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"
    assert resp.json()["title"] == "T2"
