"""
MeetingBox Device UI – Main Application

Entry point for the MeetingBox device interface.
Implements the complete boot flow defined in the PRD:
  Splash → (first-boot? Welcome → WiFi → SetupProgress → AllSet →) Home
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Optional

import httpx

# Ensure the directory containing this file (src) is on sys.path so that
# imports of screens, components, config, api_client, etc. work regardless
# of how the app is run (e.g. python src/main.py vs python -m src.main).
_src_dir = Path(__file__).resolve().parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from xauthority_util import display_refers_to_screen_zero, xauthority_list_has_display_zero

# Before importing Kivy: stable clipboard provider on Linux (see kivy_options).
if sys.platform.startswith("linux"):
    os.environ.setdefault("KIVY_CLIPBOARD", "sdl2")

# SSH sessions often set DISPLAY=localhost:10.0; MoTTY then shows no window on
# the built-in monitor. Use local :0 if that socket exists (opt out:
# MEETINGBOX_KEEP_SSH_X=1). Must run before kivy.core.window is imported.
if (
    sys.platform.startswith("linux")
    and os.environ.get("MEETINGBOX_KEEP_SSH_X") != "1"
):
    _disp = os.environ.get("DISPLAY", "")
    if _disp.startswith("localhost:") and Path("/tmp/.X11-unix/X0").exists():
        os.environ["DISPLAY"] = ":0"

from kivy.app import App
from kivy.uix.screenmanager import (
    ScreenManager, FadeTransition, SlideTransition, NoTransition
)
from kivy.clock import Clock
from kivy.config import Config
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget

# All graphics Config.set() calls must happen BEFORE 'from kivy.core.window import Window'
# because Window is instantiated at import time in Kivy.
# Setting position/size/fullscreen after Window exists only partially works and
# causes the window to render at the wrong position (top-left or bottom-left).
_FULLSCREEN = os.getenv('FULLSCREEN', '0') == '1'


def _env_display_int(name: str, default: int) -> int:
    """Same rules as config._parse_display_px — must not raise; runs before config import."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        print(
            f"[MeetingBox] WARNING: {name} is set but empty; using default {default}",
            file=sys.stderr,
            flush=True,
        )
        return default
    try:
        v = int(s)
    except ValueError:
        print(
            f"[MeetingBox] WARNING: {name}={raw!r} is not an integer; using default {default}",
            file=sys.stderr,
            flush=True,
        )
        return default
    if v < 32 or v > 32768:
        print(
            f"[MeetingBox] WARNING: {name}={v} out of range [32,32768]; using default {default}",
            file=sys.stderr,
            flush=True,
        )
        return default
    return v


_W = _env_display_int("DISPLAY_WIDTH", 1024)
_H = _env_display_int("DISPLAY_HEIGHT", 600)

Config.set('graphics', 'window_state', 'visible')
Config.set('graphics', 'position', 'custom')
Config.set('graphics', 'left', '0')
Config.set('graphics', 'top', '0')
Config.set('graphics', 'width', str(_W))
Config.set('graphics', 'height', str(_H))
Config.set('graphics', 'borderless', '1' if _FULLSCREEN else '0')
Config.set('graphics', 'fullscreen', 'auto' if _FULLSCREEN else '0')
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
# On-screen keyboard for TextInput when no system keyboard (touch / kiosk).
Config.set('kivy', 'keyboard_mode', 'systemanddock')


def _configure_kivy_default_font() -> None:
    """
    Kivy's bundled Roboto lacks many symbols (and all emoji) used on the home screen,
    which show as empty boxes on Linux/SDL. Prefer DejaVu Sans when installed
    (fonts-dejavu-core in the device-ui Docker image), or MEETINGBOX_UI_FONT for one TTF.
    """
    from pathlib import Path

    env = os.environ.get("MEETINGBOX_UI_FONT", "").strip()
    paths: list[str] | None = None
    if env:
        ep = Path(env)
        if ep.is_file():
            p = str(ep.resolve())
            paths = [p, p, p, p]
    else:
        d = Path("/usr/share/fonts/truetype/dejavu")
        quad = (
            d / "DejaVuSans.ttf",
            d / "DejaVuSans-Oblique.ttf",
            d / "DejaVuSans-Bold.ttf",
            d / "DejaVuSans-BoldOblique.ttf",
        )
        if all(p.is_file() for p in quad):
            paths = [str(p) for p in quad]
    if not paths:
        return
    try:
        Config.set(
            "kivy",
            "default_font",
            [
                "MeetingBoxSans",
                paths[0],
                paths[1],
                paths[2],
                paths[3],
            ],
        )
    except Exception:
        pass


_configure_kivy_default_font()

from kivy.core.window import Window  # noqa: E402 — must import after Config


def _register_asta_fonts() -> None:
    """Register Asta Sans (42dot Sans) TTF files with Kivy's LabelBase.

    The font is open-source (OFL) and is shipped in assets/fonts/ as part of the
    device-ui image.  We register four named families so screen code can reference
    them by weight without synthesising faux-bold/italic:

        '42dot-Sans'  – Regular (fn_regular) + Bold (fn_bold)
        '42dot-SB'    – SemiBold (maps to fn_regular so bold=False works)
        '42dot-Med'   – Medium

    Falls back silently if the files are missing.
    """
    from pathlib import Path
    from kivy.core.text import LabelBase
    from config import ASSETS_DIR as _AD

    fd = _AD / "fonts"
    files = {
        "regular":   fd / "AstaSans-Regular.ttf",
        "bold":      fd / "AstaSans-Bold.ttf",
        "semibold":  fd / "AstaSans-SemiBold.ttf",
        "medium":    fd / "AstaSans-Medium.ttf",
    }
    if not all(p.is_file() for p in files.values()):
        return
    try:
        LabelBase.register(
            name="42dot-Sans",
            fn_regular=str(files["regular"]),
            fn_bold=str(files["bold"]),
        )
        LabelBase.register(
            name="42dot-SB",
            fn_regular=str(files["semibold"]),
            fn_bold=str(files["bold"]),
        )
        LabelBase.register(
            name="42dot-Med",
            fn_regular=str(files["medium"]),
            fn_bold=str(files["bold"]),
        )
    except Exception:
        pass


_register_asta_fonts()

from config import (
    DISPLAY_WIDTH,
    DISPLAY_HEIGHT,
    FULLSCREEN,
    TARGET_FPS,
    USE_MOCK_BACKEND,
    LOG_LEVEL,
    LOG_FILE,
    LOG_TO_CONSOLE,
    SHOW_FPS,
    SHOW_MOUSE_CURSOR,
    TRANSITION_DURATION,
    DEFAULT_PRIVACY_MODE,
    display_now,
    resolve_device_config_dir,
    setup_complete_marker_paths_for_read,
    get_device_auth_token,
    clear_stored_device_auth_token,
    WAKE_LOCAL_VOICE_ONLY,
)

from api_client import BackendClient
from mock_backend import MockBackendClient
from hardware import (
    request_system_poweroff,
    request_system_reboot,
    set_brightness,
)
from network_util import linux_ethernet_ready
from profile_store import get_active_profile, clear_active_profile_selection
from voice_assistant import VoiceAssistant, VoiceIntent, utterance_is_voice_farewell

try:
    from realtime_voice_session import REALTIME_VOICE_IMPLEMENTED
except ImportError:
    REALTIME_VOICE_IMPLEMENTED = False

# Boot-flow screens
from screens.splash import SplashScreen
from screens.welcome import WelcomeScreen
from screens.room_name import RoomNameScreen
from screens.network_choice import NetworkChoiceScreen
from screens.wifi_setup import WiFiSetupScreen
from screens.wifi_connected import WiFiConnectedScreen
from screens.pair_device import PairDeviceScreen
from screens.meetingbox_ready import MeetingBoxReadyScreen
from screens.setup_progress import SetupProgressScreen
from screens.all_set import AllSetScreen

# Core screens
from screens.home import HomeScreen
from screens.recording import RecordingScreen
from screens.processing import ProcessingScreen
from screens.complete import CompleteScreen
from screens.summary_review import SummaryReviewScreen
from screens.error import ErrorScreen
from screens.briefing import BriefingScreen
from screens.idle import IdleScreen

# Settings & sub-screens
from screens.settings import SettingsScreen
from screens.auto_delete_picker import AutoDeletePickerScreen
from screens.brightness_picker import BrightnessPickerScreen
from screens.brightness_slider import BrightnessSliderScreen
from screens.speech_volume_picker import SpeechVolumePickerScreen
from screens.notification_volume_picker import NotificationVolumePickerScreen
from screens.mic_gain_picker import MicGainPickerScreen
from screens.idle_timeout_picker import IdleTimeoutPickerScreen
from screens.mic_test import MicTestScreen
from screens.update_check import UpdateCheckScreen
from screens.update_install import UpdateInstallScreen
from screens.update_channel_picker import UpdateChannelPickerScreen
from screens.timezone_picker import TimezonePickerScreen
from screens.audio_sink_picker import AudioSinkPickerScreen
from screens.audio_source_picker import AudioSourcePickerScreen
from screens.wifi_forget_screen import WiFiForgetScreen
from screens.bluetooth_screen import BluetoothScreen
from screens.datetime_screen import DateTimeScreen
from screens.storage_breakdown import StorageBreakdownScreen
from screens.diagnostic_logs import DiagnosticLogsScreen
from screens.about_screen import AboutScreen
from screens.send_feedback import SendFeedbackScreen
from screens.notifications_settings import NotificationsSettingsScreen
from screens.security_settings import SecuritySettingsScreen
from screens.integration_detail import IntegrationDetailScreen
from screens.usb_info import UsbInfoScreen
from screens.room_label_screen import RoomLabelScreen
from screens.connectivity_check import ConnectivityCheckScreen

# Retained screens (still useful)
from screens.meetings import MeetingsScreen
from screens.meeting_detail import MeetingDetailScreen
from screens.wifi import WiFiScreen
from screens.system import SystemScreen
from screens.calendar import CalendarScreen
from screens.morning_brief import MorningBriefScreen
from screens.emails import EmailsScreen
from screens.tasks import TasksScreen
from components.quick_panel import QuickPanel

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

def setup_logging():
    handlers = []
    if LOG_TO_CONSOLE:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        handlers.append(ch)
    try:
        fh = logging.FileHandler(LOG_FILE)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        handlers.append(fh)
    except Exception as e:
        print(f"Warning: Could not create log file {LOG_FILE}: {e}")
    logging.basicConfig(level=getattr(logging, LOG_LEVEL), handlers=handlers)

setup_logging()
logger = logging.getLogger(__name__)

if _FULLSCREEN and _W == 1024 and _H == 600:
    logger.warning(
        "Kivy window size is default 1024×600 (DISPLAY_WIDTH/DISPLAY_HEIGHT). "
        "The home UI scales from that baseline, so on a large or ultrawide panel everything "
        "will look small until you set DISPLAY_WIDTH and DISPLAY_HEIGHT in mini-pc/.env to match "
        "`xrandr`, then `docker compose up -d --build device-ui` (or set "
        "MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1 in .env to auto-detect via xrandr)."
    )

# Import async helper (starts background loop on import)
from async_helper import run_async, get_async_loop

# Recording start/stop uses optional auth; a 401 on pairing-status during this flow
# would clear the local token and break summary/actions. Defer unpair until idle.
_PAIRING_UNPAIR_DEFER_SCREENS = frozenset({
    'recording',
    'processing',
    'summary_review',
    'complete',
})


def _recording_start_transient_network(exc: BaseException) -> bool:
    """True when a short wait and retry may succeed (e.g. after Wi‑Fi handover)."""
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (502, 503, 504)
    return False


