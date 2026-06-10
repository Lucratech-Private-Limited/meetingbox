"""Idle screen — pixel-perfect Figma 338:60 (yJqcY4KovVjJ11vjysW533).

Layout uses a FloatLayout with pos_hint / size_hint derived directly from
Figma's absolute coordinates in a 892 × 573 design frame.  All element
positions, sizes, font sizes, and colours come from the Figma spec.  Live
data (clock, weather, next meeting) is still refreshed at runtime.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from async_helper import run_async
from config import (
    ASSETS_DIR,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    display_now,
    to_display_local,
)
from screens.base_screen import BaseScreen
from weather_client import get_weather_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Figma design constants (frame 338:60, 892 × 573 px)
# ---------------------------------------------------------------------------
_FW = 892.0
_FH = 573.0

# Asset directories
_IDLE_DIR = ASSETS_DIR / "idle"
_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Figma colour palette
_WHITE = (1.0, 1.0, 1.0, 1.0)
_MUTED = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2
_BLUE  = (0.0, 0.420, 0.976, 1.0)     # #006BF9

# Font family names registered in main.py
_FONT  = "42dot-Sans"    # Regular / Bold (bold=True)
_FONT_SB  = "42dot-SB"   # SemiBold
_FONT_MED = "42dot-Med"  # Medium


# ---------------------------------------------------------------------------
# Coordinate helpers  (Figma → Kivy FloatLayout fractions)
# ---------------------------------------------------------------------------

def _x(px: float) -> float:
    """Figma X → pos_hint 'x' fraction (left edge)."""
    return px / _FW


def _y(figma_top: float, figma_h: float) -> float:
    """Figma Y-from-top + element height → pos_hint 'y' fraction (bottom edge)."""
    return max(0.0, (_FH - figma_top - figma_h) / _FH)


def _sw(px: float) -> float:
    """Figma width → size_hint_x."""
    return px / _FW


def _sh(px: float) -> float:
    """Figma height → size_hint_y."""
    return px / _FH


def _ff(fs: float) -> int:
    """Scale a Figma font size (px) proportionally to the physical display."""
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * scale))


# ---------------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------------

def _idle_asset(name: str) -> str:
    p = _IDLE_DIR / name
    return str(p) if p.is_file() else ""


def _figma_asset(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _greeting(name: str | None) -> str:
    hour = display_now().hour
    head = (
        "Good morning" if hour < 12
        else "Good afternoon" if hour < 17
        else "Good evening"
    )
    nm = (name or "").strip()
    if nm:
        return f"{head}, {nm.split()[0]}"
    return head


def _format_meeting(next_meeting: dict | None) -> tuple[str, str, int]:
    """Return (time_str, title, more_count) from a home-summary meeting dict."""
    if not next_meeting:
        return ("Free today", "", 0)
    title = (next_meeting.get("title") or "Calendar event").strip() or "Calendar event"
    tnorm = title.lower().replace("_", " ").strip()
    if tnorm in ("schedule request", "schedule requested"):
        return ("Free today", "", 0)
    start = (next_meeting.get("start") or "").strip()
    if not start:
        return ("Free today", "", 0)
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            local_dt = to_display_local(dt)
            if local_dt.date() != display_now().date():
                return ("Free today", "", 0)
            time_str = local_dt.strftime("%I:%M %p").lstrip("0")
        else:
            d = datetime.strptime(start[:10], "%Y-%m-%d")
            if d.date() != display_now().date():
                return ("Free today", "", 0)
            time_str = d.strftime("%b %d · all day")
    except (TypeError, ValueError):
        return ("Free today", "", 0)
    return (time_str, title, 0)


def _pick_next_today_meeting_from_week(week_payload: dict | None) -> dict | None:
    if not isinstance(week_payload, dict):
        return None
    days = week_payload.get("days")
    if not isinstance(days, dict):
        return None
    today_key = display_now().date().isoformat()
    rows = (days.get(today_key) or {}).get("meetings") or []
    if not isinstance(rows, list) or not rows:
        return None
    now_local = display_now()
    parsed: list[tuple[datetime, dict]] = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        raw = (m.get("start") or m.get("start_time") or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            loc = to_display_local(dt)
            parsed.append((loc, m))
        except Exception:
            continue
    if not parsed:
        return None
    parsed.sort(key=lambda x: x[0])
    for dt, row in parsed:
        if dt >= now_local:
            return row
    return parsed[-1][1]


def _lbl(
    text: str,
    font_name: str,
    font_size: int,
    color: tuple,
    *,
    bold: bool = False,
    halign: str = "left",
    valign: str = "top",
    **kwargs,
) -> Label:
    """Create a Label with text_size bound to its own size for proper alignment."""
    lbl = Label(
        text=text,
        font_name=font_name,
        font_size=font_size,
        bold=bold,
        color=color,
        halign=halign,
        valign=valign,
        **kwargs,
    )
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


# ---------------------------------------------------------------------------
# Start Recording card
# ---------------------------------------------------------------------------

class _RecordingCard(ButtonBehavior, FloatLayout):
    """Tappable card with downloaded gradient PNG background.

    Figma: Group 12 at (427, 356), 414 × 167 px — background is Rectangle 3
    (node 395:170) with linear-gradient(#0038B6 → #002376) and r=30.
    """

    # Figma card dimensions (used for relative child positioning)
    _CW = 414.0
    _CH = 167.0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ------------------------------------------------------------------
        # Gradient background
        # ------------------------------------------------------------------
        bg_src = _idle_asset("recording_btn_bg.png")
        if bg_src:
            self.add_widget(
                Image(
                    source=bg_src,
                    size_hint=(1, 1),
                    pos_hint={"x": 0, "y": 0},
                    fit_mode="fill",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )
        else:
            # Programmatic fallback — single mid-blue approximation
            with self.canvas.before:
                Color(0.0, 0.18, 0.60, 1.0)
                self._fallback_bg = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[_ff(30)]
                )
            self.bind(
                pos=lambda *_: setattr(self._fallback_bg, "pos", self.pos),
                size=lambda *_: setattr(self._fallback_bg, "size", self.size),
            )

        # ------------------------------------------------------------------
        # Mic orb  — Group 16 at (27, 32) within card, 101 × 101
        # ------------------------------------------------------------------
        orb_src = _idle_asset("mic_orb.png")
        if orb_src:
            cw, ch = self._CW, self._CH
            self.add_widget(
                Image(
                    source=orb_src,
                    size_hint=(101.0 / cw, 101.0 / ch),
                    pos_hint={"x": 27.0 / cw, "y": (ch - 32.0 - 101.0) / ch},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # ------------------------------------------------------------------
        # "Start Recording" — (165, 49), 219 × 36, Bold 30px
        # ------------------------------------------------------------------
        cw, ch = self._CW, self._CH
        lbl_title = _lbl(
            "Start Recording",
            _FONT,
            _ff(30),
            _WHITE,
            bold=True,
            valign="middle",
            size_hint=(219.0 / cw, 36.0 / ch),
            pos_hint={"x": 165.0 / cw, "y": (ch - 49.0 - 36.0) / ch},
        )
        self.add_widget(lbl_title)

        # ------------------------------------------------------------------
        # Subtitle — (148, 94), 253 × 24, SemiBold 20px
        # ------------------------------------------------------------------
        lbl_sub = _lbl(
            'Tap or say "start recording"',
            _FONT_SB,
            _ff(20),
            _WHITE,
            size_hint=(253.0 / cw, 24.0 / ch),
            pos_hint={"x": 148.0 / cw, "y": (ch - 94.0 - 24.0) / ch},
        )
        self.add_widget(lbl_sub)


# ---------------------------------------------------------------------------
# Idle Screen
# ---------------------------------------------------------------------------

class IdleScreen(BaseScreen):
    """Lock-screen-style idle UI matching Figma 338:60 pixel-for-pixel."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_event = None
        self._home_summary_event = None
        self._weather = get_weather_client()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # ------------------------------------------------------------------
        # 1. Solid background colour  #010C25
        # ------------------------------------------------------------------
        with root.canvas.before:
            Color(0.004, 0.047, 0.145, 1.0)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._sync_bg, size=self._sync_bg)

        # ------------------------------------------------------------------
        # 2. Background photo (full-bleed, same as Figma imageRef beb9aab8…)
        # ------------------------------------------------------------------
        bg_src = _idle_asset("background_landscape.png")
        if bg_src:
            root.add_widget(
                Image(
                    source=bg_src,
                    size_hint=(1, 1),
                    pos_hint={"x": 0, "y": 0},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )

        # ------------------------------------------------------------------
        # 3. "Good morning, J.K"  — (46, 35)  171 × 24  SemiBold 20 #FFF
        # ------------------------------------------------------------------
        self.greeting_label = _lbl(
            _greeting(None),
            _FONT_SB,
            _ff(20),
            _WHITE,
            size_hint=(_sw(171), _sh(24)),
            pos_hint={"x": _x(46), "y": _y(35, 24)},
        )
        root.add_widget(self.greeting_label)

        # ------------------------------------------------------------------
        # 4. Big clock "11:01"  — (46, 59)  237 × 119  Bold 100 #FFF
        # ------------------------------------------------------------------
        self.time_label = _lbl(
            "--:--",
            _FONT,
            _ff(100),
            _WHITE,
            bold=True,
            valign="top",
            size_hint=(_sw(237), _sh(119)),
            pos_hint={"x": _x(46), "y": _y(59, 119)},
        )
        root.add_widget(self.time_label)

        # ------------------------------------------------------------------
        # 5. "AM"  — (312, 121)  55 × 42  SemiBold 35  #B6BAF2
        # ------------------------------------------------------------------
        self.ampm_label = _lbl(
            "",
            _FONT_SB,
            _ff(35),
            _MUTED,
            valign="top",
            size_hint=(_sw(55), _sh(42)),
            pos_hint={"x": _x(312), "y": _y(121, 42)},
        )
        root.add_widget(self.ampm_label)

        # ------------------------------------------------------------------
        # 6. Date "Tuesday, May 21"  — (46, 178)  230 × 36  SemiBold 30 #FFF
        # ------------------------------------------------------------------
        self.date_label = _lbl(
            "",
            _FONT_SB,
            _ff(30),
            _WHITE,
            valign="top",
            size_hint=(_sw(230), _sh(36)),
            pos_hint={"x": _x(46), "y": _y(178, 36)},
        )
        root.add_widget(self.date_label)

        # ------------------------------------------------------------------
        # 7. Sun icon  — (677, 75)  64 × 64
        # ------------------------------------------------------------------
        sun_src = _idle_asset("icon_sun.png")
        if not sun_src:
            sun_src = _figma_asset("icon_sun.png")
        if sun_src:
            self.weather_icon = Image(
                source=sun_src,
                size_hint=(_sw(64), _sh(64)),
                pos_hint={"x": _x(677), "y": _y(75, 64)},
                fit_mode="contain",
                allow_stretch=True,
            )
            root.add_widget(self.weather_icon)
        else:
            self.weather_icon = None

        # ------------------------------------------------------------------
        # 8. Temperature "28°C"  — (759, 86)  82 × 42  Bold 35 #FFF
        # ------------------------------------------------------------------
        self.temp_label = _lbl(
            "--°C",
            _FONT,
            _ff(35),
            _WHITE,
            bold=True,
            valign="top",
            size_hint=(_sw(82), _sh(42)),
            pos_hint={"x": _x(759), "y": _y(86, 42)},
        )
        root.add_widget(self.temp_label)

        # ------------------------------------------------------------------
        # 9. Condition "Sunny"  — (759, 134)  85 × 36  Medium 30  #B6BAF2
        # ------------------------------------------------------------------
        self.condition_label = _lbl(
            "--",
            _FONT_MED,
            _ff(30),
            _MUTED,
            valign="top",
            size_hint=(_sw(85), _sh(36)),
            pos_hint={"x": _x(759), "y": _y(134, 36)},
        )
        root.add_widget(self.condition_label)

        # ==================================================================
        # Schedule group  (origin 46, 333 in Figma)
        # ==================================================================

        # "Next up"  — (46, 333)  100 × 33  SemiBold 28  #006BF9
        self.next_label = _lbl(
            "Next up",
            _FONT_SB,
            _ff(28),
            _BLUE,
            size_hint=(_sw(100), _sh(33)),
            pos_hint={"x": _x(46), "y": _y(333, 33)},
        )
        root.add_widget(self.next_label)

        # Calendar icon  — group+(0, 60) = abs (46, 393)  34 × 34
        cal_src = _idle_asset("icon_calendar.png")
        if not cal_src:
            cal_src = _figma_asset("icon_calendar.png")
        if cal_src:
            root.add_widget(
                Image(
                    source=cal_src,
                    size_hint=(_sw(34), _sh(34)),
                    pos_hint={"x": _x(46), "y": _y(393, 34)},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # "11:00 AM"  — group+(53, 62) = abs (99, 395)  120 × 33  SemiBold 28  #006BF9
        self.next_time_label = _lbl(
            "--:-- --",
            _FONT_SB,
            _ff(28),
            _BLUE,
            size_hint=(_sw(120), _sh(33)),
            pos_hint={"x": _x(99), "y": _y(395, 33)},
        )
        root.add_widget(self.next_time_label)

        # "Now : Product Sync"  — group+(0, 113) = abs (46, 446)  282 × 37  Bold 31 #FFF
        self.next_title_label = _lbl(
            "--",
            _FONT,
            _ff(31),
            _WHITE,
            bold=True,
            size_hint=(_sw(282), _sh(37)),
            pos_hint={"x": _x(46), "y": _y(446, 37)},
        )
        root.add_widget(self.next_title_label)

        # "+2 more"  — group+(0, 164) = abs (46, 497)  107 × 33  Bold 28  #006BF9
        self.more_label = _lbl(
            "",
            _FONT,
            _ff(28),
            _BLUE,
            bold=True,
            size_hint=(_sw(107), _sh(33)),
            pos_hint={"x": _x(46), "y": _y(497, 33)},
        )
        root.add_widget(self.more_label)

        # ==================================================================
        # Start Recording card  — (427, 356)  414 × 167
        # ==================================================================
        card = _RecordingCard(
            size_hint=(_sw(414), _sh(167)),
            pos_hint={"x": _x(427), "y": _y(356, 167)},
        )
        card.bind(on_release=self._on_start_recording)
        self._cta_card = card
        root.add_widget(card)

        self.add_widget(root)

    def _sync_bg(self, widget, *_args):
        self._bg_rect.pos = widget.pos
        self._bg_rect.size = widget.size

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self._update_clock()
        self._update_weather(self._weather.snapshot)
        self._weather.subscribe(self._update_weather)
        self._refresh_home_summary()
        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(
            lambda _dt: self._update_clock(), 1.0
        )
        if self._home_summary_event:
            self._home_summary_event.cancel()
        self._home_summary_event = Clock.schedule_interval(
            lambda _dt: self._refresh_home_summary(), 30.0
        )

    def on_leave(self):
        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None
        if self._home_summary_event:
            self._home_summary_event.cancel()
            self._home_summary_event = None
        self._weather.unsubscribe(self._update_weather)

    # ------------------------------------------------------------------
    # Touch: full screen → home, card → start recording
    # ------------------------------------------------------------------

    def on_touch_up(self, touch):
        if super().on_touch_up(touch):
            return True
        lx, ly = self.to_widget(touch.x, touch.y)
        if not self.collide_point(lx, ly):
            return False
        cc = getattr(self, "_cta_card", None)
        if cc is not None:
            cx, cy = cc.to_widget(touch.x, touch.y)
            if cc.collide_point(cx, cy):
                self._on_start_recording(None)
                return True
        try:
            self.goto("home", transition="fade")
        except Exception:
            logger.debug("idle: goto(home) failed", exc_info=True)
        return True

    # ------------------------------------------------------------------
    # Live data
    # ------------------------------------------------------------------

    def _update_clock(self):
        now = display_now()
        self.greeting_label.text = _greeting(
            getattr(self.app, "current_display_name", None)
        )
        self.time_label.text = now.strftime("%I:%M").lstrip("0") or "12:00"
        self.ampm_label.text = now.strftime("%p")
        self.date_label.text = now.strftime("%A, %B ") + str(now.day)

    def _update_weather(self, snapshot) -> None:
        if snapshot is None:
            return
        try:
            self.temp_label.text = f"{float(snapshot.temp_c):.0f}°C"
            self.condition_label.text = snapshot.label or "--"
            self._set_weather_icon(snapshot.icon)
        except Exception:
            logger.debug("idle: bad weather snapshot %r", snapshot, exc_info=True)

    def _set_weather_icon(self, key: str) -> None:
        if self.weather_icon is None:
            return
        sun_path = _IDLE_DIR / "icon_sun.png"
        cloud_path = _FIGMA_DIR / "icon_weather.png"
        if key == "sun" and sun_path.is_file():
            self.weather_icon.source = str(sun_path)
        elif cloud_path.is_file():
            self.weather_icon.source = str(cloud_path)
        elif sun_path.is_file():
            self.weather_icon.source = str(sun_path)

    def _refresh_home_summary(self):
        async def _fetch():
            try:
                data = await self.backend.get_home_summary()
            except Exception as exc:
                logger.debug("idle: home summary fetch failed: %s", exc)
                return
            time_str, title, _ = _format_meeting(data.get("next_meeting"))
            if not title:
                try:
                    today = display_now().date()
                    monday = today - timedelta(days=today.weekday())
                    week = await self.backend.get_calendar_week(
                        monday.isoformat(),
                        (monday + timedelta(days=6)).isoformat(),
                    )
                    picked = _pick_next_today_meeting_from_week(week if isinstance(week, dict) else {})
                    if isinstance(picked, dict):
                        time_str, title, _ = _format_meeting(
                            {
                                "title": picked.get("title"),
                                "start": picked.get("start") or picked.get("start_time"),
                            }
                        )
                except Exception:
                    logger.debug("idle: calendar-week fallback failed", exc_info=True)
            today_n = int(data.get("pending_actions_today") or 0)

            def _apply(_dt):
                self.next_time_label.text = time_str
                self.next_title_label.text = f"Now: {title}" if title else ""
                self.more_label.text = f"+{max(0, today_n)} more" if today_n else ""

            Clock.schedule_once(_apply, 0)

        run_async(_fetch())

    # ------------------------------------------------------------------
    # Start Recording CTA
    # ------------------------------------------------------------------

    def _on_start_recording(self, _inst):
        try:
            self.app.start_recording()
        except Exception:
            logger.exception("idle: start_recording failed")
