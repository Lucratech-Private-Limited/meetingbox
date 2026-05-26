"""
Diagnostic log viewer — last N lines from journalctl via backend API.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from async_helper import run_async
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class DiagnosticLogsScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Diagnostic logs",
                device_name="Diagnostic logs",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        self.log_lbl = Label(
            text="Loading…",
            font_size=self.suf(10),
            color=COLORS["gray_300"],
            halign="left",
            valign="top",
            size_hint_y=None,
            padding=[pad, self.suv(8)],
            markup=False,
        )
        self.log_lbl.bind(texture_size=self.log_lbl.setter("size"))
        self.log_lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        sc.add_widget(self.log_lbl)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        async def _fetch():
            try:
                data = await self.backend.diagnostic_log(lines=120)
                text = data.get("lines", "(no log output)")

                def _apply(_dt):
                    self.log_lbl.text = text or "(empty)"

                Clock.schedule_once(_apply, 0)
            except Exception as e:
                logger.debug("Diag log: %s", e)
                Clock.schedule_once(
                    lambda *_: setattr(
                        self.log_lbl, "text",
                        "Could not retrieve logs.\n(journalctl may not be available here)"
                    ),
                    0,
                )

        run_async(_fetch())
