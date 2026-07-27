from pathlib import Path

import litellm

from app.config import get_settings
from app.services import local_models

DESCRIBE_PROMPT = (
    "Describe this document for a searchable personal archive. In 2-4 sentences, "
    "in the document's own language, summarize what it is, its purpose, and key "
    "entities (dates, amounts, names, organizations)."
)


def _kw(key: str, base: str) -> dict:
    kw: dict = {}
    if key:
        kw["api_key"] = key
    if base:
        kw["api_base"] = base
    return kw


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts remotely, one vector per input, in input order.

    Cloudflare Workers AI serves the same BAAI/bge-m3 weights, reached through
    LiteLLM's OpenAI-compatible path. Keeping indexing and querying on one model
    is what makes the stored chunk vectors and the query vector comparable — do
    not split these across providers.
    """
    if not texts:
        return []
    settings = get_settings()
    kw = _kw(settings.embedding_api_key, settings.embedding_api_base)
    resp = litellm.embedding(model=settings.embedding_model, input=texts, **kw)
    data = sorted(resp.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def describe(text: str | None = None, image_path: Path | None = None) -> str:
    if image_path is not None:
        # Vision runs locally; the file never leaves the box.
        return local_models.describe_image(Path(image_path), DESCRIBE_PROMPT).strip()
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    content = f"{DESCRIBE_PROMPT}\n\n---\n\n{(text or '')[:8000]}"
    resp = litellm.completion(
        model=settings.llm_model, messages=[{"role": "user", "content": content}], **kw
    )
    return resp.choices[0].message.content.strip()


def complete(messages: list[dict], stream: bool = False):
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    resp = litellm.completion(model=settings.llm_model, messages=messages, stream=stream, **kw)
    if not stream:
        return resp.choices[0].message.content

    def deltas():
        for chunk in resp:
            piece = chunk.choices[0].delta.content
            if piece:
                yield piece

    return deltas()
