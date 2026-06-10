"""
Shared briefing bundle builder for HTTP routes and Realtime voice tool bridge.
Keeps GET /api/briefing/context and voice tools in sync.
"""

from __future__ import annotations

import logging
import threading
import time as _time_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Briefing pre-fetch cache (Opt-B): warm the briefing context before a voice
# session starts so the first memory_search tool call responds instantly.
# Cache is keyed by user_id and expires after _BRIEFING_CACHE_TTL seconds.
# ---------------------------------------------------------------------------
_BRIEFING_CACHE_TTL = 120.0  # 2 minutes — generous for voice session lifetime
_briefing_cache: dict[str, tuple[float, dict]] = {}  # user_id -> (expires_at, result)
_briefing_cache_lock = threading.Lock()

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


def get_cached_briefing(user_id: str) -> dict | None:
    """Return a cached briefing dict for user_id if still fresh, else None."""
    uid = (user_id or "").strip()
    if not uid:
        return None
    with _briefing_cache_lock:
        entry = _briefing_cache.get(uid)
        if entry and _time_mod.monotonic() < entry[0]:
            logger.debug("briefing cache HIT user=%s", uid)
            return entry[1]
    return None


def prime_briefing_cache(actor: dict, user_id: str) -> None:
    """Pre-fetch and cache the briefing context for user_id (fire-and-forget).

    Called at voice session creation so the first tool invocation is instant.
    """
    uid = (user_id or "").strip()
    if not uid:
        return

    def _fetch():
        try:
            # Prime with the SAME params the get_briefing_context voice tool uses
            # by default (2-day window, 15 inbox rows, mem0_cap=2800) so a cache
            # hit returns an identical bundle — not a smaller/mismatched one.
            result = build_briefing_context_dict(
                actor=actor,
                user_id=uid,
                days_ahead=2,
                date=None,
                mem0_cap=2800,
                gmail_preview_max=15,
            )
            with _briefing_cache_lock:
                _briefing_cache[uid] = (_time_mod.monotonic() + _BRIEFING_CACHE_TTL, result)
            logger.info("briefing cache primed user=%s", uid)
        except Exception:
            logger.debug("briefing prime_briefing_cache failed user=%s", uid, exc_info=True)

    threading.Thread(target=_fetch, daemon=True, name=f"briefing-prime-{uid[:8]}").start()


def build_briefing_context_dict(
    *,
    actor: dict,
    user_id: str,
    days_ahead: int = 1,
    date: str | None = None,
    mem0_cap: int = 4000,
    gmail_preview_max: int = 1,
    mem0_briefing_query: str | None = None,
) -> dict:
    """
    Same payload shape as GET /api/briefing/context (without FastAPI types).

    gmail_preview_max: Gmail rows to attach (Realtime voice uses >1; SPA default stays 1).
    mem0_briefing_query: override Mem0 briefing search string (None = sensible default).
    date: ISO date string 'YYYY-MM-DD'. When provided, the calendar window starts on that
      date instead of today, so "next Tuesday" or "this Friday" return the correct day's events.
    """
    da = max(1, min(int(days_ahead), 14))
    cap = max(0, min(int(mem0_cap), 8000))
    gmax = max(1, min(int(gmail_preview_max), 25))

    tz_name = default_calendar_tz_name()
    zone = ZoneInfo(tz_name)
    today = datetime.now(zone).date()

    # When a specific date is requested, anchor the window there instead of today.
    if date:
        try:
            from datetime import date as _date
            anchor = _date.fromisoformat(str(date).strip())
        except (ValueError, TypeError):
            anchor = today
    else:
        anchor = today

    end_day = anchor + timedelta(days=da - 1)
    time_min = datetime.combine(anchor, time.min, tzinfo=zone).isoformat()
    time_max = datetime.combine(end_day, time(23, 59, 59), tzinfo=zone).isoformat()
    d0, d1 = anchor, end_day

    # Pre-fetch credentials (fast local DB reads, must happen before parallel I/O).
    creds_cal = get_credentials_for_provider(user_id, "calendar")
    creds_g = get_credentials_for_provider(user_id, "gmail")

    # ------------------------------------------------------------------
    # Network I/O fetchers — run in parallel via ThreadPoolExecutor.
    # Each is fully self-contained with its own error handling so a
    # failure in one does not affect the others.
    # ------------------------------------------------------------------

    def _fetch_calendar() -> tuple[bool, object]:
        if not creds_cal:
            return False, build_days_map_for_range(d0, d1, [], tz_name)
        try:
            raw = list_events_in_range(creds_cal, time_min, time_max, max_results=200)
            return True, build_days_map_for_range(d0, d1, raw, tz_name)
        except HttpError as e:
            logger.warning("briefing calendar HttpError: %s", getattr(e, "reason", e))
            return True, build_days_map_for_range(d0, d1, [], tz_name)
        except Exception:
            logger.exception("briefing calendar aggregation failed")
            return True, build_days_map_for_range(d0, d1, [], tz_name)

    def _fetch_mem0() -> str | None:
        if cap <= 0:
            return None
        mq = (mem0_briefing_query or "").strip() or (
            "briefing priorities follow-ups reminders calendar email tasks meetings commitments inbox"
        )
        blob = search_context_for_prompt(user_id, mq)
        return blob[:cap] if blob and blob.strip() else None

    def _fetch_gmail() -> dict:
        if not creds_g:
            return {"connected": False, "top": None, "recent_messages": [], "recent_count": 0}
        try:
            # Scope the briefing preview to the INBOX only. An empty query
            # returns all mail newest-first, including the user's own SENT
            # messages — which made the assistant pick a sent email's thread
            # when asked to "reply to the email from <person>", replying on the
            # wrong conversation. The briefing is an inbox view, so received mail
            # is the correct (and only) thing to surface here.
            msgs = list_recent_messages(creds_g, max_results=gmax, q="in:inbox")
            return {
                "connected": True,
                "top": msgs[0] if msgs else None,
                "recent_messages": msgs,
                "recent_count": len(msgs),
            }
        except HttpError as e:
            return {
                "connected": True,
                "error": getattr(e, "reason", "gmail_error"),
                "top": None,
                "recent_messages": [],
                "recent_count": 0,
            }
        except Exception:
            logger.debug("briefing gmail preview failed", exc_info=True)
            return {"connected": True, "top": None, "recent_messages": [], "recent_count": 0}

    # Submit all three network calls immediately, then run fast SQLite work
    # on this thread while they execute in the background.
    with ThreadPoolExecutor(max_workers=3) as _pool:
        future_cal = _pool.submit(_fetch_calendar)
        future_mem0 = _pool.submit(_fetch_mem0)
        future_gmail = _pool.submit(_fetch_gmail)

        # Fast local (SQLite) work — runs while network I/O is in flight.
        commitments = list_commitments_for_user(user_id, status_filter=None, limit=24)
        pending = list_assistant_queue_for_briefing(user_id, limit=24)
        meetings_recent = recent_meetings_for_briefing(user_id, limit=8)

        # Collect network results (block only if not finished yet).
        calendar_connected, calendar_days = future_cal.result()
        mem0_snippet = future_mem0.result()
        gmail_preview = future_gmail.result()

    pending_out = dict(pending)
    pending_out["count"] = pending_out.get("count_pending", 0)

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
        "requested_date": anchor.isoformat(),
        "calendar_connected": calendar_connected,
        "days": calendar_days,
        "commitments": commitments,
        "meetings_recent": meetings_recent,
        "mem0_snippet": mem0_snippet,
        "pending_assistant": pending_out,
        "gmail_preview": gmail_preview,
    }
