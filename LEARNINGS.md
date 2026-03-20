# MeetingBox — Engineering Learnings & Deployment Notes

This file captures every non-obvious fix, root cause, and hard-won lesson from
building, debugging, and deploying MeetingBox on a Raspberry Pi 5 with an OLED
touchscreen. Reference it before making changes to the device UI, boot flow, or
Docker stack.

---

## Table of Contents

1. [Kivy Window Rendering (Position / Fullscreen)](#1-kivy-window-rendering-position--fullscreen)
2. [Python Import Path in Docker (ModuleNotFoundError)](#2-python-import-path-in-docker-modulenotfounderror)
3. [sounddevice / libportaudio in Docker](#3-sounddevice--libportaudio-in-docker)
4. [config.py Directory Creation at Import Time](#4-configpy-directory-creation-at-import-time)
5. [Docker Compose Profiles Cannot Be Cleared in Overrides](#5-docker-compose-profiles-cannot-be-cleared-in-overrides)
6. [Xorg on Debian Trixie/Bookworm — Virtual Console Permissions](#6-xorg-on-debian-trixiebookworm--virtual-console-permissions)
7. [Xorg — AddScreen/ScreenInit Failed (Forced Monitor Config)](#7-xorg--addscreenscreeninit-failed-forced-monitor-config)
8. [X11 Autostart — bashrc vs profile vs systemd](#8-x11-autostart--bashrc-vs-profile-vs-systemd)
9. [tty Group Required for Startx](#9-tty-group-required-for-startx)
10. [npm on Debian arm64/Trixie — Broken Package Deps](#10-npm-on-debian-arm64trixie--broken-package-deps)
11. [Docker device-ui: /dev/dri Does Not Exist](#11-docker-device-ui-devdri-does-not-exist)
12. [Onboarding Service — Setup Marker Path Mismatch](#12-onboarding-service--setup-marker-path-mismatch)
13. [Onboarding — Skip When WiFi Already Connected](#13-onboarding--skip-when-wifi-already-connected)
14. [Privacy Mode Toggle Not Reflecting on Home Screen](#14-privacy-mode-toggle-not-reflecting-on-home-screen)
15. [Whisper Tiny Model Cannot Transcribe Non-English](#15-whisper-tiny-model-cannot-transcribe-non-english)
16. [WebSocket Event Data Extraction](#16-websocket-event-data-extraction)
17. [Auth — Device UI Has No JWT](#17-auth--device-ui-has-no-jwt)

---

## 1. Kivy Window Rendering (Position / Fullscreen)

**Symptom:** UI rendered in top-left or bottom-left corner instead of filling the
screen, even with `Window.fullscreen = 'auto'` and `Window.size = (480, 320)`.

**Root cause:** In Kivy, `from kivy.core.window import Window` instantiates the
window immediately at import time. Any `Window.size` or `Window.fullscreen`
calls made later (e.g. inside `build()`) are applied after the window already
exists, so Kivy only partially adjusts the layout — causing content to render
in a corner.

**Fix:** Set ALL graphics parameters via `Config.set()` **before** importing
`Window`. Order in `main.py` must be:

```python
from kivy.config import Config

Config.set('graphics', 'position', 'custom')
Config.set('graphics', 'left', '0')
Config.set('graphics', 'top', '0')
Config.set('graphics', 'width', '480')
Config.set('graphics', 'height', '320')
Config.set('graphics', 'borderless', '1')   # in fullscreen/kiosk mode
Config.set('graphics', 'fullscreen', 'auto') # in fullscreen/kiosk mode

from kivy.core.window import Window  # must come AFTER all Config.set calls
```

Do **not** call `Window.size`, `Window.fullscreen`, or `Window.pos` in
`build()` — they will partially work at best and cause corner-rendering at worst.

**Files:** `device-ui/src/main.py`

---

## 2. Python Import Path in Docker (ModuleNotFoundError)

**Symptom:** `ModuleNotFoundError: No module named 'screens'` when running
`python src/main.py` from `/app`.

**Root cause:** The Dockerfile `CMD` was `python src/main.py` with `WORKDIR /app`.
Python adds the script directory (or cwd) to `sys.path`. When run as
`python src/main.py`, the script dir is `/app/src` only in some shells — in
others it's the cwd (`/app`), so `screens`, `config`, etc. under `/app/src/`
are not found.

**Fix (two-part):**

1. Set `PYTHONPATH=/app/src` in the Dockerfile so it's always on the path
   regardless of how Python is invoked:
   ```dockerfile
   ENV PYTHONPATH=/app/src
   ```

2. Change `WORKDIR` to `/app/src` and run `main.py` directly:
   ```dockerfile
   WORKDIR /app/src
   CMD ["python", "main.py"]
   ```

3. Also add a defensive path insert at the top of `main.py`:
   ```python
   _src_dir = Path(__file__).resolve().parent
   if str(_src_dir) not in sys.path:
       sys.path.insert(0, str(_src_dir))
   ```

**Files:** `device-ui/Dockerfile`, `device-ui/src/main.py`

---

## 3. sounddevice / libportaudio in Docker

**Symptom:** Device UI crashes at startup with `OSError: cannot open shared
object file: libportaudio.so.2`. No Python traceback visible.

**Root cause:** `pip install sounddevice` installs the Python bindings only.
The C extension loads `libportaudio.so.2` at runtime. If the system library is
not installed, the loader raises `OSError` — not `ImportError` — which the
original `except ImportError` clause does not catch, so the crash is
unhandled and the process exits silently at import time.

**Fix:**

1. Install the system library in the Dockerfile:
   ```dockerfile
   RUN apt-get install -y libportaudio2
   ```

2. Broaden the exception catch in `mic_test.py`:
   ```python
   try:
       import sounddevice as sd
       _HAS_AUDIO = True
   except (ImportError, OSError) as e:
       _HAS_AUDIO = False
       logger.warning("sounddevice unavailable: %s", e)
   ```

**Files:** `device-ui/Dockerfile`, `device-ui/src/screens/mic_test.py`

---

## 4. config.py Directory Creation at Import Time

**Symptom:** App crashes at startup with `PermissionError` before Kivy even
initialises.

**Root cause:** `config.py` calls `ASSETS_DIR.mkdir(exist_ok=True)` at module
level (import time). If the user running the process cannot create those
directories, Python raises `PermissionError` and the app exits with no window.

**Fix:** Wrap directory creation in a try/except:
```python
try:
    ASSETS_DIR.mkdir(exist_ok=True)
    FONTS_DIR.mkdir(exist_ok=True)
    ICONS_DIR.mkdir(exist_ok=True)
except OSError as e:
    logger.warning("Could not create asset dirs: %s", e)
```

**Files:** `device-ui/src/config.py`

---

## 5. Docker Compose Profiles Cannot Be Cleared in Overrides

**Symptom:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`
starts all services except `device-ui`, which is silently skipped.

**Root cause:** In Docker Compose, override files **cannot remove** a profile.
Setting `profiles: []` in an override does not clear the `profiles: [screen]`
from the base file — the union of both values is used, so the service still
requires `--profile screen` to start.

**Fix:** Always pass `--profile screen` explicitly in every compose command:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile screen up -d
```
The prod override file (`docker-compose.prod.yml`) is only needed for device
passthrough (`/dev/input`) — it cannot remove profiles.

**Files:** `docker-compose.prod.yml`, `scripts/deploy_production.sh`,
`systemd/meetingbox.service`

---

## 6. Xorg on Debian Trixie/Bookworm — Virtual Console Permissions

**Symptom:** `startx` fails with:
```
parse_vt_settings: Cannot open /dev/tty0 (Permission denied)
```
or:
```
xf86OpenConsole: Cannot open virtual console 1 (Permission denied)
```

**Root cause:** Modern Debian defaults to rootless Xorg. The user running
`startx` must belong to the `tty` group AND the Xwrapper must be configured
to allow non-root users.

**Fix:**

1. Add user to `tty` group:
   ```bash
   sudo usermod -aG tty meetingbox
   ```
   (Also add: `docker,video,input,audio,tty`)

2. Configure Xwrapper:
   ```bash
   cat > /etc/X11/Xwrapper.config << 'EOF'
   allowed_users=anybody
   needs_root_rights=yes
   EOF
   ```

3. Make the wrapper setuid-root if present:
   ```bash
   sudo chown root:root /usr/lib/xorg/Xorg.wrap
   sudo chmod u+s /usr/lib/xorg/Xorg.wrap
   ```

**Files:** `scripts/deploy_production.sh` (applies all three automatically)

---

## 7. Xorg — AddScreen/ScreenInit Failed (Forced Monitor Config)

**Symptom:** X server crashes in a restart loop with:
```
Fatal server error: AddScreen/ScreenInit failed for driver 0
```

**Root cause:** The custom Xorg config we wrote (`/etc/X11/xorg.conf.d/99-meetingbox-display.conf`)
forced a `Monitor` + `Screen` section with `PreferredMode "480x320"`. On
modern Pi OS with KMS/DRM drivers, this prevents Xorg from auto-detecting the
display and causes the screen init to fail.

**Fix:** Do NOT write custom Monitor/Screen sections on Raspberry Pi.
Let Xorg and the kernel/KMS stack auto-detect the display:
```bash
sudo rm -f /etc/X11/xorg.conf.d/99-meetingbox-display.conf
```

The deploy script now explicitly deletes this file if it exists.

**Files:** `scripts/deploy_production.sh`

---

## 8. X11 Autostart — bashrc vs profile vs systemd

**Symptom:** Device boots to a shell prompt on tty1 instead of launching X.
No `meetingbox-startx.log` created. `.bashrc` startx block not executing.

**Root cause (in order tried):**

1. `.bashrc` is only sourced for **interactive non-login** shells. Auto-login
   via `agetty --autologin` spawns a **login shell**, which sources `.profile`
   (or `.bash_profile`) instead.

2. If `.bash_profile` exists and does not source `.profile`, the `.profile`
   additions are also never run.

3. Shell-based autostart is fragile (order-sensitive, shell type-sensitive,
   raced with Docker startup).

**Final fix:** Use a dedicated systemd service instead of any shell file:
```ini
[Unit]
Description=MeetingBox X server on tty1
After=systemd-user-sessions.service
Conflicts=getty@tty1.service

[Service]
User=meetingbox
ExecStart=/usr/bin/xinit /home/meetingbox/.xinitrc -- :0 -nocursor vt1 -keeptty
Restart=always
RestartSec=2
```

This removes all shell-startup dependency and runs X as a proper supervised
service with restart-on-failure.

**Files:** `scripts/deploy_production.sh` (writes `meetingbox-x.service`)

---

## 9. tty Group Required for Startx

**Symptom:** `startx` fails with permission denied on `/dev/tty0`.

**Root cause:** Even with `Xwrapper.config` set to `needs_root_rights=yes`,
the user must be in the `tty` group to open virtual console devices.

**Fix:** `usermod -aG tty meetingbox`

The deploy script sets: `usermod -aG docker,video,input,audio,tty "$ACTUAL_USER"`

Note: Group changes only take effect on next **login** — not on the current
session. A reboot is required after adding the user to a new group.

**Files:** `scripts/deploy_production.sh`

---

## 10. npm on Debian arm64/Trixie — Broken Package Deps

**Symptom:** `apt-get install npm` fails with:
```
Unable to correct problems, you have held broken packages.
node-css-loader depends on webpack → node-jest-worker → node-types-node → nodejs (not selected)
```

**Root cause:** The Debian `npm` package on arm64/Trixie has a dependency chain
that conflicts with itself. The `nodejs` package it depends on is not selected
for installation, making the whole chain unresolvable.

**Fix:** Do NOT use Debian's `npm` package. Install Node.js from
[nodesource](https://github.com/nodesource/distributions) which bundles a
working npm:
```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
```

**Files:** `scripts/deploy_production.sh`

---

## 11. Docker device-ui: /dev/dri Does Not Exist

**Symptom:** `docker compose up` fails with:
```
error gathering device information while adding custom device "/dev/dri": no such file or directory
```

**Root cause:** The production compose override passed `/dev/dri:/dev/dri` for
GPU acceleration. On Pi OS images that don't have the DRM/KMS device node
(or where it lives at a different path), Docker fails hard when a configured
`devices:` entry doesn't exist on the host.

**Fix:** Remove `/dev/dri` from `docker-compose.prod.yml`. It is optional for
GPU acceleration and not required for SDL2/X11 rendering. Only `/dev/input`
(touchscreen) is needed:

```yaml
services:
  device-ui:
    devices:
      - /dev/input:/dev/input
```

**Files:** `docker-compose.prod.yml`

---

## 12. Onboarding Service — Setup Marker Path Mismatch

**Symptom:** Device shows onboarding hotspot on every boot even after setup was
completed. The `meetingbox-onboard.service` `ConditionPathExists` was never
satisfied.

**Root cause:** The service used `ConditionPathExists=!/data/config/.setup_complete`.
But the setup marker is written by `onboard_server.py` to:
`<project_root>/data/config/.setup_complete` = `/opt/meetingbox/data/config/.setup_complete`.
The `/data/config/` path only exists inside the Docker containers (bind mount),
not on the host where systemd runs.

**Fix:** Correct the path in both the service unit and `needs_setup()`:
- Service: `ConditionPathExists=!/opt/meetingbox/data/config/.setup_complete`
- `main.py` `needs_setup()`: check all three candidates:
  ```python
  ['/data/config/.setup_complete',
   '/opt/meetingbox/data/config/.setup_complete',
   '/opt/meetingbox/.setup_complete']
  ```

**Files:** `systemd/meetingbox-onboard.service`, `device-ui/src/main.py`,
`scripts/deploy_production.sh`

---

## 13. Onboarding — Skip When WiFi Already Connected

**Symptom:** Device goes through the onboarding hotspot flow on every boot
even when it's already connected to a known WiFi network (e.g. office network
it was set up on).

**Root cause:** The onboarding service only checked for the `.setup_complete`
marker. If the marker was absent (e.g. fresh clone to same Pi, or marker
accidentally deleted), it would start the hotspot even with active WiFi.

**Fix:** Add an `ExecCondition` to the systemd service that skips onboarding
if WiFi is already connected:
```ini
ExecCondition=/bin/bash -c '! nmcli -t -f TYPE,STATE dev | grep -q "^wifi:connected$"'
```

Also in `dev_restart.sh`: if WiFi is connected and `--fresh` was NOT passed,
auto-restore the setup marker to skip onboarding.

**Files:** `systemd/meetingbox-onboard.service`, `scripts/dev_restart.sh`,
`scripts/deploy_production.sh`

---

## 14. Privacy Mode Toggle Not Reflecting on Home Screen

**Symptom:** Turning Privacy Mode OFF in Settings still showed
"START RECORDING ⏺ (Local Only)" on the Home screen.

**Root cause:** The `_apply_privacy_mode()` method's `else` branch (privacy off)
only reset the status bar text. It did NOT reset the Start button text or the
last-meeting label, so both stayed in their "privacy on" state.

**Fix:** In `home.py` `_apply_privacy_mode()`, restore all three elements when
privacy is off:
```python
else:
    self.status_bar.status_text = 'READY'
    self.start_btn.text = 'START RECORDING\n⏺'
    self.last_meeting_label.text = 'No meetings yet — Press start to begin'
```

`on_enter()` on Home already calls `_apply_privacy_mode()`, so returning from
Settings will trigger an update.

**Files:** `device-ui/src/screens/home.py`

---

## 15. Whisper Tiny Model Cannot Transcribe Non-English

**Symptom:** Whisper outputs "brief meeting in a foreign language" or similar
description instead of actual Telugu text.

**Root cause:** The `ggml-tiny.bin` model (39M params) has very limited
multilingual capability. For languages like Telugu, it detects the language
but cannot produce actual transcription text — it falls back to describing
the audio.

**Fix:** Use `ggml-medium.bin` (769M params) for **final transcription** after
recording stops. Keep `ggml-tiny.bin` for **live per-segment preview** during
recording (speed matters more than accuracy for live captions).

```
Final transcription → ggml-medium.bin  (accurate, multilingual, ~1.5GB)
Live preview        → ggml-tiny.bin    (fast, approximate, ~75MB)
```

**Files:** `services/transcription/Dockerfile`,
`services/transcription/transcription_service.py`

---

## 16. WebSocket Event Data Extraction

**Symptom:** `meeting_id` was always `None` after `transcription_complete`
events, causing auto-summarize to never trigger.

**Root cause:** The device-ui WebSocket handler extracted data with:
```python
data = event.get('data', {})
```
But Redis events from the `events` channel have all fields at the **top level**
of the JSON — there is no nested `data` key. So `data` was always `{}`.

**Fix:**
```python
data = event.get('data') or event
```
If no `data` key exists, use the event itself as the data object.

**Files:** `device-ui/src/main.py`

---

## 17. Auth — Device UI Has No JWT

**Principle:** The device-ui sends NO authentication token. Every backend route
it calls must use `get_optional_user` (returns `None` on missing token) and NOT
`get_current_user` (raises 401 on missing token).

**Routes that must use `get_optional_user`:**
- All of `services/web/routes/device.py` (settings, WiFi, updates, integrations)
- Meeting list, detail, delete, summarize in `meetings.py`
- Action list, update, approve, dismiss, execute in `actions.py`
- Recording start/stop/pause/resume in `meetings.py` (already correct)

Routes only called by the web dashboard (user-authenticated) can still use
`get_current_user`.

---

## Boot Flow Summary (Production)

```
Power on
  → kernel boots silently (quiet splash loglevel=0 logo.nologo)
  → systemd starts meetingbox-x.service (xinit → X server on :0)
  → systemd starts meetingbox.service (docker compose --profile screen up -d)
  → All 8 containers start: redis, audio, transcription, ai, ollama, web, nginx, device-ui
  → device-ui container connects to X11 via /tmp/.X11-unix
  → Kivy opens fullscreen (Config.set before Window import)
  → Splash screen → Home (or onboarding if first boot on unknown network)
```

**Critical:** `meetingbox-x.service` and `meetingbox.service` start in parallel.
`device-ui` has `restart: unless-stopped` so if X11 isn't ready yet, the
container restarts until it can connect. This is expected behavior.

---

## Key Files for Boot / Deployment

| File | Purpose |
|---|---|
| `scripts/deploy_production.sh` | Single-shot production installer for Pi |
| `docker-compose.prod.yml` | Production overrides: `/dev/input` passthrough only |
| `systemd/meetingbox.service` | Starts Docker Compose on boot (rewritten by deploy script) |
| `systemd/meetingbox-onboard.service` | First-boot hotspot + WiFi setup (rewritten by deploy script) |
| `scripts/meetingbox-x.service` | X server service (written by deploy script, no repo file) |
| `scripts/hotspot.sh` | NetworkManager-based WiFi AP manager |
| `scripts/onboard_server.py` | Lightweight HTTP server for first-boot WiFi config portal |
| `scripts/dev_restart.sh` | Dev cycle: pull, build, start stack (supports `--fresh`) |
