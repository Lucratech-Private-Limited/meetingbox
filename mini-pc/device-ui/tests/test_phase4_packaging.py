"""Phase 4 evaluations: packaging plumbing.

These do NOT require a built dist (they exercise the source-level logic the
packaged build depends on). A separate test verifies the dist payload when it
is present.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_WIN = REPO_ROOT / "packaging" / "windows"


def test_spec_and_env_template_exist():
    assert (PKG_WIN / "MeetingBox.spec").is_file()
    env_tmpl = PKG_WIN / "device-ui.env"
    assert env_tmpl.is_file()
    text = env_tmpl.read_text(encoding="utf-8")
    # Must seed the desktop-critical knobs.
    for key in ("BACKEND_URL", "DISPLAY_WIDTH", "DISPLAY_HEIGHT", "MEETINGBOX_SPAWN_AUDIO"):
        assert key in text, f"{key} missing from device-ui.env template"


def test_inno_script_exists():
    assert (PKG_WIN / "MeetingBox.iss").is_file()


def test_env_file_loader_setdefault_semantics(tmp_path, monkeypatch):
    env_file = importlib.import_module("env_file")
    f = tmp_path / "device-ui.env"
    f.write_text(
        "# comment\n"
        "BACKEND_URL=https://example.test\n"
        'QUOTED="hello world"\n'
        "export EXPORTED=42\n"
        "PRESET=from_file\n",
        encoding="utf-8",
    )
    # A value already in the environment must NOT be overwritten.
    monkeypatch.setenv("PRESET", "from_env")
    for k in ("BACKEND_URL", "QUOTED", "EXPORTED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MEETINGBOX_ENV_FILE", str(f))

    loaded = env_file.load_env_file()
    assert loaded == str(f)
    assert os.environ["BACKEND_URL"] == "https://example.test"
    assert os.environ["QUOTED"] == "hello world"      # quotes stripped
    assert os.environ["EXPORTED"] == "42"             # export prefix handled
    assert os.environ["PRESET"] == "from_env"          # real env wins


def test_env_file_loader_missing_is_noop(monkeypatch, tmp_path):
    env_file = importlib.import_module("env_file")
    monkeypatch.setenv("MEETINGBOX_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    # PROGRAMDATA candidate may exist on a dev box; just assert no exception.
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    assert env_file.load_env_file() is None


def test_supervisor_frozen_audio_exe_none_when_not_frozen(monkeypatch):
    sup = importlib.import_module("audio_supervisor")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert sup._resolve_frozen_audio_exe() is None


def test_audio_config_resolver_prefers_env(monkeypatch, tmp_path):
    pytest.importorskip("yaml")
    try:
        ac = importlib.import_module("audio_capture")
    except Exception as e:  # pyaudio etc. may be unavailable in this venv
        pytest.skip(f"audio_capture import unavailable: {e}")
    cfg = tmp_path / "myconfig.yaml"
    cfg.write_text("audio: {}\n", encoding="utf-8")
    monkeypatch.setenv("MEETINGBOX_AUDIO_CONFIG", str(cfg))
    resolved = ac.AudioCaptureService._resolve_config_path("config.yaml")
    assert resolved == str(cfg)


@pytest.mark.skipif(
    not (REPO_ROOT / "packaging" / "windows" / "dist" / "MeetingBox" / "MeetingBox.exe").exists(),
    reason="packaged dist not built",
)
def test_built_dist_has_both_exes_and_payload():
    dist = REPO_ROOT / "packaging" / "windows" / "dist" / "MeetingBox"
    assert (dist / "MeetingBox.exe").is_file()
    assert (dist / "meetingbox-audio.exe").is_file()
    internal = dist / "_internal"
    assert (internal / "device-ui.env").is_file()
    assert (internal / "config.yaml").is_file()
    assert (internal / "SDL2.dll").is_file()
    assert (internal / "assets").is_dir()
    # PortAudio backend for sounddevice playback/capture.
    pa = internal / "_sounddevice_data" / "portaudio-binaries"
    assert pa.is_dir() and any(pa.glob("libportaudio*"))
