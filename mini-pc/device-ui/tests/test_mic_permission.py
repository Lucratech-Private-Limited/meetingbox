"""Unit tests for the desktop microphone permission helper.

These exercise the classification logic without touching real audio hardware or
the Windows registry by monkeypatching the module's internal probes.
"""

import importlib

import pytest

mic_permission = importlib.import_module("mic_permission")


def _reload():
    return importlib.reload(mic_permission)


def test_micstatus_properties():
    ok = mic_permission.MicStatus(mic_permission.STATUS_OK, "48000 Hz")
    denied = mic_permission.MicStatus(mic_permission.STATUS_DENIED, "blocked")
    assert ok.ok and not ok.blocked
    assert denied.blocked and not denied.ok


def test_consent_deny_short_circuits(monkeypatch):
    monkeypatch.setattr(mic_permission, "windows_consent_state", lambda: "deny")
    # Probe/device helpers must NOT be needed when registry already says deny.
    monkeypatch.setattr(mic_permission, "_has_any_input_device",
                        lambda: pytest.fail("should not probe when denied"))
    status = mic_permission.check_microphone()
    assert status.state == mic_permission.STATUS_DENIED
    assert status.blocked


def test_no_input_device(monkeypatch):
    monkeypatch.setattr(mic_permission, "windows_consent_state", lambda: "allow")
    monkeypatch.setattr(mic_permission, "_has_any_input_device", lambda: False)
    status = mic_permission.check_microphone()
    assert status.state == mic_permission.STATUS_NO_DEVICE


def test_probe_ok(monkeypatch):
    monkeypatch.setattr(mic_permission, "windows_consent_state", lambda: "allow")
    monkeypatch.setattr(mic_permission, "_has_any_input_device", lambda: True)
    monkeypatch.setattr(mic_permission, "_probe_open_stream", lambda: (True, "48000 Hz"))
    status = mic_permission.check_microphone()
    assert status.ok
    assert status.state == mic_permission.STATUS_OK


def test_probe_fail_unknown_consent_on_windows_is_denied(monkeypatch):
    monkeypatch.setattr(mic_permission, "IS_WINDOWS", True)
    monkeypatch.setattr(mic_permission, "windows_consent_state", lambda: None)
    monkeypatch.setattr(mic_permission, "_has_any_input_device", lambda: True)
    monkeypatch.setattr(mic_permission, "_probe_open_stream", lambda: (False, "host error"))
    status = mic_permission.check_microphone()
    # Most common fresh-PC cause: privacy switch off -> classify as denied.
    assert status.state == mic_permission.STATUS_DENIED


def test_probe_fail_with_allow_is_unavailable(monkeypatch):
    monkeypatch.setattr(mic_permission, "IS_WINDOWS", True)
    monkeypatch.setattr(mic_permission, "windows_consent_state", lambda: "allow")
    monkeypatch.setattr(mic_permission, "_has_any_input_device", lambda: True)
    monkeypatch.setattr(mic_permission, "_probe_open_stream", lambda: (False, "driver glitch"))
    status = mic_permission.check_microphone()
    # Explicitly allowed but still failing -> not a privacy block.
    assert status.state == mic_permission.STATUS_UNAVAILABLE


def test_request_opens_settings_when_blocked(monkeypatch):
    opened = {"called": False}
    monkeypatch.setattr(mic_permission, "check_microphone",
                        lambda: mic_permission.MicStatus(mic_permission.STATUS_DENIED, "x"))
    monkeypatch.setattr(mic_permission, "open_privacy_settings",
                        lambda: opened.__setitem__("called", True) or True)
    status = mic_permission.request_microphone_access()
    assert status.blocked
    assert opened["called"] is True


def test_request_no_settings_when_ok(monkeypatch):
    opened = {"called": False}
    monkeypatch.setattr(mic_permission, "check_microphone",
                        lambda: mic_permission.MicStatus(mic_permission.STATUS_OK, "ok"))
    monkeypatch.setattr(mic_permission, "open_privacy_settings",
                        lambda: opened.__setitem__("called", True) or True)
    status = mic_permission.request_microphone_access()
    assert status.ok
    assert opened["called"] is False
