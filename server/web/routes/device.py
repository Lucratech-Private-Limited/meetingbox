"""
Device Management Routes

Endpoints used by the device-ui appliance interface.
Manages: settings, WiFi, updates, system device info.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import psutil
import redis

from auth import get_optional_user, get_optional_actor, get_current_device_row
from assistant_service import list_assistant_queue_for_briefing
from database import get_connection

router = APIRouter()
logger = logging.getLogger(__name__)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
_redis_client = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    return _redis_client


def _redis_publish_commands(payload: dict) -> None:
    """Publish to the audio / appliance command channel; fail loudly if Redis is down."""
    try:
        _get_redis().publish("commands", json.dumps(payload))
    except redis.RedisError as exc:
        logger.exception("Redis publish failed")
        raise HTTPException(
            status_code=503,
            detail="Device command bus unavailable (cannot reach Redis). Is the Redis service running?",
        ) from exc


# Persistent settings file on disk
SETTINGS_FILE = Path(os.getenv("DEVICE_SETTINGS_PATH", "/data/config/device_settings.json"))
SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
SETUP_COMPLETE_FILE = SETTINGS_FILE.parent / ".setup_complete"
PROFILES_FILE = SETTINGS_FILE.parent / "device_profiles.json"


def _all_setup_marker_paths_for_reset() -> list[Path]:
    """Every `.setup_complete` path we know about — factory reset must clear all."""
    out: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)

    add(SETUP_COMPLETE_FILE)
    for p in (
        Path("/data/config/.setup_complete"),
        Path("/opt/meetingbox/data/config/.setup_complete"),
        Path("/opt/meetingbox/.setup_complete"),
    ):
        add(p)
    root = (os.environ.get("MEETINGBOX_PROJECT_ROOT") or "").strip()
    if root:
        add(Path(root) / "data" / "config" / ".setup_complete")
    return out

# NetworkManager WiFi interface (override if not wlan0, e.g. wlp2s0)
WIFI_IFACE = os.getenv("WIFI_INTERFACE", "wlan0")

FIRMWARE_VERSION = os.getenv("FIRMWARE_VERSION", "1.0.0")
DEVICE_MODEL = "MeetingBox v1.0"

RECORDINGS_DIR_PATH = Path(os.getenv("RECORDINGS_DIR", "/data/audio/recordings"))
TEMP_SEGMENTS_PATH = Path(os.getenv("TEMP_SEGMENTS_DIR", "/data/audio/temp"))
TRANSCRIPTS_FALLBACK_DIR = Path(
    os.getenv("TRANSCRIPTS_FALLBACK_DIR", "/data/transcripts")
)

# Boot time for uptime calculation
_BOOT_TIME = time.time()

_DEFAULT_REBOOT_HELPER = "/usr/local/bin/meetingbox-host-reboot"
_DEFAULT_POWEROFF_HELPER = "/usr/local/bin/meetingbox-host-poweroff"


def _trigger_system_reboot() -> bool:
    """
    Reboot the appliance. The slim web image has no sudo; in Docker use
    MEETINGBOX_REBOOT_HELPER (host nsenter script) or MEETINGBOX_REBOOT_CMD.
    """
    env_cmd = (os.environ.get("MEETINGBOX_REBOOT_CMD") or "").strip()
    if env_cmd:
        try:
            subprocess.Popen(env_cmd, shell=True, close_fds=True)
            return True
        except Exception as e:
            logger.error("MEETINGBOX_REBOOT_CMD failed: %s", e)

    helper = (os.environ.get("MEETINGBOX_REBOOT_HELPER") or "").strip()
    for path in {h for h in (helper, _DEFAULT_REBOOT_HELPER) if h}:
        p = Path(path)
        if p.is_file():
            try:
                subprocess.Popen(["/bin/sh", str(p)], close_fds=True)
                return True
            except Exception as e:
                logger.error("reboot helper %s failed: %s", path, e)

    sudo = shutil.which("sudo")
    if sudo:
        for args in (["sudo", "-n", "reboot"], ["sudo", "-n", "shutdown", "-r", "now"]):
            try:
                subprocess.Popen(args, close_fds=True)
                return True
            except Exception as e:
                logger.debug("reboot attempt %s: %s", args, e)

    if os.geteuid() == 0:
        rb = shutil.which("reboot") or "/sbin/reboot"
        try:
            subprocess.Popen([rb], close_fds=True)
            return True
        except Exception as e:
            logger.error("reboot as root failed: %s", e)

    logger.warning(
        "System reboot was not started: configure MEETINGBOX_REBOOT_HELPER in "
        "Docker, set MEETINGBOX_REBOOT_CMD, or grant passwordless sudo reboot "
        "on the host."
    )
    return False


def _trigger_system_poweroff() -> bool:
    """
    Power off the appliance. Same deployment notes as reboot: use a host helper
    from Docker (MEETINGBOX_POWEROFF_HELPER) or MEETINGBOX_POWEROFF_CMD / sudo.
    """
    env_cmd = (os.environ.get("MEETINGBOX_POWEROFF_CMD") or "").strip()
    if env_cmd:
        try:
            subprocess.Popen(env_cmd, shell=True, close_fds=True)
            return True
        except Exception as e:
            logger.error("MEETINGBOX_POWEROFF_CMD failed: %s", e)

    helper = (os.environ.get("MEETINGBOX_POWEROFF_HELPER") or "").strip()
    for path in {h for h in (helper, _DEFAULT_POWEROFF_HELPER) if h}:
        p = Path(path)
        if p.is_file():
            try:
                subprocess.Popen(["/bin/sh", str(p)], close_fds=True)
                return True
            except Exception as e:
                logger.error("poweroff helper %s failed: %s", path, e)

    sudo = shutil.which("sudo")
    if sudo:
        for args in (
            ["sudo", "-n", "poweroff"],
            ["sudo", "-n", "shutdown", "-h", "now"],
        ):
            try:
                subprocess.Popen(args, close_fds=True)
                return True
            except Exception as e:
                logger.debug("poweroff attempt %s: %s", args, e)

    if os.geteuid() == 0:
        po = shutil.which("poweroff") or "/sbin/poweroff"
        try:
            subprocess.Popen([po], close_fds=True)
            return True
        except Exception as e:
            logger.error("poweroff as root failed: %s", e)

    logger.warning(
        "System poweroff was not started: configure MEETINGBOX_POWEROFF_HELPER, "
        "MEETINGBOX_POWEROFF_CMD, or passwordless sudo poweroff on the host."
    )
    return False


def _nmcli_run(args: list, timeout: float = 30) -> subprocess.CompletedProcess:
    """Run nmcli; on PolicyKit denial retry with sudo -n (see scripts/sudoers / polkit)."""
    res = subprocess.run(
        ["nmcli", *args], capture_output=True, text=True, timeout=timeout
    )
    combined = ((res.stderr or "") + (res.stdout or "")).lower()
    priv = any(
        s in combined
        for s in (
            "insufficient privileges",
            "not authorized",
            "permission denied",
            "not allowed to",
            "polkit",
        )
    )
    if res.returncode != 0 and priv and shutil.which("sudo"):
        res2 = subprocess.run(
            ["sudo", "-n", "nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        sudo_msg = ((res2.stderr or "") + (res2.stdout or "")).lower()
        # If sudo itself failed because it cannot prompt, keep original
        # NetworkManager permission error for clearer UI messaging.
        if (
            res2.returncode != 0
            and any(
                s in sudo_msg
                for s in (
                    "a password is required",
                    "password is required",
                    "terminal is required",
                    "no tty present",
                    "sudo: a password",
                )
            )
        ):
            return res
        return res2
    return res


def _timedatectl_run(args: list[str], timeout: float = 25) -> subprocess.CompletedProcess:
    res = subprocess.run(
        ["timedatectl", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if res.returncode != 0 and shutil.which("sudo"):
        res2 = subprocess.run(
            ["sudo", "-n", "timedatectl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return res2
    return res


def _bluetoothctl_run(args: list[str], timeout: float = 35) -> subprocess.CompletedProcess:
    bt = shutil.which("bluetoothctl")
    if not bt:
        return subprocess.CompletedProcess(args, 127, "", "bluetoothctl not found")
    res = subprocess.run(
        [bt, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if res.returncode != 0 and shutil.which("sudo"):
        return subprocess.run(
            ["sudo", "-n", bt, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return res


def _safe_dir_size_bytes(top: Path, cap_files: int = 40_000) -> int:
    if not top.is_dir():
        return 0
    total = 0
    n = 0
    try:
        for p in top.rglob("*"):
            n += 1
            if n > cap_files:
                break
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
    except Exception:
        pass
    return total


def _http_probe_ms(url: str, timeout: float = 3.0) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(128)
            code = resp.getcode()
            ok = bool(code and 200 <= int(code) < 400)
        return ok, (time.perf_counter() - t0) * 1000.0
    except Exception:
        return False, (time.perf_counter() - t0) * 1000.0


# ======================================================================
# HELPERS
# ======================================================================

def _load_settings() -> dict:
    """Load device settings from disk, with defaults."""
    defaults = {
        "device_name": "MeetingBox",
        "room_label": "",
        "timezone": "UTC",
        "auto_delete_days": "never",
        "brightness": "high",
        "screen_timeout": "never",
        "idle_screen_timeout": "30",
        "privacy_mode": False,
        "auto_record": False,
        "auto_summarize": False,
        "transcript_storage_enabled": True,
        "recording_consent_reminder": False,
        "voice_wake_phrase": "hey buddy",
        "voice_realtime_assistant": False,
        "voice_assistant_enabled": True,
        "assistant_speech_volume": 85,
        "system_output_volume": 85,
        "mic_input_volume": 90,
        "notification_enabled": True,
        "meeting_reminder_minutes": 10,
        "dnd_enabled": False,
        "dnd_start": "22:00",
        "dnd_end": "07:00",
        "email_notifications_enabled": True,
        "assistant_notifications_enabled": True,
        "auto_update_enabled": False,
        "update_channel": "stable",
        "session_timeout_minutes": 0,
        "font_size": "medium",
        "screen_always_on_recording": False,
        "meeting_chime_enabled": True,
        "alert_sounds_enabled": True,
        "trusted_wifi_ssids": "",
        "settings_pin_hash": "",
        "settings_pin_salt": "",
    }
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE) as f:
                saved = json.load(f)
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def _save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


def _get_wifi_info() -> dict:
    """Get current WiFi SSID and signal on Linux."""
    ssid = ""
    signal = 0
    try:
        result = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            ssid = result.stdout.strip()
        sig_result = subprocess.run(
            ["iwconfig"], capture_output=True, text=True, timeout=5)
        for line in sig_result.stdout.splitlines():
            if "Signal level" in line:
                # Parse "Signal level=-XX dBm"
                idx = line.index("Signal level=")
                val = line[idx + 13:].split()[0].replace("dBm", "")
                dbm = int(val)
                # Convert dBm to percentage (rough)
                signal = max(0, min(100, 2 * (dbm + 100)))
    except Exception:
        pass
    return {"ssid": ssid, "signal": signal}


def _get_ip_address() -> str:
    """Get primary IP address."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _get_serial() -> str:
    """Read Raspberry Pi serial number."""
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("Serial"):
                    return "MB-" + line.split(":")[1].strip()[-8:]
    except Exception:
        pass
    return "MB-00000000"


