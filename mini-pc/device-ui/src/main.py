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
from pathlib import Path

import httpx

# Ensure the directory containing this file (src) is on sys.path so that
# imports of screens, components, config, api_client, etc. work regardless
# of how the app is run (e.g. python src/main.py vs python -m src.main).
_src_dir = Path(__file__).resolve().parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

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

from kivy.core.window import Window  # noqa: E402 — must import after Config

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
    TRANSITION_DURATION,
    DEFAULT_PRIVACY_MODE,
    LOCAL_REDIS_HOST,
    LOCAL_REDIS_PORT,
    setup_complete_marker_paths_for_read,
    get_device_auth_token,
    clear_stored_device_auth_token,
)

from api_client import BackendClient
from mock_backend import MockBackendClient
from hardware import set_brightness, screen_off, screen_on
from profile_store import get_active_profile, clear_active_profile_selection

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

# Settings & sub-screens
from screens.settings import SettingsScreen
from screens.auto_delete_picker import AutoDeletePickerScreen
from screens.brightness_picker import BrightnessPickerScreen
from screens.timeout_picker import TimeoutPickerScreen
from screens.mic_test import MicTestScreen
from screens.update_check import UpdateCheckScreen
from screens.update_install import UpdateInstallScreen

# Retained screens (still useful)
from screens.meetings import MeetingsScreen
from screens.meeting_detail import MeetingDetailScreen
from screens.wifi import WiFiScreen
from screens.system import SystemScreen

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


