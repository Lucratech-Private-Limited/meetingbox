"""Premium meeting detail screen for reports, decisions, and action items."""

from datetime import datetime
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from async_helper import run_async

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
        self._agentic_actions = []
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

        scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            scroll_type=['bars', 'content'],
            bar_width=sv(5),
        )
        self.scroll = scroll
        self.content = BoxLayout(
            orientation='vertical',
            spacing=sv(14),
            size_hint_y=None,
            padding=[sv(SPACING['screen_padding']), sv(10), sv(SPACING['screen_padding']), sv(14)],
        )
        self.content.bind(minimum_height=self.content.setter('height'))

        hero = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            height=sv(108),
            padding=[sv(20), sv(14), sv(20), sv(12)],
            spacing=sv(4),
        )
        self.attach_card_bg(hero, radius=sv(22), color=(0.02, 0.08, 0.22, 0.92))
        self.title_label = Label(
            text='Loading…',
            font_size=sf(FONT_SIZES['large']),
            size_hint_y=None,
            height=sv(46),
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

        self.summary_container = self._section_card()
        self.content.add_widget(self.summary_container)
        self.questions_container = self._section_card()
        self.content.add_widget(self.questions_container)
        self.risks_container = self._section_card()
        self.content.add_widget(self.risks_container)
        self.actions_container = self._section_card()
        self.content.add_widget(self.actions_container)
        self.decisions_container = self._section_card()
        self.content.add_widget(self.decisions_container)

        buttons = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=sv(68),
            spacing=sv(10),
            padding=[sv(SPACING['screen_padding']), sv(6), sv(SPACING['screen_padding']), sv(8)],
        )
        self.attach_card_bg(buttons, radius=sv(18), color=(0.01, 0.04, 0.12, 0.90))
        back_btn = SecondaryButton(text='Back', font_size=sf(FONT_SIZES['small']))
        back_btn.bind(on_release=lambda *_: self.go_back())
        buttons.add_widget(back_btn)
        delete_btn = DangerButton(text='Delete Meeting', font_size=sf(FONT_SIZES['small']))
        delete_btn.bind(on_press=self._on_delete)
        buttons.add_widget(delete_btn)
        self.button_bar = buttons

        scroll.add_widget(self.content)
        root.add_widget(scroll)
        root.add_widget(buttons)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def _section_card(self):
        card = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            padding=[self.suv(18), self.suv(14), self.suv(18), self.suv(14)],
            spacing=self.suv(8),
        )
        self.attach_card_bg(card, radius=self.suv(20), color=(0.02, 0.06, 0.16, 0.86))
        card._min_height = self.suv(86)
        card.bind(minimum_height=self._sync_card_height)
        return card

    def _sync_card_height(self, card, _value=None):
        if getattr(card, '_hidden', False):
            card.height = 0
            return
        card.height = max(getattr(card, '_min_height', self.suv(86)), card.minimum_height)

    def _heading(self, text):
        row = BoxLayout(orientation='horizontal', size_hint_y=None, height=self.suv(30), spacing=self.suv(8))
        dot = Label(
            text='◆',
            font_size=self.suf(FONT_SIZES['small']),
            size_hint=(None, 1),
            width=self.suv(22),
            color=COLORS['blue'],
            halign='center',
            valign='middle',
        )
        dot.bind(size=dot.setter('text_size'))
        row.add_widget(dot)
        h = Label(
            text=text,
            font_size=self.suf(FONT_SIZES['body']),
            size_hint_y=None,
            height=self.suv(30),
            color=COLORS['white'],
            bold=True,
            halign='left',
            valign='middle',
        )
        h.bind(size=h.setter('text_size'))
        row.add_widget(h)
        return row

    def _body_text(self, text, color=None, fs=None):
        lbl = Label(
            text=text,
            font_size=fs or self.suf(FONT_SIZES['body']),
            size_hint_y=None,
            color=color or COLORS['gray_300'],
            halign='left',
            valign='top',
            line_height=1.22,
        )
        lbl.bind(width=lambda w, val: setattr(w, 'text_size', (max(1, val - self.suv(4)), None)))
        lbl.bind(texture_size=lambda w, ts: setattr(w, 'height', max(self.suv(26), ts[1] + self.suv(10))))
        return lbl

    def _list_row(self, text, meta='', trailing=None):
        row = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            spacing=self.suv(10),
            padding=[self.suv(4), self.suv(5), self.suv(4), self.suv(5)],
        )
        row.bind(minimum_height=lambda w, h: setattr(w, 'height', max(self.suv(38), h)))
        row.add_widget(self._bullet())
        body = BoxLayout(orientation='vertical', size_hint_y=None, spacing=self.suv(2))
        body.bind(minimum_height=lambda w, h: setattr(w, 'height', max(self.suv(30), h)))
        body.add_widget(self._body_text(str(text), COLORS['white'], self.suf(FONT_SIZES['small'])))
        if meta:
            body.add_widget(self._body_text(str(meta), COLORS['gray_400'], self.suf(FONT_SIZES['tiny'])))
        row.add_widget(body)
        if trailing is not None:
            row.add_widget(trailing)
        return row

    def _bullet(self):
        w = Widget(size_hint=(None, None), size=(self.suv(10), self.suv(10)))
        with w.canvas:
            Color(*COLORS['blue'])
            dot = RoundedRectangle(pos=w.pos, size=w.size, radius=[self.suv(8)])
        def _sync(widget, *_):
            size = min(widget.width, widget.height)
            dot.pos = (widget.x, widget.y + max(0, (widget.height - size) / 2))
            dot.size = (size, size)
            dot.radius = [size / 2]
        w.bind(pos=_sync, size=_sync)
        return w

    def _set_card_visible(self, card, visible):
        card._hidden = not visible
        card.opacity = 1 if visible else 0
        card.disabled = not visible
        self._sync_card_height(card)

    def _finalize_card(self, card, *, min_height=86):
        card._hidden = False
        card._min_height = self.suv(min_height)
        self._sync_card_height(card)
        Clock.schedule_once(lambda _dt, c=card: self._sync_card_height(c), 0)

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
                summary = meeting.get('summary', {}) or {}
                if not isinstance(summary, dict):
                    summary = {'summary': str(summary or '')}
                try:
                    actions = await self.backend.get_actions(self.meeting_id)
                    self._agentic_actions = [
                        a for a in actions if isinstance(a, dict) and a.get('id')
                    ] if isinstance(actions, list) else []
                except Exception:
                    self._agentic_actions = []
                if not self._agentic_actions and self._summary_has_executable_actions(summary):
                    try:
                        generated = await self.backend.generate_actions(self.meeting_id)
                        self._agentic_actions = [
                            a for a in generated if isinstance(a, dict) and a.get('id')
                        ] if isinstance(generated, list) else []
                    except Exception:
                        pass
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
        if not isinstance(summary, dict):
            summary = {'summary': str(summary or '')}
        self._populate_summary(summary)
        self._populate_list_section(
            self.questions_container,
            'Open Questions',
            summary.get('open_questions', []) or [],
            empty_hidden=True,
        )
        self._populate_list_section(
            self.risks_container,
            'Risks / Concerns',
            summary.get('risks_or_concerns', []) or [],
            empty_hidden=True,
        )
        self._populate_actions(summary.get('action_items', []) or [])
        self._populate_decisions(summary.get('decisions', []) or [])

    def _populate_summary(self, summary):
        c = self.summary_container
        c.clear_widgets()
        c.add_widget(self._heading('AI Summary'))
        c.add_widget(self._body_text(summary.get('summary') or 'No report generated yet.', COLORS['gray_300'], self.suf(FONT_SIZES['small'])))
        self._finalize_card(c, min_height=112)

    def _populate_list_section(self, container, title, items, *, empty_hidden=False):
        c = container
        c.clear_widgets()
        normalized = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not normalized and empty_hidden:
            self._set_card_visible(c, False)
            return
        self._set_card_visible(c, True)
        c.add_widget(self._heading(f'{title} ({len(normalized)})'))
        if not normalized:
            c.add_widget(self._body_text(f'No {title.lower()} captured yet.', COLORS['gray_500'], self.suf(FONT_SIZES['small'])))
        else:
            for item in normalized:
                c.add_widget(self._list_row(item))
        self._finalize_card(c)

    def _populate_actions(self, items):
        c = self.actions_container
        c.clear_widgets()
        c.add_widget(self._heading(f'Actions ({len(items)})'))
        if not items:
            c.add_widget(self._body_text('No action items found.', COLORS['gray_500'], self.suf(FONT_SIZES['small'])))
        else:
            for item in items:
                meta_parts = []
                if isinstance(item, dict):
                    task = item.get('task') or item.get('description') or str(item)
                    if item.get('assignee'):
                        meta_parts.append(str(item['assignee']))
                    if item.get('due_date'):
                        meta_parts.append(str(item['due_date']))
                    if item.get('type'):
                        meta_parts.append(str(item['type']).replace('_', ' ').title())
                else:
                    task = str(item)
                linked_action = self._match_agentic_action_for_item(item, set())
                trailing = self._make_execute_button(linked_action) if linked_action else None
                if trailing is None and isinstance(item, dict) and self._item_connector(item) == 'calendar':
                    trailing = self._make_summary_calendar_button(item)
                c.add_widget(self._list_row(task, '  ·  '.join(meta_parts), trailing=trailing))
        self._finalize_card(c)

    def _populate_decisions(self, decisions):
        self._populate_list_section(self.decisions_container, 'Decisions', decisions or [], empty_hidden=False)

    @staticmethod
    def _action_connector(action):
        if not isinstance(action, dict):
            return ''
        connector = (action.get('connector_target') or '').strip().lower()
        kind = (action.get('kind') or action.get('type') or '').strip().lower()
        if connector in {'gmail', 'calendar'}:
            return connector
        if kind in {'followup_email', 'email_draft', 'email', 'send_email'}:
            return 'gmail'
        if kind in {'schedule_followup', 'calendar_invite', 'calendar', 'calendar_event', 'schedule'}:
            return 'calendar'
        return ''

    @staticmethod
    def _item_connector(item):
        if not isinstance(item, dict):
            return ''
        typ = (item.get('type') or '').strip().lower()
        task = (item.get('task') or item.get('description') or '').strip().lower()
        haystack = f'{typ} {task}'
        if typ in {'email_draft', 'followup_email', 'email', 'send_email'}:
            return 'gmail'
        if typ in {'calendar_invite', 'schedule_followup', 'calendar', 'calendar_event', 'schedule'}:
            return 'calendar'
        if 'calendar' in haystack or 'schedule' in haystack or 'meeting invite' in haystack:
            return 'calendar'
        if 'email' in haystack or 'mail' in haystack:
            return 'gmail'
        return ''

    @classmethod
    def _summary_has_executable_actions(cls, summary):
        for item in (summary.get('action_items') or summary.get('actions') or []):
            if cls._item_connector(item) in {'gmail', 'calendar'}:
                return True
        return False

    def _match_agentic_action_for_item(self, item, used_ids):
        target = self._item_connector(item)
        if not target:
            return None
        task = (item.get('task') or item.get('description') or '').strip().lower()
        candidates = [
            action for action in self._agentic_actions
            if str(action.get('id') or '') not in used_ids
            and self._action_connector(action) == target
            and not (action.get('executed_at') or action.get('status') == 'executed')
        ]
        if not candidates:
            return None
        for action in candidates:
            title = str(action.get('title') or action.get('description') or '').strip().lower()
            if task and (task in title or title in task):
                return action
        return candidates[0]

    def _make_execute_button(self, action):
        from kivy.uix.button import Button

        connector = self._action_connector(action)
        if connector not in {'gmail', 'calendar'}:
            return None
        action_id = str(action.get('id') or '')

        def _button(text, width, *, create_draft=False, success_text='Done'):
            btn = Button(
                text=text,
                size_hint=(None, None),
                size=(self.suv(width), self.suv(40)),
                pos_hint={'center_y': 0.5},
                background_normal='',
                background_color=COLORS['blue'],
                color=COLORS['white'],
                bold=True,
                font_size=self.suf(FONT_SIZES['tiny']),
            )
            btn.bind(
                on_release=lambda _w, aid=action_id, b=btn: self._execute_action(
                    aid,
                    b,
                    create_draft=create_draft,
                    success_text=success_text,
                )
            )
            return btn

        if connector == 'gmail':
            buttons = BoxLayout(
                orientation='horizontal',
                size_hint=(None, None),
                size=(self.suv(198), self.suv(40)),
                spacing=self.suv(8),
                pos_hint={'center_y': 0.5},
            )
            buttons.add_widget(_button('Send', 84, create_draft=False, success_text='Sent'))
            buttons.add_widget(_button('Draft', 104, create_draft=True, success_text='Drafted'))
            return buttons
        return _button('Add to calendar', 150, success_text='Added')

    def _make_summary_calendar_button(self, item):
        from kivy.uix.button import Button

        btn = Button(
            text='Add to calendar',
            size_hint=(None, None),
            size=(self.suv(150), self.suv(40)),
            pos_hint={'center_y': 0.5},
            background_normal='',
            background_color=COLORS['blue'],
            color=COLORS['white'],
            bold=True,
            font_size=self.suf(FONT_SIZES['tiny']),
        )
        btn.bind(on_release=lambda _w, b=btn, it=dict(item): self._execute_summary_calendar_item(it, b))
        return btn

    def _execute_action(self, action_id, btn, *, create_draft=False, success_text='Done'):
        if not action_id:
            return
        btn.disabled = True
        btn.text = 'Working...'

        async def _run():
            ok = False
            try:
                await self.backend.execute_action(action_id, create_draft=create_draft)
                self._mark_action_executed(action_id)
                ok = True
            except Exception:
                ok = False

            def _apply(_dt):
                btn.disabled = False
                if ok:
                    btn.text = success_text
                    btn.background_color = COLORS['green']
                    self._populate()
                else:
                    btn.text = 'Retry'

            Clock.schedule_once(_apply, 0)

        run_async(_run())

    def _execute_summary_calendar_item(self, item, btn):
        if not self.meeting_id:
            return
        btn.disabled = True
        btn.text = 'Working...'

        async def _run():
            ok = False
            error = ''
            action_id = ''
            try:
                generated = await self.backend.generate_actions(self.meeting_id)
                if isinstance(generated, list):
                    self._agentic_actions = [
                        a for a in generated if isinstance(a, dict) and a.get('id')
                    ]
                    action = self._match_agentic_action_for_item(item, set())
                else:
                    action = None
                if action is None:
                    due_date = (item.get('due_date') or '').strip()
                    if not due_date:
                        raise RuntimeError('Calendar action needs a due date.')
                    action = await self.backend.create_manual_action(
                        self.meeting_id,
                        {
                            'connector': 'calendar',
                            'title': item.get('task') or 'Follow-up',
                            'description': item.get('task') or '',
                            'event_title': item.get('task') or 'Follow-up',
                            'suggested_date': due_date,
                            'suggested_time': '10:00',
                            'duration_minutes': 30,
                            'attendees': [],
                        },
                    )
                    if isinstance(action, dict) and action.get('id'):
                        self._agentic_actions.append(action)
                action_id = str((action or {}).get('id') or '')
                if not action_id:
                    raise RuntimeError('Calendar action could not be prepared.')
                await self.backend.execute_action(action_id)
                self._mark_action_executed(action_id)
                ok = True
            except Exception as exc:
                error = str(exc)

            def _apply(_dt):
                btn.disabled = False
                if ok:
                    btn.text = 'Added'
                    btn.background_color = COLORS['green']
                    self._populate()
                else:
                    btn.text = 'Need date' if 'due date' in error.lower() else 'Retry'

            Clock.schedule_once(_apply, 0)

        run_async(_run())

    def _mark_action_executed(self, action_id):
        for action in self._agentic_actions:
            if str(action.get('id') or '') == str(action_id):
                action['status'] = 'executed'
                action['executed_at'] = action.get('executed_at') or 'now'
                break

    def _on_delete(self, _inst):
        async def _delete():
            try:
                await self.backend.delete_meeting(self.meeting_id)
                Clock.schedule_once(lambda _: self.goto('meetings'), 0)
            except Exception:
                pass
        run_async(_delete())
