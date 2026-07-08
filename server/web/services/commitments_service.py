"""User commitments (tasks / reminders) in SQLite — authoritative when Mem0 is off or fails."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

_VALID_STATUS = frozenset({"active", "completed", "cancelled", "canceled", "snoozed"})


def _row_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _normalize_tags(raw: Any) -> str:
    if raw is None:
        return "[]"
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return "[]"
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError:
            return json.dumps([s], ensure_ascii=False)
    if isinstance(raw, list):
        return json.dumps([str(x) for x in raw if x is not None], ensure_ascii=False)
    return json.dumps([str(raw)], ensure_ascii=False)


def list_commitments_for_user(
    user_id: str,
    *,
    status_filter: str | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """List commitments. status_filter: active|completed|snoozed|cancelled|all (default: open work)."""
    uid = (user_id or "").strip()
    if not uid:
        return []
    lim = max(1, min(int(limit), 100))
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        sf = (status_filter or "").strip().lower()
        if sf == "all":
            cur = conn.execute(
                """
                SELECT * FROM user_commitments
                WHERE user_id = ?
                ORDER BY datetime(COALESCE(updated_at, created_at)) DESC
                LIMIT ?
                """,
                (uid, lim),
            )
        elif sf in _VALID_STATUS:
            st = "cancelled" if sf == "canceled" else sf
            cur = conn.execute(
                """
                SELECT * FROM user_commitments
                WHERE user_id = ? AND status = ?
                ORDER BY datetime(COALESCE(remind_at, due_at, created_at)) ASC
                LIMIT ?
                """,
                (uid, st, lim),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM user_commitments
                WHERE user_id = ? AND status IN ('active', 'snoozed')
                ORDER BY datetime(COALESCE(remind_at, due_at, created_at)) ASC
                LIMIT ?
                """,
                (uid, lim),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def commitments_context_for_prompt(user_id: str, *, limit: int = 22) -> str:
    """Compact text block for LLM context (tags, status, dates).

    Filters to active/snoozed tasks only so cancelled and completed work
    does not pollute the LLM context and cause stale assumptions (Fix 7).
    """
    rows = list_commitments_for_user(user_id, status_filter=None, limit=limit)
    if not rows:
        return ""
    lines: list[str] = []
    for r in rows:
        try:
            tags = json.loads(r.get("tags") or "[]")
        except json.JSONDecodeError:
            tags = [r.get("tags")]
        tag_s = ",".join(str(t) for t in tags) if tags else ""
        lines.append(
            f"- [{r.get('status')}] {r.get('title')} (id={r.get('id')}) "
            f"tags=[{tag_s}] remind={r.get('remind_at') or '-'} due={r.get('due_at') or '-'} "
            f"cal={r.get('calendar_event_id') or '-'}"
        )
        det = (r.get("detail") or "").strip()
        if det:
            snip = det[:280] + ("…" if len(det) > 280 else "")
            lines.append(f"  detail: {snip}")
    return "\n".join(lines)


