"""
Server connectivity check — pings backend/internet and reports latency.
Same pattern as update_check.py.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from platform_compat import TAP_OR_CLICK
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class ConnectivityCheckScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Connectivity check",
                device_name="Connectivity check",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(12), pad, self.suv(8)],
            spacing=self.suv(10),
        )

        self.status_lbl = Label(
            text=f"{TAP_OR_CLICK} CHECK to run a connectivity test.",
            font_size=self.suf(FONT_SIZES["small"]),
            color=COLORS["gray_400"],
            halign="left",
            size_hint_y=None,
            height=self.suv(34),
        )
        self.status_lbl.bind(size=self.status_lbl.setter("text_size"))
        inner.add_widget(self.status_lbl)

        self.backend_item = SettingsItem(title="Backend server", subtitle="—", mode="info")
        self.internet_item = SettingsItem(title="Internet (google.com)", subtitle="—", mode="info")
        inner.add_widget(self.backend_item)
        inner.add_widget(self.internet_item)
        inner.add_widget(Widget(size_hint_y=None, height=self.suv(12)))

        self.check_btn = PrimaryButton(
            text="CHECK", size_hint=(1, None), height=self.suv(50)
        )
        self.check_btn.bind(on_press=lambda *_: self._run_check())
        inner.add_widget(self.check_btn)

        root.add_widget(inner)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def _run_check(self):
        self.check_btn.disabled = True
        self.status_lbl.text = "Checking…"
        self.backend_item.subtitle_label.text = "…"
        self.internet_item.subtitle_label.text = "…"

        async def _probe():
            try:
                result = await self.backend.connectivity_check()
                backend_ok = result.get("backend_ok", False)
                backend_ms = result.get("backend_ms", None)
                internet_ok = result.get("internet_ok", False)
                internet_ms = result.get("internet_ms", None)

                def _done(_dt):
                    self.check_btn.disabled = False
                    self.status_lbl.text = "Test complete."
                    if backend_ok:
                        self.backend_item.subtitle_label.text = (
                            f"OK — {backend_ms} ms" if backend_ms is not None else "OK"
                        )
                    else:
                        self.backend_item.subtitle_label.text = "Unreachable"

                    if internet_ok:
                        self.internet_item.subtitle_label.text = (
                            f"OK — {internet_ms} ms" if internet_ms is not None else "OK"
                        )
                    else:
                        self.internet_item.subtitle_label.text = "Unreachable"

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.debug("connectivity_check: %s", e)

                def _err(_dt):
                    self.check_btn.disabled = False
                    self.status_lbl.text = "Check failed — could not reach backend."
                    self.backend_item.subtitle_label.text = "Error"
                    self.internet_item.subtitle_label.text = "—"

                Clock.schedule_once(_err, 0)

        run_async(_probe())
