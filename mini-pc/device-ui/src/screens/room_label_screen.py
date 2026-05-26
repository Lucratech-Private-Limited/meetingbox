"""
Room label / location editor — full-screen TextInputDialog-style.
Saves ``room_label`` to the backend settings.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class RoomLabelScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Room / Location",
                device_name="Room / Location",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(16), pad, self.suv(8)],
            spacing=self.suv(8),
        )
        inner.add_widget(
            Label(
                text="Enter the room or location name for this device.\nShown on the home screen.",
                font_size=self.suf(FONT_SIZES["small"]),
                color=COLORS["gray_400"],
                halign="left",
                size_hint_y=None,
                height=self.suv(50),
            )
        )
        self.text_in = TextInput(
            hint_text="e.g. Boardroom, Desk 12, London Office",
            multiline=False,
            font_size=self.suf(FONT_SIZES["medium"]),
            size_hint_y=None,
            height=self.suv(48),
            background_color=COLORS["surface_light"],
            foreground_color=COLORS["white"],
            cursor_color=COLORS["white"],
            padding=[10, 10, 10, 10],
        )
        self.text_in.bind(on_text_validate=lambda *_: self._save())
        inner.add_widget(self.text_in)
        inner.add_widget(Widget(size_hint_y=None, height=self.suv(8)))

        btn_row = BoxLayout(size_hint=(1, None), height=self.suv(50), spacing=self.suh(10))
        cancel = SecondaryButton(text="CANCEL", size_hint=(0.4, 1))
        cancel.bind(on_press=lambda *_: self.go_back())
        save = PrimaryButton(text="SAVE", size_hint=(0.6, 1))
        save.bind(on_press=lambda *_: self._save())
        btn_row.add_widget(cancel)
        btn_row.add_widget(save)
        inner.add_widget(btn_row)
        root.add_widget(inner)
        self.add_widget(root)

    def on_enter(self):
        async def _load():
            try:
                s = await self.backend.get_settings()

                def _apply(_dt):
                    self.text_in.text = s.get("room_label", "") or ""
                    Clock.schedule_once(lambda *_: setattr(self.text_in, "focus", True), 0.1)

                Clock.schedule_once(_apply, 0)
            except Exception as e:
                logger.debug("room_label load: %s", e)

        run_async(_load())

    def _save(self):
        val = (self.text_in.text or "").strip()

        async def _update():
            try:
                await self.backend.update_settings({"room_label": val})
                Clock.schedule_once(lambda *_: self.go_back(), 0)
            except Exception as e:
                logger.warning("room_label save: %s", e)

        run_async(_update())
