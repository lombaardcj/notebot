# notebot

A self-hosted Telegram bot that transcribes voice messages and audio files using [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) — no cloud speech API required.

Transcripts are saved to local markdown files and optionally to Google Sheets. The bot runs entirely on your own hardware.

→ [System architecture & interaction diagrams](docs/architecture.md)

## Features

- Transcribes voice messages and audio file attachments
- Auto-detects language (no manual language selection needed)
- Per-user preferred language list — warns when detected language differs
- Saves transcripts to local `.md` files and/or Google Sheets
- Admin account with dynamic user access control:
  - Users request access via `/requestaccess`
  - Admin approves or denies with inline buttons
  - Admin can revoke access at any time
- Structured JSONL logs for requests and transcriptions
- `/stats` command with usage breakdown (admin sees access counts too)

## Requirements

- Python 3.10+
- A Telegram bot token — create one via [@BotFather](https://t.me/BotFather)
- Your Telegram chat ID — get it from [@userinfobot](https://t.me/userinfobot)

## Setup

```bash
git clone https://github.com/lombaardcj/notebot.git
cd notebot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Copy the example env and fill in your values:

```bash
cp .env.example .env
```

```
TELEGRAM_BOT_TOKEN=your_token_here
ADMIN_CHAT_ID=your_chat_id
ALLOWED_CHAT_IDS=your_chat_id
```

Run the bot:

```bash
python3 telegram_transcriber_bot.py
```

## Running with Docker

Docker is the easiest way to run notebot without managing a Python environment yourself.

### Quick start — single container

```bash
docker run -d \
  --name notebot \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/transcripts:/app/transcripts" \
  -v "$(pwd)/secrets:/app/secrets" \
  ghcr.io/lombaardcj/notebot:latest
```

### Recommended — Docker Compose

```bash
docker compose up -d          # start in background
docker compose logs -f        # follow logs
docker compose down           # stop
docker compose pull && docker compose up -d   # update to latest image
```

**Developing locally?** A `Makefile` is included with shortcuts for the common dev cycle:

```bash
make rebuild   # teardown → no-cache build → start → tail logs  (use after code changes)
make logs      # tail live logs
make shell     # open a shell inside the running container
make down      # stop and remove containers
```

The `docker-compose.yml` in this repo mounts `logs/`, `transcripts/`, and `secrets/` from the host so your data persists across container restarts. The Whisper model is cached in a named Docker volume (`whisper-cache`) so it is only downloaded once.

### Building locally

```bash
docker build -t notebot .
docker run -d --name notebot --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/transcripts:/app/transcripts" \
  -v "$(pwd)/secrets:/app/secrets" \
  notebot
```

> **Whisper model download:** On first start the bot will download the `base` Whisper model (~150 MB). Subsequent starts use the cached volume and start immediately.

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
| `ADMIN_CHAT_ID` | Yes | Chat ID with full admin privileges |
| `ALLOWED_CHAT_IDS` | No | Comma-separated seed list of approved chat IDs |
| `GOOGLE_ENABLED` | No | Set to `true` to enable Google Sheets output (default: `false`) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | No | Path to your service account JSON (default: `secrets/your-file.json`) |
| `GOOGLE_SHEET_NAME` | No | Name of the Google Sheet to write to (default: `Voice Transcripts`) |

## Commands

| Command | Who | Description |
|---|---|---|
| `/start` | Everyone | Show help and command list |
| `/requestaccess` | Anyone | Request access to the bot |
| `/ping` | Approved | Check uptime |
| `/stats` | Approved | Usage statistics |
| `/settings` | Approved | View your preferences |
| `/setlanguages` | Approved | Set preferred language codes (e.g. `af en`) |
| `/pending` | Admin | Review pending access requests (inline buttons) |
| `/approve <id>` | Admin | Manually approve a chat ID |
| `/deny <id>` | Admin | Reject a pending request |
| `/revoke <id>` | Admin | Remove access from an approved user |

## Google Sheets integration (optional, advanced)

> **This is not required.** Transcripts are always saved to local markdown files. Google Sheets is an optional extra for users who want transcripts in a shared spreadsheet.

### What you need

- A Google account
- A Google Cloud project (free tier is sufficient)
- Comfort with the Google Cloud Console

### Step 1 — Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/)
2. Click **Select a project → New project**, give it a name, click **Create**

### Step 2 — Enable the Sheets and Drive APIs

1. In your project, go to **APIs & Services → Library**
2. Search for and enable **Google Sheets API**
3. Search for and enable **Google Drive API**

### Step 3 — Create a service account

1. Go to **IAM & Admin → Service Accounts → Create service account**
2. Give it a name (e.g. `telegram-notebot`), click **Create and continue**, then **Done**
3. Click on the service account you just created → **Keys → Add key → Create new key → JSON**
4. A `.json` file is downloaded — this is your credentials file

### Step 4 — Store the credentials file

Place the downloaded file in the `secrets/` folder inside the project:

```
secrets/your-service-account-file.json
```

> `secrets/` is excluded from git. Never commit this file.

### Step 5 — Create the Google Sheet and share it

1. Go to [sheets.google.com](https://sheets.google.com) and create a new spreadsheet
2. Name it exactly as you'll set in `GOOGLE_SHEET_NAME` (default: `Voice Transcripts`)
3. Click **Share**, paste the service account's `client_email` (found inside the `.json` file), and give it **Editor** access

### Step 6 — Update your `.env`

```
GOOGLE_ENABLED=true
GOOGLE_SERVICE_ACCOUNT_FILE=secrets/your-service-account-file.json
GOOGLE_SHEET_NAME=Voice Transcripts
```

### Step 7 — Test before running the bot

```bash
python3 test_sheets.py
```

This writes a test row, reads it back to confirm it worked, then offers to delete it.

---

## File layout

```
telegram_transcriber_bot.py   # main bot script
Dockerfile                    # container image definition
docker-compose.yml            # compose service definition
logs/
  requests.jsonl              # one entry per incoming message
  transcriptions.jsonl        # one entry per transcription
  access.json                 # approved and pending chat IDs
transcripts/                  # saved markdown transcripts
secrets/                      # service account credentials (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE). If you reuse this project, attribution to the original repository is appreciated but only required by the licence when distributing copies.
