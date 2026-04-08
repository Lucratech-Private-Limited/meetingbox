# OLED Device UI -- Diagnostic Reference

## Overview

The MeetingBox device UI is a **Kivy 2.3+** touchscreen application running on a Raspberry Pi 5 with a 3.5" OLED display (480x320, landscape). It communicates with the FastAPI backend (`server/web/`) over HTTP REST and WebSocket.

## Architecture

```
Device UI (Kivy)  --HTTP-->  FastAPI backend (port 8000)  --Redis-->  Audio/Transcription/AI services
                  <--WS---   WebSocket relay (/ws)        <--Redis--
```

- **Rendering**: Kivy + SDL2 over X11 (not low-level I2C/SPI OLED driver)
- **Backend URL**: `http://web:8000` (Docker) or `http://localhost:8000` (dev)
- **WebSocket URL**: `ws://web:8000/ws`
- **Auth**: Device UI sends NO JWT token. All backend routes it calls must use `get_optional_user`.

## Key Files

### Device UI (`device-ui/src/`)

| File | Purpose |
|---|---|
| `main.py` | Kivy app entry, screen manager, WebSocket listener, recording actions |
| `config.py` | Colors, fonts, sizes, backend URLs, display settings |
| `api_client.py` | Async HTTP client (`httpx`) for backend API |
| `async_helper.py` | Bridges Kivy main thread with asyncio event loop |
| `mock_backend.py` | Mock backend for dev/testing |
| `screens/base_screen.py` | Base class: dark bg, navigation, footer |
| `screens/home.py` | Ready state, START RECORDING button |
| `screens/recording.py` | Timer, waveform, captions, PAUSE/STOP buttons |
| `screens/processing.py` | Progress bar during transcription/summary |
| `screens/complete.py` | Meeting saved confirmation, auto-return to home |
| `screens/summary_review.py` | Summary + Actions tabs, Execute Selected button |
| `screens/settings.py` | Scrollable settings list (privacy, brightness, restart, etc.) |
| `screens/picker_base.py` | Base for radio-button pickers |
| `screens/brightness_picker.py` | Screen brightness picker |
| `screens/timeout_picker.py` | Screen timeout picker |
| `screens/auto_delete_picker.py` | Auto-delete old meetings picker |
| `screens/wifi_setup.py` | First-boot WiFi setup (QR code + instructions) |
| `screens/wifi.py` | WiFi network list |
| `screens/meetings.py` | Past meetings list |
| `screens/meeting_detail.py` | Single meeting detail |
| `screens/mic_test.py` | Microphone test waveform |
| `screens/update_check.py` | Check for firmware updates |
| `screens/update_install.py` | Install firmware update |
| `screens/error.py` | Error display with recovery action |
| `components/button.py` | PrimaryButton, SecondaryButton, DangerButton |
| `components/status_bar.py` | Top bar with status dot, device name, gear icon |
| `components/settings_item.py` | Settings row (arrow / toggle / info modes) |
| `components/modal_dialog.py` | Centered modal dialog with confirm/cancel |
| `components/toggle_switch.py` | iOS-style toggle switch |

### Backend Routes (`server/web/routes/`)

| File | Prefix | Purpose |
|---|---|---|
| `meetings.py` | `/api/meetings` | Recording control, meeting CRUD, summarization |
| `device.py` | `/api/device` | Settings, WiFi, updates, integrations |
| `system.py` | `/api/system` | System status, device-info (no auth) |
| `actions.py` | `/api` | Action items CRUD + execution |
| `auth.py` | `/api/auth` | User registration, login, JWT |
| `integrations.py` | `/api` | Google OAuth device flow |

### Other Key Files

| File | Purpose |
|---|---|
| `server/web/main.py` | FastAPI app, WebSocket endpoint, Redis event relay |
| `server/web/auth.py` | JWT auth: `get_current_user` (401) vs `get_optional_user` (None) |
| `server/web/database.py` | SQLite schema (meetings, segments, summaries, users, actions, integrations) |
| `docker-compose.yml` | Service orchestration (redis, audio, transcription, ai, web, device-ui) |

## Screen Flow

```
Splash -> Welcome -> WiFi Setup -> Setup Progress -> All Set -> Home
Home -> Recording -> Processing -> Summary Review -> Home
Home -> Settings -> Brightness/Timeout/AutoDelete Pickers
Home -> Settings -> Mic Test / Update Check / WiFi
Home -> Meetings -> Meeting Detail
```

## Button-to-API Mapping

