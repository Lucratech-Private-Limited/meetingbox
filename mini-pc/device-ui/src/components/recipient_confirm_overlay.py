"""
Recipient Confirmation Overlay  —  Figma System States_4/_5
(3027:1967 / 3028:2045, 1260 × 800 px)

Shown when the assistant resolves a recipient name to one or more contacts.
The user confirms by VOICE or TOUCH.

Visual design (Figma):
  ┌──────────────────────────────────────┐
  │  [✉]  Contact                   (scrim behind, card is white)
  │ ─────────────────────────────────────│ ← purple header h=80 #6D48CC
  │  ①  suresh.abc@gmail.com             │
  │ ─────────────────────────────────────│
  │  ②  other.user@example.com           │
  │ ─────────────────────────────────────│
  │  ③  …                                │
  │ ─────────────────────────────────────│
  │  ④  None                             │
  └──────────────────────────────────────┘

Badge colour cycling by 1-based row index (colours loop every 4):
  index % 4 == 1 → #10C76D (green)
  index % 4 == 2 → #4DA6DE (blue)
  index % 4 == 3 → #FCA862 (orange)
  index % 4 == 0 → #F155F4 (pink/magenta)

"None" is appended as the last row, taking the next colour in sequence.

Scrolling: the rows area scrolls when total rows (contacts + None) exceed 4.

Tap-to-confirm:
  - Tapping a contact highlights the row background to #BDDDF2 for 400 ms,
    then fires on_select(index, contact).
  - Tapping "None" fires on_none() (or injects "None of those." voice turn).
  - Tapping ✕ close fires on_dismiss() with no voice injection.

Public API (unchanged from previous version):
    show_candidates(query, candidates)  — render + reveal
    close()                              — hide + clear
    on_select(index, contact)           — card tapped (1-based index)
    on_dismiss()                        — ✕ tapped
    on_none()                           — "None" tapped  (NEW)
"""

from __future__ import annotations

import logging

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH

logger = logging.getLogger(__name__)

# ── Asset paths ──────────────────────────────────────────────────────────────
_EMAIL_DIR = ASSETS_DIR / "email" / "figma"


def _email_asset(name: str) -> str:
    p = _EMAIL_DIR / name
    return str(p) if p.is_file() else ""


# ── Figma scale ──────────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0


def _ff(fs: float) -> int:
    s = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * s))


# ── Colours ───────────────────────────────────────────────────────────────────
_C_SCRIM      = (0.0, 0.0, 0.0, 0.45)       # dim backdrop so the popup stands out
_C_HEADER     = (0.427, 0.282, 0.800, 1.0)  # #6D48CC  purple
_C_WHITE      = (1.0, 1.0, 1.0, 1.0)
_C_ROW_TEXT   = (0.208, 0.224, 0.231, 1.0)  # #35393B
_C_ROW_HIGHLIGHT = (0.741, 0.867, 0.949, 1.0)  # #BDDDF2  tap flash
_C_SEP        = (0.620, 0.620, 0.620, 0.50)  # divider lines

# Badge colours — cycling by (index-1) % 4
_BADGE_COLOURS = [
    (0.063, 0.780, 0.427, 1.0),   # 1,5,9…   #10C76D green
    (0.302, 0.651, 0.871, 1.0),   # 2,6,10…  #4DA6DE blue
    (0.988, 0.659, 0.384, 1.0),   # 3,7,11…  #FCA862 orange
    (0.945, 0.333, 0.957, 1.0),   # 4,8,12…  #F155F4 pink
]

_FONT_SB = "42dot-SB"
_FONT_MD = "42dot-Med"

# Popup dimensions  (Figma "Frame 21": 763 × 394 at (248, 131))
_POP_W = 763.0
_POP_H = 394.0
_HEADER_H = 80.0
_ROW_H    = 79.0   # each contact row


