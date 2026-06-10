"""
OpenAI Realtime voice bridge for the MeetingBox device UI.

Minimal, low-latency rebuild.

Design:
- Connect to wss://api.openai.com/v1/realtime with the ephemeral
  client_secret minted by the MeetingBox server.
- Trust the server's semantic_vad for turn detection — server runs with
  `create_response: true` and `interrupt_response: true`, so end-of-turn
  and barge-in are handled at the source instead of being gated on a
  second model hop (transcription + manual response.create on the
  client). This removes ~0.5–1.5 s of dead air per turn.
- Send ONE small session.update: nudge eagerness to "high" and enable
  user-audio transcription (used only for farewell detection). The
  server's full instructions, tools, voice, and audio format are left
  exactly as configured.
- Stream PCM16 mic audio at 24 kHz to input_audio_buffer.append.
- Play model audio deltas through aplay; pipe writes run on a dedicated
  single-thread executor so they never block the WebSocket heartbeat.
- On user speech_started: hard-kill aplay so the user hears themselves,
  not the assistant. The server will cancel the in-flight response on
  its own via interrupt_response.
- On user transcript completion: if the text is a farewell, close the
  session. Otherwise the server creates the next response automatically.
- On function-call output in response.done: invoke the backend tool via
  HTTP, post the result back, send response.create to continue.

No acoustic echo cancellation, no transcript-based echo guard, no
deferred barge-in. With an external mic+speaker, the previous
self-interruption loop does not occur.
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

import numpy as np
import websockets
from kivy.clock import Clock

from api_client import invoke_realtime_tool_sync

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
except ImportError:
    sd = None

REALTIME_VOICE_IMPLEMENTED = True


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_REALTIME_WS_HOST = "api.openai.com"
_REALTIME_RATE = 24000

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

# Close the session if the user is silent (and we're not speaking) for
# this many seconds. Matches the previous behavior.
_SESSION_IDLE_CLOSE_S = 40.0

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
# gpt-4o-mini-transcribe streams partial transcripts as
# `conversation.item.input_audio_transcription.delta` events so the
# user's words appear on screen live, word-by-word, while they speak.
# whisper-1 (the old default) only returns ONE final transcript at
# end-of-utterance — which is why text used to appear all at once after
# the user finished. Override via REALTIME_TRANSCRIBE_MODEL=whisper-1 to
# revert.
_DEFAULT_INPUT_TRANSCRIPTION_MODEL = (
    os.environ.get("REALTIME_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip()
    or "gpt-4o-mini-transcribe"
)
# Deliberately neutral — see server/web/routes/voice.py for the rationale.
_INPUT_TRANSCRIPTION_PROMPT = "Conversational English."


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
        "Call this tool when the user asks to start recording a meeting. "
        "Always say a brief confirmation (e.g. 'Starting the recording now') "
        "BEFORE calling this tool. The voice session will close and recording "
        "will begin immediately."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


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
        on_email_draft=None,
        on_recipient_picker=None,
        on_start_recording=None,
        should_suppress_farewell=None,
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
        self._on_email_draft_cb = on_email_draft
        self._on_recipient_picker_cb = on_recipient_picker
        self._on_start_recording_cb = on_start_recording
        # Optional predicate: when it returns True the aggressive keyword-based
        # client-side farewell close is skipped (e.g. while an email draft is
        # on screen) and we defer to the model's contextual end_session tool.
        self._should_suppress_farewell_cb = should_suppress_farewell
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

        # Worker thread + asyncio loop
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws: Any = None
        self._connected_fired = False
        self._user_ended = False

        # Mic input
        self._audio_q: queue.Queue[bytes | None] = queue.Queue(maxsize=400)
        self._mic_stream = None
        self._mic_native_sr = _REALTIME_RATE

        # Playback (aplay) — pipe writes go through a dedicated
        # single-thread executor. Writing on the asyncio loop would
        # block the heartbeat for seconds if aplay's 64 KB pipe fills
        # up, tripping ping_timeout and killing the session mid-reply.
        self._aplay_proc: subprocess.Popen | None = None
        self._aplay_pid: int | None = None
        self._aplay_writer = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rtv-aplay"
        )
        self._suppress_audio_until = 0.0
        # Fallback mic-mute window — only used if AEC is unavailable. With
        # speex AEC active we leave the mic open so the user can interrupt.
        self._mute_mic_uplink_until = 0.0

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
        # arrive (gpt-4o-mini-transcribe emits incremental deltas). We
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

        # State exposed to the UI / idle watchdog
        self._state = "idle"            # idle | listening | thinking | speaking
        self._response_in_progress = False
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

    def _emit_user_transcript(self, text: str, is_final: bool = True) -> None:
        cb = self._on_user_transcript_cb
        if cb and text:
            Clock.schedule_once(
                lambda _dt: self._safe_call(cb, text, is_final), 0
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
        Clock.schedule_once(
            lambda _dt: self._safe_call(cb, screen.strip(), target_date, target_tab), 0
        )

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
        Clock.schedule_once(
            lambda _dt: self._safe_call(cb, query, candidates), 0
        )

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

    def _open_mic(self, preferred_device_id, candidate_device_ids) -> bool:
        if sd is None:
            self._emit_error("sounddevice not installed; microphone unavailable.")
            return False

        tried: list = []
        for dev in [preferred_device_id, *candidate_device_ids]:
            if dev in tried:
                continue
            tried.append(dev)
            for sr in (48000, 44100, 32000, 16000, _REALTIME_RATE):
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
                    logger.info(
                        "Realtime mic open: device=%s samplerate=%s", dev, sr
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
        # Push the same PCM into the AEC far-end ring so the canceller knows
        # what is about to come out of the speaker. Cap to ~5 s to keep memory
        # bounded if the mic side stalls.
        if self._aec is not None:
            with self._aec_buf_lock:
                self._aec_far_buf.extend(raw)
                max_bytes = _REALTIME_RATE * 2 * 5
                excess = len(self._aec_far_buf) - max_bytes
                if excess > 0:
                    del self._aec_far_buf[:excess]
        # Extend the mic-mute window for the duration of this audio chunk plus
        # a 600 ms tail for room echo to decay.  Speex AEC cannot reliably cancel
        # echo without an exact acoustic delay measurement, so we always mute the
        # uplink while the speaker is active regardless of whether AEC is running.
        chunk_s = len(raw) / (_REALTIME_RATE * 2)   # PCM16 mono bytes → seconds
        self._mute_mic_uplink_until = max(
            self._mute_mic_uplink_until,
            time.monotonic() + chunk_s + 0.6,
        )
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
        """Hard-kill the playback subprocess immediately."""
        proc = self._aplay_proc
        self._aplay_proc = None
        self._aplay_pid = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.poll() is None:
                proc.kill()
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

                # Warm session just woken: fire the spoken "I'm listening"
                # greeting NOW, before the ALSA mic handoff, so its ~1 s
                # generation on the already-warm connection overlaps with mic
                # setup. (Cold sessions greet from the session.updated handler.)
                if self._prewarm:
                    await self._send_wake_greeting(ws)

                # Active path: let the UI close any local mic (e.g. Vosk wake
                # word) before we open ALSA for the Realtime session.
                if self._on_before_open_mic_cb is not None:
                    self._safe_call(self._on_before_open_mic_cb)
                    await asyncio.sleep(0.01)

                preferred, candidates = self._resolve_input_device()
                if not self._open_mic(preferred, candidates):
                    self._emit_error("Realtime: microphone unavailable.")
                    await ws.close()
                    self._emit_session_end()
                    return

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
                idle_task = asyncio.create_task(self._idle_watchdog())

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
                    await asyncio.gather(
                        pump_task, idle_task, return_exceptions=True
                    )

        except Exception as e:
            logger.exception("Realtime WebSocket failed")
            self._emit_error(str(e))
        finally:
            self._emit_state("idle")
            self._ws = None
            self._abort_aplay()
            self._close_mic()
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
            self._emit_session_end()

    # ------------------------------------------------------------------
    # Echo cancellation
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Mic pump (asyncio side)
    # ------------------------------------------------------------------

    async def _pump_mic(self) -> None:
        assert self._ws is not None
        ws = self._ws
        loop = asyncio.get_running_loop()
        native_sr = self._mic_native_sr

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
            try:
                resampled = resample_pcm16_mono(piece, native_sr, _REALTIME_RATE)
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
                if time.monotonic() < self._mute_mic_uplink_until:
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
                if self._aec is not None:
                    resampled = self._aec_process(resampled)
                    if not resampled:
                        continue
                # Feed the same echo-cancelled PCM to the live-caption recognizer
                # (non-blocking; dropped if the side thread falls behind).
                if self._caption_q is not None:
                    try:
                        self._caption_q.put_nowait(resampled)
                    except queue.Full:
                        pass
                payload = base64.b64encode(resampled).decode("ascii")
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": payload,
                }))
                self._touch()
            except websockets.ConnectionClosed:
                break
            except Exception:
                logger.debug("Realtime mic upload failed", exc_info=True)
                # Transient upload errors must not kill the session.
                await asyncio.sleep(0.05)

    # ------------------------------------------------------------------
    # Idle watchdog
    # ------------------------------------------------------------------

    async def _idle_watchdog(self) -> None:
        ws = self._ws
        if ws is None:
            return
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            if self._state == "speaking" or self._response_in_progress:
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
                    # Fresh utterance — clear the streaming transcript buffer
                    # so partial deltas don't append to the previous turn.
                    self._user_transcript_buf = ""
                    self._active_user_transcript_item_id = ""
                    # Reset the live-caption recognizer + reset the UI bubble
                    # tracker so captions render into a fresh bubble.
                    self._caption_active = True
                    self._caption_reset.set()
                    self._emit_user_speech_started()
                    # User started talking. Cut playback now so they hear
                    # themselves, not the assistant. The server cancels
                    # the in-flight response on its own (interrupt_response).
                    self._abort_aplay()
                    self._suppress_audio_until = (
                        time.monotonic() + _BARGE_IN_SUPPRESS_AUDIO_S
                    )
                    # Trim — do NOT fully clear — the AEC far-end reference.
                    # aplay still has ~70 ms buffered and the room contributes
                    # ~50–150 ms of reflections, so the assistant's tail audio
                    # keeps reaching the mic for a short while after we kill
                    # playback. Fully clearing the reference leaves AEC with
                    # nothing to subtract, and the user's barge-in mic frames
                    # arrive contaminated with the assistant's own voice —
                    # which then mistranscribes and biases the realtime model.
                    # Retain ~300 ms of far-end so AEC keeps suppressing the
                    # tail; older samples are discarded.
                    if self._aec is not None:
                        retain_bytes = int(_REALTIME_RATE * 0.3) * 2  # 300 ms PCM16
                        with self._aec_buf_lock:
                            if len(self._aec_far_buf) > retain_bytes:
                                del self._aec_far_buf[: len(self._aec_far_buf) - retain_bytes]
                    self._emit_state("listening")

                elif t == "input_audio_buffer.speech_stopped":
                    self._touch()
                    self._emit_state("thinking")
                    # Stop live captions — OpenAI's accurate transcript now
                    # owns the bubble for this finished utterance.
                    self._caption_active = False
                    # Tell the UI to drop in a placeholder user bubble
                    # right away so the gap before transcription/AI is
                    # filled with immediate visual feedback.
                    self._emit_user_speech_stopped()

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
                        # hallucinations so the phantom never paints a bubble.
                        # Partial — not final, so the UI skips grammar
                        # correction until the .completed event.
                        if not _is_prompt_echo(self._user_transcript_buf):
                            self._emit_user_transcript(
                                self._user_transcript_buf, is_final=False
                            )

                # ---- User transcript (final) ---------------------------
                elif t in (
                    "conversation.item.input_audio_transcription.completed",
                    "input_audio_buffer.transcription.completed",
                ):
                    self._touch()
                    spoken = self._extract_transcript(msg)
                    # Utterance finished — reset the streaming buffer so the
                    # next utterance starts clean.
                    self._user_transcript_buf = ""
                    self._active_user_transcript_item_id = ""
                    if spoken and _is_prompt_echo(spoken):
                        logger.debug("Realtime: dropped prompt-echo phantom %r", spoken)
                        spoken = ""
                    if spoken:
                        logger.info("User said: %r", spoken)
                        self._emit_user_transcript(spoken, is_final=True)
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
                    await self._handle_response_done(ws, msg)
                    self._response_in_progress = False
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

        except websockets.ConnectionClosed:
            logger.info("Realtime WebSocket closed by server")
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
        mint + connect + prefill."""
        if not _REALTIME_WAKE_GREETING_ENABLED or self._wake_greeting_sent:
            return
        self._wake_greeting_sent = True
        try:
            await ws.send(json.dumps({
                "type": "response.create",
                "response": {
                    "instructions": _REALTIME_WAKE_GREETING_INSTRUCTIONS,
                },
            }))
            logger.info("Realtime: wake-word greeting sent")
        except Exception:
            logger.warning(
                "Realtime: wake-word greeting send failed", exc_info=True
            )

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
          - input.turn_detection.eagerness — how quickly the assistant
            replies after the user stops. The server defaults to "low"
            (most conservative) for hardware without echo cancellation;
            AEC (speex) is now enabled, so we bump to "medium" (env
            REALTIME_VAD_EAGERNESS) for a snappier turn-around while
            keeping create_response/interrupt_response TRUE.
          - tools — server tools + end_session.
        """
        merged_tools = list(self._server_tools) + [END_SESSION_TOOL, START_RECORDING_TOOL]
        audio_input: dict = {
            "transcription": {
                "model": _DEFAULT_INPUT_TRANSCRIPTION_MODEL,
                "language": "en",
                "prompt": _INPUT_TRANSCRIPTION_PROMPT,
            },
        }
        # "auto" means: leave the server's turn_detection untouched.
        if _REALTIME_VAD_EAGERNESS and _REALTIME_VAD_EAGERNESS != "auto":
            audio_input["turn_detection"] = {
                "type": "semantic_vad",
                "eagerness": _REALTIME_VAD_EAGERNESS,
                "create_response": True,
                "interrupt_response": True,
            }
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
        except Exception:
            logger.warning("Realtime session.update failed", exc_info=True)

    # ------------------------------------------------------------------
    # Tool round-trip on response.done
    # ------------------------------------------------------------------

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
                logger.info(
                    "Realtime: model called start_recording (call_id=%s) — starting recording.",
                    call_id,
                )
                start_recording_requested = True
                continue

            logger.info(
                "Realtime tool invoke: name=%s call_id=%s args=%s",
                name, call_id, args[:200],
            )
            out = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda _b=self._backend_base_url, _t=self._device_token,
                       _c=call_id, _n=name, _a=args: invoke_realtime_tool_sync(
                    _b, _t, call_id=_c, name=_n, arguments=_a,
                ),
            )
            logger.info("Realtime tool result: name=%s out_len=%d", name, len(out or ""))

            model_out = out
            if name == "navigate_device_ui":
                self._emit_device_navigation(out)
            elif name == "show_email_draft":
                self._emit_email_draft(out)
                # The draft popup (incl. the full reply-all recipient list the
                # server resolved) is a DEVICE-ONLY surface. Strip those concrete
                # recipients from what we feed back to the model so it can never
                # use them to send a new (mis-threaded) email. The model only
                # needs to know the popup updated; the real send always goes via
                # the reply / reply-all tools, which compute recipients server-side.
                model_out = self._redact_email_draft_for_model(out)
            elif name == "show_recipient_picker":
                self._emit_recipient_picker(out)

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
                    await ws.send(json.dumps({"type": "response.create"}))
            except Exception:
                logger.exception("Realtime: tool round-trip failed")

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
                Clock.schedule_once(lambda _dt: self._safe_call(cb), 0)

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
