"""Optional Mem0 long-term memory (user-scoped reads/writes)."""

from __future__ import annotations

import concurrent.futures as _cf
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# All blocking mem0 API calls run through this executor so they can be
# given a hard timeout rather than blocking indefinitely on retries.
_MEM0_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="mem0")

# If a mem0 API call doesn't finish within this many seconds, we abandon
# it and return empty/None so the voice tool responds immediately instead
# of leaving the user waiting forever during rate-limit retry storms.
_MEM0_TIMEOUT_S = 5.0

# Circuit-breaker: after this many consecutive timeouts/errors, suspend
# all mem0 calls for _MEM0_CB_COOLDOWN_S seconds to let the API recover.
_MEM0_CB_THRESHOLD = 3
_MEM0_CB_COOLDOWN_S = 120.0
_mem0_cb_errors: int = 0
_mem0_cb_open_until: float = 0.0


def _cb_record_error() -> None:
    global _mem0_cb_errors, _mem0_cb_open_until
    _mem0_cb_errors += 1
    if _mem0_cb_errors >= _MEM0_CB_THRESHOLD:
        _mem0_cb_open_until = time.monotonic() + _MEM0_CB_COOLDOWN_S
        logger.warning(
            "mem0 circuit-breaker OPEN for %.0fs after %d consecutive errors",
            _MEM0_CB_COOLDOWN_S, _mem0_cb_errors,
        )


def _cb_record_ok() -> None:
    global _mem0_cb_errors
    _mem0_cb_errors = 0


def _cb_is_open() -> bool:
    if time.monotonic() < _mem0_cb_open_until:
        return True
    return False

# Normalized metadata.source values for Mem0 rows (admin filtering / audits).
SOURCE_MEETING_SUMMARY = "meeting_summary"
SOURCE_CALENDAR = "calendar"
SOURCE_GMAIL = "gmail"
SOURCE_ASSISTANT_CHAT = "assistant_chat"
SOURCE_USER_COMMITMENT = "user_commitment"
SOURCE_MEETING_ARTIFACTS = "meeting_artifacts"
SOURCE_ASSISTANT_PENDING_OUTCOME = "assistant_pending_outcome"
SOURCE_VOICE_MEMORY = "voice_memory"

_memory_singleton: Any = None
_init_failed = False


