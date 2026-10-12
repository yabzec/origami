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
