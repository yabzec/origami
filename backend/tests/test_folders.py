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
    assert resp.json()["detail"]["error"]["code"] == "duplicate_folder"


def test_move_cycle_rejected(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"parent_id": b})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "folder_cycle"


def test_move_cycle_rejected_multi_level(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    c = auth_client.post("/api/folders", json={"name": "C", "parent_id": b}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"parent_id": c})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "folder_cycle"


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
    assert resp.json()["detail"]["error"]["code"] == "folder_not_empty"


def test_delete_empty(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    assert auth_client.delete(f"/api/folders/{a}").status_code == 204


def test_requires_auth(client):
    assert client.get("/api/folders").status_code == 401
