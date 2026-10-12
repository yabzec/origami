from datetime import date, datetime, timedelta, timezone

import pytest
from pypdf import PdfReader
from sqlmodel import select

from app.models import (
    Chunk, ChunkSource, DiscImport, DiscImportStatus, DocStatus, DocType, Document, DocumentAttachment, DocumentOrigin, Job,
    JobStatus,
)
from app.services.storage import Storage
from app.worker import pipeline
from tests.helpers import make_dicom, make_text_pdf

REPORT = ["Referto: frattura pressoche saldata con callo osseo al terzo medio della tibia destra."]
PROPS = [{"key": "Patient", "value": "Mario Rossi"}, {"key": "Modality", "value": "DX"}, {"key": "Series", "value": "AP (2)"}]


@pytest.fixture
def pipeline_storage(tmp_path, monkeypatch):
    s = Storage(tmp_path / "storage")
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: s)
    return s


@pytest.fixture
def disc(session, user, pipeline_storage):
    imp = DiscImport(user_id=user.id)
    session.add(imp)
    session.commit()
    root = pipeline_storage.disc_import_dir(imp.id)
    make_dicom(root / "DICOM/a", series_number=1)
    make_dicom(root / "DICOM/b", series_number=2, frames=2)
    make_text_pdf(root / "REPORT/r.pdf", REPORT)
    make_text_pdf(root / "REPORT/scan.pdf", [])
    return imp


def commit_disc(session, imp):
    imp.status = DiscImportStatus.committed
    session.commit()


def make_study_doc(session, **kw):
    doc = Document(
        title=kw.pop("title", "Rx gamba"), doc_type=DocType.pdf, properties=PROPS, origin=DocumentOrigin.xray_disc,
        document_date=date(2026, 8, 28), ocr_enabled=kw.pop("ocr_enabled", False), **kw,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def payload(doc, imp, reports=("REPORT/r.pdf",)):
    return {
        "document_id": str(doc.id),
        "disc_import_id": str(imp.id),
        "images": [
            {"path": "DICOM/a", "kind": "dicom", "label": "AP"},
            {"path": "DICOM/b", "kind": "dicom", "label": "Lateral"},
        ],
        "reports": [{"path": p} for p in reports],
    }


def run(session, doc, data):
    pipeline.process_document(session, data)
    session.refresh(doc)
    return doc


def test_bundle_pdf_chunks_summary_and_cleanup(session, pipeline_storage, disc, llm_stub):
    commit_disc(session, disc)
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc))
    assert doc.status == DocStatus.ready
    assert doc.file_path == "Rx gamba/Rx gamba.pdf"
    assert doc.page_count == 4  # cover + 1 + 2 frames
    assert doc.ocr_applied is False
    reader = PdfReader(pipeline_storage.abs_path(doc.file_path))
    assert len(reader.pages) == 4
    [att] = session.exec(select(DocumentAttachment)).all()
    assert (att.filename, att.file_path, att.page_count) == ("Report.pdf", "Rx gamba/Report.pdf", 1)
    content = session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()
    assert content and all(c.attachment_id == att.id for c in content)
    [call] = llm_stub["describe"]
    assert call["image_path"] is None
    assert "tibia destra" in call["text"] and "Patient: Mario Rossi" in call["text"]
    assert call["text"].index("Patient: Mario Rossi") < call["text"].index("tibia destra")
    assert not pipeline_storage.disc_import_path(disc.id).exists()


def test_study_without_report_is_flat(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc, reports=()))
    assert doc.file_path == "Rx gamba.pdf"
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all() == []
    [call] = llm_stub["describe"]
    assert call["text"].startswith("Patient: Mario Rossi")


def test_textless_report_needs_ocr(session, pipeline_storage, disc, llm_stub, monkeypatch):
    calls = []

    def fake_ocr(path, languages):
        calls.append(path)
        return b"%PDF ocr", [(1, "Testo riconosciuto dal referto scansionato, abbastanza lungo.")]

    monkeypatch.setattr(pipeline, "pdf_to_searchable_pdf", fake_ocr)
    doc = make_study_doc(session)
    run(session, doc, payload(doc, disc, reports=("REPORT/scan.pdf",)))
    assert calls == []  # OCR off: no text, no OCR
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all() == []

    other = make_study_doc(session, title="Con OCR", ocr_enabled=True)
    imp2 = DiscImport(user_id=disc.user_id)
    session.add(imp2)
    session.commit()
    root = pipeline_storage.disc_import_dir(imp2.id)
    make_dicom(root / "DICOM/a")
    make_text_pdf(root / "REPORT/scan.pdf", [])
    run(session, other, payload(other, imp2, reports=("REPORT/scan.pdf",)) | {"images": [{"path": "DICOM/a", "kind": "dicom", "label": "AP"}]})
    assert len(calls) == 1
    [chunk] = session.exec(
        select(Chunk).where(Chunk.document_id == other.id, Chunk.source == ChunkSource.content)
    ).all()
    assert chunk.content.startswith("Testo riconosciuto")
    assert pipeline_storage.abs_path("Con OCR/Report.pdf").read_bytes().startswith(b"%PDF-1.4")  # original kept


