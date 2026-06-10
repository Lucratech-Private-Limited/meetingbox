"""Voice-agent session screen — Figma node 1040:60 (System States_2, 1260 × 800 px).

Shown when the user speaks the wake word.  Stays on screen for the full
voice-agent conversation, then navigates back to home.

Layout:
  • Full-bleed background image  +  rgba(255,255,255,0.45) soft overlay
  • Centre: static mic orb PNG with 3 staggered ripple rings expanding outward
  • Top-right: same Listening/Thinking/Talking pill + WiFi + battery as home
  • Top-left: ← Back button (also auto-dismissed when session ends)
  • Bottom: scrolling transcription box  (1216 × 353 px)
      – gradient opacity fade over top 30%  (oldest lines fade out gracefully)
      – auto-scrolls to newest entry
"""
from __future__ import annotations

import logging
import time

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen
# Re-use the shared pill and battery widget from the home screen.
from screens.home import _BatteryWidget, _VoiceStatePill  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Figma frame constants  (System States_2, node 1040:60, 1260 × 800 px)
# ──────────────────────────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0
_FIGMA_DIR = ASSETS_DIR / "voice-session" / "figma"

# Colours
_TEXT_USER = (0.396, 0.420, 0.431, 1.0)   # #656B6E  user transcript lines
_TEXT_AI   = (0.208, 0.224, 0.231, 1.0)   # #35393B  AI transcript lines
_TEXT_BACK = (0.208, 0.224, 0.231, 0.85)  # back-button label
_BOX_BG    = (1.0, 1.0, 1.0, 0.9)         # rgba(255,255,255,0.9) transcript box
_FONT_SB   = "42dot-SB"


# ──────────────────────────────────────────────────────────────────────────────
# Coordinate / scale helpers  (identical to home.py)
# ──────────────────────────────────────────────────────────────────────────────
def _x(px: float) -> float:
    return px / _FW

def _y(top: float, h: float) -> float:
    return max(0.0, (_FH - top - h) / _FH)

def _sw(px: float) -> float:
    return px / _FW

def _sh(px: float) -> float:
    return px / _FH

def _ff(fs: float) -> int:
    s = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * s))

