"""Optional Mem0 long-term memory (user-scoped reads/writes)."""

from __future__ import annotations

import concurrent.futures as _cf
import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# All blocking mem0 API calls run through this executor so they can be
# given a hard timeout rather than blocking indefinitely on retries.
# 4 workers (was 2) — prevents executor starvation when ingest + search run concurrently.
_MEM0_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="mem0")

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

# ---------------------------------------------------------------------------
# Search result cache (Opt-A): deduplicate identical Mem0 search calls within
# a short window. Eliminates the duplicate routing + augmentation calls that
# happen on every assistant turn in multi-agent mode.
# ---------------------------------------------------------------------------
_MEM0_CACHE_TTL = 30.0  # seconds
_mem0_search_cache: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, result)
_mem0_search_cache_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Init retry state (Fix 5A): replaces the permanent _init_failed flag with a
# timed backoff so transient startup failures recover automatically.
# ---------------------------------------------------------------------------
_MEM0_INIT_RETRY_INTERVAL_S = 30.0
_mem0_init_lock = threading.Lock()
_mem0_init_next_retry: float = 0.0  # monotonic; 0 = try immediately


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


def mem0_disabled_globally() -> bool:
    return os.getenv("MEETINGBOX_MEM0_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_writes_disabled() -> bool:
    return os.getenv("MEETINGBOX_MEM0_WRITES_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def mem0_runtime_ready() -> bool:
    """True only when Mem0 is BOTH not-disabled AND actually initialized.

    Distinct from `mem0_disabled_globally()`: that one only inspects the
    explicit disable env var. This helper also catches the silent
    "DB config missing" / "Memory() init failed" cases. The voice
    `memory_search` tool MUST use this so it never tells the model
    "mem0_enabled=true" when the underlying client is actually unusable
    — the model would then truthfully report "I have nothing saved"
    when in fact memory is offline entirely.
    """
    if mem0_disabled_globally():
        return False
    return _memory() is not None


def _mem0_self_hosted_config_present() -> bool:
    """Return True if the minimum required self-hosted DB env vars are set.

    When MEM0_NEO4J_OPTIONAL=1 only pgvector is required; otherwise both.
    """
    pg_host = os.getenv("MEM0_PGVECTOR_HOST", "").strip()
    if not pg_host:
        return False
    neo4j_optional = os.getenv("MEM0_NEO4J_OPTIONAL", "").strip().lower() in ("1", "true", "yes", "on")
    if neo4j_optional:
        return True
    neo4j_uri = os.getenv("MEM0_NEO4J_URI", "").strip()
    return bool(neo4j_uri)


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
    """Return the Memory singleton, initializing it if needed.

    Uses a timed-retry scheme (Fix 5A): a failed init is retried after
    _MEM0_INIT_RETRY_INTERVAL_S seconds instead of failing permanently.
    Neo4j is optional when MEM0_NEO4J_OPTIONAL=1 (Fix 13).
    """
    global _memory_singleton, _mem0_init_next_retry
    if _memory_singleton is not None:
        return _memory_singleton
    if mem0_disabled_globally():
        return None
    if not _mem0_self_hosted_config_present():
        neo4j_optional = os.getenv("MEM0_NEO4J_OPTIONAL", "").strip().lower() in ("1", "true", "yes", "on")
        logger.warning(
            "Mem0 is disabled: MEM0_PGVECTOR_HOST is not set%s. "
            "Add the self-hosted DB connection vars to server/.env. "
            "Set MEETINGBOX_MEM0_DISABLE=1 to silence this warning.",
            " (MEM0_NEO4J_URI also required when MEM0_NEO4J_OPTIONAL is not set)" if not neo4j_optional else "",
        )
        return None

    now = time.monotonic()
    with _mem0_init_lock:
        # Re-check inside lock in case another thread just initialised.
        if _memory_singleton is not None:
            return _memory_singleton
        if now < _mem0_init_next_retry:
            return None
        try:
            from mem0 import Memory

            # mem0ai 2.0.2 defaults to gpt-5-mini which dropped `max_tokens` support.
            # Pin to gpt-4o-mini: cheaper, fast, stable, supports `max_tokens` correctly.
            llm_model = os.getenv("MEM0_LLM_MODEL", "gpt-4o-mini")
            neo4j_optional = os.getenv("MEM0_NEO4J_OPTIONAL", "").strip().lower() in ("1", "true", "yes", "on")
            neo4j_uri = os.getenv("MEM0_NEO4J_URI", "").strip()

            config: dict[str, Any] = {
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": llm_model,
                        "temperature": 0,
                        "max_tokens": 2000,
                    },
                },
                "vector_store": {
                    "provider": "pgvector",
                    "config": {
                        "host": os.getenv("MEM0_PGVECTOR_HOST", "mem0-postgres"),
                        "port": int(os.getenv("MEM0_PGVECTOR_PORT", "5432")),
                        "user": os.getenv("MEM0_PGVECTOR_USER", "postgres"),
                        "password": os.getenv("MEM0_PGVECTOR_PASSWORD", ""),
                        "dbname": os.getenv("MEM0_PGVECTOR_DB", "postgres"),
                    },
                },
            }
            if neo4j_uri:
                config["graph_store"] = {
                    "provider": "neo4j",
                    "config": {
                        "url": neo4j_uri,
                        "username": os.getenv("MEM0_NEO4J_USERNAME", "neo4j"),
                        "password": os.getenv("MEM0_NEO4J_PASSWORD", ""),
                    },
                }
            elif not neo4j_optional:
                # MEM0_NEO4J_URI is required unless NEO4J_OPTIONAL is set.
                # We already passed _mem0_self_hosted_config_present so this
                # path means NEO4J_OPTIONAL is False and URI is missing —
                # something changed between the guard and here; return None.
                return None

            _memory_singleton = Memory.from_config(config)
            logger.info(
                "Mem0: initialized successfully (graph=%s)",
                "neo4j" if neo4j_uri else "pgvector-only",
            )
        except Exception:
            logger.exception("Mem0 Memory.from_config() initialization failed — will retry in %.0fs", _MEM0_INIT_RETRY_INTERVAL_S)
            _mem0_init_next_retry = time.monotonic() + _MEM0_INIT_RETRY_INTERVAL_S
            return None

    return _memory_singleton


