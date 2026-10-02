# Job Retries with Backoff and Email Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry every failed background job on a 30 s / 2 min / 10 min / 30 min schedule (5 attempts), email all users with an address when a job fails for good, move translation into its own retried job, and show retry progress in the UI.

**Architecture:** The generic retry policy lives in `app/services/jobs.py::fail` (constants in `app/models/job.py` to avoid an import cycle), which calls the new `app/services/notify.py` (SMTP via `smtplib`) after the final failure. The runner hands handlers `_attempt` / `_final_attempt` in a payload copy, so `process_document` and the new `translate_document` handler can keep a document `pending` (or a translation `pending`) between attempts. The documents API reports the newest open job per document as `active_job` (one query for the list endpoint) and re-process cancels queued jobs; the React document page and browse card render retry labels from pure helpers.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, Alembic, Postgres, `smtplib`/`email.message`, pytest on real Postgres; React 19, TypeScript, Vite, TanStack Query 5, vitest, oxlint.

**Spec:** `docs/superpowers/specs/2026-10-03-job-retries-notifications-design.md`

## Global Constraints

- **Build order:** this plan runs after `docs/superpowers/plans/2026-10-03-office-docs-folder-picker.md` has landed (Task 1 Step 1 verifies it). Where a step edits a function that plan also changed (`process_document`, `reprocess_document`, `DocumentPage.tsx`), read the current function first and merge the shown change into it — never paste a whole function over the current one.
- Retry schedule: `RETRY_DELAYS = [30, 120, 600, 1800]` seconds, `MAX_ATTEMPTS = len(RETRY_DELAYS) + 1 = 5`. Delay after the n-th failure is `RETRY_DELAYS[min(n - 1, len - 1)]`. One schedule for all job types.
- New statuses: `JobStatus.cancelled = "cancelled"`, `TranslationStatus.pending = "pending"`.
- Runner passes a payload copy with `_attempt = job.attempts + 1` and `_final_attempt = job.attempts + 1 >= job.max_attempts`; the stored payload is never modified. Handlers called directly without `_final_attempt` treat the call as final.
- Document between attempts: `status = "pending"`, `error_message = "Retrying: " + str(exc)[:2000]`; final attempt: `status = "failed"`, `error_message = str(exc)[:2000]`.
- Email: `smtplib.SMTP(host, port, timeout=20)`, `starttls()`, `login`, `send_message`. `send_email` and `notify_job_failed` never raise.
- Env settings (exact names): `SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT` (default `587`), `SMTP_USER`, `SMTP_APP_PASSWORD`, `SMTP_FROM` (default = `SMTP_USER`), `APP_BASE_URL` (optional).
- Labels: `process_document` → `Processing`, `translate_document` → `Translation`, `sweep_scan_sessions` → `Scan cleanup`, others → the job type.
- Subject: `[Origami] <Label> failed: <document title>` or `[Origami] <Label> failed`. Body: document title, label, attempts as `5/5`, last error trimmed to 1000 characters, link `<APP_BASE_URL>/documents/<id>` when `APP_BASE_URL` is set.
- CLI: `python -m app.cli set-email <username> <email>`, `python -m app.cli test-email`.
- `active_job` shape: `{"type", "attempts", "max_attempts", "run_at" (ISO, UTC offset), "last_error"}` or `null`.
- UI copy (verbatim): `Processing failed, retrying (<retryLabel>): <error line>`, `Translation pending…`, `Translation retrying (<retryLabel>)`, `Translation failed — notification sent. Re-process to retry.`; `retryLabel` = `attempt {attempts + 1}/{max_attempts}, next ≈ {relative}` with relative `now` / `N s` / `N min` / `N h`, `null` when `attempts === 0`.
- Mock boundaries: only `app/services/llm.py` (`llm_stub`) and `smtplib.SMTP` (spec ruling: external network service). Everything else runs for real, on Postgres.
- Commands: backend `cd /opt/origami/backend && uv run pytest -q`; frontend `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`. Baseline before the prerequisite plan: backend 185 passed, frontend 51 passed; Task 1 records the post-prerequisite baseline. Every task ends with the suite of the side it touched green.
- Commits: Conventional Commits, message ends with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Never stage `deploy/origami.service`, `deploy/origami.sh` or `..env.swp` (user's uncommitted work) — always `git add` explicit paths.

## Spec ambiguities resolved here (flagged for review)

- `RETRY_DELAYS` / `MAX_ATTEMPTS` are defined in `app/models/job.py` (the `Job.max_attempts` default needs them and `services/jobs.py` imports the models) and re-exported from `app/services/jobs.py`, so `jobs.RETRY_DELAYS` still exists as the spec names it.
- "Last error trimmed to 1000 characters" keeps the **last** 1000 characters: `last_error` is a traceback tail and the exception line is at the end.
- The banner's "<last_error, first line>" uses the last non-empty line of `last_error` (the `ExceptionType: message` line); the literal first line of a stored traceback is `Traceback (most recent call last):` or a cut-off fragment.
- Re-process keeps its 409 `document_busy` rule for `pending`/`processing` documents (the spec does not change it), so during processing retries the user waits; cancellation matters for queued `translate_document` jobs (document already `ready`) and stray queued `process_document` jobs.
- A `translate_document` run that finds the document was re-processed while it was translating (translation status no longer `pending`, or content chunks replaced) writes nothing and completes; the new processing run schedules a fresh translation. This is how "its results are overwritten by the new run" is guaranteed.
- `test-email` exits non-zero with a message when SMTP is not configured, no user has an email, or sending fails; the function is named `send_test_email` (a module-level `test_email` would be collected by pytest when imported into a test module).

## Review Focus

1. **User's browser in CEST reads a naive `run_at` as local time** — "next ≈" would be off by two hours. `run_at` is serialized with an explicit `+00:00` offset. Test in Task 6 (`test_active_job_reports_queued_retry`).
2. **Re-process clicked while a translation job is running** — the old run must not write stale translation chunks or mark the new content `done`. Test in Task 5 (`test_translation_discarded_when_document_reprocessed_meanwhile`).
3. **Gmail rejects the login (wrong/no app password) or the SMTP server is unreachable at the final failure** — the job still ends `failed`, the worker keeps running, nothing is re-queued. Tests in Task 2 (`test_send_email_returns_false_when_smtp_fails`) and Task 3 (`test_fail_survives_notification_error`).
4. **Document title with a line break or non-ASCII characters** (`Rechnung\nMüller`) — a raw newline in a header makes `EmailMessage` raise; the subject is flattened and the email is still sent. Test in Task 2 (`test_subject_flattens_title_whitespace_and_keeps_unicode`).
5. **Stored error is a multi-line traceback** — the amber banner must show `RuntimeError: provider down`, not `Traceback (most recent call last):`. Test in Task 7 (`processingRetryMessage` cases).

---

## File Structure

Backend
- `backend/app/models/job.py` — `RETRY_DELAYS`, `MAX_ATTEMPTS`, `JobStatus.cancelled`, `max_attempts` default (Task 1).
- `backend/app/models/user.py` — `email` (Task 1).
- `backend/alembic/versions/e5a1c7d93b20_user_email_job_max_attempts.py` — migration (Task 1).
- `backend/app/config.py`, `.env.example` — SMTP / `APP_BASE_URL` settings (Task 2).
- `backend/app/services/notify.py` — new: `send_email`, `notify_job_failed`, labels, message building (Task 2).
- `backend/app/cli.py` — `set-email`, `test-email` (Task 2).
- `backend/app/services/jobs.py` — retry delays, notification on final failure (Task 3).
- `backend/app/worker/runner.py` — `_attempt` / `_final_attempt`, `is_final_attempt` (Task 3).
- `backend/app/worker/pipeline.py` — retry-aware failure state (Task 4), `translate_document` job and scheduling (Task 5).
- `backend/app/models/document.py` — `TranslationStatus.pending` (Task 5).
- `backend/app/api/documents.py` — `active_jobs_for`, `active_job` in `serialize`, cancel on re-process (Task 6).
- Tests: `tests/conftest.py` (SMTP stub, settings fixture), `test_schema.py`, `test_migrations.py`, new `test_notify.py`, new `test_cli.py`, `test_jobs.py`, `test_worker.py`, `test_pipeline.py`, `test_reprocess.py`, `test_documents.py`.

Frontend
- `frontend/src/lib/types.ts` — `ActiveJob`, `TranslationStatus`, `Document.active_job` (Task 7).
- `frontend/src/lib/retry.ts` (+ test) — `retryLabel`, `errorHeadline`, `processingRetryMessage`, `badgeTitle`, `shouldPollDocument` (Task 7).
- `frontend/src/lib/translation.ts` (+ test) — `translationNote` (Task 7).
- `frontend/src/hooks/useNow.ts` (+ test) — ticking clock for relative labels (Task 7).
- `frontend/src/pages/DocumentPage.tsx`, `frontend/src/components/DocumentCard.tsx` — banner, translation line, polling, tooltip (Task 7).

---

### Task 1: Schema — user email, job retry constants, cancelled status

**Files:**
- Modify: `backend/app/models/job.py`
- Modify: `backend/app/models/user.py`
- Create: `backend/alembic/versions/e5a1c7d93b20_user_email_job_max_attempts.py`
- Test: `backend/tests/test_schema.py`, `backend/tests/test_migrations.py`, `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `app.models.job.RETRY_DELAYS: list[int] = [30, 120, 600, 1800]`, `app.models.job.MAX_ATTEMPTS: int = 5`, `JobStatus.cancelled`, `Job.max_attempts` default `5`, `User.email: str | None`, Alembic revision `e5a1c7d93b20` (column `users.email TEXT NULL`, `jobs.max_attempts` server default `5`).

- [ ] **Step 1: Verify the prerequisite plan landed**

Run:

```bash
cd /opt/origami && grep -n "preview_path" backend/app/models/document.py && grep -n "office_to_pdf" backend/app/worker/pipeline.py
```

Expected: both greps print at least one line. If either prints nothing, STOP and report "prerequisite plan 2026-10-03-office-docs-folder-picker not landed" — do not continue.

Then record the baseline: `cd /opt/origami/backend && uv run pytest -q` and `cd /opt/origami/frontend && npx vitest run` — both green; note the pass counts in your task report.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_schema.py`:

```python
def test_user_email_and_job_max_attempts_default(engine):
    inspector = inspect(engine)
    user_cols = {c["name"]: c for c in inspector.get_columns("users")}
    assert user_cols["email"]["nullable"] is True
    job_cols = {c["name"]: c for c in inspector.get_columns("jobs")}
    assert job_cols["max_attempts"]["default"] == "5"
```

Append to `backend/tests/test_migrations.py`:

```python
RETRY_REVISION = "e5a1c7d93b20"


def test_retry_migration_raises_max_attempts_of_open_jobs_only(engine):
    from alembic.script import ScriptDirectory

    cfg = _cfg()
    previous = ScriptDirectory.from_config(cfg).get_revision(RETRY_REVISION).down_revision
    command.downgrade(cfg, previous)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO jobs (type, payload, status, attempts, max_attempts, run_at, "
                "created_at, updated_at) VALUES "
                "('mig_queued', '{}', 'queued', 1, 3, now(), now(), now()), "
                "('mig_running', '{}', 'running', 0, 3, now(), now(), now()), "
                "('mig_done', '{}', 'done', 1, 3, now(), now(), now())"
            ))
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            rows = dict(conn.execute(
                text("SELECT type, max_attempts FROM jobs WHERE type LIKE 'mig_%'")
            ).all())
        assert rows == {"mig_queued": 5, "mig_running": 5, "mig_done": 3}
    finally:
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM jobs WHERE type LIKE 'mig_%'"))
```

Append to `backend/tests/test_jobs.py` (add `from app.models.job import MAX_ATTEMPTS, RETRY_DELAYS` to the imports):

```python
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
```

In the same file, delete `test_fail_requeues_with_backoff_then_fails`: it expects `failed` after 3 attempts, which stops being true in this task. The new schedule gets its own test in Task 3.

- [ ] **Step 3: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_schema.py tests/test_migrations.py tests/test_jobs.py -v`
Expected: FAIL — `ImportError: cannot import name 'MAX_ATTEMPTS'` (collection error for `test_jobs.py`); `KeyError: 'email'`; the migration test fails because revision `e5a1c7d93b20` is unknown.

- [ ] **Step 4: Implement the models**

In `backend/app/models/job.py`, add the constants above `class JobStatus`, add the status, and change the field default:

```python
RETRY_DELAYS: list[int] = [30, 120, 600, 1800]  # seconds to wait after failures 1..4
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1  # 5 attempts in total


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"  # superseded (re-process); never claimed again
```

```python
    max_attempts: int = MAX_ATTEMPTS
```

In `backend/app/models/user.py`, add after `password_hash`:

```python
    email: str | None = None  # failure notifications go to every user with an email
```

- [ ] **Step 5: Write the migration**

Run: `cd /opt/origami/backend && uv run alembic heads`
Expected: exactly one head (the prerequisite plan's migration). Copy its id; it is the `down_revision` below. If more than one head is printed, STOP and report.

Create `backend/alembic/versions/e5a1c7d93b20_user_email_job_max_attempts.py`, replacing `HEAD_ID_FROM_ALEMBIC_HEADS` with the id you just copied (it is the only value to fill in):

```python
"""user email, five job attempts

Revision ID: e5a1c7d93b20
Revises: HEAD_ID_FROM_ALEMBIC_HEADS
Create Date: 2026-10-03 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a1c7d93b20"
down_revision: Union[str, Sequence[str], None] = "HEAD_ID_FROM_ALEMBIC_HEADS"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.alter_column("jobs", "max_attempts", server_default=sa.text("5"))
    # open jobs get the new retry budget; finished ones keep their history
    op.execute("UPDATE jobs SET max_attempts = 5 WHERE status IN ('queued', 'running')")


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("jobs", "max_attempts", server_default=None)
    op.drop_column("users", "email")
```

Verify: `grep -c HEAD_ID_FROM_ALEMBIC_HEADS backend/alembic/versions/e5a1c7d93b20_user_email_job_max_attempts.py` prints `0`, and `uv run alembic heads` now prints only `e5a1c7d93b20 (head)`.

- [ ] **Step 6: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_schema.py tests/test_migrations.py tests/test_jobs.py -v`
Expected: PASS.

Then the full suite: `uv run pytest -q` → all green.

- [ ] **Step 7: Commit**

```bash
cd /opt/origami
git add backend/app/models/job.py backend/app/models/user.py \
  backend/alembic/versions/e5a1c7d93b20_user_email_job_max_attempts.py \
  backend/tests/test_schema.py backend/tests/test_migrations.py backend/tests/test_jobs.py
git commit -m "feat: add user email, cancelled jobs and five-attempt retry budget

test_fail_requeues_with_backoff_then_fails removed; the new schedule is
covered in the retry engine task.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Email notifications — settings, notify service, CLI

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Create: `backend/app/services/notify.py`
- Modify: `backend/app/cli.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_notify.py` (new), `backend/tests/test_cli.py` (new)

**Interfaces:**
- Consumes: `User.email` (Task 1), `Job`, `Document`.
- Produces:
  - Settings fields `smtp_host: str`, `smtp_port: int`, `smtp_user: str`, `smtp_app_password: str`, `smtp_from: str`, `app_base_url: str`.
  - `app.services.notify.job_label(job_type: str) -> str`
  - `app.services.notify.smtp_configured() -> bool`
  - `app.services.notify.recipients(session: Session) -> list[str]`
  - `app.services.notify.send_email(to: list[str], subject: str, body: str) -> bool` (never raises)
  - `app.services.notify.build_failure_message(session: Session, job: Job) -> tuple[str, str]` (subject, body)
  - `app.services.notify.notify_job_failed(session: Session, job: Job) -> None` (never raises on SMTP problems)
  - `app.cli.set_email(username: str, email: str) -> None`, `app.cli.send_test_email() -> None`
  - Test fixtures: autouse `smtp_stub` (list of `FakeSMTP` instances; each has `host`, `port`, `timeout`, `calls`, `messages`), `smtp_settings(**overrides) -> Settings`.

- [ ] **Step 1: Add the SMTP test fixtures**

In `backend/tests/conftest.py`, add after the imports:

```python
class FakeSMTP:
    """Records SMTP sessions instead of opening network connections."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls: list = []
        self.messages: list = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append("send_message")
        self.messages.append(msg)
```

and at the end of the file:

```python
@pytest.fixture(autouse=True)
def smtp_stub(monkeypatch):
    """Second sanctioned mock boundary: smtplib.SMTP (external network).

    Autouse, so no test can ever send a real email, even with SMTP creds in the developer's .env.
    """
    FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    return FakeSMTP.instances


@pytest.fixture
def smtp_settings(monkeypatch):
    """Point app.services.notify at explicit settings instead of the real .env."""
    from app.config import Settings

    def apply(**overrides):
        values = {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_app_password": "",
            "smtp_from": "",
            "app_base_url": "",
            **overrides,
        }
        settings = Settings(_env_file=None, **values)
        monkeypatch.setattr("app.services.notify.get_settings", lambda: settings)
        return settings

    return apply
```

Also change the `llm_stub` docstring to `"""Stub the LLM mock boundary: app.services.llm (the other one is smtplib.SMTP)."""`.

- [ ] **Step 2: Write the failing notify tests**

Create `backend/tests/test_notify.py`:

```python
import logging

from app.config import Settings
from app.models import DocType, Document, Job, JobStatus, User
from app.services import notify

CONFIGURED = {"smtp_user": "origami@gmail.com", "smtp_app_password": "abcd efgh ijkl mnop"}


def _user(session, username, email):
    user = User(username=username, password_hash="x", email=email)
    session.add(user)
    session.commit()
    return user


def _doc(session, title="Rechnung"):
    doc = Document(title=title, doc_type=DocType.pdf)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _failed_job(session, job_type="translate_document", payload=None, last_error="RuntimeError: provider down"):
    job = Job(
        type=job_type,
        payload=payload or {},
        status=JobStatus.failed,
        attempts=5,
        max_attempts=5,
        last_error=last_error,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_smtp_settings_defaults():
    s = Settings(_env_file=None)
    assert (s.smtp_host, s.smtp_port) == ("smtp.gmail.com", 587)
    assert (s.smtp_user, s.smtp_app_password, s.smtp_from, s.app_base_url) == ("", "", "", "")


def test_job_labels():
    assert notify.job_label("process_document") == "Processing"
    assert notify.job_label("translate_document") == "Translation"
    assert notify.job_label("sweep_scan_sessions") == "Scan cleanup"
    assert notify.job_label("reindex") == "reindex"


def test_not_configured_sends_nothing_and_warns(session, smtp_settings, smtp_stub, caplog):
    caplog.set_level(logging.WARNING, logger="origami.notify")
    smtp_settings()
    _user(session, "anna", "anna@example.com")
    notify.notify_job_failed(session, _failed_job(session))
    assert smtp_stub == []
    assert "not configured" in caplog.text


def test_no_recipients_sends_nothing_and_warns(session, smtp_settings, smtp_stub, caplog):
    caplog.set_level(logging.WARNING, logger="origami.notify")
    smtp_settings(**CONFIGURED)
    _user(session, "anna", None)
    notify.notify_job_failed(session, _failed_job(session))
    assert smtp_stub == []
    assert "No user has an email" in caplog.text


def test_configured_sends_with_starttls_login_and_subject(session, smtp_settings, smtp_stub):
    smtp_settings(**CONFIGURED, app_base_url="http://origami.lan:8000/")
    _user(session, "anna", "anna@example.com")
    _user(session, "bruno", "bruno@example.com")
    _user(session, "carla", None)
    doc = _doc(session)
    notify.notify_job_failed(
        session, _failed_job(session, payload={"document_id": str(doc.id)})
    )

    assert len(smtp_stub) == 1
    smtp = smtp_stub[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.gmail.com", 587, 20)
    assert smtp.calls[:3] == [
        "starttls",
        ("login", "origami@gmail.com", "abcd efgh ijkl mnop"),
        "send_message",
    ]
    msg = smtp.messages[0]
    assert msg["Subject"] == "[Origami] Translation failed: Rechnung"
    assert msg["From"] == "origami@gmail.com"  # SMTP_FROM empty → SMTP_USER
    assert msg["To"] == "anna@example.com, bruno@example.com"
    body = msg.get_content()
    assert "Document: Rechnung" in body
    assert "Attempts: 5/5" in body
    assert "RuntimeError: provider down" in body
    assert f"http://origami.lan:8000/documents/{doc.id}" in body


def test_smtp_from_overrides_sender(session, smtp_settings, smtp_stub):
    smtp_settings(**CONFIGURED, smtp_from="Origami <noreply@example.com>")
    assert notify.send_email(["anna@example.com"], "s", "b") is True
    assert smtp_stub[0].messages[0]["From"] == "Origami <noreply@example.com>"


def test_subject_without_document(session, smtp_settings):
    smtp_settings(**CONFIGURED)
    subject, body = notify.build_failure_message(session, _failed_job(session, "sweep_scan_sessions"))
    assert subject == "[Origami] Scan cleanup failed"
    assert "Document:" not in body
    assert "/documents/" not in body  # no APP_BASE_URL and no document → no link

    subject, _ = notify.build_failure_message(
        session, _failed_job(session, "process_document", payload={"document_id": "not-a-uuid"})
    )
    assert subject == "[Origami] Processing failed"


def test_last_error_keeps_last_1000_characters(session, smtp_settings):
    smtp_settings(**CONFIGURED)
    job = _failed_job(session, last_error="x" * 3000 + "\nRuntimeError: END")
    _, body = notify.build_failure_message(session, job)
    assert body.endswith("RuntimeError: END")
    assert "x" * 1000 not in body
    assert "x" * 900 in body


def test_subject_flattens_title_whitespace_and_keeps_unicode(session, smtp_settings, smtp_stub):
    smtp_settings(**CONFIGURED)
    _user(session, "anna", "anna@example.com")
    doc = _doc(session, title="Rechnung\nMüller")
    notify.notify_job_failed(
        session, _failed_job(session, "process_document", payload={"document_id": str(doc.id)})
    )
    assert smtp_stub[0].messages[0]["Subject"] == "[Origami] Processing failed: Rechnung Müller"


def test_send_email_returns_false_when_smtp_fails(smtp_settings, monkeypatch, caplog):
    smtp_settings(**CONFIGURED)

    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", refuse)
    assert notify.send_email(["anna@example.com"], "subject", "body") is False
    assert "connection refused" in caplog.text


def test_send_email_not_configured_returns_false(smtp_settings, smtp_stub):
    smtp_settings()
    assert notify.send_email(["anna@example.com"], "subject", "body") is False
    assert smtp_stub == []
```

- [ ] **Step 3: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_notify.py -v`
Expected: FAIL — `ImportError: cannot import name 'notify' from 'app.services'`.

- [ ] **Step 4: Implement settings and the notify service**

In `backend/app/config.py`, add to `Settings` after `cors_origins`:

```python
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_app_password: str = ""  # Gmail: an app password (needs 2-step verification)
    smtp_from: str = ""  # empty → smtp_user
    app_base_url: str = ""  # e.g. http://origami.lan:8000 — adds document links to emails
```

Create `backend/app/services/notify.py`:

```python
"""Email notifications over SMTP. Nothing in here may raise into the worker."""

import logging
import smtplib
import uuid
from email.message import EmailMessage

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Document, Job, User

log = logging.getLogger("origami.notify")

JOB_LABELS = {
    "process_document": "Processing",
    "translate_document": "Translation",
    "sweep_scan_sessions": "Scan cleanup",
}
ERROR_CHARS = 1000
SMTP_TIMEOUT_SECONDS = 20


def job_label(job_type: str) -> str:
    return JOB_LABELS.get(job_type, job_type)


def smtp_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_user and s.smtp_app_password)


def recipients(session: Session) -> list[str]:
    emails = session.exec(
        select(User.email).where(User.email.is_not(None)).order_by(User.id)
    ).all()
    return [e.strip() for e in emails if e and e.strip()]


def send_email(to: list[str], subject: str, body: str) -> bool:
    """Send one message to all recipients. Returns False (and logs) on any problem; never raises."""
    if not smtp_configured():
        log.warning("SMTP is not configured; email %r not sent", subject)
        return False
    s = get_settings()
    try:
        msg = EmailMessage()
        msg["From"] = s.smtp_from or s.smtp_user
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(s.smtp_user, s.smtp_app_password)
            smtp.send_message(msg)
    except Exception:
        log.exception("Sending email %r to %s failed", subject, ", ".join(to))
        return False
    return True


def _job_document(session: Session, job: Job) -> Document | None:
    raw = (job.payload or {}).get("document_id")
    if not raw:
        return None
    try:
        return session.get(Document, uuid.UUID(str(raw)))
    except ValueError:
        return None


def build_failure_message(session: Session, job: Job) -> tuple[str, str]:
    label = job_label(job.type)
    doc = _job_document(session, job)
    title = " ".join(doc.title.split()) if doc else ""  # a newline in a header would raise
    subject = f"[Origami] {label} failed: {title}" if doc else f"[Origami] {label} failed"
    lines = [f"Document: {title}"] if doc else []
    lines += [
        f"Job: {label}",
        f"Attempts: {job.attempts}/{job.max_attempts}",
    ]
    base = get_settings().app_base_url.rstrip("/")
    if doc and base:
        lines.append(f"Open: {base}/documents/{doc.id}")
    # the traceback tail holds the exception line, so keep the end
    lines += ["", "Last error:", (job.last_error or "(none)").strip()[-ERROR_CHARS:]]
    return subject, "\n".join(lines)


def notify_job_failed(session: Session, job: Job) -> None:
    if not smtp_configured():
        log.warning("SMTP is not configured; failure of job %s (%s) not emailed", job.id, job.type)
        return
    to = recipients(session)
    if not to:
        log.warning("No user has an email address; failure of job %s (%s) not emailed", job.id, job.type)
        return
    subject, body = build_failure_message(session, job)
    send_email(to, subject, body)
```

- [ ] **Step 5: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_notify.py -v`
Expected: PASS (12 tests).

- [ ] **Step 6: Write the failing CLI tests**

Create `backend/tests/test_cli.py`:

```python
import sys

import pytest

from app import cli
from app.models import User

CONFIGURED = {"smtp_user": "origami@gmail.com", "smtp_app_password": "abcd efgh ijkl mnop"}


@pytest.fixture
def cli_engine(engine, monkeypatch):
    monkeypatch.setattr(cli, "engine", engine)


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["origami", *argv])
    cli.main()


def test_set_email(session, user, cli_engine, monkeypatch, capsys):
    run_cli(monkeypatch, "set-email", "test", " anna@example.com ")
    session.refresh(user)
    assert user.email == "anna@example.com"
    assert "anna@example.com" in capsys.readouterr().out


def test_set_email_unknown_user(session, cli_engine, monkeypatch):
    with pytest.raises(SystemExit, match="not found"):
        run_cli(monkeypatch, "set-email", "ghost", "ghost@example.com")


def test_set_email_rejects_invalid_address(session, user, cli_engine, monkeypatch):
    with pytest.raises(SystemExit, match="Not a valid email"):
        run_cli(monkeypatch, "set-email", "test", "not-an-email")
    session.refresh(user)
    assert user.email is None


def test_test_email_sends_to_all_recipients(session, user, cli_engine, monkeypatch, capsys, smtp_settings, smtp_stub):
    smtp_settings(**CONFIGURED)
    user.email = "anna@example.com"
    session.add(User(username="bruno", password_hash="x", email="bruno@example.com"))
    session.commit()
    run_cli(monkeypatch, "test-email")
    msg = smtp_stub[0].messages[0]
    assert msg["Subject"] == "[Origami] Test email"
    assert msg["To"] == "anna@example.com, bruno@example.com"
    assert "Test email sent to anna@example.com, bruno@example.com" in capsys.readouterr().out


def test_test_email_not_configured(session, user, cli_engine, monkeypatch, smtp_settings, smtp_stub):
    smtp_settings()
    user.email = "anna@example.com"
    session.commit()
    with pytest.raises(SystemExit, match="SMTP is not configured"):
        run_cli(monkeypatch, "test-email")
    assert smtp_stub == []


def test_test_email_without_recipients(session, user, cli_engine, monkeypatch, smtp_settings):
    smtp_settings(**CONFIGURED)
    with pytest.raises(SystemExit, match="No user has an email"):
        run_cli(monkeypatch, "test-email")


def test_test_email_reports_send_failure(session, user, cli_engine, monkeypatch, smtp_settings):
    smtp_settings(**CONFIGURED)
    user.email = "anna@example.com"
    session.commit()

    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", refuse)
    with pytest.raises(SystemExit, match="failed"):
        run_cli(monkeypatch, "test-email")
```

- [ ] **Step 7: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_cli.py -v`
Expected: FAIL — argparse `invalid choice: 'set-email'` (`SystemExit: 2`, so the `match=` tests fail on the message) and `test_set_email` errors.

- [ ] **Step 8: Implement the CLI commands**

In `backend/app/cli.py`, add the import `from app.services.notify import recipients, send_email, smtp_configured`, add these functions after `create_user`:

```python
def set_email(username: str, email: str) -> None:
    email = email.strip()
    if "@" not in email or any(ch.isspace() for ch in email):
        raise SystemExit(f"Not a valid email address: {email!r}")
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise SystemExit(f"User {username!r} not found")
        user.email = email
        session.commit()
    print(f"Email of {username!r} set to {email}")


def send_test_email() -> None:
    if not smtp_configured():
        raise SystemExit("SMTP is not configured: set SMTP_USER and SMTP_APP_PASSWORD in .env")
    with Session(engine) as session:
        to = recipients(session)
    if not to:
        raise SystemExit("No user has an email address: run `python -m app.cli set-email <username> <email>`")
    ok = send_email(to, "[Origami] Test email", "This is a test message from Origami.\nNotifications work.")
    if not ok:
        raise SystemExit(f"Sending to {', '.join(to)} failed (see the error above)")
    print(f"Test email sent to {', '.join(to)}")
```

and replace `main` with:

```python
def main() -> None:
    parser = argparse.ArgumentParser(prog="origami")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    p_email = sub.add_parser("set-email", help="set the notification email of a user")
    p_email.add_argument("username")
    p_email.add_argument("email")
    sub.add_parser("test-email", help="send a test email to every user with an email")
    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args.username)
    elif args.command == "set-email":
        set_email(args.username, args.email)
    elif args.command == "test-email":
        send_test_email()
```

- [ ] **Step 9: Document the settings**

Append to `/opt/origami/.env.example` (comments on their own lines — inline comments are not safe for empty values):

```bash
# --- Email notifications (a background job failed 5 times) ---
# Gmail: turn on 2-Step Verification, then create an App password
# (Google Account > Security > App passwords) and paste it below.
# The normal account password does NOT work.
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_APP_PASSWORD=
# Sender address; empty = SMTP_USER
SMTP_FROM=
# Base URL of this app, e.g. http://origami.lan:8000 (adds a link to the document in emails)
APP_BASE_URL=
# Recipients: python -m app.cli set-email <username> <email>; check with: python -m app.cli test-email
```

- [ ] **Step 10: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_notify.py tests/test_cli.py -v && uv run pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 11: Commit**

```bash
cd /opt/origami
git add backend/app/config.py backend/app/services/notify.py backend/app/cli.py \
  backend/tests/conftest.py backend/tests/test_notify.py backend/tests/test_cli.py .env.example
git commit -m "feat: add SMTP email notifications and set-email/test-email CLI

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Retry engine — delays, final-failure notification, attempt info for handlers

**Files:**
- Modify: `backend/app/services/jobs.py`
- Modify: `backend/app/worker/runner.py`
- Test: `backend/tests/test_jobs.py`, `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `RETRY_DELAYS`, `MAX_ATTEMPTS` (Task 1); `notify_job_failed(session, job)` (Task 2).
- Produces:
  - `app.services.jobs.retry_delay(attempts: int) -> timedelta`; `app.services.jobs.RETRY_DELAYS`, `app.services.jobs.MAX_ATTEMPTS` (re-exported); `fail(session, job, error)` with the new schedule and notification.
  - `app.worker.runner.is_final_attempt(payload: dict) -> bool` — `payload.get("_final_attempt", True)`.
  - Handlers receive `{**job.payload, "_attempt": int, "_final_attempt": bool}`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_jobs.py`, change the imports to:

```python
from datetime import datetime, timedelta, timezone

from app.models import Job, JobStatus
from app.models.job import MAX_ATTEMPTS, RETRY_DELAYS
from app.services import jobs
from app.services.jobs import claim_next, complete, enqueue, fail
```

and append:

```python
def _claim_now(session, job_id):
    job = session.get(Job, job_id)
    job.run_at = datetime.now(timezone.utc)
    session.commit()
    return claim_next(session)


def test_fail_retries_with_growing_delays_then_fails_and_notifies(session, monkeypatch):
    notified = []
    monkeypatch.setattr(jobs, "notify_job_failed", lambda s, j: notified.append(j.id))
    job_id = enqueue(session, "process_document", {}).id
    claimed = claim_next(session)

    delays = []
    for n in range(1, 5):
        fail(session, claimed, f"boom{n}")
        fresh = session.get(Job, job_id)
        assert (fresh.status, fresh.attempts, fresh.last_error) == (JobStatus.queued, n, f"boom{n}")
        # run_at and updated_at come from the same "now" inside fail()
        delays.append((fresh.run_at - fresh.updated_at).total_seconds())
        claimed = _claim_now(session, job_id)
    assert delays == [30, 120, 600, 1800]
    assert notified == []

    fail(session, claimed, "boom5")
    fresh = session.get(Job, job_id)
    assert (fresh.status, fresh.attempts) == (JobStatus.failed, 5)
    assert notified == [job_id]


def test_retry_delay_caps_at_last_step():
    assert jobs.retry_delay(1) == timedelta(seconds=30)
    assert jobs.retry_delay(4) == timedelta(seconds=1800)
    assert jobs.retry_delay(9) == timedelta(seconds=1800)  # jobs with a larger max_attempts


def test_fail_survives_notification_error(session, monkeypatch):
    def explode(s, j):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(jobs, "notify_job_failed", explode)
    enqueue(session, "process_document", {})
    job = claim_next(session)
    job.attempts = job.max_attempts - 1
    session.commit()
    fail(session, job, "last")  # must not raise
    assert session.get(Job, job.id).status == JobStatus.failed
```

In `backend/tests/test_worker.py`, add `from datetime import datetime, timezone` to the imports, change the assertion in `test_run_once_dispatches_and_completes` to:

```python
    assert calls == [{"v": 1, "_attempt": 1, "_final_attempt": False}]
```

and append:

```python
def test_run_once_passes_attempt_info_without_storing_it(engine, session):
    seen = []

    @runner.register("peek")
    def handle_peek(s: Session, payload: dict) -> None:
        seen.append(dict(payload))
        raise RuntimeError("again")

    enqueue(session, "peek", {"v": 1})
    runner.run_once(engine)
    assert seen == [{"v": 1, "_attempt": 1, "_final_attempt": False}]
    with Session(engine) as s:
        job = s.get(Job, 1)
        assert job.payload == {"v": 1}
        job.attempts = job.max_attempts - 1  # the next run is the last one
        job.run_at = datetime.now(timezone.utc)
        s.commit()

    runner.run_once(engine)
    assert seen[-1] == {"v": 1, "_attempt": 5, "_final_attempt": True}
    with Session(engine) as s:
        job = s.get(Job, 1)
        assert job.payload == {"v": 1}
        assert job.status == JobStatus.failed


def test_is_final_attempt_defaults_to_true_for_direct_calls():
    assert runner.is_final_attempt({}) is True
    assert runner.is_final_attempt({"_final_attempt": False}) is False
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_jobs.py tests/test_worker.py -v`
Expected: FAIL — `AttributeError: module 'app.services.jobs' has no attribute 'notify_job_failed'` / `retry_delay`, delays `[60, 120, 240, 480]`, worker payload assertions fail, `is_final_attempt` missing.

- [ ] **Step 3: Implement `fail()`**

In `backend/app/services/jobs.py`: replace the imports and `BACKOFF_BASE_SECONDS` with:

```python
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session

from app.models import Job, JobStatus
from app.models.job import MAX_ATTEMPTS, RETRY_DELAYS  # noqa: F401  (re-exported policy)
from app.services.notify import notify_job_failed

log = logging.getLogger("origami.jobs")
```

and replace `fail` with:

```python
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
```

Check nothing else used the removed constant: `grep -rn BACKOFF_BASE_SECONDS /opt/origami/backend` → no output.

- [ ] **Step 4: Implement the runner change**

In `backend/app/worker/runner.py`, add after `register`:

```python
def is_final_attempt(payload: dict) -> bool:
    """True on the job's last try. Direct calls without runner info (tests, scripts) count as final."""
    return payload.get("_final_attempt", True)
```

and replace `run_once` with (only the `payload` copy is new):

```python
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
```

- [ ] **Step 5: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_jobs.py tests/test_worker.py -v && uv run pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami
git add backend/app/services/jobs.py backend/app/worker/runner.py backend/tests/test_jobs.py backend/tests/test_worker.py
git commit -m "feat: retry jobs after 30s/2m/10m/30m and email on final failure

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: process_document — keep the document pending between attempts

**Files:**
- Modify: `backend/app/worker/pipeline.py` (`process_document` except block, import line of `app.worker.runner`)
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `is_final_attempt(payload)` (Task 3); runner's `_final_attempt` key.
- Produces: on a non-final failure the document is `pending` with `error_message = "Retrying: " + str(exc)[:2000]`; on the final one `failed` with `str(exc)[:2000]` (unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pipeline.py`:

```python
def _failing_text_doc(session, pipeline_storage, monkeypatch):
    doc = make_doc(session, doc_type=DocType.text, title="Nota")
    rel, _ = pipeline_storage.store_file(doc.id, ".txt", b"Testo di prova.")
    doc.file_path = rel
    session.commit()
    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    return doc


def test_non_final_failure_leaves_document_pending_with_retry_message(
    session, pipeline_storage, llm_stub, monkeypatch
):
    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    with pytest.raises(RuntimeError):
        pipeline.process_document(
            session, {"document_id": str(doc.id), "_attempt": 1, "_final_attempt": False}
        )
    session.refresh(doc)
    assert doc.status == DocStatus.pending
    assert doc.error_message == "Retrying: embedding API down"


def test_final_failure_marks_document_failed(session, pipeline_storage, llm_stub, monkeypatch):
    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    with pytest.raises(RuntimeError):
        pipeline.process_document(
            session, {"document_id": str(doc.id), "_attempt": 5, "_final_attempt": True}
        )
    session.refresh(doc)
    assert doc.status == DocStatus.failed
    assert doc.error_message == "embedding API down"


def test_failed_attempt_via_runner_requeues_and_keeps_document_pending(
    engine, session, pipeline_storage, llm_stub, monkeypatch
):
    from app.models import Job, JobStatus
    from app.services.jobs import enqueue
    from app.worker.runner import run_once

    doc = _failing_text_doc(session, pipeline_storage, monkeypatch)
    enqueue(session, "process_document", {"document_id": str(doc.id)})
    assert run_once(engine) is True
    session.expire_all()
    assert session.get(Document, doc.id).status == DocStatus.pending
    job = session.exec(select(Job)).one()
    assert (job.status, job.attempts) == (JobStatus.queued, 1)
    assert job.payload == {"document_id": str(doc.id)}
```

`test_pipeline_resumes_after_embedding_failure` stays as it is: it calls the handler without runner info, which counts as final, so it still expects `failed`.

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_pipeline.py -k "retry_message or final_failure or via_runner" -v`
Expected: the non-final test and the runner test FAIL (`status == 'failed'`); `test_final_failure_marks_document_failed` passes.

- [ ] **Step 3: Implement**

Read the current `process_document` in `backend/app/worker/pipeline.py` first (the prerequisite plan may have changed the steps inside `try:`; keep them). Change the runner import to:

```python
from app.worker.runner import is_final_attempt, register
```

and replace only the `except Exception as exc:` block of `process_document` with:

```python
    except Exception as exc:
        session.rollback()
        if is_final_attempt(payload):
            doc.status = DocStatus.failed
            doc.error_message = str(exc)[:2000]
        else:
            # more attempts follow: show "waiting", not "failed"
            doc.status = DocStatus.pending
            doc.error_message = "Retrying: " + str(exc)[:2000]
        session.commit()
        raise
```

- [ ] **Step 4: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_pipeline.py -v && uv run pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add backend/app/worker/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: keep documents pending with a retry message between attempts

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Translation as its own retried job

**Files:**
- Modify: `backend/app/models/document.py` (`TranslationStatus`)
- Modify: `backend/app/worker/pipeline.py` (`process_document` success path, `_ensure_translation` → new functions, `translate_document` handler, model import line)
- Test: `backend/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `is_final_attempt` (Task 3); existing `_has_chunks`, `_content_chunks`, `_next_chunk_index`, `_embed_pending_chunks`, `llm_translate`, `get_primary_language()`.
- Produces:
  - `TranslationStatus.pending = "pending"`.
  - Job type `translate_document` with payload `{"document_id": "<uuid str>"}`, handler `app.worker.pipeline.translate_document(session: Session, payload: dict) -> None`.
  - `process_document` no longer translates; on success, when `detected_language` is set and differs from the primary language and content chunks exist, it sets `translation_status = "pending"` and queues `translate_document` in the same commit as `status = "ready"`.

- [ ] **Step 1: Rewrite the translation tests**

In `backend/tests/test_pipeline.py`, change the model import to `from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job` and `from sqlmodel import Session, select`.

Delete `test_german_document_gets_translation_chunks` and `test_translation_failure_is_not_fatal` (both assume translation inside `process_document`). In `test_italian_document_is_not_translated`, add at the end:

```python
    assert session.exec(select(Job).where(Job.type == "translate_document")).all() == []
```

In `test_scan_with_ocr_text_gets_summary_and_language`, replace the last line `assert doc.translation_status == "done"` with:

```python
    assert doc.translation_status == "pending"
    assert run_once(engine) is True  # the queued translate_document job
    session.refresh(doc)
    assert doc.translation_status == "done"
    assert ChunkSource.translation in chunks_by_source(session, doc)
```

Append:

```python
def _german_doc(session, pipeline_storage, llm_stub):
    llm_stub["language"] = "de"
    return run(session, _text_doc(session, pipeline_storage))


def translate(session, doc, **extra):
    pipeline.translate_document(session, {"document_id": str(doc.id), **extra})
    session.refresh(doc)
    return doc


def test_german_document_queues_translation_job(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    assert doc.status == DocStatus.ready
    assert doc.detected_language == "de"
    assert doc.translation_status == "pending"
    jobs = session.exec(select(Job).where(Job.type == "translate_document")).all()
    assert [j.payload for j in jobs] == [{"document_id": str(doc.id)}]
    assert llm_stub["translate"] == []
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translate_document_adds_embedded_translation_chunks(session, pipeline_storage, llm_stub):
    doc = translate(session, _german_doc(session, pipeline_storage, llm_stub))
    assert doc.translation_status == "done"
    by_source = chunks_by_source(session, doc)
    content = sorted(by_source[ChunkSource.content], key=lambda c: c.chunk_index)
    translated = sorted(by_source[ChunkSource.translation], key=lambda c: c.chunk_index)
    assert len(translated) == len(content)
    assert [t.page_number for t in translated] == [c.page_number for c in content]
    assert translated[0].content.startswith("[it] ")
    assert all(t.embedding is not None for t in translated)


def test_translation_error_before_final_attempt_stays_pending(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    llm_stub["translate_error"] = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        pipeline.translate_document(
            session, {"document_id": str(doc.id), "_attempt": 1, "_final_attempt": False}
        )
    session.refresh(doc)
    assert doc.translation_status == "pending"
    assert doc.status == DocStatus.ready
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translation_error_on_final_attempt_marks_failed(session, pipeline_storage, llm_stub):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    llm_stub["translate_error"] = RuntimeError("provider down")
    with pytest.raises(RuntimeError):
        pipeline.translate_document(
            session, {"document_id": str(doc.id), "_attempt": 5, "_final_attempt": True}
        )
    session.refresh(doc)
    assert doc.translation_status == "failed"
    assert doc.status == DocStatus.ready
    assert ChunkSource.translation not in chunks_by_source(session, doc)


def test_translate_document_already_done_is_noop(session, pipeline_storage, llm_stub):
    doc = translate(session, _german_doc(session, pipeline_storage, llm_stub))
    calls = len(llm_stub["translate"])
    count = len(chunks_by_source(session, doc)[ChunkSource.translation])
    doc = translate(session, doc)
    assert len(llm_stub["translate"]) == calls
    assert len(chunks_by_source(session, doc)[ChunkSource.translation]) == count


def test_translate_document_missing_document_is_noop(session, llm_stub):
    pipeline.translate_document(session, {"document_id": str(uuid.uuid4())})
    assert llm_stub["translate"] == []


def test_translate_document_retry_embeds_without_retranslating(
    session, pipeline_storage, llm_stub, monkeypatch
):
    doc = _german_doc(session, pipeline_storage, llm_stub)
    working_embed = pipeline.llm_embed  # the llm_stub fake
    boom = RuntimeError("embedding API down")
    monkeypatch.setattr(pipeline, "llm_embed", lambda texts: (_ for _ in ()).throw(boom))
    with pytest.raises(RuntimeError):
        pipeline.translate_document(session, {"document_id": str(doc.id), "_final_attempt": False})
    calls = len(llm_stub["translate"])

    monkeypatch.setattr(pipeline, "llm_embed", working_embed)
    doc = translate(session, doc)
    assert doc.translation_status == "done"
    assert len(llm_stub["translate"]) == calls  # chunks from the first try are reused
    by_source = chunks_by_source(session, doc)
    assert len(by_source[ChunkSource.translation]) == len(by_source[ChunkSource.content])
    assert all(t.embedding is not None for t in by_source[ChunkSource.translation])


def test_translation_discarded_when_document_reprocessed_meanwhile(
    session, engine, pipeline_storage, llm_stub, monkeypatch
):
    doc = _german_doc(session, pipeline_storage, llm_stub)

    def translate_during_reprocess(text, target):
        with Session(engine) as other:  # what reprocess_document commits meanwhile
            fresh = other.get(Document, doc.id)
            fresh.translation_status = None
            other.commit()
        return f"[{target}] {text}"

    monkeypatch.setattr(pipeline, "llm_translate", translate_during_reprocess)
    doc = translate(session, doc)
    assert doc.translation_status is None
    assert ChunkSource.translation not in chunks_by_source(session, doc)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `AttributeError: module 'app.worker.pipeline' has no attribute 'translate_document'`; `test_german_document_queues_translation_job` fails (`translation_status == 'done'`, translate called).

- [ ] **Step 3: Add the status**

In `backend/app/models/document.py`:

```python
class TranslationStatus(StrEnum):
    pending = "pending"  # translate_document job queued or retrying
    done = "done"
    failed = "failed"
```

- [ ] **Step 4: Implement the pipeline changes**

Read `process_document` and the area around `_ensure_summary` / `_ensure_translation` in `backend/app/worker/pipeline.py` first; the prerequisite plan changed `_ensure_summary` and `_ensure_metadata_chunk` — leave those as they are.

1. Add `Job` to the model import: `from app.models import Chunk, ChunkSource, DocStatus, DocType, Document, Job, TranslationStatus`.

2. In `process_document`, delete the line `_ensure_translation(session, doc)` and change the success tail from

```python
        doc.status = DocStatus.ready
        doc.error_message = None
        session.commit()
```

to

```python
        doc.status = DocStatus.ready
        doc.error_message = None
        _schedule_translation(session, doc)  # same commit as "ready"
        session.commit()
```

3. Replace the whole `_ensure_translation` function with:

```python
def _needs_translation(session: Session, doc: Document) -> bool:
    if not doc.detected_language or doc.detected_language == get_primary_language():
        return False
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return False
    return _has_chunks(session, doc, ChunkSource.content)


def _schedule_translation(session: Session, doc: Document) -> None:
    """Queue translate_document in the caller's transaction (no commit here)."""
    if not _needs_translation(session, doc):
        return
    doc.translation_status = TranslationStatus.pending
    session.add(
        Job(
            type="translate_document",
            payload={"document_id": str(doc.id)},
            run_at=datetime.now(timezone.utc),
        )
    )


def _insert_translation_chunks(session: Session, doc: Document) -> bool:
    """Translate every content chunk in memory, then insert all translation chunks in one commit.

    Returns False without writing when the document was re-processed meanwhile (translation
    status reset or content chunks replaced): the new processing run schedules its own job.
    """
    target = get_primary_language()
    content_chunks = _content_chunks(session, doc)
    source_ids = [c.id for c in content_chunks]
    translated = [(c.page_number, llm_translate(c.content, target)) for c in content_chunks]
    session.refresh(doc)
    current_ids = [c.id for c in _content_chunks(session, doc)]
    if not source_ids or current_ids != source_ids or doc.translation_status != TranslationStatus.pending:
        log.info("Document %s changed during translation; result discarded", doc.id)
        return False
    next_index = _next_chunk_index(session, doc)
    for offset, (page_number, text) in enumerate(translated):
        session.add(
            Chunk(
                document_id=doc.id,
                chunk_index=next_index + offset,
                page_number=page_number,
                source=ChunkSource.translation,
                content=text,
            )
        )
    session.commit()
    return True


@register("translate_document")
def translate_document(session: Session, payload: dict) -> None:
    doc = session.get(Document, payload["document_id"])
    if doc is None:
        log.info("translate_document: document %s no longer exists", payload["document_id"])
        return
    if doc.translation_status == TranslationStatus.done and _has_chunks(
        session, doc, ChunkSource.translation
    ):
        return
    try:
        # a retry after an embedding error reuses the stored translation chunks
        if not _has_chunks(session, doc, ChunkSource.translation):
            if not _insert_translation_chunks(session, doc):
                return
        _embed_pending_chunks(session, doc)  # only translation chunks are still unembedded
        doc.translation_status = TranslationStatus.done
        session.commit()
    except Exception:
        session.rollback()
        doc.translation_status = (
            TranslationStatus.failed if is_final_attempt(payload) else TranslationStatus.pending
        )
        session.commit()
        raise
```

Note: the direct-call tests that start from `_german_doc` work because `process_document` already set `translation_status = "pending"`.

- [ ] **Step 5: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_pipeline.py tests/test_reprocess.py -v && uv run pytest -q`
Expected: PASS, full suite green. If `grep -n "_ensure_translation" -r backend/app backend/tests` prints anything, remove the leftover reference.

- [ ] **Step 6: Commit**

```bash
cd /opt/origami
git add backend/app/models/document.py backend/app/worker/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: run translation as its own retried translate_document job

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Documents API — `active_job` and cancelling queued jobs on re-process

**Files:**
- Modify: `backend/app/api/documents.py` (imports, `serialize`, `list_documents`, `reprocess_document`, new helpers)
- Test: `backend/tests/test_documents.py`, `backend/tests/test_reprocess.py`

**Interfaces:**
- Consumes: `JobStatus.cancelled` (Task 1); job types `process_document`, `translate_document` (Task 5).
- Produces:
  - `app.api.documents.active_jobs_for(session: Session, doc_ids: list[uuid.UUID]) -> dict[str, dict]` — key: document id string; value: `{"type": str, "attempts": int, "max_attempts": int, "run_at": str (ISO with +00:00), "last_error": str | None}`; one query.
  - `serialize(session, doc, active_jobs: dict[str, dict] | None = None) -> dict` — adds `"active_job"`; when `active_jobs` is `None` it queries for this one document. Existing callers (`scan.py`, `uploads.py`, `search.py`) need no change.
  - Re-process sets queued `process_document` / `translate_document` jobs of that document to `cancelled` in the same commit.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_documents.py` (add `from datetime import datetime, timezone` and `from app.models import Job, JobStatus` to the imports):

```python
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
```

Append to `backend/tests/test_reprocess.py` (add `JobStatus` to the `app.models` import):

```python
def test_reprocess_cancels_queued_jobs_of_this_document_only(auth_client, session):
    doc = _ready_doc(session, doc_type=DocType.pdf)
    other = _ready_doc(session, doc_type=DocType.pdf)
    session.add_all([
        Job(type="translate_document", payload={"document_id": str(doc.id)}, attempts=2),
        Job(type="translate_document", payload={"document_id": str(other.id)}),
        Job(type="translate_document", payload={"document_id": str(doc.id)}, status=JobStatus.running),
        Job(type="sweep_scan_sessions", payload={}),
    ])
    session.commit()

    assert auth_client.post(f"/api/documents/{doc.id}/reprocess", json={"ocr_languages": "ita"}).status_code == 200
    session.expire_all()
    jobs = session.exec(select(Job).order_by(Job.id)).all()
    assert [(j.type, j.status) for j in jobs] == [
        ("translate_document", JobStatus.cancelled),
        ("translate_document", JobStatus.queued),   # other document
        ("translate_document", JobStatus.running),  # left alone
        ("sweep_scan_sessions", JobStatus.queued),
        ("process_document", JobStatus.queued),     # the new run
    ]
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /opt/origami/backend && uv run pytest tests/test_documents.py tests/test_reprocess.py -v`
Expected: FAIL — `KeyError: 'active_job'`; the first job stays `queued`.

- [ ] **Step 3: Implement**

Read the current `backend/app/api/documents.py` first (the prerequisite plan added a `sort` parameter to `list_documents` and an AI-description reset to `reprocess_document`; keep both).

Add `JobStatus` to the `app.models` import. Add above `serialize`:

```python
DOCUMENT_JOB_TYPES = ("process_document", "translate_document")


def _utc_iso(value: datetime) -> str:
    # job timestamps are stored naive in UTC; an explicit offset stops browsers reading local time
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def active_jobs_for(session: Session, doc_ids: list[uuid.UUID]) -> dict[str, dict]:
    """Newest queued/running job per document, fetched in one query."""
    if not doc_ids:
        return {}
    jobs = session.exec(
        select(Job)
        .where(
            Job.status.in_([JobStatus.queued, JobStatus.running]),
            Job.payload["document_id"].astext.in_([str(i) for i in doc_ids]),
        )
        .order_by(Job.id.desc())
    ).all()
    active: dict[str, dict] = {}
    for job in jobs:
        active.setdefault(
            job.payload["document_id"],
            {
                "type": job.type,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "run_at": _utc_iso(job.run_at),
                "last_error": job.last_error,
            },
        )
    return active
```

Replace `serialize` with:

```python
def serialize(session: Session, doc: Document, active_jobs: dict[str, dict] | None = None) -> dict:
    if active_jobs is None:
        active_jobs = active_jobs_for(session, [doc.id])
    return {
        **doc.model_dump(),
        "tags": [t.model_dump() for t in doc_tags(session, doc)],
        "active_job": active_jobs.get(str(doc.id)),
    }
```

In `list_documents`, replace the final `return [serialize(session, d) for d in session.exec(query)]` with:

```python
    docs = list(session.exec(query))
    active_jobs = active_jobs_for(session, [d.id for d in docs])
    return [serialize(session, d, active_jobs) for d in docs]
```

Add after `_scan_session_with_pages`:

```python
def _cancel_queued_jobs(session: Session, doc: Document) -> None:
    """Re-process supersedes queued work; a running job finishes and its results get replaced."""
    for job in session.exec(
        select(Job).where(
            Job.type.in_(DOCUMENT_JOB_TYPES),
            Job.status == JobStatus.queued,
            Job.payload["document_id"].astext == str(doc.id),
        )
    ):
        job.status = JobStatus.cancelled
        job.updated_at = datetime.now(timezone.utc)
```

In `reprocess_document`, insert one line directly above the chunk-deletion loop `for chunk in session.exec(` (i.e. after the 409/422 checks and the `no_source` check, so a refused re-process changes nothing):

```python
    _cancel_queued_jobs(session, doc)
```

The cancellations commit together with the reset and the new job.

- [ ] **Step 4: Run, expect PASS**

Run: `cd /opt/origami/backend && uv run pytest tests/test_documents.py tests/test_reprocess.py -v && uv run pytest -q`
Expected: PASS, full suite green (`test_reprocess_scan_without_any_source_is_409` still passes: nothing is cancelled on a 409).

- [ ] **Step 5: Commit**

```bash
cd /opt/origami
git add backend/app/api/documents.py backend/tests/test_documents.py backend/tests/test_reprocess.py
git commit -m "feat: expose active_job on documents and cancel queued jobs on re-process

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend — retry labels, translation line, polling, card tooltip

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/lib/retry.ts`, `frontend/src/lib/retry.test.ts`
- Modify: `frontend/src/lib/translation.ts`, `frontend/src/lib/translation.test.ts`
- Create: `frontend/src/hooks/useNow.ts`, `frontend/src/hooks/useNow.test.ts`
- Modify: `frontend/src/pages/DocumentPage.tsx`, `frontend/src/components/DocumentCard.tsx`

**Interfaces:**
- Consumes: API field `active_job` (Task 6), `translation_status: "pending"` (Task 5).
- Produces:
  - Types `ActiveJob { type: string; attempts: number; max_attempts: number; run_at: string; last_error: string | null }`, `TranslationStatus = "pending" | "done" | "failed"`, `Document.active_job: ActiveJob | null`.
  - `retry.ts`: `relativeTime(target: Date, now: Date): string`, `retryLabel(job: ActiveJob, now: Date): string | null`, `errorHeadline(error: string | null): string`, `processingRetryMessage(doc: Pick<Document, "status" | "active_job">, now: Date): string | null`, `badgeTitle(doc: Pick<Document, "active_job" | "error_message">, now: Date): string | undefined`, `shouldPollDocument(doc: Pick<Document, "status" | "translation_status">): boolean`.
  - `translation.ts`: `translationNote(doc: Pick<Document, "translation_status" | "active_job">, now: Date): string | null`.
  - `useNow(intervalMs?: number): Date`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/retry.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  badgeTitle,
  errorHeadline,
  processingRetryMessage,
  relativeTime,
  retryLabel,
  shouldPollDocument,
} from "./retry";
import type { ActiveJob } from "./types";

const now = new Date("2026-10-03T10:00:00Z");
const job = (over: Partial<ActiveJob> = {}): ActiveJob => ({
  type: "process_document",
  attempts: 1,
  max_attempts: 5,
  run_at: "2026-10-03T10:00:30+00:00",
  last_error: "Traceback (most recent call last):\n  File \"x.py\"\nRuntimeError: provider down\n",
  ...over,
});

describe("relativeTime", () => {
  it("formats seconds, minutes and hours, past as now", () => {
    expect(relativeTime(new Date("2026-10-03T10:00:30Z"), now)).toBe("30 s");
    expect(relativeTime(new Date("2026-10-03T10:02:00Z"), now)).toBe("2 min");
    expect(relativeTime(new Date("2026-10-03T10:30:00Z"), now)).toBe("30 min");
    expect(relativeTime(new Date("2026-10-03T12:00:00Z"), now)).toBe("2 h");
    expect(relativeTime(new Date("2026-10-03T09:59:00Z"), now)).toBe("now");
    expect(relativeTime(new Date("garbage"), now)).toBe("now");
  });
});

describe("retryLabel", () => {
  it("is null before the first failure", () => {
    expect(retryLabel(job({ attempts: 0 }), now)).toBeNull();
  });
  it("names the next attempt and when it runs", () => {
    expect(retryLabel(job(), now)).toBe("attempt 2/5, next ≈ 30 s");
    expect(retryLabel(job({ attempts: 4, run_at: "2026-10-03T10:30:00+00:00" }), now)).toBe(
      "attempt 5/5, next ≈ 30 min",
    );
  });
});

describe("errorHeadline", () => {
  it("returns the exception line of a traceback", () => {
    expect(errorHeadline(job().last_error)).toBe("RuntimeError: provider down");
    expect(errorHeadline("plain message")).toBe("plain message");
    expect(errorHeadline(null)).toBe("");
  });
});

describe("processingRetryMessage", () => {
  it("describes a pending document whose processing is retrying", () => {
    expect(
      processingRetryMessage(
        { status: "pending", active_job: job({ attempts: 2, run_at: "2026-10-03T10:10:00+00:00" }) },
        now,
      ),
    ).toBe("Processing failed, retrying (attempt 3/5, next ≈ 10 min): RuntimeError: provider down");
  });
  it("omits the colon part when there is no error text", () => {
    expect(processingRetryMessage({ status: "pending", active_job: job({ last_error: null }) }, now)).toBe(
      "Processing failed, retrying (attempt 2/5, next ≈ 30 s)",
    );
  });
  it("is null otherwise", () => {
    expect(processingRetryMessage({ status: "processing", active_job: job() }, now)).toBeNull();
    expect(processingRetryMessage({ status: "pending", active_job: job({ attempts: 0 }) }, now)).toBeNull();
    expect(
      processingRetryMessage({ status: "pending", active_job: job({ type: "translate_document" }) }, now),
    ).toBeNull();
    expect(processingRetryMessage({ status: "pending", active_job: null }, now)).toBeNull();
  });
});

describe("badgeTitle", () => {
  it("prefers the retry label, then the error message", () => {
    expect(badgeTitle({ active_job: job(), error_message: "Retrying: x" }, now)).toBe("attempt 2/5, next ≈ 30 s");
    expect(badgeTitle({ active_job: job({ attempts: 0 }), error_message: null }, now)).toBeUndefined();
    expect(badgeTitle({ active_job: null, error_message: "boom" }, now)).toBe("boom");
    expect(badgeTitle({ active_job: null, error_message: null }, now)).toBeUndefined();
  });
});

describe("shouldPollDocument", () => {
  it("polls while processing or while a translation is pending", () => {
    expect(shouldPollDocument({ status: "pending", translation_status: null })).toBe(true);
    expect(shouldPollDocument({ status: "processing", translation_status: null })).toBe(true);
    expect(shouldPollDocument({ status: "ready", translation_status: "pending" })).toBe(true);
    expect(shouldPollDocument({ status: "ready", translation_status: "done" })).toBe(false);
    expect(shouldPollDocument({ status: "failed", translation_status: null })).toBe(false);
  });
});
```

Append to `frontend/src/lib/translation.test.ts` (extend the import to `import { languageLabel, textVariants, translationNote } from "./translation";` and add `import type { ActiveJob } from "./types";`):

```ts
describe("translationNote", () => {
  const now = new Date("2026-10-03T10:00:00Z");
  const translateJob = (attempts: number): ActiveJob => ({
    type: "translate_document",
    attempts,
    max_attempts: 5,
    run_at: "2026-10-03T10:02:00+00:00",
    last_error: null,
  });

  it("shows pending without retries", () => {
    expect(translationNote({ translation_status: "pending", active_job: translateJob(0) }, now)).toBe(
      "Translation pending…",
    );
    expect(translationNote({ translation_status: "pending", active_job: null }, now)).toBe("Translation pending…");
  });
  it("shows the retry label while retrying", () => {
    expect(translationNote({ translation_status: "pending", active_job: translateJob(1) }, now)).toBe(
      "Translation retrying (attempt 2/5, next ≈ 2 min)",
    );
  });
  it("ignores retries of other job types", () => {
    expect(
      translationNote(
        { translation_status: "pending", active_job: { ...translateJob(2), type: "process_document" } },
        now,
      ),
    ).toBe("Translation pending…");
  });
  it("tells that a notification was sent on failure", () => {
    expect(translationNote({ translation_status: "failed", active_job: null }, now)).toBe(
      "Translation failed — notification sent. Re-process to retry.",
    );
  });
  it("is null when done or not needed", () => {
    expect(translationNote({ translation_status: "done", active_job: null }, now)).toBeNull();
    expect(translationNote({ translation_status: null, active_job: null }, now)).toBeNull();
  });
});
```

Create `frontend/src/hooks/useNow.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useNow } from "./useNow";

