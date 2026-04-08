MeetingBox Core Software MVP
============================

This repository can be used as a **monorepo** or split into **two packages**:

| Package | Folder | Role |
|---------|--------|------|
| **Server** | [`server/`](server/README.md) | FastAPI (`web/`), React (`frontend/` at repo root for now), nginx, Redis — deploy on a VPS |
| **Appliance** | [`mini-pc/`](mini-pc/README.md) | Kivy UI + mic capture — runs on the meeting-room device |

**Cloud deploy:** `cd server && cp .env.example .env && … && docker compose up -d --build` (see `server/README.md`).

MeetingBox captures room audio, transcribes with **OpenAI** (Whisper), summarizes with **Anthropic** Claude, and serves a React dashboard.

## Repository layout (monorepo)

- [`server/`](server/README.md) – **Cloud stack**: `web/` (FastAPI), nginx, scripts, `docker-compose.yml`; env template `server/.env.example`
- `frontend/` – React SPA (built to `frontend/dist/`; used by root `docker-compose` and symlinked via `server/.env` as `../frontend/dist`)
- [`mini-pc/`](mini-pc/README.md) – Device UI + audio; `mini-pc/.env.example`
- `data/` – Shared volume for local all-in-one dev (transcripts, audio, SQLite)
- `scripts/` – General installers (e.g. `install_device_ui.sh`); host helpers live in `server/scripts/`
- `docker-compose.yml` – **Full dev stack** (server `web` + nginx + `mini-pc` profiles)
- `.env.example` – Root dev compose env

## Splitting into two git repositories

```bash
# Server (API + dashboard)
git subtree split --prefix=server -b server-release

# Appliance (device + mic)
git subtree split --prefix=mini-pc -b mini-pc-release
```

For a **standalone `server` repo**, move or copy `frontend/` into `server/frontend/` (or adjust `FRONTEND_DIST` / `DATA_ROOT` in `server/.env`).

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
