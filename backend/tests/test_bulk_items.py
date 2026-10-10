import uuid

from sqlmodel import select

from app.models import DocStatus, DocType, Document, Folder, Job, JobStatus
from app.services.tree_sync import write_document_file


def new_folder(client, name, parent=None):
    resp = client.post("/api/folders", json={"name": name, "parent_id": parent})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def new_doc(session, storage, title, folder_id=None, with_file=True):
    doc = Document(title=title, doc_type=DocType.pdf, status=DocStatus.ready, folder_id=folder_id)
    session.add(doc)
    session.commit()
    if with_file:
        write_document_file(session, storage, doc, ".pdf", b"%PDF")
    session.refresh(doc)
    return doc


def move(client, folder_id, folders=(), docs=()):
    return client.post(
        "/api/bulk/move",
        json={"folder_ids": list(folders), "document_ids": [str(d) for d in docs], "folder_id": folder_id},
    )


def test_move_folders_and_documents(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    target = new_folder(auth_client, "Target")
    inside = new_doc(session, storage, "Inside", a)
    loose = new_doc(session, storage, "Loose")

    resp = move(auth_client, target, folders=[a], docs=[loose.id])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"moved_folders": 1, "moved_documents": 1, "missing_folders": [], "missing_documents": []}
    session.expire_all()
    assert session.get(Folder, a).parent_id == target
    assert session.get(Document, inside.id).file_path == "Target/A/Inside.pdf"
    assert session.get(Document, loose.id).file_path == "Target/Loose.pdf"
    assert storage.abs_path("Target/A/Inside.pdf").exists()
    assert storage.abs_path("Target/Loose.pdf").exists()
    assert not storage.abs_path("A").exists()


