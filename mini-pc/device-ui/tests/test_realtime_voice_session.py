"""Realtime WebSocket helpers and sync tool invoke."""

import json
import sys
import types
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Kivy is optional in CI; stub a minimal clock API ONLY when the real Kivy
# package is not importable. Installing a bare ``kivy`` stub unconditionally
# would shadow the real package for other tests in the same session (e.g.
# ``from kivy.uix...``), so we guard on real availability.
try:
    import kivy  # noqa: F401  (real package present -> use it)
    import kivy.clock  # noqa: F401
except Exception:
    _mod_kivy = types.ModuleType("kivy")
    _mod_clock = types.ModuleType("kivy.clock")

    class _Clock:
        @staticmethod
        def schedule_once(fn, dt=0):
            return None

    _mod_clock.Clock = _Clock
    sys.modules.setdefault("kivy", _mod_kivy)
    sys.modules.setdefault("kivy.clock", _mod_clock)

from realtime_voice_session import (  # noqa: E402
    _APPEND_CHUNK_MS,
    _DEFAULT_INPUT_TRANSCRIPTION_MODEL,
    _INPUT_TRANSCRIPTION_PROMPT,
    _MIC_QUEUE_POLL_S,
    RealtimeVoiceSession,
    build_realtime_websocket_url,
    resample_pcm16_mono,
)


def test_build_realtime_websocket_url_encodes_model():
    u = build_realtime_websocket_url("gpt-realtime-2")
    assert u.startswith("wss://api.openai.com/v1/realtime?model=")
    assert "gpt-realtime-2" in u


def test_build_realtime_websocket_url_defaults_when_blank():
    u = build_realtime_websocket_url("")
    assert "gpt-realtime-2" in u


def test_resample_pcm16_mono_noop_at_same_rate():
    samples = (np.ones(100, dtype=np.int16) * 1000).tobytes()
    out = resample_pcm16_mono(samples, 24000, 24000)
    assert out == samples


def test_resample_pcm16_mono_changes_rate():
    x = np.linspace(-1, 1, num=240, dtype=np.float32)
    pcm = (x * 30000).astype(np.int16).tobytes()
    out48000 = resample_pcm16_mono(pcm, 24000, 48000)
    assert len(out48000) > len(pcm)


def test_invoke_realtime_tool_sync_uses_httpx(monkeypatch):
    import api_client

    post_resp = mock.MagicMock()
    post_resp.raise_for_status = mock.MagicMock()
    post_resp.json.return_value = {"output": '{"snip":"ok"}'}

    ctx = mock.MagicMock()
    ctx.__enter__.return_value.post.return_value = post_resp
    ctx.__exit__.return_value = None
    monkeypatch.setattr(api_client.httpx, "Client", lambda **kwargs: ctx)

    out = api_client.invoke_realtime_tool_sync(
        "http://127.0.0.1:8000",
        "mbd_test",
        call_id="call_1",
        name="memory_search",
        arguments='{"query":"x"}',
    )
    assert out == '{"snip":"ok"}'
    assert ctx.__enter__.return_value.post.called


def test_resolve_sounddevice_capture_prefers_usb_then_builtin_then_first(monkeypatch):
    import mic_input_resolve as mir

    class _SD:
        @staticmethod
        def query_devices():
            return [
                {"name": "Internal Mic Array", "max_input_channels": 2},
                {"name": "USB PnP Sound Device", "max_input_channels": 1},
                {"name": "HDMI Output", "max_input_channels": 0},
            ]

    assert mir.resolve_sounddevice_capture_device_index(_SD) == 1

    monkeypatch.setenv("MEETINGBOX_USB_MIC_STRICT", "0")

    class _SDNoUsb:
        @staticmethod
        def query_devices():
            return [
                {"name": "Built-in Audio Analog Stereo", "max_input_channels": 2},
                {"name": "Another Capture", "max_input_channels": 1},
            ]

    assert mir.resolve_sounddevice_capture_device_index(_SDNoUsb) == 0

    class _SDNoHints:
        @staticmethod
        def query_devices():
            return [
                {"name": "Mic Device A", "max_input_channels": 1},
                {"name": "Mic Device B", "max_input_channels": 1},
            ]

    assert mir.resolve_sounddevice_capture_device_index(_SDNoHints) == 0


def test_capture_device_fallback_candidates_include_default_and_none():
    import mic_input_resolve as mir

    class _SD:
        default = type("D", (), {"device": [2, 0]})

        @staticmethod
        def query_devices():
            return [
                {"name": "A", "max_input_channels": 1},
                {"name": "B", "max_input_channels": 1},
                {"name": "C", "max_input_channels": 1},
            ]

    out = mir.capture_device_fallback_candidates(_SD, preferred=1)
    assert out[0] == 1
    assert 2 in out
    assert None in out


def test_realtime_latency_tuning_constants():
    # 20 ms avoids PortAudio input overflow on the appliance while staying
    # comfortably below perceptible turn-latency boundaries.
    assert _APPEND_CHUNK_MS <= 20
    assert _MIC_QUEUE_POLL_S <= 0.01


def test_realtime_transcription_defaults_are_accuracy_first():
    assert _DEFAULT_INPUT_TRANSCRIPTION_MODEL == "gpt-4o-transcribe"
    assert _INPUT_TRANSCRIPTION_PROMPT == ""


