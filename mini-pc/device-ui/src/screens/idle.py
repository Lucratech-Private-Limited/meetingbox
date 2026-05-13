"""Idle screen — pixel-perfect Figma 338:60 (yJqcY4KovVjJ11vjysW533).

Figma frame: 1260 × 800 px (landscape).  All coordinates, sizes, font sizes,
and colours come directly from Figma node data.  Live clock, weather, and
next-meeting data continue to update at runtime.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

# ---------------------------------------------------------------------------
# Gradient fill helper (see home.py for docs; duplicated to keep files independent)
# ---------------------------------------------------------------------------

_GRAD_CACHE: dict = {}


def _grad(top: tuple, bot: tuple):
    from kivy.graphics.texture import Texture
    key = (top, bot)
    if key not in _GRAD_CACHE:
        tex = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c): return [min(255, max(0, int(x * 255))) for x in c]
        tex.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        tex.wrap = "clamp_to_edge"
        _GRAD_CACHE[key] = tex
    return _GRAD_CACHE[key]


_REC_TOP = (0.0, 0.21961, 0.71373, 1.0)   # #0038B6
_REC_BOT = (0.0, 0.13725, 0.46275, 1.0)   # #002376

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
# Figma design constants (frame 338:60, 1260 × 800 px)
# ---------------------------------------------------------------------------
_FW = 1260.0
_FH = 800.0

_IDLE_DIR  = ASSETS_DIR / "idle"
_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Colours from Figma
_WHITE = (1.0, 1.0, 1.0, 1.0)
_MUTED = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2
_BLUE  = (0.0, 0.420, 0.976, 1.0)     # #006BF9

# Font families registered in main.py via _register_asta_fonts()
_FONT    = "42dot-Sans"   # Regular + Bold
_FONT_SB = "42dot-SB"    # SemiBold
_FONT_MD = "42dot-Med"   # Medium


# ---------------------------------------------------------------------------
# Coordinate helpers  (Figma absolute px → Kivy FloatLayout fractions)
# ---------------------------------------------------------------------------

def _x(px: float) -> float:
    """Figma X (left edge) → pos_hint['x']."""
    return px / _FW


def _y(figma_top: float, h: float) -> float:
    """Figma Y-from-top + element height → pos_hint['y'] (Kivy bottom edge)."""
    return max(0.0, (_FH - figma_top - h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(fs: float) -> int:
    """Scale a Figma font-size (px) to the physical display."""
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * scale))


# ---------------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------------

def _asset(name: str, subdir: str = "idle") -> str:
    p = (_IDLE_DIR if subdir == "idle" else _FIGMA_DIR) / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Reusable helpers
# ---------------------------------------------------------------------------

def _lbl(text, font, size, color, *, bold=False, halign="left", valign="top",
         **kw) -> Label:
    lbl = Label(text=text, font_name=font, font_size=size, bold=bold,
                color=color, halign=halign, valign=valign, **kw)
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


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


def _format_meeting(m: dict | None) -> tuple[str, str, int]:
    if not m:
        return ("--:-- --", "No meetings today", 0)
    title = (m.get("title") or "Calendar event").strip() or "Calendar event"
    start = (m.get("start") or "").strip()
    if not start:
        return ("Time not set", title, 0)
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            ts = to_display_local(dt).strftime("%I:%M %p").lstrip("0")
        else:
            d = datetime.strptime(start[:10], "%Y-%m-%d")
            ts = d.strftime("%b %d · all day")
    except (TypeError, ValueError):
        ts = start
    return (ts, title, 0)


# ---------------------------------------------------------------------------
# Start-recording card  (Group 12 in Figma, at (603.16, 502.87), 584.8×235.9)
# ---------------------------------------------------------------------------

class _RecordingCard(ButtonBehavior, FloatLayout):
    """Tappable gradient button for starting a recording."""

    # Card dimensions from Figma (used for relative child positioning)
    _CW = 584.8
    _CH = 235.9

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        CW, CH = self._CW, self._CH
        r = _ff(42.38)

        # Background: use exact Figma PNG if available (gradient fill + glow stroke)
        bg_src = _asset("recording_btn_bg.png")
        if bg_src:
            self.add_widget(Image(
                source=bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))
        else:
            # Gradient fallback via texture
            with self.canvas.before:
                Color(1, 1, 1, 1)
                self._bg = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[r],
                    texture=_grad(_REC_TOP, _REC_BOT),
                )
                Color(0.012, 0.306, 0.886, 1.0)
                self._line = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, r),
                    width=1.5,
                )
            def _sync(*_):
                self._bg.pos  = self.pos
                self._bg.size = self.size
                self._line.rounded_rectangle = (
                    self.x, self.y, self.width, self.height, r
                )
            self.bind(pos=_sync, size=_sync)

        # Mic orb — at (38.14, 45.2) in card, 142.67×142.67
        orb_src = _asset("mic_orb.png")
        if orb_src:
            self.add_widget(Image(
                source=orb_src,
                size_hint=(142.67 / CW, 142.67 / CH),
                pos_hint={"x": 38.14 / CW, "y": (CH - 45.2 - 142.67) / CH},
                fit_mode="contain",
                allow_stretch=True,
            ))

        # "Start Recording" — at (233.07, 69.22), 309×51, Bold 42.38px
        self.add_widget(_lbl(
            "Start Recording", _FONT, _ff(42.38), _WHITE, bold=True,
            size_hint=(309 / CW, 51 / CH),
            pos_hint={"x": 233.07 / CW, "y": (CH - 69.22 - 51) / CH},
        ))

        # Subtitle — at (209.06, 132.78), 358×34, SemiBold 28.25px
        self.add_widget(_lbl(
            'Tap or say "start recording"', _FONT_SB, _ff(28.25), _WHITE,
            size_hint=(358 / CW, 34 / CH),
            pos_hint={"x": 209.06 / CW, "y": (CH - 132.78 - 34) / CH},
        ))


# ---------------------------------------------------------------------------
# Idle Screen
# ---------------------------------------------------------------------------

class IdleScreen(BaseScreen):
    """Full-screen idle / lock UI matching Figma 338:60 (1260×800)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_event        = None
        self._home_summary_event = None
        self._weather            = get_weather_client()
        self.greeting_label      = None
        self._clock_combined     = None
        self.time_label          = None   # alias → _clock_combined after _build_ui
        self.ampm_label          = None   # no longer a separate widget
        self.date_label          = None
        self.temp_label         = None
        self.condition_label    = None
        self.weather_icon       = None
        self.next_time_label    = None
        self.next_title_label   = None
        self.more_label         = None
        self._cta_card          = None
        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # 1. Solid background colour  #010C25
        with root.canvas.before:
            Color(0.004, 0.047, 0.145, 1.0)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._sync_bg, size=self._sync_bg)

        # 2. Background photo — Figma: (-22.67, -7.82) in frame, 1305.34×816.46
        bg_src = _asset("bg.png") or _asset("background_landscape.png")
        if bg_src:
            root.add_widget(Image(
                source=bg_src,
                size_hint=(_sw(1305.34), _sh(816.46)),
                pos_hint={"x": _x(-22.67), "y": _y(-7.82, 816.46)},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # 3. Greeting "Good morning, J.K"  — (64.98, 49.44)  241×34  SemiBold 28.25px
        # Height set to 38px (Figma=34) so the font line-box is never clipped.
        self.greeting_label = _lbl(
            _greeting(None), _FONT_SB, _ff(28.25), _WHITE,
            size_hint=(_sw(500), _sh(38)),
            pos_hint={"x": _x(64.98), "y": _y(49.44, 38)},
        )
        root.add_widget(self.greeting_label)

        # 4 + 5. Big clock + AM/PM in one markup label so they stay flush.
        # Figma: clock (64.98, 83.34) 334×169 Bold 141px | AM follows inline.
        # valign="top" → text starts exactly at Figma y=83.34, adjacent to greeting.
        self._clock_combined = Label(
            text=(
                f"[b][size={_ff(141.26)}]--:--[/size][/b]"
                f"[size={_ff(49.44)}][color=B6BAF2] --[/color][/size]"
            ),
            markup=True,
            font_name=_FONT,
            color=_WHITE,
            halign="left",
            valign="top",
            size_hint=(_sw(700), _sh(169)),
            pos_hint={"x": _x(64.98), "y": _y(83.34, 169)},
        )
        self._clock_combined.bind(size=self._clock_combined.setter("text_size"))
        root.add_widget(self._clock_combined)
        # Keep legacy refs alive in case any caller checks them
        self.time_label = self._clock_combined
        self.ampm_label = None

        # 6. Date "Tuesday, May 21"  — (64.98, 251.44)  325×51  SemiBold 42.38px
        self.date_label = _lbl(
            "", _FONT_SB, _ff(42.38), _WHITE,
            size_hint=(_sw(500), _sh(51)),
            pos_hint={"x": _x(64.98), "y": _y(251.44, 51)},
        )
        root.add_widget(self.date_label)

        # 7. Sun / weather icon  — (956.3, 105.94)  90.4×90.4
        sun_src = _asset("icon_sun.png")
        self.weather_icon = Image(
            source=sun_src or "",
            size_hint=(_sw(90.4), _sh(90.4)),
            pos_hint={"x": _x(956.3), "y": _y(105.94, 90.4)},
            fit_mode="contain",
            allow_stretch=True,
        ) if sun_src else None
        if self.weather_icon:
            root.add_widget(self.weather_icon)

        # 8. Temperature "28°C"  — (1072.13, 120.07)  115×58.32  Bold 49.44px
        self.temp_label = _lbl(
            "--°C", _FONT, _ff(49.44), _WHITE, bold=True,
            size_hint=(_sw(200), _sh(58.32)),
            pos_hint={"x": _x(1072.13), "y": _y(120.07, 58.32)},
        )
        root.add_widget(self.temp_label)

        # 9. Condition "Sunny"  — (1072.13, 187.09)  120×50.41  Medium 42.38px
        self.condition_label = _lbl(
            "--", _FONT_MD, _ff(42.38), _MUTED,
            size_hint=(_sw(240), _sh(50.41)),
            pos_hint={"x": _x(1072.13), "y": _y(187.09, 50.41)},
        )
        root.add_widget(self.condition_label)

        # ==================================================================
        # Schedule / Next-up section  (Group 13 origin: 64.98, 470.38)
        # ==================================================================
        GX, GY = 64.98, 470.38  # group origin in frame

        # "Next up"  — (0, 0) in group → abs (64.98, 470.38)  141×47  SemiBold 39.55px
        root.add_widget(_lbl(
            "Next up", _FONT_SB, _ff(39.55), _BLUE,
            size_hint=(_sw(200), _sh(47)),
            pos_hint={"x": _x(GX), "y": _y(GY, 47)},
        ))

        # Calendar icon — (0, 78.31) in group → abs (64.98, 548.69)  48.03×47.47
        cal_src = _asset("icon_calendar.png")
        if cal_src:
            root.add_widget(Image(
                source=cal_src,
                size_hint=(_sw(48.03), _sh(47.47)),
                pos_hint={"x": _x(GX), "y": _y(GY + 78.31, 47.47)},
                fit_mode="contain",
                allow_stretch=True,
            ))

        # "11:00 AM" — (74.87, 87.58) in group → abs (139.85, 557.96)  169×47
        self.next_time_label = _lbl(
            "--:-- --", _FONT_SB, _ff(39.55), _BLUE,
            size_hint=(_sw(280), _sh(47)),
            pos_hint={"x": _x(GX + 74.87), "y": _y(GY + 87.58, 47)},
        )
        root.add_widget(self.next_time_label)

        # "Now : Product Sync" — (0, 159.62) in group → abs (64.98, 630)  398×52  Bold 43.79px
        self.next_title_label = _lbl(
            "--", _FONT, _ff(43.79), _WHITE, bold=True,
            size_hint=(_sw(600), _sh(52)),
            pos_hint={"x": _x(GX), "y": _y(GY + 159.62, 52)},
        )
        root.add_widget(self.next_title_label)

        # "+2 more" — (0, 231.66) in group → abs (64.98, 702.04)  151×47  Bold 39.55px
        self.more_label = _lbl(
            "", _FONT, _ff(39.55), _BLUE, bold=True,
            size_hint=(_sw(250), _sh(47)),
            pos_hint={"x": _x(GX), "y": _y(GY + 231.66, 47)},
        )
        root.add_widget(self.more_label)

        # ==================================================================
        # Start Recording card  — (603.16, 502.87)  584.8 × 235.9
        # ==================================================================
        card = _RecordingCard(
            size_hint=(_sw(584.8), _sh(235.9)),
            pos_hint={"x": _x(603.16), "y": _y(502.87, 235.9)},
        )
        card.bind(on_release=self._on_start_recording)
        self._cta_card = card
        root.add_widget(card)

        # Status-bar footer
        root.add_widget(self.build_footer())

        self.add_widget(root)

    def _sync_bg(self, widget, *_):
        self._bg_rect.pos  = widget.pos
        self._bg_rect.size = widget.size

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

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
            lambda _dt: self._refresh_home_summary(), 60.0
        )

    def on_leave(self):
        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None
        if self._home_summary_event:
            self._home_summary_event.cancel()
            self._home_summary_event = None
        self._weather.unsubscribe(self._update_weather)

    # -----------------------------------------------------------------------
    # Touch
    # -----------------------------------------------------------------------

    def on_touch_up(self, touch):
        if super().on_touch_up(touch):
            return True
        lx, ly = self.to_widget(touch.x, touch.y)
        if not self.collide_point(lx, ly):
            return False
        cc = self._cta_card
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

    # -----------------------------------------------------------------------
    # Live data
    # -----------------------------------------------------------------------

    def _update_clock(self):
        now = display_now()
        self.greeting_label.text = _greeting(
            getattr(self.app, "current_display_name", None)
        )
        hm = now.strftime("%I:%M").lstrip("0") or "12:00"
        ap = now.strftime("%p")
        # Combined markup keeps time and AM/PM flush regardless of digit count
        self._clock_combined.text = (
            f"[b][size={_ff(141.26)}]{hm}[/size][/b]"
            f"[size={_ff(49.44)}][color=B6BAF2] {ap}[/color][/size]"
        )
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
        sun_path   = _IDLE_DIR  / "icon_sun.png"
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
            today_n = int(data.get("pending_actions_today") or 0)

            def _apply(_dt):
                self.next_time_label.text  = time_str
                self.next_title_label.text = title or "--"
                self.more_label.text = (
                    f"+{max(0, today_n)} more" if today_n else ""
                )
            Clock.schedule_once(_apply, 0)

        run_async(_fetch())

    # -----------------------------------------------------------------------
    # Start recording CTA
    # -----------------------------------------------------------------------

    def _on_start_recording(self, _inst):
        try:
            self.app.start_recording()
        except Exception:
            logger.exception("idle: start_recording failed")
