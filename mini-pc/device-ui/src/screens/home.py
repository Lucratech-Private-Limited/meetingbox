"""Home screen — pixel-perfect Figma 390:187 (yJqcY4KovVjJ11vjysW533).

All element positions, sizes, font sizes, and colours come directly from the
Figma design data.  The Figma frame is 892 × 573 px; every coordinate is
converted to FloatLayout pos_hint / size_hint fractions at runtime so the
layout scales proportionally to any DISPLAY_WIDTH × DISPLAY_HEIGHT.

Live data (clock, weather, next meeting, last summary, voice state) continues
to update at runtime exactly as before.
"""

from __future__ import annotations

from datetime import datetime

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from async_helper import run_async
from components.modal_dialog import ModalDialog
from components.text_input_dialog import TextInputDialog
from config import (
    ASSETS_DIR,
    COLORS,
    DASHBOARD_URL,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    display_now,
    to_display_local,
)
from local_network import get_primary_ipv4
from network_util import linux_ethernet_ready
from screens.base_screen import BaseScreen
from weather_client import get_weather_client

# ---------------------------------------------------------------------------
# Figma design constants (frame 390:187, 892 × 573 px)
# ---------------------------------------------------------------------------
_FW = 892.0
_FH = 573.0

_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Figma colours
_WHITE  = (1.0, 1.0, 1.0, 1.0)
_MUTED  = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2
_BLUE   = (0.0, 0.420, 0.976, 1.0)     # #006BF9 / #006FFF
_BLUE2  = (0.204, 0.506, 0.945, 1.0)   # #3481F1  (schedule section)
_GREY   = (0.643, 0.643, 0.675, 1.0)   # #A4A4AC  (section headers)

# Card background and border colours (approximated from Figma gradients)
_CARD_BG     = (0.004, 0.067, 0.216, 1.0)   # top of card gradient #011137
_CARD_BORDER = (0.247, 0.259, 0.325, 1.0)   # #3F4253  (gradient start)
_HERO_BG     = (0.004, 0.047, 0.145, 1.0)   # #010C25  (hero card solid)
_PILL_BG     = (0.0,   0.059, 0.196, 1.0)   # #000F33  (listening pill)

# Font family names registered in main.py → _register_asta_fonts()
_FONT     = "42dot-Sans"   # Regular / Bold (bold=True)
_FONT_SB  = "42dot-SB"    # SemiBold
_FONT_MED = "42dot-Med"   # Medium


# ---------------------------------------------------------------------------
# Coordinate helpers  (Figma → Kivy FloatLayout fractions)
# ---------------------------------------------------------------------------

def _x(px: float) -> float:
    return px / _FW


def _y(figma_top: float, figma_h: float) -> float:
    return max(0.0, (_FH - figma_top - figma_h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(fs: float) -> int:
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * scale))


# ---------------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------------

def _fp(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Label factory
# ---------------------------------------------------------------------------

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
# Shared formatting helpers
# ---------------------------------------------------------------------------

def _greeting_name(name: str) -> str:
    hour = display_now().hour
    greet = (
        "Good morning" if hour < 12
        else "Good afternoon" if hour < 17
        else "Good evening"
    )
    return f"{greet}, {name or 'Stark'}"


def _format_next_meeting(nm) -> tuple[str, str]:
    if not nm:
        return "No focus loaded", "Ask Tony for a briefing"
    title = (nm.get("title") or "Calendar event").strip() or "Calendar event"
    start = (nm.get("start") or "").strip()
    if not start:
        return title, "Time not set"
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            line = to_display_local(dt).strftime("%I:%M %p").lstrip("0")
        else:
            d = datetime.strptime(start[:10], "%Y-%m-%d")
            line = d.strftime("%b %d · all day")
        return title, line
    except Exception:
        return title, start


# ---------------------------------------------------------------------------
# Card background helper
# ---------------------------------------------------------------------------

class _Card(FloatLayout):
    """FloatLayout with a rounded dark card background and border.

    bg_color    – Kivy RGBA tuple for the fill
    border_color – Kivy RGBA tuple for the 1-px stroke
    radius      – corner radius in display pixels
    """

    def __init__(self, bg_color=_CARD_BG, border_color=_CARD_BORDER,
                 radius=12, **kwargs):
        super().__init__(**kwargs)
        self._cr = radius
        with self.canvas.before:
            Color(*bg_color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                        radius=[radius])
            Color(*border_color)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=1,
            )
        self.bind(pos=self._sync_card, size=self._sync_card)

    def _sync_card(self, *_):
        r = self._cr
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [r]
        self._border.rounded_rectangle = (
            self.x, self.y, self.width, self.height, r
        )


# ---------------------------------------------------------------------------
# Listening pill (tappable card in header)
# ---------------------------------------------------------------------------

