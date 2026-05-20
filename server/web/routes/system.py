import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import psutil  # type: ignore

from auth import get_optional_actor, get_optional_user
from database import get_connection
from routes.meetings import _meeting_access_filter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status")
async def system_status(current_user: Optional[dict] = Depends(get_optional_user)) -> dict:
  if (os.getenv("MEETINGBOX_SYSTEM_STATUS_REQUIRE_AUTH", "") or "").strip() == "1":
    if not current_user:
      raise HTTPException(status_code=401, detail="Authentication required.")
  # Non-blocking sample keeps event loop responsive under concurrent requests.
  cpu = psutil.cpu_percent(interval=None)
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
  from routes.device import (
    _load_settings,
    _get_wifi_info,
    _get_ip_address,
    _get_serial,
    FIRMWARE_VERSION,
    SETUP_COMPLETE_FILE,
  )

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
    "setup_complete": SETUP_COMPLETE_FILE.exists(),
  }


@router.post("/cleanup")
async def cleanup_meetings(
  count: int = Query(default=5, ge=1, le=100, description="Number of oldest meetings to delete"),
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Delete the N oldest meetings (for the signed-in user or device) to free up disk space."""
  if not current_actor:
    raise HTTPException(status_code=401, detail="Sign in to delete meetings from your account.")

  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    query = "SELECT id, audio_path FROM meetings"
    params: list[Any] = []
    scope_sql, scope_params = _meeting_access_filter(current_actor, alias="meetings")
    if scope_sql:
      query += f" WHERE {scope_sql}"
      params.extend(scope_params)
    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(count)
    cur.execute(query, params)
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

