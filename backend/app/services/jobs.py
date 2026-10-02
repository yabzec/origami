import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session

from app.models import Job, JobStatus
from app.models.job import MAX_ATTEMPTS, RETRY_DELAYS  # noqa: F401  (re-exported policy)
from app.services.notify import notify_job_failed

log = logging.getLogger("origami.jobs")

CLAIM_SQL = text(
    """
    UPDATE jobs SET status = 'running', updated_at = now()
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'queued' AND run_at <= now()
        ORDER BY id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


def enqueue(
    session: Session, job_type: str, payload: dict, run_at: datetime | None = None
) -> Job:
    job = Job(type=job_type, payload=payload, run_at=run_at or datetime.now(timezone.utc))
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def claim_next(session: Session) -> Job | None:
    row = session.execute(CLAIM_SQL).first()
    session.commit()
    if row is None:
        return None
    job = session.get(Job, row[0])
    session.refresh(job)
    return job


def complete(session: Session, job: Job) -> None:
    job.status = JobStatus.done
    job.updated_at = datetime.now(timezone.utc)
    session.commit()


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next try after `attempts` failures (1-based); the last step repeats."""
    return timedelta(seconds=RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)])


def fail(session: Session, job: Job, error: str) -> None:
    now = datetime.now(timezone.utc)
    job.attempts += 1
    job.last_error = error
    job.updated_at = now
    if job.attempts < job.max_attempts:
        job.status = JobStatus.queued
        job.run_at = now + retry_delay(job.attempts)
        session.commit()
        return
    job.status = JobStatus.failed
    session.commit()
    try:
        notify_job_failed(session, job)
    except Exception:  # email problems must never crash or re-queue the worker
        log.exception("Failure notification for job %s raised", job.id)
