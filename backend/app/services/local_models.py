"""Local, in-process inference for embeddings and image description.

The only module that imports torch / transformers / sentence-transformers. Those
imports are deferred into the loader functions so that importing this module (which
`app.services.llm` does at import time) never pulls in torch — API startup and test
collection stay fast.

Lifecycle differs per model, deliberately:
  * the embedding model is resident (hot path: every search query and every chunk);
  * the vision model loads per call and is released (see `describe_image`).
"""

import logging

from app.config import get_settings

log = logging.getLogger("origami.local_models")

_embedder = None


def _get_embedder():
    """Load the sentence-transformers model once per process and keep it resident."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        settings = get_settings()
        log.info("loading embedding model %s (cpu)", settings.embedding_model_name)
        _embedder = SentenceTransformer(
            settings.embedding_model_name,
            revision=settings.embedding_model_revision,
            device="cpu",
        )
    return _embedder


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts into unit-norm dense vectors, one per input, in input order.

    bge-m3 needs no query/passage instruction prefix, so indexing text and query
    text are embedded identically — which is what the shared `llm.embed` signature
    requires.
    """
    if not texts:
        return []
    vectors = _get_embedder().encode(
        texts,
        normalize_embeddings=True,
        batch_size=8,  # small: CPU inference, 8192-token context model
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]
