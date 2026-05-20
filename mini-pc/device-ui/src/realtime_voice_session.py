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

# Mic chunk duration. Smaller chunks let the server VAD see speech edges
# sooner, which directly reduces the latency between user-stops and
# response-starts. 5 ms = 240 bytes per chunk at 24 kHz mono PCM16.
_APPEND_CHUNK_MS = 5

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

# Fast, low-cost STT for the farewell-detection-only transcript stream.
_DEFAULT_INPUT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


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
    ):
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
        self._output_voice = (
            (output_voice or "").strip().lower()
            or _REALTIME_OUTPUT_VOICE_FALLBACK
        )

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

        # Tools we received from the server in session.created. Cached so
        # we can re-send them in session.update with end_session appended.
        self._server_tools: list[dict] = []

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
        Clock.schedule_once(lambda _dt: self._safe_call(cb, screen.strip()), 0)

    @staticmethod
    def _safe_call(cb, *args) -> None:
        if not cb:
            return
        try:
            cb(*args)
        except Exception:
            logger.exception("Realtime callback failed")

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
        return preferred, capture_device_fallback_candidates(sd, preferred)

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
        try:
            self._aplay_proc = subprocess.Popen(
                [
                    "aplay",
                    "-q",
                    "-t", "raw",
                    "-f", "S16_LE",
                    "-r", str(_REALTIME_RATE),
                    "-c", "1",
                    "--buffer-time", _APLAY_BUFFER_TIME_US,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._aplay_pid = self._aplay_proc.pid
            logger.info("Realtime aplay started pid=%s", self._aplay_pid)
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

                # Let the UI close any local mic (e.g. Vosk wake word)
                # before we open ALSA for the Realtime session.
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

                recv_task = asyncio.create_task(self._recv_loop())
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
                    if not self._connected_fired:
                        self._connected_fired = True
                        await self._send_session_update(ws)
                        self._emit_connected()

                elif t == "session.updated":
                    self._log_session_summary(msg, label="session.updated")

                # ---- User speech --------------------------------------
                elif t == "input_audio_buffer.speech_started":
                    self._touch()
                    # User started talking. Cut playback now so they hear
                    # themselves, not the assistant. The server cancels
                    # the in-flight response on its own (interrupt_response).
                    self._abort_aplay()
                    self._suppress_audio_until = (
                        time.monotonic() + _BARGE_IN_SUPPRESS_AUDIO_S
                    )
                    # Drop the queued AEC far-end reference: the audio it
                    # represents is no longer going to the speaker.
                    if self._aec is not None:
                        with self._aec_buf_lock:
                            self._aec_far_buf.clear()
                    self._emit_state("listening")

                elif t == "input_audio_buffer.speech_stopped":
                    self._touch()
                    self._emit_state("thinking")

                # ---- User transcript (logged for debugging) -----------
                elif t in (
                    "conversation.item.input_audio_transcription.completed",
                    "input_audio_buffer.transcription.completed",
                ):
                    self._touch()
                    spoken = self._extract_transcript(msg)
                    if spoken:
                        logger.info("User said: %r", spoken)
                    # End-of-session is now decided by the model via the
                    # end_session tool (handled in _handle_response_done).
                    # Server's create_response: true handles every other
                    # user turn automatically — nothing else for us here.

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
          - input.turn_detection.eagerness = "high" (server default is
            "low" for legacy hardware with no echo cancellation; with
            external mic + AEC we want snappy end-of-turn detection).
          - input.transcription.model — enables a transcript stream of
            user speech (also used as a fallback farewell heuristic).
          - tools — server tools + end_session.

        create_response and interrupt_response stay TRUE.
        """
        merged_tools = list(self._server_tools) + [END_SESSION_TOOL]
        try:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "audio": {
                        "input": {
                            "transcription": {
                                "model": _DEFAULT_INPUT_TRANSCRIPTION_MODEL,
                            },
                            "turn_detection": {
                                "type": "semantic_vad",
                                "eagerness": "high",
                                "create_response": True,
                                "interrupt_response": True,
                            },
                        },
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

            if name == "navigate_device_ui":
                self._emit_device_navigation(out)

            pending.append({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": out,
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
                if not end_session_requested:
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
