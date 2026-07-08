"""
Recording intelligence — read side (ranked, context-aware retrieval).

This replaces "return the most recent item" with a scoring pipeline that
combines, in the priority order from the product spec:

    1. explicit participant matches
    2. explicit context / intent tag matches
    3. explicit project / event matches
    4. transcript matches
    5. summary matches
    6. semantic similarity (embeddings)
    7. recency  (tie-breaker only)

Each candidate gets a relevance score; the caller is handed a ranked list plus
a confidence signal. When several candidates are plausible (e.g. three meetings
with the same person) the result is flagged ``needs_clarification`` so the agent
can ask the user instead of guessing.

Notes and Meetings are distinct: ``session_type`` filters one or the other.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from database import get_connection
from services import recording_store as store

logger = logging.getLogger("meetingbox.recording_search")

# --- scoring weights (priority order) ------------------------------------- #
W_PARTICIPANT = 5.0
W_CONTEXT_TAG = 4.0
W_PROJECT_EVENT = 3.5
W_TRANSCRIPT = 2.0
W_SUMMARY = 1.5
W_SEMANTIC = 3.0       # multiplied by cosine similarity (0..1)
W_RECENCY = 0.5        # max contribution; pure tie-breaker

# Confidence / clarification thresholds.
RELEVANCE_FLOOR = 2.5          # a candidate below this is "weak"
CLARIFY_RATIO = 0.62           # if #2 score >= ratio * #1 score -> ambiguous
MIN_CONFIDENT_SCORE = 3.0      # top result must clear this to be "confident"


def _row_factory(c, r):
    return {col[0]: r[i] for i, col in enumerate(c.description)}


def _scope_clause(user_id: Optional[str]) -> tuple[str, list[Any]]:
    """Build a WHERE predicate that restricts meetings to this user.

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


# --------------------------------------------------------------------------- #
# Query parsing
# --------------------------------------------------------------------------- #
_TIME_WORDS = {
    "yesterday", "today", "morning", "afternoon", "evening", "tonight",
    "week", "month", "year", "last", "this", "recent", "latest", "ago",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}
_TYPE_NOTE = {"note", "notes", "memo", "reminder", "thought", "thoughts", "brain"}
_TYPE_MEETING = {"meeting", "meetings", "call", "calls", "discussion", "conversation", "standup", "sync"}


def parse_query(query: str, known_people: list[str]) -> dict[str, Any]:
    """Pull intent signals out of a natural-language retrieval query."""
    q = (query or "").strip()
    ql = q.lower()

    # session-type hint
    session_type: Optional[str] = None
    if any(re.search(rf"\b{re.escape(w)}\b", ql) for w in _TYPE_NOTE):
        session_type = "note"
    elif any(re.search(rf"\b{re.escape(w)}\b", ql) for w in _TYPE_MEETING):
        session_type = "meeting"

    # people: data-driven match against names we actually have on file
    people: list[str] = []
    for name in known_people:
        nm = (name or "").strip()
        if not nm:
            continue
        first = nm.split()[0]
        if re.search(rf"\b{re.escape(nm.lower())}\b", ql) or (
            len(first) > 2 and re.search(rf"\b{re.escape(first.lower())}\b", ql)
        ):
            if nm not in people:
                people.append(nm)
    # also capture an explicit "with <Name>" even if not yet on file
    for m in re.finditer(r"\bwith\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)", q):
        cand = m.group(1).strip()
        if cand and cand not in people:
            people.append(cand)

    # keywords (topic terms): drop stopwords + time words + type words
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9'-]{2,}", ql)
    keywords = [
        w for w in raw
        if w not in store._STOPWORDS
        and w not in _TIME_WORDS
        and w not in _TYPE_NOTE
        and w not in _TYPE_MEETING
        and w not in _MONTHS
        and w not in {p.lower() for p in people}
        and len(w) > 2
    ]
    # de-dup, keep order
    seen: set[str] = set()
    keywords = [w for w in keywords if not (w in seen or seen.add(w))]

    time_from, time_to = _parse_time_range(ql)

    return {
        "query": q,
        "session_type": session_type,
        "people": people,
        "keywords": keywords[:12],
        "time_from": time_from,
        "time_to": time_to,
        "wants_latest": any(w in ql for w in ("latest", "last", "most recent", "recent")),
    }


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _day_bounds(d: datetime) -> tuple[str, str]:
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = d.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.isoformat(), end.isoformat()


