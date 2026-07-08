"""HTTP API for personal notes (user_notes table)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_actor
from services.notes_service import delete_note, get_note, list_notes, upsert_note

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Request bodies ────────────────────────────────────────────────────────────


class NoteCreate(BaseModel):
    title: str = ""
    content: str = ""
    tags: list[str] = []
    pinned: bool = False
    source: str = "manual"


class NotePatch(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    pinned: Optional[bool] = None
    append: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/notes/_debug")
async def notes_debug(actor: dict = Depends(get_current_actor)):
    """Diagnostic: shows which user_id the caller resolves to, how many notes
    that user has, and the total notes across all users. Lets us detect a
    user_id mismatch between the web account and the device voice agent.

    Hit this from the web app AND note the user_id; the voice agent logs its own
    user_id via NOTE_LIST — compare the two.
    """
    user_id = actor["user"]["id"]
    from database import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM user_notes WHERE user_id = ?", (user_id,))
        mine = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM user_notes")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT DISTINCT user_id FROM user_notes LIMIT 20"
        )
        distinct_users = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return {
        "resolved_user_id": user_id,
        "actor_type": actor.get("type"),
        "my_notes_count": mine,
        "total_notes_all_users": total,
        "distinct_user_ids_with_notes": distinct_users,
    }


@router.get("/notes")
async def list_user_notes(
    actor: dict = Depends(get_current_actor),
    limit: int = Query(50, ge=1, le=200),
    pinned: bool = Query(False),
    tag: str = Query("", max_length=100),
    search: str = Query("", max_length=200),
):
    """List notes for the signed-in user, newest first.

    Pinned notes always sort to the top. Optional filters:
      - pinned=true    — only pinned notes
      - tag=<str>      — notes whose tags JSON contains <str>
      - search=<str>   — notes whose title or content contains <str>
    """
    user_id = actor["user"]["id"]
    rows = list_notes(
        user_id,
        limit=limit,
        pinned_only=bool(pinned),
        tag_filter=tag.strip() or None,
        search=search.strip() or None,
    )
    return {"notes": rows, "count": len(rows)}


@router.post("/notes", status_code=201)
async def create_user_note(
    body: NoteCreate,
    actor: dict = Depends(get_current_actor),
):
    """Create a new note. At least one of title or content must be non-empty."""
    user_id = actor["user"]["id"]
    if not body.title.strip() and not body.content.strip():
        raise HTTPException(status_code=400, detail="title or content is required")
    try:
        row = upsert_note(user_id, {
            "title": body.title,
            "content": body.content,
            "tags": body.tags,
            "pinned": body.pinned,
            "source": body.source or "manual",
        })
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _bg_ingest(user_id, row)
    return row


@router.get("/notes/{note_id}")
async def get_user_note(
    note_id: str,
    actor: dict = Depends(get_current_actor),
):
    """Fetch a single note by id."""
    user_id = actor["user"]["id"]
    row = get_note(user_id, note_id)
    if not row:
        raise HTTPException(status_code=404, detail="Note not found")
    return row


@router.patch("/notes/{note_id}")
async def patch_user_note(
    note_id: str,
    body: NotePatch,
    actor: dict = Depends(get_current_actor),
):
    """Update a note. Only provided fields are changed.

    Pass append=true with content to append to existing content instead of
    replacing it (useful for voice "add to my note" flow).
    """
    user_id = actor["user"]["id"]
    if (
        body.title is None
        and body.content is None
        and body.tags is None
        and body.pinned is None
    ):
        raise HTTPException(status_code=400, detail="At least one field must be provided")
    payload: dict = {"note_id": note_id}
    if body.title is not None:
        payload["title"] = body.title
    if body.content is not None:
        payload["content"] = body.content
        payload["append"] = body.append
    if body.tags is not None:
        payload["tags"] = body.tags
    if body.pinned is not None:
        payload["pinned"] = body.pinned
    try:
        row = upsert_note(user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _bg_ingest(user_id, row)
    return row


@router.delete("/notes/{note_id}", status_code=204)
async def delete_user_note(
    note_id: str,
    actor: dict = Depends(get_current_actor),
):
    """Delete a note permanently. Also removes it from Mem0."""
    user_id = actor["user"]["id"]
    deleted = delete_note(user_id, note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    # Remove from Mem0 so the voice agent stops recalling it.
    try:
        from services.mem0_service import delete_note_from_mem0
        delete_note_from_mem0(user_id, note_id)
    except Exception:
        logger.debug("note mem0 delete skipped note_id=%s", note_id, exc_info=True)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _bg_ingest(user_id: str, row: dict) -> None:
    """Fire-and-forget Mem0 ingest after a note save."""
    try:
        from services.mem0_service import maybe_ingest_note
        maybe_ingest_note(user_id, row)
    except Exception:
        logger.debug("note mem0 ingest skipped note_id=%s", row.get("id"), exc_info=True)
