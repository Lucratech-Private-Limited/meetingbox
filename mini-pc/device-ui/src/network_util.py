"""Lightweight network detection for onboarding (skip Wi‑Fi if wired LAN is up)."""

from __future__ import annotations

import logging
import re
import subprocess
import sys

logger = logging.getLogger(__name__)


def _run_cmd(args: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("network_util command failed %s: %s", args, e)
        return ""


def linux_ethernet_ready() -> bool:
    """
    True if an ethernet interface looks connected (carrier + IPv4 address).
    Does not require Wi‑Fi to be off.
    """
    if not sys.platform.startswith("linux"):
        return False

    nm_out = _run_cmd(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev", "status"],
        timeout=4.0,
    )
    if nm_out:
        for line in nm_out.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            dev, dev_type, state = parts[0].strip(), parts[1].strip(), parts[2].strip().lower()
            if dev_type.lower() != "ethernet":
                continue
            if "connected" not in state and "connecting" not in state:
                continue
            ip_out = _run_cmd(["ip", "-4", "addr", "show", "dev", dev], timeout=3.0)
            if ip_out and re.search(r"\binet\s+\d+\.\d+\.\d+\.\d+", ip_out):
                return True
        return False

    # Fallback without nmcli: look for common wired ifnames and carrier.
    for cand in ("eth0", "eno1", "enp0s31f6", "enp1s0", "enp2s0"):
        carrier = f"/sys/class/net/{cand}/carrier"
        try:
            with open(carrier) as f:
                if f.read().strip() == "1":
                    ip_out = _run_cmd(["ip", "-4", "addr", "show", "dev", cand], timeout=3.0)
                    if ip_out and re.search(r"\binet\s+\d+\.\d+\.\d+\.\d+", ip_out):
                        return True
        except OSError:
            continue
    return False
