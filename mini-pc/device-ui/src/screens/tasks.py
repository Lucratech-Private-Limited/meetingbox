"""Tasks screen — Figma node 1093:557 (1260 × 800 px).

Light-theme redesign
────────────────────
  • Full-bleed vs_bg.png + rgba(255,255,255,0.45) white overlay (same chrome as
    the voice screens) — replaces the old dark navy background.
  • "Tasks" title top-left (black, 42dot Bold 40).
  • Listening voice pill + WiFi + battery top-right (live state forwarded by main).
  • A light-grey (#DFDFDF) rounded segmented tab bar with 5 tabs:
        Today · Upcoming · Unplanned · Unfinished · Completed
    Active tab = filled purple (#6D48CC) pill, white text, dark-purple count
    circle. Inactive = transparent, #4D565F text, #CACACA count circle.
  • A white (90%) list card (rounded bottom corners) holding the active tab's
    task rows: purple status dot + title, thin dividers between rows, and a
    3-dot "more" icon that opens an Edit / Mark Done / Delete menu.

Behaviour
─────────
  • Tab-based: one section visible at a time.
  • Due date shown for Upcoming / Unfinished / Completed (NOT Today / Unplanned).
  • Sort: Upcoming by due date ascending; Unfinished + Completed by due date
    descending.
  • 3-dot menu → Edit (title/date/description), Mark Done, Delete.
  • Refresh on on_enter + every 60 s while visible.

Live data source
────────────────
GET /api/commitments
  • status="" → active + snoozed (open work) → overdue / today / upcoming / unplanned
  • status="completed" → completed bucket
PATCH /api/commitments/{id} → status / due_date / title / description
"""

from __future__ import annotations

import asyncio
import calendar as _cal_module
import json
import logging
from datetime import date, datetime, timedelta

from kivy.clock import Clock
from kivy.graphics import (
    Color, Ellipse, Line, PopMatrix, PushMatrix, Rectangle, RoundedRectangle, Translate,
)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.live_wifi_icon import LiveWifiIcon as _WifiIcon
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now
from frame19_layout import BG_BOT, BG_TOP
from page_swipe import PageSwipeController, any_modal_open
from screens.base_screen import BaseScreen
from screens.home import _BatteryWidget, _VoiceStatePill  # noqa: PLC2701
from ui_bg import attach_swirl_bg

logger = logging.getLogger(__name__)

# ── Design frame ───────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

_REFRESH_INTERVAL = 60.0

# ── Asset directories ──────────────────────────────────────────────────────────
_VS_DIR    = ASSETS_DIR / "voice-session" / "figma"
_CAL_DIR   = ASSETS_DIR / "calendar"      / "figma"


def _vs_asset(name: str) -> str:
    p = _VS_DIR / name
    return str(p) if p.is_file() else ""


def _cal_asset(name: str) -> str:
    p = _CAL_DIR / name
    return str(p) if p.is_file() else ""


# ── Colours — exact Figma hex (node 1093:557) ──────────────────────────────────
_BG_BASE      = (1/255,  12/255,  37/255, 1.0)    # #010C25 base behind image
_OVERLAY      = (1.0, 1.0, 1.0, 0.45)             # white 45 % overlay
_WHITE        = (1.0, 1.0, 1.0, 1.0)
_TITLE_BLK    = (0.0, 0.0, 0.0, 1.0)              # "Tasks" title

_BAR_BG       = (223/255, 223/255, 223/255, 1.0)  # #DFDFDF tab bar
_ACTIVE       = (109/255,  72/255, 204/255, 1.0)  # #6D48CC active pill / dot
_ACTIVE_BADGE = ( 90/255,  60/255, 171/255, 1.0)  # #5A3CAB active count circle
_TAB_TXT      = ( 77/255,  86/255,  95/255, 1.0)  # #4D565F inactive tab text
_BADGE_BG     = (202/255, 202/255, 202/255, 1.0)  # #CACACA inactive count circle

_CARD_BG      = (1.0, 1.0, 1.0, 0.9)              # task list card
_TASK_TXT     = ( 70/255,  78/255,  89/255, 1.0)  # #464E59 task title
_DIV_COL      = (158/255, 158/255, 158/255, 0.55) # #9E9E9E divider
_MORE_COL     = ( 76/255,  84/255,  97/255, 1.0)  # #4C5461 3-dot icon
_DUE_TXT      = (130/255, 134/255, 135/255, 1.0)  # muted due-date text

_MENU_BG      = (253/255, 253/255, 253/255, 1.0)  # #FDFDFD context menu
_MENU_TXT     = ( 77/255,  86/255,  95/255, 1.0)  # #4D565F menu item
_MENU_DEL     = (210/255,  60/255,  72/255, 1.0)  # delete tint
_SCRIM        = (0.0, 0.0, 0.0, 0.0)              # transparent tap-catcher

# Modal (Edit) palette — light, to match the new theme
_M_CARD       = (1.0, 1.0, 1.0, 1.0)
_M_FIELD      = (236/255, 236/255, 236/255, 1.0)  # #ECECEC input bg
_M_LABEL      = (130/255, 134/255, 135/255, 1.0)
_M_GREEN      = ( 16/255, 199/255, 109/255, 1.0)  # save
_M_GREY       = (210/255, 210/255, 210/255, 1.0)  # cancel

# Date-picker (kept dark — not part of this Figma node) ──────────────────────────
_PK_T   = (1/255,  17/255,  55/255, 1.0)
_PK_B   = (0.0,   10/255,  38/255, 1.0)
_PK_BDR = (63/255, 66/255,  83/255, 1.0)
_PK_MUT = (182/255, 186/255, 242/255, 1.0)
_PK_DIM = (155/255, 162/255, 178/255, 1.0)
_PK_BLU = (0.0, 107/255, 249/255, 1.0)
_PK_SELT = (14/255, 170/255, 105/255, 1.0)
_PK_SELB = (25/255, 211/255, 133/255, 1.0)
_PK_SELBDR = (40/255, 200/255, 140/255, 1.0)

# Font families (registered in main.py)
_F_BOLD = "42dot-Sans"   # use bold=True for Bold weight
_F_SB   = "42dot-SB"     # SemiBold
_F_MED  = "42dot-Med"    # Medium


