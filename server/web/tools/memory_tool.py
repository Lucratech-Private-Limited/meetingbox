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


def _scope_clause_for_user(user_id: str | None) -> tuple[str, list[Any]]:
  """Restrict to meetings owned by user or recorded on a device paired to that user.

  The device_id branch is a fallback used only when the meeting has no user_id (old recordings
  created before user attribution was added). Meetings with an explicit user_id are always
  restricted to that user, preventing cross-user leaks when devices change ownership.
  """
  if not user_id or not str(user_id).strip():
    return "1 = 0", []
  uid = str(user_id).strip()
  pred = (
    "(m.user_id = ? OR "
    "(COALESCE(TRIM(m.user_id), '') = '' AND m.device_id IN ("
    " SELECT id FROM devices WHERE user_id = ? "
    " AND (status IS NULL OR TRIM(COALESCE(status, '')) = '' OR LOWER(TRIM(status)) = 'active'))))"
  )
  return pred, [uid, uid]


def memory_search_meetings(
  user_id: str | None,
  query: str,
  max_results: int = 12,
  date_from: str | None = None,
  date_to: str | None = None,
  participant: str | None = None,
  session_type: str | None = None,
) -> dict[str, Any]:
  """
  Ranked, context-aware search over a user's recordings (notes + meetings).

  Unlike the old "match keywords, return newest" behavior, this now scores every
  candidate by participants, context/intent tags, projects/events, transcript,
  summary, and semantic similarity, using recency only as a tie-breaker. The
  result carries a relevance ``score`` per item and may flag
  ``needs_clarification`` when several recordings are plausible.

  Optional filters:
    date_from / date_to -- ISO date strings; narrow by time.
    participant         -- name fragment; biases toward that person.
    session_type        -- 'note' or 'meeting' to restrict the kind of recording.
  """
  # Delegate to the ranked retrieval engine; fall back to the legacy keyword
  # query only if the new engine is unavailable.
  try:
    return _ranked_search(
      user_id, query, max_results, date_from, date_to, participant, session_type
    )
  except Exception:
    logger.warning("ranked recording search failed; using legacy keyword search", exc_info=True)
  return _legacy_search_meetings(
    user_id, query, max_results, date_from, date_to, participant
  )


def _ranked_search(
  user_id: str | None,
  query: str,
  max_results: int,
  date_from: str | None,
  date_to: str | None,
  participant: str | None,
  session_type: str | None,
) -> dict[str, Any]:
  from services.recording_search import search_recordings

  # Fold explicit filters the planner may pass into the query text so the
  # parser picks them up (participant name, date hints).
  q = query or ""
  if participant and participant.strip() and participant.strip().lower() not in q.lower():
    q = f"{q} with {participant.strip()}".strip()

  res = search_recordings(
    user_id, q, session_type=session_type, limit=max(int(max_results or 8), 8)
  )

  # Apply explicit date filters (the parser handles relative time; these are
  # absolute overrides from a planner).
  results = res.get("results") or []
  if date_from:
    results = [r for r in results if (r.get("start_time") or r.get("created_at") or "") >= date_from[:10]]
  if date_to:
    results = [r for r in results if (r.get("start_time") or r.get("created_at") or "") <= date_to[:10] + "T23:59:59"]

  meetings = [
    {
      "id": r["meeting_id"],
      "title": r.get("title") or "(untitled)",
      "start_time": r.get("start_time"),
      "end_time": None,
      "created_at": r.get("created_at"),
      "status": None,
      "participants": r.get("participants"),
      "session_type": r.get("session_type"),
      "score": r.get("score"),
      "signals": r.get("signals"),
      "date": r.get("date"),
      "time": r.get("time"),
      "snippet": r.get("snippet"),
    }
    for r in results[:max_results]
  ]
  return {
    "meetings": meetings,
    "count": len(meetings),
    "needs_clarification": res.get("needs_clarification", False),
    "clarification": res.get("clarification"),
    "confident": res.get("confident", False),
    "query_keywords": (res.get("parsed") or {}).get("keywords", []),
    "filters_applied": {
      "date_from": date_from,
      "date_to": date_to,
      "participant": participant,
      "session_type": session_type or (res.get("parsed") or {}).get("session_type"),
    },
  }


