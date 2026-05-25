"""
Transcription Overlay

Full-screen WhatsApp-style chat overlay that shows the live conversation
between the user and the AI assistant.

AI messages appear as bubbles on the left; user messages on the right.
Tap anywhere outside a bubble (or the ✕ button) to dismiss.
The overlay reappears automatically when the next transcript arrives.
"""

from __future__ import annotations

import threading
import time

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import COLORS, FONT_SIZES, DISPLAY_WIDTH, DISPLAY_HEIGHT

# ── Bubble geometry ──────────────────────────────────────────────────────────
_BUBBLE_MAX_FRACTION = 0.68          # fraction of overlay width
_BUBBLE_PADDING_H = 14               # horizontal padding inside bubble
_BUBBLE_PADDING_V = 10               # vertical padding inside bubble
_BUBBLE_RADIUS = 16

# ── Colours ───────────────────────────────────────────────────────────────────
_BG_COLOR = (0.04, 0.04, 0.06, 0.93)                  # near-black overlay bg
_AI_BUBBLE = (0.20, 0.20, 0.22, 1.0)                  # dark charcoal
_USER_BUBBLE = (0.13, 0.56, 0.34, 1.0)                # WhatsApp-green
_HEADER_BG = (0.10, 0.10, 0.12, 1.0)
_LABEL_AI = COLORS['gray_400']
_LABEL_USER = COLORS['gray_400']
_TEXT_COLOR = COLORS['white']
_HEADER_TEXT = COLORS['white']
_CLOSE_COLOR = COLORS['gray_400']
_SPEAKER_COLOR = COLORS['gray_500']

# ── Typography ────────────────────────────────────────────────────────────────
_FONT_BUBBLE = FONT_SIZES.get('medium', 16)
_FONT_SPEAKER = FONT_SIZES.get('tiny', 10) + 1
_FONT_HEADER = FONT_SIZES.get('title', 18)
_HEADER_H = 52


def _make_bubble_bg(widget, color: tuple, radius: int = _BUBBLE_RADIUS):
    """Attach a RoundedRectangle canvas background to *widget*."""
    with widget.canvas.before:
        Color(*color)
        rr = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
    widget.bind(
        pos=lambda w, _: setattr(rr, 'pos', w.pos),
        size=lambda w, _: setattr(rr, 'size', w.size),
    )


class _Bubble(BoxLayout):
    """A single chat bubble with auto-height based on text content."""

    def __init__(self, text: str, is_user: bool, overlay_width: float, **kwargs):
        super().__init__(
            orientation='vertical',
            size_hint=(None, None),
            padding=[_BUBBLE_PADDING_H, _BUBBLE_PADDING_V,
                     _BUBBLE_PADDING_H, _BUBBLE_PADDING_V],
            spacing=0,
            **kwargs,
        )
        self._is_user = is_user
        max_w = overlay_width * _BUBBLE_MAX_FRACTION
        self.width = max_w

        bubble_color = _USER_BUBBLE if is_user else _AI_BUBBLE
        _make_bubble_bg(self, bubble_color)

        lbl = Label(
            text=text,
            font_size=_FONT_BUBBLE,
            color=_TEXT_COLOR,
            halign='left',
            valign='top',
            size_hint=(1, None),
            text_size=(max_w - _BUBBLE_PADDING_H * 2, None),
        )

        def _sync_height(lbl, texture_size):
            lbl.height = texture_size[1]
            self.height = (
                texture_size[1]
                + _BUBBLE_PADDING_V * 2
            )

        lbl.bind(texture_size=_sync_height)
        self.add_widget(lbl)
        self._label = lbl

    def update_text(self, new_text: str):
        self._label.text = new_text


class _BubbleRow(FloatLayout):
    """Wraps a _Bubble inside a full-width row, aligned left (AI) or right (user)."""

    _SIDE_MARGIN = 12

    def __init__(self, bubble: _Bubble, **kwargs):
        super().__init__(size_hint=(1, None), **kwargs)
        self._bubble = bubble
        self._is_user = bubble._is_user

        if self._is_user:
            bubble.pos_hint = {'right': 1.0 - self._SIDE_MARGIN / DISPLAY_WIDTH}
        else:
            bubble.pos_hint = {'x': self._SIDE_MARGIN / DISPLAY_WIDTH}

        self.add_widget(bubble)
        bubble.bind(height=self._sync_row_height)

    def _sync_row_height(self, bubble, h):
        self.height = h + 8   # 8px vertical gap between rows

    def update_text(self, new_text: str):
        self._bubble.update_text(new_text)


class _SpeakerRow(BoxLayout):
    """Tiny 'AI' / 'You' speaker label row, aligned to match bubble side."""

    def __init__(self, is_user: bool, **kwargs):
        super().__init__(
            orientation='horizontal',
            size_hint=(1, None),
            height=18,
            padding=[14, 0, 14, 0],
            **kwargs,
        )
        label_text = 'You' if is_user else 'AI'
        lbl = Label(
            text=label_text,
            font_size=_FONT_SPEAKER,
            color=_SPEAKER_COLOR,
            halign='right' if is_user else 'left',
            valign='middle',
            size_hint=(1, 1),
        )
        lbl.bind(size=lbl.setter('text_size'))
        self.add_widget(lbl)


