import uuid

import pytest
from sqlmodel import Session, select

from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job
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
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: [[0.2] * 1536 for _ in texts])
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert len(llm_stub["describe"]) == describe_calls_after_first_run  # summary not regenerated


def test_unknown_document_id_is_noop(session, pipeline_storage):
    pipeline.process_document(session, {"document_id": str(uuid.uuid4())})  # must not raise


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


def _text_doc(session, pipeline_storage, body="Erster Absatz.\n\nZweiter Absatz."):
    doc = make_doc(session, doc_type=DocType.text, title="Brief")
    rel, _ = pipeline_storage.store_file(doc.id, ".md", body.encode())
    doc.file_path = rel
    session.commit()
    return doc


def test_italian_document_is_not_translated(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.detected_language == "it"
    assert doc.translation_status is None
    assert llm_stub["translate"] == []
    assert ChunkSource.translation not in chunks_by_source(session, doc)
    assert session.exec(select(Job).where(Job.type == "translate_document")).all() == []


def test_unknown_language_skips_translation(session, pipeline_storage, llm_stub):
    llm_stub["language"] = None  # language detection was not confident
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.status == DocStatus.ready
    assert doc.summary == "Descrizione generata."
    assert doc.detected_language is None
    assert llm_stub["translate"] == []


def test_scan_with_ocr_text_gets_summary_and_language(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    from app.worker.runner import run_once

    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    fake_scanner._labels = iter(["RECHNUNG NUMMER 123 FUER HERRN MUELLER"] * 2)
    llm_stub["language"] = "de"
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "eng"}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "Rechnung"}).json()["id"]

    assert run_once(engine) is True
    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert ChunkSource.content in chunks_by_source(session, doc)
    assert doc.summary == "Descrizione generata."
    assert doc.detected_language == "de"
    assert doc.translation_status == "pending"
    assert run_once(engine) is True  # the queued translate_document job
    session.refresh(doc)
    assert doc.translation_status == "done"
    assert ChunkSource.translation in chunks_by_source(session, doc)


def test_embedding_is_batched(session, pipeline_storage, llm_stub):
    from tests.helpers import seed_document

    doc = seed_document(
        session,
        "Big",
        [{"content": f"chunk {i}"} for i in range(250)],
        status=DocStatus.pending,
        summary="already summarized",
    )
    pipeline._embed_pending_chunks(session, doc)
    assert [len(batch) for batch in llm_stub["embed"]] == [100, 100, 50]
    rows = session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert len(rows) == 250
    assert all(c.embedding is not None for c in rows)


def test_empty_summary_is_not_stored(session, pipeline_storage, llm_stub, monkeypatch):
    monkeypatch.setattr(pipeline, "llm_describe", lambda text=None, image_path=None: "")
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.status == DocStatus.ready
    assert not doc.summary
    assert ChunkSource.summary not in chunks_by_source(session, doc)


def test_summary_fills_empty_description(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage))
    assert doc.description == "Descrizione generata."
    metadata = chunks_by_source(session, doc)[ChunkSource.metadata]
    assert [c.content for c in metadata] == ["Brief"]  # summary has its own chunk; not embedded twice


def test_summary_fills_whitespace_description(session, pipeline_storage, llm_stub):
    doc = _text_doc(session, pipeline_storage)
    doc.description = "   "
    session.commit()
    doc = run(session, doc)
    assert doc.description == "Descrizione generata."


def test_summary_keeps_user_description(session, pipeline_storage, llm_stub):
    doc = _text_doc(session, pipeline_storage)
    doc.description = "Lettera del notaio"
    session.commit()
    doc = run(session, doc)
    assert doc.summary == "Descrizione generata."
    assert doc.description == "Lettera del notaio"
    metadata = chunks_by_source(session, doc)[ChunkSource.metadata]
    assert [c.content for c in metadata] == ["Brief\n\nLettera del notaio"]


def test_summary_does_not_overwrite_description_edited_during_llm_call(
    session, engine, pipeline_storage, llm_stub, monkeypatch
):
    from sqlmodel import Session

    doc = _text_doc(session, pipeline_storage)

    def describe_while_user_edits(text=None, image_path=None):
        with Session(engine) as other:
            other_doc = other.get(Document, doc.id)
            other_doc.description = "Scritta dall'utente"
            other.commit()
        return "Descrizione generata."

    monkeypatch.setattr(pipeline, "llm_describe", describe_while_user_edits)
    doc = run(session, doc)
    assert doc.description == "Scritta dall'utente"
    assert doc.summary == "Descrizione generata."


def _office_doc(session, pipeline_storage, tmp_path, ext):
    from tests.helpers import make_docx, make_odt

    if ext == ".docx":
        src = make_docx(tmp_path / "src.docx", ["CONTRATTO DI LOCAZIONE", "Seconda pagina"])
    else:
        src = make_odt(tmp_path / "src.odt", "VERBALE ASSEMBLEA")
    doc = make_doc(session, doc_type=DocType.text, title="Contratto", original_filename=f"contratto{ext}")
    rel, _ = pipeline_storage.store_file(doc.id, ext, src.read_bytes())
    doc.file_path = rel
    session.commit()
    return doc


