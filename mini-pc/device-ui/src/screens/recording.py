"""Recording screen — Figma ``1031:58`` (dvqlN0JtWQODt6jYbTrbDG, "Copy").

Light-theme recording UI drawn entirely with Kivy primitives (no PNG assets):

  * slate wash background
  * centre orb (navy disc + purple ring) with a 7-bar voice waveform that
    reacts to live mic levels (lavender → deep-purple gradient, rounded caps)
  * top-centre status: red/grey dot + "Recording...." / "Recording Paused"
    + "Started at hh:mm AM"
  * purple ``HH:MM:SS`` timer
  * bottom controls: round **Pause/Play** capsule (in-place toggle) and a
    "Stop Recording" capsule

Pause/Play is an *in-place* toggle: tapping Pause pauses the recording and the
button becomes a Play button (status text → "Recording Paused"); tapping Play
resumes and the button becomes Pause again. No modal dialog.

Lifecycle hooks called from main.py: on_enter / on_leave, on_paused /
on_resumed, on_audio_level, on_audio_segment.
"""

from __future__ import annotations

import logging
import math
import random
import time

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import (
    Color,
    Ellipse,
    Line,
    PopMatrix,
    PushMatrix,
    RoundedRectangle,
    Scale,
    Translate,
    Triangle,
)
from kivy.properties import NumericProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from page_swipe import PageSwipeController

from components.device_status_bar import DeviceStatusBar
from config import display_now
from frame19_layout import (
    CANVAS_H,
    BG_BOT,
    BG_TOP,
    BTN_PAUSE,
    canvas_box,
    COL_BATT_GREEN,
    COL_PURPLE,
    COL_REC_GREY,
    COL_REC_RED,
    COL_TEXT,
    ORB,
    ORB_FILL,
    ORB_RING,
    PILL_BORDER,
    PILL_FILL,
    PILL_SHADOW,
    REC_DOT,
    REC_LABEL,
    REC_LABEL_FS_RATIO,
    STARTED_FS_RATIO,
    STARTED_LABEL,
    STATUS_BAR,
    STOP_FS_RATIO,
    STOP_PILL,
    TIMER,
    TIMER_FS_RATIO,
    WAVE_BOT,
    WAVE_TOP,
    WAVEBAR,
    font_px,
    kivy_hints,
    scaled_canvas,
)
from screens.base_screen import BaseScreen
from ui_bg import attach_swirl_bg, vertical_gradient_texture

logger = logging.getLogger(__name__)

_FONT = "42dot-Sans"
_FONT_SB = "42dot-SB"
_FONT_MED = "42dot-Med"

# READY pre-state layout — Figma ``1225:34`` ("Meeting_2"). Same 1260×800 canvas
# as the active recording state, so the orb / waveform / status bar stay in place
# and the page simply evolves from READY → RECORDING.
READY_TITLE = canvas_box(453.0, 92.0, 354.0, 55.0)        # "Ready to record.."
READY_SUBTITLE = canvas_box(280.0, 569.0, 660.0, 30.0)    # supporting subtitle
START_BTN = canvas_box(415.0, 635.0, 430.0, 88.0)         # Start Recording CTA
READY_TITLE_FS_RATIO = 46.0 / CANVAS_H
READY_SUBTITLE_FS_RATIO = 25.0 / CANVAS_H
START_FS_RATIO = 40.0 / CANVAS_H
COL_SUBTITLE = (106 / 255, 104 / 255, 111 / 255, 1.0)     # #6A686F


