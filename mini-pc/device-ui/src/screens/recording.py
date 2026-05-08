"""Recording screen — Figma `408:657` (yJqcY4KovVjJ11vjysW533).

Layout:
- Header row: back button (round) | recording status + meeting info | Listening pill
- Center: circular wave-ring background with vertical waveform bars in the middle
- Below center: timer (HH:MM:SS) + "Recording in progress" caption
- Bottom row: pause button (round) | "Stop recording" pill | settings gear (round)

The screen preserves the underlying recording state machine (timer, audio
level handling, pause/resume/stop wiring) — only the visuals were rebuilt
to match the Figma design.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from config import (
    ASSETS_DIR,
    COLORS,
    FONT_SIZES,
    SPACING,
    display_now,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_REC_ASSETS = ASSETS_DIR / "recording"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rw_suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _rw_suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


def _rw_suf(fs):
    v = other_screen_vertical_scale()
    return max(6, int(round(float(fs) * v)))


def _rec_png(name: str) -> str:
    p = _REC_ASSETS / name
    return str(p) if p.is_file() else ""


_BG_NAVY = (0.004, 0.031, 0.102, 1)        # #01081a
_BORDER = (0.247, 0.259, 0.325, 1)          # #3F4253
_TEXT_MUTED = (0.714, 0.729, 0.949, 1)      # #B6BAF2
_BLUE = (0.000, 0.420, 0.976, 1)            # #006bf9
_RED = (0.96, 0.27, 0.30, 1)


class _ImageButton(ButtonBehavior, Image):
    """Image that also fires on_press / on_release like a Button."""


class _CircleButton(ButtonBehavior, FloatLayout):
    """Round bordered surface used for the back / pause / settings buttons.

    Replicates the Figma circle (#020c26 fill, 0.8px #3F4253 border, ~80px
    radius). The icon is added by the caller via ``add_widget``.
    """

    def __init__(self, fill=(0.008, 0.043, 0.149, 1), **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*fill)
            self._bg = Ellipse(pos=self.pos, size=self.size)
            Color(*_BORDER)
            self._stroke = Line(circle=(self.center_x, self.center_y, max(self.width, self.height) / 2), width=1.0)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._stroke.circle = (
            self.center_x,
            self.center_y,
            max(self.width, self.height) / 2,
        )


class _StopRecordingPill(ButtonBehavior, FloatLayout):
    """Big "Stop recording" pill in the center of the bottom controls.

    Implements the Figma gradient pill (#02123c → #000a26) with a square
    blue stop-icon and the bold white text. The square + label are added
    in __init__ so callers don't have to compose them manually.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            # Vertical Kivy gradient is non-trivial (no built-in); two flat
            # rectangles read close enough at thumb size.
            Color(0.008, 0.071, 0.235, 1.0)  # ~#02123c (top)
            self._bg_top = RoundedRectangle(pos=self.pos, size=self.size, radius=[_rw_suv(116)])
            Color(0.000, 0.039, 0.149, 1.0)  # ~#000a26 (bottom)
            self._bg_bottom = RoundedRectangle(
                pos=self.pos, size=(self.width, self.height * 0.6), radius=[0, 0, _rw_suv(116), _rw_suv(116)]
            )
            Color(*_BORDER)
            self._stroke = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, _rw_suv(116)), width=1.0)
        self.bind(pos=self._sync, size=self._sync)

        # Stop square (blue)
        self._stop_square = Widget(
            size_hint=(None, None),
            size=(_rw_suv(27), _rw_suv(27)),
            pos_hint={"center_y": 0.5, "x": 0.18},
        )
        with self._stop_square.canvas:
            Color(*_BLUE)
            self._stop_rect = RoundedRectangle(
                pos=self._stop_square.pos, size=self._stop_square.size, radius=[_rw_suv(4)]
            )
        self._stop_square.bind(
            pos=lambda w, _v: setattr(self._stop_rect, "pos", w.pos),
            size=lambda w, _v: setattr(self._stop_rect, "size", w.size),
        )
        self.add_widget(self._stop_square)

        # Label
        self._label = Label(
            text="Stop recording",
            font_size=_rw_suf(28),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(None, None),
            size=(_rw_suh(280), _rw_suv(40)),
            pos_hint={"center_y": 0.5, "x": 0.30},
        )
        self._label.bind(size=self._label.setter("text_size"))
        self.add_widget(self._label)

    def _sync(self, *_args):
        radius = _rw_suv(116)
        self._bg_top.pos = self.pos
        self._bg_top.size = self.size
        self._bg_top.radius = [radius]
        self._bg_bottom.pos = self.pos
        self._bg_bottom.size = (self.width, self.height * 0.6)
        self._bg_bottom.radius = [0, 0, radius, radius]
        self._stroke.rounded_rectangle = (self.x, self.y, self.width, self.height, radius)


