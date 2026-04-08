# MeetingBox Handover

Last updated: 2026-04-06

## Purpose

This is the single source of truth for any new agent or engineer picking up this project. It describes the current production environment, the architecture, what works, what was fixed, what is known-fragile, and what to do next. Read this file first before touching any code.

## Executive Summary

MeetingBox is an on-prem AI meeting appliance. It captures room audio, transcribes with the **OpenAI** API and summarizes with **Anthropic** Claude inside `server/web`, stores results in SQLite, and exposes a React dashboard and Kivy device UI. Legacy local Ollama / whisper.cpp worker services have been removed from the repo.

The project was originally built for a Raspberry Pi 5 with a 3.5-inch OLED touchscreen. It has now been migrated to run on a **Linux mini PC with Ubuntu Desktop and a standard monitor** connected via HDMI.

**Current production target:** Intel mini PC, Ubuntu 24.04 Desktop, standard HDMI monitor, USB headset microphone, mouse/keyboard input, Docker Compose runtime.

## Delta Update (2026-04-06)

This section is the latest validated state and overrides older details below when there is a conflict.

- **Cloud-only AI:** Removed `services/ai`, `services/transcription`, and `services/ollama`. OpenAI (Whisper) + Anthropic run in `server/web` only. Removed `POST .../summarize-local`; device-ui uses `/summarize` only. Compose no longer sets Ollama / `USE_LOCAL_LLM` env vars.
- Device pairing persistence was debugged and tightened in `device-ui/src/config.py` and `device-ui/src/main.py`.
- `get_device_auth_token()` now prefers persisted token files before `DEVICE_AUTH_TOKEN` env fallback.
- Persisted token reads now use `utf-8-sig` to tolerate BOM-prefixed token files.
- Device UI startup and pairing watchdog now refresh the backend `Authorization` header from the persisted token before calling pairing APIs.
- Remote unpair handling is deferred while on active recording / post-recording screens (`recording`, `processing`, `summary_review`, `complete`) so summary/action generation is not interrupted mid-session by a transient pairing-status 401.
- Home screen next-meeting text was cleaned up to strip redundant date-like suffixes from meeting titles.
- Web dashboard meeting cards now show both `pending_actions` and `executed_actions` counts in the lower-right area with simple icons.
- Device settings power actions were extended:
  - "Restart Device" now attempts a local host reboot path first, then backend fallback.
  - "Power Off" was added with the same local-first then backend-fallback behavior.
  - Factory reset now also triggers reboot behavior after reset completes.
- Backend device settings API in `server/web/routes/device.py` now supports `poweroff` and reports whether host reboot / poweroff initiation was attempted.
- Added `scripts/host_poweroff.sh` and corresponding compose wiring so the web service can request host shutdown when running inside Docker.
- Setup flow updates:
  - `device-ui/src/screens/room_name.py`: removed "Skip for now".
  - The "Next Step" button is only enabled when a non-empty room name is entered.
  - The entered room name is reused as the device name.
  - `device-ui/src/screens/pair_device.py`: removed the read-only room/device name display from the link page.
- Welcome screen (`device-ui/src/screens/welcome.py`) received multiple Figma-driven adjustments:
  - Hero block is vertically centered.
  - The CTA uses `Button.png` again and preserves its natural aspect ratio.
  - The CTA was recently increased from 56 px to 70 px tall because it was smaller than the reference.
  - The prior enterprise security image asset caused visible overlap because the baked text inside `shield.png` was still present; this was replaced with a text-rendered shield icon plus a separate label to eliminate overlap.
- Recent visual status of the welcome screen:
  - The overlapping enterprise-grade text issue is fixed in code.
  - The CTA is larger than before and should be closer to the Figma reference, but this still needs an on-device visual check to confirm final sizing/alignment on the actual display.

## Delta Update (2026-03-25)

This section is the latest validated state and overrides older details below when there is a conflict.

