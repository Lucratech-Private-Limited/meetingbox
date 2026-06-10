"""
Security settings — PIN lock, session timeout.
PIN is stored as a SHA-256 hash + salt in device settings (never plaintext).
"""

import hashlib
import logging
import os

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from components.modal_dialog import ModalDialog
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from components.text_input_dialog import TextInputDialog
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_TIMEOUT_OPTIONS = [
    ("0", "Never"),
    ("5", "5 minutes"),
    ("15", "15 minutes"),
    ("30", "30 minutes"),
    ("60", "1 hour"),
]


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.sha256((salt + pin).encode()).hexdigest()


class SecuritySettingsScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pin_set = False
        self._build_ui()

    def _section(self, text):
        from kivy.uix.label import Label
        lbl = Label(
            text=text,
            font_size=self.suf(FONT_SIZES["small"]),
            bold=True,
            color=COLORS["gray_500"],
            halign="left",
            size_hint_y=None,
            height=self.suv(26),
        )
        lbl.bind(size=lbl.setter("text_size"))
        return lbl

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Security",
                device_name="Security",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        sc = ScrollView(do_scroll_x=False)
        box = GridLayout(
            cols=1,
            size_hint_y=None,
            spacing=self.suv(SPACING["list_item_spacing"]),
            padding=[self.suh(SPACING["screen_padding"]), self.suv(8)],
        )
        box.bind(minimum_height=box.setter("height"))

        box.add_widget(self._section("SETTINGS PIN LOCK"))
        self.pin_item = SettingsItem(
            title="Settings PIN",
            subtitle="Not set",
            mode="arrow",
            on_press=lambda _: self._show_pin_dialog(),
        )
        box.add_widget(self.pin_item)

        self.clear_pin_item = SettingsItem(
            title="Remove PIN",
            subtitle="Allow settings access without PIN",
            mode="arrow",
            on_press=lambda _: self._remove_pin(),
        )
        box.add_widget(self.clear_pin_item)

        box.add_widget(self._section("SESSION"))
        self.timeout_item = SettingsItem(
            title="Auto-logout timeout",
            subtitle="Never",
            mode="arrow",
            on_press=lambda _: self._pick_timeout(),
        )
        box.add_widget(self.timeout_item)

        box.add_widget(Widget(size_hint_y=None, height=self.suv(16)))
        sc.add_widget(box)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        async def _load():
            try:
                s = await self.backend.get_settings()

                def _apply(_dt):
                    has_pin = bool(s.get("settings_pin_hash", "").strip())
                    self._pin_set = has_pin
                    self.pin_item.subtitle_label.text = "Active — tap to change" if has_pin else "Not set"
                    self.clear_pin_item.subtitle_label.text = (
                        "Tap to remove PIN" if has_pin else "No PIN is set"
                    )
                    tm = int(s.get("session_timeout_minutes", 0))
                    label = {0: "Never", 5: "5 min", 15: "15 min", 30: "30 min", 60: "1 hour"}.get(tm, f"{tm} min")
                    self.timeout_item.subtitle_label.text = label

                Clock.schedule_once(_apply, 0)
            except Exception as e:
                logger.debug("Security load: %s", e)

        run_async(_load())

    def _show_pin_dialog(self):
        self.add_widget(
            TextInputDialog(
                title="Set PIN",
                message="Enter a numeric PIN (4–8 digits). Leave blank to cancel.",
                placeholder="e.g. 1234",
                confirm_text="SAVE",
                on_confirm=self._apply_pin,
            )
        )

    def _apply_pin(self, pin: str):
        pin = (pin or "").strip()
        if not pin:
            return
        if not pin.isdigit() or not (4 <= len(pin) <= 8):
            self.add_widget(
                ModalDialog(
                    title="Invalid PIN",
                    message="PIN must be 4–8 digits.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        salt = os.urandom(16).hex()
        pin_hash = _hash_pin(pin, salt)

        async def _save():
            try:
                await self.backend.update_settings({
                    "settings_pin_hash": pin_hash,
                    "settings_pin_salt": salt,
                })

                def _done(_dt):
                    self._pin_set = True
                    self.pin_item.subtitle_label.text = "Active — tap to change"
                    self.clear_pin_item.subtitle_label.text = "Tap to remove PIN"

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.warning("set PIN: %s", e)

        run_async(_save())

    def _remove_pin(self):
        if not self._pin_set:
            return
        self.add_widget(
            ModalDialog(
                title="Remove PIN?",
                message="Settings will be accessible without a PIN.",
                confirm_text="REMOVE",
                cancel_text="CANCEL",
                on_confirm=self._execute_remove_pin,
            )
        )

    def _execute_remove_pin(self):
        async def _save():
            try:
                await self.backend.update_settings({"settings_pin_hash": "", "settings_pin_salt": ""})

                def _done(_dt):
                    self._pin_set = False
                    self.pin_item.subtitle_label.text = "Not set"
                    self.clear_pin_item.subtitle_label.text = "No PIN is set"

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.warning("remove PIN: %s", e)

        run_async(_save())

    def _pick_timeout(self):
        from screens.picker_base import PickerBaseScreen

        outer = self

        class _TimeoutPicker(PickerBaseScreen):
            _title = "Auto-logout timeout"
            _description = "Navigate to lock screen after this inactivity period."
            _options = _TIMEOUT_OPTIONS
            _setting_key = "session_timeout_minutes"
            _default = "0"

            def _save_setting(self_inner):
                super()._save_setting()
                mm = int(self_inner._selected)
                label = {0: "Never", 5: "5 min", 15: "15 min", 30: "30 min", 60: "1 hour"}.get(mm, f"{mm} min")
                outer.timeout_item.subtitle_label.text = label

        sn = "_session_timeout_picker_inline"
        try:
            self.app.screen_manager.get_screen(sn)
        except Exception:
            self.app.screen_manager.add_widget(_TimeoutPicker(name=sn))
        self.goto(sn, transition="slide_left")
