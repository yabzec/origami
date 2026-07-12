# Origami Phase 1 — Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Working FastAPI backend with Postgres+pgvector in Docker, full schema, JWT auth (CLI-created user), CRUD for folders/tags/documents, flat file storage, and a durable Postgres-backed job queue with a worker process.

**Architecture:** Three runtime processes: Postgres 17 + pgvector (Docker Compose, only containerized piece), FastAPI on host, worker (`python -m app.worker`) consuming a `jobs` table via `FOR UPDATE SKIP LOCKED`. Sync SQLAlchemy/SQLModel throughout (single user; simplicity over async). Alembic owns the schema.

**Tech Stack:** Python 3.12+, uv, FastAPI, SQLModel, Alembic, psycopg 3, pgvector, pydantic-settings, PyJWT, bcrypt, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-07-12-origami-dms-design.md`

## Global Constraints

- Python 3.12+; dependencies managed by `uv` in `backend/`.
- The database is NEVER mocked in tests — tests run against a real Postgres (dedicated `origami_test` database on the compose instance).
- All env vars read via `app/config.py` (pydantic-settings, `.env` file): `DATABASE_URL`, `STORAGE_PATH`, `JWT_SECRET`, `LLM_MODEL`, `VISION_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIM=1536`, `GEMINI_API_KEY`, `DEFAULT_OCR_LANGUAGES=ita+eng`.
- Physical storage: single flat dir `$STORAGE_PATH/files/`, filenames keyed by document id. Scan temp pages under `$STORAGE_PATH/tmp/scan_sessions/{session_id}/`. DB stores relative paths only.
- Folders are virtual (DB-only). Exactly one folder per document (nullable FK = root). Tags M:N.
- Enum-like columns stored as `VARCHAR` validated by Python `StrEnum` (avoids Alembic enum-migration pain).
- All API routes under `/api/`; everything except `POST /api/auth/login` and `GET /api/health` requires JWT bearer.
- Error responses: `{"error": {"code": str, "message": str, "detail": ...}}`.
- Commit after every green test cycle. Conventional Commits format.
- Deviation from spec noted: `bcrypt` lib directly instead of passlib (passlib is unmaintained and breaks with bcrypt>=4.1).

---

### Task 1: Scaffold, Docker Compose, config

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `.gitignore`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/main.py`
- Create: `backend/tests/__init__.py`, `backend/tests/test_health.py`

**Interfaces:**
- Produces: `app.config.Settings`, `app.config.get_settings()` (cached), FastAPI instance `app.main.app`, `GET /api/health` → `{"status": "ok"}`.

- [ ] **Step 1: Create repo scaffold and Docker Compose**

`.gitignore` (repo root):

```gitignore
__pycache__/
*.pyc
.venv/
.env
node_modules/
dist/
.pytest_cache/
.ruff_cache/
storage/
```

`docker-compose.yml` (repo root):

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    restart: unless-stopped
    environment:
      POSTGRES_USER: origami
      POSTGRES_PASSWORD: origami
      POSTGRES_DB: origami
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U origami"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
```

`.env.example` (repo root):

```env
DATABASE_URL=postgresql+psycopg://origami:origami@localhost:5432/origami
STORAGE_PATH=./storage
JWT_SECRET=change-me
LLM_MODEL=gemini/gemini-2.5-flash
VISION_MODEL=gemini/gemini-2.5-flash
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_DIM=1536
GEMINI_API_KEY=
DEFAULT_OCR_LANGUAGES=ita+eng
```

Copy it: `cp .env.example .env`

- [ ] **Step 2: Backend project**

`backend/pyproject.toml`:

```toml
[project]
name = "origami-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlmodel>=0.0.22",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "pgvector>=0.3",
    "pydantic-settings>=2.4",
    "pyjwt>=2.9",
    "bcrypt>=4.2",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Run: `cd backend && uv sync`

- [ ] **Step 3: Write failing test**

`backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: FAIL (ModuleNotFoundError: app.main)

- [ ] **Step 5: Implement config and app**

`backend/app/__init__.py`: empty file.

`backend/app/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://origami:origami@localhost:5432/origami"
    storage_path: Path = Path("./storage")
    jwt_secret: str = "dev-secret"
    jwt_expire_days: int = 30
    llm_model: str = "gemini/gemini-2.5-flash"
    vision_model: str = "gemini/gemini-2.5-flash"
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 1536
    gemini_api_key: str = ""
    default_ocr_languages: str = "ita+eng"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="Origami")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 7: Start the database and verify**

Run: `docker compose up -d db && docker compose ps`
Expected: db service healthy.

- [ ] **Step 8: Commit**

```bash
git add .gitignore docker-compose.yml .env.example backend/
git commit -m "feat: scaffold backend, docker compose postgres+pgvector, config"
```

---

