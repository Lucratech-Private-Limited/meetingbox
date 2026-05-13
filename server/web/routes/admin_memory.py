"""Admin-only memory inspection (Mem0 + meeting metadata). Enterprise / support use."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import get_current_admin
from database import DB_PATH, get_connection
from rate_limit import limiter
from services.mem0_service import delete_user_memories, search_memories_for_user

router = APIRouter(prefix="/admin", tags=["admin-memory"])


_MEETINGS_SCOPE_SQL = (
    "(m.user_id = ? OR m.device_id IN ("
    " SELECT id FROM devices WHERE user_id = ? "
    " AND (status IS NULL OR TRIM(COALESCE(status, '')) = '' OR LOWER(TRIM(status)) = 'active')))"
)


def _log_access(admin_id: str, target_user_id: str, action: str, detail: str = "") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO admin_memory_access_log
              (id, created_at, admin_user_id, target_user_id, action, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                admin_id,
                target_user_id,
                action,
                (detail or "")[:2000],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _user_exists(user_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


@router.get("/users/{user_id}/memories")
@limiter.limit("60/minute")
def admin_search_user_memories(
    request: Request,
    user_id: str,
    q: str = Query("", max_length=2000),
    top_k: int = Query(8, ge=1, le=50),
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    if not _user_exists(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    _log_access(admin["id"], user_id, "memories_search", f"q={q[:200]!r}")
    return search_memories_for_user(user_id, q, top_k=top_k)


@router.delete("/users/{user_id}/memories")
@limiter.limit("10/minute")
def admin_purge_user_memories(
    request: Request,
    user_id: str,
    admin: dict = Depends(get_current_admin),
) -> dict[str, str]:
    if not _user_exists(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    _log_access(admin["id"], user_id, "memories_purge", "")
    delete_user_memories(user_id)
    return {"status": "ok", "detail": "Mem0 memories deleted for user (best-effort)."}


@router.get("/users/{user_id}/meetings-summary")
@limiter.limit("60/minute")
def admin_user_meetings_summary(
    request: Request,
    user_id: str,
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    if not _user_exists(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    _log_access(admin["id"], user_id, "meetings_summary", f"limit={limit}")
    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT COUNT(*) AS n FROM meetings m
            WHERE {_MEETINGS_SCOPE_SQL}
            """,
            (user_id, user_id),
        )
        total_row = cur.fetchone() or {}
        total = int(total_row.get("n") or 0)
        cur.execute(
            f"""
            SELECT m.id, m.title, m.status, m.created_at, m.start_time, m.user_id, m.device_id
            FROM meetings m
            WHERE {_MEETINGS_SCOPE_SQL}
            ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
            LIMIT ?
            """,
            (user_id, user_id, limit),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"user_id": user_id, "meetings_total": total, "meetings": rows}


@router.get("/memory-access-log")
@limiter.limit("30/minute")
def admin_list_memory_access_log(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """Last N audit rows (all admins); for compliance review."""
    conn = get_connection()
    conn.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, created_at, admin_user_id, target_user_id, action, detail
            FROM admin_memory_access_log
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return {"entries": rows}


@router.post("/backup/database")
@limiter.limit("5/minute")
def admin_backup_sqlite_database(
    request: Request,
    admin: dict = Depends(get_current_admin),
) -> dict[str, Any]:
    """
    Hot-copy the SQLite DB to MEETINGBOX_BACKUP_DIR (default /data/backups).
    Does not replace enterprise backup automation; use for quick snapshots before migration.
    """
    src = Path(DB_PATH).resolve()
    if not src.is_file():
        raise HTTPException(status_code=500, detail="Database file not found at MEETINGBOX_DB_PATH.")
    backup_root = Path(os.getenv("MEETINGBOX_BACKUP_DIR", "/data/backups")).expanduser()
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Cannot create backup directory: {e}") from e
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = (backup_root / f"meetings-{ts}.db").resolve()
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Backup copy failed: {e}") from e
    _log_access(admin["id"], admin["id"], "database_backup", str(dest)[:1900])
    try:
        size = dest.stat().st_size
    except OSError:
        size = -1
    return {"path": str(dest), "bytes": size, "source": str(src)}
