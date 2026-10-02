import base64
import json
import os
import re
import urllib.parse
import urllib.request
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


def parse_description(raw: str | None) -> Description:
    """Parse the describe() JSON reply; fall back to the raw text with no language."""
    raw = raw or ""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        text = text[first : last + 1]  # tolerate prose around the JSON object
    try:
        data = json.loads(text)
        summary = data["summary"]
        language = data.get("language")
    except (ValueError, KeyError, TypeError, AttributeError):
        return Description(raw.strip(), None)
    if not isinstance(summary, str) or not summary.strip():
        return Description(raw.strip(), None)
    return Description(summary.strip(), _normalize_language(language))


def _normalize_language(language: object) -> str | None:
    if not isinstance(language, str):
        return None
    code = re.split(r"[-_]", language.strip(), maxsplit=1)[0].lower()
    return code if re.fullmatch(r"[a-z]{2}", code) else None


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
    prompt = _describe_prompt(get_primary_language())
    if image_path is not None:
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content: str | list = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]
        model = settings.vision_model
        # A separate vision key means another provider: don't send it the text provider's base URL.
        fallback_base = "" if settings.vision_api_key else settings.llm_api_base
        kw = _kw(
            settings.vision_api_key or settings.llm_api_key,
            settings.vision_api_base or fallback_base,
        )
    else:
        content = f"{prompt}\n\n---\n\n{(text or '')[:8000]}"
        model = settings.llm_model
        kw = _kw(settings.llm_api_key, settings.llm_api_base)
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


OPENAI_STYLE_MODEL_URLS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "openai": "https://api.openai.com/v1/models",
}
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
LIST_MODELS_TIMEOUT_SECONDS = 20


def model_provider() -> str:
    return get_settings().llm_model.split("/", 1)[0]


def _models_request() -> tuple[urllib.request.Request, str]:
    """Build the provider's model-list request; second item is the response shape."""
    settings = get_settings()
    provider = model_provider()
    if settings.llm_api_base:
        url, key = f"{settings.llm_api_base.rstrip('/')}/models", settings.llm_api_key
    elif provider in OPENAI_STYLE_MODEL_URLS:
        url = OPENAI_STYLE_MODEL_URLS[provider]
        key = settings.llm_api_key or os.environ.get(f"{provider.upper()}_API_KEY", "")
        if not key:
            raise ValueError(f"No API key for provider '{provider}': set LLM_API_KEY")
    elif provider == "gemini":
        key = settings.llm_api_key or settings.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError(f"No API key for provider '{provider}': set LLM_API_KEY")
        query = urllib.parse.urlencode({"key": key, "pageSize": 1000})
        return urllib.request.Request(f"{GEMINI_MODELS_URL}?{query}"), "gemini"
    else:
        raise ValueError(f"Model listing not supported for provider '{provider}'")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return urllib.request.Request(url, headers=headers), "openai"


def list_models() -> list[dict]:
    """Models offered by the text provider (LLM_MODEL), normalized to id/owner/context_window/active."""
    request, shape = _models_request()
    with urllib.request.urlopen(request, timeout=LIST_MODELS_TIMEOUT_SECONDS) as resp:
        data = json.load(resp)
    if shape == "gemini":
        return [
            {
                "id": (m.get("name") or "").removeprefix("models/") or None,
                "owner": None,
                "context_window": m.get("inputTokenLimit"),
                "active": None,
            }
            for m in data.get("models", [])
        ]
    return [
        {
            "id": m.get("id"),
            "owner": m.get("owned_by"),
            "context_window": m.get("context_window"),
            "active": m.get("active"),
        }
        for m in data.get("data", [])
    ]
