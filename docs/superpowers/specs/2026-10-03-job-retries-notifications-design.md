# Origami — Job Retries with Backoff and Email Notifications

**Date:** 2026-10-03
**Status:** Approved by user (brainstorming session), pending written-spec review
**Build order:** after `2026-10-03-office-docs-folder-picker-design.md` (both touch the pipeline; this spec assumes that one has landed).

## 1. Overview

Every background job that fails is retried automatically, with growing delays: **30 s, 2 min, 10 min, 30 min**. That makes 5 attempts in total. After the 5th failure the job is marked `failed` and the user gets an email. Work that depends on a failing step waits until that step succeeds.

Translation stops being a silent, non-retried side step. It becomes its own job with the same retry policy, so a provider hiccup no longer leaves "Translation failed — re-process to retry".

## 2. Current state (verified)

- `app/services/jobs.py::fail` re-queues with `30 * 2**attempts` seconds, and `Job.max_attempts` defaults to 3.
- `process_document` sets the document `failed` on any exception and re-raises, so retries happen, but the document shows `failed` in between.
- Translation errors are caught inside `_ensure_translation` and never retried.
- No email capability exists, and `users` has no email column.

## 3. Retry engine — `app/services/jobs.py`

- `RETRY_DELAYS = [30, 120, 600, 1800]` (seconds). `MAX_ATTEMPTS = len(RETRY_DELAYS) + 1 = 5`.
- `Job.max_attempts` default changes to `MAX_ATTEMPTS`.
- New `JobStatus.cancelled = "cancelled"`.
- `fail(session, job, error)`:
  - `attempts += 1`, `last_error = error`
  - If `attempts < max_attempts`: `status = queued`, `run_at = now + RETRY_DELAYS[min(attempts - 1, len - 1)]`.
  - Otherwise: `status = failed`, commit, then call `notify_job_failed(session, job)`. The notification is wrapped so it can never raise into the worker.
- Runner (`app/worker/runner.py`): before calling the handler, pass a copy of the payload with `_attempt = job.attempts + 1` and `_final_attempt = job.attempts + 1 >= job.max_attempts`. The stored payload is not modified.

## 4. Pipeline

### process_document
- The core chain is unchanged: extract → content chunks → summary → metadata chunk → embed. Steps skip completed work, so a retry resumes where it broke. Later steps never run before earlier ones succeed.
- On exception:
  - not the final attempt: `doc.status = pending`, `doc.error_message = "Retrying: " + str(exc)[:2000]`, commit, re-raise.
  - final attempt: `doc.status = failed`, `doc.error_message = str(exc)[:2000]` (as today), commit, re-raise.
- `_ensure_translation` is removed from `process_document`. After the document is `ready`, if `detected_language` is set, differs from `get_primary_language()`, and content chunks exist: set `translation_status = pending` and enqueue `translate_document {"document_id": ...}`.

### translate_document (new job)
- Skip (complete) if `translation_status == done` and translation chunks exist, or if the document no longer exists.
- Translate all content chunks in memory first (no partial chunks on error), insert the `translation` chunks, embed the pending chunks (only translation chunks are pending), set `translation_status = done`.
- On exception:
  - not the final attempt: `translation_status = pending`, re-raise.
  - final attempt: `translation_status = failed`, re-raise.
- `TranslationStatus` gains `pending = "pending"`.

### Re-process
`reprocess_document` first sets every `queued` job of type `process_document` or `translate_document` whose `payload.document_id` matches to `cancelled`, then proceeds as today. A `running` job is left alone; its results are overwritten by the new run.

### sweep_scan_sessions
Uses the same retry policy (generic in `fail`). No handler change.

## 5. Retry info in the API

Document serialization gains `active_job`:

```json
{"type": "translate_document", "attempts": 2, "max_attempts": 5, "run_at": "<iso>", "last_error": "..."}
```

