"""Premium room-name onboarding screen."""

from kivy.graphics import Color, Line
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import COLORS, FONT_SIZES
from screens.base_screen import BaseScreen

SUGGESTED_NAMES = ('Boardroom', 'Conference Room 1', 'Meeting Room A', 'War Room', 'Huddle Space')


class RoomNameScreen(BaseScreen):
    """Let user set device/room name before network setup."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(orientation='vertical', padding=[sv(24), sv(14), sv(24), sv(16)], spacing=sv(10), size_hint=(1, 1))
        self.make_dark_bg(root)

        header = BoxLayout(orientation='vertical', size_hint=(1, None), height=sv(96), spacing=sv(4))
        brand = Label(text='MeetingBox setup', font_size=sf(FONT_SIZES['small']), bold=True, color=COLORS['blue'], halign='left', valign='bottom', size_hint=(1, .35))
        brand.bind(size=brand.setter('text_size'))
        header.add_widget(brand)
        title = Label(text='Name this room', font_size=sf(FONT_SIZES['huge']), bold=True, color=COLORS['white'], halign='left', valign='middle', size_hint=(1, .65))
        title.bind(size=title.setter('text_size'))
        header.add_widget(title)
        root.add_widget(header)

        card = BoxLayout(orientation='vertical', padding=[sv(20), sv(18)], spacing=sv(12))
        self.attach_card_bg(card, radius=sv(28), color=(0.10, 0.15, 0.24, 0.88))
        subtitle = Label(text='This name appears on the home screen, recordings, and device pairing.', font_size=sf(FONT_SIZES['body']), color=COLORS['gray_300'], halign='left', valign='top', size_hint=(1, None), height=sv(44))
        subtitle.bind(size=subtitle.setter('text_size'))
        card.add_widget(subtitle)

        self._text_input = TextInput(
            hint_text='e.g. Boardroom A', multiline=False, size_hint=(1, None), height=sv(58),
            font_size=sf(FONT_SIZES['medium']), padding=[sv(16), sv(16)], background_normal='', background_active='',
            background_color=(0.16, 0.21, 0.30, 1), foreground_color=COLORS['white'], hint_text_color=COLORS['gray_500'], cursor_color=COLORS['white'],
        )
        self._text_input.bind(text=self._on_name_text_changed)
        card.add_widget(self._text_input)

        sug_label = Label(text='SUGGESTED NAMES', font_size=sf(FONT_SIZES['tiny']), bold=True, color=COLORS['gray_500'], halign='left', valign='middle', size_hint=(1, None), height=sv(22))
        sug_label.bind(size=sug_label.setter('text_size'))
        card.add_widget(sug_label)

        chip_scroll = ScrollView(do_scroll_y=False, size_hint=(1, None), height=sv(56), bar_width=4)
        chips_row = BoxLayout(orientation='horizontal', size_hint=(None, None), height=sv(50), spacing=sv(10))
        chips_row.bind(minimum_width=chips_row.setter('width'))
        for name in SUGGESTED_NAMES:
            w = max(sv(128), int(len(name) * sv(10) + sv(40)))
            chip = SecondaryButton(text=name, size_hint=(None, None), size=(w, sv(48)), font_size=sf(FONT_SIZES['small']))
            chip.bind(on_press=lambda inst, n=name: self._apply_chip(n))
            chips_row.add_widget(chip)
        chip_scroll.add_widget(chips_row)
        card.add_widget(chip_scroll)
        root.add_widget(card)

        footer = BoxLayout(orientation='horizontal', size_hint=(1, None), height=sv(56), spacing=sv(12))
        back_btn = SecondaryButton(text='Back', size_hint=(None, 1), width=sv(112), font_size=sf(FONT_SIZES['medium']))
        back_btn.bind(on_press=self._on_back)
        footer.add_widget(back_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        self._next_btn = PrimaryButton(text='Continue', size_hint=(None, 1), width=sv(160), font_size=sf(FONT_SIZES['medium']))
        self._next_btn.bind(on_press=self._on_next)
        self._next_btn.disabled = True
        footer.add_widget(self._next_btn)
        root.add_widget(footer)
        self.add_widget(root)

    def _apply_chip(self, name: str):
        self._text_input.text = name

    def _on_name_text_changed(self, _instance, _value):
        self._sync_next_enabled()

    def _sync_next_enabled(self):
        if getattr(self, '_next_btn', None):
            self._next_btn.disabled = not bool((self._text_input.text or '').strip())

    def _on_back(self, _inst):
        self.go_back()

    def _on_next(self, _inst):
        name = (self._text_input.text or '').strip()
        if not name:
            self.add_widget(ModalDialog(title='Room name required', message='Please enter a name for this MeetingBox.', confirm_text='OK', cancel_text=''))
            return
        self.app.device_name = name
        async def _save():
            try:
                await self.backend.set_device_name(name)
            except Exception:
                pass
        run_async(_save())
        self.goto('network_choice', transition='slide_left')
