import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings, get_settings

# Keep the developer's ../.env out of the test run: Settings() should see only
# code defaults plus real environment variables (monkeypatch.setenv still works).
# pydantic-settings reads model_config["env_file"] at instantiation, so this must
# run before anything caches a Settings instance (hence before importing app.main).
Settings.model_config["env_file"] = None
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")  # litellm loads ../.env into os.environ on import in DEV mode
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 32)  # >=32 bytes: avoids InsecureKeyLengthWarning
get_settings.cache_clear()

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402

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


ADMIN_URL = "postgresql+psycopg://origami:origami@localhost:5432/postgres"
TEST_URL = "postgresql+psycopg://origami:origami@localhost:5432/origami_test"


@pytest.fixture(scope="session")
def engine():
    admin = sa_create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS origami_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE origami_test"))
    admin.dispose()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.upgrade(cfg, "head")

    eng = create_engine(TEST_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()
    tables = ", ".join(t.name for t in SQLModel.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def user(session):
    from app.models import User
    from app.services.auth import hash_password

    u = User(username="test", password_hash=hash_password("testpass"))
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


@pytest.fixture
def auth_client(client, user):
    from app.services.auth import create_access_token

    client.headers["Authorization"] = f"Bearer {create_access_token(user.id)}"
    return client


@pytest.fixture
def storage(tmp_path):
    from app.services.storage import Storage, get_storage as real_get_storage
    from app.main import app as main_app

    s = Storage(tmp_path)
    main_app.dependency_overrides[real_get_storage] = lambda: s
    yield s
    main_app.dependency_overrides.pop(real_get_storage, None)


@pytest.fixture
def fake_scanner(client):
    from app.main import app as main_app
    from app.services.scanner import FakeScannerBackend, get_scanner

    backend = FakeScannerBackend()
    main_app.dependency_overrides[get_scanner] = lambda: backend
    yield backend
    main_app.dependency_overrides.pop(get_scanner, None)


@pytest.fixture
def llm_stub(monkeypatch):
    """Stub the LLM mock boundary (calls["language"] is what language detection returns for non-empty text): app.services.llm (the other one is smtplib.SMTP). select_documents is scripted via calls["select_ids"] / calls["select_error"]."""
    calls = {
        "embed": [], "describe": [], "detect": [], "translate": [], "language": "it", "translate_error": None,
        "select": [], "select_ids": None, "select_error": None,
    }

    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1] * 1536 for _ in texts]

    def fake_describe(text=None, image_path=None):
        calls["describe"].append({"text": text, "image_path": image_path})
        return "Descrizione generata."

    def fake_detect_language(text):
        calls["detect"].append(text)
        return calls["language"] if text and text.strip() else None

    def fake_translate(text, target_language):
        calls["translate"].append((text, target_language))
        if calls["translate_error"] is not None:
            raise calls["translate_error"]
        return f"[{target_language}] {text}"

    monkeypatch.setattr("app.worker.pipeline.llm_embed", fake_embed)
    monkeypatch.setattr("app.worker.pipeline.llm_describe", fake_describe)
    monkeypatch.setattr("app.worker.pipeline.detect_language", fake_detect_language)
    monkeypatch.setattr("app.worker.pipeline.llm_translate", fake_translate)

    def fake_select_documents(question, history, candidates):
        calls["select"].append(
            {"question": question, "history": [dict(m) for m in history],
             "candidates": [dict(c) for c in candidates]}
        )
        if calls["select_error"] is not None:
            raise calls["select_error"]
        return list(calls["select_ids"] or [])

    monkeypatch.setattr("app.services.rag.llm_select_documents", fake_select_documents)
    return calls


@pytest.fixture
def break_soffice(monkeypatch):
    """Call to point SOFFICE_PATH elsewhere (default /bin/false, a real binary that exits 1)."""
    from app.config import get_settings

    def _break(path: str = "/bin/false") -> None:
        monkeypatch.setenv("SOFFICE_PATH", path)
        get_settings.cache_clear()

    yield _break
    get_settings.cache_clear()  # monkeypatch restores the env afterwards; next call re-reads it


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
