"""Recording screen matching reference layout (1024x600).

Two visual states driven by self._is_paused:
  Active  – RECORDING pill, large timer, blue waveform bars, Pause + End buttons
  Paused  – PAUSED pill, "Paused at HH:MM", duration, mic-off icon, Resume + End buttons
"""

import logging
import random
from collections import deque
from datetime import datetime
from pathlib import Path

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from config import ASSETS_DIR, COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    _HAS_AUDIO = True
except (ImportError, OSError) as e:
    _HAS_AUDIO = False
    logger.warning("sounddevice unavailable – waveform will simulate: %s", e)

_REC_ASSETS = ASSETS_DIR / "recording"


class _ImageButton(ButtonBehavior, Image):
    pass


class _LabelButton(ButtonBehavior, Label):
    pass


# ---------------------------------------------------------------------------
# Waveform – blue vertical bars, driven by real RMS or simulation
# ---------------------------------------------------------------------------

class _Waveform(Widget):
    NUM_BARS = 28
    BAR_WIDTH = 4
    BAR_SPACING = 4
    MAX_H = 100

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (self.NUM_BARS * (self.BAR_WIDTH + self.BAR_SPACING), self.MAX_H * 2))
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
        total_w = self.NUM_BARS * (self.BAR_WIDTH + self.BAR_SPACING)
        start_x = self.x + (self.width - total_w) / 2
        mid_y = self.center_y

        with self.canvas:
            for i, h in enumerate(self._levels):
                half = max(1, h / 2)
                Color(0.30, 0.56, 0.98, 1)
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, mid_y - half),
                    size=(self.BAR_WIDTH, half * 2),
                    radius=[2],
                )


# ---------------------------------------------------------------------------
# Recording Screen
# ---------------------------------------------------------------------------

