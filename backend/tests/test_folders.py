def test_create_and_list(auth_client):
    resp = auth_client.post("/api/folders", json={"name": "Bills"})
    assert resp.status_code == 201
    root_id = resp.json()["id"]
    resp = auth_client.post("/api/folders", json={"name": "2026", "parent_id": root_id})
    assert resp.status_code == 201
    items = auth_client.get("/api/folders").json()
    assert {f["name"] for f in items} == {"Bills", "2026"}


def test_duplicate_sibling_name_rejected(auth_client):
    auth_client.post("/api/folders", json={"name": "Bills"})
    resp = auth_client.post("/api/folders", json={"name": "Bills"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"


def test_move_cycle_rejected(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"parent_id": b})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_cycle"


def test_move_cycle_rejected_multi_level(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    c = auth_client.post("/api/folders", json={"name": "C", "parent_id": b}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"parent_id": c})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_cycle"


def test_rename(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"name": "Archive"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Archive"


def test_delete_non_empty_rejected(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    auth_client.post("/api/folders", json={"name": "B", "parent_id": a})
    resp = auth_client.delete(f"/api/folders/{a}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "folder_not_empty"


def test_delete_empty(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    assert auth_client.delete(f"/api/folders/{a}").status_code == 204


def test_requires_auth(client):
    assert client.get("/api/folders").status_code == 401


def _new_folder(auth_client, name, parent_id=None):
    resp = auth_client.post("/api/folders", json={"name": name, "parent_id": parent_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_create_folder_makes_directory(auth_client, storage):
    home = _new_folder(auth_client, "Home")
    _new_folder(auth_client, "Bills", home)
    assert storage.abs_path("Home/Bills").is_dir()


def test_sanitized_name_collision_is_409(auth_client, storage):
    _new_folder(auth_client, "a/b")
    resp = auth_client.post("/api/folders", json={"name": "a_b"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"


def test_rename_folder_moves_directory_and_paths(auth_client, session, storage):
    from app.models import DocType, Document
    from app.services.tree_sync import write_document_file

    home = _new_folder(auth_client, "Home")
    bills = _new_folder(auth_client, "Bills", home)
    doc = Document(title="Invoice", doc_type=DocType.pdf, folder_id=bills)
    session.add(doc)
    session.commit()
    write_document_file(session, storage, doc, ".pdf", b"%PDF")

    assert auth_client.patch(f"/api/folders/{home}", json={"name": "House"}).status_code == 200
    session.refresh(doc)
    assert doc.file_path == "House/Bills/Invoice.pdf"
    assert storage.abs_path("House/Bills/Invoice.pdf").exists()
    assert not storage.abs_path("Home").exists()


def test_move_folder_under_another(auth_client, session, storage):
    a = _new_folder(auth_client, "A")
    b = _new_folder(auth_client, "B")
    assert auth_client.patch(f"/api/folders/{b}", json={"parent_id": a}).status_code == 200
    assert storage.abs_path("A/B").is_dir()


def test_move_folder_onto_stray_directory_is_409(auth_client, session, storage):
    from app.models import Folder

    a = _new_folder(auth_client, "A")
    storage.make_dir("B")  # on disk only, not in the database
    resp = auth_client.patch(f"/api/folders/{a}", json={"name": "B"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "storage_conflict"
    session.expire_all()
    assert session.get(Folder, a).name == "A"
    assert storage.abs_path("A").is_dir()


def test_delete_empty_folder_removes_directory(auth_client, storage):
    a = _new_folder(auth_client, "A")
    assert auth_client.delete(f"/api/folders/{a}").status_code == 204
    assert not storage.abs_path("A").exists()


def test_ensure_path_creates_and_reuses(auth_client, session, storage):
    home = _new_folder(auth_client, "Home")
    first = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": ["Bills", "2025"]}
    ).json()["folder_id"]
    again = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": ["Bills", "2025"]}
    ).json()["folder_id"]
    assert first == again
    assert storage.abs_path("Home/Bills/2025").is_dir()
    same = auth_client.post(
        "/api/folders/ensure-path", json={"parent_id": home, "segments": []}
    ).json()["folder_id"]
    assert same == home
    root = auth_client.post("/api/folders/ensure-path", json={"parent_id": None, "segments": []})
    assert root.json() == {"folder_id": None}


def test_ensure_path_rejects_empty_segment(auth_client, storage):
    resp = auth_client.post("/api/folders/ensure-path", json={"parent_id": None, "segments": ["A", " "]})
    assert resp.status_code == 422
