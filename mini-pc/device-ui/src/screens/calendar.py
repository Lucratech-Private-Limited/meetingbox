"""Calendar screen — pixel-perfect from Figma 927:61 (1260 × 800 px).

All visual assets are downloaded directly from Figma and referenced by path.
Layout uses a fully flat FloatLayout so every pos_hint is computed at
construction time (no deferred _build_once timing issues).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now, to_display_local
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ── Design frame ──────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

# ── Figma-downloaded asset paths ──────────────────────────────────────────────
_CAL = ASSETS_DIR / "calendar" / "figma"

def _asset(name: str) -> str:
    p = _CAL / name
    return str(p) if p.is_file() else ""


# ── Colours (exact Figma hex) ─────────────────────────────────────────────────
_BG      = (1/255,   8/255,  26/255, 1.0)   # #01081A  background
_WHITE   = (1.0, 1.0, 1.0, 1.0)
_MUTED   = (182/255, 186/255, 242/255, 1.0)  # #B6BAF2
_BDOT    = (64/255,  152/255, 252/255, 1.0)  # #4098FC  dots filled
_BLUE_A  = (0.0, 107/255, 249/255, 1.0)     # #006BF9  add-event label
_BTDAY   = (4/255,  132/255, 255/255, 1.0)  # #0484FF  today-border

# Card fill gradients
_CARD_T  = (2/255,   18/255,  60/255, 1.0)  # #02123C  top
_CARD_B  = (0.0,    10/255,  38/255, 1.0)   # #000A26  bottom

# Border colours (start colour of Figma gradient strokes)
# fill_RDXD5N → #3F4253 (grid card, free card)
_BDR_CARD = (63/255,  66/255,  83/255, 1.0)  # #3F4253
# fill_QE1YKH → #21284B (meeting cards, add-event)
_BDR_MTG  = (33/255,  40/255,  75/255, 1.0)  # #21284B
# fill_X3J4FE → #3F8CFF (Join button AND Details button — same Figma stroke)
_BDR_BTN  = (63/255, 140/255, 255/255, 1.0)  # #3F8CFF
# fill_JNFIQY → #3F4253 (icon circles inside meeting cards)
_BDR_ICON = (63/255,  66/255,  83/255, 1.0)  # #3F4253

# Meeting card fill
_MTG_T   = (1/255,   17/255,  55/255, 1.0)  # #011137
_MTG_B   = (0.0,    10/255,  38/255, 1.0)   # #000A26

# Join button fill  fill_6LSSN6 → #0059DC → #013DA7
_JOIN_T  = (0.0,    89/255, 220/255, 1.0)   # #0059DC
_JOIN_B  = (1/255,   61/255, 167/255, 1.0)  # #013DA7

# Icon-circle background
_ICON_BG = (1/255,   11/255,  38/255, 1.0)  # #010B26

_FSB = "42dot-SB"   # SemiBold
_FB  = "42dot-Sans"  # Bold weight


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
    """Figma absolute px → Kivy size_hint + pos_hint for a 1260×800 root."""
    return {
        "size_hint": (fw / FW, fh / FH),
        "pos_hint": {"x": fx / FW, "y": (FH - fy - fh) / FH},
    }


def _ff(fs: float) -> int:
    scale = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)
    return max(6, round(fs * scale))


_GC: dict = {}


def _grad(top: tuple, bot: tuple):
    from kivy.graphics.texture import Texture
    k = (top, bot)
    if k not in _GC:
        t = Texture.create(size=(1, 2), colorfmt="rgba")
        def _b(c):
            return [min(255, max(0, int(v * 255))) for v in c]
        t.blit_buffer(bytes(_b(bot) + _b(top)), colorfmt="rgba", bufferfmt="ubyte")
        t.mag_filter = t.min_filter = "linear"
        t.wrap = "clamp_to_edge"
        _GC[k] = t
    return _GC[k]


def _lbl(text: str, font: str, size: int, color: tuple,
         ha: str = "left", va: str = "top", **kw) -> Label:
    l = Label(text=text, font_name=font, font_size=size, color=color,
              halign=ha, valign=va, **kw)
    l.bind(size=l.setter("text_size"))
    return l


# ── Tappable image button ─────────────────────────────────────────────────────

class _ImgBtn(ButtonBehavior, Image):
    """An Image widget that fires on_release when tapped."""
    pass


class _NavArrow(Widget):
    """Draws a chevron nav arrow (< or >) using canvas Lines.

    Identical rendering for both directions — only the direction param differs.
    Color and weight match the Figma muted-blue palette used in the grid.
    """

    def __init__(self, direction: str = "right", **kw):
        super().__init__(**kw)
        self._dir = direction  # "left" or "right"
        with self.canvas:
            self._col = Color(*_MUTED)
            self._top = Line(points=[], width=2.2, cap="round", joint="round")
            self._bot = Line(points=[], width=2.2, cap="round", joint="round")
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def _draw(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        cx, cy = x + w / 2, y + h / 2
        # Chevron arms: tip at 55 % of half-width, arms span 45 % of half-height
        arm  = h * 0.28   # arm vertical half-length
        reach = w * 0.30  # horizontal depth of chevron
        if self._dir == "right":
            tip_x, tail_x = cx + reach, cx - reach
        else:
            tip_x, tail_x = cx - reach, cx + reach
        self._top.points = [tail_x, cy + arm, tip_x, cy, ]
        self._bot.points = [tip_x, cy, tail_x, cy - arm]


# ── Gradient card ─────────────────────────────────────────────────────────────

class _Card(FloatLayout):
    def __init__(self, ct: tuple, cb: tuple, bdr: tuple, r: float = 12,
                 bdr_alpha: float = 0.9, **kw):
        super().__init__(**kw)
        self._r = r
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[r], texture=_grad(ct, cb))
        with self.canvas.after:
            # bdr is a 4-tuple (r,g,b,a); passing *bdr + bdr_alpha = 5 args
            # which Kivy's Color silently ignores.  Slice to RGB then add alpha.
            Color(*bdr[:3], bdr_alpha)
            self._ln = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, r),
                width=1.0)
        self.bind(pos=self._s, size=self._s)

    def _s(self, *_):
        r = self._r
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [r]
        self._ln.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


class _TapCard(ButtonBehavior, _Card):
    pass


# ── Day-cell highlight overlay ────────────────────────────────────────────────

class _Highlight(Widget):
    """Highlight box drawn on top of the grid background for one day cell."""

    def __init__(self, mode: str = "none", **kw):
        super().__init__(**kw)
        self._mode = mode
        with self.canvas:
            self._fc = Color(0, 0, 0, 0)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[_ff(14.13)])
            self._bc = Color(0, 0, 0, 0)
            self._ln = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, _ff(14.13)),
                width=1.41)
        self.bind(pos=self._d, size=self._d)
        self._apply()

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._apply()
        self._d()

    def _apply(self) -> None:
        if self._mode == "today":
            self._fc.rgba = (0.016, 0.082, 0.259, 0.38)
            self._bc.rgba = _BTDAY
        elif self._mode == "sel":
            self._fc.rgba = (*_BTDAY[:3], 0.14)
            self._bc.rgba = (*_BTDAY[:3], 0.55)
        else:
            self._fc.rgba = (0, 0, 0, 0)
            self._bc.rgba = (0, 0, 0, 0)

    def _d(self, *_) -> None:
        r = _ff(14.13)
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [r]
        self._ln.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


class _TapZone(ButtonBehavior, Widget):
    pass


class _GlowDot(Widget):
    """White filled circle with a coloured stroke and optional soft outer glow,
    matching the Figma timeline dot design (white + blue stroke + blur glow).

    Pass ``glow=False`` for past meetings (dot shown without the glow rings).
    """

    def __init__(self, stroke_rgba: tuple, glow_mult: float = 1.7,
                 glow: bool = True, **kw):
        super().__init__(**kw)
        self._stroke = stroke_rgba
        self._gm = glow_mult
        self._glow = glow
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(self._draw, 0)

    def _draw(self, *_):
        self.canvas.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        sr, sg, sb = self._stroke[:3]
        gm = self._gm
        with self.canvas:
            if self._glow:
                # Soft glow rings (outer → inner, increasing opacity)
                for scale, alpha in [(gm * 1.35, 0.08), (gm, 0.18), (gm * 0.75, 0.12)]:
                    gw, gh = w * scale, h * scale
                    gx, gy = x + (w - gw) / 2, y + (h - gh) / 2
                    Color(sr, sg, sb, alpha)
                    Ellipse(pos=(gx, gy), size=(gw, gh))
            # White fill
            Color(1.0, 1.0, 1.0, 1.0)
            Ellipse(pos=(x, y), size=(w, h))
            # Coloured stroke (dimmer when no glow = past meeting)
            stroke_alpha = 1.0 if self._glow else 0.35
            Color(sr, sg, sb, stroke_alpha)
            Line(ellipse=(x, y, w, h), width=1.5)


# ── Meeting datetime helpers ─────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


def _parse_dt(iso: str):
    """Parse an ISO datetime string and return a timezone-aware datetime in the
    display timezone.  Returns None on failure."""
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # Mock backend emits local (+05:30) times without offset tag
            dt = dt.replace(tzinfo=_IST)
        return to_display_local(dt)
    except Exception:
        return None


def _m_state(m: dict, now) -> str:
    """Return the meeting state relative to *now* (tz-aware).

    Returns one of ``'active'``, ``'upcoming'``, or ``'past'``.
    """
    start = _parse_dt(m.get("start", ""))
    end = _parse_dt(m.get("end", ""))
    if start is None or end is None:
        return "upcoming"
    if now >= end:
        return "past"
    if now >= start:
        return "active"
    return "upcoming"


# ── Column layout data (from Figma dev mode) ──────────────────────────────────
#
# Grid frame: GX=24.02, GY=105.94, GW=1210.56, GH=151.14
#
# Each tuple:
#   outer_x, outer_y, outer_w, outer_h  ← outer group in grid (for tap zone)
#   inner_dx, inner_dy                  ← inner content offset within outer group
#   abbrev_dx, abbrev_dy, aw, ah        ← day-abbreviation label (within inner)
#   date_dx,   date_dy,  dw, dh         ← date-number label (within inner)
#   [(dot_dx, dot_dy, filled), ...]      ← dots (within inner group)

GX, GY = 24.02, 105.94

_COLS = [
    # MON — 117.56, 23  66×110.18  (shifted right to fit left nav arrow)
    (117.56,  23.00,  66.0, 110.18, 0.00,  0.00,
      0.00,   0.00,  66.0,  34.0,   9.89, 38.14, 47.0, 51.0,
     [(15.54, 96.05, True), (38.14, 96.05, True)]),
    # TUE — 276.79, 23  53×110.18
    (276.79,  23.00,  53.0, 110.18, 0.00,  0.00,
      0.00,   0.00,  53.0,  34.0,   1.41, 39.55, 51.0, 51.0,
     [(16.95, 96.05, True)]),
    # WED — 385, 5  139.84×139.84 (today in Figma); inner offset 38.14, 20.35
    (385.00,   5.00, 139.84, 139.84, 38.14, 20.35,
      1.41,   0.00,  64.0,  34.0,  11.30, 38.14, 46.0, 51.0,
     [(0.00, 93.23, True), (22.60, 93.23, True), (45.20, 93.23, True)]),
    # THU — 579.38, 25  57×107.35
    (579.38,  25.00,  57.0, 107.35, 0.00,  0.00,
      0.00,   0.00,  57.0,  34.0,   4.23, 38.14, 49.0, 51.0,
     [(21.19, 93.23, True), (43.79, 93.23, True)]),
    # FRI — 734.20, 25  51×107.35
    (734.20,  25.00,  51.0, 107.35, 0.00,  0.00,
      2.82,   0.00,  47.0,  34.0,   0.00, 38.14, 51.0, 51.0,
     [(18.36, 93.23, True)]),
    # SAT — 886.20, 25  51×107.35  (outline dot — no meetings)
    (886.20,  25.00,  51.0, 107.35, 0.00,  0.00,
      0.00,   0.00,  51.0,  34.0,   0.00, 38.14, 51.0, 51.0,
     [(18.37, 93.23, False)]),
    # SUN — 1033.97, 32.41  58×107.35  (outline dot — no meetings)
    (1033.97, 32.41,  58.0, 107.35, 0.00,  0.00,
      0.00,   0.00,  58.0,  34.0,   4.24, 38.14, 51.0, 51.0,
     [(22.60, 93.23, False)]),
]

# WED-style highlight dimensions: the Figma "today" box is 139.84×139.84
# starting at grid y=5 (nearly flush with grid top).  We apply these same
# fixed dimensions to whichever column is "today" so the highlight always looks
# like the Figma's WED treatment.
_HL_H  = 139.84   # highlight height (matches WED outer group)
_HL_Y  = GY + 5.0   # screen y of highlight top-edge (Figma: GY + 5)

_DAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]
_MONTHS   = ["Jan","Feb","Mar","Apr","May","Jun",
             "Jul","Aug","Sep","Oct","Nov","Dec"]


def _fmt_date(d: date) -> str:
    return f"{d.strftime('%a')} , {_MONTHS[d.month - 1]} {d.day}"


# ── CalendarScreen ────────────────────────────────────────────────────────────

class CalendarScreen(BaseScreen):
    """Calendar view — Figma 927:61, pixel-perfect flat layout."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._sel_date: date = display_now().date()
        self._heading_lbl: Label | None = None
        self._datestr_lbl: Label | None = None
        self._date_lbls: list[Label] = []
        self._highlights: list[_Highlight] = []
        self._col_dates: list[date] = []

        # Week navigation state
        self._view_week_mon: date | None = None   # Monday of the week currently shown
        self._target_date: date | None = None     # Set by main.py before on_enter to deep-link to a date

        # Dynamic day-view state
        self._week_data: dict = {}          # ISO date str -> {"meetings": [...]}
        self._root_layout: FloatLayout | None = None
        self._day_widgets: list = []        # cleared on every day-view rebuild
        self._refresh_event = None          # Clock handle for per-minute refresh

        # Grid dot state — populated in _build_grid, redrawn on week-data load
        self._dot_widgets: list = []        # all dot Widgets (removable)
        self._col_dot_info: list = []       # [(inner_sx, inner_sy, dot_y, inner_w), ...]
        # Calendar-icon widget (x position updates when heading text changes)
        self._cal_icon_img = None

        # Header summary labels (busy/free — updated after each week load)
        self._busy_lbl: Label | None = None
        self._free_lbl: Label | None = None

        self._build_ui()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))
        self._root_layout = root

        with root.canvas.before:
            Color(*_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, v: setattr(self._bg_rect, "pos", v),
            size=lambda w, v: setattr(self._bg_rect, "size", v),
        )

        self._build_header(root)
        self._build_grid(root)
        self._build_add_button(root)

        # Dynamic day view — populated on first on_enter / day tap
        self._update_day_view(self._sel_date)

        self.add_widget(root)

    # ── Header ─────────────────────────────────────────────────────────────────
    # Figma nodes referenced (all screen-absolute):
    #   927:62  back button     24.02, 21.19  76.28×76.28
    #   927:65  "Today"        118.66, 14.13  108×46
    #   927:66  date string    118.66, 60.36  169×33
    #   927:67  calendar icon  241.93, 20.49  36.98×33
    #   927:71  spark icon     851.77, 38.24  39.38×40.89
    #   927:69  busy text      905.91, 19.78  299×29
    #   927:70  free text      905.91, 55.46  207×29

    def _build_header(self, root: FloatLayout) -> None:
        # Back button — use exact Figma PNG (includes circle bg + gradient stroke)
        back_src = _asset("btn_back.png")
        if back_src:
            back = _ImgBtn(source=back_src, fit_mode="contain",
                           **_ph(24.02, 21.19, 76.28, 76.28))
            back.bind(on_release=lambda *_: self.go_back())
        else:
            back = _TapCard(ct=_ICON_BG, cb=_ICON_BG, bdr=_BDR_CARD,
                            r=_ff(38), **_ph(24.02, 21.19, 76.28, 76.28))
            back.add_widget(_lbl("<", _FB, _ff(36), _WHITE,
                                 ha="center", va="middle",
                                 size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
            back.bind(on_release=lambda *_: self.go_back())
        root.add_widget(back)

        # "Today" heading
        self._heading_lbl = _lbl(
            "Today", _FSB, _ff(38.52), _WHITE,
            **_ph(118.66, 14.13, 200.0, 46.0))
        root.add_widget(self._heading_lbl)

        # Date string — Figma fill_WIL9L0 = #006BF9 (blue), size +20%
        self._datestr_lbl = _lbl(
            _fmt_date(display_now().date()), _FSB, _ff(27.52 * 1.2), _BLUE_A,
            **_ph(118.66, 60.36, 280.0, 40.0))
        root.add_widget(self._datestr_lbl)

        # Calendar icon — placed one space after the heading text (dynamic x).
        # y is fixed (Figma: top=20.49, height=33).
        _cal_icon_path = ASSETS_DIR / "home" / "figma" / "icon_calendar_row.png"
        if not _cal_icon_path.is_file():
            _cal_icon_path = ASSETS_DIR / "brief" / "figma" / "icon_calendar.png"

        if _cal_icon_path.is_file():
            _sx = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)
            _icon_w = round(36.98 * _sx)
            _icon_h = round(33.0 * _sx)
            # Kivy y (bottom-up): (FH - top - height) / FH * display_height
            _icon_y = (FH - 20.49 - 33.0) * DISPLAY_HEIGHT / FH

            self._cal_icon_img = Image(
                source=str(_cal_icon_path), fit_mode="contain",
                size_hint=(None, None), size=(_icon_w, _icon_h),
                x=round(241.93 * _sx), y=round(_icon_y),
            )
            root.add_widget(self._cal_icon_img)

            # Update x whenever the heading text reflows
            def _upd_icon_x(*_):
                lbl = self._heading_lbl
                ic  = self._cal_icon_img
                if lbl and ic:
                    tw = lbl.texture_size[0] if lbl.texture_size else 0
                    if tw > 0:
                        gap = max(5, round(8 * _sx))
                        ic.x = lbl.x + tw + gap

            self._heading_lbl.bind(
                texture_size=lambda *_: Clock.schedule_once(
                    lambda _dt: _upd_icon_x(), 0),
                pos=lambda *_: Clock.schedule_once(
                    lambda _dt: _upd_icon_x(), 0),
            )
            Clock.schedule_once(lambda _dt: _upd_icon_x(), 0.05)

        # Spark / intelligence icon  851.77, 38.24  39.38×40.89
        spark_src = _asset("icon_spark.png")
        if spark_src:
            root.add_widget(Image(source=spark_src, fit_mode="contain",
                                  **_ph(851.77, 28.0, 39.38, 40.89)))
        else:
            root.add_widget(_lbl(
                "*", _FSB, _ff(30), _MUTED, ha="center", va="middle",
                **_ph(851.77, 28.0, 42.0, 42.0)))

        # Busy text  905.91, 19.78  299×29  (+20% size)
        # Dynamic — updated after each week load via _update_header_summary()
        self._busy_lbl = _lbl(
            "Loading calendar...", _FSB, _ff(24.61 * 1.2), _MUTED,
            **_ph(905.91, 19.78, 340.0, 36.0))
        root.add_widget(self._busy_lbl)

        # Free text  905.91, 55.46  207×29  (+20% size)
        self._free_lbl = _lbl(
            "", _FSB, _ff(24.61 * 1.2), _MUTED,
            **_ph(905.91, 55.46, 260.0, 36.0))
        root.add_widget(self._free_lbl)

    # ── Week grid ──────────────────────────────────────────────────────────────

    def _build_grid(self, root: FloatLayout) -> None:
        # Grid background card  24.02, 105.94  1210.56×151.14  r=29.66
        root.add_widget(_Card(
            ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
            r=_ff(29.66),
            **_ph(GX, GY, 1210.56, 151.14)))

        today = display_now().date()
        if self._view_week_mon is None:
            self._view_week_mon = today - timedelta(days=today.weekday())
        self._col_dates = [self._view_week_mon + timedelta(days=i) for i in range(7)]

        # Six vertical dividers — new positions matching updated Figma column layout
        # (Figma: w=2.83, h=84.75, y=33.9 within grid; divider x values within grid)
        for div_x in (224.0, 377.0, 530.0, 682.0, 834.0, 986.0):
            sx, sy = GX + div_x, GY + 33.9
            dv = Widget(**_ph(sx, sy, 2.83, 84.75))
            with dv.canvas.before:
                Color(2/255, 23/255, 77/255, 0.85)
                _r = Rectangle(pos=dv.pos, size=dv.size)
            def _mk(r):
                def _s(w, *_): r.pos = w.pos; r.size = w.size
                return _s
            dv.bind(pos=_mk(_r), size=_mk(_r))
            root.add_widget(dv)

        # Left navigation arrow — large tap zone (full grid height) + Figma icon
        _ltz = _TapZone(**_ph(GX + 4, GY, 60, 151.14))
        _ltz.bind(on_release=lambda *_: self._nav_week(-1))
        root.add_widget(_ltz)
        _nav_l = _asset("icon_nav_left.png")
        if _nav_l:
            root.add_widget(Image(source=_nav_l, fit_mode="contain",
                                  **_ph(GX + 16, GY + 51, 42, 50)))
        else:
            root.add_widget(_NavArrow("left", **_ph(GX + 16, GY + 51, 42, 50)))

        # Right navigation arrow — large tap zone + Figma icon
        _rtz = _TapZone(**_ph(GX + 1146, GY, 65, 151.14))
        _rtz.bind(on_release=lambda *_: self._nav_week(1))
        root.add_widget(_rtz)
        _nav_r = _asset("icon_nav_right.png")
        if _nav_r:
            root.add_widget(Image(source=_nav_r, fit_mode="contain",
                                  **_ph(GX + 1152, GY + 51, 42, 50)))
        else:
            root.add_widget(_NavArrow("right", **_ph(GX + 1152, GY + 51, 42, 50)))

        # Per-column: WED-style highlight, tap zone, abbrev label, date label, dots
        self._highlights.clear()
        self._date_lbls.clear()

        for col_idx, col_data in enumerate(_COLS):
            (ox, oy, ow, oh,
             idx_x, idy,
             adx, ady, aw, ah,
             ddx, ddy, dw, dh,
             dots) = col_data

            outer_sx = GX + ox
            inner_sx = outer_sx + idx_x
            inner_sy = GY + oy + idy
            col_date = self._col_dates[col_idx]
            is_today = (col_date == today)

            # Today highlight — always WED-style: 139.84px tall, centered on
            # the outer-group x centre, starting at _HL_Y (grid top + 5.65).
            hl_cx  = outer_sx + ow / 2         # outer group horizontal centre
            hl_x   = hl_cx - _HL_H / 2         # highlight left edge
            hl     = _Highlight(
                mode="today" if is_today else "none",
                **_ph(hl_x, _HL_Y, _HL_H, _HL_H))   # square 139.84×139.84
            root.add_widget(hl)
            self._highlights.append(hl)

            # Tap zone covers the outer group
            tz = _TapZone(**_ph(outer_sx, GY + oy, ow, oh))
            tz.bind(on_release=lambda inst, d=col_date, i=col_idx:
                    self._select_day(d, i))
            root.add_widget(tz)

            # Day abbreviation
            root.add_widget(_lbl(
                _DAY_ABBR[col_idx], _FSB, _ff(28.25), _MUTED,
                ha="center", va="middle",
                **_ph(inner_sx + adx, inner_sy + ady, aw, ah)))

            # Date number (dynamic)
            dl = _lbl(
                str(col_date.day), _FB, _ff(42.38), _WHITE,
                ha="center", va="middle",
                **_ph(inner_sx + ddx, inner_sy + ddy, dw, dh))
            root.add_widget(dl)
            self._date_lbls.append(dl)

            # Store dot position metadata for this column (used in _rebuild_all_dots).
            # dot_y: y of the dot row within the inner content (from first Figma dot).
            # inner_w: usable width for centering dots.
            _dot_y = dots[0][1] if dots else (oh - idy - 20)
            _inner_w = ow - idx_x
            self._col_dot_info.append((inner_sx, inner_sy, _dot_y, _inner_w))

    # ── Dynamic grid dots ──────────────────────────────────────────────────────

    def _rebuild_all_dots(self) -> None:
        """Remove existing dot widgets and redraw all 7 columns based on _week_data.

        Density rules (user spec):
          0 meetings → 1 unfilled dot
          1 meeting  → 1 filled dot
          2 meetings → 2 filled dots
          3+         → 3 filled dots
        """
        root = self._root_layout
        if root is None or not self._col_dot_info:
            return

        for w in self._dot_widgets:
            root.remove_widget(w)
        self._dot_widgets.clear()

        DOT_SZ = 14.13   # dot diameter in Figma pixels
        SPACING = 22.6   # centre-to-centre spacing between dots

        for col_idx, (inner_sx, inner_sy, dot_y, inner_w) in enumerate(
                self._col_dot_info):
            d = self._col_dates[col_idx] if col_idx < len(self._col_dates) else None
            n_meet = 0
            if d and self._week_data:
                n_meet = len(
                    self._week_data.get(d.isoformat(), {}).get("meetings", []))

            if n_meet == 0:
                dot_specs = [(False,)]               # 1 unfilled
            elif n_meet == 1:
                dot_specs = [(True,)]                # 1 filled
            elif n_meet == 2:
                dot_specs = [(True,), (True,)]       # 2 filled
            else:
                dot_specs = [(True,), (True,), (True,)]  # 3 filled

            n_dots = len(dot_specs)
            # Centre the dots horizontally within the column's inner width.
            # Clamp spacing so dots always fit.
            sp = min(SPACING, (inner_w - DOT_SZ) / max(1, n_dots - 1)) if n_dots > 1 else 0
            total_span = (n_dots - 1) * sp + DOT_SZ
            start_x = (inner_w - total_span) / 2

            for i, (filled,) in enumerate(dot_specs):
                dot_sx = inner_sx + start_x + i * sp
                dot_sy = inner_sy + dot_y
                dw2 = Widget(**_ph(dot_sx, dot_sy, DOT_SZ, DOT_SZ))
                if filled:
                    with dw2.canvas:
                        Color(*_BDOT)
                        _e = Ellipse(pos=dw2.pos, size=dw2.size)
                    def _mk_e(e):
                        def _s(w, *_): e.pos = w.pos; e.size = w.size
                        return _s
                    dw2.bind(pos=_mk_e(_e), size=_mk_e(_e))
                else:
                    with dw2.canvas:
                        Color(*_MUTED, 0.8)
                        _el = Line(
                            ellipse=(dw2.x, dw2.y, dw2.width, dw2.height),
                            width=1.2)
                    def _mk_el(el):
                        def _s(w, *_):
                            el.ellipse = (w.x, w.y, w.width, w.height)
                        return _s
                    dw2.bind(pos=_mk_el(_el), size=_mk_el(_el))
                root.add_widget(dw2)
                self._dot_widgets.append(dw2)

    # ── Dynamic day view ───────────────────────────────────────────────────────

    def _update_day_view(self, d: date) -> None:
        """Clear and rebuild the free-card + meeting area for *d*."""
        root = self._root_layout
        if root is None:
            return
        for w in self._day_widgets:
            root.remove_widget(w)
        self._day_widgets.clear()

        day_key = d.isoformat()
        meetings = self._week_data.get(day_key, {}).get("meetings", []) if self._week_data else []
        meetings = sorted(meetings, key=lambda m: m.get("start", ""))
        now = display_now()
        is_today = (d == now.date())

        self._build_day_free_card(meetings, now, is_today)
        self._build_day_meetings(meetings, now, is_today)

    # ── Free-time card (dynamic) ───────────────────────────────────────────────
    # Frame '20':  25.43, 268.39  1210.56×100.29  r=29.66

    def _build_day_free_card(self, meetings: list, now, is_today: bool) -> None:
        CW, CH = 1210.56, 100.29
        CX, CY = 25.43, 268.39

        # ── Determine display text ─────────────────────────────────────────────
        if not meetings:
            free_text = "No meetings scheduled for this day"
            mtg_text = ""
            show_sun = False
        else:
            n = len(meetings)
            mtg_noun = f"{n} meeting{'s' if n > 1 else ''}"
            if is_today:
                states = [_m_state(m, now) for m in meetings]
                if all(s == "past" for s in states):
                    free_text = "All meetings for today are done"
                    mtg_text = f"{mtg_noun} completed"
                    show_sun = True
                else:
                    upcoming = [m for m, s in zip(meetings, states)
                                if s == "upcoming"]
                    active = [m for m, s in zip(meetings, states)
                              if s == "active"]
                    if active:
                        end_dt = _parse_dt(active[0].get("end", ""))
                        end_str = (end_dt.strftime("%I:%M %p").lstrip("0")
                                   if end_dt else "")
                        in_txt = active[0].get("title", "meeting")
                        free_text = (f"In meeting: {in_txt}"
                                     + (f"  (till {end_str})" if end_str else ""))
                    elif upcoming:
                        nxt = _parse_dt(upcoming[0].get("start", ""))
                        nxt_str = (nxt.strftime("%I:%M %p").lstrip("0")
                                   if nxt else "")
                        free_text = (f"You're free till {nxt_str}"
                                     if nxt_str else f"{mtg_noun} today")
                    else:
                        free_text = f"{mtg_noun} today"
                    mtg_text = f"{mtg_noun} today"
                    show_sun = True
            else:
                free_text = f"{mtg_noun} scheduled"
                mtg_text = ""
                show_sun = False

        # ── Build card ─────────────────────────────────────────────────────────
        card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                     r=_ff(29.66), **_ph(CX, CY, CW, CH))

        # Clock icon (left)
        clock_src = _asset("icon_clock.png")
        if clock_src:
            card.add_widget(Image(
                source=clock_src, fit_mode="contain",
                size_hint=(53.68 / CW, 53.68 / CH),
                pos_hint={"x": 31.08 / CW, "y": (CH - 24.02 - 53.68) / CH}))
        else:
            card.add_widget(_lbl(
                "O", _FSB, _ff(38), _MUTED, ha="center", va="middle",
                size_hint=(53.68 / CW, 53.68 / CH),
                pos_hint={"x": 31.08 / CW, "y": (CH - 24.02 - 53.68) / CH}))

        # Free / status text
        card.add_widget(_lbl(
            free_text, _FB, _ff(32.49), _WHITE,
            va="middle",
            size_hint=(700 / CW, 39 / CH),
            pos_hint={"x": 97.47 / CW, "y": (CH - 31.08 - 39) / CH}))

        # Sun icon + meeting count (right side) — only if there are meetings
        if show_sun and mtg_text:
            sun_src = _asset("icon_sun.png")
            if sun_src:
                card.add_widget(Image(
                    source=sun_src, fit_mode="contain",
                    size_hint=(49.44 / CW, 49.44 / CH),
                    pos_hint={"x": 884.26 / CW, "y": (CH - 28.25 - 49.44) / CH}))
            card.add_widget(_lbl(
                mtg_text, _FB, _ff(31.08), _WHITE,
                va="middle",
                size_hint=(280 / CW, 37 / CH),
                pos_hint={"x": 943.59 / CW, "y": (CH - 35.32 - 37) / CH}))

        self._root_layout.add_widget(card)
        self._day_widgets.append(card)

    # ── Scrollable meeting area (dynamic) ─────────────────────────────────────
    # Scroll area: x=24.02, y=377.15  w=1210.56  h≈329 (stops above Add button)

    # Figma row geometry constants (in Figma coord space)
    _ROW_W   = 1210.56   # matches scroll container width
    _CARD_H  = 104.53    # meeting card height
    _CARD_X  = 252.84    # card x within row  (= 276.86 - 24.02)
    _CARD_W  = 954.89    # card width
    _SEP_X   = 179.39    # separator x within row (= 203.41 - 24.02)
    _TIME_X  = 24.0      # time-label x within row

    def _build_day_meetings(self, meetings: list, now, is_today: bool) -> None:
        """Build the vertical separator + scrollable meeting-card list."""
        # Full height from meeting-area top (377.15) to Add-button top (716.16)
        SEP_VIS_H = 339.01
        sep = Widget(**_ph(203.41, 377.15, 2.83, SEP_VIS_H))
        with sep.canvas.before:
            Color(154 / 255, 189 / 255, 255 / 255, 0.6)  # #9ABDFF
            _sr = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(
            pos=lambda w, v: setattr(_sr, "pos", v),
            size=lambda w, v: setattr(_sr, "size", v))
        self._root_layout.add_widget(sep)
        self._day_widgets.append(sep)

        if not meetings:
            # "No meetings" placeholder label in the scroll zone
            no_mtg = _lbl(
                "No meetings scheduled for this day",
                _FSB, _ff(28), _MUTED,
                ha="center", va="middle",
                **_ph(24.02, 377.15, 1210.56, SEP_VIS_H))
            self._root_layout.add_widget(no_mtg)
            self._day_widgets.append(no_mtg)
            return

        # Determine glow target (index of first non-past meeting, today only)
        glow_idx = None
        if is_today:
            for i, m in enumerate(meetings):
                if _m_state(m, now) != "past":
                    glow_idx = i
                    break

        SCALE = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)
        CARD_H_PX = max(70, round(self._CARD_H * SCALE))
        ROW_SPACING = max(4, round(8 * SCALE))

        scroll = ScrollView(
            do_scroll_x=False,
            bar_width=4,
            bar_color=(154 / 255, 189 / 255, 255 / 255, 0.5),
            bar_inactive_color=(154 / 255, 189 / 255, 255 / 255, 0.2),
            **_ph(24.02, 377.15, 1210.56, SEP_VIS_H),
        )
        content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=ROW_SPACING,
            padding=[0, 0, 0, ROW_SPACING],
        )
        content.bind(minimum_height=content.setter("height"))

        for i, m in enumerate(meetings):
            state = _m_state(m, now) if is_today else "upcoming"
            has_glow = (not is_today) or (i == glow_idx)
            row = self._make_meeting_row(m, state, has_glow, CARD_H_PX)
            content.add_widget(row)

        scroll.add_widget(content)
        self._root_layout.add_widget(scroll)
        self._day_widgets.append(scroll)

    def _make_meeting_row(self, m: dict, state: str, has_glow: bool,
                          card_h_px: int) -> Widget:
        """One row: timeline dot + time label (left) + meeting card (right)."""
        CW = self._ROW_W
        CH = self._CARD_H

        row = FloatLayout(size_hint=(1, None), height=card_h_px)

        # ── Timeline dot ──────────────────────────────────────────────────────
        if state in ("active", "upcoming"):
            if state == "active":
                ds, stroke = 33.9, (0.0, 0.565, 1.0, 1.0)   # #0090FF
            else:
                ds, stroke = 25.43, (0.0, 0.314, 1.0, 1.0)  # #0050FF
            row.add_widget(_GlowDot(
                stroke_rgba=stroke,
                glow_mult=1.7,
                glow=has_glow,
                size_hint=(ds / CW, ds / CH),
                pos_hint={
                    "x": (self._SEP_X - ds / 2) / CW,
                    "y": (CH / 2 - ds / 2) / CH,
                },
            ))
        else:
            # Past meeting — small dim dot without glow
            ds = 18.0
            dim = _GlowDot(
                stroke_rgba=(0.6, 0.7, 1.0, 1.0),
                glow_mult=1.4,
                glow=False,
                size_hint=(ds / CW, ds / CH),
                pos_hint={
                    "x": (self._SEP_X - ds / 2) / CW,
                    "y": (CH / 2 - ds / 2) / CH,
                },
            )
            row.add_widget(dim)

        # ── Time label ────────────────────────────────────────────────────────
        start_dt = _parse_dt(m.get("start", ""))
        time_str = (start_dt.strftime("%I:%M %p").lstrip("0")
                    if start_dt is not None else "--:--")
        tcol = _MUTED if state == "past" else _WHITE
        row.add_widget(_lbl(
            time_str, _FB, _ff(28.25 * 1.2), tcol,
            va="middle",
            size_hint=(130 / CW, 40 / CH),
            pos_hint={
                "x": self._TIME_X / CW,
                "y": (CH / 2 - 20) / CH,
            },
        ))

        # ── Meeting card ──────────────────────────────────────────────────────
        card = _Card(
            ct=_MTG_T, cb=_MTG_B, bdr=_BDR_MTG,
            r=_ff(25.43),
            size_hint=(self._CARD_W / CW, 1.0),
            pos_hint={"x": self._CARD_X / CW, "y": 0.0},
        )
        self._fill_meeting_card(card, m, state, self._CARD_W, CH)
        row.add_widget(card)
        return row

    def _fill_meeting_card(self, card, m: dict, state: str,
                           cw: float, ch: float) -> None:
        """Populate meeting card contents (icon, title, duration, details btn)."""
        title = m.get("title", "-")

        # Icon circle  32.49, 16.95  70.63×70.63
        IW, IH, IX, IY = 70.63, 70.63, 32.49, 16.95
        mtg_src = _asset("icon_meeting.png")
        if mtg_src:
            card.add_widget(Image(
                source=mtg_src, fit_mode="contain",
                size_hint=(IW / cw, IH / ch),
                pos_hint={"x": IX / cw, "y": (ch - IY - IH) / ch}))
        else:
            ic = _Card(ct=_ICON_BG, cb=_ICON_BG, bdr=_BDR_ICON,
                       r=_ff(16.21),
                       size_hint=(IW / cw, IH / ch),
                       pos_hint={"x": IX / cw, "y": (ch - IY - IH) / ch})
            ic.add_widget(_lbl(
                title[0].upper(), _FSB, _ff(30), _BTDAY,
                ha="center", va="middle",
                size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
            card.add_widget(ic)

        # Title
        title_col = _MUTED if state == "past" else _WHITE
        card.add_widget(_lbl(
            title, _FB, _ff(28.25), title_col,
            va="middle",
            size_hint=(300 / cw, 34 / ch),
            pos_hint={"x": 129.95 / cw, "y": (ch - 16.95 - 34) / ch}))

        # Duration row: clock icon + text
        dur_min = (m.get("duration") or 0) // 60
        if not dur_min:
            s = _parse_dt(m.get("start", ""))
            e = _parse_dt(m.get("end", ""))
            if s and e:
                dur_min = int((e - s).total_seconds() / 60)
        dur_str = f"{dur_min} min" if dur_min else ""

        clock_src = _asset("icon_clock.png")
        dur_row_cy = (ch - 56.50 - 31.08) / ch + (31.08 / ch) / 2
        ICON_SZ = 20.0
        icon_y = dur_row_cy - (ICON_SZ / ch) / 2
        if clock_src:
            card.add_widget(Image(
                source=clock_src, fit_mode="contain",
                size_hint=(ICON_SZ / cw, ICON_SZ / ch),
                pos_hint={"x": 129.95 / cw, "y": icon_y}))
        dur_x = 155.0 if clock_src else 129.95
        card.add_widget(_lbl(
            dur_str, _FSB, _ff(22.6), _MUTED,
            va="middle",
            size_hint=(175 / cw, 31.08 / ch),
            pos_hint={"x": dur_x / cw, "y": (ch - 56.50 - 31.08) / ch}))

        # Details button (right-aligned, no Join button)
        DW, DH = 144.08, 56.5
        db = _TapCard(
            ct=(0, 0, 0, 0), cb=(0, 0, 0, 0),
            bdr=_BDR_BTN,
            r=_ff(12.71),
            size_hint=(DW / cw, DH / ch),
            pos_hint={"x": 778.32 / cw, "y": (ch - 24.01 - DH) / ch})

        _det_h = 32
        db.add_widget(_lbl(
            "Details", _FB, _ff(21.19 * 1.3), _WHITE,
            va="middle",
            size_hint=(85 / DW, _det_h / DH),
            pos_hint={"x": 14.02 / DW, "y": (DH - _det_h) / 2 / DH}))

        # Arrow icon — priority: exact Figma node (calendar) → brief → home variants
        arr_src = ""
        for _p in [
            _CAL / "icon_arrow_details.png",          # weui:arrow-filled from Figma meeting card
            _CAL / "icon_nav_left_arrow.png",          # same weui arrow from grid
            _CAL / "icon_weui_arrow.png",              # weui component
            ASSETS_DIR / "brief" / "figma" / "icon_arrow_right.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow_card.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow.png",
        ]:
            if _p.is_file():
                arr_src = str(_p)
                break
        if arr_src:
            AW, AH = 18.0, 30.0
            db.add_widget(Image(
                source=arr_src, fit_mode="contain",
                size_hint=(AW / DW, AH / DH),
                pos_hint={"x": 110.0 / DW, "y": (DH / 2 - AH / 2) / DH}))
        else:
            db.add_widget(_lbl(
                ">", _FB, _ff(28), _WHITE,
                ha="center", va="middle",
                size_hint=(28 / DW, 34 / DH),
                pos_hint={"x": 108.0 / DW, "y": (DH - 34) / 2 / DH}))

        card.add_widget(db)

    # ── Add-event button ───────────────────────────────────────────────────────
    # Frame '27':  440.72, 716.16  378.57×60.74  r=16.95
    # gg:add icon   98.88,  9.89   42.38×42.38
    # "Add event"  146.91, 14.13  133×34  Bold 28.25  #006BF9

    def _build_add_button(self, root: FloatLayout) -> None:
        BW, BH = 378.57, 60.74
        btn = _TapCard(ct=_MTG_T, cb=_MTG_B, bdr=_BDR_MTG,
                       r=_ff(16.95), **_ph(440.72, 716.16, BW, BH))

        btn.add_widget(_lbl(
            "+", _FB, _ff(34), _BLUE_A,
            ha="center", va="middle",
            size_hint=(42.38 / BW, 42.38 / BH),
            pos_hint={"x": 98.88 / BW, "y": (BH - 9.89 - 42.38) / BH}))

        btn.add_widget(_lbl(
            "Add event", _FB, _ff(28.25), _BLUE_A,
            va="middle",
            size_hint=(133 / BW, 34 / BH),
            pos_hint={"x": 146.91 / BW, "y": (BH - 14.13 - 34) / BH}))

        root.add_widget(btn)

    # ── Day selection ──────────────────────────────────────────────────────────

    def _select_day(self, d: date, col_idx: int) -> None:
        today = display_now().date()
        self._sel_date = d

        # Only the tapped day gets a highlight; every other day is cleared.
        # This ensures Monday (or today) loses its box when another day is tapped.
        for col_date, hl in zip(self._col_dates, self._highlights):
            if col_date == d:
                hl.set_mode("today" if col_date == today else "sel")
            else:
                hl.set_mode("none")

        if self._heading_lbl:
            self._heading_lbl.text = (
                "Today" if d == today else _DAY_FULL[d.weekday()])
        if self._datestr_lbl:
            self._datestr_lbl.text = _fmt_date(d)

        self._update_day_view(d)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def set_target_date(self, d: date) -> None:
        """Called by main.py before on_enter to jump to a specific date instead of today."""
        self._target_date = d

    def on_enter(self) -> None:
        today = display_now().date()
        # NOTE: do NOT consume self._target_date here. goto_screen() in main.py
        # manually fires on_enter() once, and Kivy fires it again automatically
        # after the transition completes — so on_enter runs twice per entry.
        # If we cleared _target_date on the first call, the second call would
        # fall back to "today" and overwrite _sel_date / heading / meeting list,
        # which is exactly the "shows today even when I asked for tomorrow" bug.
        # _target_date is cleared in on_leave() instead.
        target = self._target_date if self._target_date is not None else today
        self._view_week_mon = target - timedelta(days=target.weekday())
        self._col_dates = [self._view_week_mon + timedelta(days=i) for i in range(7)]
        self._sel_date = target
        cache_key = f"calendar_week:{self._view_week_mon.isoformat()}"
        cached_week = self.app.ui_cache_get(cache_key)
        if isinstance(cached_week, dict) and cached_week:
            self._week_data = cached_week.get("days", {}) if "days" in cached_week else cached_week
        self.app.ui_cache_subscribe(cache_key, self._on_cached_week)

        for i, lbl in enumerate(self._date_lbls):
            lbl.text = str(self._col_dates[i].day)

        for col_date, hl in zip(self._col_dates, self._highlights):
            if col_date == target:
                hl.set_mode("today" if col_date == today else "sel")
            else:
                hl.set_mode("none")

        if self._heading_lbl:
            self._heading_lbl.text = "Today" if target == today else target.strftime("%A")
        if self._datestr_lbl:
            self._datestr_lbl.text = _fmt_date(target)

        # Only show "Loading calendar..." when there is no cached data to display.
        # If _week_data is already populated (e.g. returning from idle), keep the
        # existing content visible and let _load_week() update it silently.
        if not self._week_data:
            if self._busy_lbl:
                self._busy_lbl.text = "Loading calendar..."
            if self._free_lbl:
                self._free_lbl.text = ""

        # Draw dots from cached data immediately; _load_week() will refresh them.
        Clock.schedule_once(lambda _dt: self._rebuild_all_dots(), 0)
        Clock.schedule_once(lambda _dt: self._load_week(), 0)
        # Refresh meeting states every minute so glow follows the clock
        if self._refresh_event:
            self._refresh_event.cancel()
        self._refresh_event = Clock.schedule_interval(self._tick, 60)
        # No per-screen polling; centralized app sync loop updates this cache.

    def on_leave(self) -> None:
        # Clear target_date so the next entry without an explicit set_target_date()
        # call defaults to today.  (We do NOT clear it in on_enter because Kivy
        # fires on_enter twice per navigation — see comment in on_enter.)
        self._target_date = None
        if self._view_week_mon is not None:
            self.app.ui_cache_unsubscribe(
                f"calendar_week:{self._view_week_mon.isoformat()}",
                self._on_cached_week,
            )
        if self._refresh_event:
            self._refresh_event.cancel()
            self._refresh_event = None
        if getattr(self, "_data_poll_event", None):
            self._data_poll_event.cancel()
            self._data_poll_event = None

    def _tick(self, _dt) -> None:
        """Called every minute — re-renders the day view to update glow/state."""
        self._update_day_view(self._sel_date)

    def _load_week(self) -> None:
        async def _fetch():
            try:
                vm = self._view_week_mon
                if vm is None:
                    return
                cache_key = f"calendar_week:{vm.isoformat()}"
                cached = self.app.ui_cache_get(cache_key)
                if isinstance(cached, dict) and cached:
                    def _paint_cached(_dt):
                        self._week_data = cached.get("days", {}) if "days" in cached else cached
                        self._rebuild_all_dots()
                        self._update_day_view(self._sel_date)
                        self._update_header_summary()
                    Clock.schedule_once(_paint_cached, 0)
                if self.app.ui_cache_is_fresh(cache_key):
                    return
                if not self.app.ui_cache_mark_inflight(cache_key):
                    return
                end_d = vm + timedelta(days=6)
                data = await self.backend.get_calendar_week(
                    vm.isoformat(),
                    end_d.isoformat(),
                )

                def _apply(_dt):
                    self._week_data = data.get("days", {}) if data else {}
                    self.app.ui_cache_set(cache_key, dict(data or {}))
                    self._rebuild_all_dots()
                    self._update_day_view(self._sel_date)
                    self._update_header_summary()

                Clock.schedule_once(_apply, 0)
            except Exception as exc:
                logger.debug("CalendarScreen: get_calendar_week failed: %s", exc)
            finally:
                if vm is not None:
                    self.app.ui_cache_clear_inflight(f"calendar_week:{vm.isoformat()}")
        run_async(_fetch())

    def _on_cached_week(self, payload: dict) -> None:
        def _apply(_dt):
            if self.manager and self.manager.current != self.name:
                return
            self._week_data = payload.get("days", {}) if isinstance(payload, dict) else {}
            self._rebuild_all_dots()
            self._update_day_view(self._sel_date)
            self._update_header_summary()
        Clock.schedule_once(_apply, 0)

    # ── Week navigation ────────────────────────────────────────────────────────

    def _nav_week(self, delta: int) -> None:
        """Navigate to the previous (delta=-1) or next (delta=+1) week."""
        if self._view_week_mon is None:
            return
        self._view_week_mon += timedelta(weeks=delta)
        today = display_now().date()
        self._col_dates = [self._view_week_mon + timedelta(days=i) for i in range(7)]

        # Select today if visible in the new week, else select Monday
        if today in self._col_dates:
            self._sel_date = today
        else:
            self._sel_date = self._view_week_mon

        # Update date number labels
        for i, lbl in enumerate(self._date_lbls):
            lbl.text = str(self._col_dates[i].day)

        # Update highlights — only selected day is lit
        for col_date, hl in zip(self._col_dates, self._highlights):
            if col_date == self._sel_date:
                hl.set_mode("today" if col_date == today else "sel")
            else:
                hl.set_mode("none")

        # Update header day/date
        if self._heading_lbl:
            self._heading_lbl.text = (
                "Today" if self._sel_date == today
                else _DAY_FULL[self._sel_date.weekday()])
        if self._datestr_lbl:
            self._datestr_lbl.text = _fmt_date(self._sel_date)

        # Show "Loading..." while fetching the new week's data
        if self._busy_lbl:
            self._busy_lbl.text = "Loading calendar..."
        if self._free_lbl:
            self._free_lbl.text = ""

        # Clear current data / dots, then reload
        self._week_data = {}
        self._rebuild_all_dots()
        self._update_day_view(self._sel_date)
        Clock.schedule_once(lambda _dt: self._load_week(), 0)

    # ── Header summary (busy / free days) ─────────────────────────────────────

    def _update_header_summary(self) -> None:
        """Derive busy/free day labels from loaded _week_data and update header."""
        if self._busy_lbl is None:
            return

        _abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        busy: list[str] = []
        free: list[str] = []
        moderate: list[str] = []

        for i, d in enumerate(self._col_dates):
            key = d.isoformat()
            n = len(self._week_data.get(key, {}).get("meetings", []))
            abbr = _abbrs[i]
            if n >= 3:
                busy.append(abbr)
            elif n == 2:
                moderate.append(abbr)
            elif n == 0:
                free.append(abbr)

        if self._busy_lbl:
            if busy:
                self._busy_lbl.text = f"Busy: {', '.join(busy)}"
            elif moderate:
                self._busy_lbl.text = f"Moderate: {', '.join(moderate)}"
            else:
                self._busy_lbl.text = "Light week ahead"

        if self._free_lbl:
            if free:
                self._free_lbl.text = f"Free: {', '.join(free)}"
            else:
                self._free_lbl.text = ""
