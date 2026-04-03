"""
WiFi Network Item Component – Dark Theme

Row showing WiFi network info.
"""

from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle
from config import COLORS, FONT_SIZES, SPACING, BORDER_RADIUS


class WiFiNetworkItem(ButtonBehavior, BoxLayout):
    """
    Dark-themed WiFi network row.

    Shows: SSID, signal bars, connected status.
    """

    def __init__(self, network: dict, **kwargs):
        self.network = network

        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', 48)
        kwargs.setdefault('padding', [SPACING['button_spacing'], 6])
        kwargs.setdefault('spacing', 8)

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
            font_size=FONT_SIZES['medium'],
            color=COLORS['white'],
            bold=network.get('connected', False),
            halign='left',
            size_hint=(0.65, 1),
        )
        ssid.bind(size=ssid.setter('text_size'))
        self.add_widget(ssid)

        # Signal strength (0–100): bar count + color tier
        sig = int(network.get('signal_strength', 0) or 0)
        if sig >= 70:
            n_bars, sig_color = 4, COLORS['green']
        elif sig >= 45:
            n_bars, sig_color = 3, COLORS['yellow']
        elif sig >= 25:
            n_bars, sig_color = 2, COLORS['yellow']
        elif sig >= 10:
            n_bars, sig_color = 1, COLORS['red']
        else:
            n_bars, sig_color = 1, COLORS['gray_600']
        bars = '▂▄▆█'[: max(1, n_bars)]
        if not network.get('connected'):
            sig_color = COLORS['gray_500']
        sig_label = Label(
            text=bars,
            font_size=FONT_SIZES['medium'],
            color=sig_color,
            size_hint=(0.2, 1),
        )
        self.add_widget(sig_label)

        # Connected
        if network.get('connected'):
            ok = Label(
                text='✓',
                font_size=FONT_SIZES['medium'],
                color=COLORS['green'],
                size_hint=(0.15, 1),
            )
            self.add_widget(ok)

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
