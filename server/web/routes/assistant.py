"""Assistant / orchestrator HTTP API (Phases 1–3)."""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from assistant_service import (
  approve_pending_action,
  list_pending_actions_for_user,
  process_assistant_intent,
  reject_pending_action,
  update_pending_assistant_payload,
)
from auth import get_current_actor, get_optional_actor
from rate_limit import limiter

_ASSISTANT_ALLOW_ANON = os.getenv("MEETINGBOX_ASSISTANT_ALLOW_ANON", "").strip() == "1"

router = APIRouter()


class IntentRequest(BaseModel):
  message: str = Field(..., min_length=1, max_length=8000)
  meeting_id: Optional[str] = None


class PendingPayloadUpdate(BaseModel):
  payload: dict


@router.post("/intent")
@limiter.limit("60/minute")
async def post_intent(
  request: Request,
  body: IntentRequest,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """
  Route the user message through the orchestrator, run safe read-only tools,
  and queue calendar/email writes as pending actions until approved.

  Requires a dashboard JWT or paired device token unless
  MEETINGBOX_ASSISTANT_ALLOW_ANON=1 (not recommended for production or demos on the public internet).
  """
  if not _ASSISTANT_ALLOW_ANON and current_actor is None:
    raise HTTPException(status_code=401, detail="Authentication required.")
  owner_id = current_actor["user"]["id"] if current_actor else None
  return process_assistant_intent(
    message=body.message,
    user_id=owner_id,
    meeting_id=body.meeting_id,
    source="api_intent",
  )


@router.get("/pending-actions")
async def get_pending_actions(actor: dict = Depends(get_current_actor)):
  return {"pending": list_pending_actions_for_user(actor["user"]["id"])}


@router.patch("/pending-actions/{pending_id}")
async def patch_pending_payload(
  pending_id: str,
  body: PendingPayloadUpdate,
  actor: dict = Depends(get_current_actor),
):
  return update_pending_assistant_payload(pending_id, actor["user"]["id"], body.payload)


@router.post("/pending-actions/{pending_id}/approve")
async def post_approve_pending(
  pending_id: str,
  actor: dict = Depends(get_current_actor),
):
  return approve_pending_action(pending_id, actor["user"]["id"])


@router.post("/pending-actions/{pending_id}/reject")
async def post_reject_pending(
  pending_id: str,
  actor: dict = Depends(get_current_actor),
):
  return reject_pending_action(pending_id, actor["user"]["id"])