def test_retry_does_not_rebuild_bundle(session, pipeline_storage, disc, llm_stub, monkeypatch):
    original_embed = pipeline.llm_embed
    failures = {"left": 1}

    def flaky_embed(texts, role="document"):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("embedding provider down")
        return original_embed(texts, role)

    monkeypatch.setattr(pipeline, "llm_embed", flaky_embed)
    doc = make_study_doc(session)
    data = payload(doc, disc) | {"_final_attempt": False}
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, data)
    session.refresh(doc)
    assert doc.status == DocStatus.pending
    doc = run(session, doc, payload(doc, disc))
    assert doc.status == DocStatus.ready
    assert doc.file_path == "Rx gamba/Rx gamba.pdf"
    assert len(session.exec(select(DocumentAttachment)).all()) == 1
    assert not pipeline_storage.abs_path("Rx gamba (2)").exists()


def test_staging_kept_until_import_committed(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    run(session, doc, payload(doc, disc))
    assert pipeline_storage.disc_import_path(disc.id).exists()  # still staging: no release


def test_unreadable_image_names_the_label(session, pipeline_storage, disc, llm_stub, monkeypatch):
    def boom(path, kind):
        raise ValueError("bad pixel data")

    monkeypatch.setattr(pipeline, "render", boom)
    doc = make_study_doc(session)
    with pytest.raises(RuntimeError, match="AP: image cannot be decoded"):
        pipeline.process_document(session, payload(doc, disc))
    session.refresh(doc)
    assert "AP: image cannot be decoded" in doc.error_message


def test_other_pending_job_keeps_staging(session, pipeline_storage, disc, llm_stub):
    commit_disc(session, disc)
    first, second = make_study_doc(session), make_study_doc(session, title="Second")
    session.add(Job(type="process_document", payload=payload(second, disc), run_at=datetime.now(timezone.utc)))
    session.commit()
    run(session, first, payload(first, disc))
    assert pipeline_storage.disc_import_path(disc.id).exists()


def test_reprocess_keeps_image_pdf(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    doc = run(session, doc, payload(doc, disc))
    before = pipeline_storage.abs_path(doc.file_path).read_bytes()
    for chunk in session.exec(select(Chunk).where(Chunk.document_id == doc.id)):
        session.delete(chunk)
    doc.summary = None
    session.commit()
    doc = run(session, doc, {"document_id": str(doc.id), "force_ocr": True})
    assert doc.status == DocStatus.ready
    assert pipeline_storage.abs_path(doc.file_path).read_bytes() == before
    assert session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()


def test_missing_staging_fails_with_clear_message(session, pipeline_storage, disc, llm_stub):
    doc = make_study_doc(session)
    pipeline_storage.remove_disc_import_dir(disc.id)
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, payload(doc, disc))
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "import the disc again" in doc.error_message


def test_sweep_removes_old_imports(session, pipeline_storage, disc, llm_stub):
    disc.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    session.commit()
    pipeline.sweep_scan_sessions(session, {})
    assert session.get(DiscImport, disc.id) is None
    assert not pipeline_storage.disc_import_path(disc.id).exists()


def test_sweep_keeps_imports_with_jobs_in_flight(session, pipeline_storage, disc, llm_stub):
    disc.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    doc = make_study_doc(session)
    session.add(Job(type="process_document", payload=payload(doc, disc), run_at=datetime.now(timezone.utc)))
    session.commit()
    pipeline.sweep_scan_sessions(session, {})
    assert session.get(DiscImport, disc.id) is not None


def test_properties_on_uploaded_pdf_do_not_route_to_study(session, pipeline_storage, llm_stub):
    pipeline_storage.abs_path("Plain.pdf").parent.mkdir(parents=True, exist_ok=True)
    make_text_pdf(pipeline_storage.abs_path("Plain.pdf"), ["Fattura numero 12 del fornitore"])
    doc = Document(
        title="Plain", doc_type=DocType.pdf, file_path="Plain.pdf", ocr_enabled=False,
        properties=[{"key": "Fornitore", "value": "ACME"}],
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    doc = run(session, doc, {"document_id": str(doc.id)})
    assert doc.status == DocStatus.ready
    assert doc.origin == "file"
    assert session.exec(select(DocumentAttachment)).all() == []
    content = session.exec(select(Chunk).where(Chunk.source == ChunkSource.content)).all()
    assert content and all(c.attachment_id is None for c in content)
    metadata = session.exec(select(Chunk).where(Chunk.source == ChunkSource.metadata)).one()
    assert "Fornitore: ACME" in metadata.content
