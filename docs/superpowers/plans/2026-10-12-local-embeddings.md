# Local Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Origami embeds with a local EmbeddingGemma model served by an Ollama container by default, refuses to start when stored vectors do not match the configured model, and offers a `reembed` CLI to rebuild every vector.

**Architecture:** `config.resolve_embedding()` turns `.env` into one `EmbeddingConfig` (model, dim, key, base, prefix scheme). `llm.embed(texts, role)` uses it, adds role prefixes and truncates Matryoshka vectors. A one-row `embedding_meta` table stores the fingerprint of the vectors in `chunks`; `services/embedding_check.py` compares it at startup (CLI in `origami.sh`, plus API lifespan and worker), talks to Ollama over HTTP and resizes the column on an empty database. `services/reembed.py` rebuilds all vectors in one transaction.

**Tech Stack:** Python 3.13, FastAPI, SQLModel, pgvector, LiteLLM 1.92 (`ollama/*` → `/api/embed`), Ollama 0.20.4 in Docker Compose, pytest on real Postgres.

**Spec:** `docs/superpowers/specs/2026-10-12-local-embeddings-design.md`

## Global Constraints

- Local default: `ollama/embeddinggemma`, base `http://localhost:11434`, 768 dims, scheme `embeddinggemma-v1`.
- Default trigger: only an empty `EMBEDDING_MODEL`.
- `EMBEDDING_DIM` empty → 768 for embeddinggemma models, else 1536.
- Prefixes (`embeddinggemma-v1`): query `task: search result | query: `, document `title: none | text: `. Scheme `none`: no prefix.
- No `dimensions` argument for `ollama/*` models; truncate + L2-normalize instead. Shorter vector → error.
- `api_base` is not part of the fingerprint.
- Mismatch/check error exit code: 78 (`EX_CONFIG`); unit gets `RestartPreventExitStatus=78`.
- pgvector HNSW limit: `dim` ≤ 2000. Index name `ix_chunks_embedding`, `USING hnsw (embedding vector_cosine_ops)`.
- Ollama container: `ollama/ollama:0.20.4`, `container_name: origami-ollama`, port `127.0.0.1:11434:11434`, volume `ollama:/root/.ollama`, `OLLAMA_KEEP_ALIVE=5m`.
- Tests never call a real Ollama or a real embedding provider; tests never touch the developer's real database.
- Code, comments, commits, README: normal English prose, matching surrounding style.
- Work on branch `feat/local-embeddings`; merge to local `main` with `--no-ff`; never push without an explicit ask.

## Spec clarifications (decided while planning)

- An explicit `EMBEDDING_MODEL=ollama/...` uses `EMBEDDING_API_BASE` or `http://localhost:11434`, never `LLM_API_BASE` (that may point at another provider).
- Ollama model names without a tag get `:latest` when compared with `/api/tags` and when pulled.
- `Chunk.embedding` becomes `Vector()` with no dimension in the ORM: the dimension lives only in the database column. (The spec said "from resolved settings"; no dimension is simpler and equivalent, since DDL is never generated from the model.)
- No fingerprint row **and** column dimension ≠ resolved dim **and** embeddings exist → refuse (unknown origin), instead of recording.
- Empty database (no embedded chunk) with a different stored fingerprint → overwrite it, nothing to lose.
- Query embedding in `rag.py` and `chat_context.py` also uses `role="query"` (the spec only named `search.py`).

## Review Focus

1. `EMBEDDING_DIM=` (empty string) in `.env` must load as "use default", not crash Settings validation. → Task 1 test `test_empty_dim_env_means_default`.
2. Ollama replies with a model name `embeddinggemma:latest` while `.env` says `ollama/embeddinggemma` → must count as present, not trigger a pull every start. → Task 4 test `test_model_with_latest_tag_counts_as_present`.
3. `reembed` on an archive with zero chunks must succeed (resize column, write fingerprint) and not divide or index-error. → Task 6 test `test_reembed_with_no_chunks`.
4. Pull stream reports `{"error": ...}` (unknown model name typo) → clear error, exit 78, not a JSON crash. → Task 4 test `test_pull_error_event_is_reported`.
5. Ctrl-C during `reembed` must roll back (old vectors kept). → Task 6 test `test_interrupt_rolls_back` (KeyboardInterrupt from embed).

---

### Task 1: Resolve embedding settings

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/tests/conftest.py` (top, env defaults)
- Modify: `backend/tests/test_llm.py` (`pinned_settings` fixture)
- Create: `backend/tests/test_embedding_config.py`

**Interfaces:**
- Produces (in `app.config`):
  - constants `LOCAL_EMBEDDING_MODEL = "ollama/embeddinggemma"`, `LOCAL_EMBEDDING_API_BASE = "http://localhost:11434"`, `EMBEDDINGGEMMA_DIM = 768`, `DEFAULT_EMBEDDING_DIM = 1536`
  - `@dataclass(frozen=True) class EmbeddingConfig: model: str; dim: int; api_key: str; api_base: str; scheme: str` with properties `is_ollama -> bool`, `ollama_name -> str` (name after `ollama/`, `:latest` added when no tag)
  - `embedding_scheme(model: str) -> str` → `"embeddinggemma-v1"` or `"none"`
  - `resolve_embedding(settings: Settings | None = None) -> EmbeddingConfig`
  - `Settings.embedding_model: str = ""`, `Settings.embedding_dim: int | None = None`

- [ ] **Step 1: Pin test env to today's provider**

Existing tests rely on 1536-dim Gemini defaults. In `backend/tests/conftest.py`, next to the other `os.environ.setdefault` lines (before `get_settings.cache_clear()`), add:

```python
# Code defaults now mean "local Ollama model, 768 dims"; the test database and the fakes use 1536.
os.environ.setdefault("EMBEDDING_MODEL", "gemini/gemini-embedding-001")
```

In `backend/tests/test_llm.py`, fixture `pinned_settings`, change the `Settings(...)` line to:

```python
    settings = Settings(
        _env_file=None, llm_api_key="", llm_api_base="", gemini_api_key="",
        embedding_model="gemini/gemini-embedding-001",
    )
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_embedding_config.py`:

```python
from app.config import EmbeddingConfig, Settings, embedding_scheme, resolve_embedding


def make(**values) -> Settings:
    base = {"embedding_model": "", "embedding_dim": None, "embedding_api_key": "", "embedding_api_base": "",
            "llm_api_key": "", "llm_api_base": ""}
    return Settings(_env_file=None, **{**base, **values})


def test_empty_model_resolves_to_local_default():
    cfg = resolve_embedding(make())
    assert cfg == EmbeddingConfig(
        model="ollama/embeddinggemma", dim=768, api_key="", api_base="http://localhost:11434",
        scheme="embeddinggemma-v1",
    )
    assert cfg.is_ollama
    assert cfg.ollama_name == "embeddinggemma:latest"


def test_local_default_ignores_llm_key_and_base():
    cfg = resolve_embedding(make(llm_api_key="sk-groq", llm_api_base="https://api.groq.com/openai/v1"))
    assert cfg.api_key == ""
    assert cfg.api_base == "http://localhost:11434"


def test_local_default_honors_embedding_api_base():
    cfg = resolve_embedding(make(embedding_api_base="http://nas:11434"))
    assert cfg.api_base == "http://nas:11434"


def test_explicit_remote_model_keeps_llm_fallback():
    cfg = resolve_embedding(make(embedding_model="gemini/gemini-embedding-001", llm_api_key="sk-shared"))
    assert cfg.model == "gemini/gemini-embedding-001"
    assert cfg.dim == 1536
    assert cfg.api_key == "sk-shared"
    assert cfg.scheme == "none"
    assert not cfg.is_ollama