### Home Screen (`home.py`)
| Button | Handler | API Call | Auth |
|---|---|---|---|
| START RECORDING | `_on_start_recording()` -> `app.start_recording()` | `POST /api/meetings/start` | optional |
| Gear icon (status bar) | `_on_gear_pressed()` | Navigation only | N/A |
| On enter: load last meeting | `_load_last_meeting()` | `GET /api/meetings/?limit=1` | optional |
| On enter: load system status | `_load_system_status()` | `GET /api/system/device-info` | none |

### Recording Screen (`recording.py`)
| Button | Handler | API Call | Auth |
|---|---|---|---|
| PAUSE / RESUME | `_on_pause()` -> `app.pause_recording()` / `app.resume_recording()` | `POST /api/meetings/pause` or `/resume` | optional |
| STOP | `_on_stop()` -> modal -> `_do_stop()` -> `app.stop_recording()` | `POST /api/meetings/stop` | optional |

### Settings Screen (`settings.py`)
| Button | Handler | API Call | Auth |
|---|---|---|---|
| Privacy toggle | `_on_privacy_toggled()` | `PATCH /api/device/settings` | optional |
| Brightness -> picker | Navigation | `PATCH /api/device/settings` (in picker) | optional |
| Timeout -> picker | Navigation | `PATCH /api/device/settings` (in picker) | optional |
| Auto-delete -> picker | Navigation | `PATCH /api/device/settings` (in picker) | optional |
| Restart | `_show_restart_dialog()` -> `_do_restart()` | `PATCH /api/device/settings` (action: restart) | optional |
| Factory Reset | `_show_factory_reset_dialog()` -> `_execute_factory_reset()` | `PATCH /api/device/settings` (action: factory_reset) | optional |
| Back button | `go_back()` | Navigation only | N/A |
| On enter: load info | `_load_system_info()` | `GET /api/system/device-info` | none |

### Summary Review Screen (`summary_review.py`)
| Button | Handler | API Call | Auth |
|---|---|---|---|
| Close | `_on_close()` | Navigation only | N/A |
| Execute Selected | `_on_execute()` | `POST /api/actions/{id}/execute` (per selected) | optional |
| On load: fetch actions | `_load_actions()` | `GET /api/meetings/{id}/actions` | optional |

### Update Check Screen (`update_check.py`)
| Button | Handler | API Call | Auth |
|---|---|---|---|
| INSTALL UPDATE | `_on_install()` | Navigation to update_install | N/A |
| On enter: check | `_check_updates()` | `GET /api/device/check-updates` | optional |

## Authentication Design

The device-ui has NO user login. It calls the backend without JWT tokens.

- `get_optional_user` (in `auth.py`): Returns `None` if no token -- **safe for device-ui**
- `get_current_user` (in `auth.py`): Raises 401 if no token -- **breaks device-ui**

All routes called by the device-ui must use `get_optional_user`.

Routes that are NOT called by the device-ui (e.g., auth routes, upload-audio) can safely require `get_current_user`.

## Known Issues / History

### 2026-02-24: Auth-Blocked Button Responses (FIXED)
**Problem**: Most backend routes required JWT auth (`get_current_user`), but the device-ui sends no token. Only recording start/stop/pause/resume worked (they used `get_optional_user`). Everything else failed silently with 401.

**Fix**: Changed all device-facing routes in `device.py`, `meetings.py`, and `actions.py` to use `get_optional_user` instead of `get_current_user`.

**Files changed**:
- `server/web/routes/device.py` -- all endpoints to `get_optional_user`
- `server/web/routes/meetings.py` -- list, detail, delete, summarize endpoints
- `server/web/routes/actions.py` -- list, update, approve, dismiss, execute endpoints

### 2026-02-24: Device Name Hardcoded as "Conference Room A" (FIXED)
**Problem**: The status bar on home, recording, processing, complete, and settings screens all had "Conference Room A" hardcoded. The device name set via the web frontend was never pulled.

**Fix**: Default changed to "MeetingBox". On startup, `_check_backend()` fetches device settings and sets `app.device_name`. Each screen's `on_enter()` updates the status bar's `device_label.text` from `app.device_name`. Settings screen also syncs it back when loading system info.

**Files changed**:
- `device-ui/src/main.py` -- added `self.device_name`, fetch in `_check_backend()`
- `device-ui/src/screens/home.py` -- dynamic update in `on_enter()`
- `device-ui/src/screens/recording.py` -- dynamic update in `on_enter()`
- `device-ui/src/screens/processing.py` -- dynamic update in `on_enter()`
- `device-ui/src/screens/complete.py` -- dynamic update in `on_enter()`
- `device-ui/src/screens/settings.py` -- syncs `app.device_name` on load

