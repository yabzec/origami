import uuid

import pytest
from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document
from app.services.storage import Storage
from app.worker import pipeline
from tests.helpers import make_text_image


@pytest.fixture
def pipeline_storage(tmp_path, monkeypatch):
    s = Storage(tmp_path)
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: s)
    return s


def make_doc(session, **kwargs):
    doc = Document(
        title=kwargs.pop("title", "Doc"),
        doc_type=kwargs.pop("doc_type", DocType.text),
        ocr_languages=kwargs.pop("ocr_languages", "ita+eng"),
        **kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def run(session, doc):
    pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    return doc


def chunks_by_source(session, doc):
    rows = session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()
    out = {}
    for c in rows:
        out.setdefault(c.source, []).append(c)
    return out


def test_text_document_full_pipeline(session, pipeline_storage, llm_stub):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, size = pipeline_storage.store_file(doc.id, ".md", "Contenuto importante.\n\nAltro testo.".encode())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.summary == "Descrizione generata."
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.content in by_source
    assert ChunkSource.summary in by_source
    assert ChunkSource.metadata in by_source
    all_chunks = [c for group in by_source.values() for c in group]
    assert all(c.embedding is not None for c in all_chunks)
    assert len(llm_stub["embed"]) == 1  # one batched call


def test_image_document_ocr_and_vision(session, pipeline_storage, llm_stub, tmp_path):
    img = make_text_image(tmp_path / "src.png", "SCONTRINO 12")
    doc = make_doc(session, doc_type=DocType.image, title="Scontrino")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    # vision describe was called with the image path
    assert llm_stub["describe"][0]["image_path"] is not None
    # companion searchable PDF exists alongside the original
    assert pipeline_storage.abs_path(f"files/{doc.id}.pdf").exists()
    content = " ".join(c.content for c in chunks_by_source(session, doc)[ChunkSource.content])
    assert "SCONTRINO" in content.upper()


def test_pdf_without_text_layer_gets_ocr(session, pipeline_storage, llm_stub, tmp_path):
    # a PDF with no text layer: image-only via PIL save
    from PIL import Image

    img_path = make_text_image(tmp_path / "p.png", "PREVENTIVO 77")
    pdf_path = tmp_path / "raw.pdf"
    Image.open(img_path).save(pdf_path, "PDF")

    doc = make_doc(session, doc_type=DocType.pdf, title="Preventivo")
    rel, _ = pipeline_storage.store_file(doc.id, ".pdf", pdf_path.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 1
    content = " ".join(c.content for c in chunks_by_source(session, doc)[ChunkSource.content])
    assert "PREVENTIVO" in content.upper()


def test_video_document_metadata_only(session, pipeline_storage, llm_stub):
    doc = make_doc(session, doc_type=DocType.video, title="Video vacanze", description="Mare 2026")
    rel, _ = pipeline_storage.store_file(doc.id, ".mp4", b"fake video")
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.summary is None
    assert llm_stub["describe"] == []
    by_source = chunks_by_source(session, doc)
    assert list(by_source) == [ChunkSource.metadata]
    assert "Video vacanze" in by_source[ChunkSource.metadata][0].content


def test_pipeline_resumes_after_embedding_failure(session, pipeline_storage, llm_stub, monkeypatch):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, _ = pipeline_storage.store_file(doc.id, ".txt", b"Testo di prova.")
    doc.file_path = rel
    session.commit()

    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    with pytest.raises(RuntimeError):
        pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "embedding API down" in doc.error_message
    describe_calls_after_first_run = len(llm_stub["describe"])

    # retry with embedding working again: must NOT redo extraction/summary
    from app.models.chunk import EMBEDDING_DIM

    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: [[0.2] * EMBEDDING_DIM for _ in texts])
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert len(llm_stub["describe"]) == describe_calls_after_first_run  # summary not regenerated


def test_unknown_document_id_raises(session, pipeline_storage):
    with pytest.raises(ValueError):
        pipeline.process_document(session, {"document_id": str(uuid.uuid4())})


def test_no_ocr_image_skips_ocr_and_has_no_content(session, pipeline_storage, llm_stub, tmp_path):
    from PIL import Image as PILImage

    from app.models import ChunkSource, DocStatus, DocType
    from tests.helpers import make_text_image

    img = make_text_image(tmp_path / "src.png", "SCONTRINO 12")
    doc = make_doc(session, doc_type=DocType.image, title="Foto", ocr_enabled=False)
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.content not in by_source        # no OCR text
    assert not pipeline_storage.abs_path(f"files/{doc.id}.pdf").exists()  # no companion pdf
    assert llm_stub["describe"][0]["image_path"] is not None  # vision summary (photo path)


def test_no_ocr_pdf_uses_native_text_only(session, pipeline_storage, llm_stub, tmp_path):
    # image-only PDF (no text layer): with OCR it would be OCR'd; no-OCR must NOT OCR it.
    from PIL import Image as PILImage

    from app.models import ChunkSource, DocStatus, DocType
    from tests.helpers import make_text_image

    img = make_text_image(tmp_path / "p.png", "PREVENTIVO 77")
    raw_pdf = tmp_path / "raw.pdf"
    PILImage.open(img).convert("RGB").save(raw_pdf, "PDF")
    doc = make_doc(session, doc_type=DocType.pdf, title="Prev", ocr_enabled=False)
    rel, _ = pipeline_storage.store_file(doc.id, ".pdf", raw_pdf.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    # native extraction of an image-only pdf yields no usable text → no content chunks
    assert ChunkSource.content not in chunks_by_source(session, doc)


def test_no_ocr_scan_builds_image_only_pdf(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    from app.models import ChunkSource, DocStatus, Document
    from app.worker import pipeline
    from app.worker.runner import run_once

    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    sid = auth_client.post(
        "/api/scan/sessions", json={"ocr_languages": "ita+eng", "ocr_enabled": False}
    ).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "ScanNoOcr"}
    ).json()["id"]

    assert run_once(engine) is True
    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert storage.abs_path(doc.file_path).exists()
    assert ChunkSource.content not in chunks_by_source(session, doc)


def test_image_summary_uses_text_when_ocr_text_present(session, pipeline_storage, llm_stub, tmp_path):
    # rendered image whose OCR yields well over 40 chars (use large size to fit full text)
    img = make_text_image(
        tmp_path / "rich.png",
        "FATTURA NUMERO 12345 DEL 2026 IMPORTO 42 EURO CLIENTE ACME SRL",
        size=(2400, 400),
    )
    doc = make_doc(session, doc_type=DocType.image, title="Fattura")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", img.read_bytes())
    doc.file_path = rel
    session.commit()

    doc = run(session, doc)
    assert doc.summary is not None
    # text path used: describe called with text=, image_path None
    assert llm_stub["describe"][0]["text"] is not None
    assert llm_stub["describe"][0]["image_path"] is None


def test_image_summary_falls_back_to_vision_when_little_text(session, pipeline_storage, llm_stub, tmp_path):
    # image doc with a tiny content chunk (< 40 chars), simulating a near-textless photo
    doc = make_doc(session, doc_type=DocType.image, title="Foto")
    rel, _ = pipeline_storage.store_file(doc.id, ".png", b"\x89PNG fake")
    doc.file_path = rel
    session.add(
        Chunk(document_id=doc.id, chunk_index=0, page_number=1, source=ChunkSource.content, content="ciao")
    )
    session.commit()

    from app.worker import pipeline

    pipeline._ensure_summary(session, doc, pipeline_storage)
    assert llm_stub["describe"][-1]["image_path"] is not None  # vision fallback


def test_chunk_embedding_dimension_matches_the_local_model():
    from app.models.chunk import EMBEDDING_DIM
    from app.models import Chunk

    # bge-m3 dense output. The DB column and the model constant must never drift.
    assert EMBEDDING_DIM == 1024
    assert Chunk.__table__.c.embedding.type.dim == 1024
