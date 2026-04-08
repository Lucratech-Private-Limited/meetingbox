# MeetingBox mini PC (appliance)

This folder contains everything that normally runs on the **meeting room device**: the **Kivy device UI** and the **audio capture** stack. The FastAPI dashboard lives in **`server/`** in the main repo (or your VPS uses a server-only clone).

## Contents

| Path | Purpose |
|------|---------|
| `device-ui/` | Touch/kiosk UI (Python/Kivy) |
| `audio/` | Mic capture, VAD, WAV upload (`run_audio_capture.sh` + Docker image) |
| `docker-compose.yml` | Optional: run UI and/or Docker audio on the device |
| `.env.example` | All appliance env vars — copy to `.env` |

## Quick start (mini PC only)

```bash
cd mini-pc
cp .env.example .env
nano .env   # BACKEND_URL, BACKEND_WS_URL, UPLOAD_AUDIO_API_URL, DASHBOARD_URL
mkdir -p data/audio/recordings data/audio/temp data/config
```

**UI (recommended native):**

```bash
cd device-ui
cp .env.example .env   # optional per-app overrides
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
./run_device_ui.sh
```

**Mic (recommended host script):**

```bash
cd audio
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# Ensure .env or exports: REDIS_HOST, UPLOAD_AUDIO_API_URL, DEVICE_AUTH_TOKEN after pairing
./run_audio_capture.sh
```

**UI + Docker mic** (optional):

```bash
docker compose --profile mini-pc --profile docker-audio up -d --build
```

## Splitting into its own git repository

From the monorepo root (preserve history for this subtree):

```bash
git subtree split --prefix=mini-pc -b mini-pc-release
git push <your-appliance-remote> mini-pc-release:main
```

On the device, clone that repo and use only this directory — no `server/` or `frontend/` checkout required. (`run_device_ui.sh` / `run_audio_capture.sh` look for a sibling `server/docker-compose.yml` only to detect the full monorepo and load a parent `.env`; that path is absent in an appliance-only clone and scripts still work.)

## Monorepo usage

The parent **`docker-compose.yml`** still builds `mini-pc/device-ui` and `mini-pc/audio` when you use profiles `mini-pc` / `docker-audio` there. This folder’s `docker-compose.yml` is for **appliance-only** checkouts.
