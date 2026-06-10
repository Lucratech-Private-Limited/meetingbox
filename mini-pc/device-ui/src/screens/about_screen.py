"""
About screen — firmware build info, open-source license notices, support contact.
"""

import os

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, DASHBOARD_URL
from screens.base_screen import BaseScreen

_FIRMWARE = os.getenv("FIRMWARE_VERSION", "1.0.0")

_LICENSES = """
MeetingBox Device UI

Open-source components used in this software:

• Python 3.11 — PSF License
• Kivy 2.x — MIT License
• espeak-ng — GPL v3
• PulseAudio — LGPL v2.1
• NetworkManager / nmcli — GPL v2
• httpx — BSD License
• websockets — BSD License
• sounddevice — MIT License
• numpy — BSD License

For full license texts see: https://meetingbox.com/licenses
""".strip()


class AboutScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="About",
                device_name="About",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        body = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[pad, self.suv(8), pad, self.suv(12)],
            spacing=self.suv(6),
        )
        body.bind(minimum_height=body.setter("height"))

        def lbl(text, bold=False, color=None, size=None):
            l = Label(
                text=text,
                font_size=self.suf(size or FONT_SIZES["small"]),
                bold=bold,
                color=color or COLORS["white"],
                halign="left",
                size_hint_y=None,
            )
            l.bind(texture_size=l.setter("size"))
            l.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            return l

        body.add_widget(lbl("MeetingBox", bold=True, size=FONT_SIZES.get("large", 18)))
        body.add_widget(lbl(f"Firmware {_FIRMWARE}", color=COLORS["gray_300"]))
        body.add_widget(lbl(f"Dashboard: {DASHBOARD_URL}", color=COLORS["gray_400"]))
        body.add_widget(lbl("Support: support.meetingbox.com", color=COLORS["gray_400"]))
        body.add_widget(lbl(""))
        body.add_widget(lbl("Open-source licenses", bold=True))
        body.add_widget(lbl(_LICENSES, color=COLORS["gray_400"], size=10))

        sc.add_widget(body)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)
