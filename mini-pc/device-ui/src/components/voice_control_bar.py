"""
VoiceControlBar — Global root-level overlay for voice session controls.

Shown whenever a realtime voice session is active; hidden at idle and on
recording/processing screens (where the voice session is suspended).

Layout (left → right):
    [End Session pill]  [Listening / Thinking / Talking pill + waveform]

Both pills are drawn to the exact same Figma spec (Meeting_1, node 1023:2065):
the "End Session" pill is Group #1233:125 (222×47) and the voice-state pill is
Frame "27" #1023:2068 (222×47) — identical bounding box, so both are rendered
with the same vector drawing technique (no raster image) and share one
Figma→display scale factor. This guarantees pixel-identical size/typography
on every screen, regardless of which screen the overlay is shown over.

The exit pill exits the current voice session and returns the user to the home
screen.  Tapping it triggers a subtle scale-down press animation.

Added to ``root_layout`` in main.py (above the ScreenManager, below the
QuickPanel) so it floats on every screen.  Touch events pass through to the
screen below except when the user taps directly on the pill container.
"""

from __future__ import annotations

import logging
import math

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import (
    Color, Ellipse, Line, PopMatrix, PushMatrix, RoundedRectangle, Scale,
)
from kivy.properties import NumericProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import DISPLAY_WIDTH, DISPLAY_HEIGHT

logger = logging.getLogger(__name__)

# ── Design constants (Figma 1260×800 coordinate space) ───────────────────────
_FW, _FH = 1260.0, 800.0

# Voice pill visual constants (matching home.py / _VoiceStatePill)
_PILL_BG = (0.980, 0.980, 0.980, 1.0)   # #FAFAFA
_PURPLE  = (0.427, 0.282, 0.800, 1.0)   # #6D48CC
_TEXT    = (0.227, 0.231, 0.239, 1.0)   # #3A3B3D
_SHADOW  = (0.463, 0.506, 0.498, 0.18)
_FONT_SB = "42dot-SB"

# "End Session" pill visual constants (Figma Group #1233:125 / Frame #1233:126)
_END_BG     = (0.957, 0.961, 0.969, 1.0)  # #F4F5F7
_END_STROKE = (1.0, 1.0, 1.0, 0.85)       # approximates the white gradient stroke
_END_SHADOW = (0.463, 0.506, 0.498, 0.3)  # rgba(118,129,127,0.3)
_END_TEXT   = (0.208, 0.224, 0.231, 1.0)  # #35393B
_END_ICON   = (0.996, 0.141, 0.0, 1.0)    # #FE2400
_FONT_REG   = "42dot-Sans"

# Screens where the bar must stay hidden (voice session suspended)
_HIDDEN_SCREENS: frozenset[str] = frozenset({"recording", "processing"})

# Figma coordinates of the original voice pill (home.py)
_PILL_X_FIG  = 867.0   # left edge of original voice pill in Figma px
_PILL_Y_FIG  = 17.0    # top edge in Figma px (from the Figma top)
_PILL_W_FIG  = 222.0
_PILL_H_FIG  = 47.0
_PILL_GAP    = 8.0     # gap between exit pill and voice pill (Figma px)

# "End Session" pill natural size (Figma Group #1233:125) — identical box to
# the voice pill, so both pills always render at the same height/width.
_END_W_FIG, _END_H_FIG = 222.0, 47.0


def _scale() -> float:
    """Uniform Figma→display scale factor."""
    return min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)


def _ff(fs: float) -> int:
    return max(6, round(fs * _scale()))


