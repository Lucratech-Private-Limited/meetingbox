"""Home screen — System States_1 (Figma 1023:2026, 1260 × 800 px).

Complete rewrite matching the clean minimal Figma design:
  - Full-bleed abstract background image
  - Live 24-hour time (200 px SemiBold) + date (43 px SemiBold)
  - Mic orb button (tappable — activates voice agent)
  - 'Say Hey Tony' frosted-glass prompt pill (always visible)
  - Voice-state pill (hidden at idle; shows Listening / Thinking / Talking
    during voice-agent sessions; includes animated speech waveform)
  - WiFi icon + real-battery indicator in top-right

Public API preserved from previous home.py (called by main.py):
    show_listening_state()
    hide_listening_state()
    set_voice_session_state(state)   # "listening" | "thinking" | "speaking" | "idle"
    update_amplitude(amp)
    update_say_bar_transcription(speaker, text)   # no-op — new design has no say bar
    clear_say_bar_transcription()                  # no-op
    activate_say_bar() / deactivate_say_bar()      # no-op
"""
from __future__ import annotations

import logging
import math
import threading
import time

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import (
    Color, Ellipse, Line, PopMatrix, PushMatrix,
    Rectangle, RoundedRectangle, Scale,
)
from kivy.properties import NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Design constants  (Figma "System States_1"  node 1023:2026  1260 × 800 px)
# ─────────────────────────────────────────────────────────────────────────────
_FW, _FH   = 1260.0, 800.0
_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Colours lifted verbatim from Figma
_TEXT        = (0.227, 0.231, 0.239, 1.0)   # #3A3B3D  body text / dark icons
_PILL_BG     = (0.980, 0.980, 0.980, 1.0)   # #FAFAFA  voice-state pill fill
_PURPLE      = (0.427, 0.282, 0.800, 1.0)   # #6D48CC  accent dot in pill
_HEY_FILL    = (1.0,   1.0,   1.0,   0.5)   # Hey-Tony pill  rgba(255,255,255,0.5)
_HEY_STROKE  = (1.0,   1.0,   1.0,   0.6)   # Hey-Tony border rgba(255,255,255,0.6)
_SHADOW      = (0.463, 0.506, 0.498, 0.18)  # rgba(118,129,127,0.18) — shadow approximation

# Registered font names (loaded in main.py)
_FONT_SB = "42dot-SB"


# ─────────────────────────────────────────────────────────────────────────────
# Coordinate / scale helpers
# ─────────────────────────────────────────────────────────────────────────────
def _x(px: float) -> float:
    """Figma x → FloatLayout pos_hint x."""
    return px / _FW


def _y(top: float, h: float) -> float:
    """Figma y-from-top + element height → Kivy y-from-bottom pos_hint."""
    return max(0.0, (_FH - top - h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(fs: float) -> int:
    """Scale a Figma font-size to actual display pixels."""
    s = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * s))


