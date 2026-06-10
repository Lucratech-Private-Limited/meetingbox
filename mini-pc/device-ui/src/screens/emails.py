"""Email inbox screen — Figma 970:170 (1260 × 800 px) — Rebuilt from scratch.

Split-pane layout (all coordinates from Figma node data):
  BACK BTN  : x=24.02  y=21   w=76.28  h=76.28   radius=115.57 (circular)
  HEADER    : x=23     y=104  w=1214   h=101    radius=22.60
  LEFT PANEL: x=23     y=212  w=535    h=567    radius=29.66  — email list
  RT PANEL  : x=570    y=212  w=667    h=567    radius=29.66  — email detail

No Figma PNG asset dependencies — all rendering via Kivy canvas + text.
Real Gmail data via the existing backend authentication + fetch infrastructure.
"""

from __future__ import annotations

import logging

from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import (
    Color, Ellipse, Line, Rectangle, RoundedRectangle,
)
from kivy.graphics.texture import Texture
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from api_client import _GMAIL_RECENT_DAYS
from config import DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Figma frame reference size
# ─────────────────────────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0

# ─────────────────────────────────────────────────────────────────────────────
# Colours — extracted from Figma fill / stroke values
# ─────────────────────────────────────────────────────────────────────────────
_C_BG       = (0.004, 0.031, 0.102, 1.0)   # #01081A  screen background
_C_WHITE    = (1.0,   1.0,   1.0,   1.0)   # #FFFFFF
_C_MUTED    = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2  secondary text
_C_BLUE     = (0.0,   0.420, 0.976, 1.0)   # #006BF9  accent / active

# Panel gradient  fill_J3SNP1  linear-gradient(180deg, #000F33 → #000A26)
_C_PNL_T    = (0.0,   0.059, 0.200, 1.0)   # #000F33
_C_PNL_B    = (0.0,   0.039, 0.149, 1.0)   # #000A26

# Header gradient  fill_JPIDSM  linear-gradient(180deg, #02123C → #000A26)
_C_HDR_T    = (0.008, 0.071, 0.235, 1.0)   # #02123C
_C_HDR_B    = _C_PNL_B

# Panel border gradient  fill_G6VJMP  136deg  #3F4253 → #161B35
_C_BDR_T    = (0.247, 0.259, 0.325, 1.0)   # #3F4253
_C_BDR_B    = (0.086, 0.106, 0.208, 1.0)   # #161B35

# Inner search-bar border  fill_MN8GXA  136deg  #21284B → #161B35
_C_SBR_T    = (0.129, 0.157, 0.294, 1.0)   # #21284B

# Selected-row border gradient  fill_0DFO5K  #3F8CFF → #0054D2
_C_SEL_T    = (0.247, 0.549, 1.0,   1.0)   # #3F8CFF
_C_SEL_B    = (0.0,   0.329, 0.824, 1.0)   # #0054D2

# Unread dot gradient  fill_T7FJ5P  #467DFE → #0058F4
_C_DOT_T    = (0.275, 0.490, 0.996, 1.0)   # #467DFE
_C_DOT_B    = (0.0,   0.345, 0.957, 1.0)   # #0058F4

# More-button gradient  fill_LKPGSJ  #011137 → #000A26
_C_MORE_T   = (0.004, 0.067, 0.216, 1.0)   # #011137

# Scrollbar track fill_AFCIFT  #010B26
_C_SB_TRK   = (0.004, 0.043, 0.149, 1.0)   # #010B26

# ─────────────────────────────────────────────────────────────────────────────
# Font families (registered in main.py)
# ─────────────────────────────────────────────────────────────────────────────
_F_REG  = "42dot-Sans"
_F_SB   = "42dot-SB"
_F_MED  = "42dot-Med"

# ─────────────────────────────────────────────────────────────────────────────
# Coordinate helpers  (Figma px → Kivy normalised)
# ─────────────────────────────────────────────────────────────────────────────

def _x(px: float) -> float:
    """Figma x → Kivy x_hint (left-edge, relative to 1260px frame)."""
    return px / _FW