# ── Coordinate helpers ─────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
    """Figma absolute px → Kivy size_hint + pos_hint (1260×800 root)."""
    return {
        "size_hint": (fw / FW, fh / FH),
        "pos_hint":  {"x": fx / FW, "y": (FH - fy - fh) / FH},
    }


def _ff(fs: float) -> int:
    """Scale a Figma pixel value by the display scale factor → int px."""
    scale = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)
    return max(6, round(fs * scale))


# ── Widget factories ───────────────────────────────────────────────────────────

def _lbl(text: str, font: str, size: int | float, color: tuple,
         ha: str = "left", va: str = "top", bold: bool = False, **kw) -> Label:
    lw = Label(text=text, font_name=font, font_size=size, color=color,
               halign=ha, valign=va, bold=bold, **kw)
    lw.bind(size=lw.setter("text_size"))
    return lw


class _ImgBtn(ButtonBehavior, Image):
    pass



class _Dot(Widget):
    """Solid colour circle (status dot)."""

    def __init__(self, color: tuple, **kw):
        super().__init__(**kw)
        with self.canvas:
            Color(*color)
            self._e = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._s, size=self._s)

    def _s(self, *_):
        self._e.pos  = self.pos
        self._e.size = self.size


class _MoreButton(ButtonBehavior, Widget):
    """Three horizontal dots — opens the per-task action menu."""

    def __init__(self, color: tuple = _MORE_COL, **kw):
        super().__init__(**kw)
        self._col = color
        with self.canvas:
            Color(*color)
            self._d1 = Ellipse()
            self._d2 = Ellipse()
            self._d3 = Ellipse()
        self.bind(pos=self._s, size=self._s)

    def _s(self, *_):
        w, h = self.size
        d = max(4, h)
        cy = self.y + (h - d) / 2
        gap = (w - 3 * d) / 2 if w > 3 * d else d * 0.6
        x0 = self.x
        for i, e in enumerate((self._d1, self._d2, self._d3)):
            e.size = (d, d)
            e.pos  = (x0 + i * (d + gap), cy)


class _PillBG(FloatLayout):
    """A solid rounded-rectangle background (used for the tab pill + count badge).

    FloatLayout (not bare Widget) so a child label — e.g. the count number — is
    laid out by size_hint/pos_hint instead of collapsing to the (0,0) corner.
    """

    def __init__(self, color: tuple, radius: float, **kw):
        super().__init__(**kw)
        self._r = radius
        with self.canvas.before:
            self._c = Color(*color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
        self.bind(pos=self._s, size=self._s)

    def set_color(self, color: tuple) -> None:
        self._c.rgba = color

    def _s(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [self._r]


# ── Date/time helpers ──────────────────────────────────────────────────────────

def _now_naive() -> datetime:
    return display_now().replace(tzinfo=None)


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            try:
                from config import to_display_local
                d = to_display_local(d).replace(tzinfo=None)
            except Exception:
                d = d.replace(tzinfo=None)
        return d
    except Exception:
        return None


def _fmt_time_12h(d: datetime) -> str:
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _short_due(row: dict) -> str:
    """Compact due-date label for Upcoming / Unfinished / Completed rows."""
    d = _parse_dt(row.get("due_at") or row.get("remind_at"))
    if d is None:
        return ""
    now = _now_naive()
    days = (d.date() - now.date()).days
    if days == 0:
        return _fmt_time_12h(d)
    if days == 1:
        return "Tomorrow"
    if days == -1:
        return "Yesterday"
    if 1 < days < 7:
        return d.strftime("%A")
    if days < 0:
        return f"{_MONTHS[d.month - 1]} {d.day}"
    return f"{_MONTHS[d.month - 1]} {d.day}"


def _due_sort_key(row: dict) -> datetime:
    d = _parse_dt(row.get("due_at") or row.get("remind_at"))
    return d or datetime.max


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t).strip() for t in tags if str(t).strip()]
    except Exception:
        pass
    return []


# ── Task bucketing ─────────────────────────────────────────────────────────────

def _categorize(row: dict) -> str | None:
    """Bucket an open (active/snoozed) row → overdue/due_today/upcoming/unplanned."""
    status = (row.get("status") or "").lower()
    if status in ("completed", "cancelled", "canceled"):
        return None
    raw = (row.get("due_at") or "").strip()
    if not raw:
        return "unplanned"
    d = _parse_dt(raw)
    if d is None:
        return "unplanned"
    now_naive   = _now_naive()
    today_start = now_naive.replace(hour=0,  minute=0,  second=0,  microsecond=0)
    today_end   = now_naive.replace(hour=23, minute=59, second=59, microsecond=0)
    if d < today_start:
        return "overdue"
    if d <= today_end:
        return "due_today"
    return "upcoming"


# ── Date-picker (kept dark; reused by Edit modal + assign date) ─────────────────

class _ModalCard(FloatLayout):
    def __init__(self, color: tuple, bdr: tuple, r: float = 14, **kw):
        super().__init__(**kw)
        self._r = r
        with self.canvas.before:
            Color(*color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[r])
        with self.canvas.after:
            Color(*bdr[:3], 0.9)
            self._ln = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, r),
                            width=1.0)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        r = self._r
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [r]
        self._ln.rounded_rectangle = (self.x, self.y, self.width, self.height, r)


class _TapPill(ButtonBehavior, _ModalCard):
    pass


