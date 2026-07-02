"""Measure echo cancellation quality (ERLE) of WebRTC AEC3 + loopback reference.

This mirrors the real session path in ``realtime_voice_session.py``: run the
genuine WebRTC AEC3 engine at 48 kHz, near-end = the mic, far-end = the WASAPI
loopback of what the speaker actually played. Scenario = the phantom-speech case:
the assistant speaks and the user is SILENT, so a good AEC drives the residual
into the noise floor and the server VAD hears nothing.

Metrics:
  echo_dbfs     - raw mic echo (what the server VAD hears WITHOUT cancellation).
  residual_dbfs - after AEC3 + loopback cancellation (what it actually hears).
  ERLE          - echo return loss enhancement in dB (echo - residual). Higher
                  is better; ChatGPT/Meet-grade is ~30-40 dB.

Run (say nothing while it plays):
    device-ui\\.venv\\Scripts\\python.exe packaging\\windows\\probe_aec3_erle.py
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "device-ui", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import sounddevice as sd
except Exception as e:  # pragma: no cover
    print("sounddevice unavailable:", e)
    sys.exit(2)

import webrtc_apm
from aec_reference_windows import WasapiLoopbackReference, is_available

RATE = 48000       # AEC3 engine rate (also loopback native rate)
FRAME = 480        # 10 ms @ 48 kHz (WebRTC APM frame)
TEST_S = 6.0


def _dbfs(pcm_f: np.ndarray) -> float:
    if pcm_f.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(pcm_f.astype(np.float64) ** 2)))
    if rms <= 1e-9:
        return -120.0
    return 20.0 * np.log10(rms / 32768.0)


def _make_test_signal(seconds: float, rate: int) -> np.ndarray:
    """Broadband, speech-band-weighted noise burst (harder on AEC than a tone)."""
    n = int(seconds * rate)
    rng = np.random.default_rng(1234)
    x = rng.standard_normal(n).astype(np.float32)
    k = 16
    x = np.convolve(x, np.ones(k) / k, mode="same")            # low-pass
    x = x - np.convolve(x, np.ones(256) / 256, mode="same")    # high-pass
    x /= (np.max(np.abs(x)) + 1e-6)
    return (x * 0.4).astype(np.float32)


def _onset(pcm_f: np.ndarray, thresh_ratio: float = 0.15) -> int:
    if pcm_f.size == 0:
        return 0
    env = np.abs(pcm_f)
    peak = float(np.max(env))
    if peak <= 1e-6:
        return 0
    return int(np.argmax(env > peak * thresh_ratio))


def main() -> int:
    if not is_available():
        print("FAIL: WASAPI loopback not available (need Windows + PyAudioWPatch).")
        return 2
    if not webrtc_apm.is_available():
        print("FAIL: pywebrtc_audio (WebRTC AEC3) not importable in this venv.")
        return 2

    ref = WasapiLoopbackReference(rate=RATE)
    if not ref.start():
        print("FAIL: loopback reference did not start:", ref.last_error)
        return 2
    print(f"Loopback endpoint: {ref.device_name} @ {ref.output_rate} Hz")
    print("Playing test signal — please STAY SILENT for ~6s...\n")

    try:
        signal = _make_test_signal(TEST_S, RATE)
        ref.read(RATE * 4)  # flush ring
        rec = sd.playrec(signal.reshape(-1, 1), samplerate=RATE,
                         channels=1, dtype="int16")
        sd.wait()
        time.sleep(0.2)
        mic = np.frombuffer(rec.tobytes(), dtype=np.int16).astype(np.float32)
        far = np.frombuffer(ref.read(int(RATE * 2 * (TEST_S + 0.5))),
                            dtype=np.int16).astype(np.float32)
    finally:
        ref.stop()

    # Coarse onset alignment; AEC3's adaptive delay estimator handles the rest.
    off_mic, off_far = _onset(mic), _onset(far)
    if off_mic >= off_far:
        mic = mic[off_mic - off_far:]
    else:
        far = far[off_far - off_mic:]
    n = min(len(mic), len(far))
    n -= n % FRAME
    if n < FRAME * 50:
        print("FAIL: not enough aligned audio (is the mic hearing the speaker?).")
        return 2
    mic, far = mic[:n], far[:n]

    def run_aec3(far_ref: np.ndarray) -> np.ndarray:
        aec = webrtc_apm.WebRtcAEC(sample_rate=RATE, noise_suppression=True,
                                   high_pass_filter=True, auto_gain_control=False)
        out = np.empty_like(mic)
        for i in range(0, n, FRAME):
            near_b = mic[i:i + FRAME].astype(np.int16).tobytes()
            far_b = far_ref[i:i + FRAME].astype(np.int16).tobytes()
            cleaned = aec.process(near_b, far_b)
            c = np.frombuffer(cleaned, dtype=np.int16).astype(np.float32)
            out[i:i + len(c)] = c[:FRAME] if len(c) >= FRAME else np.pad(c, (0, FRAME - len(c)))
        aec.close()
        return out

    # Skip the first ~1.5 s (AEC3 delay/filter convergence) when scoring.
    skip = int(1.5 * RATE)
    skip -= skip % FRAME

    resid_loopback = run_aec3(far)
    resid_silence = run_aec3(np.zeros_like(far))

    r_loop = _dbfs(resid_loopback[skip:])
    r_none = _dbfs(resid_silence[skip:])
    e_conv = _dbfs(mic[skip:])

    print("=" * 60)
    print(f"Raw mic echo (no AEC)            : {e_conv:7.1f} dBFS")
    print(f"AEC3 + NO reference (silence)    : {r_none:7.1f} dBFS   (ERLE {e_conv - r_none:5.1f} dB)")
    print(f"AEC3 + LOOPBACK reference        : {r_loop:7.1f} dBFS   (ERLE {e_conv - r_loop:5.1f} dB)")
    print("=" * 60)
    erle = e_conv - r_loop
    print(f"\nLoopback reference adds {r_none - r_loop:.1f} dB over no reference.")
    if erle >= 30:
        print(f"RESULT: {erle:.1f} dB ERLE — ChatGPT/Meet-grade. Phantom speech eliminated.")
    elif erle >= 20:
        print(f"RESULT: {erle:.1f} dB ERLE — clean full-duplex range.")
    else:
        print(f"RESULT: {erle:.1f} dB ERLE — low; check that playback actually reached the speaker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