### Task 2: Database models, Alembic migration, test DB fixtures

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models/__init__.py`, `backend/app/models/user.py`, `backend/app/models/folder.py`, `backend/app/models/tag.py`, `backend/app/models/document.py`, `backend/app/models/chunk.py`, `backend/app/models/job.py`, `backend/app/models/scan.py`
- Create: `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/<generated>_initial_schema.py`
- Create: `backend/tests/conftest.py`, `backend/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `app.db.engine`, `app.db.get_session()` (FastAPI dependency yielding `sqlmodel.Session`).
  - Models: `User(id, username, password_hash, created_at)`, `Folder(id, name, parent_id, created_at)`, `Tag(id, name, color)`, `Document(id: UUID, title, description, summary, folder_id, doc_type, ocr_languages, status, error_message, original_filename, file_path, page_count, file_size, created_at, updated_at)`, `DocumentTag(document_id, tag_id)`, `Chunk(id, document_id, chunk_index, page_number, source, content, embedding)`, `Job(id, type, payload, status, attempts, max_attempts, run_at, last_error, created_at, updated_at)`, `ScanSession(id, status, ocr_languages, created_at)`, `ScanPage(id, session_id, page_number, image_path)`.
  - StrEnums: `DocType(scan|pdf|text|image|video)`, `DocStatus(pending|processing|ready|failed)`, `ChunkSource(content|summary|metadata)`, `JobStatus(queued|running|done|failed)`, `ScanSessionStatus(active|compiling|done|cancelled)`.
  - Test fixtures: `engine` (session-scoped, migrated `origami_test` DB), `session` (function-scoped, truncates after each test), `client` (TestClient with `get_session` override).

- [ ] **Step 1: Write failing test**

`backend/tests/test_schema.py`:

```python
from sqlalchemy import inspect, text


def test_all_tables_exist(engine):
    tables = set(inspect(engine).get_table_names())
    expected = {
        "users", "folders", "tags", "documents", "document_tags",
        "chunks", "jobs", "scan_sessions", "scan_pages",
    }
    assert expected <= tables


def test_chunks_has_vector_and_tsv(engine):
    with engine.connect() as conn:
        cols = {
            r[0]: r[1]
            for r in conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'chunks'"
            ))
        }
    assert cols["embedding"] == "USER-DEFINED"  # vector
    assert cols["content_tsv"] == "tsvector"
```

- [ ] **Step 2: Write the DB module and models**

`backend/app/db.py`:

```python
from sqlmodel import Session, create_engine

from app.config import get_settings

engine = create_engine(get_settings().database_url)


def get_session():
    with Session(engine) as session:
        yield session
```

`backend/app/models/user.py`:

```python
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)
```

`backend/app/models/folder.py`:

```python
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class Folder(SQLModel, table=True):
    __tablename__ = "folders"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    parent_id: int | None = Field(default=None, foreign_key="folders.id")
    created_at: datetime = Field(default_factory=utcnow)
```

`backend/app/models/tag.py`:

```python
from sqlmodel import Field, SQLModel


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    color: str = "#888888"
```

`backend/app/models/document.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class DocType(StrEnum):
    scan = "scan"
    pdf = "pdf"
    text = "text"
    image = "image"
    video = "video"


class DocStatus(StrEnum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str
    description: str = ""
    summary: str | None = None
    folder_id: int | None = Field(default=None, foreign_key="folders.id")
    doc_type: str  # DocType
    ocr_languages: str = "ita+eng"
    status: str = DocStatus.pending  # DocStatus
    error_message: str | None = None
    original_filename: str | None = None
    file_path: str | None = None  # relative to STORAGE_PATH
    page_count: int | None = None
    file_size: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DocumentTag(SQLModel, table=True):
    __tablename__ = "document_tags"

    document_id: uuid.UUID = Field(foreign_key="documents.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)
```

`backend/app/models/chunk.py` (note: `content_tsv` is a generated column added in the migration only — deliberately absent from the model):

```python
import uuid
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

EMBEDDING_DIM = 1536


class ChunkSource(StrEnum):
    content = "content"
    summary = "summary"
    metadata = "metadata"


class Chunk(SQLModel, table=True):
    __tablename__ = "chunks"

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="documents.id", index=True)
    chunk_index: int
    page_number: int | None = None
    source: str = ChunkSource.content  # ChunkSource
    content: str
    embedding: Any = Field(default=None, sa_column=Column(Vector(EMBEDDING_DIM)))
```

`backend/app/models/job.py`:

```python
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    status: str = Field(default=JobStatus.queued, index=True)
    attempts: int = 0
    max_attempts: int = 3
    run_at: datetime = Field(default_factory=utcnow)
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
```

`backend/app/models/scan.py`:

```python
from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class ScanSessionStatus(StrEnum):
    active = "active"
    compiling = "compiling"
    done = "done"
    cancelled = "cancelled"


class ScanSession(SQLModel, table=True):
    __tablename__ = "scan_sessions"

    id: int | None = Field(default=None, primary_key=True)
    status: str = ScanSessionStatus.active
    ocr_languages: str = "ita+eng"
    created_at: datetime = Field(default_factory=utcnow)


class ScanPage(SQLModel, table=True):
    __tablename__ = "scan_pages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="scan_sessions.id", index=True)
    page_number: int
    image_path: str
```

`backend/app/models/__init__.py`:

