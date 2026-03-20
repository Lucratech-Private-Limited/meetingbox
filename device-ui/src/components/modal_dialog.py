"""
Modal Dialog Component

Centred dialog over a dimmed overlay.
Supports title, message body, and up to two action buttons.
"""

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, RoundedRectangle

from config import COLORS, FONT_SIZES, BORDER_RADIUS, SPACING
from components.button import PrimaryButton, SecondaryButton, DangerButton


class ModalDialog(FloatLayout):
    """
    Modal overlay dialog.

    Parameters
    ----------
    title        : str
    message      : str
    confirm_text : str   – right button label (primary)
    cancel_text  : str   – left button label (secondary)
    danger       : bool  – use red styling
    on_confirm   : callable
    on_cancel    : callable
    border_color : tuple – optional border colour
    """

    def __init__(self, title='', message='',
                 confirm_text='OK', cancel_text='CANCEL',
                 danger=False, on_confirm=None, on_cancel=None,
                 border_color=None, **kwargs):
        super().__init__(**kwargs)

        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        # Dimmed overlay
        with self.canvas.before:
            if danger:
                Color(*COLORS['overlay_red'])
            else:
                Color(*COLORS['overlay'])
            self._overlay_bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda w, v: setattr(self._overlay_bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._overlay_bg, 'size', w.size),
        )

        # Card container
        card_w, card_h = 360, 220
        card = BoxLayout(
            orientation='vertical',
            size_hint=(None, None),
            size=(card_w, card_h),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            padding=16,
            spacing=12,
        )

        with card.canvas.before:
            Color(*COLORS['surface'])
            self._card_bg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
            if border_color:
                Color(*border_color)
                from kivy.graphics import Line
                Line(rounded_rectangle=(
                    card.x, card.y, card.width, card.height, BORDER_RADIUS),
                    width=2)
        card.bind(
            pos=lambda w, v: setattr(self._card_bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._card_bg, 'size', w.size),
        )

        # Title
        title_color = COLORS['red'] if danger else COLORS['white']
        title_label = Label(
            text=title,
            font_size=FONT_SIZES['title'],
            bold=True,
            color=title_color,
            halign='left',
            valign='middle',
            size_hint=(1, None),
            height=30,
        )
        title_label.bind(size=title_label.setter('text_size'))
        card.add_widget(title_label)

        # Separator
        sep = Widget(size_hint=(1, None), height=1)
        with sep.canvas:
            Color(*COLORS['gray_700'])
            _sr = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(
            pos=lambda w, v: setattr(_sr, 'pos', w.pos),
            size=lambda w, v: setattr(_sr, 'size', w.size),
        )
        card.add_widget(sep)

        # Message
        msg_label = Label(
            text=message,
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['gray_500'],
            halign='left',
            valign='top',
            size_hint=(1, 1),
        )
        msg_label.bind(size=msg_label.setter('text_size'))
        card.add_widget(msg_label)

        # Buttons row
        btn_row = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=50,
            spacing=SPACING['button_spacing'],
        )

        has_cancel = bool(cancel_text)
        if has_cancel:
            cancel_btn = SecondaryButton(
                text=cancel_text,
                size_hint=(0.5, 1),
                font_size=FONT_SIZES['medium'],
            )
            cancel_btn.bind(on_press=self._cancel)
            btn_row.add_widget(cancel_btn)

        confirm_hint = 0.5 if has_cancel else 1.0
        if danger:
            confirm_btn = DangerButton(
                text=confirm_text,
                size_hint=(confirm_hint, 1),
                font_size=FONT_SIZES['medium'],
            )
        else:
            confirm_btn = PrimaryButton(
                text=confirm_text,
                size_hint=(confirm_hint, 1),
                font_size=FONT_SIZES['medium'],
            )
        confirm_btn.bind(on_press=self._confirm)
        btn_row.add_widget(confirm_btn)

        card.add_widget(btn_row)
        self.add_widget(card)

    def _confirm(self, *_args):
        if self._on_confirm:
            self._on_confirm()
        self.dismiss()

    def _cancel(self, *_args):
        if self._on_cancel:
            self._on_cancel()
        self.dismiss()

    def dismiss(self):
        if self.parent:
            self.parent.remove_widget(self)

    def on_touch_down(self, touch):
        # Consume all touches so background is not interactive
        if self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        return True
