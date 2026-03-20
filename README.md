MeetingBox Core Software MVP
============================

This repository contains the core software stack for **MeetingBox**, a conference-room AI appliance that:

- Captures in-room audio
- Transcribes meetings with Whisper
- Generates AI summaries with Claude
- Serves a local web dashboard at `meetingbox.local`

This MVP focuses on a **single-device pipeline**, with optional Gmail/Google Calendar integrations for action delivery:

1. Start/stop a meeting recording
2. Capture audio from a USB mic array
3. Transcribe audio to text on-device
4. Generate an AI summary + action items
5. Store everything in SQLite
6. View meetings and summaries in a React dashboard

## Repository layout

- `services/audio` – Audio capture & VAD-based segmentation (PyAudio + webrtcvad)
- `services/transcription` – Whisper.cpp-based transcription service writing into SQLite
- `services/ai` – Claude integration for summaries + analysis
- `services/web` – FastAPI backend + WebSocket relay for events
- `frontend` – React + TypeScript + Tailwind dashboard
- `data` – Local data volume (audio, transcripts, exports, SQLite DB)
- `logs` – Service logs (optional, per-service)
- `scripts` – Helper scripts for setup and deployment
- `docker-compose.yml` – Local development / on-device orchestration
- `.env.example` – Example environment variables (API keys, config)

## Getting started (development)

1. Install Docker and Docker Compose.
2. Copy `.env.example` to `.env` and fill in secrets (e.g. `ANTHROPIC_API_KEY`).
3. Run:

```bash
docker compose up --build
```

4. **Build the frontend** (so the web container can serve it):

```bash
cd frontend && npm install && npm run build && cd ..
```

5. Open `http://localhost:8000` (or `http://meetingbox.local` on-device) to access the dashboard.

## Start / Stop meeting and test WAV

- **From the dashboard**: use "Start meeting" to begin recording (audio service); "Stop & process" to stop and run transcription + summary.
- **Without a mic**: use the test WAV ingest to run the full pipeline (transcription → summary) from a file:

```bash
pip install requests
python scripts/ingest_test_wav.py path/to/your.wav
# Optional: --base http://localhost:8000  (default)
```

Then open the returned `session_id` in the dashboard (e.g. `http://localhost:8000/meeting/20250211_123456`) once processing has finished.

## Linux / On-device Deployment

See **[DEPLOY_LINUX.md](DEPLOY_LINUX.md)** for the full deployment guide covering:

- VirtualBox Ubuntu VM setup (or any Linux host)
- USB microphone passthrough and ALSA configuration
- Docker stack deployment with real mic access
- End-to-end validation test plan
- Performance benchmarking for Pi 5 vs mini PC decisions

Quick start on any Ubuntu 24.04 host:

```bash
cd /opt/meetingbox
sudo ./scripts/setup_vm.sh   # installs Docker, Node.js, ALSA utils, mDNS
cp .env.example .env && nano .env   # set ANTHROPIC_API_KEY
cd frontend && npm install && npm run build && cd ..
docker compose up --build -d
```

> **Note:** The `docker-compose.yml` includes `devices: ["/dev/snd"]` and `group_add: [audio]` on the audio service for Linux mic access. Comment these out if running on Windows Docker Desktop.


