"""Windows auto-start registration for the Pepper desktop companion.

So Pepper "feels like part of the OS", the app registers itself under the
per-user *Run* key (``HKCU\\...\\CurrentVersion\\Run``) so Windows launches it at
login and the floating dock is already there — no manual launch needed. Using the
HKCU key means no admin rights are required.

Everything is best-effort and Windows-only: any failure is logged and swallowed
so it can never block startup, and it is a no-op on macOS/Linux.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import platform_compat

logger = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "MeetingBoxPepper"


def _launch_command() -> str | None:
    """Return the command Windows should run at login, or None if undeterminable."""
    exe = sys.executable
    if not exe:
        return None
    # Packaged build (PyInstaller / Nuitka): the executable *is* the app.
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    # Source run: launch this module's app entrypoint with the windowed
    # interpreter (pythonw) so no console flashes on login.
    main_py = Path(__file__).resolve().parent / "main.py"
    if not main_py.is_file():
        return None
    pyw = Path(exe).with_name("pythonw.exe")
    interp = str(pyw) if pyw.is_file() else exe
    return f'"{interp}" "{main_py}"'


def register(enabled: bool = True) -> bool:
    """Add (or remove) the login auto-start entry. Returns True on success."""
    if not platform_compat.IS_WINDOWS:
        return False
    try:
        import winreg
    except Exception:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            if not enabled:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
                return True
            cmd = _launch_command()
            if not cmd:
                logger.debug("windows_autostart: no launch command resolved")
                return False
            # Skip the write if it is already correct (avoids needless registry churn).
            try:
                existing, _ = winreg.QueryValueEx(key, _VALUE_NAME)
                if existing == cmd:
                    return True
            except FileNotFoundError:
                pass
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)
            logger.info("windows_autostart: registered login entry")
            return True
    except OSError:
        logger.debug("windows_autostart: registry write failed", exc_info=True)
        return False
