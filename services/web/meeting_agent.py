"""
Meeting Agent — PRD-aligned post-meeting pipeline (system-triggered only).

Steps: transcribe (OpenAI) → persist segments → emit events → report (Claude) → persist summary → optional action extraction.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import redis

from database import get_connection

logger = logging.getLogger("meetingbox.meeting_agent")

MEETING_AGENT_ID = "meeting_agent"


def _emit(redis_client: redis.Redis, payload: dict) -> None:
  redis_client.publish("events", json.dumps(payload))


def _emit_stage(redis_client: redis.Redis, meeting_id: str, stage: str, status: str) -> None:
  """Stage hint for logs and optional UI; processing_progress updates status text on device."""
  _emit(
    redis_client,
    {
      "type": "processing_progress",
      "meeting_id": meeting_id,
      "status": status,
      "progress": 0,
      "eta": 0,
      "stage": stage,
      "agent": MEETING_AGENT_ID,
      "timestamp": datetime.now().isoformat(),
    },
  )
  logger.info("meeting_agent stage=%s meeting_id=%s — %s", stage, meeting_id, status)


def _persist_transcript_and_status(
  session_id: str,
  duration_seconds: int,
  transcript: str,
) -> None:
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    cur.execute("DELETE FROM segments WHERE meeting_id = ?", (session_id,))
    cur.execute(
      """
      INSERT INTO segments
        (meeting_id, segment_num, start_time, end_time, text, speaker_id, confidence)
      VALUES (?, ?, ?, ?, ?, ?, ?)
      """,
      (session_id, 0, 0.0, float(duration_seconds), transcript, None, 1.0),
    )
    cur.execute(
      "UPDATE meetings SET status = ?, duration = ? WHERE id = ?",
      ("transcribed", duration_seconds, session_id),
    )
    conn.commit()
  finally:
    conn.close()


async def run_meeting_agent_pipeline(
  redis_client: redis.Redis,
  session_id: str,
  dest_wav: Path,
  duration_seconds: int,
  current_actor: Optional[dict],
) -> dict[str, Any]:
  """
  Full post-meeting run after WAV is saved. Lazy-imports routes.meetings to avoid circular imports at startup.

  Returns the same dict as summarize_meeting on success (for HTTP + summary_complete WebSocket payload).
  """
  from routes import meetings as meetings_routes

  _emit_stage(redis_client, session_id, "transcribing", "Transcribing audio…")
  transcript = meetings_routes._transcribe_audio_with_openai(dest_wav)
  _persist_transcript_and_status(session_id, duration_seconds, transcript)

  _emit(
    redis_client,
    {
      "type": "transcription_complete",
      "meeting_id": session_id,
      "last_segment_num": 0,
      "source": "openai_whisper",
      "agent": MEETING_AGENT_ID,
      "timestamp": datetime.now().isoformat(),
    },
  )

  _emit_stage(redis_client, session_id, "reporting", "Building meeting report…")
  summary_result = await meetings_routes.summarize_meeting(session_id, current_actor)

  _emit(
    redis_client,
    {
      "type": "summary_complete",
      "meeting_id": session_id,
      "summary": summary_result,
      "agent": MEETING_AGENT_ID,
      "timestamp": datetime.now().isoformat(),
    },
  )

  try:
    from assistant_service import log_pipeline_completion_audit

    log_pipeline_completion_audit(
      redis_client,
      session_id,
      current_actor["user"]["id"] if current_actor else None,
      summary_result,
    )
  except Exception:
    logger.exception("log_pipeline_completion_audit failed meeting_id=%s", session_id)

  _emit_stage(redis_client, session_id, "completed", "Meeting processing complete.")
  logger.info("meeting_agent finished meeting_id=%s", session_id)
  return summary_result
