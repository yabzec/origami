import uuid

from sqlmodel import select

from app.models import Document, DocStatus, DocType, Job, JobStatus


def make(session, title="d", **kwargs):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, **kwargs)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_bulk_move(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    a, b = make(session), make(session)
    ghost = str(uuid.uuid4())
    body = auth_client.post(
        "/api/documents/bulk/move", json={"ids": [str(a.id), str(b.id), ghost], "folder_id": folder_id}
    ).json()
    assert body == {"moved": 2, "missing": [ghost]}
    session.expire_all()
    assert {session.get(Document, a.id).folder_id, session.get(Document, b.id).folder_id} == {folder_id}


def test_bulk_move_to_root(auth_client, session):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    a = make(session, folder_id=folder_id)
    auth_client.post("/api/documents/bulk/move", json={"ids": [str(a.id)], "folder_id": None})
    session.expire_all()
    assert session.get(Document, a.id).folder_id is None


def test_bulk_move_missing_folder_changes_nothing(auth_client, session):
    a = make(session)
    resp = auth_client.post("/api/documents/bulk/move", json={"ids": [str(a.id)], "folder_id": 999})
    assert resp.status_code == 404
    session.expire_all()
    assert session.get(Document, a.id).folder_id is None


def test_bulk_delete_removes_rows_files_and_queued_jobs(auth_client, session, storage):
    a, b = make(session), make(session)
    rel, _ = storage.write_file(f"{a.id}.pdf", b"%PDF")
    a.file_path = rel
    session.add(Job(type="process_document", payload={"document_id": str(a.id)}))
    session.commit()
    ghost = str(uuid.uuid4())
    body = auth_client.post("/api/documents/bulk/delete", json={"ids": [str(a.id), str(b.id), ghost]}).json()
    assert body == {"deleted": 2, "missing": [ghost]}
    session.expire_all()
    assert session.exec(select(Document)).all() == []
    assert not storage.abs_path(rel).exists()
    assert session.exec(select(Job)).one().status == JobStatus.cancelled


def test_bulk_limits(auth_client):
    assert auth_client.post("/api/documents/bulk/delete", json={"ids": []}).status_code == 422
    ids = [str(uuid.uuid4()) for _ in range(501)]
    assert auth_client.post("/api/documents/bulk/delete", json={"ids": ids}).status_code == 422
    assert auth_client.post("/api/documents/bulk/move", json={"ids": ids, "folder_id": None}).status_code == 422