def _xauthority_has_display_zero(cookie_text: str) -> bool:
    """True if xauth list output likely includes authority for local display :0."""
    low = cookie_text.lower()
    if "unix:0" in low:
        return True
    for line in cookie_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fam = parts[0].lower()
        if fam.endswith(":0") and ":10" not in fam and ":11" not in fam:
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
        if disp.endswith(":0") and not _xauthority_has_display_zero(out):
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

        # WebSocket
        self.ws_task = None
        self._pairing_poll = None

        # Local Redis subscriber (audio_level / mic_test_level from audio container)
        self._local_redis_thread = None
        self._local_redis_stop = threading.Event()

        # Screen timeout
        self._screen_timeout_minutes = 0  # 0 = never
        self._idle_event = None
        self._screen_is_off = False

    # ==================================================================
    # BUILD
    # ==================================================================

    def build(self):
        logger.info("Building MeetingBox UI (fullscreen=%s, size=%sx%s)",
                    FULLSCREEN, DISPLAY_WIDTH, DISPLAY_HEIGHT)

        # Window geometry and fullscreen are already set via Config.set() at
        # module load time (before Window was imported), so no runtime
        # Window.size / Window.fullscreen calls needed here.

        # Show cursor in windowed mode (mouse/desktop).
        # Hide it only in fullscreen kiosk mode (touchscreen, no mouse).
        # If X11 auth failed, SDL never creates a window — show_cursor crashes internally.
        try:
            Window.show_cursor = (not FULLSCREEN) or SHOW_FPS
        except (AttributeError, TypeError) as e:
            # Do not crash the process: Docker restart loops look like a flickering panel with no UI.
            logger.error(
                "Kivy window not ready for show_cursor (X11/auth?). Continuing. "
                "Fix: xhost +local:docker and correct XAUTHORITY mount. Detail: %s",
                e,
            )

        # Screen manager – default to fade transition
        self.screen_manager = ScreenManager(
            transition=FadeTransition(duration=TRANSITION_DURATION['fade']))

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

        self.screen_manager.add_widget(SettingsScreen(name='settings'))
        self.screen_manager.add_widget(AutoDeletePickerScreen(name='auto_delete_picker'))
        self.screen_manager.add_widget(BrightnessPickerScreen(name='brightness_picker'))
        self.screen_manager.add_widget(TimeoutPickerScreen(name='timeout_picker'))
        self.screen_manager.add_widget(MicTestScreen(name='mic_test'))
        self.screen_manager.add_widget(UpdateCheckScreen(name='update_check'))
        self.screen_manager.add_widget(UpdateInstallScreen(name='update_install'))

        self.screen_manager.add_widget(MeetingsScreen(name='meetings'))
        self.screen_manager.add_widget(MeetingDetailScreen(name='meeting_detail'))
        self.screen_manager.add_widget(WiFiScreen(name='wifi'))
        self.screen_manager.add_widget(SystemScreen(name='system'))

        # BOOT: always start with splash
        self.screen_manager.current = 'splash'

        # Start WebSocket listener
        self.start_websocket_listener()

        # Start local Redis listener for real-time audio levels from the audio
        # container (both on the same Docker network).
        self._start_local_redis_listener()

        if SHOW_FPS:
            Clock.schedule_interval(self._log_fps, 1.0)

        Window.bind(on_touch_down=self._reset_idle_timer)

        # Ensure the SDL window is mapped and on top (some WMs / SSH DISPLAY
        # combinations leave it hidden until raised).
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0)
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0.3)

        logger.info("UI built – starting on splash screen")
        return self.screen_manager

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
        if not USE_MOCK_BACKEND:
            # Always align the HTTP client Bearer with persisted token (file may differ from __init__).
            tok = get_device_auth_token().strip()
            if tok:
                self.backend.set_device_auth_header(tok)
        Clock.schedule_once(self._check_backend, 2.0)
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
                self._apply_screen_timeout(
                    settings.get('screen_timeout', 'never'))
                privacy = settings.get('privacy_mode', False)
                self.privacy_mode = privacy
                auto_record = settings.get('auto_record', False)
                self.auto_record = auto_record
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
        Clock.schedule_once(lambda _: self.goto_screen('recording', 'fade'), 0)

    def _kick_post_stop_meeting_polls(self, sid):
        """HTTP fallbacks so processing screen gets transcript + summary without relying on WS."""
        if not sid:
            return
        Clock.schedule_once(lambda _dt, mid=sid: self._start_transcript_cta_poll(mid), 0)
        Clock.schedule_once(lambda _dt, mid=sid: self._start_summary_poll(mid), 0)

    def on_recording_stopped(self, data):
        self.recording_state['active'] = False
        sid = data.get('session_id') or self.current_session_id
        self._kick_post_stop_meeting_polls(sid)
        Clock.schedule_once(lambda _: self.goto_screen('processing', 'fade'), 0)

    def on_recording_paused(self, data):
        self.recording_state['paused'] = True
        screen = self.screen_manager.get_screen('recording')
        if hasattr(screen, 'on_paused'):
            Clock.schedule_once(lambda _: screen.on_paused(), 0)

    def on_recording_resumed(self, data):
        self.recording_state['paused'] = False
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
        summary = data.get('summary', {})
        if meeting_id:
            Clock.schedule_once(
                lambda _dt: self._show_processing_summary_ready(meeting_id, summary),
                0,
            )

    def _show_processing_summary_ready(self, meeting_id: str, summary: dict):
        """Keep user on processing screen and enable CTA once summary is ready."""
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
                screen = self.screen_manager.get_screen('recording')
                if hasattr(screen, 'on_resumed'):
                    Clock.schedule_once(lambda _: screen.on_resumed(), 0)
            except Exception as e:
                logger.error(f"Failed to resume: {e}")
                Clock.schedule_once(
                    lambda _: self.show_error_screen('Resume Failed', str(e)), 0)
        run_async(_resume())

    # ==================================================================
    # SCREEN TIMEOUT
    # ==================================================================

    def _apply_screen_timeout(self, value: str):
        """Configure screen timeout from setting value ('never', '5', '10')."""
        if self._idle_event:
            self._idle_event.cancel()
            self._idle_event = None

        if value == 'never' or not value:
            self._screen_timeout_minutes = 0
            return

        try:
            self._screen_timeout_minutes = int(value)
        except ValueError:
            self._screen_timeout_minutes = 0
            return

        self._reset_idle_timer()

    def _reset_idle_timer(self, *_args):
        """Reset the idle countdown. Called on every touch."""
        if self._screen_is_off:
            self._screen_is_off = False
            brightness = 'high'
            try:
                async def _get_br():
                    s = await self.backend.get_settings()
                    return s.get('brightness', 'high')
                import asyncio
                loop = get_async_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(_get_br(), loop)
            except Exception:
                pass
            screen_on(brightness)

        if self._idle_event:
            self._idle_event.cancel()

        if self._screen_timeout_minutes > 0:
            secs = self._screen_timeout_minutes * 60
            self._idle_event = Clock.schedule_once(
                self._on_idle_timeout, secs)

    def _on_idle_timeout(self, _dt):
        """Fires when idle timeout expires -- turn screen off."""
        if self.recording_state.get('active'):
            self._reset_idle_timer()
            return
        logger.info("Screen timeout — turning off display")
        self._screen_is_off = True
        screen_off()

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
