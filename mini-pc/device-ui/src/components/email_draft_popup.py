"""
Email Draft Popup

The primary visual review surface for the voice-first email workflow. While the
assistant drafts an email it drives this popup via the `show_email_draft`
realtime directive, populating fields progressively (recipient → subject →
body) so the user reviews on screen instead of having the body read aloud.

Layout follows the Meeting BOX AI "Draft email pop up" Figma frame:

    ┌───────────────────────────────────────────────[ ✕ ]┐
    │  To :       rahul@company.com                       │
    │ ─────────────────────────────────────────────────  │
    │  cc :       …            (hidden when empty)         │
    │ ─────────────────────────────────────────────────  │
    │  bcc :      …            (hidden when empty)         │
    │ ─────────────────────────────────────────────────  │
    │  subject :  Lunch on Friday                          │
    │ ─────────────────────────────────────────────────  │
    │  ┌─────────────────────────────────────────────┐   │
    │  │  <scrollable body>                          ▌│   │
    │  └─────────────────────────────────────────────┘   │
    │        [ Discard ]   [ Save as Draft ]   [ Send ]   │
    └─────────────────────────────────────────────────────┘

Per the design note: when there are no cc / bcc recipients those rows are
removed and their vertical space is given to the body instead of being left
blank.

Public API
----------
open_draft(data)   — show the popup populated from a directive dict
update_draft(data) — update fields in place (real-time progressive fill)
close()            — hide + clear

Callbacks (set by main.py, all invoked on the Kivy thread):
    on_send, on_save_draft, on_discard, on_close
"""

from __future__ import annotations

import logging

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import DISPLAY_HEIGHT, DISPLAY_WIDTH

logger = logging.getLogger(__name__)

# ── Palette (from the Figma frame) ──────────────────────────────────────────
_DIM_BG       = (0.02, 0.10, 0.25, 0.60)   # rgba(5,26,65,.6) backdrop
_CARD_BG      = (0.0, 0.039, 0.13, 1.0)    # ~#010A21 dark navy
_CARD_BORDER  = (0.25, 0.26, 0.33, 1.0)    # subtle steel edge
_LABEL_COLOR  = (1.0, 1.0, 1.0, 1.0)
_VALUE_COLOR  = (0.71, 0.73, 0.95, 1.0)    # #B6BAF2
_PLACEHOLDER  = (0.40, 0.43, 0.58, 1.0)
_SEP_COLOR    = (0.24, 0.255, 0.32, 1.0)   # #3E4152
_BODY_BOX_BG  = (0.024, 0.086, 0.26, 1.0)  # #061642
_CLOSE_COLOR  = (0.71, 0.73, 0.95, 1.0)
_STATUS_COLOR = (0.62, 0.78, 1.0, 1.0)

_SEND_BG      = (0.01, 0.66, 0.0, 1.0)     # green
_SAVE_BG      = (0.0, 0.35, 0.86, 1.0)     # blue
_DISCARD_BG   = (0.86, 0.21, 0.27, 1.0)    # red


def _scale() -> float:
    """Scale factor vs the 1260×800 Figma baseline (clamped)."""
    return max(0.55, min(DISPLAY_WIDTH / 1260.0, DISPLAY_HEIGHT / 800.0, 1.6))


def _fmt_recipients(items) -> str:
    """Render a normalized [{name,email}] list into a single display string."""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            name = (it.get("name") or "").strip()
            email = (it.get("email") or "").strip()
            if name and email:
                out.append(f"{name} <{email}>")
            else:
                out.append(email or name)
        else:
            s = str(it or "").strip()
            if s:
                out.append(s)
    return ", ".join(p for p in out if p)


class _PillButton(BoxLayout):
    """A rounded, filled, tappable button with a centred label."""

    def __init__(self, text: str, bg_color: tuple, on_tap, font_px: int, **kw):
        super().__init__(size_hint=(None, None), **kw)
        self._on_tap = on_tap
        self._enabled = True
        with self.canvas.before:
            self._color = Color(*bg_color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[10])
        self.bind(pos=self._sync, size=self._sync)
        self._lbl = Label(
            text=text, font_size=font_px, bold=True, color=(1, 1, 1, 1),
            halign="center", valign="middle",
        )
        self._lbl.bind(size=self._lbl.setter("text_size"))
        self.add_widget(self._lbl)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._color.a = 1.0 if enabled else 0.4

    def on_touch_down(self, touch):
        if self._enabled and self.collide_point(*touch.pos):
            if self._on_tap:
                self._on_tap()
            return True
        return super().on_touch_down(touch)


