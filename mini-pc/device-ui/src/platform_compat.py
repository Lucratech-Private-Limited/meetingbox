"""Cross-platform helpers for the MeetingBox device UI.

The device UI was originally written for a Linux appliance (Raspberry Pi /
mini-PC) and uses several Linux-only tools (``aplay``, ``pactl``, ``arecord``,
``nmcli``, ``bluetoothctl``, ``nsenter`` …) plus filesystem paths like
``/data/config`` and ``/tmp``. This module centralises the platform branching
needed to run the same code base on Windows (and, with the same primitives,
macOS) without sprinkling ``sys.platform`` checks everywhere.

Design goals:
* No imports from ``config`` (this module must be importable very early).
* No third-party imports (only stdlib) so it works before deps are loaded.
* Pure helpers — no global side effects on import.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Desktop = Windows/macOS port (mouse + keyboard, OS owns hardware). Used to
# hide/adapt appliance-only UI (Quick Panel, idle lock screen, brightness,
# Wi-Fi/BT radios, reboot/power, OTA, etc.). Linux = the 7" touch appliance.
IS_DESKTOP = IS_WINDOWS or IS_MACOS

# Input verb for user-facing copy: desktop is mouse/click, appliance is touch.
TAP_OR_CLICK = "Click" if IS_DESKTOP else "Tap"
tap_or_click = "click" if IS_DESKTOP else "tap"

# Product name used for per-user data directories on desktop OSes.
_APP_DIR_NAME = "MeetingBox"


def app_user_data_dir() -> Path | None:
    """Return the per-user writable data root for desktop installs.

    * Windows: ``%LOCALAPPDATA%\\MeetingBox`` (falls back to ``~/MeetingBox``).
    * macOS:   ``~/Library/Application Support/MeetingBox``.
    * Linux:   ``None`` — the Linux appliance keeps its existing behaviour of
      preferring ``/data/config`` (Docker volume) and the in-tree
      ``BASE_DIR/data`` fallback, so this returns ``None`` to avoid changing
      appliance path resolution.

    The directory is NOT created here; callers decide when to ``mkdir``.
    """
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / _APP_DIR_NAME
        return Path.home() / _APP_DIR_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / _APP_DIR_NAME
    return None


def default_config_dir() -> Path | None:
    """Per-user config dir on desktop OSes (``<data>/data/config``)."""
    root = app_user_data_dir()
    return (root / "data" / "config") if root is not None else None


def default_recordings_dir() -> Path | None:
    root = app_user_data_dir()
    return (root / "data" / "audio" / "recordings") if root is not None else None


def default_temp_segments_dir() -> Path | None:
    root = app_user_data_dir()
    return (root / "data" / "audio" / "temp") if root is not None else None


def default_log_file() -> str:
    """OS-appropriate default log file path.

    Linux/macOS keep the historical ``/tmp/meetingbox-ui.log``. Windows uses
    a writable per-user logs directory (``%LOCALAPPDATA%\\MeetingBox\\logs``).
    """
    if IS_WINDOWS:
        root = app_user_data_dir() or (Path.home() / _APP_DIR_NAME)
        logs = root / "logs"
        try:
            logs.mkdir(parents=True, exist_ok=True)
        except OSError:
            return str(Path(os.environ.get("TEMP", ".")) / "meetingbox-ui.log")
        return str(logs / "meetingbox-ui.log")
    return "/tmp/meetingbox-ui.log"


def has_linux_audio_tools() -> bool:
    """True only on Linux, where ALSA/Pulse CLI tools are expected.

    Used to gate ``pactl`` / ``arecord`` / ``aplay`` scanning so Windows does
    not spawn missing executables (which raise ``FileNotFoundError``).
    """
    return IS_LINUX


def bring_app_to_foreground() -> bool:
    """Raise and focus this process's main window (Windows only, best-effort).

    Desktop OAuth sends the user out to the system browser; after they finish a
    step (sign-in, or the Gmail/Calendar consent that redirects to the hosted
    dashboard) the app is left behind another window. Call this to pull our own
    window back to the front so the flow returns to the app instead of stranding
    the user on a web page.

    Returns True if a window was found and a foreground attempt was made. No-op
    (returns False) on non-Windows or if anything goes wrong — never raises.

    Note: ``ctypes`` argtypes/restypes are set explicitly because HWNDs are
    64-bit pointers; without them ctypes would truncate handles on 64-bit
    Windows and corrupt the calls.
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.AttachThreadInput.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.BOOL
        ]
        kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        our_pid = kernel32.GetCurrentProcessId()
        found: list[int] = []

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def _enum(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                wpid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == our_pid and user32.GetWindowTextLengthW(hwnd) > 0:
                    found.append(hwnd)
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(_enum), 0)
        if not found:
            return False
        hwnd = found[0]

        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)

        # Windows only lets the foreground thread reassign focus, so attach our
        # input to the current foreground thread for the duration of the call.
        fg = user32.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
        return True
    except Exception:
        return False
