"""
Recipient Confirmation Overlay

Part of the voice-first email workflow. When the assistant resolves an email
recipient by name (via the `show_recipient_picker` realtime directive) it
displays the matching contacts here so the user can confirm by VOICE or TOUCH —
recipient confirmation is mandatory and never assumed, even for a single match.

States rendered from the directive's candidate list:

  • 1 candidate   → single confirmation card ("Is this the right person?")
  • >1 candidates → numbered cards ("Who did you mean?"); the user can say
                    "the first one" / a name, or tap a card.
  • 0 candidates  → a prompt asking the user to dictate the email address.

A tap is fed back into the live voice session as a spoken-equivalent user turn
(handled in main.py) so the assistant stays in the loop and the rest of the
safety workflow is preserved.

Public API
----------
show_candidates(query, candidates)  — render + reveal
close()                             — hide + clear

Callbacks (set by main.py):
    on_select(index, contact)   — a card was tapped (index is 1-based)
    on_dismiss()                — overlay dismissed (✕)
"""

from __future__ import annotations

import logging

from kivy.animation import Animation
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import DISPLAY_HEIGHT, DISPLAY_WIDTH

logger = logging.getLogger(__name__)

_DIM_BG      = (0.02, 0.10, 0.25, 0.60)
_CARD_BG     = (0.0, 0.039, 0.13, 1.0)
_CARD_BORDER = (0.25, 0.26, 0.33, 1.0)
_TITLE_COLOR = (1.0, 1.0, 1.0, 1.0)
_SUB_COLOR   = (0.71, 0.73, 0.95, 1.0)
_CONTACT_BG  = (0.024, 0.086, 0.26, 1.0)
_CONTACT_BORDER = (0.0, 0.42, 1.0, 0.55)
_NAME_COLOR  = (1.0, 1.0, 1.0, 1.0)
_EMAIL_COLOR = (0.71, 0.73, 0.95, 1.0)
_INDEX_COLOR = (0.0, 0.45, 1.0, 1.0)
_HINT_COLOR  = (0.55, 0.58, 0.72, 1.0)
_CLOSE_COLOR = (0.71, 0.73, 0.95, 1.0)


def _scale() -> float:
    return max(0.55, min(DISPLAY_WIDTH / 1260.0, DISPLAY_HEIGHT / 800.0, 1.6))


class _ContactCard(BoxLayout):
    """A single tappable contact card: [index] Name / email."""

    def __init__(self, index: int, name: str, email: str, show_index: bool,
                 on_tap, s: float, **kw):
        super().__init__(orientation="horizontal", size_hint=(1, None),
                         height=int(64 * s), padding=[int(16 * s), int(8 * s)],
                         spacing=int(14 * s), **kw)
        self._on_tap = on_tap
        self._index = index
        with self.canvas.before:
            Color(*_CONTACT_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                        radius=[int(12 * s)])
            Color(*_CONTACT_BORDER)
            self._border = Line(width=1.1)
        self.bind(pos=self._sync, size=self._sync)

        if show_index:
            idx = Label(
                text=str(index), font_size=int(24 * s), bold=True,
                color=_INDEX_COLOR, size_hint=(None, 1), width=int(34 * s),
                halign="center", valign="middle",
            )
            idx.bind(size=idx.setter("text_size"))
            self.add_widget(idx)

        text_col = BoxLayout(orientation="vertical", size_hint=(1, 1))
        name_lbl = Label(
            text=name or email, font_size=int(20 * s), bold=True,
            color=_NAME_COLOR, halign="left", valign="middle", size_hint=(1, 1),
            shorten=True, shorten_from="right",
        )
        name_lbl.bind(size=name_lbl.setter("text_size"))
        email_lbl = Label(
            text=email, font_size=int(16 * s), color=_EMAIL_COLOR,
            halign="left", valign="middle", size_hint=(1, 1),
            shorten=True, shorten_from="right",
        )
        email_lbl.bind(size=email_lbl.setter("text_size"))
        text_col.add_widget(name_lbl)
        text_col.add_widget(email_lbl)
        self.add_widget(text_col)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        s = _scale()
        self._border.rounded_rectangle = (
            self.x, self.y, self.width, self.height, int(12 * s)
        )

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self._on_tap:
                self._on_tap(self._index)
            return True
        return super().on_touch_down(touch)


