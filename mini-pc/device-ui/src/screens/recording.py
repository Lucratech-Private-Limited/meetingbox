"""Recording screen matching reference layout (1024x600).

Two visual states driven by self._is_paused:
  Active  – RECORDING pill, large timer, blue waveform bars, Pause + End buttons
  Paused  – PAUSED pill, "Paused at HH:MM", duration, mic-off icon, Resume + End buttons
"""

import logging
import random
import time
from collections import deque
from datetime import datetime

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import (
    ASSETS_DIR,
    COLORS,
    DISPLAY_WIDTH,
    FONT_SIZES,
    OTHER_CONTENT_SCALE,
    SPACING,
    display_now,
    home_center_column_width,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_REC_ASSETS = ASSETS_DIR / "recording"


def _rw_suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _rw_suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


class _ImageButton(ButtonBehavior, Image):
    pass


class _LabelButton(ButtonBehavior, Label):
    pass


# ---------------------------------------------------------------------------
# Waveform – blue vertical bars, driven by real RMS or simulation
# ---------------------------------------------------------------------------

class _Waveform(Widget):
    NUM_BARS = 28

    def __init__(self, **kwargs):
        self.BAR_WIDTH = _rw_suh(4)
        self.BAR_SPACING = _rw_suh(4)
        self.MAX_H = _rw_suv(100)
        # Bars share (NUM_BARS - 1) gaps; width must match _draw extent or layout looks off-center.
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

    def update_random(self):
        if self._active:
            self._levels = [random.randint(6, self.MAX_H) for _ in range(self.NUM_BARS)]
        else:
            self._levels = [2] * self.NUM_BARS
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
        self._build_ui()

    # ==================================================================
    # BUILD
    # ==================================================================

    def _build_ui(self):
        self.root_layout = FloatLayout()
        with self.root_layout.canvas.before:
            Color(0.04, 0.06, 0.10, 1)
            self._bg = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
        self.root_layout.bind(
            pos=lambda w, _: setattr(self._bg, "pos", w.pos),
            size=lambda w, _: setattr(self._bg, "size", w.size),
        )

        # Same idea as home: AnchorLayout + fixed column width avoids horizontal BoxLayout
        # spacer drift on ultrawide / some Kivy layout paths.
        col_w = max(
            360,
            min(DISPLAY_WIDTH, int(home_center_column_width() * OTHER_CONTENT_SCALE)),
        )
        # Must fit pause+end row and paused resume+end row (scaled), or controls clip.
        min_active = (
            self.suh(268)
            + self.suh(252)
            + self.suh(24)
            + 2 * self.suh(SPACING["screen_padding"] * 3)
        )
        min_paused = (
            self.suh(292)
            + self.suh(252)
            + 2 * self.suh(SPACING["screen_padding"])
        )
        col_w = max(col_w, min_active, min_paused)
        col_w = min(col_w, DISPLAY_WIDTH)
        mid_anchor = AnchorLayout(
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
            anchor_x="center",
            anchor_y="top",
        )
        content = BoxLayout(orientation="vertical", size_hint=(None, 1), width=col_w)

        # --- top: badge centered; gear in a matching-width column (avoids left/right drift) ---
        top_block = BoxLayout(orientation="vertical", size_hint=(1, None), height=self.suv(128))

        gear_path = _REC_ASSETS / "setteing gear icon.png"
        self.gear_btn = _ImageButton(
            source=str(gear_path),
            size_hint=(None, None),
            size=(self.suh(32), self.suv(32)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.gear_btn.bind(on_press=lambda *_: self.goto("settings", transition="slide_left"))

        gear_col = max(self.suh(44), self.suv(36))
        top_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=self.suv(48),
            padding=[0, self.suv(8), 0, self.suv(4)],
        )
        top_row.add_widget(Widget(size_hint=(None, 1), width=gear_col))

        self.rec_badge = Image(
            source=str(_REC_ASSETS / "Overlay.png"),
            size_hint=(None, None),
            size=(self.suh(130), self.suv(32)),
            allow_stretch=True,
            keep_ratio=True,
        )
        badge_anchor = AnchorLayout(size_hint=(1, 1), anchor_x="center", anchor_y="center")
        badge_anchor.add_widget(self.rec_badge)
        top_row.add_widget(badge_anchor)

        gear_anchor = AnchorLayout(
            size_hint=(None, 1),
            width=gear_col,
            anchor_x="right",
            anchor_y="top",
        )
        gear_anchor.add_widget(self.gear_btn)
        top_row.add_widget(gear_anchor)
        top_block.add_widget(top_row)

        timer_anchor = AnchorLayout(
            anchor_x="center",
            anchor_y="center",
            size_hint=(1, None),
            height=self.suv(72),
            padding=[0, self.suv(4), 0, 0],
        )
        timer_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=self.suh(220),
            height=self.suv(66),
        )
        self.timer_label = Label(
            text="00:00",
            font_size=self.suf(42),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(44),
        )
        self.timer_label.bind(size=self.timer_label.setter("text_size"))
        timer_col.add_widget(self.timer_label)

        self.elapsed_sub = Label(
            text="ELAPSED TIME",
            font_size=self.suf(FONT_SIZES["tiny"]),
            color=COLORS["gray_500"],
            halign="center",
            valign="top",
            size_hint=(1, None),
            height=self.suv(18),
        )
        self.elapsed_sub.bind(size=self.elapsed_sub.setter("text_size"))
        timer_col.add_widget(self.elapsed_sub)
        timer_anchor.add_widget(timer_col)
        top_block.add_widget(timer_anchor)

        content.add_widget(top_block)

        # --- center waveform (anchor — no flex spacers) ---
        content.add_widget(Widget())

        wave_anchor = AnchorLayout(
            size_hint=(1, None),
            height=self.suv(200),
            anchor_x="center",
            anchor_y="center",
        )
        self.waveform = _Waveform()
        wave_anchor.add_widget(self.waveform)
        content.add_widget(wave_anchor)

        content.add_widget(Widget())

        # --- bottom buttons (active): fixed-width inner row, centered ---
        pause_path = _REC_ASSETS / "Pause recording button.png"
        self.pause_btn = _ImageButton(
            source=str(pause_path),
            size_hint=(None, None),
            size=(self.suh(268), self.suv(58)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.pause_btn.bind(on_press=self._on_pause)

        end_path = _REC_ASSETS / "end meetingbutton.png"
        self.end_btn = _ImageButton(
            source=str(end_path),
            size_hint=(None, None),
            size=(self.suh(252), self.suv(58)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.end_btn.bind(on_press=self._on_stop)

        active_inner = BoxLayout(
            orientation="horizontal",
            size_hint=(None, None),
            height=self.suv(58),
            spacing=self.suh(24),
            width=self.suh(268) + self.suh(252) + self.suh(24),
        )
        active_inner.add_widget(self.pause_btn)
        active_inner.add_widget(self.end_btn)

        self.active_btn_row = AnchorLayout(
            size_hint=(1, None),
            height=self.suv(76),
            anchor_x="center",
            anchor_y="top",
            padding=[0, 0, 0, self.suv(18)],
        )
        self.active_btn_row.add_widget(active_inner)
        content.add_widget(self.active_btn_row)

        content.add_widget(Widget(size_hint=(1, None), height=self.suv(12)))

        mid_anchor.add_widget(content)
        self.root_layout.add_widget(mid_anchor)

        # === PAUSED OVERLAY (hidden initially) ===
        self.paused_overlay = FloatLayout(
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
            opacity=0,
        )
        with self.paused_overlay.canvas.before:
            Color(0.04, 0.06, 0.10, 0.92)
            self._ov_bg = Rectangle(pos=self.paused_overlay.pos, size=self.paused_overlay.size)
        self.paused_overlay.bind(
            pos=lambda w, _: setattr(self._ov_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._ov_bg, "size", w.size),
        )

        ov_content = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[self.suh(24), self.suv(24), self.suh(24), self.suv(24)],
        )

        top_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=self.suv(52),
        )
        self.paused_badge = Image(
            source=str(_REC_ASSETS / "PAUSED icon for top left.png"),
            size_hint=(None, None),
            size=(self.suh(120), self.suv(36)),
            allow_stretch=True,
            keep_ratio=True,
        )
        left_badge_anchor = AnchorLayout(
            size_hint=(None, 1),
            width=self.suh(140),
            anchor_x="left",
            anchor_y="center",
        )
        left_badge_anchor.add_widget(self.paused_badge)
        top_row.add_widget(left_badge_anchor)
        top_row.add_widget(Widget())

        right_cluster = BoxLayout(
            orientation="horizontal",
            size_hint=(None, 1),
            width=self.suh(248),
            spacing=self.suh(16),
        )
        meta_col = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            spacing=0,
        )
        self.ov_room_label = Label(
            text="MeetingBox",
            font_size=self.suf(FONT_SIZES["tiny"]),
            color=(148 / 255.0, 163 / 255.0, 184 / 255.0, 1),
            halign="right",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(16),
        )
        self.ov_room_label.bind(size=self.ov_room_label.setter("text_size"))
        meta_col.add_widget(self.ov_room_label)
        self.ov_meeting_label = Label(
            text="Project Sync",
            font_size=self.suf(FONT_SIZES["small"]),
            bold=True,
            color=(241 / 255.0, 245 / 255.0, 249 / 255.0, 1),
            halign="right",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(20),
        )
        self.ov_meeting_label.bind(size=self.ov_meeting_label.setter("text_size"))
        meta_col.add_widget(self.ov_meeting_label)
        right_cluster.add_widget(meta_col)

        gear_shell = AnchorLayout(
            size_hint=(None, None),
            size=(self.suh(40), self.suv(40)),
            anchor_x="center",
            anchor_y="center",
        )
        with gear_shell.canvas.before:
            Color(74 / 255.0, 143 / 255.0, 217 / 255.0, 0.20)
            self._ov_gear_bg = RoundedRectangle(pos=gear_shell.pos, size=gear_shell.size, radius=[999])
        gear_shell.bind(
            pos=lambda w, *_: setattr(self._ov_gear_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._ov_gear_bg, "size", w.size),
        )
        self.ov_gear = _ImageButton(
            source=str(gear_path),
            size_hint=(None, None),
            size=(self.suh(18), self.suv(18)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.ov_gear.bind(on_press=lambda *_: self.goto("settings", transition="slide_left"))
        gear_shell.add_widget(self.ov_gear)
        right_cluster.add_widget(gear_shell)
        top_row.add_widget(right_cluster)
        ov_content.add_widget(top_row)

        main_wrap = AnchorLayout(size_hint=(1, 1), anchor_x="center", anchor_y="center")
        main_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=min(self.suh(760), max(self.suh(520), DISPLAY_WIDTH - self.suh(64))),
            height=self.suv(420),
            spacing=self.suv(16),
        )
        self.paused_title = Label(
            text="Paused at --:--",
            font_size=self.suf(50),
            bold=True,
            color=(241 / 255.0, 245 / 255.0, 249 / 255.0, 1),
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(62),
        )
        self.paused_title.bind(size=self.paused_title.setter("text_size"))
        main_col.add_widget(self.paused_title)

        self.paused_duration = Label(
            text="Meeting duration: 00:00",
            font_size=self.suf(18),
            color=(148 / 255.0, 163 / 255.0, 184 / 255.0, 1),
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(30),
        )
        self.paused_duration.bind(size=self.paused_duration.setter("text_size"))
        main_col.add_widget(self.paused_duration)
        main_col.add_widget(Widget(size_hint=(1, None), height=self.suv(24)))

        line_wrap = BoxLayout(
            size_hint=(1, None),
            height=self.suv(8),
            padding=[self.suh(72), 0, self.suh(72), 0],
        )
        line_w = Widget(size_hint=(1, None), height=self.suv(4))
        with line_w.canvas:
            Color(74 / 255.0, 143 / 255.0, 217 / 255.0, 1)
            self._pause_line = RoundedRectangle(pos=line_w.pos, size=line_w.size, radius=[999])
        line_w.bind(
            pos=lambda w, *_: setattr(self._pause_line, "pos", w.pos),
            size=lambda w, *_: setattr(self._pause_line, "size", w.size),
        )
        line_wrap.add_widget(line_w)
        main_col.add_widget(line_wrap)
        main_col.add_widget(Widget(size_hint=(1, None), height=self.suv(32)))

        mic_wrap = BoxLayout(orientation="vertical", size_hint=(1, None), height=self.suv(112))
        mic_icon_wrap = AnchorLayout(
            size_hint=(1, None),
            height=self.suv(64),
            anchor_x="center",
            anchor_y="center",
        )
        mic_circle = FloatLayout(size_hint=(None, None), size=(self.suv(64), self.suv(64)))
        with mic_circle.canvas.before:
            Color(30 / 255.0, 41 / 255.0, 59 / 255.0, 1)
            self._mic_bg = Ellipse(pos=mic_circle.pos, size=mic_circle.size)
        mic_circle.bind(
            pos=lambda w, _: setattr(self._mic_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._mic_bg, "size", w.size),
        )
        mic_icon = Image(
            source=str(_REC_ASSETS / "mic mute icon.png"),
            size_hint=(None, None),
            size=(self.suv(26), self.suv(26)),
            allow_stretch=True,
            keep_ratio=True,
        )
        mic_circle.add_widget(mic_icon)
        mic_circle.bind(
            pos=lambda w, _: self._center_child(mic_icon, w),
            size=lambda w, _: self._center_child(mic_icon, w),
        )
        mic_icon_wrap.add_widget(mic_circle)
        mic_wrap.add_widget(mic_icon_wrap)

        mic_label = Label(
            text="Microphone is off",
            font_size=self.suf(FONT_SIZES["body"]),
            color=(148 / 255.0, 163 / 255.0, 184 / 255.0, 1),
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=self.suv(30),
        )
        mic_label.bind(size=mic_label.setter("text_size"))
        mic_wrap.add_widget(mic_label)
        main_col.add_widget(mic_wrap)
        main_wrap.add_widget(main_col)
        ov_content.add_widget(main_wrap)

        footer = BoxLayout(orientation="vertical", size_hint=(1, None), height=self.suv(116))
        divider = Widget(size_hint=(1, None), height=1)
        with divider.canvas:
            Color(30 / 255.0, 41 / 255.0, 59 / 255.0, 1)
            self._ov_footer_divider = Rectangle(pos=divider.pos, size=divider.size)
        divider.bind(
            pos=lambda w, *_: setattr(self._ov_footer_divider, "pos", w.pos),
            size=lambda w, *_: setattr(self._ov_footer_divider, "size", w.size),
        )
        footer.add_widget(divider)
        footer.add_widget(Widget(size_hint=(1, None), height=self.suv(28)))

        resume_path = _REC_ASSETS / "resume recording button.png"
        self.resume_btn = _ImageButton(
            source=str(resume_path),
            size_hint=(None, None),
            size=(self.suh(292), self.suv(58)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.resume_btn.bind(on_press=self._on_pause)

        end_paused_path = _REC_ASSETS / "End meeting.png"
        self.end_paused_btn = _ImageButton(
            source=str(end_paused_path),
            size_hint=(None, None),
            size=(self.suh(252), self.suv(58)),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.end_paused_btn.bind(on_press=self._on_stop)

        footer_btns = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=self.suv(58),
            padding=[0, 0, 0, 0],
        )
        footer_btns.add_widget(self.resume_btn)
        footer_btns.add_widget(Widget())
        footer_btns.add_widget(self.end_paused_btn)
        footer.add_widget(footer_btns)
        ov_content.add_widget(footer)

        self.paused_overlay.add_widget(ov_content)

        self.add_widget(self.root_layout)

    @staticmethod
    def _center_child(child, parent):
        child.center_x = parent.center_x
        child.center_y = parent.center_y

    # ==================================================================
    # LIFECYCLE
    # ==================================================================

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
        self.timer_label.text = "00:00"
        self.elapsed_sub.text = "ELAPSED TIME"
        self.waveform.set_active(True)
        self._level_history = deque([0.0] * _Waveform.NUM_BARS, maxlen=_Waveform.NUM_BARS)
        self._last_audio_level_ts = 0.0
        if self.paused_overlay.parent is self.root_layout:
            self.root_layout.remove_widget(self.paused_overlay)

        self.timer_event = Clock.schedule_interval(self._tick_timer, 0.5)
        self.waveform_event = Clock.schedule_interval(self._tick_waveform, 0.08)

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None

    # ==================================================================
    # TIMER
    # ==================================================================

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
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    # ==================================================================
    # PAUSE / RESUME
    # ==================================================================

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

        now = display_now()
        self.paused_title.text = f"Paused at {now.strftime('%H:%M')}"
        self.paused_duration.text = f"Meeting duration: {self._fmt_time(self.elapsed_seconds)}"
        self.ov_room_label.text = getattr(self.app, "device_name", "MeetingBox")

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

    # ==================================================================
    # STOP
    # ==================================================================

    def _on_stop(self, _inst):
        logger.info("End Meeting pressed (duration: %s)", self._fmt_time(self.elapsed_seconds))
        self.app.stop_recording()

    # ==================================================================
    # EXTERNAL EVENTS (called from main.py)
    # ==================================================================

    def on_audio_segment(self, segment_num: int):
        pass
