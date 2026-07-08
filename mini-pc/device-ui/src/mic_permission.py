"""Microphone permission detection + request for the desktop port.

Why this exists
---------------
On Windows, a classic Win32 desktop app (which is what the PyInstaller build is)
has **no UWP-style runtime consent popup**. Whether the process can open the
microphone is governed entirely by the Windows privacy switches:

  Settings -> Privacy & security -> Microphone
    * "Microphone access"                         (per-machine / per-user master)
    * "Let apps access your microphone"
    * "Let desktop apps access your microphone"   (this one gates our .exe)

If those are off, opening a capture stream just fails / returns silence; the OS
never prompts. So the only correct, non-assuming behaviour is:

  1. Read the ConsentStore registry to learn whether access is blocked.
  2. Actually attempt to open the mic (the genuine access "request" — this is
     what registers the app under the mic privacy "recent activity" list).
  3. If blocked, deep-link the user straight to the microphone privacy page
     (``ms-settings:privacy-microphone``) so they can grant it, then relaunch.

On macOS the OS *does* prompt on first access; attempting to open the stream is
the request, and we point the user at System Settings if it was denied.

This module is intentionally UI-free so it can be unit-tested; ``main.py`` owns
the Kivy popup that surfaces the result.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# Result states.
STATUS_OK = "ok"              # a capture stream opened and produced samples
STATUS_DENIED = "denied"      # blocked by OS privacy settings
STATUS_NO_DEVICE = "no_device"  # no input device present at all
STATUS_UNAVAILABLE = "unavailable"  # audio stack missing / unknown failure

_MS_SETTINGS_MIC = "ms-settings:privacy-microphone"


@dataclass
class MicStatus:
    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == STATUS_OK

    @property
    def blocked(self) -> bool:
        return self.state == STATUS_DENIED


# ---------------------------------------------------------------------------
# Windows registry (ConsentStore) inspection
# ---------------------------------------------------------------------------

def _read_consent_value(hive, subkey: str) -> str | None:
    """Return the ConsentStore "Value" string ("Allow"/"Deny") or None."""
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    try:
        with winreg.OpenKey(hive, subkey) as key:
            val, _typ = winreg.QueryValueEx(key, "Value")
            if isinstance(val, str):
                return val.strip().lower()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def windows_consent_state() -> str | None:
    """Best-effort read of whether desktop apps may use the microphone.

    Returns "allow", "deny", or None (unknown). We treat an explicit "deny" in
    any of the relevant scopes (machine master, per-user master, or the
    NonPackaged/desktop-apps switch) as a hard block.
    """
    if not IS_WINDOWS:
        return None
    try:
        import winreg  # type: ignore
    except ImportError:
        return None

    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    checks = [
        (winreg.HKEY_LOCAL_MACHINE, base),                # machine master
        (winreg.HKEY_CURRENT_USER, base),                 # per-user master
        (winreg.HKEY_CURRENT_USER, base + r"\NonPackaged"),  # desktop apps switch
    ]
    saw_value = False
    for hive, sub in checks:
        val = _read_consent_value(hive, sub)
        if val is None:
            continue
        saw_value = True
        if val == "deny":
            logger.info("Microphone blocked by Windows privacy setting (%s).", sub)
            return "deny"
    return "allow" if saw_value else None


# ---------------------------------------------------------------------------
# Actual capture probe (the real access request)
# ---------------------------------------------------------------------------

def _has_any_input_device() -> bool | None:
    try:
        import sounddevice as sd  # type: ignore
    except Exception:
        return None
    try:
        for dev in sd.query_devices():
            if int(dev.get("max_input_channels") or 0) > 0:
                return True
        return False
    except Exception:
        return None


def _probe_open_stream() -> tuple[bool, str]:
    """Open a short input stream; returns (opened_ok, detail)."""
    try:
        import numpy as np  # type: ignore
        import sounddevice as sd  # type: ignore
    except Exception as e:  # pragma: no cover - env without audio stack
        return False, f"audio stack unavailable ({e})"

    last_err = "open failed"
    for sr in (48000, 44100, 16000):
        stream = None
        try:
            stream = sd.InputStream(channels=1, samplerate=sr, blocksize=1024, dtype="float32")
            stream.start()
            data, _ = stream.read(1024)
            if data is None or len(data) == 0:
                raise RuntimeError("empty read")
            _ = np.asarray(data)
            return True, f"{sr} Hz"
        except Exception as e:
            last_err = str(e)
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
    return False, last_err


def check_microphone() -> MicStatus:
    """Detect microphone availability without assuming it's granted.

    Order of evidence:
      1. Windows registry says "deny"  -> DENIED (no point probing).
      2. No input device present        -> NO_DEVICE.
      3. Probe opens + reads samples    -> OK.
      4. Probe fails: on Windows with an unknown/blocked consent, classify as
         DENIED (most common cause on a fresh PC); otherwise UNAVAILABLE.
    """
    consent = windows_consent_state()
    if consent == "deny":
        return MicStatus(STATUS_DENIED, "Microphone access is turned off in Windows privacy settings.")

    has_device = _has_any_input_device()
    if has_device is False:
        return MicStatus(STATUS_NO_DEVICE, "No microphone/input device was found.")

    opened, detail = _probe_open_stream()
    if opened:
        return MicStatus(STATUS_OK, detail)

    if IS_WINDOWS and consent != "allow":
        # Couldn't open and the desktop-apps switch isn't confirmed on -> almost
        # always the privacy toggle on a fresh machine.
        return MicStatus(
            STATUS_DENIED,
            "Could not open the microphone — likely blocked in Windows privacy settings.",
        )
    if IS_MACOS:
        return MicStatus(
            STATUS_DENIED,
            "Could not open the microphone — grant access under System Settings > Privacy & Security > Microphone.",
        )
    return MicStatus(STATUS_UNAVAILABLE, detail)


# ---------------------------------------------------------------------------
# Requesting access / opening the OS settings page
# ---------------------------------------------------------------------------

def open_privacy_settings() -> bool:
    """Open the OS microphone privacy page so the user can grant access."""
    try:
        if IS_WINDOWS:
            try:
                os.startfile(_MS_SETTINGS_MIC)  # type: ignore[attr-defined]
                return True
            except OSError:
                subprocess.Popen(["explorer.exe", _MS_SETTINGS_MIC])
                return True
        if IS_MACOS:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            ])
            return True
    except Exception as e:
        logger.warning("Could not open microphone privacy settings: %s", e)
    return False


def request_microphone_access() -> MicStatus:
    """Check access and, if blocked, open the privacy page for the user.

    Returns the detected :class:`MicStatus`. For Win32 there is no silent
    programmatic grant, so "requesting" means probing (the real access attempt)
    and, when denied, sending the user to the exact settings page.
    """
    status = check_microphone()
    if status.blocked:
        open_privacy_settings()
    return status
