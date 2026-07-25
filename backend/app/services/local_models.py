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
import threading
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("origami.local_models")

_embedder = None
_embedder_lock = threading.Lock()


def _get_embedder():
    """Load the sentence-transformers model once per process and keep it resident.

    Double-checked locking: `search.run_search` and `chat.chat` are plain `def`
    functions, so Starlette runs them in its threadpool (~40 workers by default).
    Without the lock, two concurrent requests arriving before the first load
    finishes could both see `_embedder is None` and each construct a
    SentenceTransformer — two simultaneous ~2.3GB loads in one process.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
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


def describe_image(image_path: Path, prompt: str) -> str:
    """Answer `prompt` about the image at `image_path` using the local vision model.

    The model is loaded, used, and released within this call. That costs ~20-30s of
    load time per call but keeps ~3.7GB out of the worker's steady-state footprint.
    The trade is right because this path is rare by design: `pipeline._ensure_summary`
    only reaches vision for images with under IMAGE_SUMMARY_TEXT_THRESHOLD chars of
    extracted text (photos), and ingestion is async so the latency is not user-facing.
    """
    import gc

    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM

    settings = get_settings()
    name = settings.vision_model_name
    revision = settings.vision_model_revision

    # Open/validate the image before loading the ~3.7GB model, so a corrupt or
    # missing image fails fast instead of wasting a full model load.
    with Image.open(image_path) as image:
        rgb_image = image.convert("RGB")

    log.info("loading vision model %s (cpu, bfloat16)", name)
    model = AutoModelForCausalLM.from_pretrained(
        name, revision=revision, trust_remote_code=True, torch_dtype=torch.bfloat16
    )
    try:
        answer = model.query(rgb_image, prompt)["answer"]
    finally:
        del model
        gc.collect()
    return answer