class _Orb(Widget):
    """Dark-navy disc with a purple ring stroke — the recording centrepiece."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(*ORB_FILL)
            self._disc = Ellipse(pos=self.pos, size=self.size)
            self._ring_color = Color(*ORB_RING)
            self._ring = Line(circle=(0, 0, 0), width=2.0)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._disc.pos = self.pos
        self._disc.size = self.size
        cx = self.x + self.width / 2.0
        cy = self.y + self.height / 2.0
        r = min(self.width, self.height) / 2.0
        self._ring.width = max(1.5, self.width * 0.014)
        self._ring.circle = (cx, cy, r - self._ring.width / 2.0)


class _Wavebar(Widget):
    """Voice waveform — 7 rounded bars with a vertical purple gradient.

    Acts like a VU meter: bars stay FLAT while silent and rise from flat in
    direct proportion to the live mic level fed via :meth:`feed_level`;
    speech amplifies the centre bars more than the edges (bell envelope).
    """

    _SILENCE_THRESHOLD = 0.04
    _FLAT_RATIO = 0.10

    # Figma "Group 194" waveform geometry (node 1225:55 / 1031:66). Bars sit in
    # a 166 × 136 box; left offsets, width and heights are taken verbatim so the
    # READY page and the active RECORDING state render the *same* bars (the orb
    # waveform must stay visually continuous through the morph).
    _BOX_W_FIG = 166.0
    _BOX_H_FIG = 136.0
    _BAR_W_FIG = 10.0
    _BAR_X_FIG = (0.0, 26.0, 52.0, 78.0, 104.0, 130.0, 156.0)
    _BAR_H_FIG = (57.0, 73.0, 136.0, 104.0, 42.0, 89.0, 58.0)

    def __init__(self, *, n_bars: int = 7, **kwargs):
        super().__init__(**kwargs)
        self.n_bars = n_bars
        self._bar_max_ratio = 1.0
        # The Figma waveform envelope (normalised bar heights). Used both as the
        # static READY shape and as the rest shape the live animation breathes
        # around, so activating the recording waveform never causes a jump.
        if n_bars == len(self._BAR_H_FIG):
            self._envelope = [h / self._BOX_H_FIG for h in self._BAR_H_FIG]
        else:
            centre = (n_bars - 1) / 2.0
            self._envelope = [
                0.24 + 0.76 * max(0.0, math.cos(((i - centre) / centre) * math.pi / 2.0))
                for i in range(n_bars)
            ]
        self._levels = list(self._envelope)
        self._latest_audio = 0.0
        self._anim_event = None
        self._is_active = False
        self._jitter = [random.uniform(0.65, 1.0) for _ in range(n_bars)]
        self._idle_phase = [random.uniform(0, math.tau) for _ in range(n_bars)]
        self._tex = vertical_gradient_texture(WAVE_TOP, WAVE_BOT)

        with self.canvas:
            self._color = Color(1, 1, 1, 1)
            self._bars = [
                RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[5], texture=self._tex)
                for _ in range(n_bars)
            ]
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    # -- public API ----------------------------------------------------
    def feed_level(self, level: float) -> None:
        try:
            v = float(level)
        except (TypeError, ValueError):
            return
        v = max(0.0, min(1.0, v))
        self._latest_audio = max(self._latest_audio * 0.55, v)

    def start(self) -> None:
        if self._anim_event is None:
            self._anim_event = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self) -> None:
        if self._anim_event is not None:
            self._anim_event.cancel()
            self._anim_event = None

    def start_voice(self) -> None:
        self._is_active = True
        self._color.rgba = (1, 1, 1, 1)

    def stop_voice(self) -> None:
        self._is_active = False
        self._latest_audio = 0.0
        self._color.rgba = (1, 1, 1, 0.5)

    def show_static(self) -> None:
        """Freeze the bars at the Figma rest shape (used by the READY page)."""
        self.stop()
        self._is_active = False
        self._latest_audio = 0.0
        self._color.rgba = (1, 1, 1, 1)
        self._levels = list(self._envelope)
        self._redraw()

    # -- tick / draw ---------------------------------------------------
    def _tick(self, dt: float) -> None:
        n = self.n_bars
        if n <= 1:
            return
        voice = self._is_active and self._latest_audio > self._SILENCE_THRESHOLD
        t = time.monotonic()
        for i in range(n):
            env = self._envelope[i]
            if voice:
                # Rise from the rest shape in proportion to the live mic level
                # (taller bars = louder voice) while keeping the Figma envelope.
                target = env * (0.6 + 0.6 * self._latest_audio) * self._jitter[i]
                target = max(env * 0.45, min(1.0, target))
            else:
                # At rest the waveform stays alive but subtle — a slow breathe
                # around the Figma shape rather than collapsing flat.
                breathe = 0.94 + 0.06 * math.sin(t * 1.6 + self._idle_phase[i])
                target = env * breathe
            self._levels[i] += (target - self._levels[i]) * 0.35
        self._latest_audio *= 0.93
        if voice and random.random() < 0.18:
            self._jitter[random.randrange(n)] = random.uniform(0.6, 1.0)
        self._redraw()

    def _redraw(self) -> None:
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        sx = w / self._BOX_W_FIG
        bar_w = max(1.0, self._BAR_W_FIG * sx)
        max_h = h * self._bar_max_ratio
        cy = self.y + h / 2.0
        radius = bar_w / 2.0
        for i, rect in enumerate(self._bars):
            if i >= len(self._BAR_X_FIG):
                break
            bar_h = max(bar_w, max_h * self._levels[i])
            x = self.x + self._BAR_X_FIG[i] * sx
            rect.pos = (x, cy - bar_h / 2.0)
            rect.size = (bar_w, bar_h)
            rect.radius = [radius]


class _TimerDigits(Widget):
    """Steady ``HH:MM:SS`` display split into fixed-width cells (no jitter)."""

    # Cell widths as a fraction of the TIMER box (400px on the Figma canvas).
    # Sized so "00:01:21" at 55px renders compact like the design (~260px wide)
    # instead of spreading digits across the whole box.
    _DIGIT_W_RATIO = 0.09
    _SEP_W_RATIO = 0.05

    def __init__(self, *, fs_ratio: float, color: tuple, **kwargs):
        super().__init__(**kwargs)
        self._fs_ratio = fs_ratio
        self._digit_labels: list[Label] = []
        self._sep_labels: list[Label] = []
        for i in range(8):
            is_sep = i in (2, 5)
            lbl = Label(
                text=":" if is_sep else "0",
                font_name=_FONT,
                bold=True,
                color=color,
                halign="center",
                valign="middle",
                markup=False,
                size_hint=(None, None),
            )
            lbl.bind(size=lbl.setter("text_size"))
            self.add_widget(lbl)
            (self._sep_labels if is_sep else self._digit_labels).append(lbl)
        self.bind(pos=self._sync_cells, size=self._sync_cells)

    def _sync_cells(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        digit_w = w * self._DIGIT_W_RATIO
        sep_w = w * self._SEP_W_RATIO
        cells: list[tuple[float, float]] = []
        cx = x
        for i in range(8):
            cw = sep_w if i in (2, 5) else digit_w
            cells.append((cx, cw))
            cx += cw
        leftover = w - (cx - x)
        if abs(leftover) > 0.5:
            pad = leftover / 2.0
            cells = [(c[0] + pad, c[1]) for c in cells]
        d_idx = s_idx = 0
        for i, (lx, lw) in enumerate(cells):
            if i in (2, 5):
                lbl = self._sep_labels[s_idx]
                s_idx += 1
            else:
                lbl = self._digit_labels[d_idx]
                d_idx += 1
            lbl.size = (lw, h)
            lbl.pos = (lx, y)

    def set_text(self, hms: str) -> None:
        digits = [c for c in (hms or "") if c.isdigit()]
        if len(digits) < 6:
            digits = ["0"] * (6 - len(digits)) + digits
        elif len(digits) > 6:
            digits = digits[-6:]
        for i, d in enumerate(digits):
            self._digit_labels[i].text = d

    @property
    def font_size(self) -> float:
        return self._digit_labels[0].font_size if self._digit_labels else 0

    @font_size.setter
    def font_size(self, value: float) -> None:
        for lbl in self._digit_labels + self._sep_labels:
            lbl.font_size = value


class _StatusDot(Widget):
    """Red recording dot (breathing) / grey paused dot."""

    _BLINK_PERIOD_S = 1.2
    _BLINK_MIN_A = 0.45

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._active_rgb = COL_REC_RED[:3]
        self._is_recording = True
        self._blink_event = None
        self._blink_phase = 0.0
        with self.canvas:
            self._color = Color(*COL_REC_RED)
            self._ellipse = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._ellipse.pos = self.pos
        self._ellipse.size = self.size

    def set_recording(self, active: bool) -> None:
        self._is_recording = bool(active)
        if self._is_recording:
            self._color.rgba = (*self._active_rgb, 1.0)
            self._start_blink()
        else:
            self._stop_blink()
            self._color.rgba = COL_REC_GREY

    def _start_blink(self) -> None:
        if self._blink_event is None:
            self._blink_phase = 0.0
            self._blink_event = Clock.schedule_interval(self._tick_blink, 1 / 30.0)

    def _stop_blink(self) -> None:
        if self._blink_event is not None:
            self._blink_event.cancel()
            self._blink_event = None

    def _tick_blink(self, dt: float) -> None:
        if not self._is_recording:
            return
        self._blink_phase = (self._blink_phase + dt) % self._BLINK_PERIOD_S
        s = 0.5 * (1.0 + math.sin(2.0 * math.pi * self._blink_phase / self._BLINK_PERIOD_S))
        alpha = self._BLINK_MIN_A + (1.0 - self._BLINK_MIN_A) * s
        self._color.rgba = (*self._active_rgb, alpha)

    def stop_blink(self) -> None:
        self._stop_blink()


class _RoundButton(ButtonBehavior, Widget):
    """Light round capsule (#F4F5F7 + white border + soft shadow) drawing a
    purple Pause (two bars) or Play (triangle) icon, toggled via
    :meth:`set_paused`."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._show_play = False
        with self.canvas:
            Color(*PILL_SHADOW)
            self._shadow = Ellipse(pos=self.pos, size=self.size)
            Color(*PILL_FILL)
            self._disc = Ellipse(pos=self.pos, size=self.size)
            self._border_color = Color(*PILL_BORDER)
            self._border = Line(circle=(0, 0, 0), width=2.0)
            # Pause bars.
            self._pause_color = Color(*COL_PURPLE)
            self._bar_l = RoundedRectangle(pos=(0, 0), size=(0, 0), radius=[3])
            self._bar_r = RoundedRectangle(pos=(0, 0), size=(0, 0), radius=[3])
            # Play triangle (filled; vertices set in _sync).
            self._play_color = Color(*COL_PURPLE)
            self._play = Triangle(points=[0, 0, 0, 0, 0, 0])
        self.bind(pos=self._sync, size=self._sync)

    def set_paused(self, paused: bool) -> None:
        """When paused → show the Play icon; otherwise show Pause bars."""
        self._show_play = bool(paused)
        self._sync()

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        self._shadow.pos = (x, y - max(2.0, h * 0.03))
        self._shadow.size = (w, h)
        self._disc.pos = (x, y)
        self._disc.size = (w, h)
        cx, cy = x + w / 2.0, y + h / 2.0
        r = min(w, h) / 2.0
        bw = max(1.5, w * 0.022)
        self._border.width = bw
        self._border.circle = (cx, cy, r - bw / 2.0)

        # Figma pause glyph: two thick rounded bars (~11px wide on an 88px
        # button) with a gap roughly equal to one bar width.
        bar_w = max(3.0, w * 0.115)
        bar_h = max(4.0, h * 0.38)
        gap = bar_w * 0.85
        tri_h = h * 0.40
        tri_w = tri_h * 0.92
        left = cx - tri_w * 0.35
        self._play.points = [
            left, cy + tri_h / 2.0,
            left, cy - tri_h / 2.0,
            left + tri_w, cy,
        ]
        if self._show_play:
            self._pause_color.a = 0.0
            self._play_color.a = 1.0
            self._bar_l.size = (0, 0)
            self._bar_r.size = (0, 0)
        else:
            self._pause_color.a = 1.0
            self._play_color.a = 0.0
            self._bar_l.pos = (cx - gap / 2.0 - bar_w, cy - bar_h / 2.0)
            self._bar_l.size = (bar_w, bar_h)
            self._bar_l.radius = [bar_w / 2.0]
            self._bar_r.pos = (cx + gap / 2.0, cy - bar_h / 2.0)
            self._bar_r.size = (bar_w, bar_h)
            self._bar_r.radius = [bar_w / 2.0]


class _StopPill(ButtonBehavior, Widget):
    """Light capsule with a red square + "Stop Recording" caption."""

    def __init__(self, *, fs_ratio: float, **kwargs):
        super().__init__(**kwargs)
        self._fs_ratio = fs_ratio
        with self.canvas:
            Color(*PILL_SHADOW)
            self._shadow = RoundedRectangle(pos=self.pos, size=self.size, radius=[44])
            Color(*PILL_FILL)
            self._fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[44])
            self._border_color = Color(*PILL_BORDER)
            self._border = Line(rounded_rectangle=(0, 0, 0, 0, 44), width=2.0)
            Color(*COL_REC_RED)
            self._square = RoundedRectangle(pos=(0, 0), size=(0, 0), radius=[6])
        self._label = Label(
            text="Stop Recording",
            font_name=_FONT_SB,
            color=COL_TEXT,
            halign="center",
            valign="middle",
            size_hint=(None, None),
        )
        self._label.bind(size=self._label.setter("text_size"))
        self.add_widget(self._label)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        r = h / 2.0
        self._shadow.pos = (x, y - max(2.0, h * 0.03))
        self._shadow.size = (w, h)
        self._shadow.radius = [r]
        self._fill.pos = (x, y)
        self._fill.size = (w, h)
        self._fill.radius = [r]
        bw = max(1.5, h * 0.022)
        self._border.width = bw
        self._border.rounded_rectangle = (x, y, w, h, r)
        sq = h * 0.33
        sq_x = x + w * 0.115
        self._square.pos = (sq_x, y + (h - sq) / 2.0)
        self._square.size = (sq, sq)
        self._square.radius = [sq * 0.2]
        # Caption centred in the remaining space to the right of the square.
        self._label.pos = (sq_x + sq, y)
        self._label.size = (w - (sq_x + sq - x) - w * 0.06, h)

    @property
    def font_size(self) -> float:
        return self._label.font_size

    @font_size.setter
    def font_size(self, value: float) -> None:
        self._label.font_size = value


