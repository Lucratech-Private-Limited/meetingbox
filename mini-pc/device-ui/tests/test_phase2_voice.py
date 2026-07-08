"""Phase 2 evals: voice stack on Windows.

Covers:
* Realtime playback routes to PortAudio (not aplay) on non-Linux, with working
  barge-in (abort + flush) semantics on the streaming sink.
* Windows/macOS native TTS (pyttsx3) availability + contract.
* The wake-phrase fuzzy matcher (deterministic, no audio).
* The offline Vosk ASR pipeline actually runs on Windows over synthetic audio
  (skipped if the model has not been downloaded yet).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

import platform_compat


# --- realtime playback routing -------------------------------------------

def test_realtime_uses_portaudio_off_linux():
    import realtime_voice_session as rvs

    assert rvs._USE_SD_PLAYBACK == (not platform_compat.IS_LINUX)


def test_pcm_stream_player_barge_in_flushes_queue():
    from audio_output import PcmStreamPlayer

    p = PcmStreamPlayer(sample_rate=24000, channels=1)
    started = p.start()
    if not started:
        pytest.skip("No PortAudio output device on this host")
    # Queue several frames then barge-in; queue must be drained and inactive.
    frame = b"\x00\x00" * 240
    for _ in range(20):
        p.write(frame)
    p.stop()
    assert p._active is False
    assert p._queue.qsize() == 0
    # write() after stop must be a no-op (no exception, nothing queued).
    p.write(frame)
    assert p._queue.qsize() == 0
    p.close()


# --- native TTS -----------------------------------------------------------

def test_tts_windows_availability_matches_platform():
    import tts_windows

    if platform_compat.IS_WINDOWS or platform_compat.IS_MACOS:
        assert tts_windows.is_available() is True
    else:
        assert tts_windows.is_available() is False


def test_tts_windows_empty_text_returns_false():
    import tts_windows

    assert tts_windows.speak("") is False
    assert tts_windows.speak("   ") is False


# --- wake-phrase matcher (no audio) --------------------------------------

def test_wake_phrase_fuzzy_match():
    from voice_assistant import VoiceCommandInterpreter

    interp = VoiceCommandInterpreter(
        wake_phrase="hey pepper",
        start_commands=["start recording"],
    )
    assert interp.heard_wake_phrase("hey pepper") is True
    # Small-model slips should still wake (fuzzy >= 0.77).
    assert interp.heard_wake_phrase("hey peppr") is True
    # Unrelated speech must not wake.
    assert interp.heard_wake_phrase("what is the weather today") is False


# --- offline ASR pipeline runs on Windows --------------------------------

def _vosk_model_dir() -> Path | None:
    import config

    base = config.resolve_device_config_dir() / "voice"
    name = "vosk-model-small-en-us-0.15"
    d = base / name
    return d if d.is_dir() else None


def test_vosk_recognizer_runs_over_synthetic_audio():
    model_dir = _vosk_model_dir()
    if model_dir is None:
        pytest.skip("Vosk model not downloaded on this host")
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except Exception:
        pytest.skip("vosk not importable")

    SetLogLevel(-1)
    model = Model(str(model_dir))
    rec = KaldiRecognizer(model, 16000)
    # 0.5 s of silence at 16 kHz, PCM16 mono.
    silence = struct.pack("<" + "h" * 8000, *([0] * 8000))
    rec.AcceptWaveform(silence)
    result = json.loads(rec.FinalResult())
    # Silence yields an empty transcript, but the pipeline must run cleanly.
    assert "text" in result
    assert result["text"].strip() == ""
