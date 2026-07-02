"""Validation probe for the WASAPI-loopback far-end reference (Phase 1).

Proves, on THIS machine, that:
  1. The loopback reference captures what the speakers actually play (its RMS
     jumps when a tone plays and is ~0 during silence).
  2. It tracks the system volume (louder playback -> louder reference), which is
     why it is a volume-correct echo reference.
  3. We can estimate the speaker->mic acoustic delay by cross-correlating the
     loopback reference against the raw mic — the number AEC3 uses to align.

This does NOT run any echo canceller; it validates the *reference source* that
the AEC will consume. Run from the repo root:

    device-ui\.venv\Scripts\python.exe packaging\windows\probe_loopback_ref.py

Play with the system volume between runs to see the reference RMS scale.
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

# Make the device-ui source importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "device-ui", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import sounddevice as sd
except Exception as e:  # pragma: no cover
    print("sounddevice unavailable:", e)
    sys.exit(2)

from aec_reference import REFERENCE_RATE
from aec_reference_windows import WasapiLoopbackReference, is_available

TONE_HZ = 440.0
TONE_S = 2.0


def _rms(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    s = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(s ** 2))) if s.size else 0.0


def _play_tone(seconds: float, amp: float = 0.3, rate: int = 48000) -> None:
    t = np.arange(int(seconds * rate)) / rate
    tone = (np.sin(2 * np.pi * TONE_HZ * t) * amp).astype(np.float32)
    sd.play(tone, samplerate=rate, blocking=True)


def main() -> int:
    if not is_available():
        print("FAIL: WASAPI loopback not available (need Windows + PyAudioWPatch).")
        print("      pip install PyAudioWPatch")
        return 2

    ref = WasapiLoopbackReference()
    if not ref.start():
        print("FAIL: could not start loopback reference:", ref.last_error)
        return 2
    print(f"Loopback endpoint: {ref.device_name}")
    try:
        # --- Silence baseline -------------------------------------------------
        ref.read(REFERENCE_RATE * 2)  # flush
        time.sleep(0.5)
        silence = ref.read(REFERENCE_RATE * 2)
        silence_rms = _rms(silence)

        # --- Tone playback ----------------------------------------------------
        print(f"Playing {TONE_HZ:.0f} Hz tone for {TONE_S:.0f}s (adjust volume to test)...")
        ref.read(REFERENCE_RATE * 4)  # flush
        _play_tone(TONE_S)
        time.sleep(0.1)
        tone_ref = ref.read(int(REFERENCE_RATE * 2 * TONE_S))
        tone_rms = _rms(tone_ref)

        print(f"\nReference RMS  silence={silence_rms:8.1f}   tone={tone_rms:8.1f}")
        if tone_rms > max(50.0, silence_rms * 5):
            print("PASS: loopback reference captures speaker output (scales with volume).")
        else:
            print("WARN: reference did not rise on playback — check default output device.")

        # --- Delay estimate vs mic -------------------------------------------
        print("\nEstimating speaker->mic delay (play tone + record mic)...")
        rec = sd.rec(int(TONE_S * REFERENCE_RATE), samplerate=REFERENCE_RATE,
                     channels=1, dtype="int16")
        ref.read(REFERENCE_RATE * 4)  # flush
        _play_tone(TONE_S)
        sd.wait()
        mic = np.frombuffer(rec.tobytes(), dtype=np.int16).astype(np.float32)
        far = np.frombuffer(ref.read(int(REFERENCE_RATE * 2 * TONE_S)),
                            dtype=np.int16).astype(np.float32)
        n = min(len(mic), len(far))
        if n > REFERENCE_RATE // 2:
            mic_n = mic[:n] - mic[:n].mean()
            far_n = far[:n] - far[:n].mean()
            # Cross-correlate over a bounded lag window (+/- 200 ms).
            max_lag = int(0.2 * REFERENCE_RATE)
            corr = np.correlate(mic_n, far_n[:max_lag * 2], mode="valid")
            lag = int(np.argmax(corr))
            delay_ms = lag / REFERENCE_RATE * 1000.0
            print(f"Estimated speaker->mic delay: ~{delay_ms:.0f} ms  "
                  f"(mic echo RMS={_rms(rec.tobytes()):.1f})")
        else:
            print("Not enough audio to estimate delay.")
    finally:
        ref.stop()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
