"""Aggregated briefing + calendar week APIs for SPA and device-ui (shared actor auth)."""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from googleapiclient.errors import HttpError

from auth import get_current_actor
from routes.integrations import get_credentials_for_provider
from services.briefing_context import build_briefing_context_dict
from services.mem0_service import maybe_ingest_calendar_snapshot, maybe_ingest_gmail_snapshot
from services.calendar import (
    build_days_map_for_range,
    default_calendar_tz_name,
    list_events_in_range,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["briefing"])


def _ingest_briefing_bundle(user_id: str, bundle: dict) -> None:
    """Best-effort Mem0 ingest of calendar and Gmail data from a briefing bundle."""
    try:
        days = bundle.get("days") or {}
        cal_rows: list = []
        for day in days.values():
            cal_rows.extend(day.get("meetings") or day.get("events") or [])
        maybe_ingest_calendar_snapshot(user_id, {"events": cal_rows, "count": len(cal_rows)})
    except Exception:
        logger.debug("briefing calendar ingest failed", exc_info=True)
    try:
        gp = bundle.get("gmail_preview") or {}
        msgs = list(gp.get("recent_messages") or [])
        if not msgs and gp.get("top"):
            msgs = [gp["top"]]
        maybe_ingest_gmail_snapshot(user_id, {"messages": msgs, "count": len(msgs)})
    except Exception:
        logger.debug("briefing gmail ingest failed", exc_info=True)


@router.get("/calendar/week")
async def get_calendar_week(
    actor: dict = Depends(get_current_actor),
    start: str = Query(..., min_length=10, max_length=10, description="Start date YYYY-MM-DD"),
    end: str = Query(..., min_length=10, max_length=10, description="End date YYYY-MM-DD"),
):
    """Weekly view of Google Calendar for the authenticated user or device owner."""
    user_id = actor["user"]["id"]
    try:
        d_start = date.fromisoformat(start.strip())
        d_end = date.fromisoformat(end.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start or end date; use YYYY-MM-DD.")

    if d_end < d_start:
        d_start, d_end = d_end, d_start

    span = (d_end - d_start).days + 1
    if span > 31:
        raise HTTPException(status_code=400, detail="Date range too large; max 31 days.")

    tz_name = default_calendar_tz_name()
    zone = ZoneInfo(tz_name)
    time_min = datetime.combine(d_start, time.min, tzinfo=zone).isoformat()
    time_max = datetime.combine(d_end, time(23, 59, 59), tzinfo=zone).isoformat()

    creds = get_credentials_for_provider(user_id, "calendar")
    if not creds:
        return {"days": build_days_map_for_range(d_start, d_end, [], tz_name)}

    try:
        raw = list_events_in_range(creds, time_min, time_max, max_results=250)
    except HttpError as e:
        status = int(e.resp.status) if e.resp else 500
        logger.warning("calendar/week HttpError status=%s", status)
        raise HTTPException(status_code=502, detail="Google Calendar request failed.") from e
    except Exception:
        logger.exception("calendar/week unexpected error")
        raise HTTPException(status_code=500, detail="Could not load calendar.") from None

    days = build_days_map_for_range(d_start, d_end, raw, tz_name)
    return {"days": days}


@router.get("/briefing/context")
async def get_briefing_context(
    actor: dict = Depends(get_current_actor),
    days_ahead: int = Query(1, ge=1, le=14, description="Calendar window from today in local tz"),
    mem0_cap: int = Query(1200, ge=0, le=8000),
):
    """
    Single JSON bundle: calendar slice, commitments, recent DB meetings, Mem0 snippet,
    pending assistant queue, optional Gmail preview.
    """
    user_id = actor["user"]["id"]
    bundle = build_briefing_context_dict(
        actor=actor,
        user_id=user_id,
        days_ahead=days_ahead,
        mem0_cap=mem0_cap,
    )
    _ingest_briefing_bundle(user_id, bundle)
    return bundle
