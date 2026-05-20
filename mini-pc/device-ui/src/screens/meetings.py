"""Premium meetings library screen for the device UI."""

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from async_helper import run_async

from components.button import SecondaryButton
from components.meeting_card import MeetingCard
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, MEETINGS_LIST_LIMIT, SPACING
from screens.base_screen import BaseScreen


class MeetingsScreen(BaseScreen):
    """Executive-readable meetings library."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meetings = []
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Meetings',
            device_name='Meeting Library',
            back_button=True,
            on_back=self.go_back,
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        body = BoxLayout(
            orientation='vertical',
            padding=[sv(SPACING['screen_padding']), sv(14), sv(SPACING['screen_padding']), sv(8)],
            spacing=sv(12),
        )

        hero = BoxLayout(orientation='horizontal', size_hint=(1, None), height=sv(92), padding=[sv(18), sv(14)], spacing=sv(12))
        self.attach_card_bg(hero, radius=sv(26), color=(0.10, 0.15, 0.24, 0.88))
        title_box = BoxLayout(orientation='vertical', spacing=sv(3))
        self.hero_title = Label(
            text='Recent meeting memory',
            font_size=sf(FONT_SIZES['large']),
            color=COLORS['white'],
            bold=True,
            halign='left',
            valign='bottom',
            size_hint=(1, 0.58),
        )
        self.hero_title.bind(size=self.hero_title.setter('text_size'))
        title_box.add_widget(self.hero_title)
        self.hero_subtitle = Label(
            text='Browse synced summaries, transcripts, decisions, and action items. Tap any meeting to open details.',
            font_size=sf(FONT_SIZES['small']),
            color=COLORS['gray_300'],
            halign='left',
            valign='top',
            size_hint=(1, 0.42),
        )
        self.hero_subtitle.bind(size=self.hero_subtitle.setter('text_size'))
        title_box.add_widget(self.hero_subtitle)
        hero.add_widget(title_box)
        refresh = SecondaryButton(text='Refresh', size_hint=(None, None), width=sv(118), height=sv(52), font_size=sf(FONT_SIZES['small']))
        refresh.bind(on_release=lambda *_: self._load_meetings())
        hero.add_widget(refresh)
        body.add_widget(hero)

        scroll_card = BoxLayout(orientation='vertical', padding=[sv(10), sv(10)], spacing=sv(8))
        self.attach_card_bg(scroll_card, radius=sv(26), color=(0.08, 0.11, 0.18, 0.72))
        scroll = ScrollView(do_scroll_x=False)
        self.meetings_container = GridLayout(
            cols=1,
            spacing=sv(SPACING['list_item_spacing']),
            size_hint_y=None,
            padding=[sv(4), sv(4)],
        )
        self.meetings_container.bind(minimum_height=self.meetings_container.setter('height'))
        scroll.add_widget(self.meetings_container)
        scroll_card.add_widget(scroll)
        body.add_widget(scroll_card)
        root.add_widget(body)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self._load_meetings()

    def _load_meetings(self):
        self._show_loading()

        async def _load():
            try:
                meetings = await self.backend.get_meetings(limit=MEETINGS_LIST_LIMIT)
                self.meetings = meetings
                Clock.schedule_once(lambda _: self._populate(), 0)
            except Exception:
                Clock.schedule_once(lambda _: self._show_empty('Could not load meetings. Check backend connection.'), 0)
        run_async(_load())

    def _show_loading(self):
        self.meetings_container.clear_widgets()
        self.meetings_container.add_widget(self._message_label('Loading meetings…'))

    def _show_empty(self, text='No meetings yet'):
        self.meetings_container.clear_widgets()
        self.meetings_container.add_widget(self._message_label(text))

    def _message_label(self, text):
        lbl = Label(
            text=text,
            font_size=self.suf(FONT_SIZES['medium']),
            color=COLORS['gray_300'],
            halign='center',
            valign='middle',
            size_hint_y=None,
            height=self.suv(120),
        )
        lbl.bind(size=lbl.setter('text_size'))
        return lbl

    def _populate(self):
        self.meetings_container.clear_widgets()
        self.hero_subtitle.text = f'{len(self.meetings)} recent meetings synced from MeetingBox memory.'
        if not self.meetings:
            self._show_empty('No meetings yet. Start a recording from Home to build memory.')
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
