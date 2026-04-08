"""
WiFi Connected confirmation screen (Frame 12 reference).

Shown after WiFi connection succeeds. Displays local IP + access URL and
continues to Create profile when the user taps "Continue setup".
"""

import logging

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton
from config import (
    ASSETS_DIR,
    BORDER_RADIUS,
    COLORS,
    DASHBOARD_PUBLIC_URL,
    FONT_SIZES,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
WIFI_BG = (0.043, 0.051, 0.067, 1)


class WiFiConnectedScreen(BaseScreen):
    """Success screen shown after WiFi connects; leads to Create profile."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wifi_ssid = ""
        self._ip_address = "Loading..."
        self._continue_btn = None
        self._ip_value = None
        self._url_value = None
        self._title_lbl = None
        self._subtitle_lbl = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=[20, 10, 20, 14], spacing=0)
        with root.canvas.before:
            Color(*WIFI_BG)
            self._bg = RoundedRectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._bg, "size", w.size),
        )

        header = BoxLayout(orientation="horizontal", size_hint=(1, None), height=52, spacing=12)
        if LOGO_PATH and Image and ASSETS_DIR:
            try:
                header.add_widget(
                    Image(source=LOGO_PATH, size_hint=(None, 1), width=40, fit_mode="contain")
                )
            except Exception:
                header.add_widget(Widget(size_hint=(None, 1), width=8))
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

        root.add_widget(Widget(size_hint=(1, None), height=12))
        root.add_widget(self._build_success_icon())
        root.add_widget(Widget(size_hint=(1, None), height=14))

        self._title_lbl = Label(
            text="You're connected",
            font_size=FONT_SIZES["huge"],
            bold=True,
            color=COLORS["white"],
            halign="center",
            size_hint=(1, None),
            height=42,
        )
        self._title_lbl.bind(size=self._title_lbl.setter("text_size"))
        root.add_widget(self._title_lbl)

        self._subtitle_lbl = Label(
            text="Your MeetingBox is now connected and ready to use.",
            font_size=FONT_SIZES["body"],
            color=COLORS["gray_400"],
            halign="center",
            size_hint=(1, None),
            height=28,
        )
        self._subtitle_lbl.bind(size=self._subtitle_lbl.setter("text_size"))
        root.add_widget(self._subtitle_lbl)

        root.add_widget(Widget(size_hint=(1, None), height=16))
        root.add_widget(self._build_info_card())
        root.add_widget(Widget(size_hint=(1, 1)))

        footer = BoxLayout(orientation="horizontal", size_hint=(1, None), height=56, spacing=12)
        footer.add_widget(Widget(size_hint=(1, 1)))
        self._continue_btn = PrimaryButton(
            text="Continue setup",
            size_hint=(None, 1),
            width=230,
            font_size=FONT_SIZES["medium"],
        )
        self._continue_btn.bind(on_press=self._on_continue)
        footer.add_widget(self._continue_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(footer)

        self.add_widget(root)

    def _build_success_icon(self):
        holder = Widget(size_hint=(1, None), height=118)
        with holder.canvas:
            Color(0.13, 0.78, 0.38, 0.16)
            self._glow = Ellipse(size=(92, 92))
            Color(0.20, 0.78, 0.35, 1)
            self._ring = Line(circle=(0, 0, 31), width=3.0)
        self._icon = Label(
            text="📶✓",
            color=COLORS["green"],
            font_size=26,
            bold=True,
            size_hint=(None, None),
            size=(92, 92),
        )
        holder.add_widget(self._icon)

        def _pos(*_args):
            cx = holder.center_x
            cy = holder.center_y
            self._glow.pos = (cx - 46, cy - 46)
            self._ring.circle = (cx, cy, 31)
            self._icon.center = (cx, cy)

        holder.bind(pos=_pos, size=_pos)
        return holder

    def _build_info_card(self):
        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(660, 128),
            pos_hint={"center_x": 0.5},
            padding=[18, 10],
            spacing=8,
        )
        with card.canvas.before:
            Color(*COLORS["surface"])
            self._card_bg = RoundedRectangle(pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
            Color(*COLORS["border"])
            self._card_border = Line(rounded_rectangle=(card.x, card.y, card.width, card.height, BORDER_RADIUS), width=1)
        with card.canvas.after:
            Color(*COLORS["gray_800"])
            self._divider = Line(points=[0, 0, 0, 0], width=1)
        card.bind(pos=self._update_card_decor, size=self._update_card_decor)

        row1 = BoxLayout(orientation="horizontal", size_hint=(1, None), height=42)
        l1 = Label(
            text="Local IP Address",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_500"],
            halign="left",
            valign="middle",
            size_hint=(0.55, 1),
        )
        l1.bind(size=l1.setter("text_size"))
        self._ip_value = Label(
            text=self._ip_address,
            font_size=FONT_SIZES["medium"],
            color=COLORS["white"],
            halign="right",
            valign="middle",
            size_hint=(0.45, 1),
        )
        self._ip_value.bind(size=self._ip_value.setter("text_size"))
        row1.add_widget(l1)
        row1.add_widget(self._ip_value)
        card.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", size_hint=(1, None), height=42)
        l2 = Label(
            text="Access URL",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_500"],
            halign="left",
            valign="middle",
            size_hint=(0.55, 1),
        )
        l2.bind(size=l2.setter("text_size"))
        self._url_value = Label(
            text=DASHBOARD_PUBLIC_URL,
            font_size=FONT_SIZES["medium"],
            color=COLORS["blue"],
            halign="right",
            valign="middle",
            size_hint=(0.45, 1),
        )
        self._url_value.bind(size=self._url_value.setter("text_size"))
        row2.add_widget(l2)
        row2.add_widget(self._url_value)
        card.add_widget(row2)
        return card

    def _update_card_decor(self, card, *_args):
        self._card_bg.pos = card.pos
        self._card_bg.size = card.size
        self._card_border.rounded_rectangle = (card.x, card.y, card.width, card.height, BORDER_RADIUS)
        mid_y = card.y + card.height / 2.0
        self._divider.points = [card.x + 14, mid_y, card.right - 14, mid_y]

    def on_enter(self):
        self._wifi_ssid = getattr(self.app, "connected_wifi_ssid", "") or ""
        if self._wifi_ssid == "Wired Ethernet" or getattr(
            self.app, "setup_network_is_ethernet", False
        ):
            if self._title_lbl is not None:
                self._title_lbl.text = "You're connected"
            if self._subtitle_lbl is not None:
                self._subtitle_lbl.text = (
                    "Using wired Ethernet. Continue when this device can reach the network."
                )
            if self._icon is not None:
                self._icon.text = "🔌✓"
        else:
            if self._title_lbl is not None:
                self._title_lbl.text = "You're connected"
            if self._subtitle_lbl is not None:
                self._subtitle_lbl.text = (
                    "Your MeetingBox is now connected and ready to use."
                )
            if self._icon is not None:
                self._icon.text = "📶✓"
        self._ip_address = "Loading..."
        if self._ip_value is not None:
            self._ip_value.text = self._ip_address
        self._load_ip_info()

    def _load_ip_info(self):
        async def _run():
            ip = ""
            try:
                info = await self.backend.get_system_info()
                ip = (info.get("ip_address") or "").strip()
            except Exception as e:
                logger.warning("Could not fetch system info on WiFi success page: %s", e)

            def _apply(*_a):
                self._ip_address = ip or "Not available"
                if self._ip_value is not None:
                    self._ip_value.text = self._ip_address

            Clock.schedule_once(_apply, 0)

        run_async(_run())

    def _on_continue(self, _inst):
        self.goto("pair_device", transition="slide_left")