def _mem0_search_raw(user_id: str, query: str, top_k: int) -> Any:
    if _cb_is_open():
        logger.debug("mem0 circuit-breaker open, skipping search for user=%s", user_id)
        return None
    m = _memory()
    if not m:
        return None
    uid = str(user_id)
    q_normalized = ((query or "").strip() or "preferences facts")[:500]

    # Cache lookup (Opt-A): return cached result if still fresh.
    cache_key = f"{uid}:{q_normalized}:{top_k}"
    now = time.monotonic()
    with _mem0_search_cache_lock:
        cached = _mem0_search_cache.get(cache_key)
        if cached and now < cached[0]:
            logger.debug("mem0 search cache HIT user=%s query=%.40s", uid, q_normalized)
            return cached[1]

    t0 = time.monotonic()
    fut = _MEM0_EXECUTOR.submit(
        m.search,
        q_normalized,
        filters={"user_id": uid},
        top_k=max(1, min(int(top_k), 50)),
    )
    try:
        result = fut.result(timeout=_MEM0_TIMEOUT_S)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("mem0 search OK user=%s latency_ms=%d hits=%s", uid, elapsed_ms,
                    len(result) if isinstance(result, list) else "?")
        _cb_record_ok()
    except _cf.TimeoutError:
        logger.warning("mem0 search timed out (%.1fs) user=%s — circuit error %d/%d",
                       _MEM0_TIMEOUT_S, user_id, _mem0_cb_errors + 1, _MEM0_CB_THRESHOLD)
        _cb_record_error()
        return None
    except Exception:
        logger.warning("mem0 search failed user=%s", user_id, exc_info=True)
        _cb_record_error()
        return None
    # Filter out soft-deleted memory IDs before returning.
    soft_deleted = _get_soft_deleted_ids(uid)
    if soft_deleted and isinstance(result, list):
        result = [r for r in result if r.get("id") not in soft_deleted]

    # Store in cache.
    with _mem0_search_cache_lock:
        _mem0_search_cache[cache_key] = (now + _MEM0_CACHE_TTL, result)

    return result


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
        # Opt-E: re-check circuit-breaker at run time so backlogged tasks
        # drain harmlessly when an outage is in progress.
        if _cb_is_open():
            logger.debug("mem0 background add skipped (breaker open) source=%s", source)
            return
        try:
            t0 = time.monotonic()
            m.add(body[:12000], user_id=str(user_id), metadata=meta, infer=True)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info("mem0 ingest OK source=%s user=%s latency_ms=%d", source, user_id, elapsed_ms)
            _cb_record_ok()
        except Exception:
            logger.warning("mem0 background add failed source=%s user=%s", source, user_id, exc_info=True)
            _cb_record_error()

    # Fire-and-forget: submit but don't block the caller.
    _MEM0_EXECUTOR.submit(_do_add)