# ======================================================================
# PAIRING (device Bearer token required)
# ======================================================================


@router.get("/pairing-status")
async def device_pairing_status(device: dict = Depends(get_current_device_row)):
    """Return 401 if this device token was revoked (e.g. unpaired from dashboard)."""
    return {
        "paired": True,
        "device_id": device["id"],
        "device_name": device.get("device_name"),
        "owner_email": device.get("owner_email"),
    }


@router.post("/unpair-self")
async def device_unpair_self(device: dict = Depends(get_current_device_row)):
    """Unlink this appliance from its owner account (device-initiated)."""
    now = datetime.utcnow().isoformat()
    device_id = device["id"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE devices
            SET status = 'unpaired', auth_token_hash = NULL, unpaired_at = ?
            WHERE id = ?
            """,
            (now, device_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status": "unpaired", "device_id": device_id}


def _normalize_hhmmss(time_raw: str) -> str:
    s = (time_raw or "10:00").strip()
    if s.count(":") == 2:
        parts = s.split(":")
    else:
        parts = s.split(":") + ["0"]
    try:
        h = max(0, min(23, int(parts[0])))
        m = max(0, min(59, int(parts[1])))
        return f"{h:02d}:{m:02d}:00"
    except (ValueError, IndexError):
        return "10:00:00"


def _latest_executed_calendar_meeting_for_scope(
    scope_sql: str,
    scope_params: list[Any],
) -> Optional[dict[str, Any]]:
    """
    Most recently executed Google Calendar action created from MeetingBox for this user/device scope.
    Uses stored payload (suggested_date/time, calendar_link) — not the live Google Calendar feed.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT a.title, a.payload
            FROM actions a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.status = 'executed'
              AND lower(coalesce(trim(a.connector_target), '')) = 'calendar'
              AND {scope_sql}
            ORDER BY datetime(COALESCE(a.executed_at, a.created_at)) DESC
            LIMIT 1
            """,
            scope_params,
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    title_db, payload_raw = row[0], row[1]
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(payload_raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    title = str(payload.get("title") or title_db or "Calendar event").strip()
    date_s = str(payload.get("suggested_date") or "").strip()[:10]
    time_norm = _normalize_hhmmss(str(payload.get("suggested_time") or "10:00"))
    tz_name = str(payload.get("timezone") or "").strip()
    start_out = ""
    if date_s and len(date_s) >= 10:
        try:
            if tz_name:
                try:
                    dt = datetime.strptime(f"{date_s} {time_norm}", "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=ZoneInfo(tz_name))
                    start_out = dt.isoformat()
                except Exception:
                    start_out = f"{date_s}T{time_norm}"
            else:
                start_out = f"{date_s}T{time_norm}"
        except Exception:
            start_out = f"{date_s}T{time_norm}" if date_s else ""
    return {
        "title": title,
        "start": start_out,
        "end": "",
        "html_link": payload.get("calendar_link"),
        "source": "executed_calendar_action",
    }


@router.get("/home-summary")
async def device_home_summary(
    current_actor: Optional[dict] = Depends(get_optional_actor),
) -> dict[str, Any]:
    """
    Last executed MeetingBox calendar action (created on Google Calendar via dashboard/device)
    plus pending action counts for the signed-in user or paired device.
    """
    empty = {
        "next_meeting": None,
        "pending_actions_today": 0,
        "pending_actions_total": 0,
        "assistant_queue": {"count_pending": 0, "items": []},
    }
    if not current_actor:
        return empty
    if current_actor["type"] == "device":
        user_id = current_actor["device"].get("owner_user_id") or current_actor["user"]["id"]
        device_id = current_actor["device"]["id"]
        scope_sql = "(m.user_id = ? OR m.device_id = ?)"
        scope_params: list[Any] = [user_id, device_id]
    else:
        user_id = current_actor["user"]["id"]
        scope_sql = "m.user_id = ?"
        scope_params = [user_id]

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*) FROM actions a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.status = 'pending'
              AND lower(coalesce(trim(a.connector_target), '')) IN ('gmail', 'calendar')
              AND {scope_sql}
            """,
            scope_params,
        )
        total = int(cur.fetchone()[0])
        cur.execute(
            f"""
            SELECT COUNT(*) FROM actions a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.status = 'pending'
              AND lower(coalesce(trim(a.connector_target), '')) IN ('gmail', 'calendar')
              AND substr(a.created_at, 1, 10) = date('now')
              AND {scope_sql}
            """,
            scope_params,
        )
        today_cnt = int(cur.fetchone()[0])
    finally:
        conn.close()

    assistant_snap = list_assistant_queue_for_briefing(user_id, limit=12)

    return {
        "next_meeting": _latest_executed_calendar_meeting_for_scope(scope_sql, scope_params),
        "pending_actions_today": today_cnt,
        "pending_actions_total": total,
        "assistant_queue": assistant_snap,
    }