class _ListeningPill(FloatLayout):
    """Top-right "Listening" pill used in the header.

    Static at this layer — the inner state (active/idle) is driven by
    voice_assistant in main.py. The pill's purpose here is purely
    visual feedback: "the device is currently listening for wake word".
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0.000, 0.060, 0.200, 1.0)  # ~#000f33
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[_rw_suv(28)])
            Color(0.129, 0.157, 0.294, 1.0)  # ~#21284b border
            self._stroke = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, _rw_suv(28)),
                width=1.0,
            )
        self.bind(pos=self._sync, size=self._sync)

        # Blue dot
        dot_path = _rec_png("icon_listening_dot.png")
        if dot_path:
            self.add_widget(Image(
                source=dot_path,
                size_hint=(None, None),
                size=(_rw_suv(14), _rw_suv(14)),
                pos_hint={"center_y": 0.5, "x": 0.10},
                fit_mode="contain",
                allow_stretch=True,
            ))
        # Label
        lbl = Label(
            text="Listening",
            font_size=_rw_suf(20),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(None, None),
            size=(_rw_suh(110), _rw_suv(28)),
            pos_hint={"center_y": 0.5, "x": 0.22},
        )
        lbl.bind(size=lbl.setter("text_size"))
        self.add_widget(lbl)
        # Soundwave glyph
        sw_path = _rec_png("icon_soundwave.png")
        if sw_path:
            self.add_widget(Image(
                source=sw_path,
                size_hint=(None, None),
                size=(_rw_suv(28), _rw_suv(28)),
                pos_hint={"center_y": 0.5, "right": 0.94},
                fit_mode="contain",
                allow_stretch=True,
                color=_BLUE,
            ))

    def _sync(self, *_args):
        radius = _rw_suv(28)
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [radius]
        self._stroke.rounded_rectangle = (self.x, self.y, self.width, self.height, radius)


# ---------------------------------------------------------------------------
# Waveform — vertical bars (kept similar to old impl, just slightly tuned)
# ---------------------------------------------------------------------------

class _Waveform(Widget):
    NUM_BARS = 28

    def __init__(self, **kwargs):
        self.BAR_WIDTH = _rw_suh(4)
        self.BAR_SPACING = _rw_suh(4)
        self.MAX_H = _rw_suv(80)
        bar_extent_w = (
            self.NUM_BARS * self.BAR_WIDTH
            + (self.NUM_BARS - 1) * self.BAR_SPACING
        )
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (bar_extent_w, self.MAX_H * 2))
        super().__init__(**kwargs)
        self._levels = [2] * self.NUM_BARS
        self._active = False
        self.bind(pos=self._draw, size=self._draw)

    def set_active(self, active: bool):
        self._active = active

    def set_levels(self, levels: list):
        self._levels = levels
        self._draw()

    def _draw(self, *_args):
        self.canvas.clear()
        extent_w = (
            self.NUM_BARS * self.BAR_WIDTH
            + (self.NUM_BARS - 1) * self.BAR_SPACING
        )
        start_x = self.x + (self.width - extent_w) / 2
        mid_y = self.center_y

        with self.canvas:
            for i, h in enumerate(self._levels):
                half = max(1, h / 2)
                Color(0.30, 0.56, 0.98, 1)
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, mid_y - half),
                    size=(self.BAR_WIDTH, half * 2),
                    radius=[max(1, _rw_suv(2))],
                )


# ---------------------------------------------------------------------------
# Recording Screen
# ---------------------------------------------------------------------------

class RecordingScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.elapsed_seconds = 0
        self.timer_event = None
        self.waveform_event = None
        self._is_paused = False
        self._level_history = deque([0.0] * _Waveform.NUM_BARS, maxlen=_Waveform.NUM_BARS)
        self._last_audio_level_ts = 0.0
        self._rec_base_elapsed = 0.0
        self._rec_active_start = None
        self._meeting_title = "Recording"
        self._participant_count = 0
        self._meeting_provider = ""
        self._started_at_str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    # BUILD
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.root_layout = FloatLayout()
        with self.root_layout.canvas.before:
            Color(*_BG_NAVY)
            self._bg = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
        self.root_layout.bind(
            pos=lambda w, _v: setattr(self._bg, "pos", w.pos),
            size=lambda w, _v: setattr(self._bg, "size", w.size),
        )

        col = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[
                _rw_suh(SPACING["screen_padding"]),
                _rw_suv(SPACING["screen_padding"]),
                _rw_suh(SPACING["screen_padding"]),
                _rw_suv(SPACING["screen_padding"]),
            ],
            spacing=_rw_suv(8),
        )

        # ---- Header ----
        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=_rw_suv(64),
            spacing=_rw_suh(12),
        )

        # Back button (round)
        back_size = _rw_suv(54)
        self.back_btn = _CircleButton(size=(back_size, back_size))
        back_arrow_path = _rec_png("icon_back_arrow.png")
        if back_arrow_path:
            arrow = Image(
                source=back_arrow_path,
                size_hint=(None, None),
                size=(_rw_suv(28), _rw_suv(28)),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
                fit_mode="contain",
                allow_stretch=True,
            )
            self.back_btn.add_widget(arrow)
        else:
            self.back_btn.add_widget(Label(
                text="<",
                font_size=_rw_suf(24),
                bold=True,
                color=COLORS["white"],
                pos_hint={"center_x": 0.5, "center_y": 0.5},
            ))
        self.back_btn.bind(on_release=lambda *_: self.go_back())
        header.add_widget(self.back_btn)

        # Recording status (left of center)
        rec_status_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, 1),
            width=_rw_suh(190),
            spacing=2,
            padding=[_rw_suh(8), 0, 0, 0],
        )
        rec_top_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=_rw_suv(26), spacing=_rw_suh(6))
        red_dot = _rec_png("icon_recording_dot.png")
        if red_dot:
            rec_top_row.add_widget(Image(
                source=red_dot,
                size_hint=(None, 1),
                width=_rw_suv(14),
                fit_mode="contain",
                allow_stretch=True,
            ))
        else:
            rec_top_row.add_widget(Label(
                text="●",
                font_size=_rw_suf(16),
                color=_RED,
                size_hint=(None, 1),
                width=_rw_suv(14),
            ))
        self.rec_state_label = Label(
            text="Recording...",
            font_size=_rw_suf(20),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        )
        self.rec_state_label.bind(size=self.rec_state_label.setter("text_size"))
        rec_top_row.add_widget(self.rec_state_label)
        rec_status_col.add_widget(rec_top_row)
        self.started_at_label = Label(
            text="Started at --:-- --",
            font_size=_rw_suf(14),
            color=_TEXT_MUTED,
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=_rw_suv(18),
        )
        self.started_at_label.bind(size=self.started_at_label.setter("text_size"))
        rec_status_col.add_widget(self.started_at_label)
        header.add_widget(rec_status_col)

        # Center: meeting info (title + participants/provider)
        meet_col = BoxLayout(orientation="vertical", size_hint=(1, 1), spacing=2)
        meet_top = BoxLayout(orientation="horizontal", size_hint=(1, None), height=_rw_suv(28), spacing=_rw_suh(8), padding=[0, _rw_suv(2), 0, 0])
        meet_top.add_widget(Widget())  # left flex spacer to center the row
        people = _rec_png("icon_people.png")
        if people:
            meet_top.add_widget(Image(
                source=people,
                size_hint=(None, 1),
                width=_rw_suv(28),
                fit_mode="contain",
                allow_stretch=True,
            ))
        self.meeting_title_label = Label(
            text="Recording",
            font_size=_rw_suf(20),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            shorten=True,
        )
        self.meeting_title_label.bind(
            size=self.meeting_title_label.setter("text_size"),
            texture_size=lambda inst, _v: setattr(inst, "width", min(inst.texture_size[0] + _rw_suh(8), _rw_suh(360))),
        )
        meet_top.add_widget(self.meeting_title_label)
        meet_top.add_widget(Widget())
        meet_col.add_widget(meet_top)

        meet_sub = BoxLayout(orientation="horizontal", size_hint=(1, None), height=_rw_suv(20), spacing=_rw_suh(14))
        meet_sub.add_widget(Widget())
        self.participants_label = Label(
            text="",
            font_size=_rw_suf(14),
            bold=True,
            color=_BLUE,
            halign="center",
            valign="middle",
            size_hint=(None, 1),
            width=_rw_suh(120),
        )
        self.participants_label.bind(size=self.participants_label.setter("text_size"))
        meet_sub.add_widget(self.participants_label)
        provider_row = BoxLayout(orientation="horizontal", size_hint=(None, 1), width=_rw_suh(180), spacing=_rw_suh(6))
        video = _rec_png("icon_video.png")
        if video:
            provider_row.add_widget(Image(
                source=video,
                size_hint=(None, 1),
                width=_rw_suv(18),
                fit_mode="contain",
                allow_stretch=True,
            ))
        self.provider_label = Label(
            text="",
            font_size=_rw_suf(14),
            color=_TEXT_MUTED,
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        )
        self.provider_label.bind(size=self.provider_label.setter("text_size"))
        provider_row.add_widget(self.provider_label)
        meet_sub.add_widget(provider_row)
        meet_sub.add_widget(Widget())
        meet_col.add_widget(meet_sub)
        meet_col.add_widget(Widget())
        header.add_widget(meet_col)

        # Right: Listening pill
        self.listening_pill = _ListeningPill(size=(_rw_suh(214), _rw_suv(54)))
        listen_anchor = AnchorLayout(size_hint=(None, 1), width=_rw_suh(220), anchor_x="right", anchor_y="center")
        listen_anchor.add_widget(self.listening_pill)
        header.add_widget(listen_anchor)
        col.add_widget(header)

        # ---- Center waveform inside circular bg ----
        center_anchor = AnchorLayout(size_hint=(1, 1), anchor_x="center", anchor_y="center")
        center_stack = FloatLayout(size_hint=(None, None), size=(_rw_suh(420), _rw_suv(280)))
        wave_bg_path = _rec_png("wave_circle_bg.png")
        if wave_bg_path:
            center_stack.add_widget(Image(
                source=wave_bg_path,
                size_hint=(None, None),
                size=(_rw_suh(420), _rw_suv(260)),
                pos_hint={"center_x": 0.5, "center_y": 0.55},
                fit_mode="contain",
                allow_stretch=True,
            ))
        self.waveform = _Waveform(pos_hint={"center_x": 0.5, "center_y": 0.55})
        center_stack.add_widget(self.waveform)

        # Timer + caption sit just below the wave bowl.
        self.timer_label = Label(
            text="00 : 00 : 00",
            font_size=_rw_suf(36),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(None, None),
            size=(_rw_suh(360), _rw_suv(46)),
            pos_hint={"center_x": 0.5, "y": 0.06},
        )
        self.timer_label.bind(size=self.timer_label.setter("text_size"))
        center_stack.add_widget(self.timer_label)

        self.elapsed_sub = Label(
            text="Recording in progress",
            font_size=_rw_suf(18),
            color=_TEXT_MUTED,
            halign="center",
            valign="middle",
            size_hint=(None, None),
            size=(_rw_suh(360), _rw_suv(26)),
            pos_hint={"center_x": 0.5, "y": -0.02},
        )
        self.elapsed_sub.bind(size=self.elapsed_sub.setter("text_size"))
        center_stack.add_widget(self.elapsed_sub)
        center_anchor.add_widget(center_stack)
        col.add_widget(center_anchor)

        # ---- Bottom controls ----
        controls = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=_rw_suv(82),
            spacing=_rw_suh(20),
            padding=[0, 0, 0, _rw_suv(6)],
        )

        # Pause (round)
        pause_size = _rw_suv(72)
        self.pause_btn = _CircleButton(size=(pause_size, pause_size))
        # Two vertical blue bars inside the button (Figma 412:828/829)
        bars_wrap = FloatLayout(size_hint=(1, 1))
        bar_h = _rw_suv(26)
        bar_w = _rw_suv(8)
        for x_hint in (0.39, 0.58):
            bar = Widget(
                size_hint=(None, None),
                size=(bar_w, bar_h),
                pos_hint={"center_x": x_hint, "center_y": 0.5},
            )
            with bar.canvas:
                Color(*_BLUE)
                rect = RoundedRectangle(pos=bar.pos, size=bar.size, radius=[_rw_suv(2)])
            bar.bind(
                pos=lambda w, _v, r=rect: setattr(r, "pos", w.pos),
                size=lambda w, _v, r=rect: setattr(r, "size", w.size),
            )
            bars_wrap.add_widget(bar)
        # Single Play triangle (when paused) — toggled in on_paused/on_resumed
        self._pause_bars_wrap = bars_wrap
        self.pause_btn.add_widget(bars_wrap)
        self.pause_btn.bind(on_release=self._on_pause)
        pause_anchor = AnchorLayout(size_hint=(None, 1), width=_rw_suh(120), anchor_x="center", anchor_y="center")
        pause_anchor.add_widget(self.pause_btn)
        controls.add_widget(pause_anchor)

        # Stop pill (center)
        stop_anchor = AnchorLayout(size_hint=(1, 1), anchor_x="center", anchor_y="center")
        self.stop_pill = _StopRecordingPill(size=(_rw_suh(458), _rw_suv(72)))
        self.stop_pill.bind(on_release=self._on_stop)
        stop_anchor.add_widget(self.stop_pill)
        controls.add_widget(stop_anchor)

        # Settings gear (round)
        gear_size = _rw_suv(72)
        self.gear_btn = _CircleButton(size=(gear_size, gear_size))
        gear_path = _rec_png("icon_settings_gear.png")
        if gear_path:
            self.gear_btn.add_widget(Image(
                source=gear_path,
                size_hint=(None, None),
                size=(_rw_suv(40), _rw_suv(40)),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
                fit_mode="contain",
                allow_stretch=True,
            ))
        self.gear_btn.bind(on_release=lambda *_: self.goto("settings", transition="slide_left"))
        gear_anchor = AnchorLayout(size_hint=(None, 1), width=_rw_suh(120), anchor_x="center", anchor_y="center")
        gear_anchor.add_widget(self.gear_btn)
        controls.add_widget(gear_anchor)

        col.add_widget(controls)
        self.root_layout.add_widget(col)

        # ---- Paused overlay (kept simple — no Figma reference for paused state) ----
        self.paused_overlay = FloatLayout(
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
            opacity=0,
        )
        with self.paused_overlay.canvas.before:
            Color(0.004, 0.031, 0.102, 0.95)
            self._ov_bg = Rectangle(pos=self.paused_overlay.pos, size=self.paused_overlay.size)
        self.paused_overlay.bind(
            pos=lambda w, _v: setattr(self._ov_bg, "pos", w.pos),
            size=lambda w, _v: setattr(self._ov_bg, "size", w.size),
        )

        ov_card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(_rw_suh(560), _rw_suv(360)),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            spacing=_rw_suv(12),
            padding=[_rw_suh(28), _rw_suv(28), _rw_suh(28), _rw_suv(28)],
        )
        with ov_card.canvas.before:
            Color(0.012, 0.043, 0.169, 1.0)
            self._ov_card_bg = RoundedRectangle(pos=ov_card.pos, size=ov_card.size, radius=[_rw_suv(20)])
            Color(*_BORDER)
            self._ov_card_stroke = Line(
                rounded_rectangle=(ov_card.x, ov_card.y, ov_card.width, ov_card.height, _rw_suv(20)),
                width=1.0,
            )
        ov_card.bind(
            pos=lambda w, _v: self._sync_ov_card(w),
            size=lambda w, _v: self._sync_ov_card(w),
        )

        self.paused_title = Label(
            text="Paused at --:--",
            font_size=_rw_suf(40),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=_rw_suv(54),
        )
        self.paused_title.bind(size=self.paused_title.setter("text_size"))
        ov_card.add_widget(self.paused_title)
        self.ov_meeting_label = Label(
            text="Recording",
            font_size=_rw_suf(22),
            bold=True,
            color=_BLUE,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=_rw_suv(30),
            shorten=True,
        )
        self.ov_meeting_label.bind(size=self.ov_meeting_label.setter("text_size"))
        ov_card.add_widget(self.ov_meeting_label)
        self.paused_duration = Label(
            text="Meeting duration: 00:00",
            font_size=_rw_suf(18),
            color=_TEXT_MUTED,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=_rw_suv(28),
        )
        self.paused_duration.bind(size=self.paused_duration.setter("text_size"))
        ov_card.add_widget(self.paused_duration)
        ov_card.add_widget(Widget())
        self.ov_room_label = Label(
            text="MeetingBox",
            font_size=_rw_suf(14),
            color=_TEXT_MUTED,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=_rw_suv(20),
        )
        self.ov_room_label.bind(size=self.ov_room_label.setter("text_size"))
        ov_card.add_widget(self.ov_room_label)
        self.paused_overlay.add_widget(ov_card)

        self.add_widget(self.root_layout)

    def _sync_ov_card(self, w):
        self._ov_card_bg.pos = w.pos
        self._ov_card_bg.size = w.size
        self._ov_card_bg.radius = [_rw_suv(20)]
        self._ov_card_stroke.rounded_rectangle = (
            w.x, w.y, w.width, w.height, _rw_suv(20)
        )

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------
    def on_enter(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None

        self._is_paused = False
        self.elapsed_seconds = 0
        self._rec_base_elapsed = 0.0
        self._rec_active_start = time.monotonic()
        self.timer_label.text = "00 : 00 : 00"
        self.elapsed_sub.text = "Recording in progress"
        self.rec_state_label.text = "Recording..."
        self.rec_state_label.color = COLORS["white"]
        self.waveform.set_active(True)
        self._level_history = deque([0.0] * _Waveform.NUM_BARS, maxlen=_Waveform.NUM_BARS)
        self._last_audio_level_ts = 0.0
        if self.paused_overlay.parent is self.root_layout:
            self.root_layout.remove_widget(self.paused_overlay)

        # Started at + meeting metadata
        now = display_now()
        self._started_at_str = now.strftime("%I:%M %p").lstrip("0")
        self.started_at_label.text = f"Started at {self._started_at_str}"
        self.meeting_title_label.text = "Recording"
        self.participants_label.text = ""
        self.provider_label.text = ""

        sid = getattr(self.app, "current_session_id", None)
        if sid:
            self._fetch_meeting_metadata(sid)

        self.timer_event = Clock.schedule_interval(self._tick_timer, 0.5)
        self.waveform_event = Clock.schedule_interval(self._tick_waveform, 0.08)

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None

    # ------------------------------------------------------------------
    # Meeting metadata (live title / participants / provider)
    # ------------------------------------------------------------------
    def _fetch_meeting_metadata(self, meeting_id: str):
        async def _run():
            try:
                detail = await self.backend.get_meeting_detail(meeting_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("recording: meeting detail fetch failed: %s", exc)
                return
            title = (detail.get("title") or "Recording").strip() or "Recording"
            # Backend doesn't ship participant_count / provider on every row;
            # fall back to "—" so the UI never shows "0 Participants".
            try:
                participants = int(detail.get("participant_count") or detail.get("attendee_count") or 0)
            except (TypeError, ValueError):
                participants = 0
            provider = (
                (detail.get("source") or "")
                or (detail.get("calendar_source") or "")
                or ""
            ).strip()

            def _apply(_dt):
                self._meeting_title = title
                self._participant_count = participants
                self._meeting_provider = provider
                self.meeting_title_label.text = title
                if participants:
                    self.participants_label.text = (
                        f"{participants} Participants" if participants != 1 else "1 Participant"
                    )
                else:
                    self.participants_label.text = ""
                self.provider_label.text = provider
                self.ov_meeting_label.text = title

            Clock.schedule_once(_apply, 0)

        run_async(_run())

    # ------------------------------------------------------------------
    # TIMER
    # ------------------------------------------------------------------
    def _elapsed_from_monotonic(self) -> int:
        if self._is_paused or self._rec_active_start is None:
            return int(self._rec_base_elapsed)
        return int(self._rec_base_elapsed + (time.monotonic() - self._rec_active_start))

    def _tick_timer(self, _dt):
        self.elapsed_seconds = self._elapsed_from_monotonic()
        self.timer_label.text = self._fmt_time(self.elapsed_seconds)

    @staticmethod
    def _fmt_time(secs):
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        return f"{h:02d} : {m:02d} : {s:02d}"

    # ------------------------------------------------------------------
    # PAUSE / RESUME
    # ------------------------------------------------------------------
    def _on_pause(self, _inst):
        if self._is_paused:
            self.app.resume_recording()
        else:
            self.app.pause_recording()

    def on_paused(self):
        if self._is_paused:
            return
        self._is_paused = True
        if self._rec_active_start is not None:
            self._rec_base_elapsed += time.monotonic() - self._rec_active_start
            self._rec_active_start = None

        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None

        self.waveform.set_active(False)
        self.waveform.set_levels([2] * _Waveform.NUM_BARS)
        self.rec_state_label.text = "Paused"
        self.elapsed_sub.text = "Recording paused"

        now = display_now()
        self.paused_title.text = f"Paused at {now.strftime('%I:%M %p').lstrip('0')}"
        self.paused_duration.text = f"Meeting duration: {self._fmt_time(self.elapsed_seconds)}"
        self.ov_room_label.text = getattr(self.app, "device_name", "MeetingBox")
        self.ov_meeting_label.text = self._meeting_title or "Recording"

        if self.paused_overlay.parent is not self.root_layout:
            self.root_layout.add_widget(self.paused_overlay)
        self.paused_overlay.opacity = 0
        Animation(opacity=1, duration=0.25).start(self.paused_overlay)

    def on_resumed(self):
        if not self._is_paused:
            return
        self._is_paused = False
        self._rec_active_start = time.monotonic()

        Animation(opacity=0, duration=0.2).start(self.paused_overlay)
        Clock.schedule_once(self._hide_paused_overlay, 0.25)

        self.waveform.set_active(True)
        self.rec_state_label.text = "Recording..."
        self.elapsed_sub.text = "Recording in progress"
        if self.timer_event:
            self.timer_event.cancel()
        if self.waveform_event:
            self.waveform_event.cancel()
        self.timer_event = Clock.schedule_interval(self._tick_timer, 1.0)
        self.waveform_event = Clock.schedule_interval(self._tick_waveform, 0.08)

    def _hide_paused_overlay(self, _dt):
        if self.paused_overlay.parent is self.root_layout:
            self.root_layout.remove_widget(self.paused_overlay)

    def on_audio_level(self, level: float):
        if self._is_paused:
            return
        # Noise gate so bars remain still in quiet rooms.
        gated = 0.0 if level < 0.015 else min(1.0, level)
        self._level_history.append(gated)
        self._last_audio_level_ts = datetime.now().timestamp()

    def _tick_waveform(self, _dt):
        if self._is_paused:
            return
        now_ts = datetime.now().timestamp()
        if now_ts - self._last_audio_level_ts > 0.25:
            self._level_history = deque([v * 0.82 for v in self._level_history], maxlen=_Waveform.NUM_BARS)
        levels = [max(2, int(v * self.waveform.MAX_H)) for v in self._level_history]
        self.waveform.set_levels(levels)

    # ------------------------------------------------------------------
    # STOP
    # ------------------------------------------------------------------
    def _on_stop(self, _inst):
        logger.info("End Meeting pressed (duration: %s)", self._fmt_time(self.elapsed_seconds))
        self.app.stop_recording()

    # ------------------------------------------------------------------
    # External events called from main.py
    # ------------------------------------------------------------------
    def on_audio_segment(self, segment_num: int):
        if self._participant_count == 0 and segment_num >= 0:
            # Fallback: surface a count from segment activity when the
            # backend hasn't returned an explicit attendee_count.
            pc = max(1, segment_num + 1)
            self._participant_count = pc
            self.participants_label.text = f"{pc} Participants" if pc != 1 else "1 Participant"
