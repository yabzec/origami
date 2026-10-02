from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Job
from app.services.ocr import ocr_image
from app.worker import pipeline
from tests.helpers import make_text_image, seed_document


def _ready_doc(session, **kwargs):
    return seed_document(
        session,
        "Doc",
        [
            {"content": "testo", "page_number": 1},
            {"content": "riassunto", "source": ChunkSource.summary},
            {"content": "traduzione", "page_number": 1, "source": ChunkSource.translation},
            {"content": "Doc", "source": ChunkSource.metadata},
        ],
        summary="riassunto",
        detected_language="de",
        translation_status="done",
        **kwargs,
    )


def test_reprocess_resets_and_enqueues(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "deu"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["ocr_languages"] == "deu"
    assert body["summary"] is None
    assert body["detected_language"] is None
    assert body["translation_status"] is None

    sources = {c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))}
    assert sources == {ChunkSource.metadata}
    job = session.exec(select(Job).where(Job.type == "process_document")).one()
    assert job.payload == {"document_id": str(doc.id), "force_ocr": True}


def test_reprocess_busy_returns_409(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf, status=DocStatus.processing)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "document_busy"


def test_reprocess_twice_second_is_409(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf)
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).status_code == 200
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).status_code == 409
    assert len(session.exec(select(Job).where(Job.type == "process_document")).all()) == 1


def test_reprocess_video_is_rejected(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.video)
    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "not_reprocessable"


LONG_TEXT = "FATTURA NUMERO 12345 DEL 2026 IMPORTO 42 EURO CLIENTE ACME SRL MILANO"


def _pdf_doc(session, tmp_path, monkeypatch, ocr_applied, text=LONG_TEXT, status=DocStatus.pending):
    """A pdf document whose stored file already has a (Tesseract) text layer."""
    from app.services.storage import Storage

    storage = Storage(tmp_path / "store")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    img = make_text_image(tmp_path / "p.png", text, size=(3600, 400))
    pdf_bytes, _ = ocr_image(img, "eng")
    doc = seed_document(
        session, "Pdf", [], doc_type=DocType.pdf, status=status, ocr_languages="ita", ocr_applied=ocr_applied
    )
    rel, _ = storage.store_file(doc.id, ".pdf", pdf_bytes)
    doc.file_path = rel
    session.commit()
    return doc


def _spy_ocr(monkeypatch):
    """Pass-through spy on pdf_to_searchable_pdf (real function still runs)."""
    calls = []
    real = pipeline.pdf_to_searchable_pdf
    monkeypatch.setattr(
        pipeline, "pdf_to_searchable_pdf", lambda path, langs: calls.append(langs) or real(path, langs)
    )
    return calls


def _content(session, doc):
    return " ".join(
        c.content
        for c in session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.content)
        )
    )


def test_force_ocr_reocrs_previously_ocred_pdf(session, tmp_path, monkeypatch, llm_stub):
    doc = _pdf_doc(session, tmp_path, monkeypatch, ocr_applied=True)
    calls = _spy_ocr(monkeypatch)
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert calls == ["ita"]
    assert doc.status == DocStatus.ready
    assert doc.ocr_applied is True
    assert "FATTURA" in _content(session, doc).upper()


def test_force_ocr_reocrs_legacy_pdf_with_unknown_provenance(session, tmp_path, monkeypatch, llm_stub):
    doc = _pdf_doc(session, tmp_path, monkeypatch, ocr_applied=None)
    calls = _spy_ocr(monkeypatch)
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert calls == ["ita"]
    assert doc.ocr_applied is True


def test_force_ocr_keeps_born_digital_pdf(session, tmp_path, monkeypatch, llm_stub):
    doc = _pdf_doc(session, tmp_path, monkeypatch, ocr_applied=False)
    original = (tmp_path / "store" / doc.file_path).read_bytes()
    calls = _spy_ocr(monkeypatch)
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert calls == []  # good native text layer: never rasterized
    assert (tmp_path / "store" / doc.file_path).read_bytes() == original
    assert doc.ocr_applied is False
    assert doc.status == DocStatus.ready
    assert "FATTURA" in _content(session, doc).upper()


