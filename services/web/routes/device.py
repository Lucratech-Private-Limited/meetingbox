"""
Device Management Routes

Endpoints used by the device-ui appliance interface.
Manages: settings, WiFi, updates, system device info.
"""

import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import psutil

from auth import get_optional_user
from database import get_connection

router = APIRouter()

# Persistent settings file on disk
SETTINGS_FILE = Path(os.getenv("DEVICE_SETTINGS_PATH", "/data/config/device_settings.json"))
SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
SETUP_COMPLETE_FILE = SETTINGS_FILE.parent / ".setup_complete"

# NetworkManager WiFi interface (override if not wlan0, e.g. wlp2s0)
WIFI_IFACE = os.getenv("WIFI_INTERFACE", "wlan0")

FIRMWARE_VERSION = os.getenv("FIRMWARE_VERSION", "1.0.0")
DEVICE_MODEL = "MeetingBox v1.0"

# Boot time for uptime calculation
_BOOT_TIME = time.time()


# ======================================================================
# HELPERS
# ======================================================================

def _load_settings() -> dict:
    """Load device settings from disk, with defaults."""
    defaults = {
        "device_name": "MeetingBox",
        "timezone": "UTC",
        "auto_delete_days": "never",
        "brightness": "high",
        "screen_timeout": "never",
        "privacy_mode": False,
        "auto_record": False,
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
# SETTINGS
# ======================================================================

@router.get("/settings")
async def get_settings(current_user: Optional[dict] = Depends(get_optional_user)):
    """Return current device settings."""
    return _load_settings()


class SettingsUpdate(BaseModel):
    device_name: Optional[str] = None
    timezone: Optional[str] = None
    auto_delete_days: Optional[str] = None
    brightness: Optional[str] = None
    screen_timeout: Optional[str] = None
    privacy_mode: Optional[bool] = None
    auto_record: Optional[bool] = None
    auto_summarize: Optional[bool] = None
    action: Optional[str] = None  # restart / factory_reset


@router.patch("/settings")
async def update_settings(body: SettingsUpdate, current_user: Optional[dict] = Depends(get_optional_user)):
    """Update one or more device settings."""
    current = _load_settings()
    updates = body.dict(exclude_none=True)

    # Handle special actions
    action = updates.pop("action", None)
    if action == "restart":
        # Schedule restart in background
        try:
            subprocess.Popen(["sudo", "reboot"], close_fds=True)
        except Exception:
            pass
        return {"status": "restarting"}

    if action == "factory_reset":
        # Delete settings and setup marker, then reboot
        try:
            SETTINGS_FILE.unlink(missing_ok=True)
            # Remove setup marker from shared config volume
            SETUP_COMPLETE_FILE.unlink(missing_ok=True)
            for p in ["/data/config/.setup_complete",
                      "/opt/meetingbox/data/config/.setup_complete",
                      "/opt/meetingbox/.setup_complete"]:
                Path(p).unlink(missing_ok=True)
            subprocess.Popen(["sudo", "reboot"], close_fds=True)
        except Exception:
            pass
        return {"status": "resetting"}

    current.update(updates)
    _save_settings(current)
    return current


class SetupCompleteBody(BaseModel):
    """Metadata written to .setup_complete when onboarding finishes."""

    wifi_ssid: str = ""
    onboarding_flow: str = "wifi_on_device_v1"


@router.post("/setup-complete")
async def post_setup_complete(
    body: SetupCompleteBody,
    current_user: Optional[dict] = Depends(get_optional_user),
):
    """
    Mark first-boot setup finished. Writes JSON metadata next to device_settings.json.
    Device UI calls this after successful WiFi, then navigates home.
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
    }


# ======================================================================
# WIFI
# ======================================================================

@router.get("/wifi/scan")
async def wifi_scan(current_user: Optional[dict] = Depends(get_optional_user)):
    """Scan for available WiFi networks."""
    networks = []
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[0]:
                networks.append({
                    "ssid": parts[0],
                    "signal_strength": int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] or "open",
                    "connected": parts[3] == "yes",
                })
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
    return networks


class WiFiConnect(BaseModel):
    ssid: str
    password: Optional[str] = None


@router.post("/wifi/connect")
async def wifi_connect(body: WiFiConnect, current_user: Optional[dict] = Depends(get_optional_user)):
    """Connect to a WiFi network using NetworkManager."""
    try:
        # Remove any stale connection profile first
        subprocess.run(
            ["nmcli", "connection", "delete", body.ssid],
            capture_output=True, text=True, timeout=10,
        )

        if body.password:
            # Explicit creation with security type to avoid key-mgmt bug
            subprocess.run(
                ["nmcli", "connection", "add",
                 "type", "wifi",
                 "ifname", WIFI_IFACE,
                 "con-name", body.ssid,
                 "ssid", body.ssid,
                 "--",
                 "wifi-sec.key-mgmt", "wpa-psk",
                 "wifi-sec.psk", body.password],
                capture_output=True, text=True, timeout=15,
            )
            result = subprocess.run(
                ["nmcli", "connection", "up", body.ssid],
                capture_output=True, text=True, timeout=30,
            )
        else:
            result = subprocess.run(
                ["nmcli", "dev", "wifi", "connect", body.ssid],
                capture_output=True, text=True, timeout=30,
            )

        if result.returncode == 0:
            return {"status": "connected", "message": f"Connected to {body.ssid}"}
        else:
            subprocess.run(
                ["nmcli", "connection", "delete", body.ssid],
                capture_output=True, text=True, timeout=10,
            )
            return {"status": "failed", "message": result.stderr.strip() or result.stdout.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wifi/disconnect")
async def wifi_disconnect(current_user: Optional[dict] = Depends(get_optional_user)):
    """Disconnect from current WiFi."""
    try:
        subprocess.run(
            ["nmcli", "dev", "disconnect", WIFI_IFACE],
            capture_output=True, text=True, timeout=10,
        )
        return {"status": "disconnected"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
# INTEGRATIONS (delegates to /api/integrations router)
# These thin wrappers exist because the frontend api/integrations.ts
# and the device-ui api_client.py both call /api/device/integrations/*.
# They forward to the real integrations module.
# ======================================================================

@router.get("/integrations")
async def list_integrations(current_user: dict | None = Depends(get_optional_user)):
    """List integration statuses (works for both authed and unauthed callers)."""
    meta = [
        {"id": "gmail", "name": "Gmail", "icon": "mail", "description": "Send AI-drafted emails"},
        {"id": "calendar", "name": "Google Calendar", "icon": "calendar", "description": "Auto-schedule meetings"},
    ]
    if not current_user:
        return [{"connected": False, **m} for m in meta]

    user_id = current_user["id"]
    results = []
    for m in meta:
        conn = get_connection()
        conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT email FROM integrations WHERE user_id = ? AND provider = ?",
                (user_id, m["id"]),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        results.append({
            **m,
            "connected": row is not None,
            "email": row["email"] if row else None,
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
async def disconnect_integration(integration_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    """Proxy to the real disconnect in the integrations router."""
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required to disconnect integrations")
    from routes.integrations import disconnect_integration as real_disconnect
    return await real_disconnect(integration_id, current_user)


