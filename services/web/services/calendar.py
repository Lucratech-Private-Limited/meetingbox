"""
Google Calendar Service -- create events using stored OAuth2 tokens.
"""

import logging
from datetime import datetime, timedelta

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def create_event(
    credentials,
    title: str,
    start_time: str | None = None,
    duration_minutes: int = 30,
    description: str = "",
    attendees: list[str] | None = None,
    location: str = "",
    timezone: str = "UTC",
) -> dict:
    """
    Create a Google Calendar event.

    Args:
        credentials: google.oauth2.credentials.Credentials with calendar scope
        title: event title/summary
        start_time: ISO 8601 datetime string. If None, defaults to tomorrow at 10:00 AM.
        duration_minutes: event duration in minutes
        description: event description/body
        attendees: list of email addresses
        location: event location
        timezone: IANA timezone string

    Returns:
        Google Calendar API event dict with 'id', 'htmlLink', etc.
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            start_dt = datetime.now() + timedelta(days=1)
            start_dt = start_dt.replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        start_dt = datetime.now() + timedelta(days=1)
        start_dt = start_dt.replace(hour=10, minute=0, second=0, microsecond=0)

    end_dt = start_dt + timedelta(minutes=duration_minutes)

    event_body = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": timezone,
        },
    }

    if attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in attendees if e.strip()]

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
