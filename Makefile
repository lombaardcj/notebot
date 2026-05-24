.PHONY: build up down restart rebuild hotreload logs shell

# Build the image without starting
build:
	docker compose build

# Start in background
up:
	@if pgrep -f telegram_transcriber_bot.py > /dev/null 2>&1; then \
		echo "⚠️  WARNING: a local telegram_transcriber_bot.py process is running!"; \
		echo "   Two instances on the same token will split updates. Kill it first."; \
	fi
	docker compose up -d

# Stop and remove containers
down:
	docker compose down

# Restart without rebuilding
restart:
	docker compose restart

# Force a full rebuild and restart (use after code changes)
rebuild:
	docker compose down
	docker compose build --no-cache
	docker compose up -d
	docker compose logs -f

# Fast reload: re-copy changed .py file and recreate container to pick up .env.
# Docker cache keeps every layer above COPY, so this takes seconds not minutes.
hotreload:
	@if pgrep -f telegram_transcriber_bot.py > /dev/null 2>&1; then \
		echo "⚠️  WARNING: a local telegram_transcriber_bot.py process is running!"; \
		echo "   Two instances on the same token will split updates. Kill it first."; \
	fi
	docker compose up -d --build
	docker compose logs -f

# Follow live logs
logs:
	docker compose logs -f

# Open a shell inside the running container
shell:
	docker compose exec notebot bash
