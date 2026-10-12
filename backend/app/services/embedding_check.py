"""Startup check: the vectors in chunks.embedding must come from the configured embedding model."""

import json
import logging
import urllib.request
from typing import Callable

from sqlalchemy import text
from sqlmodel import Session

from app.config import EmbeddingConfig, resolve_embedding
from app.models import EmbeddingMeta
from app.models.user import utcnow

log = logging.getLogger("origami.embeddings")

EX_CONFIG = 78  # sysexits.h; origami.service has RestartPreventExitStatus=78
EX_UNAVAILABLE = 69  # transient: systemd restarts the service and the check runs again
CHECK_LOCK_ID = 0x0E3BED  # pg advisory lock: API and worker startup checks run one at a time
SMOKE_TEXT = "Origami embedding check"
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


class EmbeddingUnavailableError(EmbeddingCheckError):
    """The embedding service did not answer; retrying later may work."""


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
        raise EmbeddingUnavailableError(
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
        raise EmbeddingUnavailableError(f"Pulling {cfg.ollama_name} from {base} failed: {exc}") from exc


def probe_dimension(cfg: EmbeddingConfig, embed: Callable[..., list[list[float]]]) -> None:
    """Embed one string and check the model gives cfg.dim values."""
    try:
        vectors = embed([SMOKE_TEXT], role="document")
    except ValueError as exc:  # fit_dimension: the model gives fewer values than EMBEDDING_DIM
        raise EmbeddingCheckError(str(exc)) from exc
    except Exception as exc:
        raise EmbeddingUnavailableError(f"Embedding model {cfg.model} did not answer: {exc}") from exc
    if len(vectors) != 1 or len(vectors[0]) != cfg.dim:
        raise EmbeddingCheckError(
            f"Smoke test failed: expected one vector of {cfg.dim} values, got {[len(v) for v in vectors]}"
        )


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


def check(
    session: Session, cfg: EmbeddingConfig, pull: bool, embed: Callable[..., list[list[float]]] | None = None
) -> str:
    """Compare the stored vectors with `cfg`: "ok", "recorded" (first run) or "resized" (empty archive).

    With `embed`, one probe call first proves the model gives cfg.dim values.
    """
    if cfg.dim > HNSW_MAX_DIM:
        raise EmbeddingCheckError(f"EMBEDDING_DIM {cfg.dim} is over the pgvector HNSW limit of {HNSW_MAX_DIM}")
    if cfg.is_ollama:
        ensure_ollama_model(cfg, pull)
    if embed is not None:
        probe_dimension(cfg, embed)
    # Held until commit/rollback: a concurrent check sees this one's fingerprint and column.
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHECK_LOCK_ID})
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
            if result == "ok":
                result = "recorded"
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
    session.commit()  # releases the lock
    return "ok"


def startup_guard(engine) -> None:
    """API and worker startup: same check without pulling. Raises EmbeddingCheckError."""
    with Session(engine) as session:
        result = check(session, resolve_embedding(), pull=False)
    log.info("Embedding check: %s", result)
