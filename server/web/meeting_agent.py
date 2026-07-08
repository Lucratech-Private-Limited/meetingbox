"""
Meeting Agent — PRD-aligned post-meeting pipeline (system-triggered only).

Steps: transcribe (OpenAI) → persist segments → emit events → report (Claude) → persist summary → optional action extraction.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import redis

from fastapi import HTTPException

from database import get_connection

logger = logging.getLogger("meetingbox.meeting_agent")
MEETING_AGENT_ID = "meeting_agent"


def _emit(redis_client: redis.Redis, payload: dict) -> None:
  redis_client.publish("events", json.dumps(payload))


def _emit_stage(
  redis_client: redis.Redis,
  meeting_id: str,
  stage: str,
  status: str,
  device_id: Optional[str] = None,
  user_id: str | None = None,
) -> None:
  """Stage hint for logs and optional UI; processing_progress updates status text on device."""
  event: dict = {
    "type": "processing_progress",
    "meeting_id": meeting_id,
    "status": status,
    "progress": 0,
    "eta": 0,
    "stage": stage,
    "agent": MEETING_AGENT_ID,
    "timestamp": datetime.now().isoformat(),
  }
  if device_id:
    event["device_id"] = device_id
  if user_id:
    event["user_id"] = user_id
  _emit(redis_client, event)
  logger.info("meeting_agent stage=%s meeting_id=%s — %s", stage, meeting_id, status)


def _meeting_device_id(session_id: str) -> str | None:
  conn = get_connection()
  try:
    cur = conn.cursor()
    cur.execute("SELECT device_id FROM meetings WHERE id = ?", (session_id,))
    row = cur.fetchone()
    if not row:
      return None
    val = row[0] if not isinstance(row, dict) else row.get("device_id")
    return str(val).strip() or None
  finally:
    conn.close()


def _actor_user_id(actor: Optional[dict]) -> str | None:
  if not isinstance(actor, dict):
    return None
  user = actor.get("user") or {}
  uid = (user.get("id") or "").strip() if isinstance(user, dict) else ""
  return uid or None


def _normalize_recording_mode(raw: str | None) -> str:
  mode = (raw or "").strip().lower()
  if mode in {"note", "notes", "todo", "task", "tasks"}:
    return "note"
  return "meeting"


def _compose_notes_body(notes: list[str], todos: list[dict[str, Any]]) -> str:
  parts: list[str] = []
  clean_notes = [str(n).strip() for n in notes if str(n or "").strip()]
  clean_todos = [t for t in todos if isinstance(t, dict) and str(t.get("title") or "").strip()]
  if clean_notes:
    parts.append("Notes")
    parts.extend(f"- {n.lstrip('- ').strip()}" for n in clean_notes)
  if clean_todos:
    if parts:
      parts.append("")
    parts.append("To-do list")
    for item in clean_todos:
      title = str(item.get("title") or "").strip()
      due = str(item.get("due_date") or "").strip()
      parts.append(f"- {title}" + (f" (due {due})" if due else ""))
  return "\n".join(parts).strip()


async def _create_note_summary(
  meetings_routes,
  session_id: str,
  transcript: str,
  current_actor: Optional[dict],
) -> dict[str, Any]:
  client = meetings_routes._get_anthropic_client()
  if not client:
    raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY is not configured on the server.")

  prompt = (
    "You are turning a spoken personal note into clear notes and todos.\n\n"
    "Rules:\n"
    "- Extract only what the speaker actually asked to remember or do.\n"
    "- Write notes as concise bullet points.\n"
    "- Write todos as concrete task titles, max 8 words each.\n"
    "- Set due_date only when the transcript explicitly states a date, and resolve it to YYYY-MM-DD.\n"
    "- Do not invent dates, people, or tasks.\n"
    "- Return only valid JSON with this shape:\n"
    "{\n"
    "  \"report_title\": \"Notes\",\n"
    "  \"notes\": [\"Short note\"],\n"
    "  \"todos\": [{\"title\": \"Call John\", \"description\": \"\", \"due_date\": null}]\n"
    "}\n\n"
    f"Transcript:\n\n{transcript}"
  )
  try:
    resp = client.messages.create(
      model=os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929"),
      max_tokens=int(os.getenv("AI_NOTES_MAX_TOKENS", os.getenv("AI_MAX_TOKENS", "4096"))),
      messages=[{"role": "user", "content": prompt}],
    )
    text = meetings_routes._anthropic_message_text(resp)
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Claude API error: {exc}") from exc
  if not text:
    raise HTTPException(status_code=500, detail="Claude returned an empty notes response.")
  if "```json" in text:
    start = text.find("```json") + len("```json")
    end = text.find("```", start)
    text = text[start:end].strip()
  try:
    data = json.loads(text)
  except json.JSONDecodeError as exc:
    raise HTTPException(status_code=500, detail="Failed to parse notes JSON from Claude response.") from exc
  notes = data.get("notes") if isinstance(data, dict) else []
  todos = data.get("todos") if isinstance(data, dict) else []
  if not isinstance(notes, list):
    notes = []
  if not isinstance(todos, list):
    todos = []
  clean_todos: list[dict[str, Any]] = []
  for item in todos:
    if not isinstance(item, dict):
      continue
    title = str(item.get("title") or "").strip()
    if not title:
      continue
    clean_todos.append({
      "title": title,
      "task": title,
      "description": str(item.get("description") or "").strip(),
      "due_date": str(item.get("due_date") or "").strip() or None,
      "type": "todo",
    })
  body = _compose_notes_body([str(n) for n in notes], clean_todos)
  if not body:
    body = "No clear notes or todos were detected."

  generated_at = datetime.now().isoformat()
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    conn.execute(
      """
      INSERT OR REPLACE INTO summaries
        (meeting_id, summary, action_items, decisions, topics, sentiment, generated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
      """,
      (session_id, body, json.dumps(clean_todos), "[]", "[]", "", generated_at),
    )
    conn.execute(
      "UPDATE meetings SET status = ?, end_time = ?, title = ? WHERE id = ?",
      ("completed", generated_at, "Notes", session_id),
    )
    conn.commit()
  finally:
    conn.close()

  created_tasks: list[dict[str, Any]] = []
  skipped_tasks: list[dict[str, Any]] = []
  user_id = _actor_user_id(current_actor)
  if user_id:
    from services.tasks_service import SimilarTaskExistsError, TaskFidelityError, voice_create_task

    for item in clean_todos:
      try:
        row = voice_create_task(
          user_id=user_id,
          title=item["title"],
          due_date=item.get("due_date"),
          description=item.get("description") or None,
          confirm_duplicate=True,
          source="note",
          tags=["note"],
          meeting_id=session_id,
        )
        created_tasks.append({
          "id": row.get("id"),
          "title": row.get("title"),
          "due_at": row.get("due_at"),
          "status": row.get("status"),
        })
      except SimilarTaskExistsError as exc:
        skipped_tasks.append({"title": item["title"], "reason": "similar_task_exists", "similar": exc.similar})
      except TaskFidelityError as exc:
        skipped_tasks.append({"title": item["title"], "reason": "task_fidelity", "detail": str(exc)})
      except Exception as exc:
        logger.warning("note task create failed meeting_id=%s title=%r: %s", session_id, item["title"], exc)
        skipped_tasks.append({"title": item["title"], "reason": "create_failed", "detail": str(exc)})

    # Bridge the device note recording into the user_notes store so it shows in
    # the web Notes page and is findable by the realtime voice agent (note_list).
    # Without this, device-recorded notes live only in the meetings/summaries
    # tables and the voice agent / web never see them — the device/web disconnect.
    try:
      from services.notes_service import upsert_note
      from services.mem0_service import maybe_ingest_note

      note_lines = [str(n).strip() for n in notes if str(n or "").strip()]
      note_title = (note_lines[0][:60] if note_lines else "Voice note")
      note_row = upsert_note(user_id, {
        "title": note_title,
        "content": body,
        "tags": ["voice", "device"],
        "source": "device",
      })
      try:
        maybe_ingest_note(user_id, note_row)
      except Exception:
        logger.debug("device note mem0 ingest failed meeting_id=%s", session_id, exc_info=True)
      logger.info("device note bridged to user_notes meeting_id=%s note_id=%s user=%s",
                  session_id, note_row.get("id"), user_id)
    except Exception:
      logger.warning("device note -> user_notes bridge failed meeting_id=%s", session_id, exc_info=True)

  return {
    "status": "generated",
    "recording_mode": "note",
    "content_type": "notes",
    "title": "Notes",
    "report_title": "Notes",
    "summary": body,
    "notes": [str(n).strip() for n in notes if str(n or "").strip()],
    "action_items": clean_todos,
    "created_tasks": created_tasks,
    "skipped_tasks": skipped_tasks,
    "decisions": [],
    "topics": [],
    "sentiment": "",
    "generated_at": generated_at,
  }


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
    if transcript.strip():
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


def _mark_transcription_failed(session_id: str, duration_seconds: int, detail: str) -> None:
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    conn.execute(
      "UPDATE meetings SET status = ?, duration = ?, end_time = ? WHERE id = ?",
      ("transcription_failed", duration_seconds, datetime.now().isoformat(), session_id),
    )
    conn.commit()
  finally:
    conn.close()
  logger.warning("Transcription failed meeting_id=%s: %s", session_id, detail)


async def run_meeting_agent_pipeline(
  redis_client: redis.Redis,
  session_id: str,
  dest_wav: Path,
  duration_seconds: int,
  current_actor: Optional[dict],
  recording_mode: str = "meeting",
) -> dict[str, Any]:
  """
  Full post-meeting run after WAV is saved. Lazy-imports routes.meetings to avoid circular imports at startup.

  Returns the same dict as summarize_meeting on success (for HTTP + summary_complete WebSocket payload).
  """
  from routes import meetings as meetings_routes

  # Extract actor context once for consistent event scoping throughout the pipeline.
  actor_type = current_actor.get("type") if isinstance(current_actor, dict) else None
  actor_device_id: Optional[str] = None
  actor_user_id: str | None = None
  if actor_type == "device":
    actor_device_id = (current_actor.get("device") or {}).get("id")
    actor_user_id = (current_actor.get("user") or {}).get("id")
  elif actor_type == "user":
    actor_user_id = (current_actor.get("user") or {}).get("id")
  # Normalize: fall back to meeting row if actor has no device (e.g. web upload).
  event_device_id: Optional[str] = actor_device_id or _meeting_device_id(session_id)
  actor_user_id = str(actor_user_id or "").strip() or None
  mode = _normalize_recording_mode(recording_mode)

  _emit_stage(redis_client, session_id, "transcribing", "Transcribing audio…", device_id=event_device_id, user_id=actor_user_id)
  try:
    transcript = meetings_routes._transcribe_audio_with_openai(dest_wav)
  except HTTPException as exc:
    detail = str(exc.detail if hasattr(exc, "detail") else exc)
    _mark_transcription_failed(session_id, duration_seconds, detail)
    _emit(redis_client, {
      "type": "error",
      "error_type": "Transcription Failed",
      "message": detail,
      "meeting_id": session_id,
      "recording_mode": mode,
      "agent": MEETING_AGENT_ID,
      "timestamp": datetime.now().isoformat(),
      **({"device_id": event_device_id} if event_device_id else {}),
      **({"user_id": actor_user_id} if actor_user_id else {}),
    })
    raise
  _persist_transcript_and_status(session_id, duration_seconds, transcript)

  transcription_event: dict = {
    "type": "transcription_complete",
    "meeting_id": session_id,
    "last_segment_num": 0,
    "source": "openai_whisper",
    "agent": MEETING_AGENT_ID,
    "timestamp": datetime.now().isoformat(),
    "recording_mode": mode,
  }
  if event_device_id:
    transcription_event["device_id"] = event_device_id
  if actor_user_id:
    transcription_event["user_id"] = actor_user_id
  _emit(redis_client, transcription_event)

  summary_result: dict[str, Any] = {}
  if transcript.strip():
    # Drive the three device-side stage rows (Extracting key points →
    # Identifying action items → Structuring summary). They're shown in
    # the processing screen's stage card and animate as each `stage`
    # event arrives. All three substages happen inside the single
    # ``summarize_meeting`` Claude call below, so we emit them just
    # before/after that call to give the user clear visual progress.
    _emit_stage(redis_client, session_id, "extracting_key_points", "Extracting key points…", device_id=event_device_id, user_id=actor_user_id)
    try:
      if mode == "note":
        summary_result = await _create_note_summary(
          meetings_routes,
          session_id,
          transcript,
          current_actor,
        )
      else:
        summary_result = await meetings_routes.summarize_meeting(
          session_id,
          current_actor=current_actor,
        )
      _emit_stage(redis_client, session_id, "identifying_action_items", "Identifying action items…", device_id=event_device_id, user_id=actor_user_id)
      final_stage = "Structuring notes…" if mode == "note" else "Structuring summary…"
      _emit_stage(redis_client, session_id, "structuring_summary", final_stage, device_id=event_device_id, user_id=actor_user_id)
    except HTTPException as exc:
      logger.warning(
        "summarize_meeting HTTP error meeting_id=%s: %s",
        session_id,
        getattr(exc, "detail", exc),
      )
      d = exc.detail
      if isinstance(d, str):
        detail = d
      elif isinstance(d, list):
        detail = "; ".join(str(x) for x in d)
      else:
        detail = str(d)
      summary_result = {
        "status": "failed",
        "error": detail,
        "meeting_id": session_id,
        "recording_mode": mode,
      }
    except Exception as exc:
      logger.exception("summarize_meeting failed meeting_id=%s", session_id)
      summary_result = {
        "status": "failed",
        "error": str(exc).strip() or "Summary generation failed",
        "meeting_id": session_id,
        "recording_mode": mode,
      }
  else:
    logger.info("No clear speech detected for meeting_id=%s; skipping summary generation", session_id)
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON")
    try:
      conn.execute(
        "UPDATE meetings SET status = ?, end_time = ? WHERE id = ?",
        ("completed", datetime.now().isoformat(), session_id),
      )
      conn.commit()
    finally:
      conn.close()
    summary_result = {
      "status": "no_speech",
      "summary": "",
      "action_items": [],
      "decisions": [],
      "topics": [],
      "open_questions": [],
      "risks_or_concerns": [],
      "meeting_id": session_id,
      "recording_mode": mode,
      "content_type": "notes" if mode == "note" else "summary",
    }

  if summary_result.get("status") == "failed":
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = ON")
    try:
      conn.execute(
        "UPDATE meetings SET status = ?, end_time = ? WHERE id = ?",
        ("completed", datetime.now().isoformat(), session_id),
      )
      conn.commit()
    finally:
      conn.close()

  # Build the searchable index (entities + keywords + FTS + embedding) so this
  # recording is retrievable by context/participants/topics — not just recency.
  # Best-effort: indexing failures must never break the pipeline.
  if summary_result.get("status") not in {"failed"}:
    try:
      from services.recording_store import index_recording

      index_recording(session_id, extract=True)
    except Exception:
      logger.warning("recording index build failed meeting_id=%s", session_id, exc_info=True)

  summary_event: dict = {
    "type": "summary_complete",
    "meeting_id": session_id,
    "summary": summary_result,
    "agent": MEETING_AGENT_ID,
    "timestamp": datetime.now().isoformat(),
  }
  summary_event["recording_mode"] = mode
  if event_device_id:
    summary_event["device_id"] = event_device_id
  if actor_user_id:
    summary_event["user_id"] = actor_user_id
  _emit(redis_client, summary_event)

  try:
    from assistant_service import log_pipeline_completion_audit

    log_pipeline_completion_audit(
      redis_client,
      session_id,
      actor_user_id,
      summary_result,
    )
  except Exception:
    logger.exception("log_pipeline_completion_audit failed meeting_id=%s", session_id)

  completed_status = "Notes processing complete." if mode == "note" else "Meeting processing complete."
  _emit_stage(redis_client, session_id, "completed", completed_status, device_id=event_device_id, user_id=actor_user_id)
  logger.info("meeting_agent finished meeting_id=%s", session_id)
  return summary_result
