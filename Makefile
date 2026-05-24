.PHONY: build up down restart rebuild logs shell

# Build the image without starting
build:
	docker compose build

# Start in background
up:
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

# Follow live logs
logs:
	docker compose logs -f

# Open a shell inside the running container
shell:
	docker compose exec notebot bash
