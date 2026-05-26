"""Recording screen — Figma `863:626` (VelsLhL4YHeVRZSCEmCrGw).

Composes the screen from PNG assets exported from Figma + Kivy Labels for the
dynamic text (timer, start time, meeting title).  Layout lives in
``frame19_layout.py`` and matches the Figma absolute coordinates 1:1.

Lifecycle hooks (called from main.py): on_enter / on_leave, on_paused /
on_resumed, on_audio_level, on_audio_segment.
"""

from __future__ import annotations

import logging
import math
import random
import time

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import ASSETS_DIR, display_now
from frame19_layout import (
    BACK_BTN,
    BG_RGB,
    BTN_PAUSE,
    BTN_SETTINGS,
    COL_GLOW_BLUE,
    COL_MUTED,
    COL_REC_DOT_GREY,
    COL_REC_DOT_RED,
    COL_WHITE,
    LEFT_VEC,
    REC_DOT,
    REC_LABEL,
    REC_LABEL_FS_RATIO,
    RIGHT_VEC,
    RING_DARK,
    RING_GLOW,
    RING_GRADIENT,
    STARTED_FS_RATIO,
    STARTED_LABEL,
    STATUS,
    STATUS_FS_RATIO,
    STOP_PILL,
    TIMER,
    TIMER_FS_RATIO,
    WAVEBAR,
    font_px,
    kivy_hints,
    scaled_canvas,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_FIGMA = ASSETS_DIR / "recording" / "figma"
_BG = (BG_RGB[0] / 255, BG_RGB[1] / 255, BG_RGB[2] / 255, 1.0)
_FONT_BOLD = "42dot-Sans"

# Centre Frame 19 image layers (back → front).
# `frame19_ring_glow.png` is handled separately in ``_build_ui`` so its
# greyscale halo can be tinted blue at runtime via ``Image.color``.
_FRAME19_IMAGES: tuple[tuple[str, dict], ...] = (
    ("frame19_ring_dark.png", RING_DARK),
    ("frame19_ring_gradient.png", RING_GRADIENT),
    ("frame19_vector_left.png", LEFT_VEC),
    ("frame19_vector_right.png", RIGHT_VEC),
)


def _png(name: str) -> str:
    p = _FIGMA / name
    return str(p) if p.is_file() else ""


class _ImgBtn(ButtonBehavior, Image):
    """Tappable PNG button."""


class _Wavebar(Widget):
    """Voice waveform indicator — Figma node ``863:561`` (Group 46).

    Draws ``n_bars`` vertical rounded bars that react to live microphone
    levels fed in via :meth:`feed_level`. A small idle ripple keeps it
    "alive" even when the user is silent so they can see the screen is
    actively listening; speech amplifies the centre bars more than the
    edges to mimic the bell-shaped voice envelope in the Figma reference.

    Animation runs on Kivy's main thread via ``Clock.schedule_interval``.
    Call :meth:`start` / :meth:`stop` from on_enter / on_leave (and on
    pause / resume) so we never burn CPU when the screen is hidden.
    """

    def __init__(
        self,
        *,
        n_bars: int = 21,
        color: tuple = (0.0, 107 / 255, 249 / 255, 1.0),  # #006BF9
        idle_color: tuple = (0.0, 107 / 255, 249 / 255, 0.85),
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.n_bars = n_bars
        self._color_active = color
        self._color_idle = idle_color
        self._bar_min_ratio = 0.10  # height when totally silent
        self._bar_max_ratio = 0.96  # height at peak voice
        self._levels = [0.0] * n_bars
        self._latest_audio = 0.0
        self._phase = 0.0
        self._anim_event: object | None = None
        self._is_active = False  # set by start_voice / stop_voice
        self._jitter = [random.uniform(0.65, 1.0) for _ in range(n_bars)]

        with self.canvas:
            self._color_inst = Color(*self._color_idle)
            self._bars = [
                RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[1])
                for _ in range(n_bars)
            ]

        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())

    # ----- public API --------------------------------------------------
    def feed_level(self, level: float) -> None:
        """Push the latest mic amplitude (expected range ~0-1)."""
        try:
            v = float(level)
        except (TypeError, ValueError):
            return
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        # Light attack so a single loud sample doesn't spike for one frame.
        self._latest_audio = max(self._latest_audio * 0.55, v)

    def start(self) -> None:
        if self._anim_event is None:
            self._anim_event = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self) -> None:
        if self._anim_event is not None:
            self._anim_event.cancel()
            self._anim_event = None

    def start_voice(self) -> None:
        """Indicate that recording is live (full-amplitude colour)."""
        self._is_active = True
        self._color_inst.rgba = self._color_active

    def stop_voice(self) -> None:
        """Indicate that recording is paused (dimmed colour, no audio decay)."""
        self._is_active = False
        self._latest_audio = 0.0
        self._color_inst.rgba = self._color_idle

    # ----- tick / draw -------------------------------------------------
    def _tick(self, dt: float) -> None:
        self._phase += dt * 4.5
        n = self.n_bars
        if n <= 1:
            return
        centre = (n - 1) / 2.0
        amp = self._latest_audio if self._is_active else 0.0
        for i in range(n):
            d = (i - centre) / centre  # -1..1
            # Bell-shaped envelope — centre bars are loudest.
            bell = max(0.0, math.cos(d * math.pi / 2.0))
            # Gentle idle ripple so the screen never looks dead.
            idle = 0.04 + 0.06 * math.sin(self._phase + i * 0.55)
            voice = amp * (0.35 + 0.65 * bell) * self._jitter[i]
            target = max(idle if self._is_active else 0.02, voice)
            self._levels[i] += (target - self._levels[i]) * 0.4
        # Audio decays if no fresh level arrives so the bars settle.
        self._latest_audio *= 0.93
        # Reshuffle a couple of jitter weights each frame for organic look.
        if random.random() < 0.18:
            idx = random.randrange(n)
            self._jitter[idx] = random.uniform(0.55, 1.0)
        self._redraw()

    def _redraw(self) -> None:
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        n = self.n_bars
        # Allocate 45% of the total width to bars, 55% to gaps — matches
        # the airy look in the Figma reference.
        bar_w = max(1.0, (w * 0.45) / n)
        total_bars = bar_w * n
        gap = (w - total_bars) / max(1, n - 1)
        max_h = h * self._bar_max_ratio
        min_h = max(2.0, h * self._bar_min_ratio)
        cy = self.y + h / 2.0
        radius = bar_w / 2.0
        for i, rect in enumerate(self._bars):
            bar_h = min_h + (max_h - min_h) * self._levels[i]
            x = self.x + i * (bar_w + gap)
            rect.pos = (x, cy - bar_h / 2.0)
            rect.size = (bar_w, bar_h)
            rect.radius = [radius]


