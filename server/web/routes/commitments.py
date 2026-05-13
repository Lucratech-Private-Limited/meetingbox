"""HTTP API for user commitments (tasks / reminders in SQLite)."""

import logging

from fastapi import APIRouter, Depends, Query

from auth import get_current_actor
from services.commitments_service import list_commitments_for_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/commitments")
async def list_user_commitments(
    actor: dict = Depends(get_current_actor),
    status: str = Query("", max_length=32, description="active|completed|snoozed|cancelled|all; default: open"),
    limit: int = Query(40, ge=1, le=100),
):
    """List commitments for the signed-in user or device owner."""
    user_id = actor["user"]["id"]
    sf = status.strip().lower() if status else None
    rows = list_commitments_for_user(user_id, status_filter=sf, limit=limit)
    return {"commitments": rows, "count": len(rows)}