```python
from app.models.chunk import Chunk, ChunkSource
from app.models.document import Document, DocumentTag, DocStatus, DocType
from app.models.folder import Folder
from app.models.job import Job, JobStatus
from app.models.scan import ScanPage, ScanSession, ScanSessionStatus
from app.models.tag import Tag
from app.models.user import User

__all__ = [
    "Chunk", "ChunkSource", "Document", "DocumentTag", "DocStatus", "DocType",
    "Folder", "Job", "JobStatus", "ScanPage", "ScanSession",
    "ScanSessionStatus", "Tag", "User",
]
```

- [ ] **Step 3: Initialize Alembic**

Run: `cd backend && uv run alembic init alembic`

Edit `backend/alembic.ini`: leave `sqlalchemy.url` blank (env.py sets it).

Replace the top of `backend/alembic/env.py` config section with:

```python
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers all tables on SQLModel.metadata)
from app.config import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = SQLModel.metadata
```

In `backend/alembic/env.py`, keep the standard `run_migrations_offline`/`run_migrations_online` bodies, passing `target_metadata=target_metadata`.

Edit `backend/alembic/script.py.mako` — add to the imports block so pgvector types render:

```python
import pgvector.sqlalchemy
import sqlmodel
```

- [ ] **Step 4: Generate the initial migration and add manual DDL**

Run: `cd backend && uv run alembic revision --autogenerate -m "initial schema"`

Open the generated file in `backend/alembic/versions/`. At the **top of `upgrade()`**, before any `create_table`, add:

```python
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
```

At the **bottom of `upgrade()`**, after all `create_table` calls, add:

```python
    # tsvector generated column + indexes ('simple' config: mixed ita/eng corpus)
    op.execute(
        "ALTER TABLE chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED"
    )
    op.execute("CREATE INDEX ix_chunks_content_tsv ON chunks USING gin (content_tsv)")
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "ALTER TABLE folders ADD CONSTRAINT uq_folders_parent_name "
        "UNIQUE NULLS NOT DISTINCT (parent_id, name)"
    )
    op.execute(
        "ALTER TABLE chunks DROP CONSTRAINT chunks_document_id_fkey, "
        "ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) "
        "REFERENCES documents(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE document_tags DROP CONSTRAINT document_tags_document_id_fkey, "
        "ADD CONSTRAINT document_tags_document_id_fkey FOREIGN KEY (document_id) "
        "REFERENCES documents(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE document_tags DROP CONSTRAINT document_tags_tag_id_fkey, "
        "ADD CONSTRAINT document_tags_tag_id_fkey FOREIGN KEY (tag_id) "
        "REFERENCES tags(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE scan_pages DROP CONSTRAINT scan_pages_session_id_fkey, "
        "ADD CONSTRAINT scan_pages_session_id_fkey FOREIGN KEY (session_id) "
        "REFERENCES scan_sessions(id) ON DELETE CASCADE"
    )
```

In `downgrade()` add `op.execute("DROP EXTENSION IF EXISTS vector CASCADE")` as the last line.

Verify the autogenerated part contains all 9 tables; if the `embedding` column rendered without import errors the mako edit worked.

Run against the dev DB: `cd backend && uv run alembic upgrade head`
Expected: no errors.

- [ ] **Step 5: Test fixtures**

`backend/tests/conftest.py`:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_schema.py -v`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "feat: database schema, models, alembic migration, test fixtures"
```

---

### Task 3: Auth — JWT login, current-user dependency, create-user CLI

**Files:**
- Create: `backend/app/services/__init__.py`, `backend/app/services/auth.py`
- Create: `backend/app/api/__init__.py`, `backend/app/api/deps.py`, `backend/app/api/auth.py`
- Create: `backend/app/cli.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/tests/conftest.py` (add `user` + `auth_client` fixtures)

**Interfaces:**
- Consumes: `User` model, `get_session`, `Settings`.
- Produces:
  - `app.services.auth`: `hash_password(str) -> str`, `verify_password(plain: str, hashed: str) -> bool`, `create_access_token(user_id: int) -> str`, `decode_token(token: str) -> dict` (raises `jwt.InvalidTokenError`).
  - `app.api.deps.get_current_user` FastAPI dependency → `User` (401 on missing/bad token, error code `unauthorized`).
  - `app.api.deps.api_error(status, code, message)` → `HTTPException` with `{"error": {...}}` detail shape used by ALL routers.
  - `POST /api/auth/login` `{username, password}` → `{"access_token": str, "token_type": "bearer"}`; 401 `invalid_credentials`.
  - CLI: `uv run python -m app.cli create-user <username>` (prompts password).
  - Test fixtures: `user` (creates `test`/`testpass` user), `auth_client` (TestClient with valid bearer header).

- [ ] **Step 1: Write failing tests**

`backend/tests/test_auth.py`:

