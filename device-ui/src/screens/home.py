"""
Home Screen – READY STATE (480 × 320)

PRD §5.6 – Primary interface when device is idle.

Layout (top → bottom):
┌──────────────────────────────────────────────────┐
│ ● READY               Conference Room A        ⚙ │  44 px
├──────────────────────────────────────────────────┤
│ Last: Q4 Planning · 2h ago · 45min               │  36 px
├──────────────────────────────────────────────────┤
│                                                   │
│              START RECORDING  ⏺                  │ 140 px
│                                                   │
├──────────────────────────────────────────────────┤
│ (spacer)                                          │  ~80 px
├──────────────────────────────────────────────────┤
│ WiFi: ✓  Storage: 454GB free | Actions: …        │  20 px
└──────────────────────────────────────────────────┘
"""

from datetime import datetime, timedelta
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle, Rectangle
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.button import PrimaryButton
from components.status_bar import StatusBar
from config import (COLORS, FONT_SIZES, SPACING, BORDER_RADIUS)


class HomeScreen(BaseScreen):
    """Home / Ready screen — premium dark theme."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.last_meeting = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # 1. Status bar (44 px)
        self.status_bar = StatusBar(
            status_text='READY',
            status_color=COLORS['green'],
            device_name='MeetingBox',
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        # 2. Last-meeting info band (36 px)
        card = BoxLayout(
            orientation='vertical',
            size_hint=(1, None),
            height=36,
            padding=[SPACING['screen_padding'], 4],
        )
        with card.canvas.before:
            Color(*COLORS['surface'])
            self._card_bg = Rectangle(pos=card.pos, size=card.size)
        card.bind(
            pos=lambda w, v: setattr(self._card_bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._card_bg, 'size', w.size),
        )
        self.last_meeting_label = Label(
            text='No meetings yet — Press start to begin',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_400'],
            halign='left',
            valign='middle',
        )
        self.last_meeting_label.bind(
            size=self.last_meeting_label.setter('text_size'))
        card.add_widget(self.last_meeting_label)
        root.add_widget(card)

        # 3. START RECORDING button (140 px, full width padded)
        btn_wrap = BoxLayout(
            orientation='vertical',
            size_hint=(1, None),
            height=160,
            padding=[SPACING['screen_padding'], 10],
        )

        self.start_btn = PrimaryButton(
            text='START RECORDING\n⏺',
            font_size=FONT_SIZES['large'],
            halign='center',
        )
        self.start_btn.bind(on_press=self._on_start_recording)
        btn_wrap.add_widget(self.start_btn)
        root.add_widget(btn_wrap)

        # 4. Spacer
        root.add_widget(Widget())

        # 6. Footer (20 px)
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_enter(self):
        self.status_bar.device_label.text = getattr(self.app, 'device_name', 'MeetingBox')
        self._load_last_meeting()
        self._load_system_status()
        self._apply_privacy_mode()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_start_recording(self, _inst):
        self.app.start_recording()

    # ------------------------------------------------------------------
    # Privacy mode
    # ------------------------------------------------------------------
    def _apply_privacy_mode(self):
        privacy = getattr(self.app, 'privacy_mode', False)
        if privacy:
            self.status_bar.status_text = 'READY (Privacy)'
            self.last_meeting_label.text = (
                'Privacy Mode: AI features disabled\n'
                'Transcription available · Cloud AI offline')
            self.start_btn.text = 'START RECORDING\n⏺\n(Local Only)'
        else:
            self.status_bar.status_text = 'READY'
            self.start_btn.text = 'START RECORDING\n⏺'
            self.last_meeting_label.text = 'No meetings yet — Press start to begin'

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _load_last_meeting(self):
        if getattr(self.app, 'privacy_mode', False):
            return

        async def _fetch():
            try:
                meetings = await self.backend.get_meetings(limit=1)
                if meetings:
                    m = meetings[0]
                    self.last_meeting = m
                    start = datetime.fromisoformat(
                        m['start_time'].replace('Z', '+00:00'))
                    delta = datetime.now(start.tzinfo) - start
                    if delta < timedelta(hours=1):
                        ago = f"{int(delta.total_seconds() / 60)}m ago"
                    elif delta < timedelta(days=1):
                        ago = f"{int(delta.total_seconds() / 3600)}h ago"
                    else:
                        ago = f"{delta.days}d ago"
                    dur = m['duration'] // 60 if m.get('duration') else 0
                    text = f"Last: {m['title']}\n{ago} · {dur}min"
                    Clock.schedule_once(
                        lambda _dt: setattr(
                            self.last_meeting_label, 'text', text), 0)
            except Exception:
                pass
        run_async(_fetch())

    def _load_system_status(self):
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
