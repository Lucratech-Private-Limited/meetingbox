"""User-facing memory management endpoints (Fix 8).

Allows authenticated users to inspect and delete their own Mem0 memories.
All endpoints require a valid JWT and enforce ownership — users can only see
and delete their own memories.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_actor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])


def _actor_user_id(actor: dict) -> str | None:
    try:
        return (str((actor.get("user") or {}).get("id") or "")).strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GET /api/memory/mine — list the current user's Mem0 memories
# ---------------------------------------------------------------------------

class MemoryListResponse(BaseModel):
    user_id: str
    hits: list[dict[str, Any]]
    count: int
    mem0_enabled: bool


@router.get("/memory/mine", response_model=MemoryListResponse)
async def list_my_memories(
    q: str = Query(default="", description="Search query (empty = all recent memories)"),
    top_k: int = Query(default=20, ge=1, le=50, description="Max results to return"),
    actor: dict = Depends(get_current_actor),
) -> MemoryListResponse:
    """Return the current user's Mem0 memories, optionally filtered by a query."""
    uid = _actor_user_id(actor)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    from services.mem0_service import search_memories_for_user

    query = (q or "").strip() or "preferences facts reminders tasks meetings"
    result = search_memories_for_user(uid, query, top_k=top_k)
    hits = result.get("hits") or []
    return MemoryListResponse(
        user_id=uid,
        hits=hits,
        count=len(hits),
        mem0_enabled=result.get("mem0_enabled", False),
    )


# ---------------------------------------------------------------------------
# GET /api/memory/sources — list distinct memory sources for the current user
# ---------------------------------------------------------------------------

class MemorySourcesResponse(BaseModel):
    sources: list[str]


@router.get("/memory/sources", response_model=MemorySourcesResponse)
async def list_memory_sources(actor: dict = Depends(get_current_actor)) -> MemorySourcesResponse:
    """Return the distinct source labels present in the current user's Mem0 store."""
    uid = _actor_user_id(actor)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    from services.mem0_service import search_memories_for_user, SOURCE_MEETING_SUMMARY, \
        SOURCE_CALENDAR, SOURCE_GMAIL, SOURCE_ASSISTANT_CHAT, SOURCE_USER_COMMITMENT, \
        SOURCE_MEETING_ARTIFACTS, SOURCE_ASSISTANT_PENDING_OUTCOME, SOURCE_VOICE_MEMORY

    all_sources = [
        SOURCE_MEETING_SUMMARY,
        SOURCE_CALENDAR,
        SOURCE_GMAIL,
        SOURCE_ASSISTANT_CHAT,
        SOURCE_USER_COMMITMENT,
        SOURCE_MEETING_ARTIFACTS,
        SOURCE_ASSISTANT_PENDING_OUTCOME,
        SOURCE_VOICE_MEMORY,
    ]
    # Search broadly and collect distinct sources from hits.
    result = search_memories_for_user(uid, "preferences facts meetings tasks", top_k=50)
    hits = result.get("hits") or []
    present: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        src = ((hit.get("metadata") or {}).get("source") or "").strip()
        if src and src not in seen:
            seen.add(src)
            present.append(src)
    # Ensure all canonical sources are included even if not returned by search.
    for s in all_sources:
        if s not in seen:
            present.append(s)
    return MemorySourcesResponse(sources=present)


# ---------------------------------------------------------------------------
# DELETE /api/memory/{memory_id} — soft-delete one memory for the current user
# ---------------------------------------------------------------------------

class DeleteMemoryResponse(BaseModel):
    deleted: bool
    memory_id: str


@router.delete("/memory/{memory_id}", response_model=DeleteMemoryResponse)
async def delete_my_memory(
    memory_id: str,
    actor: dict = Depends(get_current_actor),
) -> DeleteMemoryResponse:
    """Soft-delete a specific Mem0 memory for the current user.

    The memory is hidden from all future searches but remains in the vector
    store and can be restored by an admin. Returns 404 if the memory does not
    belong to this user or does not exist in their search results.
    """
    uid = _actor_user_id(actor)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")

    mid = (memory_id or "").strip()
    if not mid:
        raise HTTPException(status_code=400, detail="memory_id is required")

    # Ownership check: verify this memory_id appears in the user's search results.
    from services.mem0_service import search_memories_for_user, soft_delete_memory

    result = search_memories_for_user(uid, "preferences facts meetings tasks", top_k=100)
    hits = result.get("hits") or []
    owned_ids = {h.get("id") for h in hits if h.get("id")}
    if mid not in owned_ids:
        # Memory either doesn't exist or belongs to another user.
        raise HTTPException(status_code=404, detail="Memory not found for this user")

    ok = soft_delete_memory(uid, mid, deleted_by="user")
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete memory — it may already be deleted")

    logger.info("user memory soft-deleted user=%s memory_id=%s", uid, mid)
    return DeleteMemoryResponse(deleted=True, memory_id=mid)
