"""Calendar tool adapter — list / create events via stored OAuth (Phase 3)."""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError

from routes.integrations import get_credentials_for_provider
from services.calendar import (
    create_event,
    default_calendar_tz_name,
    find_and_delete_event,
    find_overlapping_events,
    list_upcoming_events,
    rsvp_event,
    suggest_free_slots,
    update_event,
)
from tools.base_tool import ToolError


def _wrap_google_error(exc: HttpError) -> ToolError:
    """Translate a googleapiclient.HttpError into a user-friendly ToolError."""
    status = getattr(getattr(exc, "resp", None), "status", None) or 0
    detail = ""
    try:
        # error_details is a JSON-parsed list when the library is recent enough
        msgs = [d.get("message") for d in (getattr(exc, "error_details", None) or []) if isinstance(d, dict)]
        detail = "; ".join(m for m in msgs if m)
    except Exception:
        detail = ""
    if not detail:
        detail = str(exc)
    if status in (401, 403):
        if "insufficient" in detail.lower() or "scope" in detail.lower():
            return ToolError(
                "Google Calendar access is missing the required scope for this action. "
                "Reconnect Google Calendar in Settings -> Integrations and approve all calendar permissions."
            )
        return ToolError("Google Calendar denied the request: " + detail[:200])
    if status == 404:
        return ToolError("Calendar event not found.")
    if status == 410:
        return ToolError("That calendar event was already deleted.")
    if status >= 500:
        return ToolError("Google Calendar is temporarily unavailable. Try again in a moment.")
    return ToolError("Google Calendar error: " + detail[:200])


