"""
OpenAI Realtime voice bridge for the MeetingBox device UI.

Minimal, low-latency rebuild.

Design:
- Connect to wss://api.openai.com/v1/realtime with the ephemeral
  client_secret minted by the MeetingBox server.
- Server VAD segments turns; the CLIENT decides what happens to them
  (client-authority turn-taking). The session runs with
  `create_response: false` and `interrupt_response: false`: at
  input_audio_buffer.committed the client checks the turn's acoustic
  evidence and either sends response.create (genuine speech) or
  conversation.item.delete (phantom — echo/noise the server VAD
  mis-segmented). A phantom therefore never creates a response, never
  paints a bubble, and never pollutes the model's context. Latency cost
  is one WS message (~no dead air); the evidence is already computed by
  the time the commit event arrives.
- Send ONE small session.update: nudge eagerness to "high" and enable
  user-audio transcription (used only for farewell detection). The
  server's full instructions, tools, voice, and audio format are left
  exactly as configured.
- Stream PCM16 mic audio at 24 kHz to input_audio_buffer.append.
- Play model audio deltas through aplay; pipe writes run on a dedicated
  single-thread executor so they never block the WebSocket heartbeat.
- On user speech_started with local speech evidence: hard-kill aplay so
  the user hears themselves, not the assistant, and send response.cancel
  (the client owns interruption — interrupt_response is off so a VAD
  false-positive can never truncate a genuine answer).
- On user transcript completion: if the text is a farewell, close the
  session. Otherwise the server creates the next response automatically.
- On function-call output in response.done: invoke the backend tool via
  HTTP, post the result back, send response.create to continue.

Turn speech-evidence layer (engine-agnostic, all paths):
- Every frame sent up the wire is scored by a local VAD (adaptive noise
  floor + WebRTC APM speech probability when AEC3 is live) at the single
  uplink choke point. Final transcripts are accepted only when the turn's
  audio actually carried speech — otherwise they are ASR hallucinations
  ("it" / "hello" / ".") from a noise-committed turn and are dropped, with
  the auto-created response cancelled. The same evidence gates barge-in: a
  server speech_started only stops playback when the uplink recently
  carried locally-verified speech. See _UplinkSpeechMonitor.
- Echo-aware scoring: while assistant audio plays (plus a decay hangover)
  the AEC's residual echo is both energetic and voice-like, so RMS vs the
  ambient floor cannot arbitrate. Playback-time frames must instead reach
  an absolute barge-in level AND dominate a learned residual-echo envelope
  (the double-talk principle) before they count as speech evidence. This
  stops the assistant's own leaked voice from validating phantom turns
  ("The" / "Hi") or authorizing false self-interruptions.

Echo / self-hearing handling depends on the resolved audio hardware:
- Echo-isolated combined external mic+speaker puck (AudioDevicePair
  is_combined): full-duplex. Speex AEC + an energy-based barge-in gate
  let the user talk over the assistant.
- Built-in mic + speaker (chassis-coupled) or any non-combined pair:
  half-duplex. While the assistant speaks (plus an echo-decay tail) ALL
  mic frames are dropped so the device never hears its own voice and
  loops; voice barge-in is off but screen-tap barge-in still works.
Override with REALTIME_HALF_DUPLEX (auto|1|0).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import queue
import shutil
import string
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import numpy as np
import websockets
from kivy.clock import Clock

from api_client import invoke_realtime_tool_sync
from ssl_compat import ws_ssl_context

try:
    from platform_compat import IS_DESKTOP
except Exception:  # pragma: no cover - platform_compat always present in app
    IS_DESKTOP = not sys.platform.startswith("linux")

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
except ImportError:
    sd = None

# On the Linux appliance, model audio is played by piping raw PCM to ``aplay``
# (ALSA). Windows/macOS have no ``aplay``; there we play through PortAudio via
# the cross-platform :mod:`audio_output` helper, which preserves the same
# instant barge-in (abort + flush) semantics.
_USE_SD_PLAYBACK = not sys.platform.startswith("linux")

REALTIME_VOICE_IMPLEMENTED = True


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except ValueError:
            logger.warning("%s=%r is not a float; using %s", name, raw, default)
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = int(float(str(raw).strip()))
        except ValueError:
            logger.warning("%s=%r is not an int; using %s", name, raw, default)
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


_REALTIME_WS_HOST = "api.openai.com"
_REALTIME_RATE = 24000

# --- Uplink codec (mic -> server bandwidth) -------------------------------
# The mic/AEC pipeline runs at 24 kHz PCM16 internally, but that is ~512 kbps
# on the wire (base64'd), which is more sustained UPSTREAM than many real
# connections can carry. When the uplink can't keep up, mic frames back up,
# the server hears silence, and it closes the session on a keepalive timeout
# (observed repeatedly during continuous email dictation on a ~0.4-1.1 Mbps
# link). G.711 mu-law at 8 kHz is ~64 kbps (~6x smaller after base64) — the
# standard low-bandwidth voice codec (telephone quality) — and fits in even a
# marginal upstream with headroom. The DOWNLINK (assistant voice) is
# unaffected and stays full quality. Only the mic->server leg is transcoded,
# and only at the final wire step: AEC, live captions and the speech-evidence
# monitor all still see the full 24 kHz audio.
#   REALTIME_UPLINK_CODEC=g711_ulaw  # g711_ulaw (default) | pcm16
# Deployments with guaranteed bandwidth can set pcm16 for pristine uplink.
_REALTIME_UPLINK_CODEC = (
    os.environ.get("REALTIME_UPLINK_CODEC", "g711_ulaw") or "g711_ulaw"
).strip().lower()
_G711_RATE = 8000

# G.711 encoding uses the stdlib ``audioop`` (present in CPython 3.11; the
# drop-in ``audioop-lts`` package restores it on 3.13+). We deliberately do NOT
# hand-roll a mu-law encoder: it must be bit-exact with what the server's
# decoder expects or speech comes through garbled, and matching audioop's
# 16->14-bit handling exactly is error-prone. When audioop is unavailable we
# simply keep the PCM16 uplink (correct, just higher bandwidth) rather than
# risk sending mis-encoded audio.
try:
    import audioop as _audioop  # type: ignore
except Exception:  # pragma: no cover - only on 3.13+ without audioop-lts
    _audioop = None


def _pcm16_to_ulaw(pcm16: bytes) -> bytes:
    """Encode 16-bit mono PCM to G.711 mu-law bytes (1 byte/sample)."""
    if not pcm16 or _audioop is None:
        return b""
    return _audioop.lin2ulaw(pcm16, 2)

# Mic chunk duration. Smaller chunks help the server VAD see speech edges
# sooner, but 5 ms (200 callbacks/s at 48 kHz capture) overwhelms the
# asyncio executor on this hardware and produces persistent PortAudio
# `input overflow` warnings — dropped samples in the middle of a word
# corrupt both STT and the speech-to-speech model's input. 20 ms is the
# sweet spot: 50 callbacks/s sustains cleanly, while only adding ~15 ms
# to the user-stop → response-start latency vs 5 ms.
_APPEND_CHUNK_MS = 20

# How often the mic pump polls the audio queue. Kept tight so the
# event loop never sleeps long enough to delay a flush.
_MIC_QUEUE_POLL_S = 0.01

# aplay ALSA buffer in microseconds. 70 ms keeps the speaker pipe from
# starving while leaving room to hard-kill on barge-in.
_APLAY_BUFFER_TIME_US = "70000"

# After a barge-in, drop any further audio deltas for this long to flush
# the trailing bytes of the cancelled response. Cleared as soon as a new
# response.created event arrives so we never silence a fresh response.
_BARGE_IN_SUPPRESS_AUDIO_S = 0.4

# A barge-in only means something while the assistant is ACTUALLY speaking.
# In OS-AEC full-duplex we trust the server VAD, but the server re-fires
# speech_started on the user's own trailing speech / residual-echo tail right
# after each pause — with no audio playing. Forcing an interrupt for that
# cancels the freshly-requested reply before it can speak, which is what makes
# long dictation crawl (each pause commits a turn, the next syllable kills the
# response). Require this much queued assistant audio before a server VAD blip
# is allowed to force-cancel; below it there is nothing to barge into.
_BARGE_IN_MIN_PLAYBACK_S = _env_float(
    "REALTIME_BARGE_IN_MIN_PLAYBACK_S", 0.25, minimum=0.0, maximum=2.0
)

# After WE send response.create, the server takes up to ~1-2 s to acknowledge
# with response.created (which is what sets _response_in_progress). During that
# gap the response IS in flight but no flag reflects it yet, so a stray server
# speech_started would be mis-read as "nothing to protect" and cancel the reply
# before it ever speaks. Treat a freshly-requested response as protected for
# this window; the assistant isn't audible yet, so a genuine barge-in loses
# nothing (it re-interrupts once real audio starts).
_RESPONSE_CREATE_PROTECT_S = 2.5

# A response can stop streaming without ever sending response.done (e.g. the
# realtime stream degrades / the proxy socket flaps mid-turn). _state then
# stays "speaking" forever, which (a) keeps the AEC3 residual gate armed so
# every mic frame is suppressed (the user is never heard) and (b) freezes the
# UI pill on "talking". If assistant audio has fully drained and nothing has
# extended it for this long while still "speaking", treat the turn as finished
# so the mic reopens and the pill recovers. Comfortably longer than any natural
# inter-sentence gap (audio playback keeps _assistant_audio_play_until ahead).
_RESPONSE_STALL_RECOVERY_S = 4.0

# Close the session if the user is silent (and we're not speaking) for
# this many seconds. Matches the previous behavior.
_SESSION_IDLE_CLOSE_S = 40.0

# --- Dead-link (half-open connection) detection ---------------------------
# A flaky uplink can lose its INBOUND half while the outbound half stays up:
# TCP keeps ACKing our audio (frames flow, sends are fast) but OpenAI's events
# never arrive. The WS ping/pong CANNOT catch this quickly — OpenAI legitimately
# delays protocol pongs 5-15 s+ while generating, which is why ping_timeout had
# to be raised to 120 s (a shorter timeout false-closed healthy mid-response
# sessions). So a dead inbound link previously sat undetected for ~60 s until the
# network itself reaped the zombie socket, stranding the user mid-conversation.
#
# The correct liveness signal is INBOUND DATA FLOW, not pings: a live-but-busy
# server streams audio/transcription deltas continuously (sub-second), so
# "time since the last server event" stays fresh; only a truly dead link goes
# fully silent. We flag the link dead when we have been actively streaming mic
# audio (so the server owes us SOMETHING — at minimum a speech_started) yet have
# received nothing for this long. Guarded so a legitimate long dictation (server
# already acked speech_started; a single utterance emits no further events until
# speech_stopped) is never mistaken for a dead link — except past the hard cap,
# which catches a link that dies mid-utterance so speech_stopped never comes.
_DEAD_LINK_RECV_SILENCE_S = max(
    3.0, float(os.environ.get("REALTIME_DEAD_LINK_S", "8") or "8")
)
_DEAD_LINK_HARD_S = max(_DEAD_LINK_RECV_SILENCE_S + 5.0, 30.0)
# Inbound silence alone is NOT proof of a dead link: a silent user produces the
# exact same signature (uplink trickling frames, server with nothing to say).
# So when a silence threshold trips, VERIFY with a protocol ping before killing
# the session — a healthy idle link pongs in well under a second; only a truly
# dead link stays mute. Generous timeout because OpenAI can delay pongs several
# seconds under load (though mid-generation the deltas keep recv_silence fresh,
# so we rarely probe then).
_DEAD_LINK_PING_TIMEOUT_S = 10.0

# ── Device-driven morning-brief carousel walkthrough ───────────────────────
# The Realtime model batches its navigate_device_ui calls (all three at once)
# and then narrates everything in one breath, so the carousel races to the
# last card before any speech. To keep the on-screen card in lockstep with the
# spoken section, the device takes over: it advances the carousel one section
# at a time and drives a separate, tool-less narration response per section,
# each gated until the previous section's audio has finished playing.
_BRIEF_SECTIONS = ("schedule", "tasks", "emails")
_BRIEF_SECTION_INDEX = {name: idx for idx, name in enumerate(_BRIEF_SECTIONS)}
_BRIEF_DIRECTIVE_TEMPLATES = {
    "schedule": (
        "[Morning briefing — SCHEDULE] The schedule card is now visible. "
        "The current local time is {current_time}.\n"
        "Using ONLY the briefing data already in this conversation:\n"
        "1. Count ONLY meetings whose start time is STRICTLY AFTER {current_time} — these are "
        "PENDING meetings. Any meeting that has already started or finished is NOT pending.\n"
        "2. If there are pending meetings: say exactly 'You have N meeting(s) remaining today.' "
        "(use the real count N). Then name the next upcoming meeting — its title and start time "
        "— as the highlighted meeting. Then briefly mention any further pending meetings.\n"
        "3. If ALL meetings today have already passed: say exactly "
        "'You are done with all meetings for today.'\n"
        "4. If there are NO meetings at all today: say exactly "
        "'There are no meetings planned for today.'\n"
        "Do NOT mention tasks or emails. One or two sentences total. Speak now."
    ),
    "tasks": (
        "[Morning briefing — TASKS] The tasks card is now visible. "
        "Using ONLY the briefing data already in this conversation:\n"
        "1. Count ONLY tasks that are: (a) due TODAY, and (b) still pending (not completed).\n"
        "2. If there are tasks: say exactly 'You have N task(s) planned today:' then list each "
        "task title naturally in one sentence.\n"
        "3. If there are no pending tasks due today: say exactly "
        "'There are no tasks planned for today.'\n"
        "Do NOT mention overdue tasks, future tasks, completed tasks, meetings, or emails. "
        "One or two sentences total. Speak now."
    ),
    "emails": (
        "[Morning briefing — EMAILS] The emails card is now visible. "
        "Using ONLY the briefing data already in this conversation:\n"
        "1. Count ONLY unread emails (not archived, not already read).\n"
        "2. If there are unread emails: say exactly 'You have N unread email(s).' then briefly "
        "name each sender and their subject in one natural sentence.\n"
        "3. If there are no unread emails: say exactly "
        "'You have no unread emails. You are all caught up.'\n"
        "Do NOT mention meetings or tasks. After the email summary, deliver exactly one short "
        "closing sentence that wraps up the whole morning briefing naturally. Speak now."
    ),
}


def _build_brief_directive(section: str, facts: str | None = None) -> str:
    """Return the section directive with current time + on-screen facts injected.

    When ``facts`` is provided it is the authoritative data the UI is showing for
    this section; the model must narrate exactly those facts so speech matches UI.
    """
    template = _BRIEF_DIRECTIVE_TEMPLATES.get(section, "")
    try:
        from config import display_now as _display_now
        now = _display_now()
        h12 = now.hour % 12 or 12
        am = "AM" if now.hour < 12 else "PM"
        current_time = f"{h12}:{now.minute:02d} {am}"
    except Exception:
        current_time = "unknown"
    directive = template.format(current_time=current_time)
    facts_clean = (facts or "").strip()
    if facts_clean:
        directive = (
            f"AUTHORITATIVE ON-SCREEN DATA for this section (narrate EXACTLY this, "
            f"do not invent, omit, or add anything): {facts_clean}\n\n"
            f"{directive}"
        )
    return directive


def _brief_target_index(target_tab: str | None, current_idx: int) -> int:
    """Resolve a model/user morning-brief section request into a carousel index."""
    target = (target_tab or "").strip().lower()
    if target in ("next", "forward", "right"):
        return (current_idx + 1) % len(_BRIEF_SECTIONS)
    if target in ("previous", "prev", "back", "left"):
        return (current_idx - 1) % len(_BRIEF_SECTIONS)
    return _BRIEF_SECTION_INDEX.get(target, 0)

_REALTIME_OUTPUT_VOICE_FALLBACK = "marin"

# When True, the device sends a small response.create right after the
# session is configured so the model greets the user (e.g. "Hey, how can I
# help you?"). This gives a consistent verbal "I'm listening" cue after the
# wake word triggers, instead of silence until the user speaks again.
# The greeting is interruptible (interrupt_response stays true), so if the
# user is already mid-sentence after the wake word it gets pre-empted
# naturally without dead air.
_REALTIME_WAKE_GREETING_ENABLED = os.environ.get(
    "REALTIME_WAKE_GREETING_ENABLED", "1"
).strip().lower() not in ("", "0", "false", "no", "off")

_REALTIME_WAKE_GREETING_INSTRUCTIONS = (
    "Open with exactly one short greeting sentence to confirm you are "
    "listening, max six words. Vary it naturally between phrasings like "
    "'Hey, how can I help you?', 'Yes, I'm listening', 'Hi, what do you "
    "need?', 'Go ahead.', 'I'm here.'. Then immediately stop and wait for "
    "the user's request. Do NOT introduce yourself, list capabilities, "
    "mention tools, or read out today's date / weather / schedule unless "
    "the user explicitly asks."
)

# STT model for the user-speech transcript stream (used by the UI
# overlay, farewell detection, and grammar correction).
#
# gpt-4o-transcribe was the last known good model for short conversational
# MeetingBox utterances. It avoids the random suffixes seen with the mini
# transcript model while preserving Realtime input-audio transcript events.
_DEFAULT_INPUT_TRANSCRIPTION_MODEL = (
    os.environ.get("REALTIME_TRANSCRIBE_MODEL", "gpt-4o-transcribe").strip()
    or "gpt-4o-transcribe"
)
# Deliberately empty. Prompt text was a real source of prompt-echo and
# phrase-contamination hallucinations in short/noisy turns.
_INPUT_TRANSCRIPTION_PROMPT = ""


def _is_prompt_echo(text: str) -> bool:
    """True if a transcript is a Whisper *prompt-echo hallucination*.

    When the AI's own playback audio leaks into the mic (imperfect AEC) or the
    captured segment is near-silence, the transcription model echoes the
    transcription ``prompt`` back — OpenAI wraps it as ``context: ### <prompt> ###``.
    These phantom utterances must NOT be shown as user speech or trigger a
    model turn / grammar correction.
    """
    if not text:
        return False
    t = text.strip().lower()
    if not t:
        return False
    # The wrapped prompt always carries the "###" fence — a reliable marker
    # that a real spoken utterance would essentially never contain.
    if "###" in t:
        return True
    if "context:" in t and "conversational english" in t:
        return True
    norm = " ".join(t.translate(str.maketrans({c: " " for c in string.punctuation})).split())
    if norm in ("conversational", "conversational english"):
        return True
    # A bare echo of just the prompt text (no real words around it).
    prompt = _INPUT_TRANSCRIPTION_PROMPT.strip().lower().rstrip(".")
    if prompt and prompt in t and len(t) <= len(prompt) + 12:
        return True
    return False

# Turn-end detection eagerness for semantic VAD. Higher = the assistant
# replies sooner after the user stops talking (less dead air); lower =
# waits longer to be sure the user is done. "low" was historically forced
# because the device lacked acoustic echo cancellation and high eagerness
# caught speaker echo as user speech. AEC (speex) is now enabled, so we
# can run "medium" for a snappier turn-around. Override via
# REALTIME_VAD_EAGERNESS (low|medium|high|auto).
_REALTIME_VAD_EAGERNESS = (
    os.environ.get("REALTIME_VAD_EAGERNESS", "medium").strip().lower() or "medium"
)

# Turn-detection strategy. "semantic_vad" waits for a semantic end-of-turn and
# is easily held open by ambient room noise (it keeps "listening" until the
# noise settles). "server_vad" is energy-based with an explicit threshold and a
# fixed end-of-turn silence window, so it ignores anything below the threshold
# and commits a fixed time after the user stops — much more focused on the
# active talker. With OS-AEC the mic is clean, so on desktop we default to
# server_vad. "auto" leaves the server's own config untouched.
#   server_vad | semantic_vad | auto
_REALTIME_TURN_DETECTION = (
    os.environ.get("REALTIME_TURN_DETECTION", "auto").strip().lower() or "auto"
)
# server_vad tuning. threshold: 0..1, higher = ignore quieter sounds (ambient).
# silence_ms: how long below threshold before the turn ends (lower = snappier).
# prefix_ms: audio kept before speech onset so the first word isn't clipped.
_REALTIME_VAD_THRESHOLD = _env_float(
    "REALTIME_VAD_THRESHOLD", 0.6, minimum=0.0, maximum=1.0
)
_REALTIME_VAD_SILENCE_MS = _env_int(
    "REALTIME_VAD_SILENCE_MS", 500, minimum=100, maximum=4000
)
_REALTIME_VAD_PREFIX_MS = _env_int(
    "REALTIME_VAD_PREFIX_MS", 300, minimum=0, maximum=1000
)

# Half-duplex self-hearing guard. On a device whose mic and speaker share the
# same chassis (built-in mic, no external puck) the speaker output couples
# straight back into the mic. Half-duplex still suppresses normal mic upload
# during assistant speech, but a separate local barge-in detector can cancel
# playback immediately and promote the user's speech to the active turn.
#   REALTIME_HALF_DUPLEX: auto (default) | 1/on | 0/off
#     auto → enabled UNLESS an echo-isolated combined external mic+speaker
#            puck is in use (audio pair reports is_combined).
_REALTIME_HALF_DUPLEX_ENV = (
    os.environ.get("REALTIME_HALF_DUPLEX", "auto").strip().lower() or "auto"
)

# Local barge-in detection runs even while mic upload is echo-gated. It uses a
# short adaptive baseline plus far-end reference energy to detect a new near-end
# speaker, then kills playback and forwards a small pre-roll of mic audio so the
# first word of the interruption is not clipped.
_LOCAL_BARGE_IN_ENABLED = (
    os.environ.get("REALTIME_LOCAL_BARGE_IN", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)
# Desktop (laptop/PC built-in mic) is quieter and more variable than the
# appliance's far-field USB array, so the fixed appliance floor (900) often
# sits ABOVE genuine speech and the local detector never "confirms" the user —
# the server hears them but we withhold the audio (the "it ignores my input"
# symptom). Use a lower default floor on desktop; both stay env-overridable.
_LOCAL_BARGE_IN_MIN_RMS = _env_float(
    "REALTIME_BARGE_IN_MIN_RMS", 500.0 if IS_DESKTOP else 900.0, minimum=100.0
)
_LOCAL_BARGE_IN_REF_RATIO = _env_float("REALTIME_BARGE_IN_REF_RATIO", 1.65, minimum=1.0)

# Optional digital gain applied to the desktop uplink before AEC/VAD. Helps a
# quiet built-in mic clear the server VAD floor without re-running setup.
# 1.0 = unchanged. Clipped to avoid distortion.
_REALTIME_INPUT_GAIN = _env_float("REALTIME_INPUT_GAIN", 1.0, minimum=0.1, maximum=12.0)

# When the OpenAI server VAD reports speech_started while we're half-duplex
# (mic gated for echo), trust it on desktop: open the uplink for this long so
# the user's full utterance reaches the server instead of being withheld.
_REALTIME_SPEECH_UPLINK_S = _env_float(
    "REALTIME_SPEECH_UPLINK_S", 3.0, minimum=0.5, maximum=15.0
)
_LOCAL_BARGE_IN_BASELINE_RATIO = _env_float("REALTIME_BARGE_IN_BASELINE_RATIO", 2.4, minimum=1.2)
_LOCAL_BARGE_IN_MAX_ECHO_SIMILARITY = _env_float(
    "REALTIME_BARGE_IN_MAX_ECHO_SIMILARITY",
    0.72,
    minimum=0.2,
    maximum=0.999,
)
_LOCAL_BARGE_IN_ECHO_DIVERGENCE_ENABLED = (
    os.environ.get("REALTIME_BARGE_IN_ECHO_DIVERGENCE", "0").strip().lower()
    not in ("0", "false", "no", "off", "")
)
_LOCAL_BARGE_IN_ECHO_MIN_BASELINE_RATIO = _env_float(
    "REALTIME_BARGE_IN_ECHO_MIN_BASELINE_RATIO",
    1.35,
    minimum=1.0,
)
_LOCAL_BARGE_IN_ECHO_MIN_REF_RATIO = _env_float(
    "REALTIME_BARGE_IN_ECHO_MIN_REF_RATIO",
    0.8,
    minimum=0.2,
    maximum=2.0,
)
_LOCAL_BARGE_IN_SPIKE_ECHO_SIMILARITY_GUARD = _env_float(
    "REALTIME_BARGE_IN_SPIKE_ECHO_SIMILARITY_GUARD",
    0.9,
    minimum=0.5,
    maximum=0.999,
)
_LOCAL_BARGE_IN_SPIKE_ECHO_MAX_REF_RATIO = _env_float(
    "REALTIME_BARGE_IN_SPIKE_ECHO_MAX_REF_RATIO",
    2.0,
    minimum=1.0,
    maximum=5.0,
)
_LOCAL_BARGE_IN_MIN_FRAMES = _env_int("REALTIME_BARGE_IN_MIN_FRAMES", 2, minimum=1, maximum=10)
_LOCAL_BARGE_IN_PREROLL_S = _env_float("REALTIME_BARGE_IN_PREROLL_S", 0.18, minimum=0.0, maximum=0.5)

# --- OS-grade acoustic echo cancellation (Windows Voice Capture DSP) ----------
# Drive the Windows "Voice Capture DSP" (CWMAudioAEC). It captures the mic AND
# references the system render itself, returning a clean, echo-cancelled 16 kHz
# mono stream. When live it becomes the mic SOURCE (source mode) instead of our
# own PortAudio input stream.
#
# OFF by default (opt-in via REALTIME_OS_AEC=1). Source mode only emits mic
# frames while the OS render clock ticks — we keep it clocking with a silent
# "keep-alive" render stream — but under the playback start/stop churn of a real
# session that clock stalls and the DSP stops delivering frames, so the mic goes
# permanently deaf (observed: peak_rms collapses to ~48, then 60 s+ of zero
# frames, then the realtime socket closes on a 90 s server-silence timeout).
# The shipping EXE never used this path; it captured with a normal PortAudio
# input stream (below), which delivers frames continuously regardless of
# playback. Keep the DSP available for opt-in only.
_OS_AEC_ENABLED = (
    os.environ.get("REALTIME_OS_AEC", "0").strip().lower()
    not in ("0", "false", "no", "off", "")
)

# Engine ordering on Windows: WebRTC AEC3 + WASAPI loopback FIRST, OS Voice
# Capture DSP as fallback. This is the architecture ChatGPT/Gemini/Meet
# actually ship: the echo canceller is PURE SOFTWARE (the same AEC3 code
# Chrome runs), so its behavior is deterministic and identical on every
# machine regardless of the audio chip or vendor driver. The OS DSP
# (CWMAudioAEC) delegates cancellation quality to whatever DSP the driver
# provides — measured on real hardware it leaks residual echo bursts of
# RMS 400-2000 that defeat any downstream gate, and it exposes no internal
# state (no speech probability, no ERLE) so the client is blind to what it
# did. AEC3's known trade-offs (loopback onset blind window, FIFO drift)
# are handled explicitly by the reference-blind gate and the residual gate,
# and — unlike driver behavior — they are the same on every device.
# Set REALTIME_PREFER_OS_AEC=1 to restore OS-DSP-first without a rebuild.
_PREFER_OS_AEC = (
    os.environ.get("REALTIME_PREFER_OS_AEC", "0").strip().lower()
    not in ("0", "false", "no", "off", "")
)

# Genuine WebRTC AEC3 (the echo canceller Chrome/Meet/Discord use) driven off a
# real playback reference: WASAPI loopback on Windows, the app's own PCM on
# macOS (see aec_reference.py). AEC3 only accepts 16/32/48 kHz; we run it at
# 48 kHz (loopback's native rate) and downsample the cleaned near-end to the
# 24 kHz uplink.
#
# OFF by default on desktop (opt-in via REALTIME_WEBRTC_AEC=1). The shipping
# EXE — the fast, interactive build the product baselines against — used the
# plain PortAudio mic + Speex AEC + local barge-in path below, NOT AEC3. Making
# AEC3 the default engine (commit 457929e) raised the barge-in evidence bar so
# normal-volume speech over a coupled laptop speaker/mic no longer interrupted,
# which is the regression this restores. AEC3 remains available for opt-in
# experiments but is no longer the default mic-processing engine.
_WEBRTC_AEC_ENABLED = (
    os.environ.get("REALTIME_WEBRTC_AEC", "0").strip().lower()
    not in ("0", "false", "no", "off", "")
)
_AEC3_RATE = 48000

# Residual-echo uplink gate for the AEC3 path. AEC3 delivers ~35 dB ERLE, not
# infinite suppression: on loud consonants a little echo survives in the cleaned
# near-end and can trip the *server* VAD (threshold ~0.85) into a phantom
# speech_started -> a one-token transcript ("the", "."). While the assistant is
# actually playing (far-end RMS above _AEC3_GATE_FAR_ACTIVE_RMS) we forward mic
# frames only when the cleaned near-end RMS clearly exceeds the adaptive residual
# floor (genuine double-talk); otherwise the frame is withheld so residual echo
# never reaches the server VAD. When the assistant is silent the gate is fully
# open, so normal user turns are never affected. A short preroll is flushed when
# the gate opens so the first syllable of a real barge-in is not clipped, and a
# hangover keeps it open through natural pauses. Disable via
# REALTIME_AEC3_RESIDUAL_GATE=0.
_AEC3_RESIDUAL_GATE_ENABLED = (
    os.environ.get("REALTIME_AEC3_RESIDUAL_GATE", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)
# Far-end RMS above which the assistant is considered "playing" (gate armed).
_AEC3_GATE_FAR_ACTIVE_RMS = _env_float(
    "REALTIME_AEC3_GATE_FAR_ACTIVE_RMS", 200.0, minimum=1.0
)
# Keep "assistant is still effectively playing" armed briefly after far-end RMS
# dips below the active threshold. This avoids flicker around the tail and
# blocks residual echo bursts that otherwise sneak through as one-token turns.
_AEC3_GATE_FAR_ACTIVE_HANGOVER_S = _env_float(
    "REALTIME_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45, minimum=0.0, maximum=2.0
)
# Post-playback echo-suppression cooldown, driven by our own (reliable) render
# clock rather than loopback RMS. Room resonance / decaying echo of the last
# words keeps arriving at the mic for ~1-1.5 s after playback ends, well above
# the VAD floor — this is the exact tail that leaks phantom end-of-turn words
# (e.g. "glad"). Production native voice apps (and Google's Gemini guidance)
# hold mic suppression for this long after the assistant stops. Real, clearly
# dominant user speech still barges in via the elevated double-talk threshold.
_AEC3_GATE_COOLDOWN_S = _env_float(
    "REALTIME_AEC3_GATE_COOLDOWN_S", 1.2, minimum=0.3, maximum=3.0
)
# Absolute near-end RMS floor a frame must clear to count as real speech while
# the assistant is playing. Genuine speech is ~1500-5000; post-AEC echo residual
# is far lower, so this cleanly separates them.
_AEC3_GATE_MIN_RMS = _env_float(
    "REALTIME_AEC3_GATE_MIN_RMS", 550.0 if IS_DESKTOP else 750.0, minimum=100.0
)
# A frame must also exceed the adaptive residual floor by this ratio.
_AEC3_GATE_FLOOR_RATIO = _env_float(
    "REALTIME_AEC3_GATE_FLOOR_RATIO", 3.0, minimum=1.2
)
# ERLE-aware term: post-AEC residual echo scales with the far-end (playback)
# level, and spikes higher while AEC3 is re-converging (which happens on every
# playback abort/restart). So the double-talk threshold must scale with the
# current far-end RMS, not stay fixed -- otherwise loud-playback residual bursts
# clear a fixed floor and leak one-token phantoms ("it"/"the"). A frame must
# exceed this fraction of the far-end RMS to count as genuine near-end speech.
# Set to 0 to disable the far-proportional term.
_AEC3_GATE_FAR_LEAK_RATIO = _env_float(
    "REALTIME_AEC3_GATE_FAR_LEAK_RATIO", 0.12, minimum=0.0, maximum=1.0
)
# Keep the gate open this long after the last speech frame (natural pauses).
_AEC3_GATE_HANGOVER_S = _env_float(
    "REALTIME_AEC3_GATE_HANGOVER_S", 0.4, minimum=0.0, maximum=2.0
)
# Preroll flushed when the gate opens so a real barge-in's first syllable is kept.
_AEC3_GATE_PREROLL_S = _env_float(
    "REALTIME_AEC3_GATE_PREROLL_S", 0.15, minimum=0.0, maximum=0.5
)
# A single post-AEC frame can spike above the threshold from a transient
# residual-echo burst. Real speech sustains for many consecutive frames, so
# require this many above-threshold frames in a row before opening the gate.
# The held onset frames sit in the preroll and are flushed intact on confirm,
# so genuine barge-in loses no audio (only ~this-many-frames of latency).
_AEC3_GATE_CONSEC_FRAMES = _env_int(
    "REALTIME_AEC3_GATE_CONSEC_FRAMES", 3, minimum=1, maximum=20
)
# If a tiny transcript slips through while assistant audio is still playing
# (or in the immediate playback tail), treat it as likely echo-phantom and drop
# it client-side before it can trigger a bogus assistant reply.
_AEC3_PHANTOM_TRANSCRIPT_TAIL_S = _env_float(
    "REALTIME_AEC3_PHANTOM_TRANSCRIPT_TAIL_S", 1.0, minimum=0.0, maximum=3.0
)
# Recent local near-end evidence window. Server VAD can still false-trigger on
# residual echo at playback onset on some laptop paths; only trust instantaneous
# speech_started interruptions when we have local near-end evidence above this
# adaptive floor in the immediate past.
_AEC3_SPEECH_EVIDENCE_WINDOW_S = _env_float(
    "REALTIME_AEC3_SPEECH_EVIDENCE_WINDOW_S", 1.2, minimum=0.2, maximum=4.0
)
_AEC3_SPEECH_EVIDENCE_RATIO = _env_float(
    "REALTIME_AEC3_SPEECH_EVIDENCE_RATIO", 0.55, minimum=0.2, maximum=1.0
)
_AEC3_SPEECH_EVIDENCE_MIN_RMS = _env_float(
    "REALTIME_AEC3_SPEECH_EVIDENCE_MIN_RMS",
    220.0 if IS_DESKTOP else 320.0,
    minimum=80.0,
)

# --- Residual-echo uplink gate for the Windows OS Voice Capture DSP path ------
# The OS DSP (CWMAudioAEC) cancels the assistant's echo at the source, but like
# every canceller it leaves ~30-40 dB ERLE, so a little residual survives on
# loud playback. The OS-DSP path streams the mic to OpenAI CONTINUOUSLY, so that
# residual reaches the server VAD and gets hallucinated into a stray one-word
# "turn" ("Hello"/"Sure"/"the") mid-playback. This gate closes that hole using
# the SAME double-talk logic as the AEC3 gate, but driven by our own RELIABLE
# render clock (audio_playback_remaining_s) instead of a loopback far-reference —
# so it has none of the loopback onset-blind-window / clock-drift problems that
# made the AEC3 gate fragile. While the assistant is playing, a mic frame is
# forwarded only when its RMS clearly exceeds the adaptive residual-echo floor
# (genuine double-talk / real barge-in); otherwise it is withheld so residual
# echo can never reach the server. When the assistant is silent the gate is
# fully open — normal user turns are never touched. Disable via
# REALTIME_OS_DSP_RESIDUAL_GATE=0.
_OS_DSP_GATE_ENABLED = (
    os.environ.get("REALTIME_OS_DSP_RESIDUAL_GATE", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)
# Absolute near-end RMS floor a frame must clear to count as real speech while
# the assistant is playing (safety net for when the residual floor is ~0).
_OS_DSP_GATE_MIN_RMS = _env_float(
    "REALTIME_OS_DSP_GATE_MIN_RMS", 300.0, minimum=80.0
)
# A frame must also exceed the adaptive residual-echo floor by this ratio. The
# adaptive floor is what makes the gate device-agnostic: it tracks whatever the
# real residual level is on this machine, and genuine speech is many times louder.
_OS_DSP_GATE_FLOOR_RATIO = _env_float(
    "REALTIME_OS_DSP_GATE_FLOOR_RATIO", 3.0, minimum=1.2
)
# Require this many consecutive above-threshold frames before opening, so a
# single transient residual spike can never open the gate. Held onset frames sit
# in the preroll and flush intact on confirm, so no real audio is lost.
_OS_DSP_GATE_CONSEC_FRAMES = _env_int(
    "REALTIME_OS_DSP_GATE_CONSEC_FRAMES", 2, minimum=1, maximum=20
)
# Keep the gate open this long after the last speech frame (natural pauses).
_OS_DSP_GATE_HANGOVER_S = _env_float(
    "REALTIME_OS_DSP_GATE_HANGOVER_S", 0.8, minimum=0.0, maximum=3.0
)
# Hold the suppression window this long after the render clock goes idle, to
# absorb the short decaying echo tail of the assistant's final words.
_OS_DSP_GATE_COOLDOWN_S = _env_float(
    "REALTIME_OS_DSP_GATE_COOLDOWN_S", 0.35, minimum=0.0, maximum=2.0
)
# Preroll flushed when the gate opens so a real barge-in's first syllable is kept.
_OS_DSP_GATE_PREROLL_S = _env_float(
    "REALTIME_OS_DSP_GATE_PREROLL_S", 0.3, minimum=0.0, maximum=0.6
)

# Live on-screen captions WHILE the user speaks. OpenAI's input transcription
# only runs AFTER end-of-turn (post-commit), so it can't show words mid-speech.
# To fill that gap we run the on-device Vosk model (the same one used for wake
# word) on the outgoing mic PCM in a side thread and stream its partial
# hypotheses to the transcript bubble. These are DISPLAY-ONLY and get replaced
# by OpenAI's accurate transcript once the turn commits; the model itself never
# uses them (it responds directly from audio). Disable via REALTIME_LIVE_CAPTION=0.
_REALTIME_LIVE_CAPTION = (
    os.environ.get("REALTIME_LIVE_CAPTION", "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)

# --- Turn speech-evidence layer (transcript validation + interrupt gating) ----
# Structural fix for phantom transcripts: the transcription model (a Whisper
# family model) HALLUCINATES short tokens ("it" / "hello" / ".") when the
# server VAD commits a turn that contained no real speech — residual echo,
# room noise, a breath. No amount of per-engine gate tuning can fully prevent
# such commits, so we attach acoustic ground truth to every turn instead:
# every frame actually sent up the wire is scored by a local VAD (adaptive
# noise floor + the WebRTC APM's spectral speech probability when the AEC3
# engine is live), and per-turn speech evidence is accumulated. A final
# transcript is accepted ONLY when the turn's audio actually carried
# speech-like energy; otherwise it is dropped as an ASR hallucination and the
# auto-created response is cancelled. The same evidence stream gates
# interruptions: a server speech_started only stops playback when the uplink
# recently carried speech-like audio. This layer is engine-agnostic — it
# observes the single uplink choke point, so it protects the OS-DSP, AEC3,
# Speex and raw-mic paths identically, while the assistant is speaking AND
# while it is idle. Disable via REALTIME_TURN_EVIDENCE=0.
#
# DESKTOP DEFAULT: OFF. The shipping Windows EXE — the fast, interactive
# baseline the product is measured against — ran the server-driven model
# (semantic_vad with create_response AND interrupt_response both true): the
# server detected end-of-turn and generated the reply automatically. The
# client-authority evidence layer (a manual response.create per turn plus
# phantom excision) was added after the EXE and regressed responsiveness — when
# the per-turn response.create stalls, the model never replies, the uplink goes
# quiet, and the server closes the socket on a silence timeout mid-task
# (observed: draft an email, ask for a change, then 75 s of dead air and a
# server close). Half-duplex mic-muting during playback + Speex AEC already
# suppress the echo phantoms this layer was built to catch, so on desktop we
# default back to the EXE's server-driven turn-taking. The appliance (far-field
# USB array, no chassis coupling) keeps the evidence layer on. Opt back in on
# desktop with REALTIME_TURN_EVIDENCE=1.
_TURN_EVIDENCE_ENABLED = (
    os.environ.get("REALTIME_TURN_EVIDENCE", "0" if IS_DESKTOP else "1").strip().lower()
    not in ("0", "false", "no", "off", "")
)
# Absolute RMS a frame must reach to ever count as speech. Deliberately LOW
# (well under the residual gates' floors) so quiet-but-genuine speech still
# registers as evidence — the adaptive floor ratio does the heavy lifting.
_EVIDENCE_MIN_RMS = _env_float(
    "REALTIME_EVIDENCE_MIN_RMS", 180.0 if IS_DESKTOP else 260.0, minimum=50.0
)
# A frame must also exceed the adaptive ambient-noise floor by this ratio.
_EVIDENCE_FLOOR_RATIO = _env_float(
    "REALTIME_EVIDENCE_FLOOR_RATIO", 2.5, minimum=1.2
)
# WebRTC APM spectral speech-probability bands (AEC3 path only): >= hi
# upgrades a frame to speech even at modest energy; <= lo vetoes a
# borderline-energy frame (residual echo bursts score near zero).
_EVIDENCE_PROB_HI = _env_float("REALTIME_EVIDENCE_PROB_HI", 0.70, minimum=0.5, maximum=1.0)
_EVIDENCE_PROB_LO = _env_float("REALTIME_EVIDENCE_PROB_LO", 0.20, minimum=0.0, maximum=0.5)
# A turn whose uploaded audio carried less than this much speech-like signal
# cannot have produced ANY genuine transcript — drop whatever text came back.
_EVIDENCE_HARD_MIN_SPEECH_MS = _env_float(
    "REALTIME_EVIDENCE_HARD_MIN_SPEECH_MS", 60.0, minimum=0.0, maximum=1000.0
)
# Short fragments (<= _EVIDENCE_SHORT_MAX_WORDS words) are the classic
# hallucination shape; they additionally require this much speech evidence.
# A genuinely spoken one-word answer ("yes", "confirm") carries 300+ ms of
# voiced audio; acoustic transients (keyboard clacks, coughs, echo bursts)
# that the ASR mislabels as words sit well under 200 ms.
_EVIDENCE_SHORT_MIN_SPEECH_MS = _env_float(
    "REALTIME_EVIDENCE_SHORT_MIN_SPEECH_MS", 250.0, minimum=0.0, maximum=2000.0
)
_EVIDENCE_SHORT_MAX_WORDS = _env_int(
    "REALTIME_EVIDENCE_SHORT_MAX_WORDS", 2, minimum=1, maximum=6
)
# While the assistant's own audio is playing (or just stopped), uplink frames
# ride on residual echo the AEC could not fully remove — RMS alone cannot
# distinguish the assistant's leaked voice from the user's. Such frames only
# count as speech evidence when they clearly DOMINATE the echo: at or above
# this absolute RMS AND above the learned residual-echo envelope by the ratio
# below. Genuine barge-in (the user talking over the assistant) passes both;
# echo bursts leaking through the AEC do not.
_EVIDENCE_PLAYBACK_MIN_RMS = _env_float(
    "REALTIME_EVIDENCE_PLAYBACK_MIN_RMS", 1500.0, minimum=200.0
)
_EVIDENCE_PLAYBACK_FLOOR_RATIO = _env_float(
    "REALTIME_EVIDENCE_PLAYBACK_FLOOR_RATIO", 2.5, minimum=1.2
)
# Echo-risk hangover after playback stops: covers the acoustic decay tail and
# playback-clock skew (the clock can read zero a moment before the speaker
# actually goes quiet). Frames inside this window still face the playback bar.
_EVIDENCE_PLAYBACK_HANGOVER_S = _env_float(
    "REALTIME_EVIDENCE_PLAYBACK_HANGOVER_S", 1.0, minimum=0.0, maximum=5.0
)
# While assistant audio is playing, a server speech_started only hard-stops
# playback when the uplink carried speech evidence within this window.
_EVIDENCE_INTERRUPT_WINDOW_S = _env_float(
    "REALTIME_EVIDENCE_INTERRUPT_WINDOW_S", 1.5, minimum=0.3, maximum=5.0
)
# How far before the server's speech_started timestamp the evidence window
# starts (covers VAD prefix padding + event network latency).
_EVIDENCE_TURN_LOOKBACK_S = _env_float(
    "REALTIME_EVIDENCE_TURN_LOOKBACK_S", 1.0, minimum=0.0, maximum=5.0
)

# --- Audio pipeline debug taps -------------------------------------------------
# When REALTIME_AUDIO_DEBUG_DIR is set, the session records two WAV files per
# session: the raw mic capture (pre-resample, at the native rate) and the
# exact 24 kHz uplink audio sent to OpenAI (post-AEC, post-gates). Combined
# with the VOICE_EVENT turn audits this makes "why did it transcribe X?"
# measurable offline instead of subjective. Capped to keep disk bounded.
_AUDIO_DEBUG_DIR = os.environ.get("REALTIME_AUDIO_DEBUG_DIR", "").strip()
_AUDIO_DEBUG_MAX_S = _env_float("REALTIME_AUDIO_DEBUG_MAX_S", 300.0, minimum=10.0)


# ---------------------------------------------------------------------------
# Farewell detection — only consulted on COMPLETED user transcripts
# ---------------------------------------------------------------------------

_PUNCT_TO_SPACE = str.maketrans({c: " " for c in string.punctuation})


def _normalize_words(text: str) -> str:
    """Lowercase, strip all punctuation, collapse whitespace."""
    return " ".join((text or "").lower().translate(_PUNCT_TO_SPACE).split())


# Client-only tool the model can invoke when it judges that the
# conversation has wrapped up (e.g. user said "bye", "thats it",
# "thanks goodbye", "done for now" in a closing context). Unlike a
# keyword check, this lets the model use context — saying "bye" in
# the middle of a sentence about a person ("tell Bob bye for me")
# will NOT trigger end-of-session.
END_SESSION_TOOL: dict = {
    "type": "function",
    "name": "end_session",
    "description": (
        "Call this tool to close the voice session when the user "
        "clearly signals that the conversation is over. Examples of "
        "intent to end: 'bye', 'goodbye', \"that's it\", \"that's all\", "
        "'done for now', 'thanks bye', 'nothing else', \"I'm done\", "
        "'stop', 'exit'. Do NOT call it when the user says any of "
        "these words as part of an unrelated thought (e.g. 'tell "
        "Bob goodbye from me', 'no I'm not done yet, also...'). "
        "Always say a brief friendly closing in your response BEFORE "
        "calling this tool."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

START_RECORDING_TOOL: dict = {
    "type": "function",
    "name": "start_recording",
    "description": (
        "Call this tool when the user asks to start recording or taking notes. "
        "Use recording_mode='meeting' for 'start recording', 'record', 'record this', "
        "'start meeting', 'record a meeting', or 'begin recording' -- the word 'record' "
        "or 'recording' alone always means a meeting recording. Use recording_mode='note' "
        "ONLY when the user explicitly asks to take or make notes, such as 'take a note', "
        "'take notes', 'note this down', 'capture thoughts', or 'make a todo list'. "
        "When unsure, use 'meeting'. "
        "CRITICAL: Capture the CONTEXT the user gave before recording — who they "
        "are meeting, what it's about, the event/project/purpose — and pass it in "
        "the context fields below, EVEN IF those details are not repeated once "
        "recording starts. This is what makes the recording findable later. "
        "Example: 'I'm meeting Vivek now, start recording' -> recording_mode='meeting', "
        "referenced_people=['Vivek'], session_intent='meeting with Vivek'. "
        "Example: 'take notes, this is for the board meeting' -> recording_mode='note', "
        "referenced_events=['board meeting'], session_intent='notes for the board meeting'. "
        "Always say a brief confirmation (e.g. 'Starting the recording now') "
        "BEFORE calling this tool. The voice session will close and recording "
        "will begin immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "recording_mode": {
                "type": "string",
                "enum": ["meeting", "note"],
                "description": "meeting for meeting summary flow; note for note/todo extraction flow.",
            },
            "session_intent": {
                "type": "string",
                "description": "One sentence on what this recording is for, from what the user said before recording (e.g. 'meeting with Vivek', 'notes for the board meeting').",
            },
            "referenced_people": {
                "type": "array", "items": {"type": "string"},
                "description": "People the user mentioned (attendees / who the meeting or note is about), even if not spoken during the recording.",
            },
            "referenced_topics": {
                "type": "array", "items": {"type": "string"},
                "description": "Topics/subjects the user mentioned before recording.",
            },
            "referenced_projects": {
                "type": "array", "items": {"type": "string"},
                "description": "Named projects/initiatives mentioned (e.g. 'Project Atlas').",
            },
            "referenced_events": {
                "type": "array", "items": {"type": "string"},
                "description": "Events the recording relates to (e.g. 'board meeting', 'investor call', 'client review').",
            },
            "referenced_organizations": {
                "type": "array", "items": {"type": "string"},
                "description": "Companies/teams/organizations mentioned.",
            },
        },
        "required": [],
    },
}


_START_CONTEXT_LIST_KEYS = (
    "referenced_people",
    "referenced_topics",
    "referenced_projects",
    "referenced_events",
    "referenced_organizations",
)


def _extract_start_context(parsed_args: dict) -> dict:
    """Pull the pre-recording context fields out of a start_recording tool call."""
    if not isinstance(parsed_args, dict):
        return {}
    out: dict = {}
    intent = str(parsed_args.get("session_intent") or "").strip()
    if intent:
        out["session_intent"] = intent[:500]
    for key in _START_CONTEXT_LIST_KEYS:
        val = parsed_args.get(key)
        if isinstance(val, str):
            val = [v.strip() for v in val.split(",")]
        if isinstance(val, (list, tuple)):
            cleaned = [str(v).strip() for v in val if str(v or "").strip()]
            if cleaned:
                out[key] = cleaned
    return out


_FAREWELL_EXACT = frozenset({
    "bye", "bye bye", "goodbye", "good bye",
    "okay bye", "ok bye", "alright bye",
    "thanks", "thank you", "thanks bye", "thank you bye",
    "im done", "i am done", "i'm done", "all done",
    "thats all", "that's all", "thats all for now", "that's all for now",
    "thats it", "that's it", "done for now",
    "stop", "stop now", "shut up", "stop talking",
    "be quiet", "quiet", "enough", "enough already",
    "thats enough", "that's enough",
    "we're done", "we are done", "were done",
    "end session", "end the session", "session over",
    "nothing else", "nothing more", "nothing else for now",
    "exit", "close", "close session",
})

_FAREWELL_END_MARKERS = (
    "bye", "goodbye", "good bye", "okay bye", "ok bye", "alright bye",
    "thanks bye", "thank you bye",
    "thats all", "that's all", "thats all for now", "that's all for now",
    "thats it", "that's it",
    "im done", "i'm done", "i am done", "all done",
    "we're done", "we are done", "were done",
    "end session", "end the session", "session over",
    "nothing else", "nothing more",
)


def _is_farewell(text: str) -> bool:
    t = _normalize_words(text)
    if not t:
        return False
    if t in _FAREWELL_EXACT:
        return True
    return any(t.endswith(end) for end in _FAREWELL_END_MARKERS)


_MORNING_BRIEF_MARKERS = (
    "morning brief",
    "morning briefing",
    "borning brief",
    "daily brief",
    "daily briefing",
    "todays briefing",
    "today briefing",
    "start of day",
    "morning update",
    "daily update",
    "what does my day look like",
)


def _is_morning_brief_request(text: str) -> bool:
    t = _normalize_words(text)
    if not t:
        return False
    return any(marker in t for marker in _MORNING_BRIEF_MARKERS)


# Server-side errors that are common during normal flow races and must
# NEVER terminate the session — only protocol/auth failures will close
# the underlying WebSocket and bubble up through the async exception
# handler.
_SAFE_TO_IGNORE_ERRORS = (
    "cancellation failed",
    "no active response",
    "truncation failed",
    "conversation item not found",
    "missing required parameter",
    "unknown parameter",
    "invalid value",
    "active response in progress",
    "already has an active response",
    "wait until the response is finished",
)


# ---------------------------------------------------------------------------
# Module-level helpers (exported for tests and main.py)
# ---------------------------------------------------------------------------

def build_realtime_websocket_url(model: str) -> str:
    """Return the OpenAI Realtime WebSocket URL for a given model id."""
    m = (model or "").strip() or "gpt-realtime-2"
    return f"wss://{_REALTIME_WS_HOST}/v1/realtime?model={quote(m, safe='')}"


def extract_realtime_output_voice(session: dict | None) -> str:
    """Read audio.output.voice from the session blob the server returned."""
    if not isinstance(session, dict):
        return ""
    audio = session.get("audio")
    if not isinstance(audio, dict):
        return ""
    out = audio.get("output")
    if not isinstance(out, dict):
        return ""
    v = out.get("voice")
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return ""


def resample_pcm16_mono(data: bytes, src_sr: int, dst_sr: int) -> bytes:
    """Linear-resample mono int16 PCM bytes to the target sample rate."""
    if src_sr == dst_sr or not data:
        return data
    s = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    n_src = int(s.shape[0])
    if n_src < 2:
        return data
    dur = n_src / float(src_sr)
    n_dst = max(1, int(dur * dst_sr))
    x_src = np.linspace(0.0, dur, num=n_src, endpoint=False)
    x_dst = np.linspace(0.0, dur, num=n_dst, endpoint=False)
    out = np.interp(x_dst, x_src, s)
    return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


class _AntiAliasResampler:
    """Stateful mono int16 resampler with anti-aliasing for DOWNsampling.

    The appliance's USB mic captures at/below 24 kHz, so it only ever
    UPsamples (16k->24k) — linear interpolation is fine there. A desktop mic
    captures at 32/44.1/48 kHz, so reaching the 24 kHz Realtime rate requires
    DOWNsampling. Plain linear interpolation has no anti-alias filter, so
    energy above the 12 kHz target Nyquist folds back into the speech band as
    hiss/garble and wrecks transcription. We pre-filter with a windowed-sinc
    FIR low-pass whose history is carried across chunks (no per-block edge
    clicks), then interpolate. Used on desktop only.
    """

    def __init__(self, src_sr: int, dst_sr: int, taps: int = 64) -> None:
        self.src_sr = int(src_sr)
        self.dst_sr = int(dst_sr)
        self._needs_aa = self.dst_sr < self.src_sr
        self._h = None
        self._hist = None
        if self._needs_aa:
            cutoff = 0.45 * self.dst_sr  # Hz, just under the target Nyquist
            n = np.arange(taps) - (taps - 1) / 2.0
            h = np.sinc(2.0 * cutoff / self.src_sr * n) * np.hamming(taps)
            self._h = (h / np.sum(h)).astype(np.float32)
            self._hist = np.zeros(taps - 1, dtype=np.float32)

    def process(self, pcm16: bytes) -> bytes:
        if not pcm16 or self.src_sr == self.dst_sr:
            return pcm16
        x = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if x.size < 2:
            return pcm16
        if self._needs_aa:
            buf = np.concatenate([self._hist, x])
            x = np.convolve(buf, self._h, mode="valid")  # length == x.size
            self._hist = buf[-(self._h.size - 1):]
        n_src = x.size
        dur = n_src / float(self.src_sr)
        n_dst = max(1, int(dur * self.dst_sr))
        x_src = np.linspace(0.0, dur, num=n_src, endpoint=False)
        x_dst = np.linspace(0.0, dur, num=n_dst, endpoint=False)
        out = np.interp(x_dst, x_src, x)
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


class _UplinkSpeechMonitor:
    """Acoustic speech-evidence tracker for the audio actually sent to the server.

    Observes every frame at the uplink choke point (post-AEC, post-gates) and
    scores it against an adaptive ambient-noise floor, optionally refined by
    the WebRTC APM's spectral speech probability. The per-frame decisions are
    retained in a short rolling window so the session can ask two questions
    with real acoustic ground truth:

      * ``stats_since(ts)`` — how much speech-like audio did the current turn
        actually carry? Used to validate final transcripts: a committed turn
        with (near-)zero speech evidence cannot have produced a genuine
        transcript, so whatever text the ASR returned is a hallucination.
      * ``recent_speech(window)`` — did the uplink carry speech-like audio in
        the immediate past? Used to gate interruptions: a server
        ``speech_started`` that no locally-observed speech can explain is an
        echo/noise artifact and must not stop playback.

    Echo awareness: RMS against the ambient floor is meaningless while the
    assistant's own audio is playing — residual echo that survives the AEC is
    energetic AND spectrally speech-like (it IS a voice: the assistant's).
    Frames observed during playback (plus a decay hangover) therefore face a
    dedicated bar: they must reach an absolute barge-in level and dominate a
    separately-learned residual-echo envelope. This is the double-talk
    principle: only near-end audio that overpowers the echo path counts.

    Engine-agnostic by design: identical behavior on the OS-DSP, AEC3, Speex
    and raw-mic paths, while the assistant is speaking and while it is idle.
    Single-threaded (asyncio loop) — no locking needed.
    """

    def __init__(
        self,
        *,
        rate: int = _REALTIME_RATE,
        min_rms: float = _EVIDENCE_MIN_RMS,
        floor_ratio: float = _EVIDENCE_FLOOR_RATIO,
        prob_hi: float = _EVIDENCE_PROB_HI,
        prob_lo: float = _EVIDENCE_PROB_LO,
        playback_min_rms: float = _EVIDENCE_PLAYBACK_MIN_RMS,
        playback_floor_ratio: float = _EVIDENCE_PLAYBACK_FLOOR_RATIO,
        retain_s: float = 12.0,
    ) -> None:
        self._rate = int(rate)
        self._min_rms = float(min_rms)
        self._floor_ratio = float(floor_ratio)
        self._prob_hi = float(prob_hi)
        self._prob_lo = float(prob_lo)
        self._playback_min_rms = float(playback_min_rms)
        self._playback_floor_ratio = float(playback_floor_ratio)
        self._retain_s = float(retain_s)
        self._noise_floor = 0.0
        # Envelope of residual echo observed while the assistant plays: a
        # decaying peak-tracker, learned only from playback-time frames that
        # did NOT qualify as speech. Genuine barge-in must beat it.
        self._echo_env = 0.0
        self._last_speech_at = 0.0
        # (monotonic_ts, duration_ms, rms, is_speech, echo_risk) per frame.
        self._frames: deque[tuple[float, float, float, bool, bool]] = deque()

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    @property
    def echo_env(self) -> float:
        return self._echo_env

    @property
    def last_speech_at(self) -> float:
        return self._last_speech_at

    def observe(
        self,
        pcm16: bytes,
        *,
        speech_prob: float | None = None,
        echo_risk: bool = False,
        echo_active: bool = False,
        now: float | None = None,
    ) -> bool:
        """Score one uplink frame; returns True when it looks like speech.

        ``echo_risk`` marks frames captured while the assistant's audio was
        playing (or within its decay hangover): they must clear the stricter
        playback bar before counting as speech evidence.

        ``echo_active`` marks frames captured while the speaker is ACTUALLY
        emitting audio right now (playback clock / loopback says so). Only
        these frames may teach the residual-echo envelope: during the
        post-playback hangover there is no echo source, so any energy is
        ambient or the user — learning it into the envelope would teach the
        envelope the user's own voice and lock them out.
        """
        if not pcm16:
            return False
        echo_risk = echo_risk or echo_active
        samples = np.frombuffer(pcm16, dtype=np.int16)
        if samples.size == 0:
            return False
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
        dur_ms = (len(pcm16) / 2.0) / self._rate * 1000.0
        floor = self._noise_floor
        threshold = max(self._min_rms, floor * self._floor_ratio)
        if echo_risk:
            # Double-talk bar: absolute barge-in level AND dominance over the
            # learned residual-echo envelope. Residual echo tracks its own
            # envelope by definition, so it can never dominate it.
            threshold = max(
                threshold,
                self._playback_min_rms,
                self._echo_env * self._playback_floor_ratio,
            )
        is_speech = rms >= threshold
        if speech_prob is not None and not echo_risk:
            # Spectral refinement (AEC3 path), idle only: confident speech
            # upgrades a modest-energy frame; confident non-speech vetoes a
            # borderline energy spike. During playback the residual echo is
            # itself a voice, so the spectral score cannot arbitrate and the
            # energy dominance test above stands alone.
            if speech_prob >= self._prob_hi and rms >= (self._min_rms * 0.5):
                is_speech = True
            elif speech_prob <= self._prob_lo and rms < (threshold * 2.0):
                is_speech = False
        ts = time.monotonic() if now is None else now
        if is_speech:
            self._last_speech_at = ts
        elif echo_active:
            # Learn the residual-echo envelope ONLY from frames captured while
            # the speaker is truly emitting: fast attack (echo bursts raise it
            # immediately), slow release (it outlasts gaps between TTS words).
            if rms > self._echo_env:
                self._echo_env = rms
            else:
                self._echo_env = (self._echo_env * 0.98) + (rms * 0.02)
        elif echo_risk:
            # Hangover tail: the strict bar still applies, but there is no
            # echo source anymore — do not learn this energy into the echo
            # envelope or the ambient floor (it may be the user starting to
            # talk, or echo decay; neither is a stable reference).
            self._echo_env *= 0.95
        else:
            # Adapt the ambient floor only from idle non-speech frames. Seed
            # conservatively (never above the absolute minimum) so a session
            # that opens mid-speech cannot poison the floor upward.
            if floor <= 0.0:
                self._noise_floor = min(rms, self._min_rms)
            else:
                self._noise_floor = (floor * 0.95) + (rms * 0.05)
            # The echo envelope decays toward ambience while the assistant
            # is silent so stale playback peaks don't gate the next turn.
            self._echo_env *= 0.95
        self._frames.append((ts, dur_ms, rms, is_speech, echo_risk))
        cutoff = ts - self._retain_s
        frames = self._frames
        while frames and frames[0][0] < cutoff:
            frames.popleft()
        return is_speech

    def stats_since(self, since_ts: float) -> dict:
        """Aggregate evidence for frames observed at/after ``since_ts``."""
        speech_ms = 0.0
        total_ms = 0.0
        peak_rms = 0.0
        echo_risk_ms = 0.0
        for ts, dur_ms, rms, is_speech, echo_risk in self._frames:
            if ts < since_ts:
                continue
            total_ms += dur_ms
            if is_speech:
                speech_ms += dur_ms
            if echo_risk:
                echo_risk_ms += dur_ms
            if rms > peak_rms:
                peak_rms = rms
        return {
            "speech_ms": round(speech_ms, 1),
            "total_ms": round(total_ms, 1),
            "peak_rms": round(peak_rms, 1),
            "noise_floor": round(self._noise_floor, 1),
            "echo_risk_ms": round(echo_risk_ms, 1),
            "echo_env": round(self._echo_env, 1),
        }

    def recent_speech(self, window_s: float, *, now: float | None = None) -> bool:
        """True when speech-like audio was observed within ``window_s``."""
        if self._last_speech_at <= 0.0:
            return False
        ts = time.monotonic() if now is None else now
        return (ts - self._last_speech_at) <= window_s

    def reset(self) -> None:
        self._frames.clear()
        self._noise_floor = 0.0
        self._echo_env = 0.0
        self._last_speech_at = 0.0


class _DebugWavTap:
    """Env-gated WAV recorder for offline pipeline debugging.

    Bounded (``_AUDIO_DEBUG_MAX_S``) so a long session can never fill the
    disk; failures degrade to a no-op and never touch the audio path.
    """

    def __init__(self, path: str, rate: int, max_seconds: float = _AUDIO_DEBUG_MAX_S) -> None:
        self._rate = int(rate)
        self._max_bytes = int(rate * 2 * max_seconds)
        self._written = 0
        self._wf = None
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            wf = wave.open(path, "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._rate)
            self._wf = wf
            logger.info("Realtime audio debug tap: recording %s", path)
        except Exception:
            logger.debug("debug wav tap open failed: %s", path, exc_info=True)
            self._wf = None

    def write(self, pcm16: bytes) -> None:
        wf = self._wf
        if wf is None or not pcm16 or self._written >= self._max_bytes:
            return
        try:
            wf.writeframes(pcm16)
            self._written += len(pcm16)
        except Exception:
            self._wf = None

    def close(self) -> None:
        wf = self._wf
        self._wf = None
        if wf is not None:
            try:
                wf.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# RealtimeVoiceSession
# ---------------------------------------------------------------------------

class RealtimeVoiceSession:
    """OpenAI Realtime WebSocket + mic capture on a background thread.

    Public API expected by main.py:
        .start()                   -- spawn the background thread
        .stop()                    -- shut everything down
        .ended_unexpectedly()      -- True iff session ended without user intent
    Callbacks (all marshalled onto the Kivy main thread):
        on_session_end()
        on_error(msg: str)
        on_connected()
        on_device_navigate(screen: str)   [optional]
        on_before_open_mic()              [optional, runs on worker thread]
        on_state_change(state: str)       [optional]
    """

    def __init__(
        self,
        *,
        client_secret: str,
        model: str,
        backend_base_url: str,
        device_token: str,
        on_session_end,
        on_error,
        on_connected,
        on_device_navigate=None,
        output_voice: str | None = None,
        on_before_open_mic=None,
        on_state_change=None,
        on_user_transcript=None,
        on_ai_transcript=None,
        on_ai_transcript_delta=None,
        on_user_speech_stopped=None,
        on_user_speech_started=None,
        on_user_transcript_rejected=None,
        on_email_draft=None,
        on_email_view=None,
        on_recipient_picker=None,
        on_task_creation=None,
        on_task_dismiss=None,
        on_calendar_event=None,
        on_calendar_event_dismiss=None,
        on_start_recording=None,
        should_suppress_farewell=None,
        brief_data_provider=None,
        prewarm: bool = False,
        vosk_model=None,
    ):
        # Warm-standby: when True, connect + run the session.update handshake
        # but HOLD (no mic, no audio, no greeting) until activate() is called on
        # wake. Removes the per-wake mint + WS-connect + prefill from the felt
        # latency path. Cold sessions (prewarm=False) behave exactly as before.
        self._prewarm = bool(prewarm)
        self._activate_event: asyncio.Event | None = None
        self._activate_requested = False
        self._session_update_sent = False
        self._client_secret = (client_secret or "").strip()
        self._model = (model or "").strip()
        self._backend_base_url = (backend_base_url or "").strip()
        self._device_token = (device_token or "").strip()
        self._on_session_end_cb = on_session_end
        self._on_error_cb = on_error
        self._on_connected_cb = on_connected
        self._on_device_navigate_cb = on_device_navigate
        self._on_before_open_mic_cb = on_before_open_mic
        self._on_state_change_cb = on_state_change
        self._on_user_transcript_cb = on_user_transcript
        self._on_ai_transcript_cb = on_ai_transcript
        self._on_ai_transcript_delta_cb = on_ai_transcript_delta
        self._on_user_speech_stopped_cb = on_user_speech_stopped
        self._on_user_speech_started_cb = on_user_speech_started
        self._on_user_transcript_rejected_cb = on_user_transcript_rejected
        self._on_email_draft_cb = on_email_draft
        self._on_email_view_cb  = on_email_view
        self._on_recipient_picker_cb = on_recipient_picker
        self._on_task_creation_cb = on_task_creation
        self._on_task_dismiss_cb = on_task_dismiss
        self._on_calendar_event_cb = on_calendar_event
        self._on_calendar_event_dismiss_cb = on_calendar_event_dismiss
        self._on_start_recording_cb = on_start_recording
        # Optional predicate: when it returns True the aggressive keyword-based
        # client-side farewell close is skipped (e.g. while an email draft is
        # on screen) and we defer to the model's contextual end_session tool.
        self._should_suppress_farewell_cb = should_suppress_farewell
        # Optional provider returning the morning-brief facts currently rendered
        # on screen, so the per-section narration speaks the exact same data.
        self._brief_data_provider = brief_data_provider
        self._output_voice = (
            (output_voice or "").strip().lower()
            or _REALTIME_OUTPUT_VOICE_FALLBACK
        )

        # Resolve audio device pair (combined USB mic+speaker detection).
        # Done once at init so aplay and the mic stream use a consistent device.
        try:
            from audio_device_resolve import resolve_audio_pair
            self._audio_pair = resolve_audio_pair(sd)
        except Exception:
            logger.exception("AudioPair: resolution failed — using system defaults")
            from audio_device_resolve import AudioDevicePair
            self._audio_pair = AudioDevicePair()

        # Decide duplex mode from the resolved hardware (see
        # _REALTIME_HALF_DUPLEX_ENV). Only an echo-isolated combined external
        # mic+speaker puck (is_combined) is safe for full-duplex voice barge-in;
        # everything else (built-in mic, or mic-only external + built-in
        # speaker) is acoustically coupled and must run half-duplex.
        if _REALTIME_HALF_DUPLEX_ENV in ("1", "true", "yes", "on"):
            self._half_duplex = True
        elif _REALTIME_HALF_DUPLEX_ENV in ("0", "false", "no", "off"):
            self._half_duplex = False
        else:
            self._half_duplex = not bool(
                getattr(self._audio_pair, "is_combined", False)
            )
        logger.info(
            "Realtime duplex mode: %s (audio pair is_combined=%s)",
            "half-duplex (local barge-in detector on)" if self._half_duplex
            else "full-duplex (voice barge-in on)",
            getattr(self._audio_pair, "is_combined", False),
        )
        self._log_voice_event(
            "audio_route",
            capture=getattr(self._audio_pair, "capture_name", None) or str(getattr(self._audio_pair, "capture", "")),
            playback=getattr(self._audio_pair, "playback_name", None) or str(getattr(self._audio_pair, "playback", "")),
            is_combined=bool(getattr(self._audio_pair, "is_combined", False)),
            half_duplex=self._half_duplex,
        )

        # Echo-decay tail: keep the mic muted this long AFTER the assistant's
        # queued audio finishes, so residual room echo doesn't reopen the
        # uplink. Longer in half-duplex (built-in mic) where coupling is worse.
        # Desktop opens the uplink on server VAD speech_started (below), so a
        # long echo-decay tail mostly just clips the start of the user's next
        # turn. Keep it short on desktop; the appliance keeps its tuned value.
        if IS_DESKTOP:
            _default_tail = 0.5
        else:
            _default_tail = 1.0 if self._half_duplex else 0.6
        try:
            self._mic_reopen_tail_s = float(
                os.environ.get("REALTIME_MIC_TAIL_S", "") or _default_tail
            )
        except ValueError:
            self._mic_reopen_tail_s = _default_tail

        # Worker thread + asyncio loop
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        # Device-driven morning-brief walkthrough state.
        self._brief_active = False
        self._brief_idx = 0
        self._brief_task = None  # asyncio.Task scheduling the next section
        self._brief_start_task = None  # asyncio.Task starting after auto-response cancel
        self._brief_start_pending = False
        self._brief_narration_audio_seen = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._connected_fired = False
        self._user_ended = False

        # Mic input.
        # Buffer only ~2 s of mic audio (drop-oldest when full — see
        # _mic_callback). A deeper buffer (the old 8 s) is actively harmful for
        # a realtime stream: when the uplink briefly can't keep up it lets the
        # backlog snowball into a multi-second STALE burst that never catches
        # up, so the server hears a growing gap and closes the session. Keeping
        # the queue shallow means a slow link drops the oldest frames and we
        # always send near-live audio.
        self._audio_q: queue.Queue[bytes | None] = queue.Queue(maxsize=100)
        self._mic_stream = None
        self._mic_native_sr = _REALTIME_RATE
        # Uplink transcode (24 kHz PCM16 internal -> G.711 mu-law 8 kHz on the
        # wire) when REALTIME_UPLINK_CODEC=g711_ulaw. Stateful anti-alias
        # downsampler, created once per session; None when sending raw PCM16.
        _g711_requested = _REALTIME_UPLINK_CODEC in ("g711_ulaw", "g711", "pcmu", "ulaw")
        self._uplink_g711 = _g711_requested and _audioop is not None
        if _g711_requested and _audioop is None:
            logger.warning(
                "Realtime uplink: g711_ulaw requested but 'audioop' is "
                "unavailable (Python 3.13+ without audioop-lts) — falling back "
                "to PCM16 uplink (higher bandwidth)."
            )
        self._uplink_resampler: _AntiAliasResampler | None = (
            _AntiAliasResampler(_REALTIME_RATE, _G711_RATE) if self._uplink_g711 else None
        )
        # Anti-aliased desktop downsampler (built lazily in _pump_mic once the
        # actual capture rate is known). None on the appliance / when no
        # resampling is needed.
        self._mic_resampler: _AntiAliasResampler | None = None
        # Desktop only: when the server VAD reports the user started talking,
        # open the gated uplink until this monotonic deadline so the utterance
        # is actually sent (fixes half-duplex withholding real speech).
        self._force_uplink_until = 0.0

        # Playback (aplay) — pipe writes go through a dedicated
        # single-thread executor. Writing on the asyncio loop would
        # block the heartbeat for seconds if aplay's 64 KB pipe fills
        # up, tripping ping_timeout and killing the session mid-reply.
        self._aplay_proc: subprocess.Popen | None = None
        self._aplay_pid: int | None = None
        self._aplay_writer = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rtv-aplay"
        )
        # Windows/macOS PortAudio playback sink (replaces the aplay pipe).
        self._win_player = None
        self._suppress_audio_until = 0.0
        # Playback clock. Realtime audio deltas often arrive faster than aplay
        # can speak them, so timing UI transitions from the last chunk alone is
        # too early. Track the cumulative queued audio end instead.
        self._playback_clock_lock = threading.Lock()
        self._assistant_audio_play_until = 0.0
        # Mic-mute window while assistant audio is still playing / echoing.
        self._mute_mic_uplink_until = 0.0
        preroll_frames = max(1, int((_LOCAL_BARGE_IN_PREROLL_S * 1000) / max(1, _APPEND_CHUNK_MS)))
        self._barge_in_preroll: deque[bytes] = deque(maxlen=preroll_frames)
        self._barge_in_noise_rms = 0.0
        self._barge_in_consecutive = 0
        self._barge_in_last_cancel_at = 0.0
        self._audio_q_drops = 0

        # Acoustic echo canceller. The bytes we hand to aplay are also
        # buffered as the far-end reference; the mic stream (after resample
        # to 24 kHz) is the near-end. The canceller produces the
        # echo-suppressed mic signal we forward to OpenAI.
        try:
            from _aec import SpeexAEC, is_available as _aec_available
            if _aec_available():
                self._aec = SpeexAEC(
                    frame_size=480, filter_length=4800, sample_rate=_REALTIME_RATE
                )
                logger.info("Realtime AEC: speex echo canceller enabled")
            else:
                self._aec = None
                logger.warning(
                    "Realtime AEC: libspeexdsp not found — falling back to mic-mute"
                )
        except Exception:
            self._aec = None
            logger.exception("Realtime AEC: init failed — falling back to mic-mute")
        self._aec_frame_bytes = 480 * 2
        self._aec_far_buf = bytearray()
        self._aec_near_buf = bytearray()
        self._aec_buf_lock = threading.Lock()

        # OS-grade echo cancellation (Windows Voice Capture DSP) — the single
        # echo/barge-in solution on Windows. When it starts at session-open it
        # becomes the mic source itself (source mode) and supersedes the Speex
        # path entirely. Created here only as an instance; actually started in
        # the session-open path. On the appliance / non-Windows this stays None
        # and the Speex + local barge-in path above is used.
        self._os_aec = None
        self._os_aec_full_duplex = False
        try:
            if _OS_AEC_ENABLED and IS_DESKTOP:
                import windows_aec
                if windows_aec.is_available():
                    self._os_aec = windows_aec.WindowsEchoCanceller(
                        on_frames=self._on_os_aec_frames
                    )
                    logger.info(
                        "Realtime AEC: Windows Voice Capture DSP available "
                        "(will use OS echo cancellation, full-duplex)"
                    )
        except Exception:
            self._os_aec = None
            logger.debug("OS AEC init failed", exc_info=True)

        # Genuine WebRTC AEC3 — the preferred desktop echo path. We build the
        # engine and the OS far-end reference lazily at session-open (below);
        # here we only probe availability so the log/route reflect the real
        # decision. The engine runs at 48 kHz on its own near/far buffers and
        # the mic pump downsamples the cleaned output to the 24 kHz uplink.
        self._aec3 = None
        self._far_ref = None
        # True when the render tap in PcmStreamPlayer feeds the far reference
        # (device-paced ground truth); _play_delta must then not double-feed.
        self._far_ref_via_player = False
        self._aec3_full_duplex = False
        self._aec3_in_resampler: _AntiAliasResampler | None = None   # mic -> 48k
        self._aec3_out_resampler: _AntiAliasResampler | None = None  # 48k -> 24k
        # Residual-echo uplink gate state (AEC3 path).
        self._aec3_gate_open_until = 0.0
        self._aec3_far_active_until = 0.0
        self._aec3_residual_floor = 0.0
        self._aec3_recent_speech_evidence_until = 0.0
        self._aec3_gate_consecutive = 0
        self._aec3_gate_preroll: deque[bytes] = deque()
        self._aec3_gate_preroll_bytes = 0
        self._aec3_gate_suppressed = 0
        self._aec3_gate_last_log = 0.0
        # Residual-echo uplink gate state (Windows OS Voice Capture DSP path).
        self._os_dsp_gate_open_until = 0.0
        self._os_dsp_gate_active_until = 0.0
        self._os_dsp_gate_floor = 0.0
        self._os_dsp_gate_consecutive = 0
        self._os_dsp_gate_preroll: deque[bytes] = deque()
        self._os_dsp_gate_preroll_bytes = 0
        self._os_dsp_gate_suppressed = 0
        self._os_dsp_gate_last_log = 0.0
        self._webrtc_aec_available = False
        try:
            if not (_WEBRTC_AEC_ENABLED and IS_DESKTOP):
                logger.info(
                    "Realtime AEC: WebRTC AEC3 disabled (REALTIME_WEBRTC_AEC=%s, desktop=%s)",
                    _WEBRTC_AEC_ENABLED, IS_DESKTOP,
                )
            else:
                import webrtc_apm
                import aec_reference
                if not webrtc_apm.is_available():
                    logger.warning(
                        "Realtime AEC: WebRTC AEC3 module not importable (%s) — "
                        "falling back to OS DSP / Speex",
                        getattr(webrtc_apm, "import_error", None),
                    )
                elif not (aec_reference._IS_WIN or aec_reference._IS_MAC):
                    logger.info(
                        "Realtime AEC: WebRTC AEC3 present but no OS far-end "
                        "reference on this platform — falling back"
                    )
                else:
                    self._webrtc_aec_available = True
                    logger.info(
                        "Realtime AEC: WebRTC AEC3 engine available "
                        "(will use AEC3 + app render-feed far-end reference, "
                        "full-duplex)"
                    )
        except Exception:
            self._webrtc_aec_available = False
            logger.warning("WebRTC AEC3 availability probe failed", exc_info=True)

        # Unified per-turn speech-evidence tracker (see _UplinkSpeechMonitor).
        # Observes every uploaded frame; validates transcripts and gates
        # interruptions with real acoustic evidence on every engine path.
        self._speech_monitor: _UplinkSpeechMonitor | None = (
            _UplinkSpeechMonitor() if _TURN_EVIDENCE_ENABLED else None
        )
        # Monotonic timestamp of the most recent server speech_started event —
        # anchors the evidence window for the turn's transcript validation.
        # Also keyed per item_id so back-to-back turns cannot cross-contaminate
        # each other's evidence windows.
        self._turn_started_at = 0.0
        self._turn_started_by_item: dict[str, float] = {}
        # Evidence snapshot frozen at input_audio_buffer.committed, keyed by
        # item_id. Transcription completes 0.5-1.5 s AFTER the commit; if the
        # user has already begun their next utterance by then, a live
        # recomputation would count that new speech as evidence for the OLD
        # turn and accept a phantom. The commit-time snapshot is the turn's
        # true acoustic record — nothing after the commit belongs to it.
        self._turn_commit_stats: dict[str, dict] = {}
        # When the previous turn was committed: the current turn's evidence
        # window must never reach back past it (the lookback padding would
        # otherwise swallow the tail of the previous utterance).
        self._last_turn_commit_at = 0.0
        # Echo-risk hangover clock: uplink frames until this monotonic time
        # face the stricter playback evidence bar (see _uplink_echo_risk).
        self._playback_echo_risk_until = 0.0
        # Conversation items excised as phantoms (deleted server-side). Any
        # late transcription events for these items are dropped outright.
        self._phantom_items: set[str] = set()
        # Optional WAV debug taps (REALTIME_AUDIO_DEBUG_DIR); built in _pump_mic
        # once the native capture rate is known.
        self._debug_raw_tap: _DebugWavTap | None = None
        self._debug_uplink_tap: _DebugWavTap | None = None

        # Live caption (on-device Vosk partials while the user speaks). Enabled
        # only when the feature flag is on AND a preloaded Vosk model was handed
        # in (we reuse the wake-word model — no second copy in memory).
        self._vosk_model = vosk_model
        self._caption_enabled = bool(_REALTIME_LIVE_CAPTION and vosk_model is not None)
        self._caption_rec = None
        self._caption_q: queue.Queue | None = None
        self._caption_thread: threading.Thread | None = None
        self._caption_reset = threading.Event()
        self._caption_active = False  # True only between speech_started/stopped
        self._caption_text = ""        # finalized segments for the current utterance

        # Streaming buffer for AI audio transcript deltas. We flush it
        # on the matching .done event, or on response.done as a fallback
        # when the API never emits .done at all.
        self._ai_transcript_buf: str = ""
        # item_id (or response_id) of the AI response currently streaming.
        # The UI uses this to decide whether to update the existing AI
        # bubble or create a new one for a fresh response.
        self._active_ai_transcript_item_id: str = ""
        # Running buffer for the USER transcript while streaming partials
        # arrive. We
        # accumulate so the on-screen bubble shows the growing sentence
        # rather than only the latest fragment. Reset per utterance.
        self._user_transcript_buf: str = ""
        self._active_user_transcript_item_id: str = ""

        # Tools we received from the server in session.created. Cached so
        # we can re-send them in session.update with end_session appended.
        self._server_tools: list[dict] = []

        # Set once the wake-word greeting response.create has been emitted
        # for this session, so we never send it twice.
        self._wake_greeting_sent: bool = False

        # Active summary context (the user is viewing a meeting/note summary on
        # screen). When set before the wake greeting fires, the greeting path
        # injects this as a system message and speaks a summary-specific opener
        # instead of the generic "I'm listening" greeting. Applied via
        # apply_active_context() and torn down via clear_active_context().
        self._active_summary_context: str | None = None
        self._active_summary_greeting: str | None = None

        # State exposed to the UI / idle watchdog
        self._state = "idle"            # idle | listening | thinking | speaking
        self._response_in_progress = False
        # True while a server-side tool call's HTTP round-trip is in flight
        # (_handle_response_done -> invoke_realtime_tool_sync). During that
        # window the OpenAI server legitimately sends NOTHING (it is waiting
        # for our function_call_output), the recv loop is blocked awaiting the
        # tool result (so _last_server_event_at freezes), and pongs are known
        # to be slow — the dead-link watchdog must not treat this as a dead
        # socket (a 30s+ assistant_intent email send would be killed mid-write).
        self._tool_roundtrip_active = False
        # response.done handling (which includes the tool HTTP round-trip) runs
        # as a background task, NOT inline in _recv_loop: a slow tool call
        # (36s assistant_intent email send) would otherwise block frame
        # consumption, fill the websocket read buffer, pause the library's
        # reader, leave the server's keepalive pings unanswered and get the
        # connection killed BY OPENAI mid-send. The lock serializes handlers
        # so tool outputs are still delivered in response order.
        self._response_done_tasks: set = set()
        self._response_done_lock = asyncio.Lock()
        # Monotonic time we last sent response.create (see
        # _RESPONSE_CREATE_PROTECT_S). Guards the create->created ack gap.
        self._response_requested_at = 0.0
        # Monotonic time we last received ANY frame from the realtime WS.
        # Used to quantify mid-session stalls (loop starvation vs a truly
        # silent server) in the close-reason log and the loop-lag heartbeat.
        self._last_server_event_at = time.monotonic()
        # --- Uplink-health diagnostics (see _loop_lag_monitor heartbeat) ------
        # Distinguish the three stall classes we cannot tell apart from the
        # existing logs: (a) event loop blocked, (b) outbound WS send stalled
        # (network/backpressure — frames pile up in _audio_q, pongs can't go
        # out -> server ping-timeout), (c) mic callback died (queue goes dry).
        self._frames_sent = 0
        self._last_frame_sent_at = time.monotonic()
        self._max_send_dt = 0.0
        self._active_audio_item_id: str | None = None
        self._active_audio_content_index = 0
        self._last_activity_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        def _run() -> None:
            try:
                asyncio.run(self._async_main())
            except Exception:
                logger.exception("Realtime asyncio.run failed")
                self._emit_error("Realtime voice failed unexpectedly.")

        self._thread = threading.Thread(
            target=_run, name="realtime-voice", daemon=True
        )
        self._thread.start()

    def ended_unexpectedly(self) -> bool:
        """True if the session ended without user intent (WS drop, timeout)."""
        return not self._user_ended

    def activate(self) -> None:
        """Promote a pre-warmed (held) session to active: open the mic and
        start streaming. Safe to call from the Kivy main thread."""
        self._activate_requested = True
        # Reset the idle clock so the watchdog counts from the moment the
        # user actually wakes the session, not from when the warm standby
        # was first created (which could be 40+ seconds ago, causing the
        # watchdog to fire immediately on its first tick).
        self._last_activity_monotonic = time.monotonic()
        loop, ev = self._loop, self._activate_event
        if loop is not None and ev is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(ev.set)
            except Exception:
                pass

    def is_held(self) -> bool:
        """True iff this is a warm session that is connected and waiting to be
        activated (i.e. usable as an instant-response standby)."""
        return (
            self._prewarm
            and not self._activate_requested
            and self._ws is not None
            and not self._stop.is_set()
        )

    def stop(self) -> None:
        self._user_ended = True
        self._stop.set()
        self._cancel_briefing()
        try:
            self._audio_q.put_nowait(None)
        except Exception:
            pass
        loop, ws = self._loop, self._ws
        if loop and ws and not loop.is_closed():
            async def _close():
                try:
                    await ws.close()
                except Exception:
                    pass
            try:
                asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=3.0)
            except Exception:
                pass
        self._abort_aplay()
        self._close_mic()

    # ------------------------------------------------------------------
    # Callbacks (Kivy-thread-safe)
    # ------------------------------------------------------------------

    def _emit_error(self, msg: str) -> None:
        Clock.schedule_once(lambda _dt: self._safe_call(self._on_error_cb, msg), 0)

    def _emit_connected(self) -> None:
        Clock.schedule_once(lambda _dt: self._safe_call(self._on_connected_cb), 0)

    def _emit_session_end(self) -> None:
        Clock.schedule_once(lambda _dt: self._safe_call(self._on_session_end_cb), 0)

    def _emit_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        cb = self._on_state_change_cb
        if cb:
            Clock.schedule_once(lambda _dt: self._safe_call(cb, state), 0)

    def _emit_user_transcript(
        self, text: str, is_final: bool = True, item_id: str = ""
    ) -> None:
        cb = self._on_user_transcript_cb
        if cb and text:
            Clock.schedule_once(
                lambda _dt: self._safe_call(cb, text, is_final, item_id), 0
            )

    def _emit_ai_transcript(self, text: str) -> None:
        cb = self._on_ai_transcript_cb
        if cb and text:
            Clock.schedule_once(lambda _dt: self._safe_call(cb, text), 0)

    def _emit_ai_transcript_delta(self, item_id: str, accumulated: str) -> None:
        """Stream the running AI transcript text to the UI as it arrives.

        Lets the assistant bubble grow word-by-word in sync with the audio
        playback rather than appearing in one chunk after the response ends.
        """
        cb = self._on_ai_transcript_delta_cb
        if cb and accumulated:
            Clock.schedule_once(
                lambda _dt: self._safe_call(cb, item_id, accumulated), 0
            )

    def _emit_user_speech_stopped(self) -> None:
        """Fired the moment VAD decides the user has finished speaking.

        The UI uses this to drop in an instant placeholder bubble so the
        user gets visual confirmation right away, hiding the second or
        two needed for the transcription model to finish.
        """
        cb = self._on_user_speech_stopped_cb
        if cb:
            Clock.schedule_once(lambda _dt: self._safe_call(cb), 0)

    def _emit_user_speech_started(self) -> None:
        """Fired when VAD detects the user has begun a new utterance.

        The UI uses this to reset its per-utterance bubble trackers so live
        captions (and the final transcript) land in a fresh bubble.
        """
        cb = self._on_user_speech_started_cb
        if cb:
            Clock.schedule_once(lambda _dt: self._safe_call(cb), 0)

    def _emit_user_transcript_rejected(self, item_id: str = "") -> None:
        """Fired when a turn's transcript was rejected as a phantom.

        The UI uses this to remove the rejected turn's bubble — whether it
        is still the "…" placeholder from speech_stopped or was already
        painted by streaming partials — so a rejected turn leaves no trace
        in the transcript.
        """
        cb = self._on_user_transcript_rejected_cb
        if cb:
            Clock.schedule_once(lambda _dt: self._safe_call(cb, item_id), 0)

    # ------------------------------------------------------------------
    # Live caption (on-device Vosk partials while the user speaks)
    # ------------------------------------------------------------------

    def _start_caption_worker(self) -> None:
        """Spin up the Vosk recognizer + side thread for live captions.

        No-op unless the feature is enabled and a model is available. Runs on
        its own thread so the CPU-heavy decode never stalls the asyncio loop
        (mic upload / audio playback heartbeat)."""
        if not self._caption_enabled or self._caption_thread is not None:
            return
        try:
            from vosk import KaldiRecognizer
            # Vosk resamples internally, so feeding 24 kHz against a 16 kHz
            # model is fine — we just declare the input rate.
            self._caption_rec = KaldiRecognizer(self._vosk_model, _REALTIME_RATE)
        except Exception:
            logger.debug("Live caption: recognizer init failed; disabling", exc_info=True)
            self._caption_enabled = False
            return
        self._caption_q = queue.Queue(maxsize=64)
        self._caption_thread = threading.Thread(
            target=self._caption_worker, daemon=True, name="rtv-caption"
        )
        self._caption_thread.start()
        logger.info("Realtime live caption: on-device Vosk partials enabled")

    def _caption_worker(self) -> None:
        rec = self._caption_rec
        q = self._caption_q
        if rec is None or q is None:
            return
        while not self._stop.is_set():
            try:
                pcm = q.get(timeout=0.2)
            except queue.Empty:
                continue
            if pcm is None:
                break
            # New utterance — drop any in-progress decode state.
            if self._caption_reset.is_set():
                self._caption_reset.clear()
                try:
                    rec.Reset()
                except Exception:
                    pass
                self._caption_text = ""
            # Only surface captions between speech_started and speech_stopped;
            # after the turn commits, OpenAI's accurate transcript takes over.
            if not self._caption_active:
                continue
            try:
                if rec.AcceptWaveform(pcm):
                    res = json.loads(rec.Result() or "{}")
                    seg = (res.get("text") or "").strip()
                    if seg:
                        self._caption_text = (self._caption_text + " " + seg).strip()
                        if self._caption_active:
                            self._emit_user_transcript(self._caption_text, is_final=False)
                else:
                    pres = json.loads(rec.PartialResult() or "{}")
                    part = (pres.get("partial") or "").strip()
                    if part and self._caption_active:
                        live = (self._caption_text + " " + part).strip()
                        self._emit_user_transcript(live, is_final=False)
            except Exception:
                logger.debug("Live caption decode failed", exc_info=True)

    def _emit_device_navigation(self, tool_output_json: str) -> None:
        cb = self._on_device_navigate_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        screen = data.get("device_navigate")
        if not isinstance(screen, str) or not screen.strip():
            return
        target_date = None
        target_date_str = data.get("target_date")
        if target_date_str:
            try:
                from datetime import date as _date
                target_date = _date.fromisoformat(str(target_date_str).strip())
            except (ValueError, TypeError):
                pass
        target_tab = data.get("target_tab") or None
        meeting_id = data.get("meeting_id") or None
        summary_data = data.get("summary_data") if isinstance(data.get("summary_data"), dict) else None
        Clock.schedule_once(
            lambda _dt: self._safe_call(
                cb, screen.strip(), target_date, target_tab, meeting_id, summary_data
            ),
            0,
        )

    # ── Device-driven morning-brief walkthrough ────────────────────────────

    def _emit_brief_section(self, section: str) -> None:
        """Swipe the on-screen morning-brief carousel to a given section."""
        cb = self._on_device_navigate_cb
        if not cb:
            return
        Clock.schedule_once(
            lambda _dt: self._safe_call(cb, "morning_brief", None, section), 0
        )

    async def _inject_brief_interruption_directive(self, ws) -> None:
        """Give the model the context to decide, by intent, whether the user is
        done with the morning brief.

        The carousel walkthrough is device-driven, so the model otherwise has no
        idea a briefing was even on screen. When the user barges in mid-brief we
        hand the model that missing context plus the means to act (its
        navigate_device_ui tool), then let its own language understanding — not
        keyword matching — decide whether to return to the transcription screen.
        """
        directive = (
            "[Briefing interrupted] You were delivering the morning briefing on a "
            "temporary briefing screen and the user just spoke over it. The briefing "
            "is a temporary overlay on top of the audio transcription screen, not a "
            "place to stay. Judge what the user wants from the MEANING of what they "
            "say, not from specific words:\n"
            "- If they clearly want more of the briefing (asking about a part of it, "
            "asking you to continue, repeat, or go deeper into schedule/tasks/emails), "
            "respond naturally and stay with the briefing.\n"
            "- Otherwise — if they acknowledge it, brush it off, change the subject, "
            "ask something unrelated, or in any way signal they are done hearing it — "
            "give a brief, natural reply to what they said and then call "
            "navigate_device_ui(screen=\"voice_session\") to take them back to the "
            "audio transcription screen. When unsure, prefer returning them. "
            "Decide from intent, not exact phrases."
        )
        try:
            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": directive}],
                },
            }))
        except Exception:
            logger.debug("brief interruption directive send failed", exc_info=True)

    def _cancel_briefing(self) -> None:
        """Stop driving the briefing (e.g. the user barged in / took over)."""
        self._brief_active = False
        self._brief_start_pending = False
        task = self._brief_task
        self._brief_task = None
        if task is not None and not task.done():
            try:
                task.cancel()
            except Exception:
                pass
        start_task = self._brief_start_task
        self._brief_start_task = None
        if start_task is not None and not start_task.done():
            try:
                start_task.cancel()
            except Exception:
                pass

    async def _send_brief_narration(self, ws, idx: int) -> None:
        """Inject the per-section directive and request a tool-less narration."""
        section = _BRIEF_SECTIONS[idx]
        facts = None
        provider = self._brief_data_provider
        if provider is not None:
            try:
                data = provider() or {}
                facts = data.get(section)
            except Exception:
                logger.debug("brief_data_provider failed", exc_info=True)
        directive = _build_brief_directive(section, facts)
        self._brief_narration_audio_seen = False
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": directive}],
            },
        }))
        # tool_choice="none" guarantees the model just speaks this one section
        # and cannot batch-fire more navigate calls; the device drives the rest.
        await ws.send(json.dumps({
            "type": "response.create",
            "response": {"tool_choice": "none"},
        }))

    async def _start_briefing_from_user_request(self, ws) -> None:
        """Start the visual morning briefing without relying on model tool use."""
        if self._brief_active:
            return
        self._cancel_briefing()
        self._abort_aplay()
        self._brief_active = True
        self._brief_idx = 0
        self._emit_brief_section(_BRIEF_SECTIONS[self._brief_idx])
        try:
            # Semantic VAD may have already started a generic response from the
            # preloaded briefing snapshot. Cancel it so we don't get a full
            # unsynchronised narration over the visual walkthrough.
            await ws.send(json.dumps({"type": "response.cancel"}))
        except Exception:
            logger.debug("Realtime: morning brief response.cancel failed", exc_info=True)
        self._brief_start_pending = True
        try:
            self._brief_start_task = asyncio.create_task(
                self._send_pending_brief_start_after_delay(ws)
            )
        except Exception:
            logger.debug("Realtime: could not schedule pending morning brief start", exc_info=True)

    async def _send_pending_brief_start_after_delay(self, ws) -> None:
        """Fallback start if response.cancel does not produce a response.done."""
        try:
            await asyncio.sleep(0.5)
            if not self._brief_active or not self._brief_start_pending or self._stop.is_set():
                return
            self._brief_start_pending = False
            await self._send_brief_narration(ws, self._brief_idx)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Realtime: pending morning brief start failed")

    def _schedule_brief_advance(self, ws) -> None:
        """Queue advancing to the next section once this one's audio drains."""
        if not self._brief_active:
            return
        if self._brief_task is not None and not self._brief_task.done():
            return
        try:
            self._brief_task = asyncio.create_task(self._advance_briefing(ws))
        except Exception:
            logger.debug("could not schedule briefing advance", exc_info=True)

    async def _advance_briefing(self, ws) -> None:
        """Wait for the current section's audio to finish, then drive the next."""
        try:
            # Hold the swipe + next narration until the spoken audio for the
            # section that just finished has actually played out of the speaker.
            for _ in range(180):  # cap ~45 s for longer schedule sections
                remaining = self.audio_playback_remaining_s()
                if remaining <= 0.05 or not self._brief_active or self._stop.is_set():
                    break
                await asyncio.sleep(min(remaining, 0.25))
            if not self._brief_active or self._stop.is_set():
                return
            self._brief_idx += 1
            if self._brief_idx >= len(_BRIEF_SECTIONS):
                # All three sections narrated — mark the briefing complete and
                # navigate back to the voice session screen after a short pause
                # so the closing sentence has time to finish playing.
                self._brief_active = False
                await self._navigate_after_brief()
                return
            self._emit_brief_section(_BRIEF_SECTIONS[self._brief_idx])
            await self._send_brief_narration(ws, self._brief_idx)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Realtime: briefing advance failed")

    async def _navigate_after_brief(self) -> None:
        """Wait for the final email section's audio to drain, then return to voice_session."""
        try:
            # Wait for any remaining playback (the closing sentence) to finish.
            for _ in range(120):  # up to 30 s guard
                remaining = self.audio_playback_remaining_s()
                if remaining <= 0.05 or self._stop.is_set():
                    break
                await asyncio.sleep(min(remaining, 0.25))
            if self._stop.is_set():
                return
            # An extra 1.5 s breathing room before the screen changes.
            await asyncio.sleep(1.5)
            if self._stop.is_set():
                return
            cb = self._on_device_navigate_cb
            if cb:
                Clock.schedule_once(
                    lambda _dt: self._safe_call(cb, "voice_session", None, None), 0
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Realtime: post-brief navigate failed", exc_info=True)

    def _emit_email_draft(self, tool_output_json: str) -> None:
        """Forward a show_email_draft directive payload to the UI."""
        cb = self._on_email_draft_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        draft = data.get("device_email_draft")
        if not isinstance(draft, dict):
            return
        Clock.schedule_once(lambda _dt: self._safe_call(cb, draft), 0)

    def _emit_email_view(self, tool_output_json: str) -> None:
        """Forward a show_email_view directive payload to the UI."""
        cb = self._on_email_view_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        view = data.get("device_email_view")
        if not isinstance(view, dict):
            return
        Clock.schedule_once(lambda _dt: self._safe_call(cb, view), 0)

    def _emit_task_creation(self, tool_output_json: str) -> None:
        """Forward a show_task_creation directive payload to the UI."""
        cb = self._on_task_creation_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        task = data.get("device_task_creation")
        if not isinstance(task, dict):
            return
        Clock.schedule_once(lambda _dt: self._safe_call(cb, task), 0)

    def _redact_task_creation_for_model(self, tool_output_json: str) -> str:
        """Strip the device-only task payload before feeding back to the model."""
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return tool_output_json
        if not isinstance(data, dict) or "device_task_creation" not in data:
            return tool_output_json
        slim = {k: v for k, v in data.items() if k != "device_task_creation"}
        try:
            return json.dumps(slim)
        except (TypeError, ValueError):
            return tool_output_json

    def _emit_task_dismiss(self, tool_output_json: str) -> None:
        """Forward a confirm/discard_task_creation directive that dismisses the
        task-creation screen (the actual save, if any, already happened server-side)."""
        cb = self._on_task_dismiss_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("device_task_dismiss"):
            return
        # Forward the slim payload so the UI can tell a successful confirm (carries
        # a "task" dict with its due date) from a discard/failure and route the
        # device to the Tasks screen on the right tab accordingly.
        info = {k: v for k, v in data.items() if k != "device_task_dismiss"}
        Clock.schedule_once(lambda _dt: self._safe_call(cb, info), 0)

    def _redact_task_dismiss_for_model(self, tool_output_json: str) -> str:
        """Strip the device-only dismiss flag before feeding back to the model."""
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return tool_output_json
        if not isinstance(data, dict) or "device_task_dismiss" not in data:
            return tool_output_json
        slim = {k: v for k, v in data.items() if k != "device_task_dismiss"}
        try:
            return json.dumps(slim)
        except (TypeError, ValueError):
            return tool_output_json

    def _emit_calendar_event(self, tool_output_json: str) -> None:
        """Forward a show_calendar_event directive payload to the UI."""
        cb = self._on_calendar_event_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        event = data.get("device_calendar_event")
        if not isinstance(event, dict):
            return
        Clock.schedule_once(lambda _dt: self._safe_call(cb, event), 0)

    def _redact_calendar_event_for_model(self, tool_output_json: str) -> str:
        """Strip the device-only calendar payload before feeding back to the model."""
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return tool_output_json
        if not isinstance(data, dict) or "device_calendar_event" not in data:
            return tool_output_json
        slim = {k: v for k, v in data.items() if k != "device_calendar_event"}
        try:
            return json.dumps(slim)
        except (TypeError, ValueError):
            return tool_output_json

    def _emit_calendar_event_dismiss(self, tool_output_json: str) -> None:
        """Forward a confirm/discard_calendar_event directive that dismisses the
        calendar-event screen (the actual create, if any, already happened server-side)."""
        cb = self._on_calendar_event_dismiss_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict):
            return
        dismiss = data.get("device_calendar_event_dismiss")
        if not dismiss:
            return
        # New servers send a dict ({"created": bool, "date": "...", ...}); older
        # ones sent a bare True. Normalise to a dict so the UI can decide whether
        # to navigate to the calendar.
        info = dismiss if isinstance(dismiss, dict) else {}
        Clock.schedule_once(lambda _dt: self._safe_call(cb, info), 0)

    def _redact_calendar_event_dismiss_for_model(self, tool_output_json: str) -> str:
        """Strip the device-only dismiss flag before feeding back to the model."""
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return tool_output_json
        if not isinstance(data, dict) or "device_calendar_event_dismiss" not in data:
            return tool_output_json
        slim = {k: v for k, v in data.items() if k != "device_calendar_event_dismiss"}
        try:
            return json.dumps(slim)
        except (TypeError, ValueError):
            return tool_output_json

    def _redact_email_draft_for_model(self, tool_output_json: str) -> str:
        """Remove the device-only draft payload before the result is sent back to
        the model. The email draft popup (recipients / subject / body, including
        the full reply-all Cc list the server resolved) is a device surface; the
        model must not receive concrete recipients it could use to send a new,
        mis-threaded email. We keep 'ok' and 'note' and surface only the lifecycle
        state. Returns a slimmed JSON string (falls back to the original on error)."""
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return tool_output_json
        if not isinstance(data, dict) or "device_email_draft" not in data:
            return tool_output_json
        ded = data.get("device_email_draft")
        slim = {k: v for k, v in data.items() if k != "device_email_draft"}
        if isinstance(ded, dict) and ded.get("state"):
            slim["draft_state"] = ded.get("state")
        try:
            return json.dumps(slim)
        except (TypeError, ValueError):
            return tool_output_json

    def _emit_recipient_picker(self, tool_output_json: str) -> None:
        """Forward a show_recipient_picker directive payload to the UI."""
        cb = self._on_recipient_picker_cb
        if not cb:
            return
        try:
            data = json.loads(tool_output_json)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok"):
            return
        picker = data.get("device_recipient_picker")
        if not isinstance(picker, dict):
            return
        query = str(picker.get("query") or "")
        candidates = picker.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        field = str(picker.get("field") or "to").strip().lower()
        if field not in ("to", "cc", "bcc", "attendee"):
            field = "to"
        Clock.schedule_once(
            lambda _dt: self._safe_call(cb, query, candidates, field), 0
        )

    def cancel_current_response(self) -> None:
        """Interrupt any in-progress AI speech immediately (screen tap barge-in).

        Sends ``response.cancel`` to stop the model mid-sentence, kills the
        local aplay subprocess so the speaker goes quiet, and suppresses the
        echo-tail audio briefly.  Safe to call from the Kivy main thread even
        when no response is active (the API ignores a cancel when idle).
        """
        loop, ws = self._loop, self._ws
        if loop is None or ws is None or loop.is_closed():
            return

        # Stop local audio playback immediately so the speaker goes quiet.
        self._abort_aplay()
        self._suppress_audio_until = time.monotonic() + _BARGE_IN_SUPPRESS_AUDIO_S

        async def _cancel():
            try:
                await ws.send(json.dumps({"type": "response.cancel"}))
            except Exception:
                logger.debug("cancel_current_response ws.send failed", exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_cancel(), loop)
        except Exception:
            logger.debug("cancel_current_response schedule failed", exc_info=True)

    def send_user_text(self, text: str) -> None:
        """Inject a user turn into the live session (e.g. from a screen tap).

        Creates a conversation item with the given text and asks the model to
        respond, so a touch interaction is treated exactly like the user having
        said it aloud — keeping the assistant in control of the email workflow.
        Safe to call from the Kivy main thread.
        """
        msg = (text or "").strip()
        loop, ws = self._loop, self._ws
        if not msg or loop is None or ws is None or loop.is_closed():
            return

        async def _send():
            try:
                await ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": msg}],
                    },
                }))
                await ws.send(json.dumps({"type": "response.create"}))
            except Exception:
                logger.warning("Realtime send_user_text failed", exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_send(), loop)
        except Exception:
            logger.debug("send_user_text schedule failed", exc_info=True)

    @staticmethod
    def _safe_call(cb, *args) -> None:
        if not cb:
            return
        try:
            cb(*args)
        except Exception:
            logger.exception("Realtime callback failed")

    def _farewell_suppressed(self) -> bool:
        """True when the keyword farewell fallback must be skipped (e.g. an
        email draft / recipient picker is on screen). Defers to the model's
        contextual end_session tool so mid-task closers don't kill the session."""
        cb = self._should_suppress_farewell_cb
        if not cb:
            return False
        try:
            return bool(cb())
        except Exception:
            return False

    def _touch(self) -> None:
        self._last_activity_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Mic input
    # ------------------------------------------------------------------

    def _resolve_input_device(self):
        from mic_input_resolve import (
            capture_device_fallback_candidates,
            resolve_sounddevice_capture_device_index,
        )
        if sd is None:
            return None, []
        preferred = resolve_sounddevice_capture_device_index(sd)
        candidates = capture_device_fallback_candidates(sd, preferred)

        # If the ALSA pair found a USB capture device that sounddevice missed
        # (common when PortAudio doesn't enumerate all ALSA cards), inject the
        # ALSA string as the first candidate so _open_mic tries it before the
        # PortAudio default.
        pair_capture = self._audio_pair.capture
        if pair_capture is not None and pair_capture not in candidates:
            candidates = [pair_capture, *candidates]
            if preferred is None:
                preferred = pair_capture
            label = self._audio_pair.capture_name or str(pair_capture)
            logger.info("Realtime mic: injecting ALSA capture device: %s", label)

        return preferred, candidates

    def _open_mic(self, preferred_device_id, candidate_device_ids,
                  sample_rates=None) -> bool:
        if sd is None:
            self._emit_error("sounddevice not installed; microphone unavailable.")
            return False

        # Prefer capturing at (or near) the 24 kHz Realtime rate so we avoid a
        # quality-destroying downsample. On Windows WASAPI shared mode this
        # succeeds via the OS's high-quality resampler; if the device rejects
        # it we fall back through 16 kHz (clean upsample) before the higher
        # rates that force our own downsample. The appliance keeps its original
        # order (its USB mic is natively 16 kHz / 48 kHz).
        # ``sample_rates`` overrides this order (the AEC3 path prefers a native
        # 48 kHz capture so the near-end matches the loopback reference rate).
        if sample_rates is None:
            if IS_DESKTOP:
                sample_rates = (_REALTIME_RATE, 16000, 48000, 32000, 44100)
            else:
                sample_rates = (48000, 44100, 32000, 16000, _REALTIME_RATE)

        tried: list = []
        for dev in [preferred_device_id, *candidate_device_ids]:
            if dev in tried:
                continue
            tried.append(dev)
            for sr in sample_rates:
                try:
                    stream = sd.RawInputStream(
                        samplerate=sr,
                        channels=1,
                        dtype="int16",
                        blocksize=max(1, int(sr * _APPEND_CHUNK_MS / 1000)),
                        device=dev,
                        callback=self._mic_callback,
                    )
                    stream.start()
                    self._mic_stream = stream
                    self._mic_native_sr = sr
                    dev_name = ""
                    try:
                        if dev is not None:
                            dev_name = (sd.query_devices(dev).get("name") or "")
                        else:
                            di = sd.default.device[0]
                            if isinstance(di, int) and di >= 0:
                                dev_name = (sd.query_devices(di).get("name") or "")
                    except Exception:
                        dev_name = ""
                    logger.info(
                        "Realtime mic open: device=%s (%s) samplerate=%s%s",
                        dev,
                        dev_name or "?",
                        sr,
                        "" if sr == _REALTIME_RATE else f" -> resample to {_REALTIME_RATE}",
                    )
                    return True
                except Exception as e:
                    logger.debug(
                        "Mic open failed device=%s sr=%s: %s", dev, sr, e
                    )
        return False

    def _mic_callback(self, indata, frames, time_info, status) -> None:
        if status:
            logger.debug("Realtime mic status: %s", status)
        try:
            self._audio_q.put_nowait(bytes(indata))
        except queue.Full:
            # Drop oldest if we can't keep up — better than blocking the
            # PortAudio callback (which would distort the input stream).
            try:
                _ = self._audio_q.get_nowait()
                self._audio_q_drops += 1
                self._audio_q.put_nowait(bytes(indata))
            except Exception:
                pass

    def _close_mic(self) -> None:
        s = self._mic_stream
        self._mic_stream = None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Playback (aplay subprocess)
    # ------------------------------------------------------------------

    def _ensure_win_player(self) -> None:
        """Lazily create the PortAudio streaming sink (Windows/macOS)."""
        if self._win_player is not None:
            return
        try:
            from audio_output import PcmStreamPlayer

            device = None
            env_dev = (os.getenv("MEETINGBOX_OUTPUT_DEVICE_INDEX") or "").strip()
            if env_dev.isdigit():
                device = int(env_dev)
            # Feed the AEC far-end reference straight from the device callback:
            # the exact rendered block, at device pace, including the barge-in
            # fade and the zero-fill silence — the ground-truth echo source.
            far_ref = self._far_ref
            on_pcm = None
            if (
                self._aec3_full_duplex
                and far_ref is not None
                and not getattr(far_ref, "active_capture", True)
            ):
                on_pcm = far_ref.feed_playback
            player = PcmStreamPlayer(
                sample_rate=_REALTIME_RATE, channels=1, device=device,
                on_pcm=on_pcm,
            )
            if not player.start():
                logger.warning("Realtime: PortAudio playback sink unavailable")
                return
            self._far_ref_via_player = on_pcm is not None
            if on_pcm is not None:
                # The reference is now fed at device pace: enable its
                # ride-height control (underrun re-prime + overrun trim +
                # starvation flag) and top the silence cushion up so mic-side
                # scheduling jitter drains the cushion instead of underrunning
                # the ring (an underrun inserts zeros that shift the far
                # timeline).
                try:
                    far_ref.device_paced = True
                    far_ref.prime()
                except Exception:
                    pass
            self._win_player = player
            logger.info(
                "Realtime PortAudio playback started (rate=%s device=%s)",
                _REALTIME_RATE, device if device is not None else "default",
            )
        except Exception:
            logger.exception("Realtime: PortAudio playback start failed")
            self._win_player = None

    def _ensure_aplay(self) -> None:
        if self._aplay_proc is not None and self._aplay_proc.poll() is None:
            return
        if not shutil.which("aplay"):
            return
        # Priority: explicit env override → audio_pair auto-detect (USB or fallback)
        output_device = (os.getenv("AUDIO_OUTPUT_DEVICE") or "").strip()
        if not output_device:
            output_device = self._audio_pair.playback or ""
            if output_device:
                logger.info(
                    "Realtime aplay: using auto-detected device %s (%s)",
                    output_device,
                    self._audio_pair.playback_name or output_device,
                )
        cmd = [
            "aplay",
            "-q",
            "-t", "raw",
            "-f", "S16_LE",
            "-r", str(_REALTIME_RATE),
            "-c", "1",
            "--buffer-time", _APLAY_BUFFER_TIME_US,
        ]
        if output_device:
            cmd += ["-D", output_device]
        try:
            self._aplay_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._aplay_pid = self._aplay_proc.pid
            logger.info("Realtime aplay started pid=%s device=%s", self._aplay_pid, output_device or "default")
        except Exception:
            logger.exception("Realtime: aplay start failed")
            self._aplay_proc = None
            self._aplay_pid = None

    def audio_playback_remaining_s(self) -> float:
        """Approximate seconds of assistant speech still queued for the speaker.

        Realtime may deliver audio faster than it plays. `_play_delta()` extends
        a cumulative play-until clock for each PCM chunk so UI transitions can
        wait for the whole queued utterance, not just the final websocket delta.
        """
        try:
            with self._playback_clock_lock:
                remaining = self._assistant_audio_play_until - time.monotonic()
        except Exception:
            return 0.0
        if remaining <= 0.0:
            return 0.0
        return min(remaining, 45.0)

    def _play_delta(self, delta_b64: str) -> None:
        if not delta_b64:
            return
        if time.monotonic() < self._suppress_audio_until:
            return  # trailing bytes of a barge-in'd response
        try:
            raw = base64.b64decode(delta_b64)
        except Exception:
            return
        if not raw:
            return
        # Push the same PCM into the far-end ring so the Speex AEC and local
        # barge-in detection know what is about to come out of the speaker. Cap
        # to ~5 s to keep memory bounded if the mic side stalls.
        #
        # Skipped entirely when the OS AEC is the mic source: it cancels echo at
        # the source and never reads this ring, so filling it would just be
        # wasted work.
        # AEC3 render-fed reference: normally the PcmStreamPlayer device
        # callback feeds it the exact rendered blocks (_far_ref_via_player).
        # Only feed from here as a fallback when no player tap is installed
        # (e.g. the aplay path). Loopback references capture the speaker
        # output themselves and ignore this feed.
        if _USE_SD_PLAYBACK:
            # Create the sink (and its render tap) before deciding whether to
            # fall back, so the first chunk is not double-fed.
            self._ensure_win_player()
        if (
            self._far_ref is not None
            and not self._far_ref.active_capture
            and not self._far_ref_via_player
        ):
            try:
                self._far_ref.feed_playback(raw)
            except Exception:
                logger.debug("far-ref feed_playback failed", exc_info=True)
        if (
            not self._os_aec_full_duplex
            and not self._aec3_full_duplex
            and (self._aec is not None or _LOCAL_BARGE_IN_ENABLED)
        ):
            with self._aec_buf_lock:
                self._aec_far_buf.extend(raw)
                max_bytes = _REALTIME_RATE * 2 * 5
                excess = len(self._aec_far_buf) - max_bytes
                if excess > 0:
                    del self._aec_far_buf[:excess]
        # Extend the cumulative playback clock by this chunk duration. Chunks can
        # arrive back-to-back before the speaker has played earlier chunks; using
        # max(previous_until, now) keeps a true queued-audio end time.
        chunk_s = len(raw) / (_REALTIME_RATE * 2)   # PCM16 mono bytes → seconds
        now = time.monotonic()
        with self._playback_clock_lock:
            start_at = max(self._assistant_audio_play_until, now)
            self._assistant_audio_play_until = start_at + chunk_s
            # Keep the mic muted for an echo-decay tail after playback ends
            # (longer in half-duplex; see self._mic_reopen_tail_s).
            self._mute_mic_uplink_until = max(
                self._mute_mic_uplink_until,
                self._assistant_audio_play_until + self._mic_reopen_tail_s,
            )
        if _USE_SD_PLAYBACK:
            self._ensure_win_player()
            player = self._win_player
            if player is not None:
                player.write(raw)
            return
        self._ensure_aplay()
        proc = self._aplay_proc
        if proc is None or proc.stdin is None:
            return
        try:
            self._aplay_writer.submit(self._write_to_aplay, proc, raw)
        except RuntimeError:
            # Executor already shut down (session closing).
            pass

    @staticmethod
    def _write_to_aplay(proc: subprocess.Popen, raw: bytes) -> None:
        """Runs on the rtv-aplay thread. Blocking here is fine."""
        stdin = proc.stdin
        if stdin is None:
            return
        try:
            stdin.write(raw)
        except (BrokenPipeError, ValueError):
            # Expected when we kill aplay for a barge-in (pipe closed).
            pass
        except Exception:
            logger.debug("aplay write failed", exc_info=True)

    def _abort_aplay(self) -> None:
        """Hard-stop playback immediately (barge-in)."""
        with self._playback_clock_lock:
            self._assistant_audio_play_until = 0.0
            self._mute_mic_uplink_until = 0.0
        # Playback is gone now, so there is no more echo to cancel or gate. If we
        # left the AEC3 residual gate's far-active cooldown armed, it would keep
        # the elevated echo-suppression threshold in force for up to a second and
        # swallow the user's quieter syllables mid-utterance -> garbled/clipped
        # transcripts. Release it immediately so the barge-in utterance streams
        # cleanly (the cooldown still applies to natural turn-ends, which do not
        # call _abort_aplay).
        self._aec3_far_active_until = 0.0
        self._aec3_gate_open_until = 0.0
        self._aec3_gate_consecutive = 0
        self._aec3_residual_floor = 0.0
        # Same for the OS-DSP gate: once playback is aborted there is no more echo
        # to suppress, so release the gate immediately for the barge-in utterance.
        self._os_dsp_gate_active_until = 0.0
        self._os_dsp_gate_open_until = 0.0
        self._os_dsp_gate_consecutive = 0
        self._os_dsp_gate_floor = 0.0
        # Collapse the evidence layer's echo-risk window to a short decay tail.
        # It was anchored to the pre-abort play-until clock, which could sit
        # tens of seconds in the future; leaving it would score the user's
        # NEXT utterances against the strict during-playback bar and reject
        # them as phantoms (the post-barge-in "Can you hear me?" lockout).
        self._playback_echo_risk_until = min(
            self._playback_echo_risk_until,
            time.monotonic() + _EVIDENCE_PLAYBACK_HANGOVER_S,
        )
        # Flush not-yet-consumed far-end reference audio: the render tap only
        # feeds what actually played, but anything already sitting in the ring
        # / engine buffer refers to audio the abort just discarded — consuming
        # it later would misalign the reference against the mic.
        if self._far_ref is not None and not getattr(
            self._far_ref, "active_capture", True
        ):
            try:
                self._far_ref.clear()
            except Exception:
                pass
        if self._aec3 is not None:
            try:
                self._aec3.clear_far()
            except Exception:
                pass
        if _USE_SD_PLAYBACK:
            player = self._win_player
            self._win_player = None
            if player is not None:
                try:
                    player.stop()
                    self._log_voice_event("playback_aborted")
                except Exception:
                    pass
            return
        proc = self._aplay_proc
        self._aplay_proc = None
        self._aplay_pid = None
        if proc is None:
            return
        pid = getattr(proc, "pid", None)
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.poll() is None:
                proc.kill()
                self._log_voice_event("aplay_killed", pid=pid)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Async main loop
    # ------------------------------------------------------------------

    async def _async_main(self) -> None:
        if not self._client_secret or not self._model:
            self._emit_error("Missing client secret or model for Realtime.")
            self._emit_session_end()
            return

        self._loop = asyncio.get_running_loop()
        url = build_realtime_websocket_url(self._model)
        headers = [("Authorization", f"Bearer {self._client_secret}")]

        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                max_size=None,
                ssl=ws_ssl_context(url),
                # Default open_timeout is 10s — too tight for transient slowness
                # during the TLS + HTTP-101 upgrade to api.openai.com, which
                # surfaces as "timed out during opening handshake" and kills
                # the session before the user even starts speaking.
                open_timeout=30,
                ping_interval=20,
                # OpenAI Realtime can take 5–15 s to ACK a ping while a
                # large tool call (e.g. get_briefing_context returns
                # ~35 KB) is in flight. 30 s tripped 1011 keepalive
                # timeouts mid-response; 120 s is generous enough to ride
                # those stalls while still detecting a truly dead socket.
                ping_timeout=120,
                close_timeout=3,
            ) as ws:
                self._ws = ws
                self._activate_event = asyncio.Event()

                # Start receiving immediately so the session.created ->
                # session.update -> session.updated handshake completes and the
                # socket is kept alive — including while held in warm standby.
                recv_task = asyncio.create_task(self._recv_loop())

                # Warm standby: hold the connected session WITHOUT opening the
                # mic or streaming audio until activate() is called (on wake).
                # No mic + no audio in => no VAD turn => zero billable response
                # while held. Vosk keeps the mic to detect the wake word.
                if self._prewarm and not self._activate_requested:
                    self._emit_state("idle")
                    act_task = asyncio.create_task(self._activate_event.wait())
                    done, _pending = await asyncio.wait(
                        {act_task, recv_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if recv_task in done:
                        # Connection closed before the user ever woke it
                        # (idle timeout / drop). Return and let the outer
                        # finally emit a single session_end; main.py re-prewarms.
                        act_task.cancel()
                        return

                # Active path: let the UI close any local mic (e.g. Vosk wake
                # word) before we open ALSA for the Realtime session.
                if self._on_before_open_mic_cb is not None:
                    self._safe_call(self._on_before_open_mic_cb)
                    await asyncio.sleep(0.01)

                # OS Voice Capture DSP (CWMAudioAEC): only when explicitly
                # preferred (REALTIME_PREFER_OS_AEC=1). Its cancellation
                # quality is whatever the vendor audio driver provides —
                # device-dependent by construction — so the default engine
                # order runs software AEC3 first (below) and keeps this as
                # the escape hatch / fallback. In source mode it opens the
                # default communications mic itself, so we must NOT also
                # open a PortAudio mic on the same device.
                os_aec_live = False
                if _PREFER_OS_AEC and self._os_aec is not None:
                    try:
                        if self._os_aec.start():
                            os_aec_live = True
                            self._os_aec_full_duplex = True
                            # DSP emits 16 kHz mono; _pump_mic resamples to 24k.
                            self._mic_native_sr = 16000
                            # The mic is now genuinely echo-free, so run true
                            # full-duplex: a server speech_started must hard-stop
                            # playback IMMEDIATELY (no "defer to local detector"
                            # guard, which is only needed on coupled mics).
                            self._half_duplex = False
                            # OS AEC removes echo at the source: disable Speex so
                            # nothing double-cancels.
                            if self._aec is not None:
                                try:
                                    self._aec.close()
                                except Exception:
                                    pass
                                self._aec = None
                            logger.info(
                                "Realtime AEC: Windows Voice Capture DSP live — "
                                "OS echo cancellation, full-duplex barge-in "
                                "(device='%s')",
                                self._os_aec.device_name,
                            )
                            self._log_voice_event(
                                "aec_engine",
                                engine="windows_voice_capture_dsp",
                                reference="os_shared_clock",
                                aec_rate=self._mic_native_sr,
                                mic_rate=self._mic_native_sr,
                            )
                        else:
                            logger.warning(
                                "Realtime AEC: OS AEC failed to start (%s); "
                                "falling back to WebRTC AEC3 + loopback",
                                self._os_aec.last_error,
                            )
                            self._os_aec = None
                    except Exception:
                        logger.debug("OS AEC start failed", exc_info=True)
                        self._os_aec = None

                # PREFERRED: genuine WebRTC AEC3 (the canceller Chrome/Meet/
                # ChatGPT desktop use) driven by a real playback reference —
                # WASAPI loopback on Windows (the post-mix signal the speaker
                # actually renders), app-PCM on macOS. Pure software: identical
                # deterministic behavior on every device regardless of audio
                # chip or driver. Opens a normal mic and cancels the echo in
                # software; when live it becomes the mic engine and runs
                # full-duplex behind the residual gate, with the APM speech
                # probability feeding the evidence layer.
                aec3_live = False
                if not os_aec_live and self._webrtc_aec_available:
                    try:
                        aec3_live = self._start_aec3_capture()
                    except Exception:
                        logger.debug("AEC3 capture start failed", exc_info=True)
                        aec3_live = False
                    if aec3_live:
                        # AEC3 owns the mic; do not also start the OS DSP.
                        self._os_aec = None

                # FALLBACK: OS Voice Capture DSP, when AEC3 could not start
                # (pywebrtc_audio missing, loopback capture failed, mic open
                # failed at AEC3 rates).
                if not aec3_live and not os_aec_live and self._os_aec is not None:
                    try:
                        if self._os_aec.start():
                            os_aec_live = True
                            self._os_aec_full_duplex = True
                            self._mic_native_sr = 16000
                            self._half_duplex = False
                            if self._aec is not None:
                                try:
                                    self._aec.close()
                                except Exception:
                                    pass
                                self._aec = None
                            logger.info(
                                "Realtime AEC: Windows Voice Capture DSP live "
                                "(fallback) — device='%s'",
                                self._os_aec.device_name,
                            )
                            self._log_voice_event(
                                "aec_engine",
                                engine="windows_voice_capture_dsp",
                                reference="os_shared_clock",
                                aec_rate=self._mic_native_sr,
                                mic_rate=self._mic_native_sr,
                            )
                        else:
                            logger.warning(
                                "Realtime AEC: OS AEC fallback failed to start (%s)",
                                self._os_aec.last_error,
                            )
                            self._os_aec = None
                    except Exception:
                        logger.debug("OS AEC fallback start failed", exc_info=True)
                        self._os_aec = None

                if not aec3_live and not os_aec_live:
                    preferred, candidates = self._resolve_input_device()
                    if not self._open_mic(preferred, candidates):
                        self._emit_error("Realtime: microphone unavailable.")
                        await ws.close()
                        self._emit_session_end()
                        return

                # Suppress mic uplink briefly so the room echo of the wake
                # phrase decays before audio reaches OpenAI.  Without this,
                # the VAD fires on the garbled "Hey Pepper" echo and the model
                # responds with a confused phrase ("I can't catch on to that")
                # before the proper wake greeting even plays.
                _wake_echo_settle_s = float(
                    os.environ.get("REALTIME_WAKE_ECHO_SETTLE_S", "0.5")
                )
                if _wake_echo_settle_s > 0:
                    with self._playback_clock_lock:
                        self._mute_mic_uplink_until = max(
                            self._mute_mic_uplink_until,
                            time.monotonic() + _wake_echo_settle_s,
                        )

                self._emit_state("listening")
                # Signal the UI that the live session is ready. Moved here from
                # the session.created handler so a warm-standby connect does NOT
                # flip the UI to "listening" before the user actually wakes it.
                if not self._connected_fired:
                    self._connected_fired = True
                    self._emit_connected()

                # Start the live-caption side thread now that the mic is open.
                self._start_caption_worker()

                pump_task = asyncio.create_task(self._pump_mic())
                # Reset the idle clock from the moment the mic is live so
                # any time spent connecting / in warm standby does not count
                # against the idle budget (safety net for cold sessions).
                self._touch()
                idle_task = asyncio.create_task(self._idle_watchdog())
                lag_task = asyncio.create_task(self._loop_lag_monitor())

                # Warm session just woken: greet only after the local wake-word
                # mic has been released and the Realtime mic is open. Speaking
                # before this point can feel like a delayed wake and can leak
                # assistant/prompt audio into the transcript path.
                if self._prewarm:
                    await self._send_wake_greeting(ws)

                try:
                    await recv_task
                finally:
                    self._stop.set()
                    try:
                        self._audio_q.put_nowait(None)
                    except Exception:
                        pass
                    pump_task.cancel()
                    idle_task.cancel()
                    lag_task.cancel()
                    # In-flight response.done handlers (tool round-trips) die
                    # with the session — their ws is closed anyway.
                    done_tasks = list(self._response_done_tasks)
                    for t in done_tasks:
                        t.cancel()
                    await asyncio.gather(
                        pump_task, idle_task, lag_task, *done_tasks,
                        return_exceptions=True,
                    )

        except Exception as e:
            logger.exception("Realtime WebSocket failed")
            self._emit_error(str(e))
        finally:
            self._emit_state("idle")
            self._ws = None
            self._abort_aplay()
            self._close_mic()
            if self._os_aec is not None:
                try:
                    self._os_aec.stop()
                except Exception:
                    pass
                self._os_aec = None
                self._os_aec_full_duplex = False
            if self._far_ref is not None:
                try:
                    self._far_ref.stop()
                except Exception:
                    pass
                self._far_ref = None
                self._far_ref_via_player = False
            if self._aec3 is not None:
                try:
                    self._aec3.close()
                except Exception:
                    pass
                self._aec3 = None
                self._aec3_full_duplex = False
            try:
                self._aplay_writer.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            if self._aec is not None:
                try:
                    self._aec.close()
                except Exception:
                    pass
                self._aec = None
            for tap in (self._debug_raw_tap, self._debug_uplink_tap):
                if tap is not None:
                    tap.close()
            self._debug_raw_tap = None
            self._debug_uplink_tap = None
            self._emit_session_end()

    # ------------------------------------------------------------------
    # Echo cancellation
    # ------------------------------------------------------------------

    def _start_aec3_capture(self) -> bool:
        """Bring up the WebRTC AEC3 full-duplex path. Returns True on success.

        Starts the OS far-end reference (loopback / app-PCM at 48 kHz), opens a
        normal PortAudio mic (preferring a native 48 kHz capture so the near-end
        matches the reference), and constructs the AEC3 engine. On any failure it
        tears down cleanly and returns False so the caller falls back to the OS
        DSP / Speex paths.
        """
        import webrtc_apm
        from aec_reference import create_reference

        ref = create_reference(rate=_AEC3_RATE)
        if ref is None:
            logger.info("Realtime AEC: no far-end reference available; skipping AEC3")
            return False
        if not ref.start():
            logger.warning(
                "Realtime AEC: far-end reference failed to start (%s); skipping AEC3",
                getattr(ref, "last_error", None),
            )
            try:
                ref.stop()
            except Exception:
                pass
            return False

        # Open a normal mic; prefer a native 48 kHz capture (AEC3's rate).
        preferred, candidates = self._resolve_input_device()
        if not self._open_mic(
            preferred, candidates,
            sample_rates=(_AEC3_RATE, 32000, 16000, 44100, _REALTIME_RATE),
        ):
            logger.warning("Realtime AEC: mic open failed for AEC3 path")
            try:
                ref.stop()
            except Exception:
                pass
            return False

        try:
            self._aec3 = webrtc_apm.WebRtcAEC(
                sample_rate=_AEC3_RATE,
                noise_suppression=True,
                high_pass_filter=True,
                auto_gain_control=False,
            )
        except Exception:
            logger.exception("Realtime AEC: AEC3 engine construction failed")
            self._close_mic()
            try:
                ref.stop()
            except Exception:
                pass
            return False

        self._far_ref = ref
        self._aec3_full_duplex = True
        # Genuinely echo-free near-end -> true full-duplex (server VAD +
        # interrupt_response own barge-in; no muting / energy heuristics).
        self._half_duplex = False
        # AEC3 removes echo in software: disable Speex so nothing double-cancels.
        if self._aec is not None:
            try:
                self._aec.close()
            except Exception:
                pass
            self._aec = None
        logger.info(
            "Realtime AEC: WebRTC AEC3 live — %s far-end reference @ %d Hz, "
            "mic @ %d Hz, full-duplex barge-in (device='%s')",
            "active-capture" if getattr(ref, "active_capture", False) else "app-fed",
            _AEC3_RATE, self._mic_native_sr, getattr(ref, "device_name", "?"),
        )
        self._log_voice_event(
            "aec_engine",
            engine="webrtc_aec3",
            reference="loopback" if getattr(ref, "active_capture", False) else "app_pcm",
            aec_rate=_AEC3_RATE,
            mic_rate=self._mic_native_sr,
        )
        return True

    def _on_os_aec_frames(self, pcm16: bytes) -> None:
        """Mic source feeder for the Windows Voice Capture DSP.

        Called from the OS AEC capture thread with already-echo-cancelled mono
        PCM16 at the DSP's native 16 kHz. We push it onto the same queue the
        PortAudio mic callback would use, so _pump_mic resamples it to 24 kHz
        and uploads it like any other mic audio — except the assistant's own
        voice has already been removed at the source.
        """
        if not pcm16:
            return
        try:
            self._audio_q.put_nowait(pcm16)
        except queue.Full:
            # Drop oldest rather than block the capture thread.
            try:
                _ = self._audio_q.get_nowait()
                self._audio_q_drops += 1
                self._audio_q.put_nowait(pcm16)
            except Exception:
                pass

    def _aec_process(self, mic_pcm16: bytes) -> bytes:
        """Run speex AEC on resampled mic bytes; return echo-cancelled PCM16.

        Mic chunks arrive at arbitrary sizes; AEC needs fixed 20 ms frames
        (480 samples = 960 bytes at 24 kHz). We accumulate near-end bytes
        in a buffer, pull matching far-end bytes from the playback ring
        (silence-padded if the agent is not speaking), and emit only whole
        frames. Leftover bytes stay in the buffer for the next call.
        """
        aec = self._aec
        if aec is None or not mic_pcm16:
            return mic_pcm16
        fbytes = self._aec_frame_bytes
        out = bytearray()
        with self._aec_buf_lock:
            self._aec_near_buf.extend(mic_pcm16)
            while len(self._aec_near_buf) >= fbytes:
                near = bytes(self._aec_near_buf[:fbytes])
                del self._aec_near_buf[:fbytes]
                if len(self._aec_far_buf) >= fbytes:
                    far = bytes(self._aec_far_buf[:fbytes])
                    del self._aec_far_buf[:fbytes]
                else:
                    far = b"\x00" * fbytes
                try:
                    out.extend(aec.cancel(near, far))
                except Exception:
                    logger.debug("AEC cancel failed", exc_info=True)
                    out.extend(near)
        return bytes(out)

    @staticmethod
    def _pcm_rms(pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))

    def _far_ref_slice(self, length: int) -> bytes:
        if length <= 0:
            return b""
        with self._aec_buf_lock:
            # Use the MOST RECENT far-end playback slice for barge-in checks.
            # During half-duplex mute windows we don't consume far_buf through
            # _aec_process, so the buffer front can be stale and mismatched to
            # the current echo reaching the mic; that causes false "barge-in"
            # detections that cut speaker audio.
            if len(self._aec_far_buf) <= length:
                return bytes(self._aec_far_buf)
            return bytes(self._aec_far_buf[-length:])

    def _far_ref_rms(self, length: int) -> float:
        return self._pcm_rms(self._far_ref_slice(length))

    @staticmethod
    def _echo_similarity(mic_pcm16: bytes, ref_pcm16: bytes) -> float:
        """Cosine similarity between current mic and far-end playback slices.

        Echo-only frames are highly similar to far-end playback. User barge-in
        speech mixed on top of echo drops similarity even when RMS does not
        exceed a strict amplitude ratio threshold.
        """
        if not mic_pcm16 or not ref_pcm16:
            return 1.0
        mic = np.frombuffer(mic_pcm16, dtype=np.int16).astype(np.float32)
        ref = np.frombuffer(ref_pcm16, dtype=np.int16).astype(np.float32)
        n = min(len(mic), len(ref))
        if n < 80:
            return 1.0
        mic = mic[:n]
        ref = ref[:n]
        mic -= float(np.mean(mic))
        ref -= float(np.mean(ref))
        denom = float(np.linalg.norm(mic) * np.linalg.norm(ref))
        if denom <= 1e-6:
            return 1.0
        sim = float(np.dot(mic, ref) / denom)
        return max(-1.0, min(1.0, sim))

    def _detect_local_barge_in(
        self,
        mic_pcm16: bytes,
        *,
        now: float,
    ) -> tuple[bool, float, float, float, float]:
        """Detect live user speech while normal mic upload is muted for echo.

        This is intentionally separate from server VAD. Server VAD cannot see a
        half-duplex interruption because the mic frames are being withheld; this
        detector only decides when to stop local playback and release the user's
        new turn.
        """
        if not _LOCAL_BARGE_IN_ENABLED:
            return False, 0.0, 0.0, 0.0, 1.0
        if now - self._barge_in_last_cancel_at < 0.45:
            return False, 0.0, 0.0, 0.0, 1.0
        # Ignore detector noise when no assistant response/audio is active.
        # False positives in this idle tail can suppress the next reply's first
        # chunks and appear as "transcript but no speaker audio".
        if not (self._response_in_progress or self.audio_playback_remaining_s() > 0.12):
            return False, 0.0, 0.0, 0.0, 1.0
        mic_rms = self._pcm_rms(mic_pcm16)
        ref_pcm = self._far_ref_slice(len(mic_pcm16))
        ref_rms = self._pcm_rms(ref_pcm)
        echo_similarity = self._echo_similarity(mic_pcm16, ref_pcm)
        baseline = self._barge_in_noise_rms
        if baseline <= 0.0:
            baseline = ref_rms if ref_rms > 0.0 else mic_rms
            self._barge_in_noise_rms = baseline
        threshold = max(
            _LOCAL_BARGE_IN_MIN_RMS,
            ref_rms * _LOCAL_BARGE_IN_REF_RATIO,
            baseline * _LOCAL_BARGE_IN_BASELINE_RATIO,
        )
        # Two independent barge-in paths:
        # 1) classic RMS spike over playback-ref threshold
        # 2) optional divergence from far-end echo (low similarity) even if RMS
        #    stays below a strict loudness threshold while assistant audio is loud.
        # Divergence mode is intentionally OFF by default because some hardware
        # routes introduce enough speaker->mic coloration to look like "diverged"
        # echo and cause false self-interruption.
        loud_enough = mic_rms >= threshold
        # Guard against strong pure echo spikes from external mic/speaker
        # coupling: if mic looks almost identical to far-end playback and is
        # only modestly louder than the reference, treat it as self-audio.
        if (
            loud_enough
            and ref_rms > 0.0
            and echo_similarity >= _LOCAL_BARGE_IN_SPIKE_ECHO_SIMILARITY_GUARD
            and mic_rms <= (ref_rms * _LOCAL_BARGE_IN_SPIKE_ECHO_MAX_REF_RATIO)
        ):
            loud_enough = False
        diverged_from_echo = False
        if _LOCAL_BARGE_IN_ECHO_DIVERGENCE_ENABLED:
            diverged_from_echo = (
                ref_rms > 0.0
                and baseline > 0.0
                and mic_rms >= max(
                    _LOCAL_BARGE_IN_MIN_RMS * 0.55,
                    baseline * _LOCAL_BARGE_IN_ECHO_MIN_BASELINE_RATIO,
                    ref_rms * _LOCAL_BARGE_IN_ECHO_MIN_REF_RATIO,
                    280.0,
                )
                and echo_similarity <= _LOCAL_BARGE_IN_MAX_ECHO_SIMILARITY
            )
        detected = loud_enough or diverged_from_echo
        if detected:
            self._barge_in_consecutive += 1
        else:
            self._barge_in_consecutive = 0
            # Track the echo/noise floor while muted; keep it slow so a user's
            # first syllable remains a spike rather than becoming the baseline.
            self._barge_in_noise_rms = (baseline * 0.96) + (mic_rms * 0.04)
        return (
            self._barge_in_consecutive >= _LOCAL_BARGE_IN_MIN_FRAMES,
            mic_rms,
            ref_rms,
            threshold,
            echo_similarity,
        )

    def _reset_local_barge_state(self) -> None:
        self._barge_in_consecutive = 0
        self._barge_in_noise_rms = 0.0
        self._barge_in_preroll.clear()

    def _log_voice_event(self, event: str, **fields: Any) -> None:
        payload = {
            "event": event,
            "ts": round(time.time(), 3),
            **fields,
        }
        try:
            logger.info("VOICE_EVENT %s", json.dumps(payload, sort_keys=True))
        except Exception:
            logger.info("VOICE_EVENT %s %s", event, fields)

    async def _cancel_for_local_barge_in(
        self,
        ws,
        *,
        mic_rms: float,
        ref_rms: float,
        threshold: float,
        echo_similarity: float,
    ) -> None:
        self._barge_in_last_cancel_at = time.monotonic()
        self._abort_aplay()
        self._suppress_audio_until = time.monotonic() + _BARGE_IN_SUPPRESS_AUDIO_S
        self._emit_state("listening")
        detection_mode = "rms_spike" if mic_rms >= threshold else "echo_divergence"
        self._log_voice_event(
            "barge_in_detected",
            mic_rms=round(mic_rms, 1),
            ref_rms=round(ref_rms, 1),
            threshold=round(threshold, 1),
            echo_similarity=round(echo_similarity, 3),
            detection_mode=detection_mode,
            half_duplex=self._half_duplex,
        )
        try:
            await ws.send(json.dumps({"type": "response.cancel"}))
            self._log_voice_event("response_cancel_sent", source="local_barge_in")
        except Exception:
            logger.debug("local barge-in response.cancel failed", exc_info=True)

    async def _upload_resampled_audio(
        self, ws, resampled: bytes, *, speech_prob: float | None = None
    ) -> None:
        if self._aec is not None:
            resampled = self._aec_process(resampled)
            if not resampled:
                return
        # Score the exact audio the server will hear (post-AEC, post-gates)
        # against the local VAD so every committed turn has acoustic ground
        # truth attached (transcript validation + interrupt gating).
        if self._speech_monitor is not None:
            try:
                self._speech_monitor.observe(
                    resampled,
                    speech_prob=speech_prob,
                    echo_risk=self._uplink_echo_risk(),
                    echo_active=self._uplink_echo_active(),
                )
            except Exception:
                logger.debug("speech monitor observe failed", exc_info=True)
        if self._debug_uplink_tap is not None:
            self._debug_uplink_tap.write(resampled)
        # Feed the same echo-cancelled PCM to the live-caption recognizer
        # (non-blocking; dropped if the side thread falls behind).
        if self._caption_q is not None:
            try:
                self._caption_q.put_nowait(resampled)
            except queue.Full:
                pass
        # Transcode to the wire codec at the very last step. AEC, captions and
        # the speech monitor above all consumed the full 24 kHz PCM16; only the
        # bytes we actually upload are shrunk (G.711 mu-law 8 kHz ~= 1/6 the
        # PCM16 bitrate), so a marginal upstream can sustain continuous speech.
        if self._uplink_g711 and self._uplink_resampler is not None:
            wire_bytes = _pcm16_to_ulaw(self._uplink_resampler.process(resampled))
        else:
            wire_bytes = resampled
        if not wire_bytes:
            return
        payload = base64.b64encode(wire_bytes).decode("ascii")
        _send_t0 = time.monotonic()
        await ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": payload,
        }))
        _send_dt = time.monotonic() - _send_t0
        self._frames_sent += 1
        self._last_frame_sent_at = time.monotonic()
        if _send_dt > self._max_send_dt:
            self._max_send_dt = _send_dt
        if _send_dt > 1.0:
            logger.warning(
                "Realtime uplink send stalled %.1fs (network/backpressure — "
                "WS keepalive + audio uplink at risk)", _send_dt,
            )
        self._touch()

    # ------------------------------------------------------------------
    # Mic pump (asyncio side)
    # ------------------------------------------------------------------

    def _apply_input_gain(self, pcm16: bytes) -> bytes:
        """Apply optional desktop digital gain with hard clipping."""
        if _REALTIME_INPUT_GAIN == 1.0 or not pcm16:
            return pcm16
        s = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
        s *= _REALTIME_INPUT_GAIN
        np.clip(s, -32768.0, 32767.0, out=s)
        return s.astype(np.int16).tobytes()

    def _os_dsp_gate_buffer_preroll(self, pcm16: bytes) -> None:
        """Hold onset frames so a confirmed barge-in loses no leading audio."""
        if _OS_DSP_GATE_PREROLL_S <= 0.0 or not pcm16:
            return
        self._os_dsp_gate_preroll.append(pcm16)
        self._os_dsp_gate_preroll_bytes += len(pcm16)
        max_bytes = int(_REALTIME_RATE * 2 * _OS_DSP_GATE_PREROLL_S)
        while (
            self._os_dsp_gate_preroll_bytes > max_bytes
            and self._os_dsp_gate_preroll
        ):
            dropped = self._os_dsp_gate_preroll.popleft()
            self._os_dsp_gate_preroll_bytes -= len(dropped)

    def _os_dsp_gate_should_send(self, near_pcm16: bytes) -> bool:
        """Render-clock double-talk gate for the Windows OS-DSP uplink.

        The OS Voice Capture DSP cancels the assistant's echo at the source, but
        residual survives on loud playback. Streamed continuously, that residual
        trips the server VAD into a phantom one-word turn. While the assistant is
        playing (per the reliable render clock) we withhold frames that merely
        sit at the residual-echo floor and forward only genuine double-talk
        (clearly louder than that floor), so residual echo never reaches the
        server. When the assistant is silent the gate is fully open — normal user
        turns are untouched. Unlike the AEC3 gate this needs no far-end signal:
        the render clock alone tells us when echo is possible, which sidesteps the
        loopback onset-blind-window and clock-drift that made the AEC3 gate fragile.
        """
        if not _OS_DSP_GATE_ENABLED:
            return True
        now = time.monotonic()
        playback_active = (
            self.audio_playback_remaining_s() > 0.03
            or self._state == "speaking"
        )
        if playback_active:
            self._os_dsp_gate_active_until = now + _OS_DSP_GATE_COOLDOWN_S
        if now >= self._os_dsp_gate_active_until:
            # Assistant effectively silent -> no echo risk; gate fully open.
            self._os_dsp_gate_open_until = 0.0
            self._os_dsp_gate_floor = 0.0
            self._os_dsp_gate_consecutive = 0
            if self._os_dsp_gate_preroll:
                self._os_dsp_gate_preroll.clear()
                self._os_dsp_gate_preroll_bytes = 0
            return True
        near_rms = self._pcm_rms(near_pcm16)
        floor = self._os_dsp_gate_floor
        threshold = max(_OS_DSP_GATE_MIN_RMS, floor * _OS_DSP_GATE_FLOOR_RATIO)
        # Gate already open (recent sustained double-talk): keep passing frames
        # through the hangover window so a real utterance flows across pauses.
        if now < self._os_dsp_gate_open_until:
            if near_rms >= threshold:
                self._os_dsp_gate_open_until = now + _OS_DSP_GATE_HANGOVER_S
            return True
        if near_rms >= threshold:
            self._os_dsp_gate_consecutive += 1
            if self._os_dsp_gate_consecutive >= _OS_DSP_GATE_CONSEC_FRAMES:
                self._os_dsp_gate_consecutive = 0
                self._os_dsp_gate_open_until = now + _OS_DSP_GATE_HANGOVER_S
                self._log_voice_event(
                    "os_dsp_gate_opened",
                    near_rms=round(near_rms, 1),
                    threshold=round(threshold, 1),
                )
                return True
            # Not yet confirmed — hold the onset frame in the preroll.
            self._os_dsp_gate_buffer_preroll(near_pcm16)
            return False
        # Residual echo only: reset the run, adapt the floor from this real
        # residual sample (fast initial seed, slow thereafter), buffer the
        # preroll, and withhold. Never seed the floor from a speech-level frame.
        self._os_dsp_gate_consecutive = 0
        self._os_dsp_gate_floor = (
            near_rms if floor <= 0.0 else (floor * 0.95) + (near_rms * 0.05)
        )
        self._os_dsp_gate_buffer_preroll(near_pcm16)
        self._os_dsp_gate_suppressed += 1
        if now - self._os_dsp_gate_last_log >= 2.0:
            self._os_dsp_gate_last_log = now
            self._log_voice_event(
                "os_dsp_residual_gated",
                near_rms=round(near_rms, 1),
                threshold=round(threshold, 1),
                suppressed=self._os_dsp_gate_suppressed,
            )
        return False

    def _aec3_gate_should_send(self, near_pcm24: bytes, far_pcm48: bytes) -> bool:
        """Residual-echo gate for the AEC3 uplink.

        Returns True if the cleaned near-end frame should be forwarded to the
        server. While the assistant is playing, frames that merely sit at the
        post-AEC echo-residual floor are withheld so leftover echo cannot trip
        the server VAD into a phantom turn; genuine double-talk (clearly louder
        than the residual floor) passes through immediately. When the assistant
        is silent the gate is fully open, so normal user turns are untouched.
        """
        if not _AEC3_RESIDUAL_GATE_ENABLED:
            return True
        now = time.monotonic()
        far_rms = self._pcm_rms(far_pcm48)
        # Loopback references can trail the first playback chunks on some
        # desktop routes. Use local playback clock/state as an additional
        # "assistant is active" signal so onset residual cannot leak through
        # before loopback RMS climbs.
        playback_active = (
            self.audio_playback_remaining_s() > 0.03
            or self._state == "speaking"
        )
        # Arm the echo-suppression window. Prefer the reliable render clock: if
        # the assistant is (or was just) playing, hold the window through a
        # cooldown after the last playback frame so trailing room-resonance /
        # decaying echo of the final words cannot reach the server VAD. Fall
        # back to the far-end RMS (loopback) with only a short hangover when the
        # render clock is idle, to absorb reference flicker around the tail.
        if playback_active:
            self._aec3_far_active_until = now + _AEC3_GATE_COOLDOWN_S
        elif far_rms >= _AEC3_GATE_FAR_ACTIVE_RMS:
            self._aec3_far_active_until = max(
                self._aec3_far_active_until,
                now + _AEC3_GATE_FAR_ACTIVE_HANGOVER_S,
            )
        if now >= self._aec3_far_active_until:
            # Assistant effectively silent -> no echo risk; keep the gate open.
            self._aec3_gate_open_until = 0.0
            self._aec3_residual_floor = 0.0
            self._aec3_gate_consecutive = 0
            if self._aec3_gate_preroll:
                self._aec3_gate_preroll.clear()
                self._aec3_gate_preroll_bytes = 0
            return True
        near_rms = self._pcm_rms(near_pcm24)
        floor = self._aec3_residual_floor
        # ERLE-aware threshold: the higher of the absolute floor, an adaptive
        # multiple of the measured residual floor, and a fraction of the current
        # far-end level (residual echo scales with playback level, especially
        # during AEC3 re-convergence). Genuine near-end speech clears all three;
        # loud-playback residual bursts do not.
        threshold = max(
            _AEC3_GATE_MIN_RMS,
            floor * _AEC3_GATE_FLOOR_RATIO,
            far_rms * _AEC3_GATE_FAR_LEAK_RATIO,
        )

        # Gate already confirmed open (recent sustained double-talk): keep
        # passing frames through the hangover window without re-confirming so a
        # real utterance flows uninterrupted across natural pauses.
        if now < self._aec3_gate_open_until:
            if near_rms >= threshold:
                self._aec3_gate_open_until = now + _AEC3_GATE_HANGOVER_S
            return True

        # AEC-blind guard: the assistant is playing but the reference has no
        # signal (far_rms ~ 0). With no reference AEC3 cannot subtract
        # anything, so whatever the mic hears right now is uncancellable echo
        # we cannot verify. Drop it outright so raw playback echo can never
        # reach the server transcriber. Two ways a reference goes blind:
        #   * loopback captures (active_capture) lag playback onset or drop
        #     out — always eligible;
        #   * the render-fed ring just underran (consumer clock ran ahead of
        #     the device tap) — eligible only while its starvation flag is
        #     up. Outside starvation, render-fed far silence is ground truth
        #     (a genuine pause between TTS words), and mic energy then may be
        #     REAL user onset that must flow into the normal near-vs-residual
        #     logic below, not be dropped.
        reference_live = far_rms >= _AEC3_GATE_FAR_ACTIVE_RMS
        reference_can_go_blind = bool(
            getattr(self._far_ref, "active_capture", False)
        ) or bool(getattr(self._far_ref, "starved_recently", False))
        if playback_active and not reference_live and reference_can_go_blind:
            self._aec3_gate_consecutive = 0
            self._aec3_gate_suppressed += 1
            if now - self._aec3_gate_last_log >= 2.0:
                self._aec3_gate_last_log = now
                self._log_voice_event(
                    "aec3_reference_blind_gated",
                    near_rms=round(near_rms, 1),
                    far_rms=round(far_rms, 1),
                    suppressed=self._aec3_gate_suppressed,
                )
            return False

        if near_rms >= threshold:
            # Candidate double-talk. A single above-threshold frame can be a
            # transient residual-echo spike, so require several consecutive
            # frames before opening the gate or recording speech evidence — a
            # short echo burst can never trip a phantom turn or self-interrupt.
            self._aec3_gate_consecutive += 1
            if self._aec3_gate_consecutive >= _AEC3_GATE_CONSEC_FRAMES:
                self._aec3_gate_consecutive = 0
                self._aec3_gate_open_until = now + _AEC3_GATE_HANGOVER_S
                self._aec3_recent_speech_evidence_until = (
                    now + _AEC3_SPEECH_EVIDENCE_WINDOW_S
                )
                self._log_voice_event(
                    "aec3_gate_opened",
                    near_rms=round(near_rms, 1),
                    far_rms=round(far_rms, 1),
                    threshold=round(threshold, 1),
                )
                return True
            # Not yet confirmed — hold the onset frame in the preroll so it is
            # flushed intact once (and if) the gate confirms.
            self._aec3_gate_buffer_preroll(near_pcm24)
            return False

        # Residual echo only: reset the run, seed/adapt the floor from real
        # residual frames, buffer the preroll, and withhold the frame (never
        # seed the floor from a speech-level sample).
        self._aec3_gate_consecutive = 0
        self._aec3_residual_floor = (
            near_rms if floor <= 0.0 else (floor * 0.95) + (near_rms * 0.05)
        )
        self._aec3_gate_buffer_preroll(near_pcm24)
        self._aec3_gate_suppressed += 1
        if now - self._aec3_gate_last_log >= 2.0:
            self._aec3_gate_last_log = now
            self._log_voice_event(
                "aec3_residual_gated",
                near_rms=round(near_rms, 1),
                far_rms=round(far_rms, 1),
                threshold=round(threshold, 1),
                suppressed=self._aec3_gate_suppressed,
            )
        return False

    def _aec3_gate_buffer_preroll(self, near_pcm24: bytes) -> None:
        """Append a withheld near-end frame to the bounded gate preroll ring."""
        self._aec3_gate_preroll.append(near_pcm24)
        self._aec3_gate_preroll_bytes += len(near_pcm24)
        budget = int(_REALTIME_RATE * 2 * _AEC3_GATE_PREROLL_S)
        while self._aec3_gate_preroll_bytes > budget and len(self._aec3_gate_preroll) > 1:
            dropped = self._aec3_gate_preroll.popleft()
            self._aec3_gate_preroll_bytes -= len(dropped)

    def _uplink_echo_risk(self, now: float | None = None) -> bool:
        """Is the uplink currently at risk of carrying speaker echo?

        True while the speaker is emitting audio (or within a decay hangover
        after it stops). Two oracles, best available wins:

          * Loopback ground truth (AEC3 path): ``_aec3_far_active_until`` is
            driven by the measured RMS of the WASAPI loopback capture — the
            post-mix signal the speaker is ACTUALLY rendering, including
            system sounds and other apps' audio that the playback clock can
            never see. This is authoritative.
          * Playback-clock estimate (all paths): our own play-until clock plus
            the "speaking" state. Kept as a floor because the loopback can be
            briefly blind at onset, and it is the only oracle on the OS-DSP /
            Speex paths.

        Evidence frames observed inside this window face the stricter
        double-talk bar in the monitor.
        """
        ts = time.monotonic() if now is None else now
        try:
            with self._playback_clock_lock:
                play_until = self._assistant_audio_play_until
        except Exception:
            play_until = 0.0
        # Anchor the hangover to when playback actually ends (the play-until
        # clock), not to when this happened to be called — uplink frames are
        # not guaranteed to flow continuously during playback (half-duplex
        # withholds them entirely).
        risk_until = self._playback_echo_risk_until
        if play_until > 0.0:
            risk_until = max(risk_until, play_until + _EVIDENCE_PLAYBACK_HANGOVER_S)
        if self._state == "speaking":
            risk_until = max(risk_until, ts + _EVIDENCE_PLAYBACK_HANGOVER_S)
        if self._aec3_far_active_until > 0.0:
            # Loopback heard real speaker output recently (any source).
            risk_until = max(
                risk_until,
                self._aec3_far_active_until + _EVIDENCE_PLAYBACK_HANGOVER_S,
            )
        self._playback_echo_risk_until = risk_until
        return ts <= risk_until

    def _uplink_echo_active(self, now: float | None = None) -> bool:
        """Is the speaker emitting audio RIGHT NOW (no hangover)?

        Distinct from :meth:`_uplink_echo_risk`: this excludes the decay
        hangover. Only frames captured inside this window are allowed to
        teach the monitor's residual-echo envelope — outside it there is no
        echo source, so the energy is ambient or the user's voice.
        """
        ts = time.monotonic() if now is None else now
        if self._state == "speaking":
            return True
        try:
            with self._playback_clock_lock:
                if self._assistant_audio_play_until > ts:
                    return True
        except Exception:
            pass
        return self._aec3_far_active_until >= ts

    def _turn_evidence_stats(
        self, *, item_id: str | None = None, now: float | None = None
    ) -> dict:
        """Evidence aggregate for the current/most-recent user turn.

        Once a turn has been committed, its evidence is FROZEN: the snapshot
        taken at input_audio_buffer.committed is returned as-is. Transcription
        finishes well after the commit, and any audio captured in between
        belongs to the NEXT turn — recomputing live would let the user's next
        utterance retroactively validate a phantom micro-turn.

        For a not-yet-committed turn the window opens shortly before the
        server's speech_started timestamp (covers VAD prefix padding + event
        latency) but never before the previous turn's commit, so back-to-back
        turns cannot cross-contaminate. When no speech_started was seen
        (rare), fall back to a generous recent window.
        """
        if self._speech_monitor is None:
            return {}
        if item_id:
            snapshot = self._turn_commit_stats.get(str(item_id))
            if snapshot is not None:
                return snapshot
        ts = time.monotonic() if now is None else now
        started_at = 0.0
        if item_id:
            started_at = self._turn_started_by_item.get(str(item_id), 0.0)
        if started_at <= 0.0:
            started_at = self._turn_started_at
        if started_at > 0.0:
            since = started_at - _EVIDENCE_TURN_LOOKBACK_S
        else:
            since = ts - 8.0
        if self._last_turn_commit_at > 0.0:
            since = max(since, self._last_turn_commit_at)
        return self._speech_monitor.stats_since(since)

    def _transcript_rejected_by_evidence(
        self, text: str, *, item_id: str | None = None
    ) -> tuple[bool, dict]:
        """Validate a final transcript against the turn's acoustic evidence.

        Returns (reject, stats). A transcript is rejected as an ASR
        hallucination when the audio the server actually received for this
        turn carried (near-)zero speech-like signal — the recognizer cannot
        have genuinely heard words in it. Short fragments (the classic
        hallucination shape: "it", "hello", ".") require a bit more evidence;
        real one-word answers ("yes", "confirm") comfortably clear it.
        Disabled paths (monitor off) never reject.
        """
        stats: dict = {}
        if not _TURN_EVIDENCE_ENABLED or self._speech_monitor is None:
            return False, stats
        spoken = (text or "").strip()
        if not spoken:
            return False, stats
        stats = self._turn_evidence_stats(item_id=item_id)
        speech_ms = float(stats.get("speech_ms") or 0.0)
        if speech_ms < _EVIDENCE_HARD_MIN_SPEECH_MS:
            return True, stats
        words = len(_normalize_words(spoken).split())
        if words <= _EVIDENCE_SHORT_MAX_WORDS and speech_ms < _EVIDENCE_SHORT_MIN_SPEECH_MS:
            return True, stats
        return False, stats

    def _client_turn_authority(self) -> bool:
        """True when the client (not the server VAD) owns turn-taking.

        With the evidence layer live, the session is configured with
        create_response/interrupt_response OFF and the client decides — at
        input_audio_buffer.committed, from acoustic evidence — whether a turn
        deserves a response at all. A phantom turn is deleted from the
        conversation before any response can exist, so the model never sees
        or answers words that were never spoken.
        """
        return _TURN_EVIDENCE_ENABLED and self._speech_monitor is not None

    def _turn_has_commit_evidence(self, stats: dict) -> bool:
        """Commit-time go/no-go: did this turn carry ANY genuine speech?

        Deliberately uses only the hard floor (not the stricter short-word
        bar) so a genuine turn is never left unanswered; the transcript-level
        validation still applies the finer rules once text exists.
        """
        return float(stats.get("speech_ms") or 0.0) >= _EVIDENCE_HARD_MIN_SPEECH_MS

    async def _excise_phantom_turn(self, ws, item_id: str, *, source: str) -> None:
        """Remove a phantom user turn from the server conversation.

        Deleting the item (rather than merely cancelling a response) is what
        keeps the model's context clean: the conversation history ends up
        exactly as if the phantom had never happened, so later responses
        cannot be steered by words nobody said.
        """
        if not item_id:
            return
        self._phantom_items.add(item_id)
        while len(self._phantom_items) > 32:
            self._phantom_items.pop()
        try:
            await ws.send(json.dumps({
                "type": "conversation.item.delete",
                "item_id": item_id,
            }))
            self._log_voice_event(
                "phantom_turn_deleted", item_id=item_id, source=source
            )
        except Exception:
            logger.debug("conversation.item.delete failed", exc_info=True)

    def _has_interrupt_speech_evidence(self, now: float) -> bool:
        """Should a server speech_started be allowed to interrupt the assistant?

        True when the assistant has nothing in flight (no audible playback and
        no response being generated — nothing to protect), or when the uplink
        recently carried locally-verified speech (the same audio the server
        VAD triggered on). A speech_started that no local speech evidence can
        explain is residual echo / noise — cancelling playback or an in-flight
        response for it is the false self-interruption bug.
        """
        protecting_output = (
            self._response_in_progress
            or self.audio_playback_remaining_s() > 0.05
            or self._state == "speaking"
            or (now - self._response_requested_at) < _RESPONSE_CREATE_PROTECT_S
        )
        if not protecting_output:
            return True
        if self._speech_monitor is None:
            # Evidence layer disabled — preserve pre-existing behavior.
            return not self._aec3_full_duplex or (
                now <= self._aec3_recent_speech_evidence_until
            )
        # The monitor is the SOLE authority here: it observes every frame the
        # server hears (including everything the AEC3 gate passed) and applies
        # the echo-aware double-talk bar. The AEC3 gate's own "double-talk"
        # opening must NOT override it — the gate opens on residual echo
        # bursts during loopback drift (measured: near RMS ~860 vs bar 1500),
        # and accepting it as interrupt evidence is what killed genuine
        # responses mid-sentence.
        return self._speech_monitor.recent_speech(
            _EVIDENCE_INTERRUPT_WINDOW_S, now=now
        )

    def _should_drop_aec3_phantom_transcript(self, text: str) -> bool:
        """Heuristic guard for tiny echo-only transcripts on AEC3 sessions.

        AEC3 removes most echo, but on some laptop paths a tiny residual can
        still trigger a server VAD micro-turn near assistant playback tail,
        producing one-token transcripts like "." / "it" / "the". Drop only
        those tiny fragments and only while assistant playback is still active
        (or immediately after it).
        """
        spoken = (text or "").strip()
        if not spoken:
            return False
        if not self._aec3_full_duplex:
            return False
        now = time.monotonic()
        if now > (self._assistant_audio_play_until + _AEC3_PHANTOM_TRANSCRIPT_TAIL_S):
            return False
        # Keep potentially-short real barge-ins when local near-end evidence
        # was recently observed.
        if now <= self._aec3_recent_speech_evidence_until:
            return False
        norm = _normalize_words(spoken)
        if not norm:
            return True
        return len(norm.split()) == 1

    def _drain_far(self, nbytes: int) -> None:
        """Drop ``nbytes`` from the FRONT of the AEC far-end ring.

        While the uplink is withheld during assistant playback, mic frames are
        not run through ``_aec_process`` (which is what normally consumes the
        far ring in lockstep). Without draining, the far ring's front goes
        stale, so when the uplink reopens the echo canceller subtracts the
        WRONG (old) reference and lets residual echo through — heard by the
        server as "random words". Draining keeps the front time-aligned.
        """
        if nbytes <= 0:
            return
        with self._aec_buf_lock:
            if len(self._aec_far_buf) >= nbytes:
                del self._aec_far_buf[:nbytes]

    async def _pump_mic(self) -> None:
        assert self._ws is not None
        ws = self._ws
        loop = asyncio.get_running_loop()
        native_sr = self._mic_native_sr
        if self._aec3_full_duplex:
            # AEC3 path: near-end mic -> 48 kHz (engine rate), cleaned -> 24 kHz
            # uplink. Anti-aliased resamplers (built once the capture rate is
            # known); identity when the mic is already at 48 kHz.
            if native_sr != _AEC3_RATE:
                self._aec3_in_resampler = _AntiAliasResampler(native_sr, _AEC3_RATE)
            self._aec3_out_resampler = _AntiAliasResampler(_AEC3_RATE, _REALTIME_RATE)
        elif IS_DESKTOP and native_sr != _REALTIME_RATE:
            self._mic_resampler = _AntiAliasResampler(native_sr, _REALTIME_RATE)
        resampler = self._mic_resampler

        if _AUDIO_DEBUG_DIR:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._debug_raw_tap = _DebugWavTap(
                os.path.join(_AUDIO_DEBUG_DIR, f"{stamp}_raw_mic_{native_sr}hz.wav"),
                native_sr,
            )
            self._debug_uplink_tap = _DebugWavTap(
                os.path.join(_AUDIO_DEBUG_DIR, f"{stamp}_uplink_{_REALTIME_RATE}hz.wav"),
                _REALTIME_RATE,
            )

        def _get() -> bytes:
            try:
                return self._audio_q.get(timeout=_MIC_QUEUE_POLL_S)
            except queue.Empty:
                return b""

        while not self._stop.is_set():
            piece = await loop.run_in_executor(None, _get)
            if piece is None:
                break
            if not piece:
                continue
            if self._debug_raw_tap is not None:
                self._debug_raw_tap.write(piece)
            try:
                # WebRTC AEC3 full-duplex path (preferred desktop echo engine).
                # Cancel the assistant's echo in software against the real
                # playback reference, then stream the clean near-end
                # continuously — server VAD + interrupt_response own barge-in.
                # No Speex, no mic muting, no energy heuristics. This is the
                # ChatGPT/Meet-grade model and the whole point of AEC3.
                if self._aec3_full_duplex:
                    if self._aec3_in_resampler is not None:
                        near48 = self._aec3_in_resampler.process(piece)
                    else:
                        near48 = piece
                    near48 = self._apply_input_gain(near48)
                    far48 = self._far_ref.read(len(near48)) if self._far_ref else b""
                    cleaned48 = self._aec3.process(near48, far48)
                    cleaned24 = (
                        self._aec3_out_resampler.process(cleaned48)
                        if self._aec3_out_resampler is not None else cleaned48
                    )
                    if cleaned24:
                        # WebRTC APM's spectral VAD scored this exact frame —
                        # feed it to the evidence monitor for better-than-RMS
                        # speech decisions. Exactly 0.0 means the native module
                        # does not expose the probability (wrapper fallback);
                        # treat that as unavailable rather than "not speech".
                        apm_prob = self._aec3.speech_probability
                        if apm_prob <= 0.0:
                            apm_prob = None
                        # Residual-echo gate: withhold echo-only frames so AEC3
                        # leftovers never trip the server VAD into a phantom
                        # turn; pass genuine speech (+ preroll) straight through.
                        if self._aec3_gate_should_send(cleaned24, far48):
                            if self._aec3_gate_preroll:
                                preroll = list(self._aec3_gate_preroll)
                                self._aec3_gate_preroll.clear()
                                self._aec3_gate_preroll_bytes = 0
                                for pf in preroll:
                                    await self._upload_resampled_audio(
                                        ws, pf, speech_prob=apm_prob
                                    )
                            await self._upload_resampled_audio(
                                ws, cleaned24, speech_prob=apm_prob
                            )
                    continue

                if resampler is not None:
                    resampled = resampler.process(piece)
                else:
                    resampled = resample_pcm16_mono(piece, native_sr, _REALTIME_RATE)
                resampled = self._apply_input_gain(resampled)
                now = time.monotonic()

                # OS-AEC full-duplex path: the Windows Voice Capture DSP already
                # removed the assistant's echo at the source, so the mic stream
                # is genuinely clean. Stream it continuously and let the server
                # VAD + interrupt_response handle barge-in — no Speex, no energy
                # heuristics, no mic muting. This is the phone/desktop
                # voice-assistant model and the whole point of the OS canceller.
                if self._os_aec_full_duplex:
                    # Residual-echo gate: while the assistant is playing, withhold
                    # frames that merely sit at the OS-DSP echo-residual floor so
                    # leftover echo never trips the server VAD into a phantom
                    # one-word turn; genuine speech (+ preroll) passes straight
                    # through. Fully open when the assistant is silent.
                    if self._os_dsp_gate_should_send(resampled):
                        if self._os_dsp_gate_preroll:
                            preroll = list(self._os_dsp_gate_preroll)
                            self._os_dsp_gate_preroll.clear()
                            self._os_dsp_gate_preroll_bytes = 0
                            for pf in preroll:
                                await self._upload_resampled_audio(ws, pf)
                        await self._upload_resampled_audio(ws, resampled)
                    continue

                # Desktop: once the server VAD has flagged user speech, trust it
                # and let frames through even inside the echo-mute window so the
                # full utterance is captured (AEC still strips the echo).
                force_uplink = IS_DESKTOP and now < self._force_uplink_until
                # Energy-based echo gate:
                # While the agent is speaking, suppress mic frames whose energy
                # is at or below the expected echo level (i.e. agent's own
                # voice bouncing off the room).  Frames that are significantly
                # louder than the playback reference pass through — that means
                # the USER is speaking and wants to barge in.
                # Threshold: mic RMS must exceed 40 % of the reference RMS
                # AND be above a minimum voice floor (300 ≈ -82 dBFS).
                # Both conditions ensure we don't pass near-silence or mild
                # echo while still allowing clear speech to interrupt.
                if now < self._mute_mic_uplink_until and not force_uplink:
                    self._barge_in_preroll.append(resampled)
                    detected, mic_rms, ref_rms, threshold, echo_similarity = self._detect_local_barge_in(
                        resampled,
                        now=now,
                    )
                    if detected:
                        await self._cancel_for_local_barge_in(
                            ws,
                            mic_rms=mic_rms,
                            ref_rms=ref_rms,
                            threshold=threshold,
                            echo_similarity=echo_similarity,
                        )
                        frames = list(self._barge_in_preroll)
                        self._reset_local_barge_state()
                        for frame in frames:
                            await self._upload_resampled_audio(ws, frame)
                        continue
                    if self._half_duplex:
                        # Half-duplex still withholds echo-contaminated mic
                        # frames, but local barge-in above can break out as
                        # soon as live user speech is detected. Keep the AEC
                        # far-end reference time-aligned while we withhold
                        # (desktop) so echo cancellation stays effective.
                        if IS_DESKTOP:
                            self._drain_far(len(resampled))
                        continue
                    # Full-duplex (echo-isolated puck): energy-based barge-in
                    # gate. Let a frame through only if the mic is clearly
                    # louder than the expected echo — i.e. the user is talking
                    # over the assistant.
                    mic_samples = np.frombuffer(resampled, dtype=np.int16).astype(np.float32)
                    mic_rms = float(np.sqrt(np.mean(mic_samples ** 2))) if len(mic_samples) else 0.0
                    with self._aec_buf_lock:
                        ref = bytes(self._aec_far_buf[:len(resampled)])
                    if ref:
                        ref_samples = np.frombuffer(ref, dtype=np.int16).astype(np.float32)
                        ref_rms = float(np.sqrt(np.mean(ref_samples ** 2)))
                    else:
                        ref_rms = 0.0
                    # Let through only if mic is clearly louder than the echo
                    barge_in = mic_rms > max(ref_rms * 0.4, 300.0)
                    if not barge_in:
                        continue
                else:
                    self._reset_local_barge_state()
                await self._upload_resampled_audio(ws, resampled)
            except websockets.ConnectionClosed:
                break
            except Exception:
                logger.debug("Realtime mic upload failed", exc_info=True)
                # Transient upload errors must not kill the session.
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # Idle watchdog
    # ------------------------------------------------------------------

    async def _loop_lag_monitor(self) -> None:
        """Measure event-loop responsiveness on the realtime-voice thread.

        This loop also services the WebSocket keepalive (ping/pong) and streams
        mic audio. If a plain ``asyncio.sleep(1.0)`` returns significantly late,
        the loop is being starved of CPU/GIL time — the prime suspect for
        mid-session stalls (pings ACK late -> server drops us; uplink frames
        stall -> the model hears silence). Logs only on significant lag so the
        signal is unambiguous when correlating with a stall/close.
        """
        interval = 1.0
        _hb_accum = 0.0
        _hb_last_frames = self._frames_sent
        while not self._stop.is_set():
            t0 = time.monotonic()
            await asyncio.sleep(interval)
            lag = (time.monotonic() - t0) - interval
            if lag >= 0.4:
                logger.warning(
                    "Realtime loop lag: sleep(%.1fs) overran by %.2fs "
                    "(event loop starved — WS keepalive/audio uplink at risk)",
                    interval, lag,
                )
            # Periodic uplink-health heartbeat. Because this coroutine shares the
            # loop with the WS keepalive and the mic pump, its steady ticking
            # PROVES the loop is alive. The deltas then localize any stall:
            #   * frames advancing + server events stale  -> receive/server stall
            #   * frames frozen + queue GROWING            -> outbound send stall
            #   * frames frozen + queue EMPTY              -> mic callback died
            _hb_accum += interval + max(0.0, lag)
            if _hb_accum >= 8.0:
                now = time.monotonic()
                sent = self._frames_sent
                try:
                    qdepth = self._audio_q.qsize()
                except Exception:
                    qdepth = -1
                logger.info(
                    "Realtime uplink heartbeat: frames=%d (+%d/%.0fs) q=%d "
                    "last_send=%.1fs ago last_server_evt=%.1fs ago "
                    "max_send_dt=%.2fs state=%s",
                    sent, sent - _hb_last_frames, _hb_accum, qdepth,
                    now - self._last_frame_sent_at,
                    now - self._last_server_event_at,
                    self._max_send_dt, self._state,
                )
                _hb_accum = 0.0
                _hb_last_frames = sent
                self._max_send_dt = 0.0

    async def _idle_watchdog(self) -> None:
        ws = self._ws
        if ws is None:
            return
        while not self._stop.is_set():
            await asyncio.sleep(1.0)

            # ---- Dead inbound link (half-open socket) ---------------------
            # We are actively streaming mic audio but the server has returned
            # NOTHING (no deltas, no VAD, no acks) for too long -> the inbound
            # half of the socket is dead. Reconnect immediately instead of
            # streaming into the void until the network reaps the zombie (~60 s).
            # Closing as UNEXPECTED (we do NOT set _user_ended) lets the
            # session-end handler re-arm the warm standby.
            now = time.monotonic()
            recv_silence = now - self._last_server_event_at
            uplink_active = (now - self._last_frame_sent_at) <= 2.0
            # `_caption_active` is True only between a server speech_started and
            # its speech_stopped: during that window a long single utterance
            # (dictation) legitimately produces no further server events, so we
            # suppress the fast trip and rely on the hard cap.
            in_acked_utterance = self._caption_active
            # A tool call's HTTP round-trip is another known-quiet window: the
            # server is waiting for OUR function_call_output, so it owes us no
            # events at all (and the recv loop is blocked awaiting the tool
            # result, freezing _last_server_event_at). A slow backend write
            # (e.g. assistant_intent email send taking 30s+) must never be
            # mistaken for a dead socket — suppress the check entirely; the
            # tool HTTP call has its own 90s timeout bounding this window.
            dead_link = (
                uplink_active
                and not self._tool_roundtrip_active
                and (
                    recv_silence >= _DEAD_LINK_HARD_S
                    or (
                        recv_silence >= _DEAD_LINK_RECV_SILENCE_S
                        and not in_acked_utterance
                    )
                )
            )
            if dead_link:
                # Silence threshold tripped — but that alone is only a
                # suspicion (a user quietly thinking between turns looks
                # identical). Confirm with a ws ping: pong -> link is alive,
                # the silence is legitimate; no pong -> genuinely dead.
                link_alive = False
                try:
                    pong_waiter = await ws.ping()
                    await asyncio.wait_for(
                        pong_waiter, timeout=_DEAD_LINK_PING_TIMEOUT_S
                    )
                    link_alive = True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    link_alive = False
                if link_alive:
                    # Treat the pong as a server liveness event so the
                    # detector re-arms (next probe only after another full
                    # silence threshold) instead of ping-spamming every tick.
                    self._last_server_event_at = time.monotonic()
                    logger.debug(
                        "Realtime: dead-link suspicion after %.1fs of server "
                        "silence — ping verified link alive; not reconnecting.",
                        recv_silence,
                    )
                    continue
                # Recompute after the ping wait so the logged numbers reflect
                # the moment of the decision (not up to 10s stale/negative).
                now = time.monotonic()
                recv_silence = now - self._last_server_event_at
                logger.warning(
                    "Realtime: inbound link dead — no server event for %.1fs and "
                    "ping unanswered for %.1fs while uplink active "
                    "(last_send=%.1fs ago, state=%s). Reconnecting.",
                    recv_silence, _DEAD_LINK_PING_TIMEOUT_S,
                    now - self._last_frame_sent_at, self._state,
                )
                self._log_voice_event(
                    "dead_link_reconnect",
                    recv_silence=round(recv_silence, 1),
                    ping_timeout=_DEAD_LINK_PING_TIMEOUT_S,
                    in_utterance=in_acked_utterance,
                    state=self._state,
                )
                # Leave _user_ended False so this counts as an unexpected drop
                # and the app re-arms a warm standby (wake word to resume).
                self._stop.set()
                try:
                    await ws.close()
                except Exception:
                    pass
                break

            if self._state == "speaking" or self._response_in_progress:
                # Stall recovery: if we're still "speaking" but the assistant
                # audio has fully drained and nothing has extended it for a
                # grace period, the response stopped streaming without a
                # response.done. Unstick the state so the AEC3 gate reopens
                # (mic works again) and the UI pill leaves "talking"; a healthy
                # response always flips to "listening" via response.*.done long
                # before this fires.
                if (
                    self._state == "speaking"
                    and self.audio_playback_remaining_s() <= 0.05
                    and (time.monotonic() - self._assistant_audio_play_until)
                    >= _RESPONSE_STALL_RECOVERY_S
                ):
                    self._log_voice_event(
                        "response_stall_recovered",
                        drained_for=round(
                            time.monotonic() - self._assistant_audio_play_until, 1
                        ),
                    )
                    self._response_in_progress = False
                    self._response_requested_at = 0.0
                    self._emit_state("listening")
                continue
            idle_for = time.monotonic() - self._last_activity_monotonic
            if idle_for >= _SESSION_IDLE_CLOSE_S:
                logger.info("Realtime: closing idle session after %.1fs", idle_for)
                self._user_ended = True
                self._stop.set()
                try:
                    await ws.close()
                except Exception:
                    pass
                break

    # ------------------------------------------------------------------
    # Receive loop — dispatch OpenAI events
    # ------------------------------------------------------------------

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        ws = self._ws
        try:
            async for raw in ws:
                if self._stop.is_set():
                    break
                self._last_server_event_at = time.monotonic()
                try:
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8")
                    msg = json.loads(raw)
                except Exception:
                    continue

                t = msg.get("type", "")

                # ---- Session lifecycle --------------------------------
                if t == "session.created":
                    self._log_session_summary(msg, label="session.created")
                    # Configure the session as soon as it is created — this runs
                    # during warm-standby hold too. The UI "connected" signal is
                    # emitted separately, only once the mic actually opens (see
                    # _async_main), so a held session never shows "listening".
                    if not self._session_update_sent:
                        self._session_update_sent = True
                        await self._send_session_update(ws)

                elif t == "session.updated":
                    self._log_session_summary(msg, label="session.updated")
                    # Cold sessions fire the wake greeting here, once the
                    # session.update is acked. Warm (prewarm) sessions complete
                    # this handshake while HELD — long before the user wakes
                    # them — so they fire the greeting on activate() instead
                    # (see _async_main), giving an instant "I'm listening".
                    if not self._prewarm:
                        await self._send_wake_greeting(ws)

                # ---- User speech --------------------------------------
                elif t == "input_audio_buffer.speech_started":
                    self._touch()
                    # Anchor the evidence window for this turn's transcript
                    # validation (see _turn_evidence_stats).
                    self._turn_started_at = time.monotonic()
                    _turn_item = str(msg.get("item_id") or "").strip()
                    if _turn_item:
                        self._turn_started_by_item[_turn_item] = self._turn_started_at
                        # Bounded: only recent turns matter (evidence retention
                        # is ~12 s anyway).
                        while len(self._turn_started_by_item) > 8:
                            self._turn_started_by_item.pop(
                                next(iter(self._turn_started_by_item))
                            )
                    _mon = self._speech_monitor
                    self._log_voice_event(
                        "speech_started",
                        noise_floor=round(_mon.noise_floor, 1) if _mon else None,
                        recent_speech=(
                            _mon.recent_speech(_EVIDENCE_INTERRUPT_WINDOW_S)
                            if _mon else None
                        ),
                        playback_remaining_s=round(self.audio_playback_remaining_s(), 2),
                    )
                    # The user is taking over — stop auto-driving the briefing so
                    # we don't fight their request (e.g. "skip to my emails").
                    # Hand the model the context it lacks (the briefing was on a
                    # temporary screen) so it can decide, by intent, whether to
                    # return to the transcription screen once it answers them.
                    if self._brief_active:
                        self._cancel_briefing()
                        await self._inject_brief_interruption_directive(ws)
                    # Fresh utterance — clear the streaming transcript buffer
                    # so partial deltas don't append to the previous turn.
                    self._user_transcript_buf = ""
                    self._active_user_transcript_item_id = ""
                    # Reset the live-caption recognizer + reset the UI bubble
                    # tracker so captions render into a fresh bubble.
                    self._caption_active = True
                    self._caption_reset.set()
                    self._emit_user_speech_started()
                    # A server speech_started may only stop playback when the
                    # uplink actually carried locally-verified speech recently
                    # (any engine path) — otherwise it is residual echo / noise
                    # tripping the server VAD, and killing playback for it is
                    # the false self-interruption bug. In half-duplex we
                    # additionally require a recent local barge-in confirm.
                    now_mono = time.monotonic()
                    has_local_speech_evidence = self._has_interrupt_speech_evidence(
                        now_mono
                    )
                    # OS-AEC full-duplex: the Windows Voice Capture DSP gives a
                    # genuinely clean mic, and the residual-echo gate only lets
                    # real speech reach the server — so a server speech_started
                    # here IS the evidence. Trust it and stop playback instantly
                    # (the phone/desktop full-duplex model). The local
                    # speech-monitor evidence gate below is for the Speex/AEC3
                    # paths, where residual echo can trip the server VAD; do not
                    # let it defer a genuine OS-AEC barge-in (assistant would
                    # otherwise finish its sentence before yielding).
                    # OS-AEC full-duplex trusts the server VAD as barge-in
                    # evidence — but only as a genuine barge-in, i.e. while the
                    # assistant is actually speaking. If nothing is queued for
                    # the speaker, a server speech_started is the user's own
                    # turn (or a residual-echo / trailing-speech VAD re-trigger),
                    # NOT an interrupt; cancelling the just-requested reply for
                    # it kills responses before they can speak and makes long
                    # dictation crawl. Below the playback floor, fall through to
                    # the local-evidence gate (which also honors the
                    # response-create protection window).
                    assistant_speaking = (
                        self.audio_playback_remaining_s() > _BARGE_IN_MIN_PLAYBACK_S
                    )
                    should_force_interrupt = (
                        (self._os_aec_full_duplex and assistant_speaking)
                        or (
                            has_local_speech_evidence
                            and (
                                not self._half_duplex
                                or (now_mono - self._barge_in_last_cancel_at) <= 0.9
                            )
                        )
                    )
                    if should_force_interrupt:
                        # User started talking. Cut playback now so they hear
                        # themselves, not the assistant. The server cancels
                        # the in-flight response on its own (interrupt_response).
                        self._abort_aplay()
                        self._suppress_audio_until = (
                            now_mono + _BARGE_IN_SUPPRESS_AUDIO_S
                        )
                        # Send an explicit cancel too. Some routes can take a
                        # little longer for server-side interruption.
                        try:
                            await ws.send(json.dumps({"type": "response.cancel"}))
                            self._log_voice_event("response_cancel_sent", source="speech_started")
                        except Exception:
                            logger.debug("speech_started response.cancel failed", exc_info=True)
                        # Trim — do NOT fully clear — the AEC far-end reference.
                        if self._aec is not None:
                            retain_bytes = int(_REALTIME_RATE * 0.3) * 2  # 300 ms PCM16
                            with self._aec_buf_lock:
                                if len(self._aec_far_buf) > retain_bytes:
                                    del self._aec_far_buf[: len(self._aec_far_buf) - retain_bytes]
                        self._emit_state("listening")
                    elif IS_DESKTOP and self._half_duplex:
                        # The server's VAD detected the user. On a desktop the
                        # local energy detector is unreliable (built-in mic +
                        # speakers), so instead of withholding the turn, open
                        # the gated uplink and flush the pre-roll so the full
                        # utterance reaches the server. AEC still strips the
                        # assistant's echo from these frames, and playback is
                        # only force-stopped once a real barge-in is confirmed.
                        self._force_uplink_until = (
                            time.monotonic() + _REALTIME_SPEECH_UPLINK_S
                        )
                        frames = list(self._barge_in_preroll)
                        self._barge_in_preroll.clear()
                        for frame in frames:
                            await self._upload_resampled_audio(ws, frame)
                        self._emit_state("listening")
                        self._log_voice_event(
                            "speech_started_uplink_opened",
                            reason="server_vad_desktop",
                            window_s=_REALTIME_SPEECH_UPLINK_S,
                        )
                    else:
                        reason = (
                            "no_local_speech_evidence"
                            if not has_local_speech_evidence
                            else "half_duplex_unconfirmed"
                        )
                        self._log_voice_event(
                            "speech_started_ignored",
                            reason=reason,
                        )

                elif t == "input_audio_buffer.speech_stopped":
                    self._touch()
                    # Close the desktop "trust server VAD" uplink window; the
                    # echo-mute gate resumes for the assistant's reply.
                    self._force_uplink_until = 0.0
                    self._log_voice_event("speech_stopped")
                    # Stop live captions — OpenAI's accurate transcript now
                    # owns the bubble for this finished utterance.
                    self._caption_active = False
                    # Tell the UI to drop in a placeholder user bubble right
                    # away so the gap before transcription/AI is filled with
                    # immediate visual feedback — but ONLY when the turn
                    # actually carried speech. A phantom turn (echo/noise)
                    # must never paint a "…" user bubble the dropped
                    # transcript can never replace.
                    _stop_evidence_ok = True
                    if self._client_turn_authority():
                        _stop_evidence_ok = self._turn_has_commit_evidence(
                            self._turn_evidence_stats()
                        )
                    if _stop_evidence_ok:
                        self._emit_state("thinking")
                        self._emit_user_speech_stopped()

                elif t == "input_audio_buffer.committed":
                    # A turn was committed to the conversation. This is the
                    # client-authority decision point: the server VAD only
                    # segmented the audio — whether the turn deserves a
                    # response is decided HERE, from acoustic evidence.
                    self._touch()
                    _commit_item = str(msg.get("item_id") or "")
                    _commit_stats = (
                        self._turn_evidence_stats(item_id=_commit_item)
                        if self._speech_monitor is not None else {}
                    )
                    # Freeze this turn's evidence NOW. The transcription that
                    # arrives later must be judged against the audio the turn
                    # actually contained — not against whatever the user says
                    # next while the ASR is still working.
                    if _commit_item and self._speech_monitor is not None:
                        self._turn_commit_stats[_commit_item] = dict(_commit_stats)
                        while len(self._turn_commit_stats) > 8:
                            self._turn_commit_stats.pop(
                                next(iter(self._turn_commit_stats))
                            )
                    self._last_turn_commit_at = time.monotonic()
                    self._log_voice_event(
                        "turn_committed",
                        item_id=_commit_item,
                        **_commit_stats,
                    )
                    if self._client_turn_authority():
                        if self._turn_has_commit_evidence(_commit_stats):
                            # Genuine speech: request the response ourselves
                            # (create_response is off server-side).
                            try:
                                await ws.send(json.dumps({"type": "response.create"}))
                                self._response_requested_at = time.monotonic()
                                self._log_voice_event(
                                    "response_create_sent",
                                    source="turn_commit_evidence",
                                    item_id=_commit_item,
                                )
                            except Exception:
                                logger.debug(
                                    "commit-gate response.create failed",
                                    exc_info=True,
                                )
                        else:
                            # No genuine speech reached the server for this
                            # turn: excise it before it can influence the
                            # conversation. No response is ever created, so
                            # there is nothing to cancel and nothing to hear.
                            await self._excise_phantom_turn(
                                ws, _commit_item, source="commit_evidence_gate"
                            )

                # ---- User transcript (streaming partial) ----------------
                # Newer API versions emit partial transcripts as deltas so
                # the user's words appear character-by-character. Forward
                # whatever text they contain so the UI can update the
                # placeholder bubble live.
                elif t in (
                    "conversation.item.input_audio_transcription.delta",
                    "input_audio_buffer.transcription.delta",
                ):
                    self._touch()
                    # Deltas are INCREMENTAL fragments — accumulate them so
                    # the bubble shows the growing sentence, not just the
                    # latest fragment (mirrors the AI transcript buffer).
                    delta = msg.get("delta")
                    if not isinstance(delta, str) or not delta:
                        delta = self._extract_transcript(msg)
                    if str(msg.get("item_id") or "") in self._phantom_items:
                        # Turn already excised as a phantom — its (late)
                        # transcription must never reach the UI.
                        delta = None
                    if isinstance(delta, str) and delta:
                        item_id = (
                            msg.get("item_id")
                            or msg.get("response_id")
                            or self._active_user_transcript_item_id
                            or "user_active"
                        )
                        if item_id != self._active_user_transcript_item_id:
                            self._active_user_transcript_item_id = str(item_id)
                            self._user_transcript_buf = ""
                        self._user_transcript_buf += delta
                        # Suppress streaming partials that are prompt-echo
                        # hallucinations, and partials for turns with no local
                        # speech evidence (probable hallucination — the final
                        # validation below makes the authoritative call), so a
                        # phantom never paints a bubble.
                        # Partial — not final, so the UI skips grammar
                        # correction until the .completed event.
                        evidence_ok = True
                        if _TURN_EVIDENCE_ENABLED and self._speech_monitor is not None:
                            evidence_ok = (
                                float(
                                    self._turn_evidence_stats(
                                        item_id=str(msg.get("item_id") or "")
                                    ).get("speech_ms") or 0.0
                                )
                                >= _EVIDENCE_HARD_MIN_SPEECH_MS
                            )
                        if evidence_ok and not _is_prompt_echo(self._user_transcript_buf):
                            self._emit_user_transcript(
                                self._user_transcript_buf,
                                is_final=False,
                                item_id=str(item_id),
                            )

                # ---- User transcript (final) ---------------------------
                elif t in (
                    "conversation.item.input_audio_transcription.completed",
                    "input_audio_buffer.transcription.completed",
                ):
                    self._touch()
                    spoken = self._extract_transcript(msg)
                    _final_item = str(msg.get("item_id") or "")
                    # Utterance finished — reset the streaming buffer so the
                    # next utterance starts clean.
                    self._user_transcript_buf = ""
                    self._active_user_transcript_item_id = ""
                    if spoken and _final_item in self._phantom_items:
                        # This turn was already excised at commit time; its
                        # late transcription is noise by definition.
                        self._log_voice_event(
                            "phantom_transcript_dropped",
                            text=spoken,
                            source="excised_turn",
                        )
                        spoken = ""
                    if spoken and _is_prompt_echo(spoken):
                        logger.debug("Realtime: dropped prompt-echo phantom %r", spoken)
                        spoken = ""
                    # Authoritative hallucination check: does the audio the
                    # server actually received for this turn support ANY
                    # transcript at all? Engine-agnostic, works while the
                    # assistant speaks and while it is idle.
                    evidence_stats: dict = {}
                    if spoken:
                        rejected, evidence_stats = (
                            self._transcript_rejected_by_evidence(
                                spoken, item_id=_final_item
                            )
                        )
                        self._log_voice_event(
                            "turn_audit",
                            text=spoken,
                            accepted=not rejected,
                            **evidence_stats,
                        )
                        if rejected:
                            logger.info(
                                "Realtime: dropped hallucinated transcript %r "
                                "(no speech evidence: %s)",
                                spoken, evidence_stats,
                            )
                            self._log_voice_event(
                                "phantom_transcript_dropped",
                                text=spoken,
                                source="speech_evidence_guard",
                                **evidence_stats,
                            )
                            spoken = ""
                            # The turn passed the coarse commit gate but its
                            # text failed the finer validation. Excise the
                            # item so the model's context stays clean, stop
                            # the response we requested for it, and silence
                            # whatever already reached the speaker.
                            await self._excise_phantom_turn(
                                ws, _final_item, source="speech_evidence_guard"
                            )
                            try:
                                await ws.send(json.dumps({"type": "response.cancel"}))
                                self._log_voice_event(
                                    "response_cancel_sent",
                                    source="speech_evidence_guard",
                                )
                            except Exception:
                                logger.debug(
                                    "evidence guard response.cancel failed",
                                    exc_info=True,
                                )
                            self._abort_aplay()
                            self._suppress_audio_until = (
                                time.monotonic() + _BARGE_IN_SUPPRESS_AUDIO_S
                            )
                            self._emit_user_transcript_rejected(_final_item)
                    self._turn_started_at = 0.0
                    if spoken and self._should_drop_aec3_phantom_transcript(spoken):
                        logger.info("Realtime: dropped probable AEC3 phantom %r", spoken)
                        self._log_voice_event(
                            "phantom_transcript_dropped",
                            text=spoken,
                            source="aec3_tail_guard",
                        )
                        spoken = ""
                        # Best-effort: if the server already started forming a
                        # response for this phantom micro-turn, stop it.
                        try:
                            await ws.send(json.dumps({"type": "response.cancel"}))
                            self._log_voice_event(
                                "response_cancel_sent",
                                source="phantom_transcript_guard",
                            )
                        except Exception:
                            logger.debug(
                                "phantom transcript response.cancel failed",
                                exc_info=True,
                            )
                    if spoken:
                        logger.info("User said: %r", spoken)
                        self._log_voice_event(
                            "final_transcript",
                            text=spoken,
                            transcript_model=_DEFAULT_INPUT_TRANSCRIPTION_MODEL,
                        )
                        self._emit_user_transcript(
                            spoken, is_final=True, item_id=_final_item
                        )
                        # Client-side farewell fallback: if the transcript is
                        # a clear goodbye phrase, close the session immediately
                        # without waiting for the model to call end_session.
                        # This ensures farewell always works even if the model
                        # is busy with a slow tool call (e.g. mem0 rate-limit).
                        #
                        # BUT during an active email workflow the user naturally
                        # says closers like "that's it" / "thanks" / "okay done"
                        # as part of dictating or confirming — which would tear
                        # the session down with no goodbye. While the workflow is
                        # active we defer entirely to the model's contextual
                        # end_session tool instead of the keyword fallback.
                        if _is_farewell(spoken) and not self._farewell_suppressed():
                            logger.info(
                                "Realtime: client-side farewell detected %r — closing.", spoken
                            )
                            self._user_ended = True
                            self._stop.set()
                            try:
                                await ws.close()
                            except Exception:
                                pass
                            break
                        if _is_morning_brief_request(spoken):
                            logger.info(
                                "Realtime: client-side morning brief detected %r — starting visual briefing.",
                                spoken,
                            )
                            await self._start_briefing_from_user_request(ws)

                # ---- AI audio transcript (text of what assistant said) ----
                # OpenAI renamed these events in newer API versions, mirroring
                # the audio event rename (response.audio.* -> response.output_audio.*).
                # Accept BOTH names so we don't silently miss assistant text
                # on whichever version the server is on today.
                elif t in (
                    "response.audio_transcript.done",
                    "response.output_audio_transcript.done",
                ):
                    self._touch()
                    ai_text = self._extract_transcript(msg)
                    # Fall back to whatever we accumulated from delta events
                    if not ai_text:
                        ai_text = self._ai_transcript_buf.strip()
                    item_id = (
                        msg.get("item_id")
                        or msg.get("response_id")
                        or self._active_ai_transcript_item_id
                        or "ai_active"
                    )
                    self._ai_transcript_buf = ""
                    self._active_ai_transcript_item_id = ""
                    if ai_text:
                        logger.info("AI said: %r", ai_text)
                        # Final streaming update to make sure the bubble
                        # text exactly matches the .done payload, then a
                        # backward-compatible emit for non-streaming consumers.
                        self._emit_ai_transcript_delta(str(item_id), ai_text)
                        self._emit_ai_transcript(ai_text)

                # Stream the AI transcript live as deltas arrive so the
                # bubble updates word-by-word alongside the audio playback.
                # Also accumulated as a fallback if .done never fires.
                elif t in (
                    "response.audio_transcript.delta",
                    "response.output_audio_transcript.delta",
                ):
                    delta = msg.get("delta")
                    if isinstance(delta, str) and delta:
                        item_id = (
                            msg.get("item_id")
                            or msg.get("response_id")
                            or self._active_ai_transcript_item_id
                            or "ai_active"
                        )
                        # If item_id changed, start a fresh buffer for the
                        # new assistant response.
                        if item_id != self._active_ai_transcript_item_id:
                            self._active_ai_transcript_item_id = str(item_id)
                            self._ai_transcript_buf = ""
                        self._ai_transcript_buf += delta
                        self._emit_ai_transcript_delta(
                            str(item_id), self._ai_transcript_buf
                        )

                # ---- Model response lifecycle -------------------------
                elif t in ("response.created", "response.started"):
                    self._touch()
                    # A new response is starting; clear any leftover
                    # barge-in suppression so its audio plays cleanly.
                    self._suppress_audio_until = 0.0
                    self._response_in_progress = True
                    self._emit_state("thinking")

                elif t in ("response.output_audio.delta", "response.audio.delta"):
                    item_id = msg.get("item_id")
                    if isinstance(item_id, str) and item_id.strip():
                        self._active_audio_item_id = item_id
                    try:
                        self._active_audio_content_index = int(
                            msg.get("content_index") or 0
                        )
                    except (TypeError, ValueError):
                        pass
                    self._touch()
                    self._response_in_progress = True
                    self._emit_state("speaking")
                    if self._brief_active:
                        self._brief_narration_audio_seen = True
                    self._play_delta(self._extract_audio_delta(msg))

                elif t in ("response.output_audio.done", "response.audio.done"):
                    self._touch()
                    # Don't close aplay between responses — the writer
                    # queue may still hold seconds of buffered audio that
                    # haven't reached the speaker yet. Closing now would
                    # truncate the tail. aplay underruns silently between
                    # responses and resumes on the next delta.
                    self._emit_state("listening")

                elif t == "response.done":
                    self._touch()
                    # If the .done transcript event never arrived but we
                    # accumulated deltas, flush them now.
                    leftover = self._ai_transcript_buf.strip()
                    self._ai_transcript_buf = ""
                    self._active_ai_transcript_item_id = ""
                    if leftover:
                        logger.info("AI said (flushed from deltas): %r", leftover)
                        self._emit_ai_transcript(leftover)
                    # Run the handler (tool HTTP round-trips can take 30s+) in
                    # the background so this recv loop keeps draining frames:
                    # blocking here pauses the websocket reader once its buffer
                    # fills, the server's keepalive pings go unanswered, and
                    # OpenAI drops the connection mid-tool-call.
                    task = asyncio.create_task(
                        self._run_response_done_handler(ws, msg)
                    )
                    self._response_done_tasks.add(task)
                    task.add_done_callback(self._response_done_tasks.discard)
                    self._response_in_progress = False
                    self._response_requested_at = 0.0
                    self._active_audio_item_id = None
                    self._active_audio_content_index = 0
                    # _play_delta already extended the mute window to cover
                    # the audio tail; no extra holdoff needed here.
                    self._emit_state("listening")

                elif t == "response.function_call_arguments.done":
                    logger.info(
                        "Realtime function_call.done: name=%s call_id=%s args=%s",
                        msg.get("name"),
                        msg.get("call_id"),
                        (msg.get("arguments") or "")[:200],
                    )

                # ---- Errors -------------------------------------------
                elif t in ("error", "invalid_request_error"):
                    err = msg.get("error") if t == "error" else msg
                    if isinstance(err, dict):
                        em = err.get("message") or err.get("code") or str(err)
                    else:
                        em = str(err or msg)
                    em_lower = (em or "").lower()
                    if any(s in em_lower for s in _SAFE_TO_IGNORE_ERRORS):
                        logger.debug("Realtime ignorable error: %s", em)
                        continue
                    # Loud but NOT _emit_error: the UI terminates the
                    # session on any error callback, and most server-side
                    # errors here are non-fatal. Real failures close the
                    # WS itself and surface via _async_main's except.
                    logger.warning("Realtime server (non-fatal) error: %s", em)

        except websockets.ConnectionClosed as e:
            # Capture WHY the socket died — a 1011 (keepalive/internal) or an
            # abnormal 1006 points at a starved event loop / dropped pings,
            # whereas a clean 1000 is a normal server close. Without this the
            # root cause of mid-session stalls is invisible.
            rcvd = getattr(e, "rcvd", None)
            sent = getattr(e, "sent", None)
            code = getattr(rcvd, "code", None) if rcvd else getattr(e, "code", None)
            reason = getattr(rcvd, "reason", None) if rcvd else getattr(e, "reason", None)
            since_recv = round(time.monotonic() - self._last_server_event_at, 1)
            logger.info(
                "Realtime WebSocket closed by server (code=%s reason=%r sent=%s "
                "silent_for=%ss)",
                code, reason, sent, since_recv,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime recv loop failed")

    # ------------------------------------------------------------------
    # Session update — minimal override
    # ------------------------------------------------------------------

    async def _send_wake_greeting(self, ws) -> None:
        """Send the short spoken 'I'm listening' acknowledgment once per
        session. Interruptible (interrupt_response stays true), so a user
        already mid-sentence pre-empts it cleanly. For warm sessions this is
        fired on activate() so it comes back in ~1 s instead of after a cold
        mint + connect + prefill.

        When an active summary context is set (the user opened a meeting/note
        summary), inject that context as a system message first and speak a
        summary-specific opener instead of the generic greeting."""
        if self._wake_greeting_sent:
            return
        self._wake_greeting_sent = True
        ctx = self._active_summary_context
        greeting = self._active_summary_greeting or _REALTIME_WAKE_GREETING_INSTRUCTIONS
        try:
            if ctx:
                await self._inject_system_message(ws, ctx)
            if not _REALTIME_WAKE_GREETING_ENABLED and not ctx:
                return
            await ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "instructions": greeting,
                },
            }))
            logger.info(
                "Realtime: wake-word greeting sent (summary_context=%s)",
                bool(ctx),
            )
        except Exception:
            logger.warning(
                "Realtime: wake-word greeting send failed", exc_info=True
            )

    async def _inject_system_message(self, ws, text: str) -> None:
        """Insert a system message into the conversation without forcing a
        response. Used to hand the model live screen context (active summary)
        or to tear it down."""
        await ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text", "text": text}],
            },
        }))

    def apply_active_context(self, context_text: str, greeting: str | None = None) -> None:
        """Set the active summary context for this session.

        Safe to call from the Kivy main thread, before or after the session
        connects. If the wake greeting has not fired yet, the greeting path
        picks the context up automatically. If the session is already live and
        greeted, the context (and an optional fresh opener) is injected now.
        """
        ctx = (context_text or "").strip() or None
        self._active_summary_context = ctx
        self._active_summary_greeting = (greeting or "").strip() or None
        if not ctx:
            return
        loop, ws = self._loop, self._ws
        if loop is None or ws is None or loop.is_closed():
            return
        if not self._wake_greeting_sent:
            return  # greeting path will inject it

        async def _go():
            try:
                await self._inject_system_message(ws, ctx)
                if self._active_summary_greeting:
                    await ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"instructions": self._active_summary_greeting},
                    }))
            except Exception:
                logger.debug("apply_active_context inject failed", exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_go(), loop)
        except Exception:
            logger.debug("apply_active_context schedule failed", exc_info=True)

    def clear_active_context(self) -> None:
        """Tear down the active summary context (user closed the summary).

        Injects a 'SUMMARY CONTEXT CLEARED' system message so the model stops
        resolving 'this'/'it' to the closed summary. Safe to call from the
        Kivy main thread."""
        had_ctx = self._active_summary_context is not None
        self._active_summary_context = None
        self._active_summary_greeting = None
        if not had_ctx:
            return
        loop, ws = self._loop, self._ws
        if loop is None or ws is None or loop.is_closed():
            return

        async def _go():
            try:
                await self._inject_system_message(
                    ws,
                    "SUMMARY CONTEXT CLEARED: the user has closed the summary. "
                    "Stop assuming 'this' or 'it' refers to it; resume normal behaviour.",
                )
            except Exception:
                logger.debug("clear_active_context inject failed", exc_info=True)

        try:
            asyncio.run_coroutine_threadsafe(_go(), loop)
        except Exception:
            logger.debug("clear_active_context schedule failed", exc_info=True)

    async def _send_session_update(self, ws) -> None:
        """Override only what we need + register the client-side end_session tool.

        The server already configured the session with the full system
        prompt, tools, voice, audio format, and turn-detection (semantic
        VAD with create_response and interrupt_response both true). We
        do NOT resend instructions — sending a partial session with that
        field omitted would silently wipe it. We DO resend tools, but
        only after merging the server's tool list (cached from
        session.created) with the client-only end_session tool.

        We override:
          - input.transcription.model — enables a transcript stream of
            user speech (used for farewell detection and the transcript
            overlay).
          - input.turn_detection — mode/eagerness, plus create_response
            and interrupt_response both OFF when the evidence layer is
            live (client-authority turn-taking: the client requests or
            suppresses responses per turn from acoustic evidence).
          - tools — server tools + end_session.
        """
        merged_tools = list(self._server_tools) + [END_SESSION_TOOL, START_RECORDING_TOOL]
        transcription_cfg = {
            "model": _DEFAULT_INPUT_TRANSCRIPTION_MODEL,
            "language": "en",
        }
        if _INPUT_TRANSCRIPTION_PROMPT.strip():
            transcription_cfg["prompt"] = _INPUT_TRANSCRIPTION_PROMPT
        audio_input: dict = {
            "transcription": transcription_cfg,
        }
        # Tell the server which codec the mic leg uses. The ephemeral session is
        # minted as 24 kHz PCM16; when we transcode the uplink to G.711 mu-law
        # (REALTIME_UPLINK_CODEC=g711_ulaw) the server MUST be told or it will
        # misdecode the bytes as PCM16 (garbled speech / no transcript). The
        # downlink/output format is left untouched (full-quality assistant
        # voice). Format keys follow the GA realtime schema.
        if self._uplink_g711:
            audio_input["format"] = {"type": "audio/pcmu"}
        else:
            audio_input["format"] = {"type": "audio/pcm", "rate": _REALTIME_RATE}
        # Client-authority turn-taking: when the local speech-evidence layer is
        # live, the server VAD is demoted to a pure audio segmenter. It must
        # neither auto-create responses nor auto-interrupt playback, because it
        # cannot tell genuine speech from residual echo/noise — only the client
        # holds the acoustic evidence. The client explicitly:
        #   * sends response.create when a committed turn carries real speech,
        #   * deletes committed turns that carry none (phantom excision),
        #   * cancels + aborts playback on evidence-backed barge-in.
        # This ordering is what makes phantom suppression airtight on ANY
        # device: no response can exist before the evidence check passes.
        # With the evidence layer disabled (REALTIME_TURN_EVIDENCE=0) the
        # legacy server-driven behavior is preserved.
        client_authority = _TURN_EVIDENCE_ENABLED and self._speech_monitor is not None
        create_response = not client_authority
        interrupt_response = not client_authority
        # Decide turn detection. On desktop the OS AEC gives a clean mic, so we
        # default to energy-based server_vad (focused on the active talker,
        # ignores sub-threshold ambient, commits a fixed time after you stop).
        # The appliance keeps its semantic_vad behavior unless overridden.
        td_mode = _REALTIME_TURN_DETECTION
        if td_mode == "auto":
            # Desktop (Windows): semantic end-of-turn detection — the natural,
            # low-latency turn-taking the shipping EXE used. (The energy-based
            # server_vad experiment felt laggier on coupled laptop mics and,
            # paired with the client-authority layer, could strand the uplink.)
            # Appliance keeps semantic too. Force energy-based detection
            # explicitly with REALTIME_TURN_DETECTION=server_vad.
            td_mode = "semantic_vad" if IS_DESKTOP else "semantic"
        turn_detection = None
        if td_mode == "server_vad":
            turn_detection = {
                "type": "server_vad",
                "threshold": _REALTIME_VAD_THRESHOLD,
                "prefix_padding_ms": _REALTIME_VAD_PREFIX_MS,
                "silence_duration_ms": _REALTIME_VAD_SILENCE_MS,
                "create_response": create_response,
                "interrupt_response": interrupt_response,
            }
        elif td_mode == "semantic_vad":
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": (_REALTIME_VAD_EAGERNESS if _REALTIME_VAD_EAGERNESS != "auto" else "medium"),
                "create_response": create_response,
                "interrupt_response": interrupt_response,
            }
        elif _REALTIME_VAD_EAGERNESS and _REALTIME_VAD_EAGERNESS != "auto":
            # Backwards-compatible path: eagerness set but no explicit mode.
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": _REALTIME_VAD_EAGERNESS,
                "create_response": create_response,
                "interrupt_response": interrupt_response,
            }
        if turn_detection is not None:
            audio_input["turn_detection"] = turn_detection
        try:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {
                        "input": audio_input,
                    },
                    "tools": merged_tools,
                },
            }))
            self._log_voice_event(
                "session_update_sent",
                transcript_model=_DEFAULT_INPUT_TRANSCRIPTION_MODEL,
                transcript_prompt=bool(_INPUT_TRANSCRIPTION_PROMPT.strip()),
                turn_detection=(turn_detection or {}).get("type", "server_default"),
                vad_threshold=_REALTIME_VAD_THRESHOLD if td_mode == "server_vad" else None,
                vad_silence_ms=_REALTIME_VAD_SILENCE_MS if td_mode == "server_vad" else None,
                vad_eagerness=_REALTIME_VAD_EAGERNESS,
                create_response=create_response,
                interrupt_response=interrupt_response,
                client_authority=client_authority,
                half_duplex=self._half_duplex,
                uplink_codec=("g711_ulaw" if self._uplink_g711 else "pcm16"),
            )
        except Exception:
            logger.warning("Realtime session.update failed", exc_info=True)

    # ------------------------------------------------------------------
    # Tool round-trip on response.done
    # ------------------------------------------------------------------

    async def _run_response_done_handler(self, ws, msg: dict) -> None:
        """Background wrapper for _handle_response_done (spawned by _recv_loop).

        The lock keeps handlers strictly ordered if a second response.done
        arrives while a slow tool round-trip is still in flight; exceptions
        are logged here because nothing awaits this task directly.
        """
        try:
            async with self._response_done_lock:
                await self._handle_response_done(ws, msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Realtime: response.done handler failed")

    async def _handle_response_done(self, ws, msg: dict) -> None:
        response = msg.get("response") or {}
        if not isinstance(response, dict):
            return
        outputs = response.get("output") or []
        if not isinstance(outputs, list):
            return

        pending: list[dict] = []
        end_session_requested = False
        start_recording_requested = False
        start_recording_mode = "meeting"
        start_recording_context: dict = {}
        brief_started_now = False
        for item in outputs:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            call_id = (item.get("call_id") or "").strip()
            name = (item.get("name") or "").strip()
            args = item.get("arguments")
            if args is None:
                args = "{}"
            elif not isinstance(args, str):
                args = json.dumps(args)
            if not call_id or not name:
                continue

            # Client-only tool: model decided the conversation is over.
            # Don't HTTP-roundtrip it — just mark for close after the
            # current audio finishes playing.
            if name == "end_session":
                logger.info(
                    "Realtime: model called end_session (call_id=%s) — closing.",
                    call_id,
                )
                end_session_requested = True
                continue

            # Client-only tool: model was asked to start a meeting recording.
            # Don't HTTP-roundtrip it — close the session and trigger
            # start_recording() on the main thread.
            if name == "start_recording":
                try:
                    parsed_args = json.loads(args or "{}")
                except (TypeError, ValueError):
                    parsed_args = {}
                mode = str((parsed_args or {}).get("recording_mode") or "meeting").strip().lower()
                if mode not in {"meeting", "note"}:
                    mode = "meeting"
                start_recording_context = _extract_start_context(parsed_args)
                logger.info(
                    "Realtime: model called start_recording (call_id=%s mode=%s ctx_keys=%s) — starting recording.",
                    call_id,
                    mode,
                    list(start_recording_context.keys()),
                )
                start_recording_requested = True
                start_recording_mode = mode
                continue

            logger.info(
                "Realtime tool invoke: name=%s call_id=%s args=%s",
                name, call_id, args[:200],
            )
            self._tool_roundtrip_active = True
            try:
                out = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda _b=self._backend_base_url, _t=self._device_token,
                           _c=call_id, _n=name, _a=args: invoke_realtime_tool_sync(
                        _b, _t, call_id=_c, name=_n, arguments=_a,
                    ),
                )
            finally:
                self._tool_roundtrip_active = False
            logger.info("Realtime tool result: name=%s out_len=%d", name, len(out or ""))

            model_out = out
            if name != "show_email_draft":
                try:
                    _generic_data = json.loads(out or "{}")
                except (TypeError, ValueError):
                    _generic_data = {}
                if isinstance(_generic_data, dict) and "device_email_draft" in _generic_data:
                    # Some committing tools (notably approve_pending_action) now
                    # emit the terminal email draft state themselves once the write
                    # succeeds. This makes the send/save animation deterministic
                    # instead of depending on the model to call show_email_draft
                    # again after the write.
                    self._emit_email_draft(out)
                    model_out = self._redact_email_draft_for_model(out)
            if name == "navigate_device_ui":
                nav_screen = ""
                nav_target_tab = None
                try:
                    _nav = json.loads(out)
                    if isinstance(_nav, dict) and _nav.get("ok"):
                        nav_screen = str(_nav.get("device_navigate") or "").strip()
                        nav_target_tab = _nav.get("target_tab") or None
                except Exception:
                    nav_screen = ""
                    nav_target_tab = None
                if nav_screen == "morning_brief":
                    # Take over the carousel: start the device-driven walkthrough
                    # on the first morning-brief navigate. Preserve the requested
                    # section so "show tasks" / "next" / "go back" don't reset to
                    # schedule after a user interruption; ignore any extra batched
                    # calls so the cards don't race ahead of the speech.
                    if not self._brief_active:
                        self._brief_active = True
                        self._brief_idx = _brief_target_index(nav_target_tab, self._brief_idx)
                        self._emit_brief_section(_BRIEF_SECTIONS[self._brief_idx])
                        brief_started_now = True
                    # else: already driving — swallow the model's extra switch.
                else:
                    # Any non-brief navigation means we've left the walkthrough.
                    if self._brief_active:
                        self._cancel_briefing()
                    self._emit_device_navigation(out)
            elif name in ("fetch_and_show_email", "show_email_view"):
                self._emit_email_view(out)
            elif name == "show_email_draft":
                self._emit_email_draft(out)
                # The draft popup (incl. the full reply-all recipient list the
                # server resolved) is a DEVICE-ONLY surface. Strip those concrete
                # recipients from what we feed back to the model so it can never
                # use them to send a new (mis-threaded) email. The model only
                # needs to know the popup updated; the real send always goes via
                # the reply / reply-all tools, which compute recipients server-side.
                model_out = self._redact_email_draft_for_model(out)
            elif name == "show_task_creation":
                self._emit_task_creation(out)
                model_out = self._redact_task_creation_for_model(out)
            elif name in ("confirm_task_creation", "discard_task_creation"):
                self._emit_task_dismiss(out)
                model_out = self._redact_task_dismiss_for_model(out)
            elif name == "show_calendar_event":
                self._emit_calendar_event(out)
                model_out = self._redact_calendar_event_for_model(out)
            elif name in ("confirm_calendar_event", "discard_calendar_event"):
                self._emit_calendar_event_dismiss(out)
                model_out = self._redact_calendar_event_dismiss_for_model(out)
            elif name == "show_recipient_picker":
                self._emit_recipient_picker(out)
            elif name == "show_meeting_summary":
                self._emit_device_navigation(out)
                # The summary body is a DEVICE-ONLY surface (the screen shows it).
                # Strip it from what the model sees so it confirms briefly instead
                # of reading the whole summary aloud.
                try:
                    _ms = json.loads(out)
                    if isinstance(_ms, dict) and "summary_data" in _ms:
                        model_out = json.dumps(
                            {k: v for k, v in _ms.items() if k != "summary_data"}
                        )
                except (TypeError, ValueError):
                    pass

            pending.append({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": model_out,
                },
            })

        if pending:
            try:
                for ev in pending:
                    await ws.send(json.dumps(ev))
                # The server's turn-detection auto-create only fires on
                # user audio commit, not on a tool-output commit, so we
                # must always send response.create after function call
                # outputs to keep the conversation flowing.
                if not end_session_requested and not start_recording_requested:
                    if brief_started_now:
                        # Drive the first (schedule) section ourselves with a
                        # tool-less narration instead of the default open turn.
                        await self._send_brief_narration(ws, self._brief_idx)
                    else:
                        await ws.send(json.dumps({"type": "response.create"}))
            except Exception:
                logger.exception("Realtime: tool round-trip failed")
        elif self._brief_active and self._brief_start_pending:
            # The generic auto-response we canceled has finished. Start the
            # visual, section-scoped narration now that the API is ready for a
            # fresh response.create.
            self._brief_start_pending = False
            start_task = self._brief_start_task
            self._brief_start_task = None
            if start_task is not None and not start_task.done():
                try:
                    start_task.cancel()
                except Exception:
                    pass
            await self._send_brief_narration(ws, self._brief_idx)
        elif self._brief_active and not brief_started_now and self._brief_narration_audio_seen:
            # A briefing narration response just finished (no tool calls) —
            # advance to the next card once its audio drains.
            self._brief_narration_audio_seen = False
            self._schedule_brief_advance(ws)

        if end_session_requested:
            # The model has already spoken its goodbye in this response;
            # close after the audio queue drains.
            self._user_ended = True
            self._stop.set()
            try:
                await ws.close()
            except Exception:
                pass

        if start_recording_requested:
            # The model has already spoken its confirmation; close the session
            # and trigger start_recording() on the Kivy main thread.
            self._user_ended = True
            self._stop.set()
            try:
                await ws.close()
            except Exception:
                pass
            cb = self._on_start_recording_cb
            if cb:
                Clock.schedule_once(
                    lambda _dt, m=start_recording_mode, c=start_recording_context: self._safe_call(cb, m, c),
                    0,
                )

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_audio_delta(msg: dict) -> str:
        d = msg.get("delta") or msg.get("audio")
        if isinstance(d, dict):
            d = d.get("audio") or d.get("delta")
        return str(d or "")

    @staticmethod
    def _extract_transcript(msg: dict) -> str:
        for key in ("transcript", "text"):
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def _log_session_summary(self, msg: dict, *, label: str) -> None:
        sess = msg.get("session") or {}
        if not isinstance(sess, dict):
            return
        tools = sess.get("tools") or []
        if label == "session.created" and isinstance(tools, list):
            # Cache the full tool definitions so we can re-send them in
            # session.update with end_session appended.
            self._server_tools = [t for t in tools if isinstance(t, dict)]
        tool_names = [t.get("name") for t in tools if isinstance(t, dict)]
        voice = (sess.get("audio") or {}).get("output", {}).get("voice")
        instr = sess.get("instructions") or ""
        logger.info(
            "Realtime %s: tools=%d %s voice=%s instructions_len=%d",
            label,
            len(tools),
            tool_names,
            voice,
            len(instr) if isinstance(instr, str) else 0,
        )
