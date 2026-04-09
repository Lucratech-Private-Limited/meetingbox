import json
import logging
import os
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import numpy as np
import pyaudio
import redis
import webrtcvad
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.audio")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))


class AudioCaptureService:
  """
  Capture audio from the USB mic array, write it directly to a single WAV
  file, and publish recording lifecycle events via Redis.
  """

  def __init__(self, config_path: str = "config.yaml") -> None:
    with open(config_path, "r") as f:
      self.config = yaml.safe_load(f)

    self.TARGET_RATE = self.config["audio"]["sample_rate"]  # 16000 for Whisper
    self.TARGET_CHANNELS = self.config["audio"]["channels"]
    self.CHANNELS = self.TARGET_CHANNELS
    self.CAPTURE_CHANNELS = self.TARGET_CHANNELS
    self.FORMAT = pyaudio.paInt16

    self.is_recording = False
    self.is_paused = False
    self.is_mic_test = False
    self.current_session_id: str | None = None
    self._recording_thread: object | None = None  # threading.Thread
    self._mic_test_thread: object | None = None  # threading.Thread
    self._output_path: Path | None = None
    self._wav_writer: wave.Wave_write | None = None
    self._last_level_emit_at = 0.0
    self.vad = webrtcvad.Vad(self.config["vad"]["aggressiveness"])

    self.redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    storage_cfg = self.config.get("storage", {})
    self.temp_dir = Path(os.getenv("TEMP_SEGMENTS_DIR", storage_cfg.get("temp_dir", "/data/audio/temp")))
    self.recordings_dir = Path(os.getenv("RECORDINGS_DIR", storage_cfg.get("recordings_dir", "/data/audio/recordings")))
    self.upload_on_stop = os.getenv("UPLOAD_AUDIO_ON_STOP", "1").lower() not in ("0", "false", "no")
    self.upload_audio_api_url = os.getenv("UPLOAD_AUDIO_API_URL", "http://127.0.0.1:8000/api/meetings/upload-audio").strip()
    # upload-audio runs transcription + summarization before HTTP response; keep above client/nginx limits.
    try:
      self.upload_audio_timeout_seconds = max(60, int(os.getenv("UPLOAD_AUDIO_TIMEOUT_SECONDS", "1200")))
    except ValueError:
      self.upload_audio_timeout_seconds = 1200
    # Same token as device-ui: paired device Bearer so uploads get user_id/device_id on the server.
    self._upload_auth_token = os.getenv("DEVICE_AUTH_TOKEN", "").strip()

    self.audio = pyaudio.PyAudio()
    self.stream: pyaudio.Stream | None = None

    # Actual capture rate — may differ from TARGET_RATE if the device
    # doesn't support 16kHz. We resample to TARGET_RATE before saving.
    self.RATE = self.TARGET_RATE
    self.CHUNK = self.config["audio"]["chunk_size"]

    logger.info("Initialized - target %dHz, %dch", self.TARGET_RATE, self.TARGET_CHANNELS)

  # --- Device handling -------------------------------------------------

  def find_mic_device(self) -> int | None:
    """
    Auto-detect the best available input device.

    Strategy (no hardcoded mic names):
      1. If AUDIO_INPUT_DEVICE_INDEX is set, use that index directly.
      2. If AUDIO_INPUT_DEVICE_NAME is set, use first device whose name contains it.
      3. Enumerate all input-capable devices.
      4. Test each one to see if it actually supports our sample rate.
      5. Prefer USB / external devices over built-in ones (they're almost
         always the meeting mic).
      6. If nothing passes the sample-rate test, return None (system default).

    This way any USB mic -- ReSpeaker, Jabra, Samson, cheap USB dongle,
    etc. -- works automatically without code changes.
    """
    self.CAPTURE_CHANNELS = self.TARGET_CHANNELS

    def supports_rate(dev: dict, rate: int, channels: int) -> bool:
      try:
        ok = self.audio.is_format_supported(
          rate,
          input_device=dev["index"],
          input_channels=channels,
          input_format=self.FORMAT,
        )
        return bool(ok)
      except (ValueError, OSError):
        return False

    def pick_capture_channels(dev: dict, rate: int) -> int | None:
      preferred_channels = [self.TARGET_CHANNELS]
      max_input_channels = int(dev["info"].get("maxInputChannels", 0) or 0)
      if self.TARGET_CHANNELS == 1 and max_input_channels >= 2:
        preferred_channels.append(2)
      for channels in preferred_channels:
        if supports_rate(dev, rate, channels):
          return channels
      return None

    def configure_native_rate(dev: dict) -> bool:
      native_rate = int(dev["info"].get("defaultSampleRate", 0))
      capture_channels = pick_capture_channels(dev, native_rate)
      if native_rate <= 0 or capture_channels is None:
        return False
      self.RATE = native_rate
      self.CAPTURE_CHANNELS = capture_channels
      # Scale chunk size proportionally so each chunk covers the same time duration
      self.CHUNK = int(self.config["audio"]["chunk_size"] * native_rate / self.TARGET_RATE)
      return True

    def is_generic_alias(name: str) -> bool:
      low = name.lower()
      return low in {"default", "sysdefault", "pulse"} or low.startswith(
        ("default:", "sysdefault:", "pulse:", "dmix:", "dsnoop:", "front:", "surround", "iec958:")
      )

    def classify_device(name: str) -> tuple[int, int]:
      low = name.lower()
      usb_keywords = [
        "usb", "uac", "respeaker", "jabra", "samson", "blue", "yeti",
        "rode", "fifine", "tonor", "boya", "maono", "external", "webcam", "camera",
      ]
      builtin_keywords = ["hdmi", "built-in", "bcm", "broadcom", "headphone", "analog", "spdif", "iec958"]
      if is_generic_alias(name):
        return (2, 1)
      if any(kw in low for kw in usb_keywords):
        return (0, 0)
      if "(hw:" in low:
        return (0, 1)
      if any(kw in low for kw in builtin_keywords):
        return (1, 0)
      return (1, 1)

    def label_for(name: str) -> str:
      category, subcategory = classify_device(name)
      if category == 0:
        return "USB/external" if subcategory == 0 else "hardware"
      if category == 2:
        return "generic alias"
      return "built-in"

    # Explicit device index (e.g. AUDIO_INPUT_DEVICE_INDEX=1)
    idx_env = os.getenv("AUDIO_INPUT_DEVICE_INDEX")
    if idx_env is not None and idx_env.strip() != "":
      try:
        idx = int(idx_env.strip())
        info = self.audio.get_device_info_by_index(idx)
        if info.get("maxInputChannels", 0) <= 0:
          raise ValueError("device has no input channels")
        dev = {"index": idx, "name": info.get("name", ""), "info": info}
        capture_channels = pick_capture_channels(dev, self.TARGET_RATE)
        if capture_channels is not None:
          self.CAPTURE_CHANNELS = capture_channels
          logger.info(
            "Using AUDIO_INPUT_DEVICE_INDEX=%d: %s (%dHz OK, capture=%dch)",
            idx,
            dev["name"],
            self.TARGET_RATE,
            self.CAPTURE_CHANNELS,
          )
          return idx
        if configure_native_rate(dev):
          logger.info(
            "Using AUDIO_INPUT_DEVICE_INDEX=%d: %s (native %dHz, capture=%dch — will resample to %dHz)",
            idx,
            dev["name"],
            self.RATE,
            self.CAPTURE_CHANNELS,
            self.TARGET_RATE,
          )
          return idx
        raise ValueError(f"device does not support {self.TARGET_RATE}Hz or its native rate")
      except (ValueError, OSError) as e:
        logger.warning("AUDIO_INPUT_DEVICE_INDEX=%s unusable: %s — falling back to auto-detect", idx_env, e)

    # Explicit device name substring (e.g. AUDIO_INPUT_DEVICE_NAME="USB PnP")
    name_pattern = os.getenv("AUDIO_INPUT_DEVICE_NAME", "").strip()
    if name_pattern:
      for i in range(self.audio.get_device_count()):
        device_info = self.audio.get_device_info_by_index(i)
        if device_info.get("maxInputChannels", 0) <= 0:
          continue
        name = device_info.get("name", "")
        if name_pattern.lower() not in name.lower():
          continue
        dev = {"index": i, "name": name, "info": device_info}
        capture_channels = pick_capture_channels(dev, self.TARGET_RATE)
        if capture_channels is not None:
          self.CAPTURE_CHANNELS = capture_channels
          logger.info(
            "Using AUDIO_INPUT_DEVICE_NAME match: [%d] %s (%dHz OK, capture=%dch)",
            i,
            name,
            self.TARGET_RATE,
            self.CAPTURE_CHANNELS,
          )
          return i
        if configure_native_rate(dev):
          logger.info(
            "Using AUDIO_INPUT_DEVICE_NAME match: [%d] %s (native %dHz, capture=%dch — will resample to %dHz)",
            i,
            name,
            self.RATE,
            self.CAPTURE_CHANNELS,
            self.TARGET_RATE,
          )
          return i
      logger.warning("No device matched AUDIO_INPUT_DEVICE_NAME=%r — falling back to auto-detect", name_pattern)

    num_devices = self.audio.get_device_count()

    # Collect every input device with its metadata
    candidates: list[dict] = []
    for i in range(num_devices):
      device_info = self.audio.get_device_info_by_index(i)
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

    # Sort: concrete USB/external first, built-ins next, generic aliases last.
    # This avoids choosing wrappers like "sysdefault" over the actual USB device.
    candidates.sort(key=lambda c: classify_device(c["name"]))

    # Pick the first preferred candidate that supports either our target
    # sample rate or its native rate. This keeps concrete USB devices ahead
    # of generic aliases like "sysdefault", even when the USB device needs
    # resampling to 16kHz.
    for c in candidates:
      capture_channels = pick_capture_channels(c, self.TARGET_RATE)
      if capture_channels is not None:
        self.CAPTURE_CHANNELS = capture_channels
        label = label_for(c["name"])
        logger.info(
          "Selected device %d: %s (%s, %dHz OK, capture=%dch)",
          c['index'],
          c['name'],
          label,
          self.TARGET_RATE,
          self.CAPTURE_CHANNELS,
        )
        return c["index"]
      if configure_native_rate(c):
        label = label_for(c["name"])
        logger.info(
          "Selected device %d: %s (%s, native %dHz, capture=%dch — will resample to %dHz)",
          c['index'],
          c['name'],
          label,
          self.RATE,
          self.CAPTURE_CHANNELS,
          self.TARGET_RATE,
        )
        return c["index"]

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

  def _prepare_audio_bytes(self, chunk: bytes) -> bytes:
    """Convert captured PCM into the mono target format used for storage."""
    audio_bytes = chunk
    if self.CAPTURE_CHANNELS != self.TARGET_CHANNELS:
      if self.TARGET_CHANNELS != 1 or self.CAPTURE_CHANNELS < 2:
        raise ValueError(
          f"Unsupported channel conversion: capture={self.CAPTURE_CHANNELS}, target={self.TARGET_CHANNELS}"
        )
      samples = np.frombuffer(audio_bytes, dtype=np.int16)
      frames = samples.reshape(-1, self.CAPTURE_CHANNELS)
      audio_bytes = frames.mean(axis=1).astype(np.int16).tobytes()
    if self.RATE != self.TARGET_RATE:
      audio_bytes = self._resample(audio_bytes, self.RATE, self.TARGET_RATE)
    return audio_bytes

  # --- Recording lifecycle ---------------------------------------------

  def start_recording(self, session_id: str | None = None) -> bool:
    if self.is_recording:
      logger.warning("Already recording")
      return False
    if self.is_mic_test:
      self.stop_mic_test()

    if session_id is None:
      session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    self.current_session_id = session_id
    self.is_recording = True
    self.is_paused = False

    session_temp = self.temp_dir / session_id
    session_temp.mkdir(parents=True, exist_ok=True)

    mic_index = self.find_mic_device()
    self.stream = self.audio.open(
      format=self.FORMAT,
      channels=self.CAPTURE_CHANNELS,
      rate=self.RATE,
      input=True,
      input_device_index=mic_index,
      frames_per_buffer=self.CHUNK,
    )

    output_path = self.recordings_dir / f"{session_id}.wav"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wav_writer = wave.open(str(output_path), "wb")
    wav_writer.setnchannels(self.TARGET_CHANNELS)
    wav_writer.setsampwidth(self.audio.get_sample_size(self.FORMAT))
    wav_writer.setframerate(self.TARGET_RATE)
    self._output_path = output_path
    self._wav_writer = wav_writer

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

  def _build_multipart_payload(self, wav_path: Path, session_id: str) -> tuple[str, bytes]:
    boundary = f"----MeetingBoxBoundary{uuid.uuid4().hex}"
    crlf = b"\r\n"
    chunks: list[bytes] = []

    chunks.append(f"--{boundary}".encode("utf-8"))
    chunks.append(b'Content-Disposition: form-data; name="session_id"')
    chunks.append(b"")
    chunks.append(session_id.encode("utf-8"))

    chunks.append(f"--{boundary}".encode("utf-8"))
    chunks.append(
      f'Content-Disposition: form-data; name="file"; filename="{wav_path.name}"'.encode("utf-8")
    )
    chunks.append(b"Content-Type: audio/wav")
    chunks.append(b"")
    chunks.append(wav_path.read_bytes())

    chunks.append(f"--{boundary}--".encode("utf-8"))
    body = crlf.join(chunks) + crlf
    return boundary, body

  def _upload_recording_via_api(self, wav_path: Path, session_id: str) -> bool:
    if not wav_path.exists():
      logger.error("Upload skipped: WAV file does not exist (%s)", wav_path)
      return False

    try:
      boundary, body = self._build_multipart_payload(wav_path, session_id)
      headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
      if self._upload_auth_token:
        headers["Authorization"] = f"Bearer {self._upload_auth_token}"
      req = urlrequest.Request(
        self.upload_audio_api_url,
        data=body,
        headers=headers,
        method="POST",
      )
      with urlrequest.urlopen(req, timeout=self.upload_audio_timeout_seconds) as resp:
        status = getattr(resp, "status", 200)
        raw = resp.read().decode("utf-8", errors="ignore")
        if status < 200 or status >= 300:
          logger.error("Upload failed for %s: HTTP %s %s", session_id, status, raw[:200])
          return False
        logger.info("Uploaded recording via API for %s", session_id)
        return True
    except urlerror.HTTPError as e:
      detail = e.read().decode("utf-8", errors="ignore")
      logger.error("Upload HTTP error for %s: %s %s", session_id, e.code, detail[:200])
    except Exception as e:
      logger.error("Upload failed for %s: %s", session_id, e)
    return False

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
    self.is_paused = False

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

    if self._wav_writer is not None:
      self._wav_writer.close()
      self._wav_writer = None

    final_path = self._output_path if self._output_path and self._output_path.exists() else None
    if final_path is None:
      logger.warning("Final recording file missing for session %s", self.current_session_id)
    self._output_path = None

    session_id = self.current_session_id
    uploaded = False
    attempted_upload = False
    if self.upload_on_stop and final_path and session_id:
      attempted_upload = True
      uploaded = self._upload_recording_via_api(final_path, session_id)

    if attempted_upload and not uploaded:
      self.redis_client.publish(
        "events",
        json.dumps(
          {
            "type": "error",
            "error_type": "Upload Failed",
            "message": "Could not upload audio for cloud transcription/summarization.",
            "session_id": session_id,
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
          "session_id": session_id,
        }
      ),
    )

    self.current_session_id = None
    return session_id

  def start_mic_test(self) -> bool:
    if self.is_recording:
      logger.warning("Mic test requested while recording is active")
      return False
    if self.is_mic_test:
      return True

    mic_index = self.find_mic_device()
    self.stream = self.audio.open(
      format=self.FORMAT,
      channels=self.CAPTURE_CHANNELS,
      rate=self.RATE,
      input=True,
      input_device_index=mic_index,
      frames_per_buffer=self.CHUNK,
    )
    self.is_mic_test = True
    logger.info("Mic test started")
    return True

  def stop_mic_test(self) -> None:
    if not self.is_mic_test:
      return
    self.is_mic_test = False

    if self._mic_test_thread is not None:
      import threading
      if isinstance(self._mic_test_thread, threading.Thread) and self._mic_test_thread.is_alive():
        self._mic_test_thread.join(timeout=2.0)
      self._mic_test_thread = None

    if self.stream:
      try:
        self.stream.stop_stream()
        self.stream.close()
      except Exception:
        pass
      self.stream = None
    logger.info("Mic test stopped")

  def mic_test_loop(self) -> None:
    try:
      while self.is_mic_test and not self.is_recording:
        assert self.stream is not None
        chunk = self.stream.read(self.CHUNK, exception_on_overflow=False)
        audio_bytes = self._prepare_audio_bytes(chunk)

        now = time.monotonic()
        if now - self._last_level_emit_at >= 0.08:
          samples = np.frombuffer(audio_bytes, dtype=np.int16)
          if len(samples) > 0:
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
            level = min(1.0, rms / 5000.0)
          else:
            level = 0.0
          self.redis_client.publish(
            "events",
            json.dumps(
              {
                "type": "mic_test_level",
                "level": level,
                "timestamp": datetime.now().isoformat(),
              }
            ),
          )
          self._last_level_emit_at = now
    except Exception:
      logger.exception("Error in mic test loop")

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

  def _check_silent_audio(self, audio_bytes: bytes, segment_num: int) -> None:
    """Warn if the captured audio appears to be silent (wrong device or muted mic)."""
    if segment_num > 0:
      return
    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    peak = int(np.max(np.abs(samples))) if len(samples) > 0 else 0
    # Typical speech peaks are 2000-20000; silence or wrong device is near 0
    if peak < 200:
      logger.warning(
        "SILENT AUDIO DETECTED (peak=%d). Captured audio may be empty — transcription/summary will be blank. "
        "Check: 1) AUDIO_INPUT_DEVICE_INDEX or AUDIO_INPUT_DEVICE_NAME in .env (see logs above for device list), "
        "2) Mic volume and mute switch",
        peak,
      )

  def save_audio_segment(self, frames: list[bytes], segment_num: int) -> Path:
    assert self.current_session_id is not None
    session_dir = self.temp_dir / self.current_session_id
    segment_path = session_dir / f"segment_{segment_num:04d}.wav"

    audio_bytes = b"".join(frames)
    segment_path.parent.mkdir(parents=True, exist_ok=True)

    self._check_silent_audio(audio_bytes, segment_num)

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
    Blocking loop that reads from the input stream and writes every chunk
    into the session WAV file.
    """
    checked_audio = False

    try:
      while self.is_recording:
        assert self.stream is not None
        chunk = self.stream.read(self.CHUNK, exception_on_overflow=False)
        if self.is_paused:
          continue
        audio_bytes = self._prepare_audio_bytes(chunk)

        # Emit near-real-time audio level for UI waveform (throttled).
        now = time.monotonic()
        if now - self._last_level_emit_at >= 0.08:
          samples = np.frombuffer(audio_bytes, dtype=np.int16)
          if len(samples) > 0:
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
            level = min(1.0, rms / 5000.0)
          else:
            level = 0.0
          self.redis_client.publish(
            "events",
            json.dumps(
              {
                "type": "audio_level",
                "session_id": self.current_session_id,
                "level": level,
                "timestamp": datetime.now().isoformat(),
              }
            ),
          )
          self._last_level_emit_at = now

        if not checked_audio:
          self._check_silent_audio(audio_bytes, 0)
          checked_audio = True

        if self._wav_writer is None:
          raise RuntimeError("Recording file is not open")
        self._wav_writer.writeframes(audio_bytes)

    except Exception:
      logger.exception("Error in recording loop")

  def pause_recording(self) -> bool:
    if not self.is_recording:
      logger.warning("Pause requested while not recording")
      return False
    if self.is_paused:
      return True
    self.is_paused = True
    self.redis_client.publish(
      "events",
      json.dumps(
        {
          "type": "recording_paused",
          "session_id": self.current_session_id,
          "timestamp": datetime.now().isoformat(),
        }
      ),
    )
    self.redis_client.publish(
      "events",
      json.dumps(
        {
          "type": "audio_level",
          "session_id": self.current_session_id,
          "level": 0.0,
          "timestamp": datetime.now().isoformat(),
        }
      ),
    )
    logger.info("Recording paused - session %s", self.current_session_id)
    return True

  def resume_recording(self) -> bool:
    if not self.is_recording:
      logger.warning("Resume requested while not recording")
      return False
    if not self.is_paused:
      return True
    self.is_paused = False
    self.redis_client.publish(
      "events",
      json.dumps(
        {
          "type": "recording_resumed",
          "session_id": self.current_session_id,
          "timestamp": datetime.now().isoformat(),
        }
      ),
    )
    logger.info("Recording resumed - session %s", self.current_session_id)
    return True

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

  def _poll_command_api_base(self) -> str:
    base = os.getenv("AUDIO_POLL_BASE_URL", "").strip().rstrip("/")
    if base:
      return base
    marker = "/api/meetings/upload-audio"
    up = self.upload_audio_api_url or ""
    if marker in up:
      return up.split(marker)[0].rstrip("/")
    return "http://127.0.0.1:8000"

  def _dispatch_command(self, command: dict) -> None:
    action = command.get("action")
    if action == "start_recording":
      session_id = command.get("session_id")
      if self.start_recording(session_id):
        thread = threading.Thread(target=self.recording_loop, daemon=True)
        thread.start()
        self._recording_thread = thread
    elif action == "stop_recording":
      self.stop_recording(session_id_from_command=command.get("session_id"))
    elif action == "pause_recording":
      self.pause_recording()
    elif action == "resume_recording":
      self.resume_recording()
    elif action == "start_mic_test":
      if self.start_mic_test():
        thread = threading.Thread(target=self.mic_test_loop, daemon=True)
        thread.start()
        self._mic_test_thread = thread
    elif action == "stop_mic_test":
      self.stop_mic_test()

  def run_redis(self) -> None:
    """Subscribe to Redis ``commands`` (same host or tunnel to server Redis)."""
    logger.info("Command: Redis channel 'commands' (host %s)", REDIS_HOST)
    pubsub = self.redis_client.pubsub()
    pubsub.subscribe("commands")

    for message in pubsub.listen():
      if message["type"] != "message":
        continue
      try:
        command = json.loads(message["data"])
      except json.JSONDecodeError:
        logger.warning("Invalid command payload: %s", message["data"])
        continue
      self._dispatch_command(command)

  def run_http_poll(self) -> None:
    """
    Long-poll cloud API for recording commands (no Redis subscription).

    Requires DEVICE_AUTH_TOKEN and a reachable API; uses UPLOAD_AUDIO_API_URL or
    AUDIO_POLL_BASE_URL to find ``/api/device/audio-command/wait``.
    """
    if not self._upload_auth_token:
      logger.error("AUDIO_COMMAND_SOURCE=http requires DEVICE_AUTH_TOKEN (paired device token).")
      return
    base = self._poll_command_api_base()
    url = f"{base}/api/device/audio-command/wait"
    logger.info("Command: HTTP long-poll %s", url)

    while True:
      try:
        req = urlrequest.Request(
          url,
          headers={"Authorization": f"Bearer {self._upload_auth_token}"},
        )
        with urlrequest.urlopen(req, timeout=90) as resp:
          if resp.status == 204:
            continue
          raw = resp.read().decode("utf-8", errors="replace").strip()
          if not raw:
            continue
          command = json.loads(raw)
      except urlerror.HTTPError as exc:
        if exc.code == 204:
          continue
        body = ""
        try:
          body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
          pass
        logger.warning("audio-command wait HTTP %s: %s", exc.code, body or exc.reason)
        time.sleep(2.0)
        continue
      except (urlerror.URLError, TimeoutError) as exc:
        logger.warning("audio-command wait network error: %s", exc)
        time.sleep(2.0)
        continue
      except json.JSONDecodeError:
        logger.warning("Invalid JSON from audio-command wait")
        continue

      self._dispatch_command(command)

  def run(self) -> None:
    mode = os.getenv("AUDIO_COMMAND_SOURCE", "redis").strip().lower()
    if mode in ("http", "api", "longpoll"):
      self.run_http_poll()
    else:
      self.run_redis()


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

