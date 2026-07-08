"""HTTP API for user commitments (tasks / reminders in SQLite)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_actor
from services.commitments_service import list_commitments_for_user
from services.tasks_service import (
    AmbiguousTaskMatchError,
    SimilarTaskExistsError,
    TaskFidelityError,
    TaskNotFoundError,
    voice_create_task,
    voice_update_task,
)

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


class CommitmentCreate(BaseModel):
    title: str
    due_date: str | None = None
    description: str | None = None
    confirm_duplicate: bool = False
    source: str = "manual"


@router.post("/commitments")
async def create_user_commitment(
    body: CommitmentCreate,
    actor: dict = Depends(get_current_actor),
):
    """Create a task. Used by the device Tasks screen '+ Add' button.

    Honors the same fidelity + duplicate guardrails as the voice path. On a
    duplicate hit, returns HTTP 409 with the existing task so the UI can ask
    the user whether to update or add anyway (pass confirm_duplicate=true).
    """
    user_id = actor["user"]["id"]
    try:
        row = voice_create_task(
            user_id=user_id,
            title=body.title,
            due_date=body.due_date,
            description=body.description,
            confirm_duplicate=bool(body.confirm_duplicate),
            source=(body.source or "manual"),
        )
    except SimilarTaskExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "similar_task_exists", "similar": exc.similar},
        )
    except TaskFidelityError as exc:
        raise HTTPException(status_code=400, detail={"error": "task_fidelity", "detail": str(exc)})
    try:
        from services.mem0_service import maybe_ingest_commitment_row
        maybe_ingest_commitment_row(user_id, row)
    except Exception:
        logger.debug("commitment create mem0 ingest failed", exc_info=True)
    return row


class CommitmentPatch(BaseModel):
    status: str | None = None
    due_date: str | None = None
    title: str | None = None
    description: str | None = None


@router.patch("/commitments/{commitment_id}")
async def patch_commitment(
    commitment_id: str,
    body: CommitmentPatch,
    actor: dict = Depends(get_current_actor),
):
    """Update a single commitment.

    Supports:
      • status change (active / snoozed / completed / cancelled)
      • due_date assignment / re-assignment (sets due_at on the task)
      • title / description edit (from the Tasks screen Edit menu)
    """
    user_id = actor["user"]["id"]
    if (
        body.status is None
        and body.due_date is None
        and body.title is None
        and body.description is None
    ):
        raise HTTPException(
            status_code=400, detail="status, due_date, title or description required"
        )
    try:
        row = voice_update_task(
            user_id=user_id,
            task_id=commitment_id,
            title=body.title,
            status=body.status,
            due_date=body.due_date,
            description=body.description,
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AmbiguousTaskMatchError as exc:
        raise HTTPException(status_code=400, detail={"error": "ambiguous", "candidates": exc.candidates})
    except TaskFidelityError as exc:
        raise HTTPException(status_code=400, detail={"error": "task_fidelity", "detail": str(exc)})
    try:
        from services.mem0_service import maybe_ingest_commitment_row
        maybe_ingest_commitment_row(user_id, row)
    except Exception:
        logger.debug("commitment patch mem0 ingest failed", exc_info=True)
    return row
