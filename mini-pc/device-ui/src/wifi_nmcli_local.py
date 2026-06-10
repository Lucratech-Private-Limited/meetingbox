"""
On-device WiFi via NetworkManager (nmcli).

Used by WiFi setup and Settings → WiFi so scans work when BACKEND_URL points at a
remote server (the API cannot see the mini-PC's wlan0).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


def has_nmcli() -> bool:
    return shutil.which("nmcli") is not None


def nmcli_run(args: list, timeout: float = 30):
    """
    Run nmcli; on PolicyKit denial retry with sudo -n nmcli (root bypasses polkit).
    /usr/bin/nmcli is in the container sudoers NOPASSWD list so this works without
    a password prompt.
    """
    cmd = ["nmcli"] + args
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    combined = ((res.stderr or "") + (res.stdout or "")).lower()
    priv = any(
        s in combined
        for s in (
            "insufficient privileges",
            "not authorized",
            "permission denied",
            "not allowed to",
            "polkit",
            "not authorized to control",
        )
    )
    if res.returncode != 0 and priv and shutil.which("sudo"):
        res2 = subprocess.run(
            ["sudo", "-n", "/usr/bin/nmcli"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        sudo_msg = ((res2.stderr or "") + (res2.stdout or "")).lower()
        # If sudo itself asks for a password, return the original result
        if res2.returncode != 0 and any(
            s in sudo_msg
            for s in ("a password is required", "no tty present", "sudo: a password")
        ):
            return res
        return res2
    return res


def detect_wifi_iface() -> Optional[str]:
    if not has_nmcli():
        return None
    try:
        res = nmcli_run(
            ["-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
            timeout=6,
        )
        for line in res.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "wifi":
                return parts[0]
    except Exception:
        return None
    return None


def _nmcli_version_skew_warning(stderr: str) -> bool:
    """
    True if stderr is only the common advisory when nmcli and the running
    NetworkManager daemon were built from different releases (e.g. nmcli in
    Docker vs NetworkManager on the host). The operation may still succeed.
    """
    s = (stderr or "").lower()
    if not s.strip():
        return False
    # Covers: "don't match", "doesn't match", "do not match", "does not match"
    has_mismatch = (
        "don't match" in s
        or "doesn't match" in s
        or "do not match" in s
        or "does not match" in s
        or "versions don't match" in s
        or "versions doesn't match" in s
    )
    if has_mismatch and ("networkmanager" in s or "nmcli" in s):
        return True
    if "restarting network manager" in s or "restart networkmanager" in s:
        return True
    return False


def get_wifi_radio_enabled() -> Optional[bool]:
    """
    True if NetworkManager Wi-Fi radio is on.
    Returns None if nmcli output could not be parsed.
    """
    if not has_nmcli():
        return None
    try:
        r = nmcli_run(["radio", "wifi"], timeout=5)
        line = (r.stdout or "").strip().lower()
        if not line and r.returncode != 0:
            return None
        if "enabled" in line or line in ("on", "true", "yes"):
            return True
        if "disabled" in line or line in ("off", "false", "no"):
            return False
    except Exception:
        return None
    return None


def set_wifi_radio(enabled: bool) -> dict:
    """Turn the main Wi-Fi hardware/software switch on or off (nmcli)."""
    if not has_nmcli():
        return {"ok": False, "message": "nmcli not available"}
    arg = "on" if enabled else "off"
    try:
        res = nmcli_run(["radio", "wifi", arg], timeout=15)
        stderr = (res.stderr or "").strip()
        stdout = (res.stdout or "").strip()

        if res.returncode == 0:
            return {"ok": True, "message": ""}

        # nmcli may exit non-zero while only printing a version-skew warning to
        # stderr (container nmcli vs host NetworkManager). Confirm via D-Bus state.
        time.sleep(0.25)
        actual = get_wifi_radio_enabled()
        if actual is not None and actual == enabled:
            if _nmcli_version_skew_warning(stderr):
                logger.warning(
                    "nmcli radio wifi %s: exit %s (version skew warning ignored): %s",
                    arg,
                    res.returncode,
                    stderr[:240],
                )
            return {"ok": True, "message": ""}

        msg = stderr or stdout or "nmcli failed"
        if _nmcli_version_skew_warning(stderr) and actual is not None and actual != enabled:
            msg = (
                "Wi-Fi radio did not change. nmcli and NetworkManager versions differ "
                "(often container vs host). On the appliance run:\n"
                "  sudo systemctl restart NetworkManager\n"
                "or install matching network-manager / nmcli packages, then try again."
            )
        return {"ok": False, "message": msg}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def empty_scan_hint() -> str:
    """
    Explain why the Wi-Fi list may be empty (Ethernet-only, no adapter, radio off, etc.).
    """
    # Local import: network_util must not import this module.
    from network_util import linux_ethernet_ready

    wired = linux_ethernet_ready()
    if not has_nmcli():
        return "Wi‑Fi scan needs nmcli (rebuild device-ui image)."
    iface = detect_wifi_iface()
    if not iface:
        if wired:
            return (
                "Wired Ethernet is connected.\n"
                "NetworkManager does not see a Wi‑Fi adapter yet. Add USB Wi‑Fi, "
                "then unplug Ethernet and tap SCAN — or tap ADD and type your SSID."
            )
        return (
            "No Wi‑Fi radio in NetworkManager.\n"
            "Add a USB Wi‑Fi adapter or use hardware with wireless, then tap SCAN "
            "or ADD to type the network name."
        )
    try:
        r = nmcli_run(["radio", "wifi"], timeout=5)
        line = (r.stdout or "").strip().lower()
        if line in ("disabled", "off") or "disabled" in line:
            return (
                "Wi‑Fi is turned off in software.\n"
                "Enable it on the host or run: nmcli radio wifi on\n"
                "Then SCAN or use ADD to connect by name."
            )
    except Exception:
        pass
    if wired:
        return (
            "Wired LAN is active — the scan list is often empty until you unplug "
            "Ethernet and tap SCAN.\n"
            "You can still tap ADD and enter your Wi‑Fi name and password to connect "
            "without scanning."
        )
    return (
        "No networks found.\n"
        "Move closer to a router and tap SCAN, or use ADD. Check the Wi‑Fi driver "
        "and that the radio is not blocked (rfkill)."
    )


def scan_wifi_networks(rescan: bool = False) -> list[dict]:
    if not has_nmcli():
        raise RuntimeError("nmcli not available")
    if rescan:
        try:
            nmcli_run(["device", "wifi", "rescan"], timeout=10)
        except Exception:
            pass

    res = nmcli_run(
        [
            "-m",
            "multiline",
            "-f",
            "SSID,SIGNAL,SECURITY,IN-USE",
            "device",
            "wifi",
            "list",
        ],
        timeout=15,
    )
    nets: list[dict] = []
    cur: dict[str, str] = {}

    def flush_current():
        ssid = (cur.get("SSID") or "").strip()
        if not ssid:
            return
        signal_raw = (cur.get("SIGNAL") or "0").strip()
        sec_raw = (cur.get("SECURITY") or "").strip()
        in_use = (cur.get("IN-USE") or "").strip()
        try:
            signal = int(signal_raw) if signal_raw else 0
        except ValueError:
            signal = 0
        nets.append(
            {
                "ssid": ssid,
                "signal_strength": signal,
                "security": sec_raw or "open",
                "connected": in_use == "*",
            }
        )

    for line in res.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        val = v.strip()
        if key == "SSID" and "SSID" in cur:
            flush_current()
            cur = {}
        cur[key] = val

    flush_current()
    return nets


def _is_connected_to(ssid: str) -> bool:
    """Return True if NetworkManager reports an active connection to the given SSID."""
    try:
        r = nmcli_run(["-t", "-f", "ACTIVE,SSID", "dev", "wifi"], timeout=6)
        for line in (r.stdout or "").splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip().lower() == "yes":
                if parts[1].strip() == ssid:
                    return True
    except Exception:
        pass
    return False


def connect_wifi_network(ssid: str, password: Optional[str]) -> dict:
    iface = detect_wifi_iface()
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    if iface:
        args += ["ifname", iface]
    res = nmcli_run(args, timeout=30)

    stderr = (res.stderr or "").strip()
    stdout = (res.stdout or "").strip()
    combined = (stderr + " " + stdout).lower()

    # Immediate success
    if res.returncode == 0:
        return {"status": "connected", "message": f"Connected to {ssid}"}

    # nmcli reports "successfully activated" in stdout even when exit != 0
    if "successfully activated" in combined or "successfully connected" in combined:
        return {"status": "connected", "message": f"Connected to {ssid}"}

    # Version skew between the container's nmcli and the host's NetworkManager
    # causes a non-zero exit regardless of whether the operation succeeded.
    # Wait up to 4 s and verify via the actual device state.
    if _nmcli_version_skew_warning(stderr) or _nmcli_version_skew_warning(stdout):
        for _attempt in range(4):
            time.sleep(1.0)
            if _is_connected_to(ssid):
                logger.info(
                    "connect_wifi_network: connected to %s (nmcli version-skew warning ignored)",
                    ssid,
                )
                return {"status": "connected", "message": f"Connected to {ssid}"}
        # Still not connected — real failure, but strip the version warning from the
        # user-facing message so only the actionable part is shown.
        real_lines = [
            ln for ln in (stderr + "\n" + stdout).splitlines()
            if ln.strip() and not _nmcli_version_skew_warning(ln)
        ]
        msg = "\n".join(real_lines).strip() or "Connection failed"
        return {"status": "failed", "message": msg}

    msg = stderr or stdout or "Connection failed"
    ml = msg.lower()
    if "password" not in ml and "802" not in ml:
        if any(s in ml for s in ("sudo", "privileges", "not authorized", "polkit")):
            msg += (
                "\n\nWiFi needs NetworkManager permission on this device. "
                "See scripts/sudoers.meetingbox-nmcli.example or scripts/polkit/"
            )
    return {"status": "failed", "message": msg}


def list_saved_wifi_connection_names() -> list[str]:
    """Return NetworkManager connection profile names that are Wi‑Fi type."""
    if not has_nmcli():
        return []
    try:
        res = nmcli_run(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=12)
        if res.returncode != 0:
            return []
        names: list[str] = []
        for line in (res.stdout or "").splitlines():
            if ":" not in line:
                continue
            name, typ = line.split(":", 1)
            name = name.strip()
            low = typ.strip().lower()
            if not name:
                continue
            if "wireless" in low or "wifi" in low or low == "802-11-wireless":
                names.append(name)
        return sorted(set(names))
    except Exception:
        return []


def forget_wifi_connection(con_name: str) -> dict:
    """Delete a saved Wi‑Fi profile by connection name (``nmcli connection delete``)."""
    if not has_nmcli():
        return {"ok": False, "message": "nmcli not available"}
    con = (con_name or "").strip()
    if not con:
        return {"ok": False, "message": "No connection name"}
    try:
        res = nmcli_run(["connection", "delete", con], timeout=20)
        if res.returncode == 0:
            return {"ok": True, "message": ""}
        msg = (res.stderr or res.stdout or "").strip() or "Delete failed"
        return {"ok": False, "message": msg[:400]}
    except Exception as e:
        return {"ok": False, "message": str(e)[:400]}

