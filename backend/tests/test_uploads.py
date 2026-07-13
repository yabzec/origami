from sqlmodel import select

from app.models import DocStatus, DocType, Job, Tag


def upload(client, filename, content=b"data", **form):
    return client.post(
        "/api/documents/upload",
        files={"file": (filename, content, "application/octet-stream")},
        data=form,
    )


def test_upload_pdf_creates_pending_document_and_job(auth_client, session, storage):
    resp = upload(auth_client, "bolletta.pdf", b"%PDF-1.7 fake")
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.pdf
    assert body["status"] == DocStatus.pending
    assert body["title"] == "bolletta"
    assert body["original_filename"] == "bolletta.pdf"
    assert storage.abs_path(body["file_path"]).read_bytes() == b"%PDF-1.7 fake"

    job = session.exec(select(Job)).one()
    assert job.type == "process_document"
    assert job.payload == {"document_id": body["id"]}


def test_upload_respects_form_fields(auth_client, session, storage):
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]
    t1 = Tag(name="casa")
    session.add(t1)
    session.commit()

    resp = upload(
        auth_client, "foto.jpg", b"\xff\xd8fake",
        title="Foto contatore", folder_id=str(folder_id),
        tag_ids=str(t1.id), ocr_languages="eng",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.image
    assert body["title"] == "Foto contatore"
    assert body["folder_id"] == folder_id
    assert [t["id"] for t in body["tags"]] == [t1.id]
    assert body["ocr_languages"] == "eng"


def test_upload_video_and_text_types(auth_client, session, storage):
    assert upload(auth_client, "video.mp4").json()["doc_type"] == DocType.video
    assert upload(auth_client, "note.md").json()["doc_type"] == DocType.text
    assert upload(auth_client, "doc.docx").json()["doc_type"] == DocType.text


def test_upload_unsupported_extension_422(auth_client, storage):
    resp = upload(auth_client, "archive.zip")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unsupported_type"


def test_upload_bad_folder_404(auth_client, storage):
    resp = upload(auth_client, "a.pdf", folder_id="999999")
    assert resp.status_code == 404


def test_upload_requires_auth(client, storage):
    resp = upload(client, "a.pdf")
    assert resp.status_code == 401
