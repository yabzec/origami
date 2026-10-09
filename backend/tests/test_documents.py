import uuid
from datetime import datetime, timezone

from app.models import Document, DocStatus, DocType, DocumentTag, Job, JobStatus, Tag


def make_document(session, **kwargs):
    doc = Document(
        title=kwargs.pop("title", "Doc"),
        doc_type=kwargs.pop("doc_type", DocType.pdf),
        status=kwargs.pop("status", DocStatus.ready),
        **kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_list_and_filters(auth_client, session):
    d1 = make_document(session, title="Bolletta")
    make_document(session, title="Video", doc_type=DocType.video)

    all_docs = auth_client.get("/api/documents").json()
    assert len(all_docs) == 2

    only_pdf = auth_client.get("/api/documents", params={"doc_type": "pdf"}).json()
    assert [d["id"] for d in only_pdf] == [str(d1.id)]


def test_get_includes_tags(auth_client, session):
    doc = make_document(session)
    tag = Tag(name="casa")
    session.add(tag)
    session.commit()
    session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert [t["name"] for t in body["tags"]] == ["casa"]


def test_patch_replaces_tags_and_moves_folder(auth_client, session):
    doc = make_document(session)
    t1, t2 = Tag(name="a"), Tag(name="b")
    session.add(t1); session.add(t2); session.commit()
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]

    body = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"title": "New", "folder_id": folder_id, "tag_ids": [t2.id]},
    ).json()
    assert body["title"] == "New"
    assert body["folder_id"] == folder_id
    assert [t["id"] for t in body["tags"]] == [t2.id]


def test_patch_replaces_existing_tag_set(auth_client, session):
    doc = make_document(session)
    t1, t2 = Tag(name="a"), Tag(name="b")
    session.add(t1); session.add(t2); session.commit()
    session.add(DocumentTag(document_id=doc.id, tag_id=t1.id))
    session.commit()

    body = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"tag_ids": [t2.id]},
    ).json()
    assert [t["id"] for t in body["tags"]] == [t2.id]


def test_patch_missing_folder_id_404(auth_client, session):
    doc = make_document(session)
    resp = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"folder_id": 999999},
    )
    assert resp.status_code == 404


def test_patch_missing_tag_id_404(auth_client, session):
    doc = make_document(session)
    resp = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"tag_ids": [999999]},
    )
    assert resp.status_code == 404


def test_delete_removes_file(auth_client, session, storage):
    doc = make_document(session)
    rel, _ = storage.write_file(f"{doc.id}.pdf", b"%PDF")
    doc.file_path = rel
    session.commit()

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not storage.abs_path(rel).exists()
    assert auth_client.get(f"/api/documents/{doc.id}").status_code == 404


def test_get_missing_404(auth_client):
    resp = auth_client.get(f"/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_document_text_endpoint(auth_client, session):
    from app.models import Chunk, ChunkSource
    from tests.helpers import seed_document

    doc = seed_document(
        session, "Testo",
        [
            {"content": "Pagina uno.", "page_number": 1},
            {"content": "Pagina due.", "page_number": 2},
            {"content": "Riassunto.", "source": ChunkSource.summary},
        ],
    )
    doc.summary = "Riassunto."
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}/text").json()
    assert body["summary"] == "Riassunto."
    assert [c["content"] for c in body["chunks"]] == ["Pagina uno.", "Pagina due."]
    assert body["chunks"][0]["page_number"] == 1


def test_new_document_defaults_and_serialization(auth_client, session):
    from app.models.user import utcnow
    from tests.helpers import seed_document

    doc = seed_document(session, "Fresh", [])
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["document_date"] == utcnow().date().isoformat()
    assert body["detected_language"] is None
    assert body["translation_status"] is None
    assert body["ocr_applied"] is None