- Audio capture now writes directly to one file per meeting: /data/audio/recordings/<meeting_id>.wav while recording.
- The old VAD segment fan-out path is no longer the primary transcription input path.
- Transcription now runs once after recording_stopped (single Whisper pass on full meeting WAV), then stores transcript segments in SQLite and emits transcription_complete.
- services/transcription/Dockerfile currently downloads ggml-tiny.bin by default; compose default is WHISPER_MODEL_PATH=/app/whisper.cpp/models/ggml-tiny.bin unless overridden.
- To switch Whisper size, change model file in services/transcription/Dockerfile and WHISPER_MODEL_PATH, then rebuild only transcription: docker compose build --no-cache transcription && docker compose up -d transcription.
- Actions tab behavior: action generation and Execute in server/web/services/action_engine.py call Anthropic (_call_claude_json) when ANTHROPIC_API_KEY is set. This path is not using Ollama.
- services/ai/ai_service.py summarization prompt usage: _build_prompt is used for first/full summary; _build_update_prompt is only used when an existing local summary is incrementally updated.
- Audio capture is no longer part of the default Docker stack. The `audio` service is now behind profile `docker-audio`.
- Default audio operation on the mini PC is host-side capture via `services/audio/run_audio_capture.sh`.
- Redis is published on localhost for host audio capture: `127.0.0.1:6379:6379`.
- If `webrtcvad` fails with `ModuleNotFoundError: pkg_resources`, install `setuptools` in the exact interpreter/venv running `audio_capture.py`.
- Device onboarding now includes:
  - WiFi connected -> Create profile (Frame 13)
  - MeetingBox ready summary (Frame 14)
  - finalize setup -> Home
- Local device user profiles are stored in `device_profiles.json` with hashed passwords and `active_user_id`.
- Factory reset now removes the profile store along with settings and `.setup_complete`.
- Setup marker path resolution was updated to handle writable vs non-writable config roots more safely on Docker and native host runs.
- Known ops gotchas seen recently:
  - Compose working directory mismatch causes data to appear in different host paths.
  - SQLite readonly database occurs when data/transcripts ownership/permissions do not match UID 1000 container user.
  - If model env points to a file not present in image, rebuild transcription with --no-cache.
  - For device UI over local display, ensure DISPLAY=:0 and X access is available.

## Current Production Hardware

- **Host:** Intel mini PC
- **OS:** Ubuntu 24.04 (kernel 6.17), full desktop (GNOME/GDM3)
- **Display:** Standard HDMI monitor (1280x720 default, any resolution works)
- **Input:** Mouse + keyboard (not a touchscreen)
- **Audio:** USB headset microphone (auto-detected by the audio service)
- **Network:** Ethernet (no WiFi adapter currently installed)
- **User:** `meetingbox` (UID 1000, auto-login via GDM3)

## Architecture

### Service Stack (Docker Compose)

Most services run in Docker via a single compose file. The default production command is:

```bash
cd /home/meetingbox/meetingbox
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
```

| Container | Image | Purpose |
|---|---|---|
| `meetingbox-redis` | `redis:7-alpine` | Event bus (pub/sub) and state store |
| `meetingbox-audio` | `meetingbox-audio` | Optional Docker mic capture (`--profile docker-audio`) |
| `meetingbox-transcription` | `meetingbox-transcription` | Whisper.cpp transcription after recording stops |
| `meetingbox-ai` | `meetingbox-ai` | Summary generation via Ollama (local) or Claude (cloud) |
| `meetingbox-ollama` | `meetingbox-ollama` | Local LLM runtime (phi3:mini) |
| `meetingbox-web` | `meetingbox-web` | FastAPI backend, REST API, WebSocket relay |
| `meetingbox-nginx` | `nginx:alpine` | Reverse proxy, static frontend serving (ports 80, 8000) |
| `meetingbox-ui` | `meetingbox-device-ui` | Kivy windowed UI on the desktop (profile: `screen`) |

Default audio capture on the mini PC is host-side:

```bash
cd /home/meetingbox/meetingbox/services/audio
bash run_audio_capture.sh
```

Use Docker audio only when explicitly needed:

```bash
cd /home/meetingbox/meetingbox
docker compose --profile docker-audio up -d audio
```