def test_explicit_ollama_model_does_not_use_llm_base():
    cfg = resolve_embedding(make(embedding_model="ollama/qwen3-embedding:0.6b", embedding_dim=1024,
                                 llm_api_base="https://api.groq.com/openai/v1"))
    assert cfg.api_base == "http://localhost:11434"
    assert cfg.ollama_name == "qwen3-embedding:0.6b"
    assert cfg.dim == 1024
    assert cfg.scheme == "none"


def test_explicit_dim_wins():
    assert resolve_embedding(make(embedding_dim=512)).dim == 512


def test_scheme_from_model_name():
    assert embedding_scheme("ollama/embeddinggemma:300m") == "embeddinggemma-v1"
    assert embedding_scheme("ollama/EmbeddingGemma") == "embeddinggemma-v1"
    assert embedding_scheme("gemini/gemini-embedding-001") == "none"


def test_empty_dim_env_means_default(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "")
    monkeypatch.setenv("EMBEDDING_MODEL", "")
    settings = Settings(_env_file=None)
    assert settings.embedding_dim is None
    assert resolve_embedding(settings).dim == 768
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_embedding_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'EmbeddingConfig'`

- [ ] **Step 4: Implement**

In `backend/app/config.py`:

Imports at top become:

```python
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_EMBEDDING_MODEL = "ollama/embeddinggemma"
LOCAL_EMBEDDING_API_BASE = "http://localhost:11434"
EMBEDDINGGEMMA_DIM = 768
DEFAULT_EMBEDDING_DIM = 1536
```

Replace the two embedding fields in `Settings`:

```python
    embedding_model: str = ""  # empty → LOCAL_EMBEDDING_MODEL on the Ollama container
    embedding_dim: int | None = None  # empty → 768 for embeddinggemma, else 1536
```

Add inside `Settings`, after the last field:

```python
    @field_validator("embedding_dim", mode="before")
    @classmethod
    def _empty_dim_means_default(cls, value):
        return None if isinstance(value, str) and not value.strip() else value
```

Append at the end of the file:

```python
@dataclass(frozen=True)
class EmbeddingConfig:
    model: str
    dim: int
    api_key: str
    api_base: str
    scheme: str  # query/document prefix style: "none" or "embeddinggemma-v1"

    @property
    def is_ollama(self) -> bool:
        return self.model.startswith("ollama/")

    @property
    def ollama_name(self) -> str:
        name = self.model.split("/", 1)[1]
        return name if ":" in name else f"{name}:latest"


def embedding_scheme(model: str) -> str:
    return "embeddinggemma-v1" if "embeddinggemma" in model.lower() else "none"


def resolve_embedding(settings: Settings | None = None) -> EmbeddingConfig:
    """Embedding model, dimension, credentials and prefix scheme after defaults and fallbacks."""
    settings = settings or get_settings()
    model = settings.embedding_model.strip() or LOCAL_EMBEDDING_MODEL
    scheme = embedding_scheme(model)
    if model.startswith("ollama/"):
        # LLM_API_BASE may belong to another provider: Ollama gets its own base only.
        api_key, api_base = "", settings.embedding_api_base or LOCAL_EMBEDDING_API_BASE
    else:
        api_key = settings.embedding_api_key or settings.llm_api_key
        api_base = settings.embedding_api_base or settings.llm_api_base
    default_dim = EMBEDDINGGEMMA_DIM if scheme == "embeddinggemma-v1" else DEFAULT_EMBEDDING_DIM
    return EmbeddingConfig(model, settings.embedding_dim or default_dim, api_key, api_base, scheme)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_embedding_config.py tests/test_llm.py -v`
Expected: all PASS (`test_embed_*` in `test_llm.py` still use the old `embed`, which reads `settings.embedding_model` = Gemini via the pinned settings).

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/tests/conftest.py backend/tests/test_llm.py backend/tests/test_embedding_config.py
git commit -m "feat: resolve embedding settings with a local Ollama default"
```

---

### Task 2: Role-aware `embed()` with prefixes and truncation

**Files:**
- Modify: `backend/app/services/llm.py` (`embed`, new `fit_dimension`, `EMBEDDING_PREFIXES`)
- Modify: `backend/app/services/search.py:157`, `backend/app/services/rag.py:52`, `backend/app/services/chat_context.py:43` (pass `role="query"`)
- Modify: `backend/app/worker/pipeline.py:499` (pass `role="document"`)
- Modify fakes: `backend/tests/conftest.py` (`llm_stub.fake_embed`), `backend/tests/test_search_api.py`, `backend/tests/test_rag.py`, `backend/tests/test_chat_api.py`, `backend/tests/test_chat_context.py`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `resolve_embedding`, `EmbeddingConfig` from Task 1.
- Produces: `llm.embed(texts: list[str], role: str = "document") -> list[list[float]]`; `llm.fit_dimension(vector: list[float], dim: int) -> list[float]`; `llm.EMBEDDING_PREFIXES: dict[str, dict[str, str]]`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_llm.py`, replace `test_embed_returns_vectors_in_input_order` with:

```python
def test_embed_returns_vectors_in_input_order(monkeypatch):
    def fake_embedding(model, input, dimensions):
        assert model == "gemini/gemini-embedding-001"
        assert dimensions == 1536
        data = [{"index": i, "embedding": [float(i)] * dimensions} for i in range(len(input))]
        return SimpleNamespace(data=list(reversed(data)))  # out of order on purpose

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    vectors = llm.embed(["a", "b"])
    assert [v[0] for v in vectors] == [0.0, 1.0]
    assert all(len(v) == 1536 for v in vectors)
```

Append to `backend/tests/test_llm.py`:

```python
def _local_settings(monkeypatch, **values):
    settings = Settings(_env_file=None, embedding_model="", embedding_dim=None, llm_api_key="",
                        llm_api_base="", gemini_api_key="", **values)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    return settings


def _capture_embedding(monkeypatch, length):
    captured = {}

    def fake_embedding(model, input, **kw):
        captured.update(model=model, input=list(input), kw=kw)
        return SimpleNamespace(data=[{"index": i, "embedding": [1.0] * length} for i in range(len(input))])

    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    return captured


def test_embed_local_default_document_prefix(monkeypatch):
    _local_settings(monkeypatch)
    captured = _capture_embedding(monkeypatch, 768)
    llm.embed(["Fattura 42"])
    assert captured["model"] == "ollama/embeddinggemma"
    assert captured["input"] == ["title: none | text: Fattura 42"]
    assert captured["kw"] == {"api_base": "http://localhost:11434"}  # no dimensions, no key


def test_embed_local_default_query_prefix(monkeypatch):
    _local_settings(monkeypatch)
    captured = _capture_embedding(monkeypatch, 768)
    llm.embed(["bolletta luce"], role="query")
    assert captured["input"] == ["task: search result | query: bolletta luce"]


def test_embed_scheme_none_has_no_prefix(monkeypatch):
    captured = _capture_embedding(monkeypatch, 1536)  # pinned settings: Gemini
    llm.embed(["ciao"], role="query")
    assert captured["input"] == ["ciao"]
    assert captured["kw"]["dimensions"] == 1536


def test_embed_rejects_unknown_role(monkeypatch):
    with pytest.raises(ValueError, match="role"):
        llm.embed(["x"], role="passage")


def test_embed_truncates_and_normalizes(monkeypatch):
    _local_settings(monkeypatch, embedding_dim=256)
    _capture_embedding(monkeypatch, 768)
    [vector] = llm.embed(["x"])
    assert len(vector) == 256
    assert math.isclose(sum(x * x for x in vector), 1.0)


def test_fit_dimension_keeps_exact_length():
    assert llm.fit_dimension([3.0, 4.0], 2) == [3.0, 4.0]


def test_fit_dimension_rejects_short_vector():
    with pytest.raises(ValueError, match="EMBEDDING_DIM"):
        llm.fit_dimension([1.0] * 384, 768)