def _legacy_search_meetings(
  user_id: str | None,
  query: str,
  max_results: int = 12,
  date_from: str | None = None,
  date_to: str | None = None,
  participant: str | None = None,
) -> dict[str, Any]:
  """Original keyword/LIKE search, kept as a safety net."""
  max_results = max(1, min(int(max_results or 10), 30))
  scope_sql, scope_params = _scope_clause_for_user(user_id)
  conn = get_connection()
  conn.row_factory = _row_factory
  try:
    cur = conn.cursor()
    words = _keywords(query)

    # Build optional date + participant filter clauses.
    extra_clauses: list[str] = []
    extra_params: list[Any] = []
    if date_from:
      extra_clauses.append("COALESCE(m.start_time, m.created_at, '') >= ?")
      extra_params.append(date_from[:10])
    if date_to:
      # Include the full end-of-day by comparing as text prefix.
      extra_clauses.append("COALESCE(m.start_time, m.created_at, '') <= ?")
      extra_params.append(date_to[:10] + "T23:59:59")
    if participant:
      # participants column stores JSON array or comma-separated string.
      extra_clauses.append("LOWER(COALESCE(m.participants, '')) LIKE ?")
      extra_params.append(f"%{participant.strip().lower()}%")

    extra_sql = (" AND " + " AND ".join(extra_clauses)) if extra_clauses else ""

    if not words:
      cur.execute(
        f"""
        SELECT m.id, m.title, m.start_time, m.end_time, m.created_at, m.status, m.participants
        FROM meetings m
        WHERE {scope_sql}{extra_sql}
          AND (m.recording_mode IS NULL OR m.recording_mode != 'note')
        ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
        LIMIT ?
        """,
        (*scope_params, *extra_params, max_results),
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
      # scope_params must come AFTER word params because scope_sql appears
      # last in the WHERE clause (word LIKE params are bound first).
      all_params = [*params, *scope_params, *extra_params, max_results]
      cur.execute(
        f"""
        SELECT DISTINCT m.id, m.title, m.start_time, m.end_time, m.created_at, m.status, m.participants
        FROM meetings m
        LEFT JOIN summaries s ON s.meeting_id = m.id
        LEFT JOIN local_summaries ls ON ls.meeting_id = m.id
        LEFT JOIN segments seg ON seg.meeting_id = m.id
        WHERE ({where_sql}) AND ({scope_sql}){extra_sql}
          AND (m.recording_mode IS NULL OR m.recording_mode != 'note')
        ORDER BY COALESCE(m.created_at, m.start_time, '') DESC
        LIMIT ?
        """,
        all_params,
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
        "participants": r.get("participants"),
      })
    return {
      "meetings": meetings,
      "count": len(meetings),
      "query_keywords": words,
      "filters_applied": {
        "date_from": date_from,
        "date_to": date_to,
        "participant": participant,
      },
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
  mid = (meeting_id or "").strip()
  if not mid:
    return {"error": "meeting_id is required"}

  max_segments = max(1, min(int(max_segments), 200))
  max_total_chars = max(500, min(int(max_total_chars), 80000))

  scope_sql, scope_params = _scope_clause_for_user(user_id)
  conn = get_connection()
  conn.row_factory = _row_factory
  try:
    cur = conn.cursor()
    # Fetch by explicit id returns whatever was requested — meeting OR note.
    # (The ranked search already applies the session-type filter upstream, so
    # excluding notes here would make note retrieval impossible.)
    cur.execute(
      f"SELECT * FROM meetings m WHERE m.id = ? AND {scope_sql}",
      (mid, *scope_params),
    )
    meeting = cur.fetchone()
    if not meeting:
      return {"error": "Recording not found", "meeting_id": mid}

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

    # Enrich with recording type, participants, tags, and formatted date/time so
    # the assistant can tell the user WHEN it was recorded and WHO was involved.
    recording_mode = (meeting.get("recording_mode") or "meeting")
    participants = _coerce_name_list(meeting.get("participants"))
    tags: list[str] = []
    ctx_intent = ""
    try:
      cur.execute(
        "SELECT referenced_people, intent_tags, context_tags, referenced_events, "
        "referenced_projects, session_intent FROM recording_context WHERE meeting_id = ?",
        (mid,),
      )
      ctx = cur.fetchone()
      if ctx:
        for nm in _coerce_name_list(ctx.get("referenced_people")):
          if nm not in participants:
            participants.append(nm)
        for fld in ("intent_tags", "context_tags", "referenced_events", "referenced_projects"):
          for tg in _coerce_name_list(ctx.get(fld)):
            if tg not in tags:
              tags.append(tg)
        ctx_intent = (ctx.get("session_intent") or "").strip()
    except Exception:
      pass

    when = meeting.get("start_time") or meeting.get("created_at")
    return {
      "meeting_id": mid,
      "title": meeting.get("title") or "(untitled)",
      "recording_mode": recording_mode,
      "session_type": recording_mode,
      "start_time": meeting.get("start_time"),
      "end_time": meeting.get("end_time"),
      "created_at": meeting.get("created_at"),
      "date": _fmt_date(when),
      "time": _fmt_time(when),
      "status": meeting.get("status"),
      "participants": participants,
      "tags": tags,
      "session_intent": ctx_intent,
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


def _coerce_name_list(raw: Any) -> list[str]:
  """Parse a JSON array or comma-separated string into a clean name list."""
  if not raw:
    return []
  items: list[Any]
  if isinstance(raw, list):
    items = raw
  else:
    try:
      parsed = json.loads(raw)
      items = parsed if isinstance(parsed, list) else re.split(r"[,;]+", str(raw))
    except (json.JSONDecodeError, TypeError):
      items = re.split(r"[,;]+", str(raw))
  out: list[str] = []
  seen: set[str] = set()
  for it in items:
    s = (it.get("name") if isinstance(it, dict) else str(it)).strip() if it is not None else ""
    if s and s.lower() not in seen:
      seen.add(s.lower())
      out.append(s)
  return out


def _fmt_date(val: Any) -> str:
  if not val:
    return ""
  try:
    from datetime import datetime as _dt
    d = _dt.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
    return d.strftime("%b %d, %Y")
  except (ValueError, TypeError):
    return str(val)[:10]


def _fmt_time(val: Any) -> str:
  if not val:
    return ""
  try:
    from datetime import datetime as _dt
    d = _dt.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
    return d.strftime("%I:%M %p").lstrip("0")
  except (ValueError, TypeError):
    return ""
