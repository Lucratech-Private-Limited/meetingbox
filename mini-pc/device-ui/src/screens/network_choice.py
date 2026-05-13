"""Premium network choice onboarding screen."""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import COLORS, FONT_SIZES
from network_util import linux_ethernet_ready
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class NetworkChoiceScreen(BaseScreen):
    """Choose Wi‑Fi setup or proceed with wired Ethernet."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._hint_label = None
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(orientation='vertical', padding=[sv(24), sv(14), sv(24), sv(16)], spacing=sv(12), size_hint=(1, 1))
        self.make_dark_bg(root)

        header = BoxLayout(orientation='vertical', size_hint=(1, None), height=sv(102), spacing=sv(4))
        kicker = Label(text='SETUP · NETWORK', font_size=sf(FONT_SIZES['tiny']), bold=True, color=COLORS['blue'], halign='left', valign='bottom', size_hint=(1, .32))
        kicker.bind(size=kicker.setter('text_size'))
        header.add_widget(kicker)
        title = Label(text='Connect to the internet', font_size=sf(FONT_SIZES['huge']), bold=True, color=COLORS['white'], halign='left', valign='middle', size_hint=(1, .68))
        title.bind(size=title.setter('text_size'))
        header.add_widget(title)
        root.add_widget(header)

        card = BoxLayout(orientation='vertical', padding=[sv(20), sv(18)], spacing=sv(12))
        self.attach_card_bg(card, radius=sv(28), color=(0.10, 0.15, 0.24, 0.88))
        subtitle = Label(text='Use Wi‑Fi, or skip if this MeetingBox already has working wired Ethernet.', font_size=sf(FONT_SIZES['body']), color=COLORS['gray_300'], halign='left', valign='top', size_hint=(1, None), height=sv(52))
        subtitle.bind(size=subtitle.setter('text_size'))
        card.add_widget(subtitle)
        self._hint_label = Label(text='', font_size=sf(FONT_SIZES['small']), color=COLORS['green'], halign='left', valign='middle', size_hint=(1, None), height=sv(34))
        self._hint_label.bind(size=self._hint_label.setter('text_size'))
        card.add_widget(self._hint_label)
        wifi_btn = PrimaryButton(text='Set up Wi‑Fi', size_hint=(1, None), height=sv(58), font_size=sf(FONT_SIZES['medium']))
        wifi_btn.bind(on_press=self._on_wifi)
        card.add_widget(wifi_btn)
        eth_btn = SecondaryButton(text='Use wired Ethernet', size_hint=(1, None), height=sv(58), font_size=sf(FONT_SIZES['medium']))
        eth_btn.bind(on_press=self._on_ethernet)
        card.add_widget(eth_btn)
        root.add_widget(card)

        footer = BoxLayout(orientation='horizontal', size_hint=(1, None), height=sv(56), spacing=sv(12))
        back_btn = SecondaryButton(text='Back', size_hint=(None, 1), width=sv(120), font_size=sf(FONT_SIZES['medium']))
        back_btn.bind(on_press=self._on_back)
        footer.add_widget(back_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(footer)
        self.add_widget(root)

    def on_enter(self):
        if linux_ethernet_ready():
            self._hint_label.text = 'Wired connection detected — you can skip Wi‑Fi if this link has internet.'
        else:
            self._hint_label.text = 'Choose the most reliable connection for meeting capture and cloud sync.'

    def _on_back(self, *_):
        self.go_back()

    def _on_wifi(self, *_):
        self.app.setup_network_is_ethernet = False
        self.app.connected_wifi_ssid = ''
        self.goto('wifi_setup', transition='slide_left')

    def _on_ethernet(self, *_):
        async def _check():
            ok = False
            try:
                ok = await self.backend.health_check()
            except Exception as e:
                logger.warning('health check before ethernet skip: %s', e)
                ok = False

            def _done(*_a):
                if not ok:
                    self.add_widget(ModalDialog(title='Cannot reach MeetingBox', message='Check the cable, router, and backend, then try again or use Wi‑Fi.', confirm_text='OK', cancel_text=''))
                    return
                self.app.setup_network_is_ethernet = True
                self.app.connected_wifi_ssid = 'Wired Ethernet'
                self.goto('wifi_connected', transition='slide_left')
            Clock.schedule_once(_done, 0)
        run_async(_check())
