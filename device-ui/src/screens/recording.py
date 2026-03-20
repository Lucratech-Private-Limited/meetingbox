"""
Recording Screen – Active recording interface (480 × 320)

PRD §5.7 – Timer, audio waveform, live captions, pause/stop.

Layout (top → bottom):
┌──────────────────────────────────────────────────┐
│ 🔴 RECORDING             Conference Room A     ⚙ │ 44 px
├──────────────────────────────────────────────────┤
│                    00:23:47                       │ 36 px
├──────────────────────────────────────────────────┤
│     ▂ ▄ ▆ █ ▇ ▅ ▃ ▂ ▁ ▃ ▅ ▇ █ ▆ ▄ ▂            │ 60 px
├──────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────┐   │
│ │ "…so I think we should focus on Q4…"       │   │ 70 px
│ └────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────┤
│    ┌──────────┐            ┌──────────┐          │ 60 px
│    │ ⏸ PAUSE │            │ ⏹ STOP │          │
├──────────────────────────────────────────────────┤
│ WiFi: ✓  Storage: 454GB free | …                 │ 20 px
└──────────────────────────────────────────────────┘
"""

import logging
import random
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from components.button import SecondaryButton, DangerButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, BORDER_RADIUS
from async_helper import run_async

logger = logging.getLogger(__name__)


class _WaveformWidget(Widget):
    """Simple vertical-bar audio waveform visualisation."""

    NUM_BARS = 18
    BAR_WIDTH = 14
    BAR_SPACING = 6

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (1, None))
        kwargs.setdefault('height', 60)
        super().__init__(**kwargs)
        self._levels = [2] * self.NUM_BARS
        self._active = False
        self.bind(pos=self._draw, size=self._draw)

    def set_active(self, active: bool):
        self._active = active

    def update_levels(self, levels=None):
        if levels:
            self._levels = levels
        elif self._active:
            self._levels = [random.randint(4, 55) for _ in range(self.NUM_BARS)]
        else:
            self._levels = [2] * self.NUM_BARS
        self._draw()

    def _draw(self, *_args):
        self.canvas.clear()
        total_w = self.NUM_BARS * (self.BAR_WIDTH + self.BAR_SPACING)
        start_x = self.x + (self.width - total_w) / 2
        base_y = self.y + 2

        with self.canvas:
            for i, h in enumerate(self._levels):
                ratio = h / 60.0
                r = 0.22 + ratio * (0.20 - 0.22)
                g = 0.55 + ratio * (0.78 - 0.55)
                b = 0.98 + ratio * (0.35 - 0.98)
                Color(r, g, b, 1)
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, base_y),
                    size=(self.BAR_WIDTH, max(2, h)),
                    radius=[3],
                )


