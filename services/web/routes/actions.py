"""
Agentic actions routes: generation, listing, editing, execution, and dismissal.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth import get_optional_user
from services.action_engine import (
    dismiss_action_record,
    execute_action_record,
    generate_actions_for_meeting,
    list_actions_for_meeting,
    update_action_record,
)

router = APIRouter()


class ActionResponse(BaseModel):
    id: str
    meeting_id: str
    type: str
    kind: str
    connector_target: str
    execution_mode: str
    title: Optional[str]
    description: Optional[str]
    assignee: Optional[str]
    confidence: Optional[float]
    payload: dict
    artifact: Optional[dict]
    status: str
    delivery_status: Optional[str]
    error: Optional[str]
    selected_at: Optional[str]
    executed_at: Optional[str]
    created_at: Optional[str]


class ActionUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    payload: Optional[dict] = None


@router.get("/meetings/{meeting_id}/actions", response_model=list[ActionResponse])
async def list_actions(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    return list_actions_for_meeting(meeting_id)


@router.post("/meetings/{meeting_id}/actions/generate", response_model=list[ActionResponse])
async def generate_actions(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    user_id = current_user["id"] if current_user else None
    return generate_actions_for_meeting(meeting_id, user_id)


@router.patch("/actions/{action_id}", response_model=ActionResponse)
async def update_action(action_id: str, body: ActionUpdateRequest, current_user: Optional[dict] = Depends(get_optional_user)):
    return update_action_record(
        action_id,
        title=body.title,
        description=body.description,
        payload=body.payload,
    )


@router.post("/actions/{action_id}/dismiss")
async def dismiss_action(action_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    return dismiss_action_record(action_id)


@router.post("/actions/{action_id}/execute")
async def execute_action(action_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    user_id = current_user["id"] if current_user else None
    return execute_action_record(action_id, user_id)
