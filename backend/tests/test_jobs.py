from datetime import datetime, timedelta, timezone

from app.models import Job, JobStatus
from app.services.jobs import claim_next, complete, enqueue, fail


def test_enqueue_and_claim(session):
    enqueue(session, "process_document", {"document_id": "x"})
    job = claim_next(session)
    assert job is not None
    assert job.status == JobStatus.running
    assert job.payload == {"document_id": "x"}
    assert claim_next(session) is None  # nothing else queued


def test_claim_respects_run_at(session):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    enqueue(session, "process_document", {}, run_at=future)
    assert claim_next(session) is None


def test_complete(session):
    enqueue(session, "process_document", {})
    job = claim_next(session)
    complete(session, job)
    assert session.get(Job, job.id).status == JobStatus.done


def test_fail_requeues_with_backoff_then_fails(session):
    enqueue(session, "process_document", {})
    job = claim_next(session)

    fail(session, job, "boom")
    fresh = session.get(Job, job.id)
    assert fresh.status == JobStatus.queued
    assert fresh.attempts == 1
    assert fresh.last_error == "boom"
    assert fresh.run_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)

    fresh.run_at = datetime.now(timezone.utc)
    session.commit()
    job = claim_next(session)
    fail(session, job, "boom2")
    fresh.run_at = datetime.now(timezone.utc)
    session.commit()
    job = claim_next(session)
    fail(session, job, "boom3")

    assert session.get(Job, job.id).status == JobStatus.failed
    assert session.get(Job, job.id).attempts == 3