```

Add `import math` to the top of `test_llm.py` (with the other stdlib imports). `pytest` and `Settings` are already imported there; if `Settings` is imported further down the file, keep that import and make sure it sits above these new tests.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_llm.py -v -k "embed or fit_dimension"`
Expected: FAIL (`embed() got an unexpected keyword argument 'role'`, `module 'app.services.llm' has no attribute 'fit_dimension'`).

- [ ] **Step 3: Implement `embed`**

In `backend/app/services/llm.py`, change the config import to:

```python
from app.config import get_primary_language, get_settings, resolve_embedding
```

Replace the whole `embed` function with:

```python
EMBEDDING_PREFIXES = {
    "none": {"query": "", "document": ""},
    "embeddinggemma-v1": {"query": "task: search result | query: ", "document": "title: none | text: "},
}


def fit_dimension(vector: list[float], dim: int) -> list[float]:
    """Matryoshka truncation: keep the first `dim` values and re-normalize."""
    if len(vector) < dim:
        raise ValueError(
            f"The embedding model returned {len(vector)} values but EMBEDDING_DIM is {dim}: "
            f"set EMBEDDING_DIM to {len(vector)} or less"
        )
    if len(vector) == dim:
        return list(vector)
    head = list(vector[:dim])
    norm = math.sqrt(sum(x * x for x in head))
    return [x / norm for x in head] if norm else head


def embed(texts: list[str], role: str = "document") -> list[list[float]]:
    """Vectors for `texts`; role "query" for search input, "document" for stored chunks."""
    if role not in ("document", "query"):
        raise ValueError(f"Unknown embedding role {role!r}")
    cfg = resolve_embedding(get_settings())
    prefix = EMBEDDING_PREFIXES[cfg.scheme][role]
    kw = _kw(cfg.api_key, cfg.api_base)
    if not cfg.is_ollama:
        kw["dimensions"] = cfg.dim  # Ollama does not take it: fit_dimension truncates instead
    resp = litellm.embedding(model=cfg.model, input=[prefix + t for t in texts], **kw)
    data = sorted(resp.data, key=lambda d: d["index"])
    return [fit_dimension(d["embedding"], cfg.dim) for d in data]
```

(`math` is already imported in `llm.py`.)

- [ ] **Step 4: Pass roles at the call sites**

- `backend/app/services/search.py:157`: `query_vector = llm_embed([query], role="query")[0]`
- `backend/app/services/rag.py:52`: `query_vector = llm_embed([query], role="query")[0]`
- `backend/app/services/chat_context.py:43`: `query_vector = llm_embed([query], role="query")[0]`
- `backend/app/worker/pipeline.py:499`: `vectors = llm_embed([c.content for c in batch], role="document")`

- [ ] **Step 5: Update every test fake to take `role`**

Run `cd backend && grep -n "llm_embed" tests/*.py` and update each fake it lists:

- `tests/conftest.py` `llm_stub`: `def fake_embed(texts, role="document"):` and record the role:
  ```python
      def fake_embed(texts, role="document"):
          calls["embed"].append(list(texts))
          calls["embed_roles"].append(role)
          return [[0.1] * 1536 for _ in texts]
  ```
  and add `"embed_roles": [],` to the `calls` dict literal at the top of `llm_stub`.
- `tests/test_search_api.py:10`: `lambda texts, role="document": [basis_vector(0)]`; and its `boom` function (around line 50) gets the signature `def boom(texts, role="document"):`.
- `tests/test_rag.py` `fake_embed`: `def fake_embed(texts, role="document"):`
- `tests/test_chat_api.py` `fake_embed` and `boom`: add `role="document"` parameter.
- `tests/test_chat_context.py` `fake_embed`: add `role="document"` parameter.

Then add role assertions. In `tests/test_search_api.py` add:

```python
def test_search_embeds_query_role(auth_client, session, monkeypatch):
    roles = []

    def fake_embed(texts, role="document"):
        roles.append(role)
        return [basis_vector(0)]

    monkeypatch.setattr(search_module, "llm_embed", fake_embed)
    auth_client.post("/api/search", json={"query": "bolletta", "mode": "semantic"})
    assert roles == ["query"]
```

In `tests/test_pipeline.py`, find the existing test that processes a plain text document end to end with the `llm_stub` fixture (search for `llm_stub["embed"]`). Copy it as `test_pipeline_embeds_document_role`, keep its setup and processing calls unchanged, and replace its assertions with:

```python
    assert llm_stub["embed_roles"] and set(llm_stub["embed_roles"]) == {"document"}
```

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: query/document roles, embeddinggemma prefixes and Matryoshka truncation in embed()"
```

---

### Task 3: Fingerprint table and dimensionless ORM column

**Files:**
- Create: `backend/app/models/embedding_meta.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/chunk.py`
- Create: `backend/alembic/versions/c8e2f5a1d746_embedding_meta.py`
- Test: `backend/tests/test_schema.py`

**Interfaces:**
- Produces: `app.models.EmbeddingMeta` (table `embedding_meta`: `id: int = 1` primary key, `model: str`, `dim: int`, `scheme: str`, `updated_at: datetime`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_schema.py`:

```python
def test_embedding_meta_table(engine):
    from sqlalchemy import inspect

    cols = {c["name"] for c in inspect(engine).get_columns("embedding_meta")}
    assert cols == {"id", "model", "dim", "scheme", "updated_at"}


def test_embedding_column_keeps_1536_dims(engine):
    with engine.connect() as conn:
        dim = conn.execute(text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        )).scalar_one()
    assert dim == 1536
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_schema.py -v`
Expected: `test_embedding_meta_table` FAILS (`NoSuchTableError: embedding_meta`); `test_embedding_column_keeps_1536_dims` PASSES (it pins the `atttypmod` = dim behaviour Task 4 relies on).

- [ ] **Step 3: Implement**

Create `backend/app/models/embedding_meta.py`:

```python
from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.user import utcnow


class EmbeddingMeta(SQLModel, table=True):
    """Fingerprint of the vectors in chunks.embedding (one row, id 1)."""

    __tablename__ = "embedding_meta"

    id: int = Field(default=1, primary_key=True)
    model: str
    dim: int
    scheme: str
    updated_at: datetime = Field(default_factory=utcnow)
```

In `backend/app/models/__init__.py` add `from app.models.embedding_meta import EmbeddingMeta` and `"EmbeddingMeta"` to `__all__` (keep alphabetical order of the list).

In `backend/app/models/chunk.py` delete `EMBEDDING_DIM = 1536` and change the column to:

```python
    # Dimension lives only in the database column (EMBEDDING_DIM / reembed decide it).
    embedding: Any = Field(default=None, sa_column=Column(Vector()))
```

Create `backend/alembic/versions/c8e2f5a1d746_embedding_meta.py`:

```python
"""embedding fingerprint table

Revision ID: c8e2f5a1d746
Revises: b1d7e4f20a93
Create Date: 2026-10-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e2f5a1d746"
down_revision: Union[str, Sequence[str], None] = "b1d7e4f20a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Filled on first start by `app.cli check-embeddings` from the current .env.
    op.create_table(
        "embedding_meta",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("scheme", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("embedding_meta")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_schema.py tests/test_migrations.py tests/test_search_core.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models backend/alembic/versions/c8e2f5a1d746_embedding_meta.py backend/tests/test_schema.py
git commit -m "feat: embedding_meta fingerprint table; chunk vector column without fixed ORM dimension"
```

---

### Task 4: Embedding check service (Ollama + fingerprint + column)

**Files:**
- Create: `backend/app/services/embedding_check.py`
- Test: `backend/tests/test_embedding_check.py`