# ======================================================================
# SETTINGS
# ======================================================================

@router.get("/settings")
async def get_settings(current_user: Optional[dict] = Depends(get_optional_user)):
    """Return current device settings."""
    return _load_settings()


@router.post("/mic-test/start")
async def start_mic_test(current_actor: Optional[dict] = Depends(get_optional_actor)):
    """Start live microphone level stream for device UI test screen."""
    payload = {"action": "start_mic_test"}
    if current_actor and current_actor.get("type") == "device":
        payload["device_id"] = current_actor["device"]["id"]
    _redis_publish_commands(payload)
    return {"status": "mic_test_started"}


@router.post("/mic-test/stop")
async def stop_mic_test(current_actor: Optional[dict] = Depends(get_optional_actor)):
    """Stop live microphone level stream for device UI test screen."""
    payload = {"action": "stop_mic_test"}
    if current_actor and current_actor.get("type") == "device":
        payload["device_id"] = current_actor["device"]["id"]
    _redis_publish_commands(payload)
    return {"status": "mic_test_stopped"}


class SettingsUpdate(BaseModel):
    device_name: Optional[str] = None
    room_label: Optional[str] = None
    timezone: Optional[str] = None
    auto_delete_days: Optional[str] = None
    brightness: Optional[str] = None
    screen_timeout: Optional[str] = None
    idle_screen_timeout: Optional[str] = None
    privacy_mode: Optional[bool] = None
    auto_record: Optional[bool] = None
    auto_summarize: Optional[bool] = None
    transcript_storage_enabled: Optional[bool] = None
    recording_consent_reminder: Optional[bool] = None
    voice_wake_phrase: Optional[str] = None
    voice_realtime_assistant: Optional[bool] = None
    voice_assistant_enabled: Optional[bool] = None
    assistant_speech_volume: Optional[int] = None  # 0–100 → espeak amplitude
    system_output_volume: Optional[int] = None
    mic_input_volume: Optional[int] = None
    notification_enabled: Optional[bool] = None
    meeting_reminder_minutes: Optional[int] = None
    dnd_enabled: Optional[bool] = None
    dnd_start: Optional[str] = None
    dnd_end: Optional[str] = None
    email_notifications_enabled: Optional[bool] = None
    assistant_notifications_enabled: Optional[bool] = None
    auto_update_enabled: Optional[bool] = None
    update_channel: Optional[str] = None
    session_timeout_minutes: Optional[int] = None
    font_size: Optional[str] = None
    screen_always_on_recording: Optional[bool] = None
    meeting_chime_enabled: Optional[bool] = None
    alert_sounds_enabled: Optional[bool] = None
    trusted_wifi_ssids: Optional[str] = None
    settings_pin_hash: Optional[str] = None
    settings_pin_salt: Optional[str] = None
    action: Optional[str] = None  # restart / poweroff / factory_reset


