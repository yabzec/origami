# Local Embedding & Vision Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the embedding model and the image-description (vision) model locally in-process on CPU, removing the cloud API dependency from Origami's unattended ingestion pipeline; chat/text completion stays on cloud providers via LiteLLM.

**Architecture:** A new module `backend/app/services/local_models.py` owns the lifecycle of two HuggingFace models and exposes two plain functions. `backend/app/services/llm.py` — the project's single AI boundary and only sanctioned mock point — keeps its public signatures (`embed`, `describe`, `complete`) and swaps the *implementations* of `embed()` and the image branch of `describe()` to call `local_models`. Because signatures do not change, no caller (`app/worker/pipeline.py`, `app/services/search.py`, `app/services/rag.py`) is touched. The embedding model stays resident per process; the vision model loads per call and unloads, to fit 16GB across the two processes that `deploy/origami.sh` starts.

**Tech Stack:** Python 3.10–3.13, FastAPI, SQLModel + Postgres/pgvector, Alembic, uv, pytest. New: `sentence-transformers` (BAAI/bge-m3), `transformers` + `torch` CPU-only (vikhyatk/moondream2), `einops`, `accelerate`.

**Spec:** `docs/superpowers/specs/2026-07-25-local-embedding-vision-design.md`

## Global Constraints

- **Full replace, no fallback.** The cloud embedding path and the cloud vision path are deleted, not made conditional. Do not add a "try local, fall back to cloud" branch anywhere.
- **Chat stays cloud.** `complete()` and the *text* branch of `describe()` keep using `litellm.completion` with `settings.llm_model`. Do not touch them.
- **`app/services/llm.py` is the only sanctioned mock boundary** in tests (see `backend/tests/conftest.py::llm_stub`). New tests may additionally monkeypatch `app.services.local_models` — that is the new internal seam — but must never mock `litellm` for the embedding or vision paths, which no longer use it.
- **Public signatures are frozen:** `embed(texts: list[str]) -> list[list[float]]` and `describe(text: str | None = None, image_path: Path | None = None) -> str`. Callers must not need edits.
- **Embedding dimension is 1024** (`BAAI/bge-m3` dense output). It lives in exactly one place: `EMBEDDING_DIM` in `backend/app/models/chunk.py`. It is a module constant, never a setting.
- **Embedding model:** `BAAI/bge-m3`. **Vision model:** `vikhyatk/moondream2`. No other models.
- **CPU only.** `torch` must resolve to the CPU wheel index `https://download.pytorch.org/whl/cpu`. A venv containing any `nvidia-*` package is a failed install.
- **Default test run stays fast and offline.** Tests that download or load real model weights are marked `slow` and deselected by default.
- All backend commands run from `backend/` using the project venv (`.venv/bin/...`) or `uv run`, matching `deploy/origami.sh`.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/pyproject.toml` | Deps + CPU torch index pin + pytest `slow` marker | 1 |
| `backend/app/services/local_models.py` | **New.** Owns model load/unload; `embed_texts()`, `describe_image()`. Only place torch/transformers/sentence_transformers are imported. | 2, 3 |
| `backend/tests/test_local_models.py` | **New.** `slow`-marked contract tests against real weights. | 2, 3 |
| `backend/app/config.py` | Drop cloud embedding/vision settings; add HF model-id + revision settings | 4 |
| `.env.example` | Document that embedding/vision are local and keyless | 4 |
| `backend/app/services/llm.py` | Delegate `embed()` and `describe()`'s image branch to `local_models` | 4 |
| `backend/tests/test_llm.py` | Rewrite embed/image tests against the new seam; text + `complete()` tests unchanged | 4 |
| `backend/app/models/chunk.py` | `EMBEDDING_DIM` 1536 → 1024 | 5 |
| `backend/tests/conftest.py` | `llm_stub.fake_embed` uses `EMBEDDING_DIM` instead of hardcoded 1536 | 5 |
| `backend/alembic/versions/<rev>_local_embedding_dim.py` | **New.** Wipe chunks, resize vector column, reset summaries | 5 |
| `backend/scripts/reingest_pending.py` | **New.** One-shot: enqueue `process_document` for every `pending` document | 6 |
| `README.md` | First-run model download, RAM expectations, keyless embedding/vision | 7 |

> **Atomicity note for the reviewer:** Tasks 4 and 5 together form one behavioral swap. After Task 4's commit the code emits 1024-dim vectors while the database column is still `vector(1024)`-incompatible; **do not run the app against a real database between Task 4 and Task 5.** Unit tests pass at every commit because they go through the mock boundary. Task 6 is what makes an existing install functional again.

---

## Task 1: Dependencies and the CPU-only torch pin

Establishes the environment the next tasks need, and the `slow` marker that keeps real-weight tests out of the default run. This is its own task because "no CUDA wheels landed in the venv" is a gate worth rejecting independently.

**Files:**
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable venv with `sentence_transformers`, `transformers`, `torch` (CPU), `einops`, `accelerate` importable; a pytest `slow` marker that is deselected by default.

- [ ] **Step 1: Add the runtime dependencies**

In `backend/pyproject.toml`, add these five entries to the existing `[project].dependencies` list (keep the current entries):

```toml
    "sentence-transformers>=3.0",
    "transformers>=4.44",
    "torch>=2.4",
    "einops>=0.8",
    "accelerate>=0.34",