class _StatusDot(Widget):
    """Solid recording-status dot drawn with Kivy primitives.

    The Figma export `icon_rec_dot_red.png` is a solid-black bitmap (the
    red colour was lost in the export pipeline), so the dot is drawn here
    with `Color` + `Ellipse` instead. Two colours: red while recording,
    grey while paused — toggled via :meth:`set_recording`.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._active_color = COL_REC_DOT_RED
        self._idle_color = COL_REC_DOT_GREY
        with self.canvas:
            self._color_inst = Color(*self._active_color)
            self._ellipse = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args):
        self._ellipse.pos = self.pos
        self._ellipse.size = self.size

    def set_recording(self, active: bool) -> None:
        self._color_inst.rgba = self._active_color if active else self._idle_color


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
        with self._root.canvas.before:
            Color(*_BG)
            self._bg = Rectangle(pos=self._root.pos, size=self._root.size)
        self._root.bind(
            pos=lambda w, _v: setattr(self._bg, "pos", w.pos),
            size=self._on_root_resize,
        )

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        # Centre — Frame 19 graphic (back → front).
        # The glow halo PNG is greyscale; multiplying its texture by the
        # blue tint produces the visible blue halo that the Figma design
        # calls for, without needing a re-export.
        glow_img = self._add_image("frame19_ring_glow.png", RING_GLOW)
        if glow_img is not None:
            glow_img.color = COL_GLOW_BLUE
        for filename, box in _FRAME19_IMAGES:
            self._add_image(filename, box)

        # Voice wavebar (Group 46) — sits inside the orb and animates with
        # mic input fed via on_audio_level().
        self.wavebar = _Wavebar(**kivy_hints(WAVEBAR))
        self._canvas.add_widget(self.wavebar)

        # Timer + status caption (centred in Frame 19)
        self.timer_label = self._add_label(
            "00 : 00 : 00", TIMER, TIMER_FS_RATIO, COL_WHITE, bold=True,
        )
        self.status_label = self._add_label(
            "Recording in progress", STATUS, STATUS_FS_RATIO, COL_MUTED, bold=True,
        )

        # Top-left — back button (composite PNG)
        self._add_img_btn("btn_back.png", BACK_BTN, on_release=lambda *_: self.go_back())

        # Top-left — recording status group (status dot + Recording... + Started at ...)
        self.status_dot = _StatusDot(**kivy_hints(REC_DOT))
        self._canvas.add_widget(self.status_dot)
        self.rec_label = self._add_label(
            "Recording...", REC_LABEL, REC_LABEL_FS_RATIO, COL_WHITE, bold=True, halign="left",
        )
        self.started_label = self._add_label(
            "Started at --:-- --", STARTED_LABEL, STARTED_FS_RATIO, COL_MUTED, halign="left",
        )

        # Bottom row — pause | stop recording pill | settings
        self._add_img_btn("btn_pause.png", BTN_PAUSE, on_release=self._on_pause)
        self._add_img_btn("stop_recording_pill.png", STOP_PILL, on_release=self._on_stop)
        self._add_img_btn(
            "btn_settings.png",
            BTN_SETTINGS,
            on_release=lambda *_: self.goto("settings", transition="slide_left"),
        )

        self.add_widget(self._root)
        Clock.schedule_once(lambda _dt: self._on_root_resize(self._root, self._root.size), 0)

    # --------------------------------------------------------------- helpers
    def _add_image(self, filename: str, box: dict) -> Image | None:
        src = _png(filename)
        if not src:
            return None
        img = Image(
            source=src,
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
            **kivy_hints(box),
        )
        self._canvas.add_widget(img)
        return img

    def _add_img_btn(self, filename: str, box: dict, *, on_release) -> _ImgBtn | None:
        src = _png(filename)
        if not src:
            return None
        btn = _ImgBtn(
            source=src,
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
            **kivy_hints(box),
        )
        btn.bind(on_release=on_release)
        self._canvas.add_widget(btn)
        return btn

    def _add_label(
        self,
        text: str,
        box: dict,
        fs_ratio: float,
        color: tuple,
        *,
        bold: bool = False,
        halign: str = "center",
    ) -> Label:
        lbl = Label(
            text=text,
            font_name=_FONT_BOLD,
            bold=bold,
            color=color,
            halign=halign,
            valign="middle",
            **kivy_hints(box),
        )
        lbl.bind(size=lbl.setter("text_size"))
        lbl._fs_ratio = fs_ratio  # noqa: SLF001 — resize hook
        self._canvas.add_widget(lbl)
        return lbl

    def _on_root_resize(self, _root, size):
        self._bg.size = size
        w, h = scaled_canvas(size[0], size[1])
        self._canvas.size = (w, h)
        for lbl in (
            self.timer_label,
            self.status_label,
            self.rec_label,
            self.started_label,
        ):
            if lbl is not None:
                lbl.font_size = font_px(lbl._fs_ratio, h)  # noqa: SLF001

    # ------------------------------------------------------------- lifecycle
    def on_enter(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None

        self._is_paused = False
        self.elapsed_seconds = 0
        self._rec_base_elapsed = 0.0
        self._rec_active_start = time.monotonic()
        self.timer_label.text = "00 : 00 : 00"
        self.status_label.text = "Recording in progress"
        self.rec_label.text = "Recording..."
        self.status_dot.set_recording(True)

        now = display_now()
        self.started_label.text = f"Started at {now.strftime('%I:%M %p').lstrip('0')}"

        self.timer_event = Clock.schedule_interval(self._tick_timer, 0.5)

        # Voice wavebar — start animating and mark it as live so it reacts
        # at full amplitude to incoming audio_level events from Redis.
        self.wavebar.start()
        self.wavebar.start_voice()

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        self.wavebar.stop()

    # ---------------------------------------------------------------- timer
    def _elapsed_from_monotonic(self) -> int:
        if self._is_paused or self._rec_active_start is None:
            return int(self._rec_base_elapsed)
        return int(self._rec_base_elapsed + (time.monotonic() - self._rec_active_start))

    def _tick_timer(self, _dt):
        self.elapsed_seconds = self._elapsed_from_monotonic()
        self.timer_label.text = self._fmt_time(self.elapsed_seconds)

    @staticmethod
    def _fmt_time(secs: int) -> str:
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d} : {m:02d} : {s:02d}"

    # ------------------------------------------------------------ pause/stop
    def _on_pause(self, _inst):
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
        self.rec_label.text = "Paused"
        self.status_label.text = "Recording paused"
        self.status_dot.set_recording(False)
        # Freeze the wavebar at idle so the user can see we stopped reading
        # the mic while paused.
        self.wavebar.stop_voice()

    def on_resumed(self):
        if not self._is_paused:
            return
        self._is_paused = False
        self._rec_active_start = time.monotonic()
        self.rec_label.text = "Recording..."
        self.status_label.text = "Recording in progress"
        self.status_dot.set_recording(True)
        self.wavebar.start_voice()

    def on_audio_level(self, level: float):
        """Feed live mic amplitude (0-1) into the wavebar visualiser.

        Called by ``main.py.on_audio_level`` once Redis delivers an
        ``audio_level`` event from the audio-capture process. Wiring this
        up is what makes the user *see* their voice — the audio pipeline
        itself was already running but the recording UI was discarding the
        level.
        """
        self.wavebar.feed_level(level)

    def on_audio_segment(self, segment_num: int):
        del segment_num
