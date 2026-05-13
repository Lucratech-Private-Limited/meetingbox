"""Home screen — pixel-perfect Figma 390:187 (yJqcY4KovVjJ11vjysW533).

Figma frame: 1260 × 800 px (landscape).  Every coordinate, dimension, font
size, and colour is taken directly from the Figma node data.  Live data
(clock, weather, meetings, voice state) updates at runtime exactly as before.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import (
    Color, Ellipse, Line, PopMatrix, PushMatrix, Rectangle,
    RoundedRectangle, Scale,
)
from kivy.properties import NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Figma design constants (frame 390:187, 1260 × 800 px)
# ---------------------------------------------------------------------------
_FW = 1260.0
_FH = 800.0

_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Colours from Figma
_WHITE = (1.0, 1.0, 1.0, 1.0)
_MUTED = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2
_BLUE  = (0.0, 0.420, 0.976, 1.0)     # #006BF9 / #006FFF
_BLUE2 = (0.204, 0.506, 0.945, 1.0)   # #3481F1  next-up section
_GREY  = (0.643, 0.643, 0.675, 1.0)   # #A4A4AC  section headers

# Card backgrounds / borders (approximated from Figma gradients)
_CARD_BG     = (0.004, 0.067, 0.216, 1.0)   # #011137 (top of gradient)
_CARD_BORDER = (0.137, 0.161, 0.259, 1.0)   # #232942 — subtle dark navy, between Figma gradient stops
_HERO_BG     = (0.004, 0.047, 0.145, 1.0)   # #010C25 (solid fill)
_PILL_BG     = (0.0,   0.059, 0.200, 1.0)   # #000F33 (pill gradient start)
_ROW_BG      = (0.004, 0.043, 0.149, 1.0)   # #010B26 (brief row bg)
_ROW_BORDER  = (0.106, 0.137, 0.212, 1.0)   # #1B2336 (brief row border)

# Font families registered in main.py via _register_asta_fonts()
_FONT    = "42dot-Sans"   # Regular + Bold
_FONT_SB = "42dot-SB"    # SemiBold
_FONT_MD = "42dot-Med"   # Medium


# ---------------------------------------------------------------------------
# Coordinate helpers  (Figma absolute px → Kivy FloatLayout fractions)
# ---------------------------------------------------------------------------

def _x(px: float) -> float:
    return px / _FW


def _y(top: float, h: float) -> float:
    return max(0.0, (_FH - top - h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(fs: float) -> int:
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * scale))


# ---------------------------------------------------------------------------
# Asset helper
# ---------------------------------------------------------------------------

def _fp(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Gradient fill helper (creates a 1×2 texture → vertical gradient in Kivy)
# ---------------------------------------------------------------------------

_GRAD_CACHE: dict = {}


def _grad(top: tuple, bot: tuple):
    """Return a cached 1×2 Texture that produces a vertical gradient fill."""
    from kivy.graphics.texture import Texture  # local import – Kivy not ready at module load
    key = (top, bot)
    if key not in _GRAD_CACHE:
        tex = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c): return [min(255, max(0, int(x * 255))) for x in c]
        # Kivy blit_buffer is bottom-up: first row = bottom of shape
        tex.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        tex.wrap = "clamp_to_edge"
        _GRAD_CACHE[key] = tex
    return _GRAD_CACHE[key]


# Gradient stop colours (all from Figma)
_CARD_TOP = (0.00392, 0.06667, 0.21569, 1.0)   # #011137  card gradient top
_CARD_BOT = (0.0,     0.03922, 0.14902, 1.0)   # #000A26  card gradient bottom
_PILL_TOP = (0.0,     0.05882, 0.20000, 1.0)   # #000F33  pill top
_PILL_BOT = (0.0,     0.03922, 0.14902, 1.0)   # #000A26  pill bottom
_REC_TOP  = (0.0,     0.21961, 0.71373, 1.0)   # #0038B6  recording btn top
_REC_BOT  = (0.0,     0.13725, 0.46275, 1.0)   # #002376  recording btn bottom


# ---------------------------------------------------------------------------
# Circular image widget (renders an image clipped to an ellipse)
# ---------------------------------------------------------------------------

class _CircularImg(Widget):
    """Renders *source* as a circle/ellipse using canvas Ellipse + texture."""

    def __init__(self, source: str, **kw):
        super().__init__(**kw)
        self._src = source
        self._tex = None
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def _draw(self, *_):
        if not self._src:
            return
        self.canvas.clear()
        with self.canvas:
            try:
                if self._tex is None:
                    self._tex = CoreImage(self._src).texture
                Color(1, 1, 1, 1)
                Ellipse(pos=self.pos, size=self.size, texture=self._tex)
            except Exception as exc:
                logger.debug("_CircularImg load failed %s: %s", self._src, exc)


# ---------------------------------------------------------------------------
# Scalable image widget (canvas-transform scale, layout unchanged)
# ---------------------------------------------------------------------------

class _ScalableImage(Widget):
    """Renders a PNG image and supports smooth CSS-style scale animation.

    ``orb_scale`` drives a PushMatrix/Scale/PopMatrix canvas transform so the
    visual size changes without affecting the FloatLayout positions of siblings.
    """
    orb_scale = NumericProperty(1.0)

    def __init__(self, source: str, **kw):
        super().__init__(**kw)
        self._tex = None
        try:
            self._tex = CoreImage(source).texture
        except Exception as exc:
            logger.debug("_ScalableImage load %s: %s", source, exc)
        with self.canvas:
            PushMatrix()
            self._sc = Scale(1, 1, 1)
            Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size,
                                   texture=self._tex)
            PopMatrix()
        self.bind(pos=self._sync, size=self._sync, orb_scale=self._sync_scale)
        Clock.schedule_once(self._sync, 0)

    def _sync_scale(self, *_):
        cx, cy = self.center
        self._sc.origin = (cx, cy, 0)
        self._sc.x = self.orb_scale
        self._sc.y = self.orb_scale

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._sync_scale()




# ---------------------------------------------------------------------------
# Figma waveform widget — 7 animated bars matching SVG node 927:543 exactly
# ---------------------------------------------------------------------------

class _FigmaWaveform(Widget):
    """Animated waveform that reproduces the exact 7-bar bi:soundwave SVG from Figma.

    Bar x-centres, widths, and base heights are parsed directly from SVG node 927:543
    (viewBox 0 0 46 46). Color is #006BF9 exactly as in Figma. Each bar oscillates
    independently; ``update_bars(t, amplitude)`` is called at ~30 fps.
    """

    # (x_centre, base_height) in the 46×46 SVG viewBox — directly from Figma SVG paths
    _BAR_DATA = [
        (7.185,  8.625),   # outermost left
        (12.935, 14.375),
        (18.685, 22.999),
        (24.435, 34.499),  # centre / tallest
        (30.185, 22.999),
        (35.935, 14.375),
        (41.685, 8.625),   # outermost right
    ]
    _BAR_W  = 2.875    # bar width in viewBox units (= corner radius × 2 → fully-rounded ends)
    _VB     = 46.0     # viewBox side length
    _CY_VB  = 23.0     # y-centre of every bar in the viewBox
    # Symmetric phase offsets so the wave appears to spread outward from the centre
    _PHASES = [3.0, 2.2, 1.4, 0.0, 1.4, 2.2, 3.0]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._bar_rects: list = []
        self._scale:     float = 1.0
        self._bar_w_px:  float = 1.0
        self._bar_cxy:   list  = []
        self._setup_canvas()
        self.bind(pos=self._on_resize, size=self._on_resize)
        Clock.schedule_once(self._on_resize, 0)

    def _setup_canvas(self):
        with self.canvas:
            self._color_inst = Color(0.0, 0.420, 0.976, 1.0)  # #006BF9
            self._bar_rects = [
                RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[0.5])
                for _ in self._BAR_DATA
            ]

    def _on_resize(self, *_):
        w, h = self.size
        px, py = self.pos
        if w <= 0 or h <= 0:
            return
        s  = min(w / self._VB, h / self._VB)
        ox = px + (w - self._VB * s) / 2
        oy = py + (h - self._VB * s) / 2
        self._scale    = s
        self._bar_w_px = self._BAR_W * s
        self._bar_cxy  = [
            (ox + cx * s, oy + self._CY_VB * s)
            for cx, _ in self._BAR_DATA
        ]
        # Render bars at their Figma baseline heights
        r = self._bar_w_px / 2
        for i, rect in enumerate(self._bar_rects):
            cx_px, cy_px = self._bar_cxy[i]
            bh_px = self._BAR_DATA[i][1] * s
            rect.pos    = (cx_px - self._bar_w_px / 2, cy_px - bh_px / 2)
            rect.size   = (self._bar_w_px, bh_px)
            rect.radius = [r]

    def update_bars(self, t: float, amplitude: float) -> None:
        """Animate bar heights at ~30 fps.

        Each bar breathes with a symmetric phase offset.  ``amplitude`` (0–1) scales
        both the peak height *and* the oscillation speed so louder voice → taller +
        faster movement, matching the original design intent.
        """
        if not self._bar_rects or not self._bar_cxy:
            return
        s   = self._scale
        bwp = self._bar_w_px
        r   = bwp / 2
        amp = max(0.0, min(1.0, amplitude))

        for i, rect in enumerate(self._bar_rects):
            cx_px, cy_px = self._bar_cxy[i]
            base_h_px    = self._BAR_DATA[i][1] * s
            phase        = self._PHASES[i]

            # Gentle idle oscillation (always visible even at zero amplitude)
            idle = 1.0 + 0.10 * math.sin(t * 3.0 + phase)
            # Voice-reactive: louder → taller, faster
            vspeed = 5.0 + amp * 12.0
            voice  = amp * 1.2 * abs(math.sin(t * vspeed + phase))

            h_px = base_h_px * idle * (1.0 + voice)
            rect.pos    = (cx_px - bwp / 2, cy_px - h_px / 2)
            rect.size   = (bwp, h_px)
            rect.radius = [r]


# ---------------------------------------------------------------------------
# Label factory
# ---------------------------------------------------------------------------

def _lbl(text, font, size, color, *, bold=False, halign="left", valign="top",
         **kw) -> Label:
    lbl = Label(text=text, font_name=font, font_size=size, bold=bold,
                color=color, halign=halign, valign=valign, **kw)
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _greeting_name(name: str) -> str:
    hour = display_now().hour
    greet = (
        "Good morning" if hour < 12
        else "Good afternoon" if hour < 17
        else "Good evening"
    )
    return f"{greet}, {name or 'there'}"


def _format_next_meeting(nm) -> tuple[str, str]:
    if not nm:
        return "No focus loaded", ""
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
# Card base widget (FloatLayout with rounded bg + border)
# ---------------------------------------------------------------------------

class _TappableCard(ButtonBehavior, FloatLayout):
    """_Card variant that also acts as a button (on_release fires).

    draw_bg=False skips the canvas background so callers can supply their own
    PNG background (avoids double-stacked rounded rectangles).
    """

    def __init__(self, top=None, bot=None, border=None, radius=12, draw_bg=True, **kw):
        _top = kw.pop("bg", top)
        if _top is None:
            _top = _CARD_TOP
        if bot is None:
            bot = _CARD_BOT
        _brd = border if border is not None else _CARD_BORDER
        super().__init__(**kw)
        self._r = radius
        if draw_bg:
            with self.canvas.before:
                Color(1, 1, 1, 1)
                self._bg_rect = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[radius],
                    texture=_grad(_top, bot),
                )
            # Border on top of children so it shows over background images
            with self.canvas.after:
                Color(*_brd)
                self._line = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                    width=1.0,
                )
            self.bind(pos=self._sync, size=self._sync)
        else:
            self._bg_rect = None
            self._line = None

    def _sync(self, *_):
        if self._bg_rect is None:
            return
        r = self._r
        self._bg_rect.pos    = self.pos
        self._bg_rect.size   = self.size
        self._bg_rect.radius = [r]
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


class _Card(FloatLayout):
    def __init__(self, top=None, bot=None, border=None, radius=12, **kw):
        _top = kw.pop("bg", top)
        if _top is None:
            _top = _CARD_TOP
        if bot is None:
            bot = _CARD_BOT
        _brd = border if border is not None else _CARD_BORDER
        super().__init__(**kw)
        self._r = radius
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[radius],
                texture=_grad(_top, bot),
            )
        # Border lives in canvas.after → renders on top of child widgets
        # (e.g. the hero background image won't cover the border line).
        with self.canvas.after:
            Color(*_brd)
            self._line = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=1.0,
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        r = self._r
        self._bg.pos    = self.pos
        self._bg.size   = self.size
        self._bg.radius = [r]
        self._line.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


# ---------------------------------------------------------------------------
# Morning-brief sub-row (dark card within the brief card)
# ---------------------------------------------------------------------------

class _BriefCardRow(_Card):
    def __init__(self, **kw):
        # Brief sub-rows use a solid dark bg (both gradient stops equal)
        kw.setdefault("top",    _ROW_BG)
        kw.setdefault("bot",    _ROW_BG)
        kw.setdefault("border", _ROW_BORDER)
        kw.setdefault("radius", _ff(16.82))
        super().__init__(**kw)


# ---------------------------------------------------------------------------
# Data-holder structs (for backwards-compatible _load_home_summary references)
# ---------------------------------------------------------------------------

class _CardData:
    """Gives .value_label / .text_label / ._more_label access to bottom chips."""
    def __init__(self, value_label, text_label=None):
        self.value_label  = value_label
        self.text_label   = text_label
        self._more_label  = None


class _BriefRowData:
    """Gives .title_label / .subtitle_label access to brief rows."""
    def __init__(self, title: Label, sub: Label):
        self.title_label    = title
        self.subtitle_label = sub


# ===========================================================================
# HomeScreen
# ===========================================================================

class HomeScreen(BaseScreen):
    """Main dashboard — Figma 390:187, 1260 × 800 px."""

    def __init__(self, **kw):
        super().__init__(**kw)

        # Public label refs (accessed by business-logic helpers)
        self.greeting_label:      Label | None = None
        self._big_clock_hm:       Label | None = None
        self.date_label:          Label | None = None
        self.health_label:        Label | None = None  # temperature / offline
        self._wx_condition:       Label | None = None
        self.next_time_label:     Label | None = None
        self.next_title_label:    Label | None = None
        self.more_label:          Label | None = None
        self.voice_dot:           Label | None = None
        self.voice_state_label:   Label | None = None
        self.last_title_label:    Label | None = None
        self.last_meta_label:     Label | None = None
        self.last_actions_label:  Label | None = None
        self.schedule_card:       _CardData | None = None
        self.email_card:          _CardData | None = None
        self.tasks_card:          _CardData | None = None
        self.brief_calendar_label: _BriefRowData | None = None
        self.brief_email_label:   _BriefRowData | None = None
        self._brief_wx_title:     Label | None = None
        self._brief_wx_sub:       Label | None = None
        self._latest_meeting_id:  str | None = None
        self._health_label_offline: bool = False
        self._footer_kwargs:      dict | None = None

        # Clock / event handles
        self._clock_event:       object | None = None
        self._footer_ip_event:   object | None = None
        self._voice_state_event: object | None = None
        self._summary_poll_event: object | None = None

        # Voice interaction widgets and state
        self._listening_pill:    object | None = None  # the pill _Card
        self._soundwave_wf:      _FigmaWaveform | None = None
        self._voice_orb:         _ScalableImage | None = None
        self._listening_active:  bool = False
        self._current_amplitude: float = 0.0
        self._soundwave_tick_ev: object | None = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # Solid background #01081A
        with root.canvas.before:
            Color(0.004, 0.031, 0.102, 1.0)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(self._bg_rect, "pos", v),
                  size=lambda w, v: setattr(self._bg_rect, "size", v))

        self._build_header(root)
        self._build_hero_card(root)
        self._build_summary_card(root)
        self._build_brief_card(root)
        self._build_schedule_card(root)
        self._build_email_card(root)
        self._build_tasks_card(root)
        self._build_say_bar(root)

        self.add_widget(root)

    # -----------------------------------------------------------------------
    # Header  (y = 21.19 … 97.47)
    # -----------------------------------------------------------------------

    def _build_header(self, root: FloatLayout) -> None:
        # No back button — home is the root screen; nothing to go back to.

        # Greeting  (24.01, 33.9)  — shifted left to fill the space left by the
        # removed back badge. Figma original x=122.89 assumed a back-badge on the
        # left; we start from x=24.01 now so the text uses the full left margin.
        self.greeting_label = _lbl(
            _greeting_name(None), _FONT_SB, _ff(42.38), _WHITE,
            size_hint=(_sw(700), _sh(51)),
            pos_hint={"x": _x(24.01), "y": _y(33.9, 51)},
        )
        root.add_widget(self.greeting_label)

        # Listening pill  (805.16, 21.19)  302.29 × 76.28
        self._build_listening_pill(root)

        # Settings badge  (1159.71, 21.19)  76.28 × 76.28  — tappable → settings screen
        sg_src = _fp("icon_settings.png")
        if sg_src:
            sg_btn = _TappableCard(
                draw_bg=False,
                size_hint=(_sw(76.28), _sh(76.28)),
                pos_hint={"x": _x(1159.71), "y": _y(21.19, 76.28)},
            )
            sg_btn.add_widget(Image(
                source=sg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="contain",
            ))
            sg_btn.bind(on_release=lambda *_: self.goto("settings"))
            root.add_widget(sg_btn)

    def _build_listening_pill(self, root: FloatLayout) -> None:
        """Voice-state pill  (805.16, 21.19)  302.29 × 76.28.

        Uses the exact Figma PNG (listening_pill_figma.png) as the pill background.
        An animated _FigmaWaveform is overlaid at the soundwave position.
        Hidden by default (opacity=0); shown via show_listening_state().
        Display-only — not tappable.
        """
        PW, PH = 302.29, 76.28

        # Outer container — no canvas bg; the PNG provides the visual
        pill = FloatLayout(
            size_hint=(_sw(PW), _sh(PH)),
            pos_hint={"x": _x(805.16), "y": _y(21.19, PH)},
        )
        pill.opacity = 0.0  # hidden in idle state

        # Figma pill PNG as the background (covers the full pill area)
        pill_src = _fp("listening_pill_figma.png") or _fp("listening_pill.png")
        if pill_src:
            pill.add_widget(Image(
                source=pill_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # Animated soundwave bars overlaid at the soundwave position (224.6, 15.54) 45.2×45.2
        sw_wf = _FigmaWaveform(
            size_hint=(45.2 / PW, 45.2 / PH),
            pos_hint={"x": 224.6 / PW, "y": (PH - 15.54 - 45.2) / PH},
        )
        pill.add_widget(sw_wf)
        self._soundwave_wf = sw_wf

        # Keep label refs for any callers that update content
        self.voice_dot = None
        self.voice_state_label = None

        self._listening_pill = pill
        root.add_widget(pill)

    # -----------------------------------------------------------------------
    # Hero card  (24.01, 114.42)  579.15 × 372.03
    # -----------------------------------------------------------------------

    def _build_hero_card(self, root: FloatLayout) -> None:
        CW, CH = 579.15, 372.03
        # Hero card — Figma fill is solid #010C25; gradient runs only in the border.
        card = _Card(top=_HERO_BG, bot=_HERO_BG, border=_CARD_BORDER,
                     radius=_ff(19.48),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(24.01), "y": _y(114.42, CH)})

        # Background landscape image at (-10.39, -1.3) in card  599.92 × 375.28
        hero_src = _fp("hero_bg.png") or _fp("hero_background.png")
        if hero_src:
            card.add_widget(Image(
                source=hero_src,
                size_hint=(599.92 / CW, 375.28 / CH),
                pos_hint={"x": -10.39 / CW, "y": (CH - (-1.3) - 375.28) / CH},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # Clock + AM/PM in one markup label so they always appear flush together.
        # Figma: clock at (29.87, 34.07) 154×77 Bold 64.93px; AM at 202.58, 74.32.
        # valign="top" so the text starts exactly at the container top (Figma y=34.07).
        self._big_clock_hm = Label(
            text=(
                f"[b][size={_ff(64.93)}]--:--[/size][/b]"
                f"[size={_ff(22.72)}][color=B6BAF2] --[/color][/size]"
            ),
            markup=True,
            font_name=_FONT,
            color=_WHITE,
            halign="left",
            valign="top",
            size_hint=(250 / CW, 80 / CH),
            pos_hint={"x": 29.87 / CW, "y": (CH - 34.07 - 80) / CH},
        )
        self._big_clock_hm.bind(size=self._big_clock_hm.setter("text_size"))
        card.add_widget(self._big_clock_hm)

        # Date  (0, 77.26) in group → abs (29.87, 111.33)  150 × 23  SemiBold 19.48px
        self.date_label = _lbl(
            "", _FONT_SB, _ff(19.48 * 1.2), _WHITE,
            size_hint=(240 / CW, 28 / CH),
            pos_hint={"x": 29.87 / CW, "y": (CH - 111.33 - 28) / CH},
        )
        card.add_widget(self.date_label)

        # Weather group at (439.56, 44.46) in card
        # Sun icon  (0, 0) in group → abs (439.56, 44.46)  58.7 × 58.7
        sun_src = _fp("icon_sun_brief.png") or _fp("icon_sun.png")
        if sun_src:
            card.add_widget(Image(
                source=sun_src,
                size_hint=(58.7 / CW, 58.7 / CH),
                pos_hint={"x": 439.56 / CW, "y": (CH - 44.46 - 58.7) / CH},
                fit_mode="contain",
            ))

        # Temperature  (53.24, 7.14) in group → abs (492.80, 51.60)  53 × 27  Bold 22.72px
        self.health_label = _lbl(
            "--°C", _FONT, _ff(22.72), _WHITE, bold=True,
            size_hint=(100 / CW, 27 / CH),
            pos_hint={"x": 492.80 / CW, "y": (CH - 51.60 - 27) / CH},
        )
        card.add_widget(self.health_label)

        # Condition  (53.24, 38.30) in group → abs (492.80, 82.76)  56 × 23  Medium 19.48px
        self._wx_condition = _lbl(
            "--", _FONT_MD, _ff(19.48), _MUTED,
            size_hint=(100 / CW, 23 / CH),
            pos_hint={"x": 492.80 / CW, "y": (CH - 82.76 - 23) / CH},
        )
        card.add_widget(self._wx_condition)

        # Recording button (277.24, 235.68) in card  268.39 × 108.26
        self._build_hero_rec_btn(card, CW, CH)

        # Next-up group at (29.87, 216.2) in card
        self._build_hero_next_up(card, CW, CH)

        root.add_widget(card)

    def _build_hero_rec_btn(self, card: FloatLayout, CW: float, CH: float) -> None:
        BW, BH = 268.39, 108.26
        BX, BY = 277.24, 235.68

        # Use the exact Figma PNG if available — skip canvas gradient so we don't double-stack.
        bg_src = _fp("recording_btn_bg.png")

        btn_card = _TappableCard(
            top=_REC_TOP, bot=_REC_BOT,
            border=(0.012, 0.306, 0.886, 1.0),
            radius=_ff(19.45),
            draw_bg=(not bool(bg_src)),   # canvas bg only when no PNG
            size_hint=(BW / CW, BH / CH),
            pos_hint={"x": BX / CW, "y": (CH - BY - BH) / CH},
        )
        btn_card.bind(on_release=self._on_start_recording)
        self._rec_btn = btn_card

        if bg_src:
            btn_card.add_widget(Image(
                source=bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # Mic orb  (17.5, 20.74) in button  65.48 × 65.48
        orb_src = _fp("mic_orb_mini.png")
        if orb_src:
            btn_card.add_widget(Image(
                source=orb_src,
                size_hint=(65.48 / BW, 65.48 / BH),
                pos_hint={"x": 17.5 / BW, "y": (BH - 20.74 - 65.48) / BH},
                fit_mode="contain",
            ))

        # "Start Recording"  (106.96, 31.76) in button  142 × 23  Bold 19.45px
        btn_card.add_widget(_lbl(
            "Start Recording", _FONT, _ff(19.45 * 1.2), _WHITE, bold=True,
            size_hint=(200 / BW, 28 / BH),
            pos_hint={"x": 106.96 / BW, "y": (BH - 31.76 - 28) / BH},
        ))

        # Subtitle  (95.94, 60.94) in button  164 × 15  SemiBold 12.97px
        btn_card.add_widget(_lbl(
            'Tap or say "start recording"', _FONT_SB, _ff(12.97 * 1.2), _WHITE,
            size_hint=(200 / BW, 19 / BH),
            pos_hint={"x": 95.94 / BW, "y": (BH - 60.94 - 19) / BH},
        ))

        card.add_widget(btn_card)

    def _build_hero_next_up(self, card: FloatLayout, CW: float, CH: float) -> None:
        # Group at (29.87, 216.2) in card  183 × 128.48
        GX, GY = 29.87, 216.2

        # "Next up"  (0, 0) in group  65 × 22  SemiBold 18.18px  #3481F1
        card.add_widget(_lbl(
            "Next up", _FONT_SB, _ff(18.18), _BLUE2,
            size_hint=(100 / CW, 22 / CH),
            pos_hint={"x": GX / CW, "y": (CH - GY - 22) / CH},
        ))

        # Calendar icon  (0, 38.96) in group → abs (29.87, 255.16)  31.18 × 30.82
        cal_src = _fp("icon_calendar_row.png") or _fp("icon_calendar.png")
        if cal_src:
            card.add_widget(Image(
                source=cal_src,
                size_hint=(31.18 / CW, 30.82 / CH),
                pos_hint={"x": GX / CW, "y": (CH - (GY + 38.96) - 30.82) / CH},
                fit_mode="contain",
            ))

        # "11:00 AM"  (34.41, 40.25) in group → abs (64.28, 256.45)  78 × 22  SemiBold 18.18px
        self.next_time_label = _lbl(
            "--:-- --", _FONT_SB, _ff(18.18), _BLUE2,
            size_hint=(160 / CW, 22 / CH),
            pos_hint={"x": (GX + 34.41) / CW, "y": (CH - (GY + 40.25) - 22) / CH},
        )
        card.add_widget(self.next_time_label)

        # "Now: Product Sync"  (0, 73.37) in group → abs (29.87, 289.57)  183 × 24  Bold 20.13px
        self.next_title_label = _lbl(
            "--", _FONT, _ff(20.13), _WHITE, bold=True,
            size_hint=(240 / CW, 24 / CH),
            pos_hint={"x": GX / CW, "y": (CH - (GY + 73.37) - 24) / CH},
        )
        card.add_widget(self.next_title_label)

        # "+2 more"  (0, 106.48) in group → abs (29.87, 322.68)  70 × 22  Bold 18.18px
        self.more_label = _lbl(
            "", _FONT, _ff(18.18), _BLUE2, bold=True,
            size_hint=(120 / CW, 22 / CH),
            pos_hint={"x": GX / CW, "y": (CH - (GY + 106.48) - 22) / CH},
        )
        card.add_widget(self.more_label)

        # Transparent tappable overlay covering the entire next-up group
        # (calendar icon + time + title + "+N more" area → navigate to calendar)
        tap = _TappableCard(
            draw_bg=False,
            size_hint=(260 / CW, 140 / CH),
            pos_hint={"x": GX / CW, "y": (CH - GY - 140) / CH},
        )
        tap.bind(on_release=lambda *_: self.goto("calendar", transition="slide_left"))
        card.add_widget(tap)

    # -----------------------------------------------------------------------
    # Meeting summary card  (611.64, 114.42)  307.94 × 371.5
    # -----------------------------------------------------------------------

    def _build_summary_card(self, root: FloatLayout) -> None:
        CW, CH = 307.94, 371.5
        card = _Card(radius=_ff(16.95),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(611.64), "y": _y(114.42, CH)})
        def _card_touch(w, t):
            lx, ly = w.to_widget(t.x, t.y)
            if w.collide_point(lx, ly):
                self._open_latest_meeting()
                return True
            return False
        card.bind(on_touch_up=_card_touch)

        # File icon  (21.19, 50.85) abs in card  39.55 × 39.55
        fi_src = _fp("icon_file.png")
        if fi_src:
            card.add_widget(Image(
                source=fi_src,
                size_hint=(39.55 / CW, 39.55 / CH),
                pos_hint={"x": 21.19 / CW, "y": (CH - 50.85 - 39.55) / CH},
                fit_mode="contain",
            ))

        # "Last Meeting Summary"  (62.15, 57.91) abs in card  224 × 25  SemiBold 21.19px  #A4A4AC
        card.add_widget(_lbl(
            "Last Meeting Summary", _FONT_SB, _ff(21.19 * 1.2), _GREY,
            size_hint=(240 / CW, 30 / CH),
            pos_hint={"x": 62.15 / CW, "y": (CH - 57.91 - 30) / CH},
        ))

        # Title group at (28.25, 104.53) in card
        # "Product Sync"  200 × 39  SemiBold 32.49px  #FFF
        self.last_title_label = _lbl(
            "--", _FONT_SB, _ff(32.49 * 1.2), _WHITE,
            size_hint=(240 / CW, 46 / CH),
            pos_hint={"x": 28.25 / CW, "y": (CH - 104.53 - 46) / CH},
        )
        card.add_widget(self.last_title_label)

        # "Today, 10:00 AM"  (28.25, 153.97)  164 × 25  SemiBold 21.19px  #B6BAF2
        self.last_meta_label = _lbl(
            "--", _FONT_SB, _ff(21.19 * 1.2), _MUTED,
            size_hint=(260 / CW, 30 / CH),
            pos_hint={"x": 28.25 / CW, "y": (CH - 153.97 - 30) / CH},
        )
        card.add_widget(self.last_meta_label)

        # "Open summary"  (28.25, 238.72)  137 × 25  SemiBold 21.19px  #006FFF
        self.last_actions_label = _lbl(
            "Open summary", _FONT_SB, _ff(21.19 * 1.2), _BLUE,
            size_hint=(180 / CW, 30 / CH),
            pos_hint={"x": 28.25 / CW, "y": (CH - 238.72 - 30) / CH},
        )
        card.add_widget(self.last_actions_label)

        # Arrow beside "Open summary"
        _open_sum_arr = _fp("icon_arrow.png")
        if _open_sum_arr:
            card.add_widget(Image(
                source=_open_sum_arr,
                size_hint=(12 / CW, 22 / CH),
                pos_hint={"x": (28.25 + 175) / CW, "y": (CH - 238.72 - 26) / CH},
                fit_mode="contain",
            ))

        # Avatar 1  (28.25, 274.04)  46.19 × 46.04  — circular via Ellipse texture
        av1_src = _fp("avatar_1.png") or _fp("avatar_photo_1.png")
        if av1_src:
            card.add_widget(_CircularImg(
                source=av1_src,
                size_hint=(46.19 / CW, 46.04 / CH),
                pos_hint={"x": 28.25 / CW, "y": (CH - 274.04 - 46.04) / CH},
            ))

        # Avatar 2  (85.26, 274.99)  46.19 × 46.04  — circular
        av2_src = _fp("avatar_2.png") or _fp("avatar_photo_2.png")
        if av2_src:
            card.add_widget(_CircularImg(
                source=av2_src,
                size_hint=(46.19 / CW, 46.04 / CH),
                pos_hint={"x": 85.26 / CW, "y": (CH - 274.99 - 46.04) / CH},
            ))

        # "+2 badge"  (142.27, 274.04)  45.61 × 45.61
        badge = _Card(bg=(0.004, 0.039, 0.106, 1.0),
                      border=_CARD_BORDER,
                      radius=_ff(23),
                      size_hint=(45.61 / CW, 45.61 / CH),
                      pos_hint={"x": 142.27 / CW, "y": (CH - 274.04 - 45.61) / CH})
        badge.add_widget(_lbl(
            "+2", _FONT_MD, _ff(21.17), _WHITE,
            halign="center", valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        ))
        card.add_widget(badge)

        root.add_widget(card)

    # -----------------------------------------------------------------------
    # Morning Brief card  (928.05, 114.42)  307.94 × 371.5
    # -----------------------------------------------------------------------

    def _build_brief_card(self, root: FloatLayout) -> None:
        CW, CH = 307.94, 371.5
        card = _Card(radius=_ff(16.95),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(928.05), "y": _y(114.42, CH)})

        # Header: sun icon (29.66, 14.13)  28.25 × 28.25  +  "Morning Brief" (72.04, 14.13)
        msun_src = _fp("icon_sun_brief.png")
        if msun_src:
            card.add_widget(Image(
                source=msun_src,
                size_hint=(28.25 / CW, 28.25 / CH),
                pos_hint={"x": 29.66 / CW, "y": (CH - 14.13 - 28.25) / CH},
                fit_mode="contain",
            ))
        card.add_widget(_lbl(
            "Morning Brief", _FONT_SB, _ff(24.01 * 1.2), _GREY,
            size_hint=(200 / CW, 35 / CH),
            pos_hint={"x": 72.04 / CW, "y": (CH - 14.13 - 35) / CH},
        ))

        # --- Calendar row (9.89, 57.91)  288.16 × 73.45 ---
        RW, RH = 288.16, 73.45
        cal_row = _BriefCardRow(
            size_hint=(RW / CW, RH / CH),
            pos_hint={"x": 9.89 / CW, "y": (CH - 57.91 - RH) / CH},
        )
        cal_icon_src = _fp("icon_calendar_row.png")
        if cal_icon_src:
            cal_row.add_widget(Image(
                source=cal_icon_src,
                size_hint=(47.46 / RW, 42.85 / RH),
                pos_hint={"x": 20.34 / RW, "y": (RH - 15.3 - 42.85) / RH},
                fit_mode="contain",
            ))
        brief_cal_title = _lbl(
            "Briefing ready", _FONT, _ff(21.19 * 1.2), _GREY, bold=True,
            size_hint=(210 / RW, 30 / RH),
            pos_hint={"x": 81.37 / RW, "y": (RH - 13.77 - 30) / RH},
        )
        brief_cal_sub = _lbl(
            "Ask Tony for a briefing", _FONT_SB, _ff(16.95 * 1.2), _MUTED,
            size_hint=(180 / RW, 24 / RH),
            pos_hint={"x": 81.37 / RW, "y": (RH - 41.32 - 24) / RH},
        )
        cal_row.add_widget(brief_cal_title)
        cal_row.add_widget(brief_cal_sub)
        card.add_widget(cal_row)
        self.brief_calendar_label = _BriefRowData(brief_cal_title, brief_cal_sub)

        # --- Weather row (9.89, 137.02)  288.16 × 73.45 ---
        wx_row = _BriefCardRow(
            size_hint=(RW / CW, RH / CH),
            pos_hint={"x": 9.89 / CW, "y": (CH - 137.02 - RH) / CH},
        )
        wx_icon_src = _fp("icon_weather.png")
        if wx_icon_src:
            wx_row.add_widget(Image(
                source=wx_icon_src,
                size_hint=(36.8 / RW, 36.8 / RH),
                pos_hint={"x": 13.76 / RW, "y": (RH - 10.7 - 36.8) / RH},
                fit_mode="contain",
            ))
        self._brief_wx_title = _lbl(
            "Weather: --°C", _FONT, _ff(21.19 * 1.2), _GREY, bold=True,
            size_hint=(195 / RW, 28 / RH),
            pos_hint={"x": 81.93 / RW, "y": (RH - 15.54 - 28) / RH},
        )
        self._brief_wx_sub = _lbl(
            "--", _FONT_SB, _ff(16.95 * 1.2), _MUTED,
            size_hint=(100 / RW, 24 / RH),
            pos_hint={"x": 81.37 / RW, "y": (RH - 39.79 - 24) / RH},
        )
        wx_row.add_widget(self._brief_wx_title)
        wx_row.add_widget(self._brief_wx_sub)
        card.add_widget(wx_row)

        # --- Email row (9.89, 216.12)  288.16 × 125.72 ---
        ER, EH = 288.16, 125.72
        em_row = _BriefCardRow(
            size_hint=(ER / CW, EH / CH),
            pos_hint={"x": 9.89 / CW, "y": (CH - 216.12 - EH) / CH},
        )
        em_icon_src = _fp("icon_email.png")
        if em_icon_src:
            em_row.add_widget(Image(
                source=em_icon_src,
                size_hint=(32.47 / ER, 32.47 / EH),
                pos_hint={"x": 13.76 / ER, "y": (EH - 36.69 - 32.47) / EH},
                fit_mode="contain",
            ))
        brief_em_title = _lbl(
            "email:", _FONT, _ff(21.19 * 1.2), _WHITE, bold=True,
            size_hint=(100 / ER, 30 / EH),
            pos_hint={"x": 81.37 / ER, "y": (EH - 21.46 - 30) / EH},
        )
        brief_em_sub = _lbl(
            "Connect Gmail for updates", _FONT_SB, _ff(16.95 * 1.2), _MUTED,
            size_hint=(210 / ER, 24 / EH),
            pos_hint={"x": 81.37 / ER, "y": (EH - 49.44 - 24) / EH},
        )
        em_row.add_widget(brief_em_title)
        em_row.add_widget(brief_em_sub)
        card.add_widget(em_row)
        self.brief_email_label = _BriefRowData(brief_em_title, brief_em_sub)

        # "View all"  (114.42, 346.08)  tappable → opens morning_brief screen
        view_all_tap = _TappableCard(
            size_hint=(90 / CW, 28 / CH),
            pos_hint={"x": 107.0 / CW, "y": (CH - 344.0 - 28) / CH},
        )
        view_all_tap.add_widget(_lbl(
            "View all", _FONT_SB, _ff(15.54 * 1.2), _BLUE,
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
        ))
        view_all_tap.bind(
            on_release=lambda *_: self.goto("morning_brief", transition="slide_left"))
        card.add_widget(view_all_tap)

        # Arrow icon  (193.52, 344.66)  11.3 × 22.6
        arr_src = _fp("icon_arrow.png")
        if arr_src:
            card.add_widget(Image(
                source=arr_src,
                size_hint=(11.3 / CW, 22.6 / CH),
                pos_hint={"x": 193.52 / CW, "y": (CH - 344.66 - 22.6) / CH},
                fit_mode="contain",
            ))

        root.add_widget(card)

    # -----------------------------------------------------------------------
    # Bottom chips
    # -----------------------------------------------------------------------

    def _build_bottom_chip(self, root, fig_x, fig_w, icon_src, icon_size,
                           icon_abs_x, icon_abs_y,
                           value_text, value_x, value_y,
                           label_text, label_x, label_y,
                           label_w=150, arrow_x=None, arrow_y=None,
                           more_x=None, more_y=None, nextup_x=None, nextup_y=None,
                           nextup_w=200, radius=22.6) -> _CardData:
        """Generic bottom chip builder."""
        CW, CH = fig_w, 144.08
        FIG_Y  = 507.11

        card = _Card(radius=_ff(radius),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(fig_x), "y": _y(FIG_Y, CH)})

        # Icon circle
        if icon_src:
            card.add_widget(Image(
                source=icon_src,
                size_hint=(icon_size / CW, icon_size / CH),
                pos_hint={"x": icon_abs_x / CW, "y": (CH - icon_abs_y - icon_size) / CH},
                fit_mode="contain",
            ))

        # Value (number)
        val_lbl = _lbl(
            value_text, _FONT_SB, _ff(38.14), _WHITE,
            size_hint=(80 / CW, 45 / CH),
            pos_hint={"x": value_x / CW, "y": (CH - value_y - 45) / CH},
        )
        card.add_widget(val_lbl)

        # Label
        txt_lbl = _lbl(
            label_text, _FONT_SB, _ff(25.43), _MUTED,
            size_hint=(label_w / CW, 30 / CH),
            pos_hint={"x": label_x / CW, "y": (CH - label_y - 30) / CH},
        )
        card.add_widget(txt_lbl)

        # Arrow
        arr_src = _fp("icon_arrow.png")
        if arr_src and arrow_x is not None:
            card.add_widget(Image(
                source=arr_src,
                size_hint=(19.78 / CW, 39.55 / CH),
                pos_hint={"x": arrow_x / CW, "y": (CH - arrow_y - 39.55) / CH},
                fit_mode="contain",
            ))

        data = _CardData(val_lbl, txt_lbl)

        # Optional "+N more" label (schedule card)
        if more_x is not None:
            more_lbl = _lbl(
                "", _FONT_SB, _ff(21.19), _BLUE,
                size_hint=(40 / CW, 25 / CH),
                pos_hint={"x": more_x / CW, "y": (CH - more_y - 25) / CH},
            )
            card.add_widget(more_lbl)
            data._more_label = more_lbl

        # Optional secondary time label (schedule card: "Now: ...")
        if nextup_x is not None:
            next_lbl = _lbl(
                "", _FONT_SB, _ff(22.6), _MUTED,
                size_hint=(nextup_w / CW, 27 / CH),
                pos_hint={"x": nextup_x / CW, "y": (CH - nextup_y - 27) / CH},
            )
            card.add_widget(next_lbl)
            data.text_label = next_lbl

        root.add_widget(card)
        return data

    def _build_schedule_card(self, root: FloatLayout) -> None:
        # (24.01, 507.11)  509.93 × 144.08
        # inner group at (40.96, 16.95): icon at (0, 9.89), value at (115.83, 0),
        # subtitle at (115.83, 50.85), +2 at (115.83, 84.75)
        CW, CH = 509.93, 144.08
        FIG_Y  = 507.11
        card = _Card(radius=_ff(22.6),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(24.01), "y": _y(FIG_Y, CH)})

        icon_src = _fp("icon_schedule_circle.png")
        if icon_src:
            card.add_widget(Image(
                source=icon_src,
                size_hint=(93.23 / CW, 93.23 / CH),
                pos_hint={"x": 40.96 / CW, "y": (CH - 26.84 - 93.23) / CH},
                fit_mode="contain",
            ))

        # Time value "11:00"
        val_lbl = _lbl(
            "--:--", _FONT_SB, _ff(38.14), _WHITE,
            size_hint=(110 / CW, 45 / CH),
            pos_hint={"x": (40.96 + 115.83) / CW,
                      "y": (CH - 16.95 - 45) / CH},
        )
        card.add_widget(val_lbl)

        # "+2 more" labels
        plus_lbl = _lbl(
            "", _FONT_SB, _ff(21.19), _BLUE,
            size_hint=(30 / CW, 25 / CH),
            pos_hint={"x": (40.96 + 115.83) / CW,
                      "y": (CH - 16.95 - 84.75 - 25) / CH},
        )
        card.add_widget(plus_lbl)
        more_lbl = _lbl(
            "", _FONT_SB, _ff(21.19), _BLUE,
            size_hint=(60 / CW, 25 / CH),
            pos_hint={"x": (40.96 + 146.9) / CW,
                      "y": (CH - 16.95 - 84.75 - 25) / CH},
        )
        card.add_widget(more_lbl)

        # Subtitle "Now: Product Sync"
        txt_lbl = _lbl(
            "--", _FONT_SB, _ff(22.6 * 1.2), _MUTED,
            size_hint=(240 / CW, 32 / CH),
            pos_hint={"x": (40.96 + 115.83) / CW,
                      "y": (CH - 16.95 - 50.85 - 32) / CH},
        )
        card.add_widget(txt_lbl)

        # Arrow
        arr_src = _fp("icon_arrow.png")
        if arr_src:
            card.add_widget(Image(
                source=arr_src,
                size_hint=(19.78 / CW, 39.55 / CH),
                pos_hint={"x": (40.96 + 408.23) / CW,
                          "y": (CH - 16.95 - 45.2 - 39.55) / CH},
                fit_mode="contain",
            ))

        def _sched_touch(w, t):
            lx, ly = w.to_widget(t.x, t.y)
            if w.collide_point(lx, ly):
                self.goto("calendar", transition="slide_left")
                return True
            return False
        card.bind(on_touch_up=_sched_touch)

        root.add_widget(card)

        data = _CardData(val_lbl, txt_lbl)
        data._more_label = more_lbl
        self.schedule_card = data

    def _build_email_card(self, root: FloatLayout) -> None:
        # (542.42, 507.11)  334.78 × 144.08
        CW, CH = 334.78, 144.08
        FIG_Y  = 507.11
        card = _Card(radius=_ff(22.6),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(542.42), "y": _y(FIG_Y, CH)})

        icon_src = _fp("icon_email_circle.png")
        if icon_src:
            card.add_widget(Image(
                source=icon_src,
                size_hint=(93.23 / CW, 93.23 / CH),
                pos_hint={"x": 22.6 / CW, "y": (CH - 25.43 - 93.23) / CH},
                fit_mode="contain",
            ))

        val_lbl = _lbl(
            "—", _FONT_SB, _ff(38.14), _WHITE,
            size_hint=(80 / CW, 45 / CH),
            pos_hint={"x": (22.6 + 115.83) / CW,
                      "y": (CH - 25.43 - 11.3 - 45) / CH},
        )
        card.add_widget(val_lbl)

        txt_lbl = _lbl(
            "New emails", _FONT_SB, _ff(25.43 * 1.2), _MUTED,
            size_hint=(160 / CW, 36 / CH),
            pos_hint={"x": (22.6 + 115.83) / CW,
                      "y": (CH - 25.43 - 60.74 - 36) / CH},
        )
        card.add_widget(txt_lbl)

        arr_src = _fp("icon_arrow.png")
        if arr_src:
            card.add_widget(Image(
                source=arr_src,
                size_hint=(19.78 / CW, 39.55 / CH),
                pos_hint={"x": (22.6 + 269.8) / CW,
                          "y": (CH - 25.43 - 31.08 - 39.55) / CH},
                fit_mode="contain",
            ))

        def _email_touch(w, t):
            lx, ly = w.to_widget(t.x, t.y)
            if w.collide_point(lx, ly):
                self.goto("emails", transition="slide_left")
                return True
            return False
        card.bind(on_touch_up=_email_touch)
        root.add_widget(card)
        self.email_card = _CardData(val_lbl, txt_lbl)

    def _build_tasks_card(self, root: FloatLayout) -> None:
        # (885.67, 507.11)  350.31 × 144.08
        CW, CH = 350.31, 144.08
        FIG_Y  = 507.11
        card = _Card(radius=_ff(22.6),
                     size_hint=(_sw(CW), _sh(CH)),
                     pos_hint={"x": _x(885.67), "y": _y(FIG_Y, CH)})

        icon_src = _fp("icon_tasks_circle.png")
        if icon_src:
            card.add_widget(Image(
                source=icon_src,
                size_hint=(93.23 / CW, 93.23 / CH),
                pos_hint={"x": 36.73 / CW, "y": (CH - 25.43 - 93.23) / CH},
                fit_mode="contain",
            ))

        val_lbl = _lbl(
            "—", _FONT_SB, _ff(38.14), _WHITE,
            size_hint=(80 / CW, 45 / CH),
            pos_hint={"x": (36.73 + 115.83) / CW,
                      "y": (CH - 25.43 - 7.06 - 45) / CH},
        )
        card.add_widget(val_lbl)

        txt_lbl = _lbl(
            "Tasks due", _FONT_SB, _ff(25.43 * 1.2), _MUTED,
            size_hint=(150 / CW, 36 / CH),
            pos_hint={"x": (36.73 + 115.83) / CW,
                      "y": (CH - 25.43 - 56.5 - 36) / CH},
        )
        card.add_widget(txt_lbl)

        arr_src = _fp("icon_arrow.png")
        if arr_src:
            card.add_widget(Image(
                source=arr_src,
                size_hint=(19.78 / CW, 39.55 / CH),
                pos_hint={"x": (36.73 + 257.09) / CW,
                          "y": (CH - 25.43 - 21.19 - 39.55) / CH},
                fit_mode="contain",
            ))

        root.add_widget(card)
        self.tasks_card = _CardData(val_lbl, txt_lbl)

    # -----------------------------------------------------------------------
    # Try saying bar  (38.14, 672.38)  1183.72 × 100.29
    # -----------------------------------------------------------------------

    def _build_say_bar(self, root: FloatLayout) -> None:
        BW, BH = 1183.72, 100.29
        bar = _Card(radius=_ff(29.66),
                    size_hint=(_sw(BW), _sh(BH)),
                    pos_hint={"x": _x(38.14), "y": _y(672.38, BH)})

        # Sparkle vector  (22.6, 32.49) abs in bar  33.67 × 33.66
        sp_src = _fp("icon_sparkle.png") or _fp("icon_sparkle_layer.png")
        if sp_src:
            bar.add_widget(Image(
                source=sp_src,
                size_hint=(33.67 / BW, 33.66 / BH),
                pos_hint={"x": 22.6 / BW, "y": (BH - 32.49 - 33.66) / BH},
                fit_mode="contain",
            ))

        # "+" at (46.49, 50.73) abs in bar  14 × 27  Bold 22.6px  #1B76FA
        bar.add_widget(_lbl(
            "+", _FONT, _ff(22.6), (0.106, 0.463, 0.980, 1.0), bold=True,
            size_hint=(20 / BW, 27 / BH),
            pos_hint={"x": 46.49 / BW, "y": (BH - 50.73 - 27) / BH},
        ))

        # "Try saying"  (80.51, 15.54) abs in bar  127 × 32  SemiBold 26.84px  #006BF9
        bar.add_widget(_lbl(
            "Try saying", _FONT_SB, _ff(26.84), _BLUE,
            size_hint=(160 / BW, 32 / BH),
            pos_hint={"x": 80.51 / BW, "y": (BH - 15.54 - 32) / BH},
        ))

        # Prompt  (80.51, 56.50) abs in bar  416 × 27  SemiBold 22.6px  #B6BAF2
        bar.add_widget(_lbl(
            '"Schedule a meeting tomorrow at 4 PM"',
            _FONT_SB, _ff(22.6 * 1.2), _MUTED,
            size_hint=(500 / BW, 33 / BH),
            pos_hint={"x": 80.51 / BW, "y": (BH - 56.50 - 33) / BH},
        ))

        # Voice orb  (591.86, 4.24) abs in bar  91.82 × 91.82
        # Uses _ScalableImage so wake-word scale animation doesn't shift layout.
        orb_src = _fp("icon_voice_orb.png") or _fp("icon_voice_orb_bar.png")
        if orb_src:
            voice_orb = _ScalableImage(
                source=orb_src,
                size_hint=(91.82 / BW, 91.82 / BH),
                pos_hint={"x": 591.86 / BW, "y": (BH - 4.24 - 91.82) / BH},
            )
            bar.add_widget(voice_orb)
            self._voice_orb = voice_orb

        # Transparent tap target on top of voice orb — same pos/size as orb
        orb_tap = _TappableCard(
            draw_bg=False,
            size_hint=(91.82 / BW, 91.82 / BH),
            pos_hint={"x": 591.86 / BW, "y": (BH - 4.24 - 91.82) / BH},
        )
        orb_tap.bind(on_release=self._on_mic_orb_tapped)
        bar.add_widget(orb_tap)

        # Keyboard badge  (1084.84, 16.95) abs in bar  76.28 × 67.8
        kb_src = _fp("icon_keyboard.png")
        if kb_src:
            bar.add_widget(Image(
                source=kb_src,
                size_hint=(76.28 / BW, 67.8 / BH),
                pos_hint={"x": 1084.84 / BW, "y": (BH - 16.95 - 67.8) / BH},
                fit_mode="contain",
            ))

        root.add_widget(bar)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def on_enter(self):
        # Ensure listening state is reset to idle each time we arrive at home
        self._listening_active = False
        self._current_amplitude = 0.0
        if self._soundwave_tick_ev is not None:
            self._soundwave_tick_ev.cancel()
            self._soundwave_tick_ev = None
        if self._listening_pill is not None:
            Animation.cancel_all(self._listening_pill, 'opacity')
            self._listening_pill.opacity = 0.0
        if self._voice_orb is not None:
            Animation.cancel_all(self._voice_orb, 'orb_scale')
            self._voice_orb.orb_scale = 1.0
        if self._soundwave_wf is not None:
            self._soundwave_wf.update_bars(time.monotonic(), 0.0)

        self._update_clock_labels()
        snap = get_weather_client().snapshot
        if snap:
            self._on_weather_snapshot(snap)
        get_weather_client().subscribe(self._on_weather_snapshot)
        self._refresh_voice_pill()
        self._load_system_status()
        self._load_home_summary()

        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(
            lambda _dt: self._update_clock_labels(), 1.0
        )
        if self._footer_ip_event:
            self._footer_ip_event.cancel()
        self._footer_ip_event = Clock.schedule_interval(self._refresh_footer_ip, 30.0)
        Clock.schedule_once(lambda _dt: self._refresh_footer_ip(_dt), 3.0)
        if self._voice_state_event:
            self._voice_state_event.cancel()
        self._voice_state_event = Clock.schedule_interval(
            lambda _dt: self._refresh_voice_pill(), 2.0
        )
        if self._summary_poll_event:
            self._summary_poll_event.cancel()
        self._summary_poll_event = Clock.schedule_interval(
            lambda _dt: self._load_home_summary(), 60.0
        )

    def on_leave(self):
        # Clean up listening state immediately when leaving home
        self._listening_active = False
        self._current_amplitude = 0.0
        if self._soundwave_tick_ev is not None:
            self._soundwave_tick_ev.cancel()
            self._soundwave_tick_ev = None
        if self._listening_pill is not None:
            Animation.cancel_all(self._listening_pill, 'opacity')
            self._listening_pill.opacity = 0.0
        if self._voice_orb is not None:
            Animation.cancel_all(self._voice_orb, 'orb_scale')
            self._voice_orb.orb_scale = 1.0
        if self._soundwave_wf is not None:
            self._soundwave_wf.update_bars(time.monotonic(), 0.0)

        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None
        if self._footer_ip_event:
            self._footer_ip_event.cancel()
            self._footer_ip_event = None
        if self._voice_state_event:
            self._voice_state_event.cancel()
            self._voice_state_event = None
        if self._summary_poll_event:
            self._summary_poll_event.cancel()
            self._summary_poll_event = None
        try:
            get_weather_client().unsubscribe(self._on_weather_snapshot)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Voice pill toggle (kept for backward-compat callers)
    # -----------------------------------------------------------------------

    def _toggle_voice_listening(self):
        app = self.app
        if not getattr(app, "voice_assistant", None) or not getattr(
            app.voice_assistant, "available", False
        ):
            self.add_widget(ModalDialog(
                title="Voice unavailable",
                message=(
                    "No microphone is available, or the wake-word model is "
                    "missing. Run a Microphone Test from Settings to debug."
                ),
                confirm_text="OK",
                cancel_text="",
            ))
            return
        app.user_voice_paused = not getattr(app, "user_voice_paused", False)
        app._sync_voice_assistant_state()
        self._refresh_voice_pill()

    # -----------------------------------------------------------------------
    # Mic orb tap — activates listening same as wake word
    # -----------------------------------------------------------------------

    def _on_mic_orb_tapped(self, _inst) -> None:
        """Tapping the mic orb triggers the listening state just like the wake word.

        Puts the voice interpreter in command-listening mode and runs the
        same animation/pill sequence as a real wake-phrase detection.
        """
        app = self.app
        va  = getattr(app, 'voice_assistant', None)
        if not va or not getattr(va, 'available', False):
            self.add_widget(ModalDialog(
                title="Voice unavailable",
                message=(
                    "No microphone is available, or the wake-word model is "
                    "missing. Run a Microphone Test from Settings to debug."
                ),
                confirm_text="OK",
                cancel_text="",
            ))
            return
        # Open the voice interpreter's command window
        va.simulate_wake()
        # Reuse the exact same UI flow as real wake-word detection
        app._handle_voice_wake_phrase("")

    # -----------------------------------------------------------------------
    # Weather location dialog
    # -----------------------------------------------------------------------

    def _show_weather_location_dialog(self):
        wc = get_weather_client()
        cur = wc.location
        cur_city = (cur and cur.get("city")) or ""
        self.add_widget(TextInputDialog(
            title="Weather Location",
            message=(
                'Enter a city name (e.g. "Bangalore" or "London, UK"). '
                "Leave blank to keep auto-detect."
            ),
            initial_value=cur_city,
            placeholder="City name",
            on_confirm=self._apply_weather_location,
        ))

    def _apply_weather_location(self, value: str):
        text = (value or "").strip()
        if not text:
            return
        wc = get_weather_client()

        async def _resolve():
            resolved = await wc.set_city(text)
            if resolved is None:
                Clock.schedule_once(
                    lambda _dt, t=text: self.add_widget(ModalDialog(
                        title="City not found",
                        message=(
                            f'Could not find weather data for "{t}".\n\n'
                            "Try the city name in English, or include the "
                            'country (e.g. "Bengaluru, IN").'
                        ),
                        confirm_text="OK",
                        cancel_text="",
                    )),
                    0,
                )
        run_async(_resolve())

    # -----------------------------------------------------------------------
    # Gmail dialog
    # -----------------------------------------------------------------------

    def _show_gmail_dashboard_dialog(self):
        self.add_widget(ModalDialog(
            title="Connect Gmail",
            message=(
                f"To see unread email here, open\n{DASHBOARD_URL}\n"
                "on your phone or laptop and connect Gmail."
            ),
            confirm_text="OK",
            cancel_text="",
        ))

    # -----------------------------------------------------------------------
    # Live weather
    # -----------------------------------------------------------------------

    def _on_weather_snapshot(self, snap):
        if self._health_label_offline:
            return
        try:
            temp  = float(snap.temp_c)
            label = (snap.label or "--").strip()
            self.health_label.text       = f"{temp:.0f}°C"
            self.health_label.color      = _WHITE
            self._wx_condition.text      = label
            self._brief_wx_title.text    = f"Weather: {temp:.0f}°C"
            self._brief_wx_sub.text      = label
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Voice pill refresh (content only — visibility is event-driven)
    # -----------------------------------------------------------------------

    def _refresh_voice_pill(self):
        """Update the pill's label/dot content without touching its opacity."""
        # Pill only ever shows "Listening" — keep content locked to that state.
        if self.voice_dot is not None:
            self.voice_dot.color = COLORS["blue"]
        if self.voice_state_label is not None:
            self.voice_state_label.text = "Listening"

    # -----------------------------------------------------------------------
    # Wake-word voice interaction show / hide
    # -----------------------------------------------------------------------

    def show_listening_state(self) -> None:
        """Called when the wake word is detected while on the home screen.

        1. Scales the bottom mic orb up to 1.4x then pulses 1.3↔1.4x.
        2. Fades the Listening pill in.
        3. Starts the soundwave amplitude tick.
        """
        self._listening_active = True
        self._current_amplitude = 0.0

        # -- Voice orb animation ------------------------------------------
        if self._voice_orb is not None:
            Animation.cancel_all(self._voice_orb, 'orb_scale')
            def _start_pulse(*_):
                pulse = (
                    Animation(orb_scale=1.3, duration=0.6, t='in_out_sine') +
                    Animation(orb_scale=1.4, duration=0.6, t='in_out_sine')
                )
                pulse.repeat = True
                pulse.start(self._voice_orb)
            anim_in = Animation(orb_scale=1.4, duration=0.15, t='out_quad')
            anim_in.bind(on_complete=_start_pulse)
            anim_in.start(self._voice_orb)

        # -- Listening pill fade in ----------------------------------------
        if self._listening_pill is not None:
            Animation.cancel_all(self._listening_pill, 'opacity')
            Animation(opacity=1.0, duration=0.28, t='out_cubic').start(
                self._listening_pill
            )

        # -- Start soundwave animation tick --------------------------------
        if self._soundwave_tick_ev is None and self._soundwave_wf is not None:
            self._soundwave_tick_ev = Clock.schedule_interval(
                self._soundwave_tick, 1 / 30
            )

    def hide_listening_state(self) -> None:
        """End of voice interaction: scale orb back, fade out pill, stop soundwave."""
        self._listening_active = False
        self._current_amplitude = 0.0

        # -- Stop soundwave tick and reset bars to baseline ----------------
        if self._soundwave_tick_ev is not None:
            self._soundwave_tick_ev.cancel()
            self._soundwave_tick_ev = None
        if self._soundwave_wf is not None:
            self._soundwave_wf.update_bars(time.monotonic(), 0.0)

        # -- Voice orb: scale back to 1.0x --------------------------------
        if self._voice_orb is not None:
            Animation.cancel_all(self._voice_orb, 'orb_scale')
            Animation(orb_scale=1.0, duration=0.4, t='out_sine').start(
                self._voice_orb
            )

        # -- Listening pill fade out ---------------------------------------
        if self._listening_pill is not None:
            Animation.cancel_all(self._listening_pill, 'opacity')
            Animation(opacity=0.0, duration=0.28, t='in_cubic').start(
                self._listening_pill
            )

    def _soundwave_tick(self, _dt) -> None:
        """30-fps tick: drive the _FigmaWaveform bar animation."""
        if self._soundwave_wf is None:
            return
        self._soundwave_wf.update_bars(time.monotonic(), self._current_amplitude)

    def update_amplitude(self, amp: float) -> None:
        """Receive microphone amplitude (0-1); stored for the soundwave tick."""
        if self._listening_active:
            self._current_amplitude = amp

    # -----------------------------------------------------------------------
    # Clock labels
    # -----------------------------------------------------------------------

    def _update_clock_labels(self):
        now = display_now()
        self.greeting_label.text = _greeting_name(
            getattr(self.app, "user_name", "") or ""
        )
        hm = now.strftime("%I:%M").lstrip("0") or "12:00"
        ap = now.strftime("%p")
        # Combined markup keeps time and AM/PM flush regardless of time width
        self._big_clock_hm.text = (
            f"[b][size={_ff(64.93)}]{hm}[/size][/b]"
            f"[size={_ff(22.72)}][color=B6BAF2] {ap}[/color][/size]"
        )
        self.date_label.text = now.strftime("%A, %B ") + str(now.day)

    # -----------------------------------------------------------------------
    # System status
    # -----------------------------------------------------------------------

    def _load_system_status(self):
        async def _fetch():
            try:
                info     = await self.backend.get_system_info()
                free_gb  = (info["storage_total"] - info["storage_used"]) / (1024 ** 3)
                wifi_ok  = bool(info.get("wifi_ssid"))
                wired_ok = linux_ethernet_ready()
                privacy  = getattr(self.app, "privacy_mode", False)

                def _apply(_dt):
                    online = wifi_ok or wired_ok
                    self._health_label_offline = not online
                    if not online:
                        self.health_label.text  = "Offline"
                        self.health_label.color = COLORS["red"]
                    else:
                        snap = get_weather_client().snapshot
                        if snap is not None:
                            self._on_weather_snapshot(snap)
                    self._footer_kwargs = {
                        "wifi_ok":    wifi_ok,
                        "free_gb":    free_gb,
                        "privacy_mode": privacy,
                        "wired_lan_ok": wired_ok,
                    }
                    self.update_footer(
                        wifi_ok=wifi_ok, free_gb=free_gb,
                        privacy_mode=privacy, wired_lan_ok=wired_ok,
                        local_ip=get_primary_ipv4(),
                    )
                Clock.schedule_once(_apply, 0)
            except Exception:
                def _backend_offline(_dt):
                    self._health_label_offline = True
                    self.health_label.text  = "Backend\nOffline"
                    self.health_label.color = COLORS["red"]
                Clock.schedule_once(_backend_offline, 0)

        run_async(_fetch())

    # -----------------------------------------------------------------------
    # Home summary (meetings, actions, email)
    # -----------------------------------------------------------------------

    def _load_home_summary(self):
        async def _fetch():
            try:
                data     = await self.backend.get_home_summary()
                meetings = []
                try:
                    meetings = await self.backend.get_meetings(limit=1)
                except Exception:
                    meetings = []
                latest   = meetings[0] if meetings else None
                today_n  = int(data.get("pending_actions_today") or 0)
                total_n  = int(data.get("pending_actions_total") or 0)
                unread_n = data.get("unread_email_count")
                next_title, next_time = _format_next_meeting(data.get("next_meeting"))

                def _apply(_dt):
                    self.next_time_label.text  = next_time or "—"
                    self.next_title_label.text = f"Now: {next_title}"
                    self.more_label.text       = f"+{max(0, today_n)} more"

                    self.schedule_card.value_label.text = (
                        next_time.split(" ")[0] if next_time else "—"
                    )
                    self.schedule_card.text_label.text = f"Now: {next_title}"
                    if self.schedule_card._more_label is not None:
                        self.schedule_card._more_label.text = (
                            f"+{today_n}" if today_n else ""
                        )

                    if latest:
                        self._latest_meeting_id = latest.get("id")
                        self.last_title_label.text = (
                            latest.get("title") or "Untitled meeting"
                        )
                        try:
                            raw = (latest.get("start_time")
                                   or latest.get("created_at") or "")
                            dt  = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                            when = (to_display_local(dt)
                                    .strftime("%b %d · %I:%M %p")
                                    .replace(" 0", " "))
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
                        self._latest_meeting_id    = None
                        self.last_title_label.text  = "No saved meetings yet"
                        self.last_meta_label.text   = "Start a recording to build memory"
                        self.last_actions_label.text = "Open meeting library  ›"

                    self.email_card.value_label.text = (
                        str(unread_n) if unread_n is not None else "—"
                    )
                    self.tasks_card.value_label.text = str(total_n)

                    self.brief_calendar_label.title_label.text = (
                        f"{max(0, today_n)} actions today"
                        if today_n else "Briefing ready"
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
                        self.next_title_label, "text",
                        "Now: Ask Tony for briefing"
                    ),
                    0,
                )

        run_async(_fetch())


# ---------------------------------------------------------------------------
# Compatibility alias (retained for brief card access in existing code)
# ---------------------------------------------------------------------------

_BriefRow = _BriefRowData
