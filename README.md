MeetingBox Core Software MVP
============================

This repository can be used as a **monorepo** or split into **three git repositories**:

| Package | Folder | Role |
|---------|--------|------|
| **Server** | [`server/`](server/README.md) | FastAPI (`web/`), nginx, Redis — deploy on a VPS |
| **Frontend** | [`frontend/`](frontend/README.md) | React (Vite) dashboard; build `dist/` and point `server` at it via `FRONTEND_DIST` |
| **Appliance** | [`mini-pc/`](mini-pc/README.md) | Kivy UI + mic capture — meeting-room device |

**Cloud deploy:** build the SPA (`frontend/README.md`), then `cd server && cp .env.example .env && … && docker compose up -d --build` (see `server/README.md`).

MeetingBox captures room audio, transcribes with **OpenAI** (Whisper), summarizes with **Anthropic** Claude, and serves a React dashboard.

## Repository layout (monorepo)

- [`server/`](server/README.md) – **Cloud stack**: `web/` (FastAPI), nginx, scripts, `docker-compose.yml`; env template `server/.env.example`
- `frontend/` – React SPA (built to `frontend/dist/`; used by root `docker-compose` and symlinked via `server/.env` as `../frontend/dist`)
- [`mini-pc/`](mini-pc/README.md) – Device UI + audio; `mini-pc/.env.example`
- `data/` – Shared volume for local all-in-one dev (transcripts, audio, SQLite)
- `scripts/` – General installers (e.g. `install_device_ui.sh`); host helpers live in `server/scripts/`
- `docker-compose.yml` – **Full dev stack** (server `web` + nginx + `mini-pc` profiles)
- `.env.example` – Root dev compose env

## Splitting into three git repositories

Run from the monorepo root (replace remotes and branch names as needed):

```bash
git fetch origin

# 1) API + nginx + Redis (no React sources required on the server host if you ship dist only)
git subtree split --prefix=server -b server-release
git push <server-remote> server-release:main

# 2) React dashboard (CI builds dist/, you sync artifacts to the host or a sibling checkout)
git subtree split --prefix=frontend -b frontend-release
git push <frontend-remote> frontend-release:main

# 3) Device UI + audio
git subtree split --prefix=mini-pc -b mini-pc-release
git push <mini-pc-remote> mini-pc-release:main
```

**Wiring:** In `server/.env`, set `FRONTEND_DIST` to the built SPA (for example `./frontend/dist` if you copied `frontend` under `server/`, or `../<frontend-repo-name>/dist` for sibling clones). See `server/.env.example` and `frontend/README.md`.

## Getting started (development, full stack on one machine)

1. Install Docker and Docker Compose.
2. Copy `.env.example` to `.env` and set `JWT_SECRET_KEY`, API keys, and `COMPOSE_PROFILES=backend,frontend`.
3. `cd frontend && npm install && npm run build && cd ..`
4. `docker compose up --build -d`
5. Open `http://localhost:8000`.

## Start / Stop meeting and test WAV

- **From the dashboard**: start/stop recording (with audio service / host capture).
- **Test file:** `python scripts/ingest_test_wav.py path/to/your.wav`

## Linux / on-device deployment

See **[DEPLOY_LINUX.md](DEPLOY_LINUX.md)**. Server-only deploy: **[server/README.md](server/README.md)**.

> **Note:** The `docker-audio` profile adds ALSA device access for the audio **container**. On Windows Docker Desktop, omit that profile.
