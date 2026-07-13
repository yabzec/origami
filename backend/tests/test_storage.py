import io
import uuid

from app.services.storage import Storage


def test_store_and_delete_file(tmp_path):
    storage = Storage(tmp_path)
    doc_id = uuid.uuid4()
    rel, size = storage.store_file(doc_id, ".pdf", b"%PDF-fake")
    assert rel == f"files/{doc_id}.pdf"
    assert size == 9
    assert storage.abs_path(rel).read_bytes() == b"%PDF-fake"

    storage.delete_document_file(rel)
    assert not storage.abs_path(rel).exists()
    storage.delete_document_file(rel)  # idempotent


def test_scan_session_dirs(tmp_path):
    storage = Storage(tmp_path)
    d = storage.scan_session_dir(7)
    assert d.is_dir()
    (d / "page_001.png").write_bytes(b"png")
    storage.remove_scan_session_dir(7)
    assert not d.exists()


def test_store_fileobj_streams_and_sizes(tmp_path):
    import uuid as uuid_mod

    from app.services.storage import Storage

    storage = Storage(tmp_path)
    doc_id = uuid_mod.uuid4()
    rel, size = storage.store_fileobj(doc_id, ".mp4", io.BytesIO(b"0123456789"))
    assert rel == f"files/{doc_id}.mp4"
    assert size == 10
    assert storage.abs_path(rel).read_bytes() == b"0123456789"
