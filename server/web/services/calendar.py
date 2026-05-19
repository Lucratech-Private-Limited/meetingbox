"""
Google Calendar Service -- create events using stored OAuth2 tokens.
"""

import logging
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# When CALENDAR_DEFAULT_TIMEZONE is unset, use India (Delhi) — IST, no DST.
DEFAULT_CALENDAR_IANA = "Asia/Kolkata"


def default_calendar_tz_name() -> str:
    """IANA zone for interpreting wall-clock calendar date/time when env is unset."""
    raw = (os.getenv("CALENDAR_DEFAULT_TIMEZONE") or "").strip()
    return raw or DEFAULT_CALENDAR_IANA


def _default_tz_name() -> str:
    return default_calendar_tz_name()


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
    recurrence: list[str] | str | None = None,
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
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
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

    if recurrence:
        if isinstance(recurrence, str):
            recurrence = [recurrence]
        event_body["recurrence"] = [r if r.upper().startswith("RRULE:") else f"RRULE:{r}" for r in recurrence]

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


def find_and_delete_event(
    credentials,
    *,
    event_id: str | None = None,
    title_hint: str | None = None,
    date_hint: str | None = None,
    timezone: str | None = None,
) -> dict:
    """
    Find a calendar event and delete it.

    Priority: event_id (direct) > title_hint + date_hint (search).
    Returns: {"deleted": True, "event_id": ..., "summary": ..., "start": ...}
    Raises: ValueError if no match found or multiple matches are ambiguous.
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
    zone = _safe_zone(tz_name)

    if event_id:
        try:
            ev = service.events().get(calendarId="primary", eventId=event_id).execute()
            service.events().delete(calendarId="primary", eventId=event_id).execute()
            logger.info("Calendar event deleted by id: %s", event_id)
            return {"deleted": True, "event_id": event_id, "summary": ev.get("summary", ""), "start": ev.get("start", {})}
        except Exception as exc:
            raise ValueError(f"Could not delete event {event_id}: {exc}") from exc

    # Search by title + date
    if not title_hint:
        raise ValueError("event_id or title_hint is required to delete an event")

    # Build search window centred on the date hint
    now_local = datetime.now(zone)
    base = now_local  # default: today

    if date_hint:
        dh = date_hint.strip().lower()
        if dh in ("today",):
            base = now_local
        elif dh in ("tomorrow",):
            base = now_local + timedelta(days=1)
        elif dh in ("yesterday",):
            base = now_local - timedelta(days=1)
        else:
            try:
                parsed = datetime.fromisoformat(date_hint.split("T")[0])
                base = parsed.replace(tzinfo=zone)
            except ValueError:
                base = now_local  # fallback

    # Wide window: -1 day to +21 days so we catch events near the hint
    time_min = (base - timedelta(days=1)).isoformat()
    time_max = (base + timedelta(days=21)).isoformat()

    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )
    events = result.get("items", [])

    # Fuzzy match by title
    title_lower = title_hint.strip().lower()
    matches = [e for e in events if title_lower in (e.get("summary") or "").lower()]

    if not matches:
        raise ValueError(f"No event matching '{title_hint}' found near {date_hint or 'today'}.")
    if len(matches) > 1 and date_hint:
        # Narrow by date
        date_str = date_hint.split("T")[0]
        narrowed = [
            e for e in matches
            if (e.get("start") or {}).get("dateTime", (e.get("start") or {}).get("date", "")).startswith(date_str)
        ]
        if narrowed:
            matches = narrowed

    if len(matches) > 1:
        summaries = ", ".join(
            f"'{e.get('summary')}' on {(e.get('start') or {}).get('dateTime', (e.get('start') or {}).get('date', '?'))[:10]}"
            for e in matches[:4]
        )
        raise ValueError(f"Multiple matching events found: {summaries}. Please be more specific.")

    ev = matches[0]
    ev_id = ev["id"]
    summary = ev.get("summary", "")
    start = ev.get("start", {})

    service.events().delete(calendarId="primary", eventId=ev_id).execute()
    logger.info("Calendar event deleted by search: id=%s title=%s", ev_id, summary)
    return {"deleted": True, "event_id": ev_id, "summary": summary, "start": start}


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


def list_events_time_range(
    credentials,
    *,
    days_past: int = 7,
    days_future: int = 90,
    max_results: int = 250,
) -> list[dict]:
    """
    List Google Calendar events (single instances) in a UTC window.
    Includes past items for the given days_past (e.g. completed meetings) and future items.
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=max(0, int(days_past)))).isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(days=max(1, int(days_future)))).isoformat().replace("+00:00", "Z")
    mr = max(1, min(int(max_results), 500))
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=mr,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def query_freebusy_blocks(
    credentials,
    time_min_utc: datetime,
    time_max_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return busy intervals as UTC-aware datetimes from Google Calendar freeBusy."""
    utc = ZoneInfo("UTC")
    a = time_min_utc.astimezone(utc)
    b = time_max_utc.astimezone(utc)
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    body = {
        "timeMin": a.isoformat().replace("+00:00", "Z"),
        "timeMax": b.isoformat().replace("+00:00", "Z"),
        "items": [{"id": "primary"}],
    }
    fb = service.freebusy().query(body=body).execute()
    busy = fb.get("calendars", {}).get("primary", {}).get("busy") or []
    out: list[tuple[datetime, datetime]] = []
    for block in busy:
        try:
            s_raw = str(block.get("start", "")).replace("Z", "+00:00")
            e_raw = str(block.get("end", "")).replace("Z", "+00:00")
            s_dt = datetime.fromisoformat(s_raw)
            e_dt = datetime.fromisoformat(e_raw)
            out.append((s_dt, e_dt))
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda t: t[0])
    return out


def _parse_hhmm_pair(hhmm: str, default_h: int, default_m: int) -> tuple[int, int]:
    s = (hhmm or "").strip()
    if len(s) >= 4 and ":" in s:
        a, b = s.split(":", 1)
        try:
            return int(a), int(b[:2])
        except ValueError:
            return default_h, default_m
    return default_h, default_m


def suggest_free_slots(
    credentials,
    *,
    days_ahead: int = 7,
    duration_minutes: int = 30,
    step_minutes: int = 30,
    work_start_hhmm: str = "09:00",
    work_end_hhmm: str = "18:00",
    timezone: str | None = None,
    max_slots: int = 12,
) -> dict:
    """
    Heuristic free blocks using Calendar freeBusy + simple working-hours scan.
    Does not model all-day events perfectly; good enough for assistant slot suggestions.
    """
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
    zone = _safe_zone(tz_name)
    now = datetime.now(zone)
    days = max(1, min(int(days_ahead), 21))
    end_horizon = now + timedelta(days=days)

    busy = query_freebusy_blocks(
        credentials,
        now.astimezone(ZoneInfo("UTC")),
        end_horizon.astimezone(ZoneInfo("UTC")),
    )

    ws_h, ws_m = _parse_hhmm_pair(work_start_hhmm, 9, 0)
    we_h, we_m = _parse_hhmm_pair(work_end_hhmm, 18, 0)
    step = timedelta(minutes=max(15, min(int(step_minutes), 120)))
    dur = timedelta(minutes=max(15, min(int(duration_minutes), 480)))

    def overlaps(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
        return a0 < b1 and a1 > b0

    slots: list[dict] = []
    day = now.date()
    end_day = end_horizon.date()

    while day <= end_day and len(slots) < max(1, min(int(max_slots), 24)):
        day_start = datetime.combine(day, time(ws_h, ws_m, tzinfo=zone))
        day_end = datetime.combine(day, time(we_h, we_m, tzinfo=zone))
        if day_end <= day_start:
            day = day + timedelta(days=1)
            continue
        scan_start = max(day_start, now) if day == now.date() else day_start
        t = scan_start
        while t + dur <= day_end and len(slots) < max(1, min(int(max_slots), 24)):
            t_end = t + dur
            conflict = False
            for b0, b1 in busy:
                b0l = b0.astimezone(zone)
                b1l = b1.astimezone(zone)
                if overlaps(t, t_end, b0l, b1l):
                    conflict = True
                    break
            if not conflict:
                slots.append({
                    "start_local": t.isoformat(),
                    "end_local": t_end.isoformat(),
                    "timezone": tz_name,
                    "duration_minutes": int(duration_minutes),
                })
            t += step
        day += timedelta(days=1)

    return {
        "slots": slots,
        "count": len(slots),
        "timezone": tz_name,
        "days_searched": days,
        "busy_block_count": len(busy),
    }


def list_events_in_range(
    credentials,
    time_min_rfc3339: str,
    time_max_rfc3339: str,
    max_results: int = 250,
) -> list[dict]:
    """
    List primary-calendar events whose start time falls in [timeMin, timeMax],
    as RFC3339 timestamps (with zone offset or Z).
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    mr = max(1, min(int(max_results), 500))
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min_rfc3339,
            timeMax=time_max_rfc3339,
            maxResults=mr,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return result.get("items", [])


def calendar_event_to_device_meeting(ev: dict, tz_name: str) -> dict:
    """Normalize a Google Calendar API event to the mini-pc `meetings[]` row shape."""
    zone = _safe_zone(tz_name)
    start = ev.get("start") or {}
    end = ev.get("end") or {}
    start_dt: datetime | None = None
    end_dt: datetime | None = None

    if start.get("dateTime"):
        s = str(start["dateTime"]).replace("Z", "+00:00")
        start_dt = datetime.fromisoformat(s)
    elif start.get("date"):
        d0 = datetime.fromisoformat(str(start["date"])).date()
        start_dt = datetime.combine(d0, time.min, tzinfo=zone)

    if end.get("dateTime"):
        e = str(end["dateTime"]).replace("Z", "+00:00")
        end_dt = datetime.fromisoformat(e)
    elif end.get("date"):
        # All-day end date is exclusive in Google Calendar.
        e0 = datetime.fromisoformat(str(end["date"])).date()
        end_exclusive = datetime.combine(e0, time.min, tzinfo=zone)
        end_dt = end_exclusive - timedelta(seconds=1)

    if start_dt is not None and end_dt is None:
        end_dt = start_dt + timedelta(hours=1)
    if start_dt is not None and end_dt is not None and end_dt <= start_dt:
        end_dt = start_dt + timedelta(minutes=30)

    duration_sec = 0
    if start_dt is not None and end_dt is not None:
        duration_sec = max(0, int((end_dt - start_dt).total_seconds()))

    return {
        "id": ev.get("id"),
        "title": ev.get("summary") or "(No title)",
        "start": start_dt.isoformat() if start_dt else "",
        "end": end_dt.isoformat() if end_dt else "",
        "start_time": start_dt.isoformat() if start_dt else "",
        "duration": duration_sec,
        "htmlLink": ev.get("htmlLink") or "",
    }


def local_date_key_for_meeting_start(start_iso: str, tz_name: str) -> str | None:
    """Return YYYY-MM-DD in tz_name for an ISO start timestamp, if parseable."""
    if not (start_iso or "").strip():
        return None
    zone = _safe_zone(tz_name)
    try:
        dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(zone).date().isoformat()


def build_days_map_for_range(
    d0: date,
    d1: date,
    raw_events: list[dict],
    tz_name: str,
) -> dict[str, dict]:
    """
    Initialize every date in [d0, d1] with {\"meetings\": []}, attach events by local day.
    """
    if d1 < d0:
        d0, d1 = d1, d0
    days: dict[str, dict] = {}
    cur = d0
    while cur <= d1:
        days[cur.isoformat()] = {"meetings": []}
        cur += timedelta(days=1)

    for ev in raw_events:
        m = calendar_event_to_device_meeting(ev, tz_name)
        key = local_date_key_for_meeting_start(m.get("start") or "", tz_name)
        if key and key in days:
            days[key]["meetings"].append(m)

    for ds in days:
        days[ds]["meetings"].sort(key=lambda x: x.get("start") or "")

    return days