**Interfaces:**
- Consumes: `EmbeddingConfig`, `resolve_embedding` (Task 1); `EmbeddingMeta` (Task 3).
- Produces (in `app.services.embedding_check`):
  - `EX_CONFIG = 78`, `HNSW_MAX_DIM = 2000`, `INDEX_NAME = "ix_chunks_embedding"`
  - `class EmbeddingCheckError(Exception)`
  - `ollama_request(url: str, body: dict | None = None, timeout: float = 10) -> context-manager file-like` (replaced in tests)
  - `ensure_ollama_model(cfg: EmbeddingConfig, pull: bool) -> None`
  - `column_dim(session) -> int`, `has_embeddings(session) -> bool`
  - `set_column_dim(session, dim: int) -> None` (drops index, retypes column, all embeddings NULL; no commit)
  - `create_index(session) -> None` (no commit)
  - `write_fingerprint(session, cfg: EmbeddingConfig) -> None` (no commit)
  - `check(session, cfg: EmbeddingConfig, pull: bool) -> str` → `"ok" | "recorded" | "resized"`; raises `EmbeddingCheckError`
  - `startup_guard(engine) -> None`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_embedding_check.py`:

```python
import io
import json
import urllib.error

import pytest

from app.config import EmbeddingConfig
from app.models import EmbeddingMeta
from app.services import embedding_check
from app.services.embedding_check import (
    EmbeddingCheckError, check, column_dim, create_index, set_column_dim, startup_guard,
)
from tests.helpers import basis_vector, seed_document

GEMINI = EmbeddingConfig("gemini/gemini-embedding-001", 1536, "", "", "none")
LOCAL = EmbeddingConfig("ollama/embeddinggemma", 768, "", "http://localhost:11434", "embeddinggemma-v1")


class FakeOllama:
    def __init__(self, models=(), reachable=True, pull_events=None):
        self.models = list(models)
        self.reachable = reachable
        self.pull_events = pull_events or [{"status": "pulling manifest"}, {"status": "success"}]
        self.calls = []

    def __call__(self, url, body=None, timeout=10):
        self.calls.append((url, body))
        if not self.reachable:
            raise urllib.error.URLError("Connection refused")
        if url.endswith("/api/tags"):
            return io.BytesIO(json.dumps({"models": [{"name": n} for n in self.models]}).encode())
        if url.endswith("/api/pull"):
            if not any("error" in e for e in self.pull_events):
                self.models.append(body["model"])
            return io.BytesIO(b"\n".join(json.dumps(e).encode() for e in self.pull_events))
        raise AssertionError(f"unexpected Ollama call {url}")


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(embedding_check, "ollama_request", fake)
    return fake


@pytest.fixture
def restore_column(session):
    """Tests here retype chunks.embedding; put the session-wide test database back to 1536."""
    yield
    session.rollback()
    if column_dim(session) != 1536:
        set_column_dim(session, 1536)
        create_index(session)
        session.commit()


def store(session, model="gemini/gemini-embedding-001", dim=1536, scheme="none"):
    session.add(EmbeddingMeta(id=1, model=model, dim=dim, scheme=scheme))
    session.commit()


def index_exists(session) -> bool:
    from sqlalchemy import text

    return session.execute(text("SELECT to_regclass('ix_chunks_embedding') IS NOT NULL")).scalar_one()


def test_no_row_records_fingerprint(session):
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    assert check(session, GEMINI, pull=False) == "recorded"
    meta = session.get(EmbeddingMeta, 1)
    assert (meta.model, meta.dim, meta.scheme) == ("gemini/gemini-embedding-001", 1536, "none")


def test_matching_fingerprint_is_ok(session):
    store(session)
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    assert check(session, GEMINI, pull=False) == "ok"


def test_mismatch_with_embeddings_refuses(session, ollama):
    ollama.models = ["embeddinggemma:latest"]
    store(session)
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    with pytest.raises(EmbeddingCheckError) as err:
        check(session, LOCAL, pull=False)
    message = str(err.value)
    assert "database: gemini/gemini-embedding-001, 1536 dims, scheme none" in message
    assert ".env:     ollama/embeddinggemma, 768 dims, scheme embeddinggemma-v1" in message
    assert "uv run python -m app.cli reembed" in message
    assert column_dim(session) == 1536  # nothing touched


def test_scheme_change_alone_refuses(session, ollama):
    ollama.models = ["embeddinggemma:latest"]
    store(session, model="ollama/embeddinggemma", dim=1536, scheme="none")
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    cfg = EmbeddingConfig("ollama/embeddinggemma", 1536, "", "http://localhost:11434", "embeddinggemma-v1")
    with pytest.raises(EmbeddingCheckError, match="scheme none"):
        check(session, cfg, pull=False)


def test_empty_database_resizes_column(session, ollama, restore_column):
    ollama.models = ["embeddinggemma:latest"]
    store(session)
    seed_document(session, "A", [{"content": "no vector yet"}])
    assert check(session, LOCAL, pull=False) == "resized"
    assert column_dim(session) == 768
    assert index_exists(session)
    meta = session.get(EmbeddingMeta, 1)
    session.refresh(meta)
    assert (meta.model, meta.dim) == ("ollama/embeddinggemma", 768)


def test_no_row_and_other_dim_with_embeddings_refuses(session, ollama):
    ollama.models = ["embeddinggemma:latest"]
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    with pytest.raises(EmbeddingCheckError, match="unknown model, 1536 dims"):
        check(session, LOCAL, pull=False)


def test_dim_over_hnsw_limit_refuses(session):
    cfg = EmbeddingConfig("gemini/gemini-embedding-001", 3072, "", "", "none")
    with pytest.raises(EmbeddingCheckError, match="2000"):
        check(session, cfg, pull=False)


def test_ollama_unreachable(session, ollama):
    ollama.reachable = False
    with pytest.raises(EmbeddingCheckError, match="docker compose up -d ollama"):
        check(session, LOCAL, pull=True)


def test_missing_model_is_pulled(session, ollama, restore_column):
    assert check(session, LOCAL, pull=True) == "resized"
    assert ("http://localhost:11434/api/pull", {"model": "embeddinggemma:latest"}) in ollama.calls


def test_missing_model_without_pull_refuses(session, ollama):
    with pytest.raises(EmbeddingCheckError, match="app.cli check-embeddings"):
        check(session, LOCAL, pull=False)
    assert not any(url.endswith("/api/pull") for url, _ in ollama.calls)


def test_model_with_latest_tag_counts_as_present(session, ollama, restore_column):
    ollama.models = ["embeddinggemma:latest"]
    check(session, LOCAL, pull=True)
    assert not any(url.endswith("/api/pull") for url, _ in ollama.calls)


def test_pull_error_event_is_reported(session, ollama):
    ollama.pull_events = [{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}]
    with pytest.raises(EmbeddingCheckError, match="file does not exist"):
        check(session, LOCAL, pull=True)


def test_remote_model_never_calls_ollama(session, ollama):
    check(session, GEMINI, pull=True)
    assert ollama.calls == []


def test_startup_guard_uses_settings(session, engine):
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    startup_guard(engine)  # test env: Gemini, 1536 → records
    session.expire_all()
    assert session.get(EmbeddingMeta, 1).model == "gemini/gemini-embedding-001"


def test_startup_guard_raises_on_mismatch(session, engine):
    store(session, model="ollama/embeddinggemma", dim=1536, scheme="embeddinggemma-v1")
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    with pytest.raises(EmbeddingCheckError, match="Embedding model changed"):
        startup_guard(engine)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_embedding_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.embedding_check'`

- [ ] **Step 3: Implement**

Create `backend/app/services/embedding_check.py`:

