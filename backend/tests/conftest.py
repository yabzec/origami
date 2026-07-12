import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.main import app

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
