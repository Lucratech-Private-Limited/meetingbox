"""
Tests for external mic+speaker device priority.

Verifies that resolve_audio_pair() and resolve_sounddevice_capture_device_index()
correctly prefer external devices in this order:

  1. Bluetooth combined mic+speaker  (via PulseAudio/PipeWire)
  2. USB combined mic+speaker        (via ALSA card list)
  3. Bluetooth mic-only              (via PulseAudio)
  4. USB mic-only                    (via ALSA / PortAudio)
  5. Built-in / system default       (only when no external device is present)

Bluetooth devices managed by PipeWire/PulseAudio do NOT appear in
`arecord -l` / `aplay -l`; they only appear as pactl sources/sinks.
That's why the resolver runs a PulseAudio query first.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import audio_device_resolve as adr  # noqa: E402
import mic_input_resolve as mir  # noqa: E402

# These tests assert the Linux ALSA/PulseAudio device-priority logic. On the
# Windows/macOS desktop port that logic is intentionally short-circuited (the
# port uses the PortAudio default device), so the suite only applies on Linux.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux-only ALSA/PulseAudio device-priority resolution (bypassed on desktop port)",
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FakeSounddevice:
    """Minimal stand-in for the `sounddevice` module used by the resolver."""

    def __init__(self, devices: list[dict]):
        self._devices = devices

        class _Default:
            device = (-1, -1)

        self.default = _Default()

    def query_devices(self):
        return list(self._devices)


# Realistic `arecord -L` / `aplay -L` output on a container with
# pipewire-alsa + libasound2-plugins installed.  The resolver probes this
# to pick the first PCM that actually exists in this environment.
ALSA_PCM_LIST_WITH_PIPEWIRE = (
    "null\n"
    "    Discard all samples (playback) or generate zero samples (capture)\n"
    "pipewire\n"
    "    PipeWire Sound Server\n"
    "pulse\n"
    "    PulseAudio Sound Server\n"
    "default\n"
    "    Default ALSA Output (currently PulseAudio Sound Server)\n"
    "sysdefault:CARD=PCH\n"
    "    HDA Intel PCH, ALC269VC Analog\n"
)

# When neither pipewire-alsa nor libasound2-plugins is installed,
# `arecord -L` still always lists 'default' (handled by alsa-utils).
ALSA_PCM_LIST_DEFAULT_ONLY = (
    "null\n"
    "    Discard all samples\n"
    "default\n"
    "    Playback/recording through the PulseAudio sound server\n"
)


def make_subprocess_mock(plan: dict):
    """Build a subprocess.run replacement that returns canned output per command.

    plan = {
      "pactl_sources": str,   # 'pactl list sources short' stdout
      "pactl_sinks":   str,   # 'pactl list sinks short' stdout
      "arecord_l":     str,   # 'arecord -l' stdout (hardware list)
      "aplay_l":       str,   # 'aplay -l' stdout
      "arecord_L":     str,   # 'arecord -L' stdout (PCM list — pipewire/pulse/default)
      "aplay_L":       str,   # 'aplay -L' stdout
      "set_default_calls": list[list[str]],  # appended to as side-effect
    }
    """
    set_default_calls = plan.setdefault("set_default_calls", [])
    plan.setdefault("arecord_L", ALSA_PCM_LIST_WITH_PIPEWIRE)
    plan.setdefault("aplay_L", ALSA_PCM_LIST_WITH_PIPEWIRE)

    def _fake_run(cmd, *args, **kwargs):
        if not isinstance(cmd, (list, tuple)) or not cmd:
            return _FakeCompleted()
        head = cmd[0]
        if head == "pactl":
            if len(cmd) >= 3 and cmd[1] == "list" and cmd[2] == "sources":
                return _FakeCompleted(stdout=plan.get("pactl_sources", ""))
            if len(cmd) >= 3 and cmd[1] == "list" and cmd[2] == "sinks":
                return _FakeCompleted(stdout=plan.get("pactl_sinks", ""))
            if len(cmd) >= 3 and cmd[1] in ("set-default-source", "set-default-sink"):
                set_default_calls.append(list(cmd[1:]))
                return _FakeCompleted()
        if head == "arecord":
            if len(cmd) >= 2 and cmd[1] == "-L":
                return _FakeCompleted(stdout=plan.get("arecord_L", ""))
            return _FakeCompleted(stdout=plan.get("arecord_l", plan.get("arecord", "")))
        if head == "aplay":
            if len(cmd) >= 2 and cmd[1] == "-L":
                return _FakeCompleted(stdout=plan.get("aplay_L", ""))
            return _FakeCompleted(stdout=plan.get("aplay_l", plan.get("aplay", "")))
        return _FakeCompleted()

    return _fake_run


# PCMs that route through PulseAudio/PipeWire (any one of these is acceptable
# as the resolver's output when BT is detected via pactl).
PA_ROUTING_PCMS = {"pipewire", "pulse", "default"}


# Realistic ALSA card list (no external device present)
ALSA_BUILTIN_ONLY_CAPTURE = (
    "**** List of CAPTURE Hardware Devices ****\n"
    "card 0: PCH [HDA Intel PCH], device 0: ALC269VC Analog [ALC269VC Analog]\n"
)
ALSA_BUILTIN_ONLY_PLAYBACK = (
    "**** List of PLAYBACK Hardware Devices ****\n"
    "card 0: PCH [HDA Intel PCH], device 0: ALC269VC Analog [ALC269VC Analog]\n"
)

# USB combined device (Jabra puck) — same card has capture + playback
ALSA_USB_COMBINED_CAPTURE = (
    ALSA_BUILTIN_ONLY_CAPTURE
    + "card 1: Device [Jabra Speak 410 USB], device 0: USB Audio [USB Audio]\n"
)
ALSA_USB_COMBINED_PLAYBACK = (
    ALSA_BUILTIN_ONLY_PLAYBACK
    + "card 1: Device [Jabra Speak 410 USB], device 0: USB Audio [USB Audio]\n"
)

# PulseAudio source/sink lines for the AM W45 BT headset.
# Format: id<TAB>name<TAB>driver<TAB>spec<TAB>state
PACTL_BT_SOURCE = (
    "0\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
    "1\tbluez_input.AM_W45.headset-head-unit\tPipeWire\ts16le 1ch 16000Hz\tRUNNING\n"
    "2\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
)
PACTL_BT_SINK = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
    "1\tbluez_output.AM_W45.headset-head-unit\tPipeWire\ts16le 1ch 16000Hz\tRUNNING\n"
)
PACTL_NO_BT_SOURCE = (
    "0\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
)
PACTL_NO_BT_SINK = (
    "0\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
)


# ===========================================================================
# audio_device_resolve.resolve_audio_pair()
# ===========================================================================

class TestResolveAudioPair:
    """End-to-end checks for the ALSA + PulseAudio aware pair resolver."""

    def test_bluetooth_combined_wins_over_builtin(self, monkeypatch):
        """BT mic+speaker via PulseAudio is picked even when only built-in ALSA exists."""
        plan = {
            "pactl_sources": PACTL_BT_SOURCE,
            "pactl_sinks": PACTL_BT_SINK,
            "arecord_l": ALSA_BUILTIN_ONLY_CAPTURE,
            "aplay_l": ALSA_BUILTIN_ONLY_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.is_combined is True, "BT mic+speaker should report combined=True"
        assert pair.capture in PA_ROUTING_PCMS, (
            f"capture should route via a PulseAudio PCM, got {pair.capture!r}"
        )
        assert pair.playback in PA_ROUTING_PCMS, (
            f"playback should route via a PulseAudio PCM, got {pair.playback!r}"
        )
        assert "bluez" in (pair.capture_name or "").lower()
        assert "bluez" in (pair.playback_name or "").lower()
        # Confirm PulseAudio default was switched to the BT source AND sink
        default_calls = plan["set_default_calls"]
        switched_to = {tuple(c) for c in default_calls}
        assert ("set-default-source", "bluez_input.AM_W45.headset-head-unit") in switched_to
        assert ("set-default-sink", "bluez_output.AM_W45.headset-head-unit") in switched_to

    def test_bluetooth_beats_usb_combined(self, monkeypatch):
        """When BOTH a BT headset and a USB puck are connected, BT wins."""
        plan = {
            "pactl_sources": PACTL_BT_SOURCE,
            "pactl_sinks": PACTL_BT_SINK,
            "arecord_l": ALSA_USB_COMBINED_CAPTURE,
            "aplay_l": ALSA_USB_COMBINED_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        # BT (Priority 0) must beat USB (Priority 2)
        assert pair.is_combined is True
        assert pair.capture in PA_ROUTING_PCMS
        assert pair.playback in PA_ROUTING_PCMS
        assert "bluez" in (pair.capture_name or "").lower()

    def test_usb_combined_when_no_bluetooth(self, monkeypatch):
        """No BT connected → USB combined mic+speaker (Jabra) wins."""
        plan = {
            "pactl_sources": PACTL_NO_BT_SOURCE,
            "pactl_sinks": PACTL_NO_BT_SINK,
            "arecord_l": ALSA_USB_COMBINED_CAPTURE,
            "aplay_l": ALSA_USB_COMBINED_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.is_combined is True
        # USB device is card 1 in the fixture
        assert pair.capture == "plughw:1,0"
        assert pair.playback == "plughw:1,0"
        assert "jabra" in (pair.capture_name or "").lower()

    def test_builtin_only_does_not_falsely_claim_external(self, monkeypatch):
        """No BT, no USB → resolver should NOT claim a combined external device."""
        plan = {
            "pactl_sources": PACTL_NO_BT_SOURCE,
            "pactl_sinks": PACTL_NO_BT_SINK,
            "arecord_l": ALSA_BUILTIN_ONLY_CAPTURE,
            "aplay_l": ALSA_BUILTIN_ONLY_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.is_combined is False
        assert pair.capture is None, "No external mic should leave capture=None"
        # playback falls back to plughw:0,0 (built-in) — this is expected for
        # the Docker dmix workaround, NOT a claim that a built-in is 'external'
        assert pair.playback == "plughw:0,0"

    def test_bluetooth_mic_only_no_sink(self, monkeypatch):
        """BT source but no BT sink → BT capture only, playback falls back."""
        plan = {
            "pactl_sources": PACTL_BT_SOURCE,
            "pactl_sinks": PACTL_NO_BT_SINK,
            "arecord_l": ALSA_BUILTIN_ONLY_CAPTURE,
            "aplay_l": ALSA_BUILTIN_ONLY_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.capture in PA_ROUTING_PCMS
        assert pair.is_combined is False  # no matching BT sink

    def test_bluetooth_speaker_only_no_mic_a2dp_device(self, monkeypatch):
        """BT sink but NO BT source (A2DP-only device like AM-W45) → speaker
        routes via PulseAudio, mic falls through to next priority tier."""
        plan = {
            "pactl_sources": PACTL_NO_BT_SOURCE,
            "pactl_sinks": PACTL_BT_SINK,
            "arecord_l": ALSA_BUILTIN_ONLY_CAPTURE,
            "aplay_l": ALSA_BUILTIN_ONLY_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        # BT speaker should be routed via PulseAudio even without a BT mic
        assert pair.playback in PA_ROUTING_PCMS, (
            f"BT speaker should route via PA PCM, got {pair.playback!r}"
        )
        assert "bluez" in (pair.playback_name or "").lower()
        # Mic correctly remains unresolved (no external mic present)
        assert pair.capture is None
        assert pair.is_combined is False
        # Verify PulseAudio default sink was set to the BT sink
        switched = {tuple(c) for c in plan["set_default_calls"]}
        assert ("set-default-sink", "bluez_output.AM_W45.headset-head-unit") in switched

    def test_bluetooth_speaker_only_plus_usb_combined_prefers_usb_combined(self, monkeypatch):
        """When a BT A2DP-only speaker AND a USB combined puck both exist,
        the USB combined device wins for BOTH capture and playback.
        Rationale: combined devices on the same ALSA card avoid the round-trip
        echo and latency that comes from splitting mic/speaker across devices."""
        plan = {
            "pactl_sources": PACTL_NO_BT_SOURCE,
            "pactl_sinks": PACTL_BT_SINK,
            "arecord_l": ALSA_USB_COMBINED_CAPTURE,
            "aplay_l": ALSA_USB_COMBINED_PLAYBACK,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.is_combined is True
        assert pair.capture == "plughw:1,0", "USB combined wins capture"
        assert pair.playback == "plughw:1,0", "USB combined wins playback (single card avoids echo)"

    def test_falls_back_to_default_when_pipewire_pulse_missing(self, monkeypatch):
        """No pipewire/pulse PCM in arecord -L → resolver uses 'default' for BT routing."""
        plan = {
            "pactl_sources": PACTL_BT_SOURCE,
            "pactl_sinks": PACTL_BT_SINK,
            "arecord_l": ALSA_BUILTIN_ONLY_CAPTURE,
            "aplay_l": ALSA_BUILTIN_ONLY_PLAYBACK,
            "arecord_L": ALSA_PCM_LIST_DEFAULT_ONLY,
            "aplay_L": ALSA_PCM_LIST_DEFAULT_ONLY,
        }
        monkeypatch.setattr(adr.subprocess, "run", make_subprocess_mock(plan))
        monkeypatch.delenv("AUDIO_OUTPUT_DEVICE_NAME", raising=False)
        monkeypatch.delenv("AUDIO_OUTPUT_FALLBACK_DEVICE", raising=False)

        pair = adr.resolve_audio_pair(sd=None)

        assert pair.is_combined is True
        # Only 'default' was available in -L output
        assert pair.capture == "default"
        assert pair.playback == "default"


# ===========================================================================
# mic_input_resolve.resolve_sounddevice_capture_device_index()
# ===========================================================================

class TestSounddeviceResolver:
    """Checks for the PortAudio / sounddevice capture resolver."""

    def _patch_pactl(self, monkeypatch, source_stdout: str):
        plan = {"pactl_sources": source_stdout, "pactl_sinks": "", "set_default_calls": []}
        monkeypatch.setattr(mir.subprocess, "run", make_subprocess_mock(plan))
        return plan

    def _clear_env(self, monkeypatch):
        for k in (
            "AUDIO_INPUT_DEVICE_INDEX",
            "AUDIO_INPUT_DEVICE_NAME",
            "MEETINGBOX_AUTO_SELECT_USB_MIC",
            "MEETINGBOX_USB_MIC_STRICT",
        ):
            monkeypatch.delenv(k, raising=False)
        # config.py reads these at import time — patch the module-level constants too
        monkeypatch.setattr(mir, "AUDIO_INPUT_DEVICE_INDEX", "", raising=False)
        monkeypatch.setattr(mir, "AUDIO_INPUT_DEVICE_NAME", "", raising=False)

    def test_bluetooth_via_pulse_returns_pulse_index(self, monkeypatch):
        """BT source visible in pactl → returns the index of the 'pulse' PortAudio device."""
        self._clear_env(monkeypatch)
        plan = self._patch_pactl(monkeypatch, PACTL_BT_SOURCE)

        sd = FakeSounddevice([
            {"name": "HDA Intel PCH: ALC269VC Analog (hw:0,0)",
             "max_input_channels": 2, "max_output_channels": 0},
            {"name": "pulse",
             "max_input_channels": 32, "max_output_channels": 32},
        ])
        idx = mir.resolve_sounddevice_capture_device_index(sd)
        assert idx == 1, f"Expected the 'pulse' device (index 1), got {idx!r}"
        # PulseAudio default source should be flipped to the BT source
        assert ["set-default-source", "bluez_input.AM_W45.headset-head-unit"] in plan["set_default_calls"]

    def test_bluetooth_via_pulse_no_pulse_device_returns_none(self, monkeypatch):
        """BT in pactl but no 'pulse' in PortAudio → return None so host default is used."""
        self._clear_env(monkeypatch)
        self._patch_pactl(monkeypatch, PACTL_BT_SOURCE)

        sd = FakeSounddevice([
            {"name": "HDA Intel PCH: ALC269VC Analog (hw:0,0)",
             "max_input_channels": 2, "max_output_channels": 0},
        ])
        idx = mir.resolve_sounddevice_capture_device_index(sd)
        assert idx is None  # falls through to PortAudio host default

    def test_no_bt_no_usb_strict_returns_none(self, monkeypatch):
        """No external device → strict mode (default) returns None instead of built-in."""
        self._clear_env(monkeypatch)
        self._patch_pactl(monkeypatch, PACTL_NO_BT_SOURCE)

        sd = FakeSounddevice([
            {"name": "HDA Intel PCH: ALC269VC Analog (hw:0,0)",
             "max_input_channels": 2, "max_output_channels": 0},
        ])
        idx = mir.resolve_sounddevice_capture_device_index(sd)
        assert idx is None, "Strict mode must NOT fall back to a built-in mic"

    def test_usb_when_no_bluetooth(self, monkeypatch):
        """No BT → USB device in PortAudio enumeration is picked."""
        self._clear_env(monkeypatch)
        self._patch_pactl(monkeypatch, PACTL_NO_BT_SOURCE)

        sd = FakeSounddevice([
            {"name": "HDA Intel PCH: ALC269VC Analog (hw:0,0)",
             "max_input_channels": 2, "max_output_channels": 0},
            {"name": "Jabra Speak 410 USB: USB Audio (hw:1,0)",
             "max_input_channels": 1, "max_output_channels": 2},
        ])
        idx = mir.resolve_sounddevice_capture_device_index(sd)
        assert idx == 1, f"Should pick USB combined Jabra device (index 1), got {idx!r}"

    def test_bluetooth_beats_usb(self, monkeypatch):
        """BT source in pactl AND USB device in PortAudio → BT wins."""
        self._clear_env(monkeypatch)
        self._patch_pactl(monkeypatch, PACTL_BT_SOURCE)

        sd = FakeSounddevice([
            {"name": "HDA Intel PCH: ALC269VC Analog (hw:0,0)",
             "max_input_channels": 2, "max_output_channels": 0},
            {"name": "Jabra Speak 410 USB: USB Audio (hw:1,0)",
             "max_input_channels": 1, "max_output_channels": 2},
            {"name": "pulse",
             "max_input_channels": 32, "max_output_channels": 32},
        ])
        idx = mir.resolve_sounddevice_capture_device_index(sd)
        assert idx == 2, f"BT via 'pulse' must beat USB, got {idx!r}"


# ===========================================================================
# Lightweight smoke check of the keyword classifiers
# ===========================================================================

class TestExternalKeywordDetection:
    """Sanity check that name-based detectors recognize BT and USB names."""

    @pytest.mark.parametrize("name", [
        "bluez_input.AM_W45.headset-head-unit",
        "bluez_output.41_42_43.a2dp-sink",
        "Bluetooth Hands-Free Audio",
        "HSP/HFP headset",
        "a2dp_sink.something",
    ])
    def test_bluetooth_names_match(self, name):
        assert mir._bluetooth_like_name(name), f"BT keyword detector missed: {name!r}"
        assert mir._external_like_name(name)

    @pytest.mark.parametrize("name", [
        "USB PnP Sound Device",
        "Jabra Speak 410 USB",
        "UAC2 Mic",
    ])
    def test_usb_names_match(self, name):
        assert mir._usb_like_name(name), f"USB keyword detector missed: {name!r}"
        assert mir._external_like_name(name)

    @pytest.mark.parametrize("name", [
        "HDA Intel PCH: ALC269VC Analog",
        "Built-in Audio Analog Stereo",
        "Internal Microphone",
    ])
    def test_builtin_names_not_external(self, name):
        assert not mir._external_like_name(name), f"Built-in flagged as external: {name!r}"
