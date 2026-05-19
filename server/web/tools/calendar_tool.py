"""Calendar tool adapter — list / create events via stored OAuth (Phase 3)."""

from __future__ import annotations

from typing import Any

from routes.integrations import get_credentials_for_provider
from services.calendar import (
    create_event,
    default_calendar_tz_name,
    find_and_delete_event,
    list_upcoming_events,
    suggest_free_slots,
)
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
  recurrence = payload.get("recurrence") or None
  return create_event(
    credentials=creds,
    title=title,
    start_time=start if start else None,
    duration_minutes=duration,
    description=description,
    attendees=attendees,
    location=location,
    timezone=timezone,
    recurrence=recurrence,
  )


def calendar_delete_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to delete calendar events.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  event_id = str(payload.get("event_id") or "").strip() or None
  title_hint = str(payload.get("title") or payload.get("title_hint") or "").strip() or None
  date_hint = str(payload.get("date") or payload.get("date_hint") or payload.get("start_time") or "").strip() or None
  timezone = str(payload.get("timezone") or default_calendar_tz_name())
  return find_and_delete_event(
    creds,
    event_id=event_id,
    title_hint=title_hint,
    date_hint=date_hint,
    timezone=timezone,
  )


def calendar_suggest_free_slots(user_id: str, args: dict | None = None) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to check availability.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  a = args if isinstance(args, dict) else {}
  return suggest_free_slots(
    creds,
    days_ahead=int(a.get("days_ahead") or 7),
    duration_minutes=int(a.get("duration_minutes") or 30),
    step_minutes=int(a.get("step_minutes") or 30),
    work_start_hhmm=str(a.get("work_start_hhmm") or "09:00"),
    work_end_hhmm=str(a.get("work_end_hhmm") or "18:00"),
    timezone=str(a.get("timezone") or default_calendar_tz_name()),
    max_slots=int(a.get("max_slots") or 12),
  )
