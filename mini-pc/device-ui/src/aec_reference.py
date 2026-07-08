"""Far-end (playback) reference source for acoustic echo cancellation.

The AEC engine needs to know what the speakers are actually emitting so it can
subtract that echo from the microphone.

The default reference on every desktop OS is the **render feed**: the exact
PCM blocks our own playback callback hands to the audio device, fed at device
pace (see :class:`AppPlaybackReference` + the ``on_pcm`` tap in
``audio_output.PcmStreamPlayer``). This is the architecture Chrome / the
ChatGPT desktop app use (WebRTC's ProcessReverseStream is fed the app's own
playout): the reference can never go blind, is paced by the same device clock
that produces the acoustic echo, and audio dropped by a barge-in abort is
never fed. AEC3's adaptive delay estimator absorbs the output latency.

Windows can alternatively capture the system mix via WASAPI **loopback**
(``aec_reference_windows.py``, opt-in via ``REALTIME_AEC_REFERENCE=loopback``).
Loopback also covers other apps' audio, but in production it proved unreliable
as a realtime reference: its capture callbacks run on a different device clock
than the mic consumer, and every consumer-side underrun inserts zeros that
permanently shift the reference timeline — AEC3 then intermittently sees a
silent far end while the speaker is loud ("reference blind") and cancellation
collapses. The render feed has no such race.

All implementations expose the same :class:`FarEndReference` interface, so the
AEC engine and the mic pump never branch on platform.

This module is import-safe on every OS: selecting a backend never imports the
other OS's module, and each backend degrades to a no-op if its native audio
API is unavailable.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
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

    def occupancy(self) -> int:
        with self._lock:
            return len(self._buf)

    def drop_front(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        with self._lock:
            del self._buf[:nbytes]

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


class AppPlaybackReference:
    """Passive far-end reference fed the app's own playback PCM (render feed).

    The playback sink's device callback (or, as a fallback, the session's
    playback path) hands us the exact mono PCM the speaker is being given, at
    device pace. That is by construction the echo source, so it is the ideal
    reference: never blind, never drifting relative to the rendered audio.

    The reference is pre-volume (what we hand to the OS, before the system
    volume slider), so it differs from the true acoustic output by a scalar
    gain. WebRTC AEC3 estimates the echo-path gain adaptively and its
    nonlinear residual suppressor cleans up the rest.

    Ride-height control (device-paced mode): the tap produces at the OUTPUT
    device clock while the mic pump consumes at the INPUT device clock — two
    different crystals. Without correction, drift/scheduling jitter slowly
    drains (or grows) the ring until every read underruns and the AEC sees a
    silent far end while the speaker is loud, collapsing cancellation. When
    ``device_paced`` is set, ``read()`` keeps the ring at a small target
    cushion: an underrun immediately re-primes it (bounded, one-step
    misalignment AEC3's delay estimator re-absorbs) and marks the reference
    *starved* so the session's blind-guard can withhold uncancellable frames;
    chronic overrun is trimmed back to the cushion for the same reason.
    """

    output_rate = REFERENCE_RATE
    active_capture = False

    #: Target ring occupancy (ms). Consumer jitter drains this cushion
    #: instead of underrunning; AEC3 absorbs it as constant extra delay.
    #: Kept shallow: a deeper cushion adds echo-path delay that can push the
    #: far-end reference out of AEC3's delay-estimator sweet spot and leak
    #: Pepper's own voice into the mic. Override via REALTIME_AEC_CUSHION_MS.
    CUSHION_MS = 80.0
    #: Occupancy above which the (device-paced) ring snaps back to the
    #: cushion: past this point the reference lags the mic beyond what the
    #: delay estimator tracks, so cancellation is already lost — a bounded
    #: re-alignment step is strictly better.
    HIGH_WATER_MS = 450.0
    #: How long a starvation event keeps the reference flagged unreliable.
    STARVED_HOLD_S = 0.5

    def __init__(self, rate: int = REFERENCE_RATE, feed_rate: int = REFERENCE_RATE, **_ignored) -> None:
        self.output_rate = int(rate)
        # Rate of the PCM the session hands us (its playback rate = 24 kHz).
        self._feed_rate = int(feed_rate)
        # Shallow ring: the device tap produces in real time and the mic pump
        # consumes in real time, so occupancy hovers at the primed cushion. A
        # small cap means a stalled consumer trims quickly and misalignment is
        # bounded to well under a second (AEC3 re-converges immediately).
        self._ring = _RefRing(rate=self.output_rate, max_seconds=0.6)
        self.device_name = "app render feed (pre-volume)"
        self.last_error: str | None = None
        # True once a device callback tap feeds this reference (real-time
        # producer). Only then is ride-height control valid: the fallback
        # feed (_play_delta on the aplay path) arrives at websocket pace,
        # where occupancy legitimately swings with the queued response.
        self.device_paced = False
        # Allow tuning the ride-height without a code change (deeper cushion =
        # more jitter tolerance at the cost of a little extra echo-path delay).
        try:
            self.CUSHION_MS = float(os.getenv("REALTIME_AEC_CUSHION_MS", self.CUSHION_MS))
            self.HIGH_WATER_MS = float(
                os.getenv("REALTIME_AEC_HIGH_WATER_MS", self.HIGH_WATER_MS)
            )
        except (TypeError, ValueError):
            pass
        self._cushion_bytes = self._ms_to_bytes(self.CUSHION_MS)
        self._high_water_bytes = self._ms_to_bytes(self.HIGH_WATER_MS)
        self._starved_until = 0.0
        self.underruns = 0
        self.overruns = 0
        self._last_flow_log = 0.0

    def _ms_to_bytes(self, ms: float) -> int:
        n = int(self.output_rate * 2 * max(0.0, ms) / 1000.0)
        return n - (n % 2)

    def start(self) -> bool:
        logger.info("Realtime AEC: app render-feed far-end reference active")
        return True

    def stop(self) -> None:
        self._ring.clear()

    def clear(self) -> None:
        """Drop buffered reference audio (e.g. after a barge-in abort)."""
        self._ring.clear()

    def prime(self, ms: float | None = None) -> None:
        """Top the ring up to ``ms`` (default: the cushion) of buffered audio.

        A small silence cushion means consumer-side scheduling jitter (the mic
        pump reads the reference in mic lockstep) drains the cushion instead
        of underrunning — underruns would insert zeros that permanently shift
        the reference timeline against the mic. The cushion is a constant
        extra delay AEC3's estimator absorbs. Top-up semantics keep repeated
        priming (player re-creation after a barge-in abort, underrun
        recovery) from stacking cushions into ever-growing delay.
        """
        target = self._ms_to_bytes(self.CUSHION_MS if ms is None else ms)
        deficit = target - self._ring.occupancy()
        deficit -= deficit % 2
        if deficit > 0:
            self._ring.append(b"\x00" * deficit)

    @property
    def starved_recently(self) -> bool:
        """True shortly after an underrun: the reference just went blind.

        While set, far-end silence cannot be trusted to mean "speaker quiet",
        so the session's AEC-blind guard must withhold playback-time mic
        frames exactly as it does for a lagging loopback capture.
        """
        return time.monotonic() < self._starved_until

    def feed_playback(self, pcm16: bytes) -> None:
        # The exact mono PCM being rendered (playback rate). Resample to the
        # AEC engine's rate so the far-end matches the near-end.
        if not pcm16:
            return
        if self._feed_rate == self.output_rate:
            self._ring.append(pcm16)
        else:
            arr = np.frombuffer(pcm16, dtype=np.int16)
            self._ring.append(to_mono(arr, 1, self._feed_rate, self.output_rate))

    def read(self, nbytes: int) -> bytes:
        out = self._ring.read(nbytes)
        if not self.device_paced:
            return out
        if len(out) < nbytes:
            # Underrun: the consumer clock ran ahead of the producer. Return
            # exact-length silence-padded PCM (keeps AEC 10 ms framing in
            # lockstep), flag the reference unreliable, and restore the
            # cushion so this is a single bounded step — not a permanent
            # drift the AEC can never recover from.
            self.underruns += 1
            self._starved_until = time.monotonic() + self.STARVED_HOLD_S
            out = out + b"\x00" * (nbytes - len(out))
            self.prime()
            self._log_flow("underrun")
        else:
            occ = self._ring.occupancy()
            if occ > self._high_water_bytes:
                # Chronic overrun: reference lags the mic beyond the delay
                # estimator's reach. Snap back to the cushion.
                self.overruns += 1
                self._ring.drop_front(occ - self._cushion_bytes)
                self._log_flow("overrun")
        return out

    def _log_flow(self, kind: str) -> None:
        now = time.monotonic()
        if now - self._last_flow_log < 5.0:
            return
        self._last_flow_log = now
        logger.info(
            "Realtime AEC: render-feed reference %s (underruns=%d overruns=%d)",
            kind, self.underruns, self.overruns,
        )

    def latest(self, nbytes: int) -> bytes:
        return self._ring.latest(nbytes)


def create_reference(rate: int = REFERENCE_RATE, **kwargs) -> FarEndReference | None:
    """Return the correct far-end reference source for this OS, or ``None``.

    ``rate`` is the sample rate (Hz) the reference should produce, chosen by the
    AEC engine that consumes it (24 kHz for Speex, 48 kHz for WebRTC AEC3).

    Desktop default is the render feed (:class:`AppPlaybackReference`) — the
    device-independent architecture Chrome/ChatGPT use. On Windows,
    ``REALTIME_AEC_REFERENCE=loopback`` opts back into the WASAPI system-mix
    capture (covers other apps' audio, but is prone to reference-blind races).

    ``None`` means no reference is available (e.g. Linux appliance); callers
    fall back to their existing playback-derived reference in that case.
    """
    if _IS_WIN:
        mode = (os.getenv("REALTIME_AEC_REFERENCE") or "render").strip().lower()
        if mode == "loopback":
            try:
                from aec_reference_windows import WasapiLoopbackReference

                return WasapiLoopbackReference(rate=rate, **kwargs)
            except Exception:
                logger.debug("WASAPI loopback reference unavailable", exc_info=True)
                # Fall through to the render feed.
        return AppPlaybackReference(rate=rate, **kwargs)
    if _IS_MAC:
        return AppPlaybackReference(rate=rate, **kwargs)
    return None