```python
from app.models import User
from app.services.auth import hash_password


def make_user(session, username="test", password="testpass"):
    u = User(username=username, password_hash=hash_password(password))
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_login_ok(client, session):
    make_user(session)
    resp = client.post("/api/auth/login", json={"username": "test", "password": "testpass"})
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"
    assert resp.json()["access_token"]


def test_login_wrong_password(client, session):
    make_user(session)
    resp = client.post("/api/auth/login", json={"username": "test", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "invalid_credentials"


def test_protected_route_requires_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_token(client, session):
    make_user(session)
    token = client.post(
        "/api/auth/login", json={"username": "test", "password": "testpass"}
    ).json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/services/__init__.py`: empty.

`backend/app/services/auth.py`:

```python
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
```

`backend/app/api/__init__.py`: empty.

`backend/app/api/deps.py`:

```python
import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.services.auth import decode_token

bearer = HTTPBearer(auto_error=False)


def api_error(status: int, code: str, message: str, detail=None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "detail": detail}},
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: Session = Depends(get_session),
) -> User:
    if creds is None:
        raise api_error(401, "unauthorized", "Missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except pyjwt.InvalidTokenError:
        raise api_error(401, "unauthorized", "Invalid or expired token")
    user = session.get(User, int(payload["sub"]))
    if user is None:
        raise api_error(401, "unauthorized", "Unknown user")
    return user
```

`backend/app/api/auth.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import User
from app.services.auth import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, session: Session = Depends(get_session)) -> dict:
    user = session.exec(select(User).where(User.username == body.username)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise api_error(401, "invalid_credentials", "Wrong username or password")
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "username": user.username}
```

`backend/app/cli.py`:

```python
import argparse
import getpass

from sqlmodel import Session, select

from app.db import engine
from app.models import User
from app.services.auth import hash_password


def create_user(username: str) -> None:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    with Session(engine) as session:
        if session.exec(select(User).where(User.username == username)).first():
            raise SystemExit(f"User {username!r} already exists")
        session.add(User(username=username, password_hash=hash_password(password)))
        session.commit()
    print(f"User {username!r} created")


def main() -> None:
    parser = argparse.ArgumentParser(prog="origami")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    args = parser.parse_args()
    if args.command == "create-user":
        create_user(args.username)


if __name__ == "__main__":
    main()
```

Update `backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api import auth

app = FastAPI(title="Origami")
app.include_router(auth.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_auth.py -v`
Expected: 4 PASS

- [ ] **Step 5: Add shared auth fixtures**

Append to `backend/tests/conftest.py`:

```python
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
```

Run: `cd backend && uv run pytest -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "feat: JWT auth, login endpoint, create-user CLI"
```

---

### Task 4: Folders CRUD

**Files:**
- Create: `backend/app/api/folders.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/tests/test_folders.py`

**Interfaces:**
- Consumes: `get_current_user`, `api_error`, `Folder`, `Document`.
- Produces REST API (all require auth):
  - `POST /api/folders` `{name, parent_id?}` → 201 folder; 409 `duplicate_folder` on same-name sibling; 404 `not_found` on bad parent.
  - `GET /api/folders` → flat list `[{id, name, parent_id, created_at}]` (frontend builds the tree).
  - `PATCH /api/folders/{id}` `{name?, parent_id?}` → folder; 409 `folder_cycle` if move creates a cycle (moving under itself/descendant); 409 `duplicate_folder`.
  - `DELETE /api/folders/{id}` → 204; 409 `folder_not_empty` if it has subfolders or documents.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_folders.py`:

```python
def test_create_and_list(auth_client):
    resp = auth_client.post("/api/folders", json={"name": "Bills"})
    assert resp.status_code == 201
    root_id = resp.json()["id"]
    resp = auth_client.post("/api/folders", json={"name": "2026", "parent_id": root_id})
    assert resp.status_code == 201
    items = auth_client.get("/api/folders").json()
    assert {f["name"] for f in items} == {"Bills", "2026"}