describe("useNow", () => {
  afterEach(() => vi.useRealTimers());

  it("ticks on the given interval", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T10:00:00Z"));
    const { result } = renderHook(() => useNow(1000));
    expect(result.current.toISOString()).toBe("2026-10-03T10:00:00.000Z");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(result.current.toISOString()).toBe("2026-10-03T10:00:03.000Z");
  });
});
```

- [ ] **Step 2: Run, expect FAIL**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/retry.test.ts src/lib/translation.test.ts src/hooks/useNow.test.ts`
Expected: FAIL — cannot resolve `./retry`, `./useNow`; `translationNote` is not exported.

- [ ] **Step 3: Implement types and helpers**

In `frontend/src/lib/types.ts`, add above `export interface Document`:

```ts
export type TranslationStatus = "pending" | "done" | "failed";

export interface ActiveJob {
  type: string;
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
}
```

In `Document`, change `translation_status: "done" | "failed" | null;` to `translation_status: TranslationStatus | null;` and add after `tags: Tag[];`:

```ts
  active_job: ActiveJob | null;
```

In `DocumentText`, change `translation_status` the same way: `translation_status: TranslationStatus | null;`.

Create `frontend/src/lib/retry.ts`:

```ts
import type { ActiveJob, Document } from "./types";

export function relativeTime(target: Date, now: Date): string {
  const seconds = Math.round((target.getTime() - now.getTime()) / 1000);
  if (!Number.isFinite(seconds) || seconds <= 0) return "now";
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}

export function retryLabel(job: ActiveJob, now: Date): string | null {
  if (job.attempts === 0) return null;
  return `attempt ${job.attempts + 1}/${job.max_attempts}, next ≈ ${relativeTime(new Date(job.run_at), now)}`;
}

/** Last non-empty line: the "ExceptionType: message" line of a stored traceback. */
export function errorHeadline(error: string | null): string {
  const lines = (error ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines[lines.length - 1] ?? "";
}

export function processingRetryMessage(
  doc: Pick<Document, "status" | "active_job">,
  now: Date,
): string | null {
  const job = doc.active_job;
  if (doc.status !== "pending" || !job || job.type !== "process_document") return null;
  const label = retryLabel(job, now);
  if (!label) return null;
  const headline = errorHeadline(job.last_error);
  return `Processing failed, retrying (${label})${headline ? `: ${headline}` : ""}`;
}

export function badgeTitle(doc: Pick<Document, "active_job" | "error_message">, now: Date): string | undefined {
  const label = doc.active_job ? retryLabel(doc.active_job, now) : null;
  return label ?? doc.error_message ?? undefined;
}

export function shouldPollDocument(doc: Pick<Document, "status" | "translation_status">): boolean {
  return doc.status === "pending" || doc.status === "processing" || doc.translation_status === "pending";
}
```

