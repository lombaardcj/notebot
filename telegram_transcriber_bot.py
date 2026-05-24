"""
Telegram Voice Transcription Bot
---------------------------------
Receives voice messages via Telegram, transcribes with Faster-Whisper (local, free),
and saves transcripts to:
  - Google Sheets
  - Notion
  - Local markdown files

SETUP INSTRUCTIONS:
-------------------
1. Install dependencies:
   pip install python-telegram-bot faster-whisper gspread google-auth

2. Fill in your credentials in the CONFIG section below.

3. Run: python telegram_transcriber_bot.py

HOW TO GET CREDENTIALS:
-----------------------
- TELEGRAM_BOT_TOKEN: Message @BotFather on Telegram → /newbot → copy token
- GOOGLE_SHEETS: See https://docs.gspread.org/en/latest/oauth2.html (service account method)
- NOTION_TOKEN: notion.so/my-integrations → New integration → copy secret
- NOTION_DATABASE_ID: Open your Notion DB in browser → copy ID from URL
"""

import os
import json
import logging
import datetime
from pathlib import Path

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler,
    filters, ContextTypes,
)
from faster_whisper import WhisperModel

# Optional integrations — imported only if credentials are provided
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


def load_env_file(env_path: str = ".env"):
    """Load KEY=VALUE pairs from a .env file into os.environ if unset."""
    env_file = Path(env_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()




# ─────────────────────────────────────────────
#  CONFIG — Fill these in before running
# ─────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Whisper model size: "tiny", "base", "small", "medium", "large"
# Recommendation: "base" is fast and accurate enough for voice notes
# Use "small" or "medium" for better accuracy (slower, more RAM)
WHISPER_MODEL_SIZE = "base"

# Default preferred languages for new users.
# Users can override this with the /setlanguages command.
DEFAULT_LANGUAGES = ["af", "en"]

USER_SETTINGS_FILE = "user_settings.json"   # Stored inside LOGS_DIR
ACCESS_FILE = "access.json"                 # Approved/pending chat IDs (inside LOGS_DIR)

# Master admin chat ID — loaded from ADMIN_CHAT_ID in .env.
_raw_admin = os.getenv("ADMIN_CHAT_ID", "")
ADMIN_CHAT_ID: int = int(_raw_admin.strip()) if _raw_admin.strip().lstrip("-").isdigit() else 0

# Seed approved IDs from .env — merged with persisted approvals at startup.
_raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS: list[int] = [
    int(cid.strip()) for cid in _raw_ids.split(",")
    if cid.strip().lstrip("-").isdigit()
]

# Mutable set populated by init_approved_chat_ids() — checked dynamically per message.
APPROVED_CHAT_IDS: set[int] = set()

# Google Sheets — all config driven from .env
GOOGLE_ENABLED = os.getenv("GOOGLE_ENABLED", "false").strip().lower() == "true"
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Voice Transcripts")

# Local markdown files
LOCAL_ENABLED = True                          # Enabled by default, no setup needed
LOCAL_NOTES_DIR = "transcripts"               # Folder where .md files are saved
LOGS_DIR = "logs"                            # Folder for request/transcription logs
REQUESTS_LOG_FILE = "requests.jsonl"         # One JSON event per line
TRANSCRIPTIONS_LOG_FILE = "transcriptions.jsonl"
TRANSCRIPTIONS_TEXT_LOG_FILE = "transcriptions.log"

# ─────────────────────────────────────────────

BOT_START_TIME = datetime.datetime.now()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load Whisper model once at startup
# HF_TOKEN is optional. If set, it authenticates with HuggingFace for higher
# download rate limits. If absent, the warning from huggingface_hub is silenced
# so logs stay clean — the bot works identically either way.
_hf_token = os.getenv("HF_TOKEN", "").strip()
if _hf_token:
    os.environ.setdefault("HUGGING_FACE_HUB_TOKEN", _hf_token)
else:
    logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
logger.info(f"Loading Whisper model: {WHISPER_MODEL_SIZE} ...")
whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
logger.info("Whisper model loaded.")

# Ensure local transcript folder exists
if LOCAL_ENABLED:
    Path(LOCAL_NOTES_DIR).mkdir(parents=True, exist_ok=True)

# Ensure logs folder exists
Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
#  TRANSCRIPTION
# ─────────────────────────────────────────────

def transcribe_audio(file_path: str) -> tuple:
    """Transcribe an audio file using Faster-Whisper with full auto-detection.

    Returns:
        (transcript, detected_language, language_probability)
    """
    segments, info = whisper_model.transcribe(file_path, beam_size=5, language=None)
    transcript = " ".join(segment.text.strip() for segment in segments)
    logger.info(
        f"Transcribed (detected={info.language}, prob={info.language_probability:.2f}, "
        f"{info.duration:.1f}s): {transcript[:80]}..."
    )
    return transcript, info.language, info.language_probability


# ─────────────────────────────────────────────
#  STORAGE BACKENDS
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  USER SETTINGS
# ─────────────────────────────────────────────

def _settings_path() -> Path:
    return Path(LOGS_DIR) / USER_SETTINGS_FILE


def load_all_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_all_settings(settings: dict):
    _settings_path().write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def get_user_languages(user_id: int) -> list:
    """Return the preferred language list for a user, falling back to DEFAULT_LANGUAGES."""
    settings = load_all_settings()
    return settings.get(str(user_id), {}).get("preferred_languages", list(DEFAULT_LANGUAGES))


def set_user_languages(user_id: int, username: str, languages: list):
    settings = load_all_settings()
    entry = settings.get(str(user_id), {})
    entry["preferred_languages"] = languages
    entry["username"] = username
    settings[str(user_id)] = entry
    save_all_settings(settings)


# ─────────────────────────────────────────────
#  ACCESS CONTROL
# ─────────────────────────────────────────────

def _access_path() -> Path:
    return Path(LOGS_DIR) / ACCESS_FILE


def _load_access() -> dict:
    path = _access_path()
    if not path.exists():
        return {"approved": [], "pending": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"approved": data.get("approved", []), "pending": data.get("pending", [])}
    except (json.JSONDecodeError, OSError):
        return {"approved": [], "pending": []}


def _save_access(data: dict):
    _access_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def init_approved_chat_ids():
    """Populate APPROVED_CHAT_IDS from .env seeds + persisted approvals."""
    global APPROVED_CHAT_IDS
    access = _load_access()
    APPROVED_CHAT_IDS = set(ALLOWED_CHAT_IDS) | set(access["approved"])
    if ADMIN_CHAT_ID:
        APPROVED_CHAT_IDS.add(ADMIN_CHAT_ID)
    logger.info(f"Approved chat IDs: {APPROVED_CHAT_IDS}")


def approve_chat_id(chat_id: int):
    access = _load_access()
    if chat_id not in access["approved"]:
        access["approved"].append(chat_id)
    if chat_id in access["pending"]:
        access["pending"].remove(chat_id)
    _save_access(access)
    APPROVED_CHAT_IDS.add(chat_id)


def deny_chat_id(chat_id: int):
    access = _load_access()
    if chat_id in access["pending"]:
        access["pending"].remove(chat_id)
    _save_access(access)


def revoke_chat_id(chat_id: int) -> bool:
    """Remove a user from approved list. Returns True if they were in the list."""
    access = _load_access()
    if chat_id in access["approved"]:
        access["approved"].remove(chat_id)
        _save_access(access)
        if chat_id in APPROVED_CHAT_IDS:
            APPROVED_CHAT_IDS.discard(chat_id)
        return True
    return False


def add_pending_request(chat_id: int) -> bool:
    """Queue an access request. Returns True if newly added."""
    if chat_id in APPROVED_CHAT_IDS or chat_id == ADMIN_CHAT_ID:
        return False
    access = _load_access()
    if chat_id in access["pending"] or chat_id in access["approved"]:
        return False
    access["pending"].append(chat_id)
    _save_access(access)
    return True


def get_pending_requests() -> list:
    return _load_access()["pending"]


def append_jsonl(file_path: Path, payload: dict):
    """Append a JSON object as one line in a JSONL file."""
    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def extract_user_details(update: Update) -> dict:
    """Extract common Telegram user/chat details from an update."""
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message

    return {
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "first_name": user.first_name if user else None,
        "last_name": user.last_name if user else None,
        "language_code": user.language_code if user else None,
        "chat_id": chat.id if chat else None,
        "chat_type": chat.type if chat else None,
        "message_id": message.message_id if message else None,
    }


def log_request(update: Update, handler_name: str):
    """Store every handled request with user metadata to logs/requests.jsonl."""
    message = update.effective_message
    payload = {
        "timestamp": datetime.datetime.now().isoformat(),
        "handler": handler_name,
        "update_id": update.update_id,
        "text": message.text if message else None,
        "has_voice": bool(message and message.voice),
        "has_audio": bool(message and message.audio),
        "has_document": bool(message and message.document),
        "has_command": bool(message and message.text and message.text.startswith("/")),
        **extract_user_details(update),
    }
    append_jsonl(Path(LOGS_DIR) / REQUESTS_LOG_FILE, payload)


def log_transcription(timestamp: str, sender: str, transcript: str, source: str,
                      detected_lang: str = "", lang_prob: float = 0.0):
    """Store transcription output in JSONL and a plain text log file."""
    word_count = len(transcript.split()) if transcript else 0
    payload = {
        "timestamp": timestamp,
        "source": source,
        "sender": sender,
        "transcript": transcript,
        "transcript_length": len(transcript),
        "word_count": word_count,
        "detected_lang": detected_lang,
        "lang_prob": round(lang_prob, 3),
    }

    append_jsonl(Path(LOGS_DIR) / TRANSCRIPTIONS_LOG_FILE, payload)

    text_log = Path(LOGS_DIR) / TRANSCRIPTIONS_TEXT_LOG_FILE
    with text_log.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] source={source} lang={detected_lang} sender={sender}\n")
        f.write(transcript + "\n\n")


