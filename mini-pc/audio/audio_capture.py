import json
import logging
import os
import select
import subprocess
import sys
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
import webrtcvad
import yaml

try:
  import sounddevice as sd
except ImportError:
  sd = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("meetingbox.audio")

DEVICE_AUTH_TOKEN_FILE = os.getenv("DEVICE_AUTH_TOKEN_FILE", "/data/config/device_auth_token").strip()
# Device identity (multi-device scoping). Set via the ``DEVICE_ID`` env
# var or resolved at startup from ``/api/device/pairing-status`` using
# the persisted device auth token. Tagged on every emitted event so the
# device-ui side can drop events meant for a different paired mini-PC
# that happens to be sharing the same cloud backend.
PAIRING_STATUS_URL = os.getenv(
    "PAIRING_STATUS_URL", "http://127.0.0.1:8000/api/device/pairing-status"
).strip()

# Sentinel prefix used when writing events to stdout. The
# ``audio_supervisor`` parent process scans stdout line-by-line and
# routes any line starting with this prefix into the device-ui event
# dispatcher; everything else is treated as regular log output.
EVT_PREFIX = "MEETINGBOX_EVT|"

# Local device bridge. The Flutter device UI subscribes to the bridge event
# WebSocket (``/v1/events``); audio capture mirrors live mic levels and
# recording state to it (best-effort) so the recording screen's waveform
# reacts to the user's voice. Overridable so the daemon and bridge can run
# on different hosts.
DEVICE_BRIDGE_URL = os.getenv("DEVICE_BRIDGE_URL", "http://127.0.0.1:8765").rstrip("/")
BRIDGE_PUBLISH_PATH = "/v1/events/publish"
# Lifecycle event types -> the ``recording_state`` the UI understands.
_BRIDGE_LIFECYCLE_STATE = {
    "recording_started": "recording",
    "recording_resumed": "recording",
    "recording_paused": "paused",
    "recording_stopped": "stopped",
}


def _publish_to_bridge(payload: dict) -> None:
  """Best-effort POST of a UI event to the local device bridge.

  Runs on a daemon thread so audio capture never blocks on the UI bridge
  being slow or unavailable. Any failure is swallowed.
  """
  url = f"{DEVICE_BRIDGE_URL}{BRIDGE_PUBLISH_PATH}"
  data = json.dumps(payload).encode("utf-8")

  def _do() -> None:
    try:
      req = urlrequest.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
      )
      urlrequest.urlopen(req, timeout=1.5).close()
    except Exception:  # noqa: BLE001 - UI events are best-effort
      pass

  threading.Thread(target=_do, daemon=True).start()


class ARecordInputStream:
  """Small stream wrapper around arecord for USB mic hot-plug recovery."""

  def __init__(self, device: str, rate: int, channels: int, chunk_frames: int) -> None:
    self.device = device
    self.rate = rate
    self.channels = channels
    self.chunk_bytes = max(1, int(chunk_frames)) * max(1, int(channels)) * 2
    self.proc = subprocess.Popen(
      [
        "arecord",
        "-q",
        "-D",
        device,
        "-f",
        "S16_LE",
        "-r",
        str(rate),
        "-c",
        str(channels),
        "-t",
        "raw",
      ],
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
    )
    time.sleep(0.05)
    if self.proc.poll() is not None:
      raise RuntimeError(f"arecord exited while opening {device} (code={self.proc.returncode})")

  def read(self) -> bytes:
    if self.proc.poll() is not None:
      raise RuntimeError(f"arecord exited for {self.device} (code={self.proc.returncode})")
    if self.proc.stdout is None:
      raise RuntimeError("arecord stdout is closed")
    ready, _, _ = select.select([self.proc.stdout], [], [], 2.0)
    if not ready:
      raise RuntimeError(f"arecord read timeout from {self.device}")
    data = self.proc.stdout.read(self.chunk_bytes)
    if len(data) != self.chunk_bytes:
      raise RuntimeError(f"arecord short read from {self.device}: {len(data)}/{self.chunk_bytes}")
    return data

  def stop(self) -> None:
    if self.proc.poll() is None:
      self.proc.terminate()
      try:
        self.proc.wait(timeout=1.0)
      except subprocess.TimeoutExpired:
        self.proc.kill()
        self.proc.wait(timeout=1.0)

  def close(self) -> None:
    self.stop()


