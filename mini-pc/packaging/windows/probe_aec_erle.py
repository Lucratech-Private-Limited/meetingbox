"""Measure echo cancellation quality (ERLE) of Speex fed the loopback reference.

Scenario = the exact phantom-speech case: the assistant speaks and the user is
SILENT. We play a broadband test signal out the speakers, record what the mic
picks up (pure echo), and the WASAPI-loopback reference of what was played, then
run the already-shipped Speex canceller on (near=mic, far=loopback) and measure
how much echo it removes.

Metrics:
  echo_dbfs     - level of the raw mic echo (what the server VAD would hear
                  WITHOUT cancellation -> phantom speech).
  residual_dbfs - level after Speex+loopback cancellation (what the server VAD
                  would actually hear). Lower = fewer/no false triggers.
  ERLE          - echo return loss enhancement in dB (echo - residual). Higher
                  is better; ~25-35 dB is clean full-duplex territory.

Also prints the residual when Speex gets NO reference (far=silence), to isolate
how much the loopback reference alone contributes.

Run (say nothing while it plays):
    device-ui\.venv\Scripts\python.exe packaging\windows\probe_aec_erle.py
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

import _aec
from aec_reference import REFERENCE_RATE
from aec_reference_windows import WasapiLoopbackReference, is_available

RATE = REFERENCE_RATE  # 24 kHz, matches the realtime pipeline + Speex frame size
FRAME = 480            # 20 ms @ 24 kHz (Speex frame_size)
TEST_S = 5.0


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
    # crude band-limit to ~300-3400 Hz speech band via moving average + diff
    k = 8
    x = np.convolve(x, np.ones(k) / k, mode="same")  # low-pass
    x = x - np.convolve(x, np.ones(64) / 64, mode="same")  # high-pass
    x /= (np.max(np.abs(x)) + 1e-6)
    return (x * 0.4).astype(np.float32)


def _onset(pcm_f: np.ndarray, thresh_ratio: float = 0.15) -> int:
    if pcm_f.size == 0:
        return 0
    env = np.abs(pcm_f)
    peak = float(np.max(env))
    if peak <= 1e-6:
        return 0
    idx = np.argmax(env > peak * thresh_ratio)
    return int(idx)


def main() -> int:
    if not is_available():
        print("FAIL: WASAPI loopback not available (need Windows + PyAudioWPatch).")
        return 2
    if not _aec.is_available():
        print("FAIL: libspeexdsp not available in this environment.")
        return 2

    ref = WasapiLoopbackReference()
    if not ref.start():
        print("FAIL: loopback reference did not start:", ref.last_error)
        return 2
    print(f"Loopback endpoint: {ref.device_name}")
    print("Playing test signal — please STAY SILENT for ~5s...\n")

    try:
        signal = _make_test_signal(TEST_S, RATE)
        ref.read(RATE * 4)  # flush ring
        # Play the test signal and record the mic on ONE synchronized duplex
        # stream (sd.rec + a separate sd.play clobber each other's stream).
        rec = sd.playrec(signal.reshape(-1, 1), samplerate=RATE,
                         channels=1, dtype="int16")
        sd.wait()
        time.sleep(0.15)

        mic = np.frombuffer(rec.tobytes(), dtype=np.int16).astype(np.float32)
        far = np.frombuffer(ref.read(int(RATE * 2 * (TEST_S + 0.3))),
                            dtype=np.int16).astype(np.float32)
    finally:
        ref.stop()

    # Align mic echo to the loopback reference by signal onset.
    off_mic, off_far = _onset(mic), _onset(far)
    if off_mic >= off_far:
        mic = mic[off_mic - off_far:]
    else:
        far = far[off_far - off_mic:]
    n = min(len(mic), len(far))
    n -= n % FRAME
    if n < FRAME * 50:
        print("FAIL: not enough aligned audio captured (mic hearing the speaker?).")
        return 2
    mic, far = mic[:n], far[:n]

    echo_dbfs = _dbfs(mic)

    def run_speex(far_ref: np.ndarray) -> np.ndarray:
        aec = _aec.SpeexAEC(frame_size=FRAME, filter_length=FRAME * 10, sample_rate=RATE)
        out = np.empty_like(mic)
        for i in range(0, n, FRAME):
            near_b = mic[i:i + FRAME].astype(np.int16).tobytes()
            far_b = far_ref[i:i + FRAME].astype(np.int16).tobytes()
            cleaned = aec.cancel(near_b, far_b)
            out[i:i + FRAME] = np.frombuffer(cleaned, dtype=np.int16).astype(np.float32)
        aec.close()
        return out

    # Skip the first ~1.5 s (filter convergence) when scoring.
    skip = int(1.5 * RATE)
    skip -= skip % FRAME

    resid_loopback = run_speex(far)
    resid_silence = run_speex(np.zeros_like(far))

    r_loop = _dbfs(resid_loopback[skip:])
    r_none = _dbfs(resid_silence[skip:])
    e_conv = _dbfs(mic[skip:])

    print("=" * 58)
    print(f"Raw mic echo (no AEC)            : {e_conv:7.1f} dBFS")
    print(f"Speex + NO reference (silence)   : {r_none:7.1f} dBFS   (ERLE {e_conv - r_none:5.1f} dB)")
    print(f"Speex + LOOPBACK reference       : {r_loop:7.1f} dBFS   (ERLE {e_conv - r_loop:5.1f} dB)")
    print("=" * 58)
    erle = e_conv - r_loop
    gain_from_loopback = r_none - r_loop
    print(f"\nLoopback reference adds {gain_from_loopback:.1f} dB over no reference.")
    if erle >= 25:
        print(f"RESULT: {erle:.1f} dB ERLE — clean full-duplex range; phantom speech should vanish.")
    elif erle >= 15:
        print(f"RESULT: {erle:.1f} dB ERLE — decent; may still need AEC3 for loud/coupled devices.")
    else:
        print(f"RESULT: {erle:.1f} dB ERLE — insufficient; AEC3-grade engine warranted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
