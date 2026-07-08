"""Calendar screen — light-theme redesign from Figma 3052:129 (1260 × 800 px).

Layout:
  • Left  — a fixed desk flip-calendar widget (purple "WED" header, big date
            number, month, busy-density dots, binder rings, stacked pages).
            When the displayed date changes (driven by voice) the front sheet
            flips like a tear-off calendar.
  • Right — a vertically scrollable column of meeting-detail tiles for the
            selected day.  The left calendar stays fixed while this scrolls.
  • Top-right — a "Listening" status pill (reflects the live voice state) plus
            the shared device wifi / battery status bar.

Date navigation is voice-only: main.py calls set_target_date() + on_enter().
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import (
    Color,
    Ellipse,
    Line,
    PopMatrix,
    PushMatrix,
    Rectangle,
    RoundedRectangle,
    Scale,
    Translate,
)
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now, to_display_local
from frame19_layout import BG_BOT, BG_TOP
from page_swipe import PageSwipeController, any_modal_open
from screens.base_screen import BaseScreen
from ui_bg import attach_swirl_bg

logger = logging.getLogger(__name__)

# ── Design frame ──────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

_CAL = ASSETS_DIR / "calendar" / "figma"


def _asset(name: str) -> str:
    p = _CAL / name
    return str(p) if p.is_file() else ""


# ── Colours (exact Figma hex) ─────────────────────────────────────────────────
_WHITE       = (1.0, 1.0, 1.0, 1.0)
_CARD_WHITE  = (253 / 255, 253 / 255, 253 / 255, 1.0)   # #FDFDFD
_PILL_BG     = (250 / 255, 250 / 255, 250 / 255, 1.0)   # #FAFAFA
_PURPLE      = (109 / 255, 72 / 255, 204 / 255, 1.0)    # #6D48CC header / dot / density dots
_TILE_NAME   = (109 / 255, 72 / 255, 203 / 255, 1.0)    # #6D48CB meeting name
_ACCENT_BAR  = (191 / 255, 168 / 255, 248 / 255, 1.0)   # #BFA8F8 tile top bar
_NUM_GREY    = (77 / 255, 83 / 255, 97 / 255, 1.0)      # #4D5361 big number / month
_HEADER_TXT  = (253 / 255, 253 / 255, 253 / 255, 1.0)   # #FDFDFD WED on purple
_TILE_TEXT   = (72 / 255, 79 / 255, 95 / 255, 1.0)      # #484F5F time / count / duration
_DIVIDER     = (158 / 255, 158 / 255, 158 / 255, 1.0)   # #9E9E9E
_LISTEN_TXT  = (58 / 255, 59 / 255, 61 / 255, 1.0)      # #3A3B3D
_CARD_STROKE = (202 / 255, 202 / 255, 202 / 255, 1.0)   # #CACACA back-page edge
_SHADOW      = (118 / 255, 129 / 255, 127 / 255, 0.28)  # soft drop shadow

_FB = "42dot-Sans"   # Bold weight (pass bold=True)
_SB = "42dot-SB"     # SemiBold

_IST = timezone(timedelta(hours=5, minutes=30))

_MONTHS_FULL = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
                "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_DAY_ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


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


def _rel(fx: float, fy: float, fw: float, fh: float, pw: float, ph: float) -> dict:
    """Figma px → size_hint/pos_hint relative to a parent of figma size pw×ph."""
    return {
        "size_hint": (fw / pw, fh / ph),
        "pos_hint": {"x": fx / pw, "y": (ph - fy - fh) / ph},
    }


def _scale() -> float:
    return min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)


def _lbl(text: str, font: str, size: int, color: tuple, *, bold: bool = False,
         ha: str = "left", va: str = "top", **kw) -> Label:
    l = Label(text=text, font_name=font, font_size=size, color=color, bold=bold,
              halign=ha, valign=va, **kw)
    l.bind(size=l.setter("text_size"))
    return l


def _parse_dt(iso: str):
    if not iso:
        return None
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_IST)
        return to_display_local(dt)
    except Exception:
        return None


def _density_dots(n_meet: int) -> int:
    """User spec: 0 → none, 1-2 → 1 (light), 3-4 → 2 (moderate), 5+ → 3 (busy)."""
    if n_meet <= 0:
        return 0
    if n_meet <= 2:
        return 1
    if n_meet <= 4:
        return 2
    return 3


# ── Flip page (the front tear-off sheet) ──────────────────────────────────────

class _FlipPage(FloatLayout):
    """Front calendar sheet that folds about the top binding when the date
    changes.  ``fold`` 1.0 = fully open, 0.0 = folded flat against the binding.

    All drawing — card body AND purple header strip — is done inside
    ``canvas.before`` so they share the same Scale transform and are guaranteed
    to be perfectly flush with no layout-timing gaps.
    """

    fold = NumericProperty(1.0)

    def __init__(self, radius: float, strip_frac: float, **kw):
        super().__init__(**kw)
        self._r = radius
        self._sf = strip_frac   # fraction of card height occupied by header
        self._pivot_top = True

        with self.canvas.before:
            PushMatrix()
            self._scale = Scale(1, 1, 1)
            # Drop shadow
            Color(*_SHADOW)
            self._shadow = RoundedRectangle(radius=[radius])
            # Step 1 — draw the FULL card shape in purple (all 4 corners rounded).
            # This becomes the header strip that is visible at the top.
            Color(*_PURPLE)
            self._card_purple = RoundedRectangle(radius=[radius])
            # Step 2 — overlay the white body over the bottom portion.
            # Flat top corners (radius 0) so there is NO gap at the strip boundary;
            # rounded bottom corners (radius r) to match the card's bottom edge.
            # Kivy RoundedRectangle radius order: top-left, top-right,
            #   bottom-right, bottom-left.
            Color(*_CARD_WHITE)
            self._card_white = RoundedRectangle(radius=[0, 0, radius, radius])
        with self.canvas.after:
            PopMatrix()

        self.bind(pos=self._redraw, size=self._redraw, fold=self._apply_fold)

    def _redraw(self, *_):
        r = self._r
        x, y, w, h = self.x, self.y, self.width, self.height
        sh = h * self._sf   # pixel height of the header strip

        self._shadow.pos = (x, y - max(2.0, 4.0 * _scale()))
        self._shadow.size = (w, h)
        self._shadow.radius = [r]

        # Purple card — full card shape, all corners rounded.
        self._card_purple.pos = (x, y)
        self._card_purple.size = (w, h)
        self._card_purple.radius = [r]

        # White body — covers everything below the header strip.
        # Top edge sits flush at (y + h - sh), top corners are flat (radius=0).
        self._card_white.pos = (x, y)
        self._card_white.size = (w, h - sh)
        self._card_white.radius = [0, 0, r, r]

        self._apply_fold()

    def _apply_fold(self, *_):
        ox = self.center_x
        oy = self.top if self._pivot_top else self.y
        self._scale.origin = (ox, oy)
        self._scale.y = max(0.0001, float(self.fold))


# ── CalendarScreen ────────────────────────────────────────────────────────────

class CalendarScreen(BaseScreen):
    """Calendar view — Figma 3052:129 light-theme flip-calendar layout."""

    # Front-sheet (Frame 24) geometry, screen-absolute Figma px
    _CARD_X, _CARD_Y, _CARD_W, _CARD_H = 44.0, 276.0, 284.0, 302.0
    _CARD_R = 15.0
    _STRIP_H = 54.0

    # Meeting tile geometry (Figma px)
    _TILE_W, _TILE_H = 768.0, 193.0
    _TILE_GAP = 19.0          # 303 - (91 + 193) → ~19 px vertical gap

    def __init__(self, **kw):
        super().__init__(**kw)
        self._sel_date: date = display_now().date()
        self._displayed_date: date | None = None
        self._target_date: date | None = None
        self._view_week_mon: date | None = None
        self._week_data: dict = {}

        self._root_layout: FloatLayout | None = None
        self._page: _FlipPage | None = None
        self._wed_lbl: Label | None = None
        self._num_lbl: Label | None = None
        self._month_lbl: Label | None = None
        self._dots_box: Widget | None = None

        self._scroll_content: BoxLayout | None = None
        self._refresh_event = None
        self._subscribed_key: str | None = None

        # Listening pill
        self._pill: FloatLayout | None = None   # whole pill (hidden when idle)
        self._pill_lbl: Label | None = None
        self._pill_dot = None          # purple Color instruction
        self._pill_wave: Image | None = None
        self._voice_poll_event = None
        self._last_voice_state = None

        self._page_tx = None                 # transform-only page translate

        self._build_ui()

        # Interactive adjacent-page reveals (iOS Home-Screen feel):
        #   left-to-right  → Home   (Home sits to the left)
        #   right-to-left  → Tasks  (Tasks sits to the right)
        self._home_pager = PageSwipeController(
            self,
            "home",
            direction=1,
            prepare_dest=self._prepare_adjacent,
            commit=lambda: self.app.goto_screen("home", transition="none"),
            can_start=self._can_page,
        )
        self._tasks_pager = PageSwipeController(
            self,
            "tasks",
            direction=-1,
            prepare_dest=self._prepare_adjacent,
            commit=self._commit_to_tasks,
            can_start=self._can_page,
        )

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))
        self._root_layout = root

        # Shared light paper-swirl background — identical to the Start-Recording /
        # Processing / Summary screens — so the Home page-swipe chain feels like
        # one continuous surface.
        attach_swirl_bg(root, BG_TOP, BG_BOT)

        self._build_status_area(root)
        self._build_flip_calendar(root)
        self._build_scroll_area(root)

        self.add_widget(root)
        self._render_page(self._sel_date)

    # ── Status area (Listening pill + device status bar) ───────────────────────

    def _build_status_area(self, root: FloatLayout) -> None:
        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) for consistent size/position/font.
        # self._pill / self._pill_lbl / self._pill_dot / self._pill_wave stay
        # None here on purpose; existing `if self._pill is not None:` etc.
        # guards below no-op safely.
        pass

        # Shared device status bar (wifi + battery), right-aligned top-right
        try:
            from components.device_status_bar import DeviceStatusBar
            sb = DeviceStatusBar(debug_location="CalendarScreen",
                                 **_ph(1110, 26, 140, 30))
            root.add_widget(sb)
        except Exception:
            logger.debug("CalendarScreen: device status bar unavailable", exc_info=True)

    # ── Flip calendar (left) ───────────────────────────────────────────────────

    def _build_flip_calendar(self, root: FloatLayout) -> None:
        r = self._CARD_R * _scale()

        # Back stacked sheets (Frame 22 @257 with stroke, Frame 23 @264)
        back2 = Widget(**_ph(self._CARD_X, 257.0, self._CARD_W, self._CARD_H))
        with back2.canvas:
            Color(*_CARD_WHITE)
            _b2 = RoundedRectangle(pos=back2.pos, size=back2.size, radius=[r])
            Color(*_CARD_STROKE)
            _b2l = Line(rounded_rectangle=(back2.x, back2.y, back2.width,
                                           back2.height, r), width=1.0)
        back2.bind(
            pos=lambda w, v: (setattr(_b2, "pos", v),
                              setattr(_b2l, "rounded_rectangle",
                                      (w.x, w.y, w.width, w.height, r))),
            size=lambda w, v: (setattr(_b2, "size", v),
                               setattr(_b2l, "rounded_rectangle",
                                       (w.x, w.y, w.width, w.height, r))),
        )
        root.add_widget(back2)

        back1 = Widget(**_ph(self._CARD_X, 264.0, self._CARD_W, self._CARD_H))
        with back1.canvas:
            Color(*_SHADOW)
            _b1s = RoundedRectangle(radius=[r])
            Color(*_CARD_WHITE)
            _b1 = RoundedRectangle(pos=back1.pos, size=back1.size, radius=[r])

        def _sync_b1(w, *_):
            off = max(2.0, 4.0 * _scale())
            _b1s.pos = (w.x, w.y - off)
            _b1s.size = w.size
            _b1.pos = w.pos
            _b1.size = w.size
        back1.bind(pos=_sync_b1, size=_sync_b1)
        root.add_widget(back1)

        # Front sheet — the flip page
        page = _FlipPage(radius=r, strip_frac=self._STRIP_H / self._CARD_H,
                         **_ph(self._CARD_X, self._CARD_Y, self._CARD_W, self._CARD_H))
        self._page = page

        pw, ph = self._CARD_W, self._CARD_H
        # WED — strip centre
        self._wed_lbl = _lbl("WED", _FB, _ff(36), _HEADER_TXT, bold=True,
                             ha="center", va="middle",
                             size_hint=(1.0, 43.0 / ph),
                             pos_hint={"x": 0.0, "y": (ph - 5 - 43) / ph})
        page.add_widget(self._wed_lbl)
        # Big number
        self._num_lbl = _lbl("02", _FB, _ff(150), _NUM_GREY, bold=True,
                             ha="center", va="middle",
                             size_hint=(1.0, 179.0 / ph),
                             pos_hint={"x": 0.0, "y": (ph - 54 - 179) / ph})
        page.add_widget(self._num_lbl)
        # Month
        self._month_lbl = _lbl("JUNE", _FB, _ff(32), _NUM_GREY, bold=True,
                               ha="center", va="middle",
                               size_hint=(1.0, 38.0 / ph),
                               pos_hint={"x": 0.0, "y": (ph - 211 - 38) / ph})
        page.add_widget(self._month_lbl)
        # Density dots row (rebuilt per date) — rel (112,264) 61×15
        self._dots_box = Widget(size_hint=(1.0, 18.0 / ph),
                                pos_hint={"x": 0.0, "y": (ph - 264 - 15) / ph})
        page.add_widget(self._dots_box)

        root.add_widget(page)

        # Binder rings — drawn on top, fixed (do not flip)
        bl = _asset("binder_left.png")
        if bl:
            root.add_widget(Image(source=bl, fit_mode="contain",
                                  **_ph(74, 240, 51, 41)))
        br = _asset("binder_right.png")
        if br:
            root.add_widget(Image(source=br, fit_mode="contain",
                                  **_ph(247, 240, 51, 41)))

    def _render_dots(self, n_dots: int) -> None:
        box = self._dots_box
        if box is None:
            return
        box.canvas.clear()
        if n_dots <= 0:
            return
        sz = 15.0 * _scale()
        gap = 8.0 * _scale()
        total_w = n_dots * sz + (n_dots - 1) * gap
        cx = box.center_x
        cy = box.center_y
        start_x = cx - total_w / 2
        with box.canvas:
            Color(*_PURPLE)
            for i in range(n_dots):
                Ellipse(pos=(start_x + i * (sz + gap), cy - sz / 2), size=(sz, sz))

    def _render_page(self, d: date) -> None:
        """Update the front-sheet text + density dots for date *d* (no animation)."""
        n_meet = len(self._meetings_for(d))
        if self._wed_lbl:
            self._wed_lbl.text = _DAY_ABBR[d.weekday()]
        if self._num_lbl:
            self._num_lbl.text = f"{d.day:02d}"
        if self._month_lbl:
            self._month_lbl.text = _MONTHS_FULL[d.month - 1]
        # dots depend on the box being laid out; schedule after layout settles
        Clock.schedule_once(lambda _dt: self._render_dots(_density_dots(n_meet)), 0)
        self._displayed_date = d

    def _flip_to(self, d: date) -> None:
        """Animate the front sheet flipping to date *d* (tear-off calendar)."""
        page = self._page
        if page is None:
            self._render_page(d)
            return
        later = self._displayed_date is None or d >= self._displayed_date
        page._pivot_top = later
        page._apply_fold()
        Animation.cancel_all(page, "fold")

        def _swap(*_):
            self._render_page(d)
            Animation(fold=1.0, duration=0.24, t="out_cubic").start(page)

        out = Animation(fold=0.0001, duration=0.20, t="in_cubic")
        out.bind(on_complete=_swap)
        out.start(page)

    # ── Scrollable meeting tiles (right) ───────────────────────────────────────

    def _build_scroll_area(self, root: FloatLayout) -> None:
        scroll = ScrollView(
            do_scroll_x=False,
            bar_width=4,
            bar_color=(*_PURPLE[:3], 0.45),
            bar_inactive_color=(*_PURPLE[:3], 0.15),
            **_ph(378, 78, 786, 716),
        )
        gap_px = max(6, round(self._TILE_GAP * _scale()))
        content = BoxLayout(orientation="vertical", size_hint_y=None,
                            spacing=gap_px, padding=[0, round(13 * _scale()),
                                                     0, round(24 * _scale())])
        content.bind(minimum_height=content.setter("height"))
        self._scroll_content = content
        scroll.add_widget(content)
        root.add_widget(scroll)

    def _meetings_for(self, d: date) -> list:
        if not self._week_data:
            return []
        meetings = self._week_data.get(d.isoformat(), {}).get("meetings", [])
        return sorted(meetings, key=lambda m: m.get("start", ""))

    def _rebuild_tiles(self, d: date) -> None:
        content = self._scroll_content
        if content is None:
            return
        content.clear_widgets()
        meetings = self._meetings_for(d)

        if not meetings:
            empty = _lbl("No meetings scheduled for this day", _SB, _ff(30),
                         _TILE_TEXT, ha="center", va="middle",
                         size_hint=(1, None), height=round(193 * _scale()))
            content.add_widget(empty)
            return

        tile_h = max(120, round(self._TILE_H * _scale()))
        for m in meetings:
            content.add_widget(self._make_tile(m, tile_h))

    def _make_tile(self, m: dict, tile_h: int) -> Widget:
        TW, TH = self._TILE_W, self._TILE_H
        r = 17.0 * _scale()
        tile = FloatLayout(size_hint=(1, None), height=tile_h)

        with tile.canvas.before:
            Color(*_SHADOW)
            sh = RoundedRectangle(radius=[r])
            Color(*_CARD_WHITE)
            bg = RoundedRectangle(radius=[r])
            # top accent bar (#BFA8F8) — rounded top corners, squared bottom
            Color(*_ACCENT_BAR)
            bar = RoundedRectangle(radius=[r])
            bar_sq = Rectangle()

        def _sync(w, *_):
            off = max(2.0, 4.0 * _scale())
            sh.pos = (w.x, w.y - off)
            sh.size = w.size
            bg.pos = w.pos
            bg.size = w.size
            bh = 13.0 * _scale()
            bar.pos = (w.x, w.top - bh)
            bar.size = (w.width, bh)
            bar.radius = [r]
            bar_sq.pos = (w.x, w.top - bh)
            bar_sq.size = (w.width, bh * 0.6)
        tile.bind(pos=_sync, size=_sync)

        # Time — 26,27  SemiBold 30.25  #484F5F
        start_dt = _parse_dt(m.get("start", ""))
        time_str = (start_dt.strftime("%I:%M %p").lstrip("0")
                    if start_dt is not None else "--:--")
        tile.add_widget(_lbl(time_str, _SB, _ff(30.25), _TILE_TEXT, va="middle",
                             size_hint=(220 / TW, 36 / TH),
                             pos_hint={"x": 26 / TW, "y": (TH - 27 - 36) / TH}))

        # Meeting name — 26,85  SemiBold 36  #6D48CB
        tile.add_widget(_lbl(m.get("title", "-"), _SB, _ff(36), _TILE_NAME,
                             va="middle",
                             size_hint=(560 / TW, 43 / TH),
                             pos_hint={"x": 26 / TW, "y": (TH - 85 - 43) / TH}))

        # Divider lines — 17,77 and 17,137  width 733  #9E9E9E
        for ly in (77.0, 137.0):
            dv = Widget(size_hint=(733 / TW, 2 / TH),
                        pos_hint={"x": 17 / TW, "y": (TH - ly - 2) / TH})
            with dv.canvas:
                Color(*_DIVIDER)
                _ln = Rectangle(pos=dv.pos, size=dv.size)
            dv.bind(pos=lambda w, v, _l=_ln: setattr(_l, "pos", v),
                    size=lambda w, v, _l=_ln: setattr(_l, "size", v))
            tile.add_widget(dv)

        # Attendee count (only if data has it) — "2" @447,145 + people icon @474,148
        n_attend = self._attendee_count(m)
        if n_attend:
            tile.add_widget(_lbl(str(n_attend), _SB, _ff(36), _TILE_TEXT,
                                 ha="right", va="middle",
                                 size_hint=(28 / TW, 43 / TH),
                                 pos_hint={"x": 440 / TW, "y": (TH - 145 - 43) / TH}))
            ppl = _asset("icon_people_new.png")
            if ppl:
                tile.add_widget(Image(source=ppl, fit_mode="contain",
                                      size_hint=(38 / TW, 38 / TH),
                                      pos_hint={"x": 474 / TW,
                                                "y": (TH - 148 - 38) / TH}))

        # Duration — stopwatch icon @570,148 + "15Min" @611,145
        dur_min = (m.get("duration") or 0) // 60
        if not dur_min:
            s = _parse_dt(m.get("start", ""))
            e = _parse_dt(m.get("end", ""))
            if s and e:
                dur_min = int((e - s).total_seconds() / 60)
        if dur_min:
            sw = _asset("icon_stopwatch.png")
            if sw:
                tile.add_widget(Image(source=sw, fit_mode="contain",
                                      size_hint=(38 / TW, 38 / TH),
                                      pos_hint={"x": 570 / TW,
                                                "y": (TH - 148 - 38) / TH}))
            tile.add_widget(_lbl(f"{dur_min}Min", _SB, _ff(36), _TILE_TEXT,
                                 va="middle",
                                 size_hint=(120 / TW, 43 / TH),
                                 pos_hint={"x": 611 / TW,
                                           "y": (TH - 145 - 43) / TH}))
        return tile

    @staticmethod
    def _attendee_count(m: dict) -> int:
        att = m.get("attendees")
        if isinstance(att, list):
            return len(att)
        for key in ("attendee_count", "attendees_count", "num_attendees"):
            v = m.get(key)
            if isinstance(v, int) and v > 0:
                return v
        return 0

    # ── Day view orchestration ─────────────────────────────────────────────────

    def _update_day_view(self, d: date, *, animate: bool = False) -> None:
        if animate:
            self._flip_to(d)
        else:
            # Force the page fully open (guards against a previously interrupted
            # flip leaving fold < 1, which would squish/shift the header strip).
            if self._page is not None:
                Animation.cancel_all(self._page, "fold")
                self._page.fold = 1.0
            self._render_page(d)
        self._rebuild_tiles(d)

    # ── Voice-state pill polling ───────────────────────────────────────────────

    def _poll_voice_state(self, _dt) -> None:
        state = getattr(self.app, "_voice_runtime_state", "idle") or "idle"
        if state == self._last_voice_state:
            return
        self._last_voice_state = state
        active = state in ("listening", "thinking", "speaking")
        label = {"listening": "Listening", "thinking": "Thinking",
                 "speaking": "Speaking"}.get(state, "Listening")
        if self._pill is not None:
            Animation.cancel_all(self._pill, "opacity")
            Animation(opacity=1.0 if active else 0.0,
                      duration=0.22, t="out_cubic").start(self._pill)
        if self._pill_lbl:
            self._pill_lbl.text = label
        if self._pill_dot is not None:
            self._pill_dot.rgba = _PURPLE if active else (*_PURPLE[:3], 0.45)
        if self._pill_wave is not None:
            Animation.cancel_all(self._pill_wave, "opacity")
            if active:
                pulse = (Animation(opacity=1.0, duration=0.5, t="in_out_sine")
                         + Animation(opacity=0.5, duration=0.5, t="in_out_sine"))
                pulse.repeat = True
                pulse.start(self._pill_wave)
            else:
                self._pill_wave.opacity = 0.45

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def set_target_date(self, d: date) -> None:
        """Called by main.py before on_enter to jump to a specific date."""
        self._target_date = d

    # ── Interactive page swipe (Calendar ⇄ Home / Tasks) ──────────────────────

    def _pagers(self):
        return (
            getattr(self, "_home_pager", None),
            getattr(self, "_tasks_pager", None),
        )

    def _can_page(self) -> bool:
        return not any_modal_open()

    def _prepare_adjacent(self, dest) -> None:
        try:
            dest.prime_preview()
        except Exception:
            logger.exception("calendar: failed to prime adjacent page preview")

    def _commit_to_tasks(self) -> None:
        """Settle onto Tasks, flagging it as a swipe entry (no back button)."""
        try:
            self.app.screen_manager.get_screen("tasks").entered_via_swipe = True
        except Exception:
            logger.exception("calendar: failed to flag tasks swipe entry")
        self.app.goto_screen("tasks", transition="none")

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
        """Refresh content so the swipe preview shows live data."""
        self.set_page_offset(0.0)
        try:
            today = display_now().date()
            target = self._target_date if self._target_date is not None else today
            self._sel_date = target
            self._view_week_mon = target - timedelta(days=target.weekday())
            self._update_day_view(target, animate=False)
            Clock.schedule_once(lambda _dt: self._load_week(), 0)
        except Exception:
            logger.debug("calendar: prime_preview failed", exc_info=True)

    def on_touch_down(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_down(touch):
                return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_move(touch):
                return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        for pager in self._pagers():
            if pager is not None and pager.on_touch_up(touch):
                return True
        return super().on_touch_up(touch)

    def on_enter(self) -> None:
        for pager in self._pagers():
            if pager is not None:
                pager.cancel()
        self.set_page_offset(0.0)

        today = display_now().date()
        target = self._target_date if self._target_date is not None else today

        # Animate the flip only when the screen is already showing and the date
        # actually changed (i.e. the user asked for a different date).
        already_visible = (self.manager is not None
                           and self.manager.current == self.name
                           and self._displayed_date is not None)
        animate = already_visible and target != self._displayed_date

        self._sel_date = target
        self._view_week_mon = target - timedelta(days=target.weekday())

        cache_key = f"calendar_week:{self._view_week_mon.isoformat()}"
        cached_week = self.app.ui_cache_get(cache_key)
        if isinstance(cached_week, dict) and "days" in cached_week:
            self._week_data = cached_week.get("days") or {}
        elif self._subscribed_key != cache_key:
            # Switched to a week we have no cache for yet — clear stale data so
            # we don't show another week's meetings under the new date.
            self._week_data = {}

        # Subscribe only to the week currently being viewed. When the user
        # changes the date/week while staying on this screen, drop the previous
        # week's subscription — otherwise a background refresh of the old week
        # fires _on_cached_week and overwrites _week_data, wiping the meetings.
        if self._subscribed_key and self._subscribed_key != cache_key:
            self.app.ui_cache_unsubscribe(self._subscribed_key, self._on_cached_week)
            self._subscribed_key = None
        if self._subscribed_key != cache_key:
            self.app.ui_cache_subscribe(cache_key, self._on_cached_week)
            self._subscribed_key = cache_key

        self._update_day_view(target, animate=animate)

        Clock.schedule_once(lambda _dt: self._load_week(), 0)

        if self._refresh_event:
            self._refresh_event.cancel()
        self._refresh_event = Clock.schedule_interval(self._tick, 60)

        if self._voice_poll_event:
            self._voice_poll_event.cancel()
        self._last_voice_state = None
        self._voice_poll_event = Clock.schedule_interval(self._poll_voice_state, 0.25)

    def on_leave(self) -> None:
        for pager in self._pagers():
            if pager is not None:
                pager.cancel()
        self.set_page_offset(0.0)
        self._target_date = None
        if self._subscribed_key:
            self.app.ui_cache_unsubscribe(self._subscribed_key, self._on_cached_week)
            self._subscribed_key = None
        if self._refresh_event:
            self._refresh_event.cancel()
            self._refresh_event = None
        if self._voice_poll_event:
            self._voice_poll_event.cancel()
            self._voice_poll_event = None

    def _tick(self, _dt) -> None:
        self._rebuild_tiles(self._sel_date)

    # ── Data loading ───────────────────────────────────────────────────────────

    def _load_week(self) -> None:
        async def _fetch():
            vm = self._view_week_mon
            try:
                if vm is None:
                    return
                cache_key = f"calendar_week:{vm.isoformat()}"
                cached = self.app.ui_cache_get(cache_key)
                if isinstance(cached, dict) and "days" in cached:
                    def _paint_cached(_dt):
                        self._week_data = cached.get("days") or {}
                        self._render_dots(_density_dots(len(self._meetings_for(self._sel_date))))
                        self._rebuild_tiles(self._sel_date)
                    Clock.schedule_once(_paint_cached, 0)
                if self.app.ui_cache_is_fresh(cache_key):
                    return
                if not self.app.ui_cache_mark_inflight(cache_key):
                    return
                end_d = vm + timedelta(days=6)
                data = await self.backend.get_calendar_week(
                    vm.isoformat(), end_d.isoformat())

                # Only apply a valid response; never overwrite displayed
                # meetings with an empty/failed fetch.
                if not (isinstance(data, dict) and "days" in data):
                    return

                def _apply(_dt):
                    self._week_data = data.get("days") or {}
                    self.app.ui_cache_set(cache_key, dict(data))
                    self._render_dots(_density_dots(len(self._meetings_for(self._sel_date))))
                    self._rebuild_tiles(self._sel_date)
                Clock.schedule_once(_apply, 0)
            except Exception as exc:
                logger.debug("CalendarScreen: get_calendar_week failed: %s", exc)
            finally:
                if vm is not None:
                    self.app.ui_cache_clear_inflight(f"calendar_week:{vm.isoformat()}")
        run_async(_fetch())

    def _on_cached_week(self, payload: dict) -> None:
        # The centralized sync loop stores an empty {} (no "days" key) into this
        # cache whenever get_calendar_week errors/times out. Ignore such failed
        # refreshes so we never wipe already-displayed meetings.
        if not (isinstance(payload, dict) and "days" in payload):
            return

        def _apply(_dt):
            if self.manager and self.manager.current != self.name:
                return
            self._week_data = payload.get("days") or {}
            self._render_dots(_density_dots(len(self._meetings_for(self._sel_date))))
            self._rebuild_tiles(self._sel_date)
        Clock.schedule_once(_apply, 0)
