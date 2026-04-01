"""Calendar tool adapter — list / create events via stored OAuth (Phase 3)."""

from __future__ import annotations

from typing import Any

from routes.integrations import get_credentials_for_provider
from services.calendar import create_event, default_calendar_tz_name, list_upcoming_events
from tools.base_tool import ToolError


def calendar_list_upcoming(user_id: str, max_results: int = 10) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to access your calendar.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  raw = list_upcoming_events(creds, max_results=max(1, min(int(max_results), 50)))
  slim = []
  for ev in raw:
    slim.append({
      "id": ev.get("id"),
      "summary": ev.get("summary", ""),
      "start": ev.get("start", {}),
      "end": ev.get("end", {}),
      "htmlLink": ev.get("htmlLink"),
    })
  return {"events": slim, "count": len(slim)}


def calendar_create_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to create calendar events.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  title = str(payload.get("title") or "Meeting").strip() or "Meeting"
  start = payload.get("start_time") or payload.get("start_iso") or None
  duration = int(payload.get("duration_minutes") or 30)
  description = str(payload.get("description") or "")
  attendees = payload.get("attendees") if isinstance(payload.get("attendees"), list) else []
  location = str(payload.get("location") or "")
  timezone = str(payload.get("timezone") or default_calendar_tz_name())
  return create_event(
    credentials=creds,
    title=title,
    start_time=start if start else None,
    duration_minutes=duration,
    description=description,
    attendees=attendees,
    location=location,
    timezone=timezone,
  )
