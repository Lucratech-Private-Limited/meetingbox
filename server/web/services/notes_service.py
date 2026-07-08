"""Personal notes service — CRUD for user_notes table."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(cursor, row) -> dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(t) for t in parsed if t]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def upsert_note(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Create or update a note. Pass note_id in payload to update an existing one.

    Required for create: title or content (at least one non-empty).
    Returns the saved row as a dict.
    """
    from database import get_connection

    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")

    note_id = str(payload.get("note_id") or payload.get("id") or "").strip()
    now = _now_iso()

    conn = get_connection()
    conn.row_factory = _row_to_dict  # type: ignore[assignment]
    try:
        cur = conn.cursor()

        if note_id:
            # UPDATE — verify ownership first.
            cur.execute(
                "SELECT * FROM user_notes WHERE id = ? AND user_id = ?",
                (note_id, uid),
            )
            existing = cur.fetchone()
            if not existing:
                raise ValueError(f"Note {note_id!r} not found or access denied")

            title = str(payload["title"]).strip() if "title" in payload else existing["title"]
            content_raw = payload.get("content") if "content" in payload else None
            if content_raw is None:
                content = existing["content"]
            elif payload.get("append"):
                sep = "\n\n" if existing["content"].strip() else ""
                content = existing["content"] + sep + str(content_raw)
            else:
                content = str(content_raw)

            tags_json = (
                json.dumps([str(t) for t in payload["tags"] if t])
                if "tags" in payload
                else existing["tags"]
            )
            pinned = int(bool(payload["pinned"])) if "pinned" in payload else existing["pinned"]

            conn.execute(
                """
                UPDATE user_notes
                SET title = ?, content = ?, tags = ?, pinned = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (title, content, tags_json, pinned, now, note_id, uid),
            )
            conn.commit()
            cur.execute(
                "SELECT * FROM user_notes WHERE id = ? AND user_id = ?",
                (note_id, uid),
            )
            row = cur.fetchone()
        else:
            # INSERT
            title = str(payload.get("title") or "").strip()
            content = str(payload.get("content") or "").strip()
            if not title and not content:
                raise ValueError("title or content is required to create a note")

            tags_raw = payload.get("tags") or []
            tags_json = json.dumps([str(t) for t in (tags_raw if isinstance(tags_raw, list) else []) if t])
            pinned = int(bool(payload.get("pinned", False)))
            source = str(payload.get("source") or "manual").strip()
            new_id = str(uuid.uuid4())

            conn.execute(
                """
                INSERT INTO user_notes (id, user_id, title, content, tags, pinned, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id, uid, title, content, tags_json, pinned, source, now, now),
            )
            conn.commit()
            cur.execute(
                "SELECT * FROM user_notes WHERE id = ? AND user_id = ?",
                (new_id, uid),
            )
            row = cur.fetchone()

        if not row:
            raise RuntimeError("Note save failed — row not found after write")
        row["tags"] = _parse_tags(row.get("tags"))
        row["pinned"] = bool(row.get("pinned"))
        return dict(row)
    finally:
        conn.close()


def list_notes(
    user_id: str,
    *,
    limit: int = 50,
    pinned_only: bool = False,
    tag_filter: str | None = None,
    search: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return notes for a user, newest-first. All filters are additive.

    ``search`` matches on a per-keyword basis (a note matches if ANY keyword
    appears in its title or content) so callers can pass natural phrases like
    "board meeting notes" rather than an exact title.

    ``date_from`` / ``date_to`` (YYYY-MM-DD or ISO) filter by creation date.
    """
    import re as _re
    from database import get_connection

    uid = (user_id or "").strip()
    if not uid:
        return []

    conn = get_connection()
    conn.row_factory = _row_to_dict  # type: ignore[assignment]
    try:
        conditions = ["user_id = ?"]
        params: list[Any] = [uid]

        if pinned_only:
            conditions.append("pinned = 1")

        if tag_filter:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag_filter}%")

        if search:
            # Per-keyword OR match (drop trivial tokens) so partial keywords work.
            terms = [t for t in _re.split(r"[^\w]+", search.lower()) if len(t) > 2]
            if terms:
                ors: list[str] = []
                for t in terms:
                    ors.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ?)")
                    like = f"%{t}%"
                    params.append(like)
                    params.append(like)
                conditions.append("(" + " OR ".join(ors) + ")")
            else:
                conditions.append("(title LIKE ? OR content LIKE ?)")
                like = f"%{search}%"
                params.append(like)
                params.append(like)

        if date_from:
            conditions.append("COALESCE(created_at, updated_at, '') >= ?")
            params.append(str(date_from)[:10])
        if date_to:
            conditions.append("COALESCE(created_at, updated_at, '') <= ?")
            params.append(str(date_to)[:10] + "T23:59:59")

        where = " AND ".join(conditions)
        params.append(max(1, min(int(limit), 200)))

        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT * FROM user_notes
            WHERE {where}
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            r = dict(r)
            r["tags"] = _parse_tags(r.get("tags"))
            r["pinned"] = bool(r.get("pinned"))
            result.append(r)
        return result
    finally:
        conn.close()


def get_note(user_id: str, note_id: str) -> dict[str, Any] | None:
    """Fetch a single note by id, scoped to user_id."""
    from database import get_connection

    uid = (user_id or "").strip()
    nid = (note_id or "").strip()
    if not uid or not nid:
        return None

    conn = get_connection()
    conn.row_factory = _row_to_dict  # type: ignore[assignment]
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM user_notes WHERE id = ? AND user_id = ?",
            (nid, uid),
        )
        row = cur.fetchone()
        if not row:
            return None
        row = dict(row)
        row["tags"] = _parse_tags(row.get("tags"))
        row["pinned"] = bool(row.get("pinned"))
        return row
    finally:
        conn.close()


def delete_note(user_id: str, note_id: str) -> bool:
    """Delete a note. Returns True if a row was deleted, False if not found."""
    from database import get_connection

    uid = (user_id or "").strip()
    nid = (note_id or "").strip()
    if not uid or not nid:
        return False

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM user_notes WHERE id = ? AND user_id = ?",
            (nid, uid),
        )
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("notes_service: deleted note id=%s user=%s", nid, uid)
        return deleted
    finally:
        conn.close()