def calendar_list_upcoming(user_id: str, max_results: int = 10) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to access your calendar.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  try:
    raw = list_upcoming_events(creds, max_results=max(1, min(int(max_results), 50)))
  except HttpError as e:
    raise _wrap_google_error(e) from e
  slim = []
  for ev in raw:
    # Surface attendees (without self/organizer noise) so downstream specialists
    # (e.g. communication_agent in a multi-agent chain like "email all attendees")
    # can use the recipient list directly from this read.
    attendees: list[dict[str, Any]] = []
    for a in ev.get("attendees") or []:
      em = (a.get("email") or "").strip()
      if not em:
        continue
      attendees.append({
        "email": em,
        "name": a.get("displayName") or "",
        "response": a.get("responseStatus") or "needsAction",
        "organizer": bool(a.get("organizer")),
        "self": bool(a.get("self")),
      })
    slim.append({
      "id": ev.get("id"),
      "summary": ev.get("summary", ""),
      "start": ev.get("start", {}),
      "end": ev.get("end", {}),
      "htmlLink": ev.get("htmlLink"),
      "hangoutLink": ev.get("hangoutLink") or "",
      "attendees": attendees,
      "organizer": (ev.get("organizer") or {}).get("email") or "",
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
  # Meet link: default ON when there are attendees and not explicitly disabled.
  # Accept bool, "true"/"false", "yes"/"no" from the planner.
  raw_meet = payload.get("add_meet_link")
  if raw_meet is None:
    add_meet_link = bool(attendees)
  elif isinstance(raw_meet, bool):
    add_meet_link = raw_meet
  else:
    s = str(raw_meet).strip().lower()
    add_meet_link = s in ("true", "yes", "1", "y")
  try:
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
      add_meet_link=add_meet_link,
    )
  except HttpError as e:
    raise _wrap_google_error(e) from e


def calendar_check_conflicts(user_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
  """Return overlapping events for a proposed create. Returns [] if no creds or no conflicts."""
  if not user_id:
    return []
  try:
    creds = get_credentials_for_provider(user_id, "calendar")
  except Exception:
    return []
  if not creds:
    return []
  start = payload.get("start_time") or payload.get("start_iso") or None
  duration = int(payload.get("duration_minutes") or 30)
  timezone = str(payload.get("timezone") or default_calendar_tz_name())
  if not start:
    return []
  try:
    return find_overlapping_events(
      creds,
      start_time=str(start),
      duration_minutes=duration,
      timezone=timezone,
    )
  except HttpError:
    # Best-effort conflict check; never block a create on missing scopes or transient API hiccups.
    return []
  except Exception:
    return []


def calendar_update_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to update calendar events.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  event_id = str(payload.get("event_id") or "").strip() or None
  raw_title = str(payload.get("title") or payload.get("title_hint") or "").strip()
  title_hint = _extract_event_title(raw_title) if raw_title and not event_id else None
  date_hint = str(payload.get("date") or payload.get("date_hint") or "").strip() or None
  timezone = str(payload.get("timezone") or default_calendar_tz_name())

  attendees_add = payload.get("attendees_add") if isinstance(payload.get("attendees_add"), list) else []
  attendees_remove = payload.get("attendees_remove") if isinstance(payload.get("attendees_remove"), list) else []
  new_title = str(payload.get("new_title") or "").strip() or None
  description = payload.get("description")
  if description is None:
    description = payload.get("new_description")

  new_start_time = (
    str(payload.get("new_start_time") or payload.get("new_start_iso") or "").strip() or None
  )
  raw_new_dur = payload.get("new_duration_minutes")
  new_duration_minutes = int(raw_new_dur) if raw_new_dur not in (None, "", 0) else None
  new_date = str(payload.get("new_date") or "").strip() or None
  new_location = (
    str(payload.get("new_location")).strip() if payload.get("new_location") is not None else None
  )
  new_recurrence = str(payload.get("new_recurrence") or "").strip() or None

  try:
    return update_event(
      creds,
      event_id=event_id,
      title_hint=title_hint,
      date_hint=date_hint,
      timezone=timezone,
      attendees_add=attendees_add if attendees_add else None,
      attendees_remove=attendees_remove if attendees_remove else None,
      title=new_title,
      description=str(description) if description is not None else None,
      new_start_time=new_start_time,
      new_duration_minutes=new_duration_minutes,
      new_date=new_date,
      new_location=new_location,
      new_recurrence=new_recurrence,
    )
  except HttpError as e:
    raise _wrap_google_error(e) from e
  except ValueError as e:
    raise ToolError(str(e)) from e


def calendar_rsvp_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to RSVP to calendar events.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  event_id = str(payload.get("event_id") or "").strip() or None
  raw_title = str(payload.get("title") or payload.get("title_hint") or "").strip()
  title_hint = _extract_event_title(raw_title) if raw_title and not event_id else None
  date_hint = str(payload.get("date") or payload.get("date_hint") or "").strip() or None
  timezone = str(payload.get("timezone") or default_calendar_tz_name())
  raw_status = str(payload.get("response_status") or payload.get("status") or "").strip().lower()
  # Friendly synonyms from the LLM planner
  status_map = {
    "accept": "accepted", "accepted": "accepted", "yes": "accepted", "going": "accepted", "attending": "accepted",
    "decline": "declined", "declined": "declined", "no": "declined", "not_going": "declined", "not attending": "declined",
    "maybe": "tentative", "tentative": "tentative", "may attend": "tentative",
  }
  response_status = status_map.get(raw_status, raw_status if raw_status in ("accepted", "declined", "tentative") else "accepted")
  try:
    return rsvp_event(
      creds,
      event_id=event_id,
      title_hint=title_hint,
      date_hint=date_hint,
      response_status=response_status,
      timezone=timezone,
    )
  except HttpError as e:
    raise _wrap_google_error(e) from e
  except ValueError as e:
    raise ToolError(str(e)) from e


def _extract_event_title(raw: str) -> str:
  """
  Defensively extract just the event name from a potentially full sentence.
  The LLM planner sometimes puts the full request sentence as the 'title' arg.
  """
  import re
  raw = raw.strip()
  # If short enough, use as-is
  if len(raw) <= 60 and not any(w in raw.lower() for w in ("delete ", "remove ", "cancel ", "the calendar", "scheduled")):
    return raw
  # Try quoted string first: "Focus Time" or 'Focus Time'
  quoted = re.search(r'"([^"]{2,60})"', raw) or re.search(r"'([^']{2,60})'", raw)
  if quoted:
    return quoted.group(1).strip()
  # Try "titled X", "named X", "called X" patterns
  named = re.search(
    r'(?:titled|named|called)\s+"?\'?([A-Za-z0-9 _\-]{2,60}?)\'?"?\s+(?:scheduled|on|at|for|from|tomorrow|today|in\b)',
    raw, re.I,
  )
  if named:
    return named.group(1).strip()
  # Fall back to the first few words (likely the event name before qualifying text)
  words = raw.split()
  for i, w in enumerate(words):
    if w.lower() in ("scheduled", "on", "at", "from", "in", "tomorrow", "today", "the", "delete", "remove", "cancel"):
      if i > 0:
        return " ".join(words[:i]).strip()
  return raw[:60].strip()


def calendar_delete_from_payload(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to delete calendar events.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  event_id = str(payload.get("event_id") or "").strip() or None
  raw_title = str(payload.get("title") or payload.get("title_hint") or "").strip()
  title_hint = _extract_event_title(raw_title) if raw_title else None
  date_hint = str(payload.get("date") or payload.get("date_hint") or payload.get("start_time") or "").strip() or None
  timezone = str(payload.get("timezone") or default_calendar_tz_name())
  try:
    return find_and_delete_event(
      creds,
      event_id=event_id,
      title_hint=title_hint,
      date_hint=date_hint,
      timezone=timezone,
    )
  except HttpError as e:
    raise _wrap_google_error(e) from e
  except ValueError as e:
    raise ToolError(str(e)) from e


def calendar_suggest_free_slots(user_id: str, args: dict | None = None) -> dict[str, Any]:
  if not user_id:
    raise ToolError("Sign in is required to check availability.")
  creds = get_credentials_for_provider(user_id, "calendar")
  if not creds:
    raise ToolError("Google Calendar is not connected. Connect it in Settings.")
  a = args if isinstance(args, dict) else {}
  try:
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
  except HttpError as e:
    raise _wrap_google_error(e) from e
