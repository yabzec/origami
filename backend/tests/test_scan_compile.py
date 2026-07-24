from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.models import (
    Chunk,
    ChunkSource,
    DocStatus,
    DocType,
    Document,
    Job,
    JobStatus,
    ScanSession,
    ScanSessionStatus,
)
from app.worker import pipeline
from app.worker.runner import run_once


def scanned_session(auth_client, pages=2):
    sid = auth_client.post("/api/scan/sessions", json={"ocr_languages": "ita+eng"}).json()["id"]
    for _ in range(pages):
        auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    return sid


def test_compile_creates_document_and_job(auth_client, fake_scanner, storage, session):
    sid = scanned_session(auth_client)
    resp = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "Bolletta"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["doc_type"] == DocType.scan
    assert body["status"] == DocStatus.pending
    assert body["ocr_languages"] == "ita+eng"

    assert session.get(ScanSession, sid).status == ScanSessionStatus.compiling
    job = session.exec(select(Job)).one()
    assert job.payload == {"document_id": body["id"], "scan_session_id": sid}


def test_compile_empty_session_422(auth_client, fake_scanner, storage):
    sid = auth_client.post("/api/scan/sessions", json={}).json()["id"]
    resp = auth_client.post(f"/api/scan/sessions/{sid}/compile", json={"title": "X"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_pages"


def test_worker_compiles_scan_end_to_end(
    auth_client, fake_scanner, storage, session, engine, llm_stub, monkeypatch
):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    fake_scanner._labels = iter(["PAGINA UNO", "PAGINA DUE"])  # deterministic page text
    sid = scanned_session(auth_client, pages=2)
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "Documento"}
    ).json()["id"]

    assert run_once(engine) is True

    doc = session.get(Document, doc_id)
    session.refresh(doc)
    assert doc.status == DocStatus.ready
    assert doc.page_count == 2
    assert doc.file_path == f"files/{doc.id}.pdf"
    assert storage.abs_path(doc.file_path).exists()
    assert doc.summary is None  # scans get no LLM summary
    session.expire_all()
    assert session.get(ScanSession, sid).status == ScanSessionStatus.done
    assert not (storage.tmp_scans_dir / str(sid)).exists()
    sources = {c.source for c in session.exec(select(Chunk).where(Chunk.document_id == doc.id))}
    assert sources == {ChunkSource.content, ChunkSource.metadata}


def test_sweep_purges_old_sessions(session, storage, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    old = ScanSession(
        status=ScanSessionStatus.active,
        created_at=datetime.now(timezone.utc) - timedelta(hours=30),
    )
    done = ScanSession(status=ScanSessionStatus.done)
    session.add(old)
    session.add(done)
    session.commit()
    storage.scan_session_dir(old.id)

    pipeline.sweep_scan_sessions(session, {})

    assert session.get(ScanSession, old.id) is None
    assert session.get(ScanSession, done.id) is None
    assert not (storage.tmp_scans_dir / str(old.id)).exists()
    # sweep re-scheduled itself
    from app.models import Job as JobModel

    jobs = session.exec(select(JobModel).where(JobModel.type == "sweep_scan_sessions")).all()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.queued


def test_sweep_skips_compiling_session_with_in_flight_job(session, storage, engine, monkeypatch):
    monkeypatch.setattr(pipeline, "get_pipeline_storage", lambda: storage)
    compiling = ScanSession(
        status=ScanSessionStatus.compiling,
        created_at=datetime.now(timezone.utc) - timedelta(hours=30),
    )
    session.add(compiling)
    session.commit()
    storage.scan_session_dir(compiling.id)

    job = Job(
        type="process_document",
        payload={"document_id": "doc-in-flight", "scan_session_id": compiling.id},
        status=JobStatus.queued,
    )
    session.add(job)
    session.commit()

    pipeline.sweep_scan_sessions(session, {})

    assert session.get(ScanSession, compiling.id) is not None
    assert (storage.tmp_scans_dir / str(compiling.id)).exists()


def test_ensure_sweep_scheduled_is_idempotent(session, engine):
    pipeline.ensure_sweep_scheduled(engine)
    pipeline.ensure_sweep_scheduled(engine)
    jobs = session.exec(select(Job).where(Job.type == "sweep_scan_sessions")).all()
    assert len(jobs) == 1


def test_session_and_compiled_doc_carry_ocr_enabled(auth_client, fake_scanner, storage, session):
    from app.models import Document, ScanSession

    sid = auth_client.post("/api/scan/sessions", json={"ocr_enabled": False}).json()["id"]
    assert session.get(ScanSession, sid).ocr_enabled is False
    auth_client.post(f"/api/scan/sessions/{sid}/pages", json={})
    doc_id = auth_client.post(
        f"/api/scan/sessions/{sid}/compile", json={"title": "X"}
    ).json()["id"]
    assert session.get(Document, doc_id).ocr_enabled is False
