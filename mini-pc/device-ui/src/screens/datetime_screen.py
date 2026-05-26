"""
Date & Time settings — NTP toggle (via local timedatectl) + manual set (via nsenter helper).
"""

import logging
from datetime import datetime

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from components.modal_dialog import ModalDialog
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from components.text_input_dialog import TextInputDialog
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen
from system_clock_util import try_set_ntp

logger = logging.getLogger(__name__)


class DateTimeScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Date & Time",
                device_name="Date & Time",
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

        self.clock_lbl = Label(
            text=self._now_str(),
            font_size=self.suf(FONT_SIZES.get("xlarge", 28)),
            bold=True,
            color=COLORS["white"],
            size_hint_y=None,
            height=self.suv(52),
        )
        inner.add_widget(self.clock_lbl)

        self.ntp_item = SettingsItem(
            title="Sync with internet time (NTP)",
            subtitle="Recommended — keeps clock accurate",
            mode="toggle",
            active=True,
            on_toggle=self._on_ntp_toggle,
        )
        inner.add_widget(self.ntp_item)

        self.set_time_item = SettingsItem(
            title="Set date & time manually",
            subtitle="Format: YYYY-MM-DD HH:MM:SS",
            mode="arrow",
            on_press=lambda _: self._show_set_dialog(),
        )
        inner.add_widget(self.set_time_item)

        root.add_widget(inner)
        root.add_widget(self.build_footer())
        self.add_widget(root)

        self._clock_ev = Clock.schedule_interval(self._tick_clock, 1.0)

    def _now_str(self) -> str:
        try:
            return datetime.now().strftime("%A, %B %d  %H:%M:%S")
        except Exception:
            return "--"

    def _tick_clock(self, _dt):
        if hasattr(self, "clock_lbl"):
            self.clock_lbl.text = self._now_str()

    def on_leave(self):
        if hasattr(self, "_clock_ev") and self._clock_ev:
            self._clock_ev.cancel()
            self._clock_ev = None

    def _on_ntp_toggle(self, active: bool):
        # NTP is a host-level systemd-timesyncd setting; apply via timedatectl locally.
        # The backend endpoint only handles manual time set (iso_datetime), not NTP.
        try_set_ntp(active)

    def _show_set_dialog(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.add_widget(
            TextInputDialog(
                title="Set date & time",
                message="Format: YYYY-MM-DD HH:MM:SS  (24-hour). NTP will be disabled.",
                initial_value=now,
                placeholder="2026-05-21 14:30:00",
                confirm_text="SET",
                on_confirm=self._apply_manual_time,
            )
        )

    def _apply_manual_time(self, value: str):
        value = (value or "").strip()
        if not value:
            return

        import threading
        from system_clock_util import try_set_time

        def _do():
            ok = try_set_time(value)

            def _done(_dt):
                if not ok:
                    self.add_widget(
                        ModalDialog(
                            title="Could not set time",
                            message=(
                                "timedatectl failed — confirm the meetingbox-timedatectl\n"
                                "helper is mounted and the container has been rebuilt."
                            ),
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )
                else:
                    # Disable NTP toggle visually since we just set time manually
                    self.ntp_item.toggle.active = False

            Clock.schedule_once(_done, 0)

        threading.Thread(target=_do, daemon=True).start()
