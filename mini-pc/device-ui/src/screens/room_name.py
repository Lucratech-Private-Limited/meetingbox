"""
Name this room – onboarding step after Welcome.

Design ref: UI_Ref_for_cursor/Nameing the room/Frame 2.png
"""

from pathlib import Path

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.uix.textinput import TextInput
from kivy.graphics import Color, Line, Rectangle

from screens.base_screen import BaseScreen
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import COLORS, FONT_SIZES, ASSETS_DIR
from async_helper import run_async

WELCOME_DIR = ASSETS_DIR / 'welcome'
LOGO_PATH = str(WELCOME_DIR / 'LOGO.png')
ROOM_BG = (0.043, 0.051, 0.067, 1)  # match Welcome #0B0D11

SUGGESTED_NAMES = (
    'Boardroom',
    'Conference Room 1',
    'Meeting Room A',
    'War Room',
    'Huddle Space',
)


class RoomNameScreen(BaseScreen):
    """Let user set device/room name before WiFi setup."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(
            orientation='vertical',
            padding=[24, 12, 24, 16],
            spacing=0,
            size_hint=(1, 1),
        )
        root.canvas.before.clear()
        with root.canvas.before:
            Color(*ROOM_BG)
            self._root_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._root_bg, 'pos', w.pos),
            size=lambda w, *_: setattr(self._root_bg, 'size', w.size),
        )

        # Header
        header = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=52,
            spacing=12,
        )
        if Path(LOGO_PATH).exists():
            header.add_widget(Image(
                source=LOGO_PATH,
                size_hint=(None, 1),
                width=40,
                fit_mode='contain',
            ))
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text='MeetingBox',
            font_size=self.suf(FONT_SIZES['title']),
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
            size_hint_x=1,
        )
        brand.bind(size=brand.setter('text_size'))
        header.add_widget(brand)
        root.add_widget(header)

        scroll = ScrollView(
            do_scroll_x=False,
            size_hint=(1, 1),
            bar_width=8,
        )
        inner = BoxLayout(
            orientation='vertical',
            spacing=0,
            size_hint_y=None,
            padding=[0, 4, 0, 8],
        )
        inner.bind(minimum_height=inner.setter('height'))

        inner.add_widget(Widget(size_hint=(1, None), height=4))

        title = Label(
            text='Name this room',
            font_size=self.suf(FONT_SIZES['huge']),
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
            size_hint=(1, None),
            height=44,
        )
        title.bind(size=title.setter('text_size'))
        inner.add_widget(title)

        subtitle = Label(
            text='This is your device name (home screen, link device, recordings).',
            font_size=self.suf(FONT_SIZES['body']),
            color=COLORS['gray_400'],
            halign='left',
            valign='top',
            size_hint=(1, None),
            height=40,
        )
        subtitle.bind(size=subtitle.setter('text_size'))
        inner.add_widget(subtitle)

        inner.add_widget(Widget(size_hint=(1, None), height=12))

        self._text_input = TextInput(
            hint_text='e.g. Boardroom A',
            multiline=False,
            size_hint=(1, None),
            height=52,
            font_size=self.suf(FONT_SIZES['medium']),
            padding=[16, 14],
            background_normal='',
            background_active='',
            background_color=COLORS['surface_light'],
            foreground_color=COLORS['white'],
            hint_text_color=COLORS['gray_600'],
            cursor_color=COLORS['white'],
        )
        inner.add_widget(self._text_input)
        self._text_input.bind(text=self._on_name_text_changed)

        inner.add_widget(Widget(size_hint=(1, None), height=16))

        sug_label = Label(
            text='SUGGESTED NAMES',
            font_size=self.suf(FONT_SIZES['small']),
            bold=True,
            color=COLORS['gray_500'],
            halign='left',
            valign='middle',
            size_hint=(1, None),
            height=22,
        )
        sug_label.bind(size=sug_label.setter('text_size'))
        inner.add_widget(sug_label)

        inner.add_widget(Widget(size_hint=(1, None), height=8))

        chip_scroll = ScrollView(
            do_scroll_y=False,
            size_hint=(1, None),
            height=52,
            bar_width=6,
        )
        chips_row = BoxLayout(
            orientation='horizontal',
            size_hint=(None, None),
            height=48,
            spacing=10,
        )

        chips_row.bind(minimum_width=chips_row.setter('width'))

        for name in SUGGESTED_NAMES:
            w = max(120, int(len(name) * 11 + 36))
            chip = SecondaryButton(
                text=name,
                size_hint=(None, None),
                size=(w, 44),
                font_size=self.suf(FONT_SIZES['small']),
            )
            chip.bind(on_press=lambda inst, n=name: self._apply_chip(n))
            chips_row.add_widget(chip)

        chip_scroll.add_widget(chips_row)
        inner.add_widget(chip_scroll)

        inner.add_widget(Widget(size_hint=(1, None), height=12))

        # Divider
        sep = Widget(size_hint=(1, None), height=1)
        with sep.canvas:
            Color(*COLORS['gray_800'])
            self._sep_line = Line(width=1)
        sep.bind(
            pos=self._draw_sep,
            size=self._draw_sep,
        )
        inner.add_widget(sep)

        scroll.add_widget(inner)
        root.add_widget(scroll)

        footer = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=52,
            spacing=12,
        )
        back_btn = SecondaryButton(
            text='Back',
            size_hint=(None, 1),
            width=100,
            font_size=self.suf(FONT_SIZES['medium']),
        )
        back_btn.bind(on_press=self._on_back)
        footer.add_widget(back_btn)

        footer.add_widget(Widget(size_hint=(1, 1)))

        self._next_btn = PrimaryButton(
            text='Next Step',
            size_hint=(None, 1),
            width=140,
            font_size=self.suf(FONT_SIZES['medium']),
        )
        self._next_btn.bind(on_press=self._on_next)
        self._next_btn.disabled = True
        footer.add_widget(self._next_btn)

        root.add_widget(footer)
        self.add_widget(root)

    def _draw_sep(self, w, *_):
        if hasattr(self, '_sep_line'):
            self._sep_line.points = [w.x, w.y + w.height / 2, w.x + w.width, w.y + w.height / 2]

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
            self.add_widget(ModalDialog(
                title='Room name',
                message='Enter a name or pick a suggestion to continue.',
                confirm_text='OK',
                cancel_text='',
            ))
            return

        async def _save():
            try:
                await self.backend.update_settings({'device_name': name})
                Clock.schedule_once(
                    lambda _dt: setattr(self.app, 'device_name', name), 0)
                Clock.schedule_once(
                    lambda _dt: self.goto('network_choice', transition='slide_left'), 0)
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self.add_widget(ModalDialog(
                        title='Could not save',
                        message='Check network and try again.',
                        confirm_text='OK',
                        cancel_text='',
                    )), 0)

        run_async(_save())

    def on_enter(self):
        self._text_input.text = ''
        self._sync_next_enabled()
