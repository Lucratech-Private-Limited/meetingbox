"""
Hardware utilities for Linux display power and brightness control.

Uses the standard Linux backlight sysfs interface when available and
falls back to X11 DPMS commands for screen power control.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BACKLIGHT_ROOT = Path("/sys/class/backlight")


def _x11_env() -> dict:
    """Env for xset: same DISPLAY as the running app, not hard-coded :0."""
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"
    return env

BRIGHTNESS_MAP = {
    "low": 0.30,
    "medium": 0.65,
    "high": 1.0,
}


def _find_path(name: str) -> Path | None:
    if not BACKLIGHT_ROOT.exists():
        return None
    for entry in sorted(BACKLIGHT_ROOT.iterdir()):
        p = entry / name
        if p.exists():
            return p
    return None


def _get_max_brightness() -> int:
    p = _find_path("max_brightness")
    if p:
        try:
            return int(p.read_text().strip())
        except Exception:
            pass
    return 255


def set_brightness_pct(pct: int) -> None:
    """Set backlight brightness 0–100 (% of sysfs max_brightness)."""
    pct = max(0, min(100, int(pct)))
    max_br = _get_max_brightness()
    value = max(1, min(max_br, int(round(max_br * (pct / 100.0)))))
    bp = _find_path("brightness")
    if not bp:
        logger.debug("No backlight sysfs found — skipping brightness change")
        return
    try:
        bp.write_text(str(value))
        logger.info("Brightness set to %s%% (%d/%d)", pct, value, max_br)
    except PermissionError:
        try:
            subprocess.run(
                ["sudo", "tee", str(bp)],
                input=str(value).encode(), capture_output=True, timeout=5,
            )
            logger.info("Brightness set via sudo to %s%% (%d/%d)", pct, value, max_br)
        except Exception as e:
            logger.warning("Failed to set brightness: %s", e)
    except Exception as e:
        logger.warning("Failed to set brightness: %s", e)


def get_brightness_pct() -> int | None:
    """Read current brightness as 0–100, or None if sysfs unavailable."""
    max_br = _get_max_brightness()
    bp = _find_path("brightness")
    if not bp or max_br <= 0:
        return None
    try:
        cur = int(bp.read_text().strip())
        return max(0, min(100, int(round(100.0 * cur / max_br))))
    except Exception:
        return None


def set_brightness(level: str) -> None:
    """Set display brightness. level: 'low', 'medium', 'high', or numeric percent string/int."""
    pct: int | None = None
    if isinstance(level, (int, float)):
        pct = max(0, min(100, int(level)))
    else:
        s = (level or "").strip().lower()
        if s.isdigit():
            pct = max(0, min(100, int(s)))
    if pct is not None:
        set_brightness_pct(pct)
        return
    fraction = BRIGHTNESS_MAP.get(str(level).strip().lower(), 1.0)
    max_br = _get_max_brightness()
    value = max(1, int(max_br * fraction))
    bp = _find_path("brightness")
    if not bp:
        logger.debug("No backlight sysfs found — skipping brightness change")
        return
    try:
        bp.write_text(str(value))
        logger.info("Brightness set to %s (%d/%d)", level, value, max_br)
    except PermissionError:
        try:
            subprocess.run(
                ["sudo", "tee", str(bp)],
                input=str(value).encode(), capture_output=True, timeout=5,
            )
            logger.info("Brightness set via sudo to %s (%d/%d)", level, value, max_br)
        except Exception as e:
            logger.warning("Failed to set brightness: %s", e)
    except Exception as e:
        logger.warning("Failed to set brightness: %s", e)


def screen_off() -> None:
    """Turn the display backlight off."""
    bp = _find_path("bl_power")
    if bp:
        try:
            bp.write_text("1")
            return
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "tee", str(bp)],
                    input=b"1", capture_output=True, timeout=5,
                )
                return
            except Exception:
                pass
        except Exception:
            pass
    try:
        subprocess.run(
            ["xset", "dpms", "force", "off"],
            capture_output=True, timeout=5,
            env=_x11_env(),
        )
    except Exception as e:
        logger.debug("screen_off fallback failed: %s", e)


def screen_on(level: str = "high") -> None:
    """Turn the display backlight on and restore brightness."""
    bp = _find_path("bl_power")
    if bp:
        try:
            bp.write_text("0")
        except PermissionError:
            try:
                subprocess.run(
                    ["sudo", "tee", str(bp)],
                    input=b"0", capture_output=True, timeout=5,
                )
            except Exception:
                pass
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["xset", "dpms", "force", "on"],
                capture_output=True, timeout=5,
                env=_x11_env(),
            )
        except Exception:
            pass
    set_brightness(level)


def _local_power_skip() -> bool:
    """Skip host power commands (e.g. desktop dev on Windows, or tests)."""
    if sys.platform.startswith("win"):
        return True
    v = os.environ.get("MEETINGBOX_SKIP_LOCAL_POWER", "").strip().lower()
    return v in ("1", "true", "yes")


def request_system_reboot() -> bool:
    """
    Reboot the machine that runs this UI (mini-PC / kiosk host).

    The web API often runs in Docker without permission to reboot the host; the
    appliance UI process runs on the host and can invoke systemd/sudo the same
    way brightness uses sudo tee.
    """
    if _local_power_skip():
        logger.debug("Local reboot skipped (Windows or MEETINGBOX_SKIP_LOCAL_POWER)")
        return False

    env_cmd = (os.environ.get("MEETINGBOX_LOCAL_REBOOT_CMD") or "").strip()
    if env_cmd:
        try:
            subprocess.Popen(env_cmd, shell=True, close_fds=True, start_new_session=True)
            logger.info("Reboot requested via MEETINGBOX_LOCAL_REBOOT_CMD")
            return True
        except Exception as e:
            logger.warning("MEETINGBOX_LOCAL_REBOOT_CMD failed: %s", e)

    candidates: list[list[str]] = []

    # Prefer the nsenter helper via sh (mounted in Docker, in sudoers NOPASSWD).
    # Always call via "sh" — the script files are not +x in git so direct sudo exec fails.
    _reboot_helper = "/usr/local/bin/meetingbox-host-reboot"
    if os.path.exists(_reboot_helper) and shutil.which("sudo"):
        candidates.append(["sudo", "-n", "sh", _reboot_helper])

    if os.geteuid() == 0:
        rb = shutil.which("reboot") or "/sbin/reboot"
        candidates.append([rb])
    candidates.extend(
        [
            ["systemctl", "reboot"],
            ["sudo", "-n", "systemctl", "reboot"],
            ["sudo", "-n", "reboot"],
            ["sudo", "-n", "shutdown", "-r", "now"],
        ]
    )
    for args in candidates:
        try:
            subprocess.Popen(args, close_fds=True, start_new_session=True)
            logger.info("Reboot requested via %s", args)
            return True
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.debug("reboot %s: %s", args, e)
    logger.warning(
        "Local reboot not started — allow systemctl reboot or passwordless sudo "
        "for this user (see sudoers / polkit)."
    )
    return False


def request_system_poweroff() -> bool:
    """Power off the machine that runs this UI."""
    if _local_power_skip():
        logger.debug("Local poweroff skipped (Windows or MEETINGBOX_SKIP_LOCAL_POWER)")
        return False

    env_cmd = (os.environ.get("MEETINGBOX_LOCAL_POWEROFF_CMD") or "").strip()
    if env_cmd:
        try:
            subprocess.Popen(env_cmd, shell=True, close_fds=True, start_new_session=True)
            logger.info("Poweroff requested via MEETINGBOX_LOCAL_POWEROFF_CMD")
            return True
        except Exception as e:
            logger.warning("MEETINGBOX_LOCAL_POWEROFF_CMD failed: %s", e)

    candidates: list[list[str]] = []

    # Prefer the nsenter helper via sh (mounted in Docker, in sudoers NOPASSWD).
    # Always call via "sh" — the script files are not +x in git so direct sudo exec fails.
    _poweroff_helper = "/usr/local/bin/meetingbox-host-poweroff"
    if os.path.exists(_poweroff_helper) and shutil.which("sudo"):
        candidates.append(["sudo", "-n", "sh", _poweroff_helper])

    if os.geteuid() == 0:
        po = shutil.which("poweroff") or "/sbin/poweroff"
        candidates.append([po])
    candidates.extend(
        [
            ["systemctl", "poweroff"],
            ["sudo", "-n", "systemctl", "poweroff"],
            ["sudo", "-n", "poweroff"],
            ["sudo", "-n", "shutdown", "-h", "now"],
        ]
    )
    for args in candidates:
        try:
            subprocess.Popen(args, close_fds=True, start_new_session=True)
            logger.info("Poweroff requested via %s", args)
            return True
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.debug("poweroff %s: %s", args, e)
    logger.warning(
        "Local poweroff not started — allow systemctl poweroff or passwordless sudo."
    )
    return False


# ---------------------------------------------------------------------------
# PulseAudio helpers (speaker / mic routing on the kiosk host session)
# ---------------------------------------------------------------------------

def pactl_available() -> bool:
    return shutil.which("pactl") is not None


def _wpctl_set_volume(pct: int, target: str = "@DEFAULT_AUDIO_SINK@") -> bool:
    """Set volume via wpctl (PipeWire native). Returns True on success."""
    exe = shutil.which("wpctl")
    if not exe:
        return False
    try:
        # wpctl uses 0.0–1.0 scale
        vol = round(pct / 100.0, 2)
        r = subprocess.run(
            [exe, "set-volume", target, str(vol)],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if r.returncode == 0:
            logger.info("wpctl set-volume %s %s", target, vol)
            return True
    except Exception as e:
        logger.debug("wpctl set-volume: %s", e)
    return False


def _pactl_runs() -> bool:
    """Check whether pactl can reach the PulseAudio/PipeWire daemon (no permanent cache)."""
    exe = shutil.which("pactl")
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, "info"], capture_output=True, timeout=2, check=False
        )
        return r.returncode == 0
    except Exception:
        return False


def _amixer_master_controls(device: str | None = None) -> list[str]:
    """Return ALSA playback control names that exist on this device/card."""
    exe = shutil.which("amixer")
    if not exe:
        return []
    try:
        cmd = [exe]
        if device:
            cmd += ["-D", device]
        cmd += ["scontrols"]
        out = subprocess.check_output(
            cmd, timeout=3, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
        names: list[str] = []
        for line in out.splitlines():
            if "'" in line:
                name = line.split("'")[1]
                names.append(name)
        for preferred in ("Master", "PCM", "Speaker", "Headphone"):
            if preferred in names:
                return [preferred]
        return names[:1]
    except Exception:
        return []


def _amixer_set_volume_pct(pct: int) -> bool:
    """ALSA fallback: set playback volume. Returns True on success."""
    exe = shutil.which("amixer")
    if not exe:
        return False
    # Try: default device, then pulse (PipeWire ALSA plugin), then hw:0
    for device in (None, "pulse", "default"):
        controls = _amixer_master_controls(device)
        if not controls:
            controls = ["Master"]
        for ctrl in controls:
            try:
                cmd = [exe]
                if device:
                    cmd += ["-D", device]
                cmd += ["set", ctrl, f"{pct}%"]
                r = subprocess.run(cmd, capture_output=True, timeout=3, check=False)
                if r.returncode == 0:
                    logger.info("amixer -D %s set %s %s%%", device or "default", ctrl, pct)
                    return True
            except Exception as e:
                logger.debug("amixer -D %s set %s: %s", device, ctrl, e)
    return False


def set_sink_volume_pct(pct: int) -> None:
    """Set default output sink volume 0–100%.
    Priority: wpctl (PipeWire) → pactl (PulseAudio) → amixer (ALSA)."""
    pct = max(0, min(100, int(pct)))
    if _wpctl_set_volume(pct, "@DEFAULT_AUDIO_SINK@"):
        return
    exe = shutil.which("pactl")
    if exe and _pactl_runs():
        try:
            subprocess.run(
                [exe, "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                capture_output=True,
                timeout=3,
                check=False,
            )
            return
        except Exception as e:
            logger.debug("set_sink_volume_pct pactl: %s", e)
    _amixer_set_volume_pct(pct)


def set_source_volume_pct(pct: int) -> None:
    """Set default capture source volume / gain 0–150%.
    Priority: wpctl (PipeWire) → pactl (PulseAudio) → amixer (ALSA)."""
    pct = max(0, min(150, int(pct)))
    if _wpctl_set_volume(pct, "@DEFAULT_AUDIO_SOURCE@"):
        return
    exe = shutil.which("pactl")
    if exe and _pactl_runs():
        try:
            subprocess.run(
                [exe, "set-source-volume", "@DEFAULT_SOURCE@", f"{pct}%"],
                capture_output=True,
                timeout=3,
                check=False,
            )
            return
        except Exception as e:
            logger.debug("set_source_volume_pct pactl: %s", e)
    exe_amixer = shutil.which("amixer")
    if exe_amixer:
        try:
            subprocess.run(
                [exe_amixer, "set", "Capture", f"{pct}%"],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except Exception as e:
            logger.debug("set_source_volume_pct amixer: %s", e)


def _pactl_descriptions(object_type: str) -> dict[str, str]:
    """Return {name: description} from ``pactl list <object_type>`` verbose output.

    Parses blocks like:
      Sink #0
          Name: alsa_output.pci-0000_00_1f.3.analog-stereo
          Description: Built-in Audio Analog Stereo
    """
    exe = shutil.which("pactl")
    if not exe:
        return {}
    try:
        out = subprocess.check_output(
            [exe, "list", object_type], timeout=6, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
    except Exception:
        return {}
    result: dict[str, str] = {}
    current_name = ""
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped[len("Name:"):].strip()
        elif stripped.startswith("Description:") and current_name:
            result[current_name] = stripped[len("Description:"):].strip()
    return result


def list_pulse_sinks() -> list[tuple[str, str]]:
    """Return [(sink_name, friendly_description), …]

    Uses ``pactl list sinks short`` for the name list and the verbose
    ``pactl list sinks`` for human-readable descriptions (e.g.
    "Built-in Audio Analog Stereo").  The STATE column from the short
    output (SUSPENDED / RUNNING / IDLE) is intentionally ignored.
    """
    exe = shutil.which("pactl")
    if not exe:
        return []
    try:
        out = subprocess.check_output(
            [exe, "list", "sinks", "short"], timeout=4, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
    except Exception:
        return []
    descriptions = _pactl_descriptions("sinks")
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip():
            name = parts[1].strip()
            desc = descriptions.get(name) or name
            rows.append((name, desc))
    return rows


def list_pulse_sources() -> list[tuple[str, str]]:
    """Return [(source_name, friendly_description), …]

    Monitor sources (virtual loopbacks) are excluded.
    """
    exe = shutil.which("pactl")
    if not exe:
        return []
    try:
        out = subprocess.check_output(
            [exe, "list", "sources", "short"], timeout=4, stderr=subprocess.DEVNULL
        ).decode(errors="ignore")
    except Exception:
        return []
    descriptions = _pactl_descriptions("sources")
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip() and ".monitor" not in parts[1]:
            name = parts[1].strip()
            desc = descriptions.get(name) or name
            rows.append((name, desc))
    return rows


def set_default_sink(name: str) -> bool:
    exe = shutil.which("pactl")
    if not exe or not (name or "").strip():
        return False
    try:
        r = subprocess.run(
            [exe, "set-default-sink", name.strip()],
            capture_output=True,
            timeout=4,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def set_default_source(name: str) -> bool:
    exe = shutil.which("pactl")
    if not exe or not (name or "").strip():
        return False
    try:
        r = subprocess.run(
            [exe, "set-default-source", name.strip()],
            capture_output=True,
            timeout=4,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def get_usb_devices_one_liners() -> list[str]:
    """Best-effort USB device list — ``lsusb`` or sysfs."""
    exe = shutil.which("lsusb")
    if exe:
        try:
            out = subprocess.check_output(
                [exe], timeout=5, stderr=subprocess.DEVNULL
            ).decode(errors="ignore")
            return [ln.strip() for ln in out.splitlines() if ln.strip()]
        except Exception:
            pass
    root = Path("/sys/bus/usb/devices")
    if not root.exists():
        return []
    rows: list[str] = []
    try:
        for p in sorted(root.iterdir()):
            prod = p / "product"
            manu = p / "manufacturer"
            if prod.is_file():
                try:
                    pr = prod.read_text().strip()
                    mf = manu.read_text().strip() if manu.is_file() else ""
                    rows.append((f"{mf} {pr}").strip() or p.name)
                except OSError:
                    rows.append(p.name)
    except Exception:
        pass
    return sorted(set(rows))[:48]
