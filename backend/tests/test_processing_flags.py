from sqlmodel import select

from app.models import ChunkSource, DocType, Job
from tests.test_pipeline import chunks_by_source, make_doc, pipeline_storage, run  # noqa: F401


def _text_doc(session, storage, **kwargs):
    doc = make_doc(session, doc_type=DocType.text, title="Brief", **kwargs)
    rel, _ = storage.store_file(doc.id, ".md", b"Erster Absatz.\n\nZweiter Absatz.")
    doc.file_path = rel
    session.commit()
    return doc


def test_summary_disabled_skips_summary(session, pipeline_storage, llm_stub):
    doc = run(session, _text_doc(session, pipeline_storage, summary_enabled=False))
    assert llm_stub["describe"] == []
    assert doc.summary is None
    by_source = chunks_by_source(session, doc)
    assert ChunkSource.summary not in by_source
    assert [c.content for c in by_source[ChunkSource.metadata]] == ["Brief"]


def test_translation_disabled_schedules_nothing(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    doc = run(session, _text_doc(session, pipeline_storage, translation_enabled=False))
    assert doc.detected_language == "de"
    assert doc.translation_status is None
    assert session.exec(select(Job).where(Job.type == "translate_document")).all() == []


def test_summary_disabled_still_detects_language_and_translates(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    doc = run(session, _text_doc(session, pipeline_storage, summary_enabled=False))
    assert doc.detected_language == "de"
    assert doc.translation_status == "pending"
    assert len(session.exec(select(Job).where(Job.type == "translate_document")).all()) == 1


def test_upload_stores_flags(auth_client, storage):
    body = auth_client.post(
        "/api/documents/upload",
        files={"file": ("a.md", b"x", "text/markdown")},
        data={"summary_enabled": "false", "translation_enabled": "false"},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, False)


def test_upload_flags_default_true(auth_client, storage):
    body = auth_client.post(
        "/api/documents/upload", files={"file": ("a.md", b"x", "text/markdown")}
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (True, True)


def test_compile_stores_flags(auth_client, fake_scanner, storage):
    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    body = auth_client.post(
        f"/api/scan/sessions/{sid}/compile",
        json={"title": "T", "summary_enabled": False, "translation_enabled": True},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, True)


def test_reprocess_stores_flags(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Doc", [{"content": "x", "page_number": 1}], doc_type="pdf")
    body = auth_client.post(
        f"/api/documents/{doc.id}/reprocess",
        json={"ocr_languages": "eng", "summary_enabled": False, "translation_enabled": False},
    ).json()
    assert (body["summary_enabled"], body["translation_enabled"]) == (False, False)
