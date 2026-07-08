"""Read-only network status for desktop (Windows/macOS) MeetingBox builds.

On the Linux appliance the device *manages* Wi-Fi/Bluetooth (nmcli /
bluetoothctl). On a normal PC the OS owns those radios, so per the product
decision we only *reflect* the PC's current connection — never mutate it.

This module exposes small, dependency-light readers:

* :func:`current_wifi` — the SSID + signal of the Wi-Fi the PC is on.
* :func:`wifi_radio_on` — whether a Wi-Fi interface is connected/up.
* :func:`bluetooth_radio_on` — whether a Bluetooth radio is present + enabled.
* :func:`bluetooth_devices` — connected Bluetooth devices (best-effort).
* :func:`primary_ipv4` — the LAN IPv4 others would use to reach this PC.

All functions degrade gracefully (return None / empty) and never raise.
Windows uses ``netsh``/PowerShell; macOS uses ``networksetup``/``airport``.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# Hide console windows when spawning helpers on Windows.
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _run(cmd: list[str], timeout: float = 6) -> str:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        return (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("net_status command failed %s: %s", cmd, exc)
        return ""


def _powershell(script: str, timeout: float = 8) -> str:
    return _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

def _parse_netsh_wlan_interfaces(text: str) -> dict | None:
    """Parse ``netsh wlan show interfaces`` into the connected network info."""
    ssid = None
    signal = None
    state = None
    for raw in text.splitlines():
        line = raw.strip()
        # Match "SSID" but not "BSSID"; netsh uses ": " separators.
        low = line.lower()
        if low.startswith("state") and ":" in line:
            state = line.split(":", 1)[1].strip().lower()
        elif low.startswith("ssid") and not low.startswith("bssid") and ":" in line:
            ssid = line.split(":", 1)[1].strip()
        elif low.startswith("signal") and ":" in line:
            pct = line.split(":", 1)[1].strip().rstrip("%").strip()
            try:
                signal = int(pct)
            except ValueError:
                signal = None
    if not ssid:
        return None
    connected = state in (None, "connected") and bool(ssid)
    return {"ssid": ssid, "signal_strength": signal or 0, "connected": connected}


def current_wifi() -> dict | None:
    """Return ``{"ssid", "signal_strength", "connected"}`` for the active Wi-Fi.

    Returns None when not on Wi-Fi (e.g. wired-only) or no wireless adapter.
    """
    if IS_WINDOWS:
        return _parse_netsh_wlan_interfaces(_run(["netsh", "wlan", "show", "interfaces"]))
    if IS_MACOS:
        out = _run([
            "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/"
            "Current/Resources/airport", "-I",
        ])
        ssid = None
        rssi = None
        for line in out.splitlines():
            s = line.strip()
            if s.lower().startswith("ssid:"):
                ssid = s.split(":", 1)[1].strip()
            elif s.lower().startswith("agrctlrssi:"):
                try:
                    rssi = int(s.split(":", 1)[1].strip())
                except ValueError:
                    rssi = None
        if not ssid:
            return None
        # Map RSSI (dBm) roughly to 0–100%.
        signal = 0
        if rssi is not None:
            signal = max(0, min(100, 2 * (rssi + 100)))
        return {"ssid": ssid, "signal_strength": signal, "connected": True}
    return None


def wifi_radio_on() -> bool | None:
    """Best-effort: True when a Wi-Fi interface is connected/enabled."""
    info = current_wifi()
    if info is not None:
        return bool(info.get("connected"))
    if IS_WINDOWS:
        text = _run(["netsh", "wlan", "show", "interfaces"]).lower()
        if "there is no wireless interface" in text:
            return None  # no Wi-Fi adapter at all
        # Adapter exists but disconnected.
        return False
    return None


# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------

def bluetooth_radio_on() -> bool | None:
    """True when a Bluetooth radio is present and enabled (Windows)."""
    if not IS_WINDOWS:
        return None
    out = _powershell(
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FriendlyName -match 'Radio|Adapter|Bluetooth' } | "
        "Select-Object -ExpandProperty Status"
    )
    statuses = [s.strip().upper() for s in out.splitlines() if s.strip()]
    if not statuses:
        # Fallback: any bluetooth device present?
        any_out = _powershell(
            "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty Status"
        )
        any_status = [s.strip().upper() for s in any_out.splitlines() if s.strip()]
        if not any_status:
            return None
        return any("OK" == s for s in any_status)
    return any(s == "OK" for s in statuses)


def bluetooth_devices() -> list[dict]:
    """Return connected Bluetooth devices as ``[{"name", "mac"}]`` (best-effort)."""
    if not IS_WINDOWS:
        return []
    out = _powershell(
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Status -eq 'OK' -and "
        "$_.FriendlyName -notmatch 'Radio|Adapter|Enumerator|Host' } | "
        "Select-Object -ExpandProperty FriendlyName"
    )
    devices: list[dict] = []
    seen: set[str] = set()
    for line in out.splitlines():
        name = line.strip()
        if name and name not in seen:
            seen.add(name)
            devices.append({"name": name, "mac": ""})
    return devices


# ---------------------------------------------------------------------------
# LAN IPv4
# ---------------------------------------------------------------------------

def primary_ipv4() -> str | None:
    """The LAN IPv4 address others would use to reach this PC, or None."""
    # UDP "connect" trick: no packets sent, but the OS picks the egress NIC.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError as exc:
        logger.debug("primary_ipv4 UDP probe failed: %s", exc)
    # Fallback to hostname resolution.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None
