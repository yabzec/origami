import io
import json
import urllib.error

import pytest
from sqlalchemy import text

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


def test_ollama_unreachable_is_transient(session, ollama):
    from app.services.embedding_check import EmbeddingUnavailableError

    ollama.reachable = False
    with pytest.raises(EmbeddingUnavailableError):
        check(session, LOCAL, pull=True)


def _embed_returning(dim):
    def embed(texts, role="document"):
        return [[0.5] * dim for _ in texts]

    return embed


def test_probe_dimension_mismatch_refuses_before_writing(session):
    with pytest.raises(EmbeddingCheckError, match="1536"):
        check(session, GEMINI, pull=True, embed=_embed_returning(768))
    assert session.get(EmbeddingMeta, 1) is None
    assert column_dim(session) == 1536


def test_probe_value_error_is_config_error(session):
    def embed(texts, role="document"):
        raise ValueError("The embedding model returned 768 values but EMBEDDING_DIM is 1536")

    with pytest.raises(EmbeddingCheckError, match="EMBEDDING_DIM is 1536") as err:
        check(session, GEMINI, pull=True, embed=embed)
    from app.services.embedding_check import EmbeddingUnavailableError

    assert not isinstance(err.value, EmbeddingUnavailableError)


def test_probe_provider_error_is_transient(session):
    from app.services.embedding_check import EmbeddingUnavailableError

    def embed(texts, role="document"):
        raise RuntimeError("503 Service Unavailable")

    with pytest.raises(EmbeddingUnavailableError, match="503"):
        check(session, GEMINI, pull=True, embed=embed)


def test_probe_matching_dimension_passes(session):
    assert check(session, GEMINI, pull=True, embed=_embed_returning(1536)) == "recorded"


def test_check_waits_for_concurrent_check(session, engine):
    """API and worker start together: the second check must wait for the first one's transaction."""
    import threading

    from app.services.embedding_check import CHECK_LOCK_ID

    holder = engine.connect()
    holder.begin()
    holder.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHECK_LOCK_ID})
    done = threading.Event()

    def run():
        from sqlmodel import Session

        with Session(engine) as other:
            check(other, GEMINI, pull=False)
        done.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert not done.wait(1.0)  # blocked on the lock
    finally:
        holder.rollback()
        holder.close()
    assert done.wait(10)
    worker.join()
