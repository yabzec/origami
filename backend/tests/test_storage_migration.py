import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.models import DocType, Document, Folder, ScanPage, ScanSession
from app.services.storage import Storage
from app.services.storage import OldStorageLayout
from app.services.storage_migration import check_storage, migrate_storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "storage")


def old_doc(session, store, title, ext, folder_id=None, data=b"x", **kw):
    doc = Document(title=title, doc_type=kw.pop("doc_type", DocType.pdf), folder_id=folder_id, **kw)
    session.add(doc)
    session.commit()
    rel = f"files/{doc.id}{ext}"
    store.abs_path(rel).parent.mkdir(parents=True, exist_ok=True)
    store.abs_path(rel).write_bytes(data)
    doc.file_path = rel
    session.commit()
    return doc


def test_migrates_documents_previews_companions_and_tmp(session, store):
    home = Folder(name="Home")
    session.add(home)
    session.commit()
    pdf = old_doc(session, store, "Invoice", ".pdf", home.id)
    image = old_doc(session, store, "Photo", ".png", doc_type=DocType.image)
    store.abs_path(f"files/{image.id}.pdf").write_bytes(b"companion")
    office = old_doc(session, store, "Letter", ".docx", doc_type=DocType.text)
    store.abs_path(f"files/{office.id}.preview.pdf").write_bytes(b"preview")
    office.preview_path = f"files/{office.id}.preview.pdf"
    session.commit()
    scan = ScanSession(ocr_languages="ita")
    session.add(scan)
    session.commit()
    store.abs_path(f"tmp/scan_sessions/{scan.id}").mkdir(parents=True)
    store.abs_path(f"tmp/scan_sessions/{scan.id}/page_001.png").write_bytes(b"png")
    session.add(ScanPage(session_id=scan.id, page_number=1, image_path=f"tmp/scan_sessions/{scan.id}/page_001.png"))
    session.commit()
    assert store.has_old_layout()

    report = migrate_storage(session, store)

    session.expire_all()
    assert session.get(Document, pdf.id).file_path == "Home/Invoice.pdf"
    assert session.get(Document, image.id).file_path == "Photo.png"
    assert session.get(Document, office.id).preview_path == f"{office.id}.preview.pdf"
    assert store.derived_abs(f"{image.id}.ocr.pdf").read_bytes() == b"companion"
    assert store.derived_abs(f"{office.id}.preview.pdf").read_bytes() == b"preview"
    page = session.exec(select(ScanPage)).one()
    assert page.image_path == f"scan_sessions/{scan.id}/page_001.png"
    assert store.tmp_abs(page.image_path).read_bytes() == b"png"
    assert not (store.root / "files").exists()
    assert not (store.root / "tmp").exists()
    assert not store.has_old_layout()
    assert report.missing == []


def test_rerun_is_noop(session, store):
    old_doc(session, store, "A", ".pdf")
    migrate_storage(session, store)
    assert migrate_storage(session, store).moved == []


def test_dry_run_changes_nothing(session, store):
    doc = old_doc(session, store, "A", ".pdf")
    report = migrate_storage(session, store, dry_run=True)
    assert report.moved == [(f"files/{doc.id}.pdf", "A.pdf")]
    session.refresh(doc)
    assert doc.file_path == f"files/{doc.id}.pdf"
    assert store.abs_path(f"files/{doc.id}.pdf").exists()


def test_missing_source_is_reported(session, store):
    doc = old_doc(session, store, "A", ".pdf")
    store.abs_path(doc.file_path).unlink()
    report = migrate_storage(session, store)
    assert report.missing == [f"files/{doc.id}.pdf"]


def test_same_titles_get_suffixes(session, store):
    old_doc(session, store, "A", ".pdf", data=b"1")
    old_doc(session, store, "A", ".pdf", data=b"2")
    migrate_storage(session, store)
    assert sorted(p.name for p in store.root.iterdir()) == ["A (2).pdf", "A.pdf"]


def test_check_reports_and_fixes(session, store):
    session.add(Document(title="Gone", doc_type=DocType.pdf, file_path="Gone.pdf"))
    session.commit()
    store.write_file("Stray.pdf", b"1")
    store.abs_path("half.pdf.part").write_bytes(b"")
    report = check_storage(session, store)
    assert report.missing == ["Gone.pdf"]
    assert report.unreferenced == ["Stray.pdf"]
    assert report.parts == ["half.pdf.part"]
    assert not report.ok
    check_storage(session, store, fix=True)
    assert not store.abs_path("half.pdf.part").exists()


def test_api_refuses_to_start_on_old_layout(store):
    from app.main import app
    from app.services import storage as storage_module

    store.abs_path("files").mkdir(parents=True)
    store.abs_path(f"files/{uuid.uuid4()}.pdf").write_bytes(b"%PDF")
    original = storage_module.get_storage
    storage_module.get_storage = lambda: store
    try:
        with pytest.raises(OldStorageLayout, match="migrate-storage"):
            with TestClient(app):
                pass
    finally:
        storage_module.get_storage = original
