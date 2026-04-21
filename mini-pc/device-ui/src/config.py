"""
MeetingBox Device UI Configuration

Configure display, backend connection, and UI preferences.
Based on PRD v1.0 – Apple-inspired premium dark theme.
"""

import functools
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================================
# BACKEND CONNECTION
# ============================================================================

BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000')


def _default_ws_url(http_url: str) -> str:
    """If BACKEND_WS_URL is unset, derive WebSocket URL from BACKEND_URL (http→ws, https→wss)."""
    u = (http_url or "").strip().rstrip("/")
    if u.startswith("https://"):
        return "wss://" + u[8:] + "/ws"
    if u.startswith("http://"):
        return "ws://" + u[7:] + "/ws"
    return "ws://localhost:8000/ws"


BACKEND_WS_URL = (os.getenv("BACKEND_WS_URL", "") or "").strip() or _default_ws_url(BACKEND_URL)
DEVICE_AUTH_TOKEN = os.getenv('DEVICE_AUTH_TOKEN', '')
DEVICE_AUTH_TOKEN_FILE_NAME = 'device_auth_token'

# Use mock backend for testing (set MOCK_BACKEND=1)
USE_MOCK_BACKEND = os.getenv('MOCK_BACKEND', '0') == '1'

# API timeout in seconds
API_TIMEOUT = 30

# WebSocket reconnect settings
WS_RECONNECT_DELAY = 3  # seconds
WS_MAX_RECONNECT_ATTEMPTS = 10

# ============================================================================
# LOCAL REDIS (receive audio_level / mic_test_level from audio container)
# ============================================================================

LOCAL_REDIS_HOST = (os.getenv("LOCAL_REDIS_HOST", "") or "").strip() or "redis"
LOCAL_REDIS_PORT = int(os.getenv("LOCAL_REDIS_PORT", "6379"))

# ============================================================================
# MICROPHONE (mic test + should match mini-pc/audio capture device)
# ============================================================================

AUDIO_INPUT_DEVICE_INDEX = (os.getenv("AUDIO_INPUT_DEVICE_INDEX", "") or "").strip()
AUDIO_INPUT_DEVICE_NAME = (os.getenv("AUDIO_INPUT_DEVICE_NAME", "") or "").strip()

# ============================================================================
# DISPLAY SETTINGS
# ============================================================================


def _parse_display_px(name: str, default: int) -> int:
    """Env may be unset, empty, or non-numeric (e.g. DISPLAY_WIDTH= in .env) — avoid crashing."""
    raw = os.getenv(name)
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        logger.warning("%s is set but empty; using default %s", name, default)
        return default
    try:
        v = int(s)
    except ValueError:
        logger.warning("%s=%r is not an integer; using default %s", name, raw, default)
        return default
    if v < 32 or v > 32768:
        logger.warning("%s=%s out of range [32,32768]; using default %s", name, v, default)
        return default
    return v


