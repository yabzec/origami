import pytest
from sqlmodel import select

from app.models import ChunkSource, TranslationSegment
from app.worker import pipeline
from tests.helpers import seed_document
from tests.test_pipeline import chunks_by_source, pipeline_storage  # noqa: F401


def _german(session, pages=3):
    specs = [{"content": f"Seite {n} Text.", "page_number": n} for n in range(1, pages + 1)]
    return seed_document(
        session, "Brief", specs, detected_language="de", translation_status="pending"
    )


def translate(session, doc, **extra):
    pipeline.translate_document(session, {"document_id": str(doc.id), **extra})
    session.refresh(doc)
    return doc


def test_one_call_per_page_and_chunks_per_page(session, pipeline_storage, llm_stub):
    doc = translate(session, _german(session))
    assert doc.translation_status == "done"
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 1 Text.", "Seite 2 Text.", "Seite 3 Text."]
    translated = sorted(chunks_by_source(session, doc)[ChunkSource.translation], key=lambda c: c.chunk_index)
    assert [(c.page_number, c.content) for c in translated] == [
        (1, "[it] Seite 1 Text."), (2, "[it] Seite 2 Text."), (3, "[it] Seite 3 Text."),
    ]
    assert session.exec(select(TranslationSegment)).all() == []  # cleaned up when done


def test_overlap_is_not_translated_twice(session, pipeline_storage, llm_stub):
    first = "A" * 900
    second = first[-200:] + "\n\nB" * 1
    doc = seed_document(
        session, "Brief",
        [{"content": first, "page_number": 1}, {"content": second, "page_number": 1}],
        detected_language="de", translation_status="pending",
    )
    translate(session, doc)
    assert [t for t, _ in llm_stub["translate"]] == [first + "\n\nB"]


def test_failure_keeps_finished_segments_and_retry_resumes(session, pipeline_storage, llm_stub, monkeypatch):
    doc = _german(session)
    real = pipeline.llm_translate
    def flaky(text, target):
        if text.startswith("Seite 3"):
            raise RuntimeError("429")
        return real(text, target)
    monkeypatch.setattr(pipeline, "llm_translate", flaky)
    with pytest.raises(RuntimeError):
        translate(session, doc, _final_attempt=False)
    stored = session.exec(select(TranslationSegment).order_by(TranslationSegment.segment_index)).all()
    assert [s.page_number for s in stored] == [1, 2]

    monkeypatch.setattr(pipeline, "llm_translate", real)
    llm_stub["translate"].clear()
    doc = translate(session, doc)
    assert doc.translation_status == "done"
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 3 Text."]  # pages 1-2 reused


def test_stale_segment_is_translated_again(session, pipeline_storage, llm_stub):
    doc = _german(session, pages=1)
    session.add(TranslationSegment(
        document_id=doc.id, segment_index=0, page_number=1, source_hash="old", text="stale",
    ))
    session.commit()
    doc = translate(session, doc)
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 1 Text."]
    assert chunks_by_source(session, doc)[ChunkSource.translation][0].content == "[it] Seite 1 Text."


def test_segment_size_respects_tpm_cap(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TRANSLATION_SEGMENT_CHARS", "6000")
    monkeypatch.setenv("LLM_TPM_LIMIT", "2000")
    get_settings.cache_clear()
    try:
        assert pipeline.translation_segment_chars() == 2800  # 2000 * 0.4 * 3.5
    finally:
        get_settings.cache_clear()


def test_reprocess_deletes_segments(auth_client, session):
    doc = _german(session, pages=1)
    doc.translation_status = "failed"
    doc.doc_type = "pdf"
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "eng"}).status_code == 200
    assert session.exec(select(TranslationSegment)).all() == []


def test_document_delete_cascades_segments(auth_client, session, storage):
    doc = _german(session, pages=1)
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert session.exec(select(TranslationSegment)).all() == []


def test_long_provider_wait_requeues_and_resumes(session, pipeline_storage, llm_stub, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app.models import Job, JobStatus
    from app.services.llm import TranslationDeferred

    doc = _german(session)
    real = pipeline.llm_translate
    def throttled(text, target):
        if text.startswith("Seite 2"):
            raise TranslationDeferred(42)
        return real(text, target)
    monkeypatch.setattr(pipeline, "llm_translate", throttled)
    before = datetime.now(timezone.utc)
    doc = translate(session, doc, _final_attempt=True)  # returns normally: no failure, no email
    assert doc.translation_status == "pending"
    stored = session.exec(select(TranslationSegment)).all()
    assert [s.page_number for s in stored] == [1]
    job = session.exec(select(Job)).one()
    assert (job.type, job.payload, job.status) == (
        "translate_document", {"document_id": str(doc.id)}, JobStatus.queued,
    )
    run_at = job.run_at if job.run_at.tzinfo else job.run_at.replace(tzinfo=timezone.utc)
    assert before + timedelta(seconds=41) <= run_at <= datetime.now(timezone.utc) + timedelta(seconds=43)

    monkeypatch.setattr(pipeline, "llm_translate", real)
    llm_stub["translate"].clear()
    doc = translate(session, doc)
    assert doc.translation_status == "done"
    assert [t for t, _ in llm_stub["translate"]] == ["Seite 2 Text.", "Seite 3 Text."]


def test_deferral_is_dropped_when_translation_was_reset(session, pipeline_storage, llm_stub, monkeypatch):
    from app.models import Job
    from app.services.llm import TranslationDeferred

    doc = _german(session, pages=1)
    def reset_then_defer(text, target):
        # reprocess resets the status while the worker waits on the provider
        session.execute(
            pipeline.update(pipeline.Document).where(pipeline.Document.id == doc.id).values(translation_status=None)
        )
        session.commit()
        raise TranslationDeferred(42)
    monkeypatch.setattr(pipeline, "llm_translate", reset_then_defer)
    doc = translate(session, doc)
    assert doc.translation_status is None
    assert session.exec(select(Job)).all() == []
