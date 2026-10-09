from app.services.auth import create_access_token
from tests.helpers import seed_document


def stored_doc(session, storage, data=b"%PDF-1.7 x", ext=".pdf"):
    doc = seed_document(session, "Doc", [{"content": "c"}])
    rel, size = storage.write_file(f"{doc.id}{ext}", data)
    doc.file_path = rel
    session.commit()
    return doc


def test_file_served_with_bearer_header(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.7 x"


def test_file_served_with_token_query_param(client, session, storage, user):
    doc = stored_doc(session, storage, data=b"vid", ext=".mp4")
    token = create_access_token(user.id)
    resp = client.get(f"/api/documents/{doc.id}/file?token={token}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"


def test_file_header_wins_over_mismatched_query_token(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?token=garbage-does-not-matter")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 x"


def test_file_requires_auth(client, session, storage):
    doc = stored_doc(session, storage)
    assert client.get(f"/api/documents/{doc.id}/file").status_code == 401
    assert client.get(f"/api/documents/{doc.id}/file?token=garbage").status_code == 401


def test_file_404_when_no_file(auth_client, session, storage):
    doc = seed_document(session, "NoFile", [{"content": "c"}])
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "no_file"


def test_file_is_inline_by_default(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline")


def test_file_is_attachment_when_download_requested(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?download=1")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")


def test_file_response_is_not_cached(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.headers["cache-control"] == "no-cache"


def office_doc(session, storage):
    doc = seed_document(session, "Contratto", [{"content": "c"}], original_filename="contratto.docx")
    rel, _ = storage.write_file(f"{doc.id}.docx", b"PK original docx")
    doc.file_path = rel
    doc.preview_path = storage.write_derived(f"{doc.id}.preview.pdf", b"%PDF-1.7 preview")
    session.commit()
    return doc


def test_preview_param_serves_preview_pdf_inline(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 preview"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")


def test_without_preview_param_serves_original(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file")
    assert resp.content == b"PK original docx"


def test_download_always_serves_original(auth_client, session, storage):
    doc = office_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?download=1&preview=1")
    assert resp.status_code == 200
    assert resp.content == b"PK original docx"
    assert resp.headers["content-disposition"].startswith("attachment")
    assert "contratto.docx" in resp.headers["content-disposition"]


def test_preview_param_without_preview_serves_file(auth_client, session, storage):
    doc = stored_doc(session, storage)
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 x"


def test_preview_missing_on_disk_falls_back_to_file(auth_client, session, storage):
    doc = office_doc(session, storage)
    storage.derived_abs(doc.preview_path).unlink()
    resp = auth_client.get(f"/api/documents/{doc.id}/file?preview=1")
    assert resp.status_code == 200
    assert resp.content == b"PK original docx"