class _StartButton(ButtonBehavior, Widget):
    """READY-state CTA capsule — Figma ``1225:70`` (Frame 22).

    Light ``#F4F5F7`` capsule with a soft white top-stroke + drop shadow and a
    centred "Start Recording" caption. Tapping it plays a brief tactile press
    (scale-down → soft spring back) before firing ``on_fire``.
    """

    # NB: must NOT be named ``press`` — Kivy would route changes to an
    # ``on_press`` method, colliding with ButtonBehavior's ``on_press`` event
    # (and crashing with a signature mismatch when the scale animates).
    press_scale = NumericProperty(1.0)

    def __init__(self, *, fs_ratio: float, on_fire=None, suppress=None, **kwargs):
        super().__init__(**kwargs)
        self._fs_ratio = fs_ratio
        self._on_fire = on_fire
        self._suppress = suppress
        with self.canvas.before:
            PushMatrix()
            self._scale = Scale(1, 1, 1)
        with self.canvas:
            Color(*PILL_SHADOW)
            self._shadow = RoundedRectangle(pos=self.pos, size=self.size, radius=[44])
            Color(*PILL_FILL)
            self._fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[44])
            self._border_color = Color(*PILL_BORDER)
            self._border = Line(rounded_rectangle=(0, 0, 0, 0, 44), width=2.0)
        with self.canvas.after:
            PopMatrix()
        self._label = Label(
            text="Start Recording",
            font_name=_FONT,
            color=COL_TEXT,
            halign="center",
            valign="middle",
            size_hint=(None, None),
        )
        self._label.bind(size=self._label.setter("text_size"))
        self.add_widget(self._label)
        self.bind(pos=self._sync, size=self._sync, press_scale=self._sync_scale)

    def _sync_scale(self, *_):
        cx, cy = self.center
        self._scale.origin = (cx, cy, 0)
        self._scale.x = self.press_scale
        self._scale.y = self.press_scale

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        r = h / 2.0
        self._shadow.pos = (x, y - max(2.0, h * 0.03))
        self._shadow.size = (w, h)
        self._shadow.radius = [r]
        self._fill.pos = (x, y)
        self._fill.size = (w, h)
        self._fill.radius = [r]
        bw = max(1.5, h * 0.022)
        self._border.width = bw
        self._border.rounded_rectangle = (x, y, w, h, r)
        self._label.pos = (x, y)
        self._label.size = (w, h)
        self._sync_scale()

    @property
    def font_size(self) -> float:
        return self._label.font_size

    @font_size.setter
    def font_size(self, value: float) -> None:
        self._label.font_size = value

    def on_touch_down(self, touch):
        # While disabled (the RECORDING state) this button overlaps the Pause /
        # Stop controls. A disabled Kivy widget normally *absorbs* touches that
        # land on it, which would swallow taps meant for Stop — so stay fully
        # transparent to touches here and let the controls beneath respond.
        if self.disabled:
            return False
        return super().on_touch_down(touch)

    # Phase 1 — button press: slight scale-down then soft spring release.
    def on_press(self):
        if self.disabled:
            return
        Animation.cancel_all(self, "press_scale")
        Animation(press_scale=0.96, duration=0.12, t="out_quad").start(self)

    def on_release(self):
        if self.disabled:
            return
        Animation.cancel_all(self, "press_scale")
        Animation(press_scale=1.0, duration=0.20, t="out_back").start(self)
        # A back-swipe that started on this button must not also fire the morph.
        if self._suppress is not None and self._suppress():
            return
        if self._on_fire is not None:
            self._on_fire(self)


