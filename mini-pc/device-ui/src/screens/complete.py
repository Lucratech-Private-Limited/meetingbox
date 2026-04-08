"""
Complete Screen – Meeting saved confirmation (480 × 320)

PRD §5.10 – Checkmark animation, quick stats, auto-return to Home.
Duration: 5 seconds (or tap to skip).
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.animation import Animation
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, AUTO_RETURN_DELAY


class CompleteScreen(BaseScreen):
    """Complete / Meeting Saved screen – PRD §5.10."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id = None
        self._auto_event = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Status bar
        self.status_bar = StatusBar(
            status_text='COMPLETE',
            status_color=COLORS['green'],
            device_name='MeetingBox',
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.08)))

        # Checkmark
        self.check_label = Label(
            text='✓',
            font_size=60,
            bold=True,
            color=COLORS['green'],
            halign='center',
            size_hint=(1, None), height=70,
            opacity=0,
        )
        root.add_widget(self.check_label)

        # Meeting Saved!
        self.title_label = Label(
            text='Meeting Saved!',
            font_size=FONT_SIZES['large'],
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=30,
        )
        root.add_widget(self.title_label)

        # Meeting info
        self.info_label = Label(
            text='',
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=24,
        )
        root.add_widget(self.info_label)

        root.add_widget(Widget(size_hint=(1, None), height=8))

        # Quick stats
        self.stats_label = Label(
            text='',
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['gray_500'],
            halign='center',
            valign='top',
            size_hint=(1, None), height=60,
        )
        self.stats_label.bind(size=self.stats_label.setter('text_size'))
        root.add_widget(self.stats_label)

        root.add_widget(Widget())

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def set_meeting_id(self, meeting_id: str):
        self.meeting_id = meeting_id
        self._load_meeting_info()

    def _load_meeting_info(self):
        if not self.meeting_id:
            return

        async def _load():
            try:
                meeting = await self.backend.get_meeting_detail(self.meeting_id)
                title = meeting.get('title', 'Untitled')
                dur = meeting.get('duration', 0) // 60
                summary = meeting.get('summary', {})
                ac = len(summary.get('action_items', []))
                dc = len(summary.get('decisions', []))

                privacy = getattr(self.app, 'privacy_mode', False)
                if privacy:
                    stats = '• Transcript ready\n• Local storage only\n• AI features disabled'
                else:
                    stats_parts = []
                    if ac:
                        stats_parts.append(f'• {ac} action item{"s" if ac != 1 else ""}')
                    if dc:
                        stats_parts.append(f'• {dc} decision{"s" if dc != 1 else ""} made')
                    stats_parts.append('• Meeting report ready')
                    stats = '\n'.join(stats_parts)

                def _update(_dt):
                    self.info_label.text = f'{title} · {dur} minutes'
                    self.stats_label.text = stats

                Clock.schedule_once(_update, 0)
            except Exception:
                pass

        run_async(_load())

    # ------------------------------------------------------------------
    def on_enter(self):
        self.status_bar.device_label.text = getattr(self.app, 'device_name', 'MeetingBox')

        # Checkmark spring-in
        self.check_label.opacity = 0
        anim = Animation(opacity=1, duration=0.5)
        anim.start(self.check_label)

        # Privacy
        privacy = getattr(self.app, 'privacy_mode', False)
        if privacy:
            self.status_bar.status_text = 'COMPLETE (Privacy)'

        # Auto-return
        self._auto_event = Clock.schedule_once(self._go_home, AUTO_RETURN_DELAY)

    def on_leave(self):
        if self._auto_event:
            self._auto_event.cancel()
            self._auto_event = None

    def on_touch_down(self, touch):
        # Tap anywhere to skip back to home
        if self.collide_point(*touch.pos):
            self._go_home(0)
            return True
        return super().on_touch_down(touch)

    def _go_home(self, _dt):
        if self._auto_event:
            self._auto_event.cancel()
            self._auto_event = None
        self.goto('home', transition='fade')
