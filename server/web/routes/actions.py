"""
Agentic actions routes: generation, listing, editing, execution, and dismissal.
"""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from auth import get_optional_actor
from database import get_connection
from services.action_engine import (
    create_manual_action_record,
    dismiss_action_record,
    execute_action_record,
    generate_actions_for_meeting,
    ignore_action_record,
    list_actions_for_meeting,
    update_action_record,
)

router = APIRouter()


def _actor_scope(actor: Optional[dict]) -> tuple[str, tuple[object, ...]]:
    if not actor:
        return "", ()
    if actor["type"] == "device":
        return "m.device_id = ?", (actor["device"]["id"],)
    uid = actor["user"]["id"]
    pred = (
        "(m.user_id = ? OR m.device_id IN ("
        " SELECT id FROM devices WHERE user_id = ? "
        " AND (status IS NULL OR TRIM(COALESCE(status, '')) = '' OR LOWER(TRIM(status)) = 'active')))"
    )
    return pred, (uid, uid)


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
    #: If true, run Gmail/Calendar again when status is already executed (new draft/event).
    repeat_execution: Optional[bool] = False


class CreateManualActionBody(BaseModel):
    """Create a pending Calendar or Gmail action without AI suggestions."""

    connector: Literal["calendar", "gmail"]
    title: str = Field(..., min_length=1, description="Label shown on the action card in MeetingBox")
    description: str = ""
    # Calendar
    event_title: Optional[str] = None
    suggested_date: Optional[str] = None
    suggested_time: Optional[str] = None
    duration_minutes: int = Field(default=30, ge=1, le=24 * 60)
    timezone: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)
    # Gmail
    to: Optional[list[str]] = None
    cc: list[str] = Field(default_factory=list)
    subject: Optional[str] = None
    email_body: Optional[str] = None

    @model_validator(mode="after")
    def _require_connector_fields(self) -> "CreateManualActionBody":
        if self.connector == "calendar":
            if not (self.suggested_date or "").strip() or not (self.suggested_time or "").strip():
                raise ValueError("Calendar actions require suggested_date and suggested_time.")
        elif self.connector == "gmail":
            to_list = self.to or []
            if not any(str(x).strip() for x in to_list):
                raise ValueError("Gmail actions require at least one address in to.")
            if not (self.subject or "").strip() or not (self.email_body or "").strip():
                raise ValueError("Gmail actions require subject and email_body.")
        return self


@router.get("/meetings/{meeting_id}/actions", response_model=list[ActionResponse])
async def list_actions(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_meeting_access(meeting_id, current_actor)
    return list_actions_for_meeting(meeting_id)


@router.post("/meetings/{meeting_id}/actions/generate", response_model=list[ActionResponse])
async def generate_actions(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    _assert_meeting_access(meeting_id, current_actor)
    user_id = current_actor["user"]["id"] if current_actor else None
    return generate_actions_for_meeting(meeting_id, user_id)


@router.post("/meetings/{meeting_id}/actions/manual", response_model=ActionResponse)
async def create_manual_action(
    meeting_id: str,
    body: CreateManualActionBody,
    current_actor: Optional[dict] = Depends(get_optional_actor),
):
    if not current_actor:
        raise HTTPException(status_code=401, detail="Authentication required.")
    _assert_meeting_access(meeting_id, current_actor)
    user_id = current_actor["user"]["id"]
    return create_manual_action_record(
        meeting_id,
        user_id,
        body.connector,
        title=body.title,
        description=body.description or "",
        event_title=body.event_title,
        suggested_date=body.suggested_date,
        suggested_time=body.suggested_time,
        duration_minutes=body.duration_minutes,
        timezone=body.timezone,
        attendees=body.attendees,
        to=body.to,
        cc=body.cc,
        subject=body.subject,
        email_body=body.email_body,
    )


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


@router.post("/actions/{action_id}/ignore")
async def ignore_action(action_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
    """Stop showing the suggestion and exclude it from pending counts; row is kept."""
    _assert_action_access(action_id, current_actor)
    return ignore_action_record(action_id)


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
    repeat = bool(getattr(body, "repeat_execution", False)) if body else False
    return execute_action_record(
        action_id,
        user_id,
        override,
        create_draft=draft,
        repeat_execution=repeat,
    )
