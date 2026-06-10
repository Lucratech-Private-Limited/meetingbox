"""
Notifications preferences — master toggle, reminders, DND, per-category.
All values are saved to /api/device/settings via PATCH.
"""

import logging

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.uix.widget import Widget
from kivy.clock import Clock

from async_helper import run_async
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_REMINDER_OPTIONS = [
    ("0", "At the time"),
    ("5", "5 minutes before"),
    ("10", "10 minutes before"),
    ("15", "15 minutes before"),
    ("30", "30 minutes before"),
]


class NotificationsSettingsScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
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
                status_text="Notifications",
                device_name="Notifications",
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

        box.add_widget(self._section("GENERAL"))
        self.notif_enabled = SettingsItem(
            title="Notifications",
            subtitle="Master on/off for all alerts",
            mode="toggle", active=True,
            on_toggle=lambda v: self._save("notification_enabled", v),
        )
        box.add_widget(self.notif_enabled)

        box.add_widget(self._section("MEETING REMINDERS"))
        self.reminder_item = SettingsItem(
            title="Meeting reminders",
            subtitle="10 minutes before",
            mode="arrow",
            on_press=lambda _: self._pick_reminder(),
        )
        box.add_widget(self.reminder_item)

        box.add_widget(self._section("DO NOT DISTURB"))
        self.dnd_item = SettingsItem(
            title="Do Not Disturb",
            subtitle="Silence all alerts",
            mode="toggle", active=False,
            on_toggle=lambda v: self._save("dnd_enabled", v),
        )
        box.add_widget(self.dnd_item)

        self.dnd_start_item = SettingsItem(
            title="DND start",
            subtitle="22:00",
            mode="arrow",
            on_press=lambda _: self._edit_time("dnd_start", "DND start time", self.dnd_start_item),
        )
        box.add_widget(self.dnd_start_item)

        self.dnd_end_item = SettingsItem(
            title="DND end",
            subtitle="07:00",
            mode="arrow",
            on_press=lambda _: self._edit_time("dnd_end", "DND end time", self.dnd_end_item),
        )
        box.add_widget(self.dnd_end_item)

        box.add_widget(self._section("CATEGORIES"))
        self.email_notif = SettingsItem(
            title="Email alerts",
            subtitle="",
            mode="toggle", active=True,
            on_toggle=lambda v: self._save("email_notifications_enabled", v),
        )
        box.add_widget(self.email_notif)

        self.assistant_notif = SettingsItem(
            title="Assistant alerts",
            subtitle="",
            mode="toggle", active=True,
            on_toggle=lambda v: self._save("assistant_notifications_enabled", v),
        )
        box.add_widget(self.assistant_notif)

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
                    self.notif_enabled.toggle.active = bool(s.get("notification_enabled", True))
                    self.dnd_item.toggle.active = bool(s.get("dnd_enabled", False))
                    self.email_notif.toggle.active = bool(s.get("email_notifications_enabled", True))
                    self.assistant_notif.toggle.active = bool(s.get("assistant_notifications_enabled", True))
                    self.dnd_start_item.subtitle_label.text = str(s.get("dnd_start", "22:00"))
                    self.dnd_end_item.subtitle_label.text = str(s.get("dnd_end", "07:00"))
                    mm = int(s.get("meeting_reminder_minutes", 10))
                    label = {0: "At the time", 5: "5 min before", 10: "10 min before",
                             15: "15 min before", 30: "30 min before"}.get(mm, f"{mm} min before")
                    self.reminder_item.subtitle_label.text = label

                Clock.schedule_once(_apply, 0)
            except Exception as e:
                logger.debug("Notifications load: %s", e)

        run_async(_load())

    def _save(self, key: str, value):
        async def _s():
            try:
                await self.backend.update_settings({key: value})
            except Exception:
                pass

        run_async(_s())

    def _pick_reminder(self):
        from screens.picker_base import PickerBaseScreen

        class _ReminderPicker(PickerBaseScreen):
            _title = "Meeting reminder"
            _description = "How long before a meeting to show a reminder."
            _options = _REMINDER_OPTIONS
            _setting_key = "meeting_reminder_minutes"
            _default = "10"

            def _save_setting(self_inner):
                super()._save_setting()
                minutes = int(self_inner._selected)
                label = {0: "At the time", 5: "5 min before", 10: "10 min before",
                         15: "15 min before", 30: "30 min before"}.get(minutes, f"{minutes} min")
                self.reminder_item.subtitle_label.text = label

        screen_name = "_reminder_picker_inline"
        try:
            self.app.screen_manager.get_screen(screen_name)
        except Exception:
            self.app.screen_manager.add_widget(_ReminderPicker(name=screen_name))
        self.goto(screen_name, transition="slide_left")

    def _edit_time(self, key: str, title: str, item: SettingsItem):
        from components.text_input_dialog import TextInputDialog
        self.add_widget(
            TextInputDialog(
                title=title,
                message="Format: HH:MM (24-hour)",
                initial_value=item.subtitle_label.text,
                placeholder="22:00",
                confirm_text="SAVE",
                on_confirm=lambda v: self._apply_time(key, v, item),
            )
        )

    def _apply_time(self, key: str, value: str, item: SettingsItem):
        t = (value or "").strip()
        if not t:
            return
        item.subtitle_label.text = t
        self._save(key, t)
