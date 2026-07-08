import json
import logging
import os
import random
import string
import subprocess
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict
import redis
import shutil
import httpx
import numpy as np

from auth import get_current_user, get_optional_actor
from database import get_connection
from meeting_agent import run_meeting_agent_pipeline
from rate_limit import limiter

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

# OpenAI (Whisper) transcription for upload-audio pipeline
_openai_client = None


def _get_openai_client():
  global _openai_client
  if _openai_client is None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
      return None
    from openai import OpenAI

    _openai_client = OpenAI(api_key=api_key)
  return _openai_client

router = APIRouter()
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
_redis_client = None

def _get_redis() -> redis.Redis:
  """Lazy Redis connection — created on first use, not at import time."""
  global _redis_client
  if _redis_client is None:
    _redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
  return _redis_client


def _recording_redis(callable_fn):
  """
  Run callable_fn(redis_client). Maps Redis outages to HTTP 503 so clients
  see a clear message instead of a generic 500 (common when Redis is down).
  """
  try:
    return callable_fn(_get_redis())
  except redis.RedisError as exc:
    logger.exception("Recording Redis operation failed")
    raise HTTPException(
      status_code=503,
      detail="Recording service is unavailable (cannot reach Redis). Ensure the Redis service is running and REDIS_HOST is correct.",
    ) from exc


# Must match audio capture + MEETINGBOX_ROOT layout (native installs use e.g. /opt/meetingbox/data/...).
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "/data/audio/recordings"))
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

def _env_int(name: str, default: int) -> int:
    try:
      return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
      return default


