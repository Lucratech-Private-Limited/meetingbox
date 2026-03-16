import json
import logging
import os
import random
import string
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
import redis
import shutil
import httpx

from auth import get_current_user, get_optional_user
from database import get_connection
from services.action_engine import generate_actions_for_meeting

logger = logging.getLogger(__name__)

# Lazy-loaded Anthropic client for on-demand summarization
_anthropic_client = None

def _get_anthropic_client():
  global _anthropic_client
  if _anthropic_client is None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
      return None
    from anthropic import Anthropic
    _anthropic_client = Anthropic(api_key=api_key)
  return _anthropic_client

# Ollama configuration for local summarization
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "phi3:mini")

router = APIRouter()
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
_redis_client = None

def _get_redis() -> redis.Redis:
  """Lazy Redis connection — created on first use, not at import time."""
  global _redis_client
  if _redis_client is None:
    _redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
  return _redis_client

RECORDINGS_DIR = Path("/data/audio/recordings")
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


def _generate_session_id() -> str:
    """Generate a unique session ID with timestamp + random suffix."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{suffix}"


def _derive_title(summary: str, topics: list) -> str:
    """Derive a short human-readable meeting title from the summary or topics."""
    if topics:
      clean = [t.strip().lstrip("#") for t in topics[:3] if isinstance(t, str) and t.strip()]
      if clean:
        title = " / ".join(clean)
        return title[:80]
    if summary:
      first = summary.split(".")[0].strip()
      if len(first) > 80:
        first = first[:77] + "..."
      if first:
        return first
    return ""

# Accepted upload extensions; non-WAV are converted with ffmpeg to 16kHz mono WAV
UPLOAD_AUDIO_EXTENSIONS = {".wav", ".webm", ".ogg", ".mp4", ".m4a"}


class MeetingResponse(BaseModel):
  id: str
  title: str
  start_time: str
  end_time: Optional[str]
  duration: Optional[int]
  status: str
  audio_path: Optional[str]
  created_at: str


class TranscriptSegment(BaseModel):
  segment_num: int
  start_time: float
  end_time: float
  text: str
  speaker_id: Optional[str] = None


class MeetingSummary(BaseModel):
  summary: str
  action_items: list[dict]
  decisions: list
  topics: list
  sentiment: str


class LocalSummary(BaseModel):
  summary: str
  action_items: list[dict]
  decisions: list
  topics: list
  sentiment: str
  model_name: str


def _normalize_summary_data(data: dict) -> dict:
  """Normalize LLM output so decisions/topics are always lists of strings.
  LLMs sometimes return decisions as objects like {"decision": "...", "responsible_party": null}
  instead of plain strings. This coerces everything to strings."""
  # Normalize decisions
  raw_decisions = data.get("decisions", [])
  decisions = []
  for d in raw_decisions:
    if isinstance(d, str):
      decisions.append(d)
    elif isinstance(d, dict):
      # Extract the text from common LLM object shapes
      decisions.append(d.get("decision") or d.get("text") or d.get("description") or str(d))
    else:
      decisions.append(str(d))
  data["decisions"] = decisions

  # Normalize topics
  raw_topics = data.get("topics", [])
  topics = []
  for t in raw_topics:
    if isinstance(t, str):
      topics.append(t)
    elif isinstance(t, dict):
      topics.append(t.get("topic") or t.get("name") or t.get("text") or str(t))
    else:
      topics.append(str(t))
  data["topics"] = topics

  # Normalize action_items (ensure they're dicts)
  raw_actions = data.get("action_items", [])
  actions = []
  for a in raw_actions:
    if isinstance(a, dict):
      actions.append(a)
    elif isinstance(a, str):
      actions.append({"task": a, "assignee": None, "due_date": None})
    else:
      actions.append({"task": str(a), "assignee": None, "due_date": None})
  data["action_items"] = actions

  # Ensure sentiment is a string
  sentiment = data.get("sentiment", "")
  if not isinstance(sentiment, str):
    data["sentiment"] = str(sentiment)

  return data


class MeetingDetail(BaseModel):
  meeting: MeetingResponse
  segments: List[TranscriptSegment]
  summary: Optional[MeetingSummary]
  local_summary: Optional[LocalSummary]


# --- Start / Stop meeting (wire to Redis for audio service) ---
# Recording control uses get_optional_user so the device-ui (no login) can start/stop/pause/resume.

@router.post("/start")
async def start_meeting(current_user: Optional[dict] = Depends(get_optional_user)):
  """Start a new recording. Sends command to audio service via Redis."""
  session_id = _generate_session_id()
  _get_redis().publish("commands", json.dumps({"action": "start_recording", "session_id": session_id}))
  _get_redis().set("current_meeting_id", session_id)
  _get_redis().set("recording_state", "recording")
  return {"session_id": session_id, "status": "recording_started"}


@router.post("/stop")
async def stop_meeting(current_user: Optional[dict] = Depends(get_optional_user)):
  """Stop the current recording. Sends command to audio service via Redis."""
  session_id = _get_redis().get("current_meeting_id")
  _get_redis().publish(
    "commands",
    json.dumps({"action": "stop_recording", "session_id": session_id}),
  )
  _get_redis().set("recording_state", "processing")
  if session_id:
    _get_redis().delete("current_meeting_id")
  return {"session_id": session_id, "status": "recording_stopped"}


@router.get("/recording-status")
async def recording_status(current_user: Optional[dict] = Depends(get_optional_user)):
  """Current recording state for the dashboard."""
  state = _get_redis().get("recording_state") or "idle"
  current_id = _get_redis().get("current_meeting_id")
  return {"state": state, "session_id": current_id}


@router.post("/reset-recording-state")
async def reset_recording_state(current_user: Optional[dict] = Depends(get_optional_user)):
  """Clear recording state so the dashboard shows Start/Record buttons again (e.g. if stuck on Processing)."""
  _get_redis().set("recording_state", "idle")
  _get_redis().delete("current_meeting_id")
  return {"status": "idle"}


@router.post("/pause")
async def pause_meeting(current_user: Optional[dict] = Depends(get_optional_user)):
  """Pause the current recording (stub -- audio service does not yet support pause)."""
  state = _get_redis().get("recording_state") or "idle"
  if state != "recording":
    raise HTTPException(status_code=400, detail="No active recording to pause")
  _get_redis().set("recording_state", "paused")
  return {"status": "paused"}


@router.post("/resume")
async def resume_meeting(current_user: Optional[dict] = Depends(get_optional_user)):
  """Resume a paused recording (stub -- audio service does not yet support resume)."""
  state = _get_redis().get("recording_state") or "idle"
  if state != "paused":
    raise HTTPException(status_code=400, detail="No paused recording to resume")
  _get_redis().set("recording_state", "recording")
  return {"status": "recording"}


class MeetingUpdateRequest(BaseModel):
  title: Optional[str] = None
  status: Optional[str] = None


@router.patch("/{meeting_id}")
async def update_meeting(meeting_id: str, body: MeetingUpdateRequest, current_user: Optional[dict] = Depends(get_optional_user)):
  """Update editable fields of a meeting (title, status)."""
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()
    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    updates = []
    params: list[object] = []
    if body.title is not None:
      updates.append("title = ?")
      params.append(body.title)
    if body.status is not None:
      updates.append("status = ?")
      params.append(body.status)

    if not updates:
      return meeting

    params.append(meeting_id)
    cur.execute(f"UPDATE meetings SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()

    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    return cur.fetchone()
  finally:
    conn.close()


@router.delete("/{meeting_id}")
async def delete_meeting(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
  """Delete a meeting and all its associated data."""
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    cur.execute("SELECT id, audio_path FROM meetings WHERE id = ?", (meeting_id,))
    row = cur.fetchone()
    if not row:
      raise HTTPException(status_code=404, detail="Meeting not found")

    audio_path = row[1]
    cur.execute("DELETE FROM actions WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM summaries WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    conn.commit()

    if audio_path:
      p = Path(audio_path)
      if p.exists():
        p.unlink(missing_ok=True)
  finally:
    conn.close()

  return {"status": "deleted", "meeting_id": meeting_id}


# --- Test WAV ingest (bypass mic: feed a WAV file into transcription → AI pipeline) ---

@router.post("/test/ingest-wav")
async def ingest_test_wav(file: UploadFile = File(...), current_user: dict | None = Depends(get_optional_user)):
  """
  Upload a WAV file to run through the full pipeline (transcription → summary).
  Use this to test without a microphone. Session ID is derived from filename or timestamp.
  """
  if not file.filename or not file.filename.lower().endswith(".wav"):
    raise HTTPException(status_code=400, detail="Upload must be a .wav file")
  session_id = _generate_session_id()
  dest = RECORDINGS_DIR / f"{session_id}.wav"
  try:
    with dest.open("wb") as f:
      shutil.copyfileobj(file.file, f)
  except Exception as e:
    raise HTTPException(status_code=500, detail=str(e))

  # Emit the same event the audio service would: transcription service will pick it up
  _get_redis().publish(
    "events",
    json.dumps({
      "type": "recording_stopped",
      "session_id": session_id,
      "path": str(dest),
      "timestamp": datetime.now().isoformat(),
    }),
  )
  return {"session_id": session_id, "path": str(dest), "status": "ingested"}


def _ensure_16k_mono_wav(source: Path, dest: Path) -> None:
  """Convert source audio to 16kHz mono WAV at dest using ffmpeg."""
  subprocess.run(
    [
      "ffmpeg",
      "-y",
      "-i",
      str(source),
      "-acodec",
      "pcm_s16le",
      "-ar",
      "16000",
      "-ac",
      "1",
      str(dest),
    ],
    check=True,
    capture_output=True,
    timeout=300,
  )


@router.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
  """
  Upload audio from your computer (e.g. browser recording). Accepts WAV, WebM, OGG, MP4.
  Converts to 16kHz mono WAV and runs the same pipeline (transcription → summary).
  Use this to record with your PC mic: record in the browser, then upload.
  """
  fn = (file.filename or "").lower()
  ext = Path(fn).suffix or ".webm"
  if ext not in UPLOAD_AUDIO_EXTENSIONS:
    ext = ".webm"
  session_id = _generate_session_id()
  dest_wav = RECORDINGS_DIR / f"{session_id}.wav"

  with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
    tmp_path = Path(tmp.name)
  try:
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
      raise HTTPException(status_code=413, detail=f"File too large. Maximum upload size is {MAX_UPLOAD_SIZE // (1024 * 1024)} MB.")
    tmp_path.write_bytes(content)
    _ensure_16k_mono_wav(tmp_path, dest_wav)
  except subprocess.CalledProcessError as e:
    raise HTTPException(
      status_code=400,
      detail=f"Audio conversion failed (unsupported format?): {e.stderr.decode() if e.stderr else str(e)}",
    )
  finally:
    tmp_path.unlink(missing_ok=True)

  _get_redis().set("recording_state", "processing")
  _get_redis().set("current_meeting_id", session_id)
  _get_redis().publish(
    "events",
    json.dumps({
      "type": "recording_stopped",
      "session_id": session_id,
      "path": str(dest_wav),
      "timestamp": datetime.now().isoformat(),
    }),
  )
  return {"session_id": session_id, "path": str(dest_wav), "status": "ingested"}


@router.get("/")
async def list_meetings(limit: int = 50, offset: int = 0, status: Optional[str] = None, current_user: Optional[dict] = Depends(get_optional_user)):
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}  # type: ignore
  try:
    cur = conn.cursor()
    query = """
      SELECT m.*,
             (SELECT COUNT(*) FROM actions a WHERE a.meeting_id = m.id AND a.status = 'pending') AS pending_actions
      FROM meetings m
    """
    params: list[object] = []
    if status:
      query += " WHERE m.status = ?"
      params.append(status)
    query += " ORDER BY m.created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cur.execute(query, params)
    rows = cur.fetchall()
  finally:
    conn.close()

  for row in rows:
    if row.get("duration") is None and row.get("start_time") and row.get("end_time"):
      try:
        start_dt = datetime.fromisoformat(row["start_time"])
        end_dt = datetime.fromisoformat(row["end_time"])
        row["duration"] = int((end_dt - start_dt).total_seconds())
      except Exception:
        pass

  return rows


@router.post("/{meeting_id}/summarize")
async def summarize_meeting(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
  """Generate an AI summary for a transcribed meeting using Claude."""
  client = _get_anthropic_client()
  if not client:
    raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY is not configured on the server.")

  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()

    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    existing = cur.fetchone()
    if existing:
      result = _normalize_summary_data({
        "summary": existing["summary"],
        "action_items": json.loads(existing["action_items"] or "[]"),
        "decisions": json.loads(existing["decisions"] or "[]"),
        "topics": json.loads(existing["topics"] or "[]"),
        "sentiment": existing["sentiment"],
      })
      result["status"] = "already_exists"
      return result

    cur.execute(
      "SELECT segment_num, start_time, text FROM segments WHERE meeting_id = ? ORDER BY segment_num",
      (meeting_id,),
    )
    rows = cur.fetchall()
  finally:
    conn.close()

  if not rows:
    raise HTTPException(status_code=400, detail="No transcript segments found for this meeting.")

  # Build transcript text
  parts = []
  for r in rows:
    mins = int((r["start_time"] or 0) // 60)
    secs = int((r["start_time"] or 0) % 60)
    parts.append(f"[{mins:02d}:{secs:02d}] Segment {r['segment_num']}: {r['text']}")
  transcript = "\n\n".join(parts)

  prompt = (
    "You are analyzing a meeting transcript. Please provide:\n\n"
    "1. Summary (2-3 sentences)\n"
    "2. Key discussion points (3-5 bullets)\n"
    "3. Decisions made\n"
    "4. Action items with assignees if available. Each action item MUST include a "
    '"type" field with one of these values:\n'
    '   - "email_draft" — for follow-ups that are naturally an email\n'
    '   - "calendar_invite" — for next meetings or time-blocking commitments\n'
    '   - "task" — for general human to-do items that don\'t involve email or calendar\n'
    "   Only include action items that are explicitly grounded in the meeting.\n"
    "5. 3-5 topic hashtags\n"
    "6. Overall sentiment (single word or short phrase)\n\n"
    "Return **only** valid JSON in this shape:\n"
    '{\n'
    '  "summary": "...",\n'
    '  "discussion_points": ["...", "..."],\n'
    '  "decisions": ["...", "..."],\n'
    '  "action_items": [{"task": "...", "assignee": "...", "due_date": "...", "type": "email_draft | calendar_invite | task"}],\n'
    '  "topics": ["#topic1", "#topic2"],\n'
    '  "sentiment": "Productive"\n'
    "}\n\n"
    f"Transcript:\n\n{transcript}"
  )

  model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
  max_tokens = int(os.getenv("AI_MAX_TOKENS", "2000"))

  try:
    resp = client.messages.create(
      model=model,
      max_tokens=max_tokens,
      messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Claude API error: {exc}")

  # Parse JSON from response
  if "```json" in text:
    start = text.find("```json") + len("```json")
    end = text.find("```", start)
    json_str = text[start:end].strip()
  else:
    json_str = text.strip()

  try:
    data = json.loads(json_str)
  except json.JSONDecodeError:
    raise HTTPException(status_code=500, detail="Failed to parse JSON from Claude response.")

  # Normalize LLM output (decisions/topics may be objects instead of strings)
  data = _normalize_summary_data(data)

  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    cur.execute(
      """
      INSERT OR REPLACE INTO summaries
        (meeting_id, summary, action_items, decisions, topics, sentiment, generated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
      """,
      (
        meeting_id,
        data.get("summary", ""),
        json.dumps(data.get("action_items", [])),
        json.dumps(data.get("decisions", [])),
        json.dumps(data.get("topics", [])),
        data.get("sentiment", ""),
        datetime.now().isoformat(),
      ),
    )
    auto_title = _derive_title(data.get("summary", ""), data.get("topics", []))
    if auto_title and (meeting.get("title", "").startswith("Meeting ") or not meeting.get("title")):
      cur.execute("UPDATE meetings SET status = 'completed', end_time = ?, title = ? WHERE id = ?", (datetime.now().isoformat(), auto_title, meeting_id))
    else:
      cur.execute("UPDATE meetings SET status = 'completed', end_time = ? WHERE id = ?", (datetime.now().isoformat(), meeting_id))
    conn.commit()
  finally:
    conn.close()

  if current_user:
    try:
      generate_actions_for_meeting(meeting_id, current_user["id"])
    except HTTPException:
      raise
    except Exception:
      logger.exception("Agentic action generation failed for %s", meeting_id)

  return {
    "status": "generated",
    "summary": data.get("summary", ""),
    "action_items": data.get("action_items", []),
    "decisions": data.get("decisions", []),
    "topics": data.get("topics", []),
    "sentiment": data.get("sentiment", ""),
  }


@router.post("/{meeting_id}/summarize-local")
async def summarize_meeting_local(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
  """Queue a backend-owned local summary generation request."""
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()

    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    existing = cur.fetchone()
    if existing:
      result = _normalize_summary_data({
        "summary": existing["summary"],
        "discussion_points": json.loads(existing.get("discussion_points") or "[]"),
        "action_items": json.loads(existing["action_items"] or "[]"),
        "decisions": json.loads(existing["decisions"] or "[]"),
        "topics": json.loads(existing["topics"] or "[]"),
        "sentiment": existing["sentiment"],
      })
      result["status"] = "already_exists" if int(existing.get("is_final", 0) or 0) else "in_progress"
      result["model_name"] = existing.get("model_name", LOCAL_LLM_MODEL)
      return result

    cur.execute(
      """
      INSERT OR IGNORE INTO processing_state
        (meeting_id, updated_at)
      VALUES (?, ?)
      """,
      (meeting_id, datetime.now().isoformat()),
    )
    cur.execute("UPDATE meetings SET status = 'summarizing' WHERE id = ?", (meeting_id,))
    conn.commit()
  finally:
    conn.close()

  _get_redis().publish(
    "events",
    json.dumps(
      {
        "type": "summary_requested",
        "meeting_id": meeting_id,
        "timestamp": datetime.now().isoformat(),
      }
    ),
  )
  if current_user:
    try:
      generate_actions_for_meeting(meeting_id, current_user["id"])
    except HTTPException:
      pass
    except Exception:
      logger.exception("Agentic action generation request failed for %s", meeting_id)
  return {"status": "queued", "meeting_id": meeting_id}


class EmailRequest(BaseModel):
  recipients: List[str]


@router.post("/{meeting_id}/email")
async def email_summary(meeting_id: str, body: EmailRequest, current_user: dict = Depends(get_current_user)):
  """Email the meeting summary to a list of recipients via Gmail."""
  from routes.integrations import get_credentials_for_provider

  if not body.recipients:
    raise HTTPException(status_code=400, detail="At least one recipient email is required.")

  user_id = current_user["id"]
  creds = get_credentials_for_provider(user_id, "gmail")
  if not creds:
    raise HTTPException(status_code=400, detail="Gmail is not connected. Connect it in Settings first.")

  conn = get_connection()
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()
    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    summary_row = cur.fetchone()

    cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    local_summary_row = cur.fetchone()
  finally:
    conn.close()

  chosen = summary_row or local_summary_row
  if not chosen:
    raise HTTPException(status_code=400, detail="No summary available. Summarize the meeting first.")

  title = meeting.get("title", "Untitled Meeting")
  start = meeting.get("start_time", "")
  summary_text = chosen.get("summary", "")

  decisions = []
  try:
    decisions = json.loads(chosen.get("decisions") or "[]")
  except (json.JSONDecodeError, TypeError):
    pass

  topics = []
  try:
    topics = json.loads(chosen.get("topics") or "[]")
  except (json.JSONDecodeError, TypeError):
    pass

  action_items = []
  try:
    action_items = json.loads(chosen.get("action_items") or "[]")
  except (json.JSONDecodeError, TypeError):
    pass

  body_parts = [f"Meeting: {title}", f"Date: {start}", "", summary_text]
  if decisions:
    body_parts.append("\nDecisions:")
    for d in decisions:
      body_parts.append(f"  - {d}")
  if action_items:
    body_parts.append("\nAction Items:")
    for a in action_items:
      if isinstance(a, dict):
        line = a.get("task", str(a))
        if a.get("assignee"):
          line += f" (assigned to {a['assignee']})"
        body_parts.append(f"  - {line}")
      else:
        body_parts.append(f"  - {a}")
  if topics:
    body_parts.append(f"\nTopics: {', '.join(topics)}")

  email_body = "\n".join(body_parts)

  sent_to = []
  errors = []
  for recipient in body.recipients:
    try:
      from services.gmail import send_email
      send_email(
        credentials=creds,
        to=recipient,
        subject=f"Meeting Summary: {title}",
        body=email_body,
      )
      sent_to.append(recipient)
    except Exception as e:
      logger.error("Failed to email %s: %s", recipient, e)
      errors.append({"recipient": recipient, "error": str(e)})

  if not sent_to and errors:
    raise HTTPException(status_code=500, detail=f"Failed to send to all recipients: {errors}")

  return {"status": "sent", "sent_to": sent_to, "errors": errors}


@router.get("/{meeting_id}/audio")
async def get_meeting_audio(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
  """Stream the audio recording file for a meeting."""
  from fastapi.responses import FileResponse

  conn = get_connection()
  try:
    cur = conn.cursor()
    cur.execute("SELECT audio_path FROM meetings WHERE id = ?", (meeting_id,))
    row = cur.fetchone()
    if not row:
      raise HTTPException(status_code=404, detail="Meeting not found")
    audio_path = row[0]
  finally:
    conn.close()

  if not audio_path:
    raise HTTPException(status_code=404, detail="No audio recording available for this meeting")

  p = Path(audio_path)
  if not p.exists():
    raise HTTPException(status_code=404, detail="Audio file not found on disk")

  media_type = "audio/wav"
  suffix = p.suffix.lower()
  if suffix == ".webm":
    media_type = "audio/webm"
  elif suffix == ".ogg":
    media_type = "audio/ogg"
  elif suffix in (".mp4", ".m4a"):
    media_type = "audio/mp4"

  return FileResponse(
    path=str(p),
    media_type=media_type,
    filename=p.name,
  )


@router.get("/{meeting_id}/export/{fmt}")
async def export_meeting(meeting_id: str, fmt: str, current_user: Optional[dict] = Depends(get_optional_user)):
  """Export a meeting as TXT or PDF."""
  from fastapi.responses import Response

  if fmt not in ("txt", "pdf"):
    raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}. Use 'txt' or 'pdf'.")

  conn = get_connection()
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()

    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute(
      "SELECT segment_num, start_time, end_time, text, speaker_id FROM segments WHERE meeting_id = ? ORDER BY segment_num",
      (meeting_id,),
    )
    segments = cur.fetchall()

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    summary_row = cur.fetchone()

    cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    local_summary_row = cur.fetchone()
  finally:
    conn.close()

  title = meeting.get("title", "Untitled Meeting")
  start = meeting.get("start_time", "")

  # Build transcript text
  transcript_lines = []
  for seg in segments:
    mins = int((seg["start_time"] or 0) // 60)
    secs = int((seg["start_time"] or 0) % 60)
    speaker = f" [{seg['speaker_id']}]" if seg.get("speaker_id") else ""
    transcript_lines.append(f"[{mins:02d}:{secs:02d}]{speaker} {seg['text']}")
  transcript_text = "\n".join(transcript_lines)

  # Build summary text
  summary_text = ""
  for label, row in [("API Summary", summary_row), ("Local Summary", local_summary_row)]:
    if not row:
      continue
    summary_text += f"\n--- {label} ---\n\n"
    summary_text += f"{row['summary']}\n"
    try:
      decisions = json.loads(row.get("decisions") or "[]")
      if decisions:
        summary_text += "\nDecisions:\n" + "\n".join(f"  - {d}" for d in decisions) + "\n"
    except (json.JSONDecodeError, TypeError):
      pass
    try:
      topics = json.loads(row.get("topics") or "[]")
      if topics:
        summary_text += "\nTopics: " + ", ".join(topics) + "\n"
    except (json.JSONDecodeError, TypeError):
      pass
    try:
      actions = json.loads(row.get("action_items") or "[]")
      if actions:
        summary_text += "\nAction Items:\n"
        for a in actions:
          if isinstance(a, dict):
            summary_text += f"  - {a.get('task', str(a))}"
            if a.get("assignee"):
              summary_text += f" (assigned to {a['assignee']})"
            summary_text += "\n"
          else:
            summary_text += f"  - {a}\n"
    except (json.JSONDecodeError, TypeError):
      pass
    if row.get("sentiment"):
      summary_text += f"\nSentiment: {row['sentiment']}\n"

  safe_title = title.replace(" ", "_")[:50]

  if fmt == "txt":
    content = f"{title}\nDate: {start}\n{'=' * 60}\n"
    if summary_text:
      content += f"\n{summary_text}\n"
    content += f"\n{'=' * 60}\nTRANSCRIPT\n{'=' * 60}\n\n{transcript_text}\n"
    return Response(
      content=content.encode("utf-8"),
      media_type="text/plain",
      headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
    )

  # PDF export using fpdf2
  from fpdf import FPDF

  def _pdf_safe(text: str) -> str:
    """Replace characters outside Latin-1 so Helvetica doesn't crash."""
    _REPLACEMENTS = {
      "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
      "\u2013": "-", "\u2014": "--", "\u2026": "...", "\u2022": "*",
      "\u00a0": " ", "\u200b": "",
    }
    for src, dst in _REPLACEMENTS.items():
      text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")

  try:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _pdf_safe(f"Date: {start}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    if summary_text:
      pdf.set_font("Helvetica", "B", 12)
      pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
      pdf.set_font("Helvetica", "", 10)
      for line in summary_text.strip().splitlines():
        pdf.multi_cell(0, 5, _pdf_safe(line))
      pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Transcript", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    for line in transcript_lines:
      pdf.multi_cell(0, 4, _pdf_safe(line))

    pdf_bytes = pdf.output()
  except Exception as exc:
    logger.error("PDF generation failed: %s", exc)
    raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

  return Response(
    content=bytes(pdf_bytes),
    media_type="application/pdf",
    headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
  )


@router.get("/{meeting_id}", response_model=MeetingDetail)
async def get_meeting(meeting_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}  # type: ignore
  try:
    cur = conn.cursor()

    cur.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute(
      """
      SELECT segment_num, start_time, end_time, text, speaker_id
      FROM segments
      WHERE meeting_id = ?
      ORDER BY segment_num
      """,
      (meeting_id,),
    )
    segments_rows = cur.fetchall()

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    summary_row = cur.fetchone()

    cur.execute("SELECT * FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    local_summary_row = cur.fetchone()
  finally:
    conn.close()

  segments = [
    {
      "segment_num": r["segment_num"],
      "start_time": r["start_time"],
      "end_time": r["end_time"],
      "text": r["text"],
      "speaker_id": r.get("speaker_id"),
    }
    for r in segments_rows
  ]

  summary = None
  if summary_row:
    summary = _normalize_summary_data({
      "summary": summary_row["summary"],
      "action_items": json.loads(summary_row["action_items"] or "[]"),
      "decisions": json.loads(summary_row["decisions"] or "[]"),
      "topics": json.loads(summary_row["topics"] or "[]"),
      "sentiment": summary_row["sentiment"],
    })

  local_summary = None
  if local_summary_row:
    local_summary = _normalize_summary_data({
      "summary": local_summary_row["summary"],
      "action_items": json.loads(local_summary_row["action_items"] or "[]"),
      "decisions": json.loads(local_summary_row["decisions"] or "[]"),
      "topics": json.loads(local_summary_row["topics"] or "[]"),
      "sentiment": local_summary_row["sentiment"],
    })
    local_summary["model_name"] = local_summary_row.get("model_name", "unknown")

  # derive duration if missing and we have end_time
  if meeting.get("duration") is None and meeting.get("start_time") and meeting.get("end_time"):
    try:
      start_dt = datetime.fromisoformat(meeting["start_time"])
      end_dt = datetime.fromisoformat(meeting["end_time"])
      meeting["duration"] = int((end_dt - start_dt).total_seconds())
    except Exception:
      pass

  return {
    "meeting": meeting,
    "segments": segments,
    "summary": summary,
    "local_summary": local_summary,
  }

