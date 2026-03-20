"""
Meeting Detail Screen – Dark themed (480 × 320)

Scrollable detail view with summary, action items, decisions.
"""

from datetime import datetime
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.gridlayout import GridLayout
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from components.button import SecondaryButton, DangerButton
from components.action_item import ActionItemWidget
from config import COLORS, FONT_SIZES, SPACING


class MeetingDetailScreen(BaseScreen):
    """Meeting detail – dark theme."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id = None
        self.meeting = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Meeting Detail',
            device_name='Meeting Detail',
            back_button=True,
            on_back=self.go_back,
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        scroll = ScrollView(do_scroll_x=False)
        self.content = BoxLayout(
            orientation='vertical',
            spacing=SPACING['section_spacing'],
            size_hint_y=None,
            padding=SPACING['screen_padding'],
        )
        self.content.bind(minimum_height=self.content.setter('height'))

        self.title_label = Label(
            text='Loading…',
            font_size=FONT_SIZES['large'],
            size_hint_y=None, height=28,
            color=COLORS['white'],
            bold=True, halign='left',
        )
        self.title_label.bind(size=self.title_label.setter('text_size'))
        self.content.add_widget(self.title_label)

        self.meta_label = Label(
            text='',
            font_size=FONT_SIZES['tiny'],
            size_hint_y=None, height=16,
            color=COLORS['gray_500'], halign='left',
        )
        self.meta_label.bind(size=self.meta_label.setter('text_size'))
        self.content.add_widget(self.meta_label)

        self.summary_container = BoxLayout(
            orientation='vertical', size_hint_y=None,
            spacing=SPACING['button_spacing'])
        self.content.add_widget(self.summary_container)

        self.actions_container = BoxLayout(
            orientation='vertical', size_hint_y=None,
            spacing=SPACING['button_spacing'])
        self.content.add_widget(self.actions_container)

        self.decisions_container = BoxLayout(
            orientation='vertical', size_hint_y=None,
            spacing=SPACING['button_spacing'])
        self.content.add_widget(self.decisions_container)

        buttons = BoxLayout(
            orientation='horizontal',
            size_hint_y=None, height=40,
            spacing=SPACING['button_spacing'],
        )
        delete_btn = DangerButton(text='DELETE', font_size=FONT_SIZES['small'])
        delete_btn.bind(on_press=self._on_delete)
        buttons.add_widget(delete_btn)
        self.content.add_widget(buttons)

        scroll.add_widget(self.content)
        root.add_widget(scroll)

        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def set_meeting_id(self, meeting_id: str):
        self.meeting_id = meeting_id

    def on_enter(self):
        if self.meeting_id:
            self._load_meeting()

    def _load_meeting(self):
        async def _load():
            try:
                meeting = await self.backend.get_meeting_detail(self.meeting_id)
                self.meeting = meeting
                Clock.schedule_once(lambda _: self._populate(), 0)
            except Exception:
                Clock.schedule_once(lambda _: self.go_back(), 0)
        run_async(_load())

    def _populate(self):
        if not self.meeting:
            return
        self.title_label.text = self.meeting['title']
        start = datetime.fromisoformat(
            self.meeting['start_time'].replace('Z', '+00:00'))
        dur = self.meeting.get('duration', 0) // 60
        self.meta_label.text = f"{start.strftime('%b %d, %I:%M %p')} · {dur}min"

        summary = self.meeting.get('summary', {})
        self._populate_summary(summary)
        self._populate_actions(summary.get('action_items', []))
        self._populate_decisions(summary.get('decisions', []))

    def _populate_summary(self, summary):
        self.summary_container.clear_widgets()
        h = Label(text='Summary', font_size=FONT_SIZES['medium'],
                  size_hint_y=None, height=20, color=COLORS['white'],
                  bold=True, halign='left')
        h.bind(size=h.setter('text_size'))
        self.summary_container.add_widget(h)
        t = Label(text=summary.get('summary', 'No summary'),
                  font_size=FONT_SIZES['small'], size_hint_y=None,
                  color=COLORS['gray_400'], halign='left', valign='top')
        t.bind(texture_size=t.setter('size'))
        t.bind(size=t.setter('text_size'))
        self.summary_container.add_widget(t)
        self.summary_container.height = 20 + t.height + SPACING['button_spacing']

    def _populate_actions(self, items):
        self.actions_container.clear_widgets()
        if not items:
            return
        h = Label(text=f'Actions ({len(items)})',
                  font_size=FONT_SIZES['medium'],
                  size_hint_y=None, height=20, color=COLORS['white'],
                  bold=True, halign='left')
        h.bind(size=h.setter('text_size'))
        self.actions_container.add_widget(h)
        total = 20
        for item in items:
            w = ActionItemWidget(action_item=item)
            self.actions_container.add_widget(w)
            total += w.height + SPACING['button_spacing']
        self.actions_container.height = total

    def _populate_decisions(self, decisions):
        self.decisions_container.clear_widgets()
        if not decisions:
            return
        h = Label(text=f'Decisions ({len(decisions)})',
                  font_size=FONT_SIZES['medium'],
                  size_hint_y=None, height=20, color=COLORS['white'],
                  bold=True, halign='left')
        h.bind(size=h.setter('text_size'))
        self.decisions_container.add_widget(h)
        total = 20
        for d in decisions:
            dl = Label(text=f'• {d}', font_size=FONT_SIZES['small'],
                       size_hint_y=None, color=COLORS['gray_400'],
                       halign='left', valign='top')
            dl.bind(texture_size=dl.setter('size'))
            dl.bind(size=dl.setter('text_size'))
            self.decisions_container.add_widget(dl)
            total += dl.height + SPACING['button_spacing']
        self.decisions_container.height = total

    def _on_delete(self, _inst):
        async def _delete():
            try:
                await self.backend.delete_meeting(self.meeting_id)
                Clock.schedule_once(lambda _: self.goto('meetings'), 0)
            except Exception:
                pass
        run_async(_delete())
