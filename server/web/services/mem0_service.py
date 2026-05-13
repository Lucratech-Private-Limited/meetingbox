"""Optional Mem0 long-term memory (user-scoped reads/writes)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Normalized metadata.source values for Mem0 rows (admin filtering / audits).
SOURCE_MEETING_SUMMARY = "meeting_summary"
SOURCE_CALENDAR = "calendar"
SOURCE_GMAIL = "gmail"
SOURCE_ASSISTANT_CHAT = "assistant_chat"
SOURCE_USER_COMMITMENT = "user_commitment"

_memory_singleton: Any = None
_init_failed = False


def mem0_disabled_globally() -> bool:
    return os.getenv("MEETINGBOX_MEM0_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_writes_disabled() -> bool:
    return os.getenv("MEETINGBOX_MEM0_WRITES_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def _env_ingest_enabled(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in ("1", "true", "yes", "on")


def _memory():
    global _memory_singleton, _init_failed
    if _init_failed:
        return None
    if _memory_singleton is not None:
        return _memory_singleton
    if mem0_disabled_globally():
        _init_failed = True
        return None
    try:
        from mem0 import Memory

        _memory_singleton = Memory()
    except Exception:
        logger.exception("Mem0 Memory() initialization failed")
        _init_failed = True
        return None
    return _memory_singleton


def _mem0_search_raw(user_id: str, query: str, top_k: int) -> Any:
    m = _memory()
    if not m:
        return None
    return m.search(
        (query or "").strip() or "preferences facts",
        filters={"user_id": str(user_id)},
        top_k=max(1, min(int(top_k), 50)),
    )


def search_context_for_prompt(user_id: str | None, query: str, top_k: int = 8) -> str:
    """
    Returns a short text block for orchestrator context. Treat as untrusted facts (prompt injection safe framing upstream).
    """
    if not user_id or mem0_disabled_globally():
        return ""
    try:
        hits = _mem0_search_raw(str(user_id).strip(), query, top_k)
    except Exception:
        logger.exception("mem0 search failed")
        return ""
    if not hits:
        return ""
    try:
        blob = json.dumps(hits, default=str, ensure_ascii=False)[:12000]
    except (TypeError, ValueError):
        blob = str(hits)[:12000]
    return blob


def search_memories_for_user(user_id: str, query: str, top_k: int = 8) -> dict[str, Any]:
    """
    Structured Mem0 search for a single user (admin APIs and tooling).
    Returns serializable dict; empty hits if Mem0 is off or uninitialized.
    """
    uid = (user_id or "").strip()
    if not uid or mem0_disabled_globally():
        return {"user_id": uid, "hits": [], "mem0_enabled": False}
    try:
        hits = _mem0_search_raw(uid, query, top_k)
    except Exception:
        logger.exception("mem0 search_memories_for_user failed")
        return {"user_id": uid, "hits": [], "mem0_enabled": True, "error": "search_failed"}
    if hits is None:
        return {"user_id": uid, "hits": [], "mem0_enabled": False}
    try:
        safe = json.loads(json.dumps(hits, default=str))
    except (TypeError, ValueError):
        safe = [{"raw": str(hits)[:8000]}]
    return {"user_id": uid, "hits": safe, "mem0_enabled": True}


def _optional_ingest(
    user_id: str | None,
    env_name: str,
    source: str,
    text: str,
    metadata: dict[str, Any],
) -> None:
    if (
        not user_id
        or mem0_disabled_globally()
        or mem0_writes_disabled()
        or not _env_ingest_enabled(env_name)
    ):
        return
    body = (text or "").strip()
    if len(body) < 24:
        return
    m = _memory()
    if not m:
        return
    meta = {"source": source, **{k: v for k, v in metadata.items() if v is not None}}
    try:
        m.add(body[:12000], user_id=str(user_id), metadata=meta, infer=True)
    except Exception:
        logger.exception("mem0 add failed source=%s", source)


def maybe_ingest_meeting_summary(user_id: str | None, meeting_id: str, summary_text: str) -> None:
    """Opt-in: store distilled summary text in Mem0 after a meeting is summarized."""
    if (
        not user_id
        or mem0_disabled_globally()
        or mem0_writes_disabled()
        or os.getenv("MEETINGBOX_MEM0_AUTO_INGEST_SUMMARY", "").strip().lower() not in ("1", "true", "yes", "on")
    ):
        return
    text = (summary_text or "").strip()
    if len(text) < 40:
        return
    m = _memory()
    if not m:
        return
    try:
        m.add(
            f"Meeting summary ({meeting_id}): {text[:12000]}",
            user_id=str(user_id),
            metadata={"source": SOURCE_MEETING_SUMMARY, "meeting_id": meeting_id},
            infer=True,
        )
    except Exception:
        logger.exception("mem0 add failed meeting_id=%s", meeting_id)


def maybe_ingest_calendar_snapshot(user_id: str | None, result: dict[str, Any]) -> None:
    """Opt-in: persist a compact calendar list snapshot after a successful tool read."""
    if not user_id or not isinstance(result, dict):
        return
    try:
        blob = json.dumps(result.get("events") or [], default=str, ensure_ascii=False)[:10000]
    except (TypeError, ValueError):
        blob = str(result)[:10000]
    n = result.get("count", 0)
    text = f"Calendar snapshot ({n} events): {blob}"
    _optional_ingest(
        user_id,
        "MEETINGBOX_MEM0_INGEST_CALENDAR",
        SOURCE_CALENDAR,
        text,
        {"event_count": n},
    )


def maybe_ingest_gmail_snapshot(user_id: str | None, result: dict[str, Any]) -> None:
    """Opt-in: persist a compact Gmail list snapshot after a successful tool read."""
    if not user_id or not isinstance(result, dict):
        return
    try:
        blob = json.dumps(result.get("messages") or [], default=str, ensure_ascii=False)[:10000]
    except (TypeError, ValueError):
        blob = str(result)[:10000]
    n = result.get("count", 0)
    text = f"Gmail snapshot ({n} messages): {blob}"
    _optional_ingest(
        user_id,
        "MEETINGBOX_MEM0_INGEST_GMAIL",
        SOURCE_GMAIL,
        text,
        {"message_count": n},
    )


def maybe_ingest_commitment_row(user_id: str | None, row: dict[str, Any]) -> None:
    """Push a structured commitment into Mem0 (default on; set MEETINGBOX_MEM0_INGEST_COMMITMENTS=0 to skip)."""
    if not user_id or not isinstance(row, dict) or not row.get("id"):
        return
    if mem0_disabled_globally() or mem0_writes_disabled():
        return
    raw = (os.getenv("MEETINGBOX_MEM0_INGEST_COMMITMENTS", "1") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return
    from services.commitments_service import format_commitment_for_mem0

    text = format_commitment_for_mem0(row)
    if len(text) < 16:
        return
    m = _memory()
    if not m:
        return
    try:
        tags_json = row.get("tags") or "[]"
        m.add(
            text[:12000],
            user_id=str(user_id),
            metadata={
                "source": SOURCE_USER_COMMITMENT,
                "commitment_id": str(row.get("id")),
                "status": str(row.get("status") or ""),
                "tags": tags_json if len(str(tags_json)) < 2000 else "[]",
            },
            infer=True,
        )
    except Exception:
        logger.exception("mem0 add failed commitment id=%s", row.get("id"))


def maybe_ingest_assistant_turn(
    user_id: str | None,
    *,
    user_message: str,
    assistant_reply: str,
    routed_agent_id: str | None,
    meeting_id: str | None = None,
) -> None:
    """Opt-in: store a short assistant Q/A turn for long-term recall (morning brief context)."""
    if not user_id:
        return
    um = (user_message or "").strip()
    ar = (assistant_reply or "").strip()
    if len(um) < 4 and len(ar) < 12:
        return
    text = f"User: {um[:4000]}\nAssistant: {ar[:8000]}"
    _optional_ingest(
        user_id,
        "MEETINGBOX_MEM0_INGEST_CHAT",
        SOURCE_ASSISTANT_CHAT,
        text,
        {"routed_agent_id": routed_agent_id or "", "meeting_id": meeting_id or ""},
    )


def delete_user_memories(user_id: str) -> None:
    """Best-effort purge when a user account is removed (hook from auth if needed)."""
    if not user_id or mem0_disabled_globally():
        return
    m = _memory()
    if not m:
        return
    try:
        if hasattr(m, "delete_all"):
            m.delete_all(user_id=str(user_id))
    except Exception:
        logger.exception("mem0 delete_all failed user_id=%s", user_id)
