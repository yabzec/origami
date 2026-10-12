import pytest
from sqlmodel import select

from app.models import DocType, Document, DocumentAttachment
from app.services.storage_check import check_storage
from app.services.tree_sync import AttachmentSource, write_bundle


@pytest.fixture
def report_file(tmp_path):
    path = tmp_path / "staged-report.pdf"
    path.write_bytes(b"%PDF-1.4 report")
    return path


def make_bundle(session, storage, report_file, title="Study", folder_id=None, reports=1):
    doc = Document(title=title, doc_type=DocType.pdf, folder_id=folder_id, extra_metadata={})
    session.add(doc)
    session.commit()
    session.refresh(doc)
    sources = [AttachmentSource(role="report", filename="Report.pdf", source=report_file, page_count=1)] * reports
    write_bundle(session, storage, doc, ".pdf", b"%PDF-1.4 images", sources)
    session.refresh(doc)
    return doc


def attachments(session, doc):
    return session.exec(
        select(DocumentAttachment).where(DocumentAttachment.document_id == doc.id).order_by(DocumentAttachment.id)
    ).all()


def test_write_bundle_creates_dir_with_main_file_and_attachment(session, storage, report_file):
    doc = make_bundle(session, storage, report_file)
    assert doc.file_path == "Study/Study.pdf"
    assert storage.abs_path("Study/Study.pdf").read_bytes() == b"%PDF-1.4 images"
    [att] = attachments(session, doc)
    assert (att.filename, att.file_path, att.page_count) == ("Report.pdf", "Study/Report.pdf", 1)
    assert storage.abs_path("Study/Report.pdf").read_bytes() == b"%PDF-1.4 report"


def test_two_reports_get_distinct_names(session, storage, report_file):
    doc = make_bundle(session, storage, report_file, reports=2)
    assert [a.filename for a in attachments(session, doc)] == ["Report.pdf", "Report (2).pdf"]


def test_bundle_avoids_sibling_folder_name(auth_client, session, storage, report_file):
    assert auth_client.post("/api/folders", json={"name": "Study"}).status_code == 201
    doc = make_bundle(session, storage, report_file)
    assert doc.file_path == "Study (2)/Study (2).pdf"


def test_main_file_never_overwrites_attachment(session, storage, report_file):
    doc = make_bundle(session, storage, report_file, title="Report")
    assert doc.file_path == "Report/Report (2).pdf"
    assert storage.abs_path("Report/Report.pdf").read_bytes() == b"%PDF-1.4 report"


def test_bundle_title_with_slash_and_rename(auth_client, session, storage, report_file):
    doc = make_bundle(session, storage, report_file, title="Rx gamba Destro/a")
    assert doc.file_path == "Rx gamba Destro_a/Rx gamba Destro_a.pdf"
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"title": "Rx tibia"})
    assert resp.status_code == 200
    session.refresh(doc)
    assert doc.file_path == "Rx tibia/Rx tibia.pdf"
    assert [a.file_path for a in attachments(session, doc)] == ["Rx tibia/Report.pdf"]
    assert storage.abs_path("Rx tibia/Report.pdf").exists()
    assert not storage.abs_path("Rx gamba Destro_a").exists()


def test_move_bundle_to_folder(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    doc = make_bundle(session, storage, report_file)
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"folder_id": folder_id})
    assert resp.status_code == 200
    session.refresh(doc)
    assert doc.file_path == "Salute/Study/Study.pdf"
    assert attachments(session, doc)[0].file_path == "Salute/Study/Report.pdf"
    assert storage.abs_path("Salute/Study/Report.pdf").exists()


def test_folder_rename_moves_attachment_paths(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    doc = make_bundle(session, storage, report_file, folder_id=folder_id)
    assert auth_client.patch(f"/api/folders/{folder_id}", json={"name": "Medico"}).status_code == 200
    session.expire_all()
    assert session.get(Document, doc.id).file_path == "Medico/Study/Study.pdf"
    assert attachments(session, doc)[0].file_path == "Medico/Study/Report.pdf"
    assert storage.abs_path("Medico/Study/Report.pdf").exists()


def test_new_folder_cannot_take_bundle_dir_name(auth_client, session, storage, report_file):
    make_bundle(session, storage, report_file)
    resp = auth_client.post("/api/folders", json={"name": "Study"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "duplicate_folder"


def test_delete_removes_bundle_dir(auth_client, session, storage, report_file):
    doc = make_bundle(session, storage, report_file)
    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not storage.abs_path("Study").exists()
    assert session.exec(select(DocumentAttachment)).all() == []


def test_bulk_folder_delete_removes_bundle(auth_client, session, storage, report_file):
    folder_id = auth_client.post("/api/folders", json={"name": "Salute"}).json()["id"]
    make_bundle(session, storage, report_file, folder_id=folder_id)
    resp = auth_client.post("/api/bulk/delete", json={"folder_ids": [folder_id], "document_ids": []})
    assert resp.status_code == 200
    assert not storage.abs_path("Salute").exists()


def test_check_storage_counts_attachments(session, storage, report_file):
    make_bundle(session, storage, report_file)
    report = check_storage(session, storage)
    assert report.ok, (report.missing, report.unreferenced)


def test_flat_documents_unchanged(auth_client, session, storage):
    resp = auth_client.post(
        "/api/documents/upload", files={"file": ("Study.pdf", b"%PDF", "application/pdf")}
    )
    assert resp.json()["file_path"] == "Study.pdf"