class _ListeningPill(_Card):
    """Voice state pill — (570, 15)  214 × 54.

    Figma: Frame 27 (414:1259), dark gradient #000F33 → #000A26, r=54.
    Inside: blue dot, 'Listening' label, bi:soundwave icon.
    """

    # Pill dimensions for relative child positioning
    _PW = 214.0
    _PH = 54.0

    def __init__(self, **kwargs):
        kwargs.setdefault("radius", _ff(27))
        super().__init__(bg_color=_PILL_BG, border_color=(0.129, 0.157, 0.294, 1.0),
                         **kwargs)

        # Blue status dot  — (26, 20) in pill, 14 × 14  (#467DFE)
        pw, ph = self._PW, self._PH
        dot_src = _fp("icon_listening_dot.png")
        if dot_src:
            self.voice_dot = Image(
                source=dot_src,
                size_hint=(14.0 / pw, 14.0 / ph),
                pos_hint={"x": 26.0 / pw, "y": (ph - 20.0 - 14.0) / ph},
                fit_mode="contain",
                allow_stretch=True,
            )
        else:
            self.voice_dot = _lbl(
                "●", _FONT_SB, _ff(12), COLORS["blue"],
                halign="center", valign="middle",
                size_hint=(14.0 / pw, 14.0 / ph),
                pos_hint={"x": 26.0 / pw, "y": (ph - 20.0 - 14.0) / ph},
            )
        self.add_widget(self.voice_dot)

        # "Listening"  — (57, 15), 84 × 24, SemiBold 20px
        self.voice_state_label = _lbl(
            "Listening", _FONT_SB, _ff(20), _WHITE,
            halign="left", valign="middle",
            size_hint=(84.0 / pw, 24.0 / ph),
            pos_hint={"x": 57.0 / pw, "y": (ph - 15.0 - 24.0) / ph},
        )
        self.add_widget(self.voice_state_label)

        # bi:soundwave icon  — (159, 11), 32 × 32
        sw_src = _fp("icon_soundwave.png")
        if sw_src:
            self.add_widget(
                Image(
                    source=sw_src,
                    size_hint=(32.0 / pw, 32.0 / ph),
                    pos_hint={"x": 159.0 / pw, "y": (ph - 11.0 - 32.0) / ph},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )


# ---------------------------------------------------------------------------
# Start Recording mini card (inside the hero card)
# ---------------------------------------------------------------------------

class _MiniRecordingBtn(ButtonBehavior, FloatLayout):
    """Blue gradient 'Start Recording' button inside the hero card.

    Figma: Group 16 (395:195), 190 × 76.64, r=13.77.
    Uses downloaded recording_btn_bg.png as background.
    """

    _BW = 190.0
    _BH = 76.64

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        bg_src = _fp("recording_btn_bg.png")
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
            with self.canvas.before:
                Color(0.0, 0.18, 0.60, 1.0)
                self._fbg = RoundedRectangle(pos=self.pos, size=self.size,
                                             radius=[_ff(14)])
            self.bind(
                pos=lambda *_: setattr(self._fbg, "pos", self.pos),
                size=lambda *_: setattr(self._fbg, "size", self.size),
            )

        bw, bh = self._BW, self._BH

        # Mic orb mini  — (12.39, 14.69) in btn, 46.35 × 46.35
        orb_src = _fp("mic_orb_mini.png") or _fp("mic_orb.png")
        if orb_src:
            self.add_widget(
                Image(
                    source=orb_src,
                    size_hint=(46.35 / bw, 46.35 / bh),
                    pos_hint={"x": 12.39 / bw, "y": (bh - 14.69 - 46.35) / bh},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # "Start Recording"  — (75.72, 22.49), 101 × 16, Bold 13.77px
        self.add_widget(
            _lbl(
                "Start Recording", _FONT, _ff(13.77), _WHITE, bold=True,
                valign="middle",
                size_hint=(101.0 / bw, 16.0 / bh),
                pos_hint={"x": 75.72 / bw, "y": (bh - 22.49 - 16.0) / bh},
            )
        )

        # Subtitle  — (67.92, 43.14), 117 × 11, SemiBold 9.18px
        self.add_widget(
            _lbl(
                'Tap or say "start recording"', _FONT_SB, _ff(9.18), _WHITE,
                size_hint=(117.0 / bw, 11.0 / bh),
                pos_hint={"x": 67.92 / bw, "y": (bh - 43.14 - 11.0) / bh},
            )
        )


# ---------------------------------------------------------------------------
# Circular avatar / icon badge
# ---------------------------------------------------------------------------

class _CircleBadge(FloatLayout):
    """Dark circular badge with gradient-border (like avatars and icon frames).

    Figma fill: #010B26 background + gradient stroke #3F4253 → #161B35.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0.004, 0.043, 0.149, 1.0)   # #010B26
            self._circ = Ellipse(pos=self.pos, size=self.size)
            Color(0.247, 0.259, 0.325, 1.0)   # #3F4253
            self._ring = Line(circle=(self.center_x, self.center_y, 1), width=1)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._circ.pos = self.pos
        self._circ.size = self.size
        r = min(self.width, self.height) / 2
        self._ring.circle = (self.center_x, self.center_y, r)


# ===========================================================================
# Home Screen
# ===========================================================================

class HomeScreen(BaseScreen):
    """Main MeetingBox home dashboard — pixel-perfect Figma 390:187."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_event = None
        self._footer_ip_event = None
        self._voice_state_event = None
        self._footer_kwargs = {}
        self._latest_meeting_id = None
        self._health_label_offline = False
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = FloatLayout(size_hint=(1, 1))

        # Background  #01081A
        with root.canvas.before:
            Color(0.004, 0.031, 0.102, 1.0)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._sync_bg, size=self._sync_bg)

        # ==============================================================
        # HEADER ROW  (y = 0 … ~81)
        # ==============================================================

        # Avatar circle  — (17, 15), 54 × 54
        avatar_badge = _CircleBadge(
            size_hint=(_sw(54), _sh(54)),
            pos_hint={"x": _x(17), "y": _y(15, 54)},
        )
        # Try to load avatar photo if available
        av_src = _fp("avatar_photo_1.png")
        if av_src:
            avatar_badge.add_widget(
                Image(
                    source=av_src,
                    size_hint=(1, 1),
                    pos_hint={"x": 0, "y": 0},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )
        root.add_widget(avatar_badge)

        # "Good morning, J.K"  — (87, 24), 256 × 36, SemiBold 30px
        self.greeting_label = _lbl(
            "Good morning, Stark", _FONT_SB, _ff(30), _WHITE,
            halign="left", valign="middle",
            size_hint=(_sw(256), _sh(36)),
            pos_hint={"x": _x(87), "y": _y(24, 36)},
        )
        root.add_widget(self.greeting_label)

        # Listening pill  — (570, 15), 214 × 54
        pill = _ListeningPill(
            size_hint=(_sw(214), _sh(54)),
            pos_hint={"x": _x(570), "y": _y(15, 54)},
        )
        self.voice_dot = pill.voice_dot
        self.voice_state_label = pill.voice_state_label
        self.listening_pill = pill
        pill.bind(
            on_touch_up=lambda inst, touch: (
                self._toggle_voice_listening()
                if inst.collide_point(*touch.pos)
                else None
            )
        )
        root.add_widget(pill)

        # Settings / action icon  — (821, 15), 54 × 54
        sg_src = _fp("icon_settings.png")
        if sg_src:
            settings_btn = Button(
                background_normal=sg_src,
                background_down=sg_src,
                border=[0, 0, 0, 0],
                size_hint=(_sw(54), _sh(54)),
                pos_hint={"x": _x(821), "y": _y(15, 54)},
            )
        else:
            settings_btn = _CircleBadge(
                size_hint=(_sw(54), _sh(54)),
                pos_hint={"x": _x(821), "y": _y(15, 54)},
            )
        settings_btn.bind(
            on_release=lambda *_: self.goto("settings", transition="slide_left")
        )
        root.add_widget(settings_btn)

        # ==============================================================
        # LEFT HERO CARD  — (17, 81), 410 × 263.37
        # ==============================================================
        root.add_widget(self._build_hero_card())

        # ==============================================================
        # MIDDLE CARD — Last Meeting Summary  — (433, 81), 218 × 263
        # ==============================================================
        root.add_widget(self._build_summary_card())

        # ==============================================================
        # RIGHT CARD — Morning Brief  — (657, 81), 218 × 263
        # ==============================================================
        root.add_widget(self._build_brief_card())

        # ==============================================================
        # BOTTOM ROW  (y = 359)
        # ==============================================================
        root.add_widget(self._build_schedule_card())
        root.add_widget(self._build_email_card())
        root.add_widget(self._build_tasks_card())

        # ==============================================================
        # TRY-SAYING BAR  — (27, 476), 838 × 71
        # ==============================================================
        root.add_widget(self._build_say_bar())

        # ==============================================================
        # FOOTER (slim status bar below the bar)
        # ==============================================================
        footer = self.build_footer()
        footer.size_hint = (1, None)
        footer.height = _ff(16)
        footer.pos_hint = {"x": 0, "y": 0}
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    # Hero card  (left panel)
    # ------------------------------------------------------------------

    def _build_hero_card(self) -> _Card:
        """Left panel: (17, 81), 410 × 263.37, bg #010C25."""
        CW, CH = 410.0, 263.37

        card = _Card(
            bg_color=_HERO_BG,
            border_color=_CARD_BORDER,
            radius=_ff(14),
            size_hint=(_sw(CW), _sh(CH)),
            pos_hint={"x": _x(17), "y": _y(81, CH)},
        )

        # ---- Background image (full bleed inside card) ----
        hero_src = _fp("hero_background.png")
        if not hero_src:
            hero_src = str(ASSETS_DIR / "home" / "figma_home_hero.png") if (
                ASSETS_DIR / "home" / "figma_home_hero.png"
            ).is_file() else ""
        if hero_src:
            card.add_widget(
                Image(
                    source=hero_src,
                    size_hint=(1.04, 1.01),
                    pos_hint={"x": -0.02, "y": 0},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )

        # ---- Clock group at (21.14, 24.12) in card, 147.27 × 70.7 ----
        # "11:01" — (0, 0) in group → abs in card (21.14, 24.12), 109 × 55
        self._big_clock_hm = _lbl(
            "--:--", _FONT, _ff(45.96), _WHITE, bold=True,
            halign="left", valign="top",
            size_hint=(109.0 / CW, 55.0 / CH),
            pos_hint={"x": 21.14 / CW, "y": (CH - 24.12 - 55.0) / CH},
        )
        card.add_widget(self._big_clock_hm)

        # "AM" — (122.27, 28.5) in group → abs (143.41, 52.62), 25 × 19
        self._clock_ampm = _lbl(
            "AM", _FONT_SB, _ff(16.09), _MUTED,
            halign="left", valign="top",
            size_hint=(25.0 / CW, 19.0 / CH),
            pos_hint={"x": (21.14 + 122.27) / CW, "y": (CH - (24.12 + 28.5) - 19.0) / CH},
        )
        card.add_widget(self._clock_ampm)

        # "Tuesday, May 21" — (0, 54.7) in group → abs (21.14, 78.82), 106 × 16
        self.date_label = _lbl(
            "", _FONT_SB, _ff(13.79), _WHITE,
            halign="left", valign="top",
            size_hint=(106.0 / CW, 16.0 / CH),
            pos_hint={"x": 21.14 / CW, "y": (CH - (24.12 + 54.7) - 16.0) / CH},
        )
        card.add_widget(self.date_label)

        # ---- Weather group at (311.18, 31.47) in card, 77.69 × 43.12 ----
        # Sun icon — (0, 0) in group → abs (311.18, 31.47), 29.42 × 29.42
        sun_src = _fp("icon_sun.png")
        if sun_src:
            card.add_widget(
                Image(
                    source=sun_src,
                    size_hint=(29.42 / CW, 29.42 / CH),
                    pos_hint={
                        "x": 311.18 / CW,
                        "y": (CH - 31.47 - 29.42) / CH,
                    },
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # "28°C" — (37.69, 5.06) in group → abs (348.87, 36.53), 38 × 19
        self.health_label = _lbl(
            "--°C", _FONT, _ff(16.09), _WHITE, bold=True,
            halign="left", valign="top",
            size_hint=(38.0 / CW, 19.0 / CH),
            pos_hint={
                "x": (311.18 + 37.69) / CW,
                "y": (CH - (31.47 + 5.06) - 19.0) / CH,
            },
        )
        card.add_widget(self.health_label)

        # "Sunny" — (37.69, 27.12) in group → abs (348.87, 58.59), 40 × 16
        self._wx_condition = _lbl(
            "--", _FONT_MED, _ff(13.79), _MUTED,
            halign="left", valign="top",
            size_hint=(40.0 / CW, 16.0 / CH),
            pos_hint={
                "x": (311.18 + 37.69) / CW,
                "y": (CH - (31.47 + 27.12) - 16.0) / CH,
            },
        )
        card.add_widget(self._wx_condition)

        # Tap weather block → location dialog
        self.health_label.bind(
            on_touch_up=lambda inst, touch: (
                self._show_weather_location_dialog()
                if inst.collide_point(*touch.pos)
                else None
            )
        )

        # ---- Next up section at (21.14, 153.06) in card, 130 × 90.38 ----
        # Calendar icon — (0, 27.58) in section → abs (21.14, 180.64), 15.63 × 15.63
        cal_src = _fp("icon_calendar.png")
        if cal_src:
            card.add_widget(
                Image(
                    source=cal_src,
                    size_hint=(15.63 / CW, 15.63 / CH),
                    pos_hint={
                        "x": 21.14 / CW,
                        "y": (CH - (153.06 + 27.58) - 15.63) / CH,
                    },
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # "Next up" — (0, 0) in section → abs (21.14, 153.06), 46 × 15, SemiBold 12.87
        card.add_widget(
            _lbl(
                "Next up", _FONT_SB, _ff(12.87), _BLUE2,
                size_hint=(46.0 / CW, 15.0 / CH),
                pos_hint={"x": 21.14 / CW, "y": (CH - 153.06 - 15.0) / CH},
            )
        )

        # "11:00 AM" — (24.36, 28.5) → abs (45.5, 181.56), 55 × 15
        self.next_time_label = _lbl(
            "—", _FONT_SB, _ff(12.87), _BLUE2,
            size_hint=(55.0 / CW, 15.0 / CH),
            pos_hint={
                "x": (21.14 + 24.36) / CW,
                "y": (CH - (153.06 + 28.5) - 15.0) / CH,
            },
        )
        card.add_widget(self.next_time_label)

        # "Now : Product Sync" — (0, 51.94) → abs (21.14, 205.0), 130 × 17
        self.next_title_label = _lbl(
            "Now: —", _FONT, _ff(14.25), _WHITE, bold=True,
            size_hint=(130.0 / CW, 17.0 / CH),
            pos_hint={"x": 21.14 / CW, "y": (CH - (153.06 + 51.94) - 17.0) / CH},
        )
        card.add_widget(self.next_title_label)

        # "+2 more" — (0, 75.38) → abs (21.14, 228.44), 49 × 15
        self.more_label = _lbl(
            "+0 more", _FONT, _ff(12.87), _BLUE2, bold=True,
            size_hint=(49.0 / CW, 15.0 / CH),
            pos_hint={"x": 21.14 / CW, "y": (CH - (153.06 + 75.38) - 15.0) / CH},
        )
        # Tap "+N more" → open meeting list
        self.more_label.bind(
            on_touch_up=lambda inst, touch: (
                self.goto("meetings", transition="slide_left")
                if inst.collide_point(*touch.pos)
                else None
            )
        )
        card.add_widget(self.more_label)

        # ---- Start Recording mini button at (196.27, 166.85), 190 × 76.64 ----
        self.start_btn = _MiniRecordingBtn(
            size_hint=(190.0 / CW, 76.64 / CH),
            pos_hint={"x": 196.27 / CW, "y": (CH - 166.85 - 76.64) / CH},
        )
        self.start_btn.bind(on_release=self._on_start_recording)
        card.add_widget(self.start_btn)

        return card

    # ------------------------------------------------------------------
    # Summary card (middle)
    # ------------------------------------------------------------------

    def _build_summary_card(self) -> _Card:
        """Last Meeting Summary — (433, 81), 218 × 263."""
        CW, CH = 218.0, 263.0

        card = _Card(
            radius=_ff(12),
            size_hint=(_sw(CW), _sh(CH)),
            pos_hint={"x": _x(433), "y": _y(81, CH)},
        )
        card.bind(
            on_touch_up=lambda inst, touch: (
                self._open_latest_meeting()
                if inst.collide_point(*touch.pos)
                else None
            )
        )

        # ---- "Last Meeting Summary" header with file icon ----
        # Group 20 at (15, 36), 187 × 28
        # File icon — (0, 0) in group → abs (15, 36), 28 × 28
        fd_src = _fp("icon_file_document.png")
        if fd_src:
            card.add_widget(
                Image(
                    source=fd_src,
                    size_hint=(28.0 / CW, 28.0 / CH),
                    pos_hint={"x": 15.0 / CW, "y": (CH - 36.0 - 28.0) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # "Last Meeting Summary" — (15+29, 36+5) = (44, 41), 158 × 18, SemiBold 15px
        self.last_actions_label = _lbl(
            "Last Meeting Summary", _FONT_SB, _ff(15), _GREY,
            size_hint=(158.0 / CW, 18.0 / CH),
            pos_hint={"x": 44.0 / CW, "y": (CH - 41.0 - 18.0) / CH},
        )
        card.add_widget(self.last_actions_label)

        # ---- Meeting title  — (20, 74), 141 × 27, SemiBold 23px ----
        self.last_title_label = _lbl(
            "Loading…", _FONT_SB, _ff(23), _WHITE,
            size_hint=(141.0 / CW, 27.0 / CH),
            pos_hint={"x": 20.0 / CW, "y": (CH - 74.0 - 27.0) / CH},
        )
        card.add_widget(self.last_title_label)

        # ---- Time/date metadata ----
        # "Today, 10:00 AM"  — (20, 109), 116 × 18, SemiBold 15px, #B6BAF2
        self.last_meta_label = _lbl(
            "—", _FONT_SB, _ff(15), _MUTED,
            size_hint=(116.0 / CW, 18.0 / CH),
            pos_hint={"x": 20.0 / CW, "y": (CH - 109.0 - 18.0) / CH},
        )
        card.add_widget(self.last_meta_label)

        # ---- Avatar group at (20, 194) ----
        av1_src = _fp("avatar_photo_1.png")
        av2_src = _fp("avatar_photo_2.png")
        if av1_src:
            card.add_widget(
                Image(
                    source=av1_src,
                    size_hint=(32.7 / CW, 32.59 / CH),
                    pos_hint={"x": 20.0 / CW, "y": (CH - 194.0 - 32.59) / CH},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )
        if av2_src:
            card.add_widget(
                Image(
                    source=av2_src,
                    size_hint=(32.7 / CW, 32.59 / CH),
                    pos_hint={"x": (20.0 + 40.0) / CW, "y": (CH - 194.0 - 32.59) / CH},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )

        # "+2" badge  — (100.71 + 20, 194), 32.29 × 32.29
        badge_x = 20.0 + 100.71
        badge = FloatLayout(
            size_hint=(32.29 / CW, 32.29 / CH),
            pos_hint={"x": badge_x / CW, "y": (CH - 194.0 - 32.29) / CH},
        )
        with badge.canvas.before:
            Color(0.004, 0.039, 0.106, 1.0)  # #010A1B
            _be = Ellipse(pos=badge.pos, size=badge.size)
        badge.bind(pos=lambda w, *_: setattr(_be, "pos", w.pos),
                   size=lambda w, *_: setattr(_be, "size", w.size))
        badge.add_widget(
            _lbl("+2", _FONT_MED, _ff(15), _WHITE,
                 halign="center", valign="middle",
                 size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        )
        card.add_widget(badge)

        # "2 action items"  — (20, 169), 97 × 18, SemiBold 15px, #006FFF
        card.add_widget(
            _lbl(
                "—", _FONT_SB, _ff(15), (0.0, 0.435, 1.0, 1.0),
                size_hint=(97.0 / CW, 18.0 / CH),
                pos_hint={"x": 20.0 / CW, "y": (CH - 169.0 - 18.0) / CH},
            )
        )

        # Store ref for data loading
        self._summary_card = card
        return card

    # ------------------------------------------------------------------
    # Brief card (right)
    # ------------------------------------------------------------------

    def _build_brief_card(self) -> _Card:
        """Morning Brief — (657, 81), 218 × 263."""
        CW, CH = 218.0, 263.0

        card = _Card(
            radius=_ff(12),
            size_hint=(_sw(CW), _sh(CH)),
            pos_hint={"x": _x(657), "y": _y(81, CH)},
        )

        # ---- "Morning Brief" header  ----
        # Sun icon  — (21, 10), 20 × 20
        sun_src = _fp("icon_sun_morning_brief.png") or _fp("icon_sun.png")
        if sun_src:
            card.add_widget(
                Image(
                    source=sun_src,
                    size_hint=(20.0 / CW, 20.0 / CH),
                    pos_hint={"x": 21.0 / CW, "y": (CH - 10.0 - 20.0) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        # "Morning Brief"  — (51, 10), 106 × 20, SemiBold 17px, #A4A4AC
        card.add_widget(
            _lbl(
                "Morning Brief", _FONT_SB, _ff(17), _GREY,
                size_hint=(106.0 / CW, 20.0 / CH),
                pos_hint={"x": 51.0 / CW, "y": (CH - 10.0 - 20.0) / CH},
            )
        )

        # ---- Calendar info row  — (7, 41), 204 × 52 ----
        # Calendar icon  — abs (7+14.4, 41+10.83) = (21.4, 51.83), 33.6 × 30.33
        cal_src = _fp("icon_calendar_brief.png") or _fp("icon_calendar.png")
        if cal_src:
            card.add_widget(
                Image(
                    source=cal_src,
                    size_hint=(33.6 / CW, 30.33 / CH),
                    pos_hint={"x": 21.4 / CW, "y": (CH - 51.83 - 30.33) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        # Title "3 meetings today"  — (7+57.6, 41+9.75) = (64.6, 50.75), 135 × 18
        self._brief_cal_title = _lbl(
            "3 meetings today", _FONT, _ff(15), _GREY, bold=True,
            size_hint=(135.0 / CW, 18.0 / CH),
            pos_hint={"x": 64.6 / CW, "y": (CH - 50.75 - 18.0) / CH},
        )
        card.add_widget(self._brief_cal_title)
        # Sub "First at 11:00 AM"  — (64.6, 71.25), 102 × 14
        self._brief_cal_sub = _lbl(
            "First at 11:00 AM", _FONT_SB, _ff(12), _MUTED,
            size_hint=(102.0 / CW, 14.0 / CH),
            pos_hint={"x": 64.6 / CW, "y": (CH - (41.0 + 29.25) - 14.0) / CH},
        )
        card.add_widget(self._brief_cal_sub)

        # ---- Weather info row  — (7, 97), 204 × 52 ----
        wx_src = _fp("icon_weather.png")
        if wx_src:
            card.add_widget(
                Image(
                    source=wx_src,
                    size_hint=(36.8 / CW, 36.8 / CH),
                    pos_hint={"x": (7.0 + 9.74) / CW, "y": (CH - (97.0 + 7.58) - 36.8) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        self._brief_wx_title = _lbl(
            "Weather: —", _FONT, _ff(15), _GREY, bold=True,
            size_hint=(111.0 / CW, 15.0 / CH),
            pos_hint={"x": (7.0 + 58.0) / CW, "y": (CH - (97.0 + 11.0) - 15.0) / CH},
        )
        card.add_widget(self._brief_wx_title)
        self._brief_wx_sub = _lbl(
            "Sunny", _FONT_SB, _ff(12), _MUTED,
            size_hint=(35.0 / CW, 13.0 / CH),
            pos_hint={"x": (7.0 + 57.6) / CW, "y": (CH - (97.0 + 28.17) - 13.0) / CH},
        )
        card.add_widget(self._brief_wx_sub)

        # ---- Email info row  — (7, 153), 204 × 89 ----
        email_src = _fp("icon_email.png")
        if email_src:
            card.add_widget(
                Image(
                    source=email_src,
                    size_hint=(32.47 / CW, 32.47 / CH),
                    pos_hint={"x": (7.0 + 9.74) / CW, "y": (CH - (153.0 + 25.98) - 32.47) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        self._brief_email_title = _lbl(
            "email:", _FONT, _ff(15), _WHITE, bold=True,
            size_hint=(47.0 / CW, 18.0 / CH),
            pos_hint={"x": (7.0 + 57.6) / CW, "y": (CH - (153.0 + 15.2) - 18.0) / CH},
        )
        card.add_widget(self._brief_email_title)
        self._brief_email_sub = _lbl(
            "Connect Gmail for updates", _FONT_SB, _ff(12), _MUTED,
            size_hint=(130.0 / CW, 13.0 / CH),
            pos_hint={"x": (7.0 + 58.0) / CW, "y": (CH - (153.0 + 35.0) - 13.0) / CH},
        )
        card.add_widget(self._brief_email_sub)

        # "View all"  — (81, 245), 40 × 13, SemiBold 11px, #006BF9
        view_btn = _lbl(
            "View all", _FONT_SB, _ff(11), (0.0, 0.420, 0.976, 1.0),
            size_hint=(40.0 / CW, 13.0 / CH),
            pos_hint={"x": 81.0 / CW, "y": (CH - 245.0 - 13.0) / CH},
        )
        view_btn.bind(
            on_touch_up=lambda inst, touch: (
                self.goto("briefing", transition="slide_left")
                if inst.collide_point(*touch.pos)
                else None
            )
        )
        card.add_widget(view_btn)

        # Expose brief_calendar_label / brief_email_label as duck-typed objects
        # for compatibility with _load_home_summary() callbacks.
        self.brief_calendar_label = _BriefRow(self._brief_cal_title, self._brief_cal_sub)
        self.brief_email_label = _BriefRow(self._brief_email_title, self._brief_email_sub)

        return card

    # ------------------------------------------------------------------
    # Bottom mini cards
    # ------------------------------------------------------------------

    def _build_mini_card(
        self,
        figma_x: float,
        figma_y: float,
        figma_w: float,
        figma_h: float,
        icon_file: str,
        value: str,
        label_text: str,
        radius: float,
        on_tap,
    ):
        """Generic bottom info card matching Figma layout."""
        CW, CH = figma_w, figma_h

        card = _Card(
            radius=_ff(radius),
            size_hint=(_sw(CW), _sh(CH)),
            pos_hint={"x": _x(figma_x), "y": _y(figma_y, CH)},
        )
        card.bind(
            on_touch_up=lambda inst, touch: (
                on_tap() if inst.collide_point(*touch.pos) else None
            )
        )

        # Icon circle — 66 × 66 at (0, 0) within inner group at (29/26/16, 12/18/18)
        icon_src = _fp(icon_file)
        icon_pad_x = 29.0 if figma_w > 350 else (26.0 if figma_w < 240 else 16.0)
        icon_pad_y = 12.0 if figma_w > 350 else 18.0
        inner_grp_x = (29.0 if figma_w > 350 else 26.0) if figma_w >= 240 else 16.0
        # Use one consistent inner group x from Figma data
        if figma_w > 350:
            inner_grp_x, inner_grp_y = 29.0, 12.0
        elif figma_w > 240:
            inner_grp_x, inner_grp_y = 26.0, 18.0
        else:
            inner_grp_x, inner_grp_y = 16.0, 18.0

        icon_badge = _CircleBadge(
            size_hint=(66.0 / CW, 66.0 / CH),
            pos_hint={"x": inner_grp_x / CW, "y": (CH - inner_grp_y - 66.0) / CH},
        )
        if icon_src:
            icon_badge.add_widget(
                Image(
                    source=icon_src,
                    size_hint=(0.7, 0.7),
                    pos_hint={"x": 0.15, "y": 0.15},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        card.add_widget(icon_badge)

        # Value number  — (82, 0/5/8) in group, 16 × 32, SemiBold 27px
        value_y = inner_grp_y + (0.0 if figma_w > 350 else (5.0 if figma_w > 240 else 8.0))
        value_lbl = _lbl(
            value, _FONT_SB, _ff(27), _WHITE,
            halign="left", valign="top",
            size_hint=(16.0 / CW, 32.0 / CH),
            pos_hint={"x": (inner_grp_x + 82.0) / CW, "y": (CH - value_y - 32.0) / CH},
        )
        card.add_widget(value_lbl)
        card.value_label = value_lbl

        # Arrow icon  — far right in group
        arr_src = _fp("icon_arrow_card.png") or _fp("icon_arrow.png")
        arr_x_offset = 289.0 if figma_w > 350 else (191.0 if figma_w > 240 else 182.0)
        arr_y_offset = inner_grp_y + 32.0
        if arr_src:
            card.add_widget(
                Image(
                    source=arr_src,
                    size_hint=(14.0 / CW, 28.0 / CH),
                    pos_hint={
                        "x": (inner_grp_x + arr_x_offset) / CW,
                        "y": (CH - arr_y_offset - 28.0) / CH,
                    },
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # Label text  — (82, 36/43/40) in group, SemiBold 16/18/18px
        lbl_y_offset = inner_grp_y + (36.0 if figma_w > 350 else (43.0 if figma_w > 240 else 40.0))
        lbl_fs = 16.0 if figma_w > 350 else 18.0
        text_lbl = _lbl(
            label_text, _FONT_SB, _ff(lbl_fs), _MUTED,
            size_hint=((CW - inner_grp_x - 82.0 - 14.0 - 4.0) / CW, 21.0 / CH),
            pos_hint={
                "x": (inner_grp_x + 82.0) / CW,
                "y": (CH - lbl_y_offset - 21.0) / CH,
            },
        )
        card.add_widget(text_lbl)
        card.text_label = text_lbl

        return card

    def _build_schedule_card(self):
        """Schedule/Next Meeting card — (17, 359), 361 × 102."""
        card = self._build_mini_card(
            17, 359, 361, 102,
            icon_file="icon_calendar_schedule.png",
            value="—", label_text="Now: Loading",
            radius=16,
            on_tap=lambda: self.goto("meetings", transition="slide_left"),
        )
        # Extra labels specific to schedule card
        # "+2 more"  — (82, 60) in inner group at (29, 12), SemiBold 15px
        more_lbl = _lbl(
            "", _FONT_SB, _ff(15), (0.0, 0.420, 0.976, 1.0),
            size_hint=(18.0 / 361.0, 18.0 / 102.0),
            pos_hint={"x": (29.0 + 82.0) / 361.0, "y": (102.0 - (12.0 + 60.0) - 18.0) / 102.0},
        )
        card.add_widget(more_lbl)
        card._more_label = more_lbl
        self.schedule_card = card
        return card

    def _build_email_card(self):
        """New emails card — (384, 359), 237 × 102."""
        card = self._build_mini_card(
            384, 359, 237, 102,
            icon_file="icon_email_card.png",
            value="—", label_text="New emails",
            radius=16,
            on_tap=lambda: self.goto("briefing", transition="slide_left"),
        )
        self.email_card = card
        return card

    def _build_tasks_card(self):
        """Tasks due card — (627, 359), 248 × 102."""
        card = self._build_mini_card(
            627, 359, 248, 102,
            icon_file="icon_task_check.png",
            value="0", label_text="Tasks due",
            radius=16,
            on_tap=lambda: self.goto("briefing", transition="slide_left"),
        )
        self.tasks_card = card
        return card

    # ------------------------------------------------------------------
    # Try-saying bar
    # ------------------------------------------------------------------

    def _build_say_bar(self) -> _Card:
        """'Try saying' bottom bar — (27, 476), 838 × 71."""
        CW, CH = 838.0, 71.0

        card = _Card(
            bg_color=_CARD_BG,
            border_color=_CARD_BORDER,
            radius=_ff(21),
            size_hint=(_sw(CW), _sh(CH)),
            pos_hint={"x": _x(27), "y": _y(476, CH)},
        )
        card.bind(
            on_touch_up=lambda inst, touch: (
                self.goto("briefing", transition="slide_left")
                if inst.collide_point(*touch.pos)
                else None
            )
        )

        # Inner group at (16, 3) within bar
        GX, GY = 16.0, 3.0

        # Sparkle / + icon  — (0, 20) in inner group → abs (16, 23) in bar, ~24 × 32
        spk_src = _fp("icon_sparkle_layer.png")
        if spk_src:
            card.add_widget(
                Image(
                    source=spk_src,
                    size_hint=(24.0 / CW, 24.0 / CH),
                    pos_hint={"x": GX / CW, "y": (CH - GY - 20.0 - 24.0) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )
        plus_lbl = _lbl(
            "+", _FONT, _ff(16), (0.106, 0.463, 0.980, 1.0), bold=True,
            halign="center", valign="top",
            size_hint=(10.0 / CW, 19.0 / CH),
            pos_hint={
                "x": (GX + 16.92) / CW,
                "y": (CH - GY - 20.0 - 12.92 - 19.0) / CH,
            },
        )
        card.add_widget(plus_lbl)

        # "Try saying"  — (41, 8) in inner group → abs (57, 11) in bar, 90 × 23, SemiBold 19px
        card.add_widget(
            _lbl(
                "Try saying", _FONT_SB, _ff(19), (0.0, 0.420, 0.976, 1.0),
                size_hint=(90.0 / CW, 23.0 / CH),
                pos_hint={"x": (GX + 41.0) / CW, "y": (CH - (GY + 8.0) - 23.0) / CH},
            )
        )

        # Prompt text  — (41, 37) in inner group → abs (57, 40) in bar, 294 × 19, SemiBold 16px
        card.add_widget(
            _lbl(
                '"Schedule a meeting tomorrow at 4 PM"',
                _FONT_SB, _ff(16), _MUTED,
                size_hint=(294.0 / CW, 19.0 / CH),
                pos_hint={"x": (GX + 41.0) / CW, "y": (CH - (GY + 8.0 + 29.0) - 19.0) / CH},
            )
        )

        # Voice orb  — (403, 0) in inner group → abs (419, 3) in bar, 65 × 65
        orb_src = _fp("icon_voice_orb_bar.png")
        if orb_src:
            card.add_widget(
                Image(
                    source=orb_src,
                    size_hint=(65.0 / CW, 65.0 / CH),
                    pos_hint={"x": (GX + 403.0) / CW, "y": (CH - GY - 65.0) / CH},
                    fit_mode="contain",
                    allow_stretch=True,
                )
            )

        # Keyboard icon  — (752, 9) in inner group → abs (768, 12) in bar, 54 × 48
        kb_src = _fp("icon_keyboard.png")
        if kb_src:
            kb = Button(
                background_normal=kb_src,
                background_down=kb_src,
                border=[0, 0, 0, 0],
                size_hint=(54.0 / CW, 48.0 / CH),
                pos_hint={"x": (GX + 752.0) / CW, "y": (CH - (GY + 9.0) - 48.0) / CH},
            )
            kb.bind(
                on_release=lambda *_: self.goto("briefing", transition="slide_left")
            )
            card.add_widget(kb)

        return card

    # ------------------------------------------------------------------
    # Background sync
    # ------------------------------------------------------------------

    def _sync_bg(self, widget, *_):
        self._bg.pos = widget.pos
        self._bg.size = widget.size

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self._update_clock_labels()
        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(
            lambda _dt: self._update_clock_labels(), 1.0
        )
        if self._footer_ip_event:
            self._footer_ip_event.cancel()
        self._footer_ip_event = Clock.schedule_interval(self._refresh_footer_ip, 30.0)
        Clock.schedule_once(lambda _dt: self._refresh_footer_ip(_dt), 3.0)
        self._load_system_status()
        self._load_home_summary()
        self._refresh_voice_pill()
        if self._voice_state_event:
            self._voice_state_event.cancel()
        self._voice_state_event = Clock.schedule_interval(
            lambda _dt: self._refresh_voice_pill(), 2.0
        )
        try:
            get_weather_client().subscribe(self._on_weather_snapshot)
        except Exception:
            pass

    def on_leave(self):
        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None
        if self._footer_ip_event:
            self._footer_ip_event.cancel()
            self._footer_ip_event = None
        if self._voice_state_event:
            self._voice_state_event.cancel()
            self._voice_state_event = None
        try:
            get_weather_client().unsubscribe(self._on_weather_snapshot)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_footer_ip(self, _dt):
        if not self._footer_kwargs:
            return
        kw = self._footer_kwargs
        self.update_footer(
            wifi_ok=kw["wifi_ok"],
            free_gb=kw["free_gb"],
            privacy_mode=kw["privacy_mode"],
            wired_lan_ok=kw["wired_lan_ok"],
            local_ip=get_primary_ipv4(),
        )

    def _on_start_recording(self, _inst):
        self.app.start_recording()

    def _open_latest_meeting(self):
        if self._latest_meeting_id:
            detail = self.app.screen_manager.get_screen("meeting_detail")
            detail.set_meeting_id(self._latest_meeting_id)
            self.goto("meeting_detail", transition="slide_left")
        else:
            self.goto("meetings", transition="slide_left")

    # ------------------------------------------------------------------
    # Listening pill toggle
    # ------------------------------------------------------------------

    def _toggle_voice_listening(self):
        app = self.app
        if not getattr(app, "voice_assistant", None) or not getattr(
            app.voice_assistant, "available", False
        ):
            self.add_widget(
                ModalDialog(
                    title="Voice unavailable",
                    message=(
                        "No microphone is available, or the wake-word model is "
                        "missing. Run a Microphone Test from Settings to debug."
                    ),
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        app.user_voice_paused = not getattr(app, "user_voice_paused", False)
        app._sync_voice_assistant_state()
        self._refresh_voice_pill()

    # ------------------------------------------------------------------
    # Weather location dialog
    # ------------------------------------------------------------------

    def _show_weather_location_dialog(self):
        wc = get_weather_client()
        cur = wc.location
        cur_city = (cur and cur.get("city")) or ""
        self.add_widget(
            TextInputDialog(
                title="Weather Location",
                message=(
                    'Enter a city name (e.g. "Bangalore" or "London, UK"). '
                    "Leave blank to keep auto-detect."
                ),
                initial_value=cur_city,
                placeholder="City name",
                on_confirm=self._apply_weather_location,
            )
        )

    def _apply_weather_location(self, value: str):
        text = (value or "").strip()
        if not text:
            return
        wc = get_weather_client()

        async def _resolve():
            resolved = await wc.set_city(text)
            if resolved is None:
                Clock.schedule_once(
                    lambda _dt, t=text: self.add_widget(
                        ModalDialog(
                            title="City not found",
                            message=(
                                f'Could not find weather data for "{t}".\n\n'
                                "Try the city name in English, or include the "
                                'country (e.g. "Bengaluru, IN").'
                            ),
                            confirm_text="OK",
                            cancel_text="",
                        )
                    ),
                    0,
                )

        run_async(_resolve())

    # ------------------------------------------------------------------
    # Gmail dashboard dialog
    # ------------------------------------------------------------------

    def _show_gmail_dashboard_dialog(self):
        self.add_widget(
            ModalDialog(
                title="Connect Gmail",
                message=(
                    f"To see unread email here, open\n{DASHBOARD_URL}\n"
                    "on your phone or laptop and connect Gmail."
                ),
                confirm_text="OK",
                cancel_text="",
            )
        )

    # ------------------------------------------------------------------
    # Live weather update
    # ------------------------------------------------------------------

    def _on_weather_snapshot(self, snap):
        if getattr(self, "_health_label_offline", False):
            return
        try:
            temp = float(snap.temp_c)
            label = (snap.label or "--").strip()
            self.health_label.text = f"{temp:.0f}°C"
            self.health_label.color = _WHITE
            self._wx_condition.text = label
            self._brief_wx_title.text = f"Weather: {temp:.0f}°C"
            self._brief_wx_sub.text = label
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Voice pill refresh
    # ------------------------------------------------------------------

    def _refresh_voice_pill(self):
        assistant = getattr(self.app, "voice_assistant", None)
        should_listen = getattr(self.app, "_voice_assistant_should_listen", lambda: False)()
        if assistant and getattr(assistant, "available", False) and should_listen:
            self.voice_dot.color = COLORS["blue"]
            self.voice_state_label.text = "Listening"
        elif assistant and not getattr(assistant, "available", False):
            self.voice_dot.color = COLORS["gray_300"]
            self.voice_state_label.text = "Voice offline"
        else:
            self.voice_dot.color = COLORS["gray_300"]
            self.voice_state_label.text = "Voice paused"

    # ------------------------------------------------------------------
    # Clock labels
    # ------------------------------------------------------------------

    def _update_clock_labels(self):
        now = display_now()
        self.greeting_label.text = _greeting_name(
            getattr(self.app, "user_name", "") or "Stark"
        )
        self._big_clock_hm.text = now.strftime("%I:%M").lstrip("0")
        self._clock_ampm.text = now.strftime("%p")
        self.date_label.text = now.strftime("%A, %B ") + str(now.day)

    # ------------------------------------------------------------------
    # System status (wifi, storage, weather)
    # ------------------------------------------------------------------

    def _load_system_status(self):
        async def _fetch():
            try:
                info = await self.backend.get_system_info()
                free_gb = (info["storage_total"] - info["storage_used"]) / (1024 ** 3)
                wifi_ok = bool(info.get("wifi_ssid"))
                wired_ok = linux_ethernet_ready()
                privacy = getattr(self.app, "privacy_mode", False)

                def _apply(_dt):
                    online = wifi_ok or wired_ok
                    self._health_label_offline = not online
                    if not online:
                        self.health_label.text = "Offline"
                        self.health_label.color = COLORS["red"]
                    else:
                        snap = get_weather_client().snapshot
                        if snap is not None:
                            self._on_weather_snapshot(snap)
                    self._footer_kwargs = {
                        "wifi_ok": wifi_ok,
                        "free_gb": free_gb,
                        "privacy_mode": privacy,
                        "wired_lan_ok": wired_ok,
                    }
                    self.update_footer(
                        wifi_ok=wifi_ok,
                        free_gb=free_gb,
                        privacy_mode=privacy,
                        wired_lan_ok=wired_ok,
                        local_ip=get_primary_ipv4(),
                    )

                Clock.schedule_once(_apply, 0)
            except Exception:
                def _backend_offline(_dt):
                    self._health_label_offline = True
                    self.health_label.text = "Backend\nOffline"
                    self.health_label.color = COLORS["red"]

                Clock.schedule_once(_backend_offline, 0)

        run_async(_fetch())

    # ------------------------------------------------------------------
    # Home summary (meetings, actions, email)
    # ------------------------------------------------------------------

    def _load_home_summary(self):
        async def _fetch():
            try:
                data = await self.backend.get_home_summary()
                meetings = []
                try:
                    meetings = await self.backend.get_meetings(limit=1)
                except Exception:
                    meetings = []
                latest = meetings[0] if meetings else None
                today_n = int(data.get("pending_actions_today") or 0)
                total_n = int(data.get("pending_actions_total") or 0)
                unread_n = data.get("unread_email_count")
                next_title, next_time = _format_next_meeting(data.get("next_meeting"))

                def _apply(_dt):
                    self.next_time_label.text = next_time or "—"
                    self.next_title_label.text = f"Now: {next_title}"
                    self.more_label.text = f"+{max(0, today_n)} more"
                    self.schedule_card.value_label.text = (
                        next_time.split(" ")[0] if next_time else "—"
                    )
                    self.schedule_card.text_label.text = f"Now: {next_title}"
                    if hasattr(self.schedule_card, "_more_label"):
                        self.schedule_card._more_label.text = (
                            f"+{today_n} more" if today_n else ""
                        )
                    if latest:
                        self._latest_meeting_id = latest.get("id")
                        self.last_title_label.text = (
                            latest.get("title") or "Untitled meeting"
                        )
                        try:
                            raw = latest.get("start_time") or latest.get("created_at") or ""
                            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                            when = (
                                to_display_local(dt)
                                .strftime("%b %d · %I:%M %p")
                                .replace(" 0", " ")
                            )
                        except Exception:
                            when = "Recent meeting"
                        dur = int(latest.get("duration") or 0) // 60
                        self.last_meta_label.text = (
                            f"{when} · {dur} min" if dur else when
                        )
                        pa = int(latest.get("pending_actions") or 0)
                        self.last_actions_label.text = (
                            f"{pa} pending actions  ›" if pa else "Open summary  ›"
                        )
                    else:
                        self._latest_meeting_id = None
                        self.last_title_label.text = "No saved meetings yet"
                        self.last_meta_label.text = "Start a recording to build memory"
                        self.last_actions_label.text = "Open meeting library  ›"

                    self.email_card.value_label.text = (
                        str(unread_n) if unread_n is not None else "—"
                    )
                    self.tasks_card.value_label.text = str(total_n)

                    self.brief_calendar_label.title_label.text = (
                        f"{max(0, today_n)} actions today"
                        if today_n
                        else "Briefing ready"
                    )
                    self.brief_calendar_label.subtitle_label.text = (
                        f"First at {next_time}"
                        if next_time and next_time != "Time not set"
                        else "Ask Tony for focus"
                    )
                    self.brief_email_label.title_label.text = "email:  From:"
                    self.brief_email_label.subtitle_label.text = (
                        "Connect Gmail for updates"
                        if unread_n is None
                        else f"{unread_n} new messages"
                    )

                Clock.schedule_once(_apply, 0)
            except Exception:
                Clock.schedule_once(
                    lambda _dt: setattr(
                        self.next_title_label, "text", "Now: Ask Tony for briefing"
                    ),
                    0,
                )

        run_async(_fetch())


# ---------------------------------------------------------------------------
# Duck-typed helper to keep brief card labels compatible with _load_home_summary
# ---------------------------------------------------------------------------

class _BriefRow:
    """Lightweight struct giving .title_label and .subtitle_label access."""

    def __init__(self, title_label: Label, subtitle_label: Label):
        self.title_label = title_label
        self.subtitle_label = subtitle_label
