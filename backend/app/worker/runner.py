import logging
import time
import traceback
from typing import Callable

from sqlalchemy import text
from sqlmodel import Session

from app.services.jobs import claim_next, complete, fail

log = logging.getLogger("origami.worker")

HANDLERS: dict[str, Callable[[Session, dict], None]] = {}


def register(job_type: str):
    def decorator(fn: Callable[[Session, dict], None]):
        HANDLERS[job_type] = fn
        return fn

    return decorator


def is_final_attempt(payload: dict) -> bool:
    """True on the job's last try. Direct calls without runner info (tests, scripts) count as final."""
    return payload.get("_final_attempt", True)


def run_once(engine) -> bool:
    with Session(engine) as session:
        job = claim_next(session)
        if job is None:
            return False
        handler = HANDLERS.get(job.type)
        if handler is None:
            fail(session, job, f"No handler for job type {job.type!r}")
            return True
        # handlers see attempt info in a copy; the stored payload stays untouched
        payload = {
            **job.payload,
            "_attempt": job.attempts + 1,
            "_final_attempt": job.attempts + 1 >= job.max_attempts,
        }
        try:
            handler(session, payload)
        except Exception:
            session.rollback()
            log.exception("Job %s failed", job.id)
            fail(session, job, traceback.format_exc()[-2000:])
        else:
            complete(session, job)
        return True


def recover(engine) -> int:
    with Session(engine) as session:
        result = session.execute(
            text("UPDATE jobs SET status = 'queued', updated_at = now() WHERE status = 'running'")
        )
        session.commit()
        return result.rowcount


def main_loop(engine, poll_seconds: float = 1.0) -> None:
    logging.basicConfig(level=logging.INFO)
    recovered = recover(engine)
    if recovered:
        log.info("Recovered %d interrupted job(s)", recovered)
    log.info("Worker started")
    while True:
        if not run_once(engine):
            time.sleep(poll_seconds)