```python
"""Startup check: the vectors in chunks.embedding must come from the configured embedding model."""

import json
import logging
import urllib.request

from sqlalchemy import text
from sqlmodel import Session

from app.config import EmbeddingConfig, resolve_embedding
from app.models import EmbeddingMeta
from app.models.user import utcnow

log = logging.getLogger("origami.embeddings")

EX_CONFIG = 78  # sysexits.h; origami.service has RestartPreventExitStatus=78
HNSW_MAX_DIM = 2000  # pgvector HNSW limit for the vector type
INDEX_NAME = "ix_chunks_embedding"
OLLAMA_TIMEOUT_SECONDS = 10
OLLAMA_PULL_TIMEOUT_SECONDS = 600
REEMBED_HINT = (
    "Re-embed all documents:  cd backend && uv run python -m app.cli reembed\n"
    "Or restore EMBEDDING_MODEL / EMBEDDING_DIM in .env."
)


class EmbeddingCheckError(Exception):
    """Origami must not start; the message tells the user what to do."""


def ollama_request(url: str, body: dict | None = None, timeout: float = OLLAMA_TIMEOUT_SECONDS):
    """GET `url`, or POST `body` as JSON. Replaced in tests."""
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data is not None else {}
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout)


def ensure_ollama_model(cfg: EmbeddingConfig, pull: bool) -> None:
    base = cfg.api_base.rstrip("/")
    try:
        with ollama_request(f"{base}/api/tags") as resp:
            names = {m.get("name") for m in json.load(resp).get("models", [])}
    except OSError as exc:  # URLError, connection refused, timeout
        raise EmbeddingCheckError(
            f"Ollama is not reachable at {base} ({exc}). Start it: docker compose up -d ollama"
        ) from exc
    if cfg.ollama_name in names:
        return
    if not pull:
        raise EmbeddingCheckError(
            f"Model {cfg.ollama_name} is not pulled. Run: cd backend && uv run python -m app.cli check-embeddings"
        )
    log.info("Pulling %s into Ollama (first run only)", cfg.ollama_name)
    try:
        with ollama_request(f"{base}/api/pull", {"model": cfg.ollama_name}, OLLAMA_PULL_TIMEOUT_SECONDS) as resp:
            last_status = None
            for line in resp:
                if not line.strip():
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise EmbeddingCheckError(f"Pulling {cfg.ollama_name} failed: {event['error']}")
                if event.get("status") != last_status:
                    last_status = event.get("status")
                    log.info("pull %s: %s", cfg.ollama_name, last_status)
    except OSError as exc:
        raise EmbeddingCheckError(f"Pulling {cfg.ollama_name} from {base} failed: {exc}") from exc


def column_dim(session: Session) -> int:
    """Declared dimension of chunks.embedding (pgvector keeps it in atttypmod)."""
    return session.execute(text(
        "SELECT atttypmod FROM pg_attribute WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
    )).scalar_one()


def has_embeddings(session: Session) -> bool:
    return session.execute(text("SELECT EXISTS (SELECT 1 FROM chunks WHERE embedding IS NOT NULL)")).scalar_one()


def set_column_dim(session: Session, dim: int) -> None:
    """Drop the HNSW index and retype chunks.embedding to vector(dim). Every embedding becomes NULL."""
    session.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))
    session.execute(text(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({int(dim)}) USING NULL"))


def create_index(session: Session) -> None:
    session.execute(text(f"CREATE INDEX {INDEX_NAME} ON chunks USING hnsw (embedding vector_cosine_ops)"))


def write_fingerprint(session: Session, cfg: EmbeddingConfig) -> None:
    meta = session.get(EmbeddingMeta, 1) or EmbeddingMeta(id=1, model=cfg.model, dim=cfg.dim, scheme=cfg.scheme)
    meta.model, meta.dim, meta.scheme, meta.updated_at = cfg.model, cfg.dim, cfg.scheme, utcnow()
    session.add(meta)


def _mismatch(model: str, dim: int, scheme: str, cfg: EmbeddingConfig) -> EmbeddingCheckError:
    return EmbeddingCheckError(
        "Embedding model changed.\n"
        f"  database: {model}, {dim} dims, scheme {scheme}\n"
        f"  .env:     {cfg.model}, {cfg.dim} dims, scheme {cfg.scheme}\n"
        + REEMBED_HINT
    )


def check(session: Session, cfg: EmbeddingConfig, pull: bool) -> str:
    """Compare the stored vectors with `cfg`: "ok", "recorded" (first run) or "resized" (empty archive)."""
    if cfg.dim > HNSW_MAX_DIM:
        raise EmbeddingCheckError(f"EMBEDDING_DIM {cfg.dim} is over the pgvector HNSW limit of {HNSW_MAX_DIM}")
    if cfg.is_ollama:
        ensure_ollama_model(cfg, pull)
    stored = session.get(EmbeddingMeta, 1)
    current = (stored.model, stored.dim, stored.scheme) if stored else None
    wanted = (cfg.model, cfg.dim, cfg.scheme)
    dim_now = column_dim(session)
    if not has_embeddings(session):  # nothing to lose: adapt the column and the fingerprint
        result = "ok"
        if dim_now != cfg.dim:
            set_column_dim(session, cfg.dim)
            create_index(session)
            result = "resized"
        if current != wanted:
            write_fingerprint(session, cfg)
            result = result if result == "resized" else "recorded"
        session.commit()
        return result
    if current is None:
        if dim_now != cfg.dim:
            raise _mismatch("unknown model", dim_now, "unknown", cfg)
        write_fingerprint(session, cfg)  # upgrade path: existing vectors come from the current .env
        session.commit()
        return "recorded"
    if current != wanted or dim_now != cfg.dim:
        raise _mismatch(*current, cfg)
    return "ok"


def startup_guard(engine) -> None:
    """API and worker startup: same check without pulling. Raises EmbeddingCheckError."""
    with Session(engine) as session:
        result = check(session, resolve_embedding(), pull=False)
    log.info("Embedding check: %s", result)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_embedding_check.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite** (column-retyping tests must leave the database at 1536)

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/embedding_check.py backend/tests/test_embedding_check.py
git commit -m "feat: embedding fingerprint check with Ollama model pull and empty-archive resize"
```

---

### Task 5: `check-embeddings` CLI and startup wiring

**Files:**
- Modify: `backend/app/cli.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker/__main__.py`
- Modify: `backend/tests/conftest.py` (autouse no-op guard)
- Test: `backend/tests/test_cli.py`, `backend/tests/test_embedding_startup.py` (create)

**Interfaces:**
- Consumes: `check`, `startup_guard`, `EmbeddingCheckError`, `EX_CONFIG` (Task 4); `resolve_embedding` (Task 1).
- Produces: `app.main.embedding_startup_guard() -> None` (module-level, replaceable); `app.worker.__main__.main(engine=...) -> None`; CLI command `check-embeddings`.

- [ ] **Step 1: Keep ordinary tests away from the real database**

`TestClient` in a `with` block runs the lifespan, which would call the guard on the developer's database. In `backend/tests/conftest.py` add, after the `smtp_stub` fixture:

```python
@pytest.fixture(autouse=True)
def no_embedding_guard(monkeypatch):
    """The API lifespan checks embeddings on app.db.engine (the real database): skip it in tests.
    tests/test_embedding_startup.py re-patches it to test the wiring."""
    monkeypatch.setattr("app.main.embedding_startup_guard", lambda: None)
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_embedding_startup.py`:

```python
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.embedding_check import EmbeddingCheckError


def test_api_lifespan_runs_guard(monkeypatch):
    def boom():
        raise EmbeddingCheckError("Embedding model changed.")

    monkeypatch.setattr(main, "embedding_startup_guard", boom)
    with pytest.raises(EmbeddingCheckError):
        with TestClient(main.app):
            pass


def test_worker_main_stops_before_loop(monkeypatch):
    import app.worker.__main__ as worker_main

    started = []

    def boom(engine):
        raise EmbeddingCheckError("Embedding model changed.")

    monkeypatch.setattr(worker_main, "startup_guard", boom)
    monkeypatch.setattr(worker_main, "main_loop", lambda engine: started.append(engine))
    with pytest.raises(EmbeddingCheckError):
        worker_main.main(engine=object())
    assert started == []
```

Append to `backend/tests/test_cli.py`:

