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

from kivy.clock import Clock
from kivy.graphics import (
    Color,
    Ellipse,
    Line,
    RoundedRectangle,
    Triangle,
)
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import display_now
from frame19_layout import (
    BG_BOT,
    BG_TOP,
    BTN_PAUSE,
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

    Reacts to live mic levels fed via :meth:`feed_level`. A small idle ripple
    keeps it alive while silent so the user can see the mic is listening;
    speech amplifies the centre bars more than the edges (bell envelope).
    """

    _SILENCE_THRESHOLD = 0.04
    _FLAT_RATIO = 0.10

    def __init__(self, *, n_bars: int = 7, **kwargs):
        super().__init__(**kwargs)
        self.n_bars = n_bars
        self._bar_max_ratio = 1.0
        # A fixed bell-ish envelope so the row reads as a calm voice waveform
        # even at rest (matches the Figma static — 7 bars, tall centre,
        # short edges), amplified by live audio.
        centre = (n_bars - 1) / 2.0
        self._envelope = [
            0.24 + 0.76 * max(0.0, math.cos(((i - centre) / centre) * math.pi / 2.0))
            for i in range(n_bars)
        ]
        self._levels = [self._FLAT_RATIO] * n_bars
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

    # -- tick / draw ---------------------------------------------------
    def _tick(self, dt: float) -> None:
        n = self.n_bars
        if n <= 1:
            return
        centre = (n - 1) / 2.0
        voice = self._is_active and self._latest_audio > self._SILENCE_THRESHOLD
        for i in range(n):
            env = self._envelope[i]
            self._idle_phase[i] = (self._idle_phase[i] + dt * 1.8) % math.tau
            breathe = 0.5 + 0.5 * math.sin(self._idle_phase[i])
            if voice:
                target = max(
                    self._FLAT_RATIO,
                    env * (0.50 + 0.50 * self._latest_audio) * self._jitter[i],
                )
            else:
                # Calm idle: keep the bell shape, breathing softly so it reads
                # as a live waveform without any audio. Amplitudes sized so the
                # idle state matches the Figma static (centre bar near the top
                # of the wave box).
                target = env * (0.68 + 0.14 * breathe)
            self._levels[i] += (target - self._levels[i]) * 0.35
        self._latest_audio *= 0.93
        if voice and random.random() < 0.18:
            self._jitter[random.randrange(n)] = random.uniform(0.55, 1.0)
        self._redraw()

    def _redraw(self) -> None:
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        n = self.n_bars
        # Figma: bars are slightly wider than the gaps between them.
        bar_w = max(1.0, (w * 0.58) / n)
        gap = (w - bar_w * n) / max(1, n - 1)
        max_h = h * self._bar_max_ratio
        cy = self.y + h / 2.0
        radius = bar_w / 2.0
        for i, rect in enumerate(self._bars):
            bar_h = max(bar_w, max_h * self._levels[i])
            x = self.x + i * (bar_w + gap)
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
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self._root = FloatLayout(size_hint=(1, 1))
        attach_swirl_bg(self._root, BG_TOP, BG_BOT)
        self._root.bind(size=self._on_root_resize)

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        # Top-right status bar.
        self._canvas.add_widget(_StatusBar(**kivy_hints(STATUS_BAR)))

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

        self.add_widget(self._root)
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
        for lbl in (self.rec_label, self.started_label, self.timer_label):
            if lbl is not None:
                lbl.font_size = font_px(lbl._fs_ratio, h)  # noqa: SLF001
        if hasattr(self, "stop_pill"):
            self.stop_pill.font_size = font_px(STOP_FS_RATIO, h)
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

    # ------------------------------------------------------------- lifecycle
    def on_enter(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None

        self._is_paused = False
        self.elapsed_seconds = 0
        self._rec_base_elapsed = 0.0
        self._rec_active_start = time.monotonic()
        self.timer_label.set_text("00:00:00")
        self.rec_label.text = "Recording...."
        self.status_dot.set_recording(True)
        self.pause_btn.set_paused(False)

        now = display_now()
        # Figma shows "Started at 11:01AM" (no space before AM/PM).
        self.started_label.text = f"Started at {now.strftime('%I:%M%p').lstrip('0')}"

        self.timer_event = Clock.schedule_interval(self._tick_timer, 0.5)
        self.wavebar.start()
        self.wavebar.start_voice()

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.wavebar.stop()
        self.status_dot.stop_blink()

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
        self.rec_label.text = "Recording...."
        self.status_dot.set_recording(True)
        self.pause_btn.set_paused(False)
        self.wavebar.start_voice()

    def on_audio_level(self, level: float):
        self.wavebar.feed_level(level)

    def on_audio_segment(self, segment_num: int):
        del segment_num
