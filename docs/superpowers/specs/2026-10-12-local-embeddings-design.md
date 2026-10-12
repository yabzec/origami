# Local embeddings — design

Date: 2026-10-12
Branch: `feat/local-embeddings`

## Goals

1. Origami can embed with a local model on a 16 GB, no-GPU mini PC (Intel N97, 4 cores, AVX2 + AVX-VNNI).
2. The local model is loaded only when needed and unloaded when idle.
3. When `EMBEDDING_MODEL` is empty, Origami uses the local model by default.
4. A CLI command rebuilds every vector after a model change.
5. Origami refuses to start when the configured embedding model does not match the vectors in the database, and says what to do.

Non-goals: local text/vision LLM, GPU support, running the model inside the Python processes.

## Decisions

| Topic | Decision |
|---|---|
| Local model | `embeddinggemma` (Google EmbeddingGemma 300M): multilingual, 768 dims, Matryoshka, 2k-token context, about 1 GB resident. Runner-up: `qwen3-embedding:0.6b`. |
| Runtime | Ollama, as a service in `docker-compose.yml`. Not in-process: one loaded copy serves both API and worker, `keep_alive` frees memory reliably, no new Python dependencies. |
| Load/unload | `OLLAMA_KEEP_ALIVE=5m`. First query after idle waits about 1–3 s for the model load. |
| Default trigger | Only an empty `EMBEDDING_MODEL`. API keys do not matter (Ollama needs none). |
| Model mismatch | Startup refuses (exit code 78) with instructions; systemd does not restart-loop. |
| Re-embed | `python -m app.cli reembed`, in place, one transaction, service stopped. |
| Host Ollama | The existing host install (unused leftover from old tests) is removed after the container works. |

## Runtime: Ollama in compose

`docker-compose.yml` gets an `ollama` service:

- image `ollama/ollama` with a pinned version tag
- `container_name: origami-ollama`, `restart: unless-stopped`
- port `127.0.0.1:11434:11434` (API and worker run on the host)
- named volume `ollama` mounted at `/root/.ollama`
- `OLLAMA_KEEP_ALIVE=5m`

`deploy/origami.sh` runs `docker compose up -d db ollama` instead of `up -d db`. The ollama container idles at about 30 MB when no model is loaded.

## Configuration

All resolution happens in `config.py`, in one function `resolve_embedding()` returning `model`, `dim`, `api_key`, `api_base`, `scheme`.

