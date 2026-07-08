"""Cross-platform audio playback for the MeetingBox device UI.

On the Linux appliance, model/TTS audio is played by piping PCM/WAV to
``aplay`` (ALSA). Windows has no ``aplay``; this module provides a small,
dependency-light playback layer built on :mod:`sounddevice` (PortAudio),
which is already a project dependency and works on Windows, macOS and Linux.

Two surfaces are exposed:

* :func:`play_pcm16` — blocking playback of raw little-endian PCM16 mono bytes
  at a given sample rate. Used by the local TTS fallback.
* :class:`PcmStreamPlayer` — a low-latency streaming sink for the OpenAI
  Realtime path: ``write()`` queues PCM frames, ``stop()`` performs an
  immediate barge-in flush (drop queued audio + abort the stream).

Both honour an explicit output device via the ``device`` argument or the
``MEETINGBOX_OUTPUT_DEVICE_INDEX`` env var; otherwise the system default
output device is used (the user's selected Windows playback device).
"""

from __future__ import annotations

import logging
import os
import threading
import wave

logger = logging.getLogger(__name__)

try:  # sounddevice is a hard dependency, but never let import break the app.
    import numpy as np
except Exception:  # pragma: no cover - numpy is required elsewhere
    np = None  # type: ignore

try:
    import sounddevice as _sd
except Exception:  # pragma: no cover
    _sd = None  # type: ignore


def is_available() -> bool:
    """True when a PortAudio backend and numpy are importable."""
    return _sd is not None and np is not None


def _resolve_output_device(device):
    """Return an explicit output device id, or None for the system default."""
    if device is not None:
        return device
    env = (os.getenv("MEETINGBOX_OUTPUT_DEVICE_INDEX") or "").strip()
    if env.isdigit():
        return int(env)
    return None


def play_pcm16(
    pcm_bytes: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    device=None,
    blocking: bool = True,
) -> bool:
    """Play raw PCM16 audio. Returns True on success.

    This is a best-effort helper: any failure returns False so callers can
    fall through to another engine instead of raising.
    """
    if not is_available() or not pcm_bytes:
        return False
    try:
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        if channels > 1:
            arr = arr.reshape(-1, channels)
        _sd.play(arr, samplerate=sample_rate, device=_resolve_output_device(device))
        if blocking:
            _sd.wait()
        return True
    except Exception as exc:  # noqa: BLE001 - playback is best-effort
        logger.debug("play_pcm16 failed: %s", exc)
        return False