```python
def test_check_embeddings_records(session, cli_engine, monkeypatch, capsys):
    from app.models import EmbeddingMeta

    run_cli(monkeypatch, "check-embeddings")
    assert "Embeddings OK: recorded" in capsys.readouterr().out
    assert session.get(EmbeddingMeta, 1).model == "gemini/gemini-embedding-001"


def test_check_embeddings_mismatch_exits_78(session, cli_engine, monkeypatch, capsys):
    from app.models import EmbeddingMeta
    from tests.helpers import basis_vector, seed_document

    session.add(EmbeddingMeta(id=1, model="ollama/embeddinggemma", dim=1536, scheme="embeddinggemma-v1"))
    session.commit()
    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "check-embeddings")
    assert exit_info.value.code == 78
    assert "Embedding model changed." in capsys.readouterr().err
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_embedding_startup.py tests/test_cli.py -v -k "embedding or lifespan or worker"`
Expected: FAIL (`module 'app.main' has no attribute 'embedding_startup_guard'`, `invalid choice: 'check-embeddings'`, `has no attribute 'main'`).

- [ ] **Step 4: Implement the CLI command**

In `backend/app/cli.py`, add `import sys` to the imports, and add before `main()`:

```python
def check_embeddings_cmd() -> None:
    import logging

    from app.config import resolve_embedding
    from app.services.embedding_check import EX_CONFIG, EmbeddingCheckError, check

    logging.basicConfig(level=logging.INFO)  # Ollama pull progress
    try:
        with Session(engine) as session:
            result = check(session, resolve_embedding(), pull=True)
    except EmbeddingCheckError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(EX_CONFIG)
    print(f"Embeddings OK: {result}")
```

In `main()` register and dispatch it:

```python
    sub.add_parser("check-embeddings", help="verify stored vectors match EMBEDDING_MODEL (pulls the Ollama model)")
```

```python
    elif args.command == "check-embeddings":
        check_embeddings_cmd()
```

- [ ] **Step 5: Implement the API lifespan**

In `backend/app/main.py`:

```python
from contextlib import asynccontextmanager
from pathlib import Path
```

Before `app = FastAPI(...)`:

```python
def embedding_startup_guard() -> None:
    """Refuse to serve when stored vectors don't match EMBEDDING_MODEL (replaced in tests)."""
    from app.db import engine
    from app.services.embedding_check import startup_guard

    startup_guard(engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    embedding_startup_guard()
    yield
```

and `app = FastAPI(title="Origami", lifespan=lifespan)`.

The lifespan must call `embedding_startup_guard()` by its module-global name (as written) so the monkeypatch takes effect.

- [ ] **Step 6: Implement the worker entry point**

Replace `backend/app/worker/__main__.py` with:

```python
import app.worker.pipeline  # noqa: F401  (registers handlers)
from app.db import engine as default_engine
from app.services.embedding_check import startup_guard
from app.worker.pipeline import ensure_sweep_scheduled
from app.worker.runner import main_loop


def main(engine=default_engine) -> None:
    startup_guard(engine)  # refuse to embed with a model that doesn't match the stored vectors
    ensure_sweep_scheduled(engine)
    main_loop(engine)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run tests**

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/cli.py backend/app/main.py backend/app/worker/__main__.py backend/tests
git commit -m "feat: check-embeddings CLI (exit 78) and embedding guard on API and worker startup"
```

---

### Task 6: `reembed` service and CLI

**Files:**
- Create: `backend/app/services/reembed.py`
- Modify: `backend/app/cli.py`
- Test: `backend/tests/test_reembed.py` (create), `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `HNSW_MAX_DIM`, `EmbeddingCheckError`, `ensure_ollama_model`, `set_column_dim`, `create_index`, `write_fingerprint`, `column_dim` (Task 4); `llm.embed(texts, role)` (Task 2); `resolve_embedding` (Task 1).
- Produces: `reembed_all(session, cfg, embed, progress=print, batch_size=100) -> int` (number of chunks); CLI `reembed [--yes]`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_reembed.py`:

```python
import pytest
from sqlalchemy import text
from sqlmodel import select

from app.config import EmbeddingConfig
from app.models import Chunk, EmbeddingMeta
from app.services.embedding_check import EmbeddingCheckError, column_dim, create_index, set_column_dim
from app.services.reembed import reembed_all
from tests.helpers import basis_vector, seed_document

LOCAL_768 = EmbeddingConfig("gemini/test-embedding", 768, "", "", "none")  # remote: no Ollama call


@pytest.fixture
def restore_column(session):
    yield
    session.rollback()
    if column_dim(session) != 1536:
        set_column_dim(session, 1536)
        create_index(session)
        session.commit()


def fake_embed(dim, fail_on_call=None, exc=RuntimeError("provider down")):
    calls = []

    def embed(texts, role="document"):
        calls.append((list(texts), role))
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise exc
        return [[0.5] * dim for _ in texts]

    embed.calls = calls
    return embed


def seed(session, n=3):
    session.add(EmbeddingMeta(id=1, model="gemini/gemini-embedding-001", dim=1536, scheme="none"))
    session.commit()
    return seed_document(session, "A", [{"content": f"chunk {i}", "embedding": basis_vector(i)} for i in range(n)])


def index_exists(session) -> bool:
    return session.execute(text("SELECT to_regclass('ix_chunks_embedding') IS NOT NULL")).scalar_one()


def test_reembed_changes_dimension_and_fills_every_chunk(session, restore_column):
    seed(session)
    embed = fake_embed(768)
    lines = []
    assert reembed_all(session, LOCAL_768, embed, progress=lines.append, batch_size=2) == 3
    assert column_dim(session) == 768
    assert index_exists(session)
    dims = session.execute(text("SELECT vector_dims(embedding) FROM chunks ORDER BY id")).scalars().all()
    assert dims == [768, 768, 768]
    meta = session.get(EmbeddingMeta, 1)
    session.refresh(meta)
    assert (meta.model, meta.dim, meta.scheme) == ("gemini/test-embedding", 768, "none")
    assert lines == ["embedded 2/3", "embedded 3/3"]
    assert {role for _, role in embed.calls} == {"document"}
    assert embed.calls[1][0] == ["chunk 0", "chunk 1"]  # calls[0] is the smoke test


def _assert_untouched(session):
    session.rollback()
    assert column_dim(session) == 1536
    assert index_exists(session)
    first = session.exec(select(Chunk.embedding).order_by(Chunk.id).limit(1)).one()
    assert list(first[:2]) == [1.0, 0.0]
    assert session.get(EmbeddingMeta, 1).dim == 1536


def test_failure_in_later_batch_rolls_back(session, restore_column):
    seed(session)
    with pytest.raises(RuntimeError, match="provider down"):
        reembed_all(session, LOCAL_768, fake_embed(768, fail_on_call=3), progress=lambda _: None, batch_size=2)
    _assert_untouched(session)


def test_interrupt_rolls_back(session, restore_column):
    seed(session)
    with pytest.raises(KeyboardInterrupt):
        reembed_all(session, LOCAL_768, fake_embed(768, fail_on_call=2, exc=KeyboardInterrupt()),
                    progress=lambda _: None)
    _assert_untouched(session)


def test_smoke_test_length_mismatch_aborts(session, restore_column):
    seed(session)
    with pytest.raises(EmbeddingCheckError, match="768"):
        reembed_all(session, LOCAL_768, fake_embed(384), progress=lambda _: None)
    _assert_untouched(session)


def test_dim_over_limit_aborts(session):
    seed(session)
    cfg = EmbeddingConfig("gemini/test-embedding", 3072, "", "", "none")
    with pytest.raises(EmbeddingCheckError, match="2000"):
        reembed_all(session, cfg, fake_embed(3072), progress=lambda _: None)


def test_reembed_with_no_chunks(session, restore_column):
    lines = []
    assert reembed_all(session, LOCAL_768, fake_embed(768), progress=lines.append) == 0
    assert column_dim(session) == 768
    assert session.get(EmbeddingMeta, 1).dim == 768
    assert lines == []


def test_ollama_model_is_ensured(session, restore_column, monkeypatch):
    ensured = []
    monkeypatch.setattr("app.services.reembed.ensure_ollama_model", lambda cfg, pull: ensured.append(pull))
    cfg = EmbeddingConfig("ollama/embeddinggemma", 768, "", "http://localhost:11434", "embeddinggemma-v1")
    reembed_all(session, cfg, fake_embed(768), progress=lambda _: None)
    assert ensured == [True]
```