def _parse_unit_scale(name: str, default: float) -> float:
    """Multiplier vs 1024×600 layout baseline (see MEETINGBOX_HOME_CONTENT_SCALE)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        logger.warning("%s is set but empty; using default %s", name, default)
        return default
    try:
        v = float(s)
    except ValueError:
        logger.warning("%s=%r is not a float; using default %s", name, raw, default)
        return default
    if v < 0.5 or v > 1.5:
        logger.warning("%s=%s out of range [0.5,1.5]; using default %s", name, v, default)
        return default
    return v


# Display resolution
# Figma-aligned default is 1024x600; override via env vars as needed.
DISPLAY_WIDTH = _parse_display_px("DISPLAY_WIDTH", 1024)
DISPLAY_HEIGHT = _parse_display_px("DISPLAY_HEIGHT", 600)

# Display orientation
DISPLAY_ORIENTATION = os.getenv('DISPLAY_ORIENTATION', 'landscape')

# Framerate
TARGET_FPS = int(os.getenv('TARGET_FPS', '30'))

# Fullscreen mode (set FULLSCREEN=0 for windowed dev mode)
FULLSCREEN = os.getenv('FULLSCREEN', '0') == '1'

# ============================================================================
# DISPLAY CLOCK (wall time in UI — default India Standard Time)
# ============================================================================
# Set DISPLAY_TIMEZONE to an IANA name (e.g. Europe/London) if needed.
# If zoneinfo data is missing, falls back to fixed UTC+5:30.


def _load_display_tzinfo():
    from datetime import timedelta, timezone

    name = (os.getenv("DISPLAY_TIMEZONE") or "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        logger.warning(
            "DISPLAY_TIMEZONE %r unavailable (install tzdata on this OS); using UTC+5:30",
            name,
        )
        return timezone(timedelta(hours=5, minutes=30))


DISPLAY_TZINFO = _load_display_tzinfo()


def display_now():
    """Current time in the configured display timezone (default IST)."""
    from datetime import datetime

    return datetime.now(DISPLAY_TZINFO)


def to_display_local(dt):
    """Convert an aware datetime to the display timezone; naive values treated as UTC."""
    from datetime import datetime, timezone

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZINFO)


# ============================================================================
# TOUCH SETTINGS
# ============================================================================

TOUCH_DEVICE = os.getenv('TOUCH_DEVICE', None)
TOUCH_CALIBRATION = os.getenv('TOUCH_CALIBRATION', None)
DOUBLE_TAP_TIME = 400
LONG_PRESS_TIME = 1000

# ============================================================================
# UI THEME – Apple-inspired premium dark
# ============================================================================

COLORS = {
    # Primary (blue gradient endpoints)
    'primary_start': (0.22, 0.55, 0.98, 1),    # #3888FA  bright blue
    'primary_end': (0.13, 0.45, 0.96, 1),       # #2273F5  deep blue

    # iOS-style status colours
    'green': (0.20, 0.78, 0.35, 1),             # #34C759
    'red': (1.0, 0.27, 0.23, 1),                # #FF453A
    'yellow': (1.0, 0.84, 0.04, 1),             # #FFD60A
    'blue': (0.22, 0.53, 0.98, 1),              # #3888FA

    # Surfaces
    'background': (0.11, 0.11, 0.12, 1),        # #1C1C1E  dark bg
    'surface': (0.17, 0.17, 0.18, 1),           # #2C2C2E  elevated
    'surface_light': (0.22, 0.22, 0.23, 1),     # #38383A  card bg
    'black': (0, 0, 0, 1),

    # Neutrals
    'white': (1, 1, 1, 1),
    'gray_300': (0.78, 0.78, 0.80, 1),          # #C7C7CC
    'gray_400': (0.68, 0.68, 0.70, 1),          # #AEAEB2
    'gray_500': (0.56, 0.56, 0.58, 1),          # #8E8E93
    'gray_600': (0.44, 0.44, 0.46, 1),          # #6E6E73
    'gray_700': (0.33, 0.33, 0.35, 1),          # #545458
    'gray_800': (0.23, 0.23, 0.24, 1),          # #3A3A3C
    'gray_900': (0.07, 0.07, 0.08, 1),          # #111214

    # Shadows / overlays
    'shadow': (0, 0, 0, 0.30),
    'shadow_light': (0, 0, 0, 0.15),
    'overlay': (0, 0, 0, 0.50),
    'overlay_red': (0.3, 0, 0, 0.50),

    # Border
    'border': (1, 1, 1, 0.10),

    # Transparent
    'transparent': (0, 0, 0, 0),
}

# Premium typography (SF Pro-like sizing)
FONT_SIZES = {
    'huge': 32,     # timer, large numbers
    'large': 22,    # titles, primary buttons
    'title': 20,    # settings title
    'medium': 17,   # body text, standard buttons
    'body': 16,     # regular body text
    'small': 13,    # secondary text, captions
    'tiny': 11,     # footer, helper text
}

# Button sizes (width, height in pixels)
BUTTON_SIZES = {
    'primary': (240, 60),
    'secondary': (180, 60),
    'small': (140, 50),
}

# Apple-like spacing
SPACING = {
    'screen_padding': 16,
    'button_spacing': 12,
    'section_spacing': 20,
    'list_item_spacing': 8,
}


def display_vertical_scale_raw() -> float:
    """Height vs 600px design baseline (capped)."""
    return min(max(DISPLAY_HEIGHT / 600.0, 0.72), 2.35)


def display_horizontal_scale_raw() -> float:
    """Width vs 1024px design baseline (capped)."""
    ratio = DISPLAY_WIDTH / 1024.0
    # Panels narrower than the 1024 design width (e.g. portrait 600×1024) must scale
    # down; the old 0.85 floor made everything oversized horizontally.
    if DISPLAY_WIDTH < 1024:
        return min(max(ratio, 0.48), 3.2)
    return min(max(ratio, 0.85), 3.2)


# Home uses this factor on top of display scale; other screens use OTHER (20% larger than home).
# Default 1.0 matches the Figma 1024×600 baseline; use MEETINGBOX_HOME_CONTENT_SCALE=0.75 on tight 7" panels.
HOME_CONTENT_SCALE = _parse_unit_scale("MEETINGBOX_HOME_CONTENT_SCALE", 1.0)
OTHER_CONTENT_SCALE = HOME_CONTENT_SCALE * 1.2


def home_layout_vertical_scale() -> float:
    return min(display_vertical_scale_raw(), 2.25) * HOME_CONTENT_SCALE


def home_layout_horizontal_scale() -> float:
    return display_horizontal_scale_raw() * HOME_CONTENT_SCALE


def other_screen_vertical_scale() -> float:
    return min(display_vertical_scale_raw(), 2.25) * OTHER_CONTENT_SCALE


def other_screen_horizontal_scale() -> float:
    return display_horizontal_scale_raw() * OTHER_CONTENT_SCALE


def home_center_column_width() -> int:
    """Wide panels: wide centered column; small panels: nearly full width (before HOME_CONTENT_SCALE)."""
    side = SPACING["screen_padding"] * 4
    usable = max(1, DISPLAY_WIDTH - side)
    if DISPLAY_WIDTH <= 1440:
        # Never wider than the display (old max(360, …) could exceed narrow widths).
        return max(160, usable)
    return min(2200, max(720, int(DISPLAY_WIDTH * 0.56)))


# More rounded corners (Apple style)
BORDER_RADIUS = 14

# Layout constants
STATUS_BAR_HEIGHT = 44
FOOTER_HEIGHT = 20
CONTENT_PADDING_H = 16
CONTENT_PADDING_V = 12

# ============================================================================
# ANIMATIONS & TRANSITIONS
# ============================================================================

ENABLE_ANIMATIONS = True

ANIMATION_DURATION = {
    'fast': 0.15,
    'normal': 0.3,
    'slow': 0.5,
}

# Screen transition durations (seconds)
TRANSITION_DURATION = {
    'fade': 0.3,
    'slide': 0.3,
    'fade_slow': 0.5,
}

# ============================================================================
# BOOT FLOW
# ============================================================================

# Splash screen duration (seconds)
SPLASH_DURATION = 2.0

# "You're All Set" screen duration (seconds)
ALL_SET_DURATION = 10.0

# ============================================================================
# FEATURES
# ============================================================================

# Enable live captions during recording
ENABLE_LIVE_CAPTIONS = True

# Live caption update interval (seconds)
LIVE_CAPTION_UPDATE_INTERVAL = 2

# Auto-return to home after processing complete (seconds)
AUTO_RETURN_DELAY = 5

# Number of recent meetings to show in list
MEETINGS_LIST_LIMIT = 20

# Enable haptic feedback
ENABLE_HAPTIC = False

# ============================================================================
# PRIVACY MODE
# ============================================================================

# Default privacy mode state (can be changed in settings).
# Default is OFF so cloud AI (summarization, etc.) is used unless user enables it.
DEFAULT_PRIVACY_MODE = False

# ============================================================================
# SCREEN SETTINGS (adjustable in device settings)
# ============================================================================

DEFAULT_BRIGHTNESS = 'high'        # low, medium, high
DEFAULT_SCREEN_TIMEOUT = 'never'   # never, 5min, 10min
DEFAULT_AUTO_DELETE = 'never'       # never, 30, 60, 90

# ============================================================================
# DEVICE INFO
# ============================================================================

DEVICE_MODEL = 'MeetingBox v1.0'


def _normalize_dashboard_config(raw: str) -> tuple[str, str]:
    """
    Parse DASHBOARD_URL env: accepts host:port or full URL (with optional trailing slash).
    Returns (short_label, public_url) — public_url has no trailing slash.
    """
    s = (raw or "").strip().rstrip("/")
    if not s:
        s = "meetingbox.local"
    low = s.lower()
    if low.startswith("https://"):
        rest = s[8:]
        hostport = rest.split("/")[0]
        if not hostport:
            hostport = "meetingbox.local"
        return hostport, f"https://{hostport}"
    if low.startswith("http://"):
        rest = s[7:]
        hostport = rest.split("/")[0]
        if not hostport:
            hostport = "meetingbox.local"
        return hostport, f"http://{hostport}"
    hostport = s.split("/")[0]
    return hostport, f"http://{hostport}"


_d_label, _d_public = _normalize_dashboard_config(os.getenv("DASHBOARD_URL", "meetingbox.local"))
# Compact host:port for subtitles (e.g. Configure at …)
DASHBOARD_URL = _d_label
# Full URL for QR codes and links (matches what users should open in a browser)
DASHBOARD_PUBLIC_URL = _d_public

# ============================================================================
# LOGGING
# ============================================================================

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', '/tmp/meetingbox-ui.log')
LOG_TO_CONSOLE = os.getenv('LOG_TO_CONSOLE', '1') == '1'

# ============================================================================
# PATHS
# ============================================================================

BASE_DIR = Path(__file__).parent.parent.resolve()
ASSETS_DIR = BASE_DIR / 'assets'
FONTS_DIR = ASSETS_DIR / 'fonts'
ICONS_DIR = ASSETS_DIR / 'icons'

# Legacy marker locations (older builds / server may have written here).
_SETUP_COMPLETE_LEGACY_MARKERS = (
    Path('/data/config/.setup_complete'),
    Path('/opt/meetingbox/data/config/.setup_complete'),
    Path('/opt/meetingbox/.setup_complete'),
    BASE_DIR / 'data' / 'config' / '.setup_complete',
)


def _system_config_dir_usable(d: Path) -> bool:
    """
    Use /data/config or /opt/... without mkdir(parents=True), which would try
    to create /data at filesystem root and fail with EACCES for normal users
    when /data does not exist (typical when running the UI outside Docker).
    """
    try:
        if d.is_dir():
            return bool(os.access(d, os.W_OK))
        parent = d.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            return False
        d.mkdir(exist_ok=True)
        return d.is_dir() and bool(os.access(d, os.W_OK))
    except OSError:
        return False


@functools.lru_cache(maxsize=1)
def resolve_device_config_dir() -> Path:
    """
    Writable directory for device_profiles.json and local .setup_complete.

    Prefers /data/config when the compose volume exists and is writable.
    Otherwise uses BASE_DIR/data/config (under the app tree).
    """
    for d in (Path('/data/config'), Path('/opt/meetingbox/data/config')):
        if _system_config_dir_usable(d):
            return d

    fb = BASE_DIR / 'data' / 'config'
    try:
        fb.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    if Path('/.dockerenv').exists():
        try:
            vol = Path('/data/config')
            vol_ok = vol.is_dir() and os.access(vol, os.W_OK)
        except OSError:
            vol_ok = False
        if not vol_ok:
            logger.warning(
                'Cannot persist to /data/config (not writable or missing). Using %s — '
                'setup and profiles are LOST on container restart. Fix on host: '
                'sudo chown -R 1000:1000 ./data/config',
                fb,
            )
    else:
        logger.debug(
            'Using application data dir %s (no writable /data/config on this host)',
            fb,
        )

    return fb


def setup_complete_marker_paths_for_read() -> tuple[Path, ...]:
    """If any path exists, first boot is done (keep in sync with needs_setup)."""
    seen: set[str] = set()
    out: list[Path] = []
    primary = resolve_device_config_dir() / '.setup_complete'
    for p in (primary, *_SETUP_COMPLETE_LEGACY_MARKERS):
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return tuple(out)


def setup_complete_marker_paths_for_write() -> tuple[Path, ...]:
    """Write .setup_complete to every distinct writable config root."""
    dirs: list[Path] = []
    primary = resolve_device_config_dir()
    dirs.append(primary)
    for d in (Path('/data/config'), Path('/opt/meetingbox/data/config')):
        if _system_config_dir_usable(d):
            dirs.append(d)
    seen: set[str] = set()
    uniq: list[Path] = []
    for d in dirs:
        try:
            k = str(d.resolve())
        except OSError:
            k = str(d)
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return tuple(d / '.setup_complete' for d in uniq)


def _device_token_storage_dirs() -> tuple[Path, ...]:
    """
    Config roots that may hold ``device_auth_token``.

    After reboot, ``resolve_device_config_dir()`` can switch (e.g. overlay
    ``/data/config`` becomes writable when the compose volume mounts). If we only
    ever read the token from the *current* primary, pairing looks "gone" even
    though the file still exists under another root. We read/write all distinct
    roots so the token survives path preference changes.
    """
    seen: set[str] = set()
    out: list[Path] = []
    for d in (
        Path('/data/config'),
        Path('/opt/meetingbox/data/config'),
        resolve_device_config_dir(),
        BASE_DIR / 'data' / 'config',
    ):
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return tuple(out)


def get_device_auth_token() -> str:
    """
    Bearer token for device API routes: prefer a persisted ``device_auth_token``
    file (claim always writes this) and fall back to env DEVICE_AUTH_TOKEN.

    Env-first was wrong for dev/docker: a stale DEVICE_AUTH_TOKEN in .env or
    compose overrides the file after pairing, so restart sends a bad Bearer,
    pairing-status returns 401, and the UI clears pairing.
    """
    for d in _device_token_storage_dirs():
        path = d / DEVICE_AUTH_TOKEN_FILE_NAME
        try:
            if path.is_file():
                # utf-8-sig strips a BOM; a leading U+FEFF breaks token hashing on the server.
                t = path.read_text(encoding='utf-8-sig').strip()
                if t:
                    return t
        except OSError:
            continue
    return (DEVICE_AUTH_TOKEN or "").strip()


def clear_stored_device_auth_token() -> None:
    """Remove persisted mbd_ token (after unpair). Env DEVICE_AUTH_TOKEN is unchanged."""
    for d in _device_token_storage_dirs():
        path = d / DEVICE_AUTH_TOKEN_FILE_NAME
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning('Could not remove device auth token file %s: %s', path, e)


def persist_device_auth_token(token: str) -> bool:
    """Save device API token under every writable config root (best-effort)."""
    t = (token or '').strip()
    if not t:
        return False
    ok_any = False
    for d in _device_token_storage_dirs():
        if d in (Path('/data/config'), Path('/opt/meetingbox/data/config')):
            if not _system_config_dir_usable(d):
                continue
        else:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
        path = d / DEVICE_AUTH_TOKEN_FILE_NAME
        try:
            path.write_text(t + '\n', encoding='utf-8')
            try:
                path.chmod(0o600)
            except OSError:
                pass
            ok_any = True
        except OSError as e:
            logger.debug('Could not persist device auth token to %s: %s', path, e)
    if not ok_any:
        logger.warning('Could not persist device auth token to any config directory')
    return ok_any


try:
    ASSETS_DIR.mkdir(exist_ok=True)
    FONTS_DIR.mkdir(exist_ok=True)
    ICONS_DIR.mkdir(exist_ok=True)
except OSError as e:
    logger.warning("Could not create assets/fonts/icons dirs: %s", e)

# ============================================================================
# DEVELOPMENT
# ============================================================================

DEV_MODE = os.getenv('DEV_MODE', '0') == '1'
SHOW_FPS = DEV_MODE or os.getenv('SHOW_FPS', '0') == '1'
DEBUG_BORDERS = DEV_MODE and os.getenv('DEBUG_BORDERS', '0') == '1'
