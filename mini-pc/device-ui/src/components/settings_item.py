"""
Settings Item Component – Dark Theme

Row in the scrollable settings list.
Supports three modes:
  1. Tappable row with arrow (→)
  2. Toggle row with switch
  3. Info-only row (no interaction)
"""

from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle
from config import COLORS, FONT_SIZES, SPACING, BORDER_RADIUS
from components.toggle_switch import ToggleSwitch


class SettingsItem(ButtonBehavior, BoxLayout):
    """
    Dark-themed settings row (60 px min height).

    Parameters
    ----------
    title       : str
    subtitle    : str   – current value / description
    mode        : str   – 'arrow' | 'toggle' | 'info'
    active      : bool  – initial toggle state (toggle mode)
    on_press    : callable
    on_toggle   : callable(bool) – for toggle mode
    """

    def __init__(self, title: str, subtitle: str = '',
                 mode: str = 'arrow', active: bool = False,
                 on_press=None, on_toggle=None, **kwargs):

        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', 60)
        kwargs.setdefault('padding', [16, 8])
        kwargs.setdefault('spacing', 8)

        super().__init__(**kwargs)

        self._mode = mode
        if on_press and mode == 'arrow':
            self.bind(on_press=on_press)

        # Card background
        with self.canvas.before:
            Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=lambda w, v: setattr(self._bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._bg, 'size', w.size),
        )

        # Text container (left)
        text_box = BoxLayout(
            orientation='vertical',
            size_hint=(0.75, 1),
            spacing=2,
        )

        self.title_label = Label(
            text=title,
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['white'],
            halign='left',
            valign='bottom',
            size_hint=(1, 0.5),
        )
        self.title_label.bind(size=self.title_label.setter('text_size'))
        text_box.add_widget(self.title_label)

        self.subtitle_label = Label(
            text=subtitle,
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='left',
            valign='top',
            size_hint=(1, 0.5),
        )
        self.subtitle_label.bind(size=self.subtitle_label.setter('text_size'))
        text_box.add_widget(self.subtitle_label)

        self.add_widget(text_box)

        # Right widget
        if mode == 'arrow':
            arrow = Label(
                text='→',
                font_size=FONT_SIZES['large'],
                color=COLORS['gray_500'],
                size_hint=(0.15, 1),
            )
            self.add_widget(arrow)
        elif mode == 'toggle':
            self.toggle = ToggleSwitch(
                active=active,
                on_toggle=on_toggle,
                size_hint=(None, None),
                size=(52, 30),
                pos_hint={'center_y': 0.5},
            )
            self.add_widget(self.toggle)
        else:
            # info – no indicator
            from kivy.uix.widget import Widget
            self.add_widget(Widget(size_hint=(0.1, 1)))

    # Press feedback
    def on_press(self):
        if self._mode == 'arrow':
            with self.canvas.before:
                self.canvas.before.clear()
                Color(*COLORS['surface_light'])
                self._bg = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[BORDER_RADIUS])

    def on_release(self):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