class EmailDraftPopup(FloatLayout):
    """Modal email draft review popup."""

    def __init__(self, on_send=None, on_save_draft=None, on_discard=None,
                 on_close=None, **kw):
        super().__init__(size_hint=(1, 1), **kw)
        self.on_send = on_send
        self.on_save_draft = on_save_draft
        self.on_discard = on_discard
        self.on_close = on_close

        self._visible = False
        self._state = "drafting"
        self._auto_close_ev = None
        self._row_widgets: dict[str, BoxLayout] = {}
        self._sep_widgets: dict[str, Widget] = {}
        # Persisted draft fields. Updates MERGE into this — a directive that
        # omits a field keeps the previous value, so a single-field edit (e.g.
        # adding cc) never blanks the rest of the draft.
        self._fields: dict[str, str] = {
            "to": "", "cc": "", "bcc": "", "subject": "", "body": "", "draft_id": "",
        }
        self.opacity = 0
        self._build_ui()

    # ── Build ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        s = _scale()
        # Dim backdrop
        with self.canvas.before:
            Color(*_DIM_BG)
            self._dim = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda *_: setattr(self._dim, "pos", self.pos),
            size=lambda *_: setattr(self._dim, "size", self.size),
        )

        card_w = min(int(DISPLAY_WIDTH * 0.93), int(1174 * s))
        card_h = min(int(DISPLAY_HEIGHT * 0.86), int(660 * s))
        self._card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(card_w, card_h),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=[int(26 * s), int(16 * s), int(26 * s), int(18 * s)],
            spacing=int(6 * s),
        )
        with self._card.canvas.before:
            Color(*_CARD_BG)
            self._card_bg = RoundedRectangle(
                pos=self._card.pos, size=self._card.size, radius=[int(28 * s)]
            )
            Color(*_CARD_BORDER)
            self._card_border = Line(
                rounded_rectangle=(
                    self._card.x, self._card.y, self._card.width,
                    self._card.height, int(28 * s),
                ),
                width=1.2,
            )
        self._card.bind(pos=self._sync_card, size=self._sync_card)

        # ── Top bar: title + close ✕ ──────────────────────────────────────
        topbar = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=int(40 * s))
        self._title = Label(
            text="Email Draft", font_size=int(22 * s), bold=True,
            color=_LABEL_COLOR, halign="left", valign="middle", size_hint=(1, 1),
        )
        self._title.bind(size=self._title.setter("text_size"))
        topbar.add_widget(self._title)

        self._status = Label(
            text="", font_size=int(15 * s), color=_STATUS_COLOR,
            halign="right", valign="middle", size_hint=(1, 1),
        )
        self._status.bind(size=self._status.setter("text_size"))
        topbar.add_widget(self._status)

        close_btn = Label(
            text="\u00d7", font_size=int(32 * s), bold=True, color=_CLOSE_COLOR,
            size_hint=(None, 1), width=int(40 * s),
            halign="center", valign="middle",
        )
        close_btn.bind(size=close_btn.setter("text_size"))
        close_btn.bind(on_touch_down=self._on_close_touch)
        topbar.add_widget(close_btn)
        self._card.add_widget(topbar)

        # ── Header field rows (To / cc / bcc / subject) ───────────────────
        self._header_box = BoxLayout(orientation="vertical", size_hint=(1, None),
                                     spacing=0)
        self._header_box.bind(minimum_height=self._header_box.setter("height"))
        self._row_height = int(46 * s)
        for key, label in (("to", "To :"), ("cc", "cc :"),
                           ("bcc", "bcc :"), ("subject", "subject :")):
            row, value_lbl = self._build_field_row(label, s)
            row._value_lbl = value_lbl
            self._row_widgets[key] = row
            sep = self._build_separator(s)
            self._sep_widgets[key] = sep
            self._header_box.add_widget(row)
            self._header_box.add_widget(sep)
        self._card.add_widget(self._header_box)

        # ── Body (scrollable) ─────────────────────────────────────────────
        body_wrap = BoxLayout(orientation="vertical", size_hint=(1, 1),
                              padding=[0, int(8 * s), 0, int(8 * s)])
        with body_wrap.canvas.before:
            Color(*_BODY_BOX_BG)
            self._body_bg = RoundedRectangle(radius=[int(12 * s)])
        body_wrap.bind(
            pos=lambda *_: self._sync_rect(self._body_bg, body_wrap),
            size=lambda *_: self._sync_rect(self._body_bg, body_wrap),
        )
        self._body_scroll = ScrollView(
            do_scroll_x=False, do_scroll_y=True, bar_width=int(6 * s),
            bar_color=(0.0, 0.42, 1.0, 0.9), bar_inactive_color=(0.1, 0.2, 0.45, 0.5),
        )
        self._body_label = Label(
            text="", font_size=int(18 * s), color=_VALUE_COLOR,
            halign="left", valign="top", size_hint=(1, None),
            padding=(int(18 * s), int(14 * s)),
        )
        self._body_label.bind(
            width=lambda lbl, w: setattr(lbl, "text_size", (w - int(36 * s), None)),
            texture_size=lambda lbl, ts: setattr(lbl, "height", ts[1] + int(28 * s)),
        )
        self._body_scroll.add_widget(self._body_label)
        body_wrap.add_widget(self._body_scroll)
        self._card.add_widget(body_wrap)

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=int(52 * s), spacing=int(16 * s),
                            padding=[0, int(10 * s), 0, 0])
        btn_w = int(150 * s)
        btn_h = int(42 * s)
        fpx = int(19 * s)
        btn_row.add_widget(Widget(size_hint=(1, 1)))
        self._discard_btn = _PillButton("Discard", _DISCARD_BG,
                                        self._tap_discard, fpx,
                                        size=(btn_w, btn_h))
        self._save_btn = _PillButton("Save as Draft", _SAVE_BG,
                                     self._tap_save, fpx, size=(btn_w, btn_h))
        self._send_btn = _PillButton("Send", _SEND_BG,
                                     self._tap_send, fpx, size=(btn_w, btn_h))
        btn_row.add_widget(self._discard_btn)
        btn_row.add_widget(self._save_btn)
        btn_row.add_widget(self._send_btn)
        btn_row.add_widget(Widget(size_hint=(1, 1)))
        self._card.add_widget(btn_row)

        self.add_widget(self._card)

    def _build_field_row(self, label_text: str, s: float):
        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=int(46 * s), padding=[int(6 * s), 0, int(6 * s), 0],
                        spacing=int(12 * s))
        lbl = Label(
            text=label_text, font_size=int(21 * s), bold=True,
            color=_LABEL_COLOR, halign="left", valign="middle",
            size_hint=(None, 1), width=int(110 * s),
        )
        lbl.bind(size=lbl.setter("text_size"))
        value = Label(
            text="", font_size=int(19 * s), color=_VALUE_COLOR,
            halign="left", valign="middle", size_hint=(1, 1), shorten=True,
            shorten_from="right",
        )
        value.bind(size=value.setter("text_size"))
        row.add_widget(lbl)
        row.add_widget(value)
        return row, value

    def _build_separator(self, s: float):
        sep = Widget(size_hint=(1, None), height=1)
        with sep.canvas:
            Color(*_SEP_COLOR)
            r = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(
            pos=lambda *_: setattr(r, "pos", sep.pos),
            size=lambda *_: setattr(r, "size", sep.size),
        )
        return sep

    def _sync_card(self, *_):
        self._card_bg.pos = self._card.pos
        self._card_bg.size = self._card.size
        s = _scale()
        self._card_border.rounded_rectangle = (
            self._card.x, self._card.y, self._card.width,
            self._card.height, int(28 * s),
        )

    @staticmethod
    def _sync_rect(rect, widget):
        rect.pos = widget.pos
        rect.size = widget.size

    # ── Public API ────────────────────────────────────────────────────────

    def open_draft(self, data: dict):
        self.update_draft(data)
        self.show()

    def update_draft(self, data: dict):
        if not isinstance(data, dict):
            return
        # MERGE semantics: only overwrite a field the directive actually
        # carries. Missing keys keep their previous value so progressive fills
        # and single-field edits (add cc, change subject, edit body) never wipe
        # the rest of the draft.
        for key in ("to", "cc", "bcc"):
            if key in data:
                self._fields[key] = _fmt_recipients(data.get(key))
        if "subject" in data:
            self._fields["subject"] = str(data.get("subject") or "").strip()
        if "body" in data:
            self._fields["body"] = str(data.get("body") or "")
        if "draft_id" in data:
            self._fields["draft_id"] = str(data.get("draft_id") or "")
        state = str(data.get("state") or self._state or "drafting").strip().lower()
        self._state = state

        # To & subject rows always present; show a dim placeholder while empty
        # so there is never a blank loading state.
        self._set_row("to", self._fields["to"], placeholder="\u2026")
        self._set_row("subject", self._fields["subject"], placeholder="\u2026")

        # cc / bcc rows collapse when empty so their space goes to the body,
        # and expand the moment they have a value — so To, cc and bcc are all
        # visible together whenever each is set.
        self._toggle_optional_row("cc", self._fields["cc"])
        self._toggle_optional_row("bcc", self._fields["bcc"])

        body = self._fields["body"]
        if body:
            self._body_label.text = body
            self._body_label.color = _VALUE_COLOR
        elif state in ("drafting", "ready"):
            self._body_label.text = "Drafting\u2026"
            self._body_label.color = _PLACEHOLDER

        self._apply_state(state)

    def show(self):
        if self._visible:
            return
        self._visible = True
        Animation.cancel_all(self)
        Animation(opacity=1, duration=0.2, t="out_quad").start(self)

    def close(self):
        self._cancel_auto_close()
        if not self._visible:
            return
        self._visible = False
        Animation.cancel_all(self)
        anim = Animation(opacity=0, duration=0.18, t="in_quad")
        anim.bind(on_complete=lambda *_: self._reset())
        anim.start(self)

    @property
    def visible(self) -> bool:
        return self._visible

    # ── Internals ───────────────────────────────────────────────────────────

    def _set_row(self, key: str, text: str, placeholder: str = ""):
        row = self._row_widgets.get(key)
        if row is None:
            return
        lbl = getattr(row, "_value_lbl", None)
        if lbl is None:
            return
        if text:
            lbl.text = text
            lbl.color = _VALUE_COLOR
        else:
            lbl.text = placeholder
            lbl.color = _PLACEHOLDER

    def _toggle_optional_row(self, key: str, text: str):
        """Collapse an empty cc/bcc row (height→0) so the body reclaims the
        space, per the design. Kept in the tree to preserve ordering — only its
        size/visibility change."""
        row = self._row_widgets.get(key)
        sep = self._sep_widgets.get(key)
        if row is None or sep is None:
            return
        present = bool(text)
        if present:
            row.height = self._row_height
            row.opacity = 1
            row.disabled = False
            sep.height = 1
            sep.opacity = 1
            self._set_row(key, text)
        else:
            row.height = 0
            row.opacity = 0
            row.disabled = True
            sep.height = 0
            sep.opacity = 0

    def _apply_state(self, state: str):
        terminal = {
            "sent": ("Sent \u2713", True),
            "saved": ("Saved to drafts", True),
            "discarded": ("Discarded", True),
        }
        if state in terminal:
            msg, auto = terminal[state]
            self._status.text = msg
            self._set_buttons_enabled(False)
            if auto:
                self._schedule_auto_close()
        elif state == "ready":
            self._status.text = "Ready to review"
            self._set_buttons_enabled(True)
        elif state == "sending":
            self._status.text = "Sending\u2026"
            self._set_buttons_enabled(False)
        else:
            self._status.text = "Drafting\u2026"
            self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool):
        for b in (self._send_btn, self._save_btn, self._discard_btn):
            b.set_enabled(enabled)

    def _schedule_auto_close(self):
        self._cancel_auto_close()
        self._auto_close_ev = Clock.schedule_once(lambda _dt: self.close(), 2.6)

    def _cancel_auto_close(self):
        if self._auto_close_ev is not None:
            self._auto_close_ev.cancel()
            self._auto_close_ev = None

    def _reset(self):
        if self._visible:
            return
        self._state = "drafting"
        self._fields = {
            "to": "", "cc": "", "bcc": "", "subject": "", "body": "", "draft_id": "",
        }
        self._status.text = ""
        self._body_label.text = ""
        for key in ("to", "subject"):
            self._set_row(key, "", placeholder="\u2026")
        for key in ("cc", "bcc"):
            self._toggle_optional_row(key, "")
        self._set_buttons_enabled(True)

    # ── Touch handlers ────────────────────────────────────────────────────

    def _tap_send(self):
        if self.on_send:
            self.on_send()

    def _tap_save(self):
        if self.on_save_draft:
            self.on_save_draft()

    def _tap_discard(self):
        if self.on_discard:
            self.on_discard()

    def _on_close_touch(self, widget, touch):
        if widget.collide_point(*touch.pos):
            if self.on_close:
                self.on_close()
            else:
                self.close()
            return True
        return False

    def on_touch_down(self, touch):
        # Modal: consume every touch so the screen below is inert while open.
        if self.opacity < 0.05:
            return False
        super().on_touch_down(touch)
        # Grab the touch so move + up of this gesture cannot leak through to
        # the screen behind the modal.
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
