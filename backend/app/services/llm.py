import base64
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Callable

import litellm

from app.config import get_primary_language, get_settings

LANGUAGE_NAMES = {"it": "Italian", "en": "English", "de": "German", "fr": "French", "es": "Spanish"}

log = logging.getLogger("origami.llm")


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


def _describe_prompt(target_language: str) -> str:
    return (
        "You are indexing a document for a searchable personal archive. "
        'Reply with ONLY a JSON object: {"summary": "..."}. '
        f'"summary": 2-4 sentences written in {language_name(target_language)} describing '
        "what the document is, its purpose, and key entities (dates, amounts, names, "
        "organizations)."
    )


def parse_summary(raw: str | None) -> str:
    """Summary from the describe() JSON reply; falls back to the raw text."""
    raw = raw or ""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        text = text[first : last + 1]  # tolerate prose around the JSON object
    try:
        summary = json.loads(text)["summary"]
    except (ValueError, KeyError, TypeError):
        return raw.strip()
    if not isinstance(summary, str) or not summary.strip():
        return raw.strip()
    return summary.strip()


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


def describe(text: str | None = None, image_path: Path | None = None) -> str:
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
    return parse_summary(resp.choices[0].message.content)


WINDOW_SECONDS = 60.0
RATE_LIMIT_RETRIES = 5
DEFAULT_RATE_LIMIT_WAIT = 60.0
CHARS_PER_TOKEN = 3.5

_clock: Callable[[], float] = time.monotonic  # replaced in tests
_sleep: Callable[[float], None] = time.sleep


class TokenBudget:
    """Sliding 60 s token window for one process. limit <= 0 disables throttling."""

    def __init__(self, limit_per_minute: int, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.limit = limit_per_minute
        self._clock, self._sleep = clock, sleep
        self._events: deque[list] = deque()  # [timestamp, tokens]; tokens corrected after the call
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> list | None:
        if self.limit <= 0:
            return None
        tokens = min(tokens, self.limit)  # one oversized call must not wait forever
        while True:
            with self._lock:
                now = self._clock()
                while self._events and now - self._events[0][0] >= WINDOW_SECONDS:
                    self._events.popleft()
                if sum(e[1] for e in self._events) + tokens <= self.limit:
                    entry = [now, tokens]
                    self._events.append(entry)
                    return entry
                wait = self._events[0][0] + WINDOW_SECONDS - now
            self._sleep(max(wait, 0.05))


_translate_budget: TokenBudget | None = None


def reset_translate_budget() -> None:
    global _translate_budget
    _translate_budget = None


def _budget() -> TokenBudget:
    global _translate_budget
    limit = get_settings().llm_tpm_limit
    if _translate_budget is None or _translate_budget.limit != limit:
        _translate_budget = TokenBudget(limit, lambda: _clock(), lambda s: _sleep(s))
    return _translate_budget


def _estimate_tokens(model: str, text: str) -> int:
    """Input tokens times two: the translation is about as long as the source."""
    try:
        tokens = litellm.token_counter(model=model, text=text)
    except Exception:
        tokens = len(text) / CHARS_PER_TOKEN
    return math.ceil(tokens * 2)


def rate_limit_wait(exc: Exception) -> float:
    """Seconds to wait after a 429: retry-after header, then the provider's message, then 60 s."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        pass
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if match:
        return int(match.group(1) or 0) * 60 + float(match.group(2))
    return DEFAULT_RATE_LIMIT_WAIT


def translate(text: str, target_language: str) -> str:
    settings = get_settings()
    kw = _kw(settings.llm_api_key, settings.llm_api_base)
    prompt = (
        f"Translate the following text into {language_name(target_language)}. Preserve line "
        "breaks, numbers, names, and dates. Output only the translation, with no comments."
    )
    content = f"{prompt}\n\n---\n\n{text}"
    budget = _budget()
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        entry = budget.acquire(_estimate_tokens(settings.llm_model, content))
        try:
            resp = litellm.completion(
                model=settings.llm_model, messages=[{"role": "user", "content": content}], **kw
            )
        except litellm.RateLimitError as exc:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            wait = rate_limit_wait(exc)
            log.warning("Translation rate-limited; retrying in %.1fs (%d/%d)", wait, attempt + 1, RATE_LIMIT_RETRIES)
            _sleep(wait)
            continue
        actual = getattr(getattr(resp, "usage", None), "total_tokens", None)
        if entry is not None and isinstance(actual, int):
            entry[1] = actual
        return resp.choices[0].message.content.strip()
    raise AssertionError("unreachable")


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


SELECT_HISTORY_MESSAGES = 4
SELECT_MAX_DOCUMENTS = 10


def _select_prompt(question: str, history: list[dict], candidates: list[dict]) -> str:
    lines = "\n".join(
        f"{n}. {c['id']} | {c['title']} | {c['document_date']} | {c['doc_type']} | {c['summary_line']}"
        for n, c in enumerate(candidates, start=1)
    )
    recent = history[-SELECT_HISTORY_MESSAGES:]
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in recent) or "(no earlier messages)"
    return (
        "You pick which documents of a personal archive a chat question refers to.\n"
        'Reply with ONLY a JSON object: {"document_ids": ["<id>", ...]}, listing the ids of the '
        "documents the user is asking about or that are needed to answer. "
        'Reply {"document_ids": []} if none apply.\n\n'
        f"Candidate documents (id | title | date | type | summary):\n{lines}\n\n"
        f"Recent conversation:\n{conversation}\n\n"
        f"Question: {question}"
    )


def parse_document_ids(raw: str | None, candidate_ids: list[str]) -> list[str]:
    """Candidate ids from the first {...} block of the reply, in order, de-duplicated, max 10."""
    match = re.search(r"\{.*?\}", raw or "", re.DOTALL)
    if match is None:
        raise ValueError("no JSON object in the preflight reply")
    data = json.loads(match.group(0))
    ids = data.get("document_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        raise ValueError("document_ids is not a list")
    allowed = {cid.lower(): cid for cid in candidate_ids}
    selected: list[str] = []
    for item in ids:
        if not isinstance(item, str):
            continue
        cid = allowed.get(item.strip().lower())
        if cid is not None and cid not in selected:
            selected.append(cid)
            if len(selected) == SELECT_MAX_DOCUMENTS:
                break
    return selected


def select_documents(question: str, history: list[dict], candidates: list[dict]) -> list[str]:
    """Chat preflight: ids of the candidates the question is about. Never raises: [] on failure."""
    if not candidates:
        return []
    try:
        settings = get_settings()
        kw = _kw(settings.llm_api_key, settings.llm_api_base)
        resp = litellm.completion(
            model=settings.llm_model,
            messages=[{"role": "user", "content": _select_prompt(question, history, candidates)}],
            **kw,
        )
        return parse_document_ids(resp.choices[0].message.content, [c["id"] for c in candidates])
    except Exception:
        log.exception("document preflight failed")
        return []


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