It comes from the newest `queued` or `running` job whose `payload.document_id` equals the document id, and is `null` when there is none. To avoid N+1 queries on the list endpoint, the active jobs of all listed documents are fetched in one query.

## 6. Email notifications — `app/services/notify.py`

### Configuration (env, `app/config.py`)
`SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT` (default `587`), `SMTP_USER`, `SMTP_APP_PASSWORD`, `SMTP_FROM` (default = `SMTP_USER`), `APP_BASE_URL` (optional; used for links). `.env.example` documents them, with a note that Gmail needs an **app password** (2FA enabled), not the account password.

### Recipients
New column `users.email TEXT NULL`. CLI `python -m app.cli set-email <username> <email>` sets it. A future profile page will edit it. All users with an email receive notifications.

### Sending
- `send_email(to: list[str], subject: str, body: str) -> bool`: uses `smtplib.SMTP(host, port, timeout=20)`, `starttls()`, `login`, `send_message`. Returns `False` and logs on any error. It never raises.
- `notify_job_failed(session, job)`: builds the message, sends it. If SMTP is not configured or there are no recipients, it logs a warning and returns.
- Labels: `process_document` → "Processing", `translate_document` → "Translation", `sweep_scan_sessions` → "Scan cleanup", others → the job type.
- Subject: `[Origami] <Label> failed: <document title>`, or `[Origami] <Label> failed` without a document.
- Body: document title, label, attempts (`5/5`), last error (trimmed to 1000 characters), and the link `<APP_BASE_URL>/documents/<id>` when `APP_BASE_URL` is set.
- CLI `python -m app.cli test-email` sends a test message to all recipients and prints the result.

## 7. Frontend

- `types.ts`: `Document.active_job: ActiveJob | null`; `translation_status` adds `"pending"`.
- Pure helper `retryLabel(job: ActiveJob, now: Date): string | null` in `src/lib/retry.ts`:
  - `null` when `attempts === 0`
  - otherwise `"attempt {attempts + 1}/{max_attempts}, next ≈ {relative}"`, with relative = "now" / "N s" / "N min" / "N h".
- Document page:
  - `status === "pending"` with `active_job.type === "process_document"` and `attempts > 0`: amber banner "Processing failed, retrying (<retryLabel>): <last_error, first line>".
  - Translation line in the Text tab:
    - `pending` without retries → "Translation pending…"
    - `pending` with retries → "Translation retrying (<retryLabel>)"
    - `failed` → "Translation failed — notification sent. Re-process to retry."
  - The document query keeps polling while the status is `pending`/`processing` or `translation_status === "pending"`, so the text tab updates when the translation finishes. When the translation becomes `done`, invalidate the document-text queries.
- Browse card: the badge title (tooltip) shows the retry label when `active_job` has attempts > 0.

## 8. Migration
One Alembic migration (revising the head at implementation time):
- `users.email` nullable text.
- `jobs.max_attempts` server default 5, and `UPDATE jobs SET max_attempts = 5 WHERE status IN ('queued', 'running')`.

## 9. Testing
Backend runs on real Postgres. Mock boundaries: `app/services/llm.py`, plus `smtplib.SMTP`. **Ruling:** the SMTP mock is a second sanctioned boundary, because it is an external network service like the LLM provider.
- `fail()`: delays 30/120/600/1800 across four failures. The 5th failure → `failed`, `notify_job_failed` called exactly once. A notify exception does not propagate.
- Runner passes `_attempt` / `_final_attempt` without mutating the stored payload.
- `process_document`: a non-final failure → doc `pending` with "Retrying:"; a final failure → `failed`. On success with `de` detected → `ready`, `translation_status = pending`, one `translate_document` job queued. With `it` → no translation job.
- `translate_document`:
  - success → chunks, embeddings, `done`
  - error, non-final → `pending`, exception re-raised
  - error, final → `failed`
  - already done → no-op
