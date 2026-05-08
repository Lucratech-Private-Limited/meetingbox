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
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle
from config import (
    BORDER_RADIUS,
    COLORS,
    FONT_SIZES,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)
from components.toggle_switch import ToggleSwitch


def _si_suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _si_suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


def _si_suf(fs):
    v = other_screen_vertical_scale()
    return max(6, int(round(float(fs) * v)))


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
        kwargs.setdefault('height', _si_suv(68))
        kwargs.setdefault('padding', [_si_suh(18), _si_suv(10)])
        kwargs.setdefault('spacing', _si_suh(8))

        super().__init__(**kwargs)

        self._mode = mode
        if on_press and mode == 'arrow':
            self.bind(on_press=on_press)

        # Card background (keep Color + rect; update rgba on press — avoid clear()+rebuild)
        with self.canvas.before:
            self._shadow_color = Color(0, 0, 0, 0.14)
            self._shadow = RoundedRectangle(pos=(self.x + 1, self.y - _si_suv(2)), size=self.size, radius=[BORDER_RADIUS])
            self._bg_color = Color(0.12, 0.16, 0.23, 0.86)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=self._sync_bg,
            size=self._sync_bg,
        )

        # Text container (left)
        text_box = BoxLayout(
            orientation='vertical',
            size_hint=(0.75, 1),
            spacing=_si_suv(2),
        )

        self.title_label = Label(
            text=title,
            font_size=_si_suf(FONT_SIZES['small'] + 2),
            color=COLORS['white'],
            halign='left',
            valign='bottom',
            size_hint=(1, 0.5),
        )
        self.title_label.bind(size=self.title_label.setter('text_size'))
        text_box.add_widget(self.title_label)

        self.subtitle_label = Label(
            text=subtitle,
            font_size=_si_suf(FONT_SIZES['small']),
            color=COLORS['gray_300'],
            halign='left',
            valign='top',
            size_hint=(1, 0.5),
        )
        self.subtitle_label.bind(size=self.subtitle_label.setter('text_size'))
        text_box.add_widget(self.subtitle_label)

        self.add_widget(text_box)

        # Right widget (no Unicode chevron — many embedded fonts render it as tofu)
        if mode == 'arrow':
            self.add_widget(Widget(size_hint=(None, 1), width=_si_suh(8)))
        elif mode == 'toggle':
            self.toggle = ToggleSwitch(
                active=active,
                on_toggle=on_toggle,
                size_hint=(None, None),
                size=(_si_suh(52), _si_suv(30)),
                pos_hint={'center_y': 0.5},
            )
            self.add_widget(self.toggle)
        else:
            # info – no indicator
            self.add_widget(Widget(size_hint=(0.1, 1)))


    def _sync_bg(self, *_args):
        self._shadow.pos = (self.x + 1, self.y - _si_suv(2))
        self._shadow.size = self.size
        self._bg.pos = self.pos
        self._bg.size = self.size

    # Press feedback
    def on_press(self):
        if self._mode == 'arrow':
            self._bg_color.rgba = (0.18, 0.24, 0.34, 0.96)

    def on_release(self):
        if self._mode == 'arrow':
            self._bg_color.rgba = (0.12, 0.16, 0.23, 0.86)