# ── Contact row ───────────────────────────────────────────────────────────────
class _ContactRow(BoxLayout):
    """Single tappable row: number badge + email text."""

    def __init__(self, index: int, label_text: str, badge_color: tuple,
                 on_tap, is_none: bool = False, **kw):
        super().__init__(
            orientation="horizontal",
            size_hint=(1, None),
            height=_ff(_ROW_H),
            padding=[_ff(25), _ff(10)],
            spacing=_ff(16),
            **kw,
        )
        self._on_tap  = on_tap
        self._index   = index
        self._is_none = is_none

        # row background (for tap highlight)
        with self.canvas.before:
            self._row_col = Color(1, 1, 1, 1)
            self._row_bg  = RoundedRectangle(radius=[0])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # Number badge  (36×36, full circle)
        badge = Widget(size_hint=(None, None), size=(_ff(36), _ff(36)))
        with badge.canvas:
            Color(*badge_color)
            badge_ell = RoundedRectangle(
                pos=badge.pos, size=badge.size,
                radius=[_ff(18)],
            )
        badge.bind(
            pos=lambda w, *_: setattr(badge_ell, "pos", w.pos),
            size=lambda w, *_: setattr(badge_ell, "size", w.size),
        )
        num_lbl = Label(
            text=str(index),
            font_name=_FONT_MD,
            font_size=_ff(32),
            color=_C_WHITE,
            halign="center",
            valign="middle",
            size_hint=(None, None),
            size=(_ff(36), _ff(36)),
            pos=badge.pos,
        )
        num_lbl.bind(size=num_lbl.setter("text_size"))
        # A plain Widget does not lay out its children, so the number label must
        # track the badge's position/size explicitly or it renders at (0, 0).
        badge.bind(
            pos=lambda w, p: setattr(num_lbl, "pos", p),
            size=lambda w, s: setattr(num_lbl, "size", s),
        )
        badge.add_widget(num_lbl)
        self.add_widget(badge)

        # Email / None label
        txt = Label(
            text=label_text,
            font_name=_FONT_MD,
            font_size=_ff(32),
            color=_C_ROW_TEXT,
            halign="left",
            valign="middle",
            size_hint=(1, 1),
            shorten=True,
            shorten_from="right",
        )
        txt.bind(size=txt.setter("text_size"))
        self.add_widget(txt)

    def _sync_bg(self, *_):
        self._row_bg.pos  = self.pos
        self._row_bg.size = self.size

    def flash_and_tap(self) -> None:
        """Highlight #BDDDF2 for 400 ms, then fire on_tap."""
        r, g, b, a = _C_ROW_HIGHLIGHT
        self._row_col.rgba = (r, g, b, a)
        Clock.schedule_once(lambda _dt: self._fire(), 0.4)

    def _fire(self) -> None:
        self._row_col.rgba = (1, 1, 1, 1)
        if self._on_tap:
            self._on_tap(self._index, self._is_none)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.flash_and_tap()
            return True
        return super().on_touch_down(touch)


