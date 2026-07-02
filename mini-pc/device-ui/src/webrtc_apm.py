"""WebRTC AEC3 engine wrapper (the echo canceller Chrome/Meet/Discord use).

Thin adapter over the ``pywebrtc_audio`` native module (genuine WebRTC audio
processing: AEC3 + noise suppression + high-pass + optional AGC2, plus a
spectral speech-probability VAD). We wrap it so the realtime session sees one
simple call — ``process(near, far) -> cleaned`` — and never has to know about
WebRTC's fixed 10 ms framing or which sample rate it runs at.

Design:
  * WebRTC APM only supports 16 / 32 / 48 kHz and processes in exact 10 ms
    frames. This wrapper buffers arbitrary-length near/far byte chunks and emits
    whole cleaned frames, keeping leftovers for the next call — same contract as
    the Speex ``_aec_process`` path it replaces.
  * ``far`` is the loopback / playback reference (see ``aec_reference.py``). The
    engine aligns it internally via AEC3's adaptive delay estimator; a coarse
    ``stream_delay_ms`` hint speeds convergence.
  * Import-guarded: ``is_available()`` is False until the native module is built
    and importable, so callers fall back to Speex / OS-AEC cleanly.

Sample-rate note: the realtime pipeline is 24 kHz, which WebRTC does NOT accept.
Callers run this engine at 48 kHz (loopback's native rate, cleanest) or 16 kHz
and resample the cleaned output to 24 kHz for upload.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_SUPPORTED_RATES = (16000, 32000, 48000)


import_error: str | None = None


def _import_apm():
    global import_error
    try:
        import pywebrtc_audio  # noqa: F401

        return pywebrtc_audio
    except Exception as e:
        import_error = f"{type(e).__name__}: {e}"
        return None


_apm = _import_apm()


def is_available() -> bool:
    return _apm is not None


class WebRtcAEC:
    """AEC3-based echo canceller for PCM16 mono frames at 16/32/48 kHz.

    ``process(near, far)`` returns echo-cancelled PCM16 for every whole 10 ms
    frame available; partial trailing samples are buffered until the next call.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        *,
        noise_suppression: bool = True,
        high_pass_filter: bool = True,
        auto_gain_control: bool = False,
        ns_level: int = 2,
        stream_delay_ms: int = 0,
    ) -> None:
        if _apm is None:
            raise RuntimeError("pywebrtc_audio not available")
        if sample_rate not in _SUPPORTED_RATES:
            raise ValueError(f"WebRTC APM rate must be one of {_SUPPORTED_RATES}")
        self.sample_rate = int(sample_rate)
        self.frame_samples = self.sample_rate // 100  # 10 ms
        self._frame_bytes = self.frame_samples * 2
        # Combined pipeline (matches Chrome's capture processing order:
        # HPF -> AEC -> NS -> AGC). echo_cancellation forces the HPF on too.
        self._ap = _apm.AudioProcessor(
            sample_rate=self.sample_rate,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=noise_suppression,
            high_pass_filter=high_pass_filter,
            auto_gain_control=auto_gain_control,
            ns_level=int(ns_level),
            stream_delay_ms=int(stream_delay_ms),
        )
        self._near_buf = bytearray()
        self._far_buf = bytearray()
        logger.info(
            "Realtime AEC: WebRTC AEC3 engine ready (rate=%d, ns=%s, hpf=%s, agc=%s)",
            self.sample_rate, noise_suppression, high_pass_filter, auto_gain_control,
        )

    @property
    def speech_probability(self) -> float:
        try:
            return float(self._ap.speech_probability)
        except Exception:
            return 0.0

    @property
    def stream_delay_ms(self) -> int:
        return int(self._ap.stream_delay_ms)

    @stream_delay_ms.setter
    def stream_delay_ms(self, value: int) -> None:
        try:
            self._ap.stream_delay_ms = int(value)
        except Exception:
            pass

    def process(self, near: bytes, far: bytes) -> bytes:
        """Cancel echo from ``near`` using ``far`` reference. Both PCM16 mono.

        ``far`` may be shorter than ``near`` (reference ran dry) — it is
        zero-padded per frame so the near-end still flows.
        """
        if not near:
            return b""
        self._near_buf.extend(near)
        self._far_buf.extend(far)
        out = bytearray()
        fb = self._frame_bytes
        while len(self._near_buf) >= fb:
            near_frame = bytes(self._near_buf[:fb])
            del self._near_buf[:fb]
            if len(self._far_buf) >= fb:
                far_frame = bytes(self._far_buf[:fb])
                del self._far_buf[:fb]
            else:
                # Not enough reference yet: consume what's there, zero-pad.
                have = bytes(self._far_buf)
                self._far_buf.clear()
                far_frame = have + b"\x00" * (fb - len(have))
            try:
                near_arr = np.frombuffer(near_frame, dtype=np.int16)
                far_arr = np.frombuffer(far_frame, dtype=np.int16)
                cleaned = self._ap.process(near_arr, far_arr)
                out.extend(np.asarray(cleaned, dtype=np.int16).tobytes())
            except Exception:
                logger.debug("WebRTC APM process failed", exc_info=True)
                out.extend(near_frame)
        return bytes(out)

    def reset(self) -> None:
        try:
            self._ap.reset()
        except Exception:
            pass
        self._near_buf.clear()
        self._far_buf.clear()

    def close(self) -> None:
        self._near_buf.clear()
        self._far_buf.clear()
        self._ap = None