def upsert_commitment(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")

    cid = str(payload.get("commitment_id") or payload.get("id") or "").strip()

    title_in = str(payload.get("title") or "").strip()
    detail = None
    if "detail" in payload or "description" in payload:
        detail_raw = payload.get("detail") if "detail" in payload else payload.get("description")
        detail = (str(detail_raw).strip() if detail_raw is not None else None) or None
    tags_in = payload.get("tags") if "tags" in payload else None
    status_in = payload.get("status") if "status" in payload else None
    remind_in = payload.get("remind_at") if "remind_at" in payload else None
    due_in = payload.get("due_at") if "due_at" in payload else None
    source_in = payload.get("source") if "source" in payload else None
    cal_in = payload.get("calendar_event_id") if "calendar_event_id" in payload else None
    audit_in = payload.get("audit_id") if "audit_id" in payload else None
    meet_in = payload.get("meeting_id") if "meeting_id" in payload else None

    now = datetime.utcnow().isoformat()
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        if cid:
            cur = conn.execute("SELECT * FROM user_commitments WHERE id = ? AND user_id = ?", (cid, uid))
            existing = cur.fetchone()
            if not existing:
                raise ValueError("commitment not found or access denied")
            ex = dict(existing)
            new_title = title_in or ex.get("title") or ""
            new_detail = detail if ("detail" in payload or "description" in payload) else ex.get("detail")
            new_tags = _normalize_tags(tags_in) if tags_in is not None else (ex.get("tags") or "[]")
            st = str(status_in if status_in is not None else ex.get("status") or "active").strip().lower()
            if st == "canceled":
                st = "cancelled"
            if st not in _VALID_STATUS:
                st = "active"
            new_remind = (
                (str(remind_in).strip() or None)
                if remind_in is not None
                else ex.get("remind_at")
            )
            new_due = (str(due_in).strip() or None) if due_in is not None else ex.get("due_at")
            new_src = str(source_in).strip() if source_in is not None else (ex.get("source") or "chat")
            if cal_in is not None:
                new_cal = (str(cal_in).strip() or None) if cal_in else None
            else:
                new_cal = ex.get("calendar_event_id")
            new_audit = (str(audit_in).strip() or None) if audit_in else ex.get("audit_id")
            new_meet = (str(meet_in).strip() or None) if meet_in else ex.get("meeting_id")

            conn.execute(
                """
                UPDATE user_commitments SET
                  title = ?,
                  detail = ?,
                  tags = ?,
                  status = ?,
                  remind_at = ?,
                  due_at = ?,
                  source = ?,
                  calendar_event_id = ?,
                  audit_id = ?,
                  meeting_id = ?,
                  updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    new_title,
                    new_detail,
                    new_tags,
                    st,
                    new_remind,
                    new_due,
                    new_src,
                    new_cal,
                    new_audit,
                    new_meet,
                    now,
                    cid,
                    uid,
                ),
            )
            conn.commit()
            cur2 = conn.execute("SELECT * FROM user_commitments WHERE id = ? AND user_id = ?", (cid, uid))
            row = cur2.fetchone()
            return dict(row) if row else {}

        if not title_in:
            raise ValueError("title is required for new commitments")

        tags = _normalize_tags(tags_in)
        status = str(status_in or "active").strip().lower()
        if status == "canceled":
            status = "cancelled"
        if status not in _VALID_STATUS:
            status = "active"
        remind_at = (str(remind_in or "").strip() or None) if remind_in is not None else None
        due_at = (str(due_in or "").strip() or None) if due_in is not None else None
        source = (str(source_in or "chat").strip() or "chat") if source_in is not None else "chat"
        calendar_event_id = (str(cal_in).strip() or None) if cal_in else None
        audit_id = (str(audit_in).strip() or None) if audit_in else None
        meeting_id = (str(meet_in).strip() or None) if meet_in else None

        nid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO user_commitments (
              id, user_id, title, detail, tags, status, remind_at, due_at,
              source, calendar_event_id, audit_id, meeting_id, mem0_synced, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                nid,
                uid,
                title_in,
                detail,
                tags,
                status,
                remind_at,
                due_at,
                source,
                calendar_event_id,
                audit_id,
                meeting_id,
                now,
                now,
            ),
        )
        conn.commit()
        cur2 = conn.execute("SELECT * FROM user_commitments WHERE id = ? AND user_id = ?", (nid, uid))
        row = cur2.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def format_commitment_for_mem0(row: dict[str, Any]) -> str:
    """Rich single-record text for Mem0 (searchable commitments + dates + tags)."""
    try:
        tags = json.loads(row.get("tags") or "[]")
    except json.JSONDecodeError:
        tags = []
    tag_part = ", ".join(str(t) for t in tags) if tags else "(none)"
    return (
        f"User commitment [{row.get('status')}]: {row.get('title')}. "
        f"id={row.get('id')}. tags: {tag_part}. "
        f"remind_at={row.get('remind_at') or 'n/a'}. due_at={row.get('due_at') or 'n/a'}. "
        f"source={row.get('source') or 'unknown'}. "
        f"detail: {(row.get('detail') or '')[:6000]}"
    )
