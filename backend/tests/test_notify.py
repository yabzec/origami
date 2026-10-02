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
