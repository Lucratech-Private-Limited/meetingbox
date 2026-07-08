# MeetingBox Voice Agent — Debug Handoff

_Last updated: 2026-07-07 (root cause found & fixed)_

---

## ✅ RESOLVED — root cause of the "mic goes deaf / slow / unresponsive"

**Root cause (confirmed from the 20:35–20:45 log + git history):** the mic was
being routed through capture engines the shipping EXE never used.

- The fast, interactive **EXE (`dist_mock/.../MeetingBox.exe`, built 2026-07-01)**
  predates commit `457929e` and the untracked `windows_aec.py`. It captured the
  mic with a plain PortAudio `RawInputStream` + **Speex AEC + local barge-in** —
  frames flow continuously regardless of playback.
- Commit `457929e` (07-02) made **software AEC3** the default engine (broke the
  barge-in evidence bar), and the uncommitted "floating logo" work added the
  brand-new **Windows Voice Capture DSP** capture (`windows_aec.py`). The prior
  session forced the DSP on via `REALTIME_PREFER_OS_AEC=1`.
- The DSP runs in **source mode**: it only emits mic frames while a fragile
  silent keep-alive render stream clocks it. Under the playback start/stop churn
  of a real multi-turn session that clock stalls and the DSP stops delivering
  frames — the mic goes **permanently deaf**. The log proves it: `peak_rms`
  collapsed to `47.9` on the email-draft screen, then **62 s of zero mic frames**
  after the recipient picker (so the spoken "third one" was never transcribed),
  then `Realtime WebSocket closed by server ... silent_for=90.4s`.

