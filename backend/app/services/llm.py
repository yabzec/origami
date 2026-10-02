import base64
import json
from pathlib import Path
from typing import NamedTuple

import litellm

from app.config import get_primary_language, get_settings

LANGUAGE_NAMES = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


class Description(NamedTuple):
    summary: str
    language: str | None  # ISO 639-1 of the document's own language; None if unknown


def _describe_prompt(target_language: str) -> str:
    return (
        "You are indexing a document for a searchable personal archive. "
        'Reply with ONLY a JSON object: {"summary": "...", "language": "xx"}. '
        f'"summary": 2-4 sentences written in {language_name(target_language)} describing '
        "what the document is, its purpose, and key entities (dates, amounts, names, "
        'organizations). "language": the ISO 639-1 code of the document\'s own language '
        '(for example "it", "en", "de").'
    )


def parse_description(raw: str) -> Description:
    """Parse the describe() JSON reply; fall back to the raw text with no language."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
        summary = str(data["summary"]).strip()
        language = data.get("language")
    except (ValueError, KeyError, TypeError, AttributeError):
        return Description(raw.strip(), None)
    if not summary:
        return Description(raw.strip(), None)
    if isinstance(language, str) and language.strip():
        return Description(summary, language.strip().lower()[:2])
    return Description(summary, None)


def _kw(key: str, base: str) -> dict:
    kw: dict = {}
    if key:
        kw["api_key"] = key
    if base:
        kw["api_base"] = base
    return kw


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    kw = _kw(
        settings.embedding_api_key or settings.llm_api_key,
        settings.embedding_api_base or settings.llm_api_base,
    )
    resp = litellm.embedding(
        model=settings.embedding_model, input=texts, dimensions=settings.embedding_dim, **kw
    )
    data = sorted(resp.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def describe(text: str | None = None, image_path: Path | None = None) -> Description:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = _describe_prompt(get_primary_language())
    if image_path is not None:
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content: str | list = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]
        model = settings.vision_model
    else:
        content = f"{prompt}\n\n---\n\n{(text or '')[:8000]}"
        model = settings.llm_model
    resp = litellm.completion(model=model, messages=[{"role": "user", "content": content}], **kw)
    return parse_description(resp.choices[0].message.content)


def translate(text: str, target_language: str) -> str:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = (
        f"Translate the following text into {language_name(target_language)}. Preserve line "
        "breaks, numbers, names, and dates. Output only the translation, with no comments."
    )
    resp = litellm.completion(
        model=settings.llm_model,
        messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{text}"}],
        **kw,
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
