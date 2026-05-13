"""Optional Mem0 long-term memory (user-scoped reads/writes)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_memory_singleton: Any = None
_init_failed = False


def mem0_disabled_globally() -> bool:
    return os.getenv("MEETINGBOX_MEM0_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_writes_disabled() -> bool:
    return os.getenv("MEETINGBOX_MEM0_WRITES_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


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


def search_context_for_prompt(user_id: str | None, query: str, top_k: int = 8) -> str:
    """
    Returns a short text block for orchestrator context. Treat as untrusted facts (prompt injection safe framing upstream).
    """
    if not user_id or mem0_disabled_globally():
        return ""
    m = _memory()
    if not m:
        return ""
    try:
        hits = m.search((query or "").strip() or "preferences facts", filters={"user_id": str(user_id)}, top_k=top_k)
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
            metadata={"source": "meeting_summary", "meeting_id": meeting_id},
            infer=True,
        )
    except Exception:
        logger.exception("mem0 add failed meeting_id=%s", meeting_id)


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