class _StatusBar(Widget):
    """Minimal top-right wifi + battery glyphs (Group 203).

    The wifi is drawn the way the Figma asset looks — a *solid* glyph made of
    alternating dark/background wedges (stroked arcs fuse into a blob at this
    small size). The gap colour matches the light bg behind the status bar.
    """

    _GAP = (0.937, 0.945, 0.955, 1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            # Solid wifi: dark wedge → gap wedge → dark wedge → gap wedge + dot.
            self._wedges: list[Ellipse] = []
            for col in (COL_TEXT, self._GAP, COL_TEXT, self._GAP):
                Color(*col)
                self._wedges.append(
                    Ellipse(pos=(0, 0), size=(0, 0), angle_start=-50, angle_end=50)
                )
            Color(*COL_TEXT)
            self._wifi_dot = Ellipse(pos=(0, 0), size=(0, 0))
            self._batt = Line(rounded_rectangle=(0, 0, 0, 0, 2), width=1.8)
            self._batt_tip = Line(points=[], width=1.8, cap="round")
            # Figma battery fill is green (Group 203).
            Color(*COL_BATT_GREEN)
            self._batt_fill = RoundedRectangle(pos=(0, 0), size=(0, 0), radius=[2])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        self._batt.width = max(1.5, h * 0.09)
        self._batt_tip.width = max(1.5, h * 0.09)
        # Solid upward-opening wifi wedges + base dot.
        cx = x + w * 0.18
        base = y + h * 0.08
        big_r = h * 0.85
        for ell, k in zip(self._wedges, (1.0, 0.72, 0.50, 0.27)):
            r = big_r * k
            ell.pos = (cx - r, base - r)
            ell.size = (2 * r, 2 * r)
        d = big_r * 0.34
        self._wifi_dot.pos = (cx - d / 2, base - d / 2)
        self._wifi_dot.size = (d, d)
        # Battery on the right — green fill nearly full like the design.
        bw = w * 0.42
        bh = h * 0.62
        bx = x + w - bw - w * 0.06
        by = y + (h - bh) / 2.0
        self._batt.rounded_rectangle = (bx, by, bw, bh, 3)
        self._batt_tip.points = [bx + bw + 2.5, by + bh * 0.3, bx + bw + 2.5, by + bh * 0.7]
        pad = max(1.5, bh * 0.12)
        self._batt_fill.pos = (bx + pad, by + pad)
        self._batt_fill.size = (max(1.0, (bw - 2 * pad) * 0.92), bh - 2 * pad)


class RecordingScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.elapsed_seconds = 0
        self.timer_event = None
        self._is_paused = False
        self._rec_base_elapsed = 0.0
        self._rec_active_start = None
        # READY / RECORDING state machine.
        self.enter_ready_next = False        # set by the Home swipe before entry
        self._mode = "recording"             # "ready" | "recording"
        self._morphing = False
        self._recording_active = False
        self._page_tx = None                 # transform-only page translate
        self._build_ui()
        # Interactive back-swipe (Start-Recording READY → Home), mirror of the
        # Home → Start-Recording reveal. Only armed while in the READY state.
        self._back_pager = PageSwipeController(
            self,
            "home",
            direction=-1,
            prepare_dest=lambda dest: getattr(dest, "prime_preview", lambda: None)(),
            commit=lambda: self.app.goto_screen("home", transition="none"),
            can_start=lambda: (
                self._mode == "ready"
                and not self._morphing
                and not self._recording_active
            ),
        )

    @property
    def is_recording_active(self) -> bool:
        """True once the in-place morph (or a direct entry) has begun recording."""
        return self._recording_active

    def _recording_mode(self) -> str:
        mode = (getattr(self.app, "current_recording_mode", "meeting") or "meeting").strip().lower()
        return "note" if mode == "note" else "meeting"

    def _recording_label_text(self) -> str:
        return "Taking notes...." if self._recording_mode() == "note" else "Recording...."

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self._root = FloatLayout(size_hint=(1, 1))
        attach_swirl_bg(self._root, BG_TOP, BG_BOT)
        self._root.bind(size=self._on_root_resize)

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        # Top-right hardware-aware status bar.
        self._canvas.add_widget(DeviceStatusBar(
            debug_location="recording.py:DeviceStatusBar",
            **kivy_hints(STATUS_BAR),
        ))

        # Centre orb + waveform.
        self._canvas.add_widget(_Orb(**kivy_hints(ORB)))
        self.wavebar = _Wavebar(**kivy_hints(WAVEBAR))
        self._canvas.add_widget(self.wavebar)

        # Status group (dot + Recording.... + Started at …).
        # The dot is positioned dynamically just before the caption text — the
        # caption length changes ("Recording...." vs "Recording Paused"), so a
        # fixed Figma x would overlap the longer string.
        dot_box = kivy_hints(REC_DOT)
        self.status_dot = _StatusDot(size_hint=dot_box["size_hint"])
        self._canvas.add_widget(self.status_dot)
        self.rec_label = self._add_label(
            "Recording....", REC_LABEL, REC_LABEL_FS_RATIO, COL_TEXT, bold=False,
            font_name=_FONT_SB,
        )
        self.rec_label.bind(
            text=self._sync_rec_dot,
            pos=self._sync_rec_dot,
            size=self._sync_rec_dot,
            font_size=self._sync_rec_dot,
        )
        self.status_dot.bind(size=self._sync_rec_dot)
        self.started_label = self._add_label(
            "Started at --:-- --", STARTED_LABEL, STARTED_FS_RATIO, COL_TEXT, bold=False,
        )

        # Purple timer.
        self.timer_label = _TimerDigits(fs_ratio=TIMER_FS_RATIO, color=COL_PURPLE, **kivy_hints(TIMER))
        self.timer_label._fs_ratio = TIMER_FS_RATIO  # noqa: SLF001
        self._canvas.add_widget(self.timer_label)

        # Bottom controls — pause/play toggle + stop capsule.
        self.pause_btn = _RoundButton(**kivy_hints(BTN_PAUSE))
        self.pause_btn.bind(on_release=self._on_toggle_pause)
        self._canvas.add_widget(self.pause_btn)

        self.stop_pill = _StopPill(fs_ratio=STOP_FS_RATIO, **kivy_hints(STOP_PILL))
        self.stop_pill.bind(on_release=self._on_stop)
        self._canvas.add_widget(self.stop_pill)

        # ── READY pre-state widgets (Figma 1225:34) ──────────────────────────
        # Drawn in the same slots so the morph is a pure cross-fade in place.
        self.ready_title = self._add_label(
            "Ready to record..", READY_TITLE, READY_TITLE_FS_RATIO, COL_TEXT,
            bold=False, font_name=_FONT,
        )
        self.ready_subtitle = self._add_label(
            "Tap the below button when you are ready to start meeting",
            READY_SUBTITLE, READY_SUBTITLE_FS_RATIO, COL_SUBTITLE,
            bold=False, font_name=_FONT_MED,
        )
        self.start_btn = _StartButton(
            fs_ratio=START_FS_RATIO,
            on_fire=self._on_start_pressed,
            suppress=lambda: (
                getattr(self, "_back_pager", None) is not None
                and self._back_pager.is_engaged
            ),
            **kivy_hints(START_BTN),
        )
        self._canvas.add_widget(self.start_btn)

        self.add_widget(self._root)
        # Default visual state is the active recording UI (voice / idle paths
        # enter straight into recording); the swipe path flips this in on_enter.
        self._apply_recording_visuals()
        Clock.schedule_once(lambda _dt: self._on_root_resize(self._root, self._root.size), 0)

    # --------------------------------------------------------------- helpers
    def _add_label(self, text, box, fs_ratio, color, *, bold=False, halign="center", font_name=_FONT):
        lbl = Label(
            text=text,
            font_name=font_name,
            bold=bold,
            color=color,
            halign=halign,
            valign="middle",
            **kivy_hints(box),
        )
        lbl.bind(size=lbl.setter("text_size"))
        lbl._fs_ratio = fs_ratio  # noqa: SLF001
        self._canvas.add_widget(lbl)
        return lbl

    def _on_root_resize(self, _root, size):
        w, h = scaled_canvas(size[0], size[1])
        self._canvas.size = (w, h)
        for lbl in (
            self.rec_label, self.started_label, self.timer_label,
            self.ready_title, self.ready_subtitle,
        ):
            if lbl is not None:
                lbl.font_size = font_px(lbl._fs_ratio, h)  # noqa: SLF001
        if hasattr(self, "stop_pill"):
            self.stop_pill.font_size = font_px(STOP_FS_RATIO, h)
        if hasattr(self, "start_btn"):
            self.start_btn.font_size = font_px(START_FS_RATIO, h)
        self._sync_rec_dot()

    def _sync_rec_dot(self, *_):
        """Park the status dot just left of the caption's rendered text."""
        lbl = getattr(self, "rec_label", None)
        dot = getattr(self, "status_dot", None)
        if lbl is None or dot is None or not lbl.text or lbl.width <= 0:
            return
        try:
            from kivy.core.text import Label as CoreLabel

            cl = CoreLabel(
                text=lbl.text,
                font_size=lbl.font_size,
                font_name=lbl.font_name,
                bold=lbl.bold,
            )
            cl.refresh()
            text_w = float(cl.texture.size[0]) if cl.texture is not None else lbl.width * 0.5
        except Exception:  # noqa: BLE001
            text_w = lbl.width * 0.5
        cx = lbl.x + lbl.width / 2.0
        cy = lbl.y + lbl.height / 2.0
        gap = dot.width * 0.85
        dot.pos = (cx - text_w / 2.0 - gap - dot.width, cy - dot.height / 2.0)

    # --------------------------------------------------- page translate (swipe)
    def _ensure_page_translate(self):
        """Install a transform-only translate around the whole screen so the
        page can be dragged as one GPU-cheap layer (no per-frame relayout)."""
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

    # --------------------------------------------------- READY / RECORDING state
    _READY_WIDGETS = ("ready_title", "ready_subtitle", "start_btn")
    _REC_WIDGETS = (
        "status_dot", "rec_label", "started_label",
        "timer_label", "pause_btn", "stop_pill",
    )

    def _apply_ready_visuals(self):
        for name in self._REC_WIDGETS:
            w = getattr(self, name, None)
            if w is not None:
                Animation.cancel_all(w, "opacity")
                w.opacity = 0.0
        for name in self._READY_WIDGETS:
            w = getattr(self, name, None)
            if w is not None:
                Animation.cancel_all(w, "opacity")
                w.opacity = 1.0
        self.start_btn.disabled = False
        self.start_btn.press_scale = 1.0
        self.pause_btn.disabled = True
        self.stop_pill.disabled = True
        self.status_dot.stop_blink()

    def _apply_recording_visuals(self):
        for name in self._READY_WIDGETS:
            w = getattr(self, name, None)
            if w is not None:
                Animation.cancel_all(w, "opacity")
                w.opacity = 0.0
        for name in self._REC_WIDGETS:
            w = getattr(self, name, None)
            if w is not None:
                Animation.cancel_all(w, "opacity")
                w.opacity = 1.0
        self.start_btn.disabled = True
        self.pause_btn.disabled = False
        self.stop_pill.disabled = False

    def show_ready_preview(self):
        """Put the screen into a static READY look for the Home swipe preview
        (no timers, no animation) — must match :meth:`_enter_ready` exactly."""
        self._mode = "ready"
        self._recording_active = False
        self._morphing = False
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.set_page_offset(0.0)
        self._apply_ready_visuals()
        self.wavebar.show_static()

    # ------------------------------------------------------------- lifecycle
    def on_enter(self):
        # ``enter_ready_next`` is a latch (not consumed here): on_enter may be
        # dispatched twice — once by the ScreenManager transition and once by
        # the app's manual navigation — so both passes must agree on the mode.
        # It is cleared in on_leave.
        self.set_page_offset(0.0)
        if self.enter_ready_next:
            self._enter_ready()
        else:
            self._enter_recording_direct()

    def _enter_ready(self):
        self._mode = "ready"
        self._recording_active = False
        self._morphing = False
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.status_dot.stop_blink()
        self._apply_ready_visuals()
        self.wavebar.show_static()

    def _enter_recording_direct(self):
        self._mode = "recording"
        self._morphing = False
        self._apply_recording_visuals()
        self._start_timer_now()
        self.wavebar.start()
        self.wavebar.start_voice()
        self._recording_active = True

    def _start_timer_now(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self._is_paused = False
        self.elapsed_seconds = 0
        self._rec_base_elapsed = 0.0
        self._rec_active_start = time.monotonic()
        self.timer_label.set_text("00:00:00")
        self.rec_label.text = self._recording_label_text()
        self.status_dot.set_recording(True)
        self.pause_btn.set_paused(False)
        now = display_now()
        # Figma shows "Started at 11:01AM" (no space before AM/PM).
        self.started_label.text = f"Started at {now.strftime('%I:%M%p').lstrip('0')}"
        self.timer_event = Clock.schedule_interval(self._tick_timer, 0.5)

    # ----------------------------------------------- START tap → in-place morph
    def _on_start_pressed(self, _btn):
        if self._mode != "ready" or self._morphing:
            return
        self._morphing = True
        self.start_btn.disabled = True

        # Kick the backend optimistically so capture spins up in parallel; the
        # UI evolves immediately for a native, lag-free feel.
        try:
            self.app.start_recording()
        except Exception:
            logger.exception("recording: optimistic start_recording failed")

        # Timer begins counting right away (it fades in gently below).
        self._start_timer_now()
        self._recording_active = True

        # Phase 2 — dissolve the READY content (opacity only, no movement).
        for name in self._READY_WIDGETS:
            w = getattr(self, name, None)
            if w is not None:
                Animation.cancel_all(w, "opacity")
                Animation(opacity=0.0, duration=0.20, t="out_quad").start(w)

        # Phase 3 — recording controls fade in with a gentle overlap so the
        # interface never reads as empty.
        def _phase3(_dt):
            for name in ("status_dot", "rec_label", "started_label", "timer_label"):
                w = getattr(self, name, None)
                if w is not None:
                    Animation(opacity=1.0, duration=0.26, t="out_cubic").start(w)
            for name in ("pause_btn", "stop_pill"):
                w = getattr(self, name, None)
                if w is not None:
                    Animation(opacity=1.0, duration=0.30, t="out_cubic").start(w)
        Clock.schedule_once(_phase3, 0.10)

        # Phase 4 — once controls are present, bring the waveform alive.
        def _phase4(_dt):
            self.wavebar.start()
            self.wavebar.start_voice()
        Clock.schedule_once(_phase4, 0.34)

        # Controls become interactive only after the transition settles.
        def _done(_dt):
            self._morphing = False
            self._mode = "recording"
            self.pause_btn.disabled = False
            self.stop_pill.disabled = False
        Clock.schedule_once(_done, 0.46)

    def on_leave(self):
        self.enter_ready_next = False
        if self._back_pager is not None:
            self._back_pager.cancel()
        self.set_page_offset(0.0)
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.wavebar.stop()
        self.status_dot.stop_blink()

    # ------------------------------------------------------------- back swipe
    def on_touch_down(self, touch):
        if self._back_pager is not None and self._back_pager.on_touch_down(touch):
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._back_pager is not None and self._back_pager.on_touch_move(touch):
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._back_pager is not None and self._back_pager.on_touch_up(touch):
            return True
        return super().on_touch_up(touch)

    # ---------------------------------------------------------------- timer
    def _elapsed_from_monotonic(self) -> int:
        if self._is_paused or self._rec_active_start is None:
            return int(self._rec_base_elapsed)
        return int(self._rec_base_elapsed + (time.monotonic() - self._rec_active_start))

    def _tick_timer(self, _dt):
        self.elapsed_seconds = self._elapsed_from_monotonic()
        self.timer_label.set_text(self._fmt_time(self.elapsed_seconds))

    @staticmethod
    def _fmt_time(secs: int) -> str:
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    # ------------------------------------------------------------ pause/stop
    def _on_toggle_pause(self, _inst):
        if self._is_paused:
            self.app.resume_recording()
        else:
            self.app.pause_recording()

    def _on_stop(self, _inst):
        logger.info("Stop recording pressed (duration: %s)", self._fmt_time(self.elapsed_seconds))
        self.app.stop_recording()

    def on_paused(self):
        if self._is_paused:
            return
        self._is_paused = True
        if self._rec_active_start is not None:
            self._rec_base_elapsed += time.monotonic() - self._rec_active_start
            self._rec_active_start = None
        self.rec_label.text = "Recording Paused"
        self.status_dot.set_recording(False)
        self.pause_btn.set_paused(True)
        self.wavebar.stop_voice()

    def on_resumed(self):
        if not self._is_paused:
            return
        self._is_paused = False
        self._rec_active_start = time.monotonic()
        self.rec_label.text = self._recording_label_text()
        self.status_dot.set_recording(True)
        self.pause_btn.set_paused(False)
        self.wavebar.start_voice()

    def on_audio_level(self, level: float):
        self.wavebar.feed_level(level)

    def on_audio_segment(self, segment_num: int):
        del segment_num
