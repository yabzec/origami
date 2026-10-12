from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job


def make_doc(session, **kw):
    doc = Document(title="Fattura", doc_type=DocType.pdf, status=DocStatus.ready, **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def refresh_jobs(session):
    return session.exec(select(Job).where(Job.type == "refresh_metadata")).all()


def patch(client, doc, properties):
    return client.patch(f"/api/documents/{doc.id}", json={"properties": properties})


def test_patch_properties_normalizes_and_refreshes(auth_client, session, storage):
    doc = make_doc(session)
    session.add(Chunk(document_id=doc.id, chunk_index=0, source=ChunkSource.metadata, content="Fattura"))
    session.commit()
    resp = patch(auth_client, doc, [
        {"key": "  Fornitore ", "value": " ACME  "},
        {"key": "", "value": "   "},
        {"key": "Importo", "value": ""},
    ])
    assert resp.status_code == 200
    assert resp.json()["properties"] == [{"key": "Fornitore", "value": "ACME"}, {"key": "Importo", "value": ""}]
    session.refresh(doc)
    assert doc.properties == [{"key": "Fornitore", "value": "ACME"}, {"key": "Importo", "value": ""}]
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).all() == []
    assert len(refresh_jobs(session)) == 1


def test_patch_same_properties_queues_nothing(auth_client, session, storage):
    doc = make_doc(session, properties=[{"key": "Fornitore", "value": "ACME"}])
    resp = patch(auth_client, doc, [{"key": "Fornitore ", "value": " ACME"}, {"key": "", "value": ""}])
    assert resp.status_code == 200
    assert refresh_jobs(session) == []


def test_patch_properties_keeps_order(auth_client, session, storage):
    doc = make_doc(session)
    rows = [{"key": k, "value": str(i)} for i, k in enumerate(["Zeta", "Alfa", "Mu"])]
    assert patch(auth_client, doc, rows).json()["properties"] == rows


def test_patch_properties_null_is_ignored(auth_client, session, storage):
    doc = make_doc(session, properties=[{"key": "A", "value": "1"}])
    resp = patch(auth_client, doc, None)
    assert resp.status_code == 200
    assert resp.json()["properties"] == [{"key": "A", "value": "1"}]
    assert refresh_jobs(session) == []


def test_patch_value_without_key_422(auth_client, session, storage):
    doc = make_doc(session)
    resp = patch(auth_client, doc, [{"key": "  ", "value": "ACME"}])
    assert resp.status_code == 422
    assert resp.json()["error"] == {"code": "validation_error", "message": "Every value needs a key", "detail": None}


def test_patch_duplicate_keys_422(auth_client, session, storage):
    doc = make_doc(session)
    resp = patch(auth_client, doc, [{"key": "Colore", "value": "blu"}, {"key": " colore", "value": "rosso"}])
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "Duplicate key: colore"
    session.refresh(doc)
    assert doc.properties == []


def test_patch_properties_limits_422(auth_client, session, storage):
    doc = make_doc(session)
    assert patch(auth_client, doc, [{"key": "k" * 101, "value": ""}]).status_code == 422
    assert patch(auth_client, doc, [{"key": "k", "value": "v" * 2001}]).status_code == 422
    rows = [{"key": f"k{i}", "value": ""} for i in range(101)]
    assert patch(auth_client, doc, rows).status_code == 422
    assert patch(auth_client, doc, [{"key": "k" * 100, "value": "v" * 2000}]).status_code == 200