Append to `frontend/src/lib/translation.ts` (and add `import { retryLabel } from "./retry";` at the top):

```ts
export function translationNote(doc: Pick<Document, "translation_status" | "active_job">, now: Date): string | null {
  if (doc.translation_status === "failed") return "Translation failed — notification sent. Re-process to retry.";
  if (doc.translation_status !== "pending") return null;
  const job = doc.active_job;
  const label = job && job.type === "translate_document" ? retryLabel(job, now) : null;
  return label ? `Translation retrying (${label})` : "Translation pending…";
}
```

Create `frontend/src/hooks/useNow.ts`:

```ts
import { useEffect, useState } from "react";

/** Current time, refreshed every `intervalMs` so relative labels ("next ≈ 2 min") stay current. */
export function useNow(intervalMs = 15000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
```

- [ ] **Step 4: Run the helper tests, expect PASS**

Run: `cd /opt/origami/frontend && npx vitest run src/lib/retry.test.ts src/lib/translation.test.ts src/hooks/useNow.test.ts`
Expected: PASS.

- [ ] **Step 5: Wire the document page**

Read the current `frontend/src/pages/DocumentPage.tsx` first (the prerequisite plan changed `Viewer`, the sidebar and the description field; keep those changes). Apply these targeted edits:

1. Imports — add:

```tsx
import { useNow } from "@/hooks/useNow";
import { processingRetryMessage, shouldPollDocument } from "@/lib/retry";
```

and extend the translation import to `import { languageLabel, textVariants, translationNote, type TextVariant } from "@/lib/translation";`.

2. In `TextView`, add as the first line of the function body (before `const variants = ...`):

```tsx
  const now = useNow();
  const note = translationNote(doc, now);
```

and replace the block

```tsx
        {data.translation_status === "failed" && (
          <span className="text-xs text-amber-700">Translation failed — re-process to retry.</span>
        )}
```

with

```tsx
        {note && <span className="text-xs text-amber-700">{note}</span>}
```

(`doc` is polled, so the note follows the document instead of the cached text response.)

3. In `DocumentPage`, replace the `refetchInterval` option of the `["document", id]` query with:

```tsx
    refetchInterval: (q) => (q.state.data && shouldPollDocument(q.state.data) ? 4000 : false),
```

4. Replace the effect commented `// re-process finished: refetch extracted/translated text` and the `lastStatus` ref declaration with:

```tsx
  const lastStatus = useRef<string | null>(null);
  const lastTranslation = useRef<string | null>(null);
```

```tsx
  useEffect(() => {
    if (!doc) return;
    // re-process finished, or the translation job finished: refetch extracted/translated text
    const processed = lastStatus.current && lastStatus.current !== "ready" && doc.status === "ready";
    const translated = lastTranslation.current === "pending" && doc.translation_status === "done";
    if (processed || translated) qc.invalidateQueries({ queryKey: ["document-text", doc.id] });
    lastStatus.current = doc.status;
    lastTranslation.current = doc.translation_status;
  }, [doc, qc]);
```