def _load_device_auth_token() -> str:
  """Prefer the persisted paired-device token file, then fall back to env."""
  token_file = DEVICE_AUTH_TOKEN_FILE
  if token_file:
    path = Path(token_file)
    try:
      if path.is_file():
        token = path.read_text(encoding="utf-8-sig").strip()
        if token:
          return token
    except OSError as exc:
      logger.warning("Could not read device auth token file %s: %s", path, exc)
  return os.getenv("DEVICE_AUTH_TOKEN", "").strip()


class AudioCaptureService:
  """
  Capture audio from the USB mic array, write it directly to a single WAV
  file, and emit recording lifecycle events to stdout (parsed by the
  ``audio_supervisor`` running inside the device-ui process).
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
    self._mic_status: str | None = None
    self.vad = webrtcvad.Vad(self.config["vad"]["aggressiveness"])

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
    try:
      self.input_gain = max(1.0, float(os.getenv("AUDIO_INPUT_GAIN", "1")))
    except ValueError:
      self.input_gain = 1.0
    if self.input_gain > 1.0:
      logger.info("Applying AUDIO_INPUT_GAIN=%sx to captured microphone samples", self.input_gain)
    self.capture_backend = os.getenv("AUDIO_CAPTURE_BACKEND", "arecord").strip().lower()
    if self.capture_backend not in ("arecord", "pyaudio", "sounddevice"):
      logger.warning("Unknown AUDIO_CAPTURE_BACKEND=%r; using arecord", self.capture_backend)
      self.capture_backend = "arecord"
    if self.capture_backend == "sounddevice" and sd is None:
      logger.warning("AUDIO_CAPTURE_BACKEND=sounddevice requested but sounddevice is unavailable; using pyaudio")
      self.capture_backend = "pyaudio"
    logger.info("Audio capture backend: %s", self.capture_backend)
    self.hotplug_use_system_default = (
      (os.getenv("AUDIO_HOTPLUG_USE_SYSTEM_DEFAULT") or "0").strip().lower()
      not in ("0", "false", "no", "off")
    )
    if self.hotplug_use_system_default:
      logger.info("Audio hot-plug mode: using system default input route")
    # Same token as device-ui: paired device Bearer so uploads/command polling
    # keep working after pairing without copying the token into .env manually.
    self._upload_auth_token = _load_device_auth_token()

    # Resolve this mini-PC's device identity for command filtering. Env
    # wins; falls back to the backend's ``/api/device/pairing-status``
    # using the device auth token. Failures keep ``self.device_id`` as
    # ``None`` and we accept all commands (legacy behaviour) — this is
    # safe because pre-multi-device installations always shipped a
    # single audio capture process.
    self.device_id: str | None = (os.getenv("DEVICE_ID") or "").strip() or None
    if not self.device_id and self._upload_auth_token:
      self.device_id = self._resolve_device_id_via_api()
    logger.info("Audio capture device_id=%s", self.device_id or "<unset>")

    self.audio = pyaudio.PyAudio()
    self.stream: pyaudio.Stream | None = None

    # Actual capture rate — may differ from TARGET_RATE if the device
    # doesn't support 16kHz. We resample to TARGET_RATE before saving.
    self.RATE = self.TARGET_RATE
    self.CHUNK = self.config["audio"]["chunk_size"]

    logger.info("Initialized - target %dHz, %dch", self.TARGET_RATE, self.TARGET_CHANNELS)

  # --- Device handling -------------------------------------------------

  def _emit_mic_status(self, status: str, *, message: str | None = None) -> None:
    if status == self._mic_status and not message:
      return
    self._mic_status = status
    payload = {
      "type": "mic_status",
      "status": status,
      "session_id": self.current_session_id,
      "timestamp": datetime.now().isoformat(),
    }
    if message:
      payload["message"] = message
    self._emit_event(payload)

  def _using_sounddevice(self) -> bool:
    return self.capture_backend == "sounddevice" and sd is not None

  def _using_arecord(self) -> bool:
    return self.capture_backend == "arecord"

  def _resolve_arecord_device(self) -> str:
    configured = (os.getenv("AUDIO_ALSA_INPUT_DEVICE") or "").strip()
    if configured:
      return configured
    try:
      cards = Path("/proc/asound/cards").read_text(encoding="utf-8", errors="ignore")
    except OSError:
      cards = ""
    # Current USB dongle appears as card id "Device". Use the card id, not a
    # numeric hw index, so moving USB ports does not change the configured mic.
    if "USB-Audio" in cards and "[Device" in cards:
      return "plughw:CARD=Device,DEV=0"
    return "default"

  def _open_input_stream(self, mic_index):
    if self._using_arecord():
      device = self._resolve_arecord_device()
      logger.info("Opening arecord input device %s", device)
      return ARecordInputStream(device, self.TARGET_RATE, self.TARGET_CHANNELS, self.config["audio"]["chunk_size"])

    if self._using_sounddevice():
      stream = sd.RawInputStream(
        samplerate=self.RATE,
        blocksize=self.CHUNK,
        channels=self.CAPTURE_CHANNELS,
        dtype="int16",
        device=mic_index,
      )
      stream.start()
      return stream

    return self.audio.open(
      format=self.FORMAT,
      channels=self.CAPTURE_CHANNELS,
      rate=self.RATE,
      input=True,
      input_device_index=mic_index,
      frames_per_buffer=self.CHUNK,
    )

  def _read_input_chunk(self) -> bytes:
    if self._using_arecord():
      return self.stream.read()
    if self._using_sounddevice():
      data, overflowed = self.stream.read(self.CHUNK)
      if overflowed:
        logger.debug("sounddevice input overflow")
      return bytes(data)
    return self.stream.read(self.CHUNK, exception_on_overflow=False)

  def _stop_close_stream(self) -> None:
    if not self.stream:
      return
    try:
      if self._using_sounddevice():
        self.stream.stop()
      elif self._using_arecord():
        self.stream.stop()
      else:
        self.stream.stop_stream()
    except Exception:
      pass
    try:
      self.stream.close()
    except Exception:
      pass
    self.stream = None

  def _sample_width_bytes(self) -> int:
    if self._using_arecord() or self._using_sounddevice():
      return 2
    return self.audio.get_sample_size(self.FORMAT)

  def _reinit_portaudio(self) -> None:
    """Re-enumerate audio devices via PortAudio so USB hot-plug is picked up.

    PortAudio (and therefore PyAudio) snapshots the ALSA device list at
    ``Pa_Initialize()``. Without this re-init, unplugging and re-plugging the
    USB mic leaves the cached list stale and ``find_mic_device`` keeps picking
    the gone device / the wrong default — currently a restart of the container
    is the only workaround. We tear down PortAudio (no active stream at this
    point) and create a new ``PyAudio`` instance so the next ``get_device_*``
    call reflects the current kernel state.
    """
    if self.stream is not None:
      try:
        self.stream.stop_stream()
      except Exception:
        logger.debug("PortAudio reinit: stop_stream failed", exc_info=True)
      try:
        self.stream.close()
      except Exception:
        logger.debug("PortAudio reinit: stream.close failed", exc_info=True)
      self.stream = None
    try:
      self.audio.terminate()
    except Exception:
      logger.debug("PortAudio reinit: terminate failed", exc_info=True)
    self.audio = pyaudio.PyAudio()
    try:
      count = self.audio.get_device_count()
    except Exception:
      count = -1
    logger.info("PortAudio re-initialized (device_count=%s)", count)

  def find_mic_device(self) -> int | None:
    """
    Auto-detect the best available input device.

    Strategy (no hardcoded mic names):
      1. Unless AUDIO_HOTPLUG_USE_SYSTEM_DEFAULT=0, use the system default
         input route so PipeWire/Pulse can follow USB unplug/replug.
      2. If AUDIO_INPUT_DEVICE_INDEX_STRICT=1 and AUDIO_INPUT_DEVICE_INDEX is set,
         use that index directly. Fixed PortAudio indices are unsafe for hot-plug.
      3. If AUDIO_INPUT_DEVICE_NAME is set, use first device whose name contains it.
      4. Re-enumerate the PortAudio device list (hot-plug support).
      5. Test each one to see if it actually supports our sample rate.
      6. Prefer USB / external devices over built-in ones (and when any USB
         device is detected, ignore non-USB devices entirely unless
         ``MEETINGBOX_USB_MIC_STRICT=0`` is set).
      7. If nothing passes the sample-rate test, return None (system default).

    This way any USB mic -- ReSpeaker, Jabra, Samson, cheap USB dongle,
    etc. -- works automatically without code changes, and a re-plug of the
    USB mic during runtime is picked up on the next ``start_recording`` /
    ``start_mic_test`` instead of requiring a container restart.
    """
    if self._using_arecord():
      self.RATE = self.TARGET_RATE
      self.CAPTURE_CHANNELS = self.TARGET_CHANNELS
      self.CHUNK = self.config["audio"]["chunk_size"]
      logger.info("Using arecord backend; device=%s", self._resolve_arecord_device())
      return None

    if self._using_sounddevice():
      return self.find_sounddevice_mic_device()

    # Force PortAudio to re-scan ALSA before any device-list read below.
    self._reinit_portaudio()
    self.CAPTURE_CHANNELS = self.TARGET_CHANNELS
    idx_env = os.getenv("AUDIO_INPUT_DEVICE_INDEX")
    idx_strict = (os.getenv("AUDIO_INPUT_DEVICE_INDEX_STRICT") or "").strip().lower() in ("1", "true", "yes", "on")
    name_pattern = os.getenv("AUDIO_INPUT_DEVICE_NAME", "").strip()
    if self.hotplug_use_system_default and not idx_strict and not name_pattern:
      self.RATE = self.TARGET_RATE
      self.CHUNK = self.config["audio"]["chunk_size"]
      logger.info("Using system default input for USB hot-plug resilience")
      return None

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

    # Explicit device index is disabled by default because PortAudio indices
    # change when USB mics are unplugged, moved to another port, or replaced.
    # Enable only for lab/debug setups with AUDIO_INPUT_DEVICE_INDEX_STRICT=1.
    idx_strict = (os.getenv("AUDIO_INPUT_DEVICE_INDEX_STRICT") or "").strip().lower() in ("1", "true", "yes", "on")
    if idx_env is not None and idx_env.strip() != "" and not idx_strict:
      logger.warning(
        "Ignoring AUDIO_INPUT_DEVICE_INDEX=%s because fixed indices break USB hot-plug; "
        "set AUDIO_INPUT_DEVICE_INDEX_STRICT=1 to force it.",
        idx_env,
      )
    if idx_env is not None and idx_env.strip() != "" and idx_strict:
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

    # Strict USB-only mode: when any USB-like candidate is present, drop
    # non-USB candidates entirely so we never silently fall back to the
    # built-in/HDMI/loopback mic. Disable with MEETINGBOX_USB_MIC_STRICT=0
    # to restore the legacy "prefer USB, fall back to built-in" behavior.
    strict_usb_raw = (os.getenv("MEETINGBOX_USB_MIC_STRICT") or "1").strip().lower()
    strict_usb = strict_usb_raw not in ("0", "false", "no", "off")
    if strict_usb:
      usb_keywords = (
        "usb", "uac", "respeaker", "jabra", "samson", "blue", "yeti",
        "rode", "fifine", "tonor", "boya", "maono", "external",
      )
      usb_candidates = [
        c for c in candidates
        if any(kw in c["name"].lower() for kw in usb_keywords)
      ]
      if usb_candidates:
        logger.info(
          "MEETINGBOX_USB_MIC_STRICT=1 → restricting to %d USB-like device(s)",
          len(usb_candidates),
        )
        candidates = usb_candidates
      else:
        logger.warning(
          "MEETINGBOX_USB_MIC_STRICT=1 and no USB-named PortAudio input found; "
          "using system default input so PipeWire/Pulse can route to the current mic."
        )
        return None

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

  def find_sounddevice_mic_device(self) -> int | None:
    """Find a sounddevice input device. Used when PyAudio cannot see USB ALSA capture devices."""
    assert sd is not None

    self.CAPTURE_CHANNELS = self.TARGET_CHANNELS
    self.RATE = self.TARGET_RATE
    self.CHUNK = self.config["audio"]["chunk_size"]

    devices = []
    for idx, dev in enumerate(sd.query_devices()):
      if int(dev.get("max_input_channels") or 0) <= 0:
        continue
      devices.append((idx, dev))

    logger.info("Found %d sounddevice input device(s):", len(devices))
    for idx, dev in devices:
      logger.info("  [%d] %s  (rate=%s)", idx, dev.get("name") or "", dev.get("default_samplerate"))

    idx_s = os.getenv("AUDIO_INPUT_DEVICE_INDEX", "").strip()
    idx_strict = (os.getenv("AUDIO_INPUT_DEVICE_INDEX_STRICT") or "").strip().lower() in ("1", "true", "yes", "on")
    name_pattern = os.getenv("AUDIO_INPUT_DEVICE_NAME", "").strip().lower()
    if self.hotplug_use_system_default and not idx_strict and not name_pattern:
      self.RATE = self.TARGET_RATE
      self.CHUNK = self.config["audio"]["chunk_size"]
      self.CAPTURE_CHANNELS = self.TARGET_CHANNELS
      logger.info("Using sounddevice system default input for USB hot-plug resilience")
      return None
    if idx_s.isdigit() and not idx_strict:
      logger.warning(
        "Ignoring AUDIO_INPUT_DEVICE_INDEX=%s because fixed indices break USB hot-plug; "
        "set AUDIO_INPUT_DEVICE_INDEX_STRICT=1 to force it.",
        idx_s,
      )
    if idx_s.isdigit() and idx_strict:
      return int(idx_s)

    if name_pattern:
      for idx, dev in devices:
        if name_pattern in (dev.get("name") or "").lower():
          self._configure_sounddevice_rate(dev)
          logger.info("Using AUDIO_INPUT_DEVICE_NAME match via sounddevice: [%d] %s", idx, dev.get("name") or "")
          return idx

    usb_keywords = ("usb", "uac", "respeaker", "jabra", "samson", "blue", "yeti", "rode", "fifine", "tonor", "boya", "maono", "external", "webcam", "camera")
    for idx, dev in devices:
      name = dev.get("name") or ""
      if any(keyword in name.lower() for keyword in usb_keywords):
        self._configure_sounddevice_rate(dev)
        logger.info("Selected sounddevice USB/external input [%d]: %s", idx, name)
        return idx

    if devices:
      idx, dev = devices[0]
      self._configure_sounddevice_rate(dev)
      logger.info("Selected first sounddevice input [%d]: %s", idx, dev.get("name") or "")
      return idx

    logger.warning("No sounddevice input device found. Falling back to system default.")
    return None

  def _configure_sounddevice_rate(self, dev: dict) -> None:
    native_rate = int(float(dev.get("default_samplerate") or self.TARGET_RATE))
    max_channels = int(dev.get("max_input_channels") or self.TARGET_CHANNELS)
    self.CAPTURE_CHANNELS = self.TARGET_CHANNELS if max_channels >= self.TARGET_CHANNELS else max_channels
    self.RATE = native_rate if native_rate > 0 else self.TARGET_RATE
    self.CHUNK = int(self.config["audio"]["chunk_size"] * self.RATE / self.TARGET_RATE)

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
    self._mic_status = None

    session_temp = self.temp_dir / session_id
    session_temp.mkdir(parents=True, exist_ok=True)

    try:
      output_path = self.recordings_dir / f"{session_id}.wav"
      output_path.parent.mkdir(parents=True, exist_ok=True)
      wav_writer = wave.open(str(output_path), "wb")
      wav_writer.setnchannels(self.TARGET_CHANNELS)
      wav_writer.setsampwidth(self._sample_width_bytes())
      wav_writer.setframerate(self.TARGET_RATE)
      self._output_path = output_path
      self._wav_writer = wav_writer
    except Exception:
      self.is_recording = False
      self.current_session_id = None
      self.stream = None
      self._output_path = None
      self._wav_writer = None
      logger.exception("Failed to open WAV for session %s", session_id)
      return False

    try:
      mic_index = self.find_mic_device()
      self.stream = self._open_input_stream(mic_index)
      self._emit_mic_status("connected")
    except Exception as exc:
      self.stream = None
      logger.warning(
        "Recording session %s started without an available mic; will keep retrying: %s",
        session_id,
        exc,
        exc_info=True,
      )
      self._emit_mic_status("waiting", message="No microphone available yet. Recording will attach when a mic is connected.")

    self._emit_event({
      "type": "recording_started",
      "session_id": session_id,
      "timestamp": datetime.now().isoformat(),
    })

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
      # Still emit so downstream can set recording_state back to idle
      sid = session_id_from_command or self.current_session_id
      self._emit_event({
        "type": "recording_stopped",
        "session_id": sid,
        "path": None,
        "timestamp": datetime.now().isoformat(),
      })
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

    self._stop_close_stream()

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
      self._emit_event({
        "type": "error",
        "error_type": "Upload Failed",
        "message": "Could not upload audio for cloud transcription/summarization.",
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
      })

    self.current_session_id = None
    self._mic_status = None
    return session_id

  def start_mic_test(self) -> bool:
    if self.is_recording:
      logger.warning("Mic test requested while recording is active")
      return False
    if self.is_mic_test:
      return True

    mic_index = self.find_mic_device()
    self.stream = self._open_input_stream(mic_index)
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

    self._stop_close_stream()
    logger.info("Mic test stopped")

  def mic_test_loop(self) -> None:
    try:
      while self.is_mic_test and not self.is_recording:
        assert self.stream is not None
        chunk = self._read_input_chunk()
        audio_bytes = self._prepare_audio_bytes(chunk)
        audio_bytes = self._apply_input_gain(audio_bytes)

        now = time.monotonic()
        if now - self._last_level_emit_at >= 0.08:
          samples = np.frombuffer(audio_bytes, dtype=np.int16)
          if len(samples) > 0:
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
            level = min(1.0, rms / 5000.0)
          else:
            level = 0.0
          self._emit_event({
            "type": "mic_test_level",
            "level": level,
            "timestamp": datetime.now().isoformat(),
          })
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

  def _apply_input_gain(self, audio_bytes: bytes) -> bytes:
    """Apply optional digital gain for quiet USB headset/dongle microphones."""
    if self.input_gain <= 1.0 or not audio_bytes:
      return audio_bytes
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
    samples *= self.input_gain
    samples = np.clip(samples, -32768, 32767).astype(np.int16)
    return samples.tobytes()

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

    return segment_path

  def _reopen_capture_stream(self) -> bool:
    """Tear down + reopen the active PortAudio input stream.

    Used by the recording-loop watchdog when reads start failing or the
    audio level stays at zero — the symptom the user reported as "the
    mic phone is not able to hear what i said" requiring a container
    restart. Returns ``True`` on success.
    """
    try:
      self._stop_close_stream()
    except Exception:  # noqa: BLE001
      logger.debug("Reopen: stop_close_stream failed", exc_info=True)
    try:
      mic_index = self.find_mic_device()
      self.stream = self._open_input_stream(mic_index)
      logger.warning("PortAudio capture stream reopened (mic_index=%s)", mic_index)
      self._emit_mic_status("connected")
      return True
    except Exception:
      logger.exception("Failed to reopen capture stream")
      self.stream = None
      self._emit_mic_status("waiting")
      return False

  def recording_loop(self) -> None:
    """
    Blocking loop that reads from the input stream and writes every chunk
    into the session WAV file. Self-heals on PortAudio read errors and on
    no-audio-for-too-long watchdog trips so the mic doesn't silently die
    (the cause of the "restart docker to make mic work again" symptom).
    """
    checked_audio = False
    last_successful_read_at = time.monotonic()
    silence_warning_logged_at = 0.0
    consecutive_read_errors = 0
    silent_audio_started_at: float | None = None
    # Keep the meeting alive across USB unplug/replug and mic swaps.
    # When no input stream is available, retry forever until stop_recording.
    RECONNECT_RETRY_S = 1.0
    READ_WATCHDOG_S = 5.0
    SILENT_REOPEN_S = 20.0
    SILENT_PEAK_THRESHOLD = 80
    last_reconnect_attempt_at = 0.0

    try:
      while self.is_recording:
        if self.stream is None:
          now = time.monotonic()
          if now - self._last_level_emit_at >= 1.0:
            self._emit_event({
              "type": "audio_level",
              "session_id": self.current_session_id,
              "level": 0.0,
              "timestamp": datetime.now().isoformat(),
            })
            self._last_level_emit_at = now
          if now - last_reconnect_attempt_at < RECONNECT_RETRY_S:
            time.sleep(0.2)
            continue
          last_reconnect_attempt_at = now
          logger.info("No active microphone stream; retrying mic discovery")
          if not self._reopen_capture_stream():
            time.sleep(RECONNECT_RETRY_S)
            continue
          consecutive_read_errors = 0
        try:
          chunk = self._read_input_chunk()
          consecutive_read_errors = 0
          last_successful_read_at = time.monotonic()
        except Exception:  # noqa: BLE001
          consecutive_read_errors += 1
          logger.warning(
            "PortAudio read failed (consecutive=%d); attempting recovery",
            consecutive_read_errors,
            exc_info=True,
          )
          self._emit_mic_status("waiting", message="Microphone disconnected or unavailable. Waiting for a working mic.")
          self._stop_close_stream()
          time.sleep(0.2)
          continue

        if self.is_paused:
          continue

        # Watchdog: if reads succeed but the stream is wedged (silent
        # buffer with no actual ALSA traffic), reopen it. Detected by
        # a long gap with no level > 0 emitted.
        now = time.monotonic()
        if now - last_successful_read_at > READ_WATCHDOG_S:
          if now - silence_warning_logged_at > READ_WATCHDOG_S:
            logger.warning(
              "Audio watchdog: no successful read in %.1fs — reopening stream",
              now - last_successful_read_at,
            )
            silence_warning_logged_at = now
          self._reopen_capture_stream()
          last_successful_read_at = now
          continue

        audio_bytes = self._prepare_audio_bytes(chunk)
        audio_bytes = self._apply_input_gain(audio_bytes)

        # Emit near-real-time audio level for UI waveform (throttled).
        if now - self._last_level_emit_at >= 0.08:
          samples = np.frombuffer(audio_bytes, dtype=np.int16)
          if len(samples) > 0:
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
            level = min(1.0, rms / 5000.0)
            peak = int(np.max(np.abs(samples)))
          else:
            level = 0.0
            peak = 0
          if peak <= SILENT_PEAK_THRESHOLD:
            if silent_audio_started_at is None:
              silent_audio_started_at = now
            elif now - silent_audio_started_at >= SILENT_REOPEN_S:
              logger.warning(
                "Audio stayed near-silent for %.0fs (peak<=%d); refreshing mic stream",
                now - silent_audio_started_at,
                SILENT_PEAK_THRESHOLD,
              )
              self._emit_mic_status("checking", message="Refreshing microphone because input stayed silent.")
              self._stop_close_stream()
              silent_audio_started_at = None
              continue
          else:
            silent_audio_started_at = None
          self._emit_event({
            "type": "audio_level",
            "session_id": self.current_session_id,
            "level": level,
            "timestamp": datetime.now().isoformat(),
          })
          self._last_level_emit_at = now

        if not checked_audio:
          self._check_silent_audio(audio_bytes, 0)
          checked_audio = True

        if self._wav_writer is None:
          raise RuntimeError("Recording file is not open")
        self._wav_writer.writeframes(audio_bytes)

    except Exception:
      logger.exception("Fatal error in recording loop; resetting recording state")
      self.is_recording = False
      self.is_paused = False

  def pause_recording(self) -> bool:
    if not self.is_recording:
      logger.warning("Pause requested while not recording")
      return False
    if self.is_paused:
      return True
    self.is_paused = True
    self._emit_event({
      "type": "recording_paused",
      "session_id": self.current_session_id,
      "timestamp": datetime.now().isoformat(),
    })
    self._emit_event({
      "type": "audio_level",
      "session_id": self.current_session_id,
      "level": 0.0,
      "timestamp": datetime.now().isoformat(),
    })
    logger.info("Recording paused - session %s", self.current_session_id)
    return True

  def resume_recording(self) -> bool:
    if not self.is_recording:
      logger.warning("Resume requested while not recording")
      return False
    if not self.is_paused:
      return True
    self.is_paused = False
    self._emit_event({
      "type": "recording_resumed",
      "session_id": self.current_session_id,
      "timestamp": datetime.now().isoformat(),
    })
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

  MAX_COMMAND_AGE = 60  # seconds — discard commands older than this

  def _dispatch_command(self, command: dict) -> None:
    ts = command.get("ts")
    if ts is not None:
      age = time.time() - float(ts)
      if age > self.MAX_COMMAND_AGE:
        logger.info(
          "Skipping stale command %s (%.0fs old)", command.get("action"), age,
        )
        return
    # Multi-device scoping: when this audio-capture knows its device
    # identity AND the incoming command targets a specific device, drop
    # commands targeted at a different device. Commands without a
    # ``device_id`` field are accepted (backward compat with old
    # publishers).
    cmd_device_id = (command.get("device_id") or "").strip()
    if self.device_id and cmd_device_id and cmd_device_id != self.device_id:
      logger.debug(
        "Skipping command %s for device_id=%s (ours=%s)",
        command.get("action"), cmd_device_id, self.device_id,
      )
      return
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

  def _refresh_auth_token(self) -> str:
    """Re-read the token from file/env so pairing after container start works."""
    token = _load_device_auth_token()
    self._upload_auth_token = token
    return token

  def _emit_event(self, payload: dict) -> None:
    """Write a single event to stdout for the device-ui supervisor to dispatch.

    The format is ``MEETINGBOX_EVT|<json>\\n``. ``audio_supervisor.py``
    scans stdout for this sentinel; non-event log lines pass through to
    the supervisor's logger as before. Best-effort: any I/O error is
    swallowed so a temporarily-closed stdout never crashes the capture
    thread.
    """
    if self.device_id and isinstance(payload, dict) and "device_id" not in payload:
      payload["device_id"] = self.device_id
    try:
      sys.stdout.write(f"{EVT_PREFIX}{json.dumps(payload)}\n")
      sys.stdout.flush()
    except (OSError, ValueError):
      logger.debug("emit_event failed", exc_info=True)

    # Mirror UI-relevant events to the local device bridge so the Flutter
    # recording screen (which subscribes to the bridge WebSocket) receives
    # live mic levels and recording state. Best-effort; never blocks capture.
    try:
      etype = payload.get("type") if isinstance(payload, dict) else None
      if etype == "audio_level":
        _publish_to_bridge({"type": "audio_level", "level": payload.get("level", 0.0)})
      elif etype in _BRIDGE_LIFECYCLE_STATE:
        _publish_to_bridge({"type": "recording_state", "state": _BRIDGE_LIFECYCLE_STATE[etype]})
    except Exception:  # noqa: BLE001 - forwarding is best-effort
      logger.debug("bridge forward failed", exc_info=True)

  def _resolve_device_id_via_api(self) -> str | None:
    """One-shot lookup of this mini-PC's device id via the backend.

    Used for multi-device command scoping. Best-effort: any HTTP /
    parsing / auth failure returns ``None`` and audio capture falls
    back to accepting all commands (legacy behaviour).
    """
    if not PAIRING_STATUS_URL or not self._upload_auth_token:
      return None
    try:
      req = urlrequest.Request(
        PAIRING_STATUS_URL,
        headers={"Authorization": f"Bearer {self._upload_auth_token}"},
        method="GET",
      )
      with urlrequest.urlopen(req, timeout=5.0) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
      data = json.loads(raw) if raw else {}
      did = (data.get("device_id") or "").strip() if isinstance(data, dict) else ""
      return did or None
    except Exception:  # noqa: BLE001
      logger.debug("device_id pairing-status lookup failed", exc_info=True)
      return None

  def run_http_poll(self) -> None:
    """
    Long-poll cloud API for recording commands.

    Requires DEVICE_AUTH_TOKEN and a reachable API; uses UPLOAD_AUDIO_API_URL or
    AUDIO_POLL_BASE_URL to find ``/api/device/audio-command/wait``. This is now
    the only command-source path — the appliance-side Redis was removed so the
    legacy ``run_redis`` subscriber no longer exists.
    """
    # Wait for a token to appear (pairing may happen after container start)
    while not self._upload_auth_token:
      logger.info(
        "Waiting for device auth token (pair this device from the dashboard). "
        "Checking %s every 10s…",
        DEVICE_AUTH_TOKEN_FILE,
      )
      time.sleep(10)
      self._refresh_auth_token()

    base = self._poll_command_api_base()
    url = f"{base}/api/device/audio-command/wait"
    logger.info("Command: HTTP long-poll %s (token=%s…)", url, self._upload_auth_token[:8])

    while True:
      # Re-read token each iteration so a re-pair is picked up without restart
      self._refresh_auth_token()
      if not self._upload_auth_token:
        logger.warning("Device auth token disappeared — waiting for re-pair…")
        time.sleep(10)
        continue

      try:
        req = urlrequest.Request(
          url,
          headers={"Authorization": f"Bearer {self._upload_auth_token}"},
        )
        with urlrequest.urlopen(req, timeout=90) as resp:
          status = getattr(resp, "status", None)
          if status is None:
            status = resp.getcode()
          if status == 204:
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

      try:
        self._dispatch_command(command)
      except Exception:
        logger.exception("Error handling audio command %s", command)

  def run(self) -> None:
    # Re-read token right now (may have been written after __init__)
    self._refresh_auth_token()
    # HTTP long-poll is the only command-source path now that the
    # appliance Redis has been removed. ``run_http_poll`` waits for a
    # token to appear, so we can enter it safely even before pairing.
    logger.info("Audio capture: HTTP long-poll for commands.")
    self.run_http_poll()


if __name__ == "__main__":
  service = AudioCaptureService()
  try:
    service.run()
  finally:
    service._stop_close_stream()
    service.audio.terminate()
    logger.info("PyAudio terminated")

