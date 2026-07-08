"""Background analysis service (Fix 9).

Provides idempotent jobs for cross-meeting synthesis and daily digests.
Jobs are de-duplicated via the analysis_runs table using a UNIQUE constraint
on (user_id, job_type, run_date) so multiple uvicorn workers cannot double-run.

Entry points:
    synthesize_recent_meetings(user_id) -- cross-meeting synthesis for one user.
    run_daily_digest(user_id)           -- daily briefing digest for one user.
    run_post_meeting_analysis(user_id, meeting_id) -- post-summarize trigger.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, date
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Idempotency guard
# ---------------------------------------------------------------------------

def _claim_analysis_run(user_id: str, job_type: str, run_date: str) -> str | None:
    """Insert a new analysis_runs row. Returns the run_id on success, None if already claimed.

    The UNIQUE constraint on (user_id, job_type, run_date) ensures only one
    process wins the race in a multi-worker deployment.
    """
    from database import get_connection

    run_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO analysis_runs
              (id, user_id, job_type, run_date, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (run_id, user_id, job_type, run_date, now),
        )
        conn.commit()
        # Check if our row was actually inserted (rowcount==0 means another process won).
        cur = conn.execute(
            "SELECT id FROM analysis_runs WHERE user_id=? AND job_type=? AND run_date=?",
            (user_id, job_type, run_date),
        )
        row = cur.fetchone()
        if row and row[0] == run_id:
            return run_id
        return None
    except Exception:
        logger.debug("analysis_runs claim failed user=%s job=%s", user_id, job_type, exc_info=True)
        return None
    finally:
        conn.close()


def _complete_analysis_run(run_id: str, status: str, result_summary: str | None = None) -> None:
    from database import get_connection

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE analysis_runs
            SET status=?, result_summary=?, completed_at=?
            WHERE id=?
            """,
            (status, result_summary, datetime.utcnow().isoformat(), run_id),
        )
        conn.commit()
    except Exception:
        logger.debug("analysis_runs complete failed run_id=%s", run_id, exc_info=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_recent_meetings(user_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Return the last N days of meeting summaries for user_id."""
    from database import get_connection

    conn = get_connection()

    def _rf(cursor, row):
        return {col[0]: row[i] for i, col in enumerate(cursor.description)}

    conn.row_factory = _rf
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        cur = conn.execute(
            """
            SELECT m.id, m.title, m.start_time, s.summary, s.action_items, s.decisions
            FROM meetings m
            LEFT JOIN summaries s ON s.meeting_id = m.id
            WHERE m.user_id = ?
              AND COALESCE(m.start_time, m.created_at, '') >= ?
              AND (s.summary IS NOT NULL AND TRIM(s.summary) != '')
            ORDER BY COALESCE(m.start_time, m.created_at) DESC
            LIMIT 20
            """,
            (user_id, cutoff),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _all_active_users() -> list[str]:
    """Return user_ids of users who have had at least one meeting in the past 14 days."""
    from database import get_connection

    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=14)).isoformat()
    try:
        cur = conn.execute(
            """
            SELECT DISTINCT user_id FROM meetings
            WHERE user_id IS NOT NULL AND TRIM(user_id) != ''
              AND COALESCE(start_time, created_at, '') >= ?
            """,
            (cutoff,),
        )
        return [row[0] for row in cur.fetchall() if row[0]]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Job: synthesize_recent_meetings
# ---------------------------------------------------------------------------

def synthesize_recent_meetings(user_id: str, *, days: int = 7) -> dict[str, Any]:
    """Cross-meeting synthesis: distil themes, recurring topics, and open threads.

    Idempotent per (user_id, 'synthesize_meetings', today). Safe to call from
    APScheduler with multiple workers.
    """
    uid = (user_id or "").strip()
    if not uid:
        return {"skipped": True, "reason": "empty_user_id"}

    run_date = date.today().isoformat()
    run_id = _claim_analysis_run(uid, "synthesize_meetings", run_date)
    if not run_id:
        logger.debug("synthesize_recent_meetings already claimed user=%s date=%s", uid, run_date)
        return {"skipped": True, "reason": "already_claimed"}

    try:
        meetings = _get_recent_meetings(uid, days=days)
        if not meetings:
            _complete_analysis_run(run_id, "skipped", "no_meetings")
            return {"skipped": True, "reason": "no_meetings_found"}

        import openai

        summaries_block = "\n\n".join(
            f"[Meeting: {m.get('title') or 'Untitled'} | {m.get('start_time', '')[:10]}]\n{(m.get('summary') or '')[:2000]}"
            for m in meetings
        )

        client = openai.OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4.1-nano",
            max_tokens=1200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a personal assistant analysing the user's recent meetings. "
                        "Identify: (1) recurring themes or topics, (2) open action items across meetings, "
                        "(3) key decisions made, (4) patterns in how meetings are going. "
                        "Be concise — max 400 words total. No markdown headers."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Here are my meetings from the past {days} days:\n\n{summaries_block[:10000]}",
                },
            ],
        )
        synthesis = (resp.choices[0].message.content or "").strip()
        if not synthesis:
            _complete_analysis_run(run_id, "skipped", "empty_synthesis")
            return {"skipped": True, "reason": "empty_synthesis"}

        # Persist synthesis into Mem0 so it surfaces in briefing and assistant recall.
        from services.mem0_service import _memory, SOURCE_MEETING_SUMMARY, _cb_is_open, mem0_writes_disabled, mem0_disabled_globally

        if not _cb_is_open() and not mem0_writes_disabled() and not mem0_disabled_globally():
            m = _memory()
            if m:
                try:
                    m.add(
                        f"Weekly meeting synthesis ({run_date}): {synthesis}",
                        user_id=uid,
                        metadata={"source": SOURCE_MEETING_SUMMARY, "kind": "weekly_synthesis", "date": run_date},
                        infer=False,
                    )
                except Exception:
                    logger.warning("synthesize_recent_meetings mem0 ingest failed user=%s", uid, exc_info=True)

        _complete_analysis_run(run_id, "completed", f"meetings={len(meetings)} chars={len(synthesis)}")
        logger.info("synthesize_recent_meetings done user=%s meetings=%d", uid, len(meetings))
        return {"done": True, "meetings_processed": len(meetings), "synthesis_chars": len(synthesis)}

    except Exception:
        logger.exception("synthesize_recent_meetings failed user=%s", uid)
        _complete_analysis_run(run_id, "failed", "exception")
        return {"failed": True}


# ---------------------------------------------------------------------------
# Job: run_daily_digest
# ---------------------------------------------------------------------------

def run_daily_digest(user_id: str) -> dict[str, Any]:
    """Daily synthesis job: calls synthesize_recent_meetings for a single user.

    Designed to be scheduled once per day by APScheduler. Idempotent.
    Only runs when MEETINGBOX_ANALYSIS_ENABLED=1.
    """
    if os.getenv("MEETINGBOX_ANALYSIS_ENABLED", "").strip() not in ("1", "true", "yes", "on"):
        return {"skipped": True, "reason": "analysis_disabled"}
    return synthesize_recent_meetings(user_id, days=7)


def run_daily_digest_all_users() -> None:
    """Run daily digest for every user who has had meetings in the last 14 days.

    Called by the APScheduler job registered in main.py.
    """
    if os.getenv("MEETINGBOX_ANALYSIS_ENABLED", "").strip() not in ("1", "true", "yes", "on"):
        logger.debug("daily digest skipped: MEETINGBOX_ANALYSIS_ENABLED not set")
        return
    users = _all_active_users()
    logger.info("daily digest: running for %d active users", len(users))
    for uid in users:
        try:
            run_daily_digest(uid)
        except Exception:
            logger.warning("daily digest failed for user=%s", uid, exc_info=True)


# ---------------------------------------------------------------------------
# Post-meeting trigger (called from routes/meetings.py after summarize)
# ---------------------------------------------------------------------------

def run_post_meeting_analysis(user_id: str, meeting_id: str) -> None:
    """Fire-and-forget: trigger synthesis after a meeting is summarized.

    Runs in a daemon thread so the summarize endpoint is not blocked.
    Guarded by MEETINGBOX_ANALYSIS_ENABLED so it is off by default.
    """
    if os.getenv("MEETINGBOX_ANALYSIS_ENABLED", "").strip() not in ("1", "true", "yes", "on"):
        return
    uid = (user_id or "").strip()
    if not uid:
        return

    import threading

    def _run():
        try:
            synthesize_recent_meetings(uid, days=3)
        except Exception:
            logger.debug("post_meeting_analysis failed user=%s meeting=%s", uid, meeting_id, exc_info=True)

    threading.Thread(target=_run, daemon=True, name=f"analysis-post-{meeting_id[:8]}").start()
