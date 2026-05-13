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
from datetime import datetime
from pathlib import Path

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
    LOCAL_REDIS_HOST,
    LOCAL_REDIS_PORT,
    LOCAL_REDIS_ENABLED,
    display_now,
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
from voice_assistant import VoiceAssistant, VoiceIntent

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
from screens.speech_volume_picker import SpeechVolumePickerScreen
from screens.idle_timeout_picker import IdleTimeoutPickerScreen
from screens.mic_test import MicTestScreen
from screens.update_check import UpdateCheckScreen
from screens.update_install import UpdateInstallScreen

# Retained screens (still useful)
from screens.meetings import MeetingsScreen
from screens.meeting_detail import MeetingDetailScreen
from screens.wifi import WiFiScreen
from screens.system import SystemScreen
from screens.calendar import CalendarScreen
from screens.morning_brief import MorningBriefScreen
from screens.emails import EmailsScreen

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

        # WebSocket
        self.ws_task = None
        self._pairing_poll = None

        # Local Redis subscriber (audio_level / mic_test_level from audio container)
        self._local_redis_thread = None
        self._local_redis_stop = threading.Event()

        # Idle screen timeout (seconds; 0 = never).
        # Replaces the older display-off timer: instead of cutting the
        # backlight we navigate to the dedicated `idle` screen, which is
        # itself dismissed by any touch (back to home).
        self._idle_timeout_seconds = 30
        self._idle_event = None

        # Manual user pause for the wake-word assistant (toggled from the
        # home Listening pill). When True, _voice_assistant_should_listen
        # returns False even on listen-eligible screens. This is a UI knob,
        # not a privacy setting — flipping it here doesn't touch the
        # backend ``privacy_mode`` flag.
        self.user_voice_paused = False

        # Voice UI / feedback
        self.root_layout = None
        self.voice_indicator = None
        self._voice_indicator_override = None
        self._voice_indicator_reset_ev = None
        self._voice_start_in_flight = False
        self._voice_start_confirmation_pending = False
        self._voice_pending_confirmation: VoiceIntent | None = None
        self._voice_confirmation_reset_ev = None
        self._voice_confirmation_timeout = 8.0
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
        self.voice_realtime_assistant = False
        self.voice_wake_phrase_display = "Hey buddy"
        self.voice_assistant_enabled = True
        self.assistant_speech_volume = 85

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
        self.screen_manager.add_widget(SpeechVolumePickerScreen(name='speech_volume_picker'))
        self.screen_manager.add_widget(IdleTimeoutPickerScreen(name='idle_timeout_picker'))
        self.screen_manager.add_widget(MicTestScreen(name='mic_test'))
        self.screen_manager.add_widget(UpdateCheckScreen(name='update_check'))
        self.screen_manager.add_widget(UpdateInstallScreen(name='update_install'))

        self.screen_manager.add_widget(MeetingsScreen(name='meetings'))
        self.screen_manager.add_widget(MeetingDetailScreen(name='meeting_detail'))
        self.screen_manager.add_widget(WiFiScreen(name='wifi'))
        self.screen_manager.add_widget(SystemScreen(name='system'))
        self.screen_manager.add_widget(CalendarScreen(name='calendar'))
        self.screen_manager.add_widget(MorningBriefScreen(name='morning_brief'))
        self.screen_manager.add_widget(EmailsScreen(name='emails'))

        # BOOT: always start with splash
        self.screen_manager.current = 'splash'

        # Start WebSocket listener
        self.start_websocket_listener()

        # Optional: Redis pub/sub for audio levels when appliance runs with local Redis.
        # Disable with LOCAL_REDIS_ENABLED=0 when using a remote API only (no local Redis).
        if LOCAL_REDIS_ENABLED:
            self._start_local_redis_listener()
        # Voice assistant logic (wake phrase, intents) stays active; the floating
        # "Tony" overlay is intentionally not mounted. ``self.voice_indicator``
        # remains None (set in __init__) so existing _refresh/_set helpers no-op
        # via their ``if not self.voice_indicator`` guards.
        self._sync_voice_assistant_state()
        self._refresh_voice_indicator()

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
            await self.backend.get_pairing_status()
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
        self.voice_assistant.start()
        self._sync_voice_assistant_state()
        if not USE_MOCK_BACKEND:
            # Always align the HTTP client Bearer with persisted token (file may differ from __init__).
            tok = get_device_auth_token().strip()
            if tok:
                self.backend.set_device_auth_header(tok)
        Clock.schedule_once(self._check_backend, 2.0)
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
        self._local_redis_stop.set()
        if getattr(self, '_setup_poll', None):
            self._setup_poll.cancel()
        if getattr(self, '_pairing_poll', None):
            self._pairing_poll.cancel()
            self._pairing_poll = None
        if getattr(self, '_metrics_push', None):
            self._metrics_push.cancel()
            self._metrics_push = None
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
        run_async(self.backend.close())

    def _check_backend(self, _dt):
        async def _health():
            ok = await self.backend.health_check()
            if not ok:
                logger.error("Backend health check failed")
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
                    settings.get('idle_screen_timeout', '30'))
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

                vwp = (settings.get("voice_wake_phrase") or "hey buddy").strip()
                self.voice_wake_phrase_display = vwp[:1].upper() + vwp[1:] if vwp else "Hey buddy"
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

    async def _websocket_listener(self):
        try:
            async for event in self.backend.subscribe_events():
                etype = event.get('type')
                data = event.get('data') or event
                logger.debug(f"WS event: {etype}")

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
            await asyncio.sleep(5)
            Clock.schedule_once(lambda _: self.start_websocket_listener(), 0)

    # ==================================================================
    # LOCAL REDIS LISTENER (audio_level / mic_test_level from audio container)
    # ==================================================================

    _LOCAL_REDIS_EVENT_TYPES = frozenset({
        'audio_level', 'mic_test_level',
        'recording_started', 'recording_stopped',
        'recording_paused', 'recording_resumed',
    })

    def _start_local_redis_listener(self):
        if self._local_redis_thread and self._local_redis_thread.is_alive():
            return
        self._local_redis_stop.clear()
        t = threading.Thread(
            target=self._local_redis_subscriber_loop, daemon=True,
            name="local-redis-events",
        )
        t.start()
        self._local_redis_thread = t

    def _local_redis_subscriber_loop(self):
        try:
            import redis as _redis_mod
        except ImportError:
            logger.warning("redis package not installed — local audio levels unavailable")
            return

        backoff = 1
        while not self._local_redis_stop.is_set():
            try:
                rc = _redis_mod.Redis(
                    host=LOCAL_REDIS_HOST, port=LOCAL_REDIS_PORT,
                    decode_responses=True, socket_connect_timeout=5,
                )
                rc.ping()
                logger.info("Local Redis connected (%s:%s) — subscribing to 'events'",
                            LOCAL_REDIS_HOST, LOCAL_REDIS_PORT)
                backoff = 1
                pubsub = rc.pubsub()
                pubsub.subscribe("events")
                for msg in pubsub.listen():
                    if self._local_redis_stop.is_set():
                        break
                    if msg["type"] != "message":
                        continue
                    try:
                        event = json.loads(msg["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    etype = event.get("type")
                    if etype not in self._LOCAL_REDIS_EVENT_TYPES:
                        continue
                    data = event.get("data") or event
                    handler = {
                        'audio_level': self.on_audio_level,
                        'mic_test_level': self.on_mic_test_level,
                        'recording_started': self.on_recording_started,
                        'recording_stopped': self.on_recording_stopped,
                        'recording_paused': self.on_recording_paused,
                        'recording_resumed': self.on_recording_resumed,
                    }.get(etype)
                    if handler:
                        handler(data)
            except Exception as e:
                if not self._local_redis_stop.is_set():
                    logger.warning("Local Redis error (%s:%s): %s — retrying in %ss",
                                   LOCAL_REDIS_HOST, LOCAL_REDIS_PORT, e, backoff)
                    self._local_redis_stop.wait(backoff)
                    backoff = min(backoff * 2, 30)

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
        self._announce_voice_start_success()
        Clock.schedule_once(lambda _: self._sync_voice_assistant_state(), 0)
        Clock.schedule_once(lambda _: self.goto_screen('recording', 'fade'), 0)

    def _kick_post_stop_meeting_polls(self, sid):
        """HTTP fallbacks so processing screen gets transcript + summary without relying on WS."""
        if not sid:
            return
        Clock.schedule_once(lambda _dt, mid=sid: self._start_transcript_cta_poll(mid), 0)
        Clock.schedule_once(lambda _dt, mid=sid: self._start_summary_poll(mid), 0)

    def on_recording_stopped(self, data):
        self.recording_state['active'] = False
        try:
            self._processing_summary_cache.clear()
        except Exception:
            pass
        sid = data.get('session_id') or self.current_session_id
        self._kick_post_stop_meeting_polls(sid)
        self._voice_start_in_flight = False
        self._clear_recording_elapsed_clock()
        Clock.schedule_once(lambda _: self._sync_voice_assistant_state(), 0)
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
        screen = self.screen_manager.get_screen('processing')
        if hasattr(screen, 'on_backend_progress'):
            Clock.schedule_once(
                lambda _: screen.on_backend_progress(progress, status, eta), 0)

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
        try:
            self._processing_summary_cache[meeting_id] = {'ok': True, 'summary': summary or {}}
        except Exception:
            pass
        # Any path reaching here is the authoritative "summary ready" signal —
        # silence the fallback poll so we don't duplicate work.
        if self._summary_poll_meeting_id == meeting_id:
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
            if summary:
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
            has_summary = isinstance(summary_blob, dict) and bool(summary_blob)
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
                    Clock.schedule_once(lambda _: self._announce_voice_start_success(), 0)
                    Clock.schedule_once(lambda _: self._sync_voice_assistant_state(), 0)
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
                await self.backend.stop_recording(sid)
                self.recording_state['active'] = False
                self._voice_start_in_flight = False
                self._clear_recording_elapsed_clock()
                Clock.schedule_once(lambda _: self._sync_voice_assistant_state(), 0)
                logger.info("Recording stopped successfully")
                # Device-initiated stop never goes through on_recording_stopped (Redis/WS).
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

    def _apply_idle_timeout(self, value: str):
        """Configure idle-screen timeout.

        Accepts seconds as a string (``"30"``, ``"60"``, ``"120"``, ``"300"``)
        or ``"never"``. The legacy ``screen_timeout`` value (which was in
        minutes — ``"5"``, ``"10"``) is interpreted as minutes for backward
        compatibility so existing devices don't break on first read.
        """
        if self._idle_event:
            self._idle_event.cancel()
            self._idle_event = None

        v = (value or '').strip().lower()
        if v in ('', 'never', 'off', '0'):
            self._idle_timeout_seconds = 0
            return

        try:
            n = int(v)
        except ValueError:
            self._idle_timeout_seconds = 30
            self._reset_idle_timer()
            return
        # Legacy `screen_timeout` was in minutes; treat very small numbers as
        # minutes so a stored "5" still means "5 minutes" rather than 5s.
        if n <= 30:
            self._idle_timeout_seconds = n
        elif n <= 60:
            self._idle_timeout_seconds = n  # 60s sits in the new bucket
        else:
            self._idle_timeout_seconds = n
        self._reset_idle_timer()

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
        if getattr(self, "_realtime_voice_session", None) is not None:
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
        }
        return self.screen_manager.current not in blocked

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
        if self.voice_assistant.available and self._voice_assistant_should_listen():
            self.voice_indicator.set_state("idle", 'Say "Hey Tony"')
            return
        self.voice_indicator.set_state("hidden")

    def _clear_voice_indicator_override(self, *_args) -> None:
        if self._voice_indicator_reset_ev:
            self._voice_indicator_reset_ev.cancel()
            self._voice_indicator_reset_ev = None
        self._voice_indicator_override = None
        self._refresh_voice_indicator()
        self._sync_voice_assistant_state()

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
        timeout = max(2.0, self.voice_assistant.command_timeout_seconds)
        lbl = getattr(self, "voice_wake_phrase_display", "Hey buddy") or "Hey buddy"

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
            and get_device_auth_token().strip()
            and not USE_MOCK_BACKEND
            and not WAKE_LOCAL_VOICE_ONLY
        ):
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

    def _handle_voice_conversation_turn(self, text: str) -> None:
        """Cloud assistant Q&A for speech that is not a rigid local intent (person-like dialogue)."""
        if not getattr(self, "voice_assistant_enabled", True):
            return
        if self._voice_pending_confirmation:
            return
        phrase = (text or "").strip()
        if len(phrase) < 3:
            return

        async def _go():
            self._set_voice_indicator_override("wake", "Thinking…", duration=None)
            try:
                if not USE_MOCK_BACKEND and not get_device_auth_token().strip():
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
                Clock.schedule_once(lambda _dt, m=raw: self._voice_reply_and_extend_listening(m), 0)
            except Exception as e:
                logger.warning("Assistant conversation failed: %s", e)
                Clock.schedule_once(
                    lambda _dt: self._voice_reply_and_extend_listening(
                        "I could not reach the assistant. Check BACKEND_URL and your network.",
                        error=True,
                    ),
                    0,
                )
            finally:
                Clock.schedule_once(lambda _dt: self._clear_voice_indicator_override(), 0)

        run_async(_go())

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

    def _end_realtime_voice_session(self) -> None:
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
        self._realtime_session_start_monotonic = None
        self._realtime_connected_ok = False
        self._realtime_voice_session = None
        self._realtime_session_pending = False
        self._sync_voice_assistant_state()
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
        if self._realtime_voice_session is not None:
            logger.info(
                "Ending prior Realtime voice session before starting a new one"
            )
            self._end_realtime_voice_session()

        if self._realtime_session_pending:
            logger.debug(
                "Realtime voice session request already in flight; skipping duplicate"
            )
            return

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
            try:
                data = await self.backend.create_realtime_voice_session()
            except Exception as e:
                logger.warning("Realtime voice session request failed: %s", e)
                self._realtime_session_pending = False
                Clock.schedule_once(lambda _dt: self._clear_voice_indicator_override(), 0)
                Clock.schedule_once(
                    lambda _dt: self._begin_local_voice_command_session(), 0
                )
                return
            Clock.schedule_once(lambda _dt, d=data: self._run_realtime_voice_session(d), 0)

        run_async(_go())

    def _run_realtime_voice_session(self, data: dict) -> None:
        self._realtime_session_pending = False
        try:
            from realtime_voice_session import RealtimeVoiceSession
        except ImportError:
            logger.exception("realtime_voice_session module missing")
            self._clear_voice_indicator_override()
            self._sync_voice_assistant_state()
            Clock.schedule_once(lambda _dt: self._begin_local_voice_command_session(), 0)
            return

        from config import BACKEND_URL

        secret = (data.get("client_secret") or "").strip()
        model = (data.get("model") or "").strip()
        if not secret or not model:
            self._clear_voice_indicator_override()
            self._sync_voice_assistant_state()
            Clock.schedule_once(lambda _dt: self._begin_local_voice_command_session(), 0)
            return
        tok = get_device_auth_token().strip()

        def _end() -> None:
            Clock.schedule_once(lambda _dt: self._end_realtime_voice_session(), 0)

        def _err(msg: str) -> None:
            logger.error("Realtime voice error: %s", msg)
            # Do not hide home listening here — session end + local fallback restore the UI.
            Clock.schedule_once(lambda _dt: self._end_realtime_voice_session(), 0)

        def _on_rt_connected() -> None:
            # Run on Realtime worker thread — release Vosk mic before opening Realtime input.
            try:
                self.voice_assistant.set_paused(True)
            except Exception:
                logger.exception("pause local voice for Realtime failed")

            def _ui(_dt):
                self._realtime_connected_ok = True
                self._clear_voice_indicator_override()
                self._set_voice_indicator_override(
                    "assistant_live",
                    "Speak now — assistant is listening",
                    duration=None,
                )
                self._sync_voice_assistant_state()

            Clock.schedule_once(_ui, 0)

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
            )
            self._sync_voice_assistant_state()
            self._realtime_voice_session.start()
        except Exception:
            logger.exception("Realtime voice session failed to start")
            self._realtime_voice_session = None
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

    def _speak_text_blocking(self, text: str) -> bool:
        phrase = (text or "").strip()
        if not phrase:
            return False
        amp_n = self._espeak_amplitude()
        if amp_n <= 0:
            return True
        amp = str(amp_n)
        for cmd in (
            ["espeak-ng", "-s", "165", "-a", amp, phrase],
            ["espeak", "-s", "165", "-a", amp, phrase],
        ):
            exe = shutil.which(cmd[0])
            if not exe:
                continue
            try:
                subprocess.run(
                    [exe, *cmd[1:]],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                return True
            except Exception as e:
                logger.warning("Voice feedback via %s failed: %s", exe, e)
        esng = shutil.which("espeak-ng")
        aplay = shutil.which("aplay")
        if esng and aplay:
            try:
                proc = subprocess.Popen(
                    [esng, "-s", "165", "-a", amp, phrase, "--stdout"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    subprocess.run(
                        [aplay, "-q"],
                        stdin=proc.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=30,
                    )
                finally:
                    if proc.stdout:
                        proc.stdout.close()
                    proc.wait(timeout=30)
                return True
            except Exception as e:
                logger.warning("Voice feedback via espeak-ng stdout | aplay failed: %s", e)
        logger.warning("Voice feedback unavailable: no espeak command found")
        return False

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
        self._set_voice_indicator_override(
            state,
            text,
            duration if duration is not None else self._voice_duration_seconds(text),
        )
        self._speak_text_async(text)

    def _voice_reply_and_extend_listening(self, message: str, *, error: bool = False) -> None:
        """Speak assistant text and reopen the post-wake command window for multi-turn chat."""
        msg = self._trim_voice_text(message, 450)
        words = max(1, len(msg.split()))
        dur = min(45.0, max(2.5, words * 0.42))
        st = "error" if error else "speaking"
        self._voice_reply(msg, state=st, duration=dur)

        def _extend(_dt):
            try:
                self.voice_assistant.simulate_wake()
            except Exception:
                logger.debug("simulate_wake after assistant reply failed", exc_info=True)

        Clock.schedule_once(_extend, dur + 0.35)

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
        if not self._voice_start_confirmation_pending:
            return
        self._voice_start_confirmation_pending = False
        self._voice_reply(self.voice_confirmation_text, state="speaking", duration=3.0)

    def _handle_voice_intent(self, intent: VoiceIntent) -> None:
        Clock.schedule_once(lambda _dt, iv=intent: self._process_voice_intent(iv), 0)

    def _process_voice_intent(self, intent: VoiceIntent) -> None:
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
            logger.info('Voice trigger accepted ("hey tony" -> "start meeting")')
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
# ENTRY POINT
# ==================================================================

def main():
    print(f"[MeetingBox] Starting Device UI", flush=True)
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
    try:
        app = MeetingBoxApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"[MeetingBox] FATAL: {e}", flush=True)
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
