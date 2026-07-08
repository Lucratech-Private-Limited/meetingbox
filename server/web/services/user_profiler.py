"""Background user profiler: synthesizes personality, interests, and work patterns.

Runs as a daemon thread — NEVER called during voice sessions or active agent tasks.
Triggers:
  - APScheduler cron at 03:00 UTC daily (registered in main.py)
  - Fire-and-forget after meeting summarize (via trigger_profile_build_bg)

Idempotency: uses analysis_runs table (job_type='user_profile') so only one build
runs per user per calendar day — safe with multiple uvicorn workers.

Output: a structured profile stored in Mem0 under agent_id='user_profile' with infer=False.
Old profile entries are deleted before the new one is written (replace-not-accumulate).
"""

from __future__ import annotations

import concurrent.futures as _cf
import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_PROFILE_AGENT_ID = "user_profile"
_PROFILER_OPENAI_TIMEOUT_S = 45.0  # max seconds to wait for LLM synthesis call


def profiler_enabled() -> bool:
    """Background user-profiler is ON by default; set MEETINGBOX_USER_PROFILER_ENABLED to a
    falsey value (0/false/no/off) to disable. Building a rich personality/interests profile is
    core to acting like a personal assistant with full awareness of the user, so it should not
    require explicit opt-in."""
    return os.getenv("MEETINGBOX_USER_PROFILER_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

# Dedicated executor for profiler Mem0 calls — kept separate from _MEM0_EXECUTOR
# so the profiler NEVER competes with live voice session calls for executor slots.
# 2 workers is sufficient: profiler runs at low-traffic times (03:00 UTC or post-meeting).
_PROFILER_MEM0_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="profiler-mem0")


# ---------------------------------------------------------------------------
# Idempotency helpers (reuse analysis_runs pattern)
# ---------------------------------------------------------------------------

def _claim_profile_run(user_id: str) -> str | None:
    """Claim a profile run slot for today. Returns run_id on success, None if already claimed."""
    from database import get_connection

    run_id = str(uuid.uuid4())
    run_date = date.today().isoformat()
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO analysis_runs
              (id, user_id, job_type, run_date, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (run_id, user_id, _PROFILE_AGENT_ID, run_date, now),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT id FROM analysis_runs WHERE user_id=? AND job_type=? AND run_date=?",
            (user_id, _PROFILE_AGENT_ID, run_date),
        )
        row = cur.fetchone()
        if row and row[0] == run_id:
            return run_id
        return None
    except Exception:
        logger.debug("_claim_profile_run failed user=%s", user_id, exc_info=True)
        return None
    finally:
        conn.close()