class RecordingScreen(BaseScreen):
    """Active recording screen – PRD §5.7 / §5.8 (paused state inline)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.elapsed_seconds = 0
        self.timer_event = None
        self.waveform_event = None
        self._is_paused = False
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # 1. Status bar
        self.status_bar = StatusBar(
            status_text='RECORDING',
            status_color=COLORS['red'],
            device_name='MeetingBox',
            pulsing=True,
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        # 2. Timer
        self.timer_label = Label(
            text='00:00',
            font_size=28,
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=36,
        )
        root.add_widget(self.timer_label)

        # 3. Waveform
        self.waveform = _WaveformWidget()
        root.add_widget(self.waveform)

        # 4. Live captions card
        caption_card = BoxLayout(
            orientation='vertical',
            size_hint=(1, None), height=70,
            padding=[SPACING['screen_padding'], 4],
        )
        with caption_card.canvas.before:
            Color(*COLORS['surface'])
            _cb = RoundedRectangle(
                pos=caption_card.pos, size=caption_card.size,
                radius=[BORDER_RADIUS])
        caption_card.bind(
            pos=lambda w, v: setattr(_cb, 'pos', w.pos),
            size=lambda w, v: setattr(_cb, 'size', w.size),
        )
        self.caption_label = Label(
            text='Waiting for speech…',
            font_size=14,
            color=COLORS['white'],
            halign='left',
            valign='top',
            line_height=1.4,
        )
        self.caption_label.bind(size=self.caption_label.setter('text_size'))
        caption_card.add_widget(self.caption_label)
        root.add_widget(caption_card)

        # 5. Buttons row
        btn_row = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None), height=60,
            padding=[SPACING['screen_padding'], 4],
            spacing=SPACING['button_spacing'] * 3,
        )

        self.pause_btn = SecondaryButton(
            text='⏸  PAUSE',
            font_size=FONT_SIZES['medium'],
            size_hint=(0.5, 1),
        )
        self.pause_btn.bind(on_press=self._on_pause)
        btn_row.add_widget(self.pause_btn)

        self.stop_btn = DangerButton(
            text='⏹  STOP',
            font_size=FONT_SIZES['medium'],
            size_hint=(0.5, 1),
        )
        self.stop_btn.bind(on_press=self._on_stop)
        btn_row.add_widget(self.stop_btn)

        root.add_widget(btn_row)

        # 6. Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_enter(self):
        self._is_paused = False
        self.elapsed_seconds = 0
        self.caption_label.text = 'Listening…'
        self.timer_label.text = '00:00'
        self.waveform.set_active(True)

        self.timer_event = Clock.schedule_interval(self._tick_timer, 1.0)
        self.waveform_event = Clock.schedule_interval(
            lambda _dt: self.waveform.update_levels(), 0.1)

        self.status_bar.device_label.text = getattr(self.app, 'device_name', 'MeetingBox')
        self.status_bar.status_text = 'RECORDING'
        self.status_bar.status_color = COLORS['red']
        self.status_bar.start_pulse()
        self.pause_btn.text = '⏸  PAUSE'

        self._apply_privacy_mode()
        self._load_footer_data()

    def on_leave(self):
        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None
        if self.waveform_event:
            self.waveform_event.cancel()
            self.waveform_event = None

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------
    def _tick_timer(self, _dt):
        self.elapsed_seconds += 1
        h = self.elapsed_seconds // 3600
        m = (self.elapsed_seconds % 3600) // 60
        s = self.elapsed_seconds % 60
        if h > 0:
            self.timer_label.text = f'{h:02d}:{m:02d}:{s:02d}'
        else:
            self.timer_label.text = f'{m:02d}:{s:02d}'

    # ------------------------------------------------------------------
    # Pause / Resume
    # ------------------------------------------------------------------
    def _on_pause(self, _inst):
        if self._is_paused:
            self.app.resume_recording()
        else:
            self.app.pause_recording()

    def on_paused(self):
        self._is_paused = True
        self.pause_btn.text = '▶  RESUME'
        self.status_bar.status_text = 'PAUSED'
        self.status_bar.status_color = COLORS['yellow']
        self.status_bar.stop_pulse()

        if self.timer_event:
            self.timer_event.cancel()
            self.timer_event = None

        self.waveform.set_active(False)
        self.waveform.update_levels()
        self.caption_label.text = 'Recording paused'

    def on_resumed(self):
        self._is_paused = False
        self.pause_btn.text = '⏸  PAUSE'
        self.status_bar.status_text = 'RECORDING'
        self.status_bar.status_color = COLORS['red']
        self.status_bar.start_pulse()

        self.timer_event = Clock.schedule_interval(self._tick_timer, 1.0)
        self.waveform.set_active(True)

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------
    def _on_stop(self, _inst):
        logger.info("Stop button pressed (duration: %s)", self.timer_label.text)
        self.app.stop_recording()

    # ------------------------------------------------------------------
    # Audio segment counter (shown while recording, no live transcription)
    # ------------------------------------------------------------------
    def on_audio_segment(self, segment_num: int):
        if self._is_paused:
            return
        count = segment_num + 1
        self.caption_label.text = f'Listening… ({count} segment{"s" if count != 1 else ""} captured)'

    # ------------------------------------------------------------------
    # Privacy
    # ------------------------------------------------------------------
    def _apply_privacy_mode(self):
        privacy = getattr(self.app, 'privacy_mode', False)
        if privacy:
            self.status_bar.status_text = 'RECORDING (Privacy)'
            self.caption_label.text = (
                'Privacy Mode: Processing locally only\n'
                'AI summaries disabled')

    # ------------------------------------------------------------------
    def _load_footer_data(self):
        async def _fetch():
            try:
                info = await self.backend.get_system_info()
                free_gb = (info['storage_total'] - info['storage_used']) / (1024 ** 3)
                wifi_ok = bool(info.get('wifi_ssid'))
                privacy = getattr(self.app, 'privacy_mode', False)
                Clock.schedule_once(
                    lambda _dt: self.update_footer(
                        wifi_ok=wifi_ok, free_gb=free_gb,
                        privacy_mode=privacy), 0)
            except Exception:
                pass
        run_async(_fetch())