**Fix (no patchwork — restored the EXE's capture path):**
- `realtime_voice_session.py`: `REALTIME_OS_AEC` and `REALTIME_WEBRTC_AEC` now
  **default to `0`** on desktop. With both off the engine selector falls through
  to the plain PortAudio mic + Speex + local barge-in — exactly the EXE. Both
  engines remain opt-in via their env flags (nothing removed).
- `packaging/windows/device-ui.env`: set `REALTIME_OS_AEC=0`,
  `REALTIME_WEBRTC_AEC=0`; removed the AEC3/DSP-era compensations
  `REALTIME_INPUT_GAIN=2.5` and `REALTIME_VAD_THRESHOLD=0.85` (EXE ran on
  defaults 1.0 / 0.6).
- Added regression test `test_default_desktop_echo_engines_off_matches_exe`.

**Launch from source WITHOUT the AEC flags** (do NOT set `REALTIME_PREFER_OS_AEC`
or `REALTIME_OS_AEC` anymore):
```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"
$env:BACKEND_URL = "https://win.meetingboxai.lucratechsol.com"
$env:DISPLAY_WIDTH = "1024"; $env:DISPLAY_HEIGHT = "600"
& "C:\Users\vivek\meetingbox\mini-pc\device-ui\.venv\Scripts\python.exe" `
  "C:\Users\vivek\meetingbox\mini-pc\device-ui\src\main.py"
```

The section below is the prior session's investigation log, kept for history.

---


This document is a working log for the ongoing debugging of the MeetingBox
device-UI voice agent. It captures the goal, what we believe the root causes
are, every change made so far, what worked, what failed, and the next steps.

---

## 1. Goal

Restore the voice agent to the **fast, responsive behavior it had when the app
shipped as a standalone EXE**, before it was restructured into an always-on-top
**desktop overlay**. The user's baseline expectation:

- Wake ("Hey Pepper") → transcribe → respond **near-instantly** (~1s).
- **Barge-in** works: talking over Pepper stops it immediately.
- Soft speech is heard (no need to shout).
- Drafting an email / performing actions is prompt, not minutes-long.
- The "listening / end session" pills behave as part of the app (not orphaned
  windows that linger after the app minimizes).

## 2. Scope / working rules

- **Debug + fix only.** No new features, no refactors, minimal surgical changes.
- Always re-read the relevant source and recent git history before each fix.
- Confirm root cause from **logs** before changing code.

---

## 3. Environment & how to run

- OS: Windows 10 (win32 10.0.26100), shell: PowerShell.
- App entry: `mini-pc/device-ui/src/main.py`
- Venv python: `mini-pc/device-ui/.venv/Scripts/python.exe`
- Runtime log (the source of truth for diagnosis):
  `C:\Users\vivek\AppData\Local\MeetingBox\logs\meetingbox-ui.log`

**Launch command currently in use (from source, with the OS-AEC engine forced):**

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:BACKEND_URL = "https://win.meetingboxai.lucratechsol.com"
$env:REALTIME_PREFER_OS_AEC = "1"
$env:DISPLAY_WIDTH = "1024"; $env:DISPLAY_HEIGHT = "600"
& "C:\Users\vivek\meetingbox\mini-pc\device-ui\.venv\Scripts\python.exe" `
  "C:\Users\vivek\meetingbox\mini-pc\device-ui\src\main.py"
```

`PYTHONUTF8/PYTHONIOENCODING` are required — without them the app crashes on a
`UnicodeEncodeError` when logging non-ASCII (e.g. the `→` in nav logs).

---

## 4. Files being worked on

| File | Role | Touched? |
|------|------|----------|
| `mini-pc/device-ui/src/realtime_voice_session.py` | Realtime OpenAI voice WS, AEC engine selection, VAD/barge-in, audio pump | **Yes (primary)** |
| `mini-pc/device-ui/src/components/pepper_dock.py` | Desktop overlay dock UI, Win32 click-through, poll loop | Yes (overhead cuts) |
| `mini-pc/device-ui/src/dock_win_overlay.py` | Win32 layered/topmost/transparent overlay plumbing | Reverted experiments |
| `mini-pc/device-ui/src/main.py` | App lifecycle, wake handling, `maxfps` cap, nav | Yes |
| `mini-pc/device-ui/src/aec_reference.py` | Far-end (playback) reference ring for AEC | Reverted to defaults |
| `mini-pc/device-ui/src/api_client.py` | Backend REST + events WebSocket | Yes (logging only) |
| `mini-pc/device-ui/src/async_helper.py` | Bridges Kivy main thread ↔ background asyncio loop | Read-only (context) |

Relevant recent commits (context, not made by us this session):
- `457929e feat(voice): genuine WebRTC AEC3 full-duplex echo cancellation (device-agnostic)` — **suspected regression**: flipped default echo engine to software AEC3, whose barge-in requires a high local-evidence bar.
- `2b225da fix(voice): gate AEC3 residual echo so it can't trip the server VAD into phantom turns`
- `f64207e fix(device-ui): match Say 'Hey Pepper' pill to Figma`

---

## 5. Root-cause findings (confirmed from logs)

### 5.1 Barge-in "not working" — CONFIRMED, FIXED (pending user validation)
- With software **AEC3** (the post-`457929e` default), every interrupt is gated
  behind `_has_interrupt_speech_evidence` with a high bar (~1500 RMS). On a
  laptop with coupled speaker+mic, normal-volume speech over Pepper (~900 RMS)
  never cleared it, so the server's `speech_started` was discarded:
  `speech_started_ignored / no_local_speech_evidence` (many occurrences,
  including the user's 16:33–16:34 attempts).
- The code has a first-class **Windows Voice Capture DSP** (OS echo
  cancellation) path that, when active, sets `_os_aec_full_duplex = True` and
  makes a `speech_started` hard-stop playback instantly — true full-duplex.
  Comment at `realtime_voice_session.py:488` says `REALTIME_PREFER_OS_AEC=1`
  **"restores OS-DSP-first"** — i.e. OS-DSP-first is the older (working) behavior.
- **Fix step 1:** launched with `REALTIME_PREFER_OS_AEC=1`. Log confirmed the
  engine went live: `aec_engine engine=windows_voice_capture_dsp`, and real
  barge-ins fired (`playback_aborted` while `playback_remaining_s` = 3–7s).

### 5.2 Severe response latency (email drafting took ~2 minutes) — CONFIRMED, FIXED (pending user validation)
- **Not** CPU/GIL starvation: the new `_loop_lag_monitor` logged **zero** lag
  warnings on the realtime thread all session. Simple turns were fast (~1s,
  e.g. "how many countries start with M" answered in ~1s).
- The slowness was a **response cancel thrash**. In OS-DSP full-duplex mode,
  line ~4318 forced an interrupt on **any** server `speech_started`:
  `should_force_interrupt = self._os_aec_full_duplex or (...)`. While the user
  dictated a long email with natural pauses, the server VAD re-fired on the
  trailing-speech / echo tail after each pause — with **no audio playing**
  (`playback_remaining_s: 0.0`) and **no local speech** (`recent_speech: false`)
  — and each blip cancelled the freshly-requested reply. Example, all at the
  same millisecond `16:44:17.296`: `response_create_sent` → `speech_started`
  (recent_speech=false, playback_remaining=0.0) → `response_cancel_sent`.
- The reply was requested-and-killed on a loop for ~2 minutes until the thrash
  happened to stop, then the draft finally generated.

### 5.3 Backend events WebSocket drops every ~30–45s — OPEN (separate, lower priority)
- `api_client` logs recurring `WebSocket closed (code=1006 ... sent=1011
  (internal error) keepalive ping timeout), reconnecting…`. This is the
  **backend events** socket on the `async_helper` loop (NOT the realtime voice
  loop, which showed no lag). It does not block the voice path but should be
  investigated. Likely the `async_helper` loop is intermittently starved, or
  ping_interval/ping_timeout are too tight for that loop.
- Also seen: repeated `get_meetings failed:` warnings around the same times.

### 5.4 Occasional multi-second server-side response gaps — OPEN (likely server/model latency)
- e.g. "What comes second?" — `response_create_sent` at 16:41:12, first audio at
  16:41:31 (~19s), with **no** client events and **no** loop-lag in between →
  points to server/model latency for that specific query, not a client bug.

---

## 6. Changes made this session

### 6.1 `realtime_voice_session.py`
- **Barge-in playback gate (the main fix — 5.2).** Added constant
  `_BARGE_IN_MIN_PLAYBACK_S` (default 0.25s, env `REALTIME_BARGE_IN_MIN_PLAYBACK_S`).
  Rewrote the `should_force_interrupt` decision so OS-DSP full-duplex forces an
  interrupt **only when the assistant is actually speaking**
  (`audio_playback_remaining_s() > _BARGE_IN_MIN_PLAYBACK_S`); otherwise it falls
  through to the local-evidence gate (which honors `_RESPONSE_CREATE_PROTECT_S`).
  This stops trailing-speech/phantom VAD blips from killing replies that haven't
  started, while preserving genuine barge-in (which always has queued audio).
- **Instrumentation (kept):**
  - `_loop_lag_monitor()` coroutine — logs `Realtime loop lag: sleep(...) overran
    by ...` when the realtime event loop sleeps ≥0.4s late (starvation probe).
    Started/stopped in `_async_main`.
  - Enhanced `ConnectionClosed` logging in `_recv_loop` (code/reason/sent/silent_for).
  - `_last_server_event_at` updated on every received frame.
- **Response-cancel race guard (`_RESPONSE_CREATE_PROTECT_S = 2.5`)** — protects a
  freshly-requested response during the `response.create` → `response.created`
  ack gap so a stray `speech_started` can't cancel it before it speaks.
- **Response stall recovery (`_RESPONSE_STALL_RECOVERY_S = 4.0`)** in
  `_idle_watchdog` — if state is "speaking" but audio has drained and no
  `response.done` arrived, emit `response_stall_recovered` and return to
  "listening" (prevents the mic being gated forever on a stalled stream).

### 6.2 `components/pepper_dock.py`
- Reduced overlay poll rate 60Hz → **30Hz**.
- `_set_click_through` helper + `_click_through_state`: only call the Win32
  `SetWindowLongPtrW` when the click-through bit actually changes (was every
  tick). Reduces needless GIL-holding Win32 traffic.
- Reverted earlier dynamic-overlay-resizing experiment (was a regression).

### 6.3 `main.py`
- `Config.set('graphics', 'maxfps', os.getenv('MEETINGBOX_MAXFPS', '30'))` to cap
  DWM composition load.
- Wake handling navigates to the `voice_session` screen (fixed "logo opened
  Calendar" bug).

### 6.4 `api_client.py`
- Enhanced `ConnectionClosed` logging in `subscribe_events` (code/reason/sent)
  for the backend events socket (diagnosis for 5.3).

### 6.5 `aec_reference.py`
- Reverted `CUSHION_MS`/`HIGH_WATER_MS`/ring `max_seconds` back to defaults
  (80ms / 450ms / 0.6s) after the 30fps cap removed the render-feed underruns;
  kept env overrides.

---

## 7. What we tried that FAILED / was reverted
- **Dynamic overlay resizing** (shrink the transparent window to a tight bbox to
  cut DWM cost): caused regressions — logo opened Calendar, pills orphaned,
  "heard me and said nothing". **Fully reverted.**
- **Raising AEC cushion/high-water** to fight underruns: not needed once fps was
  capped; **reverted** to avoid added latency.
- **GIL-starvation hypothesis for the voice path**: instrumented and
  **disproven** for the realtime loop — `_loop_lag_monitor` never fired ≥0.4s.
  (Starvation may still apply to the *backend* `async_helper` loop — see 5.3.)

---

## 8. Current state
- App running from source as PID `48432` with `REALTIME_PREFER_OS_AEC=1` **and**
  the new barge-in playback gate. OS-DSP engine confirmed live in prior run.
- Awaiting user validation of: (a) email-draft latency, (b) barge-in, both under
  the new gate.

## 9. Next steps / TODO
1. **Validate the barge-in playback-gate fix** from the next session log:
   confirm the phantom `response_cancel_sent (source=speech_started,
   playback_remaining=0.0)` events are gone and the email draft completes
   promptly after the user stops talking.
2. **Turn endpointing for dictation (secondary latency contributor).** The VAD
   commits a turn after only **500ms** of silence (`vad_silence_ms=500`), so a
   long dictation with thinking pauses is chopped into multiple turns. Consider
   lengthening to ~900ms for more natural dictation. _Not changed yet_ (kept the
   fix surgical; trades a little responsiveness on short commands). Decide with
   user.
3. **Backend events WS keepalive (5.3).** Investigate the recurring `1011`
   keepalive ping timeouts on the `async_helper` loop — check `ping_interval`/
   `ping_timeout` and whether that loop is starved; correlate with `get_meetings
   failed`.
4. **Pills / overlay lifecycle** — re-verify that ending a session (click
   outside / collapse) always tears down both pills and never leaves orphaned
   topmost windows after the fix churn.
5. If OS-DSP proves reliable across sessions, **make OS-DSP-first the default on
   Windows desktop** (currently gated behind `REALTIME_PREFER_OS_AEC=1`) so the
   fix survives without the env flag.

## 10. Useful diagnostic greps (log)
```text
speech_started_ignored            # barge-in rejected by evidence gate
response_cancel_sent              # who/what cancelled a reply (source=...)
Realtime loop lag                 # realtime event-loop starvation
WebSocket closed (code=            # backend events WS drops (api_client)
Realtime WebSocket closed by server  # realtime WS close (code/reason/silent_for)
aec_engine                        # which echo engine went live
turn_committed / response_create_sent / final_transcript  # turn timing
```