def parse_log_timestamp(value: str):
    """Best-effort parser for ISO-like timestamps in log files."""
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def get_usage_stats() -> dict:
    """Compute rich usage statistics from both log files."""
    now = datetime.datetime.now()
    day_start = datetime.datetime(now.year, now.month, now.day)
    week_start = day_start - datetime.timedelta(days=day_start.weekday())
    month_start = datetime.datetime(now.year, now.month, 1)

    def blank_period():
        return {"today": 0, "week": 0, "month": 0, "all_time": 0}

    transcriptions = blank_period()
    words = blank_period()
    by_source = {"voice": 0, "audio": 0}
    by_lang: dict[str, int] = {}
    active_days: set[str] = set()
    lengths: list[int] = []

    transcriptions_path = Path(LOGS_DIR) / TRANSCRIPTIONS_LOG_FILE
    if transcriptions_path.exists():
        for line in transcriptions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = parse_log_timestamp(entry.get("timestamp"))
            if not ts:
                continue

            wc = entry.get("word_count") or len(entry.get("transcript", "").split())
            char_len = entry.get("transcript_length", 0)
            source = entry.get("source", "voice")
            lang = entry.get("detected_lang", "unknown")

            transcriptions["all_time"] += 1
            words["all_time"] += wc
            by_source[source] = by_source.get(source, 0) + 1
            by_lang[lang] = by_lang.get(lang, 0) + 1
            active_days.add(ts.strftime("%Y-%m-%d"))
            lengths.append(char_len)

            if ts >= month_start:
                transcriptions["month"] += 1
                words["month"] += wc
            if ts >= week_start:
                transcriptions["week"] += 1
                words["week"] += wc
            if ts >= day_start:
                transcriptions["today"] += 1
                words["today"] += wc

    requests = blank_period()
    cmd_counts: dict[str, int] = {}

    requests_path = Path(LOGS_DIR) / REQUESTS_LOG_FILE
    if requests_path.exists():
        for line in requests_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = parse_log_timestamp(entry.get("timestamp"))
            if not ts:
                continue

            handler = entry.get("handler", "")
            cmd_counts[handler] = cmd_counts.get(handler, 0) + 1

            requests["all_time"] += 1
            if ts >= month_start:
                requests["month"] += 1
            if ts >= week_start:
                requests["week"] += 1
            if ts >= day_start:
                requests["today"] += 1

    avg_length = int(sum(lengths) / len(lengths)) if lengths else 0

    return {
        "transcriptions": transcriptions,
        "words": words,
        "avg_length_chars": avg_length,
        "by_source": by_source,
        "by_lang": dict(sorted(by_lang.items(), key=lambda x: x[1], reverse=True)),
        "active_days_all_time": len(active_days),
        "requests": requests,
        "cmd_counts": cmd_counts,
        "approved_count": len(APPROVED_CHAT_IDS),
        "pending_count": len(get_pending_requests()),
    }