def test_local_barge_in_uses_reference_and_consecutive_frames(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    session._response_in_progress = True

    ref = (np.ones(480, dtype=np.int16) * 200).tobytes()
    quiet = (np.ones(480, dtype=np.int16) * 250).tobytes()
    speech = (np.ones(480, dtype=np.int16) * 4000).tobytes()
    session._aec_far_buf.extend(ref)

    detected, *_ = session._detect_local_barge_in(quiet, now=10.0)
    assert detected is False

    detected, *_ = session._detect_local_barge_in(speech, now=10.02)
    assert detected is False
    detected, mic_rms, ref_rms, threshold, echo_similarity = session._detect_local_barge_in(speech, now=10.04)
    assert detected is True
    assert mic_rms > threshold
    assert ref_rms > 0
    assert echo_similarity <= 1.0


def test_local_barge_in_triggers_on_echo_divergence_without_rms_spike(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_LOCAL_BARGE_IN_ECHO_DIVERGENCE_ENABLED", True)
    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    session._response_in_progress = True

    t = np.linspace(0.0, 2.0 * np.pi, 480, endpoint=False, dtype=np.float32)
    ref_wave = (np.sin(t * 4.0) * 900.0).astype(np.int16)
    pure_echo = ref_wave.tobytes()
    # Use a different voice-like waveform than the far-end reference. It keeps
    # RMS below the strict spike threshold while dropping echo similarity.
    mixed = (np.sin(t * 11.0 + 0.7) * 1300.0).astype(np.int16).tobytes()
    session._aec_far_buf.extend(pure_echo)

    detected, *_ = session._detect_local_barge_in(pure_echo, now=20.0)
    assert detected is False
    detected, *_ = session._detect_local_barge_in(mixed, now=20.02)
    assert detected is False
    detected, mic_rms, ref_rms, threshold, similarity = session._detect_local_barge_in(mixed, now=20.04)
    assert detected is True
    assert mic_rms < threshold
    assert ref_rms > 0.0
    assert similarity < 0.72


def test_local_barge_in_ignores_echo_divergence_by_default(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_LOCAL_BARGE_IN_ECHO_DIVERGENCE_ENABLED", False)
    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    session._response_in_progress = True
    t = np.linspace(0.0, 2.0 * np.pi, 480, endpoint=False, dtype=np.float32)
    ref_wave = (np.sin(t * 4.0) * 900.0).astype(np.int16)
    mixed = (np.sin(t * 11.0 + 0.7) * 1300.0).astype(np.int16).tobytes()
    session._aec_far_buf.extend(ref_wave.tobytes())

    detected, *_ = session._detect_local_barge_in(mixed, now=30.0)
    assert detected is False


def test_local_barge_in_blocks_echo_like_rms_spike(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_LOCAL_BARGE_IN_ECHO_DIVERGENCE_ENABLED", False)
    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    session._response_in_progress = True
    # Simulate a stale low baseline so threshold is dominated by ref ratio.
    session._barge_in_noise_rms = 120.0

    t = np.linspace(0.0, 2.0 * np.pi, 480, endpoint=False, dtype=np.float32)
    ref_wave = (np.sin(t * 5.0) * 1000.0).astype(np.int16)
    echo_spike = (ref_wave.astype(np.float32) * 1.85).astype(np.int16).tobytes()
    session._aec_far_buf.extend(ref_wave.tobytes())

    # Similarity is near-echo and mic/ref ratio is modest; should be vetoed.
    detected, mic_rms, ref_rms, threshold, similarity = session._detect_local_barge_in(
        echo_spike,
        now=40.0,
    )
    assert mic_rms > threshold
    assert ref_rms > 0.0
    assert similarity > 0.9
    assert detected is False


def test_far_ref_slice_uses_most_recent_audio(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    old = (np.ones(120, dtype=np.int16) * 100).tobytes()
    new = (np.ones(120, dtype=np.int16) * 2000).tobytes()
    session._aec_far_buf.extend(old + new)

    ref = session._far_ref_slice(len(new))
    ref_rms = session._pcm_rms(ref)
    assert ref_rms > 1500


def test_aec3_gate_holds_far_tail_before_reopening(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)

    now = {"t": 10.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )

    near_quiet = (np.ones(480, dtype=np.int16) * 10).tobytes()
    far_loud = (np.ones(480, dtype=np.int16) * 400).tobytes()
    far_tail = (np.ones(480, dtype=np.int16) * 20).tobytes()

    # Assistant audio is active -> quiet frame is suppressed.
    assert session._aec3_gate_should_send(near_quiet, far_loud) is False

    # Far-end dipped below threshold, but still inside far-active hangover.
    now["t"] = 10.2
    assert session._aec3_gate_should_send(near_quiet, far_tail) is False

    # After hangover expires, gate reopens immediately.
    now["t"] = 10.7
    assert session._aec3_gate_should_send(near_quiet, far_tail) is True


def test_aec3_gate_uses_playback_clock_when_loopback_is_delayed(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)

    now = {"t": 30.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    # Simulate assistant audio already queued locally while loopback RMS is
    # still near-silent at playback onset.
    session._assistant_audio_play_until = 30.5
    session._state = "speaking"
    near_quiet = (np.ones(480, dtype=np.int16) * 12).tobytes()
    far_silent = (np.zeros(480, dtype=np.int16)).tobytes()
    assert session._aec3_gate_should_send(near_quiet, far_silent) is False


def test_aec3_gate_requires_consecutive_frames_before_opening(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_CONSEC_FRAMES", 3)

    now = {"t": 40.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )

    far_loud = (np.ones(480, dtype=np.int16) * 400).tobytes()
    near_loud = (np.ones(480, dtype=np.int16) * 900).tobytes()
    near_quiet = (np.ones(480, dtype=np.int16) * 10).tobytes()

    # A transient echo spike (single loud frame) must NOT open the gate nor
    # record speech evidence.
    assert session._aec3_gate_should_send(near_loud, far_loud) is False
    assert session._aec3_recent_speech_evidence_until == 0.0
    # Second consecutive loud frame is still held.
    assert session._aec3_gate_should_send(near_loud, far_loud) is False
    assert session._aec3_recent_speech_evidence_until == 0.0
    # A quiet residual frame breaks the run before it can confirm.
    assert session._aec3_gate_should_send(near_quiet, far_loud) is False
    assert session._aec3_gate_consecutive == 0

    # Sustained double-talk (N consecutive loud frames) opens the gate and
    # records local speech evidence for the barge-in path.
    assert session._aec3_gate_should_send(near_loud, far_loud) is False
    assert session._aec3_gate_should_send(near_loud, far_loud) is False
    assert session._aec3_gate_should_send(near_loud, far_loud) is True
    assert session._aec3_recent_speech_evidence_until > now["t"]


def test_abort_aplay_releases_aec3_gate_cooldown(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    # Simulate an armed echo-suppression cooldown mid-assistant-speech.
    session._aec3_far_active_until = 9_999_999.0
    session._aec3_gate_open_until = 9_999_999.0
    session._aec3_gate_consecutive = 2
    session._aec3_residual_floor = 800.0

    # A barge-in abort must release the gate so the user's utterance streams
    # cleanly instead of being suppressed by the lingering cooldown.
    session._abort_aplay()

    assert session._aec3_far_active_until == 0.0
    assert session._aec3_gate_open_until == 0.0
    assert session._aec3_gate_consecutive == 0
    assert session._aec3_residual_floor == 0.0


def _make_os_dsp_session():
    return RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )


def test_os_dsp_gate_open_when_assistant_silent(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_ENABLED", True)
    now = {"t": 40.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_os_dsp_session()
    # Assistant not playing -> even a near-silent frame streams (normal turns
    # are never gated).
    session._assistant_audio_play_until = 0.0
    session._state = "listening"
    near_quiet = (np.ones(480, dtype=np.int16) * 10).tobytes()
    assert session._os_dsp_gate_should_send(near_quiet) is True


def test_os_dsp_gate_suppresses_residual_echo_during_playback(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_MIN_RMS", 300.0)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_FLOOR_RATIO", 3.0)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_CONSEC_FRAMES", 2)
    now = {"t": 40.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_os_dsp_session()
    # Assistant actively playing (render clock has 5 s remaining).
    session._assistant_audio_play_until = 45.0
    session._state = "speaking"
    # Low-level residual echo of the assistant's own voice: below the speech
    # floor, so it must be withheld from the server (no phantom turn).
    near_residual = (np.ones(480, dtype=np.int16) * 120).tobytes()
    assert session._os_dsp_gate_should_send(near_residual) is False
    assert session._os_dsp_gate_should_send(near_residual) is False
    assert session._os_dsp_gate_should_send(near_residual) is False


def test_os_dsp_gate_opens_on_sustained_speech_during_playback(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_MIN_RMS", 300.0)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_FLOOR_RATIO", 3.0)
    monkeypatch.setattr(rtv, "_OS_DSP_GATE_CONSEC_FRAMES", 2)
    now = {"t": 40.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_os_dsp_session()
    session._assistant_audio_play_until = 45.0
    session._state = "speaking"
    near_loud = (np.ones(480, dtype=np.int16) * 2000).tobytes()
    # First loud frame is held (building the consecutive-frame run).
    assert session._os_dsp_gate_should_send(near_loud) is False
    # Second consecutive loud frame confirms genuine double-talk -> gate opens.
    assert session._os_dsp_gate_should_send(near_loud) is True
    # Gate stays open through the hangover for the rest of the utterance.
    assert session._os_dsp_gate_should_send(near_loud) is True


def test_abort_aplay_releases_os_dsp_gate(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)

    session = _make_os_dsp_session()
    session._os_dsp_gate_active_until = 9_999_999.0
    session._os_dsp_gate_open_until = 9_999_999.0
    session._os_dsp_gate_consecutive = 2
    session._os_dsp_gate_floor = 500.0

    session._abort_aplay()

    assert session._os_dsp_gate_active_until == 0.0
    assert session._os_dsp_gate_open_until == 0.0
    assert session._os_dsp_gate_consecutive == 0
    assert session._os_dsp_gate_floor == 0.0


def test_aec3_gate_holds_playback_cooldown_after_speech_ends(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_COOLDOWN_S", 1.2)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)

    now = {"t": 100.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )

    far_silent = (np.zeros(480, dtype=np.int16)).tobytes()
    # Decaying room-resonance tail of the assistant's last word: above the VAD
    # floor but below the double-talk gate threshold. This is the "glad" leak.
    near_tail_echo = (np.ones(480, dtype=np.int16) * 300).tobytes()

    # Assistant just finished: playback clock lapsed, state no longer speaking.
    session._assistant_audio_play_until = 99.9
    session._state = "listening"
    # Prime the cooldown as if the last playback frame armed it.
    session._aec3_far_active_until = 100.0 + rtv._AEC3_GATE_COOLDOWN_S

    # Within the cooldown window the trailing echo is still suppressed.
    assert session._aec3_gate_should_send(near_tail_echo, far_silent) is False
    now["t"] = 101.0
    assert session._aec3_gate_should_send(near_tail_echo, far_silent) is False

    # After the cooldown expires the gate reopens for normal turns.
    now["t"] = 101.5
    assert session._aec3_gate_should_send(near_tail_echo, far_silent) is True


def test_aec3_gate_suppresses_onset_echo_when_reference_blind(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_CONSEC_FRAMES", 3)

    now = {"t": 50.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    # Blind guard applies to LOOPBACK references only: an active-capture
    # reference can lag/drop out while the speaker is loud, leaving AEC3 with
    # nothing to subtract.
    class _LoopbackRef:
        active_capture = True

    session._far_ref = _LoopbackRef()
    # Assistant is playing, but the loopback far-end reference has not caught up
    # (far_rms ~ 0). AEC3 is blind, so even loud near-end energy (raw onset
    # echo) must be withheld and must never record speech evidence.
    session._state = "speaking"
    far_silent = (np.zeros(480, dtype=np.int16)).tobytes()
    near_echo_loud = (np.ones(480, dtype=np.int16) * 900).tobytes()
    for _ in range(5):
        assert session._aec3_gate_should_send(near_echo_loud, far_silent) is False
    assert session._aec3_recent_speech_evidence_until == 0.0

    # Once the reference starts flowing, sustained double-talk passes again.
    far_live = (np.ones(480, dtype=np.int16) * 4000).tobytes()
    assert session._aec3_gate_should_send(near_echo_loud, far_live) is False
    assert session._aec3_gate_should_send(near_echo_loud, far_live) is False
    assert session._aec3_gate_should_send(near_echo_loud, far_live) is True
    assert session._aec3_recent_speech_evidence_until > now["t"]


def test_aec3_gate_render_fed_reference_never_treated_as_blind(monkeypatch):
    """With the render-fed reference, far silence during playback means the
    speaker is GENUINELY silent at that instant (a pause between TTS words) —
    mic energy then may be real user onset and must flow through the normal
    consecutive-frames logic instead of being dropped as unverifiable echo."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_HANGOVER_S", 0.45)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FLOOR_RATIO", 3.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_CONSEC_FRAMES", 3)

    now = {"t": 60.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )

    class _RenderRef:
        active_capture = False

    session._far_ref = _RenderRef()
    session._state = "speaking"
    far_silent = (np.zeros(480, dtype=np.int16)).tobytes()
    near_user = (np.ones(480, dtype=np.int16) * 3000).tobytes()
    # User starts talking during a TTS word gap: sustained frames open the
    # gate instead of being blind-dropped.
    assert session._aec3_gate_should_send(near_user, far_silent) is False
    assert session._aec3_gate_should_send(near_user, far_silent) is False
    assert session._aec3_gate_should_send(near_user, far_silent) is True
    assert session._aec3_recent_speech_evidence_until > now["t"]


def test_aec3_phantom_transcript_guard_drops_tiny_tail_fragments(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_PHANTOM_TRANSCRIPT_TAIL_S", 1.0)

    now = {"t": 20.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )
    session._aec3_full_duplex = True
    session._assistant_audio_play_until = 20.4

    assert session._should_drop_aec3_phantom_transcript(".") is True
    assert session._should_drop_aec3_phantom_transcript("hey") is True
    assert session._should_drop_aec3_phantom_transcript("next one") is False
    session._aec3_recent_speech_evidence_until = 20.6
    assert session._should_drop_aec3_phantom_transcript("stop") is False

    # Outside assistant playback tail, keep transcripts untouched.
    now["t"] = 22.0
    assert session._should_drop_aec3_phantom_transcript(".") is False


# ---------------------------------------------------------------------------
# Turn speech-evidence layer (transcript validation + interrupt gating)
# ---------------------------------------------------------------------------

def _make_evidence_session(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    return RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )


def _frame(level: int, samples: int = 480) -> bytes:
    return (np.ones(samples, dtype=np.int16) * level).tobytes()


def test_speech_monitor_scores_silence_and_speech():
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(min_rms=180.0, floor_ratio=2.5)
    # Ambient noise never counts as speech and adapts the floor.
    for i in range(10):
        assert mon.observe(_frame(40), now=10.0 + i * 0.02) is False
    assert mon.noise_floor > 0.0
    assert mon.recent_speech(1.5, now=10.2) is False
    # Genuine speech-level audio registers as evidence.
    assert mon.observe(_frame(2000), now=10.3) is True
    assert mon.recent_speech(1.5, now=10.4) is True
    stats = mon.stats_since(10.0)
    assert stats["speech_ms"] > 0.0
    assert stats["total_ms"] > stats["speech_ms"]


def test_speech_monitor_floor_not_poisoned_by_initial_speech():
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(min_rms=180.0, floor_ratio=2.5)
    # Session opens mid-speech: loud frames must not seed the noise floor.
    assert mon.observe(_frame(3000), now=5.0) is True
    assert mon.noise_floor == 0.0
    # Quiet ambient afterwards seeds the floor conservatively (<= min_rms).
    mon.observe(_frame(50), now=5.02)
    assert 0.0 < mon.noise_floor <= 180.0


def test_speech_monitor_apm_probability_refines_decision():
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(min_rms=300.0, floor_ratio=2.5)
    # Borderline energy + confident non-speech probability -> vetoed
    # (residual echo bursts score near zero on the APM VAD).
    assert mon.observe(_frame(400), speech_prob=0.05, now=1.0) is False
    # Modest energy + confident speech probability -> upgraded to speech.
    assert mon.observe(_frame(200), speech_prob=0.95, now=1.02) is True


def test_speech_monitor_echo_risk_blocks_residual_echo_bursts():
    """Regression: the assistant's own residual echo ("The" @ RMS ~1276)
    leaked through the OS-DSP gate and was counted as speech evidence,
    validating a phantom transcript and authorizing a false interrupt."""
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(
        min_rms=180.0, floor_ratio=2.5,
        playback_min_rms=1500.0, playback_floor_ratio=2.5,
    )
    # Playback-time residual echo bursts below the barge-in level never
    # count, no matter how far above the ambient floor they sit.
    for i in range(10):
        assert mon.observe(_frame(1276), echo_risk=True, now=50.0 + i * 0.02) is False
    assert mon.recent_speech(1.5, now=50.3) is False
    stats = mon.stats_since(50.0)
    assert stats["speech_ms"] == 0.0
    assert stats["echo_risk_ms"] > 0.0


def test_speech_monitor_echo_risk_requires_dominance_over_echo_envelope():
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(
        min_rms=180.0, floor_ratio=2.5,
        playback_min_rms=1500.0, playback_floor_ratio=2.5,
    )
    # Residual echo teaches the envelope its level (fast attack) — but ONLY
    # while the speaker is actually emitting (echo_active).
    assert mon.observe(_frame(800), echo_active=True, now=60.0) is False
    assert mon.echo_env >= 800.0
    # Above the absolute bar but NOT dominating the envelope -> still echo.
    assert mon.observe(_frame(1600), echo_active=True, now=60.02) is False
    # Genuine barge-in dominates both bars -> speech.
    assert mon.observe(_frame(4500), echo_active=True, now=60.04) is True
    assert mon.recent_speech(1.0, now=60.1) is True


def test_speech_monitor_hangover_does_not_learn_user_voice_as_echo():
    """Post-playback hangover frames must never teach the echo envelope.

    Regression: after a barge-in abort, the user's own voice (rejected by the
    strict bar) was learned into the envelope, which then rose to the user's
    level and locked them out permanently (speech_ms stayed 0 for every
    subsequent utterance)."""
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(
        min_rms=180.0, floor_ratio=2.5,
        playback_min_rms=1500.0, playback_floor_ratio=2.5,
    )
    # Hangover tail (echo_risk without echo_active): loud frames below the
    # dominance bar are rejected but must NOT raise the envelope — it only
    # decays, so the user's voice breaks through within a few frames instead
    # of ratcheting the envelope up to its own level forever.
    mon._echo_env = 2000.0
    results = [
        mon.observe(_frame(4000), echo_risk=True, now=80.0 + i * 0.02)
        for i in range(10)
    ]
    assert mon.echo_env <= 2000.0  # never attacked upward
    assert any(results)  # envelope decayed below 4000/2.5 -> speech counted


def test_speech_monitor_echo_risk_ignores_apm_probability():
    from realtime_voice_session import _UplinkSpeechMonitor

    mon = _UplinkSpeechMonitor(
        min_rms=180.0, floor_ratio=2.5,
        playback_min_rms=1500.0, playback_floor_ratio=2.5,
    )
    # Residual echo IS a voice (the assistant's), so a high spectral speech
    # probability must not upgrade a modest playback-time frame.
    assert mon.observe(_frame(400), speech_prob=0.95, echo_risk=True, now=70.0) is False


def test_aec3_is_default_engine_preference():
    """Engine order contract: software AEC3 (device-independent, the
    Chrome/ChatGPT-desktop canceller) must be preferred over the vendor-
    driver-dependent OS Voice Capture DSP unless explicitly overridden."""
    import realtime_voice_session as rtv

    assert rtv._PREFER_OS_AEC is False


def test_uplink_echo_risk_follows_loopback_ground_truth(monkeypatch):
    """The loopback far-end oracle must extend echo risk even when the
    playback-clock estimate reads zero (the '\"No\"/\"Elle\" phantom' hole:
    speaker audibly playing while the clock said silent)."""
    import realtime_voice_session as rtv

    now = {"t": 2000.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])
    session = _make_evidence_session(monkeypatch)

    # Playback clock says silent, but the loopback heard speaker output.
    session._assistant_audio_play_until = 0.0
    session._aec3_far_active_until = 2001.0
    assert session._uplink_echo_risk() is True
    now["t"] = 2001.0 + (rtv._EVIDENCE_PLAYBACK_HANGOVER_S * 0.5)
    assert session._uplink_echo_risk() is True
    now["t"] = 2001.0 + rtv._EVIDENCE_PLAYBACK_HANGOVER_S + 0.5
    assert session._uplink_echo_risk() is False


def test_uplink_echo_risk_covers_playback_and_hangover(monkeypatch):
    import realtime_voice_session as rtv

    now = {"t": 1000.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])
    session = _make_evidence_session(monkeypatch)

    # Assistant actively playing -> echo risk.
    session._assistant_audio_play_until = 1002.0
    assert session._uplink_echo_risk() is True
    # Playback clock lapsed: still inside the decay hangover.
    now["t"] = 1002.0 + (rtv._EVIDENCE_PLAYBACK_HANGOVER_S * 0.5)
    assert session._uplink_echo_risk() is True
    # Hangover expired -> back to the ambient scoring path.
    now["t"] = 1002.0 + rtv._EVIDENCE_PLAYBACK_HANGOVER_S + 0.5
    assert session._uplink_echo_risk() is False


def test_playback_echo_transcript_rejected(monkeypatch):
    """End-to-end shape of the 10:59:54 phantom: server VAD committed a turn
    on residual echo during playback; the transcript ("The") must fail
    evidence validation because no frame dominated the echo."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 700.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    session._turn_started_at = 700.0
    for i in range(50):
        mon.observe(_frame(1276), echo_risk=True, now=700.0 + i * 0.02)
    now["t"] = 701.5
    rejected, stats = session._transcript_rejected_by_evidence("The")
    assert rejected is True
    assert stats["speech_ms"] == 0.0


def test_short_fragment_needs_sustained_speech(monkeypatch):
    """A ~200 ms energy blip (cough / keyboard / echo chunk-gap leak) cannot
    validate a short transcript; a genuinely spoken word (~400 ms) can."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 800.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    session._turn_started_at = 800.0
    for i in range(10):  # 10 x 20 ms = 200 ms of speech-level audio
        mon.observe(_frame(2500), now=800.0 + i * 0.02)
    now["t"] = 801.0
    rejected, stats = session._transcript_rejected_by_evidence("Yes")
    assert rejected is True
    assert stats["speech_ms"] < rtv._EVIDENCE_SHORT_MIN_SPEECH_MS


def test_commit_gate_separates_genuine_and_phantom_turns(monkeypatch):
    """Client-authority turn-taking: at commit time, a turn with genuine
    speech evidence gets a response; a phantom (echo/noise) commit does not
    and is excised from the conversation."""
    import asyncio
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 900.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    assert session._client_turn_authority() is True

    # Genuine turn: ~600 ms of speech evidence -> commit gate passes.
    session._turn_started_at = 900.0
    session._turn_started_by_item["item_real"] = 900.0
    for i in range(30):
        mon.observe(_frame(2500), now=900.0 + i * 0.02)
    now["t"] = 901.0
    assert session._turn_has_commit_evidence(
        session._turn_evidence_stats(item_id="item_real")
    ) is True

    # Phantom turn: ambient only -> commit gate fails.
    session._turn_started_at = 902.0
    session._turn_started_by_item["item_ghost"] = 902.0
    for i in range(30):
        mon.observe(_frame(40), now=902.0 + i * 0.02)
    now["t"] = 903.0
    assert session._turn_has_commit_evidence(
        session._turn_evidence_stats(item_id="item_ghost")
    ) is False

    # Excision sends conversation.item.delete and blacklists the item so
    # its late transcription can never surface.
    sent: list[dict] = []

    class _Ws:
        async def send(self, payload):
            sent.append(json.loads(payload))

    asyncio.run(session._excise_phantom_turn(_Ws(), "item_ghost", source="test"))
    assert sent == [{"type": "conversation.item.delete", "item_id": "item_ghost"}]
    assert "item_ghost" in session._phantom_items


def test_session_update_disables_server_auto_response(monkeypatch):
    """With the evidence layer live the session must run with
    create_response and interrupt_response OFF — the server VAD is a pure
    segmenter and only the client (holding acoustic evidence) may create,
    cancel, or interrupt responses."""
    import asyncio
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    session = _make_evidence_session(monkeypatch)

    sent: list[dict] = []

    class _Ws:
        async def send(self, payload):
            sent.append(json.loads(payload))

    asyncio.run(session._send_session_update(_Ws()))
    assert len(sent) == 1
    td = sent[0]["session"]["audio"]["input"].get("turn_detection")
    if td is not None:
        assert td["create_response"] is False
        assert td["interrupt_response"] is False


def test_transcript_rejected_when_turn_had_no_speech_evidence(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 100.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    assert mon is not None
    session._turn_started_at = 100.0
    # The committed turn carried only ambient noise — the classic Whisper
    # hallucination source ("it" / "hello" / ".").
    for i in range(30):
        mon.observe(_frame(40), now=100.0 + i * 0.02)
    now["t"] = 101.0
    for phantom in ("it", "hello", ".", "thank you"):
        rejected, stats = session._transcript_rejected_by_evidence(phantom)
        assert rejected is True, phantom
        assert stats["speech_ms"] < rtv._EVIDENCE_HARD_MIN_SPEECH_MS


def test_transcript_accepted_with_genuine_speech_evidence(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 200.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    session._turn_started_at = 200.0
    # A real short answer: ~400 ms of speech-level audio.
    for i in range(20):
        mon.observe(_frame(2500), now=200.0 + i * 0.02)
    now["t"] = 201.0
    rejected, stats = session._transcript_rejected_by_evidence("yes")
    assert rejected is False
    assert stats["speech_ms"] >= rtv._EVIDENCE_SHORT_MIN_SPEECH_MS
    rejected, _ = session._transcript_rejected_by_evidence(
        "schedule a meeting with Priya tomorrow at three"
    )
    assert rejected is False


def test_transcript_evidence_window_is_anchored_per_item(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 300.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    # Turn A (item_a): real speech. Turn B (item_b): silence only.
    session._turn_started_by_item["item_a"] = 300.0
    for i in range(20):
        mon.observe(_frame(2500), now=300.0 + i * 0.02)
    session._turn_started_by_item["item_b"] = 302.0
    session._turn_started_at = 302.0
    for i in range(20):
        mon.observe(_frame(40), now=302.0 + i * 0.02)
    now["t"] = 303.0
    rejected_a, _ = session._transcript_rejected_by_evidence("yes", item_id="item_a")
    rejected_b, _ = session._transcript_rejected_by_evidence("it", item_id="item_b")
    assert rejected_a is False
    assert rejected_b is True


def test_turn_evidence_frozen_at_commit(monkeypatch):
    """A phantom micro-turn must not be validated by the user's NEXT
    utterance. Evidence is snapshotted at input_audio_buffer.committed;
    speech captured afterwards (while the ASR is still transcribing the
    committed turn) never retroactively inflates its stats."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 500.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    # Phantom turn: only a ~100 ms transient (chair scrape / mic bump).
    session._turn_started_by_item["item_ghost"] = 500.0
    session._turn_started_at = 500.0
    for i in range(5):
        mon.observe(_frame(2500), now=500.0 + i * 0.02)
    # Commit freezes the evidence (mirrors the committed-handler bookkeeping).
    now["t"] = 500.6
    session._turn_commit_stats["item_ghost"] = dict(
        session._turn_evidence_stats(item_id="item_ghost")
    )
    session._last_turn_commit_at = 500.6
    # The user begins their next real utterance while the ghost turn's
    # transcription is still in flight.
    for i in range(40):
        mon.observe(_frame(2500), now=500.7 + i * 0.02)
    now["t"] = 501.6
    rejected, stats = session._transcript_rejected_by_evidence(
        "Oops", item_id="item_ghost"
    )
    assert rejected is True
    assert stats["speech_ms"] < rtv._EVIDENCE_SHORT_MIN_SPEECH_MS


def test_turn_evidence_lookback_clamped_at_previous_commit(monkeypatch):
    """The lookback padding (covers VAD prefix + latency) must never reach
    past the previous turn's commit — otherwise the tail of utterance N
    validates a phantom split off as turn N+1."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 600.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    mon = session._speech_monitor
    # Turn N: 1.4 s of genuine speech, committed at 601.5.
    session._turn_started_by_item["item_n"] = 600.0
    for i in range(70):
        mon.observe(_frame(2500), now=600.0 + i * 0.02)
    now["t"] = 601.5
    session._last_turn_commit_at = 601.5
    # Turn N+1 fires on residual noise only, 0.3 s after the commit. Its
    # 1 s lookback would overlap turn N's speech without the clamp.
    session._turn_started_by_item["item_ghost"] = 601.8
    session._turn_started_at = 601.8
    for i in range(10):
        mon.observe(_frame(40), now=601.8 + i * 0.02)
    now["t"] = 602.5
    rejected, stats = session._transcript_rejected_by_evidence(
        "sure", item_id="item_ghost"
    )
    assert rejected is True
    assert stats["speech_ms"] == 0.0


def test_interrupt_requires_local_speech_evidence_during_playback(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 400.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    # Assistant is audibly playing.
    session._assistant_audio_play_until = 405.0
    session._state = "speaking"
    # No recent uplink speech -> a server speech_started is an echo artifact
    # and must NOT stop playback.
    assert session._has_interrupt_speech_evidence(400.0) is False
    # Locally-verified speech on the uplink -> genuine barge-in, interrupt.
    session._speech_monitor.observe(_frame(2500), now=399.5)
    assert session._has_interrupt_speech_evidence(400.0) is True


def test_interrupt_evidence_not_required_when_assistant_silent(monkeypatch):
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 500.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    session._assistant_audio_play_until = 0.0
    session._state = "listening"
    # Nothing is playing — there is nothing to protect; never block the turn.
    assert session._has_interrupt_speech_evidence(500.0) is True


def test_interrupt_evidence_monitor_overrides_aec3_gate(monkeypatch):
    """The evidence monitor is the sole interrupt authority when live.

    Regression: the AEC3 residual gate can open on echo bursts during
    loopback drift (measured: near RMS ~860 vs the 1500 barge-in bar).
    Accepting the gate's opening as interrupt evidence aborted genuine
    assistant responses mid-sentence. The monitor already scores every
    frame the gate passes, so the gate must not override its verdict."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 600.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    session._assistant_audio_play_until = 605.0
    session._state = "speaking"
    session._aec3_full_duplex = True
    # AEC3's gate opened, but the monitor saw no genuine speech -> no interrupt.
    session._aec3_recent_speech_evidence_until = 600.5
    assert session._has_interrupt_speech_evidence(600.0) is False
    # Monitor-confirmed speech -> genuine barge-in.
    session._speech_monitor.observe(
        _frame(4500), echo_risk=True, echo_active=True, now=599.8
    )
    assert session._has_interrupt_speech_evidence(600.0) is True


def test_abort_aplay_collapses_echo_risk_window(monkeypatch):
    """Aborting playback must collapse the echo-risk window to a short tail.

    Regression: the window was anchored to the pre-abort play-until clock
    (tens of seconds ahead for long answers), so after a barge-in every
    subsequent user utterance was scored against the strict playback bar
    and deleted as a phantom ('Can you hear me?' lockout)."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    now = {"t": 700.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = _make_evidence_session(monkeypatch)
    session._assistant_audio_play_until = 740.0  # 40s of queued speech
    session._state = "speaking"
    assert session._uplink_echo_risk(700.0) is True  # window now ratcheted far out
    session._abort_aplay()
    session._state = "listening"
    assert (
        session._playback_echo_risk_until
        <= 700.0 + rtv._EVIDENCE_PLAYBACK_HANGOVER_S
    )
    # Past the short tail, the user's next utterance faces the ambient bar.
    now["t"] = 702.0
    assert session._uplink_echo_risk(702.0) is False
    assert session._uplink_echo_active(702.0) is False


def test_render_feed_reference_is_default_on_desktop(monkeypatch):
    """Windows/macOS default to the render-fed far reference (the exact PCM
    our playback callback hands the device), not the WASAPI loopback capture.

    Regression: the loopback reference intermittently went blind (far_rms 0.0
    while the speaker was loud) because consumer-side underruns inserted
    zeros that shifted the reference timeline; AEC3 then passed raw echo,
    producing accepted phantom transcripts ('cute', 'I am' -> end_session)."""
    import aec_reference

    monkeypatch.delenv("REALTIME_AEC_REFERENCE", raising=False)
    monkeypatch.setattr(aec_reference, "_IS_WIN", True)
    monkeypatch.setattr(aec_reference, "_IS_MAC", False)
    ref = aec_reference.create_reference(rate=48000)
    assert ref is not None
    assert ref.active_capture is False
    assert isinstance(ref, aec_reference.AppPlaybackReference)


def test_app_playback_reference_prime_feed_clear():
    from aec_reference import AppPlaybackReference

    ref = AppPlaybackReference(rate=48000, feed_rate=24000)
    assert ref.start() is True
    # Prime = silence cushion at the output rate.
    ref.prime(80.0)
    cushion = ref.read(48000 * 2)  # up to 1 s
    assert len(cushion) == int(48000 * 2 * 0.08)
    assert max(np.frombuffer(cushion, dtype=np.int16), default=0) == 0
    # Fed 24 kHz playback PCM comes out resampled to 48 kHz (2x bytes).
    tone24 = (np.ones(240, dtype=np.int16) * 2000).tobytes()  # 10 ms @ 24 kHz
    ref.feed_playback(tone24)
    out = ref.read(48000 * 2)
    assert len(out) == len(tone24) * 2
    assert np.abs(np.frombuffer(out, dtype=np.int16)).max() >= 1500
    # clear() drops anything buffered (barge-in abort).
    ref.feed_playback(tone24)
    ref.clear()
    assert ref.read(48000 * 2) == b""


def test_app_playback_reference_ride_height_control():
    """Device-paced mode: an underrun returns exact-length silence-padded
    PCM, flags the reference starved, and restores the cushion; chronic
    overrun snaps occupancy back to the cushion. Both keep the far/near
    misalignment bounded so AEC3 can always re-converge."""
    from aec_reference import AppPlaybackReference

    ref = AppPlaybackReference(rate=48000, feed_rate=48000)
    ref.device_paced = True
    # Ring holds 10 ms but the consumer asks for 20 ms -> underrun.
    ref.feed_playback((np.ones(480, dtype=np.int16) * 2000).tobytes())
    out = ref.read(48000 * 2 // 50)  # 20 ms @ 48 kHz
    assert len(out) == 48000 * 2 // 50  # exact-length, zero-padded
    assert ref.starved_recently is True
    assert ref.underruns == 1
    # Cushion restored: the next cushion-sized read succeeds fully.
    cushion_bytes = ref._ms_to_bytes(ref.CUSHION_MS)
    assert len(ref.read(cushion_bytes)) == cushion_bytes
    # Overrun: occupancy far beyond high water is trimmed back to cushion.
    ref.feed_playback(b"\x00" * ref._ms_to_bytes(560.0))
    ref.read(2)
    assert ref._ring.occupancy() <= ref._ms_to_bytes(ref.CUSHION_MS)
    assert ref.overruns == 1


def test_app_playback_reference_fallback_feed_keeps_old_semantics():
    """Without device pacing (aplay fallback feed at websocket pace) the ring
    must keep its original behavior: short reads stay short, no starvation
    flag, no occupancy trimming — occupancy legitimately swings with the
    queued response there."""
    from aec_reference import AppPlaybackReference

    ref = AppPlaybackReference(rate=48000, feed_rate=48000)
    ref.feed_playback((np.ones(480, dtype=np.int16) * 2000).tobytes())
    out = ref.read(48000 * 2 // 50)
    assert len(out) == 480 * 2  # short read, not padded
    assert ref.starved_recently is False
    assert ref.underruns == 0


def test_aec3_gate_blind_drops_when_render_reference_starved(monkeypatch):
    """A starved render-fed reference is blind — far silence no longer means
    the speaker is quiet. During playback such frames are uncancellable and
    must be withheld, exactly like a lagging loopback capture."""
    import realtime_voice_session as rtv

    monkeypatch.setattr(rtv, "sd", None)
    monkeypatch.setattr(rtv, "_AEC3_RESIDUAL_GATE_ENABLED", True)
    monkeypatch.setattr(rtv, "_AEC3_GATE_FAR_ACTIVE_RMS", 200.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_MIN_RMS", 550.0)
    monkeypatch.setattr(rtv, "_AEC3_GATE_CONSEC_FRAMES", 3)

    now = {"t": 80.0}
    monkeypatch.setattr(rtv.time, "monotonic", lambda: now["t"])

    session = RealtimeVoiceSession(
        client_secret="ek_test",
        model="gpt-realtime-2",
        backend_base_url="http://127.0.0.1:8000",
        device_token="mbd_test",
        on_session_end=lambda: None,
        on_error=lambda _msg: None,
        on_connected=lambda: None,
    )

    class _StarvedRenderRef:
        active_capture = False
        starved_recently = True

    session._far_ref = _StarvedRenderRef()
    session._state = "speaking"
    far_silent = (np.zeros(480, dtype=np.int16)).tobytes()
    near_loud = (np.ones(480, dtype=np.int16) * 3000).tobytes()
    # Uncancellable playback-time energy: dropped, never opens the gate.
    for _ in range(5):
        assert session._aec3_gate_should_send(near_loud, far_silent) is False
    assert session._aec3_gate_open_until == 0.0
    # Reference recovers -> normal double-talk logic resumes.
    session._far_ref.starved_recently = False
    assert session._aec3_gate_should_send(near_loud, far_silent) is False
    assert session._aec3_gate_should_send(near_loud, far_silent) is False
    assert session._aec3_gate_should_send(near_loud, far_silent) is True


def test_abort_aplay_flushes_render_fed_reference(monkeypatch):
    """A barge-in abort discards queued playback, so the reference audio for
    that never-played tail must be flushed too or it would misalign AEC3
    against the mic on the next turn."""
    import realtime_voice_session as rtv
    from aec_reference import AppPlaybackReference

    monkeypatch.setattr(rtv, "_TURN_EVIDENCE_ENABLED", True)
    session = _make_evidence_session(monkeypatch)

    ref = AppPlaybackReference(rate=48000, feed_rate=24000)
    ref.feed_playback((np.ones(2400, dtype=np.int16) * 2000).tobytes())
    session._far_ref = ref

    class _Aec3:
        def __init__(self):
            self.cleared = False

        def clear_far(self):
            self.cleared = True

    aec3 = _Aec3()
    session._aec3 = aec3

    session._abort_aplay()
    assert ref.read(48000 * 4) == b""
    assert aec3.cleared is True


def test_pcm_stream_player_render_tap_receives_device_blocks():
    """The on_pcm tap must receive exactly what the device callback renders:
    buffered audio when present and zero-fill silence when the queue is dry."""
    from audio_output import PcmStreamPlayer

    fed: list[bytes] = []
    player = PcmStreamPlayer(sample_rate=24000, channels=1, on_pcm=fed.append)
    player._active = True  # bypass start(): exercise the callback directly

    tone = (np.ones(240, dtype=np.int16) * 1500).tobytes()
    player.write(tone)
    out = np.zeros((240, 1), dtype=np.int16)
    player._callback(out, 240, None, None)
    assert len(fed) == 1
    assert np.abs(np.frombuffer(fed[0], dtype=np.int16)).max() == 1500

    # Queue dry -> the device renders silence, and the tap hears silence too.
    player._callback(out, 240, None, None)
    assert len(fed) == 2
    assert np.abs(np.frombuffer(fed[1], dtype=np.int16)).max() == 0


def test_default_desktop_echo_engines_off_matches_exe(monkeypatch):
    """Regression guard for the "mic goes deaf" fix.

    The shipping EXE captured the mic with a plain PortAudio input stream +
    Speex AEC + local barge-in. Two later engines regressed responsiveness on
    coupled laptop mics: WebRTC AEC3 (raised the barge-in bar) and the Windows
    Voice Capture DSP (source-mode capture that stalls and leaves the mic deaf).
    Both must be OFF by default so a stock desktop launch lands back on the
    reliable EXE path; they stay opt-in behind their env flags.
    """
    import importlib

    import realtime_voice_session as rtv

    for var in ("REALTIME_OS_AEC", "REALTIME_WEBRTC_AEC", "REALTIME_PREFER_OS_AEC"):
        monkeypatch.delenv(var, raising=False)
    try:
        reloaded = importlib.reload(rtv)
        assert reloaded._OS_AEC_ENABLED is False
        assert reloaded._WEBRTC_AEC_ENABLED is False
        assert reloaded._PREFER_OS_AEC is False
        # Opt-in still works: setting the flag re-enables the engine.
        monkeypatch.setenv("REALTIME_OS_AEC", "1")
        reloaded = importlib.reload(rtv)
        assert reloaded._OS_AEC_ENABLED is True
    finally:
        # Restore the module to the ambient (unset) environment for later tests.
        for var in ("REALTIME_OS_AEC", "REALTIME_WEBRTC_AEC", "REALTIME_PREFER_OS_AEC"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(rtv)


def test_default_desktop_turn_taking_is_server_driven_like_exe(monkeypatch):
    """Regression guard for the "slow / stops responding / closes early" fix.

    The shipping EXE ran the server-driven turn model: semantic_vad with
    create_response AND interrupt_response both true, so the server detected
    end-of-turn and generated the reply automatically. The client-authority
    evidence layer added afterwards (a manual per-turn response.create + phantom
    excision) regressed responsiveness — a stalled response.create left the
    model silent until the server closed the session on a silence timeout. On
    desktop the evidence layer must default OFF so a stock launch lands back on
    the EXE's server-driven path; it stays opt-in behind REALTIME_TURN_EVIDENCE.
    (This test runs on desktop, where IS_DESKTOP is True.)
    """
    import importlib

    import realtime_voice_session as rtv

    if not rtv.IS_DESKTOP:
        import pytest

        pytest.skip("desktop-only default")

    for var in ("REALTIME_TURN_EVIDENCE", "REALTIME_TURN_DETECTION"):
        monkeypatch.delenv(var, raising=False)
    try:
        reloaded = importlib.reload(rtv)
        # Evidence layer (client authority) OFF -> server owns turn-taking, so
        # create_response/interrupt_response are both true (the EXE config).
        assert reloaded._TURN_EVIDENCE_ENABLED is False
        # "auto" turn detection resolves to semantic_vad on desktop (EXE model).
        assert reloaded._REALTIME_TURN_DETECTION == "auto"
        # Opt back in still works.
        monkeypatch.setenv("REALTIME_TURN_EVIDENCE", "1")
        reloaded = importlib.reload(rtv)
        assert reloaded._TURN_EVIDENCE_ENABLED is True
    finally:
        for var in ("REALTIME_TURN_EVIDENCE", "REALTIME_TURN_DETECTION"):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(rtv)
