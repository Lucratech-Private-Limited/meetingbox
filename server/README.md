# MeetingBox server (cloud / VPS)

This folder is the **API + dashboard** stack: **FastAPI** (`web/`), **React SPA** (`frontend/`), **nginx**, and **Redis**.

The meeting-room device code lives in **`mini-pc/`** in the full monorepo — use a separate repository for appliances.

## Layout

| Path | Purpose |
|------|---------|
| `web/` | FastAPI (OpenAI Whisper + Anthropic, WebSocket, SQLite) |
| `frontend/` | React dashboard (`npm run build` → `dist/`) |
| `nginx/` | Reverse proxy config |
| `scripts/` | Host reboot/poweroff helpers mounted into `web` |
| `data/` | Transcripts, audio, config volumes (created on first run) |
| `docker-compose.yml` | redis + web + nginx |
| `.env.example` | Copy to `.env` |

## Deploy on a VPS

```bash
cd server
cp .env.example .env
nano .env   # JWT_SECRET_KEY, API keys, FRONTEND_DIST, DATA_ROOT, APP_BASE_URL, …
# Build the SPA (pick one):
#   cd frontend && npm ci && npm run build && cd ..   # if frontend/ exists under server/
#   — or build in a separate frontend repo and set FRONTEND_DIST to that dist/
docker compose up -d --build
```

## Monorepo vs standalone vs separate frontend repo

| Layout | `FRONTEND_DIST` (example) | `DATA_ROOT` (example) |
|--------|---------------------------|-------------------------|
| Monorepo: `meetingbox/server` + `meetingbox/frontend` | `../frontend/dist` | `../data` |
| Server repo with `frontend/` copied under `server/frontend/` | `./frontend/dist` | `./data` |
| **Three repos:** server + frontend clones are **siblings** | `../meetingbox-frontend/dist` | `./data` |

Use absolute paths if you prefer. Only `dist/` must exist before `docker compose up`; Node is not needed on the server at runtime.

## Split with `git subtree`

From the full monorepo:

```bash
git subtree split --prefix=server -b server-release
git push <your-server-remote> server-release:main
```

On the VPS, clone the **server** repository. Build the SPA in your **frontend** repository (or CI) and set `FRONTEND_DIST` accordingly — no `mini-pc/` checkout required.
