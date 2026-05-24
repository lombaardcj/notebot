# System Architecture

## Components

The bot has no external service dependencies for its core function. Transcription runs entirely on local hardware. Google Sheets is an optional cloud sink.

```mermaid
flowchart TD
    User("👤 User\nTelegram client")
    Admin("🔐 Admin\nTelegram client")
    TG("☁️ Telegram API")
    Bot("🤖 Bot Process\ntelegram_transcriber_bot.py")
    Whisper("🎙️ Faster-Whisper\nlocal CPU inference")
    Access("🗂️ access.json\napproved / pending IDs")
    Logs("📋 logs/\nrequests.jsonl\ntranscriptions.jsonl")
    Files("📄 transcripts/\nmarkdown files")
    Sheets("📊 Google Sheets\noptional")

    User -- "voice / audio message" --> TG
    Admin -- "approve · deny · revoke buttons" --> TG
    TG -- "long-poll updates" --> Bot
    Bot -- "download audio" --> TG
    TG -- "audio bytes" --> Bot
    Bot -- "transcribe\nlanguage=auto" --> Whisper
    Whisper -- "transcript + lang + confidence" --> Bot
    Bot -- "write .md" --> Files
    Bot -- "append JSONL" --> Logs
    Bot -- "read / write" --> Access
    Bot -. "append row\nif GOOGLE_ENABLED=true" .-> Sheets
    Bot -- "reply with transcript" --> TG
    TG -- "deliver reply" --> User
    Bot -- "access request alert" --> TG
    TG -- "inline button notification" --> Admin
```

---

## Transcription flow

What happens when a user sends a voice message.

```mermaid
sequenceDiagram
    actor U as User
    participant TG as Telegram API
    participant B as Bot
    participant W as Faster-Whisper
    participant F as transcripts/
    participant G as Google Sheets

    U->>TG: sends voice / audio message
    TG->>B: update delivered (polling)
    B->>TG: request audio file download
    TG-->>B: raw audio bytes
    B->>W: transcribe(audio, language=None)
    W-->>B: transcript · detected_lang · confidence
    B->>F: save transcript as .md
    B-->>G: append row (only if GOOGLE_ENABLED=true)
    B->>B: append to transcriptions.jsonl
    B->>TG: reply with transcript + lang info
    TG-->>U: ✅ Transcript delivered
```

---

## Access control flow

What happens when a new user tries to use the bot.

```mermaid
sequenceDiagram
    actor U as New User
    actor A as Admin
    participant TG as Telegram API
    participant B as Bot
    participant AC as access.json

    U->>TG: /start  (or any message)
    TG->>B: update
    B-->>TG: "🔒 Use /requestaccess"
    TG-->>U: prompt shown

    U->>TG: /requestaccess
    TG->>B: command
    B->>AC: add to pending[]
    B-->>TG: "📨 Request sent"
    TG-->>U: confirmation
    B->>TG: alert admin with Approve / Deny buttons
    TG-->>A: notification

    A->>TG: taps ✅ Approve button
    TG->>B: callback query
    B->>AC: move to approved[]
    B-->>TG: update button message
    TG-->>A: "✅ Approved"
    B->>TG: notify user
    TG-->>U: "✅ Access approved!"

    Note over U,B: User can now send voice messages
```
