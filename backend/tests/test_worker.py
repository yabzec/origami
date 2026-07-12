import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import Job, JobStatus
from app.services.jobs import claim_next, enqueue
from app.worker import runner


@pytest.fixture(autouse=True)
def clean_handlers():
    saved = dict(runner.HANDLERS)
    yield
    runner.HANDLERS.clear()
    runner.HANDLERS.update(saved)


def test_run_once_dispatches_and_completes(engine, session):
    calls = []

    @runner.register("echo")
    def handle_echo(s: Session, payload: dict) -> None:
        calls.append(payload)

    enqueue(session, "echo", {"v": 1})
    assert runner.run_once(engine) is True
    assert calls == [{"v": 1}]
    with Session(engine) as s:
        assert s.get(Job, 1).status == JobStatus.done


def test_run_once_failure_requeues(engine, session):
    @runner.register("boom")
    def handle_boom(s: Session, payload: dict) -> None:
        raise RuntimeError("kaput")

    enqueue(session, "boom", {})
    runner.run_once(engine)
    with Session(engine) as s:
        job = s.get(Job, 1)
        assert job.status == JobStatus.queued
        assert "kaput" in job.last_error


def test_run_once_unknown_type_fails_job(engine, session):
    enqueue(session, "nope", {})
    runner.run_once(engine)
    with Session(engine) as s:
        assert s.get(Job, 1).attempts == 1


def test_run_once_empty_queue(engine):
    assert runner.run_once(engine) is False


def test_run_once_rolls_back_before_fail_on_dirty_session(engine, session):
    # Simulate a handler that does DB work, leaves the transaction in an
    # aborted state (a real DBAPI error), then raises. Without a
    # session.rollback() before fail()'s session.commit(), this would
    # surface as sqlalchemy.exc.PendingRollbackError instead of cleanly
    # failing the job.
    @runner.register("dirty")
    def handle_dirty(s: Session, payload: dict) -> None:
        s.execute(text("SELECT 1/0"))  # aborts the current transaction

    enqueue(session, "dirty", {})
    assert runner.run_once(engine) is True
    with Session(engine) as s:
        job = s.get(Job, 1)
        assert job.status == JobStatus.queued
        assert job.attempts == 1
        assert job.last_error


def test_recover_resets_running(engine, session):
    enqueue(session, "echo", {})
    claim_next(session)  # leaves it 'running' as if worker crashed
    assert runner.recover(engine) == 1
    with Session(engine) as s:
        assert s.get(Job, 1).status == JobStatus.queued