@router.patch("/settings")
async def update_settings(body: SettingsUpdate, current_user: Optional[dict] = Depends(get_optional_user)):
    """Update one or more device settings."""
    current = _load_settings()
    updates = body.dict(exclude_none=True)

    vol = updates.get("assistant_speech_volume")
    if vol is not None:
        try:
            updates["assistant_speech_volume"] = max(0, min(100, int(vol)))
        except (TypeError, ValueError):
            updates.pop("assistant_speech_volume", None)

    for key, lo, hi in (
        ("system_output_volume", 0, 100),
        ("mic_input_volume", 0, 150),
    ):
        vv = updates.get(key)
        if vv is None:
            continue
        try:
            updates[key] = max(lo, min(hi, int(vv)))
        except (TypeError, ValueError):
            updates.pop(key, None)

    mm = updates.get("meeting_reminder_minutes")
    if mm is not None:
        try:
            updates["meeting_reminder_minutes"] = max(
                0, min(120, int(mm))
            )
        except (TypeError, ValueError):
            updates.pop("meeting_reminder_minutes", None)

    st = updates.get("session_timeout_minutes")
    if st is not None:
        try:
            updates["session_timeout_minutes"] = max(
                0, min(24 * 60, int(st))
            )
        except (TypeError, ValueError):
            updates.pop("session_timeout_minutes", None)

    # Handle special actions
    action = updates.pop("action", None)
    if action == "restart":
        ok = _trigger_system_reboot()
        return {"status": "restarting", "host_reboot_initiated": ok}

    if action == "poweroff":
        ok = _trigger_system_poweroff()
        return {"status": "powering_off", "host_poweroff_initiated": ok}

    if action == "factory_reset":
        # Delete settings, profiles, pairing token, every setup marker, then reboot
        try:
            SETTINGS_FILE.unlink(missing_ok=True)
            PROFILES_FILE.unlink(missing_ok=True)
            (SETTINGS_FILE.parent / "device_auth_token").unlink(missing_ok=True)
            for p in _all_setup_marker_paths_for_reset():
                try:
                    p.unlink(missing_ok=True)
                except OSError as err:
                    logger.warning("factory_reset: could not remove %s: %s", p, err)
        except Exception as e:
            logger.error("factory_reset file cleanup: %s", e)
        ok = _trigger_system_reboot()
        return {"status": "resetting", "host_reboot_initiated": ok}

    current.update(updates)
    _save_settings(current)
    return current


