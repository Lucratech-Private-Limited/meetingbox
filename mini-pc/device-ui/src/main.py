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
import sys
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
_W = int(os.getenv('DISPLAY_WIDTH', '1024'))
_H = int(os.getenv('DISPLAY_HEIGHT', '600'))

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

        # WebSocket
        self.ws_task = None
        self._pairing_poll = None

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
        Window.show_cursor = (not FULLSCREEN) or SHOW_FPS

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

        if SHOW_FPS:
            Clock.schedule_interval(self._log_fps, 1.0)

        Window.bind(on_touch_down=self._reset_idle_timer)

        # Ensure the SDL window is mapped and on top (some WMs / SSH DISPLAY
        # combinations leave it hidden until raised).
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0)
        Clock.schedule_once(lambda *_: self._ensure_window_visible(), 0.3)

        # region agent log
        def _dbg_window(_dt):
            try:
                _debug_ndjson(
                    "H2",
                    "main.py:MeetingBoxApp.build",
                    "window_after_build",
                    {
                        "size": [float(Window.size[0]), float(Window.size[1])],
                        "pos": [float(Window.pos[0]), float(Window.pos[1])],
                        "fullscreen": bool(Window.fullscreen),
                    },
                )
            except Exception as ex:
                _debug_ndjson(
                    "H2",
                    "main.py:MeetingBoxApp.build",
                    "window_probe_failed",
                    {"error": str(ex)},
                )

        Clock.schedule_once(_dbg_window, 0.5)
        # endregion

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
        if getattr(self, '_setup_poll', None):
            self._setup_poll.cancel()
        if getattr(self, '_pairing_poll', None):
            self._pairing_poll.cancel()
            self._pairing_poll = None
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
    # EVENT HANDLERS
    # ==================================================================

    def on_recording_started(self, data):
        self.current_session_id = data.get('session_id')
        self.recording_state.update(active=True, paused=False, elapsed=0)
        Clock.schedule_once(lambda _: self.goto_screen('recording', 'fade'), 0)

    def on_recording_stopped(self, data):
        self.recording_state['active'] = False
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
        meeting_id = data.get('meeting_id')
        logger.info("Transcription complete for meeting %s", meeting_id)

        def _update_status(_dt):
            screen = self.screen_manager.get_screen('processing')
            if hasattr(screen, 'set_processing_status'):
                screen.set_processing_status('Transcription done. Building meeting report…')

        Clock.schedule_once(_update_status, 0)

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
        if meeting_id and self.screen_manager.current == 'processing':
            def _show(_dt):
                screen = self.screen_manager.get_screen('summary_review')
                screen.set_meeting_data(meeting_id, summary)
                self.goto_screen('summary_review', 'fade')
            Clock.schedule_once(_show, 0)

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

                def _show(_dt):
                    screen = self.screen_manager.get_screen('summary_review')
                    screen.set_meeting_data(meeting_id, summary)
                    self.goto_screen('summary_review', 'fade')

                Clock.schedule_once(_show, 0)
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
            try:
                result = await self.backend.start_recording()
                self.current_session_id = result['session_id']
                self.recording_state.update(active=True, paused=False, elapsed=0)
                Clock.schedule_once(
                    lambda _: self.goto_screen('recording', 'fade'), 0)
            except Exception as e:
                logger.error(f"Failed to start recording: {e}")
                Clock.schedule_once(
                    lambda _: self.show_error_screen(
                        'Recording Failed',
                        'Microphone error detected. The microphone may be '
                        'disconnected or in use by another application.',
                        recovery_text='TRY AGAIN',
                        recovery_action=self.start_recording), 0)
        run_async(_start())

    def stop_recording(self):
        logger.info("stop_recording called, session_id=%s", self.current_session_id)
        async def _stop():
            try:
                await self.backend.stop_recording(self.current_session_id)
                self.recording_state['active'] = False
                logger.info("Recording stopped successfully")
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

# region agent log
_DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "debug-422319.log"


def _debug_ndjson(hypothesis_id: str, location: str, message: str, data=None):
    try:
        rec = {
            "sessionId": "422319",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass
# endregion


def main():
    print(f"[MeetingBox] Starting Device UI", flush=True)
    disp = os.environ.get('DISPLAY', '(not set)')
    print(f"[MeetingBox] DISPLAY={disp}", flush=True)
    print(f"[MeetingBox] FULLSCREEN={os.environ.get('FULLSCREEN', '(not set)')}", flush=True)
    print(f"[MeetingBox] BACKEND_URL={os.environ.get('BACKEND_URL', '(not set)')}", flush=True)
    print(f"[MeetingBox] MOCK_BACKEND={os.environ.get('MOCK_BACKEND', '(not set)')}", flush=True)

    if sys.platform.startswith('linux'):
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

    import subprocess
    try:
        result = subprocess.run(
            ['ls', '-la', '/tmp/.X11-unix/'],
            capture_output=True, text=True, timeout=5)
        print(f"[MeetingBox] X11 socket dir: {result.stdout.strip()}", flush=True)
    except Exception as e:
        print(f"[MeetingBox] X11 socket check failed: {e}", flush=True)

    # region agent log
    _debug_ndjson(
        "H1",
        "main.py:main",
        "startup_env",
        {
            "DISPLAY": os.environ.get("DISPLAY"),
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
            "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
            "X0_exists": Path("/tmp/.X11-unix/X0").exists(),
        },
    )
    # endregion

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