def save_to_local(timestamp: str, transcript: str, sender: str):
    """Save transcript as a markdown file."""
    safe_ts = timestamp.replace(":", "-").replace(" ", "_")
    filename = Path(LOCAL_NOTES_DIR) / f"{safe_ts}_{sender}.md"
    content = f"# Voice Note — {timestamp}\n\n**From:** {sender}\n\n{transcript}\n"
    filename.write_text(content, encoding="utf-8")
    logger.info(f"Saved locally: {filename}")


def save_to_google_sheets(timestamp: str, transcript: str, sender: str):
    """Append a row to Google Sheets, creating a header row on first use."""
    if not GSPREAD_AVAILABLE:
        logger.warning("gspread not installed. Skipping Google Sheets.")
        return
    if not Path(GOOGLE_SERVICE_ACCOUNT_FILE).exists():
        logger.error(f"Google credentials file not found: {GOOGLE_SERVICE_ACCOUNT_FILE}")
        return
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1

        # Write header only if the sheet is truly empty
        if not sheet.get_all_values():
            sheet.append_row(["Timestamp", "Sender", "Transcript"])

        sheet.append_row([timestamp, sender, transcript])
        logger.info("Saved to Google Sheets.")
    except Exception as e:
        logger.error(f"Google Sheets error: {e}")


