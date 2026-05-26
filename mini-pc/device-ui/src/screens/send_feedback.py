"""
Send feedback screen — multi-line text entry, POST to /api/device/feedback.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class SendFeedbackScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Send feedback",
                device_name="Send feedback",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(10), pad, self.suv(8)],
            spacing=self.suv(8),
        )

        inner.add_widget(
            Label(
                text="Describe the issue or feature request:",
                font_size=self.suf(FONT_SIZES["small"]),
                color=COLORS["gray_400"],
                halign="left",
                size_hint_y=None,
                height=self.suv(28),
            )
        )

        self.text_in = TextInput(
            hint_text="Your feedback…",
            multiline=True,
            font_size=self.suf(FONT_SIZES["small"]),
            size_hint=(1, 1),
            background_color=COLORS["surface_light"],
            foreground_color=COLORS["white"],
            cursor_color=COLORS["white"],
        )
        inner.add_widget(self.text_in)

        btn_row = BoxLayout(size_hint=(1, None), height=self.suv(52), spacing=self.suh(10))
        cancel_btn = SecondaryButton(text="CANCEL", size_hint=(0.4, 1))
        cancel_btn.bind(on_press=lambda *_: self.go_back())
        self.send_btn = PrimaryButton(text="SEND", size_hint=(0.6, 1))
        self.send_btn.bind(on_press=lambda *_: self._send())
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(self.send_btn)
        inner.add_widget(btn_row)

        root.add_widget(inner)
        self.add_widget(root)

    def on_enter(self):
        if hasattr(self, "text_in"):
            self.text_in.text = ""

    def _send(self):
        msg = (self.text_in.text or "").strip()
        if not msg:
            self.add_widget(
                ModalDialog(
                    title="Empty message",
                    message="Please enter feedback before sending.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return

        self.send_btn.disabled = True

        async def _submit():
            try:
                await self.backend.send_feedback(msg)

                def _done(_dt):
                    self.send_btn.disabled = False
                    self.add_widget(
                        ModalDialog(
                            title="Thank you",
                            message="Your feedback has been sent.",
                            confirm_text="OK",
                            cancel_text="",
                            on_confirm=self.go_back,
                        )
                    )

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.warning("send_feedback: %s", e)

                def _err(_dt):
                    self.send_btn.disabled = False
                    self.add_widget(
                        ModalDialog(
                            title="Send failed",
                            message="Could not send feedback. Check your connection.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

                Clock.schedule_once(_err, 0)

        run_async(_submit())