Append to `backend/tests/test_cli.py`:

```python
@pytest.fixture
def restore_column(session):
    from app.services.embedding_check import column_dim, create_index, set_column_dim

    yield
    session.rollback()
    if column_dim(session) != 1536:
        set_column_dim(session, 1536)
        create_index(session)
        session.commit()


def test_reembed_cli(session, cli_engine, monkeypatch, capsys, restore_column):
    from app.config import get_settings
    from app.models import EmbeddingMeta
    from app.services import llm
    from app.services.embedding_check import column_dim
    from tests.helpers import basis_vector, seed_document

    seed_document(session, "A", [{"content": "a", "embedding": basis_vector(0)}])
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    get_settings.cache_clear()
    monkeypatch.setattr(llm, "embed", lambda texts, role="document": [[0.5] * 768 for _ in texts])
    try:
        run_cli(monkeypatch, "reembed", "--yes")
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "embedded 1/1" in out
    assert "Done. Start origami: sudo systemctl start origami" in out
    assert column_dim(session) == 768
    assert session.get(EmbeddingMeta, 1).dim == 768


def test_reembed_cli_needs_confirmation(session, cli_engine, monkeypatch, restore_column):
    from app.services.embedding_check import column_dim

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(SystemExit, match="Aborted"):
        run_cli(monkeypatch, "reembed")
    assert column_dim(session) == 1536
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_reembed.py tests/test_cli.py -v -k reembed`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.reembed'`

- [ ] **Step 3: Implement the service**

Create `backend/app/services/reembed.py`:

```python
"""Rebuild every chunk vector with the configured embedding model, in one transaction."""

from typing import Callable

from sqlalchemy import text, update
from sqlmodel import Session

from app.config import EmbeddingConfig
from app.models import Chunk
from app.services.embedding_check import (
    HNSW_MAX_DIM, EmbeddingCheckError, create_index, ensure_ollama_model, set_column_dim, write_fingerprint,
)

REEMBED_BATCH_SIZE = 100
SMOKE_TEXT = "Origami embedding check"


def reembed_all(
    session: Session,
    cfg: EmbeddingConfig,
    embed: Callable[..., list[list[float]]],
    progress: Callable[[str], None] = print,
    batch_size: int = REEMBED_BATCH_SIZE,
) -> int:
    """Retype chunks.embedding to cfg.dim and embed every chunk again. Any error rolls everything back."""
    if cfg.dim > HNSW_MAX_DIM:
        raise EmbeddingCheckError(f"EMBEDDING_DIM {cfg.dim} is over the pgvector HNSW limit of {HNSW_MAX_DIM}")
    if cfg.is_ollama:
        ensure_ollama_model(cfg, pull=True)
    probe = embed([SMOKE_TEXT], role="document")
    if len(probe) != 1 or len(probe[0]) != cfg.dim:
        raise EmbeddingCheckError(
            f"Smoke test failed: expected one vector of {cfg.dim} values, got {[len(v) for v in probe]}"
        )
    try:
        rows = session.execute(text("SELECT id, content FROM chunks ORDER BY id")).all()
        set_column_dim(session, cfg.dim)
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            vectors = embed([row.content for row in batch], role="document")
            if len(vectors) != len(batch) or any(len(v) != cfg.dim for v in vectors):
                raise EmbeddingCheckError(f"Embedding batch at chunk {start} returned wrong vectors")
            for row, vector in zip(batch, vectors):
                session.execute(update(Chunk).where(Chunk.id == row.id).values(embedding=vector))
            progress(f"embedded {start + len(batch)}/{len(rows)}")
        create_index(session)
        write_fingerprint(session, cfg)
        session.commit()
    except BaseException:  # includes Ctrl-C: keep the old vectors
        session.rollback()
        raise
    return len(rows)
```

- [ ] **Step 4: Implement the CLI command**

In `backend/app/cli.py`, add before `main()`:

```python
def reembed_cmd(yes: bool) -> None:
    from app.config import resolve_embedding
    from app.services import llm
    from app.services.embedding_check import EX_CONFIG, EmbeddingCheckError
    from app.services.reembed import reembed_all

    cfg = resolve_embedding()
    print(f"Re-embedding every chunk with {cfg.model} ({cfg.dim} dims, scheme {cfg.scheme}).")
    if not yes and input("Stop origami first (sudo systemctl stop origami). Continue? [y/N] ").strip().lower() != "y":
        raise SystemExit("Aborted")
    try:
        with Session(engine) as session:
            count = reembed_all(session, cfg, lambda texts, role: llm.embed(texts, role=role))
    except EmbeddingCheckError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(EX_CONFIG)
    print(f"Re-embedded {count} chunks.")
    print("Done. Start origami: sudo systemctl start origami")
```

(The lambda looks up `llm.embed` at call time so tests can replace it.)

Register in `main()`:

```python
    p_reembed = sub.add_parser("reembed", help="rebuild every vector with EMBEDDING_MODEL (stop origami first)")
    p_reembed.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
```

```python
    elif args.command == "reembed":
        reembed_cmd(args.yes)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/reembed.py backend/app/cli.py backend/tests/test_reembed.py backend/tests/test_cli.py
git commit -m "feat: reembed CLI rebuilds every vector in one transaction"
```

---

### Task 7: Ollama container, launcher, unit and docs

**Files:**
- Modify: `docker-compose.yml`
- Modify: `deploy/origami.sh`
- Modify: `deploy/origami.service`
- Modify: `.env.example`
- Modify: `README.md`

**Interfaces:**
- Consumes: CLI `check-embeddings` (Task 5), `reembed` (Task 6).

- [ ] **Step 1: Compose service**

In `docker-compose.yml`, add after the `db` service (same indentation):

```yaml
  ollama:
    container_name: origami-ollama
    image: ollama/ollama:0.20.4
    restart: unless-stopped
    environment:
      OLLAMA_KEEP_ALIVE: 5m  # unload the model after 5 idle minutes
    ports:
      - "127.0.0.1:11434:11434"
    volumes:
      - ollama:/root/.ollama
```

and add `ollama:` under `volumes:` next to `pgdata:`.

Verify: `docker compose config --quiet && echo ok` → `ok`.

- [ ] **Step 2: Launcher**

In `deploy/origami.sh`:

- `echo "origami: ensuring db container is up"` → `echo "origami: ensuring db and ollama containers are up"`
- `"${COMPOSE[@]}" up -d db` → `"${COMPOSE[@]}" up -d db ollama`
- after the `alembic upgrade head` line add:

```bash

echo "origami: checking embeddings"
# Exit 78 when EMBEDDING_MODEL no longer matches the stored vectors (see README, "Embeddings").
.venv/bin/python -m app.cli check-embeddings
```

`set -e` makes the script exit with the command's status (78).

Verify: `bash -n deploy/origami.sh && echo ok` → `ok`.

- [ ] **Step 3: Unit**

In `deploy/origami.service`, after `RestartSec=5` add:

```ini
# check-embeddings exits 78 on a model mismatch: fail instead of restarting every 5 s.
RestartPreventExitStatus=78
```

Verify: `systemd-analyze verify deploy/origami.service 2>&1 | grep -v '@' || true` shows no error about `RestartPreventExitStatus` (warnings about the `@USER@` placeholders are expected).

- [ ] **Step 4: `.env.example`**

