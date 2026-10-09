import json
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
    session.refresh(doc)
    assert doc.file_path == "A.pdf"


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


def test_crash_between_move_and_commit_resumes(session, store):
    doc = old_doc(session, store, "A", ".pdf", data=b"data")
    old = doc.file_path
    store.derived_root.mkdir(parents=True, exist_ok=True)
    (store.derived_root / "storage-migration.journal").write_text(
        json.dumps({"doc_id": str(doc.id), "old": old, "new": "A.pdf"}) + "\n"
    )
    store.abs_path(old).rename(store.abs_path("A.pdf"))
    report = migrate_storage(session, store)
    session.expire_all()
    assert session.get(Document, doc.id).file_path == "A.pdf"
    assert store.abs_path("A.pdf").read_bytes() == b"data"
    assert not store.abs_path("A (2).pdf").exists()
    assert report.missing == []
    assert not (store.derived_root / "storage-migration.journal").exists()


def test_stranded_companion_is_moved(session, store):
    doc = Document(title="Photo", doc_type=DocType.image, file_path="Photo.png")
    session.add(doc)
    session.commit()
    store.write_file("Photo.png", b"img")
    store.abs_path("files").mkdir(parents=True)
    store.abs_path(f"files/{doc.id}.pdf").write_bytes(b"companion")
    assert store.has_old_layout()
    migrate_storage(session, store)
    assert store.derived_abs(f"{doc.id}.ocr.pdf").read_bytes() == b"companion"
    assert not store.has_old_layout()


def test_uuid_orphan_is_quarantined(session, store):
    name = f"{uuid.uuid4()}.pdf"
    store.abs_path("files").mkdir(parents=True)
    store.abs_path(f"files/{name}").write_bytes(b"orphan")
    report = migrate_storage(session, store)
    assert store.derived_abs(f"orphans/{name}").read_bytes() == b"orphan"
    assert report.quarantined == [name]
    assert not store.has_old_layout()
    assert not (store.root / "files").exists()


def test_root_folder_named_files_blocks_migration(session, store):
    from app.services.storage_migration import ReservedFolderExists

    session.add(Folder(name="Files"))
    session.commit()
    doc = old_doc(session, store, "A", ".pdf")
    with pytest.raises(ReservedFolderExists, match="rename it"):
        migrate_storage(session, store)
    session.refresh(doc)
    assert doc.file_path == f"files/{doc.id}.pdf"
    assert store.abs_path(doc.file_path).exists()


def test_sweep_never_moves_a_file_a_document_points_at(session, store):
    from app.services.storage_migration import MigrationReport, _sweep_files_dir

    other = Document(title="Other", doc_type=DocType.image, file_path="Other.png")
    session.add(other)
    session.commit()
    names = [f"{uuid.uuid4()}.txt", f"{other.id}.pdf"]  # a uuid-named file, and one shaped like a companion
    for name in names:
        store.write_file(f"files/{name}", b"live")
        session.add(Document(title=name, doc_type=DocType.text, file_path=f"files/{name}"))
    session.commit()
    report = MigrationReport()
    _sweep_files_dir(session, store, False, report)
    for name in names:
        assert store.abs_path(f"files/{name}").read_bytes() == b"live"
    assert report.quarantined == []
    assert report.moved == []