# ── RecipientConfirmOverlay ───────────────────────────────────────────────────
class RecipientConfirmOverlay(FloatLayout):
    """Modal contact-confirmation overlay (touch + voice).

    Sits on ``root_layout`` above all screens. Starts hidden (opacity=0).
    """

    def __init__(self, on_select=None, on_dismiss=None, on_none=None, **kw):
        super().__init__(size_hint=(1, 1), **kw)
        self.on_select  = on_select
        self.on_dismiss = on_dismiss
        self.on_none    = on_none

        self._visible     = False
        self._candidates: list[dict] = []
        self.opacity = 0.0
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Full-screen scrim (approximates backdrop blur)
        scrim = Widget(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        with scrim.canvas:
            Color(*_C_SCRIM)
            _sr = Rectangle(pos=scrim.pos, size=scrim.size)
        scrim.bind(
            pos=lambda w, p: setattr(_sr, "pos", p),
            size=lambda w, s: setattr(_sr, "size", s),
        )
        self.add_widget(scrim)

        # ── Popup card "Frame 21" ─────────────────────────────────────────────
        # Figma: x=248 y=131  763×394  white  radius=29  shadow
        self._card = FloatLayout(
            size_hint=(None, None),
            size=(_ff(_POP_W), _ff(_POP_H)),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
        )
        with self._card.canvas.before:
            Color(0, 0, 0, 0.18)
            self._shad = RoundedRectangle(radius=[_ff(29)])
            Color(1, 1, 1, 1)
            self._card_bg = RoundedRectangle(radius=[_ff(29)])
        self._card.bind(pos=self._sync_card, size=self._sync_card)

        # ── Purple header (full width, h=80) ─────────────────────────────────
        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=_ff(_HEADER_H),
            pos_hint={"x": 0, "top": 1},
            padding=[_ff(20), 0, _ff(20), 0],
            spacing=_ff(16),
        )
        with header.canvas.before:
            Color(*_C_HEADER)
            self._hdr_bg = RoundedRectangle(
                radius=[_ff(29), _ff(29), 0, 0],
            )
        header.bind(pos=self._sync_header, size=self._sync_header)

        # Mail icon
        mail_src = _email_asset("icon_mail.png")
        if mail_src:
            header.add_widget(Image(
                source=mail_src,
                size_hint=(None, None),
                size=(_ff(44), _ff(44)),
                fit_mode="contain",
                allow_stretch=True,
                keep_ratio=True,
            ))
        else:
            # Fallback: simple ✉ text
            header.add_widget(Label(
                text="✉",
                font_name=_FONT_SB,
                font_size=_ff(36),
                color=_C_WHITE,
                size_hint=(None, 1),
                width=_ff(44),
            ))

        header.add_widget(Label(
            text="Contact",
            font_name=_FONT_SB,
            font_size=_ff(40),
            color=_C_WHITE,
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        ))
        self._card.add_widget(header)

        # ── Row list (scrollable when > 4 total rows) ─────────────────────────
        rows_top  = 1.0 - (_ff(_HEADER_H) / _ff(_POP_H))
        rows_h    = _ff(_POP_H) - _ff(_HEADER_H)
        self._rows_scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=_ff(4),
            bar_color=(*_C_HEADER[:3], 0.6),
            bar_inactive_color=(0.6, 0.6, 0.6, 0.3),
            size_hint=(1, None),
            height=rows_h,
            pos_hint={"x": 0, "y": 0},
        )
        self._rows_box = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            spacing=0,
        )
        self._rows_box.bind(minimum_height=self._rows_box.setter("height"))
        self._rows_scroll.add_widget(self._rows_box)
        self._card.add_widget(self._rows_scroll)

        self.add_widget(self._card)

    def _sync_card(self, *_):
        c = self._card
        self._shad.pos    = (c.x + 1, c.y - 5)
        self._shad.size   = (c.width + 2, c.height + 7)
        self._card_bg.pos  = c.pos
        self._card_bg.size = c.size

    def _sync_header(self, hdr, *_):
        self._hdr_bg.pos  = hdr.pos
        self._hdr_bg.size = hdr.size

    # ── Public API ────────────────────────────────────────────────────────────

    def show_candidates(self, query: str, candidates: list[dict]) -> None:
        """Build and reveal the picker from a list of candidate contacts."""
        self._candidates = [c for c in (candidates or []) if c.get("email")]
        self._rows_box.clear_widgets()

        n = len(self._candidates)

        for i, c in enumerate(self._candidates, start=1):
            email = (c.get("email") or "").strip()
            name  = (c.get("name") or "").strip()
            label = f"{name}  {email}" if name else email
            color = _BADGE_COLOURS[(i - 1) % 4]
            row   = _ContactRow(
                index=i, label_text=label, badge_color=color,
                on_tap=self._on_row_tap, is_none=False,
            )
            self._rows_box.add_widget(row)
            if i < n or True:  # always add separator (incl. before None)
                self._rows_box.add_widget(self._make_sep())

        # "None" row — takes next colour in sequence
        none_idx   = n + 1
        none_color = _BADGE_COLOURS[(none_idx - 1) % 4]
        none_row   = _ContactRow(
            index=none_idx, label_text="None", badge_color=none_color,
            on_tap=self._on_row_tap, is_none=True,
        )
        self._rows_box.add_widget(none_row)

        # Enable / disable scroll: scroll only when total rows > 4
        total_rows = n + 1   # contacts + None
        if total_rows <= 4:
            visible_h = total_rows * _ff(_ROW_H) + (total_rows - 1) * 1  # rows + seps
            self._rows_scroll.do_scroll_y = False
            self._rows_scroll.height = min(visible_h, _ff(_POP_H) - _ff(_HEADER_H))
        else:
            self._rows_scroll.do_scroll_y = True
            self._rows_scroll.height = 4 * _ff(_ROW_H) + 3   # show 4 rows + hint

        # Resize card to fit snugly
        new_card_h = _ff(_HEADER_H) + self._rows_scroll.height
        self._card.height = new_card_h

        self.show()

    def show(self) -> None:
        if self._visible:
            return
        self._visible = True
        Animation.cancel_all(self)
        Animation(opacity=1, duration=0.18, t="out_quad").start(self)

    def close(self) -> None:
        if not self._visible:
            return
        self._visible = False
        Animation.cancel_all(self)
        Animation(opacity=0, duration=0.15, t="in_quad").start(self)

    @property
    def visible(self) -> bool:
        return self._visible

    # ── Internal callbacks ─────────────────────────────────────────────────────

    def _on_row_tap(self, index: int, is_none: bool) -> None:
        """Called by _ContactRow.flash_and_tap() after the 400 ms highlight."""
        self.close()
        if is_none:
            if self.on_none:
                self.on_none()
        else:
            if 1 <= index <= len(self._candidates):
                contact = self._candidates[index - 1]
                if self.on_select:
                    self.on_select(index, contact)

    @staticmethod
    def _make_sep() -> Widget:
        sep = Widget(size_hint=(1, None), height=1)
        with sep.canvas:
            Color(*_C_SEP)
            r = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(
            pos=lambda *_: setattr(r, "pos", sep.pos),
            size=lambda *_: setattr(r, "size", sep.size),
        )
        return sep

    # ── Touch passthrough ─────────────────────────────────────────────────────

    def on_touch_down(self, touch):
        if self.opacity < 0.05:
            return False
        # Dismiss if touch is outside the card
        if self._card and not self._card.collide_point(*touch.pos):
            if self.on_dismiss:
                self.on_dismiss()
            else:
                self.close()
            touch.grab(self)
            return True
        super().on_touch_down(touch)
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
