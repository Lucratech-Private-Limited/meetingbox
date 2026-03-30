"""Memory / meeting-archive tools — read-only search over local SQLite meetings."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from database import get_connection

logger = logging.getLogger("meetingbox.memory_tool")


def _row_factory(cursor, row):
  return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _keywords(query: str) -> list[str]:
  q = (query or "").strip().lower()
  q = re.sub(r"[^\w\s@-]", " ", q)
  return [w for w in q.split() if len(w) > 1][:12]


def memory_search_meetings(
  user_id: str | None,
  query: str,
  max_results: int = 12,
) -> dict[str, Any]:
  """
  Find meetings whose title, summaries, or transcript segments match the query.
  When query is empty, returns the most recent meetings (newest first).
  user_id reserved for future per-user scoping; currently ignored.
  """
  _ = user_id
  max_results = max(1, min(int(max_results or 10), 30))
  conn = get_connection()
  conn.row_factory = _row_factory
  try:
    cur = conn.cursor()
    words = _keywords(query)
    if not words:
      cur.execute(
        """
        SELECT m.id, m.title, m.start_time, m.end_time, m.created_at, m.status
        FROM meetings m
        ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
        LIMIT ?
        """,
        (max_results,),
      )
    else:
      cond_parts: list[str] = []
      params: list[Any] = []
      for w in words:
        pat = f"%{w}%"
        cond_parts.append(
          "("
          "LOWER(COALESCE(m.title, '')) LIKE ? OR "
          "LOWER(COALESCE(s.summary, '')) LIKE ? OR "
          "LOWER(COALESCE(ls.summary, '')) LIKE ? OR "
          "LOWER(COALESCE(ls.discussion_points, '')) LIKE ? OR "
          "LOWER(COALESCE(seg.text, '')) LIKE ?"
          ")"
        )
        params.extend([pat, pat, pat, pat, pat])
      where_sql = " AND ".join(cond_parts)
      params.append(max_results)
      cur.execute(
        f"""
        SELECT DISTINCT m.id, m.title, m.start_time, m.end_time, m.created_at, m.status
        FROM meetings m
        LEFT JOIN summaries s ON s.meeting_id = m.id
        LEFT JOIN local_summaries ls ON ls.meeting_id = m.id
        LEFT JOIN segments seg ON seg.meeting_id = m.id
        WHERE {where_sql}
        ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
        LIMIT ?
        """,
        params,
      )

    rows = cur.fetchall()
    meetings = []
    for r in rows:
      meetings.append({
        "id": r["id"],
        "title": r.get("title") or "(untitled)",
        "start_time": r.get("start_time"),
        "end_time": r.get("end_time"),
        "created_at": r.get("created_at"),
        "status": r.get("status"),
      })
    return {
      "meetings": meetings,
      "count": len(meetings),
      "query_keywords": words,
    }
  finally:
    conn.close()


def memory_fetch_meeting(
  user_id: str | None,
  meeting_id: str,
  max_segments: int = 80,
  max_total_chars: int = 20000,
) -> dict[str, Any]:
  """Load one meeting: metadata, best available summary, and transcript excerpt."""
  _ = user_id
  mid = (meeting_id or "").strip()
  if not mid:
    return {"error": "meeting_id is required"}

  max_segments = max(1, min(int(max_segments), 200))
  max_total_chars = max(500, min(int(max_total_chars), 80000))

  conn = get_connection()
  conn.row_factory = _row_factory
  try:
    cur = conn.cursor()
    cur.execute("SELECT * FROM meetings WHERE id = ?", (mid,))
    meeting = cur.fetchone()
    if not meeting:
      return {"error": "Meeting not found", "meeting_id": mid}

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (mid,))
    summary_row = cur.fetchone()
    cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (mid,))
    local_row = cur.fetchone()

    summary_text = ""
    action_items: list[Any] = []
    decisions: list[Any] = []
    topics: list[Any] = []
    sentiment = ""
    summary_source = ""

    def _apply_summary_row(row: dict, src: str) -> None:
      nonlocal summary_text, action_items, decisions, topics, sentiment, summary_source
      st = (row.get("summary") or "").strip()
      if not st:
        return
      summary_text = row["summary"] or ""
      summary_source = src
      try:
        action_items = json.loads(row.get("action_items") or "[]")
      except json.JSONDecodeError:
        action_items = []
      try:
        decisions = json.loads(row.get("decisions") or "[]")
      except json.JSONDecodeError:
        decisions = []
      try:
        topics = json.loads(row.get("topics") or "[]")
      except json.JSONDecodeError:
        topics = []
      sentiment = row.get("sentiment") or ""

    if summary_row and (summary_row.get("summary") or "").strip():
      _apply_summary_row(summary_row, "summaries")
    elif local_row:
      _apply_summary_row(local_row, "local_summaries")

    if not summary_text and local_row and (local_row.get("summary") or "").strip():
      _apply_summary_row(local_row, "local_summaries")

    cur.execute(
      """
      SELECT segment_num, text, start_time, end_time
      FROM segments
      WHERE meeting_id = ?
      ORDER BY segment_num ASC
      LIMIT ?
      """,
      (mid, max_segments),
    )
    seg_rows = cur.fetchall()
    transcript_parts: list[str] = []
    total = 0
    for sr in seg_rows:
      line = (sr.get("text") or "").strip()
      if not line:
        continue
      if total + len(line) + 1 > max_total_chars:
        break
      transcript_parts.append(line)
      total += len(line) + 1

    return {
      "meeting_id": mid,
      "title": meeting.get("title") or "(untitled)",
      "start_time": meeting.get("start_time"),
      "end_time": meeting.get("end_time"),
      "created_at": meeting.get("created_at"),
      "status": meeting.get("status"),
      "summary": summary_text,
      "summary_source": summary_source or "none",
      "action_items": action_items,
      "decisions": decisions,
      "topics": topics,
      "sentiment": sentiment,
      "transcript_excerpt": "\n".join(transcript_parts),
      "transcript_segments_included": len(transcript_parts),
    }
  finally:
    conn.close()
