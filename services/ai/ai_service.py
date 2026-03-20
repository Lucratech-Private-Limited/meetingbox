import json
import logging
import os
from datetime import datetime
from typing import Any

import httpx
import redis

from database import DB_PATH, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.ai")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "phi3:mini")
SUMMARY_BATCH_SIZE = int(os.getenv("SUMMARY_BATCH_SIZE", "5"))

# Set USE_LOCAL_LLM=false (and provide ANTHROPIC_API_KEY) to fall back to Claude.
USE_LOCAL_LLM = os.getenv("USE_LOCAL_LLM", "true").lower() not in ("false", "0", "no")


class AIService:
  """
  Consume completed transcripts from SQLite and generate structured
  summaries, then persist them for the web UI.

  Default: uses the local Ollama LLM (phi3:mini or configured LOCAL_LLM_MODEL).
  Fallback: Claude API when USE_LOCAL_LLM=false and ANTHROPIC_API_KEY is set.
  """

  def __init__(self) -> None:
    self.redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

    if USE_LOCAL_LLM:
      logger.info("Summarization mode: LOCAL LLM (%s @ %s)", LOCAL_LLM_MODEL, OLLAMA_HOST)
      self._claude_client = None
    else:
      try:
        from anthropic import Anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
          logger.warning("USE_LOCAL_LLM=false but ANTHROPIC_API_KEY is not set — falling back to local LLM.")
          self._claude_client = None
        else:
          self._claude_client = Anthropic(api_key=api_key)
          self._claude_model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
          self._claude_max_tokens = int(os.getenv("AI_MAX_TOKENS", "2000"))
          logger.info("Summarization mode: Claude API (%s)", self._claude_model)
      except ImportError:
        logger.warning("anthropic package not installed — falling back to local LLM.")
        self._claude_client = None

    logger.info("Service initialized, DB=%s", DB_PATH)

  # ------------------------------------------------------------------
  # Shared helpers
  # ------------------------------------------------------------------

  def _publish_event(self, payload: dict) -> None:
    self.redis_client.publish("events", json.dumps(payload))

  def _normalize_summary_data(self, data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
      return {
        "summary": "",
        "discussion_points": [],
        "decisions": [],
        "action_items": [],
        "topics": [],
        "sentiment": "",
      }

    def _ensure_str_list(value: Any) -> list[str]:
      items = value if isinstance(value, list) else []
      return [str(item).strip() for item in items if str(item).strip()]

    action_items = data.get("action_items", [])
    if not isinstance(action_items, list):
      action_items = []

    normalized_actions: list[dict[str, Any]] = []
    for item in action_items:
      if not isinstance(item, dict):
        continue
      normalized_actions.append(
        {
          "task": str(item.get("task", "")).strip(),
          "assignee": item.get("assignee"),
          "due_date": item.get("due_date"),
          "type": str(item.get("type", "task")).strip() or "task",
        }
      )

    return {
      "summary": str(data.get("summary", "")).strip(),
      "discussion_points": _ensure_str_list(data.get("discussion_points")),
      "decisions": _ensure_str_list(data.get("decisions")),
      "action_items": [item for item in normalized_actions if item["task"]],
      "topics": _ensure_str_list(data.get("topics")),
      "sentiment": str(data.get("sentiment", "")).strip(),
    }

  def _fetch_processing_state(self, meeting_id: str) -> dict[str, Any]:
    self._ensure_processing_state(meeting_id)
    conn = get_connection()
    conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    try:
      cur = conn.cursor()
      cur.execute("SELECT * FROM processing_state WHERE meeting_id = ?", (meeting_id,))
      return cur.fetchone() or {}
    finally:
      conn.close()

  def _ensure_processing_state(self, meeting_id: str) -> None:
    conn = get_connection()
    try:
      conn.execute(
        """
        INSERT OR IGNORE INTO processing_state
          (meeting_id, updated_at)
        VALUES (?, ?)
        """,
        (meeting_id, datetime.now().isoformat()),
      )
      conn.commit()
    finally:
      conn.close()

  def _update_processing_state(self, meeting_id: str, **fields: int | str) -> None:
    if not fields:
      return
    fields["updated_at"] = datetime.now().isoformat()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    conn = get_connection()
    try:
      conn.execute(
        f"UPDATE processing_state SET {assignments} WHERE meeting_id = ?",
        (*values, meeting_id),
      )
      conn.commit()
    finally:
      conn.close()

  def _fetch_existing_local_summary(self, meeting_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    try:
      cur = conn.cursor()
      cur.execute(
        """
        SELECT *
        FROM local_summaries
        WHERE meeting_id = ?
        """,
        (meeting_id,),
      )
      row = cur.fetchone()
    finally:
      conn.close()

    if not row:
      return None

    return self._normalize_summary_data(
      {
        "summary": row.get("summary", ""),
        "discussion_points": json.loads(row.get("discussion_points") or "[]"),
        "decisions": json.loads(row.get("decisions") or "[]"),
        "action_items": json.loads(row.get("action_items") or "[]"),
        "topics": json.loads(row.get("topics") or "[]"),
        "sentiment": row.get("sentiment", ""),
        "last_segment_num": row.get("last_segment_num", -1),
        "is_final": row.get("is_final", 0),
      }
    ) | {
      "last_segment_num": int(row.get("last_segment_num", -1) or -1),
      "is_final": int(row.get("is_final", 0) or 0),
    }

  def _fetch_transcript(self, meeting_id: str, after_segment: int = -1) -> tuple[str, int, int]:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute(
        """
        SELECT segment_num, start_time, text
        FROM segments
        WHERE meeting_id = ?
          AND segment_num > ?
          AND TRIM(COALESCE(text, '')) <> ''
        ORDER BY segment_num
        """,
        (meeting_id, after_segment),
      )
      rows = cur.fetchall()
    finally:
      conn.close()

    parts: list[str] = []
    last_segment_num = after_segment
    for seg_num, start_time, text in rows:
      mins = int((start_time or 0) // 60)
      secs = int((start_time or 0) % 60)
      parts.append(f"[{mins:02d}:{secs:02d}] Segment {seg_num}: {text}")
      last_segment_num = seg_num
    return "\n\n".join(parts), last_segment_num, len(rows)

  def _build_prompt(self, transcript: str) -> str:
    return (
      "You are analyzing a meeting transcript. Please provide:\n\n"
      "1. Summary (2-3 sentences)\n"
      "2. Key discussion points (3-5 bullets)\n"
      "3. Decisions made\n"
      "4. Action items with assignees if available. Each action item MUST include a "
      '"type" field with one of these values:\n'
      '   - "email_draft" — for follow-ups that are naturally an email\n'
      '   - "calendar_invite" — for next meetings or time-blocking commitments\n'
      '   - "task" — for general human to-do items\n'
      "   Only include action items that are explicitly grounded in the meeting.\n"
      "5. 3-5 topic hashtags\n"
      "6. Overall sentiment (single word or short phrase)\n\n"
      "Return **only** valid JSON with no additional text, in this exact shape:\n"
      '{\n'
      '  "summary": "...",\n'
      '  "discussion_points": ["...", "..."],\n'
      '  "decisions": ["...", "..."],\n'
      '  "action_items": [{"task": "...", "assignee": "...", "due_date": "...", "type": "task"}],\n'
      '  "topics": ["#topic1", "#topic2"],\n'
      '  "sentiment": "Productive"\n'
      "}\n\n"
      f"Transcript:\n\n{transcript}"
    )

  def _build_update_prompt(self, current_summary: dict[str, Any], transcript_delta: str) -> str:
    return (
      "You are updating an existing structured meeting summary.\n\n"
      "Merge the new transcript chunk into the current summary. Keep prior details that still hold, "
      "add any new decisions or action items, and remove contradictions if the new transcript clarifies them.\n\n"
      "Return only valid JSON in this exact shape:\n"
      '{\n'
      '  "summary": "...",\n'
      '  "discussion_points": ["...", "..."],\n'
      '  "decisions": ["...", "..."],\n'
      '  "action_items": [{"task": "...", "assignee": "...", "due_date": "...", "type": "task"}],\n'
      '  "topics": ["#topic1", "#topic2"],\n'
      '  "sentiment": "Productive"\n'
      "}\n\n"
      f"Current summary JSON:\n{json.dumps(current_summary, ensure_ascii=True)}\n\n"
      f"New transcript chunk:\n\n{transcript_delta}"
    )

  def _parse_json(self, text: str, source: str) -> dict | None:
    if "```json" in text:
      start = text.find("```json") + len("```json")
      end = text.find("```", start)
      json_str = text[start:end].strip()
    else:
      json_str = text.strip()
    try:
      return json.loads(json_str)
    except json.JSONDecodeError:
      logger.error("Failed to parse JSON from %s response: %s", source, json_str[:200])
      return None

  # ------------------------------------------------------------------
  # Local LLM (Ollama)
  # ------------------------------------------------------------------

  def _generate_summary_local(self, prompt: str, meeting_id: str) -> dict | None:
    try:
      resp = httpx.post(
        f"{OLLAMA_HOST}/api/generate",
        json={
          "model": LOCAL_LLM_MODEL,
          "prompt": prompt,
          "stream": False,
          "options": {"temperature": 0.3, "num_predict": 2000},
        },
        timeout=300.0,
      )
      resp.raise_for_status()
      text = resp.json().get("response", "")
    except httpx.ConnectError:
      logger.error("Cannot connect to Ollama at %s — is the container running?", OLLAMA_HOST)
      return None
    except Exception:
      logger.exception("Ollama request failed for %s", meeting_id)
      return None

    data = self._parse_json(text, "Ollama")
    return self._normalize_summary_data(data)

  def _save_summary_local(self, meeting_id: str, summary: dict, last_segment_num: int, is_final: bool) -> None:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute(
        """
        INSERT OR REPLACE INTO local_summaries
          (meeting_id, summary, discussion_points, action_items, decisions, topics, sentiment, model_name, last_segment_num, is_final, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
          meeting_id,
          summary.get("summary", ""),
          json.dumps(summary.get("discussion_points", [])),
          json.dumps(summary.get("action_items", [])),
          json.dumps(summary.get("decisions", [])),
          json.dumps(summary.get("topics", [])),
          summary.get("sentiment", ""),
          LOCAL_LLM_MODEL,
          last_segment_num,
          1 if is_final else 0,
          datetime.now().isoformat(),
        ),
      )
      if is_final:
        cur.execute(
          "UPDATE meetings SET status = 'completed', end_time = ? WHERE id = ?",
          (datetime.now().isoformat(), meeting_id),
        )
      else:
        cur.execute("UPDATE meetings SET status = 'summarizing' WHERE id = ?", (meeting_id,))
      conn.commit()
    finally:
      conn.close()

    self._update_processing_state(meeting_id, last_summarized_segment=last_segment_num)
  def _summarize_incrementally(self, meeting_id: str, finalize: bool) -> dict | None:
    self._ensure_processing_state(meeting_id)
    state = self._fetch_processing_state(meeting_id)
    existing = self._fetch_existing_local_summary(meeting_id)
    last_summarized = -1
    if existing:
      last_summarized = int(existing.get("last_segment_num", -1) or -1)
    else:
      last_summarized = int(state.get("last_summarized_segment", -1) or -1)

    transcript_delta, last_segment_num, new_count = self._fetch_transcript(meeting_id, after_segment=last_summarized)
    if not transcript_delta:
      if finalize and existing:
        self._save_summary_local(meeting_id, existing, last_summarized, True)
        return existing
      return None

    if not finalize and new_count < SUMMARY_BATCH_SIZE:
      return None

    if existing:
      prompt = self._build_update_prompt(existing, transcript_delta)
    else:
      prompt = self._build_prompt(transcript_delta)

    summary = self._generate_summary_local(prompt, meeting_id)
    if not summary:
      return None

    self._save_summary_local(meeting_id, summary, last_segment_num, finalize)
    return summary

  # ------------------------------------------------------------------
  # Claude fallback
  # ------------------------------------------------------------------

  def _generate_summary_claude(self, meeting_id: str) -> dict | None:
    if not self._claude_client:
      return None

    transcript, _, _ = self._fetch_transcript(meeting_id)
    if not transcript:
      logger.warning("No transcript found for %s", meeting_id)
      return None

    prompt = self._build_prompt(transcript)
    try:
      resp = self._claude_client.messages.create(
        model=self._claude_model,
        max_tokens=self._claude_max_tokens,
        messages=[{"role": "user", "content": prompt}],
      )
      text = resp.content[0].text
    except Exception:
      logger.exception("Error calling Claude for %s", meeting_id)
      return None

    return self._normalize_summary_data(self._parse_json(text, "Claude"))

  def _save_summary_claude(self, meeting_id: str, summary: dict) -> None:
    conn = get_connection()
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
          summary.get("summary", ""),
          json.dumps(summary.get("action_items", [])),
          json.dumps(summary.get("decisions", [])),
          json.dumps(summary.get("topics", [])),
          summary.get("sentiment", ""),
          datetime.now().isoformat(),
        ),
      )
      cur.execute(
        "UPDATE meetings SET status = 'completed', end_time = ? WHERE id = ?",
        (datetime.now().isoformat(), meeting_id),
      )
      conn.commit()
    finally:
      conn.close()

  # ------------------------------------------------------------------
  # Main loop
  # ------------------------------------------------------------------

  def run(self) -> None:
    logger.info("Service started, waiting for transcription events...")
    pubsub = self.redis_client.pubsub()
    pubsub.subscribe("events")

    for message in pubsub.listen():
      if message["type"] != "message":
        continue
      try:
        event = json.loads(message["data"])
      except json.JSONDecodeError:
        continue

      event_type = event.get("type")
      if event_type not in {"transcription_update", "transcription_complete", "summary_requested"}:
        continue

      meeting_id = event.get("meeting_id")
      if not meeting_id:
        continue

      if event_type == "transcription_update":
        state = self._fetch_processing_state(meeting_id)
        last_enqueued = int(state.get("last_enqueued_segment", -1) or -1)
        last_transcribed = int(state.get("last_transcribed_segment", -1) or -1)
        last_summarized = int(state.get("last_summarized_segment", -1) or -1)

        if last_enqueued > last_transcribed:
          logger.info("Skipping draft summary for %s while ASR backlog exists", meeting_id)
          continue
        if last_transcribed - last_summarized < SUMMARY_BATCH_SIZE:
          continue

        logger.info("Updating rolling summary for %s", meeting_id)
        summary = self._summarize_incrementally(meeting_id, finalize=False)
        if not summary:
          continue

        self._publish_event(
          {
            "type": "summary_progress",
            "meeting_id": meeting_id,
            "last_segment_num": self._fetch_processing_state(meeting_id).get("last_summarized_segment", -1),
            "timestamp": datetime.now().isoformat(),
          }
        )
        continue

      if event_type == "summary_requested":
        logger.info("Manual summary requested for %s", meeting_id)
        summary = self._summarize_incrementally(meeting_id, finalize=True)
        if not summary:
          logger.warning("Manual summary generation failed for %s", meeting_id)
          continue
        self._publish_event(
          {
            "type": "summary_complete",
            "meeting_id": meeting_id,
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
          }
        )
        continue

      logger.info("Finalizing summary for %s", meeting_id)

      if USE_LOCAL_LLM or not self._claude_client:
        summary = self._summarize_incrementally(meeting_id, finalize=True)
      else:
        summary = self._generate_summary_claude(meeting_id)
        if summary:
          self._save_summary_claude(meeting_id, summary)

      if not summary:
        logger.warning("Summary generation failed for %s", meeting_id)
        continue

      self._publish_event(
        {
          "type": "summary_complete",
          "meeting_id": meeting_id,
          "summary": summary,
          "timestamp": datetime.now().isoformat(),
        }
      )


if __name__ == "__main__":
  service = AIService()
  service.run()
