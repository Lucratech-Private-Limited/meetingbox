"""Premium meeting detail screen for reports, decisions, and action items."""

from datetime import datetime
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from async_helper import run_async

from components.action_item import ActionItemWidget
from components.button import DangerButton, SecondaryButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, to_display_local
from screens.base_screen import BaseScreen


class MeetingDetailScreen(BaseScreen):
    """Executive-readable meeting report."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id = None
        self.meeting = None
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Meeting Detail',
            device_name='Meeting Report',
            back_button=True,
            on_back=self.go_back,
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        scroll = ScrollView(do_scroll_x=False)
        self.content = BoxLayout(
            orientation='vertical',
            spacing=sv(12),
            size_hint_y=None,
            padding=[sv(SPACING['screen_padding']), sv(14)],
        )
        self.content.bind(minimum_height=self.content.setter('height'))

        hero = BoxLayout(orientation='vertical', size_hint_y=None, height=sv(116), padding=[sv(18), sv(14)], spacing=sv(4))
        self.attach_card_bg(hero, radius=sv(26), color=(0.10, 0.15, 0.24, 0.88))
        self.title_label = Label(
            text='Loading…',
            font_size=sf(FONT_SIZES['large']),
            size_hint_y=None,
            height=sv(42),
            color=COLORS['white'],
            bold=True,
            halign='left',
            valign='middle',
            shorten=True,
            shorten_from='right',
        )
        self.title_label.bind(size=self.title_label.setter('text_size'))
        hero.add_widget(self.title_label)
        self.meta_label = Label(
            text='',
            font_size=sf(FONT_SIZES['small']),
            size_hint_y=None,
            height=sv(26),
            color=COLORS['gray_300'],
            halign='left',
            valign='middle',
        )
        self.meta_label.bind(size=self.meta_label.setter('text_size'))
        hero.add_widget(self.meta_label)
        self.content.add_widget(hero)

        self.summary_container = self._section_card('Executive report')
        self.content.add_widget(self.summary_container)
        self.actions_container = self._section_card('Actions')
        self.content.add_widget(self.actions_container)
        self.decisions_container = self._section_card('Decisions')
        self.content.add_widget(self.decisions_container)

        buttons = BoxLayout(orientation='horizontal', size_hint_y=None, height=sv(54), spacing=sv(10))
        back_btn = SecondaryButton(text='Back', font_size=sf(FONT_SIZES['small']))
        back_btn.bind(on_release=lambda *_: self.go_back())
        buttons.add_widget(back_btn)
        delete_btn = DangerButton(text='Delete Meeting', font_size=sf(FONT_SIZES['small']))
        delete_btn.bind(on_press=self._on_delete)
        buttons.add_widget(delete_btn)
        self.content.add_widget(buttons)

        scroll.add_widget(self.content)
        root.add_widget(scroll)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def _section_card(self, title):
        card = BoxLayout(orientation='vertical', size_hint_y=None, padding=[self.suv(16), self.suv(14)], spacing=self.suv(8))
        card._section_title = title
        self.attach_card_bg(card, radius=self.suv(24), color=(0.08, 0.11, 0.18, 0.76))
        return card

    def _heading(self, text):
        h = Label(
            text=text,
            font_size=self.suf(FONT_SIZES['medium']),
            size_hint_y=None,
            height=self.suv(28),
            color=COLORS['white'],
            bold=True,
            halign='left',
            valign='middle',
        )
        h.bind(size=h.setter('text_size'))
        return h

    def _body_text(self, text, color=None, fs=None):
        lbl = Label(
            text=text,
            font_size=fs or self.suf(FONT_SIZES['body']),
            size_hint_y=None,
            color=color or COLORS['gray_300'],
            halign='left',
            valign='top',
            line_height=1.18,
        )
        lbl.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
        lbl.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + self.suv(8)))
        return lbl

    def set_meeting_id(self, meeting_id: str):
        self.meeting_id = meeting_id

    def on_enter(self):
        if self.meeting_id:
            self._load_meeting()

    def _load_meeting(self):
        self.title_label.text = 'Loading meeting…'
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
        self.title_label.text = self.meeting.get('title') or 'Untitled meeting'
        start = datetime.fromisoformat(self.meeting['start_time'].replace('Z', '+00:00'))
        dur = self.meeting.get('duration', 0) // 60
        local_start = to_display_local(start)
        self.meta_label.text = f"{local_start.strftime('%b %d, %I:%M %p')} · {dur} min · Meeting memory"

        summary = self.meeting.get('summary', {}) or {}
        self._populate_summary(summary)
        self._populate_actions(summary.get('action_items', []) or [])
        self._populate_decisions(summary.get('decisions', []) or [])

    def _populate_summary(self, summary):
        c = self.summary_container
        c.clear_widgets()
        c.add_widget(self._heading('Executive report'))
        c.add_widget(self._body_text(summary.get('summary') or 'No report generated yet.'))
        c.height = sum(getattr(w, 'height', 0) for w in c.children) + self.suv(42)

    def _populate_actions(self, items):
        c = self.actions_container
        c.clear_widgets()
        c.add_widget(self._heading(f'Actions ({len(items)})'))
        if not items:
            c.add_widget(self._body_text('No action items found.', COLORS['gray_500'], self.suf(FONT_SIZES['small'])))
        else:
            for item in items:
                c.add_widget(ActionItemWidget(action_item=item))
        c.height = max(self.suv(86), sum(getattr(w, 'height', 0) for w in c.children) + self.suv(42))

    def _populate_decisions(self, decisions):
        c = self.decisions_container
        c.clear_widgets()
        c.add_widget(self._heading(f'Decisions ({len(decisions)})'))
        if not decisions:
            c.add_widget(self._body_text('No decisions captured yet.', COLORS['gray_500'], self.suf(FONT_SIZES['small'])))
        else:
            c.add_widget(self._body_text('\n'.join(f'• {d}' for d in decisions), COLORS['gray_300'], self.suf(FONT_SIZES['small'])))
        c.height = max(self.suv(86), sum(getattr(w, 'height', 0) for w in c.children) + self.suv(42))

    def _on_delete(self, _inst):
        async def _delete():
            try:
                await self.backend.delete_meeting(self.meeting_id)
                Clock.schedule_once(lambda _: self.goto('meetings'), 0)
            except Exception:
                pass
        run_async(_delete())
