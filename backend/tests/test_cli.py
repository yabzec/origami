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


def test_list_models_prints_sorted_table(monkeypatch, capsys):
    from app.services import llm

    monkeypatch.setattr(llm, "model_provider", lambda: "groq")
    monkeypatch.setattr(
        llm,
        "list_models",
        lambda: [
            {"id": "whisper-large-v3", "owner": None, "context_window": None, "active": None},
            {"id": "openai/gpt-oss-120b", "owner": "OpenAI", "context_window": 131072, "active": True},
            {"id": "llama-old", "owner": "Meta", "context_window": 8192, "active": False},
        ],
    )
    run_cli(monkeypatch, "list-models")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Provider: groq"
    assert lines[1].split() == ["ID", "OWNER", "CONTEXT", "ACTIVE"]
    assert [line.split()[0] for line in lines[2:]] == ["llama-old", "openai/gpt-oss-120b", "whisper-large-v3"]
    assert lines[3].split() == ["openai/gpt-oss-120b", "OpenAI", "131072", "yes"]
    assert lines[2].split() == ["llama-old", "Meta", "8192", "no"]
    assert lines[4].split() == ["whisper-large-v3"]


def test_list_models_error_exits_1(monkeypatch, capsys):
    from app.services import llm

    def unsupported():
        raise ValueError("Model listing not supported for provider 'ollama'")

    monkeypatch.setattr(llm, "model_provider", lambda: "ollama")
    monkeypatch.setattr(llm, "list_models", unsupported)
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "list-models")
    assert exc.value.code == 1
    assert "Model listing not supported for provider 'ollama'" in capsys.readouterr().out


def test_check_storage_ok(monkeypatch, capsys, tmp_path, cli_engine):
    from app.services.storage import Storage

    store = Storage(tmp_path / "storage")
    monkeypatch.setattr("app.services.storage.get_storage", lambda: store)
    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, "check-storage")
    assert exc.value.code == 0
    assert "Storage OK" in capsys.readouterr().out