def _finish_profile_run(run_id: str, status: str, detail: str | None = None) -> None:
    from database import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE analysis_runs SET status=?, result_summary=?, completed_at=? WHERE id=?",
            (status, detail, datetime.utcnow().isoformat(), run_id),
        )
        conn.commit()
    except Exception:
        logger.debug("_finish_profile_run failed run_id=%s", run_id, exc_info=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Signal collection from SQLite
# ---------------------------------------------------------------------------

def _collect_sqlite_signals(user_id: str) -> dict[str, Any]:
    """Pull structured signals from SQLite: meetings, commitments, known_contacts."""
    from database import get_connection

    def _rf(cursor, row):
        return {col[0]: row[i] for i, col in enumerate(cursor.description)}

    signals: dict[str, Any] = {}
    conn = get_connection()
    conn.row_factory = _rf
    cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
    cutoff_90d = (datetime.utcnow() - timedelta(days=90)).isoformat()
    try:
        # Recent meeting titles + summaries (last 30 days)
        cur = conn.execute(
            """
            SELECT m.title, s.summary, s.action_items, s.decisions, s.topics
            FROM meetings m
            LEFT JOIN summaries s ON s.meeting_id = m.id
            WHERE m.user_id = ?
              AND COALESCE(m.start_time, m.created_at, '') >= ?
              AND (s.summary IS NOT NULL AND TRIM(s.summary) != '')
            ORDER BY COALESCE(m.start_time, m.created_at) DESC
            LIMIT 20
            """,
            (user_id, cutoff_30d),
        )
        meetings = cur.fetchall()
        if meetings:
            signals["recent_meetings"] = [
                {
                    "title": r.get("title") or "",
                    "summary_snippet": (r.get("summary") or "")[:600],
                    "action_items": r.get("action_items") or "",
                    "decisions": r.get("decisions") or "",
                    "topics": r.get("topics") or "",
                }
                for r in meetings
            ]

        # Open + recent commitments (last 90 days)
        cur = conn.execute(
            """
            SELECT title, description, status, tags
            FROM user_commitments
            WHERE user_id = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (user_id, cutoff_90d),
        )
        commitments = cur.fetchall()
        if commitments:
            signals["recent_commitments"] = [
                {
                    "title": r.get("title") or "",
                    "status": r.get("status") or "",
                    "tags": r.get("tags") or "",
                }
                for r in commitments
            ]

        # Known contacts (most frequent collaborators)
        cur = conn.execute(
            """
            SELECT email, display_name, interaction_count
            FROM known_contacts
            WHERE user_id = ?
            ORDER BY interaction_count DESC
            LIMIT 20
            """,
            (user_id,),
        )
        contacts = cur.fetchall()
        if contacts:
            signals["frequent_contacts"] = [
                {
                    "name": r.get("display_name") or r.get("email") or "",
                    "email": r.get("email") or "",
                    "interactions": r.get("interaction_count") or 0,
                }
                for r in contacts
            ]
    except Exception:
        logger.debug("_collect_sqlite_signals query failed user=%s", user_id, exc_info=True)
    finally:
        conn.close()

    return signals


# ---------------------------------------------------------------------------
# Signal collection from Mem0
# ---------------------------------------------------------------------------

def _collect_mem0_signals(user_id: str) -> dict[str, Any]:
    """Query Mem0 for existing explicit memories and recent assistant patterns.

    Uses _PROFILER_MEM0_EXECUTOR (2 workers, dedicated) instead of _MEM0_EXECUTOR
    so the profiler never competes with live voice session calls for executor slots.
    Circuit-breaker is checked before submitting any calls.
    """
    try:
        from services.mem0_service import mem0_runtime_ready, _memory, _MEM0_TIMEOUT_S, _cb_is_open

        if not mem0_runtime_ready() or _cb_is_open():
            return {}
        m = _memory()
        if not m:
            return {}

        uid = str(user_id)
        signals: dict[str, Any] = {}
        timeout = _MEM0_TIMEOUT_S + 5.0  # background job — slightly more generous than voice

        # Fan out all 4 Mem0 calls in parallel through the dedicated profiler executor
        # (not _MEM0_EXECUTOR) so live voice session calls are never starved.
        fut_explicit = _PROFILER_MEM0_EXECUTOR.submit(
            m.get_all, filters={"user_id": uid, "agent_id": "voice_explicit"}, top_k=100
        )
        fut_meetings = _PROFILER_MEM0_EXECUTOR.submit(
            m.search, "meeting topics decisions projects", filters={"user_id": uid}, top_k=8
        )
        fut_prefs = _PROFILER_MEM0_EXECUTOR.submit(
            m.search, "user prefers schedule preference communication", filters={"user_id": uid}, top_k=8
        )
        fut_interests = _PROFILER_MEM0_EXECUTOR.submit(
            m.search, "interests finance technology sports games art", filters={"user_id": uid}, top_k=8
        )

        def _safe_result(fut, label: str):
            try:
                return fut.result(timeout=timeout)
            except _cf.TimeoutError:
                logger.debug("profiler mem0 signal timed out: %s user=%s", label, uid)
                return None
            except Exception:
                logger.debug("profiler mem0 signal failed: %s user=%s", label, uid, exc_info=True)
                return None

        raw_explicit = _safe_result(fut_explicit, "voice_explicit")
        raw_meetings = _safe_result(fut_meetings, "meeting_search")
        raw_prefs = _safe_result(fut_prefs, "prefs_search")
        raw_interests = _safe_result(fut_interests, "interests_search")

        def _entries(raw) -> list[str]:
            if raw is None:
                return []
            items = raw if isinstance(raw, list) else (raw or {}).get("results") or []
            return [
                (h.get("memory") or h.get("text") or h.get("data") or "").strip()
                for h in items
                if isinstance(h, dict) and (h.get("memory") or h.get("text") or h.get("data"))
            ]

        explicit_facts = _entries(raw_explicit)
        meeting_patterns = _entries(raw_meetings)
        prefs = _entries(raw_prefs)
        interest_hits = _entries(raw_interests)

        if explicit_facts:
            signals["explicit_facts"] = explicit_facts[:40]
        if meeting_patterns:
            signals["meeting_patterns"] = meeting_patterns
        if prefs:
            signals["stated_preferences"] = prefs
        if interest_hits:
            signals["detected_interests"] = interest_hits

        return signals

    except Exception:
        logger.debug("_collect_mem0_signals failed user=%s", user_id, exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# LLM synthesis
# ---------------------------------------------------------------------------

def _synthesize_profile(user_id: str, signals: dict[str, Any]) -> str:
    """Call the profiler LLM to synthesize a structured user profile from collected signals."""
    import openai

    model = os.getenv("MEETINGBOX_PROFILER_MODEL", "gpt-4o-mini")

    # Build signal summary — cap total size to keep prompt cost reasonable.
    try:
        signals_json = json.dumps(signals, default=str, ensure_ascii=False)[:14000]
    except (TypeError, ValueError):
        signals_json = str(signals)[:14000]

    if len(signals_json) < 80:
        return ""

    system_prompt = (
        "You are a personal AI analyst building a user profile from their work activity data. "
        "Analyze the provided data and synthesize a concise, factual profile.\n\n"
        "Output EXACTLY this structure (keep each section to 3-7 items max, be specific not generic):\n\n"
        "INTEREST_DOMAINS: [top professional interests and topics by frequency — e.g. Finance, Product Management]\n"
        "PERSONALITY_TYPE: [2-3 work personality traits — e.g. Strategic thinker, data-driven, detail-oriented]\n"
        "COMMUNICATION_STYLE: [how they communicate — formality, detail preference, tone signals]\n"
        "WORK_PATTERNS: [scheduling preferences, meeting load, peak productivity signals]\n"
        "KEY_CONTACTS: [top people by interaction frequency with relationship context]\n"
        "ACTIVE_FOCUS: [current top 3-5 projects or priorities based on recent activity]\n"
        "BEHAVIORAL_SIGNALS: [how they prefer AI to assist — tone, format, decision support style]\n\n"
        "Rules:\n"
        "- Total output must be under 500 words\n"
        "- Be specific and evidence-based — use actual names, topics, and patterns from the data\n"
        "- Do not hallucinate. If data is insufficient for a section, write 'Insufficient data'\n"
        "- Do not use markdown formatting — plain text only\n"
        "- Respond with ONLY the profile, no preamble or explanation"
    )

    client = openai.OpenAI(timeout=_PROFILER_OPENAI_TIMEOUT_S)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=700,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Build the user profile from this activity data:\n\n{signals_json}"
                ),
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Mem0 store / replace
# ---------------------------------------------------------------------------

def _replace_profile_in_mem0(user_id: str, profile_text: str) -> None:
    """Delete existing user_profile entries, then store the new profile as a single entry."""
    from services.mem0_service import mem0_runtime_ready, _memory, _cb_is_open, _cb_record_ok, _cb_record_error

    if not mem0_runtime_ready() or _cb_is_open():
        return
    m = _memory()
    if not m:
        return

    uid = str(user_id)

    # Delete all existing profile entries so the vector store stays clean.
    try:
        existing = m.get_all(filters={"user_id": uid, "agent_id": _PROFILE_AGENT_ID}, top_k=50)
        entries = existing if isinstance(existing, list) else (existing or {}).get("results") or []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id"):
                # Defensive ownership check: only skip entries we can positively
                # confirm belong to a different user. Entries with no user_id field
                # (Mem0 may omit it) came from the user-scoped get_all and are safe
                # to delete.
                entry_uid = (entry.get("user_id") or "").strip()
                if entry_uid and entry_uid != uid:
                    logger.warning(
                        "user_profiler: skipping delete of foreign mem0 entry id=%s owner=%s expected=%s",
                        entry["id"], entry_uid, uid,
                    )
                    continue
                try:
                    m.delete(entry["id"])
                except Exception:
                    logger.debug("profile delete failed for id=%s user=%s", entry["id"], uid, exc_info=True)
    except Exception:
        logger.debug("profile get_all for deletion failed user=%s", uid, exc_info=True)

    # Store new profile. infer=False: don't extract individual facts — the full blob is the fact.
    try:
        m.add(
            profile_text[:11000],
            user_id=uid,
            agent_id=_PROFILE_AGENT_ID,
            infer=False,
        )
        _cb_record_ok()
        logger.info("user_profiler: profile stored in mem0 user=%s chars=%d", uid, len(profile_text))
    except Exception:
        logger.warning("user_profiler: mem0 add failed user=%s", uid, exc_info=True)
        _cb_record_error()


# ---------------------------------------------------------------------------
# Core profiler
# ---------------------------------------------------------------------------

def _build_user_profile(user_id: str) -> dict[str, Any]:
    """Core profiler job: collect signals → synthesize → store. Blocking; run from thread."""
    import time

    uid = (user_id or "").strip()
    if not uid:
        return {"skipped": True, "reason": "empty_user_id"}

    # If Mem0 is offline/unconfigured the synthesized profile cannot be stored, so skip
    # before spending an LLM call. Don't claim the daily slot — retry on the next trigger
    # once Mem0 is back up.
    try:
        from services.mem0_service import mem0_runtime_ready
        if not mem0_runtime_ready():
            logger.debug("user_profiler: skipping — Mem0 not ready user=%s", uid)
            return {"skipped": True, "reason": "mem0_not_ready"}
    except Exception:
        logger.debug("user_profiler: mem0_runtime_ready check failed user=%s", uid, exc_info=True)
        return {"skipped": True, "reason": "mem0_check_failed"}

    # Claim today's slot (idempotent — only one run per user per calendar day).
    run_id = _claim_profile_run(uid)
    if not run_id:
        logger.debug("user_profiler: already claimed for today user=%s", uid)
        return {"skipped": True, "reason": "already_claimed"}

    t0 = time.monotonic()
    try:
        # Collect signals from SQLite and Mem0 in parallel using threads.
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=2) as pool:
            fut_sqlite = pool.submit(_collect_sqlite_signals, uid)
            fut_mem0 = pool.submit(_collect_mem0_signals, uid)
            sqlite_signals = fut_sqlite.result(timeout=30.0)
            mem0_signals = fut_mem0.result(timeout=30.0)

        signals = {**sqlite_signals, **mem0_signals}
        if not signals:
            _finish_profile_run(run_id, "skipped", "no_signals")
            return {"skipped": True, "reason": "no_signals"}

        # Synthesize via LLM.
        profile_text = _synthesize_profile(uid, signals)
        if not profile_text:
            _finish_profile_run(run_id, "skipped", "empty_synthesis")
            return {"skipped": True, "reason": "empty_synthesis"}

        # Store in Mem0 (replace old profile entries).
        _replace_profile_in_mem0(uid, profile_text)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        detail = f"chars={len(profile_text)} elapsed_ms={elapsed_ms}"
        _finish_profile_run(run_id, "completed", detail)
        logger.info("user_profiler: done user=%s %s", uid, detail)
        return {"done": True, "profile_chars": len(profile_text), "elapsed_ms": elapsed_ms}

    except Exception:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception("user_profiler: failed user=%s elapsed_ms=%d", uid, elapsed_ms)
        _finish_profile_run(run_id, "failed", f"exception elapsed_ms={elapsed_ms}")
        return {"failed": True}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trigger_profile_build_bg(user_id: str | None) -> None:
    """Fire-and-forget: build user profile in a daemon thread (non-blocking).

    Safe to call from any endpoint or background task. Exits immediately.
    The build itself guards against double-runs via the analysis_runs table.
    """
    uid = (user_id or "").strip()
    if not uid:
        return
    if not profiler_enabled():
        return

    def _run():
        try:
            _build_user_profile(uid)
        except Exception:
            logger.debug("trigger_profile_build_bg thread failed user=%s", uid, exc_info=True)

    threading.Thread(target=_run, daemon=True, name=f"profiler-{uid[:8]}").start()


def run_profiler_all_users() -> None:
    """APScheduler entry point: build profiles for all active users.

    Registered in main.py at 03:00 UTC daily.
    Guarded by MEETINGBOX_USER_PROFILER_ENABLED so it is off unless explicitly set.
    """
    if not profiler_enabled():
        logger.debug("user_profiler: disabled (MEETINGBOX_USER_PROFILER_ENABLED is falsey)")
        return

    try:
        from services.analysis_service import _all_active_users
        users = _all_active_users()
    except Exception:
        logger.warning("user_profiler: failed to fetch active users", exc_info=True)
        return

    logger.info("user_profiler: running for %d active users", len(users))
    for i, uid in enumerate(users):
        try:
            trigger_profile_build_bg(uid)
        except Exception:
            logger.debug("user_profiler: failed to trigger for user=%s", uid, exc_info=True)
        # Stagger thread launches by 3 s to avoid hammering OpenAI rate limits
        # when many users are processed in the same cron window.
        if i < len(users) - 1:
            import time as _time
            _time.sleep(3)