# ─────────────────────────────────────────────
#  AUTH FILTER
# ─────────────────────────────────────────────

class _AuthFilter(filters.MessageFilter):
    """Passes messages only from the admin and approved chat IDs."""
    name = "AuthFilter"

    def filter(self, message) -> bool:
        return message.chat_id == ADMIN_CHAT_ID or message.chat_id in APPROVED_CHAT_IDS


auth_filter = _AuthFilter()


# ─────────────────────────────────────────────
#  UI HELPERS — keyboards and text builders
# ─────────────────────────────────────────────

# Language presets shown in the language picker (label, comma-separated codes).
_LANG_PRESETS = [
    ("🇿🇦 Afrikaans + English",   "af,en"),
    ("🇬🇧 English",               "en"),
    ("🇫🇷 French + English",      "fr,en"),
    ("🇩🇪 German + English",      "de,en"),
    ("🇪🇸 Spanish + English",     "es,en"),
    ("🇵🇹 Portuguese + English",  "pt,en"),
    ("🇳🇱 Dutch + English",       "nl,en"),
    ("🇷🇺 Russian",               "ru"),
    ("🇨🇳 Chinese",               "zh"),
    ("🇮🇳 Hindi + English",       "hi,en"),
]


def _main_menu_text() -> str:
    return (
        "👋 *Voice Transcription Bot*\n\n"
        "Send me a *voice message* or *audio file* and I'll transcribe it instantly.\n\n"
        "_Language is always auto-detected._"
    )


def _main_menu_keyboard(is_admin: bool, pending_count: int = 0) -> InlineKeyboardMarkup:
    pending_label = (
        f"📋 Pending requests ({pending_count})" if pending_count else "📋 Pending requests"
    )
    rows: list = []
    if is_admin:
        rows.append([InlineKeyboardButton(pending_label, callback_data="menu_pending")])
        rows.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="menu_admin")])
    rows.append([
        InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings"),
        InlineKeyboardButton("📊 Stats",    callback_data="menu_stats"),
    ])
    return InlineKeyboardMarkup(rows)


def _settings_text(user_id: int) -> str:
    langs = get_user_languages(user_id)
    return (
        "⚙️ *Your Settings*\n\n"
        f"Preferred languages: `{' '.join(langs)}`\n\n"
        "_Whisper always auto-detects the language. Your list is used to warn you "
        "when something unexpected is detected._"
    )


def _settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Change Languages", callback_data="menu_lang_picker")],
        [InlineKeyboardButton("🔙 Main Menu",        callback_data="menu_main")],
    ])


def _lang_picker_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(_LANG_PRESETS), 2):
        row = [
            InlineKeyboardButton(label, callback_data=f"lang_preset:{codes}")
            for label, codes in _LANG_PRESETS[i:i + 2]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="menu_settings")])
    return InlineKeyboardMarkup(rows)


def _build_stats_text(is_admin: bool) -> str:
    s = get_usage_stats()
    t = s["transcriptions"]
    w = s["words"]

    lang_lines = "\n".join(
        f"  {lang}: {count}" for lang, count in s["by_lang"].items()
    ) or "  none yet"

    source_line = (
        f"voice: {s['by_source'].get('voice', 0)}  ·  "
        f"audio file: {s['by_source'].get('audio', 0)}"
    )

    uptime = datetime.datetime.now() - BOT_START_TIME
    uptime_str = str(uptime).split(".")[0]

    msg = (
        "📊 *Usage Stats*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "*Transcriptions*\n"
        f"  Today: {t['today']}  ·  Week: {t['week']}  ·  Month: {t['month']}  ·  All-time: {t['all_time']}\n"
        "\n*Words transcribed*\n"
        f"  Today: {w['today']}  ·  Week: {w['week']}  ·  Month: {w['month']}  ·  All-time: {w['all_time']}\n"
        "\n*Input type (all-time)*\n"
        f"  {source_line}\n"
        "\n*Languages detected (all-time)*\n"
        f"{lang_lines}\n"
        "\n*Session*\n"
        f"  Uptime: {uptime_str}\n"
        f"  Active days: {s['active_days_all_time']}\n"
    )

    if s["avg_length_chars"]:
        msg += f"\n*Avg transcript length:* {s['avg_length_chars']} chars\n"

    if is_admin:
        msg += (
            "\n━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔐 *Admin — Access*\n"
            f"  Approved users: {s['approved_count']}\n"
            f"  Pending requests: {s['pending_count']}\n"
        )
        if s["pending_count"]:
            msg += "  Use /pending to review.\n"

    return msg