Replace the lines from `# Example: text on Groq, vision and embeddings on Gemini` through `EMBEDDING_API_BASE=` with:

```bash
# Example: text on Groq, vision on Gemini
#   LLM_MODEL=groq/openai/gpt-oss-120b            LLM_API_KEY=<groq key>
#   VISION_MODEL=gemini/gemini-2.5-flash          VISION_API_KEY=<google key>
# See what the text provider offers: cd backend && uv run python -m app.cli list-models
# --- Embeddings ---
# Empty EMBEDDING_MODEL = local EmbeddingGemma on the origami-ollama container (768 dims, no key).
# Remote example: EMBEDDING_MODEL=gemini/gemini-embedding-001  EMBEDDING_DIM=1536  EMBEDDING_API_KEY=<google key>
# Remote models fall back to LLM_API_KEY / LLM_API_BASE when the EMBEDDING_ ones are empty.
# Changing model or dimension later needs: python -m app.cli reembed (see README, "Embeddings").
EMBEDDING_MODEL=
EMBEDDING_DIM=
EMBEDDING_API_KEY=
EMBEDDING_API_BASE=
```

- [ ] **Step 5: README**

5a. In "### Backend", change `docker compose up -d db` to `docker compose up -d db ollama`.

5b. In "## Run as a systemd service", change the first sentence to: `One command starts everything (db and ollama containers, migrations, embedding check, API on` (keep the rest of the sentence). In the "Operate:" block change the comment of `sudo systemctl stop origami` to `# stops api+worker; the db and ollama containers stay up`. After the paragraph that begins "The API address comes from", add:

```markdown
At start the service runs `python -m app.cli check-embeddings`. When the stored
vectors don't match `EMBEDDING_MODEL`, it exits with code 78 and the unit stays
failed (`RestartPreventExitStatus=78`) instead of restarting; `journalctl -u origami`
shows what to do. An existing install must re-install the unit (the `sed … | sudo tee`
command above, then `sudo systemctl daemon-reload`) to get this setting.
```

5c. In "## AI providers", change the intro sentence to "Text, vision and embeddings can use different providers … Example `.env` with text on Groq, vision and embeddings on Gemini:" (keep the example block; it still sets `EMBEDDING_MODEL=gemini/...`; add `EMBEDDING_DIM=1536` after it).

5d. Add a new section after "## AI providers" (before "## Email notifications"):

````markdown
## Embeddings

Search and chat compare vectors ("embeddings") of your documents. With
`EMBEDDING_MODEL` empty, Origami uses **EmbeddingGemma** (multilingual,
768 dimensions) on the `origami-ollama` container, with no API key and no
data leaving the machine. Set `EMBEDDING_MODEL` (and `EMBEDDING_DIM`) to use a
remote provider instead; see "AI providers".

- The container starts with the service (`docker compose up -d ollama` by hand).
  The first start downloads the model (about 600 MB) into the `ollama` volume.
- The model stays in memory (about 1 GB) for 5 minutes after the last use, then
  unloads. The first search after a pause waits 1–3 s while it loads. Change
  `OLLAMA_KEEP_ALIVE` in `docker-compose.yml` to keep it longer.
- Other Ollama embedding models work too, e.g. `EMBEDDING_MODEL=ollama/qwen3-embedding:0.6b`
  with `EMBEDDING_DIM=1024`. `EMBEDDING_DIM` smaller than the model's size keeps
  the first values (Matryoshka models only). The maximum is 2000.

Origami records which model and dimension built the stored vectors. If `.env`
no longer matches, the API, the worker and the systemd service refuse to start
and print the command to run.

### Changing the embedding model

Vectors from different models can't be compared, so every chunk is embedded again
(no OCR, summary or translation runs again):

```bash
sudo systemctl stop origami
# edit EMBEDDING_MODEL / EMBEDDING_DIM in .env
cd backend
uv run python -m app.cli reembed
sudo systemctl start origami
```

`reembed` runs in one transaction: if it fails or you press Ctrl-C, the old
vectors stay. Fix the cause and run it again, or restore `.env`.

Upgrade Origami first and change the model after: on the first start after the
upgrade, Origami records the model in `.env` as the one that built the existing
vectors.

Check without starting the service: `uv run python -m app.cli check-embeddings`.
````

- [ ] **Step 6: Run the whole suite once more**

Run: `cd backend && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml deploy/origami.sh deploy/origami.service .env.example README.md
git commit -m "feat: Ollama container, embedding check at service start; docs for local embeddings and reembed"
```

---

### Task 8: Rollout on this machine (guided, needs the user)

Every command with `sudo` or that deletes something: show it to the user and wait for an explicit yes before running it. Commands the user must run themselves (sudo password) are given as `! <command>`.

- [ ] **Step 1: Check nothing uses host Ollama**

Run: `ss -tnp state established '( dport = :11434 or sport = :11434 )'; grep -rl 11434 /opt/origami/.env 2>/dev/null; docker ps --format '{{.Names}}'`
Expected: no established connections to 11434. Report the result to the user before removing anything.

- [ ] **Step 2: Remove host Ollama (user confirms each line)**

```bash
ollama rm llama3.2:3b
sudo systemctl disable --now ollama
sudo rm /etc/systemd/system/ollama.service
sudo systemctl daemon-reload
sudo rm /usr/local/bin/ollama
sudo rm -rf /usr/local/lib/ollama          # only if it exists
sudo userdel ollama; sudo groupdel ollama  # only if they exist
sudo rm -rf /usr/share/ollama              # model store of the host install
```

Before each `rm`, `ls -la` the target and show it. Verify: `which ollama` prints nothing; `ss -ltn | grep 11434` prints nothing.

- [ ] **Step 3: Merge and start the container**

```bash
git checkout main && git merge --no-ff feat/local-embeddings
docker compose up -d ollama
curl -s localhost:11434/api/version
```

Expected: `{"version":"0.20.4"}`.

- [ ] **Step 4: Live smoke test of the local model (no database change)**

```bash
cd backend && EMBEDDING_MODEL= EMBEDDING_DIM= uv run python - <<'EOF'
from app.config import get_settings, resolve_embedding
from app.services.embedding_check import ensure_ollama_model
from app.services import llm
cfg = resolve_embedding()
ensure_ollama_model(cfg, pull=True)
q = llm.embed(["bolletta della luce"], role="query")[0]
d = llm.embed(["Fattura Enel energia elettrica marzo 2026"], role="document")[0]
print(cfg.model, len(q), round(sum(a*b for a, b in zip(q, d)), 3))
EOF
```

Expected: `ollama/embeddinggemma 768 <similarity>`; first run pulls the model. Then `docker stats --no-stream origami-ollama` shows the memory in use, and after 5 idle minutes it drops back.

- [ ] **Step 5: Re-install the unit and restart on the current Gemini config**

User runs:

```
! sed -e "s|@USER@|$USER|g" -e "s|@REPO_DIR@|$PWD|g" deploy/origami.service | sudo tee /etc/systemd/system/origami.service >/dev/null && sudo systemctl daemon-reload && sudo systemctl restart origami
```

Verify: `journalctl -u origami -n 30 --no-pager | grep -i embedding` shows `Embeddings OK: recorded`; `systemctl is-active origami` → `active`.

- [ ] **Step 6: Ask the user whether to switch this install to the local model now**

If yes: `sudo systemctl stop origami`, empty `EMBEDDING_MODEL` and `EMBEDDING_DIM` in `.env` (show the diff first), `cd backend && uv run python -m app.cli reembed`, `sudo systemctl start origami`, then run a search in the UI. If no: done.

- [ ] **Step 7: Update memory**

Update `/home/matteo/.claude/projects/-opt-origami/memory/origami-deployment.md`: Ollama now runs as the `origami-ollama` compose container (host install removed); service start runs `check-embeddings` and exits 78 on mismatch; model changes need `app.cli reembed` with the service stopped. Do not push; tell the user `main` is merged locally.
