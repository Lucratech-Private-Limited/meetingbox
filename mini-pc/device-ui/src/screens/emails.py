"""Email inbox screen — pixel-perfect Figma 970:170 (1260 × 800 px).

Split-panel layout:
  LEFT  (23, 212)  535 × 567 — scrollable email list with NEW / EARLIER sections
  RIGHT (570, 212) 667 × 567 — email detail view

All coordinates, colours and font sizes are taken directly from the Figma node data.
All icons and action-button images come from downloaded Figma assets.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import (
    Color, Ellipse, Line, PopMatrix, PushMatrix,
    Rectangle, RoundedRectangle, Scale,
)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from api_client import _GMAIL_RECENT_DAYS
from config import ASSETS_DIR, COLORS, DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Figma frame constants  (970:170, 1260 × 800)
# ---------------------------------------------------------------------------
_FW, _FH = 1260.0, 800.0
_FIGMA_DIR = ASSETS_DIR / "home" / "figma"

# Colours from Figma node data
_WHITE  = (1.0,   1.0,   1.0,   1.0)
_MUTED  = (0.714, 0.729, 0.949, 1.0)   # #B6BAF2
_BLUE   = (0.0,   0.420, 0.976, 1.0)   # #006BF9
_BG     = (0.004, 0.031, 0.102, 1.0)   # #01081A

# Panel / card gradients
_PANEL_TOP = (0.0,   0.059, 0.200, 1.0)  # #000F33
_PANEL_BOT = (0.0,   0.039, 0.149, 1.0)  # #000A26
_HDR_TOP   = (0.008, 0.071, 0.235, 1.0)  # #02123C
_HDR_BOT   = (0.0,   0.039, 0.149, 1.0)  # #000A26
_BDR_PANEL = (0.129, 0.157, 0.294, 1.0)  # ~#21284B
_BDR_HDR   = (0.247, 0.259, 0.325, 1.0)  # ~#3F4253
_ROW_SEL_BDR_TOP = (0.247, 0.549, 1.0,   1.0)  # #3F8CFF
_ROW_SEL_BDR_BOT = (0.0,   0.329, 0.824, 1.0)  # #0054D2
_DOT_TOP   = (0.275, 0.490, 0.996, 1.0)  # #467DFE
_DOT_BOT   = (0.0,   0.345, 0.957, 1.0)  # #0058F4
_MORE_TOP  = (0.004, 0.067, 0.216, 1.0)  # #011138
_MORE_BOT  = (0.0,   0.039, 0.149, 1.0)  # #000A26

# Font families registered in main.py
_FONT    = "42dot-Sans"
_FONT_SB = "42dot-SB"
_FONT_MD = "42dot-Med"


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _x(px: float) -> float:     return px / _FW
def _y(top: float, h: float) -> float: return max(0.0, (_FH - top - h) / _FH)
def _sw(px: float) -> float:    return px / _FW
def _sh(px: float) -> float:    return px / _FH
def _ff(fs: float) -> int:
    scale = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * scale))


def _fp(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Gradient texture cache
# ---------------------------------------------------------------------------
_GRAD_CACHE: dict = {}

def _grad(top: tuple, bot: tuple):
    from kivy.graphics.texture import Texture
    key = (top, bot)
    if key not in _GRAD_CACHE:
        tex = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c): return [min(255, max(0, int(x * 255))) for x in c]
        tex.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        tex.wrap = "clamp_to_edge"
        _GRAD_CACHE[key] = tex
    return _GRAD_CACHE[key]


# ---------------------------------------------------------------------------
# Label factory
# ---------------------------------------------------------------------------

def _lbl(text, font, size, color, *, bold=False, halign="left", valign="top", **kw) -> Label:
    lbl = Label(text=text, font_name=font, font_size=size, bold=bold,
                color=color, halign=halign, valign=valign, **kw)
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


# ---------------------------------------------------------------------------
# Tappable card (button + FloatLayout)
# ---------------------------------------------------------------------------

class _Tap(ButtonBehavior, FloatLayout):
    def __init__(self, draw_bg=False, top=None, bot=None, border=None, radius=8, **kw):
        super().__init__(**kw)
        if draw_bg:
            _t = top or _PANEL_TOP
            _bo = bot or _PANEL_BOT
            _br = border or _BDR_PANEL
            with self.canvas.before:
                Color(1, 1, 1, 1)
                self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                            radius=[radius], texture=_grad(_t, _bo))
                Color(*_br)
                self._ln = Line(rounded_rectangle=(self.x, self.y, self.width,
                                                   self.height, radius), width=1.0)
            self.bind(pos=self._sync, size=self._sync)
        else:
            self._bg = None

    def _sync(self, *_):
        if self._bg:
            self._bg.pos = self.pos
            self._bg.size = self.size


# ---------------------------------------------------------------------------
# Avatar circle widget
# ---------------------------------------------------------------------------

class _AvatarCircle(Widget):
    """Renders an image clipped to a circle, or initials if no image."""

    def __init__(self, source: str = "", initials: str = "?", **kw):
        super().__init__(**kw)
        self._src = source
        self._initials = initials
        self._tex = None
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def _draw(self, *_):
        self.canvas.clear()
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
            # Fallback: coloured circle with initials drawn via Label
            Color(*_DOT_TOP[:3], 1)
            Ellipse(pos=self.pos, size=self.size)


# ---------------------------------------------------------------------------
# Email list row widget
# ---------------------------------------------------------------------------

class _EmailRow(ButtonBehavior, FloatLayout):
    """Single email row rendered to match the Figma design exactly."""

    def __init__(self, email: dict, selected: bool = False, **kw):
        super().__init__(**kw)
        self.email = email
        self._selected = selected
        self._build(email, selected)

    def _build(self, email: dict, selected: bool):
        is_unread = not email.get("is_read", True)
        RW = self.width or 480
        RH = self.height or (122.61 if (selected or is_unread) else 90)

        with self.canvas.before:
            # Background
            Color(1, 1, 1, 1)
            self._bg_rect = RoundedRectangle(
                pos=self.pos, size=self.size,
                radius=[_ff(11)],
                texture=_grad(_PANEL_TOP, _PANEL_BOT),
            )
            if selected:
                # Blue border for selected row (fill_D00RWF)
                Color(*_ROW_SEL_BDR_TOP)
                self._border = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, _ff(11)),
                    width=1.5,
                )
            elif is_unread:
                Color(*_BDR_PANEL)
                self._border = Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, _ff(11)),
                    width=1.0,
                )
            else:
                self._border = None

        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # Unread dot
        dot_src = _fp("email_dot_unread.png")
        if dot_src:
            self.add_widget(Image(
                source=dot_src,
                size_hint=(None, None),
                size=(_ff(10), _ff(10)),
                pos_hint={"x": 11 / 480, "y": None},
                fit_mode="contain",
            ))
        else:
            # Canvas dot
            with self.canvas:
                if is_unread:
                    Color(*_DOT_TOP)
                    Ellipse(pos=(self.x + _ff(11), self.y + self.height - _ff(29),
                                 ), size=(_ff(10), _ff(10)))

        # Selected row arrow (right side)
        if selected:
            arr_src = _fp("email_row_selected_arrow.png")
            if arr_src:
                self.add_widget(Image(
                    source=arr_src,
                    size_hint=(53.17 / 480, None),
                    height=self.height * 0.86,
                    pos_hint={"right": 1.0, "center_y": 0.5},
                    fit_mode="contain",
                ))

        # Sender name
        sender_font_size = _ff(26) if (selected or is_unread) else _ff(26)
        self.add_widget(_lbl(
            email.get("sender", ""),
            _FONT_SB, sender_font_size, _WHITE,
            size_hint=(0.72, None),
            height=_ff(31),
            pos_hint={"x": 33 / 480, "top": 0.92},
        ))

        # Timestamp
        self.add_widget(_lbl(
            email.get("time", ""),
            _FONT_SB, _ff(21), _MUTED,
            halign="right",
            size_hint=(0.20, None),
            height=_ff(25),
            pos_hint={"right": 0.95, "top": 0.90},
        ))

        # Subject
        self.add_widget(_lbl(
            email.get("subject", ""),
            _FONT_SB, _ff(22), _WHITE,
            size_hint=(0.88, None),
            height=_ff(27),
            pos_hint={"x": 33 / 480, "top": 0.64},
        ))

        # Preview (only for unread/selected rows — bigger height)
        if selected or is_unread:
            self.add_widget(_lbl(
                email.get("preview", ""),
                _FONT, _ff(20), _MUTED, bold=True,
                size_hint=(0.90, None),
                height=_ff(24),
                pos_hint={"x": 33 / 480, "top": 0.38},
            ))

    def _sync_bg(self, *_):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        if self._border:
            self._border.rounded_rectangle = (
                self.x, self.y, self.width, self.height, _ff(11)
            )


# ---------------------------------------------------------------------------
# EmailsScreen
# ---------------------------------------------------------------------------

class EmailsScreen(BaseScreen):
    """Split-panel email inbox — Figma 970:170 (1260 × 800 px)."""

    def __init__(self, **kw):
        super().__init__(**kw)

        self._all_emails: list = []
        self._filtered_emails: list = []
        self._selected_email: dict | None = None
        self._active_tab: str = "all"
        self._gmail_connected: bool = True
        self._gmail_error: str | None = None

        # Dynamic label refs
        self._tab_labels: dict = {}          # tab -> Label
        self._count_labels: dict = {}        # tab -> Label
        self._list_container: FloatLayout | None = None
        self._detail_panel: FloatLayout | None = None
        self._detail_sender_lbl: Label | None = None
        self._detail_subject_lbl: Label | None = None
        self._detail_to_val: Label | None = None
        self._detail_body_lbl: Label | None = None
        self._detail_dot: Widget | None = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI Construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # Background
        with root.canvas.before:
            Color(*_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, v: setattr(self._bg_rect, "pos", v),
                  size=lambda w, v: setattr(self._bg_rect, "size", v))

        # Back badge  (24.02, 21)  76.28 × 76.28
        back_src = _fp("email_icon_back_badge.png") or _fp("icon_arrow_card.png")
        if back_src:
            back_btn = _Tap(
                draw_bg=False,
                size_hint=(_sw(76.28), _sh(76.28)),
                pos_hint={"x": _x(24.02), "y": _y(21, 76.28)},
            )
            back_btn.add_widget(Image(
                source=back_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="contain",
            ))
            back_btn.bind(on_release=lambda *_: self.go_back())
            root.add_widget(back_btn)

        # Settings badge  (1159.55, 21)  76.28 × 76.28
        sg_src = _fp("email_icon_settings_badge.png") or _fp("icon_settings.png")
        if sg_src:
            sg_btn = _Tap(
                draw_bg=False,
                size_hint=(_sw(76.28), _sh(76.28)),
                pos_hint={"x": _x(1159.55), "y": _y(21, 76.28)},
            )
            sg_btn.add_widget(Image(
                source=sg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="contain",
            ))
            sg_btn.bind(on_release=lambda *_: self.goto("settings"))
            root.add_widget(sg_btn)

        self._build_header_bar(root)
        self._build_list_panel(root)
        self._build_detail_panel(root)

        self.add_widget(root)

    def _build_header_bar(self, root: FloatLayout) -> None:
        """Header bar  (23, 104)  1214 × 101  — Emails title + Today/All/Unread tabs + search."""
        BX, BY, BW, BH = 23, 104, 1214, 101

        bar = FloatLayout(
            size_hint=(_sw(BW), _sh(BH)),
            pos_hint={"x": _x(BX), "y": _y(BY, BH)},
        )

        # Figma asset background — falls back to drawn gradient
        hdr_bg_src = _fp("email_header_bar.png")
        if hdr_bg_src:
            bar.add_widget(Image(
                source=hdr_bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))
        else:
            with bar.canvas.before:
                Color(1, 1, 1, 1)
                self._hdr_bg = RoundedRectangle(
                    pos=bar.pos, size=bar.size,
                    radius=[_ff(22.6)],
                    texture=_grad(_HDR_TOP, _HDR_BOT),
                )
                Color(*_BDR_HDR)
                self._hdr_ln = Line(
                    rounded_rectangle=(bar.x, bar.y, bar.width, bar.height, _ff(22.6)),
                    width=1.0,
                )

        # "Emails" title  (37, 21)  row 1 left — SemiBold 30px white
        bar.add_widget(_lbl(
            "Emails", _FONT_SB, _ff(30), _WHITE,
            size_hint=(120 / BW, 36 / BH),
            pos_hint={"x": 37 / BW, "y": (BH - 21 - 36) / BH},
        ))

        # Search bar placeholder  (right side of row 1)  ~680px from left, width ~480
        sb = FloatLayout(
            size_hint=(480 / BW, 34 / BH),
            pos_hint={"x": 690 / BW, "y": (BH - 18 - 34) / BH},
        )
        with sb.canvas.before:
            Color(0.012, 0.063, 0.208, 1.0)
            self._sb_bg = RoundedRectangle(pos=sb.pos, size=sb.size, radius=[_ff(17)])
            Color(*_BDR_PANEL)
            self._sb_ln = Line(
                rounded_rectangle=(sb.x, sb.y, sb.width, sb.height, _ff(17)),
                width=1.0,
            )
        sb.bind(pos=self._sync_sb, size=self._sync_sb)
        sb.add_widget(_lbl(
            "Search", _FONT_MD, _ff(18), _MUTED,
            halign="left", valign="middle",
            size_hint=(0.85, 1),
            pos_hint={"x": 0.08, "y": 0},
        ))
        bar.add_widget(sb)

        # Tabs: Today | All | Unread  (row 2)
        tabs = [
            ("today",  "Today",   37, 68),
            ("all",    "All",    212, 68),
            ("unread", "Unread", 365, 68),
        ]
        count_x = {"today": 122, "all": 276, "unread": 463}

        for tab_id, tab_name, tx, ty in tabs:
            is_active = (tab_id == self._active_tab)
            col = _BLUE if is_active else _MUTED
            lbl = _lbl(
                tab_name, _FONT_SB, _ff(24), col,
                size_hint=(100 / BW, 29 / BH),
                pos_hint={"x": tx / BW, "y": (BH - ty - 29) / BH},
            )
            self._tab_labels[tab_id] = lbl
            bar.add_widget(lbl)

            cnt_lbl = _lbl(
                "0", _FONT_SB, _ff(24), _BLUE if is_active else _WHITE,
                size_hint=(25 / BW, 29 / BH),
                pos_hint={"x": count_x[tab_id] / BW, "y": (BH - ty - 29) / BH},
            )
            self._count_labels[tab_id] = cnt_lbl
            bar.add_widget(cnt_lbl)

            tap = _Tap(
                draw_bg=False,
                size_hint=(130 / BW, 35 / BH),
                pos_hint={"x": (tx - 5) / BW, "y": (BH - ty - 33) / BH},
            )
            tap.bind(on_release=lambda _, t=tab_id: self._on_tab(t))
            bar.add_widget(tap)

        root.add_widget(bar)

    def _sync_sb(self, w, *_):
        self._sb_bg.pos = w.pos
        self._sb_bg.size = w.size
        self._sb_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(17))

    def _sync_hdr(self, *_):
        pass

    def _build_list_panel(self, root: FloatLayout) -> None:
        """Left panel  (23, 212)  535 × 567 — scrollable email list."""
        PX, PY, PW, PH = 23, 212, 535, 567

        panel_outer = FloatLayout(
            size_hint=(_sw(PW), _sh(PH)),
            pos_hint={"x": _x(PX), "y": _y(PY, PH)},
        )

        list_bg_src = _fp("email_list_panel.png")
        if list_bg_src:
            panel_outer.add_widget(Image(
                source=list_bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))
        else:
            with panel_outer.canvas.before:
                Color(1, 1, 1, 1)
                self._list_bg = RoundedRectangle(
                    pos=panel_outer.pos, size=panel_outer.size,
                    radius=[_ff(29.7)],
                    texture=_grad(_PANEL_TOP, _PANEL_BOT),
                )
                Color(*_BDR_PANEL)
                self._list_ln = Line(
                    rounded_rectangle=(
                        panel_outer.x, panel_outer.y,
                        panel_outer.width, panel_outer.height, _ff(29.7)
                    ),
                    width=1.0,
                )

        sv = ScrollView(
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
            do_scroll_x=False,
            bar_width=_ff(9),
        )
        self._list_container = FloatLayout(
            size_hint=(1, None),
            height=_ff(PH),
        )
        sv.add_widget(self._list_container)
        panel_outer.add_widget(sv)
        root.add_widget(panel_outer)

    def _sync_list_bg(self, *_):
        pass

    def _build_detail_panel(self, root: FloatLayout) -> None:
        """Right panel  (570, 212)  667 × 567 — email detail view."""
        PX, PY, PW, PH = 570, 212, 667, 567

        panel = FloatLayout(
            size_hint=(_sw(PW), _sh(PH)),
            pos_hint={"x": _x(PX), "y": _y(PY, PH)},
        )

        det_bg_src = _fp("email_detail_panel.png")
        if det_bg_src:
            panel.add_widget(Image(
                source=det_bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))
        else:
            with panel.canvas.before:
                Color(1, 1, 1, 1)
                self._det_bg = RoundedRectangle(
                    pos=panel.pos, size=panel.size,
                    radius=[_ff(29.7)],
                    texture=_grad(_PANEL_TOP, _PANEL_BOT),
                )
                Color(*_BDR_PANEL)
                self._det_ln = Line(
                    rounded_rectangle=(
                        panel.x, panel.y, panel.width, panel.height, _ff(29.7)
                    ),
                    width=1.0,
                )

        self._detail_panel = panel

        # All widgets below are hidden until an email is tapped.
        # They are stored in self._detail_content_widgets and toggled via opacity.
        self._detail_content_widgets: list = []

        def _tracked(w):
            w.opacity = 0
            self._detail_content_widgets.append(w)
            return w

        # ── Action buttons row (y=17 in panel) ─────────────────────────────
        back_act_src = _fp("email_action_back.png")
        if back_act_src:
            back_act = _tracked(_Tap(
                draw_bg=False,
                size_hint=(72 / PW, 24 / PH),
                pos_hint={"x": 39 / PW, "y": (PH - 17 - 24) / PH},
            ))
            back_act.add_widget(Image(
                source=back_act_src, size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0}, fit_mode="contain",
            ))
            back_act.bind(on_release=lambda *_: self._on_detail_back())
            panel.add_widget(back_act)

        unread_act_src = _fp("email_action_mark_unread.png")
        if unread_act_src:
            unread_act = _tracked(_Tap(
                draw_bg=False,
                size_hint=(134 / PW, 24 / PH),
                pos_hint={"x": 133 / PW, "y": (PH - 17 - 24) / PH},
            ))
            unread_act.add_widget(Image(
                source=unread_act_src, size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0}, fit_mode="contain",
            ))
            unread_act.bind(on_release=lambda *_: self._on_mark_unread())
            panel.add_widget(unread_act)

        archive_act_src = _fp("email_action_archive.png")
        if archive_act_src:
            archive_act = _tracked(_Tap(
                draw_bg=False,
                size_hint=(92 / PW, 24 / PH),
                pos_hint={"x": 298 / PW, "y": (PH - 17 - 24) / PH},
            ))
            archive_act.add_widget(Image(
                source=archive_act_src, size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0}, fit_mode="contain",
            ))
            archive_act.bind(on_release=lambda *_: self._on_archive())
            panel.add_widget(archive_act)

        more_src = _fp("email_btn_more.png")
        if more_src:
            more_btn = _tracked(_Tap(
                draw_bg=False,
                size_hint=(101 / PW, 42 / PH),
                pos_hint={"x": 528 / PW, "y": (PH - 8 - 42) / PH},
            ))
            more_btn.add_widget(Image(
                source=more_src, size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill", allow_stretch=True, keep_ratio=False,
            ))
            panel.add_widget(more_btn)

        # ── Avatar + dot + sender + To row ─────────────────────────────────
        av_src = _fp("email_avatar.png")
        self._avatar = _tracked(_AvatarCircle(
            source=av_src,
            size_hint=(48 / PW, 48 / PH),
            pos_hint={"x": 50 / PW, "y": (PH - 79 - 48) / PH},
        ))
        panel.add_widget(self._avatar)

        det_dot_src = _fp("email_dot_detail.png") or _fp("email_dot_unread.png")
        if det_dot_src:
            panel.add_widget(_tracked(Image(
                source=det_dot_src,
                size_hint=(12 / PW, 12 / PH),
                pos_hint={"x": 28 / PW, "y": (PH - 99 - 12) / PH},
                fit_mode="contain",
            )))

        self._detail_sender_lbl = _tracked(_lbl(
            "", _FONT_SB, _ff(23), _WHITE,
            size_hint=(220 / PW, 27 / PH),
            pos_hint={"x": 107 / PW, "y": (PH - 74 - 27) / PH},
        ))
        panel.add_widget(self._detail_sender_lbl)

        to_key = _tracked(_lbl(
            "To:", _FONT_SB, _ff(20), _MUTED,
            size_hint=(28 / PW, 24 / PH),
            pos_hint={"x": 107 / PW, "y": (PH - 108 - 24) / PH},
        ))
        panel.add_widget(to_key)

        self._detail_to_val = _tracked(_lbl(
            "", _FONT_SB, _ff(20), _BLUE,
            size_hint=(200 / PW, 24 / PH),
            pos_hint={"x": 144 / PW, "y": (PH - 108 - 24) / PH},
        ))
        panel.add_widget(self._detail_to_val)

        # ── Subject ──────────────────────────────────────────────────────────
        self._detail_subject_lbl = _tracked(_lbl(
            "", _FONT_SB, _ff(25), _WHITE,
            size_hint=(500 / PW, 30 / PH),
            pos_hint={"x": 32 / PW, "y": (PH - 146 - 30) / PH},
        ))
        panel.add_widget(self._detail_subject_lbl)

        # ── Body scroll ──────────────────────────────────────────────────────
        body_sv = _tracked(ScrollView(
            size_hint=(590 / PW, 340 / PH),
            pos_hint={"x": 32 / PW, "y": (PH - 189 - 340) / PH},
            do_scroll_x=False,
            bar_width=0,
        ))
        self._detail_body_lbl = Label(
            text="",
            font_name=_FONT_MD,
            font_size=_ff(18),
            color=_WHITE,
            halign="left",
            valign="top",
            size_hint=(1, None),
            line_height=1.4,
        )
        self._detail_body_lbl.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, s: setattr(w, "height", s[1]),
        )
        body_sv.add_widget(self._detail_body_lbl)
        panel.add_widget(body_sv)

        # ── Empty-state prompt (visible until first tap) ─────────────────────
        self._empty_lbl = _lbl(
            "Tap an email to read",
            _FONT_MD, _ff(20), _MUTED,
            halign="center", valign="middle",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
        )
        panel.add_widget(self._empty_lbl)

        root.add_widget(panel)

    def _sync_det_bg(self, *_):
        pass

    def _sync_det_div(self, *_):
        pass

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def on_enter(self):
        self._load_emails()
        if not hasattr(self, "_refresh_event") or self._refresh_event is None:
            from kivy.clock import Clock
            self._refresh_event = Clock.schedule_interval(
                lambda _dt: self._load_emails(), 30.0
            )

    def on_leave(self):
        if hasattr(self, "_refresh_event") and self._refresh_event is not None:
            self._refresh_event.cancel()
            self._refresh_event = None

    # -----------------------------------------------------------------------
    # Data loading
    # -----------------------------------------------------------------------

    def _load_emails(self):
        async def _fetch():
            emails: list = []
            connected = True
            err_msg: str | None = None
            try:
                fetch = getattr(self.backend, "fetch_gmail_recent", None)
                if fetch is not None:
                    data = await fetch(max_results=40, days=_GMAIL_RECENT_DAYS, q="")
                    connected = bool(data.get("connected"))
                    err_msg = (data.get("error") or "").strip() or None
                    rows = data.get("messages") or []
                    if connected and isinstance(rows, list):
                        from api_client import _map_gmail_recent_row

                        emails = [
                            _map_gmail_recent_row(m)
                            for m in rows
                            if isinstance(m, dict) and m.get("id")
                        ]
                else:
                    emails = await self.backend.get_emails(filter="all", limit=50)
            except Exception as exc:
                logger.warning("_load_emails failed: %s", exc)
                emails = []
                connected = False
                err_msg = str(exc) if str(exc) else None

            def _apply(_dt):
                self._gmail_connected = connected
                self._gmail_error = err_msg
                self._all_emails = emails
                self._update_tab_counts()
                # If active tab is "today" but nothing arrived today, fall back to "all"
                if self._active_tab == "today" and not any(e.get("is_today") for e in emails):
                    self._active_tab = "all"
                    for tid, lbl in self._tab_labels.items():
                        lbl.color = _BLUE if tid == "all" else _MUTED
                    for tid, lbl in self._count_labels.items():
                        lbl.color = _BLUE if tid == "all" else _WHITE
                self._apply_filter()
                if emails and self._selected_email is None:
                    self._select_email(emails[0])
            Clock.schedule_once(_apply, 0)

        run_async(_fetch())

    def _update_tab_counts(self):
        today_n  = sum(1 for e in self._all_emails if e.get("is_today"))
        all_n    = len(self._all_emails)
        unread_n = sum(1 for e in self._all_emails if not e.get("is_read"))

        counts = {"today": today_n, "all": all_n, "unread": unread_n}
        for tab_id, lbl in self._count_labels.items():
            lbl.text = str(counts.get(tab_id, 0))

    def _apply_filter(self):
        tab = self._active_tab
        if tab == "today":
            filtered = [e for e in self._all_emails if e.get("is_today")]
        elif tab == "unread":
            filtered = [e for e in self._all_emails if not e.get("is_read")]
        else:
            filtered = list(self._all_emails)
        self._filtered_emails = filtered
        self._rebuild_list()

    def _rebuild_list(self):
        if self._list_container is None:
            return
        self._list_container.clear_widgets()

        emails = self._filtered_emails
        if not self._gmail_connected:
            err_detail = f"\n({self._gmail_error})" if self._gmail_error else ""
            self._list_container.add_widget(_lbl(
                (
                    "Connect Gmail in the web dashboard:\n"
                    "Settings → Integrations → Gmail.\n"
                    "Then reopen Emails here."
                    + err_detail
                ),
                _FONT_MD, _ff(18), _MUTED,
                halign="center", valign="middle",
                size_hint=(0.92, None),
                height=_ff(140),
                pos_hint={"x": 0.04, "y": 0.35},
            ))
            self._list_container.height = _ff(567)
            return
        if not emails and self._gmail_error:
            self._list_container.add_widget(_lbl(
                self._gmail_error,
                _FONT_MD, _ff(16), (0.9, 0.75, 0.35, 1.0),
                halign="center", valign="middle",
                size_hint=(0.92, None),
                height=_ff(80),
                pos_hint={"x": 0.04, "y": 0.42},
            ))
            self._list_container.height = _ff(567)
            return
        if not emails:
            self._list_container.add_widget(_lbl(
                "No emails", _FONT_MD, _ff(18), _MUTED,
                halign="center", valign="middle",
                size_hint=(1, None), height=_ff(40),
                pos_hint={"x": 0, "y": 0.45},
            ))
            self._list_container.height = _ff(567)
            return

        # Layout constants (in panel-local Figma px, panel=535×567)
        PW = 535
        # Row dimensions
        NEW_LABEL_Y    = 11      # "NEW" label top
        FIRST_ROW_Y    = 41      # first unread row top
        FIRST_ROW_H    = 122.61
        SECOND_ROW_Y   = 174     # subsequent rows
        READ_ROW_H     = 90
        EARLIER_LABEL_Y = 275
        ROW_GAP         = 12

        # Split into today (new) and earlier
        today_emails   = [e for e in emails if e.get("is_today")]
        earlier_emails = [e for e in emails if not e.get("is_today")]

        # We position using pos_hint relative to list_container height
        # Use absolute pixel positioning inside a fixed-height FloatLayout
        SCALE = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
        rw    = (PW - 55) * SCALE   # ~480 Figma px scaled
        x_off = 28 * SCALE
        cx    = x_off

        y_cursor = _ff(567)   # start at top, go down

        # Section: NEW
        if today_emails:
            y_cursor -= _ff(11 + 24)   # label height
            new_lbl = _lbl(
                "NEW", _FONT_SB, _ff(20), _BLUE,
                size_hint=(None, None),
                width=_ff(50), height=_ff(24),
            )
            new_lbl.pos = (cx, y_cursor)
            self._list_container.add_widget(new_lbl)
            y_cursor -= _ff(6)

            for email in today_emails:
                selected = (self._selected_email is not None and
                            self._selected_email.get("id") == email.get("id"))
                rh = _ff(122.61 if (selected or not email.get("is_read")) else 90)
                row = _EmailRow(
                    email=email,
                    selected=selected,
                    size_hint=(None, None),
                    size=(rw, rh),
                )
                row.pos = (cx, y_cursor - rh)
                row.bind(on_release=lambda r, e=email: self._select_email(e))
                self._list_container.add_widget(row)
                y_cursor -= rh + _ff(10)

        # Section: EARLIER
        if earlier_emails:
            y_cursor -= _ff(10)
            ear_lbl = _lbl(
                "EARLIER", _FONT_SB, _ff(20), _BLUE,
                size_hint=(None, None),
                width=_ff(90), height=_ff(24),
            )
            ear_lbl.pos = (cx, y_cursor - _ff(24))
            self._list_container.add_widget(ear_lbl)
            y_cursor -= _ff(24 + 6)

            for email in earlier_emails:
                selected = (self._selected_email is not None and
                            self._selected_email.get("id") == email.get("id"))
                rh = _ff(90)
                row = _EmailRow(
                    email=email,
                    selected=selected,
                    size_hint=(None, None),
                    size=(rw, rh),
                )
                row.pos = (cx, y_cursor - rh)
                row.bind(on_release=lambda r, e=email: self._select_email(e))
                self._list_container.add_widget(row)
                y_cursor -= rh + _ff(10)

        # Set content height so scroll works
        used = _ff(567) - y_cursor
        self._list_container.height = max(_ff(567), used + _ff(20))

    # -----------------------------------------------------------------------
    # Interaction
    # -----------------------------------------------------------------------

    def _on_tab(self, tab_id: str):
        if tab_id == self._active_tab:
            return
        # Update colours
        for tid, lbl in self._tab_labels.items():
            lbl.color = _BLUE if tid == tab_id else _MUTED
        for tid, lbl in self._count_labels.items():
            lbl.color = _BLUE if tid == tab_id else _WHITE
        self._active_tab = tab_id
        self._apply_filter()

    def _select_email(self, email: dict):
        self._selected_email = email
        # Mark as read locally
        email["is_read"] = True
        self._update_detail(email)
        self._rebuild_list()          # refresh row highlight

    def _update_detail(self, email: dict):
        # Populate fields
        if self._detail_sender_lbl:
            self._detail_sender_lbl.text = email.get("sender", "")
        if self._detail_to_val:
            self._detail_to_val.text = email.get("to", "—")
        if self._detail_subject_lbl:
            self._detail_subject_lbl.text = email.get("subject", "")
        if self._detail_body_lbl:
            self._detail_body_lbl.text = email.get("body", email.get("preview", ""))
        # Show all content, hide empty state
        for w in getattr(self, "_detail_content_widgets", []):
            w.opacity = 1.0
        if self._empty_lbl:
            self._empty_lbl.opacity = 0.0

    def _on_detail_back(self):
        """Back action in detail panel — deselect email."""
        self._selected_email = None
        if self._detail_sender_lbl:
            self._detail_sender_lbl.text = ""
        if self._detail_subject_lbl:
            self._detail_subject_lbl.text = ""
        if self._detail_body_lbl:
            self._detail_body_lbl.text = ""
        # Hide all content, show empty state
        for w in getattr(self, "_detail_content_widgets", []):
            w.opacity = 0.0
        if self._empty_lbl:
            self._empty_lbl.opacity = 1.0
        self._rebuild_list()

    def _on_mark_unread(self):
        email = self._selected_email
        if not email:
            return
        email["is_read"] = False
        self._rebuild_list()
        async def _call():
            try:
                await self.backend.mark_email_unread(email["id"])
            except Exception as exc:
                logger.debug("mark_email_unread failed: %s", exc)
        run_async(_call())

    def _on_archive(self):
        email = self._selected_email
        if not email:
            return
        self._all_emails = [e for e in self._all_emails if e.get("id") != email.get("id")]
        self._selected_email = None
        if self._empty_lbl:
            self._empty_lbl.opacity = 1.0
        self._update_tab_counts()
        self._apply_filter()
        async def _call():
            try:
                await self.backend.archive_email(email["id"])
            except Exception as exc:
                logger.debug("archive_email failed: %s", exc)
        run_async(_call())
