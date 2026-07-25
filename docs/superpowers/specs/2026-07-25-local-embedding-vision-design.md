# Origami — Local Embedding & Vision Models

**Date:** 2026-07-25
**Status:** Approved by user (brainstorming session)

## 1. Overview

Move the **embedding model** and the **vision (image description) model** from cloud APIs to
in-process local inference on the self-hosted box. Text chat/completion stays on cloud providers
via LiteLLM.

**Why:** reliability and independence. The embedding and vision calls are the two pipeline steps
that run unattended on every ingested document; a cloud outage, quota, or key rotation currently
fails ingestion. Both model classes are small enough to run on CPU. Text chat is interactive,
user-initiated, and benefits from large frontier models, so it stays remote.

### What is already in place (not re-done)

- `backend/app/services/llm.py` is already the single AI boundary: `embed()`, `describe()`,
  `complete()`. It is also the project's **only sanctioned mock boundary** in tests
  (`backend/tests/conftest.py::llm_stub`). This spec changes the *implementations* of `embed()`
  and the image branch of `describe()`; the public signatures do not change, so every caller
  (`app/worker/pipeline.py`, `app/services/search.py`, `app/services/rag.py`) is untouched.
- Multi-provider config via LiteLLM model strings already works for chat (verified: swapping
  `LLM_MODEL` to `groq/...` is a config-only change). That mechanism is retained for chat only.
- The pipeline already prefers extracted text over sending an image to vision: `_ensure_summary`
  only calls `describe(image_path=...)` when a document's OCR/native text is under
  `IMAGE_SUMMARY_TEXT_THRESHOLD` (40 chars). This makes the vision path rare, which the memory
  strategy in §4 depends on.

### Decisions taken as given (from the brainstorming session)

| Question | Choice |
|---|---|
| Deploy target | Same box as backend; 16GB RAM, **no GPU** |
| Scope | **Full replace** — drop the cloud embedding and cloud vision paths entirely, no fallback |
| Document languages | Mixed Italian + English → multilingual models required |
| Runtime | **In-process Python** (`sentence-transformers` / `transformers`), not a separate Ollama service |
| Embedding model | `BAAI/bge-m3` (568M, 1024-dim, 8192-token context) |
| Vision model | `vikhyatk/moondream2` (~1.86B, follows free-text prompts) |
| Existing data | Little/test data → **wipe chunks and re-ingest**, no dedicated backfill job |

Florence-2 was considered and rejected: it accepts only fixed task tokens
(`<DETAILED_CAPTION>`, `<OCR>`, …) rather than free-text instructions, and captions in English
only. The current `DESCRIBE_PROMPT` requires 2–4 sentences *in the document's own language*
naming dates, amounts, and organizations — Florence-2 structurally cannot satisfy that.
Moondream2 accepts a free-text question and is the smallest model that can.

## 2. New module: `backend/app/services/local_models.py`

One module, one job: own the lifecycle of the local models and expose two plain functions. It is
the only place `torch` / `transformers` / `sentence_transformers` are imported.

```python
def embed_texts(texts: list[str]) -> list[list[float]]      # 1024-dim, L2-normalized
def describe_image(image_path: Path, prompt: str) -> str     # free-text answer about the image
```

- **Imports are deferred** into the function bodies (or a module-level loader), so importing
  `local_models` — which `llm.py` does at import time — never pulls in torch. Test collection and
  API startup stay fast.
- **Embedding model: resident.** Loaded once per process on first `embed_texts()` call, cached in
  a module global. Used on nearly every request path, so keeping it warm is correct.
  `SentenceTransformer("BAAI/bge-m3", device="cpu")`, called with
  `encode(texts, normalize_embeddings=True, batch_size=<small>)`. bge-m3 needs **no** query/passage
  instruction prefix, so `embed_texts` treats indexing and query text identically — which is what
  the shared `embed()` signature requires.
- **Vision model: load-per-call, then unload.** Loaded inside `describe_image`, used, then dropped
  (delete the reference, `gc.collect()`) before returning. Loaded in `bfloat16`. This trades
  ~20–30s of load time per call for ~3.7GB of steady-state RAM — the right trade because the
  vision path only fires for images with no usable extracted text (see §1), i.e. photos, not the
  common case. Ingestion is already async in the worker, so the latency is not user-facing.
- **Model weights** are downloaded by the HuggingFace hub on first use into the standard cache.
  `HF_HOME` is settable via env for operators who want the cache on a specific volume; the default
  (`~/.cache/huggingface`) is fine.
- **Failure mode:** no cloud fallback exists by design. A download or load failure raises, the
  worker marks the document `failed` with the error message (existing `process_document`
  behavior), and the error is logged. Query-time embedding failure surfaces as a 500 from search —
  acceptable and honest for a single-user self-hosted app; a silent degradation to keyword-only
  search would hide a broken install.

### Memory budget

`deploy/origami.sh` runs **two** processes: `uvicorn app.main:app` and `python -m app.worker`.
The API process embeds search/RAG queries; the worker embeds chunks and describes images. So
bge-m3 is loaded twice — once per process — and that is accepted (the alternative, a third
inference service, contradicts the in-process decision).

