# Origami

Self-hosted document management system: scan, upload, OCR, semantic search, and RAG chat over your own documents.

## Features

- **Upload and scan:** PDF, images, video, text, Markdown and office documents (`.doc`, `.docx`, `.odt`, `.rtf`). Office files get a PDF preview made by headless LibreOffice, and Download still returns the original file. Upload several files or a whole folder at once (**Upload → Files…/Folder…**, or drag them onto Browse). Each file keeps its name as title and its last-modified date as document date; folder, tags and processing options apply to all files. A folder upload recreates its subfolders. Closing the batch upload dialog while uploads run asks "Stop them and close?"; confirming stops the files not yet sent.
- **AI description:** the AI summary fills an empty description and is marked "AI generated" until you edit it. Re-process refreshes an AI description and keeps one you edited.
- **Scan:** reorder pages before saving (drag the handle, or use the ← → buttons on the selected page). The folder picker selects and closes when you click a folder that has no subfolders.
- **Scan from your own network:** pick **Search local scanners** in the scanner menu to use a Wi-Fi scanner on the network of the computer you are using, even when the server is elsewhere. The browser starts the Origami Agent, which finds eSCL (AirScan) scanners and connects out to the server; no ports, no VPN.
- **Processing options:** on scan, upload and re-process, switch OCR, AI summary and translation on or off independently. OCR languages are the ones installed on the server, picked with a multi-select. Scan and upload start with `DEFAULT_OCR_LANGUAGES`, and re-process with the languages the document already uses. Translation is off by default: tick it to translate a document. It has a **Translate to** language, default `DEFAULT_TRANSLATION_LANGUAGE`; the choices are the installed OCR languages. The document language is detected locally (lingua), so translation works with the summary off.
- **Tags:** type in the tag field to pick an existing tag or press Enter to create a new one.
- **Browse:** folders work like a file manager: a breadcrumb, subfolder tiles, then the documents in that folder. A tile's count includes the documents in all its subfolders. **Root** shows top-level folders and documents without a folder; **All documents** shows everything. Filter by tag, type and document date (from/to); every filter and the order-by menu are kept in the URL. The **×** on a document page goes back to that document's folder. **📁 New folder**, next to Upload, creates a folder in the open folder.
- **Select, move and delete:** select folder tiles and documents together (checkbox, Shift-click for a range), then move or delete them. A move is refused as a whole when a name is already taken at the destination or a folder would go inside itself; nothing changes. Deleting a folder deletes its subfolders and their documents permanently; the confirm shows the totals. A directory that still holds files Origami does not track stays on disk.
- **Sidebar:** shows the top-level folders. Opening a folder expands its path and collapses the other branches; the arrow expands or collapses a folder without opening it.
- **Storage tree:** `STORAGE_PATH` mirrors the explorer: `Folder/Subfolder/Title.ext`. Renaming or moving a document or folder in the app moves the file on disk. Characters not allowed in file names become `_`; documents with the same title in one folder get `Title (2).ext`. Office previews and OCR companion PDFs live in `DERIVED_PATH`, scan pages in `TMP_PATH`. Changes made directly on disk are not picked up.
- **Search:** hybrid, semantic or keyword search, with the same filter row as Browse plus mode and folder.
- **Translation:** documents whose detected language differs from their translation target are translated page by page. Progress is saved per page, so a failed or throttled translation resumes where it stopped. **Re-translate** on the document page redoes only the translation, without OCR or summary. Re-translate can switch the target language.
- **Dark mode:** follows the operating system setting.
- **Background jobs with retries:** every job is retried after 30 s, 2 min, 10 min and 30 min (5 attempts). Between attempts the document stays `pending` and the UI shows the next attempt. After the last failure every user with an email address gets a notification. Translation runs as its own retried job.
- **Chat:** multi-turn conversation with Markdown answers and `[n]` citations that link to the documents. Origami shortlists candidate documents locally and asks the LLM which ones you mean. Passages come only from those documents and from files you pin. The files in context show as chips: remove one to keep it out of the conversation, or add one with **+ Add file**. The conversation is kept in the browser tab (session storage) until you press **New chat**.

## Installation

### System dependencies

```bash
sudo apt install tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-deu  # add any tesseract-ocr-<lang>
```