class SetupCompleteBody(BaseModel):
    """
    Payload for finishing first-boot after the device joins a Wi‑Fi LAN.

    All fields are optional; defaults are fine if SSID is unknown.
    - wifi_ssid: connected network name (informational)
    - onboarding_flow: fixed tag for analytics / support (default wifi_on_device_v1)
    """

    wifi_ssid: str = ""
    onboarding_flow: str = "wifi_on_device_v1"


def finalize_first_boot_setup(body: SetupCompleteBody) -> dict:
    """
    Write `.setup_complete` next to `device_settings.json`.
    Registered as POST /api/device/setup-complete on the main FastAPI app so the
    SPA GET catch‑all cannot cause 405 on this path.
    """
    settings = _load_settings()
    meta = {
        "version": 1,
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_name": settings.get("device_name", "MeetingBox"),
        "wifi_ssid": (body.wifi_ssid or "").strip(),
        "onboarding_flow": body.onboarding_flow or "wifi_on_device_v1",
    }
    try:
        SETUP_COMPLETE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SETUP_COMPLETE_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not write setup marker: {e}")
    return {"status": "ok", "metadata": meta}


# ======================================================================
# DEVICE INFO (extended system info for OLED UI)
# ======================================================================

@router.get("/device-info")
async def device_info(current_user: Optional[dict] = Depends(get_optional_user)):
    """
    Extended system info for the OLED display.
    Returns everything the device-ui HomeScreen footer + Settings need.
    """
    settings = _load_settings()
    wifi = _get_wifi_info()
    disk = psutil.disk_usage("/")

    meetings_count = 0
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM meetings")
            meetings_count = cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        pass

    uptime_seconds = int(time.time() - psutil.boot_time())

    return {
        "device_name": settings.get("device_name", "MeetingBox"),
        "serial_number": _get_serial(),
        "firmware_version": FIRMWARE_VERSION,
        "ip_address": _get_ip_address(),
        "wifi_ssid": wifi["ssid"],
        "wifi_signal": wifi["signal"],
        "storage_used": disk.used,
        "storage_total": disk.total,
        "uptime": uptime_seconds,
        "meetings_count": meetings_count,
        "setup_complete": SETUP_COMPLETE_FILE.exists(),
    }


# ======================================================================
# WIFI
# ======================================================================

@router.get("/wifi/scan")
async def wifi_scan(current_user: Optional[dict] = Depends(get_optional_user)):
    """Scan for available WiFi networks."""
    networks = []
    try:
        result = _nmcli_run(
            [
                "-m",
                "multiline",
                "-f",
                "SSID,SIGNAL,SECURITY,IN-USE",
                "dev",
                "wifi",
                "list",
            ],
            timeout=15,
        )
        if result.returncode == 0:
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
                networks.append({
                    "ssid": ssid,
                    "signal_strength": signal,
                    "security": sec_raw or "open",
                    "connected": in_use == "*",
                })

            for line in result.stdout.splitlines():
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
    except Exception as e:
        # Fallback: return current connection only
        wifi = _get_wifi_info()
        if wifi["ssid"]:
            networks.append({
                "ssid": wifi["ssid"],
                "signal_strength": wifi["signal"],
                "security": "wpa2",
                "connected": True,
            })
    if not networks:
        wifi = _get_wifi_info()
        if wifi["ssid"]:
            networks.append({
                "ssid": wifi["ssid"],
                "signal_strength": wifi["signal"],
                "security": "wpa2",
                "connected": True,
            })
    return networks


class WiFiConnect(BaseModel):
    ssid: str
    password: Optional[str] = None


