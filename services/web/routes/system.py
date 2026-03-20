import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import psutil  # type: ignore

from auth import get_optional_user
from database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
async def system_status() -> dict:
  cpu = psutil.cpu_percent(interval=0.5)
  mem = psutil.virtual_memory()
  disk = psutil.disk_usage("/")

  return {
    "system": {
      "cpu_percent": cpu,
      "memory_percent": mem.percent,
      "memory_used_gb": mem.used / (1024**3),
      "memory_total_gb": mem.total / (1024**3),
      "disk_percent": disk.percent,
      "disk_used_gb": disk.used / (1024**3),
      "disk_total_gb": disk.total / (1024**3),
    }
  }


@router.get("/device-info")
async def device_info() -> dict:
  """Extended device info consumed by the OLED touch-screen UI."""
  from routes.device import _load_settings, _get_wifi_info, _get_ip_address, _get_serial, FIRMWARE_VERSION

  settings = _load_settings()
  wifi = _get_wifi_info()
  disk = psutil.disk_usage("/")

  meetings_count = 0
  try:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM meetings")
    meetings_count = cur.fetchone()[0]
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


@router.post("/cleanup")
async def cleanup_meetings(
  count: int = Query(default=5, ge=1, le=100, description="Number of oldest meetings to delete"),
  current_user: Optional[dict] = Depends(get_optional_user),
):
  """Delete the N oldest meetings to free up disk space."""
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    cur.execute(
      "SELECT id, audio_path FROM meetings ORDER BY created_at ASC LIMIT ?",
      (count,),
    )
    rows = cur.fetchall()
    if not rows:
      return {"deleted": 0, "message": "No meetings to delete."}

    deleted_ids = []
    for row in rows:
      mid, audio_path = row
      cur.execute("DELETE FROM actions WHERE meeting_id = ?", (mid,))
      cur.execute("DELETE FROM segments WHERE meeting_id = ?", (mid,))
      cur.execute("DELETE FROM summaries WHERE meeting_id = ?", (mid,))
      cur.execute("DELETE FROM local_summaries WHERE meeting_id = ?", (mid,))
      cur.execute("DELETE FROM meetings WHERE id = ?", (mid,))
      if audio_path:
        p = Path(audio_path)
        if p.exists():
          p.unlink(missing_ok=True)
      deleted_ids.append(mid)
    conn.commit()
  finally:
    conn.close()

  logger.info("Cleaned up %d meetings: %s", len(deleted_ids), deleted_ids)
  return {"deleted": len(deleted_ids), "ids": deleted_ids}

