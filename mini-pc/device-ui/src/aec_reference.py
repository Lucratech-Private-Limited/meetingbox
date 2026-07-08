"""Far-end (playback) reference source for acoustic echo cancellation.

The AEC engine needs to know what the speakers are actually emitting so it can
subtract that echo from the microphone. The *only* part of the audio pipeline
that differs by OS is WHERE that reference comes from:

  * Windows -> WASAPI **loopback** capture of the real render endpoint. This is
    the post-volume, post-mix signal the speaker actually plays, so the
    reference is inherently correct at any volume and on any device.
    See ``aec_reference_windows.py``.
  * macOS   -> the app's own playback PCM (fed in by the session). macOS has no
    first-class system-loopback API without a virtual device, and we already
    have the exact bytes we send to the speaker, so we use those directly.
    See ``aec_reference_macos.py``.

Both implementations expose the same :class:`FarEndReference` interface, so the
AEC engine and the mic pump never branch on platform — they just ask the
reference for the most-recent far-end audio at the pipeline rate.

This module is import-safe on every OS: selecting a backend never imports the
other OS's module, and each backend degrades to a no-op if its native audio
API is unavailable.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

# Pipeline rate the realtime session runs the AEC at (matches _REALTIME_RATE).
REFERENCE_RATE = 24000

_IS_WIN = sys.platform.startswith("win")
_IS_MAC = sys.platform == "darwin"


@runtime_checkable
class FarEndReference(Protocol):
    """Common interface for every OS-specific far-end reference source."""

    #: Sample rate (Hz) of the mono PCM16 this source produces.
    output_rate: int
    #: True if the source actively captures its own reference (Windows
    #: loopback); False if it must be fed the app's playback (macOS).
    active_capture: bool

    def start(self) -> bool:
        """Begin producing reference audio. Returns True on success."""
        ...

    def stop(self) -> None:
        """Stop and release resources. Safe to call repeatedly."""
        ...

    def feed_playback(self, pcm16: bytes) -> None:
        """Push app playback PCM16 (mono, ``output_rate``).

        No-op for active-capture backends (Windows), which get the reference
        straight from the OS loopback instead.
        """
        ...

    def read(self, nbytes: int) -> bytes:
        """Consume and return up to ``nbytes`` of far-end PCM16 from the front.

        Used to feed the AEC's reverse (far-end) stream in lockstep with the
        near-end mic. Zero-pads (returns ``b""``) when the ring is empty.
        """
        ...

    def latest(self, nbytes: int) -> bytes:
        """Return the most-recent ``nbytes`` of far-end PCM16 without consuming.

        Used for delay-agnostic checks (e.g. barge-in energy comparison).
        """
        ...


class _RefRing:
    """Thread-safe mono PCM16 ring shared by the OS backends.

    Producers (a loopback thread on Windows, or ``feed_playback`` on macOS)
    append; the AEC consumer ``read``s from the front in lockstep with the mic,
    while ``latest`` peeks the tail for energy checks. Capacity-bounded so a
    stalled consumer can never grow memory without limit.
    """

    def __init__(self, rate: int = REFERENCE_RATE, max_seconds: float = 5.0) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._max_bytes = int(rate * 2 * max_seconds)

    def append(self, pcm16: bytes) -> None:
        if not pcm16:
            return
        with self._lock:
            self._buf.extend(pcm16)
            excess = len(self._buf) - self._max_bytes
            if excess > 0:
                del self._buf[:excess]

    def read(self, nbytes: int) -> bytes:
        if nbytes <= 0:
            return b""
        with self._lock:
            if not self._buf:
                return b""
            take = min(nbytes, len(self._buf))
            out = bytes(self._buf[:take])
            del self._buf[:take]
            return out

    def latest(self, nbytes: int) -> bytes:
        if nbytes <= 0:
            return b""
        with self._lock:
            if len(self._buf) <= nbytes:
                return bytes(self._buf)
            return bytes(self._buf[-nbytes:])

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


def to_mono(pcm: np.ndarray, channels: int, src_rate: int, dst_rate: int) -> bytes:
    """Downmix interleaved PCM to mono and linear-resample to ``dst_rate``.

    ``pcm`` is a 1-D int16/float32 array of interleaved samples. Loopback
    endpoints are commonly 48 kHz stereo; the AEC engine runs at a fixed rate
    (24 kHz for the Speex path, 48 kHz for WebRTC AEC3), so we average channels
    then resample. Linear interpolation is fine here: the reference only needs
    to match the mic's band, and the AEC's own filtering dominates quality.
    """
    if pcm.size == 0:
        return b""
    if pcm.dtype != np.float32:
        # int16 (or other) -> normalized float32 in [-1, 1]
        pcm = pcm.astype(np.float32) / 32768.0
    if channels > 1:
        n = (pcm.size // channels) * channels
        pcm = pcm[:n].reshape(-1, channels).mean(axis=1)
    if src_rate != dst_rate and pcm.size >= 2:
        dur = pcm.size / float(src_rate)
        n_dst = max(1, int(dur * dst_rate))
        x_src = np.linspace(0.0, dur, num=pcm.size, endpoint=False)
        x_dst = np.linspace(0.0, dur, num=n_dst, endpoint=False)
        pcm = np.interp(x_dst, x_src, pcm)
    return (np.clip(pcm, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def to_mono_24k(pcm: np.ndarray, channels: int, src_rate: int) -> bytes:
    """Back-compat wrapper: downmix + resample to the 24 kHz pipeline rate."""
    return to_mono(pcm, channels, src_rate, REFERENCE_RATE)


def create_reference(rate: int = REFERENCE_RATE, **kwargs) -> FarEndReference | None:
    """Return the correct far-end reference source for this OS, or ``None``.

    ``rate`` is the sample rate (Hz) the reference should produce, chosen by the
    AEC engine that consumes it (24 kHz for Speex, 48 kHz for WebRTC AEC3).

    ``None`` means no OS-specific reference is available (e.g. Linux appliance,
    or a Windows box where WASAPI loopback can't open); callers should fall
    back to their existing playback-derived reference in that case.
    """
    if _IS_WIN:
        try:
            from aec_reference_windows import WasapiLoopbackReference

            return WasapiLoopbackReference(rate=rate, **kwargs)
        except Exception:
            logger.debug("WASAPI loopback reference unavailable", exc_info=True)
            return None
    if _IS_MAC:
        try:
            from aec_reference_macos import AppPlaybackReference

            return AppPlaybackReference(rate=rate, **kwargs)
        except Exception:
            logger.debug("macOS playback reference unavailable", exc_info=True)
            return None
    return None
