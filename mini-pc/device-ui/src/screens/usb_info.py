"""
USB / peripheral device list — reads /sys/bus/usb/devices via hardware helper.
"""

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from hardware import get_usb_devices_one_liners
from screens.base_screen import BaseScreen


class UsbInfoScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="USB devices",
                device_name="USB devices",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        self.box = GridLayout(
            cols=1, size_hint_y=None,
            spacing=self.suv(6),
            padding=[pad, self.suv(8)],
        )
        self.box.bind(minimum_height=self.box.setter("height"))
        sc.add_widget(self.box)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        def _populate(_dt):
            self.box.clear_widgets()
            devices = get_usb_devices_one_liners()
            if not devices:
                self.box.add_widget(
                    Label(
                        text="No USB devices detected\n(or /sys/bus/usb not accessible)",
                        color=COLORS["gray_500"],
                        size_hint_y=None,
                        height=self.suv(60),
                    )
                )
                return
            for dev in devices:
                self.box.add_widget(SettingsItem(title=dev[:60], subtitle="", mode="info"))

        Clock.schedule_once(_populate, 0)
