import json
import logging
import os
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import pyaudio
import redis
import webrtcvad
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.audio")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")


class AudioCaptureService:
  """
  Capture audio from the USB mic array, segment it using VAD, and
  publish segment metadata + recording lifecycle events via Redis.
  """

  def __init__(self, config_path: str = "config.yaml") -> None:
    with open(config_path, "r") as f:
      self.config = yaml.safe_load(f)

    self.TARGET_RATE = self.config["audio"]["sample_rate"]  # 16000 for Whisper
    self.CHANNELS = self.config["audio"]["channels"]
    self.FORMAT = pyaudio.paInt16

    self.is_recording = False
    self.current_session_id: str | None = None
    self._recording_thread: object | None = None  # threading.Thread

    self.vad = webrtcvad.Vad(self.config["vad"]["aggressiveness"])

    self.redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)

    storage_cfg = self.config.get("storage", {})
    self.temp_dir = Path(os.getenv("TEMP_SEGMENTS_DIR", storage_cfg.get("temp_dir", "/data/audio/temp")))
    self.recordings_dir = Path(os.getenv("RECORDINGS_DIR", storage_cfg.get("recordings_dir", "/data/audio/recordings")))

    self.audio = pyaudio.PyAudio()
    self.stream: pyaudio.Stream | None = None

    # Actual capture rate — may differ from TARGET_RATE if the device
    # doesn't support 16kHz. We resample to TARGET_RATE before saving.
    self.RATE = self.TARGET_RATE
    self.CHUNK = self.config["audio"]["chunk_size"]

    logger.info("Initialized - target %dHz, %dch", self.TARGET_RATE, self.CHANNELS)

  # --- Device handling -------------------------------------------------

  def find_mic_device(self) -> int | None:
    """
    Auto-detect the best available input device.

    Strategy (no hardcoded mic names):
      1. Enumerate all input-capable devices.
      2. Test each one to see if it actually supports our sample rate.
      3. Prefer USB / external devices over built-in ones (they're almost
         always the meeting mic).
      4. If nothing passes the sample-rate test, return None (system default).

    This way any USB mic -- ReSpeaker, Jabra, Samson, cheap USB dongle,
    etc. -- works automatically without code changes.
    """
    info = self.audio.get_host_api_info_by_index(0)
    num_devices = info.get("deviceCount", 0)

    # Collect every input device with its metadata
    candidates: list[dict] = []
    for i in range(num_devices):
      device_info = self.audio.get_device_info_by_host_api_device_index(0, i)
      if device_info.get("maxInputChannels", 0) <= 0:
        continue
      name = device_info.get("name", "")
      candidates.append({"index": i, "name": name, "info": device_info})

    if not candidates:
      logger.warning("No input devices found at all")
      return None

    logger.info("Found %d input device(s):", len(candidates))
    for c in candidates:
      logger.info("  [%d] %s  (rate=%s)", c['index'], c['name'], c['info'].get('defaultSampleRate'))

    # Test which devices actually support our sample rate
    def supports_sample_rate(dev: dict) -> bool:
      try:
        ok = self.audio.is_format_supported(
          self.RATE,
          input_device=dev["index"],
          input_channels=self.CHANNELS,
          input_format=self.FORMAT,
        )
        return bool(ok)
      except (ValueError, OSError):
        return False

    # Classify into USB/external vs built-in
    usb_keywords = ["usb", "uac", "respeaker", "jabra", "samson", "blue",
                     "yeti", "rode", "fifine", "tonor", "boya", "maono",
                     "external", "webcam", "camera"]
    builtin_keywords = ["hdmi", "built-in", "bcm", "broadcom", "headphone",
                         "analog", "spdif", "iec958"]

    def is_likely_usb(name: str) -> bool:
      low = name.lower()
      if any(kw in low for kw in usb_keywords):
        return True
      if any(kw in low for kw in builtin_keywords):
        return False
      # Unknown device -- treat as external (better to try it than skip it)
      return True

    # Sort: USB/external first, then built-in
    candidates.sort(key=lambda c: (0 if is_likely_usb(c["name"]) else 1))

    # Pick the first candidate that supports our target sample rate
    for c in candidates:
      if supports_sample_rate(c):
        label = "USB/external" if is_likely_usb(c["name"]) else "built-in"
        logger.info("Selected device %d: %s (%s, %dHz OK)", c['index'], c['name'], label, self.TARGET_RATE)
        return c["index"]

    logger.info("No device supports %dHz. Trying native rates...", self.TARGET_RATE)
    for c in candidates:
      native_rate = int(c["info"].get("defaultSampleRate", 0))
      if native_rate <= 0:
        continue
      try:
        ok = self.audio.is_format_supported(
          native_rate,
          input_device=c["index"],
          input_channels=self.CHANNELS,
          input_format=self.FORMAT,
        )
        if ok:
          label = "USB/external" if is_likely_usb(c["name"]) else "built-in"
          logger.info("Selected device %d: %s (%s, native %dHz — will resample to %dHz)", c['index'], c['name'], label, native_rate, self.TARGET_RATE)
          self.RATE = native_rate
          # Scale chunk size proportionally so each chunk covers the same time duration
          self.CHUNK = int(self.config["audio"]["chunk_size"] * native_rate / self.TARGET_RATE)
          return c["index"]
      except (ValueError, OSError):
        continue

    logger.warning("No usable input device found. Falling back to system default.")
    return None

  # --- Resampling ------------------------------------------------------

  def _resample(self, audio_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample 16-bit mono PCM from one sample rate to another."""
    if from_rate == to_rate:
      return audio_bytes
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float64)
    # Number of output samples
    num_out = int(len(samples) * to_rate / from_rate)
    # Linear interpolation resample
    indices = np.linspace(0, len(samples) - 1, num_out)
    resampled = np.interp(indices, np.arange(len(samples)), samples)
    return resampled.astype(np.int16).tobytes()

  # --- Recording lifecycle ---------------------------------------------

  def start_recording(self, session_id: str | None = None) -> bool:
    if self.is_recording:
      logger.warning("Already recording")
      return False

    if session_id is None:
      session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    self.current_session_id = session_id
    self.is_recording = True

    session_temp = self.temp_dir / session_id
    session_temp.mkdir(parents=True, exist_ok=True)

    mic_index = self.find_mic_device()
    self.stream = self.audio.open(
      format=self.FORMAT,
      channels=self.CHANNELS,
      rate=self.RATE,
      input=True,
      input_device_index=mic_index,
      frames_per_buffer=self.CHUNK,
    )

    self.redis_client.publish(
      "events",
      json.dumps(
        {
          "type": "recording_started",
          "session_id": session_id,
          "timestamp": datetime.now().isoformat(),
        }
      ),
    )

    # let hardware service know (stubbed for now)
    self.redis_client.publish(
      "hardware_commands",
      json.dumps(
        {
          "action": "update_display",
          "state": "recording",
          "session_id": session_id,
        }
      ),
    )

    logger.info("Recording started - session %s", session_id)
    return True

  def stop_recording(self, session_id_from_command: str | None = None) -> str | None:
    if not self.is_recording:
      logger.warning("Not recording")
      # Still publish so downstream can set recording_state back to idle
      sid = session_id_from_command or self.current_session_id
      self.redis_client.publish(
        "events",
        json.dumps(
          {
            "type": "recording_stopped",
            "session_id": sid,
            "path": None,
            "timestamp": datetime.now().isoformat(),
          }
        ),
      )
      return None

    logger.info("Stopping recording - session %s", self.current_session_id)
    self.is_recording = False

    # Wait for the recording thread to finish reading before closing the stream.
    # Without this, stream.close() races with stream.read() in the thread,
    # causing a segfault in the native ALSA/PortAudio code.
    if self._recording_thread is not None:
      import threading
      if isinstance(self._recording_thread, threading.Thread) and self._recording_thread.is_alive():
        logger.info("Waiting for recording thread to finish...")
        self._recording_thread.join(timeout=5.0)
      self._recording_thread = None

    if self.stream:
      self.stream.stop_stream()
      self.stream.close()
      self.stream = None

    final_path = self.combine_segments()

    self.redis_client.publish(
      "events",
      json.dumps(
        {
          "type": "recording_stopped",
          "session_id": self.current_session_id,
          "path": str(final_path) if final_path else None,
          "timestamp": datetime.now().isoformat(),
        }
      ),
    )

    # update hardware state
    self.redis_client.publish(
      "hardware_commands",
      json.dumps(
        {
          "action": "update_display",
          "state": "processing",
          "session_id": self.current_session_id,
        }
      ),
    )

    session_id = self.current_session_id
    self.current_session_id = None
    return session_id

  # --- Segmentation helpers -------------------------------------------

  def process_audio_chunk(self, chunk: bytes) -> bool:
    """Return True if this chunk likely contains speech."""
    # webrtcvad supports 8000, 16000, 32000, 48000 Hz
    if self.RATE in (8000, 16000, 32000, 48000):
      vad_rate = self.RATE
      vad_chunk = chunk
    else:
      # Resample to 16kHz for VAD
      vad_rate = 16000
      vad_chunk = self._resample(chunk, self.RATE, vad_rate)
    # webrtcvad requires 10, 20, or 30ms frames
    frame_len = int(vad_rate * 0.03) * 2  # 30ms of 16-bit samples
    if len(vad_chunk) > frame_len:
      vad_chunk = vad_chunk[:frame_len]
    elif len(vad_chunk) < frame_len:
      vad_chunk = vad_chunk + b'\x00' * (frame_len - len(vad_chunk))
    try:
      return self.vad.is_speech(vad_chunk, vad_rate)
    except Exception:
      return True  # Assume speech on VAD error

  def save_audio_segment(self, frames: list[bytes], segment_num: int) -> Path:
    assert self.current_session_id is not None
    session_dir = self.temp_dir / self.current_session_id
    segment_path = session_dir / f"segment_{segment_num:04d}.wav"

    audio_bytes = b"".join(frames)
    segment_path.parent.mkdir(parents=True, exist_ok=True)

    # Resample to target rate (16kHz) if recorded at a different rate
    if self.RATE != self.TARGET_RATE:
      audio_bytes = self._resample(audio_bytes, self.RATE, self.TARGET_RATE)

    with wave.open(str(segment_path), "wb") as wf:
      wf.setnchannels(self.CHANNELS)
      wf.setsampwidth(self.audio.get_sample_size(self.FORMAT))
      wf.setframerate(self.TARGET_RATE)
      wf.writeframes(audio_bytes)

    self.redis_client.publish(
      "audio_segments",
      json.dumps(
        {
          "session_id": self.current_session_id,
          "segment_num": segment_num,
          "path": str(segment_path),
          "timestamp": time.time(),
        }
      ),
    )

    return segment_path

  def recording_loop(self) -> None:
    """
    Blocking loop that reads from the input stream, applies VAD, and
    saves segments when either:
    - a maximum number of chunks is reached, or
    - sufficient audio followed by a pause is detected.
    """
    segment_num = 0
    current_frames: list[bytes] = []
    silence_chunks = 0

    MIN_SEGMENT_CHUNKS = 50
    MAX_SEGMENT_CHUNKS = 500
    SILENCE_THRESHOLD = 10

    try:
      while self.is_recording:
        assert self.stream is not None
        chunk = self.stream.read(self.CHUNK, exception_on_overflow=False)
        is_speech = self.process_audio_chunk(chunk)

        current_frames.append(chunk)

        if is_speech:
          silence_chunks = 0
        else:
          silence_chunks += 1

        should_save = False
        if len(current_frames) >= MAX_SEGMENT_CHUNKS:
          should_save = True
        elif len(current_frames) >= MIN_SEGMENT_CHUNKS and silence_chunks >= SILENCE_THRESHOLD:
          should_save = True

        if should_save and current_frames:
          self.save_audio_segment(current_frames, segment_num)
          logger.info("Saved segment %d (%d chunks)", segment_num, len(current_frames))
          segment_num += 1
          current_frames = []
          silence_chunks = 0

    except Exception:
      logger.exception("Error in recording loop")

    finally:
      if current_frames:
        self.save_audio_segment(current_frames, segment_num)

  def combine_segments(self) -> Path | None:
    """Merge all segment WAVs into a single recording file."""
    if self.current_session_id is None:
      return None

    session_dir = self.temp_dir / self.current_session_id
    segment_files = sorted(session_dir.glob("segment_*.wav"))

    if not segment_files:
      logger.warning("No segments to combine")
      return None

    output_path = self.recordings_dir / f"{self.current_session_id}.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(output_path), "wb") as out_wav:
      with wave.open(str(segment_files[0]), "rb") as first:
        out_wav.setparams(first.getparams())

      for seg in segment_files:
        with wave.open(str(seg), "rb") as src:
          out_wav.writeframes(src.readframes(src.getnframes()))

    logger.info("Combined %d segments -> %s", len(segment_files), output_path)
    return output_path

  # --- Command listener -----------------------------------------------

  def run(self) -> None:
    """Listen for start/stop commands over Redis and manage recording."""
    logger.info("Service started, waiting for commands...")
    pubsub = self.redis_client.pubsub()
    pubsub.subscribe("commands")

    for message in pubsub.listen():
      if message["type"] != "message":
        continue
      try:
        command = json.loads(message["data"])
      except json.JSONDecodeError:
        logger.warning("Invalid command payload: %s", message['data'])
        continue

      action = command.get("action")
      if action == "start_recording":
        session_id = command.get("session_id")
        if self.start_recording(session_id):
          import threading

          thread = threading.Thread(target=self.recording_loop, daemon=True)
          thread.start()
          self._recording_thread = thread
      elif action == "stop_recording":
        self.stop_recording(session_id_from_command=command.get("session_id"))


if __name__ == "__main__":
  service = AudioCaptureService()
  try:
    service.run()
  finally:
    if service.stream:
      service.stream.stop_stream()
      service.stream.close()
    service.audio.terminate()
    logger.info("PyAudio terminated")