def _y(top: float, h: float) -> float:
    """Figma top+h → Kivy y_hint (bottom-edge, Kivy is y-up)."""
    return max(0.0, (_FH - top - h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(val: float) -> int:
    """Scale a Figma pixel value by the display scale factor → int pixels."""
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(1, round(val * scale))


def _fy(parent_h: float, fig_top: float, elem_h: float) -> float:
    """
    Within a parent whose Figma height is parent_h, an element whose Figma
    top-edge is fig_top and whose height is elem_h:
    returns the Kivy normalised y (bottom-up).
    """
    return (parent_h - fig_top - elem_h) / parent_h


# ─────────────────────────────────────────────────────────────────────────────
# Gradient texture cache
# ─────────────────────────────────────────────────────────────────────────────
_GRAD: dict = {}


def _grad(top: tuple, bot: tuple) -> Texture:
    key = (top, bot)
    if key not in _GRAD:
        tex = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c): return [min(255, max(0, int(v * 255))) for v in c]
        tex.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        tex.wrap = "clamp_to_edge"
        _GRAD[key] = tex
    return _GRAD[key]


def _divider_tex() -> Texture:
    """4-stop vertical gradient for section dividers."""
    if "divider" not in _GRAD:
        tex = Texture.create(size=(1, 4), colorfmt="rgba")
        data = bytes([
            2,  23, 77,   0,
            6,  29, 88,  92,
            15, 41, 108, 255,
            2,  23, 77,   0,
        ])
        tex.blit_buffer(data, colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.wrap = "clamp_to_edge"
        _GRAD["divider"] = tex
    return _GRAD["divider"]


# ─────────────────────────────────────────────────────────────────────────────
# Label factory
# ─────────────────────────────────────────────────────────────────────────────

def _lbl(text: str, font: str, size, color: tuple, *,
         halign: str = "left", valign: str = "top", **kw) -> Label:
    lbl = Label(
        text=text, font_name=font, font_size=size, color=color,
        halign=halign, valign=valign, **kw,
    )
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


# ─────────────────────────────────────────────────────────────────────────────
# _Tap — touch-sensitive FloatLayout with optional drawn background
# ─────────────────────────────────────────────────────────────────────────────

class _Tap(ButtonBehavior, FloatLayout):
    def __init__(self, *, draw_bg: bool = False,
                 fill_top=None, fill_bot=None,
                 bdr_top=None, bdr_bot=None,
                 radius: float = 8, bdr_w: float = 1.0,
                 **kw):
        super().__init__(**kw)
        self._bg_rr = None
        self._bg_ln = None
        if not draw_bg:
            return
        ft = fill_top or _C_PNL_T
        fb = fill_bot or _C_PNL_B
        bt = bdr_top  or _C_BDR_T
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg_rr = RoundedRectangle(
                pos=self.pos, size=self.size,
                radius=[radius],
                texture=_grad(ft, fb),
            )
            Color(*bt)
            self._bg_ln = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=bdr_w,
            )
        self._tap_radius = radius
        self.bind(pos=self._sync_tap, size=self._sync_tap)

    def _sync_tap(self, *_):
        if self._bg_rr:
            self._bg_rr.pos  = self.pos
            self._bg_rr.size = self.size
        if self._bg_ln:
            self._bg_ln.rounded_rectangle = (
                self.x, self.y, self.width, self.height, self._tap_radius,
            )


# ─────────────────────────────────────────────────────────────────────────────
# _AvatarCircle — circular avatar with image or initials fallback
# ─────────────────────────────────────────────────────────────────────────────

class _AvatarCircle(Widget):
    def __init__(self, source: str = "", initials: str = "?", **kw):
        super().__init__(**kw)
        self._src      = source
        self._initials = initials
        self._tex: Texture | None = None
        self._lbl_ref: Label | None = None
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def update(self, source: str = "", initials: str = "?"):
        self._src      = source
        self._initials = initials
        self._tex      = None
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        if self._lbl_ref and self._lbl_ref.parent:
            self.remove_widget(self._lbl_ref)
            self._lbl_ref = None

        with self.canvas:
            if self._src:
                try:
                    if self._tex is None:
                        self._tex = CoreImage(self._src).texture
                    Color(1, 1, 1, 1)
                    Ellipse(pos=self.pos, size=self.size, texture=self._tex)
                    return
                except Exception:
                    pass
            # Gradient circle
            Color(1, 1, 1, 1)
            Ellipse(pos=self.pos, size=self.size, texture=_grad(_C_DOT_T, _C_DOT_B))

        lbl = Label(
            text=self._initials[:2].upper(),
            font_name=_F_SB,
            font_size=_ff(16),
            color=_C_WHITE,
            halign="center", valign="middle",
            size=self.size,
            pos=self.pos,
        )
        lbl.bind(size=lbl.setter("text_size"))
        self._lbl_ref = lbl
        self.add_widget(lbl)


# ─────────────────────────────────────────────────────────────────────────────
# _UnreadDot — small gradient ellipse (blue) or outline (read)
# ─────────────────────────────────────────────────────────────────────────────

class _UnreadDot(Widget):
    def __init__(self, unread: bool = True, **kw):
        super().__init__(**kw)
        self._unread = unread
        with self.canvas:
            if unread:
                Color(1, 1, 1, 1)
                self._e = Ellipse(pos=self.pos, size=self.size,
                                  texture=_grad(_C_DOT_T, _C_DOT_B))
            else:
                Color(*_C_MUTED[:3], 0.5)
                self._e = Line(
                    ellipse=(self.x, self.y, self.width, self.height),
                    width=0.9,
                )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        if self._unread:
            self._e.pos  = self.pos
            self._e.size = self.size
        else:
            self._e.ellipse = (self.x, self.y, self.width, self.height)


# ─────────────────────────────────────────────────────────────────────────────
# _EmailRow — single email row (selected or normal)
# ─────────────────────────────────────────────────────────────────────────────

class _EmailRow(ButtonBehavior, FloatLayout):
    """
    Figma layout (row-local coordinates, row anchored at top-left):

    Selected (480 × 122.61 px):
      dot     x=11.30  y_top=19     w=10.43  h=10.43
      sender  x=33.05  y_top=8.57   w=161    h=31    SB 26px white
      time    x=364.34 y_top=12.18  w=92     h=25    SB 21px blue  right-align
      subject x=33.05  y_top=47.70  w=362    h=27    SB 23px white
      preview x=33.05  y_top=82.48  w=389    h=24    SB 20px muted
      arrow   x=373.41 y_top=7.14   w=53.17  h=106

    Normal (440.84 × 89.73 px):
      dot     x=0      y_top=10.33  w=10.33  h=10.33
      sender  x=21.52  y_top=0      w=160    h=31    SB 26px white
      time    right-edge at 440.84  y_top=3.58 w=81   h=25  SB 21px blue
      subject x=21.52  y_top=38.73  w=358    h=27    SB 22px white
      preview x=21.52  y_top=65.73  w=389    h=24    SB 20px muted
    """

    # Selected-row Figma constants (row w=480, h=122.61)
    _S_RW, _S_RH = 480.0, 122.61
    # Normal-row Figma constants (row w=440.84, h=89.73)
    _N_RW, _N_RH = 440.84, 89.73

    def __init__(self, email: dict, selected: bool = False, **kw):
        super().__init__(**kw)
        self.email     = email
        self._selected = selected
        self._bg_rr    = None
        self._bg_ln    = None
        self._build()

    # ── canvas background ────────────────────────────────────────────────────

    def _build(self):
        email    = self.email
        selected = self._selected
        is_unread = not email.get("is_read", True)

        with self.canvas.before:
            if selected:
                # Figma: Rectangle 16 — transparent fill + blue gradient border
                Color(0.0,   0.055, 0.180, 0.35)   # faint blue fill for visibility
                self._bg_rr = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[_ff(11)],
                )
                Color(*_C_SEL_T)
                self._bg_ln = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, _ff(11)),
                    width=1.6,
                )
            elif is_unread:
                Color(1, 1, 1, 1)
                self._bg_rr = RoundedRectangle(
                    pos=self.pos, size=self.size, radius=[_ff(11)],
                    texture=_grad(_C_PNL_T, _C_PNL_B),
                )
                Color(*_C_BDR_T, 0.35)
                self._bg_ln = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, _ff(11)),
                    width=0.8,
                )
            # Read rows: fully transparent — panel background shows through

        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # ── child widgets via pos_hint (relative to this widget) ─────────────
        RW = self.width  or (self._S_RW if selected else self._N_RW)
        RH = self.height or (self._S_RH if selected else self._N_RH)
        RW = float(RW)
        RH = float(RH)

        if selected:
            self._add_selected_children(email, RW, RH)
        else:
            self._add_normal_children(email, RW, RH, is_unread)

    def _add_selected_children(self, email: dict, RW: float, RH: float):
        FW, FH = self._S_RW, self._S_RH  # Figma row dimensions

        # Unread dot  x=11.3, y_top=19, w=10.43, h=10.43
        self.add_widget(_UnreadDot(
            unread=True,
            size_hint=(10.43 / FW, 10.43 / FH),
            pos_hint={"x": 11.3 / FW, "y": _fy(FH, 19, 10.43)},
        ))

        # Right-side selection arrow  x=373.41, y_top=7.14, w=53.17, h=106.34
        arr = _lbl(
            "›",
            _F_SB, _ff(34), (*_C_BLUE[:3], 0.7),
            halign="center", valign="middle",
            size_hint=(53.17 / FW, 106.34 / FH),
            pos_hint={"x": 373.41 / FW, "y": _fy(FH, 7.14, 106.34)},
        )
        self.add_widget(arr)

        # Sender  x=33.05, y_top=8.57, w=161..→ 240, h=31,  SB 26px
        self.add_widget(_lbl(
            email.get("sender", ""),
            _F_SB, _ff(26), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(240 / FW, 31 / FH),
            pos_hint={"x": 33.05 / FW, "y": _fy(FH, 8.57, 31)},
        ))

        # Timestamp  x=364.34, y_top=12.18, w=92, h=25  SB 21px blue right-aligned
        self.add_widget(_lbl(
            email.get("time", ""),
            _F_SB, _ff(21), _C_BLUE,
            halign="right", valign="middle",
            size_hint=(92 / FW, 25 / FH),
            pos_hint={"x": 364.34 / FW, "y": _fy(FH, 12.18, 25)},
        ))

        # Subject  x=33.05, y_top=47.70, w=362, h=27  SB 23px white
        self.add_widget(_lbl(
            email.get("subject", ""),
            _F_SB, _ff(23), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(340 / FW, 27 / FH),
            pos_hint={"x": 33.05 / FW, "y": _fy(FH, 47.70, 27)},
        ))

        # Preview  x=33.05, y_top=82.48, w=389, h=24  SB 20px muted
        self.add_widget(_lbl(
            email.get("preview", ""),
            _F_SB, _ff(20), _C_MUTED,
            halign="left", valign="middle",
            size_hint=(340 / FW, 24 / FH),
            pos_hint={"x": 33.05 / FW, "y": _fy(FH, 82.48, 24)},
        ))

    def _add_normal_children(self, email: dict, RW: float, RH: float, is_unread: bool):
        FW, FH = self._N_RW, self._N_RH

        # Unread dot  x=0, y_top=10.33, w=10.33, h=10.33
        self.add_widget(_UnreadDot(
            unread=is_unread,
            size_hint=(10.33 / FW, 10.33 / FH),
            pos_hint={"x": 0 / FW, "y": _fy(FH, 10.33, 10.33)},
        ))

        # Sender  x=21.52, y_top=0, w=160→220, h=31  SB 26px white
        self.add_widget(_lbl(
            email.get("sender", ""),
            _F_SB, _ff(26), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(220 / FW, 31 / FH),
            pos_hint={"x": 21.52 / FW, "y": _fy(FH, 0, 31)},
        ))

        # Timestamp  right-edge at 440.84, y_top=3.58, w=81, h=25  SB 21px blue
        self.add_widget(_lbl(
            email.get("time", ""),
            _F_SB, _ff(21), _C_BLUE,
            halign="right", valign="middle",
            size_hint=(81 / FW, 25 / FH),
            pos_hint={"right": 1.0, "y": _fy(FH, 3.58, 25)},
        ))

        # Subject  x=21.52, y_top=38.73, w=358, h=27  SB 22px white
        self.add_widget(_lbl(
            email.get("subject", ""),
            _F_SB, _ff(22), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(310 / FW, 27 / FH),
            pos_hint={"x": 21.52 / FW, "y": _fy(FH, 38.73, 27)},
        ))

        # Preview  x=21.52, y_top=65.73, w=389, h=24  SB 20px muted
        self.add_widget(_lbl(
            email.get("preview", ""),
            _F_SB, _ff(20), _C_MUTED,
            halign="left", valign="middle",
            size_hint=(340 / FW, 24 / FH),
            pos_hint={"x": 21.52 / FW, "y": _fy(FH, 65.73, 24)},
        ))

    def _sync_bg(self, *_):
        if self._bg_rr:
            self._bg_rr.pos  = self.pos
            self._bg_rr.size = self.size
        if self._bg_ln:
            self._bg_ln.rounded_rectangle = (
                self.x, self.y, self.width, self.height, _ff(11),
            )


