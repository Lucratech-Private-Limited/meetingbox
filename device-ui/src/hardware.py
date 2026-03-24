"""
Hardware utilities for Linux display power and brightness control.

Uses the standard Linux backlight sysfs interface when available and
falls back to X11 DPMS commands for screen power control.
"""

import logging
import os
import subprocess
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


def set_brightness(level: str) -> None:
    """Set display brightness. level: 'low', 'medium', or 'high'."""
    fraction = BRIGHTNESS_MAP.get(level, 1.0)
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
