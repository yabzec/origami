import pytest
from fastapi import HTTPException

from app.api.storage_errors import storage_errors
from app.models import DocType, Document, Folder
from app.services.storage import Storage
from app.services.tree_sync import (
    disk_name_taken,
    disk_transaction,
    lock_documents,
    relocate_document,
    write_document_file,
)


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "storage")


def make_doc(session, title="Invoice", **kw):
    doc = Document(title=title, doc_type=kw.pop("doc_type", DocType.pdf), **kw)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_write_document_file_places_by_title(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".PDF", b"%PDF")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert doc.file_size == 4
    assert store.abs_path("Invoice.pdf").read_bytes() == b"%PDF"


def test_write_document_file_same_ext_overwrites_in_place(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"old")
    write_document_file(session, store, doc, ".pdf", b"new!")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert store.abs_path("Invoice.pdf").read_bytes() == b"new!"


def test_write_document_file_new_ext_removes_old_file(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".png", b"png")
    write_document_file(session, store, doc, ".pdf", b"%PDF")
    session.refresh(doc)
    assert doc.file_path == "Invoice.pdf"
    assert not store.abs_path("Invoice.png").exists()


def test_disk_transaction_commits_moves(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"1")
    with disk_transaction(session, store) as moves:
        [locked] = lock_documents(session, [doc.id])
        locked.title = "Renamed"
        relocate_document(session, store, moves, locked)
    session.refresh(doc)
    assert doc.file_path == "Renamed.pdf"
    assert store.abs_path("Renamed.pdf").exists()


def test_disk_transaction_undoes_moves_on_error(session, store):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"1")
    with pytest.raises(RuntimeError):
        with disk_transaction(session, store) as moves:
            [locked] = lock_documents(session, [doc.id])
            locked.title = "Renamed"
            relocate_document(session, store, moves, locked)
            raise RuntimeError("commit would fail")
    session.refresh(doc)
    assert doc.title == "Invoice"
    assert doc.file_path == "Invoice.pdf"
    assert store.abs_path("Invoice.pdf").exists()
    assert not store.abs_path("Renamed.pdf").exists()


def test_relocate_without_file_only_skips(session, store):
    doc = make_doc(session)
    with disk_transaction(session, store) as moves:
        relocate_document(session, store, moves, doc)
    assert doc.file_path is None


def test_disk_name_taken(session):
    home = Folder(name="Home")
    session.add(home)
    session.commit()
    session.add(Folder(name="a/b", parent_id=home.id))
    session.commit()
    assert disk_name_taken(session, home.id, "a_b") is True
    assert disk_name_taken(session, home.id, "a:b") is True
    assert disk_name_taken(session, home.id, "other") is False
    assert disk_name_taken(session, None, "Home") is True


def test_storage_errors_mapping(tmp_path):
    with pytest.raises(HTTPException) as conflict:
        with storage_errors():
            raise FileExistsError(17, "Already exists", str(tmp_path / "x"))
    assert conflict.value.status_code == 409
    assert conflict.value.detail["error"]["code"] == "storage_conflict"
    with pytest.raises(HTTPException) as broken:
        with storage_errors():
            raise PermissionError(13, "Permission denied", str(tmp_path / "y"))
    assert broken.value.status_code == 500
    assert broken.value.detail["error"]["code"] == "storage_error"
    assert "y" in broken.value.detail["error"]["message"]


def test_write_document_file_commit_failure_keeps_old_file_on_ext_change(session, store, monkeypatch):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".png", b"png")
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        write_document_file(session, store, doc, ".pdf", b"%PDF")
    monkeypatch.undo()
    session.refresh(doc)
    assert doc.file_path == "Invoice.png"
    assert store.abs_path("Invoice.png").read_bytes() == b"png"
    assert not store.abs_path("Invoice.pdf").exists()


def test_write_document_file_commit_failure_keeps_file_on_overwrite(session, store, monkeypatch):
    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"old")
    monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        write_document_file(session, store, doc, ".pdf", b"new!")
    monkeypatch.undo()
    assert store.abs_path("Invoice.pdf").exists()
