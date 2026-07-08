"""Phase 1 evals: cross-platform foundation.

Covers:
* platform_compat data/log path resolution
* config.resolve_device_config_dir() writability on the host OS
* audio_output helper contract (graceful no-ops, env device resolution)
* device resolvers short-circuit cleanly without Linux audio tooling
* a module-import smoke test that fails if any Linux-only import sneaks in
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

import platform_compat


def test_platform_flags_are_mutually_exclusive():
    flags = [platform_compat.IS_WINDOWS, platform_compat.IS_MACOS, platform_compat.IS_LINUX]
    assert sum(1 for f in flags if f) == 1


def test_app_user_data_dir_per_os():
    d = platform_compat.app_user_data_dir()
    if platform_compat.IS_LINUX:
        assert d is None
    else:
        assert d is not None
        assert d.name == "MeetingBox"


def test_default_config_dir_under_data_dir():
    cfg = platform_compat.default_config_dir()
    if platform_compat.IS_LINUX:
        assert cfg is None
    else:
        assert cfg is not None
        assert cfg.parts[-2:] == ("data", "config")


def test_default_log_file_is_os_appropriate():
    log = platform_compat.default_log_file()
    if platform_compat.IS_WINDOWS:
        assert log.lower().endswith("meetingbox-ui.log")
        assert ":" in log  # has a drive letter / absolute path
    else:
        assert log == "/tmp/meetingbox-ui.log"


def test_has_linux_audio_tools_matches_platform():
    assert platform_compat.has_linux_audio_tools() == platform_compat.IS_LINUX


def test_resolve_device_config_dir_is_writable():
    import config

    d = config.resolve_device_config_dir()
    assert d.is_dir()
    assert os.access(d, os.W_OK)
    if platform_compat.IS_WINDOWS:
        assert "MeetingBox" in str(d)


def test_seed_desktop_audio_env_sets_paths():
    import config  # noqa: F401  (import triggers _seed_desktop_audio_env on desktop)

    if platform_compat.IS_WINDOWS or platform_compat.IS_MACOS:
        assert os.environ.get("AUDIO_CAPTURE_BACKEND") == "sounddevice"
        assert os.environ.get("RECORDINGS_DIR")
        assert os.environ.get("TEMP_SEGMENTS_DIR")
        assert Path(os.environ["RECORDINGS_DIR"]).is_dir()


# --- audio_output ---------------------------------------------------------

def test_audio_output_empty_input_is_false():
    import audio_output

    assert audio_output.play_pcm16(b"", sample_rate=24000) is False


def test_audio_output_device_env_resolution(monkeypatch):
    import audio_output

    monkeypatch.setenv("MEETINGBOX_OUTPUT_DEVICE_INDEX", "7")
    assert audio_output._resolve_output_device(None) == 7
    monkeypatch.delenv("MEETINGBOX_OUTPUT_DEVICE_INDEX", raising=False)
    assert audio_output._resolve_output_device(None) is None
    assert audio_output._resolve_output_device(3) == 3


def test_pcm_stream_player_constructs():
    import audio_output

    p = audio_output.PcmStreamPlayer(sample_rate=24000, channels=1)
    # stop() before start() must be a harmless no-op (no thread/stream yet).
    p.stop()
    p.close()


# --- device resolvers -----------------------------------------------------

def test_resolve_audio_pair_clean_on_non_linux():
    import audio_device_resolve

    pair = audio_device_resolve.resolve_audio_pair(sd=None)
    if not platform_compat.IS_LINUX:
        assert pair.capture is None
        assert pair.playback is None


def test_pulse_bt_source_name_noop_off_linux():
    import mic_input_resolve

    if not platform_compat.IS_LINUX:
        assert mic_input_resolve._pulse_bt_source_name() is None


def test_strict_usb_default_off_on_desktop(monkeypatch):
    import mic_input_resolve

    monkeypatch.delenv("MEETINGBOX_USB_MIC_STRICT", raising=False)
    expected = platform_compat.IS_LINUX
    assert mic_input_resolve._strict_usb_enabled() == expected


def test_mic_resolver_none_input_returns_none():
    import mic_input_resolve

    assert mic_input_resolve.resolve_sounddevice_capture_device_index(None) is None


# --- import smoke ---------------------------------------------------------

@pytest.mark.parametrize(
    "module_name",
    [
        "platform_compat",
        "config",
        "audio_output",
        "audio_device_resolve",
        "mic_input_resolve",
    ],
)
def test_core_modules_import_clean(module_name):
    mod = importlib.import_module(module_name)
    assert mod is not None
