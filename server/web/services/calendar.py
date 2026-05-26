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
    add_meet_link: bool | None = None,
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

    # Meet link: default ON when there are attendees, OFF for solo blocks, unless explicitly overridden
    if add_meet_link is None:
        add_meet_link = bool(attendees)

    insert_kwargs: dict = {
        "calendarId": "primary",
        "body": event_body,
        "sendUpdates": "all",
    }
    if add_meet_link:
        import uuid as _uuid
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-{_uuid.uuid4().hex[:16]}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        insert_kwargs["conferenceDataVersion"] = 1

    result = (
        service.events()
        .insert(**insert_kwargs)
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

    # When a date is specified, search only that exact day to avoid matching recurring instances
    target_date_str = base.date().isoformat()  # always YYYY-MM-DD, resolved from any hint
    if date_hint:
        day_start = base.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = base.replace(hour=23, minute=59, second=59, microsecond=0)
        time_min = day_start.isoformat()
        time_max = day_end.isoformat()
    else:
        time_min = (base - timedelta(days=1)).isoformat()
        time_max = (base + timedelta(days=7)).isoformat()

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
    if len(matches) > 1:
        # Narrow using resolved date string (YYYY-MM-DD) — works regardless of hint phrasing
        narrowed = [
            e for e in matches
            if (e.get("start") or {}).get("dateTime", (e.get("start") or {}).get("date", "")).startswith(target_date_str)
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


def update_event(
    credentials,
    *,
    event_id: str | None = None,
    title_hint: str | None = None,
    date_hint: str | None = None,
    timezone: str | None = None,
    attendees_add: list[str] | None = None,
    attendees_remove: list[str] | None = None,
    title: str | None = None,
    description: str | None = None,
    new_start_time: str | None = None,
    new_duration_minutes: int | None = None,
    new_date: str | None = None,
    new_location: str | None = None,
    new_recurrence: str | None = None,
) -> dict:
    """
    Patch an existing Google Calendar event.

    Finds event by event_id (preferred) or title_hint + date_hint.
    attendees_add: list of email addresses to merge into the existing attendee list.
    title / description: overwrite those fields if provided.
    Returns the updated Google Calendar event dict.
    Raises: ValueError if event not found or nothing to update.
    """
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
    zone = _safe_zone(tz_name)

    if event_id:
        try:
            ev = service.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception as exc:
            raise ValueError(f"Could not fetch event {event_id}: {exc}") from exc
    elif title_hint:
        # Search a ±7 day window (wider than delete so partial matches work)
        now_local = datetime.now(zone)
        base = now_local

        if date_hint:
            dh = date_hint.strip().lower()
            if dh == "today":
                base = now_local
            elif dh == "tomorrow":
                base = now_local + timedelta(days=1)
            elif dh == "yesterday":
                base = now_local - timedelta(days=1)
            else:
                try:
                    parsed = datetime.fromisoformat(date_hint.split("T")[0])
                    base = parsed.replace(tzinfo=zone)
                except ValueError:
                    pass

        if date_hint:
            day_start = base.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = base.replace(hour=23, minute=59, second=59, microsecond=0)
            time_min = day_start.isoformat()
            time_max = day_end.isoformat()
        else:
            time_min = (base - timedelta(days=1)).isoformat()
            time_max = (base + timedelta(days=7)).isoformat()

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
        title_lower = title_hint.strip().lower()
        matches = [e for e in events if title_lower in (e.get("summary") or "").lower()]

        if not matches:
            raise ValueError(f"No event matching '{title_hint}' found near {date_hint or 'today'}.")
        if len(matches) > 1:
            target_date_str = base.date().isoformat()
            narrowed = [
                e for e in matches
                if (e.get("start") or {}).get("dateTime", (e.get("start") or {}).get("date", "")).startswith(target_date_str)
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
    else:
        raise ValueError("event_id or title_hint is required to update an event")

    ev_id = ev["id"]
    patch_body: dict = {}

    if attendees_add or attendees_remove:
        existing_pairs = [
            (a["email"], a) for a in (ev.get("attendees") or []) if a.get("email")
        ]
        existing_lower = {e.lower() for e, _ in existing_pairs}
        remove_lower = {(s or "").strip().lower() for s in (attendees_remove or []) if s and s.strip()}
        merged: list[dict] = []
        for email, original in existing_pairs:
            if email.lower() in remove_lower:
                continue
            merged.append({"email": email, **{k: v for k, v in original.items() if k != "email"}})
        if attendees_add:
            for email in attendees_add:
                email = (email or "").strip()
                if email and email.lower() not in existing_lower and email.lower() not in remove_lower:
                    merged.append({"email": email})
                    existing_lower.add(email.lower())
        patch_body["attendees"] = merged

    if title:
        patch_body["summary"] = title.strip()

    if description is not None:
        patch_body["description"] = description

    if new_location is not None:
        patch_body["location"] = new_location

    # Reschedule support: new_start_time (full ISO) OR new_date (date shortcut) + optional new_duration_minutes
    if new_start_time or new_date or new_duration_minutes:
        existing_start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
        existing_end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date") or ""
        existing_tz = (ev.get("start") or {}).get("timeZone") or tz_name

        # parse existing start to a datetime to fall back on
        cur_start: datetime | None = None
        try:
            if existing_start:
                if existing_start.endswith("Z"):
                    cur_start = datetime.fromisoformat(existing_start.replace("Z", "+00:00"))
                elif len(existing_start) > 10 and existing_start[10] in "+-":
                    cur_start = datetime.fromisoformat(existing_start)
                else:
                    cur_start = datetime.fromisoformat(existing_start)
                if cur_start and cur_start.tzinfo is None:
                    cur_start = cur_start.replace(tzinfo=_safe_zone(existing_tz))
        except ValueError:
            cur_start = None

        cur_end: datetime | None = None
        try:
            if existing_end:
                if existing_end.endswith("Z"):
                    cur_end = datetime.fromisoformat(existing_end.replace("Z", "+00:00"))
                elif len(existing_end) > 10 and existing_end[10] in "+-":
                    cur_end = datetime.fromisoformat(existing_end)
                else:
                    cur_end = datetime.fromisoformat(existing_end)
                if cur_end and cur_end.tzinfo is None:
                    cur_end = cur_end.replace(tzinfo=_safe_zone(existing_tz))
        except ValueError:
            cur_end = None

        cur_duration_min = 30
        if cur_start and cur_end:
            cur_duration_min = max(5, int((cur_end - cur_start).total_seconds() // 60))

        # Build the new start datetime
        new_start_dt: datetime | None = None
        if new_start_time:
            s = new_start_time.strip()
            try:
                if s.endswith("Z"):
                    new_start_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                elif len(s) > 10 and s[10] in "+-":
                    new_start_dt = datetime.fromisoformat(s)
                else:
                    naive = datetime.fromisoformat(s.split("+")[0].split("Z")[0])
                    new_start_dt = naive.replace(tzinfo=_safe_zone(existing_tz))
            except ValueError:
                new_start_dt = None
        elif new_date and cur_start:
            # Shortcut: keep existing time-of-day, move to new_date
            try:
                d = datetime.fromisoformat(new_date.split("T")[0]).date()
                new_start_dt = cur_start.replace(year=d.year, month=d.month, day=d.day)
            except ValueError:
                new_start_dt = None

        if new_start_dt is None:
            new_start_dt = cur_start

        dur = int(new_duration_minutes) if new_duration_minutes else cur_duration_min
        if new_start_dt is None:
            raise ValueError("Cannot reschedule: no existing start time on event and no new_start_time provided.")

        new_end_dt = new_start_dt + timedelta(minutes=dur)
        patch_body["start"] = {"dateTime": new_start_dt.isoformat(), "timeZone": existing_tz}
        patch_body["end"] = {"dateTime": new_end_dt.isoformat(), "timeZone": existing_tz}

    if new_recurrence:
        rrule = new_recurrence.strip()
        if rrule and not rrule.upper().startswith("RRULE:"):
            rrule = f"RRULE:{rrule}"
        patch_body["recurrence"] = [rrule] if rrule else None

    if not patch_body:
        raise ValueError(
            "Nothing to update — provide attendees_add, attendees_remove, title, description, "
            "new_start_time/new_date/new_duration_minutes, new_location, or new_recurrence."
        )

    result = (
        service.events()
        .patch(
            calendarId="primary",
            eventId=ev_id,
            body=patch_body,
            sendUpdates="all",
        )
        .execute()
    )
    logger.info(
        "Calendar event updated: id=%s title=%s",
        ev_id,
        ev.get("summary"),
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


def find_overlapping_events(
    credentials,
    *,
    start_time: str,
    duration_minutes: int,
    timezone: str | None = None,
) -> list[dict]:
    """
    Return events on the user's primary calendar that overlap [start_time, start_time + duration).

    start_time may be a wall-clock ISO string ("YYYY-MM-DDTHH:MM:SS"), interpreted in `timezone`.
    Returns a list of minimal event dicts: {id, summary, start_iso, end_iso, start_local, end_local}.
    """
    if not start_time or not duration_minutes:
        return []

    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
    zone = _safe_zone(tz_name)

    s = start_time.strip()
    start_dt: datetime | None = None
    try:
        if s.endswith("Z"):
            start_dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        elif len(s) > 10 and s[10] in "+-":
            start_dt = datetime.fromisoformat(s)
        else:
            naive = datetime.fromisoformat(s.split("+")[0].split("Z")[0])
            start_dt = naive.replace(tzinfo=zone)
    except ValueError:
        return []

    end_dt = start_dt + timedelta(minutes=int(duration_minutes))

    # query with a small buffer so events ending exactly at start_dt aren't returned as overlapping
    time_min = start_dt.isoformat()
    time_max = end_dt.isoformat()

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

    overlapping: list[dict] = []
    for ev in result.get("items", []) or []:
        ev_start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date")
        ev_end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date")
        if not ev_start or not ev_end:
            continue
        # Parse + convert to user TZ for a friendly label
        try:
            es = ev_start
            ee = ev_end
            if es.endswith("Z"):
                es_dt = datetime.fromisoformat(es.replace("Z", "+00:00"))
            elif len(es) > 10 and es[10] in "+-":
                es_dt = datetime.fromisoformat(es)
            else:
                es_dt = datetime.fromisoformat(es).replace(tzinfo=zone)
            if ee.endswith("Z"):
                ee_dt = datetime.fromisoformat(ee.replace("Z", "+00:00"))
            elif len(ee) > 10 and ee[10] in "+-":
                ee_dt = datetime.fromisoformat(ee)
            else:
                ee_dt = datetime.fromisoformat(ee).replace(tzinfo=zone)
        except ValueError:
            continue

        # Strict overlap: end > start_dt AND start < end_dt
        if ee_dt > start_dt and es_dt < end_dt:
            es_local = es_dt.astimezone(zone)
            ee_local = ee_dt.astimezone(zone)
            overlapping.append({
                "id": ev.get("id"),
                "summary": ev.get("summary") or "(no title)",
                "start_iso": ev_start,
                "end_iso": ev_end,
                "start_local": es_local.strftime("%I:%M %p").lstrip("0"),
                "end_local": ee_local.strftime("%I:%M %p").lstrip("0"),
                "date_local": es_local.strftime("%Y-%m-%d"),
            })

    return overlapping


def rsvp_event(
    credentials,
    *,
    event_id: str | None = None,
    title_hint: str | None = None,
    date_hint: str | None = None,
    response_status: str = "accepted",
    timezone: str | None = None,
) -> dict:
    """
    Set the user's RSVP status on an event they've been invited to.

    response_status: 'accepted', 'declined', or 'tentative'.
    Locates the event by event_id (preferred) or title_hint + date_hint.
    Returns: {rsvp_applied: True, event_id, summary, response_status, start, end}.
    Raises ValueError if not found, ambiguous, or the user isn't an attendee.
    """
    rs = (response_status or "").strip().lower()
    if rs not in ("accepted", "declined", "tentative"):
        raise ValueError(
            f"response_status must be 'accepted', 'declined', or 'tentative' (got {response_status!r})"
        )

    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    tz_name = (timezone or _default_tz_name()).strip() or _default_tz_name()
    zone = _safe_zone(tz_name)

    ev: dict | None = None
    if event_id:
        try:
            ev = service.events().get(calendarId="primary", eventId=event_id).execute()
        except Exception as exc:
            raise ValueError(f"Could not fetch event {event_id}: {exc}") from exc
    elif title_hint:
        now_local = datetime.now(zone)
        base = now_local
        if date_hint:
            dh = date_hint.strip().lower()
            if dh == "today":
                base = now_local
            elif dh == "tomorrow":
                base = now_local + timedelta(days=1)
            elif dh == "yesterday":
                base = now_local - timedelta(days=1)
            else:
                try:
                    parsed = datetime.fromisoformat(date_hint.split("T")[0])
                    base = parsed.replace(tzinfo=zone)
                except ValueError:
                    pass
        if date_hint:
            day_start = base.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = base.replace(hour=23, minute=59, second=59, microsecond=0)
            time_min = day_start.isoformat()
            time_max = day_end.isoformat()
        else:
            time_min = (base - timedelta(days=1)).isoformat()
            time_max = (base + timedelta(days=14)).isoformat()
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
        events = result.get("items", []) or []
        title_lower = title_hint.strip().lower()
        matches = [e for e in events if title_lower in (e.get("summary") or "").lower()]
        if not matches:
            raise ValueError(f"No event matching '{title_hint}' found near {date_hint or 'today'}.")
        if len(matches) > 1:
            summaries = ", ".join(
                f"'{e.get('summary')}' on {(e.get('start') or {}).get('dateTime', (e.get('start') or {}).get('date', '?'))[:10]}"
                for e in matches[:4]
            )
            raise ValueError(f"Multiple matching events found: {summaries}. Please be more specific.")
        ev = matches[0]
    else:
        raise ValueError("event_id or title_hint is required to RSVP to an event")

    assert ev is not None
    ev_id = ev["id"]

    # Determine the user's own email — required to patch their attendee row
    me_email: str | None = None
    try:
        cal = service.calendars().get(calendarId="primary").execute()
        me_email = cal.get("id") or None
    except Exception:
        me_email = None

    attendees = list(ev.get("attendees") or [])
    if not attendees:
        # User created the event themselves; no RSVP applicable
        raise ValueError(
            f"You're the organizer of '{ev.get('summary')}' (no attendees to RSVP). RSVP only applies to events you were invited to."
        )

    user_row: dict | None = None
    for a in attendees:
        if a.get("self") is True:
            user_row = a
            break
        if me_email and (a.get("email") or "").lower() == me_email.lower():
            user_row = a
            break

    if user_row is None:
        raise ValueError(
            f"You don't appear to be an attendee on '{ev.get('summary')}'. RSVP only applies to events you were invited to."
        )

    user_row["responseStatus"] = rs

    updated = (
        service.events()
        .patch(
            calendarId="primary",
            eventId=ev_id,
            body={"attendees": attendees},
            sendUpdates="all",
        )
        .execute()
    )
    logger.info(
        "Calendar event RSVP set: id=%s status=%s title=%s",
        ev_id, rs, ev.get("summary"),
    )
    return {
        "rsvp_applied": True,
        "event_id": ev_id,
        "summary": updated.get("summary") or ev.get("summary") or "",
        "response_status": rs,
        "start": updated.get("start") or ev.get("start"),
        "end": updated.get("end") or ev.get("end"),
    }
