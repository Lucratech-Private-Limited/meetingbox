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
nano .env   # JWT_SECRET_KEY, API keys, APP_BASE_URL, OAUTH_PUBLIC_BASE_URL, …
cd frontend && npm ci && npm run build && cd ..
docker compose up -d --build
```

## Monorepo vs standalone

If this tree still lives under `…/meetingbox/server/` with **`frontend/`** at `…/meetingbox/frontend/`, the default `.env.example` sets `FRONTEND_DIST=../frontend/dist` and `DATA_ROOT=../data`.

If this folder **is** the git root of your server-only repo, put `frontend/` inside it (or merge subtrees) and set:

```env
FRONTEND_DIST=./frontend/dist
DATA_ROOT=./data
```

## Split with `git subtree`

From the full monorepo:

```bash
git subtree split --prefix=server -b server-release
git push <your-server-remote> server-release:main
```

On the VPS, clone that repository — no mini-pc checkout required.