# ─────────────────────────────────────────────────────────────────────────────
# Animated 7-bar waveform  (self-contained copy matching home.py proportions)
# ─────────────────────────────────────────────────────────────────────────────
class _Waveform(Widget):
    _BAR_DATA = [
        (7.185,  8.625),
        (12.935, 14.375),
        (18.685, 22.999),
        (24.435, 34.499),
        (30.185, 22.999),
        (35.935, 14.375),
        (41.685, 8.625),
    ]
    _BAR_W  = 2.875
    _VB     = 46.0
    _CY_VB  = 23.0
    _PHASES = [3.0, 2.2, 1.4, 0.0, 1.4, 2.2, 3.0]

    def __init__(self, **kw):
        super().__init__(**kw)
        self._bar_cxy: list = []
        self._s = 1.0
        self._bw = 1.0
        with self.canvas:
            self._ci = Color(*_TEXT)
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
        self._s  = s
        self._bw = self._BAR_W * s
        self._bar_cxy = [(ox + cx * s, oy + self._CY_VB * s) for cx, _ in self._BAR_DATA]
        r = self._bw / 2
        for i, bar in enumerate(self._bars):
            cx, cy = self._bar_cxy[i]
            bh = self._BAR_DATA[i][1] * s
            bar.pos    = (cx - self._bw / 2, cy - bh / 2)
            bar.size   = (self._bw, bh)
            bar.radius = [r]

    def tick(self, t: float, amp: float) -> None:
        if not self._bars or not self._bar_cxy:
            return
        s, bw = self._s, self._bw
        r   = bw / 2
        amp = max(0.0, min(1.0, amp))
        for i, bar in enumerate(self._bars):
            cx, cy = self._bar_cxy[i]
            bh  = self._BAR_DATA[i][1] * s
            ph  = self._PHASES[i]
            idle  = 1.0 + 0.10 * math.sin(t * 3.0 + ph)
            spd   = 5.0 + amp * 12.0
            voice = amp * 1.2 * abs(math.sin(t * spd + ph))
            h_px  = bh * idle * (1.0 + voice)
            bar.pos    = (cx - bw / 2, cy - h_px / 2)
            bar.size   = (bw, h_px)
            bar.radius = [r]