def _recording_start_error_screen_args(exc: BaseException) -> tuple[str, str]:
    """(title, message) for show_error_screen after start_recording failure."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return (
                "Sign-in required",
                "This device could not authorize with the server. Pair the device again "
                "or check BACKEND_URL.",
            )
        if code >= 500:
            return (
                "Server error",
                f"The backend returned HTTP {code}. Confirm the API is running and "
                "reachable from this network.",
            )
        body = (exc.response.text or "").strip()
        snippet = body[:240] + ("…" if len(body) > 240 else "")
        return ("Recording failed", snippet or f"Server returned HTTP {code}.")
    if _recording_start_transient_network(exc):
        return (
            "Cannot reach server",
            "Could not connect to the MeetingBox backend. After switching networks (for "
            "example unplugging Ethernet and using Wi‑Fi), wait a few seconds, confirm this "
            "device can reach the server URL, then tap TRY AGAIN. If it keeps failing, check "
            "BACKEND_URL in the appliance configuration.",
        )
    msg = (str(exc) or "Unknown error").strip()
    if len(msg) > 400:
        msg = msg[:397] + "…"
    return ("Recording failed", msg)


def _tts_tail_silence_seconds() -> float:
    """After TTS playback, wait this long before reopening the mic (speaker tail / echo)."""
    raw = (os.getenv("MEETINGBOX_TTS_TAIL_SILENCE_SEC") or "").strip()
    if not raw:
        return 1.15
    try:
        v = float(raw)
    except ValueError:
        return 1.15
    return max(0.25, min(5.0, v))


def _post_tts_wake_guard_seconds() -> float:
    """After the mic reopens, suppress wake-word for this long (TTS still bleeding into mic)."""
    raw = (os.getenv("MEETINGBOX_POST_TTS_WAKE_GUARD_SEC") or "").strip()
    if not raw:
        return 0.42
    try:
        v = float(raw)
    except ValueError:
        return 0.6
    return max(0.0, min(4.0, v))


def _tts_aplay_device_argv() -> list[str]:
    """
    Extra aplay argv for ALSA playback device (speaker). Prefer default card unless
    the appliance needs e.g. -D pulse or -D plughw:CARD=X,DEV=Y (see MEETINGBOX_APLAY_DEVICE).
    """
    dev = (os.getenv("MEETINGBOX_APLAY_DEVICE") or "").strip()
    if dev:
        return ["-D", dev]
    return []


def _xauth_cookie_has_display(xauth_bin: str, auth_path: str, disp: str) -> bool:
    """True if xauth reports a cookie for this DISPLAY (matches X11, not our string heuristics)."""
    variants = [disp]
    if disp == ":0":
        variants.append(":0.0")
    elif disp.startswith(":0.") and len(disp) > 3 and disp[3:].isdigit():
        variants.append(":0")
    for d in variants:
        try:
            r = subprocess.run(
                [xauth_bin, "-f", auth_path, "list", d],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        if r.returncode == 0 and (r.stdout or "").strip():
            return True
    return False


def _diagnose_xauthority_for_docker():
    """Log hints when the mounted cookie cannot authorize DISPLAY=:0 (Docker + local X11)."""
    if not sys.platform.startswith("linux"):
        return
    path = (os.environ.get("XAUTHORITY") or "").strip()
    disp = (os.environ.get("DISPLAY") or "").strip()
    if not path:
        print("[MeetingBox] WARNING: XAUTHORITY is unset — X11 will usually reject the UI.", flush=True)
        return
    p = Path(path)
    if p.is_dir():
        print(
            f"[MeetingBox] FATAL: XAUTHORITY is a directory (bad bind). Remove it on the host and use "
            f"a real cookie file; path was: {path}",
            flush=True,
        )
        return
    if not p.is_file():
        return
    xauth_bin = shutil.which("xauth")
    if not xauth_bin:
        return
    try:
        r = subprocess.run(
            [xauth_bin, "-f", path, "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if r.returncode != 0:
            print(
                f"[MeetingBox] xauth list failed (exit {r.returncode}): {err or out[:400]}",
                flush=True,
            )
            return
        if not out:
            print(
                "[MeetingBox] WARNING: mounted Xauthority file has zero entries — "
                "mount the desktop user's cookie (see XAUTHORITY_HOST in .env).",
                flush=True,
            )
            return
        if display_refers_to_screen_zero(disp) and _xauth_cookie_has_display(
            xauth_bin, path, disp
        ):
            return
        if display_refers_to_screen_zero(disp) and not xauthority_list_has_display_zero(out):
            print(
                "[MeetingBox] WARNING: this Xauthority file has no :0 / unix:0 entry but "
                "DISPLAY is :0 (SSH or wrong file often only has :10). "
                "Fix: from a terminal ON THE BUILT-IN SCREEN run:  xauth list $DISPLAY\n"
                "then set XAUTHORITY_HOST in .env to that user's ~/.Xauthority or "
                "/run/user/$(id -u)/gdm/Xauthority",
                flush=True,
            )
    except Exception as ex:
        logger.debug("xauthority diagnose: %s", ex)


def _tts_english_child_env() -> dict:
    """Force English defaults for espeak-ng / Piper subprocesses (locale skew → wrong language)."""
    e = os.environ.copy()
    loc = (os.getenv("MEETINGBOX_TTS_LOCALE") or "en_US.UTF-8").strip()
    if loc:
        e["LANG"] = loc
        e["LC_ALL"] = loc
        e["LC_MESSAGES"] = loc
        e["LANGUAGE"] = "en_US:en"
    return e


def _pick_english_piper_model_path() -> str | None:
    """
    English-only Piper ONNX. Do not glob arbitrary **/*.onnx — first hit may be non‑English.
    Override with MEETINGBOX_PIPER_MODEL (full path).
    """
    import glob as _glob

    explicit = (os.getenv("MEETINGBOX_PIPER_MODEL") or "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit

    for p in (
        "/usr/share/piper/voices/en_US-amy-medium.onnx",
        "/usr/share/piper/voices/en_US-lessac-medium.onnx",
        "/usr/share/piper/voices/en_US-ryan-medium.onnx",
        "/usr/local/share/piper/en_US-amy-medium.onnx",
    ):
        if os.path.isfile(p):
            return p

    found: list[str] = []
    seen: set[str] = set()
    for pattern in (
        "/usr/share/piper/voices/en_US*.onnx",
        "/usr/share/piper/voices/en-us*.onnx",
        "/usr/share/piper/voices/en_*-*.onnx",
        "/usr/share/piper/voices/en-*-*.onnx",
        "/usr/local/share/piper/voices/en_US*.onnx",
        "/usr/local/share/piper/voices/en_*-*.onnx",
    ):
        for fp in _glob.glob(pattern):
            norm = os.path.normpath(fp)
            if os.path.isfile(norm) and norm not in seen:
                seen.add(norm)
                found.append(norm)

    def _key(path: str) -> tuple[int, str]:
        bn = os.path.basename(path).lower()
        if bn.startswith("en_us") or bn.startswith("en-us"):
            return (0, bn)
        if bn.startswith("en_gb") or bn.startswith("en-gb"):
            return (5, bn)
        if bn.startswith(("en_", "en-")):
            return (10, bn)
        return (50, bn)

    found.sort(key=_key)
    return found[0] if found else None


# ==================================================================
# Application
# ==================================================================

class MeetingBoxApp(App):
    """
    Main Kivy application for the MeetingBox device UI.

    Manages:
    - Screen navigation with transitions
    - Navigation history stack
    - Backend API connection + WebSocket events
    - Application state (recording, privacy, etc.)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Backend client
        if USE_MOCK_BACKEND:
            self.backend = MockBackendClient()
            logger.info("Using MOCK backend")
        else:
            self.backend = BackendClient()
            logger.info("Using REAL backend")

        # Multi-device scoping: this device's id, resolved from
        # /api/device/pairing-status after we know we're paired (see
        # ``_refresh_device_identity``). When set, WS-relayed events
        # carrying a ``device_id`` field for a different device are
        # ignored so two paired mini-PCs sharing a Redis don't see
        # each other's recording lifecycle.
        self.device_id: Optional[str] = None

        # Application state
        self.current_session_id = None
        self.recording_state = {
            'active': False,
            'paused': False,
            'elapsed': 0,
            'speaker_count': 0,
        }
        # Set when WS `transcription_complete` fires; cleared on new recording.
        # Lets the processing screen enable the summary CTA even if that event
        # arrived before navigation to `processing`.
        self._transcription_done_for_session = None
        self.privacy_mode = DEFAULT_PRIVACY_MODE
        self.device_name = 'MeetingBox'
        self.auto_record = False
        self.setup_language = 'English (US)'
        self.current_user_id = None
        self.current_display_name = None
        self.connected_wifi_ssid = ''
        self.setup_network_is_ethernet = False
        self.paired_owner_email = None

        # Screen manager & nav stack
        self.screen_manager = None
        self._nav_stack = []

        # Summary-ready fallback poll state (used when the `summary_complete` WS
        # event is missed while the processing screen is shown).
        self._summary_poll_meeting_id = None
        self._summary_poll_done = False

        # Transcript CTA fallback: enable "View Meeting Summary" when segments
        # exist in the API even if `transcription_complete` never hits the WS.
        self._transcript_cta_poll_meeting_id = None
        self._transcript_cta_satisfied_meeting_id = None

        # Restore processing UI if summary/transcript-ready arrived before the processing screen.
        self._processing_summary_cache = {}
        # Lightweight shared data cache for instant screen paint (stale-while-refresh).
        self._ui_data_cache: dict = {}
        self._ui_data_cache_ts: dict[str, float] = {}
        self._ui_data_cache_ttl: dict[str, float] = {
            "emails_inbox": 45.0,
            "emails_sent": 120.0,
            "emails_drafts": 120.0,
            "calendar_week": 45.0,
            "morning_brief_context": 45.0,
            "morning_brief_gmail": 45.0,
            "home_summary_bundle": 30.0,
        }
        self._ui_cache_inflight: set[str] = set()
        self._ui_cache_subscribers: dict[str, list] = defaultdict(list)
        self._ui_sync_event = None
        self._ui_sync_inflight = False
        # Local read/unread overrides to prevent temporary Gmail lag from
        # flipping home unread counts back and forth.
        self._email_read_overrides: dict[str, bool] = {}

        # WebSocket
        self.ws_task = None
        self._pairing_poll = None

        # Local Redis subscriber (audio_level / mic_test_level from audio process)
        # Optional in-process audio capture child (MEETINGBOX_SPAWN_AUDIO=1, the
        # default inside the merged docker image). Replaces the separate audio
        # container / systemd unit. Never enable simultaneously with another
        # audio_capture instance — they would race for the mic.
        # Events flow back via stdout sentinel lines (``MEETINGBOX_EVT|...``)
        # parsed by the supervisor and dispatched to the same handlers the
        # old local Redis listener used.
        from audio_supervisor import maybe_create_from_env as _maybe_audio
        self._audio_supervisor = _maybe_audio(on_event=self._on_audio_supervisor_event)

        # Idle screen timeout (seconds; 0 = never).
        # Replaces the older display-off timer: instead of cutting the
        # backlight we navigate to the dedicated `idle` screen, which is
        # itself dismissed by any touch (back to home).
        self._idle_timeout_seconds = 600
        self._idle_event = None

        # Manual user pause for the wake-word assistant (toggled from the
        # home Listening pill). When True, _voice_assistant_should_listen
        # returns False even on listen-eligible screens. This is a UI knob,
        # not a privacy setting — flipping it here doesn't touch the
        # backend ``privacy_mode`` flag.
        self.user_voice_paused = False

        # Voice UI / feedback
        self.root_layout = None
        self._transcript_overlay = None
        self._pending_user_msg_id: str | None = None
        self.voice_indicator = None
        self._voice_indicator_override = None
        self._voice_indicator_reset_ev = None
        self._voice_start_in_flight = False
        self._voice_start_confirmation_pending = False
        self._voice_pending_confirmation: VoiceIntent | None = None
        self._voice_confirmation_reset_ev = None
        self._voice_confirmation_timeout = 8.0
        self._voice_recording_suspended = False
        self._recording_elapsed_started_at = None
        self._recording_elapsed_before_pause = 0.0
        self.voice_confirmation_text = (
            (os.getenv("VOICE_ASSISTANT_CONFIRMATION_TEXT") or "Meeting started").strip()
            or "Meeting started"
        )

        # Local voice control (wake phrase + command).
        self.voice_assistant = VoiceAssistant(
            self._handle_voice_intent,
            on_wake_phrase=self._handle_voice_wake_phrase,
            on_amplitude=self._handle_voice_amplitude,
            on_conversation_turn=self._handle_voice_conversation_turn,
        )
        self._voice_confirmation_timeout = self.voice_assistant.confirmation_timeout_seconds
        self._last_amplitude_sched = 0.0

        # OpenAI Realtime assistant (optional; uses server /api/voice + wake phrase).
        self._realtime_voice_session = None
        self._realtime_session_pending = False
        self._realtime_session_start_monotonic = None
        self._realtime_connected_ok = False
        self._realtime_mic_acquired = False
        self._voice_runtime_state = "idle"
        self.voice_realtime_assistant = False
        # Sync interpreter to the UI default immediately so wake works before
        # async device-settings load (VoiceAssistant env-var default is "hey tony").
        self.voice_wake_phrase_display = "Hey Tony"
        self.voice_assistant.apply_server_settings(wake_phrase="hey tony")
        self.voice_assistant_enabled = True
        self.assistant_speech_volume = 85
        # Realtime may only start when _handle_voice_wake_phrase sets this True (one-shot).
        self._realtime_launch_permitted = False
        # Limits cloud NL replies per wake/mic activation (local wake listening unaffected).
        self._voice_cloud_qa_budget = 0
        # Serialises TTS calls — overlapping replies are dropped, not stacked
        self._speaking_lock = threading.Lock()
        # Monotonic timestamp of when the last TTS finished playing.
        # Used to suppress wake-phrase re-detection for a few seconds after the
        # assistant speaks (prevents TTS audio echo from restarting the loop).
        self._last_tts_end_monotonic: float = 0.0
        # OpenAI Realtime plays via aplay (not _speak_text_blocking); extend the same
        # idea so speaker tail / room echo does not immediately re-trigger wake.
        self._wake_suppress_until_monotonic: float = 0.0

        # One-shot startup diagnostics overlay (see _run_startup_self_test_overlay).
        self._startup_self_test_started = False

    # ==================================================================
    # SHARED UI CACHE (stale-while-refresh)
    # ==================================================================
    def ui_cache_get(self, key: str):
        return self._ui_data_cache.get(key)

    def ui_cache_set(self, key: str, value):
        self._ui_data_cache[key] = value
        self._ui_data_cache_ts[key] = time.time()
        self._ui_cache_persist_to_disk()
        for cb in list(self._ui_cache_subscribers.get(key, [])):
            try:
                cb(value)
            except Exception:
                logger.debug("ui_cache subscriber failed for %s", key, exc_info=True)

    def ui_cache_is_fresh(self, key: str, ttl_s: float | None = None) -> bool:
        ts = self._ui_data_cache_ts.get(key)
        if ts is None:
            return False
        ttl = float(ttl_s if ttl_s is not None else self._ui_data_cache_ttl.get(key, 60.0))
        return (time.time() - ts) <= ttl

    def ui_cache_subscribe(self, key: str, callback):
        if callback not in self._ui_cache_subscribers[key]:
            self._ui_cache_subscribers[key].append(callback)

    def ui_cache_unsubscribe(self, key: str, callback):
        try:
            self._ui_cache_subscribers[key].remove(callback)
        except Exception:
            pass

    def ui_cache_mark_inflight(self, key: str) -> bool:
        if key in self._ui_cache_inflight:
            return False
        self._ui_cache_inflight.add(key)
        return True

    def ui_cache_clear_inflight(self, key: str):
        self._ui_cache_inflight.discard(key)

    def ui_set_email_read_override(self, email_id: str, is_read: bool):
        if not email_id:
            return
        self._email_read_overrides[str(email_id)] = bool(is_read)

    def ui_apply_email_read_overrides(self, feed: dict) -> dict:
        if not isinstance(feed, dict):
            return feed
        rows = feed.get("messages")
        if not isinstance(rows, list):
            return feed
        changed = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = row.get("id")
            if not mid:
                continue
            if mid in self._email_read_overrides:
                want_read = self._email_read_overrides[mid]
                cur_read = bool(row.get("is_read", True))
                if cur_read != want_read:
                    row["is_read"] = want_read
                    changed = True
                else:
                    # Backend has caught up to local override; stop forcing.
                    self._email_read_overrides.pop(mid, None)
        return feed if changed else feed

    def _ui_cache_file_path(self) -> Path:
        return resolve_device_config_dir() / "ui_data_cache.json"

    def _ui_cache_persist_to_disk(self) -> None:
        try:
            payload = {
                "saved_at": time.time(),
                "cache": self._ui_data_cache,
            }
            p = self._ui_cache_file_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            logger.debug("ui cache persist skipped", exc_info=True)

    def _ui_cache_load_from_disk(self) -> None:
        try:
            p = self._ui_cache_file_path()
            if not p.is_file():
                return
            payload = json.loads(p.read_text(encoding="utf-8"))
            cache = payload.get("cache") if isinstance(payload, dict) else None
            if not isinstance(cache, dict):
                return
            now = time.time()
            for k, v in cache.items():
                # Trust persisted cache for fast first paint; network refresh follows.
                self._ui_data_cache[k] = v
                self._ui_data_cache_ts[k] = now
            logger.info("Loaded UI cache from disk (%d keys)", len(cache))
        except Exception:
            logger.debug("ui cache load skipped", exc_info=True)

    async def _ui_cache_bootstrap_async(self) -> None:
        """Prewarm cold-start data so first screen opens are near-instant."""
        if USE_MOCK_BACKEND:
            return
        try:
            if self.ui_cache_mark_inflight("emails_inbox"):
                try:
                    data = await self.backend.fetch_gmail_recent(
                        max_results=50,
                        days=90,
                        q="",
                        folder="all",
                    )
                    rows = data.get("messages") or []
                    if isinstance(rows, list):
                        self.ui_cache_set("emails_inbox", list(rows))
                finally:
                    self.ui_cache_clear_inflight("emails_inbox")
        except Exception:
            logger.debug("emails bootstrap prewarm failed", exc_info=True)

        try:
            monday = display_now().date() - timedelta(days=display_now().date().weekday())
            key = f"calendar_week:{monday.isoformat()}"
            if self.ui_cache_mark_inflight(key):
                try:
                    end_d = monday + timedelta(days=6)
                    data = await self.backend.get_calendar_week(
                        monday.isoformat(),
                        end_d.isoformat(),
                    )
                    if isinstance(data, dict):
                        self.ui_cache_set(key, dict(data))
                finally:
                    self.ui_cache_clear_inflight(key)
        except Exception:
            logger.debug("calendar bootstrap prewarm failed", exc_info=True)

        try:
            if self.ui_cache_mark_inflight("morning_brief_context"):
                try:
                    ctx = await self.backend.get_briefing_context(days_ahead=1)
                    if isinstance(ctx, dict):
                        self.ui_cache_set("morning_brief_context", dict(ctx))
                finally:
                    self.ui_cache_clear_inflight("morning_brief_context")
        except Exception:
            logger.debug("morning brief context prewarm failed", exc_info=True)

        try:
            if self.ui_cache_mark_inflight("morning_brief_gmail"):
                try:
                    gf = await self.backend.fetch_gmail_recent(
                        max_results=40,
                        days=90,
                        q="",
                        folder="all",
                    )
                    if isinstance(gf, dict):
                        self.ui_cache_set("morning_brief_gmail", dict(gf))
                finally:
                    self.ui_cache_clear_inflight("morning_brief_gmail")
        except Exception:
            logger.debug("morning brief gmail prewarm failed", exc_info=True)

    async def _ui_cache_refresh_once(self) -> None:
        """Unified realtime-ish refresh: fetch once, fan out to all UI caches."""
        if USE_MOCK_BACKEND:
            return
        today = display_now().date()
        monday = today - timedelta(days=today.weekday())
        week_key = f"calendar_week:{monday.isoformat()}"
        end_d = monday + timedelta(days=6)
        try:
            results = await asyncio.gather(
                self.backend.fetch_gmail_recent(max_results=50, days=90, q="", folder="all"),
                self.backend.get_calendar_week(monday.isoformat(), end_d.isoformat()),
                self.backend.get_briefing_context(days_ahead=1),
                self.backend.get_home_summary(),
                self.backend.get_meetings(limit=1),
                return_exceptions=True,
            )
            gfeed = results[0] if not isinstance(results[0], BaseException) else {}
            week = results[1] if not isinstance(results[1], BaseException) else {}
            brief = results[2] if not isinstance(results[2], BaseException) else {}
            home_summary = results[3] if not isinstance(results[3], BaseException) else {}
            meetings = results[4] if not isinstance(results[4], BaseException) else []

            if isinstance(gfeed, dict):
                gfeed = self.ui_apply_email_read_overrides(dict(gfeed))
                rows = gfeed.get("messages") or []
                if isinstance(rows, list):
                    self.ui_cache_set("emails_inbox", list(rows))
                self.ui_cache_set("morning_brief_gmail", dict(gfeed))
            if isinstance(week, dict):
                self.ui_cache_set(week_key, dict(week))
            if isinstance(brief, dict):
                self.ui_cache_set("morning_brief_context", dict(brief))
            if isinstance(home_summary, dict):
                self.ui_cache_set(
                    "home_summary_bundle",
                    {
                        "summary": dict(home_summary),
                        "meetings": meetings if isinstance(meetings, list) else [],
                        "gfeed": gfeed if isinstance(gfeed, dict) else {},
                    },
                )
        except Exception:
            logger.debug("ui cache unified refresh failed", exc_info=True)

    def _ui_cache_sync_tick(self, _dt) -> None:
        if self._ui_sync_inflight:
            return
        self._ui_sync_inflight = True

        async def _go():
            try:
                await self._ui_cache_refresh_once()
            finally:
                self._ui_sync_inflight = False

        run_async(_go())

    # ==================================================================
    # BUILD
    # ==================================================================

    def build(self):
        logger.info("Building MeetingBox UI (fullscreen=%s, size=%sx%s)",
                    FULLSCREEN, DISPLAY_WIDTH, DISPLAY_HEIGHT)

        # Window geometry and fullscreen are already set via Config.set() at
        # module load time (before Window was imported), so no runtime
        # Window.size / Window.fullscreen calls needed here.

        # Show cursor in windowed mode (mouse/desktop). In fullscreen, hide it unless
        # SHOW_MOUSE_CURSOR=1 (USB mouse on a touch panel) or SHOW_FPS (dev overlay).
        # If X11 auth failed, SDL never creates a window — show_cursor crashes internally.
        try:
            Window.show_cursor = (not FULLSCREEN) or SHOW_FPS or SHOW_MOUSE_CURSOR
        except (AttributeError, TypeError) as e:
            # Do not crash the process: Docker restart loops look like a flickering panel with no UI.
            logger.error(
                "Kivy window not ready for show_cursor (X11/auth?). Continuing. "
                "Fix: xhost +local:docker and correct XAUTHORITY mount. Detail: %s",
                e,
            )

        self.root_layout = FloatLayout()

        # Screen manager – default to fade transition
        self.screen_manager = ScreenManager(
            transition=FadeTransition(duration=TRANSITION_DURATION['fade']))
        self.screen_manager.size_hint = (1, 1)
        self.root_layout.add_widget(self.screen_manager)

        # Register ALL screens
        self.screen_manager.add_widget(SplashScreen(name='splash'))
        self.screen_manager.add_widget(WelcomeScreen(name='welcome'))
        self.screen_manager.add_widget(RoomNameScreen(name='room_name'))
        self.screen_manager.add_widget(NetworkChoiceScreen(name='network_choice'))
        self.screen_manager.add_widget(WiFiSetupScreen(name='wifi_setup'))
        self.screen_manager.add_widget(WiFiConnectedScreen(name='wifi_connected'))
        self.screen_manager.add_widget(PairDeviceScreen(name='pair_device'))
        self.screen_manager.add_widget(MeetingBoxReadyScreen(name='meetingbox_ready'))
        self.screen_manager.add_widget(SetupProgressScreen(name='setup_progress'))
        self.screen_manager.add_widget(AllSetScreen(name='all_set'))

        self.screen_manager.add_widget(HomeScreen(name='home'))
        self.screen_manager.add_widget(RecordingScreen(name='recording'))
        self.screen_manager.add_widget(ProcessingScreen(name='processing'))
        self.screen_manager.add_widget(CompleteScreen(name='complete'))
        self.screen_manager.add_widget(SummaryReviewScreen(name='summary_review'))
        self.screen_manager.add_widget(ErrorScreen(name='error'))
        self.screen_manager.add_widget(BriefingScreen(name='briefing'))
        self.screen_manager.add_widget(IdleScreen(name='idle'))

        self.screen_manager.add_widget(SettingsScreen(name='settings'))
        self.screen_manager.add_widget(AutoDeletePickerScreen(name='auto_delete_picker'))
        self.screen_manager.add_widget(BrightnessPickerScreen(name='brightness_picker'))
        self.screen_manager.add_widget(BrightnessSliderScreen(name='brightness_slider'))
        self.screen_manager.add_widget(SpeechVolumePickerScreen(name='speech_volume_picker'))
        self.screen_manager.add_widget(NotificationVolumePickerScreen(name='notification_volume_picker'))
        self.screen_manager.add_widget(MicGainPickerScreen(name='mic_gain_picker'))
        self.screen_manager.add_widget(IdleTimeoutPickerScreen(name='idle_timeout_picker'))
        self.screen_manager.add_widget(MicTestScreen(name='mic_test'))
        self.screen_manager.add_widget(UpdateCheckScreen(name='update_check'))
        self.screen_manager.add_widget(UpdateInstallScreen(name='update_install'))
        self.screen_manager.add_widget(UpdateChannelPickerScreen(name='update_channel_picker'))
        self.screen_manager.add_widget(TimezonePickerScreen(name='timezone_picker'))
        self.screen_manager.add_widget(AudioSinkPickerScreen(name='audio_output_picker'))
        self.screen_manager.add_widget(AudioSourcePickerScreen(name='audio_input_picker'))
        self.screen_manager.add_widget(WiFiForgetScreen(name='wifi_forget_screen'))
        self.screen_manager.add_widget(BluetoothScreen(name='bluetooth_screen'))
        self.screen_manager.add_widget(DateTimeScreen(name='datetime_screen'))
        self.screen_manager.add_widget(StorageBreakdownScreen(name='storage_breakdown'))
        self.screen_manager.add_widget(DiagnosticLogsScreen(name='diagnostic_logs'))
        self.screen_manager.add_widget(AboutScreen(name='about_screen'))
        self.screen_manager.add_widget(SendFeedbackScreen(name='send_feedback'))
        self.screen_manager.add_widget(NotificationsSettingsScreen(name='notifications_settings'))
        self.screen_manager.add_widget(SecuritySettingsScreen(name='security_settings'))
        self.screen_manager.add_widget(IntegrationDetailScreen(name='integration_detail'))
        self.screen_manager.add_widget(UsbInfoScreen(name='usb_info'))
        self.screen_manager.add_widget(RoomLabelScreen(name='room_label_screen'))
        self.screen_manager.add_widget(ConnectivityCheckScreen(name='connectivity_check'))

        self.screen_manager.add_widget(MeetingsScreen(name='meetings'))
        self.screen_manager.add_widget(MeetingDetailScreen(name='meeting_detail'))
        self.screen_manager.add_widget(WiFiScreen(name='wifi'))
        self.screen_manager.add_widget(SystemScreen(name='system'))
        self.screen_manager.add_widget(CalendarScreen(name='calendar'))
        self.screen_manager.add_widget(MorningBriefScreen(name='morning_brief'))
        self.screen_manager.add_widget(EmailsScreen(name='emails'))
        self.screen_manager.add_widget(TasksScreen(name='tasks'))

        # BOOT: always start with splash
        self.screen_manager.current = 'splash'

        # Start WebSocket listener
        self.start_websocket_listener()

        # Audio_level / lifecycle events from the audio_capture child are
        # delivered via audio_supervisor stdout (see
        # ``_on_audio_supervisor_event``). Nothing to start here.
        # Voice assistant logic (wake phrase, intents) stays active; the floating
        # "Tony" overlay is intentionally not mounted. ``self.voice_indicator``
        # remains None (set in __init__) so existing _refresh/_set helpers no-op
        # via their ``if not self.voice_indicator`` guards.
        self._sync_voice_assistant_state()
        self._refresh_voice_indicator()

        # Email draft popup + recipient confirmation overlay (voice-first email
        # workflow). Added BEFORE the transcript overlay so the transcript bar
        # renders on top and stays visible while drafting. Both start hidden.
        try:
            from components.email_draft_popup import EmailDraftPopup
            self._email_draft_popup = EmailDraftPopup(
                on_send=self._on_email_draft_send_tapped,
                on_save_draft=self._on_email_draft_save_tapped,
                on_discard=self._on_email_draft_discard_tapped,
                on_close=self._on_email_draft_close_tapped,
            )
            self.root_layout.add_widget(self._email_draft_popup)
        except Exception:
            logger.exception("EmailDraftPopup failed to load")
            self._email_draft_popup = None

        try:
            from components.recipient_confirm_overlay import RecipientConfirmOverlay
            self._recipient_overlay = RecipientConfirmOverlay(
                on_select=self._on_recipient_selected,
                on_dismiss=self._on_recipient_dismissed,
            )
            self.root_layout.add_widget(self._recipient_overlay)
        except Exception:
            logger.exception("RecipientConfirmOverlay failed to load")
            self._recipient_overlay = None

        # Transcript overlay — floats above everything, starts hidden.
        try:
            from components.transcription_overlay import TranscriptionOverlay
            self._transcript_overlay = TranscriptionOverlay()
            self.root_layout.add_widget(self._transcript_overlay)
            # Switch between full-screen mode (on home) and compact bottom-
            # strip mode (on every other screen) whenever the user navigates.
            self.screen_manager.bind(
                current=lambda _sm, name: self._sync_transcript_overlay_mode(name)
            )
        except Exception:
            logger.exception("TranscriptionOverlay failed to load")

        # Quick pull-down panel — floats above everything, hidden by default.
        try:
            self.quick_panel = QuickPanel()
            self.quick_panel.app = self
            self.root_layout.add_widget(self.quick_panel)
        except Exception:
            logger.exception("QuickPanel failed to load")
            self.quick_panel = None

        # Swipe handle — thin bar at the very top of the screen.
        # Added LAST so it is drawn and hit-tested before other widgets.
        # Uses touch.grab() so it is coordinate-system-independent.
        try:
            _handle = _SwipeHandle(app=self)
            self.root_layout.add_widget(_handle)
        except Exception:
            logger.exception("SwipeHandle failed to load")

        if SHOW_FPS:
            Clock.schedule_interval(self._log_fps, 1.0)

        Window.bind(on_touch_down=self._reset_idle_timer)

        # Ensure the SDL window is mapped and on top (some WMs / SSH DISPLAY
        # combinations leave it hidden until raised).
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0)
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0.3)

        logger.info("UI built – starting on splash screen")
        return self.root_layout

    def _ensure_window_visible(self):
        try:
            if hasattr(Window, 'show'):
                Window.show()
            if hasattr(Window, 'raise_window'):
                Window.raise_window()
        except Exception as e:
            logger.debug('ensure_window_visible: %s', e)

    # ==================================================================
    # SETUP CHECK
    # ==================================================================

    def needs_setup(self) -> bool:
        if USE_MOCK_BACKEND:
            return False
        # Check shared config volume (mounted at /data/config in Docker,
        # falls back to /opt/meetingbox for bare-metal installs)
        for marker_path in setup_complete_marker_paths_for_read():
            if marker_path.exists():
                return False
        return True

    def clear_local_setup_markers_best_effort(self) -> None:
        """Remove `.setup_complete` files this UI treats as authoritative."""
        for marker_path in setup_complete_marker_paths_for_read():
            try:
                marker_path.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Could not remove setup marker %s: %s", marker_path, e)

    def reenter_onboarding_after_remote_reset(self):
        """After API factory reset: markers may be gone before reboot completes."""
        self.clear_local_setup_markers_best_effort()
        if not self.needs_setup():
            logger.warning(
                "Factory reset did not leave the device in first-boot state "
                "(a setup-complete marker may still exist on disk).")
            return
        if getattr(self, '_setup_poll', None):
            self._setup_poll.cancel()
            self._setup_poll = None
        self._setup_poll = Clock.schedule_interval(self._global_setup_check, 3.0)
        self.goto_screen('splash', 'fade')

    def on_account_unpaired(self, remote: bool = False):
        """Clear local pairing after dashboard or device-initiated unpair."""
        logger.info("Device unlinked from account (%s)",
                    "remote revoke" if remote else "local unpair")
        clear_stored_device_auth_token()
        self.backend.set_device_auth_header(None)
        self.current_user_id = None
        self.current_display_name = None
        self.paired_owner_email = None
        try:
            clear_active_profile_selection()
        except Exception as e:
            logger.debug("clear_active_profile_selection: %s", e)
        self._nav_stack.clear()
        self.goto_screen('pair_device', 'fade')

    def _pairing_watchdog(self, _dt):
        if USE_MOCK_BACKEND:
            return
        tok = get_device_auth_token().strip()
        if not tok:
            return
        self.backend.set_device_auth_header(tok)
        run_async(self._pairing_watchdog_async())

    async def _pairing_watchdog_async(self):
        try:
            status = await self.backend.get_pairing_status()
            # Cache our device id from the same response so multi-device
            # event filtering knows our identity without an extra round
            # trip. Best-effort: bad/empty values just leave it unset.
            try:
                did = (status or {}).get("device_id") if isinstance(status, dict) else None
                if isinstance(did, str) and did and did != self.device_id:
                    self.device_id = did
                    logger.info("Resolved device_id=%s", did)
            except Exception:
                logger.debug("device_id cache update failed", exc_info=True)
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 401:
                detail = None
                try:
                    body = e.response.json()
                    d = body.get('detail')
                    if isinstance(d, str):
                        detail = d
                except Exception:
                    pass
                logger.warning("Pairing status 401 (%s)", detail or 'no detail')

                def _apply_remote_unpair(_clk):
                    cur = self.screen_manager.current
                    if cur in _PAIRING_UNPAIR_DEFER_SCREENS:
                        logger.warning(
                            "Deferring remote unpair while on screen %s "
                            "(recording pipeline does not require device Bearer)",
                            cur,
                        )
                        return
                    self.on_account_unpaired(remote=True)

                Clock.schedule_once(_apply_remote_unpair, 0)
        except Exception as e:
            logger.debug("Pairing check skipped: %s", e)

    # ==================================================================
    # APP LIFECYCLE
    # ==================================================================

    def on_start(self):
        logger.info("MeetingBox UI started")
        self._ui_cache_load_from_disk()
        if self._audio_supervisor is not None:
            try:
                self._audio_supervisor.start()
            except Exception:
                logger.exception("Failed to start in-process audio supervisor")
        self.voice_assistant.start()
        self._sync_voice_assistant_state()
        if not USE_MOCK_BACKEND:
            # Always align the HTTP client Bearer with persisted token (file may differ from __init__).
            tok = get_device_auth_token().strip()
            if tok:
                self.backend.set_device_auth_header(tok)
        # Defer only until Kivy/async loop are up; reach API quickly after boot/restart.
        Clock.schedule_once(self._check_backend, 0.35)
        # Idle + home both consume weather; start the singleton refresh loop
        # once here so it's running by the time those screens are entered.
        try:
            from weather_client import get_weather_client

            get_weather_client().start(refresh_seconds=900)
        except Exception as e:  # noqa: BLE001
            logger.debug("weather client start failed: %s", e)
        # Kick off the idle countdown immediately so a freshly-booted device
        # that gets no touches still falls asleep into the lock screen.
        self._reset_idle_timer()
        if self.needs_setup():
            self._setup_poll = Clock.schedule_interval(self._global_setup_check, 3.0)
        else:
            self._setup_poll = None
        if not USE_MOCK_BACKEND:
            self._pairing_poll = Clock.schedule_interval(
                self._pairing_watchdog, 45.0)
            self._metrics_push = Clock.schedule_interval(
                self._push_appliance_metrics_tick, 30.0)
            Clock.schedule_once(lambda _dt: self._push_appliance_metrics_tick(0), 6.0)
        else:
            self._metrics_push = None

        # After boot-time API bursts settle, run connectivity / mic / model checks once.
        # A slightly later start avoids transient false-negatives during initial network churn.
        Clock.schedule_once(self._run_startup_self_test_overlay, 8.0)
        # Cold-start prewarm for instant first-open calendar/emails.
        Clock.schedule_once(lambda _dt: run_async(self._ui_cache_bootstrap_async()), 0.8)
        # Centralized sync loop to keep caches hot across all screens.
        if self._ui_sync_event:
            self._ui_sync_event.cancel()
        self._ui_sync_event = Clock.schedule_interval(self._ui_cache_sync_tick, 5.0)
        Clock.schedule_once(lambda _dt: self._ui_cache_sync_tick(0), 1.6)

    def _run_startup_self_test_overlay(self, _dt):
        """Boot-time self-test modal (disable with MEETINGBOX_STARTUP_SELF_TEST=0)."""
        raw = (os.environ.get("MEETINGBOX_STARTUP_SELF_TEST") or "1").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return
        # Do not interrupt active user navigation. If the user has already
        # left home during boot, skip this one-shot overlay entirely.
        try:
            cur = self.screen_manager.current if self.screen_manager is not None else ""
            if cur and cur != "home":
                logger.info(
                    "Skipping startup self-test overlay (current screen=%s, user already navigating).",
                    cur,
                )
                return
        except Exception:
            pass
        if self._startup_self_test_started:
            return
        self._startup_self_test_started = True
        try:
            from components.startup_self_test_overlay import StartupSelfTestOverlay

            overlay = StartupSelfTestOverlay()
            self.root_layout.add_widget(overlay)
            overlay.run(self)
        except Exception:
            self._startup_self_test_started = False
            logger.exception("Startup self-test overlay failed to load")

    def _push_appliance_metrics_tick(self, _dt):
        if USE_MOCK_BACKEND:
            return
        if not get_device_auth_token().strip():
            return
        run_async(self._push_appliance_metrics_async())

    async def _push_appliance_metrics_async(self):
        try:
            from appliance_metrics import collect_appliance_metrics

            data = collect_appliance_metrics()
            await self.backend.post_appliance_system_metrics(data)
        except Exception as e:
            logger.debug("Appliance metrics push skipped: %s", e)

    def _global_setup_check(self, _dt):
        """Global poll for setup_complete marker -- fires from any screen."""
        if not self.needs_setup():
            if self._setup_poll:
                self._setup_poll.cancel()
                self._setup_poll = None
            # If setup completed elsewhere (e.g. marker synced), leave onboarding.
            onboarding_screens = {
                'welcome', 'room_name', 'network_choice', 'setup_progress', 'wifi_setup',
                'wifi_connected', 'pair_device', 'meetingbox_ready', 'all_set',
            }
            current = self.screen_manager.current
            if current in onboarding_screens:
                self.goto_screen('home', 'fade')

    def on_stop(self):
        logger.info("MeetingBox UI stopping")
        self.voice_assistant.stop()
        if self._audio_supervisor is not None:
            try:
                self._audio_supervisor.stop()
            except Exception:
                logger.exception("Audio supervisor stop failed")
        if getattr(self, '_setup_poll', None):
            self._setup_poll.cancel()
        if getattr(self, '_pairing_poll', None):
            self._pairing_poll.cancel()
            self._pairing_poll = None
        if getattr(self, '_metrics_push', None):
            self._metrics_push.cancel()
            self._metrics_push = None
        if getattr(self, "_ui_sync_event", None):
            self._ui_sync_event.cancel()
            self._ui_sync_event = None
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
        run_async(self.backend.close())

    def _check_backend(self, _dt):
        async def _health():
            ok = False
            for attempt in range(6):
                ok = await self.backend.health_check()
                if ok:
                    break
                if attempt < 5:
                    await asyncio.sleep(0.35 * (attempt + 1))
            if not ok:
                logger.error(
                    "Backend health check failed (base_url=%s) — check BACKEND_URL / network",
                    getattr(self.backend, "base_url", ""),
                )
                local_idle = self._load_local_idle_timeout()
                if local_idle is not None:
                    self._apply_idle_timeout(local_idle)
                return
            try:
                settings = await self.backend.get_settings()
                name = settings.get('device_name', 'MeetingBox')
                if name:
                    self.device_name = name
                    logger.info("Device name loaded: %s", name)
                brightness = settings.get('brightness', 'high')
                set_brightness(brightness)
                # Backend stores the value as seconds (or "never"). Default
                # 30s matches the idle picker default.
                self._apply_idle_timeout(
                    settings.get('idle_screen_timeout', '600'))
                privacy = settings.get('privacy_mode', False)
                self.privacy_mode = privacy
                auto_record = settings.get('auto_record', False)
                self.auto_record = auto_record

                vra = settings.get("voice_realtime_assistant", False)
                if isinstance(vra, str):
                    vra = str(vra).strip().lower() in ("1", "true", "yes", "on")
                self.voice_realtime_assistant = bool(vra)

                vae = settings.get("voice_assistant_enabled", True)
                if isinstance(vae, str):
                    vae = str(vae).strip().lower() in ("1", "true", "yes", "on")
                self.voice_assistant_enabled = bool(vae)

                vwp = (settings.get("voice_wake_phrase") or "hey tony").strip().lower() or "hey tony"
                self.voice_wake_phrase_display = vwp[:1].upper() + vwp[1:] if vwp else "Hey Tony"
                try:
                    sv = settings.get("assistant_speech_volume", 85)
                    if isinstance(sv, str):
                        sv = int(float(sv.strip()))
                    else:
                        sv = int(sv)
                except (TypeError, ValueError):
                    sv = 85
                self.assistant_speech_volume = max(0, min(100, sv))
                self.voice_assistant.apply_server_settings(
                    wake_phrase=vwp,
                    enabled=self.voice_assistant_enabled,
                )
            except Exception as e:
                logger.warning("Could not load settings: %s", e)
            try:
                prof = get_active_profile()
                if prof:
                    self.current_user_id = prof.get('user_id')
                    self.current_display_name = prof.get('display_name')
            except Exception as e:
                logger.debug("Active profile load skipped: %s", e)
        run_async(_health())

    # ==================================================================
    # NAVIGATION (with history stack & transitions)
    # ==================================================================

    def goto_screen(self, screen_name: str, transition='fade'):
        """Navigate to *screen_name* with the specified transition."""
        logger.info(f"Nav → {screen_name} ({transition})")

        # Push current screen onto stack (avoid duplicates)
        current = self.screen_manager.current
        if not self._nav_stack or self._nav_stack[-1] != current:
            self._nav_stack.append(current)

        # Set transition
        self._set_transition(transition)

        # Notify current screen
        cur_screen = self.screen_manager.current_screen
        if hasattr(cur_screen, 'on_leave'):
            cur_screen.on_leave()

        self.screen_manager.current = screen_name

        # Notify new screen
        new_screen = self.screen_manager.current_screen
        if hasattr(new_screen, 'on_enter'):
            new_screen.on_enter()
        self._sync_voice_assistant_state()
        self._refresh_voice_indicator()

    def go_back(self):
        """Pop navigation stack and slide back."""
        if self._nav_stack:
            target = self._nav_stack.pop()
            # Skip non-core screens in stack when going back
            skip = {
                'splash', 'welcome', 'network_choice', 'wifi_setup', 'wifi_connected',
                'setup_progress', 'all_set', 'pair_device', 'meetingbox_ready',
            }
            while target in skip and self._nav_stack:
                target = self._nav_stack.pop()
            self._set_transition('slide_right')
            cur = self.screen_manager.current_screen
            if hasattr(cur, 'on_leave'):
                cur.on_leave()
            self.screen_manager.current = target
            new = self.screen_manager.current_screen
            if hasattr(new, 'on_enter'):
                new.on_enter()
            self._sync_voice_assistant_state()
            self._refresh_voice_indicator()
        else:
            self.goto_screen('home', transition='fade')

    def _set_transition(self, kind):
        dur = TRANSITION_DURATION.get('fade', 0.3)
        if kind == 'fade':
            self.screen_manager.transition = FadeTransition(duration=dur)
        elif kind == 'slide_left':
            self.screen_manager.transition = SlideTransition(
                direction='left', duration=dur)
        elif kind == 'slide_right':
            self.screen_manager.transition = SlideTransition(
                direction='right', duration=dur)
        elif kind == 'none':
            self.screen_manager.transition = NoTransition()
        else:
            self.screen_manager.transition = FadeTransition(duration=dur)

    # ==================================================================
    # WEBSOCKET EVENT HANDLING
    # ==================================================================

    def start_websocket_listener(self):
        loop = get_async_loop()
        if loop and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._websocket_listener(), loop)
            self.ws_task = future

    def _event_belongs_to_this_device(self, event: dict, data: dict) -> bool:
        """Multi-device scoping filter.

        When this device knows its identity AND the incoming event
        carries an explicit ``device_id`` for a *different* device,
        drop it. Events without a ``device_id`` (legacy publishers,
        backend system events) are always accepted so we stay
        backward compatible.
        """
        my_id = self.device_id
        if not my_id:
            return True
        for src in (data, event):
            try:
                ev_id = (src or {}).get("device_id") if isinstance(src, dict) else None
            except Exception:
                ev_id = None
            if isinstance(ev_id, str) and ev_id:
                return ev_id == my_id
        return True

    async def _websocket_listener(self):
        try:
            async for event in self.backend.subscribe_events():
                etype = event.get('type')
                data = event.get('data') or event
                logger.debug(f"WS event: {etype}")

                if not self._event_belongs_to_this_device(event, data):
                    logger.debug("Dropping cross-device WS event %s", etype)
                    continue

                dispatch = {
                    'recording_started': self.on_recording_started,
                    'recording_stopped': self.on_recording_stopped,
                    'recording_paused': self.on_recording_paused,
                    'recording_resumed': self.on_recording_resumed,
                    'audio_level': self.on_audio_level,
                    'mic_test_level': self.on_mic_test_level,
                    'transcription_complete': self.on_transcription_complete,
                    'audio_segment': self.on_audio_segment,
                    'processing_started': self.on_processing_started,
                    'processing_progress': self.on_processing_progress,
                    'processing_complete': self.on_processing_complete,
                    'summary_progress': self.on_summary_progress,
                    'summary_complete': self.on_summary_complete,
                    'setup_complete': self.on_setup_complete,
                    'update_progress': self.on_update_progress,
                    'error': self.on_error_event,
                }
                handler = dispatch.get(etype)
                if handler:
                    handler(data)
                elif etype != 'heartbeat':
                    logger.warning(f"Unknown WS event: {etype}")
        except asyncio.CancelledError:
            logger.info("WebSocket listener cancelled")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            # Reset reconnect counter before the outer restart so the next
            # subscribe_events() run gets a full 10 fresh attempts.
            self.backend._ws_reconnect_attempts = 0
            await asyncio.sleep(1)
            Clock.schedule_once(lambda _: self.start_websocket_listener(), 0)

    # ==================================================================
    # AUDIO SUPERVISOR STDOUT EVENT DISPATCH
    # ==================================================================
    # Replaces the previous appliance-side Redis ``events`` listener.
    # ``audio_capture.py`` now writes ``MEETINGBOX_EVT|<json>`` lines to
    # stdout; ``audio_supervisor`` parses them and calls
    # ``_on_audio_supervisor_event`` on its reader thread for each one.

    _SUPERVISOR_EVENT_TYPES = frozenset({
        'audio_level', 'mic_test_level',
        'recording_started', 'recording_stopped',
        'recording_paused', 'recording_resumed',
        'error',
    })

    def _on_audio_supervisor_event(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        etype = event.get('type')
        if etype not in self._SUPERVISOR_EVENT_TYPES:
            return
        data = event.get('data') or event
        if not self._event_belongs_to_this_device(event, data):
            logger.debug("Dropping cross-device supervisor event %s", etype)
            return
        handler = {
            'audio_level': self.on_audio_level,
            'mic_test_level': self.on_mic_test_level,
            'recording_started': self.on_recording_started,
            'recording_stopped': self.on_recording_stopped,
            'recording_paused': self.on_recording_paused,
            'recording_resumed': self.on_recording_resumed,
            'error': self.on_error_event,
        }.get(etype)
        if handler is None:
            return
        try:
            handler(data)
        except Exception:
            logger.exception("supervisor event handler failed for %s", etype)

    # ==================================================================
    # EVENT HANDLERS
    # ==================================================================

    def _reset_recording_elapsed_clock(self) -> None:
        self._recording_elapsed_before_pause = 0.0
        self._recording_elapsed_started_at = time.monotonic()

    def _pause_recording_elapsed_clock(self) -> None:
        if self._recording_elapsed_started_at is None:
            return
        self._recording_elapsed_before_pause += (
            time.monotonic() - self._recording_elapsed_started_at
        )
        self._recording_elapsed_started_at = None

    def _resume_recording_elapsed_clock(self) -> None:
        if self._recording_elapsed_started_at is None:
            self._recording_elapsed_started_at = time.monotonic()

    def _clear_recording_elapsed_clock(self) -> None:
        self._recording_elapsed_before_pause = 0.0
        self._recording_elapsed_started_at = None

    def _current_recording_elapsed_seconds(self) -> int:
        if self._recording_elapsed_started_at is None:
            return int(self._recording_elapsed_before_pause)
        return int(
            self._recording_elapsed_before_pause
            + (time.monotonic() - self._recording_elapsed_started_at)
        )

    def on_recording_started(self, data):
        sid = data.get('session_id')
        # API + local audio may both publish recording_started when Redis is shared.
        if sid and self.current_session_id == sid and self.recording_state.get('active'):
            return
        self.current_session_id = sid
        self._transcription_done_for_session = None
        self._transcript_cta_satisfied_meeting_id = None
        self._transcript_cta_poll_meeting_id = None
        self.recording_state.update(active=True, paused=False, elapsed=0)
        self._reset_recording_elapsed_clock()
        Clock.schedule_once(lambda _: self._suspend_voice_assistant_for_recording(), 0)
        Clock.schedule_once(lambda _: self.goto_screen('recording', 'fade'), 0)

    def _kick_post_stop_meeting_polls(self, sid):
        """HTTP fallbacks so processing screen gets transcript + summary without relying on WS."""
        if not sid:
            return
        Clock.schedule_once(lambda _dt, mid=sid: self._start_transcript_cta_poll(mid), 0)
        Clock.schedule_once(lambda _dt, mid=sid: self._start_summary_poll(mid), 0)

    def _prime_processing_screen(self, sid: str | None, duration_seconds: int | None = None):
        """Seed processing header metadata before backend progress events arrive."""
        if not sid:
            return
        try:
            screen = self.screen_manager.get_screen('processing')
        except Exception as e:
            logger.debug("Processing screen unavailable for metadata prime: %s", e)
            return
        if hasattr(screen, 'on_processing_started'):
            screen.on_processing_started({
                'title': f'Meeting {sid}',
                'duration': max(0, int(duration_seconds or 0)),
            })

    def on_recording_stopped(self, data):
        self.recording_state['active'] = False
        try:
            self._processing_summary_cache.clear()
        except Exception:
            pass
        sid = data.get('session_id') or self.current_session_id
        duration_seconds = data.get('duration') or self._current_recording_elapsed_seconds()
        self._prime_processing_screen(sid, duration_seconds)
        self._kick_post_stop_meeting_polls(sid)
        self._voice_start_in_flight = False
        self._clear_recording_elapsed_clock()
        Clock.schedule_once(lambda _: self._resume_voice_assistant_after_recording(), 0)
        Clock.schedule_once(lambda _: self.goto_screen('processing', 'fade'), 0)

    def on_recording_paused(self, data):
        if self.recording_state.get('paused'):
            return
        self.recording_state['paused'] = True
        self._pause_recording_elapsed_clock()
        screen = self.screen_manager.get_screen('recording')
        if hasattr(screen, 'on_paused'):
            Clock.schedule_once(lambda _: screen.on_paused(), 0)

    def on_recording_resumed(self, data):
        if not self.recording_state.get('paused'):
            return
        self.recording_state['paused'] = False
        self._resume_recording_elapsed_clock()
        screen = self.screen_manager.get_screen('recording')
        if hasattr(screen, 'on_resumed'):
            Clock.schedule_once(lambda _: screen.on_resumed(), 0)

    def on_audio_segment(self, data):
        seg_data = data if 'segment_num' in data else data.get('data', {})
        seg_num = seg_data.get('segment_num', 0)
        self.recording_state['speaker_count'] = seg_num + 1
        screen = self.screen_manager.get_screen('recording')
        if hasattr(screen, 'on_audio_segment'):
            Clock.schedule_once(
                lambda _: screen.on_audio_segment(seg_num), 0)

    def on_audio_level(self, data):
        level_data = data if 'level' in data else data.get('data', {})
        session_id = level_data.get('session_id')
        if self.current_session_id and session_id and session_id != self.current_session_id:
            return
        level = float(level_data.get('level', 0.0) or 0.0)
        screen = self.screen_manager.get_screen('recording')
        if hasattr(screen, 'on_audio_level'):
            Clock.schedule_once(lambda _: screen.on_audio_level(level), 0)

    def on_mic_test_level(self, data):
        level_data = data if 'level' in data else data.get('data', {})
        level = float(level_data.get('level', 0.0) or 0.0)
        screen = self.screen_manager.get_screen('mic_test')
        if hasattr(screen, 'on_mic_test_level'):
            Clock.schedule_once(lambda _: screen.on_mic_test_level(level), 0)

    def on_processing_started(self, data):
        screen = self.screen_manager.get_screen('processing')
        if hasattr(screen, 'on_processing_started'):
            Clock.schedule_once(lambda _: screen.on_processing_started(data), 0)

    def on_processing_progress(self, data):
        progress = data.get('progress', 0)
        status = data.get('status', '')
        eta = data.get('eta', 0)
        stage = data.get('stage') or ''
        screen = self.screen_manager.get_screen('processing')
        if hasattr(screen, 'on_backend_progress'):
            Clock.schedule_once(
                lambda _: screen.on_backend_progress(progress, status, eta, stage), 0)

    def on_transcription_complete(self, data):
        meeting_id = data.get('meeting_id') or data.get('session_id')
        logger.info("Transcription complete for meeting %s", meeting_id)

        def _update_status(_dt):
            screen = self.screen_manager.get_screen('processing')
            if hasattr(screen, 'set_processing_status'):
                screen.set_processing_status('Transcription done. Building meeting report…')

        Clock.schedule_once(_update_status, 0)

        if meeting_id:
            self._transcription_done_for_session = meeting_id

            def _enable_processing_cta(_dt):
                screen = self.screen_manager.get_screen("processing")
                if hasattr(screen, "on_transcription_ready"):
                    screen.on_transcription_ready(meeting_id)

            Clock.schedule_once(_enable_processing_cta, 0)

        # Safety net: if `summary_complete` WS packet never arrives (network
        # hiccup, WS disconnected during the window, server emit dropped), we
        # still need to reveal the CTA. Start a bounded HTTP poll.
        if meeting_id:
            self._start_summary_poll(meeting_id)

    def on_summary_progress(self, data):
        def _update_status(_dt):
            screen = self.screen_manager.get_screen('processing')
            if hasattr(screen, 'set_processing_status'):
                screen.set_processing_status('Updating report…')

        Clock.schedule_once(_update_status, 0)

    def on_processing_complete(self, data):
        meeting_id = data.get('meeting_id')

        def _update_status(_dt):
            screen = self.screen_manager.get_screen('processing')
            if hasattr(screen, 'set_processing_status'):
                screen.set_processing_status('Building meeting report…')

        Clock.schedule_once(_update_status, 0)
        self._auto_summarize(meeting_id)

    def on_setup_complete(self, data):
        """Handle setup_complete event globally -- works from any onboarding screen."""
        logger.info("Setup complete event received")
        onboarding_screens = {'welcome', 'room_name', 'network_choice', 'setup_progress'}
        current = self.screen_manager.current

        def _advance(_dt):
            if current in onboarding_screens:
                self.goto_screen('all_set', 'fade')
            elif current == 'splash':
                pass  # splash will auto-advance to home via _advance check

        Clock.schedule_once(_advance, 0)

    def on_update_progress(self, data):
        progress = data.get('progress', 0)
        stage = data.get('stage', '')
        eta = data.get('eta', 0)
        screen = self.screen_manager.get_screen('update_install')
        if hasattr(screen, 'on_progress_update'):
            Clock.schedule_once(
                lambda _: screen.on_progress_update(progress, stage, eta), 0)

    def on_summary_complete(self, data):
        """Handle summary_complete event from AI service (if it fires separately)."""
        meeting_id = data.get('meeting_id')
        summary = data.get('summary') or {}
        if not meeting_id:
            return
        if isinstance(summary, dict) and summary.get('status') == 'failed':
            err = str(summary.get('error') or 'Report could not be generated.')
            Clock.schedule_once(
                lambda _dt, mid=meeting_id, msg=err: self._show_processing_summary_failed(mid, msg),
                0,
            )
            return
        Clock.schedule_once(
            lambda _dt, mid=meeting_id, sm=summary: self._show_processing_summary_ready(mid, sm),
            0,
        )

    def _show_processing_summary_ready(self, meeting_id: str, summary: dict):
        """Keep user on processing screen and enable CTA once summary is ready."""
        ready_for_review = self._summary_payload_ready_for_review(summary or {})
        if ready_for_review:
            try:
                self._processing_summary_cache[meeting_id] = {'ok': True, 'summary': summary or {}}
            except Exception:
                pass
        # Any path reaching here is the authoritative "summary ready" signal —
        # silence the fallback poll so we don't duplicate work.
        if ready_for_review and self._summary_poll_meeting_id == meeting_id:
            self._summary_poll_done = True
        try:
            processing = self.screen_manager.get_screen('processing')
        except Exception as e:
            logger.debug("Processing screen unavailable for summary-ready update: %s", e)
            return
        if hasattr(processing, 'on_summary_ready'):
            processing.on_summary_ready(meeting_id, summary or {})

    def _show_processing_summary_failed(self, meeting_id: str, message: str):
        """Summary/report failed — still allow transcript-only review when available."""
        if self._summary_poll_meeting_id == meeting_id:
            self._summary_poll_done = True
        try:
            self._processing_summary_cache[meeting_id] = {
                'ok': False,
                'error': message or 'Report unavailable.',
            }
        except Exception:
            pass
        try:
            processing = self.screen_manager.get_screen('processing')
        except Exception as e:
            logger.debug('Processing screen unavailable for summary-failed update: %s', e)
            return
        if hasattr(processing, 'on_summary_failed'):
            processing.on_summary_failed(meeting_id, message or 'Report unavailable.')

    def _start_summary_poll(self, meeting_id: str):
        """Kick off the HTTP fallback poll that watches for a saved summary.

        Safe to call multiple times — new meeting_id replaces the previous one,
        and _show_processing_summary_ready() flips the done flag regardless of
        which path delivered the summary first.
        """
        if not meeting_id:
            return
        self._summary_poll_meeting_id = meeting_id
        self._summary_poll_done = False
        run_async(self._poll_summary_until_ready(meeting_id))

    @staticmethod
    def _summary_payload_has_text(summary: dict) -> bool:
        """True once the API has a saved summary body that the review screen can render."""
        if not isinstance(summary, dict):
            return False
        raw_summary = summary.get('summary')
        if isinstance(raw_summary, dict):
            raw_summary = raw_summary.get('summary')
        text = (
            raw_summary
            or summary.get('summary_text')
            or summary.get('text')
            or ''
        )
        return bool(str(text).strip())

    @classmethod
    def _summary_payload_ready_for_review(cls, summary: dict) -> bool:
        """Match web readiness: summary review is available once summary text exists.

        Topics, decisions, and action items can legitimately be empty for a
        meeting, and the web frontend still renders the completed report in
        that case. The device CTA should follow the same rule.
        """
        return cls._summary_payload_has_text(summary)

    async def _poll_summary_until_ready(self, meeting_id: str):
        """Poll GET /api/meetings/{id} every 5s for up to ~5 minutes. If a
        summary appears, deliver it via _show_processing_summary_ready."""
        logger.info("Summary poll starting for meeting %s", meeting_id)
        for attempt in range(60):  # 60 * 5s = 5 minutes
            if self._summary_poll_done or self._summary_poll_meeting_id != meeting_id:
                return
            await asyncio.sleep(5.0)
            if self._summary_poll_done or self._summary_poll_meeting_id != meeting_id:
                return
            try:
                detail = await self.backend.get_meeting_detail(meeting_id)
            except Exception as e:
                logger.debug(
                    "Summary poll attempt %d failed for %s: %s",
                    attempt + 1, meeting_id, e,
                )
                continue
            summary = (detail or {}).get('summary') or {}
            if self._summary_payload_ready_for_review(summary):
                logger.info(
                    "Summary poll found summary for %s after %d attempt(s)",
                    meeting_id, attempt + 1,
                )
                Clock.schedule_once(
                    lambda _dt, _mid=meeting_id, _s=summary:
                        self._show_processing_summary_ready(_mid, _s),
                    0,
                )
                return
        logger.warning(
            "Summary poll gave up for meeting %s (no summary within 5 min)",
            meeting_id,
        )
        detail = None
        try:
            detail = await self.backend.get_meeting_detail(meeting_id)
        except Exception as e:
            logger.debug('Summary poll final detail fetch failed: %s', e)
        segments = (detail or {}).get('segments') or []
        if segments:
            Clock.schedule_once(
                lambda _dt, mid=meeting_id: self._show_processing_summary_failed(
                    mid,
                    'Full report is still unavailable — you can open the transcript.',
                ),
                0,
            )
        else:
            Clock.schedule_once(
                lambda _dt: self.show_error_screen(
                    'Processing timeout',
                    'No transcript or summary appeared. Check your connection and try again.',
                ),
                0,
            )

    def _start_transcript_cta_poll(self, meeting_id: str):
        if not meeting_id:
            return
        self._transcript_cta_poll_meeting_id = meeting_id
        run_async(self._poll_transcript_cta_until_ready(meeting_id))

    def _deliver_transcript_cta_from_poll(self, meeting_id: str):
        """Main-thread: reveal CTA once HTTP confirms transcript (or summary) rows exist."""
        if self._transcript_cta_satisfied_meeting_id == meeting_id:
            return
        self._transcript_cta_satisfied_meeting_id = meeting_id
        self._transcription_done_for_session = meeting_id
        try:
            proc = self.screen_manager.get_screen('processing')
        except Exception as e:
            logger.debug("Processing screen missing for transcript CTA poll: %s", e)
            return
        if hasattr(proc, 'on_transcription_ready'):
            proc.on_transcription_ready(meeting_id)

    async def _poll_transcript_cta_until_ready(self, meeting_id: str):
        """Poll GET /api/meetings/{id} until segments or summary exist (WS fallback)."""
        logger.info("Transcript CTA poll starting for meeting %s", meeting_id)
        for attempt in range(120):
            if self._transcript_cta_poll_meeting_id != meeting_id:
                return
            if self._transcript_cta_satisfied_meeting_id == meeting_id:
                return
            if attempt > 0:
                await asyncio.sleep(3.0)
            if self._transcript_cta_poll_meeting_id != meeting_id:
                return
            if self._transcript_cta_satisfied_meeting_id == meeting_id:
                return
            try:
                detail = await self.backend.get_meeting_detail(meeting_id)
            except Exception as e:
                logger.debug(
                    "Transcript CTA poll attempt %d failed for %s: %s",
                    attempt + 1, meeting_id, e,
                )
                continue
            segments = (detail or {}).get('segments') or []
            summary_blob = (detail or {}).get('summary') or {}
            has_segments = len(segments) > 0
            has_summary = self._summary_payload_ready_for_review(summary_blob)
            if has_segments or has_summary:
                logger.info(
                    "Transcript CTA poll: content ready for %s (segments=%d summary=%s)",
                    meeting_id,
                    len(segments),
                    has_summary,
                )
                Clock.schedule_once(
                    lambda _dt, _mid=meeting_id: self._deliver_transcript_cta_from_poll(_mid),
                    0,
                )
                if has_summary:
                    Clock.schedule_once(
                        lambda _dt, _mid=meeting_id, _s=summary_blob:
                            self._show_processing_summary_ready(_mid, _s),
                        0,
                    )
                return
        logger.warning(
            "Transcript CTA poll gave up for meeting %s (no segments within ~6 min)",
            meeting_id,
        )
        detail = None
        try:
            detail = await self.backend.get_meeting_detail(meeting_id)
        except Exception as e:
            logger.debug('Transcript CTA poll final detail fetch failed: %s', e)
        if (detail or {}).get('segments'):
            Clock.schedule_once(
                lambda _dt, mid=meeting_id: self._deliver_transcript_cta_from_poll(mid),
                0,
            )
        else:
            Clock.schedule_once(
                lambda _dt: self.show_error_screen(
                    'Processing timeout',
                    'Transcript was not saved in time. Check your connection and try again.',
                ),
                0,
            )

    def _auto_summarize(self, meeting_id: str):
        """After transcription completes, auto-trigger summarization then show review screen."""
        async def _run():
            try:
                def _status_actions(_dt):
                    screen = self.screen_manager.get_screen('processing')
                    if hasattr(screen, 'set_processing_status'):
                        screen.set_processing_status(
                            'Finishing report and Gmail/Calendar suggestions…',
                        )

                Clock.schedule_once(_status_actions, 0)
                summary = await self.backend.summarize_meeting(meeting_id)

                Clock.schedule_once(
                    lambda _dt: self._show_processing_summary_ready(meeting_id, summary),
                    0,
                )
            except Exception as e:
                logger.error(f"Auto-summarize failed: {e}")
                def _fallback(_dt):
                    screen = self.screen_manager.get_screen('complete')
                    if hasattr(screen, 'set_meeting_id'):
                        screen.set_meeting_id(meeting_id)
                    self.goto_screen('complete', 'fade')
                Clock.schedule_once(_fallback, 0)

        run_async(_run())

    def on_error_event(self, data):
        error_type = data.get('error_type', 'Unknown Error')
        message = data.get('message', '')
        Clock.schedule_once(
            lambda _: self.show_error_screen(error_type, message), 0)

    # ==================================================================
    # ERROR DISPLAY
    # ==================================================================

    def show_error_screen(self, error_type: str, message: str,
                          recovery_text=None, recovery_action=None):
        screen = self.screen_manager.get_screen('error')
        if hasattr(screen, 'set_error'):
            screen.set_error(error_type, message, recovery_text, recovery_action)
        self.goto_screen('error', 'fade')

    # ==================================================================
    # RECORDING ACTIONS
    # ==================================================================

    def start_recording(self):
        async def _start():
            last_exc: BaseException | None = None
            max_attempts = 3
            Clock.schedule_once(lambda _: self._suspend_voice_assistant_for_recording(), 0)
            await asyncio.sleep(0.35)
            for attempt in range(max_attempts):
                if attempt > 0:
                    delay = 2.0 * attempt
                    logger.info(
                        "Retrying start_recording after %.1fs (attempt %s/%s)",
                        delay,
                        attempt + 1,
                        max_attempts,
                    )
                    await asyncio.sleep(delay)
                try:
                    result = await self.backend.start_recording()
                    self.current_session_id = result['session_id']
                    self.recording_state.update(active=True, paused=False, elapsed=0)
                    self._voice_start_in_flight = False
                    self._reset_recording_elapsed_clock()
                    Clock.schedule_once(lambda _: self._suspend_voice_assistant_for_recording(), 0)
                    Clock.schedule_once(
                        lambda _: self.goto_screen('recording', 'fade'), 0)
                    return
                except Exception as e:
                    last_exc = e
                    logger.error(
                        "Failed to start recording (attempt %s/%s): %s",
                        attempt + 1,
                        max_attempts,
                        e,
                    )
                    if (
                        attempt < max_attempts - 1
                        and _recording_start_transient_network(e)
                    ):
                        continue
                    break
            self._voice_start_in_flight = False
            self._voice_start_confirmation_pending = False
            self._voice_recording_suspended = False
            Clock.schedule_once(lambda _: self._clear_voice_indicator_override(), 0)
            Clock.schedule_once(lambda _: self._sync_voice_assistant_state(), 0)
            title, message = _recording_start_error_screen_args(
                last_exc or RuntimeError("Unknown error")
            )
            Clock.schedule_once(
                lambda _: self.show_error_screen(
                    title,
                    message,
                    recovery_text='TRY AGAIN',
                    recovery_action=self.start_recording), 0)
        run_async(_start())

    def stop_recording(self):
        logger.info("stop_recording called, session_id=%s", self.current_session_id)
        async def _stop():
            try:
                sid = self.current_session_id
                duration_seconds = self._current_recording_elapsed_seconds()
                await self.backend.stop_recording(sid)
                self.recording_state['active'] = False
                self._voice_start_in_flight = False
                self._clear_recording_elapsed_clock()
                Clock.schedule_once(lambda _: self._resume_voice_assistant_after_recording(), 0)
                logger.info("Recording stopped successfully")
                # Device-initiated stop never goes through on_recording_stopped (Redis/WS).
                Clock.schedule_once(
                    lambda _dt, _sid=sid, _dur=duration_seconds:
                        self._prime_processing_screen(_sid, _dur),
                    0,
                )
                self._kick_post_stop_meeting_polls(sid)
                Clock.schedule_once(
                    lambda _: self.goto_screen('processing', 'fade'), 0)
            except Exception as e:
                logger.error(f"Failed to stop recording: {e}")
                Clock.schedule_once(
                    lambda _: self.show_error_screen(
                        'Stop Failed', str(e)), 0)
        run_async(_stop())

    def pause_recording(self):
        async def _pause():
            try:
                await self.backend.pause_recording(self.current_session_id)
                self.recording_state['paused'] = True
                self._pause_recording_elapsed_clock()
                screen = self.screen_manager.get_screen('recording')
                if hasattr(screen, 'on_paused'):
                    Clock.schedule_once(lambda _: screen.on_paused(), 0)
            except Exception as e:
                logger.error(f"Failed to pause: {e}")
                Clock.schedule_once(
                    lambda _: self.show_error_screen('Pause Failed', str(e)), 0)
        run_async(_pause())

    def resume_recording(self):
        async def _resume():
            try:
                await self.backend.resume_recording(self.current_session_id)
                self.recording_state['paused'] = False
                self._resume_recording_elapsed_clock()
                screen = self.screen_manager.get_screen('recording')
                if hasattr(screen, 'on_resumed'):
                    Clock.schedule_once(lambda _: screen.on_resumed(), 0)
            except Exception as e:
                logger.error(f"Failed to resume: {e}")
                Clock.schedule_once(
                    lambda _: self.show_error_screen('Resume Failed', str(e)), 0)
        run_async(_resume())

    # ==================================================================
    # IDLE SCREEN TIMEOUT
    # ==================================================================

    # Screens where the idle timer must NOT fire — recording is the obvious
    # one (the device is actively in use), and onboarding screens have their
    # own controlled flow that the lock screen would interrupt.
    _IDLE_TIMER_DISABLED_SCREENS = frozenset({
        'splash', 'welcome', 'room_name', 'network_choice',
        'wifi_setup', 'wifi_connected', 'pair_device', 'meetingbox_ready',
        'setup_progress', 'all_set',
        'recording', 'processing',
        'idle',
    })

    # ------------------------------------------------------------------
    # Local settings cache helpers (survive backend outages across restarts)
    # ------------------------------------------------------------------

    def _local_ui_settings_path(self) -> Path:
        from config import resolve_device_config_dir
        return resolve_device_config_dir() / "local_ui_settings.json"

    def _persist_local_idle_timeout(self, value: str) -> None:
        try:
            path = self._local_ui_settings_path()
            existing: dict = {}
            if path.is_file():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            existing["idle_screen_timeout"] = value
            path.write_text(json.dumps(existing), encoding="utf-8")
        except Exception as exc:
            logger.debug("Could not persist idle timeout locally: %s", exc)

    def _load_local_idle_timeout(self) -> str | None:
        try:
            path = self._local_ui_settings_path()
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                val = data.get("idle_screen_timeout")
                if val is not None:
                    return str(val)
        except Exception:
            pass
        return None

    def _apply_idle_timeout(self, value: str):
        """Configure idle-screen timeout.

        Accepts seconds as a string (``"30"``, ``"60"``, ``"120"``, ``"300"``,
        ``"1800"``) or ``"never"``. Also persists the value to a local cache
        file so the setting survives backend outages across device restarts.
        """
        if self._idle_event:
            self._idle_event.cancel()
            self._idle_event = None

        v = (value or '').strip().lower()
        if v in ('', 'never', 'off', '0'):
            self._idle_timeout_seconds = 0
            self._persist_local_idle_timeout('never')
            return

        try:
            n = int(v)
        except ValueError:
            self._idle_timeout_seconds = 600
            self._persist_local_idle_timeout('600')
            self._reset_idle_timer()
            return

        self._idle_timeout_seconds = n
        self._persist_local_idle_timeout(value)
        self._reset_idle_timer()

    # _panel_touch_down / _panel_touch_move removed — gesture is now handled
    # by the _SwipeHandle widget added to root_layout (see class below).

    def _reset_idle_timer(self, *_args):
        """Reset the idle countdown. Called on every touch.

        If the user touches while the idle screen is up, immediately return
        to home — the idle screen also handles this in its own ``on_touch_up``
        (which fires once children pass), but doing it here as well covers
        gestures that don't reach a screen widget (e.g. global swipes).
        """
        if self.screen_manager and self.screen_manager.current == 'idle':
            self.goto_screen('home', 'fade')

        if self._idle_event:
            self._idle_event.cancel()
            self._idle_event = None

        if self._idle_timeout_seconds > 0:
            self._idle_event = Clock.schedule_once(
                self._on_idle_timeout, self._idle_timeout_seconds)

    def _on_idle_timeout(self, _dt):
        """Fires when idle timeout expires — show the dedicated idle screen."""
        if not self.screen_manager:
            return
        cur = self.screen_manager.current
        if cur in self._IDLE_TIMER_DISABLED_SCREENS:
            # Don't interrupt onboarding / recording / processing. Restart
            # the timer; once the user lands on a normal screen, the next
            # touch will set the cycle going again via _reset_idle_timer.
            self._reset_idle_timer()
            return
        if self.recording_state.get('active'):
            self._reset_idle_timer()
            return
        logger.info("Idle timeout reached on screen %s — showing idle screen", cur)
        self.goto_screen('idle', 'fade')

    def _voice_assistant_should_listen(self) -> bool:
        if self.screen_manager is None:
            return False
        if not getattr(self, "voice_assistant_enabled", True):
            return False
        if getattr(self, "_voice_recording_suspended", False):
            return False
        if self.recording_state.get("active"):
            return False
        if (
            getattr(self, "_realtime_voice_session", None) is not None
            and getattr(self, "_realtime_mic_acquired", False)
        ):
            return False
        if self._voice_start_in_flight:
            return False
        if self.user_voice_paused:
            return False
        blocked = {
            'splash',
            'welcome',
            'room_name',
            'network_choice',
            'wifi_setup',
            'wifi_connected',
            'pair_device',
            'meetingbox_ready',
            'setup_progress',
            'all_set',
            'mic_test',
            # 'recording' blocks the wake-word listener while a meeting is being
            # recorded. The listener uses sd.RawInputStream on PortAudio's
            # default device, which on this hardware routes to the USB mic via
            # PulseAudio/PipeWire. Holding it open starves audio_capture.py
            # (the recorder) — it falls back to the silent built-in mic, so the
            # WAV ends up silent and the on-screen waveform stays flat. Pausing
            # here triggers _close_stream(), releasing the mic for the recorder.
            'recording',
        }
        return self.screen_manager.current not in blocked

    def _suspend_voice_assistant_for_recording(self) -> None:
        """Guarantee meeting audio is not mixed with assistant mic/speaker use."""
        self._voice_recording_suspended = True
        self._voice_start_confirmation_pending = False
        self._voice_start_in_flight = False
        self._clear_voice_indicator_override()
        try:
            if getattr(self, "voice_assistant", None) is not None:
                self.voice_assistant.set_paused(True)
                self.voice_assistant.clear_confirmation()
                self.voice_assistant.exit_command_window()
        except Exception:
            logger.exception("Failed to pause local voice assistant for recording")
        if getattr(self, "_realtime_voice_session", None) is not None or self._realtime_session_pending:
            try:
                logger.info("Recording active — stopping Realtime voice assistant")
                self._end_realtime_voice_session()
            except Exception:
                logger.exception("Failed to stop Realtime voice session for recording")
        self._set_voice_runtime_state("idle")
        self._sync_voice_assistant_state()
        self._refresh_voice_indicator()

    def _resume_voice_assistant_after_recording(self) -> None:
        self._voice_recording_suspended = False
        self._sync_voice_assistant_state()

    def _sync_voice_assistant_state(self) -> None:
        if not getattr(self, 'voice_assistant', None):
            return
        self.voice_assistant.set_paused(not self._voice_assistant_should_listen())
        self._refresh_voice_indicator()

    def _refresh_voice_indicator(self) -> None:
        if not self.voice_indicator:
            return
        if self._voice_indicator_override:
            state, message = self._voice_indicator_override
            self.voice_indicator.set_state(state, message)
            return
        rt = getattr(self, "_voice_runtime_state", "idle")
        if rt in ("listening", "thinking", "speaking"):
            if rt == "listening":
                self.voice_indicator.set_state("wake", "Listening…")
            elif rt == "thinking":
                self.voice_indicator.set_state("wake", "Thinking…")
            else:
                self.voice_indicator.set_state("speaking", "Speaking…")
            return
        if self.voice_assistant.available and self._voice_assistant_should_listen():
            wkd = getattr(self, "voice_wake_phrase_display", None) or "Hey Tony"
            self.voice_indicator.set_state("idle", f'Say "{wkd}"')
            return
        self.voice_indicator.set_state("hidden")

    def _clear_voice_indicator_override(self, *_args) -> None:
        if self._voice_indicator_reset_ev:
            self._voice_indicator_reset_ev.cancel()
            self._voice_indicator_reset_ev = None
        self._voice_indicator_override = None
        self._refresh_voice_indicator()
        self._sync_voice_assistant_state()

    def _set_voice_runtime_state(self, state: str) -> None:
        self._voice_runtime_state = (state or "idle").strip().lower()
        self._refresh_voice_indicator()

    def _voice_mark_post_realtime_wake_suppression(self) -> None:
        """Realtime PCM uses aplay, not _speak_text_blocking — apply same tail/guard timeline."""
        import time as _time

        deadline = _time.monotonic() + (
            _tts_tail_silence_seconds() + _post_tts_wake_guard_seconds()
        )
        prev = float(getattr(self, "_wake_suppress_until_monotonic", 0.0) or 0.0)
        self._wake_suppress_until_monotonic = max(prev, deadline)

    def _set_voice_indicator_override(
        self,
        state: str,
        message: str | None = None,
        duration: float | None = None,
    ) -> None:
        self._voice_indicator_override = (state, message)
        if self._voice_indicator_reset_ev:
            self._voice_indicator_reset_ev.cancel()
            self._voice_indicator_reset_ev = None
        self._refresh_voice_indicator()
        if duration:
            self._voice_indicator_reset_ev = Clock.schedule_once(
                self._clear_voice_indicator_override, duration
            )

    def _handle_voice_wake_phrase(self, _text: str) -> None:
        """Run after local wake detection or mic orb (same flow).

        Arms at most one cloud Q&A reply for this wake. OpenAI Realtime may only start
        when voice_realtime_assistant is on and `_realtime_launch_permitted` is set here.
        """
        import logging as _logging
        import time as _time

        nowm = _time.monotonic()
        # Local espeak TTS marks _last_tts_end_monotonic; Realtime marks _wake_suppress_until_monotonic.
        quiet_until = max(
            getattr(self, "_wake_suppress_until_monotonic", 0.0),
            getattr(self, "_last_tts_end_monotonic", 0.0) + _post_tts_wake_guard_seconds(),
        )
        if nowm < quiet_until:
            _logging.getLogger(__name__).debug(
                "Wake phrase suppressed (post-voice-output quiet period, %.1fs remaining)",
                quiet_until - nowm,
            )
            return

        if getattr(self, "voice_assistant_enabled", True):
            # During an active meeting recording, suppress cloud Q&A so the
            # ambient conversation is not sent to the AI. Local commands
            # (stop/pause/resume meeting etc.) still work.
            if self.recording_state.get("active"):
                self._voice_cloud_qa_budget = 0
            else:
                will_use_realtime = (
                    getattr(self, "voice_realtime_assistant", False)
                    and REALTIME_VOICE_IMPLEMENTED
                    and bool(get_device_auth_token().strip())
                    and not USE_MOCK_BACKEND
                    and not WAKE_LOCAL_VOICE_ONLY
                )
                # OpenAI Realtime handles the whole spoken turn. If we leave the
                # HTTP /assistant/intent "QA" budget at 1, Vosk often delivers
                # wake + question in one Result() and fires on_conversation_turn
                # in the same callback as on_wake_phrase — parallel to Realtime
                # mint — so the user hears the wrong pipeline and Realtime feels broken.
                self._voice_cloud_qa_budget = 0 if will_use_realtime else 1
        else:
            self._voice_cloud_qa_budget = 0
        self._realtime_launch_permitted = False

        timeout = max(2.0, self.voice_assistant.command_timeout_seconds)
        lbl = getattr(self, "voice_wake_phrase_display", "Hey Tony") or "Hey Tony"

        def _wake_ui(_dt):
            self._set_voice_indicator_override(
                "wake",
                f'Heard "{lbl}"',
                timeout,
            )
            if (
                self.screen_manager is not None
                and self.screen_manager.current == "home"
            ):
                try:
                    home = self.screen_manager.get_screen("home")
                    home.show_listening_state()
                    Clock.schedule_once(
                        lambda _dt2: self._hide_home_listening_state(),
                        timeout,
                    )
                except Exception:
                    pass

        Clock.schedule_once(_wake_ui, 0)

        if (
            getattr(self, "voice_realtime_assistant", False)
            and REALTIME_VOICE_IMPLEMENTED
            and get_device_auth_token().strip()
            and not USE_MOCK_BACKEND
            and not WAKE_LOCAL_VOICE_ONLY
        ):
            self._realtime_launch_permitted = True

            def _kick_realtime(_dt):
                self._show_home_listening_after_wake()
                self._start_realtime_voice_session()

            Clock.schedule_once(_kick_realtime, 0)
            return

        Clock.schedule_once(
            lambda _dt: self._begin_local_voice_command_session(),
            0,
        )

    def _show_home_listening_after_wake(self) -> None:
        """Home listening animation while OpenAI Realtime connects."""
        timeout = max(2.0, self.voice_assistant.command_timeout_seconds)
        if (
            self.screen_manager is not None
            and self.screen_manager.current == "home"
        ):
            try:
                home = self.screen_manager.get_screen("home")
                home.show_listening_state()
                Clock.schedule_once(
                    lambda _dt: self._hide_home_listening_state(),
                    timeout,
                )
            except Exception:
                pass

    def _begin_local_voice_command_session(self) -> None:
        """Post-wake window for local Vosk commands (no Realtime).

        Refreshes the home listening animation and hide timer so Realtime failures
        or API fallbacks do not collapse the UI after a fraction of a second.
        """
        try:
            self.voice_assistant.simulate_wake()
        except Exception:
            logger.exception("simulate_wake after wake phrase failed")
        # Wake cleared QA budget when Realtime was armed; restore one HTTP reply
        # for this mic window when we actually fell back to local listening.
        if (
            getattr(self, "voice_realtime_assistant", False)
            and getattr(self, "voice_assistant_enabled", True)
            and not self.recording_state.get("active")
            and getattr(self, "_voice_cloud_qa_budget", 0) < 1
        ):
            self._voice_cloud_qa_budget = 1
        timeout = max(2.0, self.voice_assistant.command_timeout_seconds)
        if (
            self.screen_manager is not None
            and self.screen_manager.current == "home"
        ):
            try:
                home = self.screen_manager.get_screen("home")
                home.show_listening_state()
                Clock.schedule_once(
                    lambda _dt: self._hide_home_listening_state(),
                    timeout,
                )
            except Exception:
                pass

    def _finalize_voice_exchange_idle(self) -> None:
        """User said goodbye — stop HTTP Q&A follow-up; no extra TTS or API call."""
        self._voice_cloud_qa_budget = 0
        try:
            self.voice_assistant.exit_command_window()
        except Exception:
            logger.exception("exit_command_window after farewell failed")
        Clock.schedule_once(lambda _dt: self._hide_home_listening_state(), 0)
        self._clear_voice_indicator_override()
        self._refresh_voice_indicator()

    def _handle_voice_conversation_turn(self, text: str) -> None:
        """Cloud assistant Q&A for speech that is not a rigid local intent (person-like dialogue)."""
        if not getattr(self, "voice_assistant_enabled", True):
            return
        if self._voice_pending_confirmation:
            return
        phrase = (text or "").strip()
        if phrase and utterance_is_voice_farewell(self.voice_assistant.wake_phrase, phrase):
            self._finalize_voice_exchange_idle()
            return
        if getattr(self, "_voice_cloud_qa_budget", 0) <= 0:
            logger.debug("Cloud assistant Q&A skipped (no budget for this wake cycle)")
            return
        norm_p = "".join((phrase or "").lower().split())
        if len(norm_p) < 4:
            return

        self._voice_cloud_qa_budget -= 1

        async def _go():
            # Always use Clock.schedule_once for Kivy UI ops — this coroutine
            # runs on the asyncio background thread, not the Kivy main thread.
            Clock.schedule_once(
                lambda _dt: self._set_voice_indicator_override("wake", "Thinking…", duration=None),
                0,
            )
            try:
                if not USE_MOCK_BACKEND and not get_device_auth_token().strip():
                    if getattr(self, "voice_assistant_enabled", True):
                        Clock.schedule_once(
                            lambda _dt: self._voice_reply_and_extend_listening(
                                "Pair this device with your account so I can answer questions.",
                                error=True,
                            ),
                            0,
                        )
                    return
                res = await self.backend.post_assistant_intent(phrase)
                raw = (res.get("assistant_message") or "").strip() or "Okay."
                if getattr(self, "voice_assistant_enabled", True):
                    Clock.schedule_once(
                        lambda _dt, m=raw: self._voice_reply_and_extend_listening(m), 0
                    )
            except Exception as e:
                logger.warning("Assistant conversation failed: %s", e)
                if getattr(self, "voice_assistant_enabled", True):
                    Clock.schedule_once(
                        lambda _dt: self._set_voice_indicator_override(
                            "error", "No server connection", 2.5
                        ),
                        0,
                    )
            # Do not schedule _clear_voice_indicator_override here: it races with delay=0
            # callbacks above and cancels the timers _voice_reply just created, so the
            # orb/text clears while audio plays (or labels the assistant as silent).

        fut = run_async(_go())
        if fut is None:
            logger.error("Assistant Q&A skipped: background asyncio loop is not running")
            self._voice_cloud_qa_budget += 1
            Clock.schedule_once(
                lambda _dt: self._voice_reply_and_extend_listening(
                    "Voice assistant is not ready. Please restart the app.",
                    error=True,
                ),
                0,
            )

    def _hide_home_listening_state(self, *_args) -> None:
        """Called after wake-word timeout to restore the home screen to idle."""
        if (self.screen_manager is not None
                and self.screen_manager.current == 'home'):
            try:
                home = self.screen_manager.get_screen('home')
                home.hide_listening_state()
            except Exception:
                pass

    def _handle_voice_amplitude(self, amplitude: float) -> None:
        """Received from the audio thread — forward to home screen at ~30 fps."""
        import time as _time
        now = _time.monotonic()
        if now - self._last_amplitude_sched < 0.033:
            return
        self._last_amplitude_sched = now
        Clock.schedule_once(
            lambda _dt, a=amplitude: self._apply_amplitude_to_home(a), 0
        )

    def _apply_amplitude_to_home(self, amplitude: float) -> None:
        if (self.screen_manager is not None
                and self.screen_manager.current == 'home'):
            try:
                home = self.screen_manager.get_screen('home')
                home.update_amplitude(amplitude)
            except Exception:
                pass

    def _realtime_voice_navigate(self, screen: str, target_date=None) -> None:
        """Open a main UI screen when the cloud Realtime model calls navigate_device_ui."""
        s = (screen or "").strip()
        routes = {
            "home": ("home", "fade"),
            "calendar": ("calendar", "slide_left"),
            "emails": ("emails", "slide_left"),
            "meetings": ("meetings", "slide_left"),
            "morning_brief": ("morning_brief", "slide_left"),
            "settings": ("settings", "slide_left"),
            "mic_test": ("mic_test", "slide_left"),
        }
        pair = routes.get(s)
        if not pair:
            return
        name, tr = pair
        if target_date and name == "calendar":
            try:
                cal = self.screen_manager.get_screen("calendar")
                cal.set_target_date(target_date)
            except Exception:
                logger.exception("Failed to set calendar target_date")
        try:
            self.goto_screen(name, tr)
        except Exception:
            logger.exception("Realtime navigate to %s failed", name)

    # ── Voice-first email workflow: directives + touch feedback ───────────
    def _send_voice_user_text(self, text: str) -> None:
        """Inject a spoken-equivalent user turn into the live Realtime session.

        Used so a screen tap (recipient card, Send / Save / Discard) is handled
        by the assistant exactly as if the user had said it, preserving the
        recipient-confirmation and send-approval workflow.
        """
        sess = getattr(self, "_realtime_voice_session", None)
        if sess is None:
            logger.debug("No active voice session for injected text: %r", text)
            return
        try:
            sess.send_user_text(text)
        except Exception:
            logger.debug("send_user_text failed", exc_info=True)

    def _on_email_draft_directive(self, draft: dict) -> None:
        """Open/update the draft popup from a show_email_draft directive."""
        popup = getattr(self, "_email_draft_popup", None)
        if popup is None or not isinstance(draft, dict):
            return
        try:
            if popup.visible:
                popup.update_draft(draft)
            else:
                popup.open_draft(draft)
        except Exception:
            logger.exception("Failed to render email draft directive")

    def _on_recipient_picker_directive(self, query: str, candidates: list) -> None:
        """Show the recipient confirmation overlay from a show_recipient_picker directive."""
        overlay = getattr(self, "_recipient_overlay", None)
        if overlay is None:
            return
        try:
            overlay.show_candidates(query, candidates or [])
        except Exception:
            logger.exception("Failed to render recipient picker directive")

    def _on_recipient_selected(self, index: int, contact: dict) -> None:
        overlay = getattr(self, "_recipient_overlay", None)
        if overlay is not None:
            overlay.close()
        email = (contact or {}).get("email", "")
        name = (contact or {}).get("name", "") or email
        if email:
            self._send_voice_user_text(f"Use {email} for {name}.")

    def _on_recipient_dismissed(self) -> None:
        overlay = getattr(self, "_recipient_overlay", None)
        if overlay is not None:
            overlay.close()

    def _on_email_draft_send_tapped(self) -> None:
        self._send_voice_user_text("Yes, send it.")

    def _on_email_draft_save_tapped(self) -> None:
        self._send_voice_user_text("Save it as a draft.")

    def _on_email_draft_discard_tapped(self) -> None:
        popup = getattr(self, "_email_draft_popup", None)
        if popup is not None:
            popup.close()
        self._send_voice_user_text("Discard the email.")

    def _on_email_draft_close_tapped(self) -> None:
        popup = getattr(self, "_email_draft_popup", None)
        if popup is not None:
            popup.close()

    def _sync_transcript_overlay_mode(self, screen_name: str | None = None) -> None:
        """Sync the transcript overlay mode for the current screen.

        On the home screen the say-bar handles transcription display, so the
        overlay is suppressed and hidden. On all other screens the overlay
        shows as a compact bottom strip.
        """
        overlay = self._transcript_overlay
        if overlay is None:
            return
        name = screen_name or (
            self.screen_manager.current if self.screen_manager else None
        )
        # Always compact — the full-screen overlay mode is no longer used.
        overlay.set_compact(True)
        if name == 'home':
            # Home screen uses the say bar for transcription — keep overlay hidden.
            overlay.suppress_auto_show = True
            overlay.hide()
        else:
            overlay.suppress_auto_show = False

    def _clear_home_say_bar(self) -> None:
        """Clear the say-bar transcription on the home screen (if visible)."""
        if self.screen_manager is None:
            return
        try:
            home = self.screen_manager.get_screen('home')
            home.clear_say_bar_transcription()
        except Exception:
            pass

    def _end_realtime_voice_session(self) -> None:
        self._realtime_mic_acquired = False
        self._pending_user_msg_id = None
        self._set_voice_runtime_state("idle")
        if self._transcript_overlay is not None:
            self._transcript_overlay.hide()
        # Tear down the email workflow overlays when the voice session ends.
        popup = getattr(self, "_email_draft_popup", None)
        if popup is not None and popup.visible:
            popup.close()
        recip = getattr(self, "_recipient_overlay", None)
        if recip is not None and recip.visible:
            recip.close()
        Clock.schedule_once(lambda _dt: self._clear_home_say_bar(), 0)
        sess = self._realtime_voice_session
        started = getattr(self, "_realtime_session_start_monotonic", None)
        connected = getattr(self, "_realtime_connected_ok", False)
        if sess is not None:
            try:
                sess.stop()
            except Exception:
                logger.debug("Realtime session stop", exc_info=True)
        short_failed = (
            started is not None
            and not connected
            and (time.monotonic() - float(started)) < 30.0
        )
        if connected:
            self._voice_mark_post_realtime_wake_suppression()
        self._realtime_session_start_monotonic = None
        self._realtime_connected_ok = False
        self._realtime_voice_session = None
        self._realtime_session_pending = False
        self._sync_voice_assistant_state()
        if self.recording_state.get("active"):
            self._clear_voice_indicator_override()
            self._hide_home_listening_state()
            self._refresh_voice_indicator()
            return
        if short_failed:
            # Do not hide listening here — local fallback re-shows it for the full timeout.
            Clock.schedule_once(
                lambda _dt: self._begin_local_voice_command_session(), 0
            )
            return
        self._clear_voice_indicator_override()
        self._hide_home_listening_state()
        self._refresh_voice_indicator()

    def _start_realtime_voice_session(self) -> None:
        if self.recording_state.get("active"):
            logger.info("Realtime voice session blocked while recording is active")
            return
        if self._realtime_voice_session is not None:
            logger.info(
                "Ending prior Realtime voice session before starting a new one"
            )
            self._end_realtime_voice_session()

        if self._realtime_session_pending:
            logger.debug(
                "Realtime voice session request already in flight; skipping duplicate"
            )
            self._realtime_launch_permitted = False
            return

        if not getattr(self, "_realtime_launch_permitted", False):
            logger.warning(
                "Realtime voice session rejected (not armed by wake phrase); using local assistant"
            )
            Clock.schedule_once(
                lambda _dt: self._begin_local_voice_command_session(), 0
            )
            return
        self._realtime_launch_permitted = False

        if not get_device_auth_token().strip():
            Clock.schedule_once(
                lambda _dt: self._begin_local_voice_command_session(), 0
            )
            return

        self._realtime_session_pending = True

        self._set_voice_indicator_override(
            "wake",
            "Connecting to assistant…",
            duration=None,
        )

        async def _go():
            def _friendly_realtime_failure(exc: BaseException) -> str:
                if isinstance(exc, httpx.HTTPStatusError):
                    code = exc.response.status_code
                    detail = ""
                    try:
                        body = exc.response.json()
                        if isinstance(body, dict) and body.get("detail"):
                            detail = str(body["detail"])
                    except Exception:
                        detail = (exc.response.text or "").strip()[:240]
                    if code == 503:
                        return "Realtime voice is off or not configured on the server."
                    if code == 502:
                        return "Could not start cloud assistant session. Try again later."
                    if code in (401, 403):
                        return "Device not authorized — check pairing."
                    tail = (detail or "").strip()
                    if tail:
                        return f"Assistant unavailable ({code}): {tail[:200]}"
                    return f"Assistant unavailable ({code})."
                if isinstance(
                    exc,
                    (
                        httpx.ConnectError,
                        httpx.ConnectTimeout,
                        httpx.ReadTimeout,
                        httpx.RemoteProtocolError,
                        httpx.HTTPError,
                    ),
                ):
                    return "Network error — could not reach the assistant."
                return "Cloud assistant unavailable."

            try:
                data = await self.backend.create_realtime_voice_session()
            except Exception as e:
                logger.warning("Realtime voice session request failed: %s", e)
                self._realtime_session_pending = False
                label = _friendly_realtime_failure(e)
                Clock.schedule_once(
                    lambda _dt: self._set_voice_indicator_override(
                        "wake", label, duration=5.5
                    ),
                    0,
                )
                Clock.schedule_once(
                    lambda _dt: self._begin_local_voice_command_session(), 5.6
                )
                return
            Clock.schedule_once(lambda _dt, d=data: self._run_realtime_voice_session(d), 0)

        run_async(_go())

    def _run_realtime_voice_session(self, data: dict) -> None:
        self._realtime_session_pending = False
        if self.recording_state.get("active"):
            logger.info("Realtime voice session launch cancelled because recording is active")
            self._sync_voice_assistant_state()
            return
        try:
            from realtime_voice_session import (
                RealtimeVoiceSession,
                extract_realtime_output_voice,
            )
        except ImportError:
            logger.exception("realtime_voice_session module missing")
            self._clear_voice_indicator_override()
            self._sync_voice_assistant_state()
            Clock.schedule_once(lambda _dt: self._begin_local_voice_command_session(), 0)
            return

        from config import BACKEND_URL

        secret = (data.get("client_secret") or "").strip()
        model = (data.get("model") or "").strip()
        sess_blob = data.get("session")
        rt_voice = ""
        if isinstance(sess_blob, dict):
            rt_voice = extract_realtime_output_voice(sess_blob)
        if not secret or not model:
            self._clear_voice_indicator_override()
            self._sync_voice_assistant_state()
            Clock.schedule_once(lambda _dt: self._begin_local_voice_command_session(), 0)
            return
        tok = get_device_auth_token().strip()

        def _before_realtime_mic() -> None:
            # Run from Realtime asyncio thread — do NOT block on Kivy Clock/Event:
            # schedule_once + threading.Event.wait is unreliable across threads on
            # some installs and causes on_before_open_mic timeouts → mic opens while
            # Vosk still holds ALSA → Realtime failures and silent assistant.
            self._realtime_mic_acquired = True
            try:
                self.voice_assistant.exit_command_window()
            except Exception:
                logger.exception("exit_command_window before realtime mic failed")
            try:
                self.voice_assistant.set_paused(True)
            except Exception:
                logger.exception("pause local Vosk before Realtime mic failed")

            def _ui_refresh(_dt):
                try:
                    self._refresh_voice_indicator()
                except Exception:
                    logger.debug("voice indicator refresh after Realtime mic handoff failed", exc_info=True)

            try:
                Clock.schedule_once(_ui_refresh, 0)
            except Exception:
                logger.debug("Clock.schedule_once for voice indicator skipped", exc_info=True)

        def _end() -> None:
            # If the session ended unexpectedly (WS dropped, OpenAI hit the
            # ~15-60 min hard session cap), immediately start a fresh session
            # so the user doesn't have to re-say the wake word mid-thought.
            sess = self._realtime_voice_session
            unexpected = False
            try:
                if sess is not None and hasattr(sess, "ended_unexpectedly"):
                    unexpected = bool(sess.ended_unexpectedly())
            except Exception:
                unexpected = False

            def _after_end(_dt):
                self._end_realtime_voice_session()
                if unexpected:
                    logger.info("Realtime session ended unexpectedly; auto-reconnecting.")
                    # Re-arm the launch permission (normally set by the wake
                    # word) so the reconnect bypasses the arming check.
                    self._realtime_launch_permitted = True

                    def _reconnect(_dt2):
                        try:
                            self._start_realtime_voice_session()
                        except Exception:
                            logger.exception("Realtime auto-reconnect failed")

                    Clock.schedule_once(_reconnect, 0.3)

            Clock.schedule_once(_after_end, 0)

        def _err(msg: str) -> None:
            logger.error("Realtime voice error: %s", msg)
            # Do not hide home listening here — session end + local fallback restore the UI.
            Clock.schedule_once(lambda _dt: self._end_realtime_voice_session(), 0)

        def _on_rt_connected() -> None:
            # Vosk is paused from on_before_open_mic right before ALSA opens for Realtime.
            def _ui(_dt):
                self._realtime_connected_ok = True
                self._set_voice_runtime_state("listening")
                self._clear_voice_indicator_override()
                self._set_voice_indicator_override(
                    "assistant_live",
                    "Speak now — assistant is listening",
                    duration=None,
                )
                self._sync_voice_assistant_state()
                # Clear previous session's transcript and show overlay
                if self._transcript_overlay is not None:
                    self._transcript_overlay.clear_session()
                    self._sync_transcript_overlay_mode()
                    # On home screen the say bar handles transcription; on
                    # other screens show the compact overlay strip.
                    if not (self.screen_manager
                            and self.screen_manager.current == 'home'):
                        self._transcript_overlay.show()
                # Reset home say bar for the new session
                self._clear_home_say_bar()

            Clock.schedule_once(_ui, 0)

        def _on_rt_state(state: str) -> None:
            self._set_voice_runtime_state(state)

            def _update_home(_dt):
                if (self.screen_manager is not None
                        and self.screen_manager.current == 'home'):
                    try:
                        home = self.screen_manager.get_screen('home')
                        home.set_voice_session_state(state)
                    except Exception:
                        pass

            Clock.schedule_once(_update_home, 0)

        # When VAD detects the user has finished speaking, drop in an
        # instant placeholder so the overlay reacts within a frame instead
        # of waiting ~1-2s for transcription. The placeholder is then
        # replaced in place when the real transcript / partial deltas arrive.
        def _on_user_speech_stopped() -> None:
            overlay = self._transcript_overlay
            if overlay is None:
                return
            # New utterance starts — reset the current bubble tracker so the
            # next transcript event creates a fresh bubble (or updates the new
            # placeholder), never the bubble from the previous utterance.
            self._current_user_msg_id = None
            if not getattr(self, "_pending_user_msg_id", None):
                self._pending_user_msg_id = overlay.add_user_message("…")
            # Update home say bar with a placeholder immediately
            def _say_bar_placeholder(_dt):
                if (self.screen_manager is not None
                        and self.screen_manager.current == 'home'):
                    try:
                        self.screen_manager.get_screen('home').update_say_bar_transcription("You", "…")
                    except Exception:
                        pass
            Clock.schedule_once(_say_bar_placeholder, 0)

        def _on_user_transcript(text: str) -> None:
            overlay = self._transcript_overlay
            if overlay is None:
                return
            pending = getattr(self, "_pending_user_msg_id", None)
            current = getattr(self, "_current_user_msg_id", None)
            if pending:
                # First transcript event for this utterance: replace the "…"
                # placeholder and remember the bubble id for subsequent deltas.
                overlay.update_user_message(pending, text)
                msg_id = pending
                self._pending_user_msg_id = None
                self._current_user_msg_id = msg_id
            elif current:
                # Subsequent partial delta or the final .completed event:
                # update the same bubble in place — never create a new one.
                overlay.update_user_message(current, text)
                msg_id = current
            else:
                # No placeholder and no active bubble (e.g. speech_stopped
                # never fired): create a fresh bubble and track it.
                msg_id = overlay.add_user_message(text)
                self._current_user_msg_id = msg_id

            # Also update home say bar with user transcript
            def _say_bar_user(_dt, _t=text):
                if (self.screen_manager is not None
                        and self.screen_manager.current == 'home'):
                    try:
                        self.screen_manager.get_screen('home').update_say_bar_transcription("You", _t)
                    except Exception:
                        pass
            Clock.schedule_once(_say_bar_user, 0)

            # Grammar correction — run only on non-trivial text so we don't
            # fire an API call for every tiny streaming fragment. The corrected
            # text replaces the bubble once the background call returns.
            if not text or len(text) < 4:
                return
            _backend = BACKEND_URL
            _token = tok

            def _correct_and_update():
                from api_client import correct_transcript_sync
                corrected = correct_transcript_sync(_backend, _token, text)
                if corrected and corrected != text:
                    Clock.schedule_once(
                        lambda _dt: overlay.update_user_message(msg_id, corrected), 0
                    )
                    def _say_bar_corrected(_dt, _c=corrected):
                        if (self.screen_manager is not None
                                and self.screen_manager.current == 'home'):
                            try:
                                self.screen_manager.get_screen('home').update_say_bar_transcription("You", _c)
                            except Exception:
                                pass
                    Clock.schedule_once(_say_bar_corrected, 0)

            threading.Thread(target=_correct_and_update, daemon=True).start()

        def _on_ai_transcript(text: str) -> None:
            # Final transcript at end-of-response. By now the streaming
            # delta callback has already created and populated the AI
            # bubble — there's nothing more to do unless streaming was
            # somehow skipped (e.g. no delta events fired at all).
            overlay = self._transcript_overlay
            if overlay is None or not text:
                return
            # If no active streaming bubble for any item, fall back to a
            # one-shot append so the message still shows up.
            if not overlay._active_ai_msg_ids:
                overlay.add_ai_message(text)

        def _on_ai_transcript_delta(item_id: str, accumulated: str) -> None:
            overlay = self._transcript_overlay
            if overlay is None:
                return
            overlay.stream_ai_message(item_id, accumulated)
            # Also update home say bar with streaming AI text
            def _say_bar_ai(_dt, _t=accumulated):
                if (self.screen_manager is not None
                        and self.screen_manager.current == 'home'):
                    try:
                        self.screen_manager.get_screen('home').update_say_bar_transcription("AI", _t)
                    except Exception:
                        pass
            Clock.schedule_once(_say_bar_ai, 0)

        try:
            self._realtime_connected_ok = False
            self._realtime_session_start_monotonic = time.monotonic()
            self._realtime_voice_session = RealtimeVoiceSession(
                client_secret=secret,
                model=model,
                backend_base_url=BACKEND_URL,
                device_token=tok,
                on_session_end=_end,
                on_error=_err,
                on_connected=_on_rt_connected,
                on_device_navigate=self._realtime_voice_navigate,
                output_voice=rt_voice or None,
                on_before_open_mic=_before_realtime_mic,
                on_state_change=_on_rt_state,
                on_user_transcript=_on_user_transcript,
                on_ai_transcript=_on_ai_transcript,
                on_ai_transcript_delta=_on_ai_transcript_delta,
                on_user_speech_stopped=_on_user_speech_stopped,
                on_email_draft=self._on_email_draft_directive,
                on_recipient_picker=self._on_recipient_picker_directive,
            )
            self._sync_voice_assistant_state()
            self._realtime_voice_session.start()
        except Exception:
            logger.exception("Realtime voice session failed to start")
            self._realtime_voice_session = None
            self._realtime_mic_acquired = False
            self._set_voice_runtime_state("idle")
            self._realtime_session_pending = False
            self._clear_voice_indicator_override()
            self._sync_voice_assistant_state()
            Clock.schedule_once(lambda _dt: self._begin_local_voice_command_session(), 0)

    def _espeak_amplitude(self) -> int:
        """Map stored volume 0–100 to espeak-ng -a (0–200)."""
        try:
            v = int(getattr(self, "assistant_speech_volume", 85) or 85)
        except (TypeError, ValueError):
            v = 85
        v = max(0, min(100, v))
        return max(0, min(200, int(round(v * 2))))

    def _speak_via_openai_tts(self, text: str) -> bool:
        """
        Call the server's /api/tts/speak endpoint (backed by OpenAI TTS).
        Returns True if audio was played successfully.
        Falls through silently on any error so espeak-ng can take over.
        """
        try:
            from config import BACKEND_URL
            token = get_device_auth_token().strip()
            if not token:
                return False

            resp = httpx.post(
                f"{BACKEND_URL}/api/tts/speak",
                json={"text": text, "voice": os.environ.get("OPENAI_TTS_VOICE", "shimmer")},
                headers={"Authorization": f"Bearer {token}"},
                timeout=20.0,
            )
            if resp.status_code != 200:
                logger.debug("OpenAI TTS server returned %s: %s", resp.status_code, resp.text[:200])
                return False

            audio_bytes = resp.content
            if not audio_bytes:
                return False

            aplay = shutil.which("aplay")
            if not aplay:
                logger.debug("aplay not found — cannot play OpenAI TTS audio")
                return False

            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            try:
                r_play = subprocess.run(
                    [aplay, "-q", *_tts_aplay_device_argv(), "-r", "24000", "-f", "S16_LE", "-c", "1", tmp_path],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
                if r_play.returncode != 0:
                    logger.debug("OpenAI TTS aplay exited %s", r_play.returncode)
                    return False
                return True
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception as exc:
            logger.debug("OpenAI TTS failed, falling back to espeak: %s", exc)
            return False

    def _speak_text_blocking(self, text: str) -> bool:
        phrase = (text or "").strip()
        if not phrase:
            return False

        # Drop this reply if another TTS call is already in progress.
        # Prevents multiple overlapping voices when the cloud returns
        # a reply while a previous one is still playing.
        if not self._speaking_lock.acquire(blocking=False):
            logger.debug("_speak_text_blocking: skipping (already speaking)")
            return False

        va = getattr(self, "voice_assistant", None)
        try:
            # Suppress Vosk mic input while speaker is active so the
            # device's own voice is not picked up and re-processed.
            if va is not None:
                va.set_tts_active(True)

            # --- 0. OpenAI TTS via server (natural AI voice — best quality) ---
            if self._speak_via_openai_tts(phrase):
                return True

            amp_n = self._espeak_amplitude()
            if amp_n <= 0:
                # UI can be slid to 0%; still attempt quiet offline fallback when cloud failed.
                amp_n = max(72, int(round(40 * 2)))
            amp = str(amp_n)

            # --- 1. piper (neural TTS — best quality, fully offline) ---
            piper = shutil.which("piper")
            aplay = shutil.which("aplay")
            if piper and aplay:
                piper_model = _pick_english_piper_model_path()
                if piper_model:
                    try:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                            tmp_path = tmp.name
                        proc = subprocess.run(
                            [piper, "--model", piper_model, "--output_file", tmp_path],
                            input=phrase.encode(),
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                            check=False,
                            env=_tts_english_child_env(),
                        )
                        if proc.returncode == 0 and os.path.getsize(tmp_path) > 0:
                            r_ap = subprocess.run(
                                [aplay, "-q", *_tts_aplay_device_argv(), tmp_path],
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=30,
                            )
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                            if r_ap.returncode == 0:
                                return True
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                    except Exception as e:
                        logger.debug("piper TTS failed: %s", e)

            # --- 2. mimic3 (Mycroft neural TTS — good quality) ---
            mimic3 = shutil.which("mimic3")
            if mimic3:
                try:
                    result = subprocess.run(
                        [mimic3, "--voice", "en_US/vctk_low#p236", phrase],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                        env=_tts_english_child_env(),
                    )
                    if result.returncode == 0:
                        return True
                except Exception as e:
                    logger.debug("mimic3 TTS failed: %s", e)

            # --- 3. espeak-ng / espeak ---
            # IMPORTANT: use a SINGLE attempt per binary — the cascade previously
            # tried multiple voice flags, and if en-us+f3 played audio but returned
            # non-zero (voice data not installed), the next variant would ALSO play
            # the same text (double/triple voice loop).  One attempt per binary;
            # only fall through to the stdout|aplay pipe if the binary couldn't run
            # at all (exception raised), not merely if it returned non-zero.
            esng = shutil.which("espeak-ng")
            esp = shutil.which("espeak")
            _espeak_ran = False
            for exe, voice_flags in [
                (esng, ["-v", "en-us", "-s", "130"]),
                (esp,  ["-v", "en",    "-s", "130"]),
            ]:
                if not exe:
                    continue
                try:
                    result = subprocess.run(
                        [exe, *voice_flags, "-a", amp, phrase],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=20,
                        env=_tts_english_child_env(),
                    )
                    _espeak_ran = True
                    if result.returncode == 0:
                        return True
                    # Non-zero exit but subprocess ran — audio may already have been
                    # produced (voice-data fallback).  Do NOT try the next variant;
                    # stop here to prevent the same text being spoken twice.
                    logger.debug("espeak returncode=%s (stopping cascade to avoid double-speak)",
                                 result.returncode)
                    break
                except Exception as e:
                    logger.debug("espeak attempt failed (%s): %s", voice_flags, e)

            # --- 4. espeak-ng --stdout | aplay (only when binary couldn't run at all) ---
            if esng and aplay and not _espeak_ran:
                try:
                    proc = subprocess.Popen(
                        [esng, "-v", "en-us", "-s", "130", "-a", amp, phrase, "--stdout"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        env=_tts_english_child_env(),
                    )
                    r_ap = None
                    es_rc = 1
                    try:
                        r_ap = subprocess.run(
                            [aplay, "-q", *_tts_aplay_device_argv()],
                            stdin=proc.stdout,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            timeout=30,
                        )
                    finally:
                        if proc.stdout:
                            proc.stdout.close()
                        try:
                            es_rc = proc.wait(timeout=5)
                        except Exception:
                            proc.kill()
                            try:
                                es_rc = int(proc.wait(timeout=2))
                            except Exception:
                                es_rc = 1
                    if (
                        r_ap is not None
                        and r_ap.returncode == 0
                        and es_rc == 0
                    ):
                        return True
                except Exception as e:
                    logger.warning("Voice feedback via espeak-ng stdout | aplay failed: %s", e)

            logger.warning("Voice feedback unavailable: no TTS engine found")
            return False

        finally:
            # Pause before mic on — avoids TTS tail / room echo triggering Vosk.
            import time as _time
            _time.sleep(_tts_tail_silence_seconds())
            # Wake suppression extends a bit longer (see _handle_voice_wake_phrase).
            self._last_tts_end_monotonic = _time.monotonic()
            try:
                if va is not None:
                    va.set_tts_active(False)
            except Exception:
                pass
            try:
                self._speaking_lock.release()
            except RuntimeError:
                pass

    def _speak_text_async(self, text: str) -> None:
        threading.Thread(
            target=self._speak_text_blocking,
            args=(text,),
            name="voice-feedback",
            daemon=True,
        ).start()

    @staticmethod
    def _voice_duration_seconds(text: str) -> float:
        words = max(1, len((text or "").split()))
        return min(8.0, max(2.5, words * 0.42))

    def _voice_reply(self, text: str, state: str = "speaking", duration: float | None = None) -> None:
        if not text:
            return
        if self.recording_state.get("active"):
            logger.debug("Voice reply suppressed while recording is active")
            return
        # Keep realtime voice identity consistent: do not play local fallback TTS
        # while a realtime session is active.
        if getattr(self, "_realtime_voice_session", None) is not None:
            return
        self._set_voice_indicator_override(
            state,
            text,
            duration if duration is not None else self._voice_duration_seconds(text),
        )
        self._speak_text_async(text)

    def _voice_reply_and_extend_listening(self, message: str, *, error: bool = False) -> None:
        """Speak assistant text; next question requires another wake phrase or mic tap."""
        # Strip markdown before TTS so symbols like ** aren't read aloud by espeak.
        clean = self._strip_markdown(message)
        clean = self._trim_realtime_reply(clean)
        msg = self._trim_voice_text(clean, 450)
        words = max(1, len(msg.split()))
        dur = min(45.0, max(2.5, words * 0.42))
        st = "error" if error else "speaking"
        self._voice_reply(msg, state=st, duration=dur)

    @staticmethod
    def _trim_realtime_reply(text: str) -> str:
        """Remove repetitive closers and keep speech concise."""
        s = " ".join((text or "").split())
        if not s:
            return s
        lowers = s.lower()
        closers = (
            "let me know if there is anything else",
            "let me know if you need anything else",
            "anything else i can help with",
            "take care",
            "all good",
        )
        for c in closers:
            pos = lowers.find(c)
            if pos > 0:
                s = s[:pos].rstrip(" ,.;:-")
                break
        # Keep default spoken output short unless user asks for detail.
        parts = [p.strip() for p in s.replace("?", ".").split(".") if p.strip()]
        if len(parts) > 3:
            s = ". ".join(parts[:3]).strip() + "."
        return s

    @staticmethod
    def _format_voice_duration(seconds: int) -> str:
        secs = max(0, int(seconds))
        hours, rem = divmod(secs, 3600)
        minutes, rem = divmod(rem, 60)
        parts = []
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if minutes:
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
        if rem or not parts:
            parts.append(f"{rem} second" + ("s" if rem != 1 else ""))
        return " ".join(parts[:2])

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove common markdown symbols so espeak does not read them aloud."""
        import re as _re
        s = text or ""
        # Bold/italic markers: **text**, *text*, __text__, _text_
        s = _re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
        s = _re.sub(r"_{1,3}([^_]+)_{1,3}", r"\1", s)
        # Inline code / backtick
        s = _re.sub(r"`([^`]+)`", r"\1", s)
        # Markdown links [text](url) → text
        s = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
        # Bare URLs
        s = _re.sub(r"https?://\S+", "", s)
        # Headings: # text
        s = _re.sub(r"^#{1,6}\s+", "", s, flags=_re.MULTILINE)
        # Bullet/numbered list markers
        s = _re.sub(r"^\s*[-*•]\s+", "", s, flags=_re.MULTILINE)
        s = _re.sub(r"^\s*\d+\.\s+", "", s, flags=_re.MULTILINE)
        # Collapse extra whitespace
        s = " ".join(s.split())
        return s

    @staticmethod
    def _trim_voice_text(text: str, max_chars: int = 220) -> str:
        s = " ".join((text or "").split())
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 1].rstrip() + "…"

    def _voice_selected_meeting_id(self) -> str | None:
        current = self.screen_manager.current if self.screen_manager else ""
        if current == "meeting_detail":
            try:
                return getattr(self.screen_manager.get_screen("meeting_detail"), "meeting_id", None)
            except Exception:
                return None
        return None

    def _clear_voice_confirmation_pending(self) -> None:
        if self._voice_confirmation_reset_ev:
            self._voice_confirmation_reset_ev.cancel()
            self._voice_confirmation_reset_ev = None
        self._voice_pending_confirmation = None
        self.voice_assistant.clear_confirmation()

    def _on_voice_confirmation_timeout(self, _dt) -> None:
        self._clear_voice_confirmation_pending()
        self._voice_reply("Confirmation timed out.", state="speaking", duration=3.0)

    def _voice_begin_confirmation(self, intent: VoiceIntent, prompt: str) -> None:
        self._clear_voice_confirmation_pending()
        self._voice_pending_confirmation = intent
        self.voice_assistant.begin_confirmation()
        self._voice_confirmation_reset_ev = Clock.schedule_once(
            self._on_voice_confirmation_timeout,
            self._voice_confirmation_timeout,
        )
        self._voice_reply(prompt, state="wake", duration=self._voice_confirmation_timeout)

    def _voice_cancel_confirmation(self, message: str = "Cancelled.") -> None:
        self._clear_voice_confirmation_pending()
        self._voice_reply(message, state="speaking", duration=3.0)

    def _voice_requires_confirmation(self, intent: VoiceIntent) -> bool:
        return intent.name in {
            "restart_device",
            "power_off",
            "unpair_device",
            "delete_this_meeting",
            "delete_old_meetings",
            "factory_reset",
        }

    def _voice_confirmation_prompt(self, intent: VoiceIntent) -> str:
        return {
            "restart_device": "Say confirm restart or cancel.",
            "power_off": "Say confirm shutdown or cancel.",
            "unpair_device": "Say confirm unpair or cancel.",
            "delete_this_meeting": "Say confirm delete meeting or cancel.",
            "delete_old_meetings": "Say confirm delete meetings or cancel.",
            "factory_reset": "Say confirm factory reset or cancel.",
        }.get(intent.name, "Say confirm or cancel.")

    def _voice_unsupported_message(self, topic: str | None) -> str:
        return {
            "volume_up": "I can't change speaker volume yet.",
            "volume_down": "I can't change speaker volume yet.",
            "mute": "I can't mute the speaker yet.",
            "unmute": "I can't unmute the speaker yet.",
            "speaker_test": "I can't run a speaker test yet.",
            "cpu_temperature": "I can't read CPU temperature yet.",
        }.get(topic or "", "I can't do that yet.")

    def _announce_voice_start_success(self) -> None:
        if self.recording_state.get("active"):
            self._voice_start_confirmation_pending = False
            return
        if not self._voice_start_confirmation_pending:
            return
        self._voice_start_confirmation_pending = False
        self._voice_reply(self.voice_confirmation_text, state="speaking", duration=3.0)

    def _handle_voice_intent(self, intent: VoiceIntent) -> None:
        Clock.schedule_once(lambda _dt, iv=intent: self._process_voice_intent(iv), 0)

    def _process_voice_intent(self, intent: VoiceIntent) -> None:
        # When OpenAI Realtime is on it handles the whole spoken turn.
        # Vosk often delivers wake-phrase + the follow-up question in a
        # single Result(), which fires this intent callback in parallel
        # with the Realtime mint. Without this guard the device plays
        # an espeak reply ("Opening inbox.") on top of Realtime's audio.
        if (
            getattr(self, "voice_realtime_assistant", False)
            and REALTIME_VOICE_IMPLEMENTED
            and bool(get_device_auth_token().strip())
            and not USE_MOCK_BACKEND
            and not WAKE_LOCAL_VOICE_ONLY
            # confirm/cancel are answers to a local prompt, not new commands
            and intent.name not in ("confirm", "cancel")
        ):
            return

        if intent.name == "confirm":
            if not self._voice_pending_confirmation:
                self._voice_reply("There is nothing to confirm right now.", duration=3.0)
                return
            pending = self._voice_pending_confirmation
            self._clear_voice_confirmation_pending()
            self._execute_voice_intent(pending)
            return

        if intent.name == "cancel":
            if not self._voice_pending_confirmation:
                self._voice_reply("Nothing is pending.", duration=3.0)
                return
            self._voice_cancel_confirmation()
            return

        if self._voice_pending_confirmation:
            self._voice_reply("Please say confirm or cancel first.", state="wake", duration=3.0)
            return

        if intent.name == "unsupported":
            self._voice_reply(self._voice_unsupported_message(intent.value), duration=3.5)
            return

        if self._voice_requires_confirmation(intent):
            self._voice_begin_confirmation(intent, self._voice_confirmation_prompt(intent))
            return

        self._execute_voice_intent(intent)

    def _voice_open_meeting_detail(self, meeting_id: str) -> None:
        detail = self.screen_manager.get_screen("meeting_detail")
        detail.set_meeting_id(meeting_id)
        self.goto_screen("meeting_detail", "slide_left")

    def _voice_save_setting_async(self, payload: dict, failure_text: str | None = None) -> None:
        async def _save():
            try:
                await self.backend.update_settings(payload)
            except Exception as e:
                logger.warning("voice settings update failed (%s): %s", payload, e)
                if failure_text:
                    Clock.schedule_once(
                        lambda _dt, msg=failure_text: self._voice_reply(msg, state="error"),
                        0,
                    )

        run_async(_save())

    def _voice_report_recording_status(self) -> None:
        if self.recording_state.get("active"):
            elapsed = self._format_voice_duration(self._current_recording_elapsed_seconds())
            if self.recording_state.get("paused"):
                self._voice_reply(f"Recording is paused. Elapsed time is {elapsed}.")
            else:
                self._voice_reply(f"Meeting recording is active. Elapsed time is {elapsed}.")
            return
        self._voice_reply("No meeting is recording right now.")

    def _voice_report_time(self) -> None:
        now = display_now()
        self._voice_reply(f"It is {now.strftime('%I:%M %p').lstrip('0')}.")

    def _voice_report_wifi_status(self) -> None:
        async def _run():
            try:
                info = await self.backend.get_system_info()
                ssid = (info.get("wifi_ssid") or "").strip()
                signal = int(info.get("wifi_signal") or 0)
                ip = (info.get("ip_address") or "").strip()
                if ssid:
                    msg = f"WiFi is connected to {ssid}"
                    if signal:
                        msg += f" at {signal} percent signal"
                    if ip:
                        msg += f". IP address is {ip}"
                    Clock.schedule_once(lambda _dt, m=msg + ".": self._voice_reply(m), 0)
                    return
                if linux_ethernet_ready():
                    msg = "The device is using wired network"
                    if ip:
                        msg += f". IP address is {ip}"
                    Clock.schedule_once(lambda _dt, m=msg + ".": self._voice_reply(m), 0)
                    return
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("The device is not connected to a network."),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't check network status.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_report_storage_left(self) -> None:
        async def _run():
            try:
                info = await self.backend.get_system_info()
                total = float(info.get("storage_total") or 0) / (1024 ** 3)
                used = float(info.get("storage_used") or 0) / (1024 ** 3)
                free = max(0.0, total - used)
                Clock.schedule_once(
                    lambda _dt: self._voice_reply(
                        f"The device has about {free:.0f} gigabytes free out of {total:.0f}."
                    ),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't check storage right now.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_report_version(self) -> None:
        async def _run():
            try:
                info = await self.backend.get_system_info()
                fw = (info.get("firmware_version") or "unknown").strip() or "unknown"
                Clock.schedule_once(
                    lambda _dt: self._voice_reply(f"The current firmware version is {fw}."),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't check the version right now.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_report_next_calendar(self) -> None:
        async def _run():
            try:
                data = await self.backend.get_home_summary()
                next_meeting = data.get("next_meeting") or {}
                title = (next_meeting.get("title") or "").strip()
                start = (next_meeting.get("start") or "").strip()
                if not title:
                    Clock.schedule_once(
                        lambda _dt: self._voice_reply("I don't have a next calendar meeting right now."),
                        0,
                    )
                    return
                line = title
                if start:
                    try:
                        if "T" in start:
                            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                            line += f", at {dt.astimezone(display_now().tzinfo).strftime('%I:%M %p').lstrip('0')}"
                        else:
                            line += f", on {start[:10]}"
                    except Exception:
                        pass
                Clock.schedule_once(
                    lambda _dt, m=f"Your next calendar meeting is {line}.": self._voice_reply(m),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't read the calendar right now.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_report_system_status(self) -> None:
        async def _run():
            try:
                info = await self.backend.get_system_info()
                fw = (info.get("firmware_version") or "unknown").strip() or "unknown"
                total = float(info.get("storage_total") or 0) / (1024 ** 3)
                used = float(info.get("storage_used") or 0) / (1024 ** 3)
                free = max(0.0, total - used)
                ssid = (info.get("wifi_ssid") or "").strip()
                if ssid:
                    network = f"WiFi is connected to {ssid}"
                elif linux_ethernet_ready():
                    network = "wired network is connected"
                else:
                    network = "network is disconnected"
                msg = f"{network}. About {free:.0f} gigabytes are free. Firmware version is {fw}."
                Clock.schedule_once(lambda _dt, m=msg: self._voice_reply(m), 0)
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't read system status.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_show_last_meeting(self, speak_summary: bool = False, speak_actions: bool = False) -> None:
        async def _run():
            try:
                meetings = await self.backend.get_meetings(limit=1)
                if not meetings:
                    Clock.schedule_once(
                        lambda _dt: self._voice_reply("There are no meetings to show yet."),
                        0,
                    )
                    return
                meeting = meetings[0]
                detail = await self.backend.get_meeting_detail(meeting["id"])

                def _open(_dt):
                    self._voice_open_meeting_detail(meeting["id"])
                    title = (meeting.get("title") or "the last meeting").strip()
                    if speak_summary:
                        summary = self._trim_voice_text(
                            ((detail.get("summary") or {}).get("summary") or "").strip() or "No summary is available yet."
                        )
                        self._voice_reply(f"Last meeting was {title}. {summary}")
                    elif speak_actions:
                        items = (detail.get("summary") or {}).get("action_items") or []
                        if not items:
                            self._voice_reply(f"{title} has no action items.")
                        else:
                            lines = []
                            for item in items[:3]:
                                task = (item.get("task") or item.get("title") or "").strip()
                                if task:
                                    lines.append(task)
                            if not lines:
                                self._voice_reply(f"{title} has action items, but I couldn't read them clearly.")
                            else:
                                joined = ". ".join(self._trim_voice_text(t, 90) for t in lines)
                                self._voice_reply(f"Action items from {title}: {joined}.")
                    else:
                        self._voice_reply(f"Opening the last meeting, {title}.", duration=3.0)

                Clock.schedule_once(_open, 0)
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't load the last meeting.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_disconnect_wifi(self) -> None:
        async def _run():
            try:
                await self.backend.disconnect_wifi()
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("WiFi disconnected."),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't disconnect WiFi.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_restart_device(self) -> None:
        self._voice_reply("Restarting device.", state="starting", duration=4.0)

        async def _run():
            local_ok = request_system_reboot()
            api_ok = False
            if not local_ok:
                try:
                    resp = await self.backend.update_settings({"action": "restart"})
                    api_ok = bool(resp.get("host_reboot_initiated"))
                except Exception as e:
                    logger.warning("voice restart fallback failed: %s", e)
            if not local_ok and not api_ok:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't restart the device.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_power_off(self) -> None:
        self._voice_reply("Shutting down the device.", state="starting", duration=4.0)

        async def _run():
            local_ok = request_system_poweroff()
            api_ok = False
            if not local_ok:
                try:
                    resp = await self.backend.update_settings({"action": "poweroff"})
                    api_ok = bool(resp.get("host_poweroff_initiated"))
                except Exception as e:
                    logger.warning("voice poweroff fallback failed: %s", e)
            if not local_ok and not api_ok:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't power off the device.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_unpair_device(self) -> None:
        self._voice_reply("Unpairing this device.", state="starting", duration=4.0)

        async def _run():
            try:
                await self.backend.unpair_self()
            except Exception:
                pass
            Clock.schedule_once(
                    lambda _dt: self.on_account_unpaired(remote=False),
                0,
            )

        run_async(_run())

    def _voice_delete_meeting(self, meeting_id: str) -> None:
        async def _run():
            try:
                await self.backend.delete_meeting(meeting_id)

                def _after(_dt):
                    if self.screen_manager.current == "meeting_detail":
                        self.goto_screen("meetings", "fade")
                    self._voice_reply("Meeting deleted.", state="speaking", duration=3.0)

                Clock.schedule_once(_after, 0)
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't delete that meeting.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_delete_old_meetings(self) -> None:
        async def _run():
            try:
                meetings = await self.backend.get_meetings(limit=100)
                if not meetings:
                    Clock.schedule_once(
                        lambda _dt: self._voice_reply("There are no meetings to delete."),
                        0,
                    )
                    return
                deleted = 0
                for meeting in meetings:
                    try:
                        await self.backend.delete_meeting(meeting["id"])
                        deleted += 1
                    except Exception:
                        continue
                Clock.schedule_once(
                    lambda _dt, n=deleted: self._voice_reply(
                        f"Deleted {n} meeting" + ("s." if n != 1 else "."),
                        state="speaking",
                    ),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't delete the meetings.", state="error"),
                    0,
                )

        run_async(_run())

    def _voice_factory_reset(self) -> None:
        self._voice_reply("Starting factory reset.", state="starting", duration=4.0)

        async def _run():
            try:
                await self.backend.update_settings({"action": "factory_reset"})
                request_system_reboot()
                Clock.schedule_once(
                    lambda _dt: self.reenter_onboarding_after_remote_reset(),
                    0,
                )
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self._voice_reply("I couldn't start factory reset.", state="error"),
                    0,
                )

        run_async(_run())

    def _execute_voice_intent(self, intent: VoiceIntent) -> None:
        if intent.name == "start_meeting":
            if self.recording_state.get("active"):
                self._voice_reply("A meeting is already recording.", duration=3.0)
                return
            logger.info('Voice trigger accepted ("hey buddy" -> "start meeting")')
            self._voice_start_in_flight = True
            self._voice_start_confirmation_pending = True
            self._reset_idle_timer()
            self._sync_voice_assistant_state()
            self._set_voice_indicator_override("starting", "Starting meeting", 4.0)
            self.start_recording()
            return

        if intent.name == "stop_meeting":
            if not self.recording_state.get("active"):
                self._voice_reply("No meeting is recording right now.")
                return
            self._voice_reply("Stopping meeting.", state="starting", duration=3.0)
            self.stop_recording()
            return

        if intent.name == "pause_meeting":
            if not self.recording_state.get("active"):
                self._voice_reply("No meeting is recording right now.")
                return
            if self.recording_state.get("paused"):
                self._voice_reply("Recording is already paused.")
                return
            self._voice_reply("Pausing meeting.", state="starting", duration=3.0)
            self.pause_recording()
            return

        if intent.name == "resume_meeting":
            if not self.recording_state.get("active"):
                self._voice_reply("No meeting is recording right now.")
                return
            if not self.recording_state.get("paused"):
                self._voice_reply("Recording is already running.")
                return
            self._voice_reply("Resuming meeting.", state="starting", duration=3.0)
            self.resume_recording()
            return

        if intent.name == "recording_status":
            self._voice_report_recording_status()
            return

        if intent.name == "recording_elapsed":
            if not self.recording_state.get("active"):
                self._voice_reply("No meeting is recording right now.")
            else:
                self._voice_reply(
                    f"Elapsed recording time is {self._format_voice_duration(self._current_recording_elapsed_seconds())}."
                )
            return

        if intent.name == "go_home":
            self.goto_screen("home", "fade")
            self._voice_reply("Going home.", duration=2.5)
            return

        if intent.name == "open_settings":
            self.goto_screen("settings", "slide_left")
            self._voice_reply("Opening settings.", duration=2.5)
            return

        if intent.name == "show_emails":
            self.goto_screen("emails", "slide_left")
            self._voice_reply("Opening inbox.", duration=2.5)
            return

        if intent.name == "show_calendar":
            self.goto_screen("calendar", "slide_left")
            self._voice_reply("Opening calendar.", duration=2.5)
            return

        if intent.name == "morning_brief":
            self.goto_screen("morning_brief", "slide_left")
            self._voice_reply("Opening your morning briefing.", duration=3.0)
            return

        if intent.name == "show_tasks":
            self.goto_screen("tasks", "slide_left")
            self._voice_reply("Opening your tasks.", duration=3.0)
            return

        if intent.name == "show_meetings":
            self.goto_screen("meetings", "slide_left")
            self._voice_reply("Opening meetings.", duration=2.5)
            return

        if intent.name == "show_last_meeting":
            self._voice_show_last_meeting()
            return

        if intent.name == "summarize_last_meeting":
            self._voice_show_last_meeting(speak_summary=True)
            return

        if intent.name == "read_action_items":
            self._voice_show_last_meeting(speak_actions=True)
            return

        if intent.name == "test_microphone":
            self.goto_screen("mic_test", "slide_left")
            self._voice_reply("Opening microphone test.", duration=3.0)
            return

        if intent.name == "what_time":
            self._voice_report_time()
            return

        if intent.name == "wifi_status":
            self._voice_report_wifi_status()
            return

        if intent.name == "storage_left":
            self._voice_report_storage_left()
            return

        if intent.name == "version_status":
            self._voice_report_version()
            return

        if intent.name == "next_calendar":
            self._voice_report_next_calendar()
            return

        if intent.name == "system_status":
            self._voice_report_system_status()
            return

        if intent.name == "privacy_mode":
            active = intent.value == "on"
            self.privacy_mode = active
            self._voice_save_setting_async(
                {"privacy_mode": active},
                failure_text="I couldn't save privacy mode.",
            )
            self._voice_reply(
                "Privacy mode is on." if active else "Privacy mode is off.",
                duration=3.0,
            )
            return

        if intent.name == "brightness":
            level = intent.value or "high"
            set_brightness(level)
            self._voice_save_setting_async(
                {"brightness": level},
                failure_text="I couldn't save brightness.",
            )
            self._voice_reply(f"Brightness set to {level}.", duration=3.0)
            return

        if intent.name == "screen_off":
            self._voice_reply("Showing idle screen.", duration=2.5)
            Clock.schedule_once(lambda _dt: self.goto_screen('idle', 'fade'), 0.2)
            return

        if intent.name == "wake_screen":
            self.goto_screen('home', 'fade')
            self._reset_idle_timer()
            self._voice_reply("Waking the screen.", duration=2.5)
            return

        if intent.name == "disconnect_wifi":
            self._voice_disconnect_wifi()
            return

        if intent.name == "pair_device":
            self.goto_screen("pair_device", "slide_left")
            self._voice_reply("Opening pairing screen.", duration=3.0)
            return

        if intent.name == "restart_device":
            self._voice_restart_device()
            return

        if intent.name == "power_off":
            self._voice_power_off()
            return

        if intent.name == "unpair_device":
            self._voice_unpair_device()
            return

        if intent.name == "delete_this_meeting":
            meeting_id = self._voice_selected_meeting_id()
            if not meeting_id:
                self._voice_reply("Open a meeting first so I know which one to delete.", duration=4.0)
                return
            self._voice_delete_meeting(meeting_id)
            return

        if intent.name == "delete_old_meetings":
            self._voice_delete_old_meetings()
            return

        if intent.name == "factory_reset":
            self._voice_factory_reset()
            return

        if intent.name == "help":
            self._voice_reply(
                "I can start, stop, pause or resume meetings, open settings or meetings, check time, WiFi, storage, version and calendar, change privacy or brightness, turn the screen off, and restart or shut down with confirmation."
            )
            return

        self._voice_reply("I can't do that yet.")

    # ==================================================================
    # UTILITIES
    # ==================================================================

    def _log_fps(self, _dt):
        logger.debug(f"FPS: {Clock.get_fps():.1f}")


# ==================================================================
# ==================================================================
# SwipeHandle — dedicated widget that opens the QuickPanel
# ==================================================================

class _SwipeHandle(Widget):
    """Invisible (but tappable) bar at the top of the screen.

    A simple tap OR a short downward drag on this widget opens the
    QuickPanel.  Using a real widget + touch.grab() is far more reliable
    than Window-level coordinate math, which breaks when touchscreen
    drivers use inverted or scaled Y axes.

    Visual: a subtle white pill line (iOS-style pull handle) drawn in
    the center of the bar so users can see where to interact.
    """

    _HANDLE_H = 22      # widget height in px
    _DRAG_THRESHOLD = 8  # px of downward drag that triggers the panel

    def __init__(self, app, **kwargs):
        from kivy.graphics import Color, RoundedRectangle
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", self._HANDLE_H)
        kwargs.setdefault("pos_hint", {"top": 1})
        super().__init__(**kwargs)
        self._app = app
        self._start_y: float | None = None

        # Subtle pill indicator so users know this area is interactive
        with self.canvas:
            Color(1, 1, 1, 0.18)
            self._pill = RoundedRectangle(radius=[3])
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *_):
        pw, ph = 36, 4
        self._pill.pos = (self.center_x - pw / 2,
                          self.y + (self.height - ph) / 2)
        self._pill.size = (pw, ph)

    # ------------------------------------------------------------------
    # Touch handling
    # ------------------------------------------------------------------

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        self._start_y = touch.y
        touch.grab(self)
        return True     # consume the touch so it doesn't fall through

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return False
        if self._start_y is None:
            return True
        # Downward drag (in Kivy y=0 is bottom, so "down" = y decreases)
        moved_down = self._start_y - touch.y
        # Also handle inverted-Y touchscreens (y increases when moving down)
        moved_any = abs(self._start_y - touch.y)
        if moved_any >= self._DRAG_THRESHOLD:
            self._open_panel()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        # Tap (very little movement) also opens the panel
        if self._start_y is not None:
            if abs(self._start_y - touch.y) < self._DRAG_THRESHOLD:
                self._open_panel()
        touch.ungrab(self)
        self._start_y = None
        return True

    def _open_panel(self):
        qp = getattr(self._app, "quick_panel", None)
        if qp and not qp._visible:
            qp.show()


# ==================================================================
# ENTRY POINT
# ==================================================================

def main():
    _boot_ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(
        f"[MeetingBox] Starting Device UI (pid={os.getpid()}, time={_boot_ts})",
        flush=True,
    )
    disp = os.environ.get('DISPLAY', '(not set)')
    print(f"[MeetingBox] DISPLAY={disp}", flush=True)
    print(f"[MeetingBox] FULLSCREEN={os.environ.get('FULLSCREEN', '(not set)')}", flush=True)
    print(
        f"[MeetingBox] SHOW_MOUSE_CURSOR={os.environ.get('SHOW_MOUSE_CURSOR', '(not set)')}",
        flush=True,
    )
    print(f"[MeetingBox] BACKEND_URL={os.environ.get('BACKEND_URL', '(not set)')}", flush=True)
    print(f"[MeetingBox] MOCK_BACKEND={os.environ.get('MOCK_BACKEND', '(not set)')}", flush=True)

    if sys.platform.startswith('linux'):
        xauth = os.environ.get("XAUTHORITY", "")
        if xauth:
            p = Path(xauth)
            if not p.is_file():
                print(
                    f"[MeetingBox] WARNING: XAUTHORITY={xauth!r} is not a readable file — "
                    "set XAUTHORITY_HOST in .env to the host cookie (see mini-pc/.env.example).",
                    flush=True,
                )
        if not shutil.which('xclip') and not shutil.which('xsel'):
            print(
                '[MeetingBox] Tip: sudo apt install xclip  '
                '(stops Kivy Cutbuffer CRITICAL on Linux)',
                flush=True,
            )
        if isinstance(disp, str) and (
            disp.startswith('localhost:') or ':10.' in disp
        ):
            print(
                '[MeetingBox] Tip: DISPLAY looks like SSH X11; for kiosk use '
                'local session e.g. DISPLAY=:0',
                flush=True,
            )
        x0 = Path("/tmp/.X11-unix/X0")
        if not x0.exists():
            print(
                "[MeetingBox] WARNING: no /tmp/.X11-unix/X0 — no X server on :0 inside this "
                "environment. On the mini PC: log in on the built-in screen (local graphical "
                "session), or use Xorg not Wayland-only, or set DISPLAY=:1 if X uses that.",
                flush=True,
            )

    try:
        result = subprocess.run(
            ['ls', '-la', '/tmp/.X11-unix/'],
            capture_output=True, text=True, timeout=5)
        print(f"[MeetingBox] X11 socket dir: {result.stdout.strip()}", flush=True)
    except Exception as e:
        print(f"[MeetingBox] X11 socket check failed: {e}", flush=True)

    _diagnose_xauthority_for_docker()

    logger.info("Starting MeetingBox Device UI")
    print(
        f"[MeetingBox] Kivy UI starting — full log: {LOG_FILE}",
        flush=True,
    )
    try:
        app = MeetingBoxApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        tb_str = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(
            f"[MeetingBox] FATAL: {type(e).__name__}: {e!r}",
            flush=True,
        )
        print("[MeetingBox] Traceback:", flush=True)
        for _line in tb_str.rstrip().splitlines():
            print(f"[MeetingBox]   {_line}", flush=True)
        sys.stdout.flush()
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
