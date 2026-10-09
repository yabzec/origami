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


def _blocked_while_tree_locked(engine, work):
    """Run `work(session)` in a thread while another session holds the tree lock.

    Returns (finished_while_locked, error)."""
    import threading

    from sqlmodel import Session

    from app.services.tree_sync import lock_tree

    errors = []

    def run():
        with Session(engine) as other:
            try:
                work(other)
            except BaseException as exc:  # surfaced by the test
                errors.append(exc)

    with Session(engine) as holder:
        lock_tree(holder)
        thread = threading.Thread(target=run)
        thread.start()
        thread.join(0.7)
        finished_while_locked = not thread.is_alive()
        holder.commit()  # releases the advisory lock
    thread.join(10)
    assert not thread.is_alive()
    return finished_while_locked, errors[0] if errors else None


def test_write_document_file_waits_for_tree_lock_and_same_titles_stay_distinct(session, engine, store):
    from sqlmodel import Session

    first, second = make_doc(session), make_doc(session)

    def write_second(other):
        doc = other.get(Document, second.id)
        write_document_file(other, store, doc, ".pdf", b"second")

    import threading

    from app.services.tree_sync import lock_tree

    done = threading.Event()

    def run():
        with Session(engine) as other:
            write_second(other)
        done.set()

    with Session(engine) as holder:
        lock_tree(holder)
        thread = threading.Thread(target=run)
        thread.start()
        assert not done.wait(0.7), "write_document_file did not wait for the tree lock"
        doc = holder.get(Document, first.id)
        write_document_file(holder, store, doc, ".pdf", b"first")  # commits: releases the lock
    thread.join(10)
    assert done.is_set()
    session.expire_all()
    a, b = session.get(Document, first.id), session.get(Document, second.id)
    assert {a.file_path, b.file_path} == {"Invoice.pdf", "Invoice (2).pdf"}
    assert store.abs_path(a.file_path).read_bytes() == b"first"
    assert store.abs_path(b.file_path).read_bytes() == b"second"
    assert not list(store.root.rglob("*.part"))


def test_folder_rename_waits_for_tree_lock(session, engine, store):
    from app.api.folders import FolderPatch, update_folder

    folder = Folder(name="Bills")
    session.add(folder)
    session.commit()
    finished, error = _blocked_while_tree_locked(
        engine, lambda other: update_folder(folder.id, FolderPatch(name="Old bills"), other, store)
    )
    assert error is None
    assert not finished, "update_folder did not wait for the tree lock"
    session.expire_all()
    assert session.get(Folder, folder.id).name == "Old bills"


@pytest.mark.parametrize("bulk", [False, True])
def test_delete_removes_the_file_a_concurrent_rename_moved(session, engine, store, bulk):
    import threading

    from sqlmodel import Session

    from app.api.documents import BulkIds, bulk_delete, delete_document

    doc = make_doc(session)
    write_document_file(session, store, doc, ".pdf", b"1")
    done = threading.Event()

    def run():
        with Session(engine) as other:
            if bulk:
                bulk_delete(BulkIds(ids=[doc.id]), other, store)
            else:
                delete_document(doc.id, other, store)
        done.set()

    with Session(engine) as holder:
        with disk_transaction(holder, store) as moves:
            [locked] = lock_documents(holder, [doc.id])
            thread = threading.Thread(target=run)
            thread.start()
            assert not done.wait(0.7), "delete did not wait for the row lock"
            locked.title = "Renamed"
            relocate_document(holder, store, moves, locked)
    thread.join(10)
    assert done.is_set()
    assert not store.abs_path("Renamed.pdf").exists()
    assert not store.abs_path("Invoice.pdf").exists()
