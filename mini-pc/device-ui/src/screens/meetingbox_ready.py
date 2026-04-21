"""
MeetingBox is ready — final onboarding summary (Frame 14 ref).

Shows room name, language, Wi‑Fi; writes `.setup_complete` and notifies API.
"""

import logging
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, BORDER_RADIUS, COLORS, FONT_SIZES
from screens.base_screen import BaseScreen
from setup_finalize import post_setup_complete_safe, write_local_setup_complete_marker

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
SCREEN_BG = (0.043, 0.051, 0.067, 1)


class MeetingBoxReadyScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._room_value = None
        self._wifi_value = None
        self._lang_value = None
        self._start_btn = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=[20, 10, 20, 14], spacing=0)
        with root.canvas.before:
            Color(*SCREEN_BG)
            self._bg = RoundedRectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._bg, "size", w.size),
        )

        header = BoxLayout(orientation="horizontal", size_hint=(1, None), height=48, spacing=10)
        if Path(LOGO_PATH).exists():
            header.add_widget(
                Image(source=LOGO_PATH, size_hint=(None, 1), width=36, fit_mode="contain")
            )
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text="MeetingBox",
            font_size=self.suf(FONT_SIZES["title"]),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint_x=1,
        )
        brand.bind(size=brand.setter("text_size"))
        header.add_widget(brand)
        root.add_widget(header)

        root.add_widget(Widget(size_hint=(1, None), height=8))
        root.add_widget(self._build_check_icon())
        root.add_widget(Widget(size_hint=(1, None), height=12))

        title = Label(
            text="MeetingBox is ready.",
            font_size=self.suf(FONT_SIZES["huge"]),
            bold=True,
            color=COLORS["white"],
            halign="center",
            size_hint=(1, None),
            height=40,
        )
        title.bind(size=title.setter("text_size"))
        root.add_widget(title)

        root.add_widget(Widget(size_hint=(1, None), height=14))
        root.add_widget(self._build_summary_card())
        root.add_widget(Widget(size_hint=(1, 1)))

        foot = BoxLayout(orientation="horizontal", size_hint=(1, None), height=54, spacing=12)
        back_btn = SecondaryButton(
            text="Back",
            size_hint=(None, 1),
            width=100,
            font_size=self.suf(FONT_SIZES["medium"]),
        )
        back_btn.bind(on_press=lambda *_: self.go_back())
        foot.add_widget(back_btn)
        foot.add_widget(Widget(size_hint=(1, 1)))
        self._start_btn = PrimaryButton(
            text="Get started",
            size_hint=(None, 1),
            width=220,
            font_size=self.suf(FONT_SIZES["medium"]),
        )
        self._start_btn.bind(on_press=self._on_get_started)
        foot.add_widget(self._start_btn)
        foot.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(foot)

        self.add_widget(root)

    def _build_check_icon(self):
        holder = Widget(size_hint=(1, None), height=108)
        with holder.canvas:
            Color(*COLORS["primary_start"])
            self._glow = Ellipse(size=(88, 88), pos=(0, 0))
        self._check = Label(
            text="✓",
            font_size=self.suf(44),
            bold=True,
            color=COLORS["white"],
            size_hint=(None, None),
            size=(88, 88),
        )
        holder.add_widget(self._check)

        def _pos(*_a):
            cx = holder.center_x
            cy = holder.center_y
            self._glow.pos = (cx - 44, cy - 44)
            self._glow.size = (88, 88)
            self._check.center = (cx, cy)

        holder.bind(pos=_pos, size=_pos)
        return holder

    def _summary_row(self, label: str, value_widget: Label):
        row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=44)
        lb = Label(
            text=label,
            font_size=self.suf(FONT_SIZES["small"]),
            color=COLORS["gray_500"],
            halign="left",
            valign="middle",
            size_hint=(0.42, 1),
        )
        lb.bind(size=lb.setter("text_size"))
        row.add_widget(lb)
        value_widget.font_size = FONT_SIZES["medium"]
        value_widget.color = COLORS["white"]
        value_widget.bold = True
        value_widget.halign = "right"
        value_widget.valign = "middle"
        value_widget.size_hint = (0.58, 1)
        value_widget.bind(size=value_widget.setter("text_size"))
        row.add_widget(value_widget)
        return row

    def _build_summary_card(self):
        card = BoxLayout(
            orientation="vertical",
            size_hint=(0.92, None),
            height=220,
            pos_hint={"center_x": 0.5},
            padding=[16, 12],
            spacing=0,
        )
        with card.canvas.before:
            Color(*COLORS["surface"])
            self._card_bg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS]
            )
            Color(*COLORS["border"])
            self._card_border = Line(
                rounded_rectangle=(card.x, card.y, card.width, card.height, BORDER_RADIUS),
                width=1,
            )
        with card.canvas.after:
            Color(*COLORS["gray_800"])
            self._div1 = Line(points=[0, 0, 0, 0], width=1)
            self._div2 = Line(points=[0, 0, 0, 0], width=1)
            self._div3 = Line(points=[0, 0, 0, 0], width=1)

        def _decor(*_a):
            self._card_bg.pos = card.pos
            self._card_bg.size = card.size
            self._card_border.rounded_rectangle = (
                card.x,
                card.y,
                card.width,
                card.height,
                BORDER_RADIUS,
            )
            y1 = card.y + card.height - 56
            y2 = y1 - 44
            y3 = y2 - 44
            x0, x1 = card.x + 14, card.right - 14
            self._div1.points = [x0, y1, x1, y1]
            self._div2.points = [x0, y2, x1, y2]
            self._div3.points = [x0, y3, x1, y3]

        card.bind(pos=_decor, size=_decor)

        self._room_value = Label(text="—")
        self._account_value = Label(text="—")
        self._lang_value = Label(text="🌐 English (US)")
        self._wifi_value = Label(text="📶 —")

        card.add_widget(self._summary_row("Room Name", self._room_value))
        card.add_widget(self._summary_row("Google account", self._account_value))
        card.add_widget(self._summary_row("Language", self._lang_value))
        card.add_widget(self._summary_row("WiFi", self._wifi_value))
        return card

    def on_enter(self):
        room = getattr(self.app, "device_name", "MeetingBox") or "MeetingBox"
        acct = getattr(self.app, "paired_owner_email", "") or ""
        lang = getattr(self.app, "setup_language", "English (US)") or "English (US)"
        wifi = getattr(self.app, "connected_wifi_ssid", "") or "—"
        if self._room_value:
            self._room_value.text = room
        if self._account_value:
            self._account_value.text = acct if acct else "—"
        if self._lang_value:
            self._lang_value.text = f"🌐 {lang}"
        if self._wifi_value:
            self._wifi_value.text = f"📶 {wifi}"

    def _on_get_started(self, _inst):
        if self._start_btn:
            self._start_btn.disabled = True

        async def _run():
            flow = "wifi_on_device_v1"
            wifi = getattr(self.app, "connected_wifi_ssid", "") or ""
            device_name = getattr(self.app, "device_name", "MeetingBox") or "MeetingBox"
            lang = getattr(self.app, "setup_language", "English (US)") or "English (US)"

            api_ok = await post_setup_complete_safe(self.backend, wifi, flow)
            extra = {"language": lang}
            local_ok = write_local_setup_complete_marker(
                wifi, device_name, flow, extra=extra
            )

            def _done(*_a):
                if self._start_btn:
                    self._start_btn.disabled = False
                if api_ok or local_ok:
                    poll = getattr(self.app, "_setup_poll", None)
                    if poll:
                        poll.cancel()
                        self.app._setup_poll = None
                    self.goto("home", transition="fade")
                else:
                    self.add_widget(
                        ModalDialog(
                            title="Could not finish setup",
                            message=(
                                "Setup could not be saved. Check the web service "
                                "and storage, then try again."
                            ),
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

            Clock.schedule_once(_done, 0)

        run_async(_run())
