import json
import logging
import os
import subprocess
import wave
from datetime import datetime
from pathlib import Path

import redis

from database import DB_PATH, init_database, get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.transcription")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
TEMP_SEGMENTS_DIR = Path(os.getenv("TEMP_SEGMENTS_DIR", "/data/audio/temp"))
DEFAULT_WHISPER_ROOT = Path(os.getenv("WHISPER_ROOT", "/opt/meetingbox/runtime/whisper.cpp"))


class TranscriptionService:
  """
  Consume completed recordings, run Whisper.cpp (medium model) on them,
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

  def transcribe_segment_text(self, audio_path: str) -> str | None:
    logger.info("Processing segment %s", audio_path)
    path = Path(audio_path)
    if not path.exists():
      logger.error("Segment audio file not found: %s", audio_path)
      return None

    output_base = str(path.with_suffix(""))
    txt_path = Path(output_base + ".txt")
    result = self._run_whisper(audio_path, ["-of", output_base, "-otxt"], timeout=180)
    if result is None or result.returncode != 0:
      return None
    if not txt_path.exists():
      logger.error("Expected transcript output missing: %s", txt_path)
      return None

    try:
      return txt_path.read_text(encoding="utf-8", errors="ignore").strip()
    finally:
      txt_path.unlink(missing_ok=True)

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
    if result is None:
      return None

    if result.returncode != 0:
      return None

    parent = path.parent
    found_files = sorted(parent.glob(path.stem + ".*"))
    logger.debug("Files matching %s.*: %s", path.stem, [str(f) for f in found_files])

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

  def _segment_exists(self, meeting_id: str, segment_num: int) -> bool:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute(
        "SELECT 1 FROM segments WHERE meeting_id = ? AND segment_num = ?",
        (meeting_id, segment_num),
      )
      return cur.fetchone() is not None
    finally:
      conn.close()

  def _get_next_segment_start(self, meeting_id: str) -> float:
    conn = get_connection()
    try:
      cur = conn.cursor()
      cur.execute("SELECT COALESCE(MAX(end_time), 0) FROM segments WHERE meeting_id = ?", (meeting_id,))
      row = cur.fetchone()
      return float(row[0] or 0)
    finally:
      conn.close()

  def _audio_duration_seconds(self, audio_path: str) -> float:
    with wave.open(audio_path, "rb") as wav_file:
      frame_rate = wav_file.getframerate() or 1
      return wav_file.getnframes() / frame_rate

  def _save_incremental_segment(
    self,
    meeting_id: str,
    segment_num: int,
    start_time: float,
    end_time: float,
    text: str,
  ) -> None:
    conn = get_connection()
    try:
      conn.execute(
        """
        INSERT OR REPLACE INTO segments
          (meeting_id, segment_num, start_time, end_time, text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (meeting_id, segment_num, start_time, end_time, text),
      )
      conn.execute("UPDATE meetings SET status = 'transcribing' WHERE id = ?", (meeting_id,))
      conn.commit()
    finally:
      conn.close()

  def _save_transcription(self, meeting_id: str, transcription: dict) -> None:
    conn = get_connection()
    try:
      cur = conn.cursor()
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
        SET last_transcribed_segment = ?, last_enqueued_segment = MAX(last_enqueued_segment, ?), updated_at = ?
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

  def _handle_audio_segment(self, event: dict) -> None:
    meeting_id = event.get("session_id")
    segment_num = event.get("segment_num")
    audio_path = event.get("path")
    if meeting_id is None or segment_num is None or not audio_path:
      logger.warning("audio_segments missing session_id, segment_num, or path")
      return

    segment_num = int(segment_num)
    self._ensure_meeting_record(meeting_id, None, status="transcribing")

    state = self._get_processing_state(meeting_id)
    last_enqueued = int(state.get("last_enqueued_segment", -1) or -1)
    if segment_num > last_enqueued:
      self._update_processing_state(meeting_id, last_enqueued_segment=segment_num)

    if self._segment_exists(meeting_id, segment_num):
      logger.info("Skipping already transcribed segment %s/%d", meeting_id, segment_num)
      if segment_num > int(state.get("last_transcribed_segment", -1) or -1):
        self._update_processing_state(meeting_id, last_transcribed_segment=segment_num)
      return

    text = self.transcribe_segment_text(audio_path)
    if text is None:
      logger.warning("Incremental transcription failed for %s/%d", meeting_id, segment_num)
      return

    start_time = self._get_next_segment_start(meeting_id)
    try:
      duration = self._audio_duration_seconds(audio_path)
    except wave.Error:
      logger.exception("Failed reading audio duration for %s", audio_path)
      duration = 0.0
    end_time = start_time + duration
    self._save_incremental_segment(meeting_id, segment_num, start_time, end_time, text)
    self._update_processing_state(meeting_id, last_transcribed_segment=segment_num)

    if text.strip():
      self._publish_event(
        {
          "type": "transcription_update",
          "meeting_id": meeting_id,
          "segment_num": segment_num,
          "text": text.strip(),
          "start_time": start_time,
          "end_time": end_time,
          "timestamp": datetime.now().isoformat(),
        }
      )

  def _finalize_incremental_transcription(self, meeting_id: str, audio_path: str | None) -> bool:
    state = self._get_processing_state(meeting_id)
    last_transcribed = int(state.get("last_transcribed_segment", -1) or -1)

    if last_transcribed >= 0:
      return True

    if not audio_path:
      logger.warning("No audio path or incremental transcript for %s", meeting_id)
      return False

    logger.info("No incremental transcript available for %s; using full-file transcription", meeting_id)
    transcription = self.transcribe_with_whisper(audio_path)
    if not transcription:
      return False
    self._save_transcription(meeting_id, transcription)
    return True

  # --- Event loop ------------------------------------------------------

  def run(self) -> None:
    logger.info("Service started, waiting for recording and segment events...")

    pubsub = self.redis_client.pubsub()
    pubsub.subscribe("events", "audio_segments")

    for message in pubsub.listen():
      if message["type"] != "message":
        continue

      try:
        event = json.loads(message["data"])
      except json.JSONDecodeError:
        continue

      channel = message.get("channel")

      def set_recording_idle() -> None:
        self.redis_client.set("recording_state", "idle")

      if channel == "audio_segments":
        self._handle_audio_segment(event)
        continue

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

      self._ensure_meeting_record(meeting_id, audio_path, status="finalizing")
      self._update_processing_state(meeting_id, recording_stopped=1)

      if not self._finalize_incremental_transcription(meeting_id, audio_path):
        self._set_meeting_status(meeting_id, "transcription_failed")
        set_recording_idle()
        continue

      state = self._get_processing_state(meeting_id)
      self._set_meeting_status(meeting_id, "transcribed")
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