class RecipientConfirmOverlay(FloatLayout):
    """Modal contact-confirmation overlay (touch + voice)."""

    def __init__(self, on_select=None, on_dismiss=None, **kw):
        super().__init__(size_hint=(1, 1), **kw)
        self.on_select = on_select
        self.on_dismiss = on_dismiss
        self._visible = False
        self._candidates: list[dict] = []
        self.opacity = 0
        self._build_ui()

    def _build_ui(self):
        s = _scale()
        with self.canvas.before:
            Color(*_DIM_BG)
            self._dim = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda *_: setattr(self._dim, "pos", self.pos),
            size=lambda *_: setattr(self._dim, "size", self.size),
        )

        card_w = min(int(DISPLAY_WIDTH * 0.7), int(640 * s))
        card_h = min(int(DISPLAY_HEIGHT * 0.8), int(560 * s))
        self._card = BoxLayout(
            orientation="vertical", size_hint=(None, None),
            size=(card_w, card_h),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=[int(24 * s), int(18 * s)], spacing=int(12 * s),
        )
        with self._card.canvas.before:
            Color(*_CARD_BG)
            self._card_bg = RoundedRectangle(
                pos=self._card.pos, size=self._card.size, radius=[int(24 * s)]
            )
            Color(*_CARD_BORDER)
            self._card_border = Line(width=1.2)
        self._card.bind(pos=self._sync_card, size=self._sync_card)

        # Header
        header = BoxLayout(orientation="horizontal", size_hint=(1, None),
                           height=int(36 * s))
        self._title = Label(
            text="", font_size=int(23 * s), bold=True, color=_TITLE_COLOR,
            halign="left", valign="middle", size_hint=(1, 1),
        )
        self._title.bind(size=self._title.setter("text_size"))
        header.add_widget(self._title)
        close_btn = Label(
            text="\u00d7", font_size=int(30 * s), bold=True, color=_CLOSE_COLOR,
            size_hint=(None, 1), width=int(36 * s), halign="center", valign="middle",
        )
        close_btn.bind(size=close_btn.setter("text_size"))
        close_btn.bind(on_touch_down=self._on_close_touch)
        header.add_widget(close_btn)
        self._card.add_widget(header)

        self._subtitle = Label(
            text="", font_size=int(16 * s), color=_SUB_COLOR,
            halign="left", valign="middle", size_hint=(1, None), height=int(26 * s),
        )
        self._subtitle.bind(size=self._subtitle.setter("text_size"))
        self._card.add_widget(self._subtitle)

        # Scrollable card list
        self._scroll = ScrollView(do_scroll_x=False, do_scroll_y=True,
                                  bar_width=int(5 * s))
        self._list = BoxLayout(orientation="vertical", size_hint=(1, None),
                               spacing=int(12 * s), padding=[0, int(4 * s)])
        self._list.bind(minimum_height=self._list.setter("height"))
        self._scroll.add_widget(self._list)
        self._card.add_widget(self._scroll)

        # Footer hint
        self._hint = Label(
            text="", font_size=int(14 * s), color=_HINT_COLOR,
            halign="center", valign="middle", size_hint=(1, None), height=int(24 * s),
        )
        self._hint.bind(size=self._hint.setter("text_size"))
        self._card.add_widget(self._hint)

        self.add_widget(self._card)

    def _sync_card(self, *_):
        self._card_bg.pos = self._card.pos
        self._card_bg.size = self._card.size
        s = _scale()
        self._card_border.rounded_rectangle = (
            self._card.x, self._card.y, self._card.width,
            self._card.height, int(24 * s),
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def show_candidates(self, query: str, candidates: list[dict]):
        s = _scale()
        self._candidates = [c for c in (candidates or []) if c.get("email")]
        self._list.clear_widgets()
        q = (query or "").strip()
        n = len(self._candidates)

        if n == 0:
            self._title.text = "No contact found"
            self._subtitle.text = (
                f"I couldn't find anyone called \u201c{q}\u201d." if q
                else "I couldn't find that contact."
            )
            self._hint.text = "Tell me their email address and I'll remember it."
            empty = Label(
                text="Say the email address out loud, e.g. \u201crahul at company dot com\u201d.",
                font_size=int(17 * s), color=_SUB_COLOR, halign="center",
                valign="middle", size_hint=(1, None), height=int(80 * s),
            )
            empty.bind(size=empty.setter("text_size"))
            self._list.add_widget(empty)
        else:
            show_index = n > 1
            if n == 1:
                self._title.text = "Confirm recipient"
                self._subtitle.text = (
                    f"Is this the right person for \u201c{q}\u201d?" if q
                    else "Is this the right person?"
                )
                self._hint.text = "Say \u201cyes\u201d / \u201cuse that one\u201d, or tap the card."
            else:
                self._title.text = "Who did you mean?"
                self._subtitle.text = (
                    f"{n} contacts match \u201c{q}\u201d." if q
                    else f"{n} matching contacts."
                )
                self._hint.text = "Say \u201cthe first one\u201d / a name, or tap a card."
            for i, c in enumerate(self._candidates, start=1):
                card = _ContactCard(
                    index=i, name=(c.get("name") or "").strip(),
                    email=(c.get("email") or "").strip(),
                    show_index=show_index, on_tap=self._select, s=s,
                )
                self._list.add_widget(card)

        self.show()

    def show(self):
        if self._visible:
            return
        self._visible = True
        Animation.cancel_all(self)
        Animation(opacity=1, duration=0.2, t="out_quad").start(self)

    def close(self):
        if not self._visible:
            return
        self._visible = False
        Animation.cancel_all(self)
        Animation(opacity=0, duration=0.18, t="in_quad").start(self)

    @property
    def visible(self) -> bool:
        return self._visible

    # ── Internals ─────────────────────────────────────────────────────────

    def _select(self, index: int):
        if 1 <= index <= len(self._candidates):
            contact = self._candidates[index - 1]
            if self.on_select:
                self.on_select(index, contact)

    def _on_close_touch(self, widget, touch):
        if widget.collide_point(*touch.pos):
            if self.on_dismiss:
                self.on_dismiss()
            else:
                self.close()
            return True
        return False

    def on_touch_down(self, touch):
        if self.opacity < 0.05:
            return False
        super().on_touch_down(touch)
        # Grab the touch so the ENTIRE gesture (move + up) is owned by this
        # modal overlay and never leaks through to the screen behind it.
        touch.grab(self)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is self:
            return True
        if self.opacity < 0.05:
            return False
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            return True
        if self.opacity < 0.05:
            return False
        super().on_touch_up(touch)
        return True