def test_move_into_selected_folder_or_descendant_is_cycle(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    for target in (a, b):
        resp = move(auth_client, target, folders=[a])
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "folder_cycle"
    session.expire_all()
    assert session.get(Folder, a).parent_id is None


def test_name_conflict_rejects_everything(auth_client, session, storage):
    target = new_folder(auth_client, "Target")
    new_folder(auth_client, "X", target)
    elsewhere = new_folder(auth_client, "Elsewhere")
    x = new_folder(auth_client, "X", elsewhere)
    loose = new_doc(session, storage, "Loose")

    resp = move(auth_client, target, folders=[x], docs=[loose.id])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"
    assert "X" in resp.json()["error"]["message"]
    session.expire_all()
    assert session.get(Folder, x).parent_id == elsewhere
    assert session.get(Document, loose.id).folder_id is None
    assert storage.abs_path("Elsewhere/X").is_dir()
    assert storage.abs_path("Loose.pdf").exists()


def test_two_selected_folders_with_same_name_conflict(auth_client, session, storage):
    p1 = new_folder(auth_client, "P1")
    p2 = new_folder(auth_client, "P2")
    x1 = new_folder(auth_client, "X", p1)
    x2 = new_folder(auth_client, "X", p2)
    target = new_folder(auth_client, "Target")
    resp = move(auth_client, target, folders=[x1, x2])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"
    session.expire_all()
    assert session.get(Folder, x1).parent_id == p1


def test_nested_selection_moves_once(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    doc = new_doc(session, storage, "Deep", b)
    target = new_folder(auth_client, "Target")

    resp = move(auth_client, target, folders=[b, a], docs=[doc.id])
    assert resp.status_code == 200, resp.text
    session.expire_all()
    assert session.get(Folder, a).parent_id == target
    assert session.get(Folder, b).parent_id == a
    assert session.get(Document, doc.id).folder_id == b
    assert session.get(Document, doc.id).file_path == "Target/A/B/Deep.pdf"


def test_folder_already_in_destination_is_noop(auth_client, session, storage):
    target = new_folder(auth_client, "Target")
    x = new_folder(auth_client, "X", target)
    resp = move(auth_client, target, folders=[x])
    assert resp.status_code == 200, resp.text
    assert storage.abs_path("Target/X").is_dir()


def test_stray_directory_at_destination_rolls_back(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    target = new_folder(auth_client, "Target")
    storage.make_dir("Target/A")  # on disk only
    loose = new_doc(session, storage, "Loose")
    resp = move(auth_client, target, folders=[a], docs=[loose.id])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.expire_all()
    assert session.get(Folder, a).parent_id is None
    assert storage.abs_path("A").is_dir()
    assert storage.abs_path("Loose.pdf").exists()


def test_move_folder_named_files_to_root_is_reserved(auth_client, session, storage):
    parent = new_folder(auth_client, "Parent")
    files = new_folder(auth_client, "files", parent)
    resp = move(auth_client, None, folders=[files])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "reserved_folder_name"


def test_move_reports_missing_and_checks_destination(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    ghost = uuid.uuid4()
    resp = move(auth_client, None, folders=[a, 999], docs=[ghost])
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing_folders"] == [999]
    assert resp.json()["missing_documents"] == [str(ghost)]
    assert move(auth_client, 999, folders=[a]).status_code == 404


def test_bulk_items_validation_and_auth(client, auth_client):
    assert auth_client.post("/api/bulk/move", json={"folder_id": None}).status_code == 422
    assert auth_client.post("/api/bulk/delete", json={"folder_ids": [], "document_ids": []}).status_code == 422
    too_many = list(range(1, 502))
    assert auth_client.post("/api/bulk/delete", json={"folder_ids": too_many}).status_code == 422


def delete(client, folders=(), docs=()):
    return client.post(
        "/api/bulk/delete", json={"folder_ids": list(folders), "document_ids": [str(d) for d in docs]}
    )


def test_delete_is_recursive(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    keep = new_folder(auth_client, "Keep")
    in_a = new_doc(session, storage, "InA", a)
    in_b = new_doc(session, storage, "InB", b)
    pending = new_doc(session, storage, "Pending", b, with_file=False)  # still processing: no file yet
    loose = new_doc(session, storage, "Loose")
    kept = new_doc(session, storage, "Kept", keep)
    session.add(Job(type="process_document", payload={"document_id": str(in_b.id)}))
    session.commit()

    resp = delete(auth_client, folders=[a], docs=[loose.id, in_a.id])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted_folders": 2, "deleted_documents": 4, "missing_folders": [], "missing_documents": []}
    session.expire_all()
    assert session.exec(select(Folder.id)).all() == [keep]
    assert [d.id for d in session.exec(select(Document))] == [kept.id]
    assert session.exec(select(Job)).one().status == JobStatus.cancelled
    assert not storage.abs_path("A").exists()
    assert not storage.abs_path("Loose.pdf").exists()
    assert storage.abs_path("Keep/Kept.pdf").exists()
    assert pending.id not in {d.id for d in session.exec(select(Document))}


def test_delete_keeps_directory_with_untracked_file(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    storage.write_file("A/notes.txt", b"mine")  # not a document
    resp = delete(auth_client, folders=[a])
    assert resp.status_code == 200, resp.text
    session.expire_all()
    assert session.get(Folder, a) is None
    assert storage.abs_path("A/notes.txt").read_bytes() == b"mine"


def test_delete_reports_missing(auth_client, session, storage):
    ghost = uuid.uuid4()
    resp = delete(auth_client, folders=[999], docs=[ghost])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "deleted_folders": 0, "deleted_documents": 0, "missing_folders": [999], "missing_documents": [str(ghost)],
    }


def test_delete_nested_selection(auth_client, session, storage):
    a = new_folder(auth_client, "A")
    b = new_folder(auth_client, "B", a)
    c = new_folder(auth_client, "C", b)
    new_doc(session, storage, "Deep", c)
    resp = delete(auth_client, folders=[b, a])
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_folders"] == 3
    session.expire_all()
    assert session.exec(select(Folder.id)).all() == []
    assert not storage.abs_path("A").exists()


def test_delete_cleanup_continues_after_disk_error(auth_client, session, storage, monkeypatch):
    first = new_doc(session, storage, "First")
    second = new_doc(session, storage, "Second")
    real = storage.delete_document_files
    calls = []

    def flaky(file_rel, preview, document_id):
        calls.append(document_id)
        if len(calls) == 1:
            raise OSError("disk gone")
        real(file_rel, preview, document_id)

    monkeypatch.setattr(storage, "delete_document_files", flaky)
    resp = delete(auth_client, docs=[first.id, second.id])
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted_documents"] == 2
    assert len(calls) == 2
    survivors = [p for p in ("First.pdf", "Second.pdf") if storage.abs_path(p).exists()]
    assert len(survivors) == 1  # only the first processed document's file is left behind


def test_move_two_folders_undoes_first_when_second_conflicts(auth_client, session, storage):
    x = new_folder(auth_client, "X")
    y = new_folder(auth_client, "Y")
    target = new_folder(auth_client, "Target")
    storage.make_dir("Target/Y")  # on disk only
    resp = move(auth_client, target, folders=[x, y])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.expire_all()
    assert session.get(Folder, x).parent_id is None
    assert storage.abs_path("X").is_dir()
    assert not storage.abs_path("Target/X").exists()
