import base64
from pathlib import Path

import litellm

from app.config import get_settings

DESCRIBE_PROMPT = (
    "Describe this document for a searchable personal archive. In 2-4 sentences, "
    "in the document's own language, summarize what it is, its purpose, and key "
    "entities (dates, amounts, names, organizations)."
)


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    resp = litellm.embedding(
        model=settings.embedding_model, input=texts, dimensions=settings.embedding_dim
    )
    data = sorted(resp.data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def describe(text: str | None = None, image_path: Path | None = None) -> str:
    settings = get_settings()
    if image_path is not None:
        suffix = Path(image_path).suffix.lstrip(".").lower() or "png"
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        content: str | list = [
            {"type": "text", "text": DESCRIBE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{b64}"}},
        ]
        model = settings.vision_model
    else:
        content = f"{DESCRIBE_PROMPT}\n\n---\n\n{(text or '')[:8000]}"
        model = settings.llm_model
    resp = litellm.completion(model=model, messages=[{"role": "user", "content": content}])
    return resp.choices[0].message.content.strip()