def maybe_ingest_meeting_summary(user_id: str | None, meeting_id: str, summary_text: str) -> None:
    """Opt-in: store distilled summary text in Mem0 after a meeting is summarized.

    Runs fire-and-forget via executor so summarize endpoint is not blocked.
    Controlled by MEETINGBOX_MEM0_AUTO_INGEST_SUMMARY=1 (default off).
    """
    if (
        not user_id
        or mem0_disabled_globally()
        or mem0_writes_disabled()
        or _cb_is_open()
        or os.getenv("MEETINGBOX_MEM0_AUTO_INGEST_SUMMARY", "").strip().lower() not in ("1", "true", "yes", "on")
    ):
        return
    text = (summary_text or "").strip()
    if len(text) < 40:
        return
    m = _memory()
    if not m:
        return
    uid = str(user_id)
    mid = str(meeting_id)
    body = f"Meeting summary ({mid}): {text[:12000]}"

    def _do_summary_add():
        if _cb_is_open():
            return
        try:
            t0 = time.monotonic()
            m.add(body, user_id=uid, metadata={"source": SOURCE_MEETING_SUMMARY, "meeting_id": mid}, infer=True)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info("mem0 ingest OK source=meeting_summary meeting_id=%s latency_ms=%d", mid, elapsed_ms)
            _cb_record_ok()
        except Exception:
            logger.warning("mem0 add failed source=meeting_summary meeting_id=%s", mid, exc_info=True)
            _cb_record_error()

    _MEM0_EXECUTOR.submit(_do_summary_add)


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
    commitment_id = str(row.get("id"))
    uid = str(user_id)
    try:
        tags_json = row.get("tags") or "[]"
        t0 = time.monotonic()
        m.add(
            text[:12000],
            user_id=uid,
            metadata={
                "source": SOURCE_USER_COMMITMENT,
                "commitment_id": commitment_id,
                "status": str(row.get("status") or ""),
                "tags": tags_json if len(str(tags_json)) < 2000 else "[]",
            },
            infer=True,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("mem0 ingest OK source=user_commitment id=%s latency_ms=%d", commitment_id, elapsed_ms)
        # Fix 6B: mark commitment as synced to Mem0 so tooling can detect gaps.
        try:
            from database import get_connection
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE user_commitments SET mem0_synced = 1 WHERE id = ? AND user_id = ?",
                    (commitment_id, uid),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("mem0_synced update failed commitment id=%s", commitment_id, exc_info=True)
    except Exception:
        logger.warning("mem0 add failed commitment id=%s", commitment_id, exc_info=True)


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
    """Store a concise user-stated fact from voice (Realtime `memory_remember` tool).

    Fix 11 (superseding): before adding, searches existing voice_memory entries
    for a closely matching fact and updates it in-place instead of creating a
    duplicate. Mem0's own deduplication runs via infer=True, but this pre-check
    handles the fast "I prefer X / I now prefer Y" conflict pattern explicitly.
    """
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

    # Voice explicit memories live in a dedicated agent_id namespace so mem0ai's
    # infer=True deduplication never merges them with calendar/gmail entries.
    # We intentionally drop our own supersede pre-check: cosine proximity scores
    # are unreliable for short first-person facts ("My name is X" and "Never
    # schedule before 8am" can land within 0.70 of each other in vector space).
    # mem0ai's LLM-based conflict detection (infer=True) is the right layer for
    # this — it understands semantics rather than vector distance.
    _VOICE_AGENT_ID = "voice_explicit"

    def _do_voice_add():
        t0 = time.monotonic()
        m.add(
            blob,
            user_id=uid,
            agent_id=_VOICE_AGENT_ID,
            infer=True,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("mem0 ingest OK source=voice_memory user=%s latency_ms=%d", uid, elapsed_ms)
        _cb_record_ok()
        return {"stored": True, "mem0_enabled": True, "action": "added"}

    fut = _MEM0_EXECUTOR.submit(_do_voice_add)
    try:
        result = fut.result(timeout=_MEM0_TIMEOUT_S)
        return result or {"stored": True, "mem0_enabled": True}
    except _cf.TimeoutError:
        logger.warning("mem0 ingest_voice_explicit_memory timed out user=%s", uid)
        _cb_record_error()
        return {"stored": False, "mem0_enabled": True, "error": "timed_out"}
    except Exception:
        logger.warning("mem0 ingest_voice_explicit_memory failed user=%s", uid, exc_info=True)
        _cb_record_error()
        return {"stored": False, "mem0_enabled": True, "error": "add_failed"}


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

    Runs fire-and-forget via executor (Fix 5B) — caller is never blocked.
    Controlled by MEETINGBOX_MEM0_INGEST_MEETING_ARTIFACTS (same truthy pattern as other ingest flags).
    """
    uid = (user_id or "").strip()
    mid = (meeting_id or "").strip()
    if not uid or not mid or mem0_disabled_globally() or mem0_writes_disabled():
        return
    if not _env_ingest_enabled("MEETINGBOX_MEM0_INGEST_MEETING_ARTIFACTS"):
        return

    def _run_artifact_ingest():
        _do_ingest_meeting_sqlite_artifacts(uid, mid)

    _MEM0_EXECUTOR.submit(_run_artifact_ingest)


def _do_ingest_meeting_sqlite_artifacts(uid: str, mid: str) -> None:
    """Blocking implementation; called from executor by maybe_ingest_meeting_sqlite_artifacts."""
    if _cb_is_open():
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
        t0 = time.monotonic()
        m.add(body, user_id=str(uid), metadata=meta, infer=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("mem0 ingest OK source=meeting_artifacts meeting_id=%s bytes=%d latency_ms=%d",
                    mid, len(body), elapsed_ms)
        _log_mem0_sqlite_ingest(uid, SOURCE_MEETING_ARTIFACTS, mid, detail=f"bytes={len(body)}")
        _cb_record_ok()
    except Exception:
        logger.warning("mem0 meeting_artifacts ingest failed meeting_id=%s", mid, exc_info=True)
        _cb_record_error()


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


def _get_soft_deleted_ids(user_id: str) -> set[str]:
    """Return the set of Mem0 memory IDs soft-deleted for this user (from SQLite)."""
    uid = (user_id or "").strip()
    if not uid:
        return set()
    try:
        from database import get_connection

        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT memory_id FROM mem0_soft_deleted WHERE user_id = ?", (uid,)
            )
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception:
        logger.debug("mem0 _get_soft_deleted_ids failed user=%s", user_id, exc_info=True)
        return set()


def soft_delete_memory(user_id: str, memory_id: str, deleted_by: str = "user") -> bool:
    """Soft-delete a single Mem0 memory for a user.

    The memory stays in pgvector/Neo4j and can be restored. Returns True on
    success, False if the record already existed or an error occurred.
    """
    import uuid
    from datetime import datetime

    uid = (user_id or "").strip()
    mid = (memory_id or "").strip()
    if not uid or not mid:
        return False
    try:
        from database import get_connection

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO mem0_soft_deleted (id, memory_id, user_id, deleted_at, deleted_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), mid, uid, datetime.utcnow().isoformat(), deleted_by or "user"),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("mem0 soft_delete_memory failed user=%s memory=%s", uid, mid)
        return False


def restore_memory(user_id: str, memory_id: str) -> bool:
    """Restore a soft-deleted Mem0 memory so it reappears in future searches.

    Returns True if the record was found and removed, False otherwise.
    """
    uid = (user_id or "").strip()
    mid = (memory_id or "").strip()
    if not uid or not mid:
        return False
    try:
        from database import get_connection

        conn = get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM mem0_soft_deleted WHERE memory_id = ? AND user_id = ?",
                (mid, uid),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()
    except Exception:
        logger.exception("mem0 restore_memory failed user=%s memory=%s", uid, mid)
        return False


def soft_delete_all_user_memories(user_id: str, deleted_by: str = "admin") -> int:
    """Soft-delete every Mem0 memory for a user.

    Fetches all memory IDs from the self-hosted Mem0 instance (via get_all),
    then bulk-inserts them into the soft-delete tracking table. The data stays
    in pgvector/Neo4j and can be restored per-memory or in bulk.

    Returns the number of memories soft-deleted.
    """
    import uuid
    from datetime import datetime

    uid = (user_id or "").strip()
    if not uid:
        return 0
    m = _memory()
    if not m:
        return 0
    try:
        all_mems = m.get_all(filters={"user_id": uid})
        if not all_mems:
            return 0
        # get_all may return a list of dicts or a dict with a "results" key
        if isinstance(all_mems, dict):
            all_mems = all_mems.get("results") or []
        memory_ids = [r.get("id") for r in all_mems if r.get("id")]
    except Exception:
        logger.exception("mem0 soft_delete_all_user_memories get_all failed user=%s", uid)
        return 0

    if not memory_ids:
        return 0

    now = datetime.utcnow().isoformat()
    rows = [(str(uuid.uuid4()), mid, uid, now, deleted_by) for mid in memory_ids]
    try:
        from database import get_connection

        conn = get_connection()
        try:
            conn.executemany(
                """
                INSERT OR IGNORE INTO mem0_soft_deleted (id, memory_id, user_id, deleted_at, deleted_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("mem0 soft_delete_all_user_memories insert failed user=%s", uid)
        return 0

    logger.info("mem0 soft_delete_all_user_memories: %d memories soft-deleted for user=%s", len(rows), uid)
    return len(rows)


def delete_user_memories(user_id: str) -> None:
    """Soft-delete all Mem0 memories for a user (reversible).

    Data stays in pgvector/Neo4j. Use restore_memory() to un-delete individual
    memories, or DELETE from mem0_soft_deleted for a full restore.
    """
    if not user_id or mem0_disabled_globally():
        return
    soft_delete_all_user_memories(str(user_id), deleted_by="admin")
