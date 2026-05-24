FROM python:3.12-slim

# ffmpeg is needed by faster-whisper for audio decoding
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer-cached until requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot script
COPY telegram_transcriber_bot.py .

# Runtime directories are mounted as volumes; create them so the bot
# can start even if the host hasn't initialised them yet.
RUN mkdir -p logs transcripts secrets

CMD ["python3", "-u", "telegram_transcriber_bot.py"]
