def test_create_list_update_delete(auth_client):
    resp = auth_client.post("/api/tags", json={"name": "fiscale", "color": "#ff0000"})
    assert resp.status_code == 201
    tag_id = resp.json()["id"]

    assert auth_client.get("/api/tags").json()[0]["name"] == "fiscale"

    resp = auth_client.patch(f"/api/tags/{tag_id}", json={"color": "#00ff00"})
    assert resp.json()["color"] == "#00ff00"

    assert auth_client.delete(f"/api/tags/{tag_id}").status_code == 204
    assert auth_client.get("/api/tags").json() == []


def test_duplicate_name_rejected(auth_client):
    auth_client.post("/api/tags", json={"name": "casa"})
    resp = auth_client.post("/api/tags", json={"name": "casa"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "duplicate_tag"
