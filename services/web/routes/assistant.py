"""Assistant / orchestrator HTTP API (Phases 1–3)."""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from assistant_service import (
  approve_pending_action,
  list_pending_actions_for_user,
  process_assistant_intent,
  reject_pending_action,
)
from auth import get_current_user, get_optional_user

router = APIRouter()


class IntentRequest(BaseModel):
  message: str = Field(..., min_length=1, max_length=8000)
  meeting_id: Optional[str] = None


@router.post("/intent")
async def post_intent(
  body: IntentRequest,
  current_user: Optional[dict] = Depends(get_optional_user),
):
  """
  Route the user message through the orchestrator, run safe read-only tools,
  and queue calendar/email writes as pending actions until approved.
  """
  uid = current_user["id"] if current_user else None
  return process_assistant_intent(
    message=body.message,
    user_id=uid,
    meeting_id=body.meeting_id,
    source="api_intent",
  )


@router.get("/pending-actions")
async def get_pending_actions(current_user: dict = Depends(get_current_user)):
  return {"pending": list_pending_actions_for_user(current_user["id"])}


@router.post("/pending-actions/{pending_id}/approve")
async def post_approve_pending(
  pending_id: str,
  current_user: dict = Depends(get_current_user),
):
  return approve_pending_action(pending_id, current_user["id"])


@router.post("/pending-actions/{pending_id}/reject")
async def post_reject_pending(
  pending_id: str,
  current_user: dict = Depends(get_current_user),
):
  return reject_pending_action(pending_id, current_user["id"])
