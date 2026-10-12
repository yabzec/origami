"""Rebuild every chunk vector with the configured embedding model, in one transaction."""

from typing import Callable

from sqlalchemy import text, update
from sqlmodel import Session

from app.config import EmbeddingConfig
from app.models import Chunk
from app.services.embedding_check import (
    HNSW_MAX_DIM, EmbeddingCheckError, create_index, ensure_ollama_model, probe_dimension, set_column_dim,
    write_fingerprint,
)

REEMBED_BATCH_SIZE = 100


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
    probe_dimension(cfg, embed)
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