# ─────────────────────────────────────────────────────────────────────────────
# EmailsScreen
# ─────────────────────────────────────────────────────────────────────────────

class EmailsScreen(BaseScreen):
    """Split-pane Gmail inbox — Figma 970:170 (1260 × 800 px), rebuilt from zero."""

    # ── init ─────────────────────────────────────────────────────────────────

    def __init__(self, **kw):
        super().__init__(**kw)

        # State — inbox/sent/drafts are fetched and cached separately
        self._all_emails: list              = []   # inbox (all/today/unread)
        self._sent_emails: list             = []
        self._draft_emails: list            = []
        self._filtered_emails: list         = []
        self._selected_email: dict | None   = None
        self._active_tab: str               = "all"
        self._search_q: str                 = ""
        self._gmail_connected: bool         = True
        self._gmail_error: str | None       = None
        # Track whether sent/drafts have been loaded at least once this session
        self._sent_loaded: bool             = False
        self._drafts_loaded: bool           = False

        # Widget refs set during _build_ui
        self._tab_labels: dict[str, Label]  = {}
        self._count_labels: dict[str, Label] = {}
        self._list_container: FloatLayout | None = None
        self._list_sv: ScrollView | None = None
        self._list_pull_armed: bool = False
        self._pull_refresh_pending: bool = False
        self._avatar_w: _AvatarCircle | None = None
        self._det_sender: Label | None = None
        self._det_to_val: Label | None = None
        self._det_subject: Label | None = None
        self._det_body: Label | None   = None
        self._det_body_sv: ScrollView | None = None
        self._empty_lbl: Label | None  = None
        self._det_content: list        = []   # hidden until email selected

        self._build_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = FloatLayout(size_hint=(1, 1))

        # Screen background  #01081A
        with root.canvas.before:
            Color(*_C_BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, v: setattr(self._bg, "pos", v),
            size=lambda w, v: setattr(self._bg, "size", v),
        )

        self._build_back_btn(root)
        self._build_header(root)
        self._build_list_panel(root)
        self._build_detail_panel(root)

        self.add_widget(root)

    # ── Back button  970:171 ──────────────────────────────────────────────────
    # x=24.02, y=21, w=76.28, h=76.28, radius=115.57 (circular), fill=#010B26,
    # border=linear-gradient(145deg, #3F4253 14%, #161B35 51%)

    def _build_back_btn(self, root: FloatLayout):
        BX, BY, BW, BH = 24.02, 21, 76.28, 76.28
        btn = FloatLayout(
            size_hint=(_sw(BW), _sh(BH)),
            pos_hint={"x": _x(BX), "y": _y(BY, BH)},
        )
        with btn.canvas.before:
            Color(1, 1, 1, 1)
            self._back_bg = RoundedRectangle(
                pos=btn.pos, size=btn.size,
                radius=[_ff(BW / 2)],
                texture=_grad(_C_PNL_T, _C_BG),
            )
            Color(*_C_BDR_T)
            self._back_ln = Line(
                rounded_rectangle=(btn.x, btn.y, btn.width, btn.height, _ff(BW / 2)),
                width=1.0,
            )
        btn.bind(pos=self._sync_back, size=self._sync_back)

        btn.add_widget(_lbl(
            "←", _F_SB, _ff(22), _C_WHITE,
            halign="center", valign="middle",
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
        ))
        tap = _Tap(draw_bg=False, size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        tap.bind(on_release=lambda *_: self.go_back())
        btn.add_widget(tap)
        root.add_widget(btn)

    def _sync_back(self, w, *_):
        r = _ff(w.width / 2)
        self._back_bg.pos  = w.pos
        self._back_bg.size = w.size
        self._back_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, r)

    # ── Header bar  970:180 ──────────────────────────────────────────────────
    # x=23, y=104, w=1214, h=101, radius=22.60
    # fill=linear-gradient(180deg, #02123C, #000A26)
    # border=linear-gradient(136deg, #3F4253, #161B35)

    def _build_header(self, root: FloatLayout):
        BX, BY, BW, BH, R = 23, 104, 1214, 101, 22.60
        hdr = FloatLayout(
            size_hint=(_sw(BW), _sh(BH)),
            pos_hint={"x": _x(BX), "y": _y(BY, BH)},
        )
        with hdr.canvas.before:
            Color(1, 1, 1, 1)
            self._hdr_bg = RoundedRectangle(
                pos=hdr.pos, size=hdr.size,
                radius=[_ff(R)],
                texture=_grad(_C_HDR_T, _C_HDR_B),
            )
            Color(*_C_BDR_T)
            self._hdr_ln = Line(
                rounded_rectangle=(hdr.x, hdr.y, hdr.width, hdr.height, _ff(R)),
                width=1.0,
            )
        hdr.bind(pos=self._sync_hdr, size=self._sync_hdr)

        # "Emails" title  x=37, y=21, w=90, h=36  SB 30px white
        hdr.add_widget(_lbl(
            "Emails", _F_SB, _ff(30), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(90 / BW, 36 / BH),
            pos_hint={"x": 37 / BW, "y": _fy(BH, 21, 36)},
        ))

        # Tabs — 5 tabs across the header (BW=1214, search bar starts x=925)
        #   Today:  label x=37   count x=105
        #   All:    label x=175  count x=220
        #   Unread: label x=295  count x=378
        #   Sent:   label x=460  count x=510
        #   Drafts: label x=600  count x=668
        _tab_defs = [
            ("today",  "Today",  37,  105),
            ("all",    "All",    175, 220),
            ("unread", "Unread", 295, 378),
            ("sent",   "Sent",   460, 510),
            ("drafts", "Drafts", 600, 668),
        ]
        for tab_id, tab_name, tx, cx in _tab_defs:
            active = (tab_id == self._active_tab)
            lbl_col = _C_BLUE if active else _C_MUTED
            cnt_col = _C_BLUE if active else _C_WHITE

            tab_lbl = _lbl(
                tab_name, _F_SB, _ff(22), lbl_col,
                halign="left", valign="middle",
                size_hint=(None, 29 / BH),
                width=_ff(80),
                pos_hint={"x": tx / BW, "y": _fy(BH, 62, 29)},
            )
            self._tab_labels[tab_id] = tab_lbl
            hdr.add_widget(tab_lbl)

            cnt_lbl = _lbl(
                "0", _F_SB, _ff(22), cnt_col,
                halign="left", valign="middle",
                size_hint=(None, 29 / BH),
                width=_ff(30),
                pos_hint={"x": cx / BW, "y": _fy(BH, 62, 29)},
            )
            self._count_labels[tab_id] = cnt_lbl
            hdr.add_widget(cnt_lbl)

            # Tap area covers label + count
            tap_w = 130
            tap = _Tap(
                draw_bg=False,
                size_hint=(tap_w / BW, 45 / BH),
                pos_hint={"x": (tx - 4) / BW, "y": _fy(BH, 57, 45)},
            )
            tap.bind(on_release=lambda _, t=tab_id: self._on_tab(t))
            hdr.add_widget(tap)

        # Search bar  x=925, y=16, w=249, h=45, radius=16
        self._build_search_bar(hdr, BW, BH)

        root.add_widget(hdr)

    def _sync_hdr(self, w, *_):
        self._hdr_bg.pos  = w.pos
        self._hdr_bg.size = w.size
        self._hdr_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(22.60))

    def _build_search_bar(self, parent: FloatLayout, BW: float, BH: float):
        SX, SY, SW, SH = 925, 16, 249, 45
        sb = FloatLayout(
            size_hint=(SW / BW, SH / BH),
            pos_hint={"x": SX / BW, "y": _fy(BH, SY, SH)},
        )
        with sb.canvas.before:
            Color(0.004, 0.027, 0.090, 1.0)
            self._sb_bg = RoundedRectangle(pos=sb.pos, size=sb.size, radius=[_ff(16)])
            Color(*_C_SBR_T)
            self._sb_ln = Line(
                rounded_rectangle=(sb.x, sb.y, sb.width, sb.height, _ff(16)),
                width=1.0,
            )
        sb.bind(pos=self._sync_sb, size=self._sync_sb)

        # Search icon
        sb.add_widget(_lbl(
            "⌕", _F_MED, _ff(18), (*_C_MUTED[:3], 0.7),
            halign="center", valign="middle",
            size_hint=(None, 1), width=_ff(26),
            pos_hint={"x": 8 / SW, "y": 0},
        ))

        # Placeholder label (acts as search indicator)
        self._search_placeholder = _lbl(
            "Search emails", _F_MED, _ff(17), (*_C_MUTED[:3], 0.65),
            halign="left", valign="middle",
            size_hint=(0.75, 1),
            pos_hint={"x": 36 / SW, "y": 0},
        )
        sb.add_widget(self._search_placeholder)

        tap = _Tap(draw_bg=False, size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        tap.bind(on_release=self._on_search_tap)
        sb.add_widget(tap)
        parent.add_widget(sb)

    def _sync_sb(self, w, *_):
        self._sb_bg.pos  = w.pos
        self._sb_bg.size = w.size
        self._sb_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(16))

    # ── Left (list) panel  970:205 ───────────────────────────────────────────
    # x=23, y=212, w=535, h=567, radius=29.66
    # fill=linear-gradient(180deg, #000F33, #000A26)

    def _build_list_panel(self, root: FloatLayout):
        PX, PY, PW, PH, R = 23, 212, 535, 567, 29.66
        panel = FloatLayout(
            size_hint=(_sw(PW), _sh(PH)),
            pos_hint={"x": _x(PX), "y": _y(PY, PH)},
        )
        with panel.canvas.before:
            Color(1, 1, 1, 1)
            self._lp_bg = RoundedRectangle(
                pos=panel.pos, size=panel.size,
                radius=[_ff(R)],
                texture=_grad(_C_PNL_T, _C_PNL_B),
            )
            Color(*_C_BDR_T)
            self._lp_ln = Line(
                rounded_rectangle=(panel.x, panel.y, panel.width, panel.height, _ff(R)),
                width=1.0,
            )
        panel.bind(pos=self._sync_lp, size=self._sync_lp)

        # ScrollView (hide native scrollbar — we draw our own)
        sv = ScrollView(
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
            do_scroll_x=False,
            bar_width=0, bar_color=(0, 0, 0, 0), bar_inactive_color=(0, 0, 0, 0),
        )
        self._list_sv = sv
        sv.bind(scroll_y=self._on_list_scroll_y)
        self._list_container = FloatLayout(size_hint=(1, None), height=_ff(PH))
        sv.add_widget(self._list_container)
        panel.add_widget(sv)

        # Scrollbar track  x=519, y=49, w=9, h=456, fill=#010B26, radius=12
        # Thumb           x=519, y=320, w=9, h=72,  fill=#006BF9, radius=12
        self._build_list_scrollbar(panel, PW, PH)

        root.add_widget(panel)

    def _build_list_scrollbar(self, parent: FloatLayout, PW: float, PH: float):
        SX, SY, SW, SH = 519, 49, 9, 456

        track = FloatLayout(
            size_hint=(SW / PW, SH / PH),
            pos_hint={"x": SX / PW, "y": _fy(PH, SY, SH)},
        )
        with track.canvas.before:
            Color(*_C_SB_TRK)
            self._lst_trk = RoundedRectangle(
                pos=track.pos, size=track.size, radius=[_ff(12)],
            )
        track.bind(pos=self._sync_lst_trk, size=self._sync_lst_trk)

        # Thumb — initial position near bottom (y=320 in panel coords = 320px from top)
        # Normalized within track: (320-49)/(456) = 271/456 ≈ 0.59 from top → y = 1-0.59-72/456
        thumb_h_ratio = 72 / SH
        thumb = FloatLayout(
            size_hint=(1, thumb_h_ratio),
            pos_hint={"x": 0, "top": 1},
        )
        with thumb.canvas.before:
            Color(*_C_BLUE)
            self._lst_thm = RoundedRectangle(
                pos=thumb.pos, size=thumb.size, radius=[_ff(12)],
            )
        thumb.bind(pos=self._sync_lst_thm, size=self._sync_lst_thm)
        self._lst_thumb = thumb
        track.add_widget(thumb)
        parent.add_widget(track)

    def _sync_lp(self, w, *_):
        self._lp_bg.pos  = w.pos
        self._lp_bg.size = w.size
        self._lp_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(29.66))

    def _sync_lst_trk(self, w, *_):
        self._lst_trk.pos  = w.pos
        self._lst_trk.size = w.size

    def _sync_lst_thm(self, w, *_):
        self._lst_thm.pos  = w.pos
        self._lst_thm.size = w.size

    # ── Right (detail) panel  970:256 ────────────────────────────────────────
    # x=570, y=212, w=667, h=567, radius=29.66

    def _build_detail_panel(self, root: FloatLayout):
        PX, PY, PW, PH, R = 570, 212, 667, 567, 29.66
        panel = FloatLayout(
            size_hint=(_sw(PW), _sh(PH)),
            pos_hint={"x": _x(PX), "y": _y(PY, PH)},
        )
        with panel.canvas.before:
            Color(1, 1, 1, 1)
            self._dp_bg = RoundedRectangle(
                pos=panel.pos, size=panel.size,
                radius=[_ff(R)],
                texture=_grad(_C_PNL_T, _C_PNL_B),
            )
            Color(*_C_BDR_T)
            self._dp_ln = Line(
                rounded_rectangle=(panel.x, panel.y, panel.width, panel.height, _ff(R)),
                width=1.0,
            )
        panel.bind(pos=self._sync_dp, size=self._sync_dp)

        self._det_content = []

        def _track(w):
            """Register a detail widget (hidden until an email is selected)."""
            w.opacity = 0
            self._det_content.append(w)
            return w

        # ── Divider  x=28, y=57, w=611, h=3 ─────────────────────────────────
        div = _track(Widget(
            size_hint=(611 / PW, 3 / PH),
            pos_hint={"x": 28 / PW, "y": _fy(PH, 57, 3)},
        ))
        with div.canvas.before:
            Color(1, 1, 1, 1)
            self._div_rect = Rectangle(pos=div.pos, size=div.size,
                                       texture=_divider_tex())
        div.bind(
            pos=lambda w, v: setattr(self._div_rect, "pos", v),
            size=lambda w, v: setattr(self._div_rect, "size", v),
        )
        panel.add_widget(div)

        # ── Action bar  y=17 (panel-local) ────────────────────────────────────
        # Back  x=39, y=17, w=72, h=24
        back_btn = _track(self._action_btn("← Back", 39, 17, 72, 24, PW, PH,
                                           self._on_detail_back))
        panel.add_widget(back_btn)

        # Mark unread  x=133, y=17, w=134, h=24
        unread_btn = _track(self._action_btn("✉ Mark unread", 133, 17, 134, 24, PW, PH,
                                             self._on_mark_unread))
        panel.add_widget(unread_btn)

        # Archive  x=298, y=17, w=92, h=24
        arch_btn = _track(self._action_btn("⬚ Archive", 298, 17, 92, 24, PW, PH,
                                           self._on_archive))
        panel.add_widget(arch_btn)

        # More  x=528, y=8, w=101, h=42, radius=11,
        #       fill=linear-gradient(180deg, #011137, #000A26)
        more_btn = _track(self._more_btn(528, 8, 101, 42, PW, PH))
        panel.add_widget(more_btn)

        # ── Avatar  x=50, y=79, w=48, h=48 ───────────────────────────────────
        self._avatar_w = _track(_AvatarCircle(
            initials="?",
            size_hint=(48 / PW, 48 / PH),
            pos_hint={"x": 50 / PW, "y": _fy(PH, 79, 48)},
        ))
        panel.add_widget(self._avatar_w)

        # Unread dot  x=28, y=99, w=12, h=12
        det_dot = _track(_UnreadDot(
            unread=True,
            size_hint=(12 / PW, 12 / PH),
            pos_hint={"x": 28 / PW, "y": _fy(PH, 99, 12)},
        ))
        panel.add_widget(det_dot)

        # Sender  x=107, y=74, w=220, h=27  SB 23px white
        self._det_sender = _track(_lbl(
            "", _F_SB, _ff(23), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(None, 27 / PH),
            width=_ff(220),
            pos_hint={"x": 107 / PW, "y": _fy(PH, 74, 27)},
        ))
        panel.add_widget(self._det_sender)

        # "To:"  x=107, y=108, w=28, h=24  SB 20px muted
        panel.add_widget(_track(_lbl(
            "To:", _F_SB, _ff(20), _C_MUTED,
            halign="left", valign="middle",
            size_hint=(28 / PW, 24 / PH),
            pos_hint={"x": 107 / PW, "y": _fy(PH, 108, 24)},
        )))

        # To value  x=144, y=108, w=200, h=24  SB 20px blue
        self._det_to_val = _track(_lbl(
            "", _F_SB, _ff(20), _C_BLUE,
            halign="left", valign="middle",
            size_hint=(None, 24 / PH),
            width=_ff(220),
            pos_hint={"x": 144 / PW, "y": _fy(PH, 108, 24)},
        ))
        panel.add_widget(self._det_to_val)

        # Subject  x=32, y=146, w=580, h=30  SB 25px white
        self._det_subject = _track(_lbl(
            "", _F_SB, _ff(25), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(None, 30 / PH),
            width=_ff(580),
            pos_hint={"x": 32 / PW, "y": _fy(PH, 146, 30)},
        ))
        panel.add_widget(self._det_subject)

        # Body scroll view  x=32, y=189, w=580, h≈(567-189-20)=358
        body_h_px = PH - 189 - 20
        body_sv = _track(ScrollView(
            size_hint=(580 / PW, body_h_px / PH),
            pos_hint={"x": 32 / PW, "y": _fy(PH, 189, body_h_px)},
            do_scroll_x=False,
            bar_width=0, bar_color=(0, 0, 0, 0),
            bar_inactive_color=(0, 0, 0, 0),
        ))
        self._det_body_sv = body_sv
        self._det_body = Label(
            text="",
            font_name=_F_MED,
            font_size=_ff(18),
            color=_C_WHITE,
            halign="left",
            valign="top",
            size_hint=(1, None),
            line_height=1.45,
        )
        self._det_body.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, s: setattr(w, "height", s[1]),
        )
        self._det_body.bind(on_touch_down=self._on_detail_body_touch_down)
        body_sv.bind(on_touch_down=self._on_detail_body_touch_down)
        body_sv.add_widget(self._det_body)
        panel.add_widget(body_sv)

        # Empty-state label
        self._empty_lbl = _lbl(
            "Select an email to read",
            _F_MED, _ff(20), (*_C_MUTED[:3], 0.55),
            halign="center", valign="middle",
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
        )
        self._empty_lbl.opacity = 1
        panel.add_widget(self._empty_lbl)

        # Detail scrollbar  right edge of panel
        self._build_detail_scrollbar(panel, PW, PH)

        root.add_widget(panel)

    def _action_btn(self, label: str,
                    bx: float, by: float, bw: float, bh: float,
                    PW: float, PH: float, callback) -> _Tap:
        """Inline action button: icon+text, no background, 42dot Medium 18px."""
        btn = _Tap(
            draw_bg=False,
            size_hint=(bw / PW, bh / PH),
            pos_hint={"x": bx / PW, "y": _fy(PH, by, bh)},
        )
        btn.add_widget(_lbl(
            label, _F_MED, _ff(18), _C_WHITE,
            halign="left", valign="middle",
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
        ))
        btn.bind(on_release=lambda *_: callback())
        return btn

    def _more_btn(self, bx: float, by: float, bw: float, bh: float,
                  PW: float, PH: float) -> _Tap:
        """More button: x=528, y=8, w=101, h=42, radius=11."""
        btn = _Tap(
            draw_bg=True,
            fill_top=_C_MORE_T,
            fill_bot=_C_PNL_B,
            bdr_top=_C_SBR_T,
            radius=_ff(11),
            bdr_w=1.0,
            size_hint=(bw / PW, bh / PH),
            pos_hint={"x": bx / PW, "y": _fy(PH, by, bh)},
        )
        # "..." dots  x=14, y=5, w=23, h=24  Med 20px blue
        btn.add_widget(_lbl(
            "...", _F_MED, _ff(20), _C_BLUE,
            halign="center", valign="middle",
            size_hint=(None, None),
            width=_ff(24), height=_ff(24),
            pos_hint={"x": 14 / bw, "y": 5 / bh},
        ))
        # "More"  x=44, y=9, w=42, h=21  Med 18px blue
        btn.add_widget(_lbl(
            "More", _F_MED, _ff(18), _C_BLUE,
            halign="left", valign="middle",
            size_hint=(None, None),
            width=_ff(42), height=_ff(21),
            pos_hint={"x": 44 / bw, "y": 9 / bh},
        ))
        return btn

    def _build_detail_scrollbar(self, parent: FloatLayout, PW: float, PH: float):
        """Thin scrollbar on right edge of detail panel."""
        SX, SY, SW, SH = 650, 31, 9, 510
        track = FloatLayout(
            size_hint=(SW / PW, SH / PH),
            pos_hint={"x": SX / PW, "y": _fy(PH, SY, SH)},
        )
        with track.canvas.before:
            Color(*_C_SB_TRK)
            self._det_trk = RoundedRectangle(
                pos=track.pos, size=track.size, radius=[_ff(12)],
            )
        track.bind(pos=self._sync_det_trk, size=self._sync_det_trk)
        parent.add_widget(track)

    def _sync_dp(self, w, *_):
        self._dp_bg.pos  = w.pos
        self._dp_bg.size = w.size
        self._dp_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(29.66))

    def _sync_det_trk(self, w, *_):
        self._det_trk.pos  = w.pos
        self._det_trk.size = w.size

    # ─────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def on_enter(self):
        # Reset sent/drafts so they refresh on next tab visit
        self._sent_loaded   = False
        self._drafts_loaded = False
        # Keep tab deterministic on each open and ensure right pane defaults
        # to first email from the currently visible list.
        self._active_tab = "all"
        self._selected_email = None
        for tid, lbl in self._tab_labels.items():
            lbl.color = _C_BLUE if tid == "all" else _C_MUTED
        for tid, lbl in self._count_labels.items():
            lbl.color = _C_BLUE if tid == "all" else _C_WHITE
        self.app.ui_cache_subscribe("emails_inbox", self._on_cached_inbox)
        cached_inbox = self.app.ui_cache_get("emails_inbox")
        if isinstance(cached_inbox, list):
            self._all_emails = list(cached_inbox)
            self._update_counts()
            self._apply_filter()
            if self._filtered_emails:
                self._show_default_email()
        # Cache-fresh guard: centralized app sync loop keeps inbox hot.
        if not self.app.ui_cache_is_fresh("emails_inbox"):
            self._load_emails()

    def on_leave(self):
        self.app.ui_cache_unsubscribe("emails_inbox", self._on_cached_inbox)

    def _on_cached_inbox(self, rows: list):
        def _apply(_dt):
            if self.manager and self.manager.current != self.name:
                return
            self._all_emails = list(rows or [])
            self._update_counts()
            self._apply_filter()
            if self._filtered_emails and (
                self._selected_email is None
                or not any(e.get("id") == self._selected_email.get("id") for e in self._filtered_emails)
            ):
                self._show_default_email()
        Clock.schedule_once(_apply, 0)

    def _show_default_email(self):
        if not self._filtered_emails:
            return
        # Default preview must always be the top row, but opening screen
        # must not auto-mark it as read until the user explicitly taps.
        top = self._filtered_emails[0]
        self._selected_email = top
        self._show_detail(top)
        self._rebuild_list()

    def _on_list_scroll_y(self, _sv, scroll_y):
        # Pull-to-refresh behavior for left pane:
        # arm near top, refresh when user drags away/release.
        try:
            sy = float(scroll_y)
            if sy >= 0.995:
                self._list_pull_armed = True
                return
            if self._list_pull_armed and sy <= 0.98:
                self._list_pull_armed = False
                if not self._pull_refresh_pending:
                    self._pull_refresh_pending = True
                    self._refresh_all(force=True)
                    Clock.schedule_once(
                        lambda _dt: setattr(self, "_pull_refresh_pending", False),
                        1.0,
                    )
        except Exception:
            logger.debug("Emails pull-refresh handler failed", exc_info=True)

    def _refresh_all(self, force: bool = False):
        """Background refresh — reload whichever folders have been visited."""
        self._load_emails(force=force)
        if self._sent_loaded:
            self._load_folder("sent", force=force)
        if self._drafts_loaded:
            self._load_folder("drafts", force=force)

    # ─────────────────────────────────────────────────────────────────────────
    # Data layer (reuses existing backend / Gmail authentication)
    # ─────────────────────────────────────────────────────────────────────────

    def _load_emails(self, force: bool = False):
        """Load inbox emails (used for all/today/unread tabs)."""
        self._load_folder("inbox", force=force)

    def _load_folder(self, folder: str, force: bool = False):
        """
        Fetch emails for the given folder and update the matching cache.
        folder: "inbox" | "sent" | "drafts"
        """
        async def _fetch():
            emails: list       = []
            connected          = True
            err: str | None    = None
            api_folder         = "all" if folder == "inbox" else folder
            cache_key = {
                "inbox": "emails_inbox",
                "sent": "emails_sent",
                "drafts": "emails_drafts",
            }.get(folder)
            if cache_key:
                cached = self.app.ui_cache_get(cache_key)
                if isinstance(cached, list) and folder == "inbox":
                    def _paint_cached(_dt):
                        self._all_emails = list(cached)
                        self._update_counts()
                        self._apply_filter()
                    Clock.schedule_once(_paint_cached, 0)
            try:
                if cache_key and not force and self.app.ui_cache_is_fresh(cache_key):
                    return
                if cache_key and not self.app.ui_cache_mark_inflight(cache_key):
                    return
                fn = getattr(self.backend, "fetch_gmail_recent", None)
                if fn is not None:
                    data      = await fn(
                        max_results=50,
                        days=_GMAIL_RECENT_DAYS,
                        q="",
                        folder=api_folder,
                    )
                    connected = bool(data.get("connected"))
                    err       = (data.get("error") or "").strip() or None
                    rows      = data.get("messages") or []
                    if isinstance(rows, list):
                        emails = [m for m in rows if isinstance(m, dict) and m.get("id")]
                else:
                    emails = await self.backend.get_emails(filter=api_folder, limit=50)
            except Exception as exc:
                logger.warning("EmailsScreen._load_folder(%s): %s", folder, exc)
                emails    = []
                connected = False
                err       = str(exc) or None

            def _apply(_dt):
                is_auth_error = err and (
                    "401" in err or "403" in err
                    or "not authenticated" in err
                    or "not connected" in err
                )

                if folder == "sent":
                    # Keep existing sent emails on background refresh failure
                    if not connected and self._sent_emails and not is_auth_error:
                        return
                    self._sent_loaded  = True
                    self._sent_emails  = emails
                    self._update_counts()
                    if self._active_tab == "sent":
                        self._apply_filter()
                    return

                if folder == "drafts":
                    # Keep existing drafts on background refresh failure
                    if not connected and self._draft_emails and not is_auth_error:
                        return
                    self._drafts_loaded  = True
                    self._draft_emails   = emails
                    self._update_counts()
                    if self._active_tab == "drafts":
                        self._apply_filter()
                    return

                # ── inbox ────────────────────────────────────────────────
                if not connected and self._all_emails and not is_auth_error:
                    logger.debug("EmailsScreen: inbox refresh failed (%s), keeping current", err)
                    return

                self._gmail_connected = connected
                self._gmail_error     = err
                self._all_emails      = emails
                if cache_key and isinstance(emails, list):
                    self.app.ui_cache_set(cache_key, list(emails))
                self._update_counts()
                if self._active_tab == "today" and not any(
                    e.get("is_today") for e in emails
                ):
                    self._set_tab("all")
                elif self._active_tab not in ("sent", "drafts"):
                    self._apply_filter()
                # Always keep selection aligned with currently filtered list.
                if self._filtered_emails:
                    if (
                        self._selected_email is None
                        or not any(
                            e.get("id") == self._selected_email.get("id")
                            for e in self._filtered_emails
                        )
                    ):
                        self._show_default_email()
                else:
                    self._selected_email = None
                    if self._det_sender:
                        self._det_sender.text = ""
                    if self._det_subject:
                        self._det_subject.text = ""
                    if self._det_body:
                        self._det_body.text = ""
                    for w in self._det_content:
                        w.opacity = 0.0
                    if self._empty_lbl:
                        self._empty_lbl.opacity = 1.0

            Clock.schedule_once(_apply, 0)
            if cache_key:
                self.app.ui_cache_clear_inflight(cache_key)

        run_async(_fetch())

    def _update_counts(self):
        today_n  = sum(1 for e in self._all_emails if e.get("is_today"))
        all_n    = len(self._all_emails)
        unread_n = sum(1 for e in self._all_emails if not e.get("is_read"))
        counts   = {
            "today":  today_n,
            "all":    all_n,
            "unread": unread_n,
            "sent":   len(self._sent_emails),
            "drafts": len(self._draft_emails),
        }
        for tid, lbl in self._count_labels.items():
            lbl.text = str(counts.get(tid, 0))

    def _apply_filter(self):
        tab = self._active_tab
        q   = self._search_q.lower().strip()

        if tab == "sent":
            base = list(self._sent_emails)
        elif tab == "drafts":
            base = list(self._draft_emails)
        elif tab == "today":
            base = [e for e in self._all_emails if e.get("is_today")]
        elif tab == "unread":
            base = [e for e in self._all_emails if not e.get("is_read")]
        else:
            base = list(self._all_emails)

        if q:
            base = [
                e for e in base
                if q in (e.get("sender")  or "").lower()
                or q in (e.get("subject") or "").lower()
                or q in (e.get("preview") or "").lower()
            ]

        self._filtered_emails = base
        self._rebuild_list()

    # ─────────────────────────────────────────────────────────────────────────
    # List rendering
    # ─────────────────────────────────────────────────────────────────────────

    def _rebuild_list(self):
        if self._list_container is None:
            return
        self._list_container.clear_widgets()
        emails = self._filtered_emails
        placed_widgets: list[Widget] = []

        # ── Error / empty states ─────────────────────────────────────────────
        if not self._gmail_connected:
            err = (self._gmail_error or "").lower()
            if "401" in err or "not authenticated" in err:
                msg = (
                    "Device not paired.\n"
                    "Open Settings \u2192 Pair Device and link this\n"
                    "device to your account first."
                )
            elif "403" in err or "not connected" in err:
                msg = (
                    "Gmail not connected.\n"
                    "Open the web dashboard \u2192 Settings \u2192\n"
                    "Integrations \u2192 Gmail and connect."
                )
            elif "timeout" in err or "connect" in err or "network" in err:
                msg = (
                    "Cannot reach server.\n"
                    "Check internet connection and that the\n"
                    "backend is running, then pull to refresh."
                )
            else:
                msg = (
                    "Connect Gmail in the web dashboard:\n"
                    "Settings \u2192 Integrations \u2192 Gmail.\n"
                    "Then reopen Emails here."
                )
            self._list_container.add_widget(_lbl(
                msg,
                _F_MED, _ff(18), _C_MUTED,
                halign="center", valign="middle",
                size_hint=(None, None),
                width=_ff(460), height=_ff(110),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
            ))
            self._list_container.height = _ff(567)
            return

        if not emails and self._gmail_error:
            self._list_container.add_widget(_lbl(
                self._gmail_error,
                _F_MED, _ff(16), (0.9, 0.75, 0.35, 1.0),
                halign="center", valign="middle",
                size_hint=(None, None),
                width=_ff(460), height=_ff(80),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
            ))
            self._list_container.height = _ff(567)
            return

        if not emails:
            msg = "No results" if self._search_q else "No emails"
            self._list_container.add_widget(_lbl(
                msg, _F_MED, _ff(18), _C_MUTED,
                halign="center", valign="middle",
                size_hint=(None, None),
                width=_ff(200), height=_ff(40),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
            ))
            self._list_container.height = _ff(567)
            return

        # ── Layout constants (Figma, panel-local, 535×567) ───────────────────
        # Selected row:  x=28, w=480, h=122.61
        # Normal row:    x=41.59, w=440.84, h=89.73
        SEL_X  = _ff(28)
        NRM_X  = _ff(42)          # ≈41.59
        SEL_W  = _ff(480)
        NRM_W  = _ff(440)         # ≈440.84

        today_emails   = [e for e in emails if e.get("is_today")]
        earlier_emails = [e for e in emails if not e.get("is_today")]

        # y_cursor starts at the TOP of the content area (highest absolute y)
        # and moves downward.  We'll compute absolute pixel heights and use
        # the Kivy convention where pos.y is the BOTTOM edge.
        PH_PX = _ff(567)
        y_cur = PH_PX   # start at top

        def _section_label(text: str):
            nonlocal y_cur
            lbl_h = _ff(24)
            y_cur -= _ff(11)   # top padding
            lbl = _lbl(
                text, _F_SB, _ff(20), _C_BLUE,
                size_hint=(None, None),
                width=_ff(90), height=lbl_h,
            )
            lbl.pos = (SEL_X, y_cur - lbl_h)
            self._list_container.add_widget(lbl)
            placed_widgets.append(lbl)
            y_cur -= lbl_h + _ff(6)

        def _add_row(email: dict, row_h: int, row_w: int, row_x: int):
            nonlocal y_cur
            selected = (
                self._selected_email is not None
                and self._selected_email.get("id") == email.get("id")
            )
            row = _EmailRow(
                email=email,
                selected=selected,
                size_hint=(None, None),
                size=(row_w, row_h),
            )
            # Container origin is bottom; stack from top by converting y_cur into
            # a bottom-based y that grows with content height.
            row.pos = (row_x, y_cur - row_h)
            row.bind(on_release=lambda r, e=email: self._select_email(e))
            self._list_container.add_widget(row)
            placed_widgets.append(row)
            y_cur -= row_h + _ff(10)

        # NEW section
        if today_emails:
            _section_label("NEW")
            for em in today_emails:
                is_unread = not em.get("is_read", True)
                sel = (self._selected_email is not None
                       and self._selected_email.get("id") == em.get("id"))
                if sel:
                    _add_row(em, _ff(122.61), SEL_W, SEL_X)
                elif is_unread:
                    _add_row(em, _ff(122.61), SEL_W, SEL_X)
                else:
                    _add_row(em, _ff(90), NRM_W, NRM_X)

        # Divider between sections
        if today_emails and earlier_emails:
            y_cur -= _ff(8)
            div = Widget(size_hint=(None, None), size=(_ff(478), _ff(3)))
            div.pos = (SEL_X + _ff(1), y_cur - _ff(3))
            with div.canvas.before:
                Color(1, 1, 1, 1)
                Rectangle(pos=div.pos, size=div.size, texture=_divider_tex())
            self._list_container.add_widget(div)
            placed_widgets.append(div)
            y_cur -= _ff(3) + _ff(8)

        # EARLIER section
        if earlier_emails:
            _section_label("EARLIER")
            for em in earlier_emails:
                sel = (self._selected_email is not None
                       and self._selected_email.get("id") == em.get("id"))
                if sel:
                    _add_row(em, _ff(122.61), SEL_W, SEL_X)
                else:
                    _add_row(em, _ff(90), NRM_W, NRM_X)

        used = PH_PX - y_cur
        self._list_container.height = max(PH_PX, used + _ff(24))
        # Keep the first row pinned to the top of the scroll content even when
        # content height exceeds one viewport; this makes all rows reachable.
        top_offset = self._list_container.height - PH_PX
        if top_offset > 0:
            for w in placed_widgets:
                w.y += top_offset

    # ─────────────────────────────────────────────────────────────────────────
    # Interactions
    # ─────────────────────────────────────────────────────────────────────────

    def set_active_tab(self, tab_id: str) -> None:
        """Called by main.py to jump to a specific tab via voice command.

        Tab ids: today, all, unread, sent, drafts.
        Safe to call before or after on_enter; stores tab and triggers lazy
        loads for sent/drafts if needed.
        """
        valid = {"today", "all", "unread", "sent", "drafts"}
        if tab_id not in valid:
            return
        if tab_id == self._active_tab:
            # Already on the right tab — force a visual refresh if labels exist.
            if self._tab_labels:
                self._set_tab(tab_id)
            return
        self._on_tab(tab_id)

    def _on_tab(self, tab_id: str):
        if tab_id == self._active_tab:
            return
        self._set_tab(tab_id)
        # Lazy-load sent/drafts on first visit to that tab
        if tab_id == "sent" and not self._sent_loaded:
            self._load_folder("sent", force=True)
        elif tab_id == "drafts" and not self._drafts_loaded:
            self._load_folder("drafts", force=True)

    def _set_tab(self, tab_id: str):
        self._active_tab = tab_id
        for tid, lbl in self._tab_labels.items():
            lbl.color = _C_BLUE if tid == tab_id else _C_MUTED
        for tid, lbl in self._count_labels.items():
            lbl.color = _C_BLUE if tid == tab_id else _C_WHITE
        self._apply_filter()

    def _on_search_tap(self, *_):
        """Cycle through search mode or clear (keyboard not available on device)."""
        pass   # Full keyboard search is an optional future enhancement

    def _select_email(self, email: dict):
        self._selected_email = email
        self._mark_selected_as_read(email)
        self._show_detail(email)
        self._rebuild_list()

    def _mark_selected_as_read(self, email: dict | None = None):
        email = email or self._selected_email
        if not email:
            return
        was_unread = not bool(email.get("is_read", True))
        if not was_unread:
            return
        email["is_read"] = True
        target_id = email.get("id")
        if target_id:
            self.app.ui_set_email_read_override(target_id, True)
        self.app.ui_cache_set("emails_inbox", list(self._all_emails))
        self._update_counts()

        bundle = self.app.ui_cache_get("home_summary_bundle")
        if isinstance(bundle, dict):
            gfeed = bundle.get("gfeed") if isinstance(bundle.get("gfeed"), dict) else {}
            msgs = gfeed.get("messages") if isinstance(gfeed.get("messages"), list) else []
            changed = False
            for m in msgs:
                if isinstance(m, dict) and m.get("id") == target_id and not m.get("is_read", True):
                    m["is_read"] = True
                    changed = True
                    break
            if changed:
                self.app.ui_cache_set("home_summary_bundle", dict(bundle))

        async def _mark_read():
            try:
                await self.backend.mark_email_read(email.get("id"))
            except Exception as exc:
                logger.debug("mark_email_read: %s", exc)

        run_async(_mark_read())

    def _show_detail(self, email: dict):
        """Populate the right panel with email metadata, then async-fetch full body."""
        if self._det_sender:
            self._det_sender.text = email.get("sender", "")
        if self._det_to_val:
            raw_to = email.get("to", "")
            self._det_to_val.text = raw_to.split("<")[0].strip() or "me"
        if self._det_subject:
            self._det_subject.text = email.get("subject", "")
        if self._det_body:
            self._det_body.text = email.get("preview", email.get("body", ""))
        if self._avatar_w:
            sender  = email.get("sender", "?")
            initials = "".join(w[0] for w in sender.split() if w)[:2].upper() or "?"
            self._avatar_w.update(initials=initials)

        for w in self._det_content:
            w.opacity = 1.0
        if self._empty_lbl:
            self._empty_lbl.opacity = 0.0

        # Async fetch of full body
        email_id = email.get("id")

        async def _fetch_body():
            try:
                detail = await self.backend.get_email_detail(email_id)
                body = (
                    detail.get("body") or detail.get("snippet")
                    or detail.get("text") or ""
                ).strip()
            except Exception:
                body = ""

            def _apply(_dt):
                if (self._selected_email is not None
                        and self._selected_email.get("id") == email_id
                        and self._det_body is not None):
                    if body:
                        self._det_body.text = body

            Clock.schedule_once(_apply, 0)

        run_async(_fetch_body())

    def _on_detail_body_touch_down(self, widget, touch):
        target = self._det_body_sv if self._det_body_sv is not None else widget
        if not target.collide_point(*touch.pos):
            return False
        if self._selected_email is not None:
            self._mark_selected_as_read(self._selected_email)
            self._rebuild_list()
        return False

    def _on_detail_back(self):
        self._selected_email = None
        if self._det_sender:  self._det_sender.text  = ""
        if self._det_subject: self._det_subject.text = ""
        if self._det_body:    self._det_body.text    = ""
        for w in self._det_content:
            w.opacity = 0.0
        if self._empty_lbl:
            self._empty_lbl.opacity = 1.0
        self._rebuild_list()

    def _on_mark_unread(self):
        email = self._selected_email
        if not email:
            return
        email["is_read"] = False
        target_id = email.get("id")
        if target_id:
            self.app.ui_set_email_read_override(target_id, False)
        self.app.ui_cache_set("emails_inbox", list(self._all_emails))
        self._rebuild_list()
        self._update_counts()
        bundle = self.app.ui_cache_get("home_summary_bundle")
        if isinstance(bundle, dict):
            gfeed = bundle.get("gfeed") if isinstance(bundle.get("gfeed"), dict) else {}
            msgs = gfeed.get("messages") if isinstance(gfeed.get("messages"), list) else []
            target_id = email.get("id")
            changed = False
            for m in msgs:
                if isinstance(m, dict) and m.get("id") == target_id and m.get("is_read", True):
                    m["is_read"] = False
                    changed = True
                    break
            if changed:
                self.app.ui_cache_set("home_summary_bundle", dict(bundle))

        async def _call():
            try:
                await self.backend.mark_email_unread(email["id"])
            except Exception as exc:
                logger.debug("mark_email_unread: %s", exc)

        run_async(_call())

    def _on_archive(self):
        email = self._selected_email
        if not email:
            return
        self._all_emails     = [e for e in self._all_emails
                                 if e.get("id") != email.get("id")]
        self._selected_email = None
        if self._det_body:    self._det_body.text    = ""
        if self._det_sender:  self._det_sender.text  = ""
        if self._det_subject: self._det_subject.text = ""
        for w in self._det_content:
            w.opacity = 0.0
        if self._empty_lbl:
            self._empty_lbl.opacity = 1.0
        self._update_counts()
        self._apply_filter()

        async def _call():
            try:
                await self.backend.archive_email(email["id"])
            except Exception as exc:
                logger.debug("archive_email: %s", exc)

        run_async(_call())
