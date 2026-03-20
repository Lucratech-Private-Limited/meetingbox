"""
Meetings List Screen – Dark themed (480 × 320)

Scrollable list of past meetings.
"""

from datetime import datetime, timedelta
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from components.meeting_card import MeetingCard
from config import COLORS, FONT_SIZES, SPACING, MEETINGS_LIST_LIMIT


class MeetingsScreen(BaseScreen):
    """Meetings list – dark theme."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meetings = []
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Recent Meetings',
            device_name='Recent Meetings',
            back_button=True,
            on_back=self.go_back,
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        scroll = ScrollView(do_scroll_x=False)
        self.meetings_container = GridLayout(
            cols=1,
            spacing=SPACING['list_item_spacing'],
            size_hint_y=None,
            padding=SPACING['screen_padding'],
        )
        self.meetings_container.bind(
            minimum_height=self.meetings_container.setter('height'))
        scroll.add_widget(self.meetings_container)
        root.add_widget(scroll)

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        self._load_meetings()

    def _load_meetings(self):
        async def _load():
            try:
                meetings = await self.backend.get_meetings(limit=MEETINGS_LIST_LIMIT)
                self.meetings = meetings
                Clock.schedule_once(lambda _: self._populate(), 0)
            except Exception:
                pass
        run_async(_load())

    def _populate(self):
        self.meetings_container.clear_widgets()
        if not self.meetings:
            empty = Label(
                text='No meetings yet',
                font_size=FONT_SIZES['medium'],
                color=COLORS['gray_500'],
                halign='center',
            )
            self.meetings_container.add_widget(empty)
            return
        for m in self.meetings:
            card = MeetingCard(meeting=m)
            card.bind(on_press=self._on_meeting)
            self.meetings_container.add_widget(card)

    def _on_meeting(self, instance):
        mid = instance.meeting['id']
        detail = self.app.screen_manager.get_screen('meeting_detail')
        detail.set_meeting_id(mid)
        self.goto('meeting_detail', transition='slide_left')
