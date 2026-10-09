from sqlmodel import select

from app.models import Chunk, ChunkSource, DocStatus, Job, TranslationSegment
from tests.helpers import seed_document


def _doc(session, **kwargs):
    defaults = dict(detected_language="de", translation_status="done", translation_enabled=False)
    defaults.update(kwargs)
    return seed_document(
        session, "Brief",
        [
            {"content": "Seite eins", "page_number": 1},
            {"content": "old overlap translation 1", "page_number": 1, "source": ChunkSource.translation},
            {"content": "old overlap translation 2", "page_number": 1, "source": ChunkSource.translation},
        ],
        **defaults,
    )


def test_retranslate_resets_translation_and_queues_job(auth_client, session):
    doc = _doc(session)
    session.add(TranslationSegment(document_id=doc.id, segment_index=0, page_number=1, source_hash="h", text="t"))
    session.commit()
    resp = auth_client.post(f"/api/documents/{doc.id}/retranslate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["translation_status"] == "pending"
    assert body["translation_enabled"] is True
    assert body["status"] == "ready"
    sources = [c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))]
    assert sources == [ChunkSource.content]  # legacy translation chunks removed, content kept
    assert session.exec(select(TranslationSegment)).all() == []
    job = session.exec(select(Job)).one()
    assert (job.type, job.payload) == ("translate_document", {"document_id": str(doc.id)})


def _code(resp):
    assert resp.status_code == 409
    return resp.json()["error"]["code"]


def test_retranslate_conflicts(auth_client, session):
    busy = _doc(session, status=DocStatus.processing)
    assert _code(auth_client.post(f"/api/documents/{busy.id}/retranslate")) == "document_busy"
    pending = _doc(session, translation_status="pending")
    assert _code(auth_client.post(f"/api/documents/{pending.id}/retranslate")) == "translation_busy"
    unknown = _doc(session, detected_language=None)
    assert _code(auth_client.post(f"/api/documents/{unknown.id}/retranslate")) == "nothing_to_translate"
    italian = _doc(session, detected_language="it")
    assert _code(auth_client.post(f"/api/documents/{italian.id}/retranslate")) == "nothing_to_translate"
    assert session.exec(select(Job)).all() == []


def test_retranslate_then_worker_translates(auth_client, session, llm_stub):
    from app.worker import pipeline

    doc = _doc(session)
    auth_client.post(f"/api/documents/{doc.id}/retranslate")
    pipeline.translate_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.translation_status == "done"
    contents = [
        c.content
        for c in session.exec(
            select(Chunk).where(Chunk.document_id == doc.id, Chunk.source == ChunkSource.translation)
        )
    ]
    assert contents == ["[it] Seite eins"]


def test_retranslate_requires_content_chunks(auth_client, session):
    # an older vision-summarised image: language known, but no content chunks to translate
    doc = seed_document(session, "Foto", [], detected_language="de", doc_type="image")
    assert auth_client.get(f"/api/documents/{doc.id}").json()["translatable"] is False
    listed = auth_client.get("/api/documents").json()
    assert [d["translatable"] for d in listed] == [False]
    assert _code(auth_client.post(f"/api/documents/{doc.id}/retranslate")) == "nothing_to_translate"
    assert session.exec(select(Job)).all() == []


def test_translatable_needs_content_chunks_in_list(auth_client, session):
    with_text = _doc(session)
    seed_document(session, "Foto", [], detected_language="de", doc_type="image")
    flags = {d["id"]: d["translatable"] for d in auth_client.get("/api/documents").json()}
    assert flags[str(with_text.id)] is True
    assert sorted(flags.values()) == [False, True]


def test_retranslate_rejects_failed_document(auth_client, session):
    failed = _doc(session, status=DocStatus.failed)
    assert _code(auth_client.post(f"/api/documents/{failed.id}/retranslate")) == "document_busy"


def test_translate_job_without_content_chunks_clears_pending(session, llm_stub):
    from app.worker import pipeline

    doc = seed_document(
        session, "Foto", [], detected_language="de", doc_type="image", translation_status="pending",
        translation_enabled=True,
    )
    pipeline.translate_document(session, {"document_id": str(doc.id)})
    session.refresh(doc)
    assert doc.translation_status is None
    assert llm_stub["translate"] == []
