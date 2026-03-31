"""
Google Calendar Service -- create events using stored OAuth2 tokens.
"""

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _default_tz_name() -> str:
    return (os.getenv("CALENDAR_DEFAULT_TIMEZONE") or "UTC").strip() or "UTC"


def _safe_zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_wall_start(
    start_date: str | None,
    start_time_hhmm: str | None,
    tz_name: str | None,
) -> datetime:
    """
    Interpret date + HH:MM as wall-clock time in the given IANA timezone.
    Falls back to tomorrow 10:00 in that zone if inputs are missing/invalid.
    """
    zone = _safe_zone(tz_name or _default_tz_name())
    date_part = (start_date or "").strip()
    time_part = (start_time_hhmm or "10:00").strip()
    if len(time_part) == 5 and time_part[2] == ":":
        pass
    elif len(time_part) == 4 and ":" not in time_part:
        time_part = f"{time_part[:2]}:{time_part[2:]}"
    try:
        if date_part:
            naive = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
            return naive.replace(tzinfo=zone)
    except ValueError:
        pass

    start_dt = datetime.now(zone) + timedelta(days=1)
    return start_dt.replace(hour=10, minute=0, second=0, microsecond=0)


def create_event(
    credentials,
    title: str,
    start_time: str | None = None,
    duration_minutes: int = 30,
    description: str = "",
    attendees: list[str] | None = None,
    location: str = "",
    timezone: str | None = None,
    *,
    start_date: str | None = None,
    start_time_hhmm: str | None = None,
) -> dict:
    """
    Create a Google Calendar event.

    Preferred: pass start_date (YYYY-MM-DD), start_time_hhmm (HH:MM), and timezone (IANA).
    Legacy: start_time as naive "YYYY-MM-DDTHH:MM:SS" — interpreted in *timezone* (or default).

    Args:
        credentials: google.oauth2.credentials.Credentials with calendar scope
        title: event title/summary
        start_time: legacy combined local wall time (no offset) or ISO with Z/offset
        duration_minutes: event duration
        description: event description/body
        attendees: list of email addresses
        location: event location
        timezone: IANA timezone for wall-clock interpretation
        start_date: YYYY-MM-DD (with start_time_hhmm)
        start_time_hhmm: HH:MM

    Returns:
        Google Calendar API event dict with 'id', 'htmlLink', etc.
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    tz_name = (timezone or _default_tz_name()).strip() or "UTC"
    zone = _safe_zone(tz_name)

    start_dt: datetime | None = None

    if start_date and start_time_hhmm:
        start_dt = _parse_wall_start(start_date, start_time_hhmm, tz_name)
    elif start_time:
        s = start_time.strip()
        try:
            if s.endswith("Z"):
                start_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            elif len(s) > 10 and s[10] in "+-" and ":" in s[11:]:
                start_dt = datetime.fromisoformat(s)
            else:
                naive = datetime.fromisoformat(s.split("+")[0].split("Z")[0])
                start_dt = naive.replace(tzinfo=zone)
        except ValueError:
            start_dt = None

    if start_dt is None:
        start_dt = _parse_wall_start(None, None, tz_name)

    end_dt = start_dt + timedelta(minutes=duration_minutes)

    event_body = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": tz_name,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": tz_name,
        },
    }

    if attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in attendees if e and str(e).strip()]

    result = (
        service.events()
        .insert(calendarId="primary", body=event_body, sendUpdates="all")
        .execute()
    )

    logger.info(
        "Calendar event created: id=%s title=%s link=%s",
        result.get("id"),
        title,
        result.get("htmlLink"),
    )
    return result


def list_upcoming_events(credentials, max_results: int = 10) -> list[dict]:
    """Return upcoming calendar events (for context in action execution)."""
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    now = datetime.utcnow().isoformat() + "Z"
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])