def test_normal_pipeline_records_native_pdf_as_not_ocred(session, tmp_path, monkeypatch, llm_stub):
    doc = _pdf_doc(session, tmp_path, monkeypatch, ocr_applied=None)
    calls = _spy_ocr(monkeypatch)
    pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert calls == []
    assert doc.ocr_applied is False


def test_normal_pipeline_records_ocred_pdf(session, tmp_path, monkeypatch, llm_stub):
    from PIL import Image as PILImage

    from app.services.storage import Storage

    storage = Storage(tmp_path / "store")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    img = make_text_image(tmp_path / "raw.png", LONG_TEXT, size=(3600, 400))
    raw_pdf = tmp_path / "raw.pdf"
    PILImage.open(img).convert("RGB").save(raw_pdf, "PDF")  # image-only: no text layer
    doc = seed_document(session, "Raw", [], doc_type=DocType.pdf, status=DocStatus.pending)
    rel, _ = storage.store_file(doc.id, ".pdf", raw_pdf.read_bytes())
    doc.file_path = rel
    session.commit()
    pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.ocr_applied is True


def test_force_ocr_scan_uses_stored_pdf_without_session(session, tmp_path, monkeypatch, llm_stub):
    from app.services.storage import Storage

    storage = Storage(tmp_path / "store")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    img = make_text_image(tmp_path / "s.png", "VERBALE 9")
    pdf_bytes, _ = ocr_image(img, "eng")
    doc = seed_document(session, "Scan", [], doc_type=DocType.scan, status=DocStatus.pending, ocr_applied=True)
    rel, _ = storage.store_file(doc.id, ".pdf", pdf_bytes)
    doc.file_path = rel
    session.commit()

    # payload has no scan_session_id: the scan branch must not be entered
    pipeline.process_document(session, {"document_id": str(doc.id), "force_ocr": True})
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 1


def _failed_scan_doc(auth_client, session):
    """Compile a scan whose first OCR failed before the PDF was stored (no file_path)."""
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "Scan"}).json()["id"]
    from app.models import Document, JobStatus

    job = session.exec(select(Job).where(Job.type == "process_document")).one()
    job.status = JobStatus.failed
    doc = session.get(Document, doc_id)
    doc.status = DocStatus.failed
    doc.error_message = "tesseract crashed"
    session.commit()
    assert doc.file_path is None
    return sid, doc


def test_reprocess_failed_scan_rebuilds_from_page_images(
    auth_client, fake_scanner, storage, session, monkeypatch, llm_stub
):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    sid, doc = _failed_scan_doc(auth_client, session)

    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "eng"})
    assert resp.status_code == 200
    job = session.exec(
        select(Job).where(Job.type == "process_document").order_by(Job.id.desc())
    ).first()
    assert job.payload == {"document_id": str(doc.id), "force_ocr": True, "scan_session_id": sid}

    pipeline.process_document(session, job.payload)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert doc.file_path is not None
    assert storage.abs_path(doc.file_path).exists()


def test_reprocess_scan_without_any_source_is_409(
    auth_client, fake_scanner, storage, session, monkeypatch
):
    from app.models import ScanPage

    sid, doc = _failed_scan_doc(auth_client, session)
    for page in session.exec(select(ScanPage).where(ScanPage.session_id == sid)).all():
        session.delete(page)
    session.commit()
    storage.remove_scan_session_dir(sid)
    chunk_count = len(session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all())

    resp = auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "deu"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_source"
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert doc.ocr_languages == "eng"
    assert doc.error_message == "tesseract crashed"
    assert len(session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()) == chunk_count
    assert len(session.exec(select(Job).where(Job.type == "process_document")).all()) == 1
