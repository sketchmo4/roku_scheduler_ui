# roku_scheduler_ui

Portainer-friendly Docker stack that provides a small web UI to schedule launching YouTube on a Roku (via Roku ECP).

## Features
- Set time of day (HH:MM)
- Choose days of week
- Repeat every N weeks
- "Run now" button
- Persistent config + log tail
- Optional (best-effort) YouTube deep-link via URL (playlist/video)

## Run (local)
```bash
cp .env.example .env
cd docker
docker compose up -d --build
```

Open:
- http://localhost:4050/

## Portainer
Use `docker/docker-compose.yml` as a Stack.
Set env vars as needed (UI_PORT, TZ, DATA_DIR).