| | API process | Worker process |
|---|---|---|
| bge-m3 (fp32, resident) | ~2.3 GB | ~2.3 GB |
| moondream2 (bf16, transient) | — | ~3.7 GB during a vision call only |

Steady state ≈ 4.6 GB, peak ≈ 8.3 GB, on a 16 GB box also running Postgres in Docker. Fits.

## 3. Changes to `backend/app/services/llm.py`

- `embed(texts)` → delegates to `local_models.embed_texts(texts)`. The `litellm.embedding()` call,
  the `dimensions=` argument, and the `_kw(...)` embedding-credential resolution are deleted.
- `describe(text=..., image_path=...)` → the **image branch** calls
  `local_models.describe_image(image_path, DESCRIBE_PROMPT)`. The base64 data-URL construction and
  the vision `litellm.completion()` call are deleted. The **text branch is unchanged** (still
  `litellm.completion` with `settings.llm_model`).
- `complete()` unchanged.
- `DESCRIBE_PROMPT` stays a single shared constant used by both branches — keeping one prompt means
  summaries read consistently whether they came from text or from an image.

## 4. Config changes (`backend/app/config.py`, `.env.example`)

**Removed** (no longer meaningful — nothing reads them once the cloud paths are gone):
`vision_model`, `embedding_model`, `embedding_dim`, `embedding_api_key`, `embedding_api_base`.

**Added:**

- `embedding_model_name: str = "BAAI/bge-m3"` — HuggingFace repo id, not a LiteLLM model string.
  Named distinctly from the deleted `embedding_model` so a stale `EMBEDDING_MODEL=gemini/...` line
  in someone's `.env` cannot silently take effect (`extra="ignore"` would otherwise swallow it).
- `vision_model_name: str = "vikhyatk/moondream2"` — same reasoning.

**Retained:** `llm_model`, `llm_api_key`, `llm_api_base`, `gemini_api_key` — all still used by the
chat/text path.

`.env.example`: delete the embedding/vision provider examples, replace with a short block
explaining that embeddings and vision are local (no key required) and that only chat needs a
provider key. Keep the per-provider chat examples (Groq, OpenAI, Gemini, Ollama).

`EMBEDDING_DIM` in `backend/app/models/chunk.py` changes from `1536` to `1024` — it stays a module
constant (not a setting), because the database column type is fixed at migration time and must not
be able to drift from the model at runtime.

## 5. Dependencies (`backend/pyproject.toml`)

