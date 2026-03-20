"""
Summary Review Screen -- Post-recording summary & actions (480 x 320)

Two tabs: Summary | Actions
- Summary tab: shows the AI-generated summary text, decisions, topics
- Actions tab: shows action items with checkboxes and Execute Selected button
"""

import logging
from functools import partial

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.checkbox import CheckBox
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from components.button import PrimaryButton, SecondaryButton
from components.status_bar import StatusBar
from components.modal_dialog import ModalDialog
from config import COLORS, FONT_SIZES, SPACING, DASHBOARD_URL
from async_helper import run_async

logger = logging.getLogger(__name__)


class SummaryReviewScreen(BaseScreen):
    """Post-recording screen with Summary and Actions tabs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id = None
        self._summary_data = {}
        self._actions_data = []
        self._selected_actions = set()
        self._current_tab = 'summary'
        self._build_ui()

    def _build_ui(self):
        self.root_layout = BoxLayout(orientation='vertical')
        self.make_dark_bg(self.root_layout)

        self.status_bar = StatusBar(
            status_text='SUMMARY',
            status_color=COLORS['green'],
            device_name='MeetingBox AI',
            show_settings=False,
        )
        self.root_layout.add_widget(self.status_bar)

        # Tab bar
        tab_bar = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=40,
            padding=[SPACING['screen_padding'], 4],
            spacing=8,
        )

        self.summary_tab_btn = SecondaryButton(
            text='Summary',
            font_size=FONT_SIZES['body'],
            size_hint=(0.5, 1),
        )
        self.summary_tab_btn.bind(on_press=lambda _: self._switch_tab('summary'))
        tab_bar.add_widget(self.summary_tab_btn)

        self.actions_tab_btn = SecondaryButton(
            text='Actions',
            font_size=FONT_SIZES['body'],
            size_hint=(0.5, 1),
        )
        self.actions_tab_btn.bind(on_press=lambda _: self._switch_tab('actions'))
        tab_bar.add_widget(self.actions_tab_btn)

        self.root_layout.add_widget(tab_bar)

        # Content area (swapped depending on tab)
        self.content_area = BoxLayout(orientation='vertical', size_hint=(1, 1))
        self.root_layout.add_widget(self.content_area)

        # Bottom buttons
        btn_row = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=50,
            padding=[SPACING['screen_padding'], 4],
            spacing=8,
        )

        self.close_btn = SecondaryButton(
            text='Close',
            font_size=FONT_SIZES['body'],
            size_hint=(0.4, 1),
        )
        self.close_btn.bind(on_press=self._on_close)
        btn_row.add_widget(self.close_btn)

        self.execute_btn = PrimaryButton(
            text='Execute Selected',
            font_size=FONT_SIZES['body'],
            size_hint=(0.6, 1),
        )
        self.execute_btn.bind(on_press=self._on_execute)
        btn_row.add_widget(self.execute_btn)

        self.root_layout.add_widget(btn_row)
        self.root_layout.add_widget(Widget(size_hint=(1, None), height=4))

        self.add_widget(self.root_layout)

    def set_meeting_data(self, meeting_id: str, summary_data: dict):
        self.meeting_id = meeting_id
        self._summary_data = summary_data or {}
        self._actions_data = []
        self._selected_actions = set()
        self._load_actions()

    def _load_actions(self):
        if not self.meeting_id:
            return

        async def _fetch():
            try:
                actions = await self.backend.get_actions(self.meeting_id)
                def _update(_dt):
                    self._actions_data = actions
                    if self._current_tab == 'actions':
                        self._render_actions_tab()
                Clock.schedule_once(_update, 0)
            except Exception as e:
                logger.error(f"Failed to load actions: {e}")

        run_async(_fetch())

    def _switch_tab(self, tab: str):
        self._current_tab = tab
        self._render_tab()

    def _render_tab(self):
        self.content_area.clear_widgets()
        if self._current_tab == 'summary':
            self._render_summary_tab()
            self.execute_btn.opacity = 0
            self.execute_btn.disabled = True
        else:
            self._render_actions_tab()
            self.execute_btn.opacity = 1
            self.execute_btn.disabled = False

    def _render_summary_tab(self):
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            padding=[SPACING['screen_padding'], 8],
            spacing=6,
        )
        content.bind(minimum_height=content.setter('height'))

        summary_text = self._summary_data.get('summary', 'No summary available.')
        lbl = Label(
            text=summary_text,
            font_size=FONT_SIZES['small'] + 1,
            color=COLORS['white'],
            halign='left',
            valign='top',
            size_hint_y=None,
            markup=True,
        )
        lbl.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
        lbl.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + 8))
        content.add_widget(lbl)

        decisions = self._summary_data.get('decisions', [])
        if decisions:
            hdr = Label(
                text='Decisions',
                font_size=FONT_SIZES['body'],
                bold=True,
                color=COLORS['blue'],
                halign='left',
                size_hint_y=None,
                height=24,
            )
            hdr.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
            content.add_widget(hdr)
            for d in decisions:
                dl = Label(
                    text=f"  - {d}",
                    font_size=FONT_SIZES['small'],
                    color=COLORS['gray_300'],
                    halign='left',
                    valign='top',
                    size_hint_y=None,
                )
                dl.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
                dl.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + 4))
                content.add_widget(dl)

        topics = self._summary_data.get('topics', [])
        if topics:
            topics_str = '  '.join(str(t) for t in topics)
            tl = Label(
                text=topics_str,
                font_size=FONT_SIZES['small'],
                color=COLORS['gray_500'],
                halign='left',
                size_hint_y=None,
                height=20,
            )
            tl.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
            content.add_widget(tl)

        scroll.add_widget(content)
        self.content_area.add_widget(scroll)

    def _render_actions_tab(self):
        self.content_area.clear_widgets()
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            padding=[SPACING['screen_padding'], 8],
            spacing=6,
        )
        content.bind(minimum_height=content.setter('height'))

        if not self._actions_data:
            empty = Label(
                text='No action items found.',
                font_size=FONT_SIZES['body'],
                color=COLORS['gray_500'],
                halign='center',
                size_hint_y=None,
                height=40,
            )
            content.add_widget(empty)
        else:
            for action in self._actions_data:
                row = BoxLayout(
                    orientation='horizontal',
                    size_hint_y=None,
                    height=36,
                    spacing=8,
                )

                cb = CheckBox(
                    size_hint=(None, None),
                    size=(28, 28),
                    active=action['id'] in self._selected_actions,
                )
                cb.bind(active=partial(self._on_action_toggle, action['id']))
                row.add_widget(cb)

                title = action.get('title', 'Untitled action')
                assignee = action.get('assignee', '')
                status = action.get('status', 'pending')
                color = COLORS['white'] if status == 'pending' else COLORS['gray_500']

                text = title
                if assignee:
                    text += f"  ({assignee})"
                if status != 'pending':
                    text += f"  [{status}]"

                al = Label(
                    text=text,
                    font_size=FONT_SIZES['small'] + 1,
                    color=color,
                    halign='left',
                    valign='middle',
                    size_hint=(1, 1),
                )
                al.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
                row.add_widget(al)

                content.add_widget(row)

        scroll.add_widget(content)
        self.content_area.add_widget(scroll)

    def _on_action_toggle(self, action_id, checkbox, value):
        if value:
            self._selected_actions.add(action_id)
        else:
            self._selected_actions.discard(action_id)

    def _on_close(self, _inst):
        self.goto('home', transition='fade')

    def _on_execute(self, _inst):
        if not self._selected_actions:
            return

        async def _run():
            for action_id in list(self._selected_actions):
                try:
                    await self.backend.execute_action(action_id)
                except Exception as e:
                    logger.error(f"Failed to execute action {action_id}: {e}")

        run_async(_run())

        self._selected_actions.clear()
        dialog = ModalDialog(
            title='Actions Queued',
            message=(
                f'Your actions are being executed.\n'
                f'Check {DASHBOARD_URL} for details.'
            ),
            confirm_text='OK',
            cancel_text='',
            on_confirm=lambda: self.goto('home', transition='fade'),
        )
        self.add_widget(dialog)

    def on_enter(self):
        self._current_tab = 'summary'
        self._render_tab()
