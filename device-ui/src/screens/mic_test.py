"""
Microphone Test Screen – PRD §5.15

Live audio waveform + input level indicator.
Uses sounddevice for real mic capture when available,
falls back to simulated bars otherwise.
"""

import logging
import struct
import wave
from collections import deque
from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, BORDER_RADIUS

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    _HAS_AUDIO = True
except (ImportError, OSError) as e:
    _HAS_AUDIO = False
    logger.warning(
        "sounddevice unavailable — mic test will use simulated data: %s", e
    )


class _TestWaveform(Widget):
    """Bar-chart waveform visualisation."""

    NUM_BARS = 18
    BAR_WIDTH = 14
    BAR_SPACING = 6

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (1, None))
        kwargs.setdefault('height', 60)
        super().__init__(**kwargs)
        self._levels = [2] * self.NUM_BARS
        self.bind(pos=self._draw, size=self._draw)

    def set_levels(self, levels: list):
        self._levels = levels
        self._draw()

    def _draw(self, *_args):
        self.canvas.clear()
        total_w = self.NUM_BARS * (self.BAR_WIDTH + self.BAR_SPACING)
        start_x = self.x + (self.width - total_w) / 2
        base_y = self.y + 2

        with self.canvas:
            for i, h in enumerate(self._levels):
                Color(*COLORS['blue'])
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, base_y),
                    size=(self.BAR_WIDTH, max(2, h)),
                    radius=[3],
                )


class MicTestScreen(BaseScreen):
    """Microphone test screen – PRD §5.15."""

    SAMPLE_RATE = 16000
    BLOCK_SIZE = 1600  # 100ms chunks
    MAX_BAR_HEIGHT = 55

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wave_event = None
        self._stream = None
        self._rms_history = deque(maxlen=_TestWaveform.NUM_BARS)
        for _ in range(_TestWaveform.NUM_BARS):
            self._rms_history.append(0.0)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Microphone Test',
            device_name='Microphone Test',
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.1)))

        instr = Label(
            text='Speak to test your microphone',
            font_size=FONT_SIZES['medium'],
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        instr.bind(size=instr.setter('text_size'))
        root.add_widget(instr)

        root.add_widget(Widget(size_hint=(1, 0.1)))

        self.waveform = _TestWaveform()
        root.add_widget(self.waveform)

        root.add_widget(Widget(size_hint=(1, 0.1)))

        self.level_label = Label(
            text='Detecting…',
            font_size=FONT_SIZES['small'] + 2,
            bold=True,
            color=COLORS['gray_500'],
            halign='center',
            size_hint=(1, None), height=24,
        )
        root.add_widget(self.level_label)

        root.add_widget(Widget())

        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        if _HAS_AUDIO:
            self._start_audio_stream()
        self._wave_event = Clock.schedule_interval(self._tick, 0.1)

    def on_leave(self):
        if self._wave_event:
            self._wave_event.cancel()
            self._wave_event = None
        self._stop_audio_stream()

    # ------------------------------------------------------------------
    # Audio capture
    # ------------------------------------------------------------------
    def _start_audio_stream(self):
        try:
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype='int16',
                blocksize=self.BLOCK_SIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
            logger.info("Mic test: audio stream started")
        except Exception as e:
            logger.warning("Mic test: could not open audio stream: %s", e)
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
        if status:
            logger.debug("Mic test audio status: %s", status)
        samples = indata[:, 0]
        rms = (sum(int(s) ** 2 for s in samples) / len(samples)) ** 0.5
        normalised = min(1.0, rms / 5000.0)
        self._rms_history.append(normalised)

    # ------------------------------------------------------------------
    # UI update tick
    # ------------------------------------------------------------------
    def _tick(self, _dt):
        levels = [max(2, int(v * self.MAX_BAR_HEIGHT))
                  for v in self._rms_history]
        self.waveform.set_levels(levels)

        if not _HAS_AUDIO or not self._stream:
            self.level_label.text = 'No microphone detected'
            self.level_label.color = COLORS['red']
            return

        peak = max(self._rms_history)
        if peak > 0.15:
            self.level_label.text = 'Input Level: Good'
            self.level_label.color = COLORS['green']
        elif peak > 0.03:
            self.level_label.text = 'Input Level: Low'
            self.level_label.color = COLORS['yellow']
        else:
            self.level_label.text = 'Input Level: No Sound'
            self.level_label.color = COLORS['gray_500']