# ─────────────────────────────────────────────
#  TELEGRAM HANDLERS
# ─────────────────────────────────────────────

def _build_transcript_reply(transcript: str, detected_lang: str, lang_prob: float,
                             preferred_langs: list, destinations: list) -> str:
    """Compose the reply message shown to the user after a transcription."""
    saved_to = " · ".join(destinations)
    lang_line = f"_Language: {detected_lang} ({lang_prob * 100:.0f}% confidence)_"

    warning = ""
    if detected_lang not in preferred_langs:
        warning = (
            f"\n⚠️ _Detected language '{detected_lang}' is not in your preferred list "
            f"{preferred_langs}. Use /setlanguages to update your preferences._"
        )

    return (
        f"✅ *Transcript:*\n\n{transcript}\n\n"
        f"{lang_line}{warning}\n"
        f"_Saved to: {saved_to}_"
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming voice messages."""
    log_request(update, "handle_voice")
    message = update.message
    user_id = message.from_user.id
    sender = message.from_user.username or str(user_id)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    await message.reply_text("🎙️ Received your voice note! Transcribing...")

    # Download the voice file
    voice_file = await context.bot.get_file(message.voice.file_id)
    audio_path = f"temp_voice_{message.message_id}.ogg"
    await voice_file.download_to_drive(audio_path)

    try:
        transcript, detected_lang, lang_prob = transcribe_audio(audio_path)
        log_transcription(timestamp, sender, transcript, source="voice",
                          detected_lang=detected_lang, lang_prob=lang_prob)

        if LOCAL_ENABLED:
            save_to_local(timestamp, transcript, sender)
        if GOOGLE_ENABLED:
            save_to_google_sheets(timestamp, transcript, sender)

        destinations = []
        if LOCAL_ENABLED:
            destinations.append("📄 Local file")
        if GOOGLE_ENABLED:
            destinations.append("📊 Google Sheets")

        preferred = get_user_languages(user_id)
        reply = _build_transcript_reply(transcript, detected_lang, lang_prob, preferred, destinations)
        await message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        await message.reply_text(f"❌ Error during transcription: {e}")

    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded audio files (e.g. recorded voice notes sent as files)."""
    log_request(update, "handle_audio")
    message = update.message
    user_id = message.from_user.id
    sender = message.from_user.username or str(user_id)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    await message.reply_text("🎵 Received your audio file! Transcribing...")

    audio = message.audio or message.document
    audio_file = await context.bot.get_file(audio.file_id)
    audio_path = f"temp_audio_{message.message_id}"
    await audio_file.download_to_drive(audio_path)

    try:
        transcript, detected_lang, lang_prob = transcribe_audio(audio_path)
        log_transcription(timestamp, sender, transcript, source="audio",
                          detected_lang=detected_lang, lang_prob=lang_prob)

        if LOCAL_ENABLED:
            save_to_local(timestamp, transcript, sender)
        if GOOGLE_ENABLED:
            save_to_google_sheets(timestamp, transcript, sender)

        destinations = []
        if LOCAL_ENABLED:
            destinations.append("📄 Local file")
        if GOOGLE_ENABLED:
            destinations.append("📊 Google Sheets")

        preferred = get_user_languages(user_id)
        reply = _build_transcript_reply(transcript, detected_lang, lang_prob, preferred, destinations)
        await message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Audio transcription error: {e}")
        await message.reply_text(f"❌ Error: {e}")

    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle plain text messages — nudge the user toward the menu."""
    log_request(update, "handle_text")
    chat_id = update.effective_chat.id
    is_admin = chat_id == ADMIN_CHAT_ID
    pending_count = len(get_pending_requests()) if is_admin else 0
    await update.message.reply_text(
        "👋 Send me a *voice message* or *audio file* and I'll transcribe it for you!",
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(is_admin, pending_count),
    )


async def handle_setlanguages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set preferred languages: /setlanguages af en"""
    log_request(update, "handle_setlanguages")
    user = update.effective_user
    args = context.args

    if not args:
        current = get_user_languages(user.id)
        await update.message.reply_text(
            f"Your current preferred languages: `{' '.join(current)}`\n\n"
            "Usage: `/setlanguages af en` (space-separated Whisper language codes)\n"
            "See full list: https://github.com/openai/whisper#available-models-and-languages",
            parse_mode="Markdown"
        )
        return

    languages = [lang.lower().strip() for lang in args]
    set_user_languages(user.id, user.username or str(user.id), languages)
    await update.message.reply_text(
        f"✅ Preferred languages updated to: `{' '.join(languages)}`\n"
        "Whisper will still auto-detect the language — you'll be notified if it detects "
        "something outside your list.",
        parse_mode="Markdown"
    )


async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the current user settings with inline edit buttons."""
    log_request(update, "handle_settings")
    user = update.effective_user
    await update.message.reply_text(
        _settings_text(user.id),
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(),
    )


async def handle_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all for messages from unapproved chat IDs."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    # Silently ignore if the user is already approved (they sent a message type
    # we don't handle, e.g. a sticker).
    if chat_id in APPROVED_CHAT_IDS or chat_id == ADMIN_CHAT_ID:
        return
    logger.warning(f"Unauthorized access attempt from chat_id={chat_id}")
    access = _load_access()
    if chat_id in access["pending"]:
        await update.effective_message.reply_text(
            "⏳ Your access request is still pending. You'll be notified when approved."
        )
    else:
        await update.effective_message.reply_text(
            "🔒 *This bot is private.*\n\nTap the button below to request access.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📨 Request Access", callback_data="req_access"),
            ]]),
        )


async def handle_requestaccess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow anyone to request access. Notifies the admin."""
    log_request(update, "handle_requestaccess")
    chat_id = update.effective_chat.id
    if chat_id in APPROVED_CHAT_IDS or chat_id == ADMIN_CHAT_ID:
        await update.message.reply_text("✅ You already have access.")
        return
    added = add_pending_request(chat_id)
    if added:
        await update.message.reply_text(
            "📨 Access request sent. You will be notified once approved."
        )
        if ADMIN_CHAT_ID:
            user = update.effective_user
            name_hint = f"@{user.username}" if user.username else (user.first_name or str(chat_id))
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    "🔔 *New access request*\n\n"
                    f"From: {name_hint}\n"
                    f"chat_id: `{chat_id}`"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_{chat_id}"),
                    InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{chat_id}"),
                ]]),
            )
    else:
        await update.message.reply_text("⏳ Your request is already pending.")


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: /approve <chat_id>"""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    log_request(update, "handle_approve")
    if not context.args:
        await update.message.reply_text("Usage: `/approve <chat_id>`", parse_mode="Markdown")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat ID.")
        return
    approve_chat_id(target)
    await update.message.reply_text(f"✅ Approved `{target}`.", parse_mode="Markdown")
    try:
        await context.bot.send_message(
            chat_id=target,
            text="✅ Your access has been approved! Tap below to get started.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 Get Started", callback_data="menu_main"),
            ]]),
        )
    except Exception:
        await update.message.reply_text(f"_(Could not notify {target} — they may not have messaged the bot yet.)_",
                                        parse_mode="Markdown")


async def handle_deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: /deny <chat_id>"""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    log_request(update, "handle_deny")
    if not context.args:
        await update.message.reply_text("Usage: `/deny <chat_id>`", parse_mode="Markdown")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat ID.")
        return
    deny_chat_id(target)
    await update.message.reply_text(f"⛔ Denied `{target}`.", parse_mode="Markdown")
    try:
        await context.bot.send_message(chat_id=target, text="⛔ Your access request was not approved.")
    except Exception:
        pass