- Re-process cancels queued jobs for that document only.
- Serialization: `active_job` is present for a queued retry and `null` otherwise. The list endpoint uses one query for active jobs (assert by count or by structure).
- `notify`:
  - not configured → no SMTP call, warning logged
  - configured → `SMTP` called with STARTTLS/login and the subject format
  - SMTP raises → returns False
  - `set-email` and `test-email` CLI.
- Frontend: `retryLabel` cases; the translation status line selection helper.

## 10. Out of scope
- Notifications other than email (push, in-app inbox).
- A per-job-type retry schedule (one schedule for all).
- Manual "retry now" button (re-process covers it).

## 11. Decisions log
| Decision | Choice | Why |
|---|---|---|
| Packaging | Separate spec after the office/folder batch | User choice; keeps both plans reviewable |
| Recipient | `users.email` + CLI; SMTP creds in env | User choice; fits future profile page |
| Structure | One retry policy; translation as its own job | Steps already idempotent; dependents wait naturally; no dependency graph needed |
| Schedule | 30 s, 2 min, 10 min, 30 min, then fail + email | User-specified |
| Document state during retries | `pending` with "Retrying:" message | Avoids showing `failed` between attempts |
| Notify failure | Logged, never raises, never re-queues | The worker must not crash on email problems |
| SMTP in tests | Mock `smtplib.SMTP` | External network boundary, like the LLM |

## 12. Addendum: vision credentials and model listing

Approved by the user after the main spec. Context: text moves to Groq (`LLM_MODEL=groq/openai/gpt-oss-120b`), while vision and embeddings stay on Gemini (`VISION_MODEL=gemini/gemini-2.5-flash`). Today `llm.describe()` sends `LLM_API_KEY`/`LLM_API_BASE` on vision calls too, so the Groq key would go to Gemini.

### Vision credentials
- New settings `vision_api_key` / `vision_api_base` (env `VISION_API_KEY`, `VISION_API_BASE`, default empty).
- `llm.describe(image_path=...)` uses `_kw(vision_api_key or llm_api_key, vision_api_base or llm_api_base)`, the same fallback as embeddings. The text path is unchanged and never receives the vision key.
- `.env.example` documents both settings with a "Groq text + Gemini vision/embeddings" example.

### Model listing
- `llm.list_models() -> list[dict]` in `app/services/llm.py` (provider HTTP stays in `llm.py`; stdlib `urllib.request` + `json`, timeout 20 s, no new dependency). The endpoint comes from the `LLM_MODEL` provider prefix:
  - `LLM_API_BASE` set → `{base}/models`, Bearer `LLM_API_KEY`
  - `groq/` → `https://api.groq.com/openai/v1/models`, Bearer `LLM_API_KEY` or `GROQ_API_KEY`
  - `openai/` → `https://api.openai.com/v1/models`, Bearer `LLM_API_KEY` or `OPENAI_API_KEY`
  - `gemini/` → `https://generativelanguage.googleapis.com/v1beta/models?key=<LLM_API_KEY or GEMINI_API_KEY>`
  - anything else → `ValueError("Model listing not supported for provider '<prefix>'")`
- Result items: `{id, owner, context_window, active}`. Missing fields are `None`. For Gemini, `id` is `name` without the `models/` prefix and `context_window` is `inputTokenLimit`.
- CLI `python -m app.cli list-models` prints a header line with the provider and then a table (id, owner, context, active) sorted by id. On error it prints the message and exits with code 1.

### Testing
- `tests/test_llm.py`: the vision call gets `VISION_API_KEY` when it is set and falls back to `LLM_API_KEY` when it is not. The text call never gets `VISION_API_KEY` (`litellm.completion` monkeypatched, as in the existing tests).
- `urllib.request.urlopen` is monkeypatched as part of the LLM mock boundary: the Groq (OpenAI-style `{"data": [...]}`) and Gemini (`{"models": [...]}`) shapes, plus the unsupported-provider error.
- CLI: the table output and the error exit, with `llm.list_models` monkeypatched.