### Data Flow

```
USB Mic → audio (single meeting WAV) → Redis event (recording_stopped) → transcription (Whisper.cpp once) → SQLite
                                                     ↓
                                               ai (Ollama/Claude) → SQLite
                                                     ↓
                                          web (FastAPI) → React Dashboard + Device UI
```

### Data Persistence

All data lives under `./data/` relative to the compose working directory. On the current mini PC this is `/home/meetingbox/meetingbox/data/`.

| Host Path | Container Mount | Contents |
|---|---|---|
| `data/audio/recordings/` | `/data/audio/recordings` | Final combined WAV recordings |
| `data/transcripts/` | `/data/transcripts` | `meetings.db` (SQLite), optional model files |
| `data/config/` | `/data/config` | `device_settings.json`, `.setup_complete`, `device_profiles.json` |

### Key Files

| File | What It Does |
|---|---|
| `docker-compose.yml` | Service definitions, volumes, networks, localhost Redis publish, optional `docker-audio` profile |
| `docker-compose.prod.yml` | Production overrides (adds `/dev/input` to device-ui) |
| `.env` / `.env.example` | Environment variables (API keys, model config, JWT secret) |
| `services/audio/audio_capture.py` | Mic detection, recording loop, direct WAV writer |
| `services/audio/config.yaml` | Audio sample rate, VAD aggressiveness, storage paths |
| `services/audio/run_audio_capture.sh` | Host-side audio capture runner (preferred on mini PC) |
| `services/transcription/transcription_service.py` | Post-stop Whisper runner, SQLite persistence, Redis events |
| `services/ai/ai_service.py` | Ollama/Claude summary generation |
| `server/web/main.py` | FastAPI app, WebSocket relay, Redis listener |
| `server/web/routes/meetings.py` | Recording control, meeting CRUD, summarization endpoints |
| `server/web/routes/device.py` | Device settings, WiFi, system info (used by device UI) |
| `server/web/database.py` | SQLite schema (meetings, segments, summaries, actions) |
| `services/ollama/Dockerfile` | Ollama container (no curl needed, uses `ollama list` for health) |
| `services/ollama/entrypoint.sh` | Starts Ollama, waits for ready, pulls configured model |
| `device-ui/src/main.py` | Kivy app entry point, screen manager, WebSocket listener |
| `device-ui/src/config.py` | Display defaults (1280x720), colors, fonts, backend URLs |
| `device-ui/src/hardware.py` | Generic Linux backlight sysfs discovery (no Pi paths) |
| `device-ui/Dockerfile` | Kivy/SDL2 build (Debian Bookworm compatible packages) |
| `frontend/` | React + TypeScript dashboard |
| `nginx/nginx.conf` | Reverse proxy config |
| `scripts/deploy_production.sh` | Full production setup (systemd, X11, Docker, boot) |
| `scripts/install_native_minipc.sh` | Non-Docker native install path (systemd services) |
| `scripts/hotspot.sh` | WiFi hotspot manager (auto-detects interface, needs adapter) |

### Authentication Model

- **Web dashboard:** JWT-based authentication (login/register/onboarding)
- **Device UI:** No JWT. Uses optional-auth backend routes (`get_optional_user`). This is intentional and must be preserved.

## What Was Changed During the Pi-to-MiniPC Migration

### Removed

- `scripts/setup_display.sh` — Pi-only Xorg config that forced 480x320 resolution
- Pi-specific backlight sysfs paths (`rpi_backlight`, `10-0045`) from `hardware.py`
- Pi rainbow splash disable from `deploy_production.sh`
- `libegl1-mesa-dev` and `libmtdev1` from device-ui Dockerfile (renamed/removed in Debian Bookworm)
- `curl` installation from Ollama Dockerfile (broken packages in `ollama/ollama:latest`)
- Default always-on Docker audio capture
- Xauthority file mount from device-ui (fragile, unnecessary with `xhost +local:`)
- `version: "3.9"` from docker-compose.yml (obsolete in modern Docker Compose)

### Changed

