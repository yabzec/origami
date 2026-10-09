import io
import uuid

from app.services.storage import Storage


def test_scan_session_dirs(tmp_path):
    storage = Storage(tmp_path)
    d = storage.scan_session_dir(7)
    assert d.is_dir()
    (d / "page_001.png").write_bytes(b"png")
    storage.remove_scan_session_dir(7)
    assert not d.exists()


import os
import time

import pytest

from app.services.storage import OldStorageLayout, companion_name, preview_name, require_new_layout


def make_storage(tmp_path):
    return Storage(tmp_path / "storage")


def test_roots_default_to_siblings(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.root == tmp_path / "storage"
    assert storage.derived_root == tmp_path / "derived"
    assert storage.tmp_root == tmp_path / "tmp"


def test_explicit_roots(tmp_path):
    storage = Storage(tmp_path / "s", tmp_path / "d2", tmp_path / "t2")
    assert storage.derived_root == tmp_path / "d2"
    assert storage.tmp_root == tmp_path / "t2"


def test_write_file_creates_dirs_and_leaves_no_part(tmp_path):
    storage = make_storage(tmp_path)
    rel, size = storage.write_file("Home/Bills/Invoice.pdf", b"%PDF-1")
    assert rel == "Home/Bills/Invoice.pdf"
    assert size == 6
    assert storage.abs_path(rel).read_bytes() == b"%PDF-1"
    assert not list(storage.root.rglob("*.part"))


def test_write_file_streams_fileobj(tmp_path):
    storage = make_storage(tmp_path)
    rel, size = storage.write_file("a.mp4", io.BytesIO(b"0123456789"))
    assert size == 10
    assert storage.abs_path(rel).read_bytes() == b"0123456789"


def test_write_file_replaces_existing(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("a.pdf", b"old")
    storage.write_file("a.pdf", b"new")
    assert storage.abs_path("a.pdf").read_bytes() == b"new"


def test_write_derived(tmp_path):
    storage = make_storage(tmp_path)
    doc_id = uuid.uuid4()
    name = storage.write_derived(preview_name(doc_id), b"%PDF")
    assert name == f"{doc_id}.preview.pdf"
    assert storage.derived_abs(name).read_bytes() == b"%PDF"
    assert companion_name(doc_id) == f"{doc_id}.ocr.pdf"


def test_move_file(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("A/x.pdf", b"1")
    assert storage.move_file("A/x.pdf", "B/C/y.pdf") is True
    assert storage.abs_path("B/C/y.pdf").read_bytes() == b"1"
    assert not storage.abs_path("A/x.pdf").exists()


def test_move_file_missing_source_returns_false(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.move_file("nope.pdf", "other.pdf") is False


def test_move_file_refuses_existing_target(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("a.pdf", b"1")
    storage.write_file("b.pdf", b"2")
    with pytest.raises(FileExistsError):
        storage.move_file("a.pdf", "b.pdf")
    assert storage.abs_path("b.pdf").read_bytes() == b"2"


def test_move_dir(tmp_path):
    storage = make_storage(tmp_path)
    storage.write_file("A/B/x.pdf", b"1")
    assert storage.move_dir("A/B", "C/D") is True
    assert storage.abs_path("C/D/x.pdf").exists()
    assert not storage.abs_path("A/B").exists()


def test_move_dir_missing_source_creates_target(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.move_dir("Gone", "New") is False
    assert storage.abs_path("New").is_dir()


def test_move_dir_refuses_existing_target(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("A")
    storage.make_dir("B")
    with pytest.raises(FileExistsError):
        storage.move_dir("A", "B")


def test_remove_dir_keeps_non_empty(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("Empty")
    storage.write_file("Full/x.pdf", b"1")
    storage.remove_dir("Empty")
    storage.remove_dir("Full")
    storage.remove_dir("Missing")
    assert not storage.abs_path("Empty").exists()
    assert storage.abs_path("Full/x.pdf").exists()


def test_delete_document_files(tmp_path):
    storage = make_storage(tmp_path)
    doc_id = uuid.uuid4()
    storage.write_file("a.png", b"1")
    storage.write_derived(preview_name(doc_id), b"2")
    storage.write_derived(companion_name(doc_id), b"3")
    storage.delete_document_files("a.png", preview_name(doc_id), doc_id)
    assert not storage.abs_path("a.png").exists()
    assert not list(storage.derived_root.iterdir())
    storage.delete_document_files(None, None, doc_id)  # idempotent


def test_remove_part_files_only_old(tmp_path):
    storage = make_storage(tmp_path)
    storage.make_dir("A")
    old = storage.abs_path("A/x.pdf.part")
    old.write_bytes(b"")
    past = time.time() - 7200
    os.utime(old, (past, past))
    fresh = storage.abs_path("y.pdf.part")
    fresh.write_bytes(b"")
    assert storage.remove_part_files(3600) == [old]
    assert fresh.exists()


def test_old_layout_detection(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.has_old_layout() is False
    require_new_layout(storage)
    storage.write_file("files/notes.txt", b"user folder named files")
    assert storage.has_old_layout() is False
    storage.write_file(f"files/{uuid.uuid4()}.pdf", b"%PDF")
    assert storage.has_old_layout() is True
    with pytest.raises(OldStorageLayout, match="migrate-storage"):
        require_new_layout(storage)
