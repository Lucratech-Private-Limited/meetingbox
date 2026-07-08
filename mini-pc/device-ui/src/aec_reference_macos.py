"""macOS far-end reference from the app's own playback PCM.

macOS has no first-class, install-free way to capture the system output mix
(unlike Windows WASAPI loopback): a true loopback needs a virtual audio device
(BlackHole/Loopback) or Core Audio process taps (macOS 14.4+ only). But we
already produce the exact bytes we send to the speaker via ``PcmStreamPlayer``,
so we use those directly as the echo reference.

The reference is fed *pre-volume* (it's what we hand to the OS, before the
system volume slider), so it differs from the true acoustic output by a scalar
gain. The AEC engine (WebRTC AEC3) estimates the echo-path gain adaptively and
its nonlinear residual suppressor cleans up what a fixed-gain reference can't,
so full-duplex still works without a system-loopback capture.

macOS-only by intent, but import-safe everywhere. A future upgrade can add a
Core Audio process-tap backend on macOS 14.4+ for a true post-mix reference.
"""

from __future__ import annotations

import logging
import sys

import numpy as np

from aec_reference import REFERENCE_RATE, _RefRing, to_mono

logger = logging.getLogger(__name__)

_IS_MAC = sys.platform == "darwin"


def is_available() -> bool:
    return _IS_MAC


class AppPlaybackReference:
    """Passive far-end reference: the session feeds it the playback PCM it queues."""

    output_rate = REFERENCE_RATE
    active_capture = False

    def __init__(self, rate: int = REFERENCE_RATE, feed_rate: int = REFERENCE_RATE, **_ignored) -> None:
        self.output_rate = int(rate)
        # Rate of the PCM the session hands us (its playback clock = 24 kHz).
        self._feed_rate = int(feed_rate)
        self._ring = _RefRing(rate=self.output_rate)
        self.device_name = "app playback (pre-volume)"
        self.last_error: str | None = None

    def start(self) -> bool:
        logger.info("Realtime AEC: macOS app-playback reference active")
        return True

    def stop(self) -> None:
        self._ring.clear()

    def feed_playback(self, pcm16: bytes) -> None:
        # Session hands us the same mono PCM it sends to the speaker (24 kHz).
        # Resample to the AEC engine's rate so the far-end matches the near-end.
        if not pcm16:
            return
        if self._feed_rate == self.output_rate:
            self._ring.append(pcm16)
        else:
            arr = np.frombuffer(pcm16, dtype=np.int16)
            self._ring.append(to_mono(arr, 1, self._feed_rate, self.output_rate))

    def read(self, nbytes: int) -> bytes:
        return self._ring.read(nbytes)

    def latest(self, nbytes: int) -> bytes:
        return self._ring.latest(nbytes)