MAX_UPLOAD_SIZE = _env_int("MEETINGBOX_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024)
OPENAI_TRANSCRIBE_MAX_BYTES = _env_int("OPENAI_TRANSCRIBE_MAX_BYTES", 24 * 1024 * 1024)
OPENAI_TRANSCRIBE_CHUNK_SECONDS = _env_int("OPENAI_TRANSCRIBE_CHUNK_SECONDS", 10 * 60)
DEFAULT_MAX_MEETING_UPLOAD_SECONDS = _env_int("MEETINGBOX_MAX_MEETING_UPLOAD_SECONDS", 8 * 60 * 60)
DEFAULT_TRANSCRIBE_PROMPT = (
  "This is a multilingual meeting. Speakers may switch between English, Telugu, Hindi, "
  "and other Indian languages. Transcribe all speech accurately in the language spoken. "
  "Preserve names, numbers, dates, product names, English technical terms, and code-mixed phrases. "
  "Do not translate during transcription."
)
_PROMPT_ECHO_MARKERS = (
  "transcribe all speech accurately in the language spoken",
  "this is a multilingual meeting",
  "preserve names numbers dates product names",
  "do not translate during transcription",
)

# Silence/noise guard before Whisper to avoid hallucinating text from fan hum
# or empty rooms. Tunable through env vars if field conditions vary.
NO_SPEECH_MIN_SECONDS = max(1, _env_int("MEETINGBOX_NO_SPEECH_MIN_SECONDS", 6))
NO_SPEECH_SPEECHLIKE_RMS = max(1, _env_int("MEETINGBOX_NO_SPEECH_SPEECHLIKE_RMS", 320))
NO_SPEECH_SPEECHLIKE_PEAK = max(1, _env_int("MEETINGBOX_NO_SPEECH_SPEECHLIKE_PEAK", 1200))
NO_SPEECH_MAX_GLOBAL_RMS = max(1, _env_int("MEETINGBOX_NO_SPEECH_MAX_GLOBAL_RMS", 180))
NO_SPEECH_MAX_P95_PEAK = max(1, _env_int("MEETINGBOX_NO_SPEECH_MAX_P95_PEAK", 850))


def _generate_session_id() -> str:
    """Generate a unique session ID with timestamp + random suffix."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{suffix}"


def _derive_title(report_body: str) -> str:
    """Derive a short human-readable meeting title from the report text."""
    if not report_body:
      return ""
    line = report_body.strip().split("\n")[0].strip()
    if not line:
      return ""
    if len(line) > 100:
      first_sent = line.split(".")[0].strip()
      if first_sent and len(first_sent) <= 100:
        line = first_sent + ("." if first_sent[-1] not in ".?!" else "")
    if len(line) > 80:
      return line[:77] + "..."
    return line


def _coerce_str_list(raw: object) -> list[str]:
  out: list[str] = []
  for item in (raw if isinstance(raw, list) else []) or []:
    if isinstance(item, str):
      out.append(item)
    elif isinstance(item, dict):
      out.append(str(item.get("text") or item.get("point") or item.get("question") or item))
    else:
      out.append(str(item))
  return out


def _compose_stored_report_body(data: dict) -> str:
  """Single text stored in summaries.summary for API + device-ui (full report body)."""
  main = (data.get("full_report") or data.get("summary") or "").strip()
  parts: list[str] = [main] if main else []
  oq = data.get("open_questions") or []
  if oq:
    parts.append("\n\n---\nOPEN QUESTIONS\n" + "\n".join(f"• {x}" for x in oq))
  rc = data.get("risks_or_concerns") or []
  if rc:
    parts.append("\n\n---\nRISKS / CONCERNS\n" + "\n".join(f"• {x}" for x in rc))
  return "".join(parts).strip()


# Accepted upload extensions; non-WAV are converted with ffmpeg to 16kHz mono WAV
UPLOAD_AUDIO_EXTENSIONS = {".wav", ".webm", ".ogg", ".mp4", ".m4a"}


class MeetingResponse(BaseModel):
  id: str
  user_id: Optional[str] = None
  device_id: Optional[str] = None
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
  # ISO timestamp when the summary row was persisted. Surfaced so the
  # device-ui footer can show "Created: <date>" instead of falling back
  # to the meeting's ``started_at``.
  generated_at: Optional[str] = None


class LocalSummary(BaseModel):
  model_config = ConfigDict(protected_namespaces=())

  summary: str
  action_items: list[dict]
  decisions: list
  topics: list
  sentiment: str
  model_name: str
  generated_at: Optional[str] = None


def _normalize_summary_data(data: dict) -> dict:
  """Normalize LLM output so decisions are lists of strings and action_items are dicts."""
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

  data["open_questions"] = _coerce_str_list(data.get("open_questions"))
  data["risks_or_concerns"] = _coerce_str_list(data.get("risks_or_concerns"))

  # Normalize topics into [{"name": str, "value": int 0-100}] so the device-ui
  # Key Topics / Key Points views can render progress bars directly. Drop
  # malformed entries; tolerate plain strings as zero-value labels.
  raw_topics = data.get("topics", [])
  topics: list[dict] = []
  for t in raw_topics or []:
    if isinstance(t, dict):
      name = (t.get("name") or t.get("topic") or "").strip()
      if not name:
        continue
      val_raw = t.get("value")
      if val_raw is None:
        val_raw = t.get("percentage")
      try:
        value = int(round(float(val_raw or 0)))
      except (TypeError, ValueError):
        value = 0
      topics.append({"name": name[:60], "value": max(0, min(100, value))})
    elif isinstance(t, str):
      n = t.strip()
      if n:
        topics.append({"name": n[:60], "value": 0})
  # Cap at 6 to keep the UI grid tidy.
  data["topics"] = topics[:6]

  # Sentiment is not surfaced in the product; keep the column empty.
  data["sentiment"] = ""

  return data


def _resolve_user_id_for_post_summarize_actions(actor: Optional[dict]) -> Optional[str]:
  """
  Logged-in web clients and device actors: use the actor's own user id.
  Never fall back to an unrelated user's integrations — that would attribute
  meeting memories to the wrong person on multi-user servers.
  """
  direct_user_id = _actor_user_id(actor)
  if direct_user_id:
    return str(direct_user_id)
  # If the actor is a device but user resolution failed (edge case: device JWT
  # with no owner_user_id), look the device up directly in the devices table.
  device_id = _actor_device_id(actor)
  if device_id:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute("SELECT user_id FROM devices WHERE id = ?", (device_id,))
      row = cur.fetchone()
      if row and row[0]:
        return str(row[0]).strip()
    finally:
      conn.close()
  return None


class MeetingDetail(BaseModel):
  meeting: MeetingResponse
  segments: List[TranscriptSegment]
  summary: Optional[MeetingSummary]
  local_summary: Optional[LocalSummary]


class RecordingControlRequest(BaseModel):
  device_id: Optional[str] = None
  target_device_id: Optional[str] = None
  recording_mode: Optional[str] = None
  # Rich context the user gave *before* recording starts (captured by the voice
  # layer). Stored as searchable metadata so the recording is findable later
  # even when these words never appear in the transcript. Free-form: any of
  # session_intent / pre_context and entity arrays (referenced_people, …).
  context: Optional[dict] = None


def _app_setting_int(key: str, default: int) -> int:
  conn = get_connection()
  try:
    cur = conn.cursor()
    cur.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
  finally:
    conn.close()
  if not row or row[0] is None:
    return default
  try:
    return int(str(row[0]).strip())
  except (TypeError, ValueError):
    return default


def _actor_user_id(actor: Optional[dict]) -> Optional[str]:
  if not actor:
    return None
  if actor["type"] == "device":
    return actor["user"]["id"]
  return actor["user"]["id"]


def _user_display_names(user_id: str) -> list[str]:
  """Best-effort fetch of names the user is likely called by in meeting transcripts.

  Returns display_name, username, and the local part of the email (if any),
  so the meeting -> tasks bridge can match owner mentions like 'Vivek to send...'.
  """
  if not (user_id or "").strip():
    return []
  conn = get_connection()
  try:
    cur = conn.cursor()
    cur.execute(
      "SELECT display_name, username, email FROM users WHERE id = ?",
      (user_id,),
    )
    row = cur.fetchone()
  finally:
    conn.close()
  if not row:
    return []
  names: list[str] = []
  display_name = row[0] if isinstance(row, (list, tuple)) else row.get("display_name")
  username = row[1] if isinstance(row, (list, tuple)) else row.get("username")
  email = row[2] if isinstance(row, (list, tuple)) else row.get("email")
  if display_name:
    names.append(str(display_name).strip())
    parts = str(display_name).strip().split()
    if parts:
      names.append(parts[0])
  if username:
    names.append(str(username).strip())
  if email and "@" in str(email):
    local = str(email).split("@", 1)[0].strip()
    if local:
      names.append(local)
  return [n for n in dict.fromkeys(names) if n]


def _actor_device_id(actor: Optional[dict]) -> Optional[str]:
  if not actor or actor["type"] != "device":
    return None
  return actor["device"]["id"]


def _recording_state_key(device_id: Optional[str]) -> str:
  return f"recording_state:{device_id}" if device_id else "recording_state"


def _current_meeting_key(device_id: Optional[str]) -> str:
  return f"current_meeting_id:{device_id}" if device_id else "current_meeting_id"


def _active_user_device_ids(user_id: str) -> list[str]:
  conn = get_connection()
  try:
    cur = conn.cursor()
    cur.execute(
      """
      SELECT id FROM devices
      WHERE user_id = ?
        AND (status IS NULL OR TRIM(COALESCE(status, '')) = ''
             OR LOWER(TRIM(status)) = 'active')
      ORDER BY datetime(COALESCE(last_seen_at, paired_at, created_at, '1970-01-01')) DESC
      """,
      (user_id,),
    )
    return [str(row[0]) for row in cur.fetchall() if row and row[0]]
  finally:
    conn.close()


def _resolve_recording_device_id(actor: dict, requested_device_id: Optional[str] = None) -> Optional[str]:
  """Resolve the one appliance a recording command is allowed to control."""
  actor_device_id = _actor_device_id(actor)
  if actor_device_id:
    return actor_device_id

  requested = (requested_device_id or "").strip() or None
  user_id = _actor_user_id(actor)
  if not user_id:
    return None

  active_ids = _active_user_device_ids(user_id)
  if requested:
    if requested not in active_ids:
      raise HTTPException(status_code=404, detail="Device not found for this account.")
    return requested
  if len(active_ids) == 1:
    return active_ids[0]
  if len(active_ids) > 1:
    raise HTTPException(
      status_code=400,
      detail="Multiple devices are paired. Choose a device_id for this recording command.",
    )
  return None


def emit_audio_command(actor: dict, payload: dict, device_id: Optional[str] = None) -> Optional[str]:
  """Publish one recording command, scoped to exactly one appliance when possible."""
  target_device_id = device_id or _resolve_recording_device_id(actor)
  cmd = dict(payload)
  if target_device_id:
    cmd["device_id"] = target_device_id
  _get_redis().publish("commands", json.dumps(cmd))
  return target_device_id


def _publish_recording_ws_event(
  event_type: str,
  session_id: Optional[str],
  device_id: Optional[str] = None,
  user_id: Optional[str] = None,
) -> None:
  payload = {
    "type": event_type,
    "session_id": session_id,
    "timestamp": datetime.now().isoformat(),
  }
  if device_id:
    payload["device_id"] = device_id
  if user_id:
    payload["user_id"] = user_id
  _get_redis().publish("events", json.dumps(payload))


def _session_owner_key(session_id: str) -> str:
  return f"meeting_session_owner:{session_id}"


def _session_context_key(session_id: str) -> str:
  return f"meeting_session_context:{session_id}"


# Keys we accept in a pre-recording context payload (everything else ignored).
_CONTEXT_TEXT_KEYS = ("session_intent", "pre_context")
_CONTEXT_LIST_KEYS = (
  "intent_tags",
  "context_tags",
  "referenced_people",
  "referenced_projects",
  "referenced_events",
  "referenced_organizations",
  "referenced_locations",
  "referenced_topics",
  "future_reference_tags",
)


def _sanitize_context_payload(raw: object) -> dict:
  """Whitelist + coerce a caller-supplied context dict into storable fields."""
  if not isinstance(raw, dict):
    return {}
  out: dict = {}
  for k in _CONTEXT_TEXT_KEYS:
    if raw.get(k) is not None:
      val = str(raw.get(k) or "").strip()
      if val:
        out[k] = val[:2000]
  for k in _CONTEXT_LIST_KEYS:
    if raw.get(k) is not None:
      out[k] = raw.get(k)
  return out


def _stage_session_context(session_id: str, context: object) -> None:
  payload = _sanitize_context_payload(context)
  if not session_id or not payload:
    return
  try:
    _get_redis().setex(_session_context_key(session_id), 6 * 60 * 60, json.dumps(payload))
  except Exception:
    logger.debug("failed to stage session context for %s", session_id, exc_info=True)


def _load_session_context(session_id: Optional[str]) -> dict:
  if not session_id:
    return {}
  try:
    raw = _get_redis().get(_session_context_key(session_id))
  except Exception:
    return {}
  if not raw:
    return {}
  try:
    return json.loads(raw)
  except json.JSONDecodeError:
    return {}


def _store_session_owner(
  session_id: str,
  actor: Optional[dict],
  device_id_override: Optional[str] = None,
) -> None:
  user_id = _actor_user_id(actor)
  device_id = device_id_override or _actor_device_id(actor)
  if not session_id or (not user_id and not device_id):
    return
  _get_redis().setex(
    _session_owner_key(session_id),
    6 * 60 * 60,
    json.dumps({"user_id": user_id, "device_id": device_id}),
  )


def _load_session_owner(session_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
  if not session_id:
    return None, None
  raw = _get_redis().get(_session_owner_key(session_id))
  if not raw:
    return None, None
  try:
    data = json.loads(raw)
  except json.JSONDecodeError:
    return None, None
  user_id = str(data.get("user_id") or "").strip() or None
  device_id = str(data.get("device_id") or "").strip() or None
  return user_id, device_id


def _require_actor(current_actor: Optional[dict]) -> dict:
  """Reject unauthenticated access to tenant-scoped meeting APIs."""
  if not current_actor:
    raise HTTPException(status_code=401, detail="Authentication required.")
  return current_actor


def _meeting_access_filter(actor: Optional[dict], alias: str = "meetings") -> tuple[str, list[object]]:
  """
  Scope meetings to the actor:
  - device: rows explicitly owned by this user OR unclaimed rows recorded on this device
  - user: rows owned by user_id OR unclaimed rows recorded on any device paired to that user

  The device_id branch is intentionally a fallback (only when user_id IS NULL/empty) so that
  a meeting's explicit user_id attribution always wins — preventing cross-user data leaks when
  a device changes ownership.
  """
  if not actor:
    return "", []
  if actor["type"] == "device":
    uid = _actor_user_id(actor)
    did = _actor_device_id(actor)
    # A meeting belongs to this device actor if:
    #   1. Its user_id is already attributed to the device owner, OR
    #   2. It has no user_id (orphaned device recording) AND matches this device_id.
    pred = (
      f"({alias}.user_id = ? OR "
      f"(COALESCE(TRIM({alias}.user_id), '') = '' AND {alias}.device_id = ?))"
    )
    return pred, [uid, did]
  uid = _actor_user_id(actor)
  # A meeting belongs to this user if:
  #   1. Its user_id matches, OR
  #   2. It has no user_id (old recording) AND was recorded on a device paired to this user.
  pred = (
    f"({alias}.user_id = ? OR "
    f"(COALESCE(TRIM({alias}.user_id), '') = '' AND {alias}.device_id IN ("
    " SELECT id FROM devices WHERE user_id = ? "
    " AND (status IS NULL OR TRIM(COALESCE(status, '')) = '' OR LOWER(TRIM(status)) = 'active')))"
    ")"
  )
  return pred, [uid, uid]


# --- Start / Stop meeting (wire to Redis for audio service) ---
# Recording control accepts a signed-in user or a paired device token.

@router.post("/start")
async def start_meeting(
  body: Optional[RecordingControlRequest] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Start a new recording. Sends command to audio service via Redis."""
  actor = _require_actor(current_actor)
  session_id = _generate_session_id()
  requested_device_id = (body.device_id or body.target_device_id) if body else None
  device_id = _resolve_recording_device_id(actor, requested_device_id)
  mode = ((body.recording_mode if body else None) or "meeting").strip().lower()
  if mode not in {"meeting", "note"}:
    mode = "meeting"

  def _work(r):
    _store_session_owner(session_id, current_actor, device_id_override=device_id)
    cmd = {"action": "start_recording", "session_id": session_id, "recording_mode": mode}
    if device_id:
      cmd["device_id"] = device_id
    r.publish("commands", json.dumps(cmd))
    r.set(_current_meeting_key(device_id), session_id)
    r.set(_recording_state_key(device_id), "recording")

  _recording_redis(_work)
  # Stash any pre-recording context (mentioned people/topics/intent) so it can
  # be persisted as searchable metadata when the audio is uploaded.
  if body and body.context:
    _stage_session_context(session_id, body.context)
  return {"session_id": session_id, "status": "recording_started", "recording_mode": mode}


@router.post("/stop")
async def stop_meeting(
  body: Optional[RecordingControlRequest] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Stop the current recording. Sends command to audio service via Redis."""
  actor = _require_actor(current_actor)
  requested_device_id = (body.device_id or body.target_device_id) if body else None
  device_id = _resolve_recording_device_id(actor, requested_device_id)

  def _work(r):
    session_id = r.get(_current_meeting_key(device_id))
    cmd = {"action": "stop_recording", "session_id": session_id}
    if device_id:
      cmd["device_id"] = device_id
    r.publish("commands", json.dumps(cmd))
    r.set(_recording_state_key(device_id), "processing")
    if session_id:
      r.delete(_current_meeting_key(device_id))
    return session_id

  session_id = _recording_redis(_work)
  return {"session_id": session_id, "status": "recording_stopped"}


@router.get("/recording-status")
async def recording_status(
  device_id: Optional[str] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Current recording state for the dashboard."""
  actor = _require_actor(current_actor)
  target_device_id = _resolve_recording_device_id(actor, device_id)

  def _read(r):
    state = r.get(_recording_state_key(target_device_id)) or "idle"
    current_id = r.get(_current_meeting_key(target_device_id))
    return {"state": state, "session_id": current_id}

  return _recording_redis(_read)


@router.post("/reset-recording-state")
async def reset_recording_state(
  body: Optional[RecordingControlRequest] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Clear recording state so the dashboard shows Start/Record buttons again (e.g. if stuck on Processing)."""
  actor = _require_actor(current_actor)
  requested_device_id = (body.device_id or body.target_device_id) if body else None
  device_id = _resolve_recording_device_id(actor, requested_device_id)

  def _reset(r):
    r.set(_recording_state_key(device_id), "idle")
    r.delete(_current_meeting_key(device_id))

  _recording_redis(_reset)
  return {"status": "idle"}


@router.post("/pause")
async def pause_meeting(
  body: Optional[RecordingControlRequest] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Pause the current recording."""
  actor = _require_actor(current_actor)
  requested_device_id = (body.device_id or body.target_device_id) if body else None
  device_id = _resolve_recording_device_id(actor, requested_device_id)

  def _work(r):
    state = r.get(_recording_state_key(device_id)) or "idle"
    if state != "recording":
      raise HTTPException(status_code=400, detail="No active recording to pause")
    session_id = r.get(_current_meeting_key(device_id))
    cmd = {"action": "pause_recording", "session_id": session_id}
    if device_id:
      cmd["device_id"] = device_id
    r.publish("commands", json.dumps(cmd))
    r.set(_recording_state_key(device_id), "paused")
    return session_id

  session_id = _recording_redis(_work)
  return {"status": "paused", "session_id": session_id}


@router.post("/resume")
async def resume_meeting(
  body: Optional[RecordingControlRequest] = None,
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """Resume a paused recording."""
  actor = _require_actor(current_actor)
  requested_device_id = (body.device_id or body.target_device_id) if body else None
  device_id = _resolve_recording_device_id(actor, requested_device_id)

  def _work(r):
    state = r.get(_recording_state_key(device_id)) or "idle"
    if state != "paused":
      raise HTTPException(status_code=400, detail="No paused recording to resume")
    session_id = r.get(_current_meeting_key(device_id))
    cmd = {"action": "resume_recording", "session_id": session_id}
    if device_id:
      cmd["device_id"] = device_id
    r.publish("commands", json.dumps(cmd))
    r.set(_recording_state_key(device_id), "recording")
    return session_id

  session_id = _recording_redis(_work)
  return {"status": "recording", "session_id": session_id}


class MeetingUpdateRequest(BaseModel):
  title: Optional[str] = None
  status: Optional[str] = None


@router.patch("/{meeting_id}")
async def update_meeting(meeting_id: str, body: MeetingUpdateRequest, current_actor: Optional[dict] = Depends(get_optional_actor)):
  """Update editable fields of a meeting (title, status)."""
  _require_actor(current_actor)
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()
    where_sql, where_params = _meeting_access_filter(current_actor)
    query = "SELECT * FROM meetings WHERE id = ?"
    query_params: list[object] = [meeting_id]
    if where_sql:
      query += f" AND {where_sql}"
      query_params.extend(where_params)
    cur.execute(query, query_params)
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

    w_sql, w_params = _meeting_access_filter(current_actor)
    upd_sql = f"UPDATE meetings SET {', '.join(updates)} WHERE id = ? AND {w_sql}"
    cur.execute(upd_sql, params + [meeting_id] + w_params)
    conn.commit()

    cur.execute(query, query_params)
    return cur.fetchone()
  finally:
    conn.close()


@router.delete("/{meeting_id}")
async def delete_meeting(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
  """Delete a meeting and all its associated data."""
  _require_actor(current_actor)
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  try:
    cur = conn.cursor()
    where_sql, where_params = _meeting_access_filter(current_actor)
    query = "SELECT id, audio_path FROM meetings WHERE id = ?"
    query_params: list[object] = [meeting_id]
    if where_sql:
      query += f" AND {where_sql}"
      query_params.extend(where_params)
    cur.execute(query, query_params)
    row = cur.fetchone()
    if not row:
      raise HTTPException(status_code=404, detail="Meeting not found")

    audio_path = row[1]
    cur.execute("DELETE FROM actions WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM summaries WHERE meeting_id = ?", (meeting_id,))
    cur.execute("DELETE FROM local_summaries WHERE meeting_id = ?", (meeting_id,))
    del_sql = "DELETE FROM meetings WHERE id = ?"
    del_params: list[object] = [meeting_id]
    if where_sql:
      del_sql += f" AND {where_sql}"
      del_params.extend(where_params)
    cur.execute(del_sql, del_params)
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
async def ingest_test_wav(file: UploadFile = File(...), current_actor: dict | None = Depends(get_optional_actor)):
  """
  Upload a WAV file to run through the cloud-only Anthropic pipeline.
  """
  _require_actor(current_actor)
  if not file.filename or not file.filename.lower().endswith(".wav"):
    raise HTTPException(status_code=400, detail="Upload must be a .wav file")
  return await upload_audio(file=file, session_id=_generate_session_id(), current_actor=current_actor)


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
    timeout=_env_int("MEETINGBOX_FFMPEG_TIMEOUT_SECONDS", 1800),
  )


def _no_speech_active_ratio_default() -> float:
  raw = (os.getenv("MEETINGBOX_NO_SPEECH_MIN_ACTIVE_RATIO") or "").strip()
  if not raw:
    return 0.02
  try:
    val = float(raw)
  except (TypeError, ValueError):
    return 0.02
  return max(0.0, min(1.0, val))


NO_SPEECH_MIN_ACTIVE_RATIO = _no_speech_active_ratio_default()


def _detect_no_speech_audio(audio_path: Path) -> tuple[bool, dict]:
  """Detect fan/noise-only WAVs before Whisper transcription."""
  frame_ms = 30
  try:
    with wave.open(str(audio_path), "rb") as wf:
      sample_rate = int(wf.getframerate() or 16000)
      channels = int(wf.getnchannels() or 1)
      frame_width = max(1, int(sample_rate * frame_ms / 1000))
      all_frames: list[np.ndarray] = []
      while True:
        raw = wf.readframes(frame_width)
        if not raw:
          break
        arr = np.frombuffer(raw, dtype=np.int16)
        if arr.size == 0:
          continue
        if channels > 1:
          arr = arr.reshape(-1, channels).mean(axis=1).astype(np.int16)
        all_frames.append(arr)
  except Exception:
    logger.debug("No-speech detector failed to read wav: %s", audio_path, exc_info=True)
    return False, {}

  if not all_frames:
    return True, {"reason": "empty_audio"}

  rms_values: list[float] = []
  peak_values: list[int] = []
  active_frames = 0
  for arr in all_frames:
    f = arr.astype(np.float64)
    rms = float(np.sqrt(np.mean(np.square(f))))
    peak = int(np.max(np.abs(arr)))
    rms_values.append(rms)
    peak_values.append(peak)
    if rms >= NO_SPEECH_SPEECHLIKE_RMS or peak >= NO_SPEECH_SPEECHLIKE_PEAK:
      active_frames += 1

  duration_s = sum(frame.size for frame in all_frames) / float(sample_rate)
  global_rms = float(np.sqrt(np.mean(np.square(np.concatenate(all_frames).astype(np.float64)))))
  p95_peak = int(np.percentile(np.array(peak_values, dtype=np.float64), 95)) if peak_values else 0
  active_ratio = active_frames / float(len(all_frames))

  is_no_speech = (
    duration_s >= NO_SPEECH_MIN_SECONDS
    and active_ratio < NO_SPEECH_MIN_ACTIVE_RATIO
    and global_rms <= NO_SPEECH_MAX_GLOBAL_RMS
    and p95_peak <= NO_SPEECH_MAX_P95_PEAK
  )
  stats = {
    "duration_s": round(duration_s, 2),
    "active_ratio": round(active_ratio, 4),
    "global_rms": round(global_rms, 2),
    "p95_peak": p95_peak,
  }
  return is_no_speech, stats


def _normalize_prompt_echo_text(text: str) -> str:
  return " ".join(
    "".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split()
  )


def _looks_like_transcription_prompt_echo(text: str) -> bool:
  """Whisper sometimes echoes the prompt when audio is clipped/unintelligible."""
  norm = _normalize_prompt_echo_text(text)
  if not norm:
    return False
  markers_found = sum(1 for marker in _PROMPT_ECHO_MARKERS if marker in norm)
  repeated_core = norm.count(_PROMPT_ECHO_MARKERS[0]) >= 2
  prompt = _normalize_prompt_echo_text(
    (os.getenv("OPENAI_TRANSCRIBE_PROMPT") or DEFAULT_TRANSCRIBE_PROMPT).strip()
  )
  prompt_words = set(prompt.split())
  words = norm.split()
  prompt_word_ratio = (
    sum(1 for word in words if word in prompt_words) / float(len(words))
    if words and prompt_words else 0.0
  )
  return repeated_core or (markers_found >= 2 and prompt_word_ratio >= 0.75)


def _transcribe_audio_file_once(client, model: str, audio_path: Path, *, use_prompt: bool = True) -> str:
  with open(audio_path, "rb") as audio_fp:
    kwargs = {
      "model": model,
      "file": (audio_path.name, audio_fp, "audio/wav"),
    }
    prompt = (os.getenv("OPENAI_TRANSCRIBE_PROMPT") or DEFAULT_TRANSCRIBE_PROMPT).strip()
    language = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "").strip()
    if prompt and use_prompt:
      kwargs["prompt"] = prompt
    if language:
      kwargs["language"] = language
    tr = client.audio.transcriptions.create(**kwargs)
  return (getattr(tr, "text", None) or "").strip()


def _transcribe_audio_file(client, model: str, audio_path: Path) -> str:
  transcript = _transcribe_audio_file_once(client, model, audio_path, use_prompt=True)
  if not _looks_like_transcription_prompt_echo(transcript):
    return transcript
  logger.warning(
    "Whisper returned transcription prompt echo for %s; retrying once without prompt",
    audio_path.name,
  )
  retry = _transcribe_audio_file_once(client, model, audio_path, use_prompt=False)
  if retry and not _looks_like_transcription_prompt_echo(retry):
    return retry
  raise HTTPException(
    status_code=422,
    detail=(
      "Transcription failed because Whisper returned only the transcription prompt. "
      "The audio was likely clipped, too noisy, or otherwise unintelligible."
    ),
  )


def _split_wav_for_transcription(audio_path: Path) -> list[Path]:
  """Split a PCM WAV into chunks small enough for OpenAI's transcription file cap."""
  chunks: list[Path] = []
  try:
    with wave.open(str(audio_path), "rb") as src:
      channels = src.getnchannels()
      sample_width = src.getsampwidth()
      frame_rate = src.getframerate()
      bytes_per_second = max(1, channels * sample_width * frame_rate)
      max_seconds_by_size = max(60, (OPENAI_TRANSCRIBE_MAX_BYTES // bytes_per_second) - 5)
      chunk_seconds = max(60, min(OPENAI_TRANSCRIBE_CHUNK_SECONDS, max_seconds_by_size))
      frames_per_chunk = int(frame_rate * chunk_seconds)
      idx = 0
      while True:
        frames = src.readframes(frames_per_chunk)
        if not frames:
          break
        fd, raw_chunk_path = tempfile.mkstemp(
          prefix=f"{audio_path.stem}_part{idx:03d}_",
          suffix=".wav",
          dir=str(audio_path.parent),
        )
        os.close(fd)
        chunk_path = Path(raw_chunk_path)
        with wave.open(str(chunk_path), "wb") as out:
          out.setnchannels(channels)
          out.setsampwidth(sample_width)
          out.setframerate(frame_rate)
          out.writeframes(frames)
        chunks.append(chunk_path)
        idx += 1
  except Exception:
    for chunk in chunks:
      chunk.unlink(missing_ok=True)
    raise
  return chunks


def _transcribe_audio_with_openai(audio_path: Path) -> str:
  """Transcribe meeting WAV via OpenAI Audio API, chunking long recordings."""
  no_speech, stats = _detect_no_speech_audio(audio_path)
  if no_speech:
    logger.info(
      "Skipping Whisper for %s: no clear speech detected (%s)",
      audio_path.name,
      stats,
    )
    return ""
  client = _get_openai_client()
  if not client:
    raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not configured on the server.")
  model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
  logger.info("Transcribing %s with OpenAI (%s)", audio_path.name, model)
  try:
    if audio_path.stat().st_size <= OPENAI_TRANSCRIBE_MAX_BYTES:
      transcript = _transcribe_audio_file(client, model, audio_path)
    else:
      chunks = _split_wav_for_transcription(audio_path)
      try:
        parts: list[str] = []
        total = len(chunks)
        logger.info("Transcribing %s in %d chunks", audio_path.name, total)
        for idx, chunk_path in enumerate(chunks, start=1):
          logger.info("Transcribing chunk %d/%d for %s", idx, total, audio_path.name)
          part = _transcribe_audio_file(client, model, chunk_path)
          if part:
            parts.append(part)
        transcript = "\n\n".join(parts).strip()
      finally:
        for chunk_path in chunks:
          chunk_path.unlink(missing_ok=True)
  except HTTPException:
    raise
  except Exception as exc:
    logger.exception("OpenAI transcription failed: %s", exc)
    raise HTTPException(status_code=502, detail=f"OpenAI transcription failed: {exc}") from exc
  return transcript


@router.post("/upload-audio")
@limiter.limit("30/hour")
async def upload_audio(
  request: Request,
  file: UploadFile = File(...),
  session_id: Optional[str] = Form(default=None),
  recording_mode: Optional[str] = Form(default="meeting"),
  context_json: Optional[str] = Form(default=None),
  current_actor: Optional[dict] = Depends(get_optional_actor),
):
  """
  Upload audio from your computer (e.g. browser recording). Accepts WAV, WebM, OGG, MP4.
  Converts to 16kHz mono WAV, transcribes with OpenAI (Whisper), summarizes with Anthropic.
  Use this to record with your PC mic: record in the browser, then upload.
  """
  _require_actor(current_actor)
  fn = (file.filename or "").lower()
  ext = Path(fn).suffix or ".webm"
  if ext not in UPLOAD_AUDIO_EXTENSIONS:
    ext = ".webm"
  session_id = session_id or _generate_session_id()
  mode = (recording_mode or "meeting").strip().lower()
  if mode not in {"meeting", "note"}:
    mode = "meeting"
  title_prefix = "Notes" if mode == "note" else "Meeting"
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

  now_iso = datetime.now().isoformat()

  duration_seconds = 0
  try:
    with wave.open(str(dest_wav), "rb") as wf:
      frames = wf.getnframes()
      rate = wf.getframerate() or 16000
      duration_seconds = int(frames / float(rate))
  except Exception:
    duration_seconds = 0

  max_duration_seconds = max(
    _app_setting_int("max_meeting_upload_seconds", DEFAULT_MAX_MEETING_UPLOAD_SECONDS),
    DEFAULT_MAX_MEETING_UPLOAD_SECONDS,
  )
  if duration_seconds and duration_seconds > max_duration_seconds:
    dest_wav.unlink(missing_ok=True)
    raise HTTPException(
      status_code=413,
      detail=f"Meeting audio exceeds the configured upload limit of {max_duration_seconds // 3600} hour(s).",
    )

  owner_user_id = _actor_user_id(current_actor)
  owner_device_id = _actor_device_id(current_actor)
  if not owner_user_id and not owner_device_id:
    owner_user_id, owner_device_id = _load_session_owner(session_id)

  _get_redis().set(_recording_state_key(owner_device_id), "processing")
  _get_redis().set(_current_meeting_key(owner_device_id), session_id)

  _get_redis().publish(
    "events",
    json.dumps({
      "type": "processing_started",
      "meeting_id": session_id,
      "device_id": owner_device_id,
      "user_id": owner_user_id,
      "title": f"{title_prefix} {session_id}",
      "recording_mode": mode,
      "duration": duration_seconds,
      "timestamp": now_iso,
    }),
  )

  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, device_id FROM meetings WHERE id = ?", (session_id,))
    exists = cur.fetchone()
    if not exists:
      cur.execute(
        """
        INSERT INTO meetings (id, user_id, device_id, title, start_time, end_time, duration, audio_path, status, created_at, recording_mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
          session_id,
          owner_user_id,
          owner_device_id,
          f"{title_prefix} {session_id}",
          now_iso,
          None,
          None,
          str(dest_wav),
          "transcribing",
          now_iso,
          mode,
        ),
      )
    else:
      # Verify ownership before updating an existing meeting row.
      # A meeting belongs to this actor if its user_id or device_id matches,
      # or if both are empty (orphaned device recording being claimed).
      row_user = (exists["user_id"] or "").strip()
      row_device = (exists["device_id"] or "").strip()
      actor_owns = (
        (owner_user_id and row_user == str(owner_user_id))
        or (owner_device_id and row_device == str(owner_device_id))
        or (not row_user and not row_device)
      )
      if not actor_owns:
        raise HTTPException(status_code=403, detail="You do not have permission to update this meeting.")
      cur.execute(
        """
        UPDATE meetings
        SET user_id = COALESCE(user_id, ?),
            device_id = COALESCE(device_id, ?),
            audio_path = ?,
            status = ?,
            start_time = COALESCE(start_time, ?),
            recording_mode = ?
        WHERE id = ?
        """,
        (owner_user_id, owner_device_id, str(dest_wav), "transcribing", now_iso, mode, session_id),
      )
    conn.commit()
  finally:
    conn.close()

  # Persist captured context (pre-recording staged at /start + anything sent
  # with this upload, e.g. post-recording comments) as searchable metadata
  # BEFORE the pipeline runs, so entity extraction can use it.
  try:
    staged = _load_session_context(session_id)
    uploaded: dict = {}
    if context_json:
      try:
        uploaded = _sanitize_context_payload(json.loads(context_json))
      except json.JSONDecodeError:
        uploaded = {}
    merged_ctx = {**staged, **uploaded}
    if merged_ctx or mode:
      from services.recording_store import save_recording_context

      save_recording_context(session_id, session_type=mode, merge=True, **merged_ctx)
    _get_redis().delete(_session_context_key(session_id))
  except Exception:
    logger.debug("failed to persist recording context meeting_id=%s", session_id, exc_info=True)

  try:
    await run_meeting_agent_pipeline(
      _get_redis(),
      session_id,
      dest_wav,
      duration_seconds,
      current_actor,
      recording_mode=mode,
    )

    _get_redis().set(_recording_state_key(owner_device_id), "idle")
    _get_redis().delete(_current_meeting_key(owner_device_id))
    _get_redis().delete(_session_owner_key(session_id))
    return {"session_id": session_id, "path": str(dest_wav), "status": "completed", "recording_mode": mode}
  except HTTPException as exc:
    _get_redis().set(_recording_state_key(owner_device_id), "idle")
    _get_redis().delete(_current_meeting_key(owner_device_id))
    _get_redis().delete(_session_owner_key(session_id))
    _get_redis().publish(
      "events",
      json.dumps({
        "type": "error",
        "error_type": "Processing Failed",
        "message": exc.detail,
        "meeting_id": session_id,
        "device_id": owner_device_id,
        "timestamp": datetime.now().isoformat(),
      }),
    )
    raise
  except Exception as exc:
    logger.exception("upload_audio pipeline failed meeting_id=%s", session_id)
    _get_redis().set(_recording_state_key(owner_device_id), "idle")
    _get_redis().delete(_current_meeting_key(owner_device_id))
    _get_redis().delete(_session_owner_key(session_id))
    err_msg = str(exc).strip() or "Unexpected processing error"
    _get_redis().publish(
      "events",
      json.dumps({
        "type": "error",
        "error_type": "Processing Failed",
        "message": err_msg,
        "meeting_id": session_id,
        "device_id": owner_device_id,
        "timestamp": datetime.now().isoformat(),
      }),
    )
    raise HTTPException(status_code=500, detail=err_msg) from exc


@router.get("/")
async def list_meetings(limit: int = 50, offset: int = 0, status: Optional[str] = None, current_actor: Optional[dict] = Depends(get_optional_actor)):
  _require_actor(current_actor)
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}  # type: ignore
  try:
    cur = conn.cursor()
    query = """
      SELECT m.*,
             EXISTS(SELECT 1 FROM summaries s WHERE s.meeting_id = m.id) AS has_summary,
             (SELECT COUNT(*) FROM segments seg WHERE seg.meeting_id = m.id) AS transcript_segments,
             (SELECT COUNT(*) FROM actions a
              WHERE a.meeting_id = m.id
                AND a.status = 'pending'
                AND lower(coalesce(trim(a.connector_target), '')) IN ('gmail', 'calendar')) AS pending_actions,
             (SELECT COUNT(*) FROM actions a
              WHERE a.meeting_id = m.id
                AND a.status = 'executed'
                AND lower(coalesce(trim(a.connector_target), '')) IN ('gmail', 'calendar')) AS executed_actions
      FROM meetings m
    """
    params: list[object] = []
    filters: list[str] = []
    scope_sql, scope_params = _meeting_access_filter(current_actor, alias="m")
    if scope_sql:
      filters.append(scope_sql)
      params.extend(scope_params)
    if status:
      filters.append("m.status = ?")
      params.append(status)
    # Exclude note recordings — those are surfaced via the Notes page / voice
    # agent (user_notes), not the Meetings list. NULL-safe so legacy rows
    # (created before the recording_mode column existed) still appear.
    filters.append("(m.recording_mode IS NULL OR m.recording_mode != 'note')")
    if filters:
      query += " WHERE " + " AND ".join(filters)
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


def _anthropic_message_text(resp) -> str:
  """Concatenate all text blocks (some models split long replies across blocks)."""
  parts: list[str] = []
  for block in getattr(resp, "content", None) or []:
    if getattr(block, "type", None) == "text":
      t = getattr(block, "text", None) or ""
      if t:
        parts.append(t)
  return "\n".join(parts).strip()


@router.post("/{meeting_id}/summarize")
async def summarize_meeting(meeting_id: str, force: bool = False, current_actor: Optional[dict] = Depends(get_optional_actor)):
  """Generate a concise meeting summary from the transcript using Claude."""
  _require_actor(current_actor)
  client = _get_anthropic_client()
  if not client:
    raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY is not configured on the server.")

  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()

    scope_sql, scope_params = _meeting_access_filter(current_actor)
    meeting_query = "SELECT * FROM meetings WHERE id = ?"
    meeting_params: list[object] = [meeting_id]
    if scope_sql:
      meeting_query += f" AND {scope_sql}"
      meeting_params.extend(scope_params)
    cur.execute(meeting_query, meeting_params)
    meeting = cur.fetchone()
    if not meeting:
      raise HTTPException(status_code=404, detail="Meeting not found")

    cur.execute("SELECT * FROM summaries WHERE meeting_id = ?", (meeting_id,))
    existing = cur.fetchone()
    if existing and not force:
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
          "You are summarizing an entire meeting from its transcript. First read and understand the "
          "WHOLE transcript, then write a single overall summary of the meeting and present that "
          "summary as bullet points. Someone who did not attend should be able to read the bullets "
          "and fully understand what the meeting was about and what came out of it.\n\n"

          "HOW TO SUMMARIZE:\n"
          "- Summarize the meeting as a whole. Do NOT convert the transcript line by line into bullets.\n"
          "- Synthesize: combine related discussion across the transcript into a few meaningful summary points.\n"
          "- Each bullet should capture a theme or key takeaway, not a single sentence someone said.\n"
          "- Condense the conversation: merge repeated or scattered points into one clear bullet.\n"
          "- Aim for a small set of high-level bullets that together tell the full story of the meeting "
          "(typically 4-10 bullets; use more only if the meeting genuinely covered many distinct topics).\n\n"

          "OUTPUT STYLE:\n"
          "- Write the summary as bullet points in `full_report`.\n"
          "- Start every bullet with \"- \" and put each bullet on its own line.\n"
          "- Order the bullets to follow the overall flow of the meeting so it reads as a coherent summary.\n"
          "- Keep the important specifics that appear: names, numbers, dates, decisions, outcomes, and follow-ups.\n\n"

          "STRICTLY AVOID:\n"
          "- Do NOT quote or paraphrase the transcript turn by turn.\n"
          "- Do NOT add meta commentary about the meeting itself, such as \"this was a brief meeting\", "
          "\"the transcript is short\", \"the discussion was limited\", or any similar filler.\n"
          "- Do NOT add an introduction, conclusion, headings, or generic phrases.\n"
          "- Do NOT restate the same point in different words.\n"
          "- Do NOT invent anything that is not supported by the transcript.\n\n"

          "MULTILINGUAL TRANSCRIPTS:\n"
          "- The transcript may contain English, Telugu, Hindi, other Indian languages, or code-switched speech.\n"
          "- Understand all languages from context, then write every bullet in clear English.\n"
          "- Preserve original names, quoted phrases, numbers, dates, product names, and technical terms accurately.\n"
          "- Do not drop a point just because it appears in Telugu, Hindi, or mixed-language speech.\n\n"

          "Return only valid JSON with this exact shape (no markdown fences outside the JSON). "
          "Put the entire summary in `full_report` as bullet lines, and leave `decisions`, "
          "`action_items`, `open_questions`, `risks_or_concerns`, and `topics` as empty arrays:\n"
          "{\n"
          "  \"report_title\": \"Short title for the meeting (max ~80 chars)\",\n"
          "  \"full_report\": \"- First bullet point\\n- Second bullet point\",\n"
          "  \"decisions\": [],\n"
          "  \"action_items\": [],\n"
          "  \"open_questions\": [],\n"
          "  \"risks_or_concerns\": [],\n"
          "  \"topics\": []\n"
          "}\n\n"

          f"Transcript:\n\n{transcript}"
  )

  model = os.getenv("AI_MODEL", "claude-sonnet-4-5-20250929")
  max_tokens = int(os.getenv("AI_REPORT_MAX_TOKENS", os.getenv("AI_MAX_TOKENS", "16384")))

  try:
    resp = client.messages.create(
      model=model,
      max_tokens=max_tokens,
      messages=[{"role": "user", "content": prompt}],
    )
    stop_reason = getattr(resp, "stop_reason", None)
    if stop_reason == "max_tokens":
      logger.warning(
        "Meeting report hit max_tokens (%s); raise AI_REPORT_MAX_TOKENS for longer output.",
        max_tokens,
      )
    text = _anthropic_message_text(resp)
    if not text:
      raise HTTPException(status_code=500, detail="Claude returned an empty message.")
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=500, detail=f"Claude API error: {exc}") from exc

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

  # Normalize LLM output (decisions/action_items may need coercion)
  data = _normalize_summary_data(data)

  report_body = _compose_stored_report_body(data)
  if not report_body:
    raise HTTPException(status_code=500, detail="Model returned an empty report (full_report).")

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
        report_body,
        json.dumps(data.get("action_items", [])),
        json.dumps(data.get("decisions", [])),
        json.dumps(data.get("topics", [])),
        data.get("sentiment", ""),
        datetime.now().isoformat(),
      ),
    )
    rt = (data.get("report_title") or "").strip()
    auto_title = (rt[:80] if rt else "") or _derive_title(report_body)
    if auto_title and (meeting.get("title", "").startswith("Meeting ") or not meeting.get("title")):
      cur.execute("UPDATE meetings SET status = 'completed', end_time = ?, title = ? WHERE id = ?", (datetime.now().isoformat(), auto_title, meeting_id))
    else:
      cur.execute("UPDATE meetings SET status = 'completed', end_time = ? WHERE id = ?", (datetime.now().isoformat(), meeting_id))
    conn.commit()
  finally:
    conn.close()

  # Create Gmail/Calendar agentic actions in the same request so clients (e.g. device UI)
  # see suggestions as soon as the summary response returns, without a second round-trip.
  user_for_actions = _resolve_user_id_for_post_summarize_actions(current_actor)
  if not user_for_actions:
    logger.info(
      "Skipping post-summarize agentic actions for meeting %s: sign in or connect integrations on one account",
      meeting_id,
    )
  else:
    try:
      from services.action_engine import generate_actions_for_meeting

      generate_actions_for_meeting(meeting_id, user_for_actions)
    except Exception as exc:
      logger.warning(
        "Agentic actions not generated after summarize for meeting %s: %s",
        meeting_id,
        exc,
      )

  # Persist meeting action_items as user_commitments so they surface on the Tasks screen.
  # Clear-user-owned items become active tasks; items with no named owner become tasks
  # tagged 'needs-review' (visible but flagged for user confirmation); items clearly
  # owned by someone else are skipped.
  uid_for_tasks = meeting.get("user_id") or user_for_actions
  if uid_for_tasks:
    try:
      user_names = _user_display_names(uid_for_tasks)
      from services.tasks_service import create_tasks_from_meeting

      task_summary = create_tasks_from_meeting(
        user_id=uid_for_tasks,
        meeting_id=meeting_id,
        meeting_title=meeting.get("title") or "",
        meeting_date=meeting.get("start_time") or "",
        action_items=data.get("action_items", []),
        user_display_names=user_names,
      )
      logger.info(
        "meeting %s -> tasks: created=%d needs_review=%d skipped_other=%d",
        meeting_id,
        task_summary.get("created_count", 0),
        task_summary.get("needs_review_count", 0),
        task_summary.get("skipped_other_owner_count", 0),
      )
    except Exception as exc:
      logger.warning(
        "Meeting -> tasks bridge failed for meeting %s: %s", meeting_id, exc
      )

  # Fix 2 (populate participants): extract unique participant names from action_items
  # and save them back to the meetings row so memory_search_meetings can filter on them.
  try:
    participant_names: list[str] = []
    for ai in (data.get("action_items") or []):
      if isinstance(ai, dict):
        for key in ("owner", "assignee", "assigned_to", "person"):
          val = (ai.get(key) or "").strip()
          if val and val not in participant_names:
            participant_names.append(val)
    if participant_names:
      import json as _json
      participants_json = _json.dumps(participant_names, ensure_ascii=False)
      _p_conn = get_connection()
      try:
        _p_conn.execute(
          "UPDATE meetings SET participants = ? WHERE id = ?",
          (participants_json, meeting_id),
        )
        _p_conn.commit()
      finally:
        _p_conn.close()
  except Exception:
    logger.debug("participants update after summarize failed", exc_info=True)

  try:
    from services.mem0_service import maybe_ingest_meeting_summary, maybe_ingest_meeting_sqlite_artifacts

    # Fix 5C: for device-uploaded meetings meeting.user_id may be empty.
    # Fall back to the resolved action-user (which already does a device->user lookup)
    # so Mem0 ingest is never silently skipped for device recordings.
    uid_mem = (meeting.get("user_id") or "").strip() or user_for_actions or _actor_user_id(current_actor)
    if not uid_mem:
      logger.warning("mem0 ingest skipped meeting_id=%s: could not resolve user_id", meeting_id)
    else:
      maybe_ingest_meeting_summary(uid_mem, meeting_id, report_body)
      maybe_ingest_meeting_sqlite_artifacts(uid_mem, meeting_id)
      # Fix 9: trigger background cross-meeting synthesis after each summarize.
      try:
        from services.analysis_service import run_post_meeting_analysis
        run_post_meeting_analysis(uid_mem, meeting_id)
      except Exception:
        pass
      # Trigger background user profile build (fire-and-forget, guarded by cooldown).
      try:
        from services.user_profiler import trigger_profile_build_bg
        trigger_profile_build_bg(uid_mem)
      except Exception:
        pass
  except Exception:
    logger.debug("mem0 ingest after summarize failed", exc_info=True)

  return {
    "status": "generated",
    "summary": report_body,
    "action_items": data.get("action_items", []),
    "decisions": data.get("decisions", []),
    "topics": data.get("topics", []),
    "sentiment": data.get("sentiment", ""),
    "report_title": (data.get("report_title") or "").strip(),
    "open_questions": data.get("open_questions", []),
    "risks_or_concerns": data.get("risks_or_concerns", []),
  }


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
    scope_sql, scope_params = _meeting_access_filter({"type": "user", "user": current_user})
    mq = "SELECT * FROM meetings WHERE id = ? AND " + scope_sql
    cur.execute(mq, [meeting_id] + scope_params)
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
async def get_meeting_audio(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
  """Stream the audio recording file for a meeting."""
  from fastapi.responses import FileResponse

  _require_actor(current_actor)
  conn = get_connection()
  try:
    cur = conn.cursor()
    scope_sql, scope_params = _meeting_access_filter(current_actor)
    query = "SELECT audio_path FROM meetings WHERE id = ?"
    query_params: list[object] = [meeting_id]
    if scope_sql:
      query += f" AND {scope_sql}"
      query_params.extend(scope_params)
    cur.execute(query, query_params)
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
async def export_meeting(meeting_id: str, fmt: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
  """Export a meeting as TXT or PDF."""
  from fastapi.responses import Response

  _require_actor(current_actor)
  if fmt not in ("txt", "pdf"):
    raise HTTPException(status_code=400, detail=f"Unsupported export format: {fmt}. Use 'txt' or 'pdf'.")

  conn = get_connection()
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
  try:
    cur = conn.cursor()

    scope_sql, scope_params = _meeting_access_filter(current_actor)
    meeting_query = "SELECT * FROM meetings WHERE id = ?"
    meeting_params: list[object] = [meeting_id]
    if scope_sql:
      meeting_query += f" AND {scope_sql}"
      meeting_params.extend(scope_params)
    cur.execute(meeting_query, meeting_params)
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
async def get_meeting(meeting_id: str, current_actor: Optional[dict] = Depends(get_optional_actor)):
  _require_actor(current_actor)
  conn = get_connection()
  conn.execute("PRAGMA foreign_keys = ON")
  conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}  # type: ignore
  try:
    cur = conn.cursor()

    scope_sql, scope_params = _meeting_access_filter(current_actor)
    meeting_query = "SELECT * FROM meetings WHERE id = ?"
    meeting_params: list[object] = [meeting_id]
    if scope_sql:
      meeting_query += f" AND {scope_sql}"
      meeting_params.extend(scope_params)
    cur.execute(meeting_query, meeting_params)
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
      # Surface the DB ``generated_at`` so the device-ui footer can
      # render "Created: <date>" instead of falling back to the
      # meeting's start_time.
      "generated_at": summary_row.get("generated_at"),
    })

  local_summary = None
  if local_summary_row:
    local_summary = _normalize_summary_data({
      "summary": local_summary_row["summary"],
      "action_items": json.loads(local_summary_row["action_items"] or "[]"),
      "decisions": json.loads(local_summary_row["decisions"] or "[]"),
      "topics": json.loads(local_summary_row["topics"] or "[]"),
      "sentiment": local_summary_row["sentiment"],
      "generated_at": local_summary_row.get("generated_at"),
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

