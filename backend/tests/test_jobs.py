from datetime import datetime, timedelta, timezone

from app.models import Job, JobStatus
from app.models.job import MAX_ATTEMPTS, RETRY_DELAYS
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


def test_retry_constants_and_job_default():
    assert RETRY_DELAYS == [30, 120, 600, 1800]
    assert MAX_ATTEMPTS == 5


def test_enqueued_job_allows_five_attempts(session):
    job = enqueue(session, "process_document", {})
    assert job.max_attempts == 5


def test_cancelled_job_is_never_claimed(session):
    job = enqueue(session, "process_document", {})
    job.status = JobStatus.cancelled
    session.commit()
    assert claim_next(session) is None