def _resolve_year(month: int, day: int, explicit_year: Optional[int]) -> int:
    """Pick a sensible year for a date with no year. Recordings are in the past,
    so if the date would be in the future this year, use last year."""
    now = datetime.now()
    if explicit_year:
        return explicit_year
    try:
        candidate = datetime(now.year, month, day)
    except ValueError:
        return now.year
    return now.year if candidate <= now + timedelta(days=1) else now.year - 1


def _parse_absolute_date(ql: str) -> tuple[Optional[str], Optional[str]]:
    """Understand explicit calendar dates -> single-day (from, to). Returns
    (None, None) if no absolute date is present."""
    now = datetime.now()
    # ISO: 2026-06-17
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", ql)
    if m:
        try:
            return _day_bounds(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    # Month name + day: "june 17", "june 17th 2026", "17 june", "17th of june"
    m = re.search(r"\b([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", ql)
    if m and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        day = int(m.group(2))
        year = _resolve_year(month, day, int(m.group(3)) if m.group(3) else None)
        try:
            return _day_bounds(datetime(year, month, day))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})(?:,?\s+(\d{4}))?\b", ql)
    if m and m.group(2) in _MONTHS:
        day = int(m.group(1))
        month = _MONTHS[m.group(2)]
        year = _resolve_year(month, day, int(m.group(3)) if m.group(3) else None)
        try:
            return _day_bounds(datetime(year, month, day))
        except ValueError:
            pass
    # Numeric M/D or M/D/Y
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", ql)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        yr = m.group(3)
        year = _resolve_year(month, day, (2000 + int(yr)) if yr and len(yr) == 2 else int(yr) if yr else None)
        try:
            return _day_bounds(datetime(year, month, day))
        except ValueError:
            pass
    # "on the 15th" -> that day in the current (or last) month
    m = re.search(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", ql)
    if m:
        day = int(m.group(1))
        try:
            candidate = now.replace(day=day)
            if candidate > now + timedelta(days=1):
                # fall back to last month
                prev = (now.replace(day=1) - timedelta(days=1)).replace(day=day)
                candidate = prev
            return _day_bounds(candidate)
        except ValueError:
            pass
    return None, None


def _parse_time_range(ql: str) -> tuple[Optional[str], Optional[str]]:
    """Time understanding -> (iso_from, iso_to). Absolute calendar dates take
    precedence over relative phrases."""
    abs_from, abs_to = _parse_absolute_date(ql)
    if abs_from:
        return abs_from, abs_to

    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def iso(d: datetime) -> str:
        return d.isoformat()

    if "yesterday" in ql:
        start = today - timedelta(days=1)
        return iso(start), iso(today - timedelta(seconds=1))
    if "this morning" in ql or ("morning" in ql and "today" in ql):
        return iso(today), iso(today.replace(hour=12))
    if "today" in ql or "this morning" in ql:
        return iso(today), iso(now)
    if "last week" in ql:
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return iso(start), iso(end)
    if "this week" in ql:
        start = today - timedelta(days=today.weekday())
        return iso(start), iso(now)
    if "last month" in ql:
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(seconds=1)
        last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0)
        return iso(last_month_start), iso(last_month_end)
    if "this month" in ql:
        return iso(today.replace(day=1)), iso(now)
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, wd in enumerate(weekdays):
        if re.search(rf"\b(last\s+)?{wd}\b", ql):
            delta = (today.weekday() - i) % 7
            if delta == 0:
                delta = 7
            d = today - timedelta(days=delta)
            return iso(d), iso(d + timedelta(days=1) - timedelta(seconds=1))
    return None, None


