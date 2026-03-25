"""Microphone test screen with backend-driven live voice waveform."""

import logging
from collections import deque
from time import time

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle
from kivy.clock import Clock

from async_helper import run_async
from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES

logger = logging.getLogger(__name__)


class _TestWaveform(Widget):
    """Mirrored bar waveform matching recording screen style."""

    NUM_BARS = 28
    BAR_WIDTH = 4
    BAR_SPACING = 4
    MAX_BAR_HEIGHT = 100

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (1, None))
        kwargs.setdefault('height', 200)
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
        mid_y = self.center_y

        with self.canvas:
            for i, h in enumerate(self._levels):
                Color(*COLORS['blue'])
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, mid_y - max(1, h / 2)),
                    size=(self.BAR_WIDTH, max(2, h)),
                    radius=[2],
                )


class MicTestScreen(BaseScreen):
    """Microphone test screen – PRD §5.15."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wave_event = None
        self._rms_history = deque(maxlen=_TestWaveform.NUM_BARS)
        self._last_level_ts = 0.0
        self._mic_test_running = False
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

        root.add_widget(Widget(size_hint=(1, 0.06)))

        instr = Label(
            text='Speak to test your microphone',
            font_size=FONT_SIZES['medium'],
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        instr.bind(size=instr.setter('text_size'))
        root.add_widget(instr)

        root.add_widget(Widget(size_hint=(1, 0.05)))

        self.waveform = _TestWaveform()
        root.add_widget(self.waveform)

        root.add_widget(Widget(size_hint=(1, 0.05)))

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
        self._mic_test_running = False
        self._last_level_ts = 0.0
        self.level_label.text = 'Starting microphone test...'
        self.level_label.color = COLORS['gray_400']
        run_async(self._start_mic_test())
        self._wave_event = Clock.schedule_interval(self._tick, 0.1)

    def on_leave(self):
        if self._wave_event:
            self._wave_event.cancel()
            self._wave_event = None
        run_async(self._stop_mic_test())

    # ------------------------------------------------------------------
    # Backend mic test control
    # ------------------------------------------------------------------
    async def _start_mic_test(self):
        try:
            await self.backend.start_mic_test()
            self._mic_test_running = True
        except Exception as e:
            logger.warning("Mic test start failed: %s", e)
            self._mic_test_running = False
            self.level_label.text = 'No microphone detected'
            self.level_label.color = COLORS['red']

    async def _stop_mic_test(self):
        try:
            await self.backend.stop_mic_test()
        except Exception:
            pass
        self._mic_test_running = False

    def on_mic_test_level(self, level: float):
        gated = 0.0 if level < 0.015 else min(1.0, level)
        self._rms_history.append(gated)
        self._last_level_ts = time()

    # ------------------------------------------------------------------
    # UI update tick
    # ------------------------------------------------------------------
    def _tick(self, _dt):
        if self._last_level_ts and (time() - self._last_level_ts > 0.25):
            self._rms_history = deque([v * 0.82 for v in self._rms_history], maxlen=_TestWaveform.NUM_BARS)

        levels = [max(2, int(v * _TestWaveform.MAX_BAR_HEIGHT))
                  for v in self._rms_history]
        self.waveform.set_levels(levels)

        if not self._mic_test_running:
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