### 2026-02-24: Stop Button Not Working on Device (FIXED)
**Problem**: `stop_recording()` in `main.py` had `if not self.current_session_id: return` which silently aborted the stop if session_id was not set. The backend API reads session_id from Redis and doesn't need it from the client.

**Fix**: Removed the `self.current_session_id` guard so the stop API call always fires. Added diagnostic logging to `_on_stop`, `_do_stop`, and `stop_recording`.

**Files changed**:
- `device-ui/src/main.py` -- removed guard in `stop_recording()`
- `device-ui/src/screens/recording.py` -- added logging to stop flow

### 2026-02-24: Processing Screen Stuck Forever (FIXED — two rounds)
**Problem (round 1)**: The transcription service publishes `transcription_complete` events, but the device-ui's WebSocket dispatch table only listened for `processing_complete` (which no service ever publishes).

**Problem (round 2)**: Even after adding `transcription_complete` to the dispatch, the `meeting_id` was never extracted. The WebSocket dispatch did `data = event.get('data', {})`, but events from the `events` Redis channel have fields at the TOP LEVEL — there's no `data` wrapper. So `data` was `{}` and `meeting_id` was `None`.

**Also**: Progress bar jumped from 0% to 80% instantly because no `processing_progress` events are ever published during transcription.

**Fix**:
1. Changed `data = event.get('data', {})` to `data = event.get('data') or event` — if no `data` key, use the event itself.
2. Added `on_transcription_complete` handler + dispatch entry.
3. Added simulated progress animation to the processing screen (0→68% gradual, then real events take over).

**Files changed**:
- `device-ui/src/main.py` -- fixed data extraction, added `on_transcription_complete`
- `device-ui/src/screens/processing.py` -- added simulated progress, reset state on enter

### 2026-02-24: Live Transcription During Recording (FIXED — two rounds)
**Problem (round 1)**: No service published `transcription_update` events during recording. Initial fix only showed segment counts.

**Problem (round 2)**: User wanted actual live text, not just "8 segments processed".

**Fix**: Added real per-segment live transcription to the transcription service:
1. Downloaded `ggml-tiny.en.bin` model (~75MB, fast enough for live use on RPi5) in the Dockerfile.
2. Added a background thread in `transcription_service.py` that subscribes to `audio_segments` Redis channel.
3. When a segment WAV is saved, runs Whisper.cpp (tiny model, 2 threads) on it immediately.
4. Publishes `transcription_update` events to the `events` channel with the text.
5. Device-ui's existing `on_transcription_update` handler shows the last 2 lines of text.
6. Falls back to segment count display until the first live text arrives.

**Files changed**:
- `services/transcription/Dockerfile` -- added `ggml-tiny.en.bin` download
- `services/transcription/transcription_service.py` -- added `_live_transcribe_segment`, `_live_segment_listener`, background thread
- `device-ui/src/api_client.py` -- classify segment events as `audio_segment`
- `device-ui/src/main.py` -- added `on_audio_segment` handler + dispatch entry
- `device-ui/src/screens/recording.py` -- shows last 2 lines of live text, falls back to segment count

## Error Handling Pattern

The device-ui swallows most API errors silently:
```python
except Exception:
    pass  # or logger.error(...)
```
This means failed API calls don't show any user-visible feedback. The UI just doesn't update. If debugging, always check backend logs for 401/500 errors.

## WebSocket Events

The device-ui subscribes to `ws://web:8000/ws` and dispatches these event types:

| Event Type | Handler | Effect |
|---|---|---|
| `recording_started` | `on_recording_started()` | Navigate to recording screen |
| `recording_stopped` | `on_recording_stopped()` | Navigate to processing screen |
| `recording_paused` | `on_recording_paused()` | Update recording screen to paused |
| `recording_resumed` | `on_recording_resumed()` | Resume recording screen |
| `transcription_update` | `on_transcription_update()` | Update live captions (future: real-time ASR) |
| `transcription_complete` | `on_transcription_complete()` | Trigger auto-summarize after transcription |
| `audio_segment` | `on_audio_segment()` | Show segment count on recording screen |
| `processing_started` | `on_processing_started()` | Show meeting info on processing screen |
| `processing_progress` | `on_processing_progress()` | Update progress bar |
| `processing_complete` | `on_processing_complete()` | Trigger auto-summarize |
| `summary_complete` | `on_summary_complete()` | Show summary review screen |
| `setup_complete` | `on_setup_complete()` | Advance from onboarding to all_set |
| `update_progress` | `on_update_progress()` | Update firmware install progress |
| `error` | `on_error_event()` | Show error screen |