| `.env` | Resolved |
|---|---|
| `EMBEDDING_MODEL` empty | model `ollama/embeddinggemma`, api_base `EMBEDDING_API_BASE` or `http://localhost:11434`, no key, scheme `embeddinggemma-v1` |
| `EMBEDDING_MODEL` set | as today: key `EMBEDDING_API_KEY` or `LLM_API_KEY`, base `EMBEDDING_API_BASE` or `LLM_API_BASE`; scheme from the model name (`embeddinggemma-v1` for `*embeddinggemma*`, else `none`) |
| `EMBEDDING_DIM` empty | 768 for `ollama/embeddinggemma`, else 1536 (today's value) |
| `EMBEDDING_DIM` set | that value |

`Settings.embedding_model` and `Settings.embedding_dim` default to empty (`embedding_dim: int | None = None`).

`.env.example`: `EMBEDDING_MODEL=` and `EMBEDDING_DIM=` empty, with a comment explaining the local default and Gemini as a commented example. The live `.env` sets Gemini explicitly, so it does not change behavior.

## Embedding call

`llm.embed(texts, role="document")` gains a `role` argument: `"document"` or `"query"`.

- `services/search.py` calls `embed([query], role="query")`.
- `worker/pipeline.py` and `reembed` call `role="document"`.

Prefixes by scheme:

| Scheme | Query | Document |
|---|---|---|
| `none` | none | none |
| `embeddinggemma-v1` | `task: search result \| query: ` | `title: none \| text: ` |

Gemini stays on scheme `none`, so existing vectors stay valid.

For `ollama/*` models Origami does not send `dimensions`. When the returned vector is longer than `dim`, Origami keeps the first `dim` values and L2-normalizes. When it is shorter, `embed` raises.

## Fingerprint

A migration adds table `embedding_meta`, one row:

| Column | Type |
|---|---|
| `id` | int primary key, always 1 |
| `model` | text, resolved model string |
| `dim` | int |
| `scheme` | text |
| `updated_at` | timestamptz |

`api_base` is not part of the fingerprint.

`Chunk.embedding` no longer hardcodes `Vector(1536)`: the ORM type takes the dimension from `resolve_embedding()`. The real column dimension changes only through `check-embeddings` (empty database) or `reembed`.

## Startup check

New module `services/embedding_check.py`, function `check(pull: bool)`, and CLI command `python -m app.cli check-embeddings`.

Steps:

1. Resolve the settings.
2. If the model is `ollama/*`: `GET /api/tags` on the api_base. Unreachable → error "Ollama is not reachable at <url>. Start it: docker compose up -d ollama". Model missing → with `pull=True`, `POST /api/pull` and log progress; with `pull=False`, error "Model <name> is not pulled. Run: uv run python -m app.cli check-embeddings".
3. Read the fingerprint row and the real column dimension (`atttypmod` of `chunks.embedding`).
4. No fingerprint row → insert the resolved settings. This is the upgrade path: existing vectors were built with the current `.env`.
5. No chunk has an embedding and the column dimension differs → `ALTER COLUMN ... TYPE vector(N)`, recreate the HNSW index, update the fingerprint. Nothing is lost.
6. Fingerprint or column dimension differs from resolved settings, and embeddings exist → mismatch.

Mismatch message:

```
Embedding model changed.
  database: gemini/gemini-embedding-001, 1536 dims, scheme none
  .env:     ollama/embeddinggemma, 768 dims, scheme embeddinggemma-v1
Re-embed all documents:  cd backend && uv run python -m app.cli reembed
Or restore EMBEDDING_MODEL / EMBEDDING_DIM in .env.
```

The CLI exits with code 78 (`EX_CONFIG`) on mismatch or any check error.

Wiring:

- `deploy/origami.sh` runs `.venv/bin/python -m app.cli check-embeddings` after `alembic upgrade head` and before starting API and worker.
- `deploy/origami.service` gets `RestartPreventExitStatus=78`: systemd marks the unit failed instead of restarting every 5 s. The message is in `journalctl -u origami`.
- API lifespan and worker main call `check(pull=False)` and raise on mismatch, for dev runs (`uvicorn --reload`, `python -m app.worker`).

Rule documented in README: upgrade Origami first, change the model after. Changing `.env` and upgrading in one step records the wrong fingerprint.

## `reembed` command

`python -m app.cli reembed [--yes]`

1. Prompt "Stop origami first (sudo systemctl stop origami). Continue? [y/N]"; `--yes` skips it. No process detection.
2. Resolve settings; for `ollama/*`, check reachability and pull the model if missing.
3. Smoke test: embed one string with `role="document"`; the vector length must equal `dim`; `dim` must be ≤ 2000 (pgvector HNSW limit). Failure exits before any change.
4. One transaction:
   - `DROP INDEX ix_chunks_embedding`
   - `ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(N) USING NULL`
   - embed all chunks from stored `content`, batches of 100 (`EMBED_BATCH_SIZE`), `role="document"`, print `embedded 300/550`
   - `CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops)`
   - upsert the fingerprint
   - commit
5. Print "Done. Start origami: sudo systemctl start origami".

Only stored chunks are re-embedded (content, summary, metadata, translation). No OCR, summary or translation runs again.

Any failure rolls back the whole transaction: old vectors and fingerprint stay. The service still refuses to start with the new `.env`; fix the cause and rerun, or restore `.env`.

`reembed` also works with an unchanged model, to apply a new scheme or repair the database.

## Error handling summary

| Situation | Behavior |
|---|---|
| Ollama down at startup | exit 78, "start it: docker compose up -d ollama" |
| Ollama down during a job | embed raises, normal job retry (30 s … 30 min) |
| Ollama down during search | semantic part fails as with a remote provider today |
| Model mismatch | exit 78 with instructions |
| Wrong vector length from provider | `embed` raises |
| `reembed` failure | full rollback |

## Tests

Real Postgres, fake LLM clients, fake Ollama HTTP. No test calls a real Ollama.

- `resolve_embedding()`: empty model → local default and dim 768; explicit model keeps today's key/base fallback; explicit dim wins; scheme from model name.
- `embed`: prefixes per role and scheme; no prefix for `none`; truncation + L2 normalization; shorter vector raises; no `dimensions` sent for `ollama/*`.
- `check`: no row → records; mismatch → error with message and CLI exit 78; empty database with other dim → column resized; database with vectors and other dim → refused; Ollama unreachable → error; model missing → pull called (with `pull=True`), error (with `pull=False`).
- `reembed`: 1536 → 768 changes column type, fills every chunk, recreates index, updates fingerprint; failure in a later batch rolls everything back; smoke-test length mismatch aborts with no change; dim > 2000 aborts.
- API lifespan and worker main raise on mismatch.
- Search passes `role="query"`, pipeline passes `role="document"`.

## Documentation

README:

- New "Embeddings" section: local default, Ollama compose service, `keep_alive`, memory use, first-query delay, model choice.
- "Changing the embedding model": stop service, edit `.env`, `reembed`, start service; "upgrade first, switch after".
- systemd section: `check-embeddings` at start, exit 78, `RestartPreventExitStatus`, `journalctl -u origami`.
- Backend install: `docker compose up -d db ollama`.

`.env.example` updated as in Configuration.

## Rollout on this machine (manual, after merge)

1. Remove the host Ollama, step by step with confirmation: `sudo systemctl disable --now ollama`, `ollama rm llama3.2:3b`, remove the binary, unit file, `ollama` user and `/usr/share/ollama`.
2. Start the container: `docker compose up -d ollama`.
3. Restart Origami on the existing Gemini config; the fingerprint is recorded.
4. Optional switch to local: stop service, empty `EMBEDDING_MODEL` and `EMBEDDING_DIM` in `.env`, `uv run python -m app.cli reembed`, start service.