def _fp(name: str) -> str:
    """Return absolute path to a Figma asset, or '' if the file is missing."""
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ─────────────────────────────────────────────────────────────────────────────
# Animated voice waveform  (7 bars, matching Figma SVG proportions)
# ─────────────────────────────────────────────────────────────────────────────
class _VoiceWaveform(Widget):
    """Animated 7-bar speech waveform.

    Bar proportions are taken directly from the existing Figma SVG (46×46 viewBox).
    Color is configurable so the same widget works on both light and dark surfaces.
    ``update_bars(t, amplitude)`` should be called at ~30 fps.
    """

    _BAR_DATA = [
        (7.185,  8.625),
        (12.935, 14.375),
        (18.685, 22.999),
        (24.435, 34.499),   # centre / tallest
        (30.185, 22.999),
        (35.935, 14.375),
        (41.685, 8.625),
    ]
    _BAR_W  = 2.875
    _VB     = 46.0
    _CY_VB  = 23.0
    _PHASES = [3.0, 2.2, 1.4, 0.0, 1.4, 2.2, 3.0]

    def __init__(self, bar_color=_TEXT, **kw):
        super().__init__(**kw)
        self._scale    = 1.0
        self._bar_w_px = 1.0
        self._bar_cxy: list = []
        with self.canvas:
            self._ci = Color(*bar_color)
            self._bars = [
                RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[0.5])
                for _ in self._BAR_DATA
            ]
        self.bind(pos=self._rebuild, size=self._rebuild)
        Clock.schedule_once(self._rebuild, 0)

    def _rebuild(self, *_):
        w, h = self.size
        px, py = self.pos
        if w <= 0 or h <= 0:
            return
        s  = min(w / self._VB, h / self._VB)
        ox = px + (w - self._VB * s) / 2
        oy = py + (h - self._VB * s) / 2
        self._scale    = s
        self._bar_w_px = self._BAR_W * s
        self._bar_cxy  = [(ox + cx * s, oy + self._CY_VB * s) for cx, _ in self._BAR_DATA]
        r = self._bar_w_px / 2
        for i, bar in enumerate(self._bars):
            cx, cy = self._bar_cxy[i]
            bh = self._BAR_DATA[i][1] * s
            bar.pos    = (cx - self._bar_w_px / 2, cy - bh / 2)
            bar.size   = (self._bar_w_px, bh)
            bar.radius = [r]

    def update_bars(self, t: float, amplitude: float) -> None:
        if not self._bars or not self._bar_cxy:
            return
        s, bwp = self._scale, self._bar_w_px
        r = bwp / 2
        amp = max(0.0, min(1.0, amplitude))
        for i, bar in enumerate(self._bars):
            cx, cy = self._bar_cxy[i]
            bh     = self._BAR_DATA[i][1] * s
            ph     = self._PHASES[i]
            idle   = 1.0 + 0.10 * math.sin(t * 3.0 + ph)
            spd    = 5.0 + amp * 12.0
            voice  = amp * 1.2 * abs(math.sin(t * spd + ph))
            h_px   = bh * idle * (1.0 + voice)
            bar.pos    = (cx - bwp / 2, cy - h_px / 2)
            bar.size   = (bwp, h_px)
            bar.radius = [r]


# ─────────────────────────────────────────────────────────────────────────────
# Battery icon  (47 × 21 px in Figma, node 1023:2053)
# ─────────────────────────────────────────────────────────────────────────────
class _BatteryWidget(Widget):
    """Dynamic battery icon drawn in code.

    Call ``set_level(0–1)`` and ``set_color(rgba)`` to update.
    Bar colour automatically shifts green → amber → red based on level.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self._level      = 1.0
        self._base_color = (0.44, 0.44, 0.46, 0.85)
        with self.canvas:
            self._bc    = Color(*self._base_color)
            self._outer = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[2])
            self._nc    = Color(*self._base_color)
            self._nub   = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[1])
            self._fc    = Color(0.22, 0.80, 0.35, 0.9)
            self._fill  = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[1])
        self.bind(pos=self._draw, size=self._draw)

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self._draw()

    def set_color(self, color) -> None:
        self._base_color = color
        self._draw()

    def _draw(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        nub_w  = max(2.0, w * 0.085)
        body_w = max(1.0, w - nub_w - 1)
        r, g, b, a = self._base_color
        # Outer body border
        self._bc.rgba   = (r, g, b, a)
        self._outer.pos    = (x, y)
        self._outer.size   = (body_w, h)
        self._outer.radius = [max(1.0, h * 0.22)]
        # Terminal nub on right
        nub_h = h * 0.5
        self._nc.rgba = (r, g, b, a)
        self._nub.pos  = (x + body_w + 1, y + (h - nub_h) / 2)
        self._nub.size = (nub_w, nub_h)
        self._nub.radius = [max(1.0, nub_w * 0.3)]
        # Inner fill
        border = 1.5
        fw = max(0.0, (body_w - 2 * border) * self._level)
        lvl = self._level
        fc = (
            (0.22, 0.80, 0.35, 0.9) if lvl > 0.50 else
            (0.95, 0.65, 0.10, 0.9) if lvl > 0.20 else
            (0.95, 0.25, 0.20, 0.9)
        )
        self._fc.rgba = fc
        self._fill.pos  = (x + border, y + border)
        self._fill.size = (fw, max(0.0, h - 2 * border))
        if fw > 0:
            self._fill.radius = [max(1.0, (h - 2 * border) * 0.22)]


# ─────────────────────────────────────────────────────────────────────────────
# Tappable mic orb button  (new_mic_btn.png, 122 × 122 px)
# ─────────────────────────────────────────────────────────────────────────────
class _MicButton(ButtonBehavior, Widget):
    """PNG mic button that supports a smooth scale pulse animation."""

    orb_scale = NumericProperty(1.0)

    def __init__(self, source: str, on_tap=None, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap
        self._tex = None
        try:
            self._tex = CoreImage(source).texture
        except Exception as exc:
            logger.debug("_MicButton load %s: %s", source, exc)
        with self.canvas:
            PushMatrix()
            self._sc   = Scale(1, 1, 1)
            Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size, texture=self._tex)
            PopMatrix()
        self.bind(pos=self._sync, size=self._sync, orb_scale=self._sync_scale)

    def _sync_scale(self, *_):
        cx, cy = self.center
        self._sc.origin = (cx, cy, 0)
        self._sc.x = self.orb_scale
        self._sc.y = self.orb_scale

    def _sync(self, *_):
        self._rect.pos  = self.pos
        self._rect.size = self.size
        self._sync_scale()

    def on_press(self):
        if self._on_tap:
            self._on_tap(self)


# ─────────────────────────────────────────────────────────────────────────────
# 'Say Hey Tony' frosted-glass prompt pill  (node 1023:2042, 657 × 78 px)
# ─────────────────────────────────────────────────────────────────────────────
class _HeyTonyPill(Widget):
    """Frosted-glass pill drawn entirely in canvas.

    Figma spec: fill rgba(255,255,255,0.5), stroke rgba(255,255,255,0.6),
    borderRadius 40 px, shadow 0 11 19 rgba(118,129,127,0.7).
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            # Shadow approximation (Kivy has no blur; use layered semi-transparent rects)
            self._shc  = Color(*_SHADOW)
            self._shad = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[44])
            # Fill: rgba(255,255,255,0.5)
            self._fillc = Color(*_HEY_FILL)
            self._fillr = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[40])
            # Border: rgba(255,255,255,0.6), 2 px
            self._strokec = Color(*_HEY_STROKE)
            self._border  = Line(rounded_rectangle=(0, 0, 0, 0, 40), width=2)
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        r = _ff(40)
        self._shad.pos    = (x + 2, y - 9)
        self._shad.size   = (w + 2, h + 12)
        self._shad.radius = [r + 5]
        self._fillr.pos    = (x, y)
        self._fillr.size   = (w, h)
        self._fillr.radius = [r]
        self._border.rounded_rectangle = (x + 1, y + 1, w - 2, h - 2, max(2, r - 1))