@router.post("/wifi/connect")
async def wifi_connect(body: WiFiConnect, current_user: Optional[dict] = Depends(get_optional_user)):
    """Connect to a WiFi network using NetworkManager."""
    try:
        # Remove any stale connection profile first
        _nmcli_run(["connection", "delete", body.ssid], timeout=10)

        if body.password:
            # Explicit creation with security type to avoid key-mgmt bug
            _nmcli_run(
                [
                    "connection",
                    "add",
                    "type",
                    "wifi",
                    "ifname",
                    WIFI_IFACE,
                    "con-name",
                    body.ssid,
                    "ssid",
                    body.ssid,
                    "--",
                    "wifi-sec.key-mgmt",
                    "wpa-psk",
                    "wifi-sec.psk",
                    body.password,
                ],
                timeout=15,
            )
            result = _nmcli_run(["connection", "up", body.ssid], timeout=30)
        else:
            result = _nmcli_run(["dev", "wifi", "connect", body.ssid], timeout=30)

        if result.returncode == 0:
            return {"status": "connected", "message": f"Connected to {body.ssid}"}
        else:
            _nmcli_run(["connection", "delete", body.ssid], timeout=10)
            return {"status": "failed", "message": result.stderr.strip() or result.stdout.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wifi/disconnect")
async def wifi_disconnect(current_user: Optional[dict] = Depends(get_optional_user)):
    """Disconnect from current WiFi."""
    try:
        _nmcli_run(["dev", "disconnect", WIFI_IFACE], timeout=10)
        return {"status": "disconnected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- Additional host-integration endpoints (called from device-ui or dashboards) ---
class WiFiRadioBody(BaseModel):
    enabled: bool


class WiFiForgetBody(BaseModel):
    connection_name: str


class TimezoneBody(BaseModel):
    timezone: str


class DateTimeBody(BaseModel):
    iso_datetime: str


class BluetoothRadioBody(BaseModel):
    enabled: bool


class BluetoothPairBody(BaseModel):
    mac: str


class FeedbackBody(BaseModel):
    message: str


@router.post("/wifi/radio")
async def wifi_radio_set(body: WiFiRadioBody):
    """Enable/disable Wi-Fi radio on the NetworkManager host."""
    arg = "on" if body.enabled else "off"
    r = _nmcli_run(["radio", "wifi", arg], timeout=20)
    ok = r.returncode == 0 or "enabled" in (r.stdout or "").lower()
    return {
        "ok": ok,
        "message": (r.stderr or r.stdout or "").strip()[:320],
    }


@router.get("/wifi/saved")
async def wifi_saved_connections():
    """List saved Wi-Fi connection profile names."""
    r = _nmcli_run(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=20)
    if r.returncode != 0:
        return {"connections": [], "message": (r.stderr or "").strip()[:320]}
    names: list[str] = []
    for line in r.stdout.splitlines():
        if ":" not in line:
            continue
        name, typ = line.split(":", 1)
        low = typ.strip().lower()
        nm = name.strip()
        if not nm:
            continue
        if "wireless" in low or "wifi" in low or low == "802-11-wireless":
            names.append(nm)
    return {"connections": sorted(set(names)), "message": ""}


@router.post("/wifi/forget")
async def wifi_forget_saved(body: WiFiForgetBody):
    """Delete a saved Wi-Fi NM connection profile."""
    nm = body.connection_name.strip()
    if not nm:
        raise HTTPException(status_code=400, detail="connection_name required")
    r = _nmcli_run(["connection", "delete", nm], timeout=25)
    if r.returncode == 0:
        return {"ok": True}
    return {
        "ok": False,
        "message": (r.stderr or r.stdout or "").strip()[:400],
    }


@router.post("/system/timezone")
async def device_set_timezone(body: TimezoneBody):
    tz = body.timezone.strip()
    if not tz:
        raise HTTPException(status_code=400, detail="timezone required")
    r = _timedatectl_run(["set-timezone", tz])
    ok = r.returncode == 0
    settings = _load_settings()
    if ok:
        settings["timezone"] = tz
        _save_settings(settings)
    return {"ok": ok, "message": (r.stderr or r.stdout or "").strip()[:400]}


@router.post("/system/datetime")
async def device_set_datetime(body: DateTimeBody):
    when = body.iso_datetime.strip()
    if not when:
        raise HTTPException(status_code=400, detail="iso_datetime required")
    r = _timedatectl_run(["set-time", when])
    return {"ok": r.returncode == 0, "message": (r.stderr or r.stdout or "").strip()[:400]}


@router.post("/bluetooth/radio")
async def bluetooth_radio(body: BluetoothRadioBody):
    arg = "on" if body.enabled else "off"
    r = _bluetoothctl_run(["power", arg])
    return {"ok": r.returncode == 0, "message": (r.stderr or r.stdout or "").strip()[:400]}


@router.get("/bluetooth/devices")
async def bluetooth_devices():
    """Paired bluetooth devices via bluetoothctl (best-effort)."""
    r = _bluetoothctl_run(["devices", "Paired"], timeout=20)
    rows: list[dict[str, str]] = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = line.split(None, 2)
        if len(parts) >= 3:
            rows.append({"mac": parts[1], "name": parts[2]})
    return {"paired": rows, "ok": r.returncode == 0, "stderr": r.stderr.strip()[:200]}


@router.post("/bluetooth/pair")
async def bluetooth_pair(body: BluetoothPairBody):
    mac = body.mac.strip()
    if not mac:
        raise HTTPException(status_code=400, detail="mac required")
    r = _bluetoothctl_run(["pair", mac], timeout=45)
    return {"ok": r.returncode == 0, "message": (r.stderr or r.stdout or "").strip()[:400]}


@router.get("/storage-breakdown")
async def storage_breakdown():
    rec = _safe_dir_size_bytes(RECORDINGS_DIR_PATH)
    tmp = _safe_dir_size_bytes(TEMP_SEGMENTS_PATH)
    tr = _safe_dir_size_bytes(TRANSCRIPTS_FALLBACK_DIR)
    cache = _safe_dir_size_bytes(Path(os.getenv("MEETINGBOX_CACHE_DIR", "/tmp/meetingbox-cache")))
    return {
        "recordings_gb": round(rec / (1024**3), 3),
        "temp_segments_gb": round(tmp / (1024**3), 3),
        "transcripts_cache_gb": round(tr / (1024**3), 3),
        "app_cache_gb": round(cache / (1024**3), 3),
        "meetings_gb": round(rec / (1024**3), 3),
    }


@router.post("/clear-cache")
async def clear_device_cache_endpoint():
    n = 0
    roots = [
        Path(os.getenv("MEETINGBOX_CACHE_DIR", "/tmp/meetingbox-cache")),
        TEMP_SEGMENTS_PATH,
    ]
    for root in roots:
        if root.is_dir():
            try:
                for child in root.iterdir():
                    try:
                        if child.is_file():
                            child.unlink(missing_ok=True)
                            n += 1
                        elif child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                            n += 1
                    except OSError:
                        continue
            except Exception:
                pass
    return {"ok": True, "entries_removed": n}


@router.post("/recordings/clear-all")
async def clear_all_recordings():
    deleted = 0
    if RECORDINGS_DIR_PATH.is_dir():
        for p in list(RECORDINGS_DIR_PATH.glob("*")):
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
                    deleted += 1
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    deleted += 1
            except OSError:
                continue
    return {"ok": True, "removed": deleted}


@router.post("/transcripts/clear-all")
async def clear_transcript_files():
    """Remove transcript JSON artefacts from the fallback transcripts directory."""
    deleted = 0
    if TRANSCRIPTS_FALLBACK_DIR.is_dir():
        for p in TRANSCRIPTS_FALLBACK_DIR.rglob("*"):
            try:
                if p.is_file() and p.suffix.lower() in {".json", ".txt", ".vtt"}:
                    p.unlink(missing_ok=True)
                    deleted += 1
            except OSError:
                continue
    return {"ok": True, "removed": deleted}


@router.get("/connectivity")
async def connectivity_probe():
    """Measure HTTP latency to configurable probe URL (defaults to /health on APP_BASE_URL)."""
    env_url = os.getenv("MEETINGBOX_CONNECTIVITY_URL", "").strip()
    if env_url:
        url = env_url
    else:
        base = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
        url = f"{base}/health"
    ok, ms = _http_probe_ms(url, timeout=float(os.getenv("CONNECTIVITY_PROBE_TIMEOUT", "3")))
    return {"ok": ok, "latency_ms": round(ms, 2), "url": url}


@router.get("/diagnostic-log")
async def diagnostic_journal_tail(lines: int = 120):
    """Last *lines* of journalctl (host) if available."""
    nlines = max(20, min(500, lines))
    try:
        r = subprocess.run(
            ["journalctl", "-n", str(nlines), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        txt = r.stdout if r.stdout else r.stderr
        return {"lines": txt or "(no journal output)", "ok": r.returncode == 0}
    except Exception as e:
        return {"lines": "", "ok": False, "message": str(e)[:300]}


@router.post("/diagnostic-report")
async def diagnostic_submit(body: FeedbackBody):
    """Store a lightweight diagnostic ticket (logged server-side — extend to ticketing)."""
    msg = body.message.strip()[:2000]
    logger.info("device_diagnostic_report: %s", msg or "(empty)")
    return {"ok": True, "received": True}


@router.post("/feedback")
async def device_feedback(body: FeedbackBody):
    msg = body.message.strip()[:4000]
    logger.info("device_feedback: %s", msg)
    return {"ok": True}


# ======================================================================
# UPDATES
# ======================================================================

@router.get("/check-updates")
async def check_updates(current_user: Optional[dict] = Depends(get_optional_user)):
    """Check for firmware updates (placeholder – real impl would check a server)."""
    return {
        "update_available": False,
        "current_version": FIRMWARE_VERSION,
        "latest_version": None,
        "release_notes": None,
    }


@router.post("/install-update")
async def install_update(current_user: Optional[dict] = Depends(get_optional_user)):
    """Install firmware update (placeholder)."""
    return {"status": "no_update_available"}


# ======================================================================
# AUDIO COMMAND LONG-POLL (cloud mode — no local Redis on device)
# ======================================================================

def _command_matches_device(command: dict, device_id: str) -> bool:
    """Return true when a command is global or addressed to this device."""
    cmd_device_id = str(command.get("device_id") or "").strip()
    return not cmd_device_id or cmd_device_id == device_id


async def _wait_for_redis_command(redis_host: str, timeout: float, device_id: str) -> dict | None:
    """Subscribe to the Redis 'commands' channel and wait up to *timeout* seconds."""
    import redis.asyncio as aioredis
    import asyncio

    r = aioredis.Redis(host=redis_host, port=6379, decode_responses=True)
    try:
        async with r.pubsub() as pubsub:
            await pubsub.subscribe("commands")
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                left = deadline - asyncio.get_event_loop().time()
                if left <= 0:
                    return None
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=min(left, 1.0),
                    )
                    if msg is not None:
                        try:
                            command = json.loads(msg["data"])
                        except (json.JSONDecodeError, TypeError, KeyError):
                            continue
                        if isinstance(command, dict) and _command_matches_device(command, device_id):
                            return command
                except asyncio.TimeoutError:
                    pass
    finally:
        await r.aclose()


@router.get("/audio-command/wait")
async def audio_command_wait(
    current_device: dict = Depends(get_current_device_row),
):
    """Long-poll: block up to 28 s waiting for the next recording/mic-test command.

    The audio capture container on the device uses this when it cannot reach a
    local Redis instance (cloud / remote-server deployment).  Returns 204 No
    Content on timeout so the client can immediately re-poll.
    """
    from starlette.responses import Response

    command = await _wait_for_redis_command(
        REDIS_HOST,
        timeout=28.0,
        device_id=str(current_device["id"]),
    )
    if command is None:
        return Response(status_code=204)
    return command


# ======================================================================
# APPLIANCE SYSTEM METRICS (device UI → server → web dashboard)
# ======================================================================

class ApplianceMetrics(BaseModel):
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_total_gb: float = 0.0
    disk_percent: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0


_APPLIANCE_METRICS_KEY = "appliance:system_metrics"
_APPLIANCE_METRICS_TTL = 300  # seconds — expire if device stops reporting


@router.post("/system-metrics")
async def post_system_metrics(
    metrics: ApplianceMetrics,
    current_device: dict = Depends(get_current_device_row),
):
    """Accept CPU/RAM/disk metrics pushed periodically by the device UI.

    Stored in Redis with a 5-minute TTL so the web dashboard can display
    live appliance health even when the device itself has no public IP.
    Returns 200 silently on Redis errors (non-critical telemetry).
    """
    try:
        _get_redis().setex(
            _APPLIANCE_METRICS_KEY,
            _APPLIANCE_METRICS_TTL,
            json.dumps(metrics.model_dump()),
        )
    except Exception as exc:
        logger.debug("Could not store appliance metrics in Redis: %s", exc)
    return {"ok": True}


# ======================================================================
# INTEGRATIONS (delegates to /api/integrations router)
# These thin wrappers exist because the frontend api/integrations.ts
# and the device-ui api_client.py both call /api/device/integrations/*.
# They forward to the real integrations module.
# ======================================================================

@router.get("/integrations")
async def list_integrations(current_actor: dict | None = Depends(get_optional_actor)):
    """List integration statuses for dashboard JWT or paired device (owner account)."""
    meta = [
        {"id": "gmail", "name": "Gmail", "icon": "mail", "description": "Send AI-drafted emails"},
        {"id": "calendar", "name": "Google Calendar", "icon": "calendar", "description": "Auto-schedule meetings"},
    ]
    if not current_actor:
        return [{"connected": False, **m} for m in meta]

    user_id = current_actor["user"]["id"]
    results = []
    for m in meta:
        conn = get_connection()
        conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT email, connected_at FROM integrations WHERE user_id = ? AND provider = ?",
                (user_id, m["id"]),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        results.append({
            **m,
            "connected": row is not None,
            "email": row["email"] if row else None,
            "last_sync": row.get("connected_at") if row else None,
        })
    return results


@router.get("/integrations/{integration_id}/auth-url")
async def get_integration_auth_url(integration_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    """Proxy to the auth-url endpoint in the integrations router."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required to connect integrations")
    from routes.integrations import get_auth_url
    return await get_auth_url(integration_id, current_user)


@router.post("/integrations/{integration_id}/disconnect")
async def disconnect_integration(
    integration_id: str,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """Proxy disconnect — works with dashboard JWT or paired device Bearer token."""
    if not current_actor:
        raise HTTPException(
            status_code=401, detail="Authentication required to disconnect integrations"
        )
    current_user = current_actor["user"]
    from routes.integrations import disconnect_integration as real_disconnect

    return await real_disconnect(integration_id, current_user)


@router.post("/integrations/{integration_id}/sync")
async def integration_manual_sync(
    integration_id: str,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """Manual re-sync acknowledgement (hooks can enqueue background jobs later)."""
    del integration_id  # gmail / calendar
    if not current_actor:
        raise HTTPException(status_code=401, detail="Authentication required")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ok": True, "synced_at": ts}