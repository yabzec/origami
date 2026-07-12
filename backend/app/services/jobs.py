from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session

from app.models import Job, JobStatus

BACKOFF_BASE_SECONDS = 30

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


def fail(session: Session, job: Job, error: str) -> None:
    job.attempts += 1
    job.last_error = error
    job.updated_at = datetime.now(timezone.utc)
    if job.attempts < job.max_attempts:
        job.status = JobStatus.queued
        job.run_at = datetime.now(timezone.utc) + timedelta(
            seconds=BACKOFF_BASE_SECONDS * 2**job.attempts
        )
    else:
        job.status = JobStatus.failed
    session.commit()
