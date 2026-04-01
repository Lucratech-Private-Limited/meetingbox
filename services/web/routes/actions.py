"""
Agentic actions routes: generation, listing, editing, execution, and dismissal.
"""

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from auth import get_optional_actor
from database import get_connection
from services.action_engine import (
    dismiss_action_record,
    execute_action_record,
    generate_actions_for_meeting,
    list_actions_for_meeting,
    update_action_record,
)

router = APIRouter()


def _actor_scope(actor: Optional[dict]) -> tuple[str, tuple[object, ...]]:
    if not actor:
        return "", ()
    if actor["type"] == "device":
        return "m.device_id = ?", (actor["device"]["id"],)
    return "m.user_id = ?", (actor["user"]["id"],)


def _assert_meeting_access(meeting_id: str, actor: Optional[dict]) -> None:
    scope_sql, scope_params = _actor_scope(actor)
    if not scope_sql:
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM meetings m WHERE m.id = ? AND {scope_sql}", (meeting_id, *scope_params))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Meeting not found")
    finally:
        conn.close()


def _assert_action_access(action_id: str, actor: Optional[dict]) -> None:
    scope_sql, scope_params = _actor_scope(actor)
    if not scope_sql:
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT 1
            FROM actions a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.id = ? AND {scope_sql}
            """,
            (action_id, *scope_params),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Action not found")
    finally:
        conn.close()


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


class ExecuteActionRequest(BaseModel):
    """Optional fields merged into the action payload before execute (user review step)."""

    payload: Optional[dict[str, Any]] = None
    #: If true and connector is Gmail, create a draft only (does not send). Ignored for Calendar.
    create_draft: Optional[bool] = False


@router.get("/meetings/{meeting_id}/actions", response_model=list[ActionResponse])
async def list_actions(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_meeting_access(meeting_id, current_actor)
    return list_actions_for_meeting(meeting_id)


@router.post("/meetings/{meeting_id}/actions/generate", response_model=list[ActionResponse])
async def generate_actions(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_meeting_access(meeting_id, current_actor)
    user_id = current_actor["user"]["id"] if current_actor else None
    return generate_actions_for_meeting(meeting_id, user_id)


@router.patch("/actions/{action_id}", response_model=ActionResponse)
async def update_action(action_id: str, body: ActionUpdateRequest, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_action_access(action_id, current_actor)
    return update_action_record(
        action_id,
        title=body.title,
        description=body.description,
        payload=body.payload,
    )


@router.post("/actions/{action_id}/dismiss")
async def dismiss_action(action_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_action_access(action_id, current_actor)
    return dismiss_action_record(action_id)


@router.post("/actions/{action_id}/execute")
async def execute_action(
    action_id: str,
    body: ExecuteActionRequest = Body(default_factory=ExecuteActionRequest),
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    _assert_action_access(action_id, current_actor)
    user_id = current_actor["user"]["id"] if current_actor else None
    override = body.payload if body and body.payload else None
    draft = bool(body.create_draft) if body else False
    return execute_action_record(action_id, user_id, override, create_draft=draft)
