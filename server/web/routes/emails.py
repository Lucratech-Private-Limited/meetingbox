"""
Email Routes — expose Gmail inbox to the device UI.

GET  /api/emails                       list inbox messages
GET  /api/emails/{id}                  fetch full message body
POST /api/emails/{id}/mark-unread      add UNREAD label
POST /api/emails/{id}/archive          remove from INBOX
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_optional_actor
from routes.integrations import get_credentials_for_provider
from services.gmail import (
    list_recent_messages,
    get_message_full,
    mark_message_unread,
    archive_message,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sender(raw: str) -> tuple[str, str]:
    """Parse 'Display Name <addr@host>' into (display_name, email)."""
    m = re.match(r'^(.*?)\s*<([^>]+)>', raw.strip())
    if m:
        name = m.group(1).strip().strip('"')
        addr = m.group(2).strip()
        return name or addr, addr
    return raw.strip(), raw.strip()


def _friendly_time(raw_date: str) -> str:
    """Turn an RFC 2822 date string into 'H:MM AM' or 'Mon DD' if older."""
    try:
        dt = parsedate_to_datetime(raw_date)
        now = datetime.now(tz=timezone.utc)
        local_dt = dt.astimezone()
        if (now - dt).days == 0:
            return local_dt.strftime("%-I:%M %p")
        if (now - dt).days < 7:
            return local_dt.strftime("%a %d")
        return local_dt.strftime("%b %d")
    except Exception:
        return raw_date[:10] if raw_date else "—"


def _is_today(raw_date: str) -> bool:
    try:
        dt = parsedate_to_datetime(raw_date)
        now = datetime.now(tz=timezone.utc)
        return (now - dt).days == 0
    except Exception:
        return False


def _get_user_id(actor: dict) -> Optional[str]:
    if not actor:
        return None
    if actor.get("type") == "device":
        return actor["device"].get("owner_user_id") or actor["user"]["id"]
    return actor["user"]["id"]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/emails")
async def list_emails(
    filter: str = Query(default="all", description="all | today | unread"),
    limit: int = Query(default=20, ge=1, le=50),
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """List Gmail inbox messages for the authenticated user / device owner."""
    user_id = _get_user_id(current_actor)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(
            status_code=403,
            detail="Gmail not connected. Connect Gmail from the Settings page.",
        )

    # Build Gmail query based on filter
    q = "in:inbox"
    if filter == "unread":
        q = "in:inbox is:unread"
    elif filter == "today":
        q = "in:inbox newer_than:1d"

    try:
        raw_messages = list_recent_messages(creds, max_results=limit, q=q)
    except Exception as exc:
        logger.error("Gmail list_recent_messages failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    emails = []
    for msg in raw_messages:
        sender_name, sender_addr = _parse_sender(msg.get("from", ""))
        raw_date = msg.get("date", "")
        emails.append({
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "sender": sender_name,
            "sender_email": sender_addr,
            "subject": msg.get("subject", "(no subject)"),
            "preview": msg.get("snippet", ""),
            "body": msg.get("snippet", ""),   # full body loaded on detail fetch
            "time": _friendly_time(raw_date),
            "date": raw_date,
            "is_today": _is_today(raw_date),
            "is_read": msg.get("is_read", True),
            "to": msg.get("to", ""),
        })

    return emails


@router.get("/emails/{email_id}")
async def get_email(
    email_id: str,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """Fetch the full body of a single Gmail message."""
    user_id = _get_user_id(current_actor)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected")

    try:
        msg = get_message_full(creds, email_id)
    except Exception as exc:
        logger.error("get_message_full %s failed: %s", email_id, exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    sender_name, sender_addr = _parse_sender(msg.get("from", ""))
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "sender": sender_name,
        "sender_email": sender_addr,
        "subject": msg.get("subject", "(no subject)"),
        "preview": msg.get("snippet", ""),
        "body": msg.get("body", msg.get("snippet", "")),
        "time": _friendly_time(msg.get("date", "")),
        "date": msg.get("date", ""),
        "is_today": _is_today(msg.get("date", "")),
        "is_read": msg.get("is_read", True),
        "to": "",
    }


@router.post("/emails/{email_id}/mark-unread")
async def mark_unread(
    email_id: str,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """Mark a Gmail message as unread (adds UNREAD label)."""
    user_id = _get_user_id(current_actor)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected")

    try:
        mark_message_unread(creds, email_id)
    except Exception as exc:
        logger.error("mark_message_unread %s failed: %s", email_id, exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    return {"status": "ok", "id": email_id}


@router.post("/emails/{email_id}/archive")
async def archive_email(
    email_id: str,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    """Archive a Gmail message (removes INBOX label)."""
    user_id = _get_user_id(current_actor)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    creds = get_credentials_for_provider(user_id, "gmail")
    if not creds:
        raise HTTPException(status_code=403, detail="Gmail not connected")

    try:
        archive_message(creds, email_id)
    except Exception as exc:
        logger.error("archive_message %s failed: %s", email_id, exc)
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    return {"status": "ok", "id": email_id}
