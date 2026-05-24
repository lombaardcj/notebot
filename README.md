# notebot

A self-hosted Telegram bot that transcribes voice messages and audio files using [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) — no cloud speech API required.

Transcripts are saved to local markdown files and optionally to Google Sheets. The bot runs entirely on your own hardware.

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

## Configuration

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
| `ADMIN_CHAT_ID` | Yes | Chat ID with full admin privileges |
| `ALLOWED_CHAT_IDS` | No | Comma-separated seed list of approved chat IDs |

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

## File layout

```
telegram_transcriber_bot.py   # main bot script
logs/
  requests.jsonl              # one entry per incoming message
  transcriptions.jsonl        # one entry per transcription
  access.json                 # approved and pending chat IDs
transcripts/                  # saved markdown transcripts
```

## License

MIT — see [LICENSE](LICENSE). If you reuse this project, attribution to the original repository is appreciated but only required by the licence when distributing copies.