5. Add `const now = useNow();` next to the other hooks at the top of `DocumentPage` (before `if (!doc) return ...`, hooks must not follow the early return), and right after the `if (!doc) return <div ...>Loading…</div>;` line add:

```tsx
  const retryMessage = processingRetryMessage(doc, now);
```

6. Directly after the `{doc.status === "failed" && ( ... )}` red banner block, insert:

```tsx
        {retryMessage && (
          <div className="mb-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {retryMessage}
          </div>
        )}
```

- [ ] **Step 6: Wire the browse card**

In `frontend/src/components/DocumentCard.tsx`, add `import { badgeTitle } from "@/lib/retry";` and change the status badge's `title` prop from `title={doc.error_message ?? undefined}` to:

```tsx
title={badgeTitle(doc, new Date())}
```

(The browse list already polls every 4 s while a document is pending, so the tooltip refreshes; read the current file first — the prerequisite plan replaced the type icon, the badge line is unchanged.)

- [ ] **Step 7: Run the full frontend checks, expect PASS**

Run: `cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build`
Expected: all tests pass, lint clean, build succeeds. If `tsc` reports a test fixture literal typed as `Document` missing `active_job`, add `active_job: null` to that fixture.

- [ ] **Step 8: Commit**

```bash
cd /opt/origami
git add frontend/src/lib/types.ts frontend/src/lib/retry.ts frontend/src/lib/retry.test.ts \
  frontend/src/lib/translation.ts frontend/src/lib/translation.test.ts \
  frontend/src/hooks/useNow.ts frontend/src/hooks/useNow.test.ts \
  frontend/src/pages/DocumentPage.tsx frontend/src/components/DocumentCard.tsx
git commit -m "feat: show job retries and translation progress in the UI

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Full verification, deploy and manual checks

**Files:**
- No code changes expected. If a manual check finds a bug, fix it with a failing test first, in the task that owns the code, and commit separately.

**Interfaces:**
- Consumes: everything above.
- Produces: verified release; user restarts the service.

- [ ] **Step 1: Full suites**

Run:

```bash
cd /opt/origami/backend && uv run pytest -q
cd /opt/origami/frontend && npx vitest run && npm run lint && npm run build
```

Expected: all green; backend count = Task 1 baseline + the tests added here; frontend likewise. `git status` shows no unintended changes (only the user's `deploy/` files and `..env.swp` remain modified/untracked).

- [ ] **Step 2: Restart the service (human action — needs sudo)**

Ask the user to run: `! sudo systemctl restart origami` (the service applies migrations on start). Then verify: `cd /opt/origami/backend && uv run alembic current` prints `e5a1c7d93b20 (head)`, and `systemctl status origami --no-pager` is `active (running)`.

- [ ] **Step 3: Email setup (human action)**

Ask the user to put a Gmail **app password** in `/opt/origami/.env`: `SMTP_USER=<gmail address>`, `SMTP_APP_PASSWORD=<16-char app password>`, optionally `APP_BASE_URL=<http://host:port>`, then `! sudo systemctl restart origami`. Then run:

```bash
cd /opt/origami/backend && uv run python -m app.cli set-email <username> <email>
cd /opt/origami/backend && uv run python -m app.cli test-email
```

Expected: `Email of '<username>' set to <email>`, then `Test email sent to <email>`, and the message arrives with subject `[Origami] Test email`.

- [ ] **Step 4: Watch retries and the failure email (human action)**

1. Ask the user to set `LLM_API_KEY=invalid` in `.env` and restart the service.
2. Upload a small `.txt` document in the UI. Within a few seconds the document page shows `pending` and the amber banner `Processing failed, retrying (attempt 2/5, next ≈ 30 s): ...`; the browse card badge tooltip shows the retry label.
3. Fast-forward the waits instead of sitting through 43 minutes — run this four times, a few seconds apart, checking the banner's attempt number each time:

```bash
cd /opt/origami/backend && uv run python -c "
from sqlalchemy import text
from app.db import engine
with engine.begin() as c:
    print(c.execute(text(\"UPDATE jobs SET run_at = now() WHERE status = 'queued' AND type = 'process_document'\")).rowcount, 'job(s) moved')
"
```

4. After the 5th attempt: the document shows `failed` with the red banner, and an email `[Origami] Processing failed: <title>` arrives with `Attempts: 5/5`, the error and (if `APP_BASE_URL` is set) a working link.
5. Restore the real `LLM_API_KEY`, restart, press **Re-process** on the document → `ready`.

- [ ] **Step 5: Translation job (human action)**

Upload a short German text (e.g. `Sehr geehrte Damen und Herren, anbei die Rechnung für März.`). Expected: the document becomes `ready`, the Text tab shows `Translation pending…`, then (worker picks the `translate_document` job) the language toggle appears with the Italian translation without reloading the page. Searching an Italian word from the translation finds the document.

- [ ] **Step 6: Report**

Report the final test counts, the manual check results, and remind the user that the deploy files they had modified were left untouched.
