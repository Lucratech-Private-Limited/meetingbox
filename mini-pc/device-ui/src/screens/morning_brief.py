"""Morning Brief screen — pixel-perfect from Figma 927:220 (1260 × 800 px).

Layout mirrors the Figma node tree exactly:
  • Header / greeting (Group 65)
  • Weather card  (Frame 19 — full-width, 1214.8 × 185.04)
  • Today's Schedule card  (Frame 22 — 639.89 × 327.71, left)
  • Tasks Overview card    (Frame 22 — 553.72 × 327.71, right)
  • Recent Emails card     (Frame 23 — full-width, 1214.8 × 113)

All coordinates are Figma absolute px, converted with _ph() to Kivy fractions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from api_client import (
    _GMAIL_RECENT_DAYS,
    _map_gmail_recent_row,
    summarize_gmail_feed_for_home,
)
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now
from screens.base_screen import BaseScreen
from weather_client import WeatherSnapshot, get_weather_client

logger = logging.getLogger(__name__)

# ── Design frame ──────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

# ── Asset paths ───────────────────────────────────────────────────────────────
_BRIEF   = ASSETS_DIR / "brief"   / "figma"
_CAL_DIR = ASSETS_DIR / "calendar" / "figma"


def _asset(name: str) -> str:
    p = _BRIEF / name
    return str(p) if p.is_file() else ""


def _cal_asset(name: str) -> str:
    p = _CAL_DIR / name
    return str(p) if p.is_file() else ""


# ── Colours ───────────────────────────────────────────────────────────────────
_BG      = (1/255,   8/255,  26/255, 1.0)    # #01081A  background
_WHITE   = (1.0, 1.0, 1.0, 1.0)
_MUTED   = (182/255, 186/255, 242/255, 1.0)  # #B6BAF2
_DIM     = (155/255, 162/255, 178/255, 1.0)  # #9BA2B2  "partly cloudy"
_BLUE    = (0.0,    107/255, 249/255, 1.0)   # #006BF9  links / times
_BLUE2   = (52/255, 129/255, 241/255, 1.0)   # #3481F1  greeting
_GREEN   = (25/255, 211/255, 133/255, 1.0)   # #19D385
_PURPLE  = (169/255, 113/255, 212/255, 1.0)  # #A971D4

# Card fill gradients
_CARD_T  = (2/255,  18/255,  60/255, 1.0)   # #02123C
_CARD_B  = (0.0,   10/255,  38/255, 1.0)    # #000A26
_SCH_T   = (1/255,  17/255,  55/255, 1.0)   # #011137
_SCH_B   = (0.0,   10/255,  38/255, 1.0)    # #000A26

# Border (fill_9NEWTX first colour: #3F4253)
_BDR     = (63/255,  66/255,  83/255, 1.0)

# Dot gradient (simplified to top colour; gradient not supported in Kivy Ellipse)
_DOT_C   = (70/255, 125/255, 254/255, 1.0)  # #467DFE

# Font names (registered in main.py)
_FSB = "42dot-SB"    # SemiBold
_FB  = "42dot-Sans"  # Bold
_FMD = "42dot-Med"   # Medium


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
    """Figma absolute px → Kivy size_hint + pos_hint (1260 × 800 root)."""
    return {
        "size_hint": (fw / FW, fh / FH),
        "pos_hint":  {"x": fx / FW, "y": (FH - fy - fh) / FH},
    }


def _ff(fs: float) -> float:
    """Return the Figma font size scaled to the actual display, plus 20%
    user-requested boost.  On the standard 1260×800 device the display
    scale is 1.0, so this returns fs * 1.2."""
    scale = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)
    return max(6.0, fs * scale * 1.2)


_GC: dict = {}


def _aqi_category(aqi: int) -> tuple[str, str]:
    """Return (label, hex_colour) for a US AQI value."""
    if aqi <= 50:
        return "Good",        "#19D385"
    if aqi <= 100:
        return "Moderate",    "#FFD500"
    if aqi <= 150:
        return "Sensitive",   "#FF7E00"
    if aqi <= 200:
        return "Unhealthy",   "#FF0000"
    if aqi <= 300:
        return "Very Poor",   "#960032"
    return "Hazardous",       "#7E0023"


def _grad(top: tuple, bot: tuple):
    from kivy.graphics.texture import Texture
    k = (top, bot)
    if k not in _GC:
        t = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c): return [min(255, max(0, int(v * 255))) for v in c]
        t.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        t.mag_filter = t.min_filter = "linear"
        t.wrap = "clamp_to_edge"
        _GC[k] = t
    return _GC[k]


def _lbl(text: str, font: str, size: float, color: tuple,
         ha: str = "left", va: str = "top", **kw) -> Label:
    lw = Label(text=text, font_name=font, font_size=size, color=color,
               halign=ha, valign=va, **kw)
    lw.bind(size=lw.setter("text_size"))
    return lw


def _mlbl(text: str, font: str, size: float, color: tuple,
          ha: str = "left", va: str = "top", **kw) -> Label:
    """Label with Kivy markup enabled (for mixed colours)."""
    lw = Label(text=text, font_name=font, font_size=size, color=color,
               markup=True, halign=ha, valign=va, **kw)
    lw.bind(size=lw.setter("text_size"))
    return lw


def _img(src: str, CW: float, CH: float,
         rel_x: float, rel_y: float, iw: float, ih: float) -> Image:
    return Image(source=src, fit_mode="contain",
                 size_hint=(iw / CW, ih / CH),
                 pos_hint={"x": rel_x / CW, "y": (CH - rel_y - ih) / CH})


# ── Gradient card ─────────────────────────────────────────────────────────────

class _Card(FloatLayout):
    def __init__(self, ct: tuple, cb: tuple, bdr: tuple, r: float = 12,
                 bdr_alpha: float = 0.9, **kw):
        super().__init__(**kw)
        self._r = r
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[r],
                texture=_grad(ct, cb))
        with self.canvas.after:
            Color(*bdr[:3], bdr_alpha)
            self._ln = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, r),
                width=1.0)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        r = self._r
        self._bg.pos  = self.pos
        self._bg.size = self.size
        self._bg.radius = [r]
        self._ln.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


class _ImgBtn(ButtonBehavior, Image):
    pass


# ── Small reusable builders ───────────────────────────────────────────────────

def _add_divider(parent: FloatLayout, CW: float, CH: float,
                 rel_x: float, rel_y: float,
                 rel_w: float, rel_h: float) -> None:
    """Horizontal/vertical separator line (dark fade-through blue)."""
    dv = Widget(size_hint=(rel_w / CW, rel_h / CH),
                pos_hint={"x": rel_x / CW,
                          "y": (CH - rel_y - rel_h) / CH})
    with dv.canvas.before:
        Color(2/255, 23/255, 77/255, 0.85)
        _r = Rectangle(pos=dv.pos, size=dv.size)
    def _mk(r):
        def _s(w, *_): r.pos = w.pos; r.size = w.size
        return _s
    dv.bind(pos=_mk(_r), size=_mk(_r))
    parent.add_widget(dv)


def _add_dot(parent: FloatLayout, CW: float, CH: float,
             rel_x: float, rel_y: float, d: float = 11.3) -> None:
    """Small blue circle dot."""
    dw = Widget(size_hint=(d / CW, d / CH),
                pos_hint={"x": rel_x / CW,
                          "y": (CH - rel_y - d) / CH})
    with dw.canvas:
        Color(*_DOT_C)
        _e = Ellipse(pos=dw.pos, size=dw.size)
    def _mk(e):
        def _s(w, *_): e.pos = w.pos; e.size = w.size
        return _s
    dw.bind(pos=_mk(_e), size=_mk(_e))
    parent.add_widget(dw)


# ── Screen ────────────────────────────────────────────────────────────────────

class MorningBriefScreen(BaseScreen):
    """Morning Brief page — full-screen Figma 927:220 render."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hdr_greeting = None
        self._hdr_subtitle = None
        self._wx_temp = None
        self._wx_condition = None
        self._wx_city = None
        self._wx_hi_lo = None
        self._wx_hum = None
        self._wx_wind = None
        self._wx_aqi = None
        self._wx_aqi_lbl = None
        self._sched_rows = []
        self._task_nums = []
        self._task_titles = []
        self._task_subs = []
        self._em_sender = None
        self._em_subject = None
        self._em_time = None
        self._weather_unsub = None
        self._build_ui()

    # ── Top-level build ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))
        with root.canvas.before:
            Color(*_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, v: setattr(self._bg_rect, "pos", v),
            size=lambda w, v: setattr(self._bg_rect, "size", v),
        )
        self._build_header(root)
        self._build_weather_card(root)
        self._build_schedule_card(root)
        self._build_tasks_card(root)
        self._build_email_card(root)
        self.add_widget(root)

    # ── Header (Group 65) ──────────────────────────────────────────────────────
    # Figma: Group at (22.6, 21.19), 381 × 104.11
    #   "Morning Brief"          (0, 0)     238 × 45   SemiBold 38.14  #FFFFFF
    #   "Good morning, J.K"      (0, 43.79) 193 × 27   SemiBold 22.6   #3481F1
    #   "Here's your overview…"  (0, 79.11) 381 × 25   SemiBold 21.19  #B6BAF2
    # Back button added at (24.02, 21.19); header texts shifted to x=118.66.

    def _build_header(self, root: FloatLayout) -> None:
        back_src = _cal_asset("btn_back.png")
        if back_src:
            btn = _ImgBtn(source=back_src, fit_mode="contain",
                          **_ph(24.02, 21.19, 76.28, 76.28))
            btn.bind(on_release=lambda *_: self.go_back())
            root.add_widget(btn)

        root.add_widget(_lbl(
            "Morning Brief", _FSB, _ff(38.14), _WHITE,
            **_ph(118.66, 21.19, 320.0, 50.0)))

        self._hdr_greeting = _lbl(
            "Good morning", _FSB, 22.6, _BLUE2,
            **_ph(118.66, 63.0, 380.0, 32.0))
        root.add_widget(self._hdr_greeting)

        self._hdr_subtitle = _lbl(
            "Loading your overview…", _FSB, _ff(21.19), _MUTED,
            **_ph(118.66, 98.0, 560.0, 32.0))
        root.add_widget(self._hdr_subtitle)

    # ── Weather card (Frame 19) ────────────────────────────────────────────────
    # Figma: (22.6, 132.78)  1214.8 × 185.04  r=22.6
    # Gradient fill: #02123C → #000A26
    # 4 vertical dividers at x = 387.04, 591.86, 796.68, 1001.5 (relative)
    # Columns: main temp | High/Low | Humidity | Wind | AQI

    def _build_weather_card(self, root: FloatLayout) -> None:
        CX, CY = 22.6, 132.78
        CW, CH = 1214.8, 185.04

        card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR,
                     r=_ff(22.6), **_ph(CX, CY, CW, CH))

        # ── Header label
        card.add_widget(_lbl(
            "Weather Update", _FSB, _ff(21.19), _WHITE,
            size_hint=(200 / CW, 32 / CH),
            pos_hint={"x": 35.31 / CW, "y": (CH - 20.0 - 32) / CH}))

        # ── Sun / cloud image  (29.66, 72.04)  79.1 × 79.1
        cloud_src = _asset("weather_cloud.png")
        if cloud_src:
            card.add_widget(_img(cloud_src, CW, CH, 29.66, 72.04, 79.1, 79.1))

        # ── Main temperature  (128.54, 62.15)  58 × 42  Bold 35.31
        self._wx_temp = _lbl(
            "—°", _FB, _ff(35.31), _WHITE,
            size_hint=(80 / CW, 42 / CH),
            pos_hint={"x": 128.54 / CW, "y": (CH - 62.15 - 42) / CH})
        card.add_widget(self._wx_temp)

        # ── "partly cloudy"  (128.54, 104.53)
        self._wx_condition = _lbl(
            "…", _FSB, _ff(21.19), _DIM,
            size_hint=(190 / CW, 32 / CH),
            pos_hint={"x": 128.54 / CW, "y": (CH - 104.53 - 32) / CH})
        card.add_widget(self._wx_condition)

        # ── Location icon + city  (128.54, 137.01)
        loc_src = _asset("icon_location.png")
        if loc_src:
            card.add_widget(_img(loc_src, CW, CH, 128.54, 137.01, 19.78, 19.78))
        self._wx_city = _lbl(
            "…", _FSB, _ff(21.19), _MUTED,
            size_hint=(240 / CW, 32 / CH),
            pos_hint={"x": 151.14 / CW, "y": (CH - 134.19 - 32) / CH})
        card.add_widget(self._wx_city)

        # ── Four vertical dividers
        for div_x in (387.04, 591.86, 796.68, 1001.5):
            dv = Widget(size_hint=(2.83 / CW, 115.83 / CH),
                        pos_hint={"x": div_x / CW,
                                  "y": (CH - 35.32 - 115.83) / CH})
            with dv.canvas.before:
                Color(2/255, 23/255, 77/255, 0.85)
                _r = Rectangle(pos=dv.pos, size=dv.size)
            def _mk(r):
                def _s(w, *_): r.pos = w.pos; r.size = w.size
                return _s
            dv.bind(pos=_mk(_r), size=_mk(_r))
            card.add_widget(dv)

        # ── High / Low column  (group at 413.88, 63.57)
        tmp_src = _asset("icon_temperature.png")
        if tmp_src:
            card.add_widget(_img(tmp_src, CW, CH, 413.88, 64.98, 31.08, 31.08))
        self._wx_hi_lo = _lbl(
            "— / —", _FB, _ff(26.84), _WHITE,
            size_hint=(140 / CW, 38 / CH),
            pos_hint={"x": 450.61 / CW, "y": (CH - 63.57 - 38) / CH})
        card.add_widget(self._wx_hi_lo)
        card.add_widget(_lbl(
            "High / Low", _FSB, _ff(16.95), _MUTED,
            size_hint=(90 / CW, 26 / CH),
            pos_hint={"x": 463.32 / CW, "y": (CH - 103.12 - 26) / CH}))

        # ── Humidity column  (group at 644.13, 63.57)
        hum_src = _asset("icon_humidity.png")
        if hum_src:
            card.add_widget(_img(hum_src, CW, CH, 644.13, 66.39, 28.25, 28.25))
        self._wx_hum = _lbl(
            "—%", _FB, _ff(26.84), _WHITE,
            size_hint=(70 / CW, 38 / CH),
            pos_hint={"x": 678.03 / CW, "y": (CH - 63.57 - 38) / CH})
        card.add_widget(self._wx_hum)
        card.add_widget(_lbl(
            "Humidity", _FSB, _ff(16.95), _MUTED,
            size_hint=(80 / CW, 26 / CH),
            pos_hint={"x": 658.26 / CW, "y": (CH - 103.12 - 26) / CH}))

        # ── Wind column  (group at 827.76, 63.57)
        wind_src = _asset("icon_wind.png")
        if wind_src:
            card.add_widget(_img(wind_src, CW, CH, 827.76, 64.28, 35.31, 35.31))
        self._wx_wind = _lbl(
            "—", _FB, _ff(26.84), _WHITE,
            size_hint=(120 / CW, 38 / CH),
            pos_hint={"x": 875.08 / CW, "y": (CH - 63.57 - 38) / CH})
        card.add_widget(self._wx_wind)
        card.add_widget(_lbl(
            "Wind", _FSB, _ff(16.95), _MUTED,
            size_hint=(50 / CW, 26 / CH),
            pos_hint={"x": 896.27 / CW, "y": (CH - 103.12 - 26) / CH}))

        # ── AQI column  (group at 1063.66, 66.39)
        # "42" rendered in #19D385 via Kivy markup
        self._wx_aqi = _mlbl(
            "AQI —",
            _FB, _ff(26.84), _WHITE,
            size_hint=(140 / CW, 38 / CH),
            pos_hint={"x": 1063.66 / CW, "y": (CH - 66.39 - 38) / CH})
        card.add_widget(self._wx_aqi)
        self._wx_aqi_lbl = _lbl(
            "—", _FSB, _ff(16.95), _MUTED,
            size_hint=(80 / CW, 26 / CH),
            pos_hint={"x": 1091.91 / CW, "y": (CH - 105.94 - 26) / CH})
        card.add_widget(self._wx_aqi_lbl)

        root.add_widget(card)

    # ── Today's Schedule card (Frame 22, left) ─────────────────────────────────
    # Figma: (29.66, 324.89)  639.89 × 327.71  r=16.95
    # Gradient fill: #011137 → #000A26
    # 3 horizontal dividers at card-relative y = 81.93, 162.44, 242.96

    def _build_schedule_card(self, root: FloatLayout) -> None:
        CX, CY = 29.66, 324.89
        CW, CH = 639.89, 327.71

        card = _Card(ct=_SCH_T, cb=_SCH_B, bdr=_BDR,
                     r=_ff(16.95), **_ph(CX, CY, CW, CH))

        # Calendar icon  (24.02, 25.43)  36.98 × 33.39
        cal_src = _asset("icon_calendar.png")
        if cal_src:
            card.add_widget(_img(cal_src, CW, CH, 24.02, 25.43, 36.98, 33.39))

        # "Today's Schedule"  (70.63, 29.66)
        card.add_widget(_lbl(
            "Today's Schedule", _FSB, _ff(21.19), _WHITE,
            size_hint=(200 / CW, 32 / CH),
            pos_hint={"x": 70.63 / CW, "y": (CH - 26.0 - 32) / CH}))

        # "View full calender"  (402.58, 29.66)  + arrow  (597.51, 22.6)
        card.add_widget(_lbl(
            "View full calender", _FSB, _ff(21.19), _BLUE,
            size_hint=(185 / CW, 32 / CH),
            pos_hint={"x": 402.58 / CW, "y": (CH - 26.0 - 32) / CH}))
        arr_src = _asset("icon_arrow_right.png")
        if arr_src:
            card.add_widget(_img(arr_src, CW, CH, 597.51, 22.6, 19.78, 39.55))

        # Horizontal dividers
        for div_y in (81.93, 162.44, 242.96):
            _add_divider(card, CW, CH, 24.02, div_y, 593.27, 2.83)

        # Schedule rows
        # Each row: time (left, blue) · dot (centre) · title · duration (right)
        self._sched_rows = []
        for row_meta in [
            ("10:00 AM", "Product Roadmap Sync",  "45 min", 111.59, 118.65),
            ("1:00 PM",  "Client Review Meeting", "60 min", 193.52, 200.58),
            ("4:00 PM",  "Design Discussion",     "30 min", 271.21, 278.27),
        ]:
            time_s, title_s, dur_s, row_y, dot_y = row_meta
            lt = _lbl(
                time_s, _FMD, _ff(21.19), _BLUE,
                size_hint=(145 / CW, 32 / CH),
                pos_hint={"x": 15.0 / CW, "y": (CH - row_y - 32) / CH})
            card.add_widget(lt)
            _add_dot(card, CW, CH, 158.2, dot_y)
            tit = _lbl(
                title_s, _FSB, _ff(21.19), _WHITE,
                size_hint=(360 / CW, 32 / CH),
                pos_hint={"x": 182.22 / CW, "y": (CH - row_y - 32) / CH})
            card.add_widget(tit)
            du = _lbl(
                dur_s, _FSB, _ff(21.19), _MUTED,
                size_hint=(75 / CW, 32 / CH),
                pos_hint={"x": 548.07 / CW, "y": (CH - row_y - 32) / CH})
            card.add_widget(du)
            self._sched_rows.append((lt, tit, du))

        root.add_widget(card)

    # ── Tasks Overview card (Frame 22, right) ──────────────────────────────────
    # Figma: (676.62, 324.89)  553.72 × 327.71  r=16.95
    # Same gradient as schedule card.
    # 3 task rows — each with icon card, count badge, label, sub-label.

    def _build_tasks_card(self, root: FloatLayout) -> None:
        CX, CY = 676.62, 324.89
        CW, CH = 553.72, 327.71

        card = _Card(ct=_SCH_T, cb=_SCH_B, bdr=_BDR,
                     r=_ff(16.95), **_ph(CX, CY, CW, CH))

        # Tick / tasks icon  (21.19, 21.19)  42.38 × 42.38
        tick_src = _asset("icon_tick.png")
        if tick_src:
            card.add_widget(_img(tick_src, CW, CH, 21.19, 21.19, 42.38, 42.38))

        # "Tasks Overview"  (70.63, 29.66)
        card.add_widget(_lbl(
            "Tasks Overview", _FSB, _ff(21.19), _WHITE,
            size_hint=(170 / CW, 32 / CH),
            pos_hint={"x": 70.63 / CW, "y": (CH - 26.0 - 32) / CH}))

        # "View full tasks"  (360.2, 29.66) + arrow  (512.76, 22.6)
        card.add_widget(_lbl(
            "View full tasks", _FSB, _ff(21.19), _BLUE,
            size_hint=(150 / CW, 32 / CH),
            pos_hint={"x": 360.2 / CW, "y": (CH - 26.0 - 32) / CH}))
        arr_src = _asset("icon_arrow_right.png")
        if arr_src:
            card.add_widget(_img(arr_src, CW, CH, 512.76, 22.6, 19.78, 39.55))

        # Horizontal dividers (width capped to card interior)
        for div_y in (81.93, 162.44, 242.96):
            _add_divider(card, CW, CH, 24.02, div_y, 505.68, 2.83)

        self._task_nums = []
        self._task_titles = []
        self._task_subs = []
        # Task rows — labels updated from commitments / briefing API
        for (icon_file, num_s, num_col, label_s, sub_s,
             icon_y, num_y, grp_y) in [
            ("icon_task_1.png", "—", _BLUE,   "Due Today", "—",
             93.23,  103.12, 98.88),
            ("icon_task_2.png", "—", _PURPLE, "Upcoming",  "—",
             173.74, 183.63, 179.39),
            ("icon_task_3.png", "—", _GREEN,  "Unplanned", "—",
             252.85, 262.73, 259.91),
        ]:
            task_src = _asset(icon_file)
            if task_src:
                card.add_widget(_img(
                    task_src, CW, CH, 31.08, icon_y, 70.63, 62.15))
            num_l = _lbl(
                num_s, _FB, _ff(35.31), num_col,
                ha="center", va="middle",
                size_hint=(40 / CW, 48 / CH),
                pos_hint={"x": 120.0 / CW, "y": (CH - num_y - 48) / CH})
            card.add_widget(num_l)
            self._task_nums.append(num_l)
            t1 = _lbl(
                label_s, _FSB, _ff(21.19), _WHITE,
                size_hint=(180 / CW, 32 / CH),
                pos_hint={"x": 217.53 / CW, "y": (CH - grp_y - 32) / CH})
            card.add_widget(t1)
            self._task_titles.append(t1)
            t2 = _lbl(
                sub_s, _FMD, _ff(16.95), _MUTED,
                size_hint=(280 / CW, 26 / CH),
                pos_hint={"x": 217.53 / CW,
                          "y": (CH - grp_y - 31.07 - 26) / CH})
            card.add_widget(t2)
            self._task_subs.append(t2)

        root.add_widget(card)

    # ── Recent Emails card (Frame 23) ──────────────────────────────────────────
    # Figma: (22.6, 659.66)  1214.8 × 113  r=24.01
    # Single email preview row.

    def _build_email_card(self, root: FloatLayout) -> None:
        CX, CY = 22.6, 659.66
        CW, CH = 1214.8, 113.0

        card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR,
                     r=_ff(24.01), **_ph(CX, CY, CW, CH))

        # Email icon  (46.62, 14.13)  33.9 × 33.9
        em_src = _asset("icon_email.png")
        if em_src:
            card.add_widget(_img(em_src, CW, CH, 46.62, 14.13, 33.9, 33.9))

        # Section header label  (87.58, 18.37)
        card.add_widget(_lbl(
            "Recent Emails", _FSB, _ff(21.19), _WHITE,
            size_hint=(200 / CW, 32 / CH),
            pos_hint={"x": 87.58 / CW, "y": (CH - 14.0 - 32) / CH}))

        # "Go to emails" link  (1001.5, 15.54) + arrow  (1148.41, 8.48)
        from kivy.uix.behaviors import ButtonBehavior
        from kivy.uix.floatlayout import FloatLayout as _FL

        class _EmailLink(ButtonBehavior, _FL):
            pass

        email_tap = _EmailLink(
            size_hint=(160 / CW, 40 / CH),
            pos_hint={"x": 1001.5 / CW, "y": (CH - 11.0 - 40) / CH},
        )
        email_tap.add_widget(_lbl(
            "Go to emails  ›", _FSB, _ff(21.19), _BLUE,
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
        ))
        email_tap.bind(on_release=lambda *_: self.goto("emails", transition="slide_left"))
        card.add_widget(email_tap)

        # Horizontal divider  (29.66, 55.09)  1155.47 × 2.83
        _add_divider(card, CW, CH, 29.66, 55.09, 1155.47, 2.83)

        # Sender dot  at (59.33, 80.51)  11.3 × 11.3
        _add_dot(card, CW, CH, 59.33, 80.51)

        # Sender name  (93.23, 73.45)
        self._em_sender = _lbl(
            "—", _FSB, _ff(21.19), _WHITE,
            size_hint=(220 / CW, 30 / CH),
            pos_hint={"x": 93.23 / CW, "y": (CH - 70.0 - 30) / CH})
        card.add_widget(self._em_sender)

        # Email subject  (372.91, 76.28)
        self._em_subject = _lbl(
            "—",
            _FMD, _ff(18.36), _MUTED,
            size_hint=(480 / CW, 28 / CH),
            pos_hint={"x": 372.91 / CW, "y": (CH - 73.0 - 28) / CH})
        card.add_widget(self._em_subject)

        # Timestamp  (1084.84, 76.28)
        self._em_time = _lbl(
            "—", _FMD, _ff(18.36), _MUTED,
            size_hint=(120 / CW, 28 / CH),
            pos_hint={"x": 1084.84 / CW, "y": (CH - 73.0 - 28) / CH})
        card.add_widget(self._em_time)

        root.add_widget(card)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @staticmethod
    def _first_name(display_name: str | None) -> str:
        if not (display_name or "").strip():
            return "there"
        part = display_name.strip().split()[0]
        return part if part else "there"

    @staticmethod
    def _fmt_ampm(dt: datetime) -> str:
        h24 = dt.hour
        m = dt.minute
        am = "AM" if h24 < 12 else "PM"
        h12 = h24 % 12
        if h12 == 0:
            h12 = 12
        return f"{h12}:{m:02d} {am}"

    def _on_weather_snapshot(self, snap: WeatherSnapshot) -> None:
        def _apply(_dt):
            try:
                if self._wx_temp:
                    self._wx_temp.text = f"{round(snap.temp_c):.0f}°"
                if self._wx_condition:
                    self._wx_condition.text = (snap.label or "-").lower()
                if self._wx_city:
                    self._wx_city.text = snap.city or "-"

                if self._wx_hi_lo:
                    hi = f"{round(snap.hi_c)}" if snap.hi_c is not None else "-"
                    lo = f"{round(snap.lo_c)}" if snap.lo_c is not None else "-"
                    self._wx_hi_lo.text = f"{hi} / {lo}"

                if self._wx_hum:
                    self._wx_hum.text = (
                        f"{snap.humidity_pct}%"
                        if snap.humidity_pct is not None else "-%"
                    )

                if self._wx_wind:
                    self._wx_wind.text = (
                        f"{round(snap.wind_kmh)} km/h"
                        if snap.wind_kmh is not None else "-"
                    )

                if self._wx_aqi:
                    if snap.aqi is not None:
                        cat, hex_col = _aqi_category(snap.aqi)
                        self._wx_aqi.text = (
                            f"AQI [color={hex_col}]{snap.aqi}[/color]"
                        )
                        if self._wx_aqi_lbl:
                            self._wx_aqi_lbl.text = cat
                    else:
                        self._wx_aqi.text = "AQI -"
                        if self._wx_aqi_lbl:
                            self._wx_aqi_lbl.text = "-"
            except Exception:
                logger.debug("morning_brief weather UI apply failed", exc_info=True)
        Clock.schedule_once(_apply, 0)

    def on_enter(self) -> None:
        wc = get_weather_client()
        self._weather_unsub = self._on_weather_snapshot
        wc.subscribe(self._weather_unsub)
        wc.start(refresh_seconds=900)
        if wc.snapshot:
            self._on_weather_snapshot(wc.snapshot)
        else:
            wc.refresh_now()
        Clock.schedule_once(lambda _dt: self._load_briefing_backend(), 0)

    def on_leave(self) -> None:
        if self._weather_unsub:
            try:
                get_weather_client().unsubscribe(self._weather_unsub)
            except Exception:
                pass
            self._weather_unsub = None

    def _load_briefing_backend(self) -> None:
        async def _go():
            async def _briefing():
                return await self.backend.get_briefing_context(days_ahead=1)

            async def _gmail():
                gf = getattr(self.backend, "fetch_gmail_recent", None)
                if gf is None:
                    return {}
                return await gf(max_results=40, days=_GMAIL_RECENT_DAYS, q="")

            results = await asyncio.gather(_briefing(), _gmail(), return_exceptions=True)
            data  = results[0] if not isinstance(results[0], BaseException) else {}
            gfeed = results[1] if not isinstance(results[1], BaseException) else {}
            Clock.schedule_once(
                lambda dt: self._apply_briefing_data(data or {}, gfeed),
                0,
            )
        run_async(_go())

    def _apply_briefing_data(self, data: dict, gfeed: dict | None = None) -> None:
        try:
            dn = data.get("user_display_name")
            greet = (data.get("greeting") or "Hello").strip()
            if self._hdr_greeting:
                self._hdr_greeting.text = f"{greet}, {self._first_name(dn)}"
            today_s = (data.get("today") or "").strip()
            if not today_s:
                today_s = display_now().date().isoformat()
            try:
                td = date.fromisoformat(today_s)
                months = (
                    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                )
                nice = f"{td.day} {months[td.month - 1]}"
            except ValueError:
                nice = today_s
            if self._hdr_subtitle:
                self._hdr_subtitle.text = f"Here's your overview for today, {nice}"

            pa = data.get("pending_assistant") or {}
            try:
                pend_n = int(pa.get("count_pending", pa.get("count") or 0) or 0)
            except (TypeError, ValueError):
                pend_n = 0
            items = pa.get("items") or []
            if pend_n > 0 and self._hdr_subtitle:
                first = ""
                if items and isinstance(items[0], dict):
                    first = str(items[0].get("brief_label") or items[0].get("title") or "").strip()
                tail = f" — e.g. {first[:56]}" if first else ""
                self._hdr_subtitle.text = (
                    self._hdr_subtitle.text
                    + f"\n{pend_n} assistant approval(s) waiting{tail}."
                )

            meetings = ((data.get("days") or {}).get(today_s) or {}).get("meetings") or []
            # schedule rows (max 3)
            for idx, row in enumerate(self._sched_rows):
                t_l, tit_l, d_l = row
                if idx < len(meetings):
                    ev = meetings[idx]
                    start_s = ev.get("start") or ev.get("start_time") or ""
                    try:
                        sdt = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
                        t_l.text = self._fmt_ampm(sdt)
                    except Exception:
                        t_l.text = "—"
                    tit_l.text = (ev.get("title") or "—")[:48]
                    dur = int(ev.get("duration") or 0)
                    if dur > 0:
                        d_l.text = f"{max(1, dur // 60)} min"
                    else:
                        d_l.text = "—"
                else:
                    t_l.text = "—"
                    tit_l.text = "Free" if idx == 0 else ""
                    d_l.text = ""

            rows = data.get("commitments") or []
            today_d = date.fromisoformat(today_s)
            due_today = 0
            upcoming = 0
            unplanned = 0
            next_line = "No upcoming"
            for r in rows:
                if (r.get("status") or "") not in ("active", "snoozed"):
                    continue
                da = (r.get("due_at") or r.get("remind_at") or "").strip()
                if not da:
                    unplanned += 1
                    continue
                try:
                    if "T" in da:
                        dpart = datetime.fromisoformat(da.replace("Z", "+00:00")).date()
                    else:
                        dpart = date.fromisoformat(da[:10])
                except Exception:
                    unplanned += 1
                    continue
                if dpart == today_d:
                    due_today += 1
                elif dpart > today_d:
                    upcoming += 1
                    if next_line == "No upcoming":
                        next_line = (r.get("title") or "Task")[:40]
                else:
                    upcoming += 1

            if self._task_nums and len(self._task_nums) >= 3:
                self._task_nums[0].text = str(due_today)
                self._task_nums[1].text = str(upcoming)
                self._task_nums[2].text = str(unplanned)
            if self._task_subs and len(self._task_subs) >= 3:
                self._task_subs[0].text = f"{due_today} due today"
                self._task_subs[1].text = f"Next: {next_line}"
                self._task_subs[2].text = f"{unplanned} without date"

            gprev = data.get("gmail_preview") or {}
            top = gprev.get("top") if isinstance(gprev, dict) else None
            gsum = summarize_gmail_feed_for_home(gfeed or {})
            top_raw = gsum.get("top_raw")
            if isinstance(top_raw, dict) and self._em_sender and self._em_subject:
                row = _map_gmail_recent_row(top_raw)
                self._em_sender.text = (row.get("sender") or "—")[:42]
                self._em_subject.text = (
                    (row.get("subject") or row.get("preview") or "—")[:64]
                )
                self._em_time.text = (row.get("time") or "—")[:12]
            elif top and self._em_sender and self._em_subject:
                self._em_sender.text = (top.get("from") or "—")[:42]
                self._em_subject.text = (top.get("subject") or top.get("snippet") or "—")[:64]
                self._em_time.text = (top.get("date") or "")[-12:] or "—"
            elif self._em_sender:
                self._em_sender.text = "No recent mail"
                if self._em_subject:
                    self._em_subject.text = (
                        "Connect Gmail in settings"
                        if not (gsum.get("connected") or gprev.get("connected"))
                        else "—"
                    )
                if self._em_time:
                    self._em_time.text = ""
        except Exception:
            logger.debug("apply briefing data failed", exc_info=True)
