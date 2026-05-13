"""Commitments tool adapter — SQLite tasks/reminders for assistant."""

from __future__ import annotations

from typing import Any

from services.commitments_service import list_commitments_for_user, upsert_commitment
from tools.base_tool import ToolError


def commitment_list_for_user(user_id: str, max_results: int = 30, status: str = "") -> dict[str, Any]:
    if not user_id:
        raise ToolError("Sign in is required to list commitments.")
    lim = max(1, min(int(max_results), 100))
    sf = (status or "").strip().lower() or None
    rows = list_commitments_for_user(user_id, status_filter=sf, limit=lim)
    slim = []
    for r in rows:
        slim.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "status": r.get("status"),
            "tags": r.get("tags"),
            "remind_at": r.get("remind_at"),
            "due_at": r.get("due_at"),
            "detail": (r.get("detail") or "")[:500],
            "calendar_event_id": r.get("calendar_event_id"),
        })
    return {"commitments": slim, "count": len(slim)}


def commitment_upsert_for_user(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not user_id:
        raise ToolError("Sign in is required to save commitments.")
    if not isinstance(payload, dict):
        raise ToolError("Invalid commitment payload.")
    try:
        row = upsert_commitment(user_id, payload)
    except ValueError as e:
        raise ToolError(str(e)) from e
    return {"commitment": row, "saved": True}
