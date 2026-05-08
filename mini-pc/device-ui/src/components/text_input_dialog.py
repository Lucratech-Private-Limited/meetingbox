"""
TextInputDialog Component

A modal dialog with one Kivy TextInput row plus Cancel / Save buttons.
Designed to mirror :class:`components.modal_dialog.ModalDialog` visually
so users see a consistent style across all popups.

Used for short single-line input flows that don't justify a full screen
(e.g. weather location, future device-name override, etc.).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from components.button import PrimaryButton, SecondaryButton
from config import BORDER_RADIUS, COLORS, FONT_SIZES

logger = logging.getLogger(__name__)


class TextInputDialog(FloatLayout):
    """Modal dialog with a single TextInput row + Cancel / Save buttons.

    Parameters
    ----------
    title : str
        Bold title above the message.
    message : str
        Short helper paragraph between the title and the input.
    initial_value : str
        Pre-fills the TextInput.
    placeholder : str
        Hint shown when the input is empty.
    on_confirm : Callable[[str], None] | None
        Called with the trimmed/raw input text on Save. Cancel and tap-out
        both dismiss without firing the callback.
    """

    def __init__(
        self,
        title: str = "",
        message: str = "",
        initial_value: str = "",
        placeholder: str = "",
        confirm_text: str = "SAVE",
        cancel_text: str = "CANCEL",
        on_confirm: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_confirm = on_confirm

        with self.canvas.before:
            Color(*COLORS["overlay"])
            self._overlay_bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda w, _v: setattr(self._overlay_bg, "pos", w.pos),
            size=lambda w, _v: setattr(self._overlay_bg, "size", w.size),
        )

        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(440, 260),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=18,
            spacing=10,
        )
        with card.canvas.before:
            Color(*COLORS["surface"])
            self._card_bg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS]
            )
        card.bind(
            pos=lambda w, _v: setattr(self._card_bg, "pos", w.pos),
            size=lambda w, _v: setattr(self._card_bg, "size", w.size),
        )

        title_lbl = Label(
            text=title,
            font_size=FONT_SIZES["large"],
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint_y=None,
            height=30,
        )
        title_lbl.bind(size=title_lbl.setter("text_size"))
        card.add_widget(title_lbl)

        msg_lbl = Label(
            text=message,
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_400"],
            halign="left",
            valign="top",
            size_hint_y=None,
            height=64,
        )
        msg_lbl.bind(size=msg_lbl.setter("text_size"))
        card.add_widget(msg_lbl)

        self.text_input = TextInput(
            text=initial_value or "",
            hint_text=placeholder,
            multiline=False,
            font_size=FONT_SIZES["medium"],
            size_hint_y=None,
            height=44,
            padding=[10, 10, 10, 10],
            background_color=COLORS["surface_light"],
            foreground_color=COLORS["white"],
            cursor_color=COLORS["white"],
            write_tab=False,
        )
        # Pressing Enter saves — convenient on hardware keyboards.
        self.text_input.bind(on_text_validate=lambda *_: self._confirm())
        card.add_widget(self.text_input)

        card.add_widget(Widget(size_hint_y=None, height=4))

        btn_row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=48,
            spacing=12,
        )
        btn_row.add_widget(SecondaryButton(text=cancel_text, on_release=lambda *_: self._dismiss()))
        btn_row.add_widget(PrimaryButton(text=confirm_text, on_release=lambda *_: self._confirm()))
        card.add_widget(btn_row)

        self.add_widget(card)
        Clock.schedule_once(lambda _dt: setattr(self.text_input, "focus", True), 0.1)

    def _confirm(self):
        value = self.text_input.text
        self._dismiss()
        if self._on_confirm:
            try:
                self._on_confirm(value)
            except Exception:  # noqa: BLE001
                logger.debug("TextInputDialog on_confirm raised", exc_info=True)

    def _dismiss(self):
        try:
            if self.parent:
                self.parent.remove_widget(self)
        except Exception:  # noqa: BLE001
            pass

    def on_touch_down(self, touch):
        # Tap outside the card (the dim overlay) closes the dialog.
        for child in self.children:
            if hasattr(child, "collide_point") and child.collide_point(*touch.pos):
                return super().on_touch_down(touch)
        self._dismiss()
        return True


__all__ = ["TextInputDialog"]
