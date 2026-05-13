"""
WiFi Network Item Component – Dark Theme

Row showing WiFi network info.
"""

from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle
from config import (
    BORDER_RADIUS,
    COLORS,
    FONT_SIZES,
    SPACING,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)


def _wn_suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _wn_suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


def _wn_suf(fs):
    v = other_screen_vertical_scale()
    return max(6, int(round(float(fs) * v)))


class WiFiNetworkItem(ButtonBehavior, BoxLayout):
    """
    Dark-themed WiFi network row.

    Shows: SSID, signal bars, connected status.
    """

    def __init__(self, network: dict, **kwargs):
        self.network = network

        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', _wn_suv(48))
        kwargs.setdefault('padding', [_wn_suh(SPACING['button_spacing']), _wn_suv(6)])
        kwargs.setdefault('spacing', _wn_suh(8))

        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=lambda w, v: setattr(self._bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._bg, 'size', w.size),
        )

        # SSID
        ssid = Label(
            text=network['ssid'],
            font_size=_wn_suf(FONT_SIZES['medium']),
            color=COLORS['white'],
            bold=network.get('connected', False),
            halign='left',
            size_hint=(0.65, 1),
        )
        ssid.bind(size=ssid.setter('text_size'))
        self.add_widget(ssid)

        # Signal as plain percent (avoids missing-glyph “bars” on device fonts)
        sig = int(network.get('signal_strength', 0) or 0)
        if network.get('connected'):
            sig_color = COLORS['green']
        elif sig >= 45:
            sig_color = COLORS['yellow']
        elif sig >= 25:
            sig_color = COLORS['yellow']
        else:
            sig_color = COLORS['gray_500']
        sig_label = Label(
            text=f'{sig}%',
            font_size=_wn_suf(FONT_SIZES['small']),
            color=sig_color,
            size_hint=(0.22, 1),
            halign='right',
        )
        sig_label.bind(size=sig_label.setter('text_size'))
        self.add_widget(sig_label)

    def on_press(self):
        if not self.network.get('connected'):
            self.canvas.before.clear()
            with self.canvas.before:
                Color(*COLORS['surface_light'])
                self._bg = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[BORDER_RADIUS])

    def on_release(self):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