### Event Flow (Recording Lifecycle)
```
User taps START -> POST /api/meetings/start -> Audio service starts
                                             -> publishes recording_started (events channel)
                                             -> publishes audio_segment (audio_segments channel) per segment

User taps STOP  -> POST /api/meetings/stop  -> Audio service stops
                                             -> publishes recording_stopped (events channel)
                                             -> Transcription service picks up recording_stopped
                                             -> Runs Whisper, publishes transcription_complete (events channel)
                                             -> Device triggers _auto_summarize (API call)
                                             -> Summary shown on summary_review screen
```

---

## WiFi Onboarding – Wrong Password Handling (Feb 2026)

### Problem
During the onboarding flow, if the user entered the wrong WiFi password:
1. The onboard server (`scripts/onboard_server.py`) responded `"saved"` immediately (before verifying connection)
2. The web page showed "WiFi Configured!" even though the connection hadn't been tested
3. The background `nmcli connection up` failed, leaving wlan0 in a broken state
4. The hotspot was NOT restarted, so the phone lost connectivity to the Pi
5. The OLED/Pi desktop could become exposed due to the destabilized network state

### Fix
**File changed**: `scripts/onboard_server.py`

1. **Added `_wifi_status` global** – thread-safe status tracking (`idle` → `connecting` → `connected` | `failed`)
2. **Added `GET /api/status` endpoint** – phone polls this to learn the actual connection outcome
3. **Hotspot recovery on failure** – when `nmcli connection up` fails, the server:
   - Deletes the bad WiFi profile
   - Restarts the hotspot via `hotspot.sh start`
   - Sets `_wifi_status` to `failed` with a user-facing message
4. **Updated web page JS**:
   - After saving credentials, shows "Connecting…" screen (not "WiFi Configured!")
   - Polls `GET /api/status` every 2 seconds (up to 30 attempts = 60s)
   - On `connected` → shows success + redirect countdown
   - On `failed` → shows red "Connection Failed" card with the error message and a **Try Again** button
   - On timeout → shows error with "password may be wrong" message
   - `retrySetup()` resets the form, clears password field, re-scans networks
5. **Flow after "Try Again"**: phone reconnects to the restarted hotspot, user re-enters credentials, cycle repeats until correct password is provided

---

## Settings Screen – Full Activation (Feb 2026)

### Summary
Audited all 18 settings items. **Before**: 6 functional, 2 partial, 10 non-functional. **After**: all items operational.

### Changes by Item

| Item | Fix | File(s) |
|------|-----|---------|
| Device Name | Arrow now shows ModalDialog directing user to meetingbox.local | `settings.py` |
| Model/Serial | Fetches real `serial_number` from `GET /api/system/device-info` | `settings.py` |
| WiFi | Changed from info-only to arrow mode; navigates to existing WiFi screen | `settings.py` |
| Auto-delete | Backend now runs a background thread every 6h that deletes meetings older than the configured threshold | `server/web/main.py` |
| Screen Brightness | New `hardware.py` module writes to `/sys/class/backlight/*/brightness`. Applied on picker save + app startup | `hardware.py`, `brightness_picker.py`, `main.py` |
| Screen Timeout | Idle timer in `main.py` resets on touch, blanks screen after N minutes, restores on next touch. Skipped during active recording | `main.py`, `timeout_picker.py` |
| Privacy Mode | UI labels + optional reduced cloud use; `_auto_summarize()` calls `/api/meetings/{id}/summarize` (Claude) | `main.py` |
| Microphone Test | Replaced `random.randint()` fake bars with real `sounddevice` audio capture. Shows RMS levels, "Good/Low/No Sound" indicator | `mic_test.py`, `requirements.txt` |
| Gmail/Calendar | Fetches integration status from `GET /api/device/integrations`. Shows "Connected (email)" or "Not connected" | `settings.py` |
| All Pickers | Added `on_enter` to `PickerBaseScreen` that fetches current saved value and pre-selects it | `picker_base.py` |
| Settings subtitles | Auto-delete, Brightness, Timeout subtitles now reflect saved values (fetched from backend on enter) | `settings.py` |

### New File
- `device-ui/src/hardware.py` — RPi backlight control (brightness set, screen on/off) via sysfs with sudo fallback
