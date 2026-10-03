# Origami

Self-hosted document management system: scan, upload, OCR, semantic search, and RAG chat over your own documents.

## Features

- **Upload and scan:** PDF, images, video, text, Markdown and office documents (`.doc`, `.docx`, `.odt`, `.rtf`). Office files get a PDF preview made by headless LibreOffice, and Download still returns the original file.
- **AI description:** the AI summary fills an empty description and is marked "AI generated" until you edit it. Re-process refreshes an AI description and keeps one you edited.
- **Browse:** drill-down folder picker (upload, scan and document pages), an order-by menu (document date newest/oldest, date added, title A–Z) kept in the URL, and type icons per document.
- **Background jobs with retries:** every job is retried after 30 s, 2 min, 10 min and 30 min (5 attempts). Between attempts the document stays `pending` and the UI shows the next attempt. After the last failure every user with an email address gets a notification. Translation runs as its own retried job.
- **Chat:** multi-turn conversation with Markdown answers and `[n]` citations that link to the documents. Origami shortlists candidate documents locally and asks the LLM which ones you mean. Passages come only from those documents and from files you pin. The files in context show as chips: remove one to keep it out of the conversation, or add one with **+ Add file**. The conversation is kept in the browser tab (session storage) until you press **New chat**.

## Installation

### System dependencies

```bash
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu
```

Required for OCR (Italian, English and German). Also needed on the host:

- `poppler-utils` (`pdftoppm`) — PDF rasterization for OCR fallback. Usually preinstalled; otherwise `sudo apt install poppler-utils`.
- `sane-utils` (`scanimage`) — flatbed scanner access. Usually preinstalled; otherwise `sudo apt install sane-utils`.
- `libreoffice-writer` (`soffice`) — converts `.doc`, `.docx`, `.odt` and `.rtf` uploads to PDF for the preview. Usually installed with LibreOffice; otherwise `sudo apt install libreoffice-writer`. Set `SOFFICE_PATH` if the binary is not on the service's `PATH`.
- `fonts-dejavu` — only needed to run the OCR test suite (renders test fixture images). Usually preinstalled; otherwise `sudo apt install fonts-dejavu`.

Verify:

```bash
tesseract --list-langs   # must include ita, eng and deu
pdftoppm -v
scanimage --version
soffice --version
```

### Backend

```bash
cd backend
cp ../.env.example ../.env   # edit STORAGE_PATH, GEMINI_API_KEY, JWT_SECRET
# optional: PRIMARY_LANGUAGE (ISO 639-1, default "it") - language for AI summaries and translations
uv sync
docker compose up -d db
uv run alembic upgrade head
uv run python -m app.cli create-user <username>
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
```

Run the background worker (separate process, required for uploads/scans to process):

```bash
uv run python -m app.worker
```

Run tests (real Postgres, no mocks — `docker compose up -d db` must be running):

```bash
uv run pytest
```

The tests never read your `.env`: they use the code defaults, a throwaway `origami_test` database, and fake LLM and SMTP clients, so no real API call or email is made.

### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server on :5173, proxies /api to :8000
npm test           # vitest
npm run build      # outputs frontend/dist
```

In production, build the frontend and run only the backend: FastAPI serves
`frontend/dist` automatically when it exists, so the Cloudflare tunnel needs
just the one backend port.

## Run as a systemd service

One command starts everything (db container, migrations, API on
`127.0.0.1:8124`, worker). Prerequisites, once:

- `.env` configured (copy from `.env.example`)
- `cd backend && uv sync`
- `cd frontend && npm install && npm run build` (the backend serves `frontend/dist`)
- docker available (the unit runs with the `docker` supplementary group)

Install and start. `deploy/origami.service` is a template: `@USER@` is the
account that runs Origami and `@REPO_DIR@` is the absolute path of this repo.
From the repo root, as that user:

```bash
sed -e "s|@USER@|$USER|g" -e "s|@REPO_DIR@|$PWD|g" deploy/origami.service \
  | sudo tee /etc/systemd/system/origami.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now origami
```

The API address comes from `ORIGAMI_HOST` and `ORIGAMI_PORT`. The unit sets
`127.0.0.1:8124` (this machine only); set `Environment=ORIGAMI_HOST=0.0.0.0`
in `/etc/systemd/system/origami.service` to reach it from the LAN, then run
`sudo systemctl daemon-reload && sudo systemctl restart origami`. Run directly,
`deploy/origami.sh` defaults to `0.0.0.0:8124`.

Operate:

```bash
systemctl status origami
journalctl -u origami -f      # logs (api + worker + launcher)
sudo systemctl restart origami
sudo systemctl stop origami   # stops api+worker; the db container stays up
docker compose stop db        # stop the db too, when you really want to
```

With the default unit the app is served at `http://127.0.0.1:8124` — front it
with cloudflared, or set `ORIGAMI_HOST=0.0.0.0` to expose it on the LAN. If the
repo moves, re-run the install commands from the new location.

## Create a user

There is no signup endpoint by design. With the db up (service running, or
just `docker compose up -d db`):

```bash
cd backend
uv run python -m app.cli create-user <username>
```

You'll be prompted for the password twice.

## AI providers

Text, vision and embeddings can use different providers (model names follow [litellm](https://docs.litellm.ai/docs/providers)). Example `.env` with text on Groq and vision plus embeddings on Gemini:

```bash
LLM_MODEL=groq/openai/gpt-oss-120b
LLM_API_KEY=<groq key>
VISION_MODEL=gemini/gemini-2.5-flash
VISION_API_KEY=<google key>
EMBEDDING_MODEL=gemini/gemini-embedding-001
EMBEDDING_API_KEY=<google key>
```

`VISION_API_KEY` / `VISION_API_BASE` fall back to `LLM_API_KEY` / `LLM_API_BASE` as a pair: when `VISION_API_KEY` is empty, vision uses both LLM values. When it is set, `VISION_API_BASE` is used on its own (empty means the provider default). `EMBEDDING_API_KEY` and `EMBEDDING_API_BASE` each fall back to the LLM value on their own.

List the models offered by the `LLM_MODEL` provider (Groq, OpenAI, Gemini or an OpenAI-compatible `LLM_API_BASE`):

```bash
cd backend
uv run python -m app.cli list-models
```

## Email notifications

When a background job fails for the 5th time, Origami emails every user that has an email address. Without SMTP settings, nothing is sent and the failure is only logged.

1. Gmail: turn on 2-Step Verification, then create an app password (Google Account › Security › App passwords). The normal account password does not work.
2. Add to `.env`:

   ```bash
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=<gmail address>
   SMTP_APP_PASSWORD=<16-character app password>
   SMTP_FROM=
   APP_BASE_URL=http://<host>:<port>
   ```

   `SMTP_FROM` empty means the sender is `SMTP_USER`. `APP_BASE_URL` is optional; when set, emails link to the document.

3. Restart the service, set the recipients and send a test message:

   ```bash
   sudo systemctl restart origami
   cd backend
   uv run python -m app.cli set-email <username> <email>
   uv run python -m app.cli test-email
   ```

## Storage

Documents live in the host folder pointed to by `STORAGE_PATH` in `.env`
(created automatically). It is a plain directory — back it up together with
the `pgdata` docker volume.