def test_docx_gets_pdf_preview_and_page_numbers(session, pipeline_storage, llm_stub, tmp_path):
    doc = _office_doc(session, pipeline_storage, tmp_path, ".docx")
    original = doc.file_path
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    assert doc.file_path == original  # download keeps the .docx
    assert doc.preview_path == f"files/{doc.id}.preview.pdf"
    assert pipeline_storage.abs_path(doc.preview_path).read_bytes().startswith(b"%PDF")
    assert doc.page_count == 2
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert {c.page_number for c in content} == {1, 2}
    assert "CONTRATTO" in " ".join(c.content for c in content)


def test_docx_conversion_failure_falls_back_to_text(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    break_soffice()
    doc = run(session, _office_doc(session, pipeline_storage, tmp_path, ".docx"))
    assert doc.status == DocStatus.ready
    assert doc.preview_path is None
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert [c.page_number for c in content] == [None]
    assert "CONTRATTO" in content[0].content


def test_odt_conversion_failure_fails_document(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    from app.services.convert import ConversionError

    break_soffice()
    doc = _office_doc(session, pipeline_storage, tmp_path, ".odt")
    with pytest.raises(ConversionError):
        pipeline.process_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert "Office conversion failed" in doc.error_message
    assert doc.preview_path is None


def test_reprocess_reuses_existing_preview(session, pipeline_storage, llm_stub, tmp_path, break_soffice):
    doc = run(session, _office_doc(session, pipeline_storage, tmp_path, ".odt"))
    assert doc.preview_path is not None
    for chunk in session.exec(select(Chunk).where(Chunk.document_id == doc.id)).all():
        session.delete(chunk)
    doc.summary = None
    session.commit()

    break_soffice()  # a second conversion would now fail the .odt
    doc = run(session, doc)
    assert doc.status == DocStatus.ready
    content = chunks_by_source(session, doc)[ChunkSource.content]
    assert [c.page_number for c in content] == [1]
    assert "VERBALE" in content[0].content


def _failing_text_doc(session, pipeline_storage, monkeypatch):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, _ = pipeline_storage.store_file(doc.id, ".txt", b"Testo di prova.")
    doc.file_path = rel
    session.commit()
    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    return doc


def test_non_final_failure_leaves_document_pending_with_retry_message(
    session, pipeline_storage, llm_stub, monkeypatch
):
    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    with pytest.raises(RuntimeError):
        pipeline.process_document(
            session, {"document_id": str(doc.id), "_attempt": 1, "_final_attempt": False}
        )
    session.refresh(doc)
    assert doc.status == DocStatus.pending
    assert doc.error_message == "Retrying: embedding API down"


def test_final_failure_marks_document_failed(session, pipeline_storage, llm_stub, monkeypatch):
    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    with pytest.raises(RuntimeError):
        pipeline.process_document(
            session, {"document_id": str(doc.id), "_attempt": 5, "_final_attempt": True}
        )
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert doc.error_message == "embedding API down"


def test_failed_attempt_via_runner_requeues_and_keeps_document_pending(
    engine, session, pipeline_storage, llm_stub, monkeypatch
):
    from app.models import Job, JobStatus
    from app.services.jobs import enqueue
    from app.worker.runner import run_once

    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    enqueue(session, "process_document", {"document_id": str(doc.id)})
    assert run_once(engine) is True
    session.expire_all()
    assert session.get(Document, doc.id).status == DocStatus.pending
    job = session.exec(select(Job)).one()
    assert (job.status, job.attempts) == (JobStatus.queued, 1)
    assert job.payload == {"document_id": str(doc.id)}


def _german_doc(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    return run(session, _text_doc(session, pipeline_storage))


def translate(session, doc, **extra):
    pipeline.translate_document(session, {"document_id": str(doc.id), **extra})
    session.refresh(doc)
    return doc


def test_german_document_queues_translation_job(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    assert doc.status == DocStatus.ready
    assert doc.detected_language == "de"
    assert doc.translation_status == "pending"
    jobs = session.exec(select(Job).where(Job.type == "translate_document")).all()
    assert [j.payload for j in jobs] == [{"document_id": str(doc.id)}]
    assert llm_stub["translate"] == []
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translate_document_adds_embedded_translation_chunks(session, pipeline_storage, llm_stub):
    doc = translate(session, _german_doc(session, pipeline_storage, llm_stub))
    assert doc.translation_status == "done"
    by_source = chunks_by_source(session, doc)
    content = sorted(by_source[ChunkSource.content], key=lambda c: c.chunk_index)
    translated = sorted(by_source[ChunkSource.translation], key=lambda c: c.chunk_index)
    assert len(translated) == len(content)
    assert [t.page_number for t in translated] == [c.page_number for c in content]
    assert translated[0].content.startswith("[it] ")
    assert all(t.embedding is not None for t in translated)


def test_translate_noop_clears_pending_status(session, pipeline_storage):
    doc = make_doc(session, detected_language="it", translation_status="pending")
    doc = translate(session, doc)
    assert doc.translation_status is None


def test_translation_error_before_final_attempt_stays_pending(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    llm_stub["translate_error"] = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        pipeline.translate_document(
            session, {"document_id": str(doc.id), "_attempt": 1, "_final_attempt": False}
        )
    session.refresh(doc)
    assert doc.translation_status == "pending"
    assert doc.status == DocStatus.ready
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translation_error_on_final_attempt_marks_failed(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    llm_stub["translate_error"] = RuntimeError("provider down")
    with pytest.raises(RuntimeError):
        pipeline.translate_document(
            session, {"document_id": str(doc.id), "_attempt": 5, "_final_attempt": True}
        )
    session.refresh(doc)
    assert doc.translation_status == "failed"
    assert doc.status == DocStatus.ready
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translate_document_already_done_is_noop(session, pipeline_storage, llm_stub):
    doc = translate(session, _german_doc(session, pipeline_storage, llm_stub))
    calls = len(llm_stub["translate"])
    count = len(chunks_by_source(session, doc)[ChunkSource.translation])
    doc = translate(session, doc)
    assert len(llm_stub["translate"]) == calls
    assert len(chunks_by_source(session, doc)[ChunkSource.translation]) == count


def test_translate_document_missing_document_is_noop(session, llm_stub):
    pipeline.translate_document(session, {"document_id": str(uuid.uuid4())})
    assert llm_stub["translate"] == []


def test_translate_document_retry_embeds_without_retranslating(
    session, pipeline_storage, llm_stub, monkeypatch
):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    working_embed = pipeline.llm_embed  # the llm_stub fake
    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    with pytest.raises(RuntimeError):
        pipeline.translate_document(session, {"document_id": str(doc.id), "_final_attempt": False})
    calls = len(llm_stub["translate"])

    monkeypatch.setattr(pipeline, "llm_embed", working_embed)
    doc = translate(session, doc)
    assert doc.translation_status == "done"
    assert len(llm_stub["translate"]) == calls  # chunks from the first try are reused
    by_source = chunks_by_source(session, doc)
    assert len(by_source[ChunkSource.translation]) == len(by_source[ChunkSource.content])
    assert all(t.embedding is not None for t in by_source[ChunkSource.translation])


def test_translation_discarded_when_document_reprocessed_meanwhile(
    session, engine, pipeline_storage, llm_stub, monkeypatch
):
    doc = _german_doc(session, pipeline_storage, llm_stub)

    def translate_during_reprocess(text, target):
        with Session(engine) as other:  # what reprocess_document commits meanwhile
            fresh = other.get(Document, doc.id)
            fresh.translation_status = None
            other.commit()
        return f"[{target}] {text}"

    monkeypatch.setattr(pipeline, "llm_translate", translate_during_reprocess)
    doc = translate(session, doc)
    assert doc.translation_status is None
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def _reprocess_reset(engine, doc_id):
    with Session(engine) as other:  # what reprocess_document commits
        for c in other.exec(select(Chunk).where(Chunk.document_id == doc_id)).all():
            other.delete(c)
        fresh = other.get(Document, doc_id)
        fresh.translation_status = None
        fresh.detected_language = None
        other.commit()


def test_translation_discarded_when_reprocessed_during_embedding(
    session, engine, pipeline_storage, llm_stub, monkeypatch
):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    real_embed = pipeline.llm_embed

    def embed_during_reprocess(texts):
        _reprocess_reset(engine, doc.id)
        return real_embed(texts)

    monkeypatch.setattr(pipeline, "llm_embed", embed_during_reprocess)
    # final attempt: a superseded run must still neither raise nor mark failed
    doc = translate(session, doc, _attempt=5, _final_attempt=True)
    assert doc.translation_status is None
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translation_insert_holds_document_row_lock(
    session, engine, pipeline_storage, llm_stub, monkeypatch
):
    """reprocess_document locks the same row before deleting chunks, so it cannot slip in
    between the re-check and the insert."""
    from sqlalchemy.exc import OperationalError

    doc = _german_doc(session, pipeline_storage, llm_stub)
    real = pipeline._next_chunk_index
    seen = {}

    def next_index_probing_lock(s, d):
        with Session(engine) as other:
            try:
                other.exec(
                    select(Document).where(Document.id == doc.id).with_for_update(nowait=True)
                ).one()
                seen["locked"] = False
            except OperationalError:
                seen["locked"] = True
        return real(s, d)

    monkeypatch.setattr(pipeline, "_next_chunk_index", next_index_probing_lock)
    doc = translate(session, doc)
    assert seen["locked"] is True
    assert doc.translation_status == "done"
