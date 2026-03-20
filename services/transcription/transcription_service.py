import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import redis

from database import DB_PATH, init_database, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.transcription")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
TEMP_SEGMENTS_DIR = Path(os.getenv("TEMP_SEGMENTS_DIR", "/data/audio/temp"))
# Docker image builds whisper.cpp under /app/whisper.cpp.
# (Native mini-PC installs can still override WHISPER_ROOT via env.)
DEFAULT_WHISPER_ROOT = Path(os.getenv("WHISPER_ROOT", "/app/whisper.cpp"))


class TranscriptionService:
  """
  Consume completed recordings, run Whisper.cpp once on the final audio file,
  and persist structured transcript segments into SQLite.
  """

  def __init__(self) -> None:
    self.redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

    init_database()

    self.whisper_bin = os.getenv(
      "WHISPER_BIN",
      str(DEFAULT_WHISPER_ROOT / "build" / "bin" / "whisper-cli"),
    )
    self.model_path = os.getenv(
      "WHISPER_MODEL_PATH",
      str(DEFAULT_WHISPER_ROOT / "models" / "ggml-medium.bin"),
    )

    logger.info("Service initialized, model=%s, DB=%s", self.model_path, DB_PATH)

  # --- Whisper wrapper -------------------------------------------------

  def _run_whisper(self, audio_path: str, extra_args: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
    cmd = [
      self.whisper_bin,
      "-m", self.model_path,
      "-f", audio_path,
      *extra_args,
      "--threads", os.getenv("WHISPER_THREADS", "4"),
    ]

    logger.info("Running: %s", " ".join(cmd))
    try:
      result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
      )
    except subprocess.TimeoutExpired:
      logger.error("Whisper timed out for %s", audio_path)
      return None

    if result.stdout:
      for line in result.stdout.strip().splitlines()[-10:]:
        logger.debug("whisper stdout: %s", line)
    if result.stderr:
      for line in result.stderr.strip().splitlines()[-10:]:
        logger.debug("whisper stderr: %s", line)
    logger.info("whisper exit code: %d", result.returncode)
    return result

  def transcribe_with_whisper(self, audio_path: str) -> dict | None:
    logger.info("Processing %s", audio_path)
    path = Path(audio_path)
    if not path.exists():
      logger.error("Audio file not found: %s", audio_path)
      return None

    output_base = str(path.with_suffix(""))
    txt_path = Path(output_base + ".txt")
    srt_path = Path(output_base + ".srt")

    result = self._run_whisper(audio_path, ["-of", output_base, "-otxt", "-osrt"], timeout=600)
    if result is None or result.returncode != 0:
      return None

    if not txt_path.exists() or not srt_path.exists():
      logger.error("Expected: %s and %s", txt_path, srt_path)
      return None

    full_text = txt_path.read_text(encoding="utf-8", errors="ignore")
    segments = self._parse_srt(srt_path)

    logger.info("Completed with %d segments", len(segments))
    return {"full_text": full_text, "segments": segments}

  def _parse_srt(self, path: Path) -> list[dict]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    blocks = content.strip().split("\n\n")
    segments: list[dict] = []

    for block in blocks:
      lines = block.splitlines()
      if len(lines) < 3:
        continue
      try:
        num = int(lines[0])
      except ValueError:
        continue

      timestamp_line = lines[1]
      try:
        start_str, end_str = timestamp_line.split(" --> ")
      except ValueError:
        continue

      start_sec = self._srt_time_to_seconds(start_str)
      end_sec = self._srt_time_to_seconds(end_str)
      text = "\n".join(lines[2:]).strip()

      segments.append(
        {
          "segment_num": num,
          "start_time": start_sec,
          "end_time": end_sec,
          "text": text,
        }
      )
    return segments

  def _srt_time_to_seconds(self, ts: str) -> float:
    hh, mm, rest = ts.replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)

  # --- Persistence -----------------------------------------------------

  def _publish_event(self, payload: dict) -> None:
    self.redis_client.publish("events", json.dumps(payload))

  def _ensure_meeting_record(self, meeting_id: str, audio_path: str | None, status: str = "recording") -> None:
    conn = get_connection()
    try:
      cur = conn.cursor()
      now = datetime.now().isoformat()
      cur.execute(
        """
        INSERT OR IGNORE INTO meetings
          (id, title, start_time, audio_path, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
          meeting_id,
          f"Meeting {meeting_id}",
          now,
          audio_path,
          status,
          now,
        ),
      )
      cur.execute(
        """
        UPDATE meetings
        SET status = ?, audio_path = COALESCE(?, audio_path)
        WHERE id = ?
        """,
        (status, audio_path, meeting_id),
      )
      cur.execute(
        """
        INSERT OR IGNORE INTO processing_state
          (meeting_id, updated_at)
        VALUES (?, ?)
        """,
        (meeting_id, now),
      )
      conn.commit()
    finally:
      conn.close()

  def _set_meeting_status(self, meeting_id: str, status: str) -> None:
    conn = get_connection()
    try:
      conn.execute("UPDATE meetings SET status = ? WHERE id = ?", (status, meeting_id))
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

  def _get_processing_state(self, meeting_id: str) -> dict:
    conn = get_connection()
    conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    try:
      cur = conn.cursor()
      cur.execute("SELECT * FROM processing_state WHERE meeting_id = ?", (meeting_id,))
      return cur.fetchone() or {}
    finally:
      conn.close()

  def _save_transcription(self, meeting_id: str, transcription: dict) -> None:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
      for seg in transcription["segments"]:
        cur.execute(
          """
          INSERT OR REPLACE INTO segments
            (meeting_id, segment_num, start_time, end_time, text)
          VALUES (?, ?, ?, ?, ?)
          """,
          (
            meeting_id,
            seg["segment_num"],
            seg["start_time"],
            seg["end_time"],
            seg["text"],
          ),
        )
      last_segment = transcription["segments"][-1]["segment_num"] if transcription["segments"] else -1
      cur.execute("UPDATE meetings SET status = 'transcribed' WHERE id = ?", (meeting_id,))
      cur.execute(
        """
        UPDATE processing_state
        SET last_transcribed_segment = ?, last_enqueued_segment = ?, updated_at = ?
        WHERE meeting_id = ?
        """,
        (last_segment, last_segment, datetime.now().isoformat(), meeting_id),
      )
      conn.commit()
    finally:
      conn.close()

  def _cleanup_temp_segments(self, meeting_id: str) -> None:
    session_dir = TEMP_SEGMENTS_DIR / meeting_id
    if not session_dir.exists():
      return

    for seg in session_dir.glob("segment_*.wav"):
      seg.unlink(missing_ok=True)
    for txt in session_dir.glob("segment_*.txt"):
      txt.unlink(missing_ok=True)
    for srt in session_dir.glob("segment_*.srt"):
      srt.unlink(missing_ok=True)
    try:
      session_dir.rmdir()
    except OSError:
      pass

  def _handle_recording_started(self, meeting_id: str) -> None:
    self._ensure_meeting_record(meeting_id, None, status="recording")

  def _handle_recording_stopped(self, meeting_id: str, audio_path: str | None) -> bool:
    if not audio_path:
      logger.warning("No final audio path available for %s", meeting_id)
      return False

    self._ensure_meeting_record(meeting_id, audio_path, status="transcribing")
    self._update_processing_state(meeting_id, recording_stopped=1)

    logger.info("Running post-meeting transcription for %s", meeting_id)
    transcription = self.transcribe_with_whisper(audio_path)
    if not transcription:
      return False

    self._save_transcription(meeting_id, transcription)
    return True

  # --- Event loop ------------------------------------------------------

  def run(self) -> None:
    logger.info("Service started, waiting for recording events...")

    pubsub = self.redis_client.pubsub()
    pubsub.subscribe("events")

    for message in pubsub.listen():
      if message["type"] != "message":
        continue

      try:
        event = json.loads(message["data"])
      except json.JSONDecodeError:
        continue

      def set_recording_idle() -> None:
        self.redis_client.set("recording_state", "idle")

      event_type = event.get("type")
      if event_type == "recording_started":
        meeting_id = event.get("session_id")
        if meeting_id:
          self._handle_recording_started(meeting_id)
        continue

      if event_type != "recording_stopped":
        continue

      meeting_id = event.get("session_id")
      audio_path = event.get("path")

      if not meeting_id:
        logger.warning("recording_stopped missing session_id")
        set_recording_idle()
        continue

      if not self._handle_recording_stopped(meeting_id, audio_path):
        self._set_meeting_status(meeting_id, "transcription_failed")
        set_recording_idle()
        continue

      state = self._get_processing_state(meeting_id)
      self._publish_event(
        {
          "type": "transcription_complete",
          "meeting_id": meeting_id,
          "last_segment_num": state.get("last_transcribed_segment", -1),
          "timestamp": datetime.now().isoformat(),
        }
      )
      self._cleanup_temp_segments(meeting_id)
      set_recording_idle()


if __name__ == "__main__":
  service = TranscriptionService()
  service.run()
