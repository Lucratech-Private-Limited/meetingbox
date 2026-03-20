"""
System Info Screen – Dark themed (480 × 320)

Displays system information with update button.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from components.button import PrimaryButton
from config import COLORS, FONT_SIZES, SPACING


class SystemScreen(BaseScreen):
    """System info – dark theme."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.system_info = {}
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='System',
            device_name='System',
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        content = BoxLayout(
            orientation='horizontal',
            padding=SPACING['screen_padding'],
            spacing=SPACING['section_spacing'],
        )

        scroll = ScrollView(size_hint=(0.65, 1), do_scroll_x=False)
        self.info_label = Label(
            text='Loading…',
            font_size=FONT_SIZES['small'],
            size_hint_y=None,
            color=COLORS['gray_400'],
            halign='left', valign='top',
        )
        self.info_label.bind(texture_size=self.info_label.setter('size'))
        self.info_label.bind(size=self.info_label.setter('text_size'))
        scroll.add_widget(self.info_label)
        content.add_widget(scroll)

        right = BoxLayout(orientation='vertical', size_hint=(0.35, 1),
                          spacing=SPACING['button_spacing'])
        from kivy.uix.widget import Widget
        right.add_widget(Widget(size_hint=(1, 0.5)))
        self.update_btn = PrimaryButton(
            text='CHECK\nUPDATES', size_hint=(1, 0.4))
        self.update_btn.bind(on_press=self._on_update)
        right.add_widget(self.update_btn)
        content.add_widget(right)

        root.add_widget(content)

        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        self._load_info()

    def _load_info(self):
        async def _load():
            try:
                info = await self.backend.get_system_info()
                self.system_info = info
                Clock.schedule_once(lambda _: self._populate(), 0)
            except Exception:
                pass
        run_async(_load())

    def _populate(self):
        if not self.system_info:
            return
        i = self.system_info
        su = i.get('storage_used', 0) / (1024**3)
        st = i.get('storage_total', 1) / (1024**3)
        sf = st - su
        up_s = i.get('uptime', 0)
        up_d = up_s // 86400
        up_h = (up_s % 86400) // 3600
        sig = i.get('wifi_signal', 0)
        bars = '▂▄▆█'[:max(1, sig // 25)]

        self.info_label.text = (
            f"Name: {i.get('device_name', '?')}\n"
            f"IP: {i.get('ip_address', '?')}\n"
            f"WiFi: {i.get('wifi_ssid', 'N/A')} {bars}\n"
            f"Storage: {su:.0f}/{st:.0f}GB ({sf:.0f}GB free)\n"
            f"Meetings: {i.get('meetings_count', 0)}\n"
            f"Firmware: {i.get('firmware_version', '?')}\n"
            f"Uptime: {up_d}d {up_h}h"
        )

    def _on_update(self, _inst):
        self.goto('update_check', transition='slide_left')