```

`einops` and `accelerate` are required by moondream2's remote modeling code; they are direct dependencies here because we rely on them, even though we never import them ourselves.

- [ ] **Step 2: Pin torch to the CPU wheel index**

Append to `backend/pyproject.toml`. Without this, the default PyPI `torch` on Linux pulls ~2.5GB of `nvidia-*` CUDA wheels that are dead weight on a GPU-less box.

```toml
[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
```

`explicit = true` means this index is used *only* for packages that name it in `[tool.uv.sources]` — every other dependency still resolves from PyPI.

- [ ] **Step 3: Register the `slow` marker and deselect it by default**

Replace the existing `[tool.pytest.ini_options]` block in `backend/pyproject.toml` with:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not slow'"
markers = [
    "slow: loads real ML model weights from disk/network; deselected by default, run with -m slow",
]
filterwarnings = [
    # Upstream FastAPI TestClient uses deprecated httpx API; tracked for removal when FastAPI updates TestClient
    "ignore:Using.*httpx.*with.*starlette.testclient.*is deprecated",
]
```

- [ ] **Step 4: Install and verify no CUDA wheels landed**

Run from `backend/`:

```bash
uv sync
uv pip list | grep -i nvidia
```

Expected: `uv sync` succeeds; the `grep` prints **nothing** and exits 1. If any `nvidia-*` package appears, the index pin in Step 2 is wrong — fix it and re-sync from a clean venv (`rm -rf .venv && uv sync`).

- [ ] **Step 5: Verify the imports work and the default suite still passes**

Run from `backend/`:

```bash
uv run python -c "import torch, transformers, sentence_transformers, einops, accelerate; print(torch.__version__)"
uv run pytest -q
```

Expected: the version prints (a `+cpu` suffix is normal and confirms the CPU build); the existing suite passes unchanged — nothing in this task alters behavior.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "build: add local model deps with CPU-only torch, register slow test marker"
```

---

## Task 2: `local_models.embed_texts`

**Files:**
- Create: `backend/app/services/local_models.py`
- Test: `backend/tests/test_local_models.py` (create)

**Interfaces:**
- Consumes: Task 1's venv.
- Produces: `local_models.embed_texts(texts: list[str]) -> list[list[float]]` — one 1024-float L2-normalized vector per input, in input order. Task 4 calls this from `llm.embed`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_local_models.py`. These are contract tests against real weights — marked `slow`, so they do not run by default.

```python
import math

import pytest

from app.services import local_models

pytestmark = pytest.mark.slow


def test_embed_texts_returns_normalized_1024_dim_vectors_in_order():
    vectors = local_models.embed_texts(["prima frase", "second sentence"])

    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, abs=1e-3)  # normalize_embeddings=True
    assert vectors[0] != vectors[1]  # order preserved, not the same vector twice


def test_embed_texts_places_related_multilingual_text_closer_than_unrelated():
    # Weak semantic assertion: catches a silently wrong model or missing normalization,
    # without asserting on exact float values.
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    invoice_it, invoice_en, unrelated = local_models.embed_texts(
        [
            "Fattura per la fornitura di energia elettrica, importo 120 euro.",
            "Invoice for the supply of electricity, amount 120 euros.",
            "Le ricette della nonna per la torta di mele.",
        ]
    )

    assert cos(invoice_it, invoice_en) > cos(invoice_it, unrelated)


def test_embed_texts_accepts_empty_list():
    assert local_models.embed_texts([]) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run from `backend/`:

```bash
uv run pytest tests/test_local_models.py -m slow -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.services.local_models'`.

- [ ] **Step 3: Write the implementation**

Create `backend/app/services/local_models.py`:

```python
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
```

- [ ] **Step 4: Add the config settings this depends on**

`_get_embedder` reads two settings that do not exist yet. Add them to the `Settings` class in `backend/app/config.py` now (Task 4 removes the obsolete cloud ones):

```python
    embedding_model_name: str = "BAAI/bge-m3"
    embedding_model_revision: str = "main"
```

Named `embedding_model_name`, distinct from the existing `embedding_model`, so a stale `EMBEDDING_MODEL=gemini/...` line in an operator's `.env` cannot silently take effect — `Settings` uses `extra="ignore"` and would otherwise swallow it.

- [ ] **Step 5: Run the test to verify it passes**

Run from `backend/`:

```bash
uv run pytest tests/test_local_models.py -m slow -v
```

Expected: PASS (all three tests). The first run downloads ~2.3GB from HuggingFace and takes several minutes — that is the model cache warming, not a hang.

- [ ] **Step 6: Confirm the default suite is untouched**

```bash
uv run pytest -q
```

Expected: the same passes as before, and the new file's tests are **deselected** (pytest reports "N deselected"). If they ran, Task 1 Step 3's `addopts` is wrong.

- [ ] **Step 7: Record the resolved revision**

The default `revision = "main"` is a moving target. Pin it:

```bash
uv run python -c "
from huggingface_hub import HfApi
print(HfApi().model_info('BAAI/bge-m3').sha)
"
```

Copy the printed commit sha into `.env.example` as a documented, commented-out example (`# EMBEDDING_MODEL_REVISION=<sha>  # pin for reproducibility`). Task 4 writes the surrounding `.env.example` block; add the line there if that task has already run, otherwise note the sha in the commit message so Task 4 can use it.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/local_models.py backend/tests/test_local_models.py backend/app/config.py
git commit -m "feat: add local bge-m3 embedding inference"
```

---

## Task 3: `local_models.describe_image`

**Files:**
- Modify: `backend/app/services/local_models.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_local_models.py`

**Interfaces:**
- Consumes: `local_models` module from Task 2.
- Produces: `local_models.describe_image(image_path: Path, prompt: str) -> str` — a free-text answer about the image. Task 4 calls this from `llm.describe`'s image branch.

- [ ] **Step 1: Determine moondream2's current API and pin the revision**

moondream2 ships its own modeling code in the model repo (`trust_remote_code=True`), and that code's API **changed across revisions**: older revisions expose `encode_image()` + `answer_question(enc, prompt, tokenizer)`, newer ones expose `query(image, question) -> {"answer": ...}`. Find out which one you get before writing the implementation:

```bash
cd backend && uv run python -c "
import torch
from transformers import AutoModelForCausalLM
from huggingface_hub import HfApi
sha = HfApi().model_info('vikhyatk/moondream2').sha
print('revision sha:', sha)
m = AutoModelForCausalLM.from_pretrained(
    'vikhyatk/moondream2', revision=sha, trust_remote_code=True, torch_dtype=torch.bfloat16
)
print('has query:', hasattr(m, 'query'))
print('has answer_question:', hasattr(m, 'answer_question'))
"
```

Record the printed sha — it becomes the `vision_model_revision` default in Step 3. The implementation below uses `query()`. **If `has query` is False and `has answer_question` is True**, replace the single `model.query(...)` line in Step 4 with:

```python
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(name, revision=revision)
        answer = model.answer_question(model.encode_image(image), prompt, tokenizer)
```

and keep everything else identical. Note which API you used in the commit message.

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_local_models.py`:

```python
def test_describe_image_returns_non_empty_text(tmp_path):
    from PIL import Image, ImageDraw

    img_path = tmp_path / "receipt.png"
    image = Image.new("RGB", (640, 320), "white")
    ImageDraw.Draw(image).text((20, 140), "TOTALE 42,00 EUR", fill="black")
    image.save(img_path)

    answer = local_models.describe_image(img_path, "What does this image show?")

    assert isinstance(answer, str)
    assert answer.strip()


def test_describe_image_releases_the_model_after_the_call(tmp_path):
    from PIL import Image

    img_path = tmp_path / "blank.png"
    Image.new("RGB", (64, 64), "white").save(img_path)

    local_models.describe_image(img_path, "Describe this.")

    # The vision model must not be cached anywhere: ~3.7GB of resident RAM per
    # process is the difference between fitting in 16GB and not.
    assert not any(
        name for name in vars(local_models) if name.startswith("_vision")
        and getattr(local_models, name) is not None
    )
```

- [ ] **Step 3: Add the config settings**

Add to the `Settings` class in `backend/app/config.py`, using the sha recorded in Step 1 as the revision default:

```python
    vision_model_name: str = "vikhyatk/moondream2"
    vision_model_revision: str = "<sha printed in Step 1>"
```

Pinning the revision matters more here than for the embedding model: `trust_remote_code=True` means we execute Python from that repo, and a pin makes the executed code stable and reviewable rather than whatever `main` becomes later.

- [ ] **Step 4: Write the implementation**

Append to `backend/app/services/local_models.py`:

```python
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
    log.info("loading vision model %s (cpu, bfloat16)", name)
    model = AutoModelForCausalLM.from_pretrained(
        name, revision=revision, trust_remote_code=True, torch_dtype=torch.bfloat16
    )
    try:
        with Image.open(image_path) as image:
            answer = model.query(image.convert("RGB"), prompt)["answer"]
    finally:
        del model
        gc.collect()
    return answer.strip()
```

Add `from pathlib import Path` to the module's imports at the top of the file.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_local_models.py -m slow -v
```

Expected: PASS (all five tests). First run downloads ~3.7GB.

- [ ] **Step 6: Verify memory actually returns to baseline**

The unload is the load-bearing part of the memory budget, so check it for real rather than trusting the reference-count test:

```bash
cd backend && uv run python -c "
import os, resource
from pathlib import Path
from PIL import Image
from app.services import local_models

Image.new('RGB', (64, 64), 'white').save('/tmp/blank.png')
local_models.describe_image(Path('/tmp/blank.png'), 'Describe this.')
print('peak RSS MB:', resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)
print('current RSS MB:', int(open(f'/proc/{os.getpid()}/statm').read().split()[1]) * 4096 // 1024 // 1024)
"
```

Expected: peak in the ~4000-8000 MB range, current well below peak (roughly 500-1500 MB). If current ≈ peak, the model was not released — check for a stray module-level reference.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/local_models.py backend/tests/test_local_models.py backend/app/config.py
git commit -m "feat: add local moondream2 image description with load-per-call lifecycle"
```

---

## Task 4: Rewire `llm.py` to the local models, and clean up config

Config and `llm.py` change together: removing `settings.embedding_model` while `llm.embed` still reads it would break at runtime, so they are one task.

**Files:**
- Modify: `backend/app/services/llm.py`
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Test: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `local_models.embed_texts(texts)`, `local_models.describe_image(image_path, prompt)` from Tasks 2–3.
- Produces: unchanged public API — `llm.embed(texts) -> list[list[float]]`, `llm.describe(text=None, image_path=None) -> str`, `llm.complete(messages, stream=False)`. No caller changes.

- [ ] **Step 1: Rewrite the embed and image tests**

In `backend/tests/test_llm.py`, **delete** `test_embed_returns_vectors_in_input_order`, `test_describe_image`, and `test_embed_falls_back_to_llm_key` (all three test cloud paths that no longer exist), and add these in their place. Leave `test_describe_text`, `test_complete_non_stream`, `test_complete_stream_yields_deltas`, and `test_complete_passes_api_key_and_base` **exactly as they are** — those cover the chat path, and their passing unmodified is the evidence chat was not disturbed.

```python
def test_embed_delegates_to_local_model(monkeypatch):
    captured = {}

    def fake_embed_texts(texts):
        captured["texts"] = list(texts)
        return [[0.5, 0.5], [0.1, 0.9]]

    monkeypatch.setattr(local_models, "embed_texts", fake_embed_texts)
    vectors = llm.embed(["a", "b"])

    assert vectors == [[0.5, 0.5], [0.1, 0.9]]  # returned unchanged, order preserved
    assert captured["texts"] == ["a", "b"]


def test_embed_makes_no_litellm_call(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("embedding must not reach litellm — it is local now")

    monkeypatch.setattr(litellm, "embedding", explode)
    monkeypatch.setattr(local_models, "embed_texts", lambda texts: [[0.0]] * len(texts))
    assert llm.embed(["a"]) == [[0.0]]


def test_describe_image_delegates_to_local_model(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    captured = {}

    def fake_describe_image(image_path, prompt):
        captured["image_path"] = image_path
        captured["prompt"] = prompt
        return "  Una foto di una ricevuta.  "

    monkeypatch.setattr(local_models, "describe_image", fake_describe_image)
    result = llm.describe(image_path=img)

    assert result == "Una foto di una ricevuta."  # stripped
    assert captured["image_path"] == img
    assert captured["prompt"] == llm.DESCRIBE_PROMPT  # same prompt as the text branch


def test_describe_image_makes_no_litellm_call(monkeypatch, tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")

    def explode(*args, **kwargs):
        raise AssertionError("vision must not reach litellm — it is local now")

    monkeypatch.setattr(litellm, "completion", explode)
    monkeypatch.setattr(local_models, "describe_image", lambda image_path, prompt: "ok")
    assert llm.describe(image_path=img) == "ok"
```

Add `local_models` to the imports at the top of the test file:

```python
from app.services import llm, local_models
```

(replacing the existing `from app.services import llm`).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_llm.py -v
```

Expected: the four new tests FAIL — `llm.embed` still calls `litellm.embedding` (so `test_embed_makes_no_litellm_call` raises the AssertionError) and `llm.describe(image_path=...)` still calls `litellm.completion`. The four retained chat tests PASS.

- [ ] **Step 3: Rewire `llm.py`**

In `backend/app/services/llm.py`:

Replace the `embed` function entirely:

```python
def embed(texts: list[str]) -> list[list[float]]:
    """Embeddings run locally — see app.services.local_models."""
    return local_models.embed_texts(texts)
```

Replace the image branch of `describe`. The function becomes:

```python
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
```

Add `from app.services import local_models` to the imports. Delete the now-unused `import base64`. Keep `from pathlib import Path`, `import litellm`, `_kw`, `DESCRIBE_PROMPT`, and `complete()` as they are — `_kw` is still used by the text branch and `complete()`.

- [ ] **Step 4: Remove the dead config settings**

In `backend/app/config.py`, **delete** these five fields from `Settings` — nothing reads them once the cloud paths are gone:

```python
    vision_model: str = "gemini/gemini-2.5-flash"
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dim: int = 1536
    embedding_api_key: str = ""
    embedding_api_base: str = ""
```

**Keep** `llm_model`, `llm_api_key`, `llm_api_base`, `gemini_api_key` (all still used by the chat path) and the four new `*_model_name` / `*_model_revision` fields added in Tasks 2–3.

- [ ] **Step 5: Update `.env.example`**

Replace the AI block in `.env.example` (from the `# --- LLM provider ...` comment through the `GEMINI_API_KEY=` line) with:

```
# --- Chat / text LLM (LiteLLM model strings select the provider) ---
# Only the chat path is remote. Set the model string + one API key. Examples:
#   Gemini:  LLM_MODEL=gemini/gemini-2.5-flash        LLM_API_KEY=<google key>
#   Groq:    LLM_MODEL=groq/llama-3.3-70b-versatile   LLM_API_KEY=<groq key>
#   OpenAI:  LLM_MODEL=openai/gpt-4o-mini             LLM_API_KEY=<openai key>
#   Ollama:  LLM_MODEL=ollama/llama3.1  LLM_API_BASE=http://localhost:11434  (no key)
LLM_MODEL=gemini/gemini-2.5-flash
LLM_API_KEY=
LLM_API_BASE=
# Legacy: still honored by LiteLLM if set instead of LLM_API_KEY.
GEMINI_API_KEY=
# --- Embedding + vision models run LOCALLY on CPU. No API key needed. ---
# Downloaded from HuggingFace on first use (~2.3GB + ~3.7GB). Set HF_HOME to move the cache.
# Defaults are baked into app/config.py; override only to change models, and note that
# changing EMBEDDING_MODEL_NAME requires a matching EMBEDDING_DIM migration.
# EMBEDDING_MODEL_NAME=BAAI/bge-m3
# EMBEDDING_MODEL_REVISION=<sha from Task 2 Step 7>   # pin for reproducibility
# VISION_MODEL_NAME=vikhyatk/moondream2
# VISION_MODEL_REVISION=<sha from Task 3 Step 1>      # pinned; we execute this repo's code
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_llm.py -v
```

Expected: all eight tests PASS.

- [ ] **Step 7: Confirm callers were genuinely unaffected**

```bash
cd backend && uv run pytest -q
```

Expected: the whole default suite passes with **no edits** to `test_pipeline.py`, `test_rag.py`, `test_search_api.py`, `test_chat_api.py`, or `test_scan_compile.py`. That is the proof the swap is transparent behind the `llm_stub` boundary. If any of them needed changing, the public signatures moved — revisit Step 3.

- [ ] **Step 8: Verify no stale references to the deleted settings remain**

```bash
cd backend && grep -rn "embedding_model\b\|embedding_api\|embedding_dim\|vision_model\b" app/ tests/
```

Expected: no hits (the surviving names are `embedding_model_name`, `embedding_model_revision`, `vision_model_name`, `vision_model_revision`, which the `\b` anchors exclude). Any hit is dead code referencing a field that no longer exists.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/llm.py backend/app/config.py backend/tests/test_llm.py .env.example
git commit -m "feat: route embeddings and vision through local models

Deletes the cloud embedding path and the cloud vision path from llm.py
along with the settings that fed them. The text branch of describe() and
complete() still use LiteLLM, so chat is unchanged. Public signatures are
untouched, so no caller changes."
```

---

## Task 5: Embedding dimension 1536 → 1024 and the data migration

**Files:**
- Modify: `backend/app/models/chunk.py:9`
- Modify: `backend/tests/conftest.py:100`
- Create: `backend/alembic/versions/<rev>_local_embedding_dim.py`

**Interfaces:**
- Consumes: `local_models.embed_texts` producing 1024-dim vectors (Task 2).
- Produces: `EMBEDDING_DIM == 1024` importable from `app.models.chunk`; a `chunks.embedding` column of type `vector(1024)`; documents left in `pending` status for Task 6 to enqueue.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_pipeline.py` (it already uses the `llm_stub` fixture and a real Postgres session):

```python
def test_chunk_embedding_dimension_matches_the_local_model():
    from app.models.chunk import EMBEDDING_DIM
    from app.models import Chunk

    # bge-m3 dense output. The DB column and the model constant must never drift.
    assert EMBEDDING_DIM == 1024
    assert Chunk.__table__.c.embedding.type.dim == 1024
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd backend && uv run pytest tests/test_pipeline.py::test_chunk_embedding_dimension_matches_the_local_model -v
```

Expected: FAIL — `assert 1536 == 1024`.

- [ ] **Step 3: Change the constant and the test fixture**

In `backend/app/models/chunk.py`, change line 9:

```python
EMBEDDING_DIM = 1024  # BAAI/bge-m3 dense output; must match the migrated column type
```

In `backend/tests/conftest.py`, the `llm_stub` fixture hardcodes the old dimension. Replace:

```python
    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1] * 1536 for _ in texts]
```

with:

```python
    def fake_embed(texts):
        calls["embed"].append(list(texts))
        return [[0.1] * EMBEDDING_DIM for _ in texts]
```

and add the import at the top of `conftest.py`:

```python
from app.models.chunk import EMBEDDING_DIM
```

This is why the hardcoded literal was a latent bug: the fixture could silently disagree with the column.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/local_embedding_dim_local_embedding_dim.py` — or generate the filename with `uv run alembic revision -m "local embedding dim"` and fill in the body below, which is the preferred route since it stamps a real revision id. Set `down_revision = "616180658622"`.

```python
"""switch chunks.embedding to vector(1024) for the local bge-m3 model

Revision ID: <generated>
Revises: 616180658622

The embedding dimension AND the vector space both change when moving from
gemini-embedding-001 (1536) to BAAI/bge-m3 (1024), so every existing embedding is
invalid — this is not optional cleanup. Derived data (chunks, summaries) is wiped and
rebuilt from the original files, which are untouched in storage. Documents are left
`pending` so the normal `process_document` worker task regenerates them; see
scripts/reingest_pending.py.

Searchable PDFs in storage are NOT invalidated — they come from Tesseract, which is
unaffected by this change.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "616180658622"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HNSW_INDEX = (
    "CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)")
    op.execute(_HNSW_INDEX)
    op.execute(
        "UPDATE documents SET summary = NULL, status = 'pending', error_message = NULL"
    )


def downgrade() -> None:
    """Downgrade schema.

    Cannot restore the old vectors — they are deleted, and re-deriving them needs the
    cloud embedding provider this change removed. Reverts the column type only.
    """
    op.execute("DELETE FROM chunks")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536)")
    op.execute(_HNSW_INDEX)
    op.execute(
        "UPDATE documents SET summary = NULL, status = 'pending', error_message = NULL"
    )
```

The index name and definition are copied from `9f9c707d06be_initial_schema.py`, which created it as `USING hnsw (embedding vector_cosine_ops)`. It must be dropped before the type change and recreated identically after.

- [ ] **Step 5: Apply the migration and verify the schema**

```bash
cd backend && uv run alembic upgrade head
docker compose -f ../docker-compose.yml exec -T db psql -U origami -d origami -c "\d chunks"
```

Expected: `embedding` shows type `vector(1024)`, and `ix_chunks_embedding` is listed as an hnsw index.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && uv run pytest -q
```

Expected: the whole default suite passes, including the new dimension test.

- [ ] **Step 7: Verify the downgrade path works**

A migration whose downgrade has never been run is a migration that does not have a downgrade.

```bash
cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head
```

Expected: both succeed without error.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/chunk.py backend/tests/conftest.py backend/tests/test_pipeline.py backend/alembic/versions/
git commit -m "feat: migrate chunk embeddings to 1024 dims for local bge-m3

Wipes all chunks and resets summaries: both the dimension and the vector
space change, so existing embeddings cannot be reused. Documents are left
pending for re-ingestion."
```

---

## Task 6: Re-ingestion script

The migration resets state but must not enqueue work — Alembic cannot depend on the worker. This script closes that gap by reusing the existing `process_document` task rather than adding a parallel pipeline.

**Files:**
- Create: `backend/scripts/reingest_pending.py`

**Interfaces:**
- Consumes: `enqueue(session, job_type, payload) -> Job` from `app/services/jobs.py:25` — the same helper `app/api/uploads.py:111` uses after an upload. Note it commits the session itself.
- Produces: a runnable script that moves every `pending` document through the normal pipeline.

- [ ] **Step 1: Write the script**

Create `backend/scripts/reingest_pending.py`. This reuses the existing `enqueue` helper and the `process_document` task registered at `app/worker/pipeline.py:32` — no new pipeline code, and the payload shape (`{"document_id": <str uuid>}`) is copied from `app/api/uploads.py:111`.

```python
"""Enqueue a process_document job for every pending document.

One-shot companion to the vector-dimension migration, which resets documents to
`pending` without enqueuing work (Alembic must not depend on the worker). Safe to
re-run: it only ever looks at documents already in `pending`.

Usage, from backend/:  uv run python -m scripts.reingest_pending
"""

import logging

from sqlmodel import Session, create_engine, select

from app.config import get_settings
from app.models import DocStatus, Document
from app.services.jobs import enqueue

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("origami.reingest")


def main() -> None:
    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        pending = session.exec(
            select(Document).where(Document.status == DocStatus.pending)
        ).all()
        log.info("found %d pending documents", len(pending))
        for doc in pending:
            # `enqueue` commits per job, so an interrupted run leaves the jobs it
            # already created — re-running only picks up what is still pending.
            enqueue(session, "process_document", {"document_id": str(doc.id)})
            log.info("enqueued %s (%s)", doc.id, doc.title)
    log.info("done; the worker will process them")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and watch the worker**

With the worker running (`uv run python -m app.worker` in another shell, or the systemd service up):

```bash
cd backend && uv run python -m scripts.reingest_pending
```

Expected: it logs the pending count and one line per document. In the worker log, each document loads the local embedding model once (first document only), embeds chunks, and reaches `ready`.

- [ ] **Step 3: Verify the data end-to-end**

```bash
docker compose -f ../docker-compose.yml exec -T db psql -U origami -d origami -c "
SELECT status, count(*) FROM documents GROUP BY status;
SELECT count(*) AS chunks, count(embedding) AS embedded, vector_dims(embedding) AS dims
FROM chunks GROUP BY dims;"
```

Expected: all documents `ready` (or `failed` with a real, explicable error); `chunks == embedded`; `dims == 1024`.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/reingest_pending.py
git commit -m "feat: add reingest script for pending documents after the dim migration"
```

---

## Task 7: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: setup docs that match reality.

- [ ] **Step 1: Read the current setup section**

```bash
grep -n "API_KEY\|GEMINI\|EMBEDDING\|setup\|Setup\|install" README.md
```

Find where env configuration and first-run setup are described; the new content belongs there, in the existing style.

- [ ] **Step 2: Document the local models**

Add a subsection covering exactly these points:

- Embedding (`BAAI/bge-m3`) and image description (`vikhyatk/moondream2`) run **locally on CPU**; **no API key is needed** for them. Only chat/text needs a provider key (`LLM_API_KEY`).
- **First run downloads ~6GB** of model weights from HuggingFace (~2.3GB embedding + ~3.7GB vision), cached under `~/.cache/huggingface`. Set `HF_HOME` to relocate the cache. The first ingestion after a fresh install is slow for this reason, and the first image description is slower still.
- **RAM:** expect ~2.3GB resident in each of the two processes (`uvicorn` and the worker both embed), plus a transient ~3.7GB in the worker while an image is being described. Steady ~4.6GB, peak ~8.3GB. A 16GB box running the Postgres container alongside is the tested configuration; less than that is not recommended.
- **Changing `EMBEDDING_MODEL_NAME`** requires a new Alembic migration to match the new dimension, and a full re-embed — it is not a config-only switch.
- `VISION_MODEL_REVISION` is pinned because moondream2 executes code from its own HF repo (`trust_remote_code`); do not unpin it casually.

- [ ] **Step 3: Verify the docs against a clean read**

Re-read the section as if setting up fresh: is the key requirement (only chat needs a key), the disk cost, and the RAM cost all findable? Fix anything ambiguous.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document local embedding and vision models, disk and RAM costs"
```

---

## Final Verification (spec §9, run after Task 7)

Not a task — the acceptance run for the whole change. Do it on the real deployment.

- [ ] Fresh venv: `rm -rf backend/.venv && cd backend && uv sync && uv pip list | grep -i nvidia` prints nothing.
- [ ] `uv run alembic upgrade head`; `chunks.embedding` is `vector(1024)`.
- [ ] `uv run python -m scripts.reingest_pending`; all documents reach `ready`, chunks are 1024-dim, summaries regenerated.
- [ ] Ingest a **text-bearing Italian PDF** → summary comes from the cloud text model, chunks embedded locally.
- [ ] Ingest a **photo with no text** → worker logs the moondream2 load, produces an Italian description, and worker RSS returns near baseline afterwards.
- [ ] Semantic search returns sensible hits for both Italian and English queries; RAG chat answers stay grounded.
- [ ] **Disconnect the network.** Embedding and image description still work; only chat fails. This is the entire point of the change, so it is the acceptance test.
- [ ] `cd backend && uv run pytest -q` (fast suite) and `uv run pytest -m slow -q` (real weights) both pass.
