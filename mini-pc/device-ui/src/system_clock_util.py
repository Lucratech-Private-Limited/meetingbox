"""Best-effort host clock/timezone changes from the kiosk UI container.

All three operations run timedatectl via the meetingbox-timedatectl nsenter
helper script (mounted at /usr/local/bin/meetingbox-timedatectl).  This puts
the process in the host's namespaces so timedatectl talks to the host's
systemd-timedated — identical to the pattern used for WiFi (nmcli) and
Bluetooth (bluetoothctl).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)

_HELPER = "/usr/local/bin/meetingbox-timedatectl"


def _run_timedatectl(*args: str, timeout: float = 20) -> bool:
    """Run timedatectl <args> via the nsenter helper as root (sudo -n sh helper ...).

    Falls back to running timedatectl directly in the container if the helper
    is not mounted (e.g. bare-metal install without Docker).
    """
    sudo = shutil.which("sudo")

    # Primary: nsenter helper (runs timedatectl in host namespace as root)
    if sudo and os.path.exists(_HELPER):
        try:
            r = subprocess.run(
                [sudo, "-n", "sh", _HELPER, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            logger.info("timedatectl %s via helper: rc=%s stderr=%s",
                        " ".join(args), r.returncode, (r.stderr or "").strip()[:120])
            if r.returncode == 0:
                return True
        except Exception as e:
            logger.debug("timedatectl helper failed: %s", e)

    # Fallback: direct timedatectl (may work on bare-metal or if systemd D-Bus is reachable)
    exe = shutil.which("timedatectl")
    if not exe:
        logger.debug("timedatectl not found on PATH")
        return False

    # Try with sudo first, then without
    for cmd in (
        [sudo, "-n", exe, *args] if sudo else None,
        [exe, *args],
    ):
        if cmd is None:
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                logger.info("timedatectl %s (direct): rc=0", " ".join(args))
                return True
        except Exception as e:
            logger.debug("timedatectl direct %s: %s", " ".join(args), e)

    return False


def try_set_timezone(tz: str) -> bool:
    tz = (tz or "").strip()
    if not tz:
        return False
    return _run_timedatectl("set-timezone", tz)


def try_set_time(iso_fragment: str) -> bool:
    """*iso_fragment* e.g. ``2026-05-21 14:35:00`` (local time, 24-hour)."""
    s = (iso_fragment or "").strip()
    if not s:
        return False
    # timedatectl set-time requires NTP to be off first
    _run_timedatectl("set-ntp", "false", timeout=10)
    return _run_timedatectl("set-time", s)


def try_set_ntp(enabled: bool) -> bool:
    arg = "true" if enabled else "false"
    return _run_timedatectl("set-ntp", arg)