class _CalendarPickerModal(ModalView):
    _MONTH_NAMES = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    _DOW_LABELS = ("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")

    def __init__(self, on_pick, *, allow_clear: bool = False, **kw):
        super().__init__(size_hint=(0.68, None), background_color=(0, 0, 0, 0.6), **kw)
        self._on_pick = on_pick
        self._allow_clear = allow_clear
        self._today = display_now().date()
        self._year = self._today.year
        self._month = self._today.month
        self._selected: date | None = None
        self._root = _ModalCard(color=_PK_T, bdr=_PK_BDR, r=_ff(20))
        self.add_widget(self._root)
        self._build()

    def _build(self) -> None:
        root = self._root
        root.clear_widgets()
        GAP, NAV_H, DOW_H, CELL_H, FOOT_H, PAD, CELL_G = (
            _ff(6), _ff(50), _ff(26), _ff(44), _ff(48), _ff(14), _ff(4))
        weeks = _cal_module.Calendar(firstweekday=6).monthdayscalendar(self._year, self._month)
        n_weeks = len(weeks)
        vbox_h = (PAD + NAV_H + GAP + DOW_H + GAP
                  + n_weeks * CELL_H + max(0, n_weeks - 1) * GAP + GAP + FOOT_H + PAD)
        self.height = vbox_h / 0.90
        vbox = BoxLayout(orientation="vertical", size_hint=(0.90, None), height=vbox_h,
                         pos_hint={"center_x": 0.5, "center_y": 0.5},
                         spacing=GAP, padding=[0, PAD, 0, PAD])
        nav = BoxLayout(orientation="horizontal", size_hint=(1, None), height=NAV_H, spacing=_ff(8))
        prev_btn = _TapPill(color=_PK_T, bdr=_PK_BDR, r=_ff(10))
        prev_btn.size_hint = (None, 1); prev_btn.width = _ff(50)
        prev_btn.add_widget(_lbl("‹", _F_SB, _ff(26), _WHITE, ha="center", va="middle",
                                 size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        prev_btn.bind(on_release=lambda *_: self._go_month(-1))
        nav.add_widget(prev_btn)
        nav.add_widget(_lbl(f"{self._MONTH_NAMES[self._month - 1]}  {self._year}",
                            _F_SB, _ff(18), _WHITE, ha="center", va="middle", size_hint=(1, 1)))
        next_btn = _TapPill(color=_PK_T, bdr=_PK_BDR, r=_ff(10))
        next_btn.size_hint = (None, 1); next_btn.width = _ff(50)
        next_btn.add_widget(_lbl("›", _F_SB, _ff(26), _WHITE, ha="center", va="middle",
                                 size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        next_btn.bind(on_release=lambda *_: self._go_month(1))
        nav.add_widget(next_btn)
        vbox.add_widget(nav)
        dow_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=DOW_H, spacing=CELL_G)
        for label in self._DOW_LABELS:
            dow_row.add_widget(_lbl(label, _F_MED, _ff(11), _PK_DIM, ha="center", va="middle", size_hint=(1, 1)))
        vbox.add_widget(dow_row)
        for week in weeks:
            week_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=CELL_H, spacing=CELL_G)
            for day_num in week:
                if day_num == 0:
                    week_row.add_widget(Widget(size_hint=(1, 1)))
                    continue
                d = date(self._year, self._month, day_num)
                is_past = (d < self._today)
                is_today = (d == self._today)
                is_selected = (d == self._selected)
                if is_selected:
                    cc, bdr, text_col = _PK_SELT, _PK_SELBDR, _WHITE
                elif is_today:
                    cc, bdr, text_col = _PK_T, _PK_BDR, _PK_BLU
                elif is_past:
                    cc, bdr, text_col = _PK_T, (0.25, 0.26, 0.33, 0.4), (1.0, 1.0, 1.0, 0.22)
                else:
                    cc, bdr, text_col = _PK_T, _PK_BDR, _WHITE
                cell = _TapPill(color=cc, bdr=bdr, r=_ff(7))
                cell.size_hint = (1, 1)
                cell.add_widget(_lbl(str(day_num), _F_MED, _ff(13), text_col, ha="center",
                                     va="middle", size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
                if not is_past:
                    _iso = d.isoformat()
                    cell.bind(on_release=lambda *_, iso=_iso: self._select(iso))
                week_row.add_widget(cell)
            vbox.add_widget(week_row)
        footer = BoxLayout(orientation="horizontal", size_hint=(1, None), height=FOOT_H, spacing=_ff(10))
        if self._allow_clear:
            clr = _TapPill(color=_PK_T, bdr=_PK_BDR, r=_ff(10)); clr.size_hint = (0.5, 1)
            clr.add_widget(_lbl("No date", _F_MED, _ff(14), _PK_MUT, ha="center", va="middle",
                                size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
            clr.bind(on_release=lambda *_: self._pick_and_close(None))
            footer.add_widget(clr)
        cancel = _TapPill(color=_PK_T, bdr=_PK_BDR, r=_ff(10))
        cancel.size_hint = (1, 1) if not self._allow_clear else (0.5, 1)
        cancel.add_widget(_lbl("Cancel", _F_MED, _ff(14), _WHITE, ha="center", va="middle",
                               size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        cancel.bind(on_release=lambda *_: self.dismiss())
        footer.add_widget(cancel)
        vbox.add_widget(footer)
        root.add_widget(vbox)

    def _go_month(self, delta: int) -> None:
        month = self._month + delta
        year = self._year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self._month, self._year = month, year
        self._build()

    def _select(self, iso: str) -> None:
        try:
            self._selected = date.fromisoformat(iso)
        except Exception:
            pass
        self._pick_and_close(iso)

    def _pick_and_close(self, iso: str | None) -> None:
        try:
            self._on_pick(iso)
        except Exception:
            logger.exception("calendar date pick callback failed")
        self.dismiss()


# ── Edit task modal (light theme) ───────────────────────────────────────────────

class _EditTaskModal(ModalView):
    """Edit a task's title / due date / description.

    on_save(title: str, due_date_iso: str | None, detail: str | None)
    """

    def __init__(self, on_save, *, title: str, due_iso: str | None,
                 detail: str | None, **kw):
        super().__init__(size_hint=(0.7, 0.78), background_color=(0, 0, 0, 0.55), **kw)
        self._on_save = on_save
        self._due_iso = due_iso

        root = FloatLayout()
        with root.canvas.before:
            Color(*_M_CARD)
            self._bg = RoundedRectangle(pos=root.pos, size=root.size, radius=[_ff(24)])
        root.bind(pos=lambda w, v: setattr(self._bg, "pos", v),
                  size=lambda w, v: (setattr(self._bg, "size", v),
                                     setattr(self._bg, "radius", [_ff(24)])))
        self.add_widget(root)

        root.add_widget(_lbl("Edit task", _F_BOLD, _ff(30), _TASK_TXT, bold=True,
                             ha="left", va="middle", size_hint=(0.9, None), height=_ff(46),
                             pos_hint={"x": 0.05, "top": 0.96}))

        root.add_widget(_lbl("Title", _F_MED, _ff(15), _M_LABEL, ha="left", va="bottom",
                             size_hint=(0.9, None), height=_ff(22), pos_hint={"x": 0.05, "top": 0.84}))
        self._title_input = TextInput(
            multiline=False, text=title or "", font_size=_ff(19),
            size_hint=(0.9, None), height=_ff(58), pos_hint={"x": 0.05, "top": 0.80},
            background_color=_M_FIELD, foreground_color=(0.1, 0.1, 0.12, 1),
            cursor_color=(0.1, 0.1, 0.12, 1), padding=[_ff(14), _ff(14), _ff(14), _ff(14)])
        root.add_widget(self._title_input)

        root.add_widget(_lbl("Due date", _F_MED, _ff(15), _M_LABEL, ha="left", va="bottom",
                             size_hint=(0.9, None), height=_ff(22), pos_hint={"x": 0.05, "top": 0.65}))
        date_row = BoxLayout(orientation="horizontal", size_hint=(0.9, None), height=_ff(52),
                             pos_hint={"x": 0.05, "top": 0.61}, spacing=_ff(10))
        self._date_pill = self._light_pill(0.7)
        self._date_label = _lbl("", _F_MED, _ff(17), _TASK_TXT, ha="center", va="middle",
                                size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        self._date_pill.add_widget(self._date_label)
        self._date_pill.bind(on_release=lambda *_: self._open_date_picker())
        date_row.add_widget(self._date_pill)
        clr = self._light_pill(0.3)
        clr.add_widget(_lbl("Clear", _F_MED, _ff(16), _M_LABEL, ha="center", va="middle",
                            size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        clr.bind(on_release=lambda *_: self._set_due(None))
        date_row.add_widget(clr)
        root.add_widget(date_row)

        root.add_widget(_lbl("Description", _F_MED, _ff(15), _M_LABEL, ha="left", va="bottom",
                             size_hint=(0.9, None), height=_ff(22), pos_hint={"x": 0.05, "top": 0.47}))
        self._detail_input = TextInput(
            multiline=True, text=detail or "", font_size=_ff(16),
            size_hint=(0.9, 0.22), pos_hint={"x": 0.05, "top": 0.43},
            background_color=_M_FIELD, foreground_color=(0.1, 0.1, 0.12, 1),
            cursor_color=(0.1, 0.1, 0.12, 1), padding=[_ff(14), _ff(14), _ff(14), _ff(14)])
        root.add_widget(self._detail_input)

        self._error_lbl = _lbl("", _F_MED, _ff(14), _MENU_DEL, ha="left", va="middle",
                               size_hint=(0.9, None), height=_ff(24), pos_hint={"x": 0.05, "y": 0.15})
        root.add_widget(self._error_lbl)

        footer = BoxLayout(orientation="horizontal", size_hint=(0.9, 0.11),
                           pos_hint={"x": 0.05, "y": 0.04}, spacing=_ff(12))
        cancel = self._light_pill(1.0, color=_M_GREY)
        cancel.add_widget(_lbl("Cancel", _F_SB, _ff(17), _TASK_TXT, ha="center", va="middle",
                               size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        cancel.bind(on_release=lambda *_: self.dismiss())
        footer.add_widget(cancel)
        save = self._light_pill(1.0, color=_M_GREEN)
        save.add_widget(_lbl("Save", _F_SB, _ff(17), _WHITE, ha="center", va="middle",
                             size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        save.bind(on_release=lambda *_: self._submit())
        footer.add_widget(save)
        root.add_widget(footer)

        self._set_due(due_iso)

    @staticmethod
    def _light_pill(width_hint: float, color: tuple = _M_FIELD):
        pill = _LightTapPill(color=color, r=_ff(12))
        pill.size_hint = (width_hint, 1)
        return pill

    def _open_date_picker(self) -> None:
        _CalendarPickerModal(self._set_due, allow_clear=True).open()

    def _set_due(self, iso: str | None) -> None:
        self._due_iso = iso
        if iso:
            try:
                d = datetime.fromisoformat(iso).date()
                self._date_label.text = d.strftime("%a %d %b %Y")
            except Exception:
                self._date_label.text = iso
            self._date_label.color = _ACTIVE
        else:
            self._date_label.text = "No date — Unplanned"
            self._date_label.color = _M_LABEL

    def _submit(self) -> None:
        title = (self._title_input.text or "").strip()
        if not title:
            self._error_lbl.text = "Title is required."
            return
        if len(title) > 160:
            self._error_lbl.text = "Keep the title under 160 characters."
            return
        detail = (self._detail_input.text or "").strip()
        try:
            self._on_save(title, self._due_iso, detail)
        except Exception:
            logger.exception("EditTaskModal save callback failed")
        self.dismiss()


class _LightTapPill(ButtonBehavior, FloatLayout):
    """Tappable solid rounded pill (light theme)."""

    def __init__(self, color: tuple, r: float = 12, **kw):
        super().__init__(**kw)
        self._r = r
        with self.canvas.before:
            Color(*color)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[r])
        self.bind(pos=self._s, size=self._s)

    def _s(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [self._r]


# ── Tabs metadata ──────────────────────────────────────────────────────────────
# Figma order (left → right): Today, Upcoming, Unplanned, Unfinished, Completed.
_TABS = [
    ("due_today", "Today"),
    ("upcoming",  "Upcoming"),
    ("unplanned", "Unplanned"),
    ("overdue",   "Unfinished"),
    ("completed", "Completed"),
]
_TAB_IDS = [t[0] for t in _TABS]
_SHOW_DUE = {"upcoming", "overdue", "completed"}   # buckets that display a due date


# ── TasksScreen ────────────────────────────────────────────────────────────────

class TasksScreen(BaseScreen):
    """Tasks screen — Figma 1093:557 (1260 × 800 px)."""

    def __init__(self, **kw):
        super().__init__(**kw)

        self._active_tab: str = "due_today"
        self._rows: dict[str, list] = {k: [] for k in _TAB_IDS}
        self._loading: bool = False
        self._refresh_ev = None
        # Optimistically-added rows (e.g. a task the user just created by voice).
        # Kept visible until the real row arrives from the backend fetch.
        self._optimistic: list[dict] = []

        # runtime widget refs
        self._tab_cells:   dict[str, FloatLayout] = {}
        self._tab_pills:   dict[str, _PillBG]     = {}
        self._tab_labels:  dict[str, Label]       = {}
        self._count_circles: dict[str, _PillBG]   = {}
        self._count_labels:  dict[str, Label]     = {}
        self._list_box:    BoxLayout | None        = None
        self._voice_pill:  _VoiceStatePill | None  = None
        self._menu_overlay: Widget | None          = None
        self._back_btn = None                      # top-left back button
        # Set by the Calendar→Tasks swipe so the back button is suppressed when
        # Tasks is reached as part of the Home page-swipe chain (you swipe back).
        self.entered_via_swipe = False
        self._page_tx = None                       # transform-only page translate

        self._build_ui()

        # Tasks is the right-most page in the chain. A left-to-right swipe reveals
        # Calendar (which sits to its left); there is no page to the right.
        self._calendar_pager = PageSwipeController(
            self,
            "calendar",
            direction=1,
            prepare_dest=self._prepare_adjacent,
            commit=lambda: self.app.goto_screen("calendar", transition="none"),
            can_start=self._can_page,
        )

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # Shared light paper-swirl background — identical to the Start-Recording /
        # Calendar screens — so the Home page-swipe chain feels continuous.
        attach_swirl_bg(root, BG_TOP, BG_BOT)

        self._build_header(root)
        self._build_tab_bar(root)
        self._build_list_area(root)
        self._build_chrome(root)
        self.add_widget(root)

    # ── Header ─────────────────────────────────────────────────────────────────

    def _build_header(self, root: FloatLayout) -> None:
        # Back button (top-left) — image if available, else a black chevron.
        back_src = _cal_asset("btn_back.png")
        if back_src:
            back = _ImgBtn(source=back_src, fit_mode="contain", **_ph(24.0, 82.0, 60.0, 60.0))
        else:
            back = _LightTapPill(color=(0, 0, 0, 0), r=0, **_ph(24.0, 82.0, 60.0, 60.0))
            back.add_widget(_lbl("‹", _F_BOLD, _ff(40), _TITLE_BLK, bold=True,
                                 ha="center", va="middle", size_hint=(1, 1),
                                 pos_hint={"x": 0, "y": 0}))
        back.bind(on_release=lambda *_: self.go_back())
        self._back_btn = back
        root.add_widget(back)

        # "Tasks" title — black, 42dot Bold 40. When the back button is
        # suppressed (floating-dock companion) pull the title to the left margin
        # so it aligns with the content instead of leaving an awkward gap.
        from config import dock_companion_enabled
        _title_x = 40.0 if dock_companion_enabled() else 98.0
        root.add_widget(_lbl("Tasks", _F_BOLD, _ff(40), _TITLE_BLK, bold=True,
                             ha="left", va="middle", **_ph(_title_x, 88.0, 320.0, 48.0)))

    # ── Tab bar  (Figma 29,170 1202×69 #DFDFDF r38) ─────────────────────────────

    def _build_tab_bar(self, root: FloatLayout) -> None:
        bar = _PillBG(_BAR_BG, _ff(38), **_ph(29.0, 170.0, 1202.0, 69.0))
        root.add_widget(bar)

        # 5 cells of 234×69 at Figma x 29 + {0,242,484,726,968}, y 170 (root-frame
        # absolute coords — cells are children of *root*, not the bar widget).
        for i, (tab_id, text) in enumerate(_TABS):
            cell = FloatLayout(**_ph(29.0 + i * 242.0, 170.0, 234.0, 69.0))

            pill = _PillBG((0, 0, 0, 0), _ff(34), size_hint=(1, 1),
                           pos_hint={"x": 0, "y": 0})
            cell.add_widget(pill)
            self._tab_pills[tab_id] = pill

            # Centred row: label + count circle.  Label auto-sized, circle 34×34.
            inner = BoxLayout(orientation="horizontal", size_hint=(None, None),
                              height=_ff(40), spacing=_ff(10),
                              pos_hint={"center_x": 0.5, "center_y": 0.5})
            inner.bind(minimum_width=inner.setter("width"))

            # Auto-width label (no text_size binding → no feedback loop).
            lbl = Label(text=text, font_name=_F_SB, font_size=_ff(32), color=_TAB_TXT,
                        halign="center", valign="middle", size_hint=(None, 1))
            lbl.bind(texture_size=lambda w, ts: setattr(w, "width", ts[0] + _ff(2)))
            inner.add_widget(lbl)
            self._tab_labels[tab_id] = lbl

            circle = _PillBG(_BADGE_BG, _ff(17), size_hint=(None, None),
                             size=(_ff(34), _ff(34)), pos_hint={"center_y": 0.5})
            cnt = _lbl("", _F_SB, _ff(20), _TAB_TXT, ha="center", va="middle",
                       size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
            circle.add_widget(cnt)
            inner.add_widget(circle)
            self._count_circles[tab_id] = circle
            self._count_labels[tab_id] = cnt

            cell.add_widget(inner)

            tap = _TabTap(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
            _tid = tab_id
            tap.bind(on_release=lambda *_, tid=_tid: self._on_tab(tid))
            cell.add_widget(tap)

            self._tab_cells[tab_id] = cell
            root.add_widget(cell)

        self._sync_tab_styles()

    # ── List card  (Figma 29,263 1202×… white90 r[0,0,38,38]) ───────────────────

    def _build_list_area(self, root: FloatLayout) -> None:
        # Two decoupled pieces avoid the ScrollView-resize bug:
        #   1. A canvas-only white background widget that HUGS the active section's
        #      content height (Figma: 143 px card for 2 tasks), top square / bottom
        #      rounded, anchored at Figma y=263 and growing downward.
        #   2. A FIXED full-height (transparent) ScrollView holding the task rows.
        #      Resizing a ScrollView dynamically detaches its content to (0,0); a
        #      fixed-size ScrollView keeps the rows pinned at the top correctly.
        CARD_TOP_FY = 263.0
        AVAIL_FH = FH - CARD_TOP_FY - 21.0
        r = _ff(38)

        # 1 ── white background that hugs the content height ─────────────────────
        card_bg = Widget(size_hint=(1202.0 / FW, None),
                         pos_hint={"x": 29.0 / FW, "top": (FH - CARD_TOP_FY) / FH})
        card_bg.height = _ff(150)
        self._card = card_bg
        # Single rounded rect with per-corner radius: square top, rounded bottom.
        # (Order is [bottom-left, bottom-right, top-right, top-left].)
        _crad = [r, r, 0, 0]
        with card_bg.canvas.before:
            Color(*_CARD_BG)
            self._card_round = RoundedRectangle(pos=card_bg.pos, size=card_bg.size, radius=_crad)

        def _sync_bg(w, *_):
            self._card_round.pos = w.pos
            self._card_round.size = w.size
            self._card_round.radius = _crad

        card_bg.bind(pos=_sync_bg, size=_sync_bg)
        Clock.schedule_once(lambda _dt: _sync_bg(card_bg), 0)
        root.add_widget(card_bg)

        # 2 ── fixed full-height scroll area with the task rows (on top of bg) ────
        sv = ScrollView(do_scroll_x=False, do_scroll_y=True,
                        bar_width=_ff(5), bar_color=[*_ACTIVE[:3], 0.5],
                        bar_inactive_color=[*_ACTIVE[:3], 0.15],
                        scroll_type=["bars", "content"],
                        **_ph(29.0, CARD_TOP_FY, 1202.0, AVAIL_FH))
        self._scroll = sv
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[0, 0, 0, _ff(14)], spacing=0)
        box.bind(minimum_height=box.setter("height"))
        self._list_box = box

        # Background hugs content, clamped to the space below the tab bar.
        def _fit_bg(*_):
            ph = self.height or float(DISPLAY_HEIGHT)
            max_h = max(_ff(80), ph * (AVAIL_FH / FH))
            card_bg.height = max(_ff(80), min(box.minimum_height, max_h))
        self._fit_card = _fit_bg
        box.bind(minimum_height=_fit_bg)
        self.bind(height=_fit_bg)

        sv.add_widget(box)
        root.add_widget(sv)

        box.add_widget(_lbl("Loading tasks…", _F_MED, _ff(26), _DUE_TXT,
                            ha="center", va="middle", size_hint=(1, None), height=_ff(120)))
        Clock.schedule_once(lambda _dt: _fit_bg(), 0)

    # ── Top-right chrome: voice pill + wifi + battery ───────────────────────────

    def _build_chrome(self, root: FloatLayout) -> None:
        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) for consistent size/position/font.
        # self._voice_pill stays None here on purpose; existing
        # `if self._voice_pill:` guards elsewhere no-op safely.
        # (WiFi + battery indicators removed — not relevant for the desktop app.)
        return

    # ── Tab selection / styling ─────────────────────────────────────────────────

    def set_active_tab(self, tab_id: str) -> None:
        if tab_id not in _TAB_IDS:
            return
        self._active_tab = tab_id
        if self._tab_labels:
            self._sync_tab_styles()
            self._rebuild_task_list()

    def _on_tab(self, tab_id: str) -> None:
        self._close_menu()
        if tab_id == self._active_tab:
            return
        self._active_tab = tab_id
        self._sync_tab_styles()
        self._rebuild_task_list()

    def _sync_tab_styles(self) -> None:
        for tid in _TAB_IDS:
            active = (tid == self._active_tab)
            self._tab_pills[tid].set_color(_ACTIVE if active else (0, 0, 0, 0))
            self._tab_labels[tid].color = _WHITE if active else _TAB_TXT
            self._count_circles[tid].set_color(_ACTIVE_BADGE if active else _BADGE_BG)
            self._count_labels[tid].color = _WHITE if active else _TAB_TXT

    # ── Task list renderer ──────────────────────────────────────────────────────

    def _rebuild_task_list(self, error_msg: str = "") -> None:
        if self._list_box is None:
            return
        self._close_menu()
        self._list_box.clear_widgets()

        if error_msg:
            self._list_box.add_widget(_lbl(
                f"Could not load tasks\n{error_msg[:80]}", _F_MED, _ff(20), _MENU_DEL,
                ha="center", va="middle", size_hint=(1, None), height=_ff(120)))
            return

        rows = list(self._rows.get(self._active_tab, []))
        if not rows:
            self._list_box.add_widget(_lbl(
                "No tasks here", _F_MED, _ff(26), _DUE_TXT,
                ha="center", va="middle", size_hint=(1, None), height=_ff(160)))
            return

        for idx, row in enumerate(rows):
            self._add_task_row(row, self._active_tab, last=(idx == len(rows) - 1))

    def _add_task_row(self, row: dict, bucket: str, *, last: bool) -> None:
        task_id = str(row.get("id") or "")
        title   = (row.get("title") or "Untitled task").strip()
        show_due = bucket in _SHOW_DUE
        due_text = _short_due(row) if show_due else ""

        ROW_H = _ff(72)
        roww = FloatLayout(size_hint=(1, None), height=ROW_H)

        # status dot — Figma: x:58, size:13×13 within 1202px card
        D = _ff(13)
        roww.add_widget(_Dot(_ACTIVE, size_hint=(None, None), size=(D, D),
                             pos_hint={"x": 58.0 / 1202.0, "center_y": 0.5}))

        # 3-dot more button — Figma: x:1089, width:41, height:9, right edge at 1130
        more = _MoreButton(size_hint=(None, None), size=(_ff(41), _ff(9)),
                           pos_hint={"right": 1130.0 / 1202.0, "center_y": 0.5})
        more.bind(on_release=lambda btn, tid=task_id, r=row, b=bucket: self._open_menu(btn, tid, r, b))
        roww.add_widget(more)

        # due date (left of the more button, 10px gap from button left edge at 1089)
        # right edge of due date = 1079/1202; due date width = 180px
        _DUE_RIGHT = 1079.0 / 1202.0
        title_right = _DUE_RIGHT
        if due_text:
            roww.add_widget(_lbl(due_text, _F_MED, _ff(24), _DUE_TXT, ha="right", va="middle",
                                 size_hint=(None, 1), width=_ff(180),
                                 pos_hint={"right": _DUE_RIGHT, "y": 0}))
            # title ends 10px left of due date's left edge (899/1202)
            title_right = 889.0 / 1202.0

        # title — Figma: x:98, fontSize:37, color #464E59
        title_x = 98.0 / 1202.0
        title_w = max(0.05, title_right - title_x)
        roww.add_widget(_lbl(title, _F_MED, _ff(37), _TASK_TXT, ha="left", va="middle",
                             size_hint=(title_w, 1), pos_hint={"x": title_x, "center_y": 0.5},
                             shorten=True, shorten_from="right"))

        self._list_box.add_widget(roww)

        if not last:
            div = Widget(size_hint=(1, None), height=max(1, _ff(1.5)))
            with div.canvas:
                Color(*_DIV_COL)
                _dr = Rectangle(pos=div.pos, size=div.size)

            def _sd(w, *_):
                inset = _ff(20)
                _dr.pos = (w.x + inset, w.y)
                _dr.size = (max(0, w.width - 2 * inset), w.height)
            div.bind(pos=_sd, size=_sd)
            self._list_box.add_widget(div)

    # ── Per-task action menu (Edit / Mark Done / Delete) ────────────────────────

    def _open_menu(self, anchor, task_id: str, row: dict, bucket: str) -> None:
        self._close_menu()
        if not task_id:
            return

        items: list[tuple[str, callable, tuple]] = []
        items.append(("Edit", lambda: self._edit_task(row), _MENU_TXT))
        if bucket != "completed":
            items.append(("Mark Done", lambda: self._patch_task(task_id, status="completed"), _MENU_TXT))
        items.append(("Delete", lambda: self._patch_task(task_id, status="cancelled"), _MENU_DEL))

        ITEM_H = _ff(56)
        PAD_V  = _ff(8)
        MENU_W = _ff(230)
        MENU_H = ITEM_H * len(items) + PAD_V * 2

        # Overlay covers the whole screen; tap outside closes.
        overlay = _MenuScrim(on_dismiss=self._close_menu, size_hint=(1, 1),
                             pos_hint={"x": 0, "y": 0})

        menu = FloatLayout(size_hint=(None, None), size=(MENU_W, MENU_H))
        with menu.canvas.before:
            Color(*_MENU_BG)
            _mbg = RoundedRectangle(pos=menu.pos, size=menu.size, radius=[_ff(20)])
        menu.bind(pos=lambda w, v: setattr(_mbg, "pos", v),
                  size=lambda w, v: (setattr(_mbg, "size", v), setattr(_mbg, "radius", [_ff(20)])))

        col = BoxLayout(orientation="vertical", size_hint=(1, 1), pos_hint={"x": 0, "y": 0},
                        padding=[0, PAD_V, 0, PAD_V], spacing=0)
        for n, (label, cb, color) in enumerate(items):
            it = _LightTapPill(color=(0, 0, 0, 0), r=0, size_hint=(1, None), height=ITEM_H)
            it.add_widget(_lbl(label, _F_BOLD, _ff(28), color, bold=True, ha="center",
                               va="middle", size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))

            def _make(cb=cb):
                def _run(*_a):
                    self._close_menu()
                    cb()
                return _run
            it.bind(on_release=_make())
            col.add_widget(it)
            if n != len(items) - 1:
                sep = Widget(size_hint=(1, None), height=max(1, _ff(1)))
                with sep.canvas:
                    Color(*_DIV_COL)
                    _sr = Rectangle(pos=sep.pos, size=sep.size)
                sep.bind(pos=lambda w, v, r=_sr: setattr(r, "pos", v),
                         size=lambda w, v, r=_sr: setattr(r, "size", v))
                col.add_widget(sep)
        menu.add_widget(col)

        # Position the menu just below the dots, right-aligned to them, clamped on-screen.
        ax, ay = anchor.to_window(anchor.x, anchor.y)
        mx = ax + anchor.width - MENU_W
        my = ay - MENU_H - _ff(6)
        mx = max(_ff(8), min(mx, self.width - MENU_W - _ff(8)))
        if my < _ff(8):
            my = ay + anchor.height + _ff(6)
        menu.pos = (mx, my)
        overlay.add_widget(menu)

        self._menu_overlay = overlay
        self.add_widget(overlay)

    def _close_menu(self, *_a) -> None:
        if self._menu_overlay is not None:
            try:
                self.remove_widget(self._menu_overlay)
            except Exception:
                pass
            self._menu_overlay = None

    # ── Menu actions ────────────────────────────────────────────────────────────

    def _edit_task(self, row: dict) -> None:
        task_id = str(row.get("id") or "")
        if not task_id:
            return
        title = (row.get("title") or "").strip()
        detail = (row.get("detail") or "").strip() or None
        due_iso = None
        d = _parse_dt(row.get("due_at") or row.get("remind_at"))
        if d is not None:
            due_iso = d.date().isoformat()

        def _save(new_title: str, new_due: str | None, new_detail: str | None) -> None:
            async def _go():
                try:
                    await self.backend.patch_commitment(
                        task_id, title=new_title,
                        due_date=new_due if new_due else None,
                        description=new_detail if new_detail is not None else "",
                    )
                except Exception as exc:
                    logger.warning("edit task patch failed: %s", exc)
                await asyncio.sleep(0.2)
                Clock.schedule_once(self._load_tasks, 0)
            run_async(_go())

        _EditTaskModal(_save, title=title, due_iso=due_iso, detail=detail).open()

    def _patch_task(self, task_id: str, status: str | None = None,
                    due_date: str | None = None) -> None:
        if not task_id or (not status and not due_date):
            return
        drop_row = status in ("completed", "cancelled")
        if drop_row:
            for bucket in self._rows:
                self._rows[bucket] = [r for r in self._rows[bucket]
                                      if str(r.get("id") or "") != task_id]
            self._update_counts()
            self._rebuild_task_list()

        async def _call():
            try:
                await self.backend.patch_commitment(task_id, status=status, due_date=due_date)
            except Exception as exc:
                logger.warning("patch_commitment failed: %s", exc)
            await asyncio.sleep(0.4)
            Clock.schedule_once(self._load_tasks, 0)
        run_async(_call())

    # ── Count badges ────────────────────────────────────────────────────────────

    def _update_counts(self) -> None:
        for tid in _TAB_IDS:
            n = len(self._rows.get(tid, []))
            self._count_labels[tid].text = str(n)

    # ── Data loading ────────────────────────────────────────────────────────────

    def _load_tasks(self, *_) -> None:
        async def _go():
            bucketed: dict[str, list] = {k: [] for k in _TAB_IDS}
            error_msg = ""
            try:
                # open work (active + snoozed)
                open_res = await self.backend.get_commitments(status="", limit=100)
                for r in (open_res.get("commitments") or []):
                    b = _categorize(r)
                    if b is not None:
                        bucketed[b].append(r)
                # completed
                done_res = await self.backend.get_commitments(status="completed", limit=100)
                bucketed["completed"] = list(done_res.get("commitments") or [])
            except Exception as exc:
                logger.error("tasks: load failed: %s", exc, exc_info=True)
                error_msg = str(exc)

            # Sorting rules
            bucketed["upcoming"].sort(key=_due_sort_key)                 # ascending
            bucketed["overdue"].sort(key=_due_sort_key, reverse=True)    # descending
            bucketed["completed"].sort(key=_due_sort_key, reverse=True)  # descending

            def _apply(_dt):
                self._loading = False
                self._merge_optimistic(bucketed)
                self._rows = bucketed
                self._update_counts()
                self._rebuild_task_list(error_msg=error_msg)

            Clock.schedule_once(_apply, 0)

        run_async(_go())

    def add_optimistic_task(self, title: str, due_date: str | None = None) -> str | None:
        """Show a just-created task immediately, before the backend round-trips.

        Returns the bucket id the task landed in (so callers can select the
        matching tab), or ``None`` if nothing was added.
        """
        title = (title or "").strip()
        if not title:
            return None
        import uuid
        due_at = (due_date or "").strip() or None
        row = {
            "id": f"opt-{uuid.uuid4().hex[:8]}",
            "title": title,
            "due_at": due_at,
            "status": "",
            "_optimistic": True,
        }
        bucket = _categorize(row) or "unplanned"
        # De-dupe against an identical pending optimistic row.
        if not any(
            (o.get("title") or "").strip().lower() == title.lower()
            for o in self._optimistic
        ):
            self._optimistic.append(row)
            self._rows.setdefault(bucket, []).append(row)
            if bucket in _SHOW_DUE:
                self._rows[bucket].sort(
                    key=_due_sort_key, reverse=(bucket != "upcoming"))
            self._update_counts()
            self._rebuild_task_list()
        return bucket

    def _merge_optimistic(self, bucketed: dict[str, list]) -> None:
        """Fold still-pending optimistic rows into a freshly-fetched bucket map.

        An optimistic row is dropped once a real row with the same title shows
        up in the fetch (the backend now owns it)."""
        if not self._optimistic:
            return
        real_titles = {
            (r.get("title") or "").strip().lower()
            for rows in bucketed.values() for r in rows
        }
        still_pending: list[dict] = []
        for opt in self._optimistic:
            title = (opt.get("title") or "").strip().lower()
            if title in real_titles:
                continue
            bucket = _categorize(opt) or "unplanned"
            bucketed.setdefault(bucket, []).append(opt)
            if bucket in _SHOW_DUE:
                bucketed[bucket].sort(
                    key=_due_sort_key, reverse=(bucket != "upcoming"))
            still_pending.append(opt)
        self._optimistic = still_pending

    # ── Voice state (live Listening pill) ───────────────────────────────────────

    def set_voice_session_state(self, state: str) -> None:
        if self._voice_pill is None:
            return
        label = {"listening": "Listening", "thinking": "Thinking",
                 "speaking": "Talking"}.get(state)
        if label:
            self._voice_pill.set_state_text(label)
            self._voice_pill.opacity = 1.0
        else:
            self._voice_pill.opacity = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    # ── Interactive page swipe (Tasks → Calendar) ─────────────────────────────

    def _can_page(self) -> bool:
        return self._menu_overlay is None and not any_modal_open()

    def _prepare_adjacent(self, dest) -> None:
        try:
            dest.prime_preview()
        except Exception:
            logger.exception("tasks: failed to prime adjacent page preview")

    def _ensure_page_translate(self) -> None:
        if self._page_tx is not None:
            return
        with self.canvas.before:
            PushMatrix()
            self._page_tx = Translate(0, 0, 0)
        with self.canvas.after:
            PopMatrix()

    def set_page_offset(self, dx: float) -> None:
        self._ensure_page_translate()
        self._page_tx.x = float(dx)

    def prime_preview(self) -> None:
        """Refresh content so the swipe preview shows live data.

        Reaching Tasks via a swipe means the back button is redundant (you swipe
        back), so hide it for the preview too — avoids a flash on commit.
        """
        self.set_page_offset(0.0)
        self._set_back_visible(False)
        try:
            Clock.schedule_once(self._load_tasks, 0)
        except Exception:
            logger.debug("tasks: prime_preview failed", exc_info=True)

    def _set_back_visible(self, visible: bool) -> None:
        if self._back_btn is None:
            return
        # In the floating-dock companion the dock is the only navigation surface,
        # so the back button is always suppressed.
        from config import dock_companion_enabled
        if dock_companion_enabled():
            visible = False
        self._back_btn.opacity = 1.0 if visible else 0.0
        self._back_btn.disabled = not visible

    def on_touch_down(self, touch):
        pager = getattr(self, "_calendar_pager", None)
        if pager is not None and pager.on_touch_down(touch):
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        pager = getattr(self, "_calendar_pager", None)
        if pager is not None and pager.on_touch_move(touch):
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        pager = getattr(self, "_calendar_pager", None)
        if pager is not None and pager.on_touch_up(touch):
            return True
        return super().on_touch_up(touch)

    def on_enter(self) -> None:
        pager = getattr(self, "_calendar_pager", None)
        if pager is not None:
            pager.cancel()
        self.set_page_offset(0.0)
        # Show the back button only when Tasks was opened by normal navigation
        # (e.g. voice), not when reached via the Home→Calendar→Tasks swipe chain.
        self._set_back_visible(not self.entered_via_swipe)
        self._loading = True
        if self._list_box is not None:
            self._list_box.clear_widgets()
            self._list_box.add_widget(_lbl(
                "Loading tasks…", _F_MED, _ff(26), _DUE_TXT, ha="center", va="middle",
                size_hint=(1, None), height=_ff(120)))
        Clock.schedule_once(self._load_tasks, 0)
        if self._refresh_ev is None:
            self._refresh_ev = Clock.schedule_interval(self._load_tasks, _REFRESH_INTERVAL)

    def on_leave(self) -> None:
        pager = getattr(self, "_calendar_pager", None)
        if pager is not None:
            pager.cancel()
        self.set_page_offset(0.0)
        self.entered_via_swipe = False
        self._close_menu()
        if self._refresh_ev is not None:
            self._refresh_ev.cancel()
            self._refresh_ev = None


class _TabTap(ButtonBehavior, Widget):
    """Transparent tap target laid over a tab cell."""


class _MenuScrim(FloatLayout):
    """Full-screen transparent catcher that closes the menu on outside tap."""

    def __init__(self, on_dismiss, **kw):
        super().__init__(**kw)
        self._on_dismiss = on_dismiss

    def on_touch_down(self, touch):
        # Let the menu (a child) handle its own taps; close on taps elsewhere.
        for child in self.children:
            if child.collide_point(*touch.pos):
                return super().on_touch_down(touch)
        self._on_dismiss()
        return True
