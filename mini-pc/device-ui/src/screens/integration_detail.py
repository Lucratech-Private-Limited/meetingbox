"""
Integration detail screen — shows account email, last sync, disconnect + sync buttons.
Receives ``integration_id`` ('gmail' or 'calendar') set before navigation.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import DangerButton, PrimaryButton
from components.modal_dialog import ModalDialog
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING, DASHBOARD_URL
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class IntegrationDetailScreen(BaseScreen):
    """Set ``integration_id`` attribute before navigating here."""

    integration_id: str = "gmail"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        self.status_bar = StatusBar(
            status_text="Integration",
            device_name="Integration",
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(12), pad, self.suv(8)],
            spacing=self.suv(10),
        )

        self.status_item = SettingsItem(title="Status", subtitle="Loading…", mode="info")
        self.email_item = SettingsItem(title="Account", subtitle="—", mode="info")
        self.sync_item = SettingsItem(title="Last connected", subtitle="—", mode="info")
        for w in (self.status_item, self.email_item, self.sync_item):
            inner.add_widget(w)

        inner.add_widget(Widget(size_hint_y=None, height=self.suv(12)))

        self.sync_btn = PrimaryButton(
            text="Re-sync", size_hint=(1, None), height=self.suv(50), disabled=True, opacity=0.5
        )
        self.sync_btn.bind(on_press=lambda *_: self._manual_sync())
        inner.add_widget(self.sync_btn)

        self.disconnect_btn = DangerButton(
            text="Disconnect", size_hint=(1, None), height=self.suv(50), disabled=True, opacity=0.5
        )
        self.disconnect_btn.bind(on_press=lambda *_: self._confirm_disconnect())
        inner.add_widget(self.disconnect_btn)

        self.connect_hint = Label(
            text=f"To connect, visit {DASHBOARD_URL}",
            font_size=self.suf(FONT_SIZES["small"]),
            color=COLORS["gray_500"],
            halign="left",
            size_hint_y=None,
            height=self.suv(32),
        )
        self.connect_hint.bind(size=self.connect_hint.setter("text_size"))
        inner.add_widget(self.connect_hint)

        root.add_widget(inner)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        iid = getattr(self, "integration_id", "gmail")
        name = {"gmail": "Gmail", "calendar": "Google Calendar"}.get(iid, iid.title())
        self.status_bar.device_name = name

        async def _fetch():
            integrations = []
            try:
                integrations = await self.backend.get_integrations()
            except Exception as e:
                logger.debug("integration_detail load: %s", e)

            match = None
            for integ in integrations:
                i = (integ.get("id") or integ.get("name") or "").lower()
                if iid in i:
                    match = integ
                    break

            def _apply(_dt):
                if not match:
                    self.status_item.subtitle_label.text = "Not connected"
                    self.email_item.subtitle_label.text = "—"
                    self.sync_item.subtitle_label.text = "—"
                    self.sync_btn.disabled = True
                    self.sync_btn.opacity = 0.5
                    self.disconnect_btn.disabled = True
                    self.disconnect_btn.opacity = 0.5
                    return

                connected = bool(match.get("connected"))
                self.status_item.subtitle_label.text = "Connected" if connected else "Not connected"
                self.email_item.subtitle_label.text = match.get("email") or "—"
                last = match.get("last_sync") or "—"
                self.sync_item.subtitle_label.text = last
                active = connected
                self.sync_btn.disabled = not active
                self.sync_btn.opacity = 1.0 if active else 0.5
                self.disconnect_btn.disabled = not active
                self.disconnect_btn.opacity = 1.0 if active else 0.5

            Clock.schedule_once(_apply, 0)

        run_async(_fetch())

    def _manual_sync(self):
        iid = getattr(self, "integration_id", "gmail")

        async def _sync():
            try:
                result = await self.backend.integration_sync(iid)
                ts = result.get("synced_at", "")

                def _done(_dt):
                    self.sync_item.subtitle_label.text = ts or "Just now"
                    self.add_widget(
                        ModalDialog(
                            title="Synced",
                            message="Re-sync requested.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.warning("integration sync: %s", e)

        run_async(_sync())

    def _confirm_disconnect(self):
        iid = getattr(self, "integration_id", "gmail")
        name = {"gmail": "Gmail", "calendar": "Google Calendar"}.get(iid, iid.title())
        self.add_widget(
            ModalDialog(
                title=f"Disconnect {name}?",
                message="OAuth tokens will be removed. Reconnect from the web dashboard.",
                confirm_text="DISCONNECT",
                cancel_text="CANCEL",
                danger=True,
                on_confirm=self._execute_disconnect,
            )
        )

    def _execute_disconnect(self):
        iid = getattr(self, "integration_id", "gmail")

        async def _disc():
            try:
                await self.backend.disconnect_integration(iid)
                Clock.schedule_once(lambda *_: self.go_back(), 0)
            except Exception as e:
                logger.warning("disconnect integration: %s", e)

        run_async(_disc())
