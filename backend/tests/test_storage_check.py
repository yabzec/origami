import pytest

from app.models import DocType, Document
from app.services.storage import Storage
from app.services.storage_check import check_storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "storage")


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