# --------------------------------------------------------------------------- #
# Candidate loading
# --------------------------------------------------------------------------- #
def _load_candidates(
    user_id: Optional[str],
    session_type: Optional[str],
    time_from: Optional[str],
    time_to: Optional[str],
) -> list[dict[str, Any]]:
    scope_sql, scope_params = _scope_clause(user_id)
    clauses = [scope_sql]
    params: list[Any] = list(scope_params)

    if session_type == "note":
        clauses.append("LOWER(COALESCE(m.recording_mode, 'meeting')) = 'note'")
    elif session_type == "meeting":
        clauses.append("LOWER(COALESCE(m.recording_mode, 'meeting')) != 'note'")

    if time_from:
        clauses.append("COALESCE(m.start_time, m.created_at, '') >= ?")
        params.append(time_from)
    if time_to:
        clauses.append("COALESCE(m.start_time, m.created_at, '') <= ?")
        params.append(time_to)

    where = " AND ".join(f"({c})" for c in clauses)
    conn = get_connection()
    conn.row_factory = _row_factory
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT m.id, m.title, m.start_time, m.created_at, m.end_time,
                   m.status, m.duration, m.participants, m.recording_mode,
                   s.summary AS summary,
                   rc.search_blob AS search_blob,
                   rc.referenced_people AS referenced_people,
                   rc.referenced_projects AS referenced_projects,
                   rc.referenced_events AS referenced_events,
                   rc.intent_tags AS intent_tags,
                   rc.context_tags AS context_tags,
                   rc.session_intent AS session_intent,
                   rc.pre_context AS pre_context,
                   rc.post_context AS post_context
            FROM meetings m
            LEFT JOIN summaries s ON s.meeting_id = m.id
            LEFT JOIN recording_context rc ON rc.meeting_id = m.id
            WHERE {where}
            ORDER BY COALESCE(m.start_time, m.created_at, '') DESC
            LIMIT 500
            """,
            params,
        )
        return cur.fetchall()
    finally:
        conn.close()


def _fts_field_matches(meeting_ids: list[str], keywords: list[str]) -> dict[str, set[str]]:
    """
    Return {'transcript': {ids...}, 'summary': {ids...}} for ids (within the
    caller-supplied user-scoped set) matching any keyword in that FTS column.
    Falls back to {} if FTS unavailable.

    The meeting_ids filter is applied in SQL so the FTS search stays within the
    user's own recordings rather than scanning all users' data.
    """
    out = {"transcript": set(), "summary": set()}
    if not keywords or not meeting_ids:
        return out
    # FTS5 OR query, sanitized to bare terms.
    terms = " OR ".join(f'"{re.sub(chr(34), "", k)}"' for k in keywords if k)
    if not terms:
        return out
    placeholders = ",".join("?" for _ in meeting_ids)
    conn = get_connection()
    try:
        # Probe availability.
        try:
            conn.execute("SELECT 1 FROM recordings_fts LIMIT 1")
        except Exception:
            return out
        for field in ("transcript", "summary"):
            try:
                rows = conn.execute(
                    f"SELECT meeting_id FROM recordings_fts "
                    f"WHERE meeting_id IN ({placeholders}) AND {field} MATCH ?",
                    (*meeting_ids, terms),
                ).fetchall()
                out[field] = {r[0] for r in rows}
            except Exception:
                logger.debug("FTS query failed for field=%s", field, exc_info=True)
        return out
    finally:
        conn.close()


def _like_field_matches(meeting_ids: list[str], keywords: list[str]) -> dict[str, set[str]]:
    """LIKE fallback over segments + summaries when FTS is unavailable."""
    out = {"transcript": set(), "summary": set()}
    if not keywords or not meeting_ids:
        return out
    placeholders = ",".join("?" for _ in meeting_ids)
    conn = get_connection()
    try:
        for kw in keywords:
            pat = f"%{kw}%"
            t = conn.execute(
                f"SELECT DISTINCT meeting_id FROM segments "
                f"WHERE meeting_id IN ({placeholders}) AND LOWER(COALESCE(text,'')) LIKE ?",
                (*meeting_ids, pat),
            ).fetchall()
            out["transcript"].update(r[0] for r in t)
            s = conn.execute(
                f"SELECT DISTINCT meeting_id FROM summaries "
                f"WHERE meeting_id IN ({placeholders}) AND LOWER(COALESCE(summary,'')) LIKE ?",
                (*meeting_ids, pat),
            ).fetchall()
            out["summary"].update(r[0] for r in s)
        return out
    finally:
        conn.close()


def _load_embeddings(meeting_ids: list[str]) -> dict[str, list[float]]:
    if not meeting_ids:
        return {}
    placeholders = ",".join("?" for _ in meeting_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT meeting_id, vector FROM recording_embeddings WHERE meeting_id IN ({placeholders})",
            meeting_ids,
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, list[float]] = {}
    for mid, blob in rows:
        try:
            out[mid] = store.blob_to_vector(blob)
        except Exception:
            continue
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# Main search
# --------------------------------------------------------------------------- #
def search_recordings(
    user_id: Optional[str],
    query: str,
    *,
    session_type: Optional[str] = None,
    limit: int = 8,
) -> dict[str, Any]:
    """
    Rank a user's recordings against a natural-language query.

    Returns:
      {
        "results": [ {meeting_id, title, score, signals, date, time, participants,
                      session_type, snippet} ... ],
        "count": int,
        "needs_clarification": bool,
        "clarification": {message, candidates:[...]} | None,
        "confident": bool,
        "parsed": {...},
      }
    """
    limit = max(1, min(int(limit or 8), 25))

    # First pass: load (possibly time/type filtered) candidates so we can learn
    # the set of known people for query parsing.
    raw_candidates = _load_candidates(user_id, session_type, None, None)
    known_people = _collect_known_people(raw_candidates)
    parsed = parse_query(query, known_people)

    # Honor an explicit caller-provided session_type, else the parsed hint.
    effective_type = session_type or parsed["session_type"]

    candidates = _load_candidates(
        user_id, effective_type, parsed["time_from"], parsed["time_to"]
    )
    # If a time filter wiped everything out, relax it (the user may be vague).
    if not candidates and (parsed["time_from"] or parsed["time_to"]):
        candidates = _load_candidates(user_id, effective_type, None, None)

    if not candidates:
        return {
            "results": [],
            "count": 0,
            "needs_clarification": False,
            "clarification": None,
            "confident": False,
            "parsed": parsed,
        }

    ids = [c["id"] for c in candidates]
    keywords = parsed["keywords"]

    field_hits = _fts_field_matches(ids, keywords)
    if not field_hits["transcript"] and not field_hits["summary"] and keywords:
        field_hits = _like_field_matches(ids, keywords)

    # Semantic: embed the query once and compare to stored vectors.
    embeddings = _load_embeddings(ids)
    query_vec = store.embed_text(query) if embeddings else None

    scored: list[dict[str, Any]] = []
    newest, oldest = _time_bounds(candidates)
    for c in candidates:
        mid = c["id"]
        people = store._load_json_list(c.get("referenced_people"))
        participants = store._load_json_list(c.get("participants")) + people
        projects = store._load_json_list(c.get("referenced_projects"))
        events = store._load_json_list(c.get("referenced_events"))
        tags = (
            store._load_json_list(c.get("intent_tags"))
            + store._load_json_list(c.get("context_tags"))
        )
        blob = (c.get("search_blob") or "").lower()

        signals: dict[str, float] = {}
        score = 0.0

        # 1. participants
        p_hits = _count_name_hits(parsed["people"], participants)
        if p_hits:
            s = W_PARTICIPANT * min(p_hits, 2)
            score += s
            signals["participant"] = s

        # 2. context / intent tags (+ free-text intent)
        tag_text = " ".join(tags).lower() + " " + (c.get("session_intent") or "").lower() \
            + " " + (c.get("pre_context") or "").lower() + " " + (c.get("post_context") or "").lower()
        t_hits = sum(1 for k in keywords if k in tag_text)
        if t_hits:
            s = W_CONTEXT_TAG * min(t_hits, 3)
            score += s
            signals["context_tag"] = s

        # 3. projects / events
        pe_text = " ".join(projects + events).lower()
        pe_hits = sum(1 for k in keywords if k in pe_text)
        if pe_hits:
            s = W_PROJECT_EVENT * min(pe_hits, 3)
            score += s
            signals["project_event"] = s

        # 4. transcript
        if mid in field_hits["transcript"]:
            score += W_TRANSCRIPT
            signals["transcript"] = W_TRANSCRIPT

        # 5. summary (title counts here too)
        title_l = (c.get("title") or "").lower()
        title_hit = any(k in title_l for k in keywords)
        if mid in field_hits["summary"] or title_hit:
            score += W_SUMMARY
            signals["summary"] = W_SUMMARY

        # extra: keywords appearing anywhere in metadata blob (light boost)
        blob_hits = sum(1 for k in keywords if k in blob)
        if blob_hits and "context_tag" not in signals and "project_event" not in signals:
            s = min(blob_hits, 2) * 0.8
            score += s
            signals["metadata"] = s

        # 6. semantic
        if query_vec and mid in embeddings:
            cos = _cosine(query_vec, embeddings[mid])
            if cos > 0:
                s = W_SEMANTIC * cos
                score += s
                signals["semantic"] = round(s, 3)

        # 7. recency (tie-breaker only)
        rec = _recency_score(c, newest, oldest)
        score += rec
        signals["recency"] = round(rec, 3)

        scored.append({
            "meeting_id": mid,
            "title": c.get("title") or "(untitled)",
            "score": round(score, 3),
            "signals": signals,
            "date": _date_part(c),
            "time": _time_part(c),
            "created_at": c.get("created_at"),
            "start_time": c.get("start_time"),
            "session_type": (c.get("recording_mode") or "meeting"),
            "participants": _as_unique(participants),
            "snippet": _snippet(c.get("summary")),
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    results = scored[:limit]

    confident, needs_clarification, clarification = _assess_confidence(
        scored, parsed
    )

    return {
        "results": results,
        "count": len(results),
        "needs_clarification": needs_clarification,
        "clarification": clarification,
        "confident": confident,
        "parsed": parsed,
    }


# --------------------------------------------------------------------------- #
# Confidence & clarification
# --------------------------------------------------------------------------- #
def _assess_confidence(scored: list[dict[str, Any]], parsed: dict[str, Any]):
    strong = [r for r in scored if r["score"] >= RELEVANCE_FLOOR]
    if not scored:
        return False, False, None

    top = scored[0]
    # No meaningful match at all.
    if top["score"] < MIN_CONFIDENT_SCORE and not strong:
        return False, False, None

    # If the user explicitly asked for the latest/last one, honor that recency
    # preference instead of asking which of several they meant. Candidates are
    # already ordered by score then recency, so the top is the right pick.
    if parsed.get("wants_latest"):
        return True, False, None

    if len(strong) >= 2:
        second = strong[1]
        if top["score"] > 0 and (second["score"] / top["score"]) >= CLARIFY_RATIO:
            candidates = strong[:3]
            return False, True, {
                "message": _clarify_message(candidates, parsed),
                "candidates": [
                    {
                        "meeting_id": c["meeting_id"],
                        "title": c["title"],
                        "date": c["date"],
                        "time": c["time"],
                        "participants": c["participants"],
                        "session_type": c["session_type"],
                    }
                    for c in candidates
                ],
            }

    return (top["score"] >= MIN_CONFIDENT_SCORE), False, None


def _clarify_message(candidates: list[dict[str, Any]], parsed: dict[str, Any]) -> str:
    noun = "notes" if parsed.get("session_type") == "note" else "recordings"
    who = ""
    if parsed.get("people"):
        who = f" involving {parsed['people'][0]}"
        noun = "meetings" if parsed.get("session_type") != "note" else "notes"
    descriptors = []
    for c in candidates:
        label = c.get("date") or "an earlier date"
        if c.get("title") and not str(c["title"]).startswith(("Meeting ", "Notes ")):
            label = f"{c['title']} ({label})"
        descriptors.append(label)
    joined = ", ".join(descriptors[:-1]) + (", or " + descriptors[-1] if len(descriptors) > 1 else descriptors[0])
    return f"I found {len(candidates)} {noun}{who}: {joined}. Which one did you mean?"


# --------------------------------------------------------------------------- #
# Rich result card (for verification / read-back)
# --------------------------------------------------------------------------- #
def get_recording_card(user_id: Optional[str], meeting_id: str) -> dict[str, Any]:
    """Full verification card: title, date, time, participants, summary,
    action items, decisions, tags."""
    from tools.memory_tool import memory_fetch_meeting

    detail = memory_fetch_meeting(user_id, meeting_id, max_segments=40, max_total_chars=8000)
    if detail.get("error"):
        return detail
    ctx = store.get_recording_context(meeting_id)
    tags = _as_unique(
        ctx.get("intent_tags", [])
        + ctx.get("context_tags", [])
        + ctx.get("referenced_events", [])
        + ctx.get("referenced_projects", [])
        + ctx.get("future_reference_tags", [])
    )
    participants = _as_unique(
        store._load_json_list(_participants_for(meeting_id))
        + ctx.get("referenced_people", [])
    )
    start = detail.get("start_time") or detail.get("created_at")
    return {
        "meeting_id": meeting_id,
        "title": detail.get("title"),
        "session_type": ctx.get("session_type", "meeting"),
        "date": _fmt_date(start),
        "time": _fmt_time(start),
        "participants": participants,
        "summary": detail.get("summary"),
        "action_items": detail.get("action_items", []),
        "decisions": detail.get("decisions", []),
        "tags": tags,
        "session_intent": ctx.get("session_intent", ""),
        "pre_context": ctx.get("pre_context", ""),
        "post_context": ctx.get("post_context", ""),
    }


def _participants_for(meeting_id: str) -> Any:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT participants FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# small utils
# --------------------------------------------------------------------------- #
def _collect_known_people(candidates: list[dict[str, Any]]) -> list[str]:
    people: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        for n in store._load_json_list(c.get("participants")) + store._load_json_list(c.get("referenced_people")):
            k = n.lower()
            if k not in seen:
                seen.add(k)
                people.append(n)
    return people


def _count_name_hits(query_people: list[str], candidate_people: list[str]) -> int:
    if not query_people or not candidate_people:
        return 0
    cand_l = [p.lower() for p in candidate_people]
    hits = 0
    for qp in query_people:
        qpl = qp.lower()
        qfirst = qpl.split()[0]
        for cp in cand_l:
            if qpl in cp or cp in qpl or (len(qfirst) > 2 and qfirst in cp.split()):
                hits += 1
                break
    return hits


def _as_unique(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        s = str(it).strip()
        if not s:
            continue
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _parse_dt(c: dict[str, Any]) -> Optional[datetime]:
    val = c.get("start_time") or c.get("created_at")
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
    except (ValueError, TypeError):
        return None


def _time_bounds(candidates: list[dict[str, Any]]):
    dts = [d for d in (_parse_dt(c) for c in candidates) if d]
    if not dts:
        return None, None
    return max(dts), min(dts)


def _recency_score(c: dict[str, Any], newest, oldest) -> float:
    dt = _parse_dt(c)
    if not dt or not newest or not oldest or newest == oldest:
        return 0.0
    span = (newest - oldest).total_seconds()
    if span <= 0:
        return W_RECENCY
    frac = (dt - oldest).total_seconds() / span
    return W_RECENCY * max(0.0, min(1.0, frac))


def _date_part(c: dict[str, Any]) -> str:
    return _fmt_date(c.get("start_time") or c.get("created_at"))


def _time_part(c: dict[str, Any]) -> str:
    return _fmt_time(c.get("start_time") or c.get("created_at"))


def _fmt_date(val: Any) -> str:
    if not val:
        return ""
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return str(val)[:10]


def _fmt_time(val: Any) -> str:
    if not val:
        return ""
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00").split("+")[0])
        return dt.strftime("%I:%M %p").lstrip("0")
    except (ValueError, TypeError):
        return ""


def _snippet(summary: Any, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", str(summary or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")