def play_wav_file(path: str, device=None, blocking: bool = True) -> bool:
    """Play a WAV file (PCM16 expected). Returns True on success."""
    if not is_available():
        return False
    try:
        with wave.open(path, "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())
        return play_pcm16(frames, sample_rate=sr, channels=ch, device=device, blocking=blocking)
    except Exception as exc:  # noqa: BLE001
        logger.debug("play_wav_file failed (%s): %s", path, exc)
        return False


def stop_all() -> None:
    """Immediately stop any audio started via :func:`play_pcm16`/:func:`play_wav_file`."""
    if _sd is None:
        return
    try:
        _sd.stop()
    except Exception:  # noqa: BLE001
        pass


class PcmStreamPlayer:
    """Low-latency streaming PCM16 sink with barge-in support.

    Mirrors the semantics the Linux path got from a long-lived ``aplay`` pipe:
    audio frames are written continuously and playback can be killed instantly
    when the user starts speaking (barge-in). Internally a background thread
    feeds a :class:`sounddevice.RawOutputStream` from a queue.

    Usage::

        player = PcmStreamPlayer(sample_rate=24000)
        player.start()
        player.write(pcm_frame_bytes)   # repeatedly
        player.stop()                   # barge-in: drop queue + abort stream
        player.close()                  # on teardown
    """

    def __init__(self, sample_rate: int = 24000, channels: int = 1, device=None,
                 fade_ms: float | None = None, on_pcm=None):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self._device = _resolve_output_device(device)
        # Render tap: called from the device callback with the exact PCM16
        # block handed to the output device (post fade, zero-filled when the
        # queue is dry). This is the ground-truth far-end reference for AEC —
        # paced by the render device clock, including real silence, and never
        # including audio that a barge-in abort dropped before it played.
        self._on_pcm = on_pcm
        # Barge-in fade-out length. Cutting a PortAudio stream mid-waveform
        # (abort()) leaves the DAC at a non-zero sample; that step to zero is an
        # audible click/"scratch". Instead we ramp the last few ms to zero in
        # the callback before stopping, so the waveform lands on silence. A few
        # ms is inaudible as latency but removes the pop.
        env_fade = (os.getenv("MEETINGBOX_BARGE_FADE_MS") or "").strip()
        if fade_ms is None:
            try:
                fade_ms = float(env_fade) if env_fade else 12.0
            except ValueError:
                fade_ms = 12.0
        self._fade_ms = max(1.0, float(fade_ms))
        self._fade_total = max(1, int(self.sample_rate * self._fade_ms / 1000.0))
        self._stream = None
        self._buf = bytearray()             # int16 PCM awaiting playback
        self._buf_lock = threading.Lock()
        self._lock = threading.Lock()
        self._active = False
        self._fade_remaining: int | None = None  # None = not fading
        self._finished = threading.Event()

    def is_supported(self) -> bool:
        return _sd is not None and np is not None

    def start(self) -> bool:
        if not self.is_supported():
            return False
        with self._lock:
            if self._active:
                return True
            with self._buf_lock:
                self._buf.clear()
                self._fade_remaining = None
            self._finished.clear()
            try:
                self._stream = _sd.OutputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="int16",
                    device=self._device,
                    blocksize=0,
                    callback=self._callback,
                )
                self._stream.start()
            except Exception as exc:  # noqa: BLE001
                logger.warning("PcmStreamPlayer could not open output stream: %s", exc)
                self._stream = None
                return False
            self._active = True
            return True

    def _callback(self, outdata, frames, time_info, status):  # noqa: ANN001
        del time_info, status
        ch = self.channels
        want = frames * ch
        with self._buf_lock:
            avail = len(self._buf) // 2
            take = min(want, avail)
            if take:
                chunk = np.frombuffer(bytes(self._buf[: take * 2]), dtype=np.int16).copy()
                del self._buf[: take * 2]
            else:
                chunk = None
            fade_rem = self._fade_remaining

        block = np.zeros(want, dtype=np.int16)
        if chunk is not None and chunk.size:
            block[: chunk.size] = chunk
        block = block.reshape(frames, ch)

        if fade_rem is not None:
            # Linear ramp from the current gain down to zero across the tail.
            idx = np.arange(frames, dtype=np.float32)
            gains = (float(fade_rem) - idx) / float(self._fade_total)
            np.clip(gains, 0.0, 1.0, out=gains)
            block = (block.astype(np.float32) * gains[:, None]).astype(np.int16)
            outdata[:] = block
            self._emit_tap(block)
            new_rem = fade_rem - frames
            if new_rem <= 0:
                with self._buf_lock:
                    self._fade_remaining = 0
                self._finished.set()
                raise _sd.CallbackStop
            with self._buf_lock:
                if self._fade_remaining is not None:
                    self._fade_remaining = new_rem
            return

        outdata[:] = block
        self._emit_tap(block)

    def _emit_tap(self, block) -> None:
        tap = self._on_pcm
        if tap is None:
            return
        try:
            tap(block.tobytes())
        except Exception:  # noqa: BLE001 - never disturb the device callback
            pass

    def write(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes or not self._active:
            return
        with self._buf_lock:
            if self._fade_remaining is not None:
                return  # barge-in in progress; ignore trailing audio
            self._buf.extend(pcm_bytes)

    def stop(self) -> None:
        """Barge-in: ramp the tail to zero (no click) then stop the stream."""
        with self._lock:
            if not self._active:
                return
            st = self._stream
            if st is None:
                self._active = False
                return
            with self._buf_lock:
                if self._fade_remaining is None:
                    self._fade_remaining = self._fade_total
        # Let the callback play out the fade ramp before we tear down.
        self._finished.wait(timeout=max(0.05, (self._fade_ms / 1000.0) * 4))
        self._teardown()

    def _teardown(self) -> None:
        with self._lock:
            st = self._stream
            self._stream = None
            self._active = False
        if st is not None:
            try:
                st.stop()
            except Exception:  # noqa: BLE001
                pass
            try:
                st.close()
            except Exception:  # noqa: BLE001
                pass
        with self._buf_lock:
            self._buf.clear()
            self._fade_remaining = None

    def close(self) -> None:
        self.stop()
