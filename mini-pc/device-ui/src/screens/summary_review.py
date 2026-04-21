"""
Report Review Screen -- Post-recording detailed report & actions

Two tabs: Report | Actions
- Report tab: full meeting report from transcript (detailed, not a short summary)
- Actions tab: action items with checkboxes and Execute Selected button
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

# Horizontal space for checkbox + per-row Execute + spacing (must fit on screen)
_ACTION_ROW_RESERVED = 28 + 96 + 20

# Prefix for transcript-only body until the AI report row exists in DB.
_TRANSCRIPT_ONLY_PREFIX = "[i]Transcript[/i]"


class SummaryReviewScreen(BaseScreen):
    """Post-recording screen with Report and Actions tabs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id = None
        self._summary_data = {}
        self._actions_data = []
        self._selected_actions = set()
        self._auto_generate_attempted = False
        self._current_tab = 'summary'
        self._detail_loading = False
        self._build_ui()

    def _build_ui(self):
        self.root_layout = BoxLayout(orientation='vertical')
        self.make_dark_bg(self.root_layout)

        self.status_bar = StatusBar(
            status_text='MEETING REPORT',
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
            text='Report',
            font_size=self.suf(FONT_SIZES['body']),
            size_hint=(0.5, 1),
        )
        self.summary_tab_btn.bind(on_press=lambda _: self._switch_tab('summary'))
        tab_bar.add_widget(self.summary_tab_btn)

        self.actions_tab_btn = SecondaryButton(
            text='Actions',
            font_size=self.suf(FONT_SIZES['body']),
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
            font_size=self.suf(FONT_SIZES['body']),
            size_hint=(0.4, 1),
        )
        self.close_btn.bind(on_press=self._on_close)
        btn_row.add_widget(self.close_btn)

        self.execute_btn = PrimaryButton(
            text='Execute Selected',
            font_size=self.suf(FONT_SIZES['body']),
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
        self._auto_generate_attempted = False
        self._detail_loading = True
        self._render_loading_tab()
        self._fetch_and_merge_detail()

    @staticmethod
    def _segments_to_transcript_text(segments):
        lines = []
        for seg in segments or []:
            if not isinstance(seg, dict):
                continue
            t = (seg.get("text") or "").strip()
            if not t:
                continue
            st = float(seg.get("start_time") or 0.0)
            mins, secs = divmod(int(st), 60)
            spk = seg.get("speaker_id")
            prefix = f"[{mins:02d}:{secs:02d}]"
            if spk is not None and str(spk).strip() != "":
                prefix += f" Speaker {spk}:"
            lines.append(f"{prefix} {t}")
        return "\n".join(lines)

    def _apply_meeting_detail(self, detail: dict):
        """Merge GET /api/meetings/{id} into _summary_data; fall back to transcript."""
        if not detail:
            self._summary_data = {"summary": "Could not load this meeting."}
            return
        block = detail.get("summary")
        if not isinstance(block, dict):
            block = {}
        segments = detail.get("segments") or []
        report = (block.get("summary") or "").strip()
        if not report and segments:
            report = (
                f"{_TRANSCRIPT_ONLY_PREFIX} — the full AI report will appear here when "
                "analysis finishes. You can read the transcript below.\n\n"
                + self._segments_to_transcript_text(segments)
            )
        elif not report:
            report = "No report or transcript is available yet."
        merged = {**block, "summary": report}
        self._summary_data = merged

    def _render_loading_tab(self):
        self._current_tab = "summary"
        self.content_area.clear_widgets()
        self.execute_btn.opacity = 0
        self.execute_btn.disabled = True
        hold = BoxLayout(orientation="vertical", padding=[SPACING["screen_padding"], 24])
        hold.add_widget(
            Label(
                text="Loading meeting…",
                font_size=self.suf(FONT_SIZES["body"]),
                color=COLORS["gray_400"],
                halign="center",
                valign="middle",
                size_hint=(1, 1),
            )
        )
        self.content_area.add_widget(hold)

    def _fetch_and_merge_detail(self):
        if not self.meeting_id:
            self._detail_loading = False
            self._render_tab()
            return

        async def _run():
            try:
                detail = await self.backend.get_meeting_detail(self.meeting_id)
            except Exception as e:
                logger.error("get_meeting_detail failed: %s", e)
                detail = {}

            def _done(_dt):
                self._detail_loading = False
                self._apply_meeting_detail(detail)
                self._load_actions()
                self._render_tab()

            Clock.schedule_once(_done, 0)

        run_async(_run())

    def _load_actions(self):
        if not self.meeting_id:
            return

        async def _fetch():
            try:
                actions = await self.backend.get_actions(self.meeting_id)

                raw_s = (self._summary_data or {}).get("summary") or ""
                tx_only = isinstance(raw_s, str) and raw_s.startswith(
                    _TRANSCRIPT_ONLY_PREFIX
                )
                has_summary_content = bool(
                    ((not tx_only) and str(raw_s).strip())
                    or (self._summary_data or {}).get('action_items')
                    or (self._summary_data or {}).get('decisions')
                )
                if (
                    not actions
                    and has_summary_content
                    and not self._auto_generate_attempted
                ):
                    self._auto_generate_attempted = True
                    try:
                        actions = await self.backend.generate_actions(self.meeting_id)
                    except Exception as gen_err:
                        logger.error(f"Failed to auto-generate actions: {gen_err}")

                def _update(_dt):
                    self._actions_data = actions
                    if self._current_tab == 'actions':
                        self._render_actions_tab()
                Clock.schedule_once(_update, 0)
            except Exception as e:
                logger.error(f"Failed to load actions: {e}")

        run_async(_fetch())

    @staticmethod
    def _coerce_summary_action_items(raw):
        out = []
        for a in raw or []:
            if isinstance(a, dict):
                task = (a.get("task") or a.get("description") or "").strip()
                if not task:
                    task = str(a)
                out.append({
                    "task": task,
                    "assignee": a.get("assignee"),
                    "due_date": a.get("due_date"),
                    "completed": bool(a.get("completed", False)),
                })
            else:
                s = str(a).strip()
                if s:
                    out.append({
                        "task": s,
                        "assignee": None,
                        "due_date": None,
                        "completed": False,
                    })
        return out

    @staticmethod
    def _effective_connector(action: dict) -> str:
        """
        gmail | calendar | '' — derive from connector_target and fall back to kind/type
        so Execute still appears when the model returns mixed or alternate labels.
        """
        if not action:
            return ''
        ct = str(action.get('connector_target') or '').strip().lower()
        if ct in ('gmail', 'calendar'):
            return ct
        if ct in ('google_calendar', 'gcal', 'google calendar'):
            return 'calendar'
        if ct in ('email', 'e-mail', 'mail'):
            return 'gmail'
        kind = str(action.get('kind') or '').strip().lower()
        if kind == 'followup_email':
            return 'gmail'
        if kind == 'schedule_followup':
            return 'calendar'
        lt = str(action.get('type') or '').strip().lower()
        if lt == 'email_draft':
            return 'gmail'
        if lt == 'calendar_invite':
            return 'calendar'
        return ''

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

    def _render_summary_tab(self):
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(
            orientation='vertical',
            size_hint_y=None,
            padding=[SPACING['screen_padding'], 8],
            spacing=6,
        )
        content.bind(minimum_height=content.setter('height'))

        summary_text = self._summary_data.get('summary', 'No report available.')
        lbl = Label(
            text=summary_text,
            font_size=self.suf(FONT_SIZES['body']),
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
                font_size=self.suf(FONT_SIZES['body']),
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
                    font_size=self.suf(FONT_SIZES['small']),
                    color=COLORS['gray_300'],
                    halign='left',
                    valign='top',
                    size_hint_y=None,
                )
                dl.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
                dl.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + 4))
                content.add_widget(dl)

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

        self.execute_btn.opacity = 1

        agentic = list(self._actions_data or [])
        summary_items = self._coerce_summary_action_items(
            (self._summary_data or {}).get("action_items", []),
        )

        if agentic:
            hdr = Label(
                text='AI actions — tap Execute on any row, or select multiple and use Execute Selected',
                font_size=self.suf(FONT_SIZES['small']),
                bold=True,
                color=COLORS['blue'],
                halign='left',
                valign='middle',
                size_hint_y=None,
                height=22,
            )
            hdr.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
            content.add_widget(hdr)

            for action in agentic:
                aid = action.get('id')
                if not aid:
                    logger.warning('Skipping action row without id: %s', action.get('title'))
                    continue

                row = BoxLayout(
                    orientation='horizontal',
                    size_hint_y=None,
                    size_hint_x=1,
                    spacing=6,
                    height=52,
                )

                cb = CheckBox(
                    size_hint=(None, None),
                    size=(28, 28),
                    active=aid in self._selected_actions,
                )
                cb.bind(active=partial(self._on_action_toggle, aid))
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
                    font_size=self.suf(FONT_SIZES['small'] + 1),
                    color=color,
                    halign='left',
                    valign='middle',
                    size_hint=(1, 1),
                )

                def _sync_label_text_size(*_a, lbl=al, rw=row, res=_ACTION_ROW_RESERVED):
                    w = rw.width
                    if w and w > res:
                        lbl.text_size = (w - res, None)

                row.bind(width=_sync_label_text_size)
                al.bind(
                    texture_size=lambda _lbl, ts, rw=row: setattr(
                        rw, 'height', max(52, ts[1] + 14),
                    ),
                )
                row.add_widget(al)

                eff = self._effective_connector(action)
                if eff in ('calendar', 'gmail'):
                    run_btn = SecondaryButton(
                        text='Execute',
                        font_size=self.suf(FONT_SIZES['small']),
                        size_hint=(None, None),
                        width=96,
                        height=34,
                    )
                    is_pending = status == 'pending'
                    run_btn.disabled = not is_pending
                    run_btn.opacity = 1.0 if is_pending else 0.45
                    run_btn.bind(on_press=partial(self._on_single_action_execute, action))
                    row.add_widget(run_btn)

                content.add_widget(row)
                Clock.schedule_once(lambda dt, fn=_sync_label_text_size: fn(), 0)
                Clock.schedule_once(lambda dt, fn=_sync_label_text_size: fn(), 0.2)

            self.execute_btn.disabled = False
            self.execute_btn.opacity = 1

        elif summary_items:
            hdr = Label(
                text='Action items from report',
                font_size=self.suf(FONT_SIZES['body']),
                bold=True,
                color=COLORS['blue'],
                halign='left',
                valign='middle',
                size_hint_y=None,
                height=24,
            )
            hdr.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
            content.add_widget(hdr)

            note = Label(
                text=(
                    'These are checklist items from the report. Gmail/Calendar actions are created on the server '
                    'with the report when you are signed in and integrations are connected. '
                    'If the AI Actions list is still empty, connect accounts in the web app or reopen this screen.'
                ),
                font_size=self.suf(FONT_SIZES['small']),
                color=COLORS['gray_500'],
                halign='left',
                valign='top',
                size_hint_y=None,
            )
            note.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
            note.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + 4))
            content.add_widget(note)

            for item in summary_items:
                meta_bits = []
                if item.get('assignee'):
                    meta_bits.append(str(item['assignee']))
                if item.get('due_date'):
                    meta_bits.append(str(item['due_date']))
                meta = f" · {' · '.join(meta_bits)}" if meta_bits else ''
                line = f"{'[x] ' if item.get('completed') else ''}{item['task']}{meta}"

                al = Label(
                    text=line,
                    font_size=self.suf(FONT_SIZES['small'] + 1),
                    color=COLORS['gray_300'],
                    halign='left',
                    valign='top',
                    size_hint_y=None,
                )
                al.bind(width=lambda w, val: setattr(w, 'text_size', (val, None)))
                al.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1] + 6))
                content.add_widget(al)

            self.execute_btn.disabled = True
            self.execute_btn.opacity = 0.45

        else:
            empty = Label(
                text='No action items found.',
                font_size=self.suf(FONT_SIZES['body']),
                color=COLORS['gray_500'],
                halign='center',
                size_hint_y=None,
                height=40,
            )
            content.add_widget(empty)
            self.execute_btn.disabled = True
            self.execute_btn.opacity = 0.45

        scroll.add_widget(content)
        self.content_area.add_widget(scroll)

    def _on_action_toggle(self, action_id, checkbox, value):
        if value:
            self._selected_actions.add(action_id)
        else:
            self._selected_actions.discard(action_id)

    def _on_single_action_execute(self, action, _inst):
        """Run one agentic action: calendar → create event; gmail → save draft only."""
        action_id = action.get('id')
        if not action_id:
            return
        eff = self._effective_connector(action)
        if eff not in ('gmail', 'calendar'):
            return
        if (action.get('status') or 'pending') != 'pending':
            return
        create_draft = eff == 'gmail'

        async def _run():
            try:
                await self.backend.execute_action(action_id, create_draft=create_draft)
                msg = (
                    'Calendar event was created. Check Google Calendar.'
                    if not create_draft
                    else 'Email draft was saved. Open Gmail → Drafts.'
                )

                def _after_ok(_dt):
                    self._load_actions()
                    dlg = ModalDialog(
                        title='Done',
                        message=msg,
                        confirm_text='OK',
                        cancel_text='',
                        on_confirm=lambda: None,
                    )
                    self.add_widget(dlg)

                Clock.schedule_once(_after_ok, 0)
            except Exception as e:
                # Python 3.12+ deletes `e` after this block; capture text before scheduling UI.
                err_text = (str(e) or 'Could not complete action. Try the web dashboard.')[:500]
                logger.error("Single action execute failed %s: %s", action_id, err_text)

                def _err(_dt):
                    dlg = ModalDialog(
                        title='Action failed',
                        message=err_text,
                        confirm_text='OK',
                        cancel_text='',
                        on_confirm=lambda: None,
                    )
                    self.add_widget(dlg)

                Clock.schedule_once(_err, 0)

        run_async(_run())

    def _on_close(self, _inst):
        self.goto('home', transition='fade')

    def _on_execute(self, _inst):
        if not self._selected_actions:
            return

        async def _run():
            for action_id in list(self._selected_actions):
                try:
                    act = next(
                        (a for a in (self._actions_data or []) if a.get('id') == action_id),
                        None,
                    )
                    eff = self._effective_connector(act or {})
                    if eff not in ('gmail', 'calendar'):
                        continue
                    await self.backend.execute_action(
                        action_id,
                        create_draft=(eff == 'gmail'),
                    )
                except Exception as e:
                    logger.error(f"Failed to execute action {action_id}: {e}")

        run_async(_run())

        self._selected_actions.clear()
        dialog = ModalDialog(
            title='Actions Queued',
            message=(
                'Calendar selections create events. Gmail selections save drafts only '
                '(not sent). Check your Gmail Drafts and the dashboard.\n'
                f'Dashboard: {DASHBOARD_URL}'
            ),
            confirm_text='OK',
            cancel_text='',
            on_confirm=lambda: self.goto('home', transition='fade'),
        )
        self.add_widget(dialog)

    def on_enter(self):
        self._current_tab = 'summary'
        if self._detail_loading:
            return
        if self.meeting_id:
            self._detail_loading = True
            self._render_loading_tab()
            self._fetch_and_merge_detail()
        else:
            self._render_tab()