def test_patch_document_date(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Dated", [])
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"


def test_patch_null_document_date_is_ignored(auth_client, session):
    from tests.helpers import seed_document

    doc = seed_document(session, "Dated", [])
    auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": "2019-03-04"})
    resp = auth_client.patch(f"/api/documents/{doc.id}", json={"document_date": None, "title": "T2"})
    assert resp.status_code == 200
    assert resp.json()["document_date"] == "2019-03-04"
    assert resp.json()["title"] == "T2"


def test_document_text_translation_variant(auth_client, session):
    from app.models import ChunkSource
    from tests.helpers import seed_document

    doc = seed_document(
        session,
        "Brief",
        [
            {"content": "Hallo Welt", "page_number": 1},
            {"content": "Ciao mondo", "page_number": 1, "source": ChunkSource.translation},
        ],
        detected_language="de",
        translation_status="done",
    )
    original = auth_client.get(f"/api/documents/{doc.id}/text").json()
    assert [c["content"] for c in original["chunks"]] == ["Hallo Welt"]
    assert original["variant"] == "content"
    assert original["detected_language"] == "de"
    assert original["translation_status"] == "done"
    assert original["translation_language"] == "it"

    translated = auth_client.get(f"/api/documents/{doc.id}/text?variant=translation").json()
    assert [c["content"] for c in translated["chunks"]] == ["Ciao mondo"]
    assert auth_client.get(f"/api/documents/{doc.id}/text?variant=bogus").status_code == 422


def test_delete_removes_preview(auth_client, session, storage):
    doc = make_document(session, doc_type=DocType.text)
    rel, _ = storage.write_file(f"{doc.id}.docx", b"PK")
    doc.file_path = rel
    doc.preview_path = storage.write_derived(f"{doc.id}.preview.pdf", b"%PDF")
    session.commit()
    original, preview = storage.abs_path(rel), storage.derived_abs(doc.preview_path)

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not original.exists()
    assert not preview.exists()


def _sort_fixture(session):
    from datetime import date, datetime, timezone

    utc = timezone.utc
    a = make_document(session, title="Beta", document_date=date(2026, 1, 10), created_at=datetime(2026, 3, 1, tzinfo=utc))
    b = make_document(session, title="alpha", document_date=date(2026, 2, 1), created_at=datetime(2026, 1, 1, tzinfo=utc))
    c = make_document(session, title="gamma", document_date=date(2026, 2, 1), created_at=datetime(2026, 2, 1, tzinfo=utc))
    return a, b, c


def _titles(auth_client, **params):
    resp = auth_client.get("/api/documents", params=params)
    assert resp.status_code == 200
    return [d["title"] for d in resp.json()]


def test_list_sort_orders(auth_client, session):
    _sort_fixture(session)
    # date ties (alpha/gamma share 2026-02-01) break on created_at
    assert _titles(auth_client) == ["gamma", "alpha", "Beta"]  # default date_desc
    assert _titles(auth_client, sort="date_desc") == ["gamma", "alpha", "Beta"]
    assert _titles(auth_client, sort="date_asc") == ["Beta", "alpha", "gamma"]
    assert _titles(auth_client, sort="added_desc") == ["Beta", "gamma", "alpha"]
    assert _titles(auth_client, sort="title_asc") == ["alpha", "Beta", "gamma"]  # case-insensitive


def test_list_bad_sort_is_422(auth_client, session):
    assert auth_client.get("/api/documents", params={"sort": "size"}).status_code == 422


def test_active_job_reports_queued_retry(auth_client, session):
    doc = make_document(session, status=DocStatus.pending)
    session.add(Job(type="process_document", payload={"document_id": str(doc.id)}, status=JobStatus.done))
    session.add(
        Job(
            type="process_document",
            payload={"document_id": str(doc.id)},
            attempts=2,
            last_error="Traceback...\nRuntimeError: down",
            run_at=datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()
    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert body["active_job"] == {
        "type": "process_document",
        "attempts": 2,
        "max_attempts": 5,
        "run_at": "2026-10-03T10:00:00+00:00",
        "last_error": "Traceback...\nRuntimeError: down",
    }


def test_active_job_is_null_without_open_job(auth_client, session):
    doc = make_document(session)
    other = make_document(session, title="Other")
    session.add(Job(type="translate_document", payload={"document_id": str(doc.id)}, status=JobStatus.failed))
    session.add(Job(type="translate_document", payload={"document_id": str(doc.id)}, status=JobStatus.cancelled))
    session.add(Job(type="translate_document", payload={"document_id": str(other.id)}))
    session.commit()
    assert auth_client.get(f"/api/documents/{doc.id}").json()["active_job"] is None


def test_active_job_prefers_newest_open_job(auth_client, session):
    doc = make_document(session)
    session.add(Job(type="process_document", payload={"document_id": str(doc.id)}, status=JobStatus.running))
    session.add(Job(type="translate_document", payload={"document_id": str(doc.id)}))
    session.commit()
    assert auth_client.get(f"/api/documents/{doc.id}").json()["active_job"]["type"] == "translate_document"


def test_list_fetches_active_jobs_in_one_query(auth_client, session, engine):
    from sqlalchemy import event

    docs = [make_document(session, title=f"D{i}") for i in range(3)]
    for d in docs:
        session.add(Job(type="translate_document", payload={"document_id": str(d.id)}, attempts=1))
    session.commit()

    job_queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if "FROM jobs" in statement:
            job_queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        body = auth_client.get("/api/documents").json()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(job_queries) == 1
    assert [d["active_job"]["attempts"] for d in body] == [1, 1, 1]


def test_delete_cancels_queued_retry_and_late_run_sends_no_email(
    auth_client, session, engine, smtp_settings, smtp_stub
):
    from app.models import User
    from app.worker import runner
    import app.worker.pipeline  # noqa: F401  (registers the handler)

    smtp_settings(smtp_user="origami@gmail.com", smtp_app_password="abcd efgh ijkl mnop")
    session.add(User(username="mail-user", password_hash="x", email="me@example.com"))
    doc = make_document(session)
    job = Job(
        type="process_document",
        payload={"document_id": str(doc.id)},
        attempts=1,
        run_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    session.refresh(job)
    assert job.status == JobStatus.cancelled

    job.status = JobStatus.queued  # a worker that had already claimed it would still run it
    job.run_at = datetime.now(timezone.utc)
    session.commit()
    assert runner.run_once(engine) is True
    session.refresh(job)
    assert job.status == JobStatus.done
    assert smtp_stub == []


def test_delete_removes_ocr_companion(auth_client, session, storage):
    from app.services.storage import companion_name

    doc = make_document(session, doc_type=DocType.image)
    rel, _ = storage.write_file(f"{doc.id}.png", b"png")
    doc.file_path = rel
    session.commit()
    companion = storage.derived_abs(companion_name(doc.id))
    storage.write_derived(companion_name(doc.id), b"%PDF")
    assert companion.exists()

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not companion.exists()