| File | Change |
|---|---|
| `device-ui/src/config.py` | Default display: 480x320 → 1280x720 |
| `device-ui/src/main.py` | Default display: 480x320 → 1280x720; cursor visible in windowed mode |
| `device-ui/src/hardware.py` | Generic Linux backlight discovery instead of hardcoded Pi paths |
| `device-ui/Dockerfile` | `libmtdev1` → `libmtdev1t64`; removed `libegl1-mesa-dev`, `libinput-dev`, GStreamer devs |
| `services/ollama/Dockerfile` | Removed `apt-get install curl`; health checks use `ollama list` |
| `services/ollama/entrypoint.sh` | Readiness check: `curl` → `ollama list` |
| `services/transcription/transcription_service.py` | Default Whisper path: `/opt/meetingbox/runtime/whisper.cpp` → `/app/whisper.cpp` |
| `docker-compose.yml` | Redis published on `127.0.0.1:6379`; `audio` moved behind `docker-audio` profile; `user: "1000:1000"` on audio/transcription/ai; Ollama healthcheck uses `ollama list`; device-ui gets `/dev/dri`, `LIBGL_ALWAYS_SOFTWARE=1`, `FULLSCREEN=0`; Whisper model/threads configurable via env |
| `scripts/deploy_production.sh` | Generalized from Pi to Linux; removed Pi boot config; disabled onboarding; disabled `meetingbox-x.service` |
| `scripts/hotspot.sh` | Auto-detects WiFi interface name instead of hardcoding `wlan0` |
| `scripts/install_native_minipc.sh` | Default display: 480x320 → 1280x720 |

### Added

- GDM3 auto-login config (`/etc/gdm3/custom.conf`)
- Desktop autostart entry (`~/.config/autostart/meetingbox-ui.desktop`) that runs `xhost +local:` and restarts device-ui on login
- `.setup_complete` marker created by deploy script to bypass onboarding
- Profile creation + final ready screens in device onboarding
- `device_profiles.json` local profile storage
- `WHISPER_MODEL_PATH` and `WHISPER_THREADS` env vars in compose for runtime tuning

## Current Boot Sequence (After Reboot)

1. Ubuntu boots → `graphical.target` → GDM3 starts
2. GDM3 auto-logs in as `meetingbox` (configured in `/etc/gdm3/custom.conf`)
3. Ubuntu desktop appears on monitor
4. `meetingbox.service` (systemd) starts Docker Compose for the default stack (audio is not started unless `docker-audio` profile is requested)
5. Autostart desktop entry (`meetingbox-ui.desktop`) runs `xhost +local:` then restarts `meetingbox-ui`
6. MeetingBox device UI window appears on desktop
7. If using host audio, run `services/audio/run_audio_capture.sh`
8. Web dashboard available at `http://localhost` or `http://meetingbox.local`

## Systemd Services

| Service | Status | Purpose |
|---|---|---|
| `meetingbox.service` | **enabled** | Starts Docker Compose on boot |
| `meetingbox-x.service` | **masked** | Old kiosk X server (not needed with GDM3 desktop) |
| `meetingbox-onboard.service` | **masked** | WiFi hotspot onboarding (no WiFi adapter currently) |
| `gdm3.service` | **enabled** | Ubuntu desktop display manager |
| `avahi-daemon` | **enabled** | mDNS for `meetingbox.local` |
| `redis-server` (native) | **disabled** | Conflicts with Docker Redis on port 6379 |

## Known Issues and Limitations

### 1. Whisper transcription is slow with ggml-medium

On mini PC CPU, `ggml-medium.bin` takes ~50-60 seconds per audio segment. A 15-second recording produces ~10 segments, so transcription can take 8-12 minutes. The device UI shows a simulated progress bar that caps at 68% while waiting for backend events, making it look stuck.

**Mitigation:** Download a faster model and set it in `.env`:

```bash
# Download tiny.en (English only, ~75MB, very fast)
wget -O data/transcripts/models/ggml-tiny.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin

# Or base multilingual (~140MB, good balance)
wget -O data/transcripts/models/ggml-base.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
```

