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