# ─────────────────────────────────────────────────────────────────────────────
# Voice-state pill  (white capsule with dot + state text + waveform)
# ─────────────────────────────────────────────────────────────────────────────
class _VoicePill(FloatLayout):
    _PW, _PH = 222.0, 47.0

    def __init__(self, **kw):
        super().__init__(**kw)
        PW, PH = self._PW, self._PH
        with self.canvas.before:
            Color(*_SHADOW)
            self._shad = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
            Color(*_PILL_BG)
            self._bg   = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
        self.bind(pos=self._draw_bg, size=self._draw_bg)

        dot = Widget(size_hint=(17 / PW, 17 / PH), pos_hint={"x": 13 / PW, "y": 15 / PH})
        with dot.canvas:
            Color(*_PURPLE)
            _dot_ell = Ellipse(pos=dot.pos, size=dot.size)
        dot.bind(
            pos=lambda w, *_: setattr(_dot_ell, "pos",  w.pos),
            size=lambda w, *_: setattr(_dot_ell, "size", w.size),
        )
        self.add_widget(dot)

        self._lbl = Label(
            text="Listening",
            font_name=_FONT_SB,
            font_size=_ff(24.24),
            color=_TEXT,
            halign="left",
            valign="middle",
            size_hint=(102 / PW, 29 / PH),
            pos_hint={"x": 42 / PW, "y": 9 / PH},
        )
        self._lbl.bind(size=self._lbl.setter("text_size"))
        self.add_widget(self._lbl)

        self._wave = _Waveform(
            size_hint=(39 / PW, 29 / PH),
            pos_hint={"x": 170 / PW, "y": 9 / PH},
        )
        self.add_widget(self._wave)

    def _draw_bg(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        r = min(w, h) / 2
        self._shad.pos    = (x + 1, y - 4)
        self._shad.size   = (w + 2, h + 5)
        self._shad.radius = [r + 2]
        self._bg.pos    = (x, y)
        self._bg.size   = (w, h)
        self._bg.radius = [r]

    def set_state(self, state: str) -> None:
        text_map = {
            "listening": "Listening",
            "thinking":  "Thinking",
            "speaking":  "Talking",
        }
        self._lbl.text = text_map.get(state.lower(), "Listening")

    def tick(self, t: float, amp: float) -> None:
        self._wave.tick(t, amp)


# ─────────────────────────────────────────────────────────────────────────────
# End Session pill  (vector-drawn to the exact Figma spec — Group #1233:125)
# ─────────────────────────────────────────────────────────────────────────────
class _EndSessionPill(ButtonBehavior, FloatLayout):
    """Light capsule with a red icon + "End Session" caption.

    Drawn with the same vector technique as :class:`_VoicePill` (rather than a
    raster PNG) so its rendered size and font always scale in lockstep with
    the voice-state pill, on every screen and DPI.

    Figma spec (Group #1233:125, 222×47):
      • Frame #1233:126 — fill #F4F5F7, ~2px white stroke, radius = capsule,
        shadow rgba(118,129,127,0.3)
      • Red icon (#1233:129) — rel (27,16) 17×17, fill #FE2400
      • "End Session" text (#1233:128) — rel (59,9) 137×30, 42dot Sans
        Regular 25px, centered, #35393B
    """

    _PW, _PH = _END_W_FIG, _END_H_FIG

    btn_scale = NumericProperty(1.0)

    def __init__(self, on_tap=None, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap
        PW, PH = self._PW, self._PH

        with self.canvas.before:
            PushMatrix()
            self._sc = Scale(1.0, 1.0, 1.0)
            Color(*_END_SHADOW)
            self._shad = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
            Color(*_END_BG)
            self._bg = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[24])
            self._stroke_color = Color(*_END_STROKE)
            self._stroke = Line(rounded_rectangle=(0, 0, 0, 0, 24), width=2)
        with self.canvas.after:
            PopMatrix()
        self.bind(pos=self._draw_bg, size=self._draw_bg, btn_scale=self._sync_scale)

        icon = Widget(size_hint=(17 / PW, 17 / PH), pos_hint={"x": 27 / PW, "y": 16 / PH})
        with icon.canvas:
            Color(*_END_ICON)
            self._icon_rect = RoundedRectangle(pos=icon.pos, size=icon.size, radius=[3])
        icon.bind(
            pos=lambda w, *_: setattr(self._icon_rect, "pos", w.pos),
            size=lambda w, *_: setattr(self._icon_rect, "size", w.size),
        )
        self.add_widget(icon)

        self._lbl = Label(
            text="End Session",
            font_name=_FONT_REG,
            font_size=_ff(25),
            color=_END_TEXT,
            halign="center",
            valign="middle",
            size_hint=(137 / PW, 30 / PH),
            pos_hint={"x": 59 / PW, "y": 9 / PH},
        )
        self._lbl.bind(size=self._lbl.setter("text_size"))
        self.add_widget(self._lbl)

    def _draw_bg(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        r = min(w, h) / 2
        self._shad.pos    = (x + 1, y - 3)
        self._shad.size   = (w, h + 3)
        self._shad.radius = [r]
        self._bg.pos    = (x, y)
        self._bg.size   = (w, h)
        self._bg.radius = [r]
        self._stroke.rounded_rectangle = (x + 1, y + 1, w - 2, h - 2, max(2, r - 1))
        self._sync_scale()

    def _sync_scale(self, *_):
        cx, cy = self.center
        self._sc.origin = (cx, cy, 0)
        self._sc.x = self.btn_scale
        self._sc.y = self.btn_scale

    def on_press(self):
        Animation.cancel_all(self, "btn_scale")
        Animation(btn_scale=0.96, duration=0.08).start(self)

    def on_release(self):
        Animation.cancel_all(self, "btn_scale")
        Animation(btn_scale=1.0, duration=0.18, t="out_back").start(self)
        if self._on_tap:
            self._on_tap()


# ─────────────────────────────────────────────────────────────────────────────
# VoiceControlBar  (the public overlay widget)
# ─────────────────────────────────────────────────────────────────────────────
class VoiceControlBar(FloatLayout):
    """Full-screen FloatLayout overlay; only the pill row is interactive.

    Usage in main.py::

        self._voice_control_bar = VoiceControlBar(app=self)
        self.root_layout.add_widget(self._voice_control_bar)

        # When voice state changes:
        self._voice_control_bar.notify_state(new_state)

        # When screen changes:
        self._voice_control_bar.notify_screen(screen_name)

        # When audio amplitude arrives:
        self._voice_control_bar.update_amplitude(amp)
    """

    def __init__(self, app=None, **kw):
        super().__init__(**kw)
        self._app      = app
        self._visible  = False
        self._state    = "idle"
        self._screen   = ""
        self._t        = 0.0
        self._amp      = 0.0
        self._anim_ev  = None
        self.opacity   = 0.0

        s = _scale()

        # ── Voice state pill ────────────────────────────────────────────────
        # Both pills share the identical 222×47 Figma bounding box, so a
        # single reference height keeps them pixel-identical on every screen.
        common_h = round(_PILL_H_FIG * s)
        voice_h = common_h
        voice_w = round((_PILL_W_FIG / _PILL_H_FIG) * voice_h)
        self._voice_pill = _VoicePill(
            size_hint=(None, None),
            size=(voice_w, voice_h),
        )

        # ── End Session pill (vector-drawn, same Figma box as the voice pill) ─
        end_h = common_h
        end_w = round((_END_W_FIG / _END_H_FIG) * end_h)
        self._exit_pill = _EndSessionPill(
            on_tap=self._on_exit_tapped,
            size_hint=(None, None),
            size=(end_w, end_h),
        )

        # ── Row container ───────────────────────────────────────────────────
        gap = round(_PILL_GAP * s)
        self._row = BoxLayout(
            orientation="horizontal",
            spacing=gap,
            size_hint=(None, None),
            size=(end_w + gap + voice_w, voice_h),
        )
        self._row.add_widget(self._exit_pill)
        self._row.add_widget(self._voice_pill)
        self.add_widget(self._row)

        # Position the row after the layout pass so the surface size is known,
        # and re-pin whenever the overlay is resized (the live Kivy surface can
        # differ from the logical DISPLAY_WIDTH/HEIGHT under Windows DPI scaling).
        Clock.schedule_once(self._place_row, 0)
        self.bind(size=self._place_row, pos=self._place_row)

    # ── Positioning ──────────────────────────────────────────────────────────

    def _place_row(self, *_):
        """Size + pin the row top-right, matching the home-screen voice pill.

        Uses the overlay's ACTUAL size (the live Kivy surface) rather than the
        logical DISPLAY_WIDTH/HEIGHT constants. Under Windows DPI scaling the
        real surface is larger than the logical size, so computing absolute
        pixel offsets from the constants pinned the row near screen center. The
        rest of the UI positions off fractions of the live surface, so we do the
        same here to land top-right on every DPI (and on the device at 1:1).
        """
        W = self.width if self.width > 1 else DISPLAY_WIDTH
        H = self.height if self.height > 1 else DISPLAY_HEIGHT
        ox = oy = 0.0
        # Dock companion: anchor the pills INSIDE the floating 7" panel instead
        # of the full desktop window. Otherwise they pin to the screen's top edge
        # (outside the panel) and overlap the floating dock. The panel shares the
        # same 1260x800 design canvas, so the Figma math below places them
        # identically, just scaled to the panel.
        dc = getattr(self._app, "dock_controller", None)
        if dc is not None:
            try:
                rect = dc.panel_rect_for_overlay()
            except Exception:
                rect = None
            if rect:
                ox, oy, W, H = rect
        sa = min(W / _FW, H / _FH)

        # Re-derive sizes from the live surface using the same Figma reference
        # height, so both pills stay consistent across screens and DPI scales.
        common_h = max(1, round(_PILL_H_FIG * sa))
        voice_w = round((_PILL_W_FIG / _PILL_H_FIG) * common_h)
        end_w = round((_END_W_FIG / _END_H_FIG) * common_h)
        gap = round(_PILL_GAP * sa)
        self._voice_pill.size = (voice_w, common_h)
        self._exit_pill.size = (end_w, common_h)
        self._row.spacing = gap
        self._row.size = (end_w + gap + voice_w, common_h)
        self._voice_pill._lbl.font_size = max(6, round(24.24 * sa))
        self._exit_pill._lbl.font_size = max(6, round(25 * sa))

        # Right edge of the home-screen voice pill (Figma), as a fraction of W,
        # offset by the frame origin (0,0 for full window; panel corner in dock).
        right_px = ox + (_PILL_X_FIG + _PILL_W_FIG) / _FW * W
        # Top edge → Kivy y-from-bottom.
        top_y_px = oy + H - (_PILL_Y_FIG / _FH * H)
        self._row.x = right_px - self._row.width
        self._row.y = top_y_px - self._row.height

    def reanchor(self) -> None:
        """Re-evaluate visibility + re-pin the pill row.

        Called by the dock whenever its floating panel opens, moves, or closes.
        Re-running ``_refresh`` first lets the pills appear the moment the panel
        opens even if the voice state went active before the panel did (the
        screen-change and dock-state updates race on startup), and hides them
        again the moment the panel closes.
        """
        self._refresh()
        if self._visible:
            self._place_row()

    def _dock_without_panel(self) -> bool:
        """True in Windows dock mode while the floating 7" panel is closed.

        In that state there is no panel to anchor to, so showing the pills would
        pin them to the top-right of the whole desktop overlay (outside the app).
        The pills must stay hidden until the panel is open again.
        """
        dc = getattr(self._app, "dock_controller", None)
        if dc is None:
            return False
        try:
            return dc.panel_rect_for_overlay() is None
        except Exception:
            return False

    # ── Touch pass-through ───────────────────────────────────────────────────

    def on_touch_down(self, touch):
        if self._visible and self._row.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        return False

    def on_touch_move(self, touch):
        if self._visible and self._row.collide_point(*touch.pos):
            return super().on_touch_move(touch)
        return False

    def on_touch_up(self, touch):
        if self._visible and self._row.collide_point(*touch.pos):
            return super().on_touch_up(touch)
        return False

    # ── Public API ───────────────────────────────────────────────────────────

    def notify_state(self, state: str) -> None:
        """Called from main.py whenever the realtime voice runtime state changes."""
        self._state = (state or "idle").strip().lower()
        self._refresh()
        self._suppress_current_screen_local_pill()
        if self._visible and self._state not in ("idle", ""):
            self._voice_pill.set_state(self._state)

    def notify_screen(self, screen_name: str) -> None:
        """Called from main.py whenever the ScreenManager switches screens."""
        self._screen = screen_name or ""
        self._refresh()
        self._suppress_current_screen_local_pill()

    def update_amplitude(self, amp: float) -> None:
        """Receive audio amplitude (0.0–1.0) from the voice pipeline."""
        self._amp = max(0.0, min(1.0, amp))

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _refresh(self) -> None:
        active       = self._state not in ("idle", "")
        hidden_screen = self._screen in _HIDDEN_SCREENS
        should_show  = active and not hidden_screen and not self._dock_without_panel()

        if should_show and not self._visible:
            self._show()
        elif not should_show and self._visible:
            self._hide()

    def _show(self) -> None:
        self._visible = True
        Animation.cancel_all(self, "opacity")
        # Re-anchor now: in dock mode the 7" panel may have opened after this bar
        # was created, so the panel rect is only known at show time.
        self._place_row()
        # Instant (no fade): both pills must appear together, at once, with
        # no perceptible transition from a solo/legacy pill.
        self.opacity = 1.0
        self._start_waveform()

    def _hide(self) -> None:
        self._visible = False
        Animation.cancel_all(self, "opacity")
        Animation(opacity=0.0, duration=0.20).start(self)
        self._stop_waveform()

    def _start_waveform(self) -> None:
        if self._anim_ev is None:
            self._anim_ev = Clock.schedule_interval(self._tick, 1.0 / 30.0)

    def _stop_waveform(self) -> None:
        if self._anim_ev is not None:
            self._anim_ev.cancel()
            self._anim_ev = None

    def _tick(self, dt: float) -> None:
        self._t += dt
        self._voice_pill.tick(self._t, self._amp)

    def _on_exit_tapped(self) -> None:
        app = self._app
        if app is None:
            return
        try:
            app._end_realtime_voice_session()
        except Exception:
            logger.debug("VoiceControlBar: end session error", exc_info=True)
        try:
            app.goto_screen("home")
        except Exception:
            logger.debug("VoiceControlBar: goto_screen error", exc_info=True)

    # ── Public hit-test helper for top-edge controls ────────────────────────

    def is_touch_on_controls(self, x: float, y: float) -> bool:
        """Return True when a touch is directly on the visible pill row."""
        return self._visible and self._row.collide_point(x, y)

    # ── Local pill suppression (avoid double-render with legacy per-screen UI) ──

    def _suppress_current_screen_local_pill(self) -> None:
        """Hide any legacy screen-local listening pill while global bar is active."""
        if not self._visible:
            return
        app = self._app
        if app is None:
            return
        sm = getattr(app, "screen_manager", None)
        if sm is None:
            return
        try:
            scr = sm.get_screen(sm.current)
        except Exception:
            return
        # Legacy screens don't all use the same attribute name for their local
        # listening pill; hide whichever variant exists.
        hidden_any = False
        for attr in ("_voice_pill", "_pill", "_status_pill"):
            pill = getattr(scr, attr, None)
            if pill is None:
                continue
            try:
                pill.opacity = 0.0
                hidden_any = True
            except Exception:
                logger.debug(
                    "VoiceControlBar: failed to hide local pill '%s'",
                    attr,
                    exc_info=True,
                )
        if not hidden_any:
            return