Add to `.env`:
```
WHISPER_MODEL_PATH=/data/transcripts/models/ggml-tiny.en.bin
WHISPER_THREADS=8
```

Then recreate: `docker compose ... up -d --force-recreate transcription`

### 2. Data permissions require UID 1000 alignment

The `audio`, `transcription`, and `ai` containers run as `user: "1000:1000"` to match the host `meetingbox` user. If the `data/` directory ownership changes (e.g., after `sudo` operations or `rsync`), containers will crash with `PermissionError` or `sqlite3.OperationalError: attempt to write a readonly database`.

**Fix:**
```bash
sudo chown -R meetingbox:meetingbox /home/meetingbox/meetingbox/data
sudo find /home/meetingbox/meetingbox/data -type d -exec chmod 775 {} \;
sudo find /home/meetingbox/meetingbox/data -type f -exec chmod 664 {} \;
```

### 3. Device UI requires `xhost +local:` to draw on the desktop

The `meetingbox-ui` container connects to the host X11 display via the X socket (`/tmp/.X11-unix`). GDM3/GNOME does not allow unauthenticated X connections by default. The autostart desktop entry runs `xhost +local:` to open this. If the UI doesn't appear after reboot, run manually:

```bash
DISPLAY=:0 xhost +local:
docker restart meetingbox-ui
```

### 4. Docker Compose must always be run from the same directory

Relative volume mounts (`./data/audio`) resolve to the current working directory. If you run compose from `/opt/meetingbox` sometimes and `/home/meetingbox/meetingbox` other times, each location gets its own `data/` directory and containers can't see each other's files.

**Current standard location:** `/home/meetingbox/meetingbox/`

### 5. Ollama model download on first start

The first time `meetingbox-ollama` starts, it downloads `phi3:mini` (~2.3GB). Until that finishes and the healthcheck passes, `meetingbox-web` will not start (it depends on `ollama: condition: service_healthy`). This can take 10-15 minutes on first boot.

### 6. WiFi adapter not yet installed

The mini PC currently has no WiFi adapter. The hotspot onboarding flow (`scripts/hotspot.sh`) is disabled and the `.setup_complete` marker bypasses the onboarding screen. When a USB WiFi adapter is added:

1. `hotspot.sh` will auto-detect the interface (no `wlan0` hardcoding)
2. Remove the marker: `rm /home/meetingbox/meetingbox/data/config/.setup_complete`
3. Unmask the service: `sudo systemctl unmask meetingbox-onboard.service && sudo systemctl enable meetingbox-onboard.service`
4. Restart device-ui: `docker restart meetingbox-ui`

### 7. Some frontend API client methods are not yet surfaced in UI

- `meetings.uploadAudio` — API exists but no dashboard UI
- `meetings.emailSummary` — API exists but no dashboard UI
- `actions.update` — API exists but no dashboard UI

These are not blockers.

### 8. Update check/install backend endpoints are placeholders

The device UI has update screens but the backend endpoints don't do real update logic.

### 9. Empty/silent audio — wrong mic selected (segments created but no speech)

**Symptom:** Segments are created (1, 2, 3...) during recording, but when you play the audio file it's silent. Transcription outputs "blank" and summarization has no content. Progress may stick at 68%.

**Root cause:** Segments are created by time (~15 s) and VAD (noise), not by actual speech. The audio container may be capturing the wrong ALSA device (HDMI, built-in mic, or default) instead of your USB mic. Inside Docker, ALSA device order can differ from the host.

**Fix:**
1. List devices: `./scripts/list_audio_devices.sh` (or `docker compose exec audio python -c "import pyaudio; p=pyaudio.PyAudio(); [print(i,p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count()) if p.get_device_info_by_index(i).get('maxInputChannels')>0]"`)
2. Add to `.env` to force your USB mic:
   - By index: `AUDIO_INPUT_DEVICE_INDEX=1` (use the index from the list)
   - By name: `AUDIO_INPUT_DEVICE_NAME=USB` (substring match, e.g. "Generic USB PnP Sound Device")
