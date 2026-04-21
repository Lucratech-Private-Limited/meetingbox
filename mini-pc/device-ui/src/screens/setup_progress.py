"""
Setup In Progress Screen

Waiting state while user completes web-based setup.
Exit conditions (whichever fires first):
  1. Backend sends setup_complete WebSocket event
  2. .setup_complete marker file appears on disk (written when setup finishes via dashboard / API)
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES


class SetupProgressScreen(BaseScreen):
    """Setup in progress – waiting for web setup completion."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dot_index = 0
        self._dot_event = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        root.add_widget(Widget(size_hint=(1, 0.25)))

        msg1 = Label(
            text='Setting up your MeetingBox...',
            font_size=self.suf(FONT_SIZES['medium']),
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        msg1.bind(size=msg1.setter('text_size'))
        root.add_widget(msg1)

        self.status_label = Label(
            text='Waiting for WiFi configuration',
            font_size=self.suf(FONT_SIZES['body']),
            color=COLORS['gray_400'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        self.status_label.bind(size=self.status_label.setter('text_size'))
        root.add_widget(self.status_label)

        root.add_widget(Widget(size_hint=(1, 0.15)))

        self.dots_label = Label(
            text='●  ○  ○',
            font_size=self.suf(FONT_SIZES['large']),
            color=COLORS['gray_500'],
            halign='center',
            size_hint=(1, None), height=30,
        )
        root.add_widget(self.dots_label)

        root.add_widget(Widget(size_hint=(1, 0.35)))
        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        self._dot_index = 0
        self._dot_event = Clock.schedule_interval(self._animate_dots, 0.5)

    def on_leave(self):
        if self._dot_event:
            self._dot_event.cancel()
            self._dot_event = None

    def _animate_dots(self, _dt):
        self._dot_index = (self._dot_index + 1) % 3
        dots = ['○', '○', '○']
        dots[self._dot_index] = '●'
        self.dots_label.text = '  '.join(dots)
