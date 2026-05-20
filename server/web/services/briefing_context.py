"""
Shared briefing bundle builder for HTTP routes and Realtime voice tool bridge.
Keeps GET /api/briefing/context and voice tools in sync.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from assistant_service import list_assistant_queue_for_briefing
from database import get_connection
from routes.integrations import get_credentials_for_provider
from services.calendar import (
    build_days_map_for_range,
    default_calendar_tz_name,
    list_events_in_range,
)
from services.commitments_service import list_commitments_for_user
from services.gmail import list_recent_messages
from services.mem0_service import search_context_for_prompt

logger = logging.getLogger(__name__)


def _row_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _user_display_name(actor: dict) -> str | None:
    u = actor.get("user") or {}
    return (u.get("display_name") or u.get("email") or "").strip() or None


def recent_meetings_for_briefing(user_id: str, limit: int = 8) -> list[dict]:
    uid = (user_id or "").strip()
    if not uid:
        return []
    lim = max(1, min(int(limit), 50))
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.execute(
            """
            SELECT m.id, m.title, m.start_time, m.created_at,
                   (SELECT substr(s.summary, 1, 160) FROM summaries s WHERE s.meeting_id = m.id) AS summary_excerpt
            FROM meetings m
            WHERE m.user_id = ?
            ORDER BY datetime(COALESCE(m.start_time, m.created_at)) DESC
            LIMIT ?
            """,
            (uid, lim),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "title": r.get("title") or "(Meeting)",
            "start_time": r.get("start_time"),
            "created_at": r.get("created_at"),
            "summary_excerpt": r.get("summary_excerpt") or "",
        })
    return out


def build_briefing_context_dict(
    *,
    actor: dict,
    user_id: str,
    days_ahead: int = 1,
    mem0_cap: int = 1200,
    gmail_preview_max: int = 1,
    mem0_briefing_query: str | None = None,
) -> dict:
    """
    Same payload shape as GET /api/briefing/context (without FastAPI types).

    gmail_preview_max: Gmail rows to attach (Realtime voice uses >1; SPA default stays 1).
    mem0_briefing_query: override Mem0 briefing search string (None = sensible default).
    """
    # da=1 → today only. da=2 → today through tomorrow (included). Voice questions often mean
    # "tomorrow" while the tool omitted days_ahead; Realtime tooling defaults da=2 in realtime_voice_tools.
    da = max(1, min(int(days_ahead), 14))
    cap = max(0, min(int(mem0_cap), 8000))

    tz_name = default_calendar_tz_name()
    zone = ZoneInfo(tz_name)
    today = datetime.now(zone).date()
    end_day = today + timedelta(days=da - 1)

    time_min = datetime.combine(today, time.min, tzinfo=zone).isoformat()
    time_max = datetime.combine(end_day, time(23, 59, 59), tzinfo=zone).isoformat()

    d0, d1 = today, end_day
    calendar_days = build_days_map_for_range(d0, d1, [], tz_name)
    calendar_connected = False
    creds_cal = get_credentials_for_provider(user_id, "calendar")
    if creds_cal:
        calendar_connected = True
        try:
            raw = list_events_in_range(creds_cal, time_min, time_max, max_results=200)
            calendar_days = build_days_map_for_range(d0, d1, raw, tz_name)
        except HttpError as e:
            logger.warning("briefing calendar HttpError: %s", getattr(e, "reason", e))
            calendar_days = build_days_map_for_range(d0, d1, [], tz_name)
        except Exception:
            logger.exception("briefing calendar aggregation failed")
            calendar_days = build_days_map_for_range(d0, d1, [], tz_name)

    commitments = list_commitments_for_user(user_id, status_filter=None, limit=24)

    mem0_snippet: str | None = None
    if cap > 0:
        mq = (mem0_briefing_query or "").strip() or (
            "briefing priorities follow-ups reminders calendar email tasks meetings commitments inbox"
        )
        blob = search_context_for_prompt(user_id, mq)
        if blob and blob.strip():
            mem0_snippet = blob[:cap]

    pending = list_assistant_queue_for_briefing(user_id, limit=24)
    pending_out = dict(pending)
    pending_out["count"] = pending_out.get("count_pending", 0)
    meetings_recent = recent_meetings_for_briefing(user_id, limit=8)

    gmax = max(1, min(int(gmail_preview_max), 25))

    gmail_preview: dict | None = None
    creds_g = get_credentials_for_provider(user_id, "gmail")
    if creds_g:
        try:
            msgs = list_recent_messages(creds_g, max_results=gmax, q="")
            gmail_preview = {
                "connected": True,
                "top": msgs[0] if msgs else None,
                "recent_messages": msgs,
                "recent_count": len(msgs),
            }
        except HttpError as e:
            gmail_preview = {
                "connected": True,
                "error": getattr(e, "reason", "gmail_error"),
                "top": None,
                "recent_messages": [],
                "recent_count": 0,
            }
        except Exception:
            logger.debug("briefing gmail preview failed", exc_info=True)
            gmail_preview = {
                "connected": True,
                "top": None,
                "recent_messages": [],
                "recent_count": 0,
            }
    else:
        gmail_preview = {"connected": False, "top": None, "recent_messages": [], "recent_count": 0}

    name = _user_display_name(actor)
    hour = datetime.now(zone).hour
    greet = "Good evening"
    if 5 <= hour < 12:
        greet = "Good morning"
    elif 12 <= hour < 17:
        greet = "Good afternoon"

    return {
        "greeting": greet,
        "user_display_name": name,
        "timezone": tz_name,
        "today": today.isoformat(),
        "calendar_connected": calendar_connected,
        "days": calendar_days,
        "commitments": commitments,
        "meetings_recent": meetings_recent,
        "mem0_snippet": mem0_snippet,
        "pending_assistant": pending_out,
        "gmail_preview": gmail_preview,
    }