# ─────────────────────────────────────────────────────────────────────────────
# Voice-state pill  (node 1023:2029, 222 × 47 px)
# ─────────────────────────────────────────────────────────────────────────────
class _VoiceStatePill(FloatLayout):
    """White pill that shows the active voice-agent state text + waveform.

    Figma: #FAFAFA fill, 76 px radius (full capsule), shadow 0 6 19 rgba(118,129,127,0.3).
    Hidden (opacity=0) at idle; fades in when a voice session is active.

    Internal layout (all in the 222 × 47 px Figma coordinate space):
      • Purple dot  #6D48CC  at (13, 15)  17 × 17
      • State text  #3A3B3D  at (42,  9) 102 × 29  24.24 px SemiBold
      • Waveform              at (170, 9)  39 × 29
    """

    _PW, _PH = 222.0, 47.0

    def __init__(self, **kw):
        super().__init__(**kw)
        self.opacity = 0.0
        PW, PH = self._PW, self._PH

        # ── Canvas background ─────────────────────────────────────────────
        with self.canvas.before:
            Color(*_SHADOW)
            self._shad = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
            Color(*_PILL_BG)
            self._bg   = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
        self.bind(pos=self._draw_bg, size=self._draw_bg)

        # ── Purple dot  (13, 15) 17 × 17 ─────────────────────────────────
        dot = Widget(size_hint=(17/PW, 17/PH), pos_hint={"x": 13/PW, "y": 15/PH})
        with dot.canvas:
            Color(*_PURPLE)
            dot_ell = Ellipse(pos=dot.pos, size=dot.size)
        dot.bind(
            pos=lambda w, *_: setattr(dot_ell, "pos",  w.pos),
            size=lambda w, *_: setattr(dot_ell, "size", w.size),
        )
        self.add_widget(dot)

        # ── State label  (42, 9)  102 × 29  24.24 px SemiBold ────────────
        self._state_lbl = Label(
            text="Listening",
            font_name=_FONT_SB,
            font_size=_ff(24.24),
            color=_TEXT,
            halign="left",
            valign="middle",
            size_hint=(102 / PW, 29 / PH),
            pos_hint={"x": 42 / PW, "y": 9 / PH},
        )
        self._state_lbl.bind(size=self._state_lbl.setter("text_size"))
        self.add_widget(self._state_lbl)

        # ── Animated waveform  (170, 9)  39 × 29 ─────────────────────────
        self._waveform = _VoiceWaveform(
            bar_color=_TEXT,
            size_hint=(39 / PW, 29 / PH),
            pos_hint={"x": 170 / PW, "y": 9 / PH},
        )
        self.add_widget(self._waveform)

    def _draw_bg(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        r = min(w, h) / 2   # full capsule
        self._shad.pos    = (x + 1, y - 4)
        self._shad.size   = (w + 2, h + 5)
        self._shad.radius = [r + 2]
        self._bg.pos    = (x, y)
        self._bg.size   = (w, h)
        self._bg.radius = [r]

    # -- Public helpers -------------------------------------------------------

    def set_state_text(self, text: str) -> None:
        self._state_lbl.text = text

    def update_bars(self, t: float, amp: float) -> None:
        self._waveform.update_bars(t, amp)


# ─────────────────────────────────────────────────────────────────────────────
# HomeScreen
# ─────────────────────────────────────────────────────────────────────────────
class HomeScreen(BaseScreen):
    """Clean minimal home screen matching Figma System States_1 (1023:2026).

    All element positions and sizes are taken pixel-for-pixel from Figma.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_ev:          object | None = None
        self._status_ev:         object | None = None
        self._voice_tick_ev:     object | None = None
        self._listening_active:  bool          = False
        self._current_amplitude: float         = 0.0
        # Mutable widget refs (set in _build_ui)
        self._battery:    _BatteryWidget  | None = None
        self._voice_pill: _VoiceStatePill | None = None
        self._mic_btn:    _MicButton      | None = None
        self._time_lbl:   Label           | None = None
        self._date_lbl:   Label           | None = None
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout()

        # 1 · Full-bleed background image  (0, 0)  1260 × 800 ──────────────
        bg_src = _fp("new_home_bg.png")
        if bg_src:
            root.add_widget(Image(
                source=bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # 2 · WiFi icon  (1125, 31)  29 × 20 ─────────────────────────────
        wifi_src = _fp("new_wifi_icon.png")
        if wifi_src:
            root.add_widget(Image(
                source=wifi_src,
                size_hint=(_sw(29), _sh(20)),
                pos_hint={"x": _x(1125), "y": _y(31, 20)},
                fit_mode="contain",
                allow_stretch=True,
                keep_ratio=True,
            ))

        # 3 · Battery indicator  (1191, 30)  47 × 21 ─────────────────────
        self._battery = _BatteryWidget(
            size_hint=(_sw(47), _sh(21)),
            pos_hint={"x": _x(1191), "y": _y(30, 21)},
        )
        root.add_widget(self._battery)

        # 4 · Voice-state pill  (867, 17)  222 × 47  (hidden by default) ─
        self._voice_pill = _VoiceStatePill(
            size_hint=(_sw(222), _sh(47)),
            pos_hint={"x": _x(867), "y": _y(17, 47)},
        )
        root.add_widget(self._voice_pill)

        # 5 · Date label  (553, 126)  189 × 51  43 px SemiBold ───────────
        #     Figma text: "Tue Apr 2"  →  live: display_now()
        self._date_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(43),
            color=_TEXT,
            halign="center",
            valign="top",
            size_hint=(_sw(189), _sh(51)),
            pos_hint={"x": _x(553), "y": _y(126, 51)},
        )
        self._date_lbl.bind(size=self._date_lbl.setter("text_size"))
        root.add_widget(self._date_lbl)

        # 6 · Time label  (358, 144)  545 × 239  200 px SemiBold ─────────
        #     Figma text: "09:45"  →  live: HH:MM (24-hour)
        self._time_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(200),
            color=_TEXT,
            halign="center",
            valign="top",
            size_hint=(_sw(545), _sh(239)),
            pos_hint={"x": _x(358), "y": _y(144, 239)},
        )
        self._time_lbl.bind(size=self._time_lbl.setter("text_size"))
        root.add_widget(self._time_lbl)

        # 7 · 'Say Hey Tony' pill  (302, 596)  657 × 78  40 px radius ────
        hey_pill = _HeyTonyPill(
            size_hint=(_sw(657), _sh(78)),
            pos_hint={"x": _x(302), "y": _y(596, 78)},
        )
        root.add_widget(hey_pill)

        # 8 · Hey-Tony text  (Figma: x+54 inside pill, 549 × 38, centred) ─
        #     Figma: 32 px SemiBold, #3A3B3D, center
        hey_lbl = Label(
            text="Say 'Hey Tony' to start a conversation",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT,
            halign="center",
            valign="middle",
            size_hint=(_sw(549), _sh(38)),
            pos_hint={"x": _x(302 + 54), "y": _y(596 + 19, 38)},
        )
        hey_lbl.bind(size=hey_lbl.setter("text_size"))
        root.add_widget(hey_lbl)

        # 9 · Mic orb button  (570, 415)  122 × 122 ──────────────────────
        mic_src = _fp("new_mic_btn.png")
        self._mic_btn = _MicButton(
            source=mic_src or "",
            on_tap=self._on_mic_tapped,
            size_hint=(_sw(122), _sh(122)),
            pos_hint={"x": _x(570), "y": _y(415, 122)},
        )
        root.add_widget(self._mic_btn)

        self.add_widget(root)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_enter(self):
        # Reset voice state to idle
        self._listening_active  = False
        self._current_amplitude = 0.0
        self._stop_waveform_tick()
        if self._voice_pill is not None:
            Animation.cancel_all(self._voice_pill, "opacity")
            self._voice_pill.opacity = 0.0
        if self._mic_btn is not None:
            Animation.cancel_all(self._mic_btn, "orb_scale")
            self._mic_btn.orb_scale = 1.0

        # Start live clock (update immediately, then every 30 s)
        self._update_clock()
        if self._clock_ev:
            self._clock_ev.cancel()
        self._clock_ev = Clock.schedule_interval(lambda _dt: self._update_clock(), 30.0)

        # Hardware status (battery) — first poll after 1.5 s, then every 30 s
        if self._status_ev:
            self._status_ev.cancel()
        Clock.schedule_once(lambda _dt: self._refresh_status(), 1.5)
        self._status_ev = Clock.schedule_interval(lambda _dt: self._refresh_status(), 30.0)

    def on_leave(self):
        self._listening_active  = False
        self._current_amplitude = 0.0
        self._stop_waveform_tick()
        if self._voice_pill is not None:
            Animation.cancel_all(self._voice_pill, "opacity")
            self._voice_pill.opacity = 0.0
        if self._mic_btn is not None:
            Animation.cancel_all(self._mic_btn, "orb_scale")
            self._mic_btn.orb_scale = 1.0
        if self._clock_ev:
            self._clock_ev.cancel()
            self._clock_ev = None
        if self._status_ev:
            self._status_ev.cancel()
            self._status_ev = None

    # ── Live clock ────────────────────────────────────────────────────────────

    def _update_clock(self):
        now = display_now()
        if self._time_lbl is not None:
            self._time_lbl.text = now.strftime("%H:%M")
        if self._date_lbl is not None:
            # "Wed Jun 4" — abbreviated weekday, abbreviated month, no-leading-zero day
            try:
                self._date_lbl.text = now.strftime("%a %b %-d")
            except ValueError:
                # Windows fallback (not the deployment target, but safe)
                self._date_lbl.text = now.strftime("%a %b %#d")

    # ── Hardware status ───────────────────────────────────────────────────────

    def _refresh_status(self):
        threading.Thread(target=self._fetch_status, daemon=True).start()

    def _fetch_status(self):
        try:
            import hardware as _hw          # noqa: PLC0415
            batt = _hw.get_battery_info()
        except Exception:
            return

        def _apply(_dt):
            if self._battery is None:
                return
            pct      = batt.get("percent")
            charging = batt.get("charging", False)  # noqa: F841  (future use)
            if pct is not None:
                level   = pct / 100.0
                bat_col = (
                    (0.22, 0.80, 0.35, 0.88) if level > 0.50 else
                    (0.95, 0.65, 0.10, 0.88) if level > 0.20 else
                    (0.95, 0.25, 0.20, 0.88)
                )
                self._battery.set_color(bat_col)
                self._battery.set_level(level)
            else:
                self._battery.set_level(1.0)
                self._battery.set_color((0.44, 0.44, 0.46, 0.85))

        Clock.schedule_once(_apply, 0)

    # ── Waveform animation tick ───────────────────────────────────────────────

    def _start_waveform_tick(self):
        if self._voice_tick_ev is None and self._voice_pill is not None:
            self._voice_tick_ev = Clock.schedule_interval(self._waveform_tick, 1 / 30)

    def _stop_waveform_tick(self):
        if self._voice_tick_ev is not None:
            self._voice_tick_ev.cancel()
            self._voice_tick_ev = None
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), 0.0)

    def _waveform_tick(self, _dt):
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), self._current_amplitude)

    # ── Mic tap ───────────────────────────────────────────────────────────────

    def _on_mic_tapped(self, _inst) -> None:
        """Tapping the mic triggers the voice agent, same as saying the wake word."""
        app = self.app
        va  = getattr(app, "voice_assistant", None)
        if not va or not getattr(va, "available", False):
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
        va.simulate_wake()
        app._handle_voice_wake_phrase("")

    # ── Public voice-state API (called by main.py) ────────────────────────────

    def show_listening_state(self) -> None:
        """Wake word detected — show Listening pill and start waveform animation."""
        self._listening_active  = True
        self._current_amplitude = 0.0
        if self._voice_pill is not None:
            self._voice_pill.set_state_text("Listening")
            Animation.cancel_all(self._voice_pill, "opacity")
            Animation(opacity=1.0, duration=0.28, t="out_cubic").start(self._voice_pill)
        if self._mic_btn is not None:
            Animation.cancel_all(self._mic_btn, "orb_scale")
            pulse = (
                Animation(orb_scale=1.07, duration=0.55, t="in_out_sine") +
                Animation(orb_scale=1.0,  duration=0.55, t="in_out_sine")
            )
            pulse.repeat = True
            pulse.start(self._mic_btn)
        self._start_waveform_tick()

    def hide_listening_state(self) -> None:
        """End of voice interaction — fade out pill, stop waveform, reset mic."""
        self._listening_active  = False
        self._current_amplitude = 0.0
        self._stop_waveform_tick()
        if self._voice_pill is not None:
            Animation.cancel_all(self._voice_pill, "opacity")
            Animation(opacity=0.0, duration=0.28, t="in_cubic").start(self._voice_pill)
        if self._mic_btn is not None:
            Animation.cancel_all(self._mic_btn, "orb_scale")
            Animation(orb_scale=1.0, duration=0.3).start(self._mic_btn)

    def set_voice_session_state(self, state: str) -> None:
        """Update the voice-state pill for Realtime session state transitions.

        "listening" → pill shows "Listening", waveform animates
        "thinking"  → pill shows "Thinking",  waveform at rest
        "speaking"  → pill shows "Talking",   waveform at rest
        "idle"      → pill fades out
        """
        if state == "listening":
            self._listening_active = True
            if self._voice_pill is not None:
                self._voice_pill.set_state_text("Listening")
                Animation.cancel_all(self._voice_pill, "opacity")
                Animation(opacity=1.0, duration=0.22, t="out_cubic").start(self._voice_pill)
            self._start_waveform_tick()

        elif state == "thinking":
            self._listening_active  = False
            self._current_amplitude = 0.0
            if self._voice_pill is not None:
                self._voice_pill.set_state_text("Thinking")
                Animation.cancel_all(self._voice_pill, "opacity")
                Animation(opacity=1.0, duration=0.18).start(self._voice_pill)
            self._stop_waveform_tick()

        elif state == "speaking":
            self._listening_active  = False
            self._current_amplitude = 0.0
            if self._voice_pill is not None:
                self._voice_pill.set_state_text("Talking")
                Animation.cancel_all(self._voice_pill, "opacity")
                Animation(opacity=1.0, duration=0.18).start(self._voice_pill)
            self._stop_waveform_tick()

        else:
            # "idle" or any unknown state → hide pill
            self.hide_listening_state()

    def update_amplitude(self, amp: float) -> None:
        """Receive microphone amplitude (0–1); drives the waveform animation."""
        if self._listening_active:
            self._current_amplitude = amp

    # ── Stubs kept for main.py compatibility (say-bar removed in new design) ──

    def activate_say_bar(self) -> None:
        pass

    def deactivate_say_bar(self) -> None:
        pass

    def update_say_bar_transcription(self, speaker: str, text: str) -> None:
        pass

    def clear_say_bar_transcription(self) -> None:
        pass