def mem0_disabled_globally() -> bool:
    return os.getenv("MEETINGBOX_MEM0_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_writes_disabled() -> bool:
    return os.getenv("MEETINGBOX_MEM0_WRITES_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_runtime_ready() -> bool:
    """True only when Mem0 is BOTH not-disabled AND actually initialized.

    Distinct from `mem0_disabled_globally()`: that one only inspects the
    explicit disable env var. This helper also catches the silent
    "MEM0_API_KEY missing" / "Memory() init failed" cases. The voice
    `memory_search` tool MUST use this so it never tells the model
    "mem0_enabled=true" when the underlying client is actually unusable
    — the model would then truthfully report "I have nothing saved"
    when in fact memory is offline entirely.
    """
    if mem0_disabled_globally():
        return False
    return _memory() is not None


def _env_ingest_enabled(name: str) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    # Backward-compatible default: when unset, keep chat-turn ingest ON.
    if name == "MEETINGBOX_MEM0_INGEST_CHAT":
        return True
    return False


def _memory():
    global _memory_singleton, _init_failed
    if _init_failed:
        return None
    if _memory_singleton is not None:
        return _memory_singleton
    if mem0_disabled_globally():
        _init_failed = True
        return None
    api_key = os.getenv("MEM0_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "Mem0 is disabled: MEM0_API_KEY is not set. "
            "Get a key at https://app.mem0.ai and add MEM0_API_KEY=... to server/web/.env. "
            "Set MEETINGBOX_MEM0_DISABLE=1 to silence this warning."
        )
        _init_failed = True
        return None
    try:
        # `Memory` is the SELF-HOSTED class (takes a config dict and runs
        # against a local vector store). `MemoryClient` is the CLOUD client
        # — what our `m0-...` API keys target. mem0ai 2.x removed the
        # `api_key` kwarg from `Memory()`; passing it raises:
        #   TypeError: Memory.__init__() got an unexpected keyword argument 'api_key'
        # which previously caused silent init failure, leaving the voice
        # assistant truthfully but misleadingly saying "I have nothing
        # saved" for every memory_search call.
        from mem0 import MemoryClient

        _memory_singleton = MemoryClient(api_key=api_key)
    except Exception:
        logger.exception("Mem0 MemoryClient() initialization failed")
        _init_failed = True
        return None
    return _memory_singleton


def _mem0_search_raw(user_id: str, query: str, top_k: int) -> Any:
    if _cb_is_open():
        logger.debug("mem0 circuit-breaker open, skipping search for user=%s", user_id)
        return None
    m = _memory()
    if not m:
        return None
    fut = _MEM0_EXECUTOR.submit(
        m.search,
        (query or "").strip() or "preferences facts",
        filters={"user_id": str(user_id)},
        top_k=max(1, min(int(top_k), 50)),
    )
    try:
        result = fut.result(timeout=_MEM0_TIMEOUT_S)
        _cb_record_ok()
        return result
    except _cf.TimeoutError:
        logger.warning("mem0 search timed out (%.1fs) user=%s — circuit error %d/%d",
                       _MEM0_TIMEOUT_S, user_id, _mem0_cb_errors + 1, _MEM0_CB_THRESHOLD)
        _cb_record_error()
        return None
    except Exception:
        logger.warning("mem0 search failed user=%s", user_id, exc_info=True)
        _cb_record_error()
        return None


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
        or _cb_is_open()
    ):
        return
    body = (text or "").strip()
    if len(body) < 24:
        return
    m = _memory()
    if not m:
        return
    meta = {"source": source, **{k: v for k, v in metadata.items() if v is not None}}

    def _do_add():
        try:
            m.add(body[:12000], user_id=str(user_id), metadata=meta, infer=True)
        except Exception:
            logger.debug("mem0 background add failed source=%s", source, exc_info=True)
            _cb_record_error()

    # Fire-and-forget: submit but don't block the caller.
    _MEM0_EXECUTOR.submit(_do_add)


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


def ingest_voice_explicit_memory(
    user_id: str | None,
    *,
    fact: str,
    context_note: str | None = None,
) -> dict[str, Any]:
    """Store a concise user-stated fact from voice (Realtime `memory_remember` tool)."""
    uid = (user_id or "").strip()
    body = (fact or "").strip()
    if not uid:
        return {"stored": False, "error": "missing_user"}
    if mem0_disabled_globally():
        return {"stored": False, "mem0_enabled": False}
    if mem0_writes_disabled():
        return {"stored": False, "mem0_enabled": True, "writes_disabled": True}
    if len(body) < 12:
        return {"stored": False, "error": "too_short", "mem0_enabled": True}
    m = _memory()
    if not m:
        return {"stored": False, "mem0_enabled": False}
    cn = (context_note or "").strip()
    blob = body[:11000]
    if cn:
        blob = f"{blob}\n(Context: {cn[:4000]})"
    blob = blob[:12000]
    if _cb_is_open():
        return {"stored": False, "mem0_enabled": True, "error": "rate_limited"}
    fut = _MEM0_EXECUTOR.submit(
        m.add,
        blob,
        user_id=str(uid),
        metadata={"source": SOURCE_VOICE_MEMORY, "kind": "explicit_fact"},
        infer=True,
    )
    try:
        fut.result(timeout=_MEM0_TIMEOUT_S)
        _cb_record_ok()
    except _cf.TimeoutError:
        logger.warning("mem0 ingest_voice_explicit_memory timed out user=%s", uid)
        _cb_record_error()
        return {"stored": False, "mem0_enabled": True, "error": "timed_out"}
    except Exception:
        logger.warning("mem0 ingest_voice_explicit_memory failed user=%s", uid, exc_info=True)
        _cb_record_error()
        return {"stored": False, "mem0_enabled": True, "error": "add_failed"}
    return {"stored": True, "mem0_enabled": True}


def _log_mem0_sqlite_ingest(
    user_id: str,
    kind: str,
    ref_id: str,
    detail: str | None = None,
) -> None:
    """Persist a SQLite audit row for Mem0 payloads driven from meetings.db."""
    import uuid
    from datetime import datetime

    uid = (user_id or "").strip()
    if not uid:
        return
    try:
        from database import get_connection

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO mem0_sqlite_ingest_log (id, user_id, kind, ref_id, created_at, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    uid,
                    (kind or "")[:128],
                    (ref_id or "")[:512],
                    datetime.utcnow().isoformat(),
                    ((detail or "")[:2000] if detail else None),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("mem0_sqlite_ingest_log insert failed", exc_info=True)


def maybe_ingest_meeting_sqlite_artifacts(user_id: str | None, meeting_id: str) -> None:
    """
    Push structured meeting facts (summaries.action_items/decisions/topics, discussion_points,
    extracted actions rows) into Mem0 for Realtime + assistant recall.

    Controlled by MEETINGBOX_MEM0_INGEST_MEETING_ARTIFACTS (same truthy pattern as other ingest flags).
    """
    uid = (user_id or "").strip()
    mid = (meeting_id or "").strip()
    if not uid or not mid or mem0_disabled_globally() or mem0_writes_disabled():
        return
    if not _env_ingest_enabled("MEETINGBOX_MEM0_INGEST_MEETING_ARTIFACTS"):
        return

    from database import get_connection

    def _row(cursor, row):
        return {col[0]: row[i] for i, col in enumerate(cursor.description)}

    conn = get_connection()
    conn.row_factory = _row
    parts: list[str] = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id, title FROM meetings WHERE id = ?", (mid,))
        m = cur.fetchone()
        if not m or (m.get("user_id") or "").strip() != uid:
            return
        mt = (m.get("title") or "").strip()
        if mt:
            parts.append(f"Meeting title: {mt}")

        cur.execute(
            """
            SELECT summary, action_items, decisions, topics, sentiment, generated_at
            FROM summaries WHERE meeting_id = ?
            """,
            (mid,),
        )
        summ = cur.fetchone()
        if summ:
            for label, key in (
                ("Final summary text", "summary"),
                ("Final structured action_items", "action_items"),
                ("Final structured decisions", "decisions"),
                ("Final structured topics", "topics"),
                ("Sentiment", "sentiment"),
                ("Generated at", "generated_at"),
            ):
                val = summ.get(key)
                if not val:
                    continue
                if isinstance(val, str):
                    parts.append(f"{label}: {val[:12000]}")
                else:
                    parts.append(f"{label}: {json.dumps(val, default=str)[:12000]}")

        cur.execute(
            """
            SELECT summary, discussion_points, action_items, decisions, topics, sentiment, generated_at
            FROM local_summaries WHERE meeting_id = ?
            """,
            (mid,),
        )
        loc = cur.fetchone()
        if loc:
            for label, key in (
                ("Live/over-the-air summary text", "summary"),
                ("Live discussion_points", "discussion_points"),
                ("Live structured action_items", "action_items"),
                ("Live structured decisions", "decisions"),
                ("Live structured topics", "topics"),
                ("Live sentiment", "sentiment"),
                ("Live generated at", "generated_at"),
            ):
                val = loc.get(key)
                if not val:
                    continue
                if isinstance(val, str):
                    parts.append(f"{label}: {val[:12000]}")
                else:
                    parts.append(f"{label}: {json.dumps(val, default=str)[:12000]}")

        cur.execute(
            """
            SELECT type, title, description, status, connector_target, kind, draft
            FROM actions
            WHERE meeting_id = ?
            ORDER BY datetime(COALESCE(executed_at, created_at)) DESC
            LIMIT 48
            """,
            (mid,),
        )
        acts = cur.fetchall()
        if acts:
            slim = []
            for a in acts:
                slim.append(
                    {
                        k: a.get(k)
                        for k in (
                            "type",
                            "title",
                            "description",
                            "status",
                            "connector_target",
                            "kind",
                        )
                        if a.get(k)
                    }
                )
            parts.append(f"Suggested / extracted actions ({len(slim)}): {json.dumps(slim, default=str)[:14000]}")
    finally:
        conn.close()

    blob = "\n".join(parts).strip()
    if len(blob) < 48:
        return
    prefix = f"Meeting archive ({mid}) — SQLite facts for assistant recall:\n"
    body = prefix + blob
    body = body[:11800]

    m = _memory()
    if not m:
        return
    meta = {"source": SOURCE_MEETING_ARTIFACTS, "meeting_id": mid}
    try:
        m.add(body, user_id=str(uid), metadata=meta, infer=True)
        _log_mem0_sqlite_ingest(uid, SOURCE_MEETING_ARTIFACTS, mid, detail=f"bytes={len(body)}")
    except Exception:
        logger.exception("mem0 meeting_artifacts ingest failed meeting_id=%s", mid)


def maybe_ingest_pending_assistant_outcome(
    user_id: str | None,
    *,
    pending_id: str,
    tool_name: str,
    status: str,
    brief_label: str | None = None,
    error: str | None = None,
) -> None:
    """Record approve/reject/fail outcomes for queued assistant writes (short Mem0 digest)."""
    uid = (user_id or "").strip()
    pid = (pending_id or "").strip()
    if not uid or not pid:
        return
    bits = [f"Pending assistant outcome status={status}", f"tool={tool_name}", f"pending_id={pid}"]
    if brief_label:
        bits.append(f"summary={brief_label[:400]}")
    if error:
        bits.append(f"error={str(error).strip()[:800]}")
    text = "; ".join(bits)
    _optional_ingest(
        uid,
        "MEETINGBOX_MEM0_INGEST_PENDING_OUTCOMES",
        SOURCE_ASSISTANT_PENDING_OUTCOME,
        text,
        {"pending_id": pid, "tool": tool_name, "status": status},
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
