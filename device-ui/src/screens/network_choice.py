"""
Network choice — after naming the room: Wi‑Fi setup or skip to wired Ethernet.

If Linux detects an active wired connection, we hint that Wi‑Fi can be skipped.
"""

import logging
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.uix.scrollview import ScrollView

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, COLORS, FONT_SIZES
from network_util import linux_ethernet_ready
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
SCREEN_BG = (0.043, 0.051, 0.067, 1)


class NetworkChoiceScreen(BaseScreen):
    """Choose Wi‑Fi setup or proceed with wired Ethernet (skip Wi‑Fi)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._hint_label = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(
            orientation="vertical",
            padding=[24, 12, 24, 16],
            spacing=0,
            size_hint=(1, 1),
        )
        root.canvas.before.clear()
        with root.canvas.before:
            Color(*SCREEN_BG)
            self._root_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._root_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._root_bg, "size", w.size),
        )

        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=52,
            spacing=12,
        )
        if Path(LOGO_PATH).exists():
            header.add_widget(
                Image(source=LOGO_PATH, size_hint=(None, 1), width=40, fit_mode="contain")
            )
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text="MeetingBox",
            font_size=FONT_SIZES["title"],
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint_x=1,
        )
        brand.bind(size=brand.setter("text_size"))
        header.add_widget(brand)
        root.add_widget(header)

        scroll = ScrollView(do_scroll_x=False, size_hint=(1, 1), bar_width=8)
        inner = BoxLayout(
            orientation="vertical",
            spacing=0,
            size_hint_y=None,
            padding=[0, 8, 0, 8],
        )
        inner.bind(minimum_height=inner.setter("height"))

        title = Label(
            text="Connect to the internet",
            font_size=FONT_SIZES["huge"],
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=44,
        )
        title.bind(size=title.setter("text_size"))
        inner.add_widget(title)

        subtitle = Label(
            text="Use Wi‑Fi, or skip if this device already has a working wired connection.",
            font_size=FONT_SIZES["body"],
            color=COLORS["gray_400"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=52,
        )
        subtitle.bind(size=subtitle.setter("text_size"))
        inner.add_widget(subtitle)

        self._hint_label = Label(
            text="",
            font_size=FONT_SIZES["small"],
            color=COLORS["green"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=36,
        )
        self._hint_label.bind(size=self._hint_label.setter("text_size"))
        inner.add_widget(self._hint_label)

        inner.add_widget(Widget(size_hint=(1, None), height=16))

        wifi_btn = PrimaryButton(
            text="Set up Wi‑Fi",
            size_hint=(1, None),
            height=52,
            font_size=FONT_SIZES["medium"],
        )
        wifi_btn.bind(on_press=self._on_wifi)
        inner.add_widget(wifi_btn)

        inner.add_widget(Widget(size_hint=(1, None), height=12))

        eth_btn = SecondaryButton(
            text="Use wired Ethernet (skip Wi‑Fi)",
            size_hint=(1, None),
            height=52,
            font_size=FONT_SIZES["medium"],
        )
        eth_btn.bind(on_press=self._on_ethernet)
        inner.add_widget(eth_btn)

        scroll.add_widget(inner)
        root.add_widget(scroll)

        footer = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=52,
            spacing=12,
        )
        back_btn = SecondaryButton(
            text="Back",
            size_hint=(None, 1),
            width=120,
            font_size=FONT_SIZES["medium"],
        )
        back_btn.bind(on_press=self._on_back)
        footer.add_widget(back_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        if linux_ethernet_ready():
            self._hint_label.text = (
                "Wired connection detected — you can skip Wi‑Fi if this link has internet."
            )
        else:
            self._hint_label.text = ""

    def _on_back(self, *_):
        self.go_back()

    def _on_wifi(self, *_):
        self.app.setup_network_is_ethernet = False
        self.app.connected_wifi_ssid = ""
        self.goto("wifi_setup", transition="slide_left")

    def _on_ethernet(self, *_):
        async def _check():
            ok = False
            try:
                ok = await self.backend.health_check()
            except Exception as e:
                logger.warning("health check before ethernet skip: %s", e)
                ok = False

            def _done(*_a):
                if not ok:
                    self.add_widget(
                        ModalDialog(
                            title="Cannot reach MeetingBox",
                            message=(
                                "The app could not reach the backend. "
                                "Check the cable, router, and that the server is running, "
                                "then try again or use Wi‑Fi."
                            ),
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )
                    return
                self.app.setup_network_is_ethernet = True
                self.app.connected_wifi_ssid = "Wired Ethernet"
                self.goto("wifi_connected", transition="slide_left")

            Clock.schedule_once(_done, 0)

        run_async(_check())
