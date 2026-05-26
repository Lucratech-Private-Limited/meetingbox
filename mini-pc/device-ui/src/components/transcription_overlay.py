"""
Transcription Overlay

Persistent full-screen chat panel that shows the live conversation between the
user and the AI assistant for the entire voice session.

AI messages appear as bubbles on the left; user messages on the right.
The overlay is always visible while the voice session is active.
Tap ✕ to dismiss; it reappears automatically on the next transcript.
"""

from __future__ import annotations

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from config import COLORS, FONT_SIZES, DISPLAY_WIDTH, DISPLAY_HEIGHT

# ── Bubble geometry ──────────────────────────────────────────────────────────
_BUBBLE_MAX_FRACTION = 0.70          # fraction of overlay width
_BUBBLE_PADDING_H = 14
_BUBBLE_PADDING_V = 10
_BUBBLE_RADIUS = 16

# ── Colours ───────────────────────────────────────────────────────────────────
_BG_COLOR        = (0.04, 0.04, 0.06, 0.93)
_AI_BUBBLE       = (0.20, 0.20, 0.22, 1.0)   # dark charcoal
_USER_BUBBLE     = (0.13, 0.56, 0.34, 1.0)   # WhatsApp-green
_HEADER_BG       = (0.08, 0.08, 0.10, 1.0)
_TEXT_COLOR      = COLORS['white']
_HEADER_TEXT     = COLORS['white']
_CLOSE_COLOR     = COLORS['gray_400']
_SPEAKER_COLOR   = COLORS['gray_500']

# ── Typography ────────────────────────────────────────────────────────────────
_FONT_BUBBLE  = FONT_SIZES.get('medium', 16)
_FONT_SPEAKER = FONT_SIZES.get('tiny', 10) + 1
_FONT_HEADER  = FONT_SIZES.get('title', 18)
_HEADER_H     = 52


def _attach_bg(widget, color: tuple, radius: int = 0):
    """Attach a canvas.before background (rounded if radius > 0)."""
    with widget.canvas.before:
        Color(*color)
        if radius:
            rect = RoundedRectangle(pos=widget.pos, size=widget.size,
                                    radius=[radius])
        else:
            rect = Rectangle(pos=widget.pos, size=widget.size)
    widget.bind(
        pos=lambda w, _: setattr(rect, 'pos', w.pos),
        size=lambda w, _: setattr(rect, 'size', w.size),
    )


# ── Bubble ────────────────────────────────────────────────────────────────────

class _Bubble(BoxLayout):
    def __init__(self, text: str, is_user: bool, overlay_width: float, **kw):
        super().__init__(
            orientation='vertical',
            size_hint=(None, None),
            padding=[_BUBBLE_PADDING_H, _BUBBLE_PADDING_V,
                     _BUBBLE_PADDING_H, _BUBBLE_PADDING_V],
            spacing=0,
            **kw,
        )
        max_w = (overlay_width or DISPLAY_WIDTH) * _BUBBLE_MAX_FRACTION
        self.width = max_w
        _attach_bg(self, _USER_BUBBLE if is_user else _AI_BUBBLE,
                   radius=_BUBBLE_RADIUS)

        lbl = Label(
            text=text,
            font_size=_FONT_BUBBLE,
            color=_TEXT_COLOR,
            halign='left',
            valign='top',
            size_hint=(1, None),
            text_size=(max_w - _BUBBLE_PADDING_H * 2, None),
        )
        lbl.bind(texture_size=lambda l, ts: self._sync_height(l, ts))
        self.add_widget(lbl)
        self._label = lbl

    def _sync_height(self, lbl, ts):
        lbl.height = ts[1]
        self.height = ts[1] + _BUBBLE_PADDING_V * 2

    def update_text(self, text: str):
        self._label.text = text


class _BubbleRow(FloatLayout):
    _MARGIN = 12

    def __init__(self, bubble: _Bubble, **kw):
        super().__init__(size_hint=(1, None), **kw)
        is_user = bubble._is_user
        if is_user:
            bubble.pos_hint = {'right': 1.0 - self._MARGIN / DISPLAY_WIDTH}
        else:
            bubble.pos_hint = {'x': self._MARGIN / DISPLAY_WIDTH}
        self.add_widget(bubble)
        bubble.bind(height=lambda b, h: setattr(self, 'height', h + 8))
        self._bubble = bubble

    def update_text(self, text: str):
        self._bubble.update_text(text)


