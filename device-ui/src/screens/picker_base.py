"""
Picker Base Screen – Radio-button selection list

Used by Auto-Delete, Brightness, and Timeout pickers.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle

from kivy.clock import Clock
from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, BORDER_RADIUS
from async_helper import run_async


class _RadioRow(ButtonBehavior, BoxLayout):
    """Single radio-button row."""

    def __init__(self, label_text, selected=False, **kwargs):
        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', 60)
        kwargs.setdefault('padding', [16, 8])
        kwargs.setdefault('spacing', 12)
        super().__init__(**kwargs)

        self.label_text = label_text

        with self.canvas.before:
            Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=lambda w, v: setattr(self._bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._bg, 'size', w.size),
        )

        self.radio_label = Label(
            text='●' if selected else '○',
            font_size=FONT_SIZES['large'],
            color=COLORS['blue'] if selected else COLORS['gray_500'],
            size_hint=(None, 1),
            width=30,
        )
        self.add_widget(self.radio_label)

        self.text_label = Label(
            text=label_text,
            font_size=FONT_SIZES['medium'],
            color=COLORS['white'],
            halign='left',
            size_hint=(1, 1),
        )
        self.text_label.bind(size=self.text_label.setter('text_size'))
        self.add_widget(self.text_label)

    def set_selected(self, selected: bool):
        self.radio_label.text = '●' if selected else '○'
        self.radio_label.color = COLORS['blue'] if selected else COLORS['gray_500']


class PickerBaseScreen(BaseScreen):
    """
    Base picker screen with radio-button list.

    Subclasses set:
      _title       : str
      _description : str or None
      _options     : list of (value, display_text)
      _setting_key : str (backend setting name)
      _default     : str (default value)
    """

    _title = 'Picker'
    _description = None
    _options = []
    _setting_key = ''
    _default = ''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._selected = self._default
        self._rows = []
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Header
        self.status_bar = StatusBar(
            status_text=self._title,
            device_name=self._title,
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        # Description (if any)
        if self._description:
            desc = Label(
                text=self._description,
                font_size=14,
                color=COLORS['gray_500'],
                halign='left',
                valign='top',
                size_hint=(1, None),
                height=40,
                padding=[SPACING['screen_padding'], 4],
            )
            desc.bind(size=desc.setter('text_size'))
            root.add_widget(desc)

        # Options
        options_box = GridLayout(
            cols=1,
            spacing=SPACING['list_item_spacing'],
            padding=[SPACING['screen_padding'], 8],
            size_hint_y=None,
        )
        options_box.bind(minimum_height=options_box.setter('height'))

        for value, display in self._options:
            row = _RadioRow(
                label_text=display,
                selected=(value == self._default),
            )
            row._value = value
            row.bind(on_press=self._on_option_selected)
            options_box.add_widget(row)
            self._rows.append(row)

        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(options_box)
        root.add_widget(scroll)

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        async def _load():
            try:
                settings = await self.backend.get_settings()
                saved = settings.get(self._setting_key, self._default)
                def _apply(_dt):
                    self._selected = saved
                    for r in self._rows:
                        r.set_selected(r._value == saved)
                Clock.schedule_once(_apply, 0)
            except Exception:
                pass
        run_async(_load())

    def _on_option_selected(self, row):
        self._selected = row._value
        for r in self._rows:
            r.set_selected(r._value == self._selected)
        self._save_setting()

    def _save_setting(self):
        async def _save():
            try:
                await self.backend.update_settings(
                    {self._setting_key: self._selected})
            except Exception:
                pass
        run_async(_save())