class RecordingScreen(BaseScreen):
    SAMPLE_RATE = 16000
    BLOCK_SIZE = 1600

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.elapsed_seconds = 0
        self.timer_event = None
        self.waveform_event = None
        self._is_paused = False
        self._paused_at_text = ""
        self._stream = None
        self._rms_history = deque(maxlen=_Waveform.NUM_BARS)
        for _ in range(_Waveform.NUM_BARS):
            self._rms_history.append(0.0)
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

        content = BoxLayout(orientation="vertical", size_hint=(1, 1))

        # --- top bar ---
        top = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=62,
            padding=[SPACING["screen_padding"], 12],
            spacing=10,
        )

        self.rec_badge = Image(
            source=str(_REC_ASSETS / "Overlay.png"),
            size_hint=(None, None),
            size=(130, 32),
            allow_stretch=True,
            keep_ratio=True,
        )
        top.add_widget(self.rec_badge)
        top.add_widget(Widget())

        timer_col = BoxLayout(orientation="vertical", size_hint=(None, 1), width=200)
        self.timer_label = Label(
            text="00:00",
            font_size=48,
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="bottom",
        )
        self.timer_label.bind(size=self.timer_label.setter("text_size"))
        timer_col.add_widget(self.timer_label)

        self.elapsed_sub = Label(
            text="ELAPSED TIME",
            font_size=FONT_SIZES["tiny"],
            color=COLORS["gray_500"],
            halign="center",
            valign="top",
            size_hint=(1, None),
            height=16,
        )
        self.elapsed_sub.bind(size=self.elapsed_sub.setter("text_size"))
        timer_col.add_widget(self.elapsed_sub)
        top.add_widget(timer_col)

        top.add_widget(Widget())

        gear_path = _REC_ASSETS / "setteing gear icon.png"
        self.gear_btn = _ImageButton(
            source=str(gear_path),
            size_hint=(None, None),
            size=(32, 32),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.gear_btn.bind(on_press=lambda *_: self.goto("settings", transition="slide_left"))
        top.add_widget(self.gear_btn)
        content.add_widget(top)

        # --- center waveform ---
        content.add_widget(Widget())

        wave_wrap = BoxLayout(orientation="horizontal", size_hint=(1, None), height=200)
        wave_wrap.add_widget(Widget())
        self.waveform = _Waveform()
        wave_wrap.add_widget(self.waveform)
        wave_wrap.add_widget(Widget())
        content.add_widget(wave_wrap)

        content.add_widget(Widget())

        # --- bottom buttons (active state) ---
        self.active_btn_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=64,
            padding=[SPACING["screen_padding"] * 4, 0],
            spacing=24,
        )
        self.active_btn_row.add_widget(Widget())

        pause_path = _REC_ASSETS / "Pause recording button.png"
        self.pause_btn = _ImageButton(
            source=str(pause_path),
            size_hint=(None, None),
            size=(220, 50),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.pause_btn.bind(on_press=self._on_pause)
        self.active_btn_row.add_widget(self.pause_btn)

        end_path = _REC_ASSETS / "end meetingbutton.png"
        self.end_btn = _ImageButton(
            source=str(end_path),
            size_hint=(None, None),
            size=(200, 50),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.end_btn.bind(on_press=self._on_stop)
        self.active_btn_row.add_widget(self.end_btn)

        self.active_btn_row.add_widget(Widget())
        content.add_widget(self.active_btn_row)

        content.add_widget(Widget(size_hint=(1, None), height=12))

        self.root_layout.add_widget(content)

        # === PAUSED OVERLAY (hidden initially) ===
        self.paused_overlay = FloatLayout(size_hint=(1, 1), opacity=0)
        with self.paused_overlay.canvas.before:
            Color(0.04, 0.06, 0.10, 0.92)
            self._ov_bg = Rectangle(pos=self.paused_overlay.pos, size=self.paused_overlay.size)
        self.paused_overlay.bind(
            pos=lambda w, _: setattr(self._ov_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._ov_bg, "size", w.size),
        )

        ov_content = BoxLayout(orientation="vertical", size_hint=(1, 1))

        ov_top = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=62,
            padding=[SPACING["screen_padding"], 12],
            spacing=10,
        )
        self.paused_badge = Image(
            source=str(_REC_ASSETS / "PAUSED icon for top left.png"),
            size_hint=(None, None),
            size=(120, 32),
            allow_stretch=True,
            keep_ratio=True,
        )
        ov_top.add_widget(self.paused_badge)
        ov_top.add_widget(Widget())

        ov_right = BoxLayout(orientation="vertical", size_hint=(None, 1), width=200)
        self.ov_room_label = Label(
            text="MeetingBox",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_400"],
            halign="right",
            valign="bottom",
        )
        self.ov_room_label.bind(size=self.ov_room_label.setter("text_size"))
        ov_right.add_widget(self.ov_room_label)
        ov_top.add_widget(ov_right)

        self.ov_gear = _ImageButton(
            source=str(gear_path),
            size_hint=(None, None),
            size=(32, 32),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.ov_gear.bind(on_press=lambda *_: self.goto("settings", transition="slide_left"))
        ov_top.add_widget(self.ov_gear)
        ov_content.add_widget(ov_top)

        ov_content.add_widget(Widget())

        self.paused_title = Label(
            text="Paused at --:--",
            font_size=52,
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=70,
        )
        self.paused_title.bind(size=self.paused_title.setter("text_size"))
        ov_content.add_widget(self.paused_title)

        self.paused_duration = Label(
            text="Meeting duration: 00:00",
            font_size=FONT_SIZES["body"],
            color=COLORS["gray_400"],
            halign="center",
            size_hint=(1, None),
            height=26,
        )
        self.paused_duration.bind(size=self.paused_duration.setter("text_size"))
        ov_content.add_widget(self.paused_duration)

        ov_content.add_widget(Widget(size_hint=(1, None), height=16))

        line_wrap = BoxLayout(size_hint=(1, None), height=2, padding=[120, 0])
        line_w = Widget(size_hint=(1, 1))
        with line_w.canvas:
            Color(0.30, 0.56, 0.98, 0.6)
            self._pause_line = Rectangle(pos=line_w.pos, size=line_w.size)
        line_w.bind(
            pos=lambda w, _: setattr(self._pause_line, "pos", w.pos),
            size=lambda w, _: setattr(self._pause_line, "size", w.size),
        )
        line_wrap.add_widget(line_w)
        ov_content.add_widget(line_wrap)

        ov_content.add_widget(Widget(size_hint=(1, None), height=20))

        mic_wrap = BoxLayout(orientation="vertical", size_hint=(1, None), height=80)
        mic_icon_wrap = BoxLayout(orientation="horizontal", size_hint=(1, None), height=46)
        mic_icon_wrap.add_widget(Widget())
        mic_circle = FloatLayout(size_hint=(None, None), size=(42, 42))
        with mic_circle.canvas.before:
            Color(*COLORS["gray_700"])
            self._mic_bg = Ellipse(pos=mic_circle.pos, size=mic_circle.size)
        mic_circle.bind(
            pos=lambda w, _: setattr(self._mic_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._mic_bg, "size", w.size),
        )
        mic_icon = Image(
            source=str(_REC_ASSETS / "mic mute icon.png"),
            size_hint=(None, None),
            size=(20, 20),
            allow_stretch=True,
            keep_ratio=True,
        )
        mic_circle.add_widget(mic_icon)
        mic_circle.bind(
            pos=lambda w, _: self._center_child(mic_icon, w),
            size=lambda w, _: self._center_child(mic_icon, w),
        )
        mic_icon_wrap.add_widget(mic_circle)
        mic_icon_wrap.add_widget(Widget())
        mic_wrap.add_widget(mic_icon_wrap)

        mic_label = Label(
            text="Microphone is off",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_500"],
            halign="center",
            size_hint=(1, None),
            height=22,
        )
        mic_label.bind(size=mic_label.setter("text_size"))
        mic_wrap.add_widget(mic_label)
        ov_content.add_widget(mic_wrap)

        ov_content.add_widget(Widget())

        ov_btn_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=64,
            padding=[SPACING["screen_padding"], 0],
            spacing=0,
        )
        resume_path = _REC_ASSETS / "resume recording button.png"
        self.resume_btn = _ImageButton(
            source=str(resume_path),
            size_hint=(None, None),
            size=(240, 50),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.resume_btn.bind(on_press=self._on_pause)
        ov_btn_row.add_widget(self.resume_btn)

        ov_btn_row.add_widget(Widget())

        end_paused_path = _REC_ASSETS / "End meeting.png"
        self.end_paused_btn = _ImageButton(
            source=str(end_paused_path),
            size_hint=(None, None),
            size=(200, 50),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.end_paused_btn.bind(on_press=self._on_stop)
        ov_btn_row.add_widget(self.end_paused_btn)
        ov_content.add_widget(ov_btn_row)
        ov_content.add_widget(Widget(size_hint=(1, None), height=12))

        self.paused_overlay.add_widget(ov_content)
        self.root_layout.add_widget(self.paused_overlay)

        self.add_widget(self.root_layout)

    @staticmethod
    def _center_child(child, parent):
        child.center_x = parent.center_x
        child.center_y = parent.center_y

    # ==================================================================
    # LIFECYCLE
    # ==================================================================

    def on_enter(self):
        self._is_paused = False
        self.elapsed_seconds = 0
        self.timer_label.text = "00:00"
        self.elapsed_sub.text = "ELAPSED TIME"
        self.waveform.set_active(True)
        self.paused_overlay.opacity = 0
        self.paused_overlay.disabled = True

        self.timer_event = Clock.schedule_interval(self._tick_timer, 1.0)

        if _HAS_AUDIO:
            self._start_audio_stream()
            self.waveform_event = Clock.schedule_interval(self._tick_waveform_real, 0.08)
        else:
            self.waveform_event = Clock.schedule_interval(lambda _: self.waveform.update_random(), 0.1)

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None
        self._stop_audio_stream()

    # ==================================================================
    # AUDIO STREAM (real mic levels)
    # ==================================================================

    def _start_audio_stream(self):
        try:
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=self.BLOCK_SIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            logger.warning("Recording waveform: could not open audio: %s", e)
            self._stream = None

    def _stop_audio_stream(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_callback(self, indata, frames, time_info, status):
        samples = indata[:, 0]
        rms = (sum(int(s) ** 2 for s in samples) / len(samples)) ** 0.5
        normalised = min(1.0, rms / 5000.0)
        self._rms_history.append(normalised)

    def _tick_waveform_real(self, _dt):
        levels = [max(2, int(v * _Waveform.MAX_H)) for v in self._rms_history]
        self.waveform.set_levels(levels)

    # ==================================================================
    # TIMER
    # ==================================================================

    def _tick_timer(self, _dt):
        self.elapsed_seconds += 1
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
        self._is_paused = True

        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None
        self._stop_audio_stream()

        self.waveform.set_active(False)
        self.waveform.set_levels([2] * _Waveform.NUM_BARS)

        now = datetime.now()
        self._paused_at_text = now.strftime("%H:%M")
        self.paused_title.text = f"Paused at {self._paused_at_text}"
        self.paused_duration.text = f"Meeting duration: {self._fmt_time(self.elapsed_seconds)}"
        self.ov_room_label.text = getattr(self.app, "device_name", "MeetingBox")

        self.paused_overlay.disabled = False
        Animation(opacity=1, duration=0.25).start(self.paused_overlay)

    def on_resumed(self):
        self._is_paused = False

        Animation(opacity=0, duration=0.2).start(self.paused_overlay)
        Clock.schedule_once(lambda _: setattr(self.paused_overlay, "disabled", True), 0.25)

        self.waveform.set_active(True)
        self.timer_event = Clock.schedule_interval(self._tick_timer, 1.0)

        if _HAS_AUDIO:
            self._start_audio_stream()
            self.waveform_event = Clock.schedule_interval(self._tick_waveform_real, 0.08)
        else:
            self.waveform_event = Clock.schedule_interval(lambda _: self.waveform.update_random(), 0.1)

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