Add `sentence-transformers`, `transformers`, `torch`, `einops`, `accelerate`
(moondream2's remote code needs `einops`; `accelerate` for its dtype/device loading path).

**`torch` must resolve to the CPU-only build.** The default PyPI `torch` on Linux drags in ~2.5GB
of `nvidia-*` CUDA wheels that are dead weight on a GPU-less box. Pin the CPU index explicitly via
`[tool.uv.sources]` / `[[tool.uv.index]]` in `pyproject.toml` pointing `torch` at
`https://download.pytorch.org/whl/cpu`. Verify after install that no `nvidia-*` package is present
in the venv.

moondream2 requires `trust_remote_code=True` (its modeling code ships in the model repo, not in
`transformers`). This is a deliberate, documented choice: we execute code from that HF repo. The
model id is pinned in config, and the implementation pins a specific **revision** so the code that
runs is the code that was reviewed, not whatever the repo's `main` becomes later.

## 6. Migration

The vector dimension changes 1536 → 1024 *and* the embedding space changes, so **every existing
embedding is invalid** — this is not optional cleanup. Per the decision in §1 (little real data),
we wipe derived data and let normal ingestion rebuild it from the original files, which are
untouched in storage.

One Alembic migration, after `616180658622_add_ocr_enabled_and_device`:

1. `DELETE FROM chunks` — every row, all sources (`content`, `summary`, `metadata`).
2. Drop the HNSW index `ix_chunks_embedding` (created in `9f9c707d06be_initial_schema` as
   `USING hnsw (embedding vector_cosine_ops)`), `ALTER TABLE chunks ALTER COLUMN embedding TYPE
   vector(1024)` (safe now that the table is empty), then recreate the index identically.
3. `UPDATE documents SET summary = NULL, status = 'pending', error_message = NULL` — so summaries
   are regenerated by the local vision/text path rather than left as stale cloud output.
4. **Downgrade** reverses the column type to `vector(1536)` and deletes chunks again. It cannot
   restore the old vectors; the docstring says so plainly.

**Re-ingestion:** the migration only resets state; it does not enqueue work (Alembic must not
depend on the worker). Documents left `pending` are picked up by the existing
`process_document` task. The plan specifies the exact trigger — a small one-shot script that
enqueues a `process_document` job per `pending` document, reusing the existing worker task
rather than adding a parallel code path. Nothing about the pipeline itself changes.

Note `storage`-level artifacts (searchable PDFs) are **not** invalidated — they come from
Tesseract, which is unaffected.

## 7. Testing

The project rule stands: `app/services/llm.py` is the only mock boundary, and the default test run
must stay fast and offline. So:

- **`backend/tests/test_llm.py` is rewritten** for the two changed functions. `test_embed_*` and
  `test_describe_image` currently monkeypatch `litellm.embedding` / `litellm.completion`; they are
  replaced by tests that monkeypatch `local_models.embed_texts` / `local_models.describe_image`
  and assert `llm.embed`/`llm.describe` delegate correctly (right arguments, `DESCRIBE_PROMPT`
  passed through, results returned unchanged). The text-branch and `complete()` tests keep their
  existing `litellm` monkeypatching — that path is unchanged, and those tests passing unmodified is
  the evidence chat was not disturbed.
- **`backend/tests/conftest.py::llm_stub`**: `fake_embed` returns `[0.1] * 1536` hardcoded → must
  use `EMBEDDING_DIM` imported from `app.models.chunk`, so the dimension can never drift from the
  column again.
- **New `backend/tests/test_local_models.py`**, marked `@pytest.mark.slow` and **deselected by
  default** (`addopts = "-m 'not slow'"` in `[tool.pytest.ini_options]`): loads the real models and
  asserts contract, not content — `embed_texts` returns one 1024-float vector per input, unit norm,
  and semantically related Italian/English sentences score closer than unrelated ones (a weak
  assertion that catches a silently wrong model or missing normalization); `describe_image` on a
  fixture image returns a non-empty string. This is the only place real weights load, run manually
  or in a nightly job.
- **Unchanged and expected to pass as-is**: `test_pipeline.py`, `test_rag.py`, `test_search_api.py`,
  `test_chat_api.py`, `test_scan_compile.py` — they all go through the `llm_stub` boundary. Their
  passing without edits is the proof that the swap is transparent to callers.
- **Not unit-tested**: the CPU-wheel torch pin and HF download behavior (install-time concerns,
  verified by hand per §9).

## 8. Documentation

- `README` / setup docs: note the first-run model download (~2.3GB + ~3.7GB from HuggingFace),
  that no embedding/vision API key is needed, and that only chat requires a provider key.
- Note the RAM expectation from §4 so an operator on a smaller box knows what they are signing up
  for.

## 9. Verification (manual, end-to-end)

1. Fresh venv install; `uv pip list | grep nvidia` returns nothing (the CPU-wheel pin worked).
2. `alembic upgrade head`; confirm `chunks.embedding` is `vector(1024)` and the table is empty.
3. Run the re-ingest script; watch worker logs. Confirm documents reach `ready`, chunks have
   1024-dim embeddings, and summaries are regenerated.
4. Ingest a **text-bearing PDF** (Italian) → summary via the cloud text model, chunks embedded
   locally.
5. Ingest a **photo with no text** → confirm the worker loads moondream2, produces an Italian
   description, and that process RSS returns to baseline afterwards (proving the unload works).
6. Semantic search in both Italian and English returns sensible hits; RAG chat answers are grounded.
7. Disconnect the network → embedding and image description still work; only chat fails. This is
   the whole point of the change, so it is the acceptance test.

## 10. Decisions log

| Decision | Choice | Why |
|---|---|---|
| Which models go local | Embedding + vision only; chat stays cloud | The unattended pipeline steps are the reliability risk; chat is interactive and wants a frontier model |
| Runtime | In-process `transformers`/`sentence-transformers` | User choice; no extra service to run or supervise |
| Cloud fallback | None (full replace) | User choice; avoids dual-path code and a second config surface |
| Embedding model | `BAAI/bge-m3` (1024-dim) | Multilingual (IT+EN), strong retrieval, 8192-token context; user chose it over the lighter e5-small |
| Vision model | `vikhyatk/moondream2` | Smallest model that follows a free-text prompt; Florence-2's fixed task tokens + English-only output cannot satisfy `DESCRIBE_PROMPT` |
| Vision model lifecycle | Load per call, unload after, bfloat16 | Keeps steady-state RAM at ~4.6GB on a 16GB box; the vision path is rare by design |
| Embedding model lifecycle | Resident per process | On the hot path for every search and every chunk |
| bge-m3 loaded in two processes | Accepted | API and worker are separate processes; a shared inference service contradicts the in-process decision |
| torch build | Pin CPU-only index | Avoids ~2.5GB of useless CUDA wheels on a GPU-less box |
| `trust_remote_code` | Accepted, with a pinned revision | Required by moondream2; pinning makes the executed code reviewable and stable |
| Old embeddings | Wipe chunks, reset summaries, re-ingest | Dimension *and* vector space both change; user confirmed little real data |
| New config names | `EMBEDDING_MODEL_NAME` / `VISION_MODEL_NAME` | Distinct from the deleted LiteLLM-string settings, so a stale `.env` line cannot silently apply |
| `EMBEDDING_DIM` | Stays a code constant, not a setting | Must match the migrated column; a runtime override could only ever be a bug |
| Real-model tests | Separate `slow` marker, deselected by default | Keeps the default suite fast and offline per project rule |
