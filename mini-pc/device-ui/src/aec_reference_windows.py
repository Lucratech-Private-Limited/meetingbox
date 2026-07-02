"""Windows far-end reference via WASAPI loopback capture.

Captures the audio being played out of the current default render endpoint
(speakers/headset) as an input stream. Because loopback taps the endpoint's
*shared-mode mix*, this is exactly what the speaker emits — after the volume
slider, after the OS mixer, at the real device. That makes it the correct echo
reference at **any volume** and on **any playback device** (built-in speakers,
USB DAC, HDMI, Bluetooth), with no per-device tuning.

Windows-only. Import is safe elsewhere but :func:`is_available` is False and
construction raises, so the selector in ``aec_reference.py`` falls back
gracefully.

We use PyAudioWPatch (a PyAudio fork with first-class WASAPI loopback). The
loopback endpoint is exposed as an *input* device whose name mirrors the
default speakers; we resample its native format (usually 48 kHz stereo) down to
mono 24 kHz to match the realtime pipeline.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

import numpy as np

from aec_reference import REFERENCE_RATE, _RefRing, to_mono

logger = logging.getLogger(__name__)

_IS_WIN = sys.platform.startswith("win")


def is_available() -> bool:
    if not _IS_WIN:
        return False
    try:
        import pyaudiowpatch  # noqa: F401

        return True
    except Exception:
        return False


class WasapiLoopbackReference:
    """Background WASAPI-loopback capturer exposing the FarEndReference API."""

    output_rate = REFERENCE_RATE
    active_capture = True

    def __init__(self, rate: int = REFERENCE_RATE, poll_endpoint_s: float = 1.0, **_ignored) -> None:
        if not is_available():
            raise RuntimeError("WASAPI loopback unavailable (not Windows / PyAudioWPatch missing)")
        self.output_rate = int(rate)
        self._ring = _RefRing(rate=self.output_rate)
        self._pa = None
        self._stream = None
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._poll_endpoint_s = max(0.25, float(poll_endpoint_s))
        # Native format of the currently-open loopback endpoint.
        self._src_rate = 48000
        self._src_channels = 2
        self._src_is_int16 = True
        self._cur_endpoint_index: int | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.device_name: str = "default render (loopback)"

    # ------------------------------------------------------------------
    def start(self) -> bool:
        try:
            import pyaudiowpatch as pyaudio

            self._pa = pyaudio.PyAudio()
        except Exception as e:
            self.last_error = f"PyAudio init failed: {e}"
            logger.debug("loopback PyAudio init failed", exc_info=True)
            return False
        if not self._open_default_loopback():
            self.stop()
            return False
        self._stop.clear()
        self._monitor = threading.Thread(
            target=self._endpoint_monitor, name="rtv-loopback-mon", daemon=True
        )
        self._monitor.start()
        logger.info(
            "Realtime AEC: WASAPI loopback reference started (device='%s', %s Hz x%d)",
            self.device_name, self._src_rate, self._src_channels,
        )
        return True

    def stop(self) -> None:
        self._stop.set()
        self._close_stream()
        pa = self._pa
        self._pa = None
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass

    def feed_playback(self, pcm16: bytes) -> None:  # noqa: D401 - interface no-op
        # Active-capture backend: the reference comes from the OS loopback, so
        # the app's playback bytes are ignored here.
        return

    def read(self, nbytes: int) -> bytes:
        return self._ring.read(nbytes)

    def latest(self, nbytes: int) -> bytes:
        return self._ring.latest(nbytes)

    # ------------------------------------------------------------------
    def _resolve_default_loopback(self):
        """Return the PyAudio device-info dict for the default speakers' loopback."""
        import pyaudiowpatch as pyaudio

        pa = self._pa
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        # Loopback endpoints are separate input devices whose name contains the
        # render device's name plus a "[Loopback]" suffix.
        if not default_out.get("isLoopbackDevice", False):
            for lb in pa.get_loopback_device_info_generator():
                if default_out["name"] in lb["name"]:
                    return lb, wasapi["defaultOutputDevice"]
            return None, wasapi["defaultOutputDevice"]
        return default_out, wasapi["defaultOutputDevice"]

    def _open_default_loopback(self) -> bool:
        import pyaudiowpatch as pyaudio

        try:
            info, endpoint_index = self._resolve_default_loopback()
        except Exception as e:
            self.last_error = f"resolve loopback failed: {e}"
            logger.debug("resolve loopback failed", exc_info=True)
            return False
        if info is None:
            self.last_error = "no loopback endpoint found for default speakers"
            return False

        channels = max(1, int(info.get("maxInputChannels", 2)))
        rate = int(info.get("defaultSampleRate", 48000))
        index = int(info["index"])
        # Prefer int16 (cheap); fall back to float32 if the endpoint rejects it.
        for fmt, is_int16 in ((pyaudio.paInt16, True), (pyaudio.paFloat32, False)):
            try:
                blocksize = max(1, int(rate * 0.02))  # ~20 ms
                stream = self._pa.open(
                    format=fmt,
                    channels=channels,
                    rate=rate,
                    frames_per_buffer=blocksize,
                    input=True,
                    input_device_index=index,
                    stream_callback=self._cb,
                )
                with self._lock:
                    self._close_stream_locked()
                    self._stream = stream
                    self._src_rate = rate
                    self._src_channels = channels
                    self._src_is_int16 = is_int16
                    self._cur_endpoint_index = endpoint_index
                    self.device_name = str(info.get("name", "loopback"))
                stream.start_stream()
                return True
            except Exception:
                logger.debug("loopback open failed fmt=%s", fmt, exc_info=True)
                continue
        self.last_error = "could not open loopback stream (int16/float32 both failed)"
        return False

    def _cb(self, in_data, frame_count, time_info, status):  # noqa: ANN001
        import pyaudiowpatch as pyaudio

        try:
            with self._lock:
                is_int16 = self._src_is_int16
                channels = self._src_channels
                rate = self._src_rate
            dtype = np.int16 if is_int16 else np.float32
            arr = np.frombuffer(in_data, dtype=dtype)
            self._ring.append(to_mono(arr, channels, rate, self.output_rate))
        except Exception:
            logger.debug("loopback callback failed", exc_info=True)
        return (None, pyaudio.paContinue)

    def _endpoint_monitor(self) -> None:
        """Reopen loopback if the user switches the default render endpoint."""
        import pyaudiowpatch as pyaudio

        while not self._stop.wait(self._poll_endpoint_s):
            try:
                wasapi = self._pa.get_host_api_info_by_type(pyaudio.paWASAPI)
                new_default = wasapi["defaultOutputDevice"]
            except Exception:
                continue
            with self._lock:
                cur = self._cur_endpoint_index
            if new_default != cur:
                logger.info(
                    "Realtime AEC: default render endpoint changed -> reopening loopback"
                )
                self._ring.clear()
                self._open_default_loopback()

    # ------------------------------------------------------------------
    def _close_stream(self) -> None:
        with self._lock:
            self._close_stream_locked()

    def _close_stream_locked(self) -> None:
        s = self._stream
        self._stream = None
        if s is not None:
            try:
                s.stop_stream()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
