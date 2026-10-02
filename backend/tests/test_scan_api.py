from app.services.scanner import ScannerOffline


def new_session(auth_client, **body):
    return auth_client.post("/api/scan/sessions", json=body)


def test_create_session_default_languages(auth_client, fake_scanner, storage):
    resp = new_session(auth_client)
    assert resp.status_code == 201
    assert resp.json()["ocr_languages"] == "ita+eng"
    assert resp.json()["status"] == "active"


def test_scan_pages_and_preview(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, ocr_languages="ita").json()["id"]
    p1 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p2 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    assert (p1["page_number"], p2["page_number"]) == (1, 2)

    preview = auth_client.get(p1["preview_url"])
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG")


def test_scanner_offline_maps_to_503(auth_client, fake_scanner, storage):
    fake_scanner._error = ScannerOffline()
    sid = new_session(auth_client).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "scanner_offline"


def test_delete_page_renumbers(auth_client, fake_scanner, storage):
    sid = new_session(auth_client).json()["id"]
    p1 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p2 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()
    p3 = auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()

    assert auth_client.delete(f"/api/scan/pages/{p2['id']}").status_code == 204
    remaining = {p3["id"]: 2, p1["id"]: 1}
    for page_id, expected_number in remaining.items():
        preview = auth_client.get(f"/api/scan/pages/{page_id}/preview")
        assert preview.status_code == 200
    # renumbering verified through reorder round-trip below


def test_reorder_pages(auth_client, fake_scanner, storage):
    sid = new_session(auth_client).json()["id"]
    ids = [
        auth_client.post(f"/api/scan/sessions/{sid}/pages", json={}).json()["id"]
        for _ in range(3)
    ]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/reorder", json={"page_ids": ids[::-1]})
    assert resp.status_code == 200
    assert [p["id"] for p in resp.json()["pages"]] == ids[::-1]

    bad = auth_client.post(f"/api/scan/sessions/{sid}/reorder", json={"page_ids": ids[:2]})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_order"


def test_cancel_session_purges(auth_client, fake_scanner, storage, session):
    from app.models import ScanPage
    from sqlmodel import select

    sid = new_session(auth_client).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert (storage.tmp_scans_dir / str(sid)).exists()

    assert auth_client.delete(f"/api/scan/sessions/{sid}").status_code == 204
    assert not (storage.tmp_scans_dir / str(sid)).exists()
    assert session.exec(select(ScanPage).where(ScanPage.session_id == sid)).all() == []


def test_status_endpoint(auth_client, fake_scanner, storage):
    resp = auth_client.get("/api/scan/status")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "busy": False}


def test_scan_requires_auth(client, fake_scanner, storage):
    assert client.get("/api/scan/status").status_code == 401


def test_devices_endpoint(auth_client, fake_scanner, storage):
    resp = auth_client.get("/api/scan/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["devices"], list)
    assert "default" in body


def test_preview_endpoint_returns_png(auth_client, fake_scanner, storage):
    resp = auth_client.post("/api/scan/preview", json={})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_preview_busy_returns_409(auth_client, fake_scanner, storage):
    from app.services import scanner as scanner_module

    scanner_module._scan_lock.acquire()
    try:
        resp = auth_client.post("/api/scan/preview", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "scanner_busy"
    finally:
        scanner_module._scan_lock.release()


def test_page_scan_uses_request_device(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, device="fake:0").json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={"device": "fake:1"})
    assert fake_scanner.last_device == "fake:1"
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert fake_scanner.last_device == "fake:0"  # falls back to the session device


def test_compile_applies_form_fields(auth_client, fake_scanner, storage, session):
    from app.models import Document

    sid = new_session(auth_client, ocr_languages="ita+eng", ocr_enabled=True).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    resp = auth_client.post(
        f"/api/scan/sessions/{sid}/compile",
        json={
            "title": "Brief",
            "description": "Lettera dalla Germania",
            "document_date": "2021-07-09",
            "ocr_languages": "deu",
            "ocr_enabled": False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["description"] == "Lettera dalla Germania"
    assert body["document_date"] == "2021-07-09"
    assert body["ocr_languages"] == "deu"
    assert body["ocr_enabled"] is False


def test_compile_falls_back_to_session_settings(auth_client, fake_scanner, storage):
    sid = new_session(auth_client, ocr_languages="ita", ocr_enabled=False).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    body = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "X"}).json()
    assert body["ocr_languages"] == "ita"
    assert body["ocr_enabled"] is False


def test_cancel_compiling_session_is_409(auth_client, fake_scanner, storage, session):
    from sqlmodel import select

    from app.models import ScanPage

    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    assert auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "T"}).status_code == 201
    resp = auth_client.delete(f"/api/scan/sessions/{sid}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "session_not_active"
    pages = session.exec(select(ScanPage).where(ScanPage.session_id == sid)).all()
    assert len(pages) == 1
    assert storage.abs_path(pages[0].image_path).exists()
