"""Tasks service — voice tool backends + meeting/email -> commitments bridges.

Acts as a thin orchestration layer above services/commitments_service.py that
adds the strict-fidelity guardrails the Tasks Agent guarantees:

  - Title is a paraphrase of source text (≤ 8 words, no invention).
  - Description is empty unless the source explicitly provided one (or is a
    meeting/email source — in which case it's a source-attribution sentence).
  - Due date is only set when the source utterance / line explicitly mentions one.
  - Duplicate detection on create (similar active titles surface a warning).
  - Title fuzzy match for update flows ('mark X done').

Exceptions
==========
TaskFidelityError       -- input violates faithfulness rules (e.g. empty title).
SimilarTaskExistsError  -- create blocked because a similar active task exists.
TaskNotFoundError       -- update target could not be resolved.
AmbiguousTaskMatchError -- title_match resolved to multiple candidates.

Public entry points (sync)
==========================
voice_create_task(...)            -- create_task voice tool backend.
voice_update_task(...)            -- update_task voice tool backend.
extract_tasks_from_emails_sync(...) -- extract_tasks_from_emails voice tool backend.
create_tasks_from_meeting(...)    -- meeting -> tasks bridge (Phase 2, called from
                                     meeting summary persistence path).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from services.commitments_service import (
    list_commitments_for_user,
    upsert_commitment,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class TaskFidelityError(ValueError):
    """Input violates strict-fidelity rules (empty title, malformed date, etc.)."""


class SimilarTaskExistsError(Exception):
    """Create blocked because a similar active task already exists."""

    def __init__(self, similar: dict[str, Any]) -> None:
        self.similar = similar
        super().__init__(f"Similar active task exists: {similar.get('title')}")


class TaskNotFoundError(Exception):
    """Could not resolve update target to an existing task."""


class AmbiguousTaskMatchError(Exception):
    """title_match resolved to >1 candidate. Caller must disambiguate."""

    def __init__(self, candidates: list[dict[str, Any]]) -> None:
        self.candidates = candidates
        super().__init__(f"Ambiguous match: {len(candidates)} candidates")


# ─────────────────────────────────────────────────────────────────────────────
# Title normalisation + similarity
# ─────────────────────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[A-Za-z0-9']+")
_STOP_WORDS = frozenset({
    "the", "a", "an", "to", "for", "of", "in", "on", "at", "by", "with",
    "and", "or", "but", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should",
    "could", "may", "might", "must", "can", "this", "that", "these", "those",
    "i", "me", "my", "you", "your", "we", "our", "they", "their",
    "task", "todo", "to-do",
})


def _normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split()).strip()


def _title_tokens(title: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(title or "") if t.lower() not in _STOP_WORDS}


def _title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on content tokens (stopwords removed)."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def _find_similar_active_task(user_id: str, title: str) -> dict[str, Any] | None:
    """Return the most-similar active task with overlap >= 0.7, else None."""
    norm = _normalize_title(title)
    if not norm:
        return None
    rows = list_commitments_for_user(user_id, status_filter="active", limit=100)
    snoozed = list_commitments_for_user(user_id, status_filter="snoozed", limit=50)
    candidates = list(rows) + list(snoozed)
    best: dict[str, Any] | None = None
    best_score = 0.0
    for r in candidates:
        existing = _normalize_title(r.get("title") or "")
        if not existing:
            continue
        if existing == norm:
            return r
        s = _title_similarity(norm, existing)
        if s > best_score:
            best_score = s
            best = r
    if best is not None and best_score >= 0.7:
        return best
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Date parsing
# ─────────────────────────────────────────────────────────────────────────────

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d|$)")
_LOOSE_DATE_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")


def _parse_due_date(raw: str | None) -> str | None:
    """Validate and normalise a due_date string to YYYY-MM-DD.

    Returns:
      - None when raw is empty / None.
    Raises:
      - TaskFidelityError for invalid date strings.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Accept full ISO datetime — keep only the date component.
    m = _ISO_DATE_RE.match(s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise TaskFidelityError(f"Invalid date: {s}") from exc
        return d.isoformat()
    m = _LOOSE_DATE_RE.match(s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise TaskFidelityError(f"Invalid date: {s}") from exc
        return d.isoformat()
    raise TaskFidelityError(
        f"due_date must be ISO YYYY-MM-DD (got '{s}'). Resolve relative dates "
        "(today/tomorrow/Friday) before passing."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Title fidelity check (length cap)
# ─────────────────────────────────────────────────────────────────────────────


def _check_title_fidelity(title: str) -> str:
    t = (title or "").strip()
    if not t:
        raise TaskFidelityError("title is required")
    word_count = len(_WORD_RE.findall(t))
    # The agent prompt asks for ≤8-word paraphrase; we allow some headroom and
    # only refuse outright when the title clearly contains a full transcript
    # paragraph (a sign of no paraphrasing happening).
    if word_count > 18 or len(t) > 160:
        raise TaskFidelityError(
            "title too long — must be a paraphrase (≤ 8 words). "
            "Re-condense to the verb + object."
        )
    return t


# ─────────────────────────────────────────────────────────────────────────────
# voice_create_task
# ─────────────────────────────────────────────────────────────────────────────


def voice_create_task(
    *,
    user_id: str,
    title: str,
    due_date: str | None = None,
    description: str | None = None,
    confirm_duplicate: bool = False,
    source: str = "voice",
    tags: list[str] | None = None,
    meeting_id: str | None = None,
) -> dict[str, Any]:
    """Create a task with fidelity + duplicate guardrails.

    Raises:
      TaskFidelityError      -- title empty/too long or date malformed.
      SimilarTaskExistsError -- similar active task exists and confirm_duplicate=False.
    """
    if not (user_id or "").strip():
        raise TaskFidelityError("user_id required")
    t = _check_title_fidelity(title)
    due_at = _parse_due_date(due_date)
    detail = (description or "").strip() or None

    if not confirm_duplicate:
        similar = _find_similar_active_task(user_id, t)
        if similar is not None:
            slim = {
                "id": similar.get("id"),
                "title": similar.get("title"),
                "status": similar.get("status"),
                "due_at": similar.get("due_at"),
                "detail": (similar.get("detail") or "")[:200],
            }
            raise SimilarTaskExistsError(slim)

    payload: dict[str, Any] = {
        "title": t,
        "source": source,
        "status": "active",
    }
    if due_at is not None:
        payload["due_at"] = due_at
    if detail is not None:
        payload["detail"] = detail
    if tags:
        payload["tags"] = list(tags)
    if meeting_id:
        payload["meeting_id"] = meeting_id

    row = upsert_commitment(user_id, payload)
    logger.info(
        "tasks_service: created task id=%s title=%r source=%s",
        row.get("id"),
        row.get("title"),
        source,
    )
    try:
        from services.mem0_service import maybe_ingest_commitment_row
        maybe_ingest_commitment_row(user_id, row)
    except Exception:
        logger.debug("task mem0 ingest failed", exc_info=True)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# voice_update_task
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_NEW_STATUSES = frozenset({"active", "snoozed", "completed", "cancelled"})


def _resolve_task_id_by_title(user_id: str, query: str) -> str:
    """Resolve a partial-title query to a single task id.

    Raises TaskNotFoundError if nothing matches; AmbiguousTaskMatchError if >1.
    """
    qn = _normalize_title(query)
    if not qn:
        raise TaskNotFoundError()
    rows = list_commitments_for_user(user_id, status_filter="active", limit=100)
    rows += list_commitments_for_user(user_id, status_filter="snoozed", limit=50)

    # Pass 1: exact normalised match.
    exact = [r for r in rows if _normalize_title(r.get("title") or "") == qn]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        raise AmbiguousTaskMatchError(_slim_rows(exact))

    # Pass 2: high Jaccard similarity (>=0.5).
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        s = _title_similarity(qn, r.get("title") or "")
        if s >= 0.5:
            scored.append((s, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        raise TaskNotFoundError()
    if len(scored) == 1:
        return scored[0][1]["id"]
    # If top score is clearly ahead of the runner-up, take it.
    if scored[0][0] - scored[1][0] >= 0.25:
        return scored[0][1]["id"]
    raise AmbiguousTaskMatchError(_slim_rows([r for _, r in scored[:5]]))


def _slim_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "status": r.get("status"),
            "due_at": r.get("due_at"),
        }
        for r in rows
    ]


def voice_update_task(
    *,
    user_id: str,
    task_id: str | None = None,
    title_match: str | None = None,
    title: str | None = None,
    status: str | None = None,
    due_date: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Update an existing task. Resolves id via title_match if needed."""
    if not (user_id or "").strip():
        raise TaskFidelityError("user_id required")
    if not task_id and not title_match:
        raise TaskFidelityError("task_id or title_match required")
    resolved_id = task_id
    if not resolved_id:
        assert title_match is not None
        resolved_id = _resolve_task_id_by_title(user_id, title_match)

    payload: dict[str, Any] = {"commitment_id": resolved_id}
    if status is not None:
        s = status.strip().lower()
        if s not in _ALLOWED_NEW_STATUSES:
            raise TaskFidelityError(
                f"status must be one of {sorted(_ALLOWED_NEW_STATUSES)} (got '{s}')"
            )
        payload["status"] = s
    if due_date is not None:
        payload["due_at"] = _parse_due_date(due_date)
    if description is not None:
        payload["detail"] = description.strip()
    if title is not None and title.strip():
        payload["title"] = title.strip()

    try:
        row = upsert_commitment(user_id, payload)
    except ValueError as exc:
        # commitment not found
        raise TaskNotFoundError(str(exc)) from exc
    logger.info(
        "tasks_service: updated task id=%s status=%s due=%s",
        row.get("id"),
        row.get("status"),
        row.get("due_at"),
    )
    try:
        from services.mem0_service import maybe_ingest_commitment_row
        maybe_ingest_commitment_row(user_id, row)
    except Exception:
        logger.debug("task mem0 ingest failed", exc_info=True)
    return row


# ─────────────────────────────────────────────────────────────────────────────
# Meeting -> tasks bridge
# ─────────────────────────────────────────────────────────────────────────────

_DATE_HINT_PATTERNS = (
    re.compile(r"\bby\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b", re.I),
    re.compile(r"\bby\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\b", re.I),
    re.compile(r"\b(?:due|by)\s+(\d{4}-\d{2}-\d{2})\b", re.I),
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
)
_WEEKDAY_HINTS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_MONTH_HINTS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _try_extract_due_date(text: str, anchor: date | None = None) -> str | None:
    """Extract a YYYY-MM-DD if the text explicitly contains a date phrase.

    Returns None when no date can be confidently extracted.
    """
    if not text:
        return None
    anchor = anchor or date.today()
    t = text.strip()
    # Direct ISO date
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    tl = t.lower()
    # "by Friday" / "next Friday" / weekday names
    for name, idx in _WEEKDAY_HINTS.items():
        if re.search(rf"\b(?:by|on|this|next)\s+{name}\b", tl) or re.search(rf"\bby\s+{name}\b", tl):
            today_idx = anchor.weekday()
            delta = (idx - today_idx) % 7
            if delta == 0:
                delta = 7  # "by Monday" said on a Monday → next Monday
            if "next " in tl:
                if delta <= 7:
                    delta += 7
            return (anchor + timedelta(days=delta)).isoformat()
    # "by 5th June" / "by June 5"
    m = re.search(r"\bby\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b", tl)
    if m:
        day_n = int(m.group(1))
        mon = _MONTH_HINTS.get(m.group(2).lower())
        if mon:
            year = anchor.year
            try:
                d = date(year, mon, day_n)
                if d < anchor:
                    d = date(year + 1, mon, day_n)
                return d.isoformat()
            except ValueError:
                pass
    m = re.search(r"\bby\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", tl)
    if m:
        mon = _MONTH_HINTS.get(m.group(1).lower())
        if mon:
            day_n = int(m.group(2))
            year = anchor.year
            try:
                d = date(year, mon, day_n)
                if d < anchor:
                    d = date(year + 1, mon, day_n)
                return d.isoformat()
            except ValueError:
                pass
    # "tomorrow"
    if re.search(r"\btomorrow\b", tl):
        return (anchor + timedelta(days=1)).isoformat()
    # "next week" → following Monday
    if re.search(r"\bnext week\b", tl):
        today_idx = anchor.weekday()
        return (anchor + timedelta(days=(7 - today_idx))).isoformat()
    return None


def shorten_action_text_to_title(text: str, max_words: int = 8) -> str:
    """Condense an action_item line or chat phrase into a short task title.

    Drops leading "<Name> to ", common verb prefixes, trailing date phrases.
    Public — reused by the chat-side tasks_agent dispatch as well as the
    meeting -> tasks bridge.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # Drop trailing "by ..." date phrase to keep the title verb+object only
    t = re.sub(
        r"\s+(?:by|due|before|on)\s+(?:(?:next|this)\s+)?[A-Za-z0-9\-,/]+(?:\s+\d{1,2}(?:st|nd|rd|th)?)?\s*$",
        "",
        t,
        flags=re.I,
    ).strip()
    # Drop owner prefix: "John to send report" / "Vivek will set up demo"
    t = re.sub(r"^[A-Za-z]+(?:\s[A-Za-z]+)?\s+(?:to|will|should|must|needs to)\s+", "", t, flags=re.I)
    # Sentence-case the verb
    if t:
        t = t[0].upper() + t[1:]
    # Truncate
    words = t.split()
    if len(words) > max_words:
        t = " ".join(words[:max_words]).rstrip(",.;:")
    return t


def _action_item_assignee(item: Any) -> str | None:
    """Extract the assignee/owner from an action_item entry (dict or string)."""
    if isinstance(item, dict):
        for key in ("owner", "assignee", "assigned_to", "responsible"):
            v = item.get(key)
            if v:
                return str(v).strip()
    return None


def _action_item_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("task", "text", "description", "item", "title"):
            v = item.get(key)
            if v:
                return str(v).strip()
        # Fall back to JSON-ish representation
        return json.dumps(item, default=str)[:300]
    return str(item).strip()


_OWNER_HINT_PATTERNS = (
    re.compile(r"^([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)\s+(?:to|will|should|must|needs to)\b"),
)


def _infer_owner_from_text(text: str) -> str | None:
    if not text:
        return None
    for pat in _OWNER_HINT_PATTERNS:
        m = pat.match(text.strip())
        if m:
            return m.group(1).strip()
    return None


def _is_user_owner(owner: str | None, user_display_names: list[str]) -> bool:
    if not owner:
        return False
    o = owner.lower().strip()
    if o in {"user", "me", "i", "you"}:
        return True
    for name in user_display_names:
        n = (name or "").lower().strip()
        if not n:
            continue
        if o == n or n in o or o in n:
            return True
    return False


def create_tasks_from_meeting(
    *,
    user_id: str,
    meeting_id: str,
    meeting_title: str,
    meeting_date: str | None,
    action_items: list[Any],
    user_display_names: list[str] | None = None,
) -> dict[str, Any]:
    """Persist meeting action_items into user_commitments.

    Rules (per user spec):
      • Action items where the user is the named owner -> auto-create active task.
      • Action items with no owner -> create task with tags=['needs-review'] and a
        description explaining 'assigned to you because no owner was specified'.
      • Action items clearly assigned to someone else -> SKIP entirely.

    Returns a summary dict with counts and the created/skipped breakdown.
    """
    user_display_names = user_display_names or []
    created: list[dict[str, Any]] = []
    needs_review: list[dict[str, Any]] = []
    skipped_other_owner: list[dict[str, Any]] = []
    skipped_invalid: list[dict[str, Any]] = []

    anchor_date: date | None = None
    if meeting_date:
        try:
            anchor_date = datetime.fromisoformat(meeting_date.replace("Z", "+00:00")).date()
        except (ValueError, TypeError):
            anchor_date = None

    mtg_label = (meeting_title or "Untitled meeting").strip()
    mtg_date_label = ""
    if anchor_date:
        mtg_date_label = anchor_date.isoformat()

    for raw in action_items or []:
        text = _action_item_text(raw)
        if not text:
            skipped_invalid.append({"reason": "empty_text", "item": raw})
            continue

        owner = _action_item_assignee(raw) or _infer_owner_from_text(text)
        title_short = shorten_action_text_to_title(text, max_words=8)
        if not title_short:
            skipped_invalid.append({"reason": "no_title", "item": raw})
            continue

        due_at = _try_extract_due_date(text, anchor=anchor_date)

        tags: list[str] = []
        if _is_user_owner(owner, user_display_names):
            detail = (
                f"From meeting '{mtg_label}'"
                + (f" on {mtg_date_label}" if mtg_date_label else "")
                + f": {text}"
            )
            row = upsert_commitment(
                user_id,
                {
                    "title": title_short,
                    "detail": detail,
                    "source": "meeting",
                    "meeting_id": meeting_id,
                    "due_at": due_at,
                    "tags": tags,
                    "status": "active",
                },
            )
            created.append({"id": row.get("id"), "title": row.get("title"), "owner": owner})
            # Fix 6A: push meeting-derived tasks into Mem0 so voice assistant can recall them.
            try:
                from services.mem0_service import maybe_ingest_commitment_row
                maybe_ingest_commitment_row(user_id, row)
            except Exception:
                logger.debug("mem0 commitment ingest skipped for meeting task id=%s", row.get("id"))
            continue

        if owner is None:
            # Ambiguous owner -> create as needs-review (user decides on device).
            tags = ["needs-review"]
            detail = (
                f"Assigned to you because the meeting did not name an owner. "
                f"Source — meeting '{mtg_label}'"
                + (f" on {mtg_date_label}" if mtg_date_label else "")
                + f": {text}"
            )
            row = upsert_commitment(
                user_id,
                {
                    "title": title_short,
                    "detail": detail,
                    "source": "meeting",
                    "meeting_id": meeting_id,
                    "due_at": due_at,
                    "tags": tags,
                    "status": "active",
                },
            )
            needs_review.append({"id": row.get("id"), "title": row.get("title")})
            # Fix 6A: also ingest needs-review tasks so they appear in voice memory.
            try:
                from services.mem0_service import maybe_ingest_commitment_row
                maybe_ingest_commitment_row(user_id, row)
            except Exception:
                logger.debug("mem0 commitment ingest skipped for needs-review task id=%s", row.get("id"))
            continue

        # owner is set but not the user -> skip
        skipped_other_owner.append({"owner": owner, "text": text[:120]})

    summary = {
        "meeting_id": meeting_id,
        "meeting_title": mtg_label,
        "created_count": len(created),
        "needs_review_count": len(needs_review),
        "skipped_other_owner_count": len(skipped_other_owner),
        "skipped_invalid_count": len(skipped_invalid),
        "created": created,
        "needs_review": needs_review,
        "skipped_other_owner": skipped_other_owner,
        "skipped_invalid": skipped_invalid,
    }
    logger.info(
        "tasks_service: meeting %s -> created=%d needs_review=%d skipped_other=%d invalid=%d",
        meeting_id,
        summary["created_count"],
        summary["needs_review_count"],
        summary["skipped_other_owner_count"],
        summary["skipped_invalid_count"],
    )
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Email -> tasks extraction (voice-triggered, returns PROPOSALS only)
# ─────────────────────────────────────────────────────────────────────────────


def _get_anthropic_client():
    """Lazy Anthropic client. Returns None when no API key is configured."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.warning("anthropic package not installed; email task extraction unavailable")
        return None
    try:
        return Anthropic(api_key=api_key)
    except Exception:
        logger.exception("Anthropic client init failed")
        return None


_EMAIL_EXTRACTION_PROMPT = (
    "You are extracting personal tasks (to-dos) from a small batch of recent emails. "
    "Your output is ONLY the tasks WHERE THE EMAIL RECIPIENT (the user) is the actionee — "
    "things THEY need to do, not things mentioned in passing or things assigned to other "
    "people. Be strict.\n\n"
    "CRITICAL RULES:\n"
    "1. Title must be a ≤8-word paraphrase of the actual instruction. Drop filler. "
    "Keep verb + object.\n"
    "2. If the email is a newsletter, promotional, automated digest, marketing, calendar "
    "invite, security alert, or receipt — emit NO tasks for it.\n"
    "3. If the email mentions an instruction directed at someone OTHER than the user — emit "
    "no task for it.\n"
    "4. If the email is informational with no action — emit no task.\n"
    "5. due_date only when the email explicitly states one. Resolve relative dates "
    "(tomorrow / by Friday / next Tuesday) using today's anchor date. If no due date, "
    "set due_date to null.\n"
    "6. Never invent details. The detail field must quote / paraphrase the actual line in "
    "the email that requested the task.\n\n"
    "Return ONLY valid JSON in this shape:\n"
    "{\n"
    "  \"proposals\": [\n"
    "    {\n"
    "      \"title\": \"...\",\n"
    "      \"due_date\": \"YYYY-MM-DD or null\",\n"
    "      \"detail\": \"From '<sender>' / '<subject>': <quoted/paraphrased line>\",\n"
    "      \"email_id\": \"<gmail message id>\"\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
)


def _parse_json_loose(text: str) -> Any:
    if not text:
        raise json.JSONDecodeError("empty", text or "", 0)
    if "```json" in text:
        start = text.find("```json") + len("```json")
        end = text.find("```", start)
        if end != -1:
            return json.loads(text[start:end].strip())
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        return json.loads(text[start:end])
    raise json.JSONDecodeError("no JSON", text, 0)


def extract_tasks_from_emails_sync(
    *,
    user_id: str,
    query: str | None = None,
    max_emails: int = 5,
) -> dict[str, Any]:
    """Voice tool backend: scan recent emails and propose tasks where the user is actionee.

    Returns a dict shaped {proposals: [...], scanned_count: int, anchor_date: 'YYYY-MM-DD'}.
    Does NOT save any tasks — caller must verbally confirm each and then call create_task
    for each accepted proposal.
    """
    from tools.gmail_tool import gmail_list_recent
    from tools.base_tool import ToolError

    try:
        emails_res = gmail_list_recent(user_id, max_results=max(1, min(max_emails, 15)), q=query or "")
    except ToolError as exc:
        return {
            "error": "gmail_unavailable",
            "detail": str(exc),
            "proposals": [],
            "scanned_count": 0,
        }
    msgs = list(emails_res.get("messages") or [])
    if not msgs:
        return {
            "proposals": [],
            "scanned_count": 0,
            "note": "No recent emails matched the query.",
        }

    client = _get_anthropic_client()
    if client is None:
        return {
            "error": "extraction_unavailable",
            "detail": "ANTHROPIC_API_KEY is not configured on the server.",
            "proposals": [],
            "scanned_count": len(msgs),
        }

    anchor = date.today().isoformat()
    summary_lines = [f"Today's date for resolving relative dates: {anchor}\n"]
    summary_lines.append("Emails to scan:\n")
    for m in msgs:
        summary_lines.append(
            "----\n"
            f"id: {m.get('id')}\n"
            f"from: {m.get('from') or m.get('sender') or '(unknown)'}\n"
            f"subject: {m.get('subject') or '(no subject)'}\n"
            f"date: {m.get('date') or ''}\n"
            f"snippet: {(m.get('snippet') or '')[:600]}\n"
        )
    prompt = _EMAIL_EXTRACTION_PROMPT + "\n".join(summary_lines)

    model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning("email task extraction LLM call failed: %s", exc)
        return {
            "error": "llm_failed",
            "detail": str(exc)[:300],
            "proposals": [],
            "scanned_count": len(msgs),
        }

    try:
        text = resp.content[0].text if resp.content else ""
    except Exception:
        text = ""

    try:
        data = _parse_json_loose(text or "")
    except json.JSONDecodeError:
        logger.warning("email extraction: invalid JSON from LLM: %s", (text or "")[:200])
        return {
            "error": "llm_invalid_response",
            "proposals": [],
            "scanned_count": len(msgs),
        }

    raw_props = data.get("proposals") if isinstance(data, dict) else None
    if not isinstance(raw_props, list):
        raw_props = []

    proposals: list[dict[str, Any]] = []
    for p in raw_props:
        if not isinstance(p, dict):
            continue
        title = (p.get("title") or "").strip()
        if not title:
            continue
        try:
            title = _check_title_fidelity(title)
        except TaskFidelityError:
            # Skip overly-long titles rather than fail the whole batch.
            continue
        due = p.get("due_date")
        if due in (None, "", "null"):
            due_norm = None
        else:
            try:
                due_norm = _parse_due_date(str(due))
            except TaskFidelityError:
                due_norm = None
        detail = (p.get("detail") or "").strip() or None
        eid = (p.get("email_id") or "").strip() or None
        proposals.append({
            "title": title,
            "due_date": due_norm,
            "detail": detail,
            "email_id": eid,
        })

    return {
        "proposals": proposals,
        "scanned_count": len(msgs),
        "anchor_date": anchor,
        "note": (
            "These are PROPOSALS only — nothing has been saved. Read each one to the user "
            "and call create_task only for the ones they confirm."
        ),
    }