def test_duplicate_sibling_name_rejected(auth_client):
    auth_client.post("/api/folders", json={"name": "Bills"})
    resp = auth_client.post("/api/folders", json={"name": "Bills"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "duplicate_folder"


def test_move_cycle_rejected(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    b = auth_client.post("/api/folders", json={"name": "B", "parent_id": a}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"parent_id": b})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "folder_cycle"


def test_rename(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    resp = auth_client.patch(f"/api/folders/{a}", json={"name": "Archive"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Archive"


def test_delete_non_empty_rejected(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    auth_client.post("/api/folders", json={"name": "B", "parent_id": a})
    resp = auth_client.delete(f"/api/folders/{a}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "folder_not_empty"


def test_delete_empty(auth_client):
    a = auth_client.post("/api/folders", json={"name": "A"}).json()["id"]
    assert auth_client.delete(f"/api/folders/{a}").status_code == 204


def test_requires_auth(client):
    assert client.get("/api/folders").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_folders.py -v`
Expected: FAIL (404s — router missing)

- [ ] **Step 3: Implement**

`backend/app/api/folders.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Document, Folder

router = APIRouter(
    prefix="/api/folders", tags=["folders"], dependencies=[Depends(get_current_user)]
)


class FolderCreate(BaseModel):
    name: str
    parent_id: int | None = None


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    model_config = {"json_schema_extra": {"note": "parent_id explicit null = move to root"}}


def get_folder_or_404(session: Session, folder_id: int) -> Folder:
    folder = session.get(Folder, folder_id)
    if folder is None:
        raise api_error(404, "not_found", f"Folder {folder_id} not found")
    return folder


def is_descendant(session: Session, candidate_id: int, ancestor_id: int) -> bool:
    """True if candidate_id is ancestor_id or lies in its subtree."""
    current: int | None = candidate_id
    while current is not None:
        if current == ancestor_id:
            return True
        current = session.get(Folder, current).parent_id
    return False


@router.post("", status_code=201)
def create_folder(body: FolderCreate, session: Session = Depends(get_session)) -> Folder:
    if body.parent_id is not None:
        get_folder_or_404(session, body.parent_id)
    folder = Folder(name=body.name, parent_id=body.parent_id)
    session.add(folder)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    session.refresh(folder)
    return folder


@router.get("")
def list_folders(session: Session = Depends(get_session)) -> list[Folder]:
    return list(session.exec(select(Folder)))


@router.patch("/{folder_id}")
def update_folder(
    folder_id: int, body: FolderPatch, session: Session = Depends(get_session)
) -> Folder:
    folder = get_folder_or_404(session, folder_id)
    fields = body.model_dump(exclude_unset=True)
    if "parent_id" in fields and fields["parent_id"] is not None:
        get_folder_or_404(session, fields["parent_id"])
        if is_descendant(session, fields["parent_id"], folder_id):
            raise api_error(409, "folder_cycle", "Cannot move a folder under itself")
    for key, value in fields.items():
        setattr(folder, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_folder", "Sibling folder with same name exists")
    session.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=204)
def delete_folder(folder_id: int, session: Session = Depends(get_session)) -> None:
    folder = get_folder_or_404(session, folder_id)
    has_children = session.exec(
        select(Folder).where(Folder.parent_id == folder_id)
    ).first()
    has_documents = session.exec(
        select(Document).where(Document.folder_id == folder_id)
    ).first()
    if has_children or has_documents:
        raise api_error(409, "folder_not_empty", "Folder contains items")
    session.delete(folder)
    session.commit()
```

In `backend/app/main.py` add:

```python
from app.api import auth, folders

app.include_router(auth.router)
app.include_router(folders.router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_folders.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat: folders CRUD with cycle and duplicate protection"
```

---

### Task 5: Tags CRUD

**Files:**
- Create: `backend/app/api/tags.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/tests/test_tags.py`

**Interfaces:**
- Consumes: `get_current_user`, `api_error`, `Tag`.
- Produces (all require auth):
  - `POST /api/tags` `{name, color?}` → 201 tag; 409 `duplicate_tag`.
  - `GET /api/tags` → list.
  - `PATCH /api/tags/{id}` `{name?, color?}` → tag.
  - `DELETE /api/tags/{id}` → 204 (join rows cascade).

- [ ] **Step 1: Write failing tests**

`backend/tests/test_tags.py`:

```python
def test_create_list_update_delete(auth_client):
    resp = auth_client.post("/api/tags", json={"name": "fiscale", "color": "#ff0000"})
    assert resp.status_code == 201
    tag_id = resp.json()["id"]

    assert auth_client.get("/api/tags").json()[0]["name"] == "fiscale"

    resp = auth_client.patch(f"/api/tags/{tag_id}", json={"color": "#00ff00"})
    assert resp.json()["color"] == "#00ff00"

    assert auth_client.delete(f"/api/tags/{tag_id}").status_code == 204
    assert auth_client.get("/api/tags").json() == []


def test_duplicate_name_rejected(auth_client):
    auth_client.post("/api/tags", json={"name": "casa"})
    resp = auth_client.post("/api/tags", json={"name": "casa"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "duplicate_tag"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_tags.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`backend/app/api/tags.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Tag

router = APIRouter(
    prefix="/api/tags", tags=["tags"], dependencies=[Depends(get_current_user)]
)


class TagCreate(BaseModel):
    name: str
    color: str = "#888888"


class TagPatch(BaseModel):
    name: str | None = None
    color: str | None = None


@router.post("", status_code=201)
def create_tag(body: TagCreate, session: Session = Depends(get_session)) -> Tag:
    tag = Tag(name=body.name, color=body.color)
    session.add(tag)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_tag", "Tag with same name exists")
    session.refresh(tag)
    return tag


@router.get("")
def list_tags(session: Session = Depends(get_session)) -> list[Tag]:
    return list(session.exec(select(Tag)))


@router.patch("/{tag_id}")
def update_tag(tag_id: int, body: TagPatch, session: Session = Depends(get_session)) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise api_error(404, "not_found", f"Tag {tag_id} not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(tag, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise api_error(409, "duplicate_tag", "Tag with same name exists")
    session.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: int, session: Session = Depends(get_session)) -> None:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise api_error(404, "not_found", f"Tag {tag_id} not found")
    session.delete(tag)
    session.commit()
```

In `backend/app/main.py` add `tags` to the import and `app.include_router(tags.router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_tags.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat: tags CRUD"
```

---

### Task 6: Storage service + Documents metadata CRUD

**Files:**
- Create: `backend/app/services/storage.py`
- Create: `backend/app/api/documents.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/tests/test_storage.py`, `backend/tests/test_documents.py`
- Modify: `backend/tests/conftest.py` (add `storage` fixture with tmp_path override)

**Interfaces:**
- Consumes: models, `get_current_user`, `api_error`.
- Produces:
  - `app.services.storage.Storage(root: Path)` with: `files_dir: Path` (property, `root/files`, auto-created), `tmp_scans_dir: Path` (property, `root/tmp/scan_sessions`, auto-created), `abs_path(rel: str) -> Path`, `store_file(document_id: UUID, ext: str, data: bytes) -> tuple[str, int]` (returns `(relative_path, size)`; relative like `files/{id}.pdf`), `delete_document_file(rel: str | None) -> None` (missing file is not an error), `scan_session_dir(session_id: int) -> Path` (auto-created), `remove_scan_session_dir(session_id: int) -> None`.
  - `app.services.storage.get_storage() -> Storage` FastAPI-friendly factory reading `Settings.storage_path` (NOT cached — tests override settings).
  - Documents API (all require auth):
    - `GET /api/documents?folder_id=&tag_id=&doc_type=&status=` → list of `{id, title, description, summary, folder_id, doc_type, ocr_languages, status, error_message, original_filename, file_path, page_count, file_size, created_at, updated_at, tags: [Tag]}`.
    - `GET /api/documents/{id}` → same shape; 404 `not_found`.
    - `PATCH /api/documents/{id}` `{title?, description?, folder_id?, tag_ids?}` → updated document (tag_ids replaces the set; folder validated).
    - `DELETE /api/documents/{id}` → 204; removes the physical file and cascades chunks/tag joins.
    - Document creation happens in Phase 2 (scan compile / upload) — no POST here.

- [ ] **Step 1: Write failing storage tests**

`backend/tests/test_storage.py`:

```python
import uuid

from app.services.storage import Storage


def test_store_and_delete_file(tmp_path):
    storage = Storage(tmp_path)
    doc_id = uuid.uuid4()
    rel, size = storage.store_file(doc_id, ".pdf", b"%PDF-fake")
    assert rel == f"files/{doc_id}.pdf"
    assert size == 9
    assert storage.abs_path(rel).read_bytes() == b"%PDF-fake"

    storage.delete_document_file(rel)
    assert not storage.abs_path(rel).exists()
    storage.delete_document_file(rel)  # idempotent


def test_scan_session_dirs(tmp_path):
    storage = Storage(tmp_path)
    d = storage.scan_session_dir(7)
    assert d.is_dir()
    (d / "page_001.png").write_bytes(b"png")
    storage.remove_scan_session_dir(7)
    assert not d.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_storage.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement storage**

`backend/app/services/storage.py`:

```python
import shutil
import uuid
from pathlib import Path

from app.config import get_settings


class Storage:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def files_dir(self) -> Path:
        d = self.root / "files"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def tmp_scans_dir(self) -> Path:
        d = self.root / "tmp" / "scan_sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def abs_path(self, rel: str) -> Path:
        return self.root / rel

    def store_file(self, document_id: uuid.UUID, ext: str, data: bytes) -> tuple[str, int]:
        name = f"{document_id}{ext}"
        path = self.files_dir / name
        path.write_bytes(data)
        return f"files/{name}", len(data)

    def delete_document_file(self, rel: str | None) -> None:
        if rel:
            self.abs_path(rel).unlink(missing_ok=True)

    def scan_session_dir(self, session_id: int) -> Path:
        d = self.tmp_scans_dir / str(session_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_scan_session_dir(self, session_id: int) -> None:
        shutil.rmtree(self.tmp_scans_dir / str(session_id), ignore_errors=True)


def get_storage() -> Storage:
    return Storage(get_settings().storage_path)
```

Run: `cd backend && uv run pytest tests/test_storage.py -v` — Expected: 2 PASS.

- [ ] **Step 4: Add storage fixture, write failing documents tests**

Append to `backend/tests/conftest.py`:

```python
@pytest.fixture
def storage(tmp_path):
    from app.services.storage import Storage, get_storage as real_get_storage
    from app.main import app as main_app

    s = Storage(tmp_path)
    main_app.dependency_overrides[real_get_storage] = lambda: s
    yield s
    main_app.dependency_overrides.pop(real_get_storage, None)
```

`backend/tests/test_documents.py`:

```python
import uuid

from app.models import Document, DocStatus, DocType, DocumentTag, Tag


def make_document(session, **kwargs):
    doc = Document(
        title=kwargs.pop("title", "Doc"),
        doc_type=kwargs.pop("doc_type", DocType.pdf),
        status=kwargs.pop("status", DocStatus.ready),
        **kwargs,
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def test_list_and_filters(auth_client, session):
    d1 = make_document(session, title="Bolletta")
    make_document(session, title="Video", doc_type=DocType.video)

    all_docs = auth_client.get("/api/documents").json()
    assert len(all_docs) == 2

    only_pdf = auth_client.get("/api/documents", params={"doc_type": "pdf"}).json()
    assert [d["id"] for d in only_pdf] == [str(d1.id)]


def test_get_includes_tags(auth_client, session):
    doc = make_document(session)
    tag = Tag(name="casa")
    session.add(tag)
    session.commit()
    session.add(DocumentTag(document_id=doc.id, tag_id=tag.id))
    session.commit()

    body = auth_client.get(f"/api/documents/{doc.id}").json()
    assert [t["name"] for t in body["tags"]] == ["casa"]


def test_patch_replaces_tags_and_moves_folder(auth_client, session):
    doc = make_document(session)
    t1, t2 = Tag(name="a"), Tag(name="b")
    session.add(t1); session.add(t2); session.commit()
    folder_id = auth_client.post("/api/folders", json={"name": "F"}).json()["id"]

    body = auth_client.patch(
        f"/api/documents/{doc.id}",
        json={"title": "New", "folder_id": folder_id, "tag_ids": [t2.id]},
    ).json()
    assert body["title"] == "New"
    assert body["folder_id"] == folder_id
    assert [t["id"] for t in body["tags"]] == [t2.id]


def test_delete_removes_file(auth_client, session, storage):
    doc = make_document(session)
    rel, _ = storage.store_file(doc.id, ".pdf", b"%PDF")
    doc.file_path = rel
    session.commit()

    assert auth_client.delete(f"/api/documents/{doc.id}").status_code == 204
    assert not storage.abs_path(rel).exists()
    assert auth_client.get(f"/api/documents/{doc.id}").status_code == 404


def test_get_missing_404(auth_client):
    resp = auth_client.get(f"/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_documents.py -v`
Expected: FAIL (404s — router missing)

- [ ] **Step 6: Implement documents router**

`backend/app/api/documents.py`:

```python
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps import api_error, get_current_user
from app.db import get_session
from app.models import Document, DocumentTag, Folder, Tag
from app.services.storage import Storage, get_storage

router = APIRouter(
    prefix="/api/documents", tags=["documents"], dependencies=[Depends(get_current_user)]
)


class DocumentPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    folder_id: int | None = None
    tag_ids: list[int] | None = None


def doc_tags(session: Session, doc: Document) -> list[Tag]:
    return list(
        session.exec(
            select(Tag).join(DocumentTag, DocumentTag.tag_id == Tag.id)
            .where(DocumentTag.document_id == doc.id)
        )
    )


def serialize(session: Session, doc: Document) -> dict:
    return {**doc.model_dump(), "tags": [t.model_dump() for t in doc_tags(session, doc)]}


def get_doc_or_404(session: Session, document_id: uuid.UUID) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise api_error(404, "not_found", f"Document {document_id} not found")
    return doc


@router.get("")
def list_documents(
    folder_id: int | None = None,
    tag_id: int | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_session),
) -> list[dict]:
    query = select(Document)
    if folder_id is not None:
        query = query.where(Document.folder_id == folder_id)
    if doc_type is not None:
        query = query.where(Document.doc_type == doc_type)
    if status is not None:
        query = query.where(Document.status == status)
    if tag_id is not None:
        query = query.join(DocumentTag, DocumentTag.document_id == Document.id).where(
            DocumentTag.tag_id == tag_id
        )
    query = query.order_by(Document.created_at.desc())
    return [serialize(session, d) for d in session.exec(query)]


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, session: Session = Depends(get_session)) -> dict:
    return serialize(session, get_doc_or_404(session, document_id))


@router.patch("/{document_id}")
def update_document(
    document_id: uuid.UUID,
    body: DocumentPatch,
    session: Session = Depends(get_session),
) -> dict:
    doc = get_doc_or_404(session, document_id)
    fields = body.model_dump(exclude_unset=True)
    tag_ids = fields.pop("tag_ids", None)

    if "folder_id" in fields and fields["folder_id"] is not None:
        if session.get(Folder, fields["folder_id"]) is None:
            raise api_error(404, "not_found", "Folder not found")

    for key, value in fields.items():
        setattr(doc, key, value)

    if tag_ids is not None:
        for link in session.exec(
            select(DocumentTag).where(DocumentTag.document_id == doc.id)
        ):
            session.delete(link)
        for tag_id in tag_ids:
            if session.get(Tag, tag_id) is None:
                raise api_error(404, "not_found", f"Tag {tag_id} not found")
            session.add(DocumentTag(document_id=doc.id, tag_id=tag_id))

    doc.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(doc)
    return serialize(session, doc)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> None:
    doc = get_doc_or_404(session, document_id)
    storage.delete_document_file(doc.file_path)
    session.delete(doc)  # chunks and document_tags cascade via FK
    session.commit()
```

In `backend/app/main.py` add `documents` to the import and `app.include_router(documents.router)`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_documents.py tests/test_storage.py -v`
Expected: 7 PASS

- [ ] **Step 8: Commit**

```bash
git add backend/
git commit -m "feat: flat-file storage service and documents metadata CRUD"
```

---

### Task 7: Job queue

**Files:**
- Create: `backend/app/services/jobs.py`
- Create: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: `Job`, `JobStatus`.
- Produces `app.services.jobs`:
  - `enqueue(session: Session, job_type: str, payload: dict, run_at: datetime | None = None) -> Job` (commits).
  - `claim_next(session: Session) -> Job | None` — atomically claims the oldest due queued job via `FOR UPDATE SKIP LOCKED`, sets status=running, commits, returns it.
  - `complete(session: Session, job: Job) -> None` — status=done, commits.
  - `fail(session: Session, job: Job, error: str) -> None` — increments attempts; if `attempts < max_attempts` requeue with `run_at = now + 30 * 2**attempts` seconds; else status=failed. Commits.
  - Backoff base constant `BACKOFF_BASE_SECONDS = 30`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_jobs.py`:

```python
from datetime import datetime, timedelta, timezone

from app.models import Job, JobStatus
from app.services.jobs import claim_next, complete, enqueue, fail


def test_enqueue_and_claim(session):
    enqueue(session, "process_document", {"document_id": "x"})
    job = claim_next(session)
    assert job is not None
    assert job.status == JobStatus.running
    assert job.payload == {"document_id": "x"}
    assert claim_next(session) is None  # nothing else queued


def test_claim_respects_run_at(session):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    enqueue(session, "process_document", {}, run_at=future)
    assert claim_next(session) is None


def test_complete(session):
    enqueue(session, "process_document", {})
    job = claim_next(session)
    complete(session, job)
    assert session.get(Job, job.id).status == JobStatus.done


def test_fail_requeues_with_backoff_then_fails(session):
    enqueue(session, "process_document", {})
    job = claim_next(session)

    fail(session, job, "boom")
    fresh = session.get(Job, job.id)
    assert fresh.status == JobStatus.queued
    assert fresh.attempts == 1
    assert fresh.last_error == "boom"
    assert fresh.run_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)

    fresh.run_at = datetime.now(timezone.utc)
    session.commit()
    job = claim_next(session)
    fail(session, job, "boom2")
    fresh.run_at = datetime.now(timezone.utc)
    session.commit()
    job = claim_next(session)
    fail(session, job, "boom3")

    assert session.get(Job, job.id).status == JobStatus.failed
    assert session.get(Job, job.id).attempts == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_jobs.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/services/jobs.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_jobs.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat: postgres-backed job queue with skip-locked claim and backoff retry"
```

---

### Task 8: Worker process

**Files:**
- Create: `backend/app/worker/__init__.py`, `backend/app/worker/runner.py`, `backend/app/worker/__main__.py`
- Create: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `app.services.jobs`, `app.db.engine`.
- Produces `app.worker.runner`:
  - `HANDLERS: dict[str, Callable[[Session, dict], None]]` — handler registry keyed by job type. Phase 2 registers `process_document` here.
  - `register(job_type: str)` decorator adding to `HANDLERS`.
  - `run_once(engine) -> bool` — claims one job; dispatches to its handler (unknown type → `fail`); handler exception → `fail(session, job, traceback-str)`; success → `complete`. Returns True if a job was processed.
  - `recover(engine) -> int` — resets `running` jobs to `queued` (crash recovery on worker start), returns count.
  - `main_loop(engine, poll_seconds=1.0)` — `recover()` once, then loop: `run_once` else sleep.
- `python -m app.worker` runs `main_loop(app.db.engine)`.

- [ ] **Step 1: Write failing tests**

`backend/tests/test_worker.py`:

```python
import pytest
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


def test_recover_resets_running(engine, session):
    enqueue(session, "echo", {})
    claim_next(session)  # leaves it 'running' as if worker crashed
    assert runner.recover(engine) == 1
    with Session(engine) as s:
        assert s.get(Job, 1).status == JobStatus.queued
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement**

`backend/app/worker/__init__.py`: empty.

`backend/app/worker/runner.py`:

```python
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


def run_once(engine) -> bool:
    with Session(engine) as session:
        job = claim_next(session)
        if job is None:
            return False
        handler = HANDLERS.get(job.type)
        if handler is None:
            fail(session, job, f"No handler for job type {job.type!r}")
            return True
        try:
            handler(session, job.payload)
        except Exception:
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
```

`backend/app/worker/__main__.py`:

```python
from app.db import engine
from app.worker.runner import main_loop

main_loop(engine)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_worker.py -v`
Expected: 5 PASS

- [ ] **Step 5: Run the full suite**

Run: `cd backend && uv run pytest -v`
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "feat: worker process with handler registry and crash recovery"
```

---

## Phase 1 exit criteria

- `docker compose up -d db` + `uv run alembic upgrade head` + `uv run uvicorn app.main:app` + `uv run python -m app.worker` all run cleanly.
- `uv run python -m app.cli create-user marco` then login via `POST /api/auth/login` returns a token.
- Full pytest suite green against real Postgres.
- Phase 2 (ingestion + scanner) builds on: `Storage`, `enqueue`/`HANDLERS`, `Document`/`Chunk` models, `api_error` shape.
