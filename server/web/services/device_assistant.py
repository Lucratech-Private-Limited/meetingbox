"""Assistant-triggered recording commands for the paired appliance (HTTP Redis queue)."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import HTTPException

from auth import get_user_by_id

logger = logging.getLogger(__name__)

TOOL_DEVICE_START = "device_start_recording"
TOOL_DEVICE_STOP = "device_stop_recording"
TOOL_DEVICE_PAUSE = "device_pause_recording"
TOOL_DEVICE_RESUME = "device_resume_recording"
DEVICE_TOOLS = frozenset({TOOL_DEVICE_START, TOOL_DEVICE_STOP, TOOL_DEVICE_PAUSE, TOOL_DEVICE_RESUME})


def assistant_device_tools_enabled() -> bool:
    v = os.getenv("MEETINGBOX_ASSISTANT_DEVICE_TOOLS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def resolve_primary_device_id(user_id: str) -> Optional[str]:
    from database import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id FROM devices
            WHERE user_id = ?
              AND (status IS NULL OR TRIM(COALESCE(status, '')) = ''
                   OR LOWER(TRIM(status)) = 'active')
            ORDER BY datetime(COALESCE(last_seen_at, paired_at, created_at, '1970-01-01')) DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()


def plan_device_steps(message: str) -> list[dict[str, Any]]:
    """Single-action heuristic planner (no LLM) for appliance recording control."""
    m = (message or "").lower().strip()
    if not m:
        return []
    # Order matters: more specific phrases first
    if "pause" in m and "recording" in m:
        return [{"tool": TOOL_DEVICE_PAUSE, "args": {}, "is_write": True}]
    if ("resume" in m and "recording" in m) or ("continue" in m and "recording" in m):
        return [{"tool": TOOL_DEVICE_RESUME, "args": {}, "is_write": True}]
    if any(
        x in m
        for x in (
            "stop recording",
            "end recording",
            "finish recording",
            "stop the recording",
            "end the recording",
        )
    ):
        return [{"tool": TOOL_DEVICE_STOP, "args": {}, "is_write": True}]
    if any(
        x in m
        for x in (
            "start recording",
            "begin recording",
            "start the recording",
            "begin the recording",
            "record this meeting",
        )
    ):
        return [{"tool": TOOL_DEVICE_START, "args": {}, "is_write": True}]
    return []


def execute_device_tool(user_id: str, tool: str) -> dict[str, Any]:
    """Apply an approved device recording command (same Redis side-effects as REST handlers)."""
    from routes.meetings import (
        _generate_session_id,
        _get_redis,
        _publish_recording_ws_event,
        _store_session_owner,
        emit_audio_command,
    )

    if tool not in DEVICE_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported device tool: {tool}")

    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    actor = {"type": "user", "user": user}
    r = _get_redis()
    state = r.get("recording_state") or "idle"
    if isinstance(state, bytes):
        state = state.decode()

    if tool == TOOL_DEVICE_START:
        if state == "recording":
            return {"status": "already_recording", "note": "Recording is already active on this server."}
        sid = _generate_session_id()
        _store_session_owner(sid, actor)
        emit_audio_command(actor, {"action": "start_recording", "session_id": sid})
        r.set("current_meeting_id", sid)
        r.set("recording_state", "recording")
        _publish_recording_ws_event("recording_started", sid)
        return {"session_id": sid, "status": "recording_started"}

    if tool == TOOL_DEVICE_STOP:
        sid = r.get("current_meeting_id")
        if isinstance(sid, bytes):
            sid = sid.decode()
        emit_audio_command(actor, {"action": "stop_recording", "session_id": sid})
        r.set("recording_state", "processing")
        _publish_recording_ws_event("recording_stopped", sid)
        if sid:
            r.delete("current_meeting_id")
        return {"session_id": sid, "status": "recording_stopped"}

    if tool == TOOL_DEVICE_PAUSE:
        if state != "recording":
            raise HTTPException(status_code=400, detail="No active recording to pause.")
        sid = r.get("current_meeting_id")
        if isinstance(sid, bytes):
            sid = sid.decode()
        emit_audio_command(actor, {"action": "pause_recording", "session_id": sid})
        r.set("recording_state", "paused")
        _publish_recording_ws_event("recording_paused", sid)
        return {"session_id": sid, "status": "paused"}

    if tool == TOOL_DEVICE_RESUME:
        if state != "paused":
            raise HTTPException(status_code=400, detail="No paused recording to resume.")
        sid = r.get("current_meeting_id")
        if isinstance(sid, bytes):
            sid = sid.decode()
        emit_audio_command(actor, {"action": "resume_recording", "session_id": sid})
        r.set("recording_state", "recording")
        _publish_recording_ws_event("recording_resumed", sid)
        return {"session_id": sid, "status": "recording"}

    raise HTTPException(status_code=400, detail=f"Unsupported device tool: {tool}")