class _SpeakerLabel(BoxLayout):
    def __init__(self, is_user: bool, **kw):
        super().__init__(
            orientation='horizontal',
            size_hint=(1, None),
            height=18,
            padding=[14, 0, 14, 0],
            **kw,
        )
        lbl = Label(
            text='You' if is_user else 'AI',
            font_size=_FONT_SPEAKER,
            color=_SPEAKER_COLOR,
            halign='right' if is_user else 'left',
            valign='middle',
            size_hint=(1, 1),
        )
        lbl.bind(size=lbl.setter('text_size'))
        self.add_widget(lbl)


# ── Main overlay ──────────────────────────────────────────────────────────────

class TranscriptionOverlay(FloatLayout):
    """
    Persistent full-screen transcript panel.

    Public API
    ----------
    show()                         — make visible (animates in)
    hide()                         — animate out
    clear_session()                — remove all messages (call at session start)
    add_ai_message(text) -> str    — append AI bubble; returns msg_id
    add_user_message(text) -> str  — append user bubble; returns msg_id
    update_user_message(id, text)  — replace user bubble text (grammar fix)
    """

    def __init__(self, **kw):
        super().__init__(size_hint=(1, 1), **kw)
        self._visible = False
        self._messages: dict[str, _BubbleRow] = {}
        self._msg_counter = 0
        self.opacity = 0
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Full-screen background
        _attach_bg(self, _BG_COLOR)

        # ── ScrollView (added FIRST → drawn behind header) ─────────────────
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
            # top padding reserves space for the header bar
            padding=[0, _HEADER_H + 12, 0, 20],
        )
        self._chat_box.bind(minimum_height=self._chat_box.setter('height'))
        self._scroll.add_widget(self._chat_box)
        self.add_widget(self._scroll)   # ← behind header

        # ── Header bar (added SECOND → drawn on top of scroll view) ────────
        header = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=_HEADER_H,
            pos_hint={'top': 1},
            padding=[20, 0, 16, 0],
            spacing=8,
        )
        _attach_bg(header, _HEADER_BG)

        title = Label(
            text='Live Transcript',
            font_size=_FONT_HEADER,
            bold=True,
            color=_HEADER_TEXT,
            halign='left',
            valign='middle',
            size_hint=(1, 1),
        )
        title.bind(size=title.setter('text_size'))
        header.add_widget(title)

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

        self.add_widget(header)         # ← on top of scroll view

    # ── Public API ────────────────────────────────────────────────────────────

    def show(self):
        if self._visible:
            return
        self._visible = True
        Animation.cancel_all(self)
        Animation(opacity=1, duration=0.25, t='out_quad').start(self)

    def hide(self):
        if not self._visible:
            return
        self._visible = False
        Animation.cancel_all(self)
        Animation(opacity=0, duration=0.2, t='in_quad').start(self)

    def clear_session(self):
        self._chat_box.clear_widgets()
        self._messages.clear()
        self._msg_counter = 0

    def add_ai_message(self, text: str) -> str:
        return self._add_message(text, is_user=False)

    def add_user_message(self, text: str) -> str:
        return self._add_message(text, is_user=True)

    def update_user_message(self, msg_id: str, corrected: str):
        row = self._messages.get(msg_id)
        if row is not None:
            row.update_text(corrected)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _add_message(self, text: str, is_user: bool) -> str:
        self._msg_counter += 1
        msg_id = f"msg_{self._msg_counter}"

        self._chat_box.add_widget(_SpeakerLabel(is_user=is_user))
        bubble = _Bubble(text=text, is_user=is_user,
                         overlay_width=self.width or DISPLAY_WIDTH)
        row = _BubbleRow(bubble=bubble)
        self._chat_box.add_widget(row)
        self._messages[msg_id] = row

        # Scroll to bottom so the newest message is always visible
        Clock.schedule_once(lambda _dt: setattr(self._scroll, 'scroll_y', 0), 0.15)

        # Always show when new content arrives (reappears even after dismiss)
        self.show()
        return msg_id

    def _on_close_touch(self, widget, touch):
        if widget.collide_point(*touch.pos):
            self.hide()
            return True
        return False

    def on_touch_down(self, touch):
        """Consume all touches while visible so underlying screen stays inactive."""
        if self.opacity < 0.05:
            return False
        super().on_touch_down(touch)
        return True