def _fp(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ──────────────────────────────────────────────────────────────────────────────
# Programmatic WiFi icon  (Figma fill #000000, 29 × 20 px)
# ──────────────────────────────────────────────────────────────────────────────
class _WifiIcon(Widget):
    """Three concentric arcs + centre dot drawn in canvas — true vector, no PNG."""

    _COL = (0.0, 0.0, 0.0, 1.0)   # Figma fill: #000000

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._c    = Color(*self._COL)
            self._arc1 = Line(width=1.4)   # innermost (smallest)
            self._arc2 = Line(width=1.4)
            self._arc3 = Line(width=1.4)   # outermost
            self._dotc = Color(*self._COL)
            self._dot  = Ellipse()
        self.bind(pos=self._redraw, size=self._redraw)
        Clock.schedule_once(self._redraw, 0)

    def _redraw(self, *_) -> None:
        w, h = self.size
        if w <= 1 or h <= 1:
            return
        # Arc pivot: bottom-centre of the icon
        cx = self.x + w / 2
        cy = self.y + h * 0.08

        # Three arc radii as fractions of icon height
        for arc, frac in [(self._arc1, 0.30), (self._arc2, 0.58), (self._arc3, 0.86)]:
            r = h * frac
            # Kivy arc angles: 0° = 3 o'clock, counterclockwise.
            # 45°–135° gives an upward-opening fan.
            arc.ellipse = (cx - r, cy - r, 2 * r, 2 * r, 45, 135)

        # Dot
        dr = h * 0.09
        self._dot.pos  = (cx - dr, cy - dr)
        self._dot.size = (dr * 2, dr * 2)


# ──────────────────────────────────────────────────────────────────────────────
# Wave-ring animation  (3 staggered rings emanating from mic circumference)
# ──────────────────────────────────────────────────────────────────────────────
class _WaveRings(Widget):
    """Three white circle-rings that expand outward from the mic edge, looping
    infinitely.  Each ring starts at the mic circumference (radius mic_r) and
    expands to max_r, fading as it grows.  The three rings are staggered by
    one-third of the cycle period so the motion looks like continuous ripples.
    """

    PERIOD   = 2.0    # seconds for one complete ring expansion
    N_RINGS  = 3
    LINE_W   = 2.5    # initial stroke width (px)

    def __init__(self, mic_r_px: float, max_r_px: float, **kw):
        """
        mic_r_px  – radius of the mic orb in Figma pixels (= 61 px)
        max_r_px  – maximum ring radius in Figma pixels (≈ 140 px)
        """
        super().__init__(**kw)
        self._mic_r_figma = mic_r_px
        self._max_r_figma = max_r_px
        self._t = 0.0
        self._ev: object | None = None

        with self.canvas:
            self._ring_data: list[tuple[Color, Line]] = []
            for _ in range(self.N_RINGS):
                col  = Color(1, 1, 1, 0)
                ring = Line(circle=(0, 0, 0), width=self.LINE_W)
                self._ring_data.append((col, ring))

        self.bind(pos=self._sync, size=self._sync)

    # ── public ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._ev is None:
            self._t  = 0.0
            self._ev = Clock.schedule_interval(self._tick, 1 / 30)

    def stop(self) -> None:
        if self._ev:
            self._ev.cancel()
            self._ev = None
        for col, _ in self._ring_data:
            col.a = 0.0

    # ── internal ────────────────────────────────────────────────────────────

    def _tick(self, dt: float) -> None:
        self._t += dt
        self._sync()

    def _sync(self, *_) -> None:
        w, h = self.size
        if w <= 0 or h <= 0:
            return

        # Scale from Figma pixels to actual screen pixels.
        # The wave widget width represents 320 Figma-px, so:
        scale  = w / 320.0
        mic_r  = self._mic_r_figma * scale
        max_r  = self._max_r_figma * scale
        cx, cy = self.center_x, self.center_y
        T      = self.PERIOD

        for i, (col, ring) in enumerate(self._ring_data):
            phase = (self._t / T + i / self.N_RINGS) % 1.0   # 0 → 1
            r     = mic_r + (max_r - mic_r) * phase
            alpha = (1.0 - phase) * 0.85
            col.a = alpha
            ring.circle = (cx, cy, r)
            # Taper stroke width as ring expands
            ring.width = max(1.0, self.LINE_W * (1.0 - phase * 0.6))


# ──────────────────────────────────────────────────────────────────────────────
# Gradient-fade overlay  (reveals text from fully-hidden at top to visible below)
# ──────────────────────────────────────────────────────────────────────────────
class _GradientFade(Widget):
    """Transparent-to-white vertical gradient covering the top N % of its area.

    The gradient runs from fully white (opaque) at the very top to fully
    transparent at the bottom edge, masking the oldest transcript lines.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._c    = Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._redraw, size=self._redraw)
        Clock.schedule_once(self._redraw, 0)

    def _redraw(self, *_) -> None:
        w, h = int(self.width), int(self.height)
        if w < 1 or h < 1:
            return
        self._rect.pos     = self.pos
        self._rect.size    = (self.width, self.height)
        self._rect.texture = self._build_texture(h)

    @staticmethod
    def _build_texture(h: int) -> Texture:
        """Create a 1-pixel-wide RGBA texture: opaque white at top, transparent at bottom."""
        tex  = Texture.create(size=(1, max(2, h)), colorfmt="rgba")
        rows = []
        for row in range(max(2, h)):
            # row 0 = bottom-left in OpenGL = transparent
            t = row / (max(2, h) - 1)   # 0 = transparent, 1 = opaque
            a = int(255 * t)
            rows += [255, 255, 255, a]
        tex.blit_buffer(bytes(rows), colorfmt="rgba", bufferfmt="ubyte")
        return tex


# ──────────────────────────────────────────────────────────────────────────────
# Back button
# ──────────────────────────────────────────────────────────────────────────────
class _BackButton(ButtonBehavior, Widget):
    """Tappable '← Back' label rendered in canvas."""

    def __init__(self, on_tap=None, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap
        self._lbl = Label(
            text="← Back",
            font_name=_FONT_SB,
            font_size=_ff(22),
            color=_TEXT_BACK,
            halign="left",
            valign="middle",
        )
        self._lbl.size_hint = (None, None)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_) -> None:
        self._lbl.size      = self.size
        self._lbl.pos       = self.pos
        self._lbl.text_size = self.size

    def on_press(self) -> None:
        if self._on_tap:
            self._on_tap()


# ──────────────────────────────────────────────────────────────────────────────
# Transcription box
# ──────────────────────────────────────────────────────────────────────────────
class _TranscriptionBox(FloatLayout):
    """Rounded-rectangle transcript panel with auto-scroll and top-fade overlay.

    Messages are added via :meth:`add_message`.  The panel auto-scrolls so the
    newest entry is always visible at the bottom.  The top 30 % is covered by
    a gradient white overlay so older lines fade out gracefully.
    """

    _PADDING  = 49   # left/right inner padding  (Figma: text starts at x=49 within box)
    _VPAD_TOP = 16   # extra top padding inside the scrollview
    _VPAD_BOT = 24   # extra bottom padding
    _SPACING  = 14   # vertical gap between messages

    def __init__(self, **kw):
        super().__init__(**kw)

        # ── rounded-rectangle background ────────────────────────────────────
        with self.canvas.before:
            Color(*_BOX_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[_ff(38)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # ── scroll view + message stack ──────────────────────────────────────
        self._inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            spacing=_ff(self._SPACING),
            padding=[
                _ff(self._PADDING),
                _ff(self._VPAD_TOP),
                _ff(self._PADDING),
                _ff(self._VPAD_BOT),
            ],
        )
        self._inner.bind(minimum_height=self._inner.setter("height"))

        self._scroll = ScrollView(
            size_hint=(1, 1),
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=0,             # hide scrollbar
            always_overscroll=False,
        )
        self._scroll.add_widget(self._inner)
        self.add_widget(self._scroll)

        # ── gradient fade — top 30 % ─────────────────────────────────────────
        self._fade = _GradientFade(
            size_hint=(1, 0.30),
            pos_hint={"x": 0, "y": 0.70},
        )
        self.add_widget(self._fade)

    # ── background sync ──────────────────────────────────────────────────────

    def _sync_bg(self, *_) -> None:
        self._bg.pos    = self.pos
        self._bg.size   = self.size
        self._bg.radius = [_ff(38)]

    # ── public API ────────────────────────────────────────────────────────────

    def add_message(self, speaker: str, text: str) -> None:
        is_user = speaker.strip().lower() in ("you", "user")
        color   = _TEXT_USER if is_user else _TEXT_AI
        prefix  = f"{speaker}: " if speaker.strip() else ""

        lbl = Label(
            text=f"{prefix}{text}",
            font_name=_FONT_SB,
            font_size=_ff(37),
            color=color,
            halign="left",
            valign="top",
            size_hint_x=1,
            size_hint_y=None,
            height=_ff(44),
        )
        # Wrap at widget width; grow height to fit
        lbl.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(w, "height", ts[1] + _ff(6)),
        )
        self._inner.add_widget(lbl)
        Clock.schedule_once(lambda _dt: self._scroll_bottom(), 0.05)

    def clear_messages(self) -> None:
        self._inner.clear_widgets()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _scroll_bottom(self) -> None:
        self._scroll.scroll_y = 0   # 0 = bottom in Kivy's inverted scroll axis


# ──────────────────────────────────────────────────────────────────────────────
# VoiceSessionScreen
# ──────────────────────────────────────────────────────────────────────────────
class VoiceSessionScreen(BaseScreen):
    """Full-screen audio-agent session (Figma System States_2, node 1040:60).

    Public API mirrors the home screen so ``main.py`` can call the same
    methods regardless of which screen is active:
        show_listening_state()
        hide_listening_state()
        set_voice_session_state(state)
        update_amplitude(amp)
        update_say_bar_transcription(speaker, text)
        clear_say_bar_transcription()
        activate_say_bar() / deactivate_say_bar()
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._listening:  bool  = False
        self._amplitude:  float = 0.0
        self._wave_rings: _WaveRings      | None = None
        self._voice_pill: _VoiceStatePill | None = None
        self._battery:    _BatteryWidget  | None = None
        self._transcript: _TranscriptionBox | None = None
        self._voice_tick_ev: object | None = None
        self._build_ui()

    # ── build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout()

        # 1 · Background image  ──────────────────────────────────────────────
        bg_src = _fp("vs_bg.png")
        if bg_src:
            root.add_widget(Image(
                source=bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # 2 · Semi-transparent overlay  (rgba 255,255,255, 0.45) ────────────
        ov = Widget(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        with ov.canvas:
            _ovc  = Color(1, 1, 1, 0.45)   # noqa: F841
            _ovr  = Rectangle(pos=ov.pos, size=ov.size)
        ov.bind(
            pos=lambda w, p: setattr(_ovr, "pos",  p),
            size=lambda w, s: setattr(_ovr, "size", s),
        )
        root.add_widget(ov)

        # 3 · Wave rings  (centred on mic orb at Figma (630, 292)) ──────────
        #     Wave widget: 320 × 320 px centred on mic centre
        _ww = 320
        _wleft = 630 - _ww / 2    # = 470
        _wtop  = 292 - _ww / 2    # = 132

        self._wave_rings = _WaveRings(
            mic_r_px=61.0,    # half of 122 px mic button
            max_r_px=140.0,   # well beyond outer Figma ring (84 px)
            size_hint=(_sw(_ww), _sh(_ww)),
            pos_hint={"x": _x(_wleft), "y": _y(_wtop, _ww)},
        )
        root.add_widget(self._wave_rings)

        # 4 · Mic orb  (static PNG, 122 × 122 px at (569, 231)) ─────────────
        mic_src = _fp("vs_mic_btn.png")
        if not mic_src:
            mic_src = str(ASSETS_DIR / "home" / "figma" / "new_mic_btn.png")
        root.add_widget(Image(
            source=mic_src,
            size_hint=(_sw(122), _sh(122)),
            pos_hint={"x": _x(569), "y": _y(231, 122)},
            fit_mode="fill",
            allow_stretch=True,
            keep_ratio=False,
        ))

        # 5 · Transcription box  (22, 425)  1216 × 353 ──────────────────────
        self._transcript = _TranscriptionBox(
            size_hint=(_sw(1216), _sh(353)),
            pos_hint={"x": _x(22), "y": _y(425, 353)},
        )
        root.add_widget(self._transcript)

        # 6 · Voice-state pill  (910, 17)  222 × 47  (always visible here) ──
        self._voice_pill = _VoiceStatePill(
            size_hint=(_sw(222), _sh(47)),
            pos_hint={"x": _x(910), "y": _y(17, 47)},
        )
        self._voice_pill.opacity = 1.0   # override home-screen default of 0
        root.add_widget(self._voice_pill)

        # 7 · WiFi icon  (1147, 31)  29 × 20  — drawn as canvas vectors ────────
        root.add_widget(_WifiIcon(
            size_hint=(_sw(29), _sh(20)),
            pos_hint={"x": _x(1147), "y": _y(31, 20)},
        ))

        # 8 · Battery indicator  (1191, 30)  47 × 21 ─────────────────────────
        self._battery = _BatteryWidget(
            size_hint=(_sw(47), _sh(21)),
            pos_hint={"x": _x(1191), "y": _y(30, 21)},
        )
        root.add_widget(self._battery)

        # 9 · Back button  (top-left, same row as pill) ───────────────────────
        back = _BackButton(
            on_tap=self._on_back,
            size_hint=(_sw(140), _sh(47)),
            pos_hint={"x": _x(20), "y": _y(17, 47)},
        )
        root.add_widget(back)
        # The _BackButton widget renders via a child label that must be added:
        root.add_widget(back._lbl)

        self.add_widget(root)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_enter(self) -> None:
        self._listening = False
        self._amplitude = 0.0
        if self._wave_rings:
            self._wave_rings.start()
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
            self._voice_pill.opacity = 1.0

    def on_leave(self) -> None:
        if self._wave_rings:
            self._wave_rings.stop()
        self._stop_voice_tick()
        self._listening = False
        self._amplitude = 0.0

    # ── back button ────────────────────────────────────────────────────────────

    def _on_back(self) -> None:
        if self.manager:
            self.manager.current = "home"

    # ── voice-state API  (identical signature to home screen) ─────────────────

    def show_listening_state(self) -> None:
        self._listening = True
        self._amplitude = 0.0
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
            self._voice_pill.opacity = 1.0
        self._start_voice_tick()

    def hide_listening_state(self) -> None:
        """Session ended — navigate back to home automatically."""
        self._listening = False
        self._amplitude = 0.0
        self._stop_voice_tick()
        if self.manager and self.manager.current == "voice_session":
            self.manager.current = "home"

    def set_voice_session_state(self, state: str) -> None:
        if state == "listening":
            self.show_listening_state()

        elif state == "thinking":
            self._listening = False
            self._amplitude = 0.0
            if self._voice_pill:
                self._voice_pill.set_state_text("Thinking")
                self._voice_pill.opacity = 1.0
            self._stop_voice_tick()

        elif state == "speaking":
            self._listening = False
            self._amplitude = 0.0
            if self._voice_pill:
                self._voice_pill.set_state_text("Talking")
                self._voice_pill.opacity = 1.0
            self._stop_voice_tick()

        else:
            # "idle" or any unknown state → end session
            self.hide_listening_state()

    def update_amplitude(self, amp: float) -> None:
        if self._listening:
            self._amplitude = amp

    # ── say-bar API  (forwarded from home.py; same names, new behaviour) ──────

    def activate_say_bar(self) -> None:
        """No-op here: navigation is handled by home.py routing."""

    def deactivate_say_bar(self) -> None:
        """Session deactivated — navigate back to home."""
        if self.manager and self.manager.current == "voice_session":
            self.manager.current = "home"

    def update_say_bar_transcription(self, speaker: str, text: str) -> None:
        if self._transcript:
            self._transcript.add_message(speaker, text)

    def clear_say_bar_transcription(self) -> None:
        if self._transcript:
            self._transcript.clear_messages()

    # ── waveform tick ─────────────────────────────────────────────────────────

    def _start_voice_tick(self) -> None:
        if self._voice_tick_ev is None and self._voice_pill is not None:
            self._voice_tick_ev = Clock.schedule_interval(self._voice_tick, 1 / 30)

    def _stop_voice_tick(self) -> None:
        if self._voice_tick_ev is not None:
            self._voice_tick_ev.cancel()
            self._voice_tick_ev = None
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), 0.0)

    def _voice_tick(self, _dt) -> None:
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), self._amplitude)