Required for OCR. Install any `tesseract-ocr-<lang>` packages you need: the app discovers installed languages automatically (within 5 minutes, no restart). The test suite needs `ita`, `eng` and `deu`. Also needed on the host:

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
# optional: PRIMARY_LANGUAGE (ISO 639-1, default "it") - language for AI summaries
# optional: DEFAULT_TRANSLATION_LANGUAGE (ISO 639-1, default PRIMARY_LANGUAGE) - default translation target for scan and upload
# note: DEFAULT_OCR_LANGUAGES uses Tesseract codes (ita, eng), the two language settings above use 2-letter codes (it, en)
uv sync
docker compose up -d db
uv run alembic upgrade head
uv run python -m app.cli create-user <username>
```

### Upgrading from the flat `files/<uuid>` layout

Older versions stored every document as `STORAGE_PATH/files/<uuid>.<ext>`. The API and the worker refuse to start until you move the files once. Do it in this order:

```bash
sudo systemctl stop origami                          # the old code must not run during the move
# back up the database (pgdata volume) and STORAGE_PATH
cd backend
uv run alembic upgrade head                          # migrate-storage needs the new columns
uv run python -m app.cli migrate-storage --dry-run   # print the moves
uv run python -m app.cli migrate-storage
uv run python -m app.cli migrate-storage --check     # report database/disk drift; --fix removes partial writes
cd ../frontend && npm run build                      # the new UI needs the new API
sudo systemctl start origami
```

The migration refuses to run while a top-level folder named `files` exists: rename it first.

`migrate-storage` keeps a journal at `<DERIVED_PATH>/storage-migration.journal`, so an interrupted run resumes safely. Run it again.

uuid-named files in the old `files/` folder that belong to no document are moved, never deleted, to `<DERIVED_PATH>/orphans/`.

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

## Client scanner agent

The agent lets a browser use Wi-Fi scanners on its own network. It runs only when the scan page asks for it, and stops after 30 minutes without a scan.

### Build

Needs Go 1.22+ on PATH on the build machine (not on the server or clients):

```bash
agent/build.sh
```

This writes Windows and Linux builds to `agent/dist/`. The macOS builds (`origami-agent-darwin-*.zip`) need a Mac: run the same script there and copy the zips into `agent/dist/` on the server. The server serves the files from `AGENT_DIST_DIR` (default `../agent/dist`, relative to `backend/`).

Set `PUBLIC_URL` in `.env` to the address clients use to reach Origami (e.g. `https://origami.example.com`). The agent has no built-in server address: it gets this URL from the launch link, so the same agent build works for any Origami install. When `PUBLIC_URL` is empty, the server uses the address of the incoming request, which is wrong behind a reverse proxy or tunnel that rewrites the host.

### First run on a client

On the scan page open the scanner menu, choose **Search local scanners** and download the agent for your system. After the download the menu shows the exact steps for that system. Put the file in a hidden folder where it can stay, then run it once:

- **Windows:** move it to `%LOCALAPPDATA%\Origami Agent\` and run it. If SmartScreen says "Windows protected your PC", choose **More info → Run anyway**. Allow it on private networks when the firewall asks.
- **macOS:** unzip, move **Origami Agent** to Applications, right-click → **Open** once.
- **Linux:** move it to `~/.local/share/origami-agent/`, then `chmod +x` it and run it once. It needs `xdg-mime` (package `xdg-utils`).

Keep the file in that folder: the browser starts it from that path. Then click **Installed, search now**. The browser asks once whether to open Origami Agent; tick "always allow".

The agent remembers the first Origami server that starts it, and refuses others. To pair it with another server, run it with `--reset`. It also keeps a private key in `origami-agent/handoff.key` in your user settings folder: when the agent is already running, a second start must show this key, so another user on the same computer cannot take it over.

If a new start replaces the agent's current Origami session, it shows the notice "switched to a new Origami session". The agent never follows HTTP redirects from scanners.

### Troubleshooting

- **No scanners found:** the scanner must support eSCL/AirScan (look for AirPrint or Mopria Scan in its specs). Guest Wi-Fi with client isolation, or a firewall blocking multicast DNS (UDP 5353), hides the scanner.
- **Agent not responding:** the browser prompt may have been dismissed, or the agent was moved after the first run. Run it again once, then search again.
- **"port 47811 is in use by another program or user":** another program or another user on this computer is using port 47811. Close it, or sign the other user out, then start the agent again.
- **Browser prompt for `origami-agent://` links:** any website can ask the browser to open an `origami-agent://` link. After you confirm the browser prompt, such a link can stop a running agent (it cannot connect it to another server). Only allow the prompt for your Origami site.

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

- `LLM_TPM_LIMIT` — your provider's tokens-per-minute limit (for example `8000` on a free tier). Translation waits to stay under it and retries 429 responses. `0` (default) disables the throttle. The budget is per worker process.
  Waits longer than a few seconds re-queue the translation job for later, so new documents keep processing meanwhile.
- `TRANSLATION_SEGMENT_CHARS` — maximum characters sent in one translation call (default `6000`; capped automatically to fit `LLM_TPM_LIMIT`).

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
(created automatically). It mirrors the folders in the app:
`STORAGE_PATH/<folder>/<subfolder>/<title>.<ext>`. Back it up together with
the `pgdata` docker volume.

Two sibling folders hold app data, not documents: `derived/` (office previews
and OCR companion PDFs) and `tmp/` (scan pages in progress). Set
`DERIVED_PATH` and `TMP_PATH` to put them elsewhere. Do not add, rename or
delete files in `STORAGE_PATH` by hand: the app does not see changes made on
disk. `migrate-storage --check` lists any drift.

The server reads `.env` only at start. After editing it, or after pulling new
code, run `sudo systemctl restart origami` (and `npm run build` in `frontend/`
when the UI changed).

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md): free to use, modify and share for any noncommercial purpose. Commercial use, including selling the code, is not allowed.