async def handle_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: /revoke <chat_id> to remove access from approved user."""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    log_request(update, "handle_revoke")
    if not context.args:
        await update.message.reply_text("Usage: `/revoke <chat_id>`", parse_mode="Markdown")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat ID.")
        return
    
    if target in ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            f"⚠️ `{target}` is in the hardcoded allow list (.env). "
            "Remove it from `ALLOWED_CHAT_IDS` in .env and restart the bot.",
            parse_mode="Markdown"
        )
        return
    
    if revoke_chat_id(target):
        await update.message.reply_text(f"🚫 Revoked access for `{target}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target, text="🚫 Your access has been revoked.")
        except Exception:
            logger.warning(f"Could not notify {target} of revocation")
    else:
        await update.message.reply_text(f"⚠️ `{target}` is not in the approved list.", parse_mode="Markdown")


async def handle_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: list pending access requests with approve/deny buttons."""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    log_request(update, "handle_pending")
    pending = get_pending_requests()
    if not pending:
        await update.message.reply_text("✅ No pending access requests.")
        return
    
    keyboard = []
    for cid in pending:
        keyboard.append([
            InlineKeyboardButton(f"✅ Approve {cid}", callback_data=f"approve_{cid}"),
            InlineKeyboardButton(f"❌ Deny {cid}", callback_data=f"deny_{cid}"),
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")])
    await update.message.reply_text(
        f"📋 *Pending access requests ({len(pending)}):*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button presses."""
    query = update.callback_query
    await query.answer()

    if not query.message:
        return  # message was deleted before the button was pressed

    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    is_admin = chat_id == ADMIN_CHAT_ID

    # ── Access request (unapproved users tapping the button) ─────────────────
    if data == "req_access":
        if chat_id in APPROVED_CHAT_IDS or is_admin:
            await query.edit_message_text("✅ You already have access.")
            return
        added = add_pending_request(chat_id)
        if added:
            await query.edit_message_text(
                "📨 Access request sent. You will be notified once approved."
            )
            if ADMIN_CHAT_ID:
                user = query.from_user
                name_hint = f"@{user.username}" if user.username else (user.first_name or str(chat_id))
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        "🔔 *New access request*\n\n"
                        f"From: {name_hint}\n"
                        f"chat_id: `{chat_id}`"
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Approve", callback_data=f"approve_{chat_id}"),
                        InlineKeyboardButton("❌ Deny",    callback_data=f"deny_{chat_id}"),
                    ]]),
                )
        else:
            await query.edit_message_text(
                "⏳ Your request is already pending. You'll be notified when approved."
            )
        return

    # ── Navigation ────────────────────────────────────────────────────────────
    if data == "menu_main":
        pending_count = len(get_pending_requests()) if is_admin else 0
        await query.edit_message_text(
            _main_menu_text(),
            parse_mode="Markdown",
            reply_markup=_main_menu_keyboard(is_admin, pending_count),
        )
        return

    if data == "menu_settings":
        await query.edit_message_text(
            _settings_text(user_id),
            parse_mode="Markdown",
            reply_markup=_settings_keyboard(),
        )
        return

    if data == "menu_stats":
        await query.edit_message_text(
            _build_stats_text(is_admin),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh",   callback_data="menu_stats"),
                 InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")],
            ]),
        )
        return

    if data == "menu_ping":
        if not is_admin:
            return
        uptime = datetime.datetime.now() - BOT_START_TIME
        uptime_str = str(uptime).split(".")[0]
        await query.edit_message_text(
            f"pong ✅  uptime: {uptime_str}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Admin Panel", callback_data="menu_admin"),
            ]]),
        )
        return

    if data == "menu_admin":
        if not is_admin:
            return
        await query.edit_message_text(
            "🔐 *Admin Panel*\n\n"
            "_User-specific actions require an ID — use the commands below:_\n"
            "`/approve <id>`  ·  `/deny <id>`  ·  `/revoke <id>`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🏓 Ping",              callback_data="menu_ping"),
                    InlineKeyboardButton("📄 Pending requests", callback_data="menu_pending"),
                ],
                [InlineKeyboardButton("📊 Stats",              callback_data="menu_stats")],
                [InlineKeyboardButton("🔙 Main Menu",          callback_data="menu_main")],
            ]),
        )
        return

    if data == "menu_pending":
        if not is_admin:
            return
        pending = get_pending_requests()
        if not pending:
            await query.edit_message_text(
                "✅ No pending access requests.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main"),
                ]]),
            )
            return
        keyboard = [
            [InlineKeyboardButton(f"✅ Approve {cid}", callback_data=f"approve_{cid}"),
             InlineKeyboardButton(f"❌ Deny {cid}",    callback_data=f"deny_{cid}")]
            for cid in pending
        ]
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")])
        await query.edit_message_text(
            f"📋 *Pending access requests ({len(pending)}):*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data == "menu_lang_picker":
        await query.edit_message_text(
            "🌐 *Choose your preferred languages:*\n\n"
            "_Whisper always auto-detects — this controls which languages are 'expected'._",
            parse_mode="Markdown",
            reply_markup=_lang_picker_keyboard(),
        )
        return

    # ── Language preset selection ─────────────────────────────────────────────
    if data.startswith("lang_preset:"):
        codes_str = data.split(":", 1)[1]
        langs = [c.strip() for c in codes_str.split(",") if c.strip()]
        username = query.from_user.username or str(user_id)
        set_user_languages(user_id, username, langs)
        await query.edit_message_text(
            f"✅ Languages set to: `{' '.join(langs)}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Back to Settings", callback_data="menu_settings")],
                [InlineKeyboardButton("🔙 Main Menu",        callback_data="menu_main")],
            ]),
        )
        return

    # ── Admin approve / deny ──────────────────────────────────────────────────
    if data.startswith("approve_"):
        try:
            target = int(data.split("_")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Invalid request.")
            return
        approve_chat_id(target)
        await query.edit_message_text(f"✅ Approved `{target}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target,
                text="✅ Your access has been approved! Tap below to get started.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚀 Get Started", callback_data="menu_main"),
                ]]),
            )
        except Exception as e:
            logger.warning(f"Could not notify {target}: {e}")
        return

    if data.startswith("deny_"):
        try:
            target = int(data.split("_")[1])
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Invalid request.")
            return
        deny_chat_id(target)
        await query.edit_message_text(f"⛔ Denied `{target}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target, text="⛔ Your access request was not approved.")
        except Exception as e:
            logger.warning(f"Could not notify {target}: {e}")
        return


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Respond to /start and /help — visible to everyone."""
    log_request(update, "handle_start")
    chat_id = update.effective_chat.id
    is_admin = chat_id == ADMIN_CHAT_ID
    approved = chat_id in APPROVED_CHAT_IDS or is_admin

    if not approved:
        await update.message.reply_text(
            "🔒 *This bot is private.*\n\nTap the button below to request access.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📨 Request Access", callback_data="req_access"),
            ]]),
        )
        return

    pending_count = len(get_pending_requests()) if is_admin else 0
    await update.message.reply_text(
        _main_menu_text(),
        parse_mode="Markdown",
        reply_markup=_main_menu_keyboard(is_admin, pending_count),
    )


async def handle_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: simple health-check endpoint."""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    log_request(update, "handle_ping")
    uptime = datetime.datetime.now() - BOT_START_TIME
    uptime_seconds = int(uptime.total_seconds())
    await update.message.reply_text(f"pong ✅ uptime: {uptime_seconds}s")


async def handle_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show rich usage metrics with a refresh button."""
    log_request(update, "handle_stats")
    is_admin = update.effective_chat.id == ADMIN_CHAT_ID
    await update.message.reply_text(
        _build_stats_text(is_admin),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh",   callback_data="menu_stats"),
             InlineKeyboardButton("🔙 Main Menu", callback_data="menu_main")],
        ]),
    )


# ─────────────────────────────────────────────
#  ERROR HANDLER
# ─────────────────────────────────────────────

async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log exceptions and notify the admin so errors don't silently disappear."""
    logger.error("Unhandled exception:", exc_info=context.error)
    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ *Bot error*\n\n`{type(context.error).__name__}: {context.error}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        raise ValueError("Set TELEGRAM_BOT_TOKEN in your .env file before running the bot.")
    if not ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID is not set in .env — no one will have admin privileges.")

    init_approved_chat_ids()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    af = auth_filter  # approved users + admin

    # /start and /requestaccess are open to everyone so unapproved users can request access.
    app.add_handler(CommandHandler(["start", "help"], handle_start))
    app.add_handler(CommandHandler("requestaccess",  handle_requestaccess))

    # Admin-only commands (checked inside each handler).
    app.add_handler(CommandHandler("approve",  handle_approve))
    app.add_handler(CommandHandler("deny",     handle_deny))
    app.add_handler(CommandHandler("revoke",   handle_revoke))
    app.add_handler(CommandHandler("pending",  handle_pending))

    # Approved-user commands.
    app.add_handler(CommandHandler("ping",         handle_ping))
    app.add_handler(CommandHandler("stats",        handle_stats,        filters=af))
    app.add_handler(CommandHandler("setlanguages", handle_setlanguages, filters=af))
    app.add_handler(CommandHandler("settings",     handle_settings,     filters=af))
    app.add_handler(MessageHandler(af & filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(af & (filters.AUDIO | filters.Document.AUDIO), handle_audio))
    app.add_handler(MessageHandler(af & filters.TEXT & ~filters.COMMAND, handle_text))

    # Callback query handler must come before the catch-all MessageHandler because
    # update.effective_message is non-null for callback queries in PTB, so
    # MessageHandler(filters.ALL) would otherwise intercept every button press.
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Catch-all: guides unapproved users, silently ignores unsupported message types.
    app.add_handler(MessageHandler(filters.ALL, handle_unauthorized))

    app.add_error_handler(handle_error)

    logger.info(f"Bot running. Admin: {ADMIN_CHAT_ID}  Approved: {APPROVED_CHAT_IDS}")
    # Explicitly request all update types so Telegram's server-side cache can't
    # silently drop callback_query (or any other type) from a previous session config.
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
