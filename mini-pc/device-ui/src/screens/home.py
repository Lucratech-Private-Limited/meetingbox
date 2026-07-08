"""Home screen — System States_1 (Figma 1023:2026, 1260 × 800 px).

Complete rewrite matching the clean minimal Figma design:
  - Full-bleed abstract background image
  - Live 24-hour time (200 px SemiBold) + date (43 px SemiBold)
  - Mic orb button (tappable — activates voice agent)
  - 'Say Hey Pepper' frosted-glass prompt pill (always visible)
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
    Rectangle, RoundedRectangle, Scale, Translate,
)
from kivy.properties import NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from components.live_wifi_icon import LiveWifiIcon
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
_HEY_FILL    = (1.0,   1.0,   1.0,   0.5)   # Hey-Pepper pill  rgba(255,255,255,0.5)
_HEY_STROKE  = (1.0,   1.0,   1.0,   0.6)   # Hey-Pepper border rgba(255,255,255,0.6)
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

    def __init__(self, source: str, on_tap=None, suppress=None, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap
        self._suppress = suppress
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

    def on_release(self):
        # Fire on release (not press) so a horizontal page-swipe that begins on
        # the orb cancels cleanly instead of triggering the voice agent.
        if self._suppress is not None and self._suppress():
            return
        if self._on_tap:
            self._on_tap(self)


# ─────────────────────────────────────────────────────────────────────────────
# 'Say Hey Pepper' frosted-glass prompt pill  (node 1023:2042, 657 × 78 px)
# ─────────────────────────────────────────────────────────────────────────────
class _HeyPepperPill(Widget):
    """Frosted-glass pill drawn entirely in canvas.

    Figma spec (node 1023:2042): fill rgba(255,255,255,0.5),
    stroke rgba(255,255,255,0.6) 2 px, borderRadius 40 px, no drop shadow.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
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


class _SummaryPopupPill(FloatLayout):
    """Rounded summary-ready notification container."""

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*_SHADOW)
            self._shadow = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[30])
            Color(1.0, 1.0, 1.0, 0.96)
            self._bg = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[28])
            Color(1.0, 1.0, 1.0, 0.72)
            self._border = Line(rounded_rectangle=(0, 0, 0, 0, 28), width=1.4)
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        r = min(w, h) / 2
        self._shadow.pos = (x + 2, y - 8)
        self._shadow.size = (w, h + 8)
        self._shadow.radius = [r]
        self._bg.pos = (x, y)
        self._bg.size = (w, h)
        self._bg.radius = [r]
        self._border.rounded_rectangle = (x + 1, y + 1, w - 2, h - 2, max(2, r - 1))