3. Recreate the audio container: `docker compose ... up -d --force-recreate audio`
4. Check mic volume and mute switch on the device.

If the audio service logs `SILENT AUDIO DETECTED (peak=…)`, the wrong device is being used.

## How to Operate

### Start default stack
```bash
cd /home/meetingbox/meetingbox
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
```

### Start host audio capture
```bash
cd /home/meetingbox/meetingbox/services/audio
bash run_audio_capture.sh
```

### Start Docker audio capture instead
```bash
cd /home/meetingbox/meetingbox
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen --profile docker-audio up -d audio
```

### Stop everything
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen down
```

### View logs
```bash
# All services
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen logs -f

# Specific service
docker logs meetingbox-audio -f
docker logs meetingbox-transcription -f
docker logs meetingbox-ai -f
docker logs meetingbox-ui -f
```

If using host audio instead of Docker audio, watch the host process terminal or redirect logs from `services/audio/run_audio_capture.sh`.

### Rebuild after code changes
```bash
# Single service (fast)
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen build device-ui
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d --no-deps device-ui

# All services
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen build --no-cache
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
```

### Reset stuck recording state
```bash
docker exec meetingbox-redis redis-cli set recording_state idle
docker exec meetingbox-redis redis-cli del current_meeting_id
```

### Fix data permissions
```bash
sudo chown -R meetingbox:meetingbox /home/meetingbox/meetingbox/data
sudo find /home/meetingbox/meetingbox/data -type d -exec chmod 775 {} \;
sudo find /home/meetingbox/meetingbox/data -type f -exec chmod 664 {} \;
```

### Access the web dashboard
- Local: `http://localhost` or `http://localhost:8000`
- Network: `http://meetingbox.local` or `http://192.168.1.17`

### SSH access
```
ssh meetingbox@192.168.1.17
```

## .env File

Must exist at the compose working directory (`/home/meetingbox/meetingbox/.env`). Minimum required:

```env
JWT_SECRET_KEY=<output of: openssl rand -hex 32>
```

Optional but recommended:

```env
ANTHROPIC_API_KEY=<your key if using cloud summarization>
WHISPER_MODEL_PATH=/data/transcripts/models/ggml-tiny.en.bin
WHISPER_THREADS=8
```

See `.env.example` for all available variables.

## Recommended Reading Order for New Agents

1. **This file** — current state and context
2. `docker-compose.yml` — service definitions and runtime config
3. `services/audio/audio_capture.py` — mic detection and recording pipeline
4. `services/transcription/transcription_service.py` — Whisper integration and event handling
5. `services/ai/ai_service.py` — summary generation logic
6. `server/web/routes/meetings.py` — recording control and meeting API
7. `device-ui/src/main.py` — Kivy app entry point and event handling
8. `device-ui/src/config.py` — display and UI configuration
9. `frontend/FRONTEND_REFERENCE.md` — dashboard feature inventory
10. `LEARNINGS.md` — historical Pi debugging lessons (still useful context)

## Suggested Next Steps

### Priority 1: Speed up transcription
Switch to `ggml-tiny.en.bin` or `ggml-base.bin` via `.env` for practical real-time use.

### Priority 2: Improve the processing screen UX
The simulated progress bar (caps at 68%) is misleading. Options:
- Show "Still transcribing on device..." message after 68%
- Add backend failure event handling so UI shows error instead of hanging
- Send real progress events from the transcription service

### Priority 3: Add USB WiFi adapter and re-enable onboarding
When a WiFi adapter is available, the hotspot flow can be tested. `hotspot.sh` already auto-detects the interface name.

### Priority 4: Consolidate the working directory
Currently compose runs from `/home/meetingbox/meetingbox/`. The production deploy script copies to `/opt/meetingbox/`. Pick one and stick with it. Recommendation: use `/home/meetingbox/meetingbox/` for everything since that's where the git repo lives.

### Priority 5: Wire up remaining frontend features
`uploadAudio`, `emailSummary`, and `actions.update` have backend endpoints but no dashboard UI yet.