class TranscriptionOverlay(FloatLayout):
    """
    Full-screen transcript overlay.

    Public API
    ----------
    show()                          — make visible (animates in if hidden)
    hide()                          — animate out
    clear_session()                 — remove all messages (call at session start)
    add_ai_message(text) -> str     — append AI bubble; returns msg_id
    add_user_message(text) -> str   — append user bubble; returns msg_id
    update_user_message(id, text)   — replace user bubble text (grammar correction)
    """

    def __init__(self, **kwargs):
        super().__init__(size_hint=(1, 1), **kwargs)
        self._visible = False
        self._dismissed = False
        self._messages: dict[str, _BubbleRow] = {}   # msg_id → BubbleRow
        self._msg_counter = 0
        self.opacity = 0

        self._build_ui()

    # ── Build UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Full-screen dark background
        with self.canvas.before:
            Color(*_BG_COLOR)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda w, _: setattr(self._bg_rect, 'pos', w.pos),
            size=lambda w, _: setattr(self._bg_rect, 'size', w.size),
        )

        # ── Header bar ────────────────────────────────────────────────────────
        header = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=_HEADER_H,
            pos_hint={'top': 1},
            padding=[20, 0, 16, 0],
            spacing=8,
        )
        with header.canvas.before:
            Color(*_HEADER_BG)
            _hbg = Rectangle(pos=header.pos, size=header.size)
        header.bind(
            pos=lambda w, _: setattr(_hbg, 'pos', w.pos),
            size=lambda w, _: setattr(_hbg, 'size', w.size),
        )

        title_lbl = Label(
            text='Live Transcript',
            font_size=_FONT_HEADER,
            bold=True,
            color=_HEADER_TEXT,
            halign='left',
            valign='middle',
            size_hint=(1, 1),
        )
        title_lbl.bind(size=title_lbl.setter('text_size'))
        header.add_widget(title_lbl)

        close_btn = Label(
            text='✕',
            font_size=_FONT_HEADER + 2,
            color=_CLOSE_COLOR,
            size_hint=(None, 1),
            width=44,
            halign='center',
            valign='middle',
        )
        close_btn.bind(size=close_btn.setter('text_size'))
        close_btn.bind(on_touch_down=self._on_close_touch)
        header.add_widget(close_btn)
        self.add_widget(header)

        # ── ScrollView with chat list ─────────────────────────────────────────
        self._scroll = ScrollView(
            size_hint=(1, 1),
            pos_hint={'x': 0, 'y': 0},
            do_scroll_x=False,
            do_scroll_y=True,
            scroll_type=['bars', 'content'],
            bar_width=4,
            bar_color=list(COLORS['gray_700']),
            bar_inactive_color=list(COLORS['gray_800']),
        )

        self._chat_box = BoxLayout(
            orientation='vertical',
            size_hint=(1, None),
            spacing=4,
            padding=[0, _HEADER_H + 12, 0, 20],
        )
        self._chat_box.bind(minimum_height=self._chat_box.setter('height'))

        self._scroll.add_widget(self._chat_box)
        self.add_widget(self._scroll)

        # Touch-to-dismiss on the background (not on scroll content)
        self.bind(on_touch_down=self._on_bg_touch)

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self):
        """Animate the overlay into view."""
        self._dismissed = False
        if self._visible:
            return
        self._visible = True
        Animation.cancel_all(self)
        anim = Animation(opacity=1, duration=0.25, t='out_quad')
        anim.start(self)

    def hide(self):
        """Animate the overlay out of view."""
        if not self._visible:
            return
        self._visible = False
        Animation.cancel_all(self)
        anim = Animation(opacity=0, duration=0.2, t='in_quad')
        anim.start(self)

    def clear_session(self):
        """Remove all messages — call at the start of a new voice session."""
        self._chat_box.clear_widgets()
        self._messages.clear()
        self._msg_counter = 0

    def add_ai_message(self, text: str) -> str:
        """Append an AI speech bubble and return its msg_id."""
        return self._add_message(text, is_user=False)

    def add_user_message(self, text: str) -> str:
        """Append a user speech bubble and return its msg_id."""
        return self._add_message(text, is_user=True)

    def update_user_message(self, msg_id: str, corrected_text: str):
        """Replace the text of an existing user bubble (grammar correction)."""
        row = self._messages.get(msg_id)
        if row is not None:
            row.update_text(corrected_text)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _add_message(self, text: str, is_user: bool) -> str:
        self._msg_counter += 1
        msg_id = f"msg_{self._msg_counter}"

        # Speaker label row (tiny "AI" / "You" above the bubble)
        speaker_row = _SpeakerRow(is_user=is_user)
        self._chat_box.add_widget(speaker_row)

        bubble = _Bubble(
            text=text,
            is_user=is_user,
            overlay_width=self.width or DISPLAY_WIDTH,
        )
        row = _BubbleRow(bubble=bubble)
        self._chat_box.add_widget(row)
        self._messages[msg_id] = row

        # Scroll to bottom after layout settles
        Clock.schedule_once(lambda _dt: setattr(self._scroll, 'scroll_y', 0), 0.15)
        return msg_id

    def _on_close_touch(self, widget, touch):
        if widget.collide_point(*touch.pos):
            self._dismissed = True
            self.hide()
            return True
        return False

    def _on_bg_touch(self, widget, touch):
        """Dismiss when user taps the dark background area (not the scroll content)."""
        if not self._visible:
            return False
        # Only dismiss if the touch is NOT inside the scroll view content
        if not self._scroll.collide_point(*touch.pos):
            self._dismissed = True
            self.hide()
            return True
        return False

    def on_touch_down(self, touch):
        """Consume all touches so underlying home screen is not interactive."""
        if self.opacity < 0.05:
            return False
        super().on_touch_down(touch)
        return True