class _PopupButton(ButtonBehavior, FloatLayout):
    """Small rounded button used inside the summary-ready popup."""

    def __init__(self, fill, **kw):
        super().__init__(**kw)
        self._fill = fill
        with self.canvas.before:
            self._color = Color(*fill)
            self._bg = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[22])
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        self._color.rgba = self._fill
        self._bg.pos = (x, y)
        self._bg.size = (w, h)
        self._bg.radius = [min(w, h) / 2]


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
        self._summary_poll_ev:   object | None = None
        self._listening_active:  bool          = False
        self._current_amplitude: float         = 0.0
        self._shown_summary_ids: set[str] = set()
        # Mutable widget refs (set in _build_ui)
        self._root:       FloatLayout     | None = None
        self._battery:    _BatteryWidget  | None = None
        self._voice_pill: _VoiceStatePill | None = None
        self._mic_btn:    _MicButton      | None = None
        self._time_lbl:   Label           | None = None
        self._date_lbl:   Label           | None = None
        self._summary_ready_popup: _SummaryPopupPill | None = None
        self._page_tx = None
        self._build_ui()
        # Swipe page-navigation removed: the always-on-top Pepper dock is now the
        # primary navigation surface, so Home no longer reveals adjacent pages.
        # ``_pagers()`` reads these via ``getattr(..., None)``, so leaving them
        # unset cleanly disables both the forward (Start-Recording) and reverse
        # (Calendar/Tasks) reveals while the rest of the screen keeps working.

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout()
        self._root = root

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

        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) to keep size/position/font consistent
        # across every screen. self._voice_pill stays None here on purpose;
        # existing `if self._voice_pill:` guards below no-op safely.
        # (WiFi + battery indicators removed — not relevant for the desktop app.)

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

        # 7 · 'Say Hey Pepper' pill  (302, 596)  657 × 78  40 px radius ────
        hey_pill = _HeyPepperPill(
            size_hint=(_sw(657), _sh(78)),
            pos_hint={"x": _x(302), "y": _y(596, 78)},
        )
        root.add_widget(hey_pill)

        # 8 · Hey-Pepper text  (node 1023:2043) — 32 px SemiBold #3A3B3D,
        #     centred on both axes inside the 657 × 78 pill.
        hey_lbl = Label(
            text="Say ‘Hey Pepper’ to start a conversation",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT,
            halign="center",
            valign="middle",
            size_hint=(_sw(657), _sh(78)),
            pos_hint={"x": _x(302), "y": _y(596, 78)},
        )
        hey_lbl.bind(size=hey_lbl.setter("text_size"))
        root.add_widget(hey_lbl)

        # 9 · Mic orb button  (570, 415)  122 × 122 ──────────────────────
        mic_src = _fp("new_mic_btn.png")
        self._mic_btn = _MicButton(
            source=mic_src or "",
            on_tap=self._on_mic_tapped,
            suppress=lambda: (
                getattr(self, "_fwd_pager", None) is not None
                and self._fwd_pager.is_engaged
            ),
            size_hint=(_sw(122), _sh(122)),
            pos_hint={"x": _x(570), "y": _y(415, 122)},
        )
        root.add_widget(self._mic_btn)

        self.add_widget(root)

    # ── Interactive page swipe (Home → Start Recording) ───────────────────────

    def _can_start_swipe(self) -> bool:
        # Don't hijack touches while a voice session is animating or a modal
        # dialog is on screen.
        if self._listening_active:
            return False
        for child in self.children:
            if isinstance(child, ModalDialog):
                return False
        return True

    def _prepare_recording_ready(self, rec) -> None:
        """Put the borrowed recording screen into its static READY look."""
        try:
            rec.show_ready_preview()
        except Exception:
            logger.exception("home: failed to prepare recording ready preview")

    def _commit_to_recording_ready(self) -> None:
        """Settle onto the real recording screen in its READY state."""
        try:
            rec = self.app.screen_manager.get_screen("recording")
            rec.enter_ready_next = True
        except Exception:
            logger.exception("home: failed to flag recording ready entry")
        self.app.goto_screen("recording", transition="none")

    def _prepare_adjacent(self, dest) -> None:
        """Prime an adjacent page (Calendar) with live content for the preview."""
        try:
            dest.prime_preview()
        except Exception:
            logger.exception("home: failed to prime adjacent page preview")

    def _commit_to_calendar(self) -> None:
        """Settle onto the Calendar page."""
        self.app.goto_screen("calendar", transition="none")

    def _ensure_page_translate(self) -> None:
        if self._page_tx is not None:
            return
        with self.canvas.before:
            PushMatrix()
            self._page_tx = Translate(0, 0, 0)
        with self.canvas.after:
            PopMatrix()

    def set_page_offset(self, dx: float) -> None:
        self._ensure_page_translate()
        self._page_tx.x = float(dx)

    def prime_preview(self) -> None:
        """Refresh transient content so the back-swipe preview looks live."""
        self.set_page_offset(0.0)
        try:
            self._update_clock()
        except Exception:
            logger.debug("home: prime_preview clock refresh failed", exc_info=True)

    def _pagers(self):
        return (
            getattr(self, "_fwd_pager", None),
            getattr(self, "_cal_pager", None),
        )

    def on_touch_down(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_down(touch):
                return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_move(touch):
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_up(touch):
                return True
        return super().on_touch_up(touch)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_enter(self):
        # Start the audio capture child lazily the first time home is reached
        # (deferred from app startup so onboarding boots cleanly).
        try:
            self.app._ensure_audio_capture_started()
        except Exception:
            pass
        # Reset any in-flight page swipe and the page translate.
        for pager in self._pagers():
            if pager is not None:
                pager.cancel()
        self.set_page_offset(0.0)
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

        # Poll the app-level processing cache for newly completed summaries.
        self._check_summary_ready()
        if self._summary_poll_ev:
            self._summary_poll_ev.cancel()
        self._summary_poll_ev = Clock.schedule_interval(self._check_summary_ready, 1.5)

    def on_leave(self):
        for pager in self._pagers():
            if pager is not None:
                pager.cancel()
        self.set_page_offset(0.0)
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
        if self._summary_poll_ev:
            self._summary_poll_ev.cancel()
            self._summary_poll_ev = None

    # ── Summary-ready popup ──────────────────────────────────────────────────

    def _check_summary_ready(self, *_):
        if self._summary_ready_popup is not None:
            return
        cache = getattr(self.app, "_processing_summary_cache", None)
        if not isinstance(cache, dict) or not cache:
            return
        for meeting_id, entry in list(cache.items()):
            if not isinstance(entry, dict) or not entry.get("ok"):
                continue
            if meeting_id in self._shown_summary_ids:
                continue
            self._show_summary_ready_popup(meeting_id, entry.get("summary") or {})
            break

    def _show_summary_ready_popup(self, meeting_id: str, summary: dict) -> None:
        self._dismiss_summary_popup()
        title = "Your meeting"
        mode = "meeting"
        if isinstance(summary, dict):
            mode = str(summary.get("recording_mode") or summary.get("content_type") or "meeting").strip().lower()
            for key in ("title", "report_title", "meeting_title", "name"):
                value = str(summary.get(key) or "").strip()
                if value:
                    title = value
                    break
        is_note = mode in {"note", "notes"}
        if is_note and title == "Your meeting":
            title = "Notes"

        popup = _SummaryPopupPill(
            size_hint=(_sw(760), _sh(88)),
            pos_hint={"x": _x((_FW - 760) / 2), "y": _y(96, 88)},
        )

        headline = Label(
            text="Notes are ready" if is_note else "Meeting summary is ready",
            font_name=_FONT_SB,
            font_size=_ff(22),
            color=_TEXT,
            halign="left",
            valign="middle",
            size_hint=(0.56, 0.34),
            pos_hint={"x": 0.045, "center_y": 0.63},
        )
        headline.bind(size=headline.setter("text_size"))
        popup.add_widget(headline)

        subtitle = Label(
            text=title,
            font_name=_FONT_SB,
            font_size=_ff(17),
            color=(0.45, 0.45, 0.50, 1.0),
            halign="left",
            valign="middle",
            shorten=True,
            shorten_from="right",
            max_lines=1,
            size_hint=(0.56, 0.30),
            pos_hint={"x": 0.045, "center_y": 0.34},
        )
        subtitle.bind(size=subtitle.setter("text_size"))
        popup.add_widget(subtitle)

        view_btn = _PopupButton(
            fill=_PURPLE,
            size_hint=(0.1447, 0.59),
            pos_hint={"x": 0.687, "center_y": 0.5},
        )
        view_label = Label(
            text="View",
            font_name=_FONT_SB,
            font_size=_ff(20),
            color=(1, 1, 1, 1),
            halign="center",
            valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        view_label.bind(size=view_label.setter("text_size"))
        view_btn.add_widget(view_label)
        view_btn.bind(on_release=lambda *_a, mid=meeting_id, sm=summary: self._on_view_summary(mid, sm))
        popup.add_widget(view_btn)

        close_btn = _PopupButton(
            fill=(0.93, 0.93, 0.95, 1.0),
            size_hint=(0.126, 0.59),
            pos_hint={"x": 0.847, "center_y": 0.5},
        )
        close_label = Label(
            text="Close",
            font_name=_FONT_SB,
            font_size=_ff(18),
            color=_TEXT,
            halign="center",
            valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        close_label.bind(size=close_label.setter("text_size"))
        close_btn.add_widget(close_label)
        close_btn.bind(on_release=lambda *_a, mid=meeting_id: self._dismiss_summary_popup(mark=mid))
        popup.add_widget(close_btn)

        self._summary_ready_popup = popup
        if self._root is not None:
            self._root.add_widget(popup)

    def _on_view_summary(self, meeting_id: str, summary: dict) -> None:
        self._shown_summary_ids.add(meeting_id)
        self._dismiss_summary_popup()
        try:
            screen = self.app.screen_manager.get_screen("summary_review")
            if hasattr(screen, "set_meeting_data"):
                screen.set_meeting_data(meeting_id, summary or {})
        except Exception:
            logger.exception("Failed to open summary_review from home popup")
        self.app.goto_screen("summary_review", "fade")

    def _dismiss_summary_popup(self, mark: str | None = None) -> None:
        if mark:
            self._shown_summary_ids.add(mark)
        popup = self._summary_ready_popup
        self._summary_ready_popup = None
        if popup is None:
            return
        try:
            if popup.parent is not None:
                popup.parent.remove_widget(popup)
        except Exception:
            pass

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

    # ── Voice-session routing helper ─────────────────────────────────────────

    def _voice_session(self):
        """Return the VoiceSessionScreen if registered, else None."""
        if self.manager and "voice_session" in self.manager.screen_names:
            return self.manager.get_screen("voice_session")
        return None

    def _navigate_to_voice_session(self) -> None:
        """Switch to the voice-session screen, clearing previous transcript."""
        vs = self._voice_session()
        if vs and self.manager and self.manager.current != "voice_session":
            vs.clear_say_bar_transcription()
            self.manager.current = "voice_session"

    # ── Public voice-state API (called by main.py) ────────────────────────────

    def show_listening_state(self) -> None:
        """Wake word detected — open voice-session screen and set Listening state."""
        vs = self._voice_session()
        if vs:
            self._navigate_to_voice_session()
            vs.show_listening_state()
            return
        # Fallback: no voice_session screen registered (legacy behaviour)
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
        """End of voice interaction — forward to voice-session or reset home."""
        vs = self._voice_session()
        if vs:
            vs.hide_listening_state()
            return
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
        """Forward state updates to voice-session screen if active, else handle locally."""
        vs = self._voice_session()
        if vs:
            vs.set_voice_session_state(state)
            return
        # Fallback
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
            self.hide_listening_state()

    def update_amplitude(self, amp: float) -> None:
        """Receive microphone amplitude (0–1); forward to voice-session or home waveform."""
        vs = self._voice_session()
        if vs:
            vs.update_amplitude(amp)
            return
        if self._listening_active:
            self._current_amplitude = amp

    # ── Say-bar API — now routes to voice_session screen ─────────────────────

    def activate_say_bar(self) -> None:
        """Navigate to the voice-session screen to start showing the conversation."""
        self._navigate_to_voice_session()

    def deactivate_say_bar(self) -> None:
        """Session over — delegate to voice_session (which navigates back to home)."""
        vs = self._voice_session()
        if vs:
            vs.deactivate_say_bar()

    def update_say_bar_transcription(self, speaker: str, text: str) -> None:
        """Forward transcript line to voice-session screen."""
        vs = self._voice_session()
        if vs:
            vs.update_say_bar_transcription(speaker, text)

    def clear_say_bar_transcription(self) -> None:
        """Clear transcript on voice-session screen."""
        vs = self._voice_session()
        if vs:
            vs.clear_say_bar_transcription()
