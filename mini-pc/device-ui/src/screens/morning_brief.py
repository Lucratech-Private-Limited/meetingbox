"""Morning Brief screen — pixel-perfect from Figma 3056:244 / 3056:315 / 3056:383.

The page is a light "frosted glass" layout (full-bleed background photo + 45 %
white overlay) carrying a 3-card horizontal carousel:

  • Section 1  Today's schedule  — time · title · duration rows, NEXT highlight
  • Section 2  Task Overview      — due-label · title rows
  • Section 3  Emails             — sender · subject · time rows (unread)

Each card has a purple header (icon + title + pending-count badge), a vertical
scroll list with a visible scrollbar track + thumb, and row dividers.

Only one card is centred at a time; the next card peeks at the right edge.
Swipe left / right (or voice — wired in a later pass) moves between sections,
looping around.

All coordinates are Figma absolute px on the 1260 × 800 design frame, converted
with the _ph()/_rel() helpers to Kivy size_hint + pos_hint fractions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.properties import NumericProperty
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from api_client import (
    _GMAIL_RECENT_DAYS,
    summarize_gmail_feed_for_home,
)
from components.live_wifi_icon import LiveWifiIcon
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now, to_display_local
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ── Design frame ──────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

# Card geometry (identical for all three sections)
CARD_X, CARD_Y = 35.0, 230.0
CARD_W, CARD_H = 1002.0, 474.53
CARD_PITCH = 1032.0          # centre-to-centre between carousel cards (1067 − 35)
CARD_R = 38.44               # corner radius
HDR_H = 86.97                # purple header height
CONTENT_H = CARD_H - HDR_H   # scroll viewport design height (387.56)
ROW_H = 96.89                # uniform list-row height (≈ CONTENT_H / 4)

# Scaling: design px → device px (1.0 on a true 1260×800 panel → pixel-perfect)
_SCALE = min(DISPLAY_WIDTH / FW, DISPLAY_HEIGHT / FH)

# ── Asset paths ───────────────────────────────────────────────────────────────
_V2 = ASSETS_DIR / "brief" / "v2"


def _asset(name: str) -> str:
    p = _V2 / name
    return str(p) if p.is_file() else ""


# ── Colours ───────────────────────────────────────────────────────────────────
_BLACK   = (0.0, 0.0, 0.0, 1.0)                       # #000000
_PURPLE  = (107/255,  71/255, 204/255, 1.0)           # #6B47CC  greeting
_SUBTLE  = (74/255,   82/255,  95/255, 1.0)           # #4A525F  body / muted
_WHITE   = (1.0, 1.0, 1.0, 1.0)                       # #FFFFFF
_HDR     = (109/255,  72/255, 204/255, 1.0)           # #6D48CC  card header
_CARD    = (253/255, 253/255, 253/255, 1.0)           # #FDFDFD  card body
_BADGE   = (90/255,   63/255, 165/255, 1.0)           # #5A3FA5  count badge
_DIV     = (158/255, 158/255, 158/255, 1.0)           # #9E9E9E  row divider
_TRACK   = (218/255, 218/255, 218/255, 1.0)           # #DADADA  scrollbar track
_THUMB   = (251/255, 251/255, 251/255, 1.0)           # #FBFBFB  scrollbar thumb
_PILL_BG = (250/255, 250/255, 250/255, 1.0)           # #FAFAFA  status pill
_PILL_TX = (58/255,   59/255,  61/255, 1.0)           # #3A3B3D  pill text

# Font families registered in main.py
_F_BOLD = "42dot-Sans"   # Regular + Bold (use bold=True for 700)
_F_SB   = "42dot-SB"     # SemiBold (600)
_F_MED  = "42dot-Med"    # Medium (500)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
    """Figma absolute px → Kivy size_hint + pos_hint on the 1260 × 800 root."""
    return {
        "size_hint": (fw / FW, fh / FH),
        "pos_hint":  {"x": fx / FW, "y": (FH - fy - fh) / FH},
    }


def _rel(fx: float, fy: float, fw: float, fh: float,
         cw: float = CARD_W, ch: float = CARD_H) -> dict:
    """Card-relative Figma px → size_hint + pos_hint inside a card."""
    return {
        "size_hint": (fw / cw, fh / ch),
        "pos_hint":  {"x": fx / cw, "y": (ch - fy - fh) / ch},
    }


def _ff(fs: float) -> float:
    """Figma font px → device font px (no boost → matches the design)."""
    return max(6.0, fs * _SCALE)


def _sz(d: float) -> float:
    """Figma px → device px for absolute (non-hint) sizes."""
    return max(1.0, d * _SCALE)


def _lbl(text: str, font: str, size: float, color: tuple,
         ha: str = "left", va: str = "middle", bold: bool = False, **kw) -> Label:
    lw = Label(text=text, font_name=font, font_size=size, color=color,
               halign=ha, valign=va, bold=bold, shorten=True,
               shorten_from="right", **kw)
    lw.bind(size=lw.setter("text_size"))
    return lw


def _img(src: str, cw: float, ch: float,
         fx: float, fy: float, iw: float, ih: float) -> Image:
    return Image(source=src, fit_mode="contain",
                 size_hint=(iw / cw, ih / ch),
                 pos_hint={"x": fx / cw, "y": (ch - fy - ih) / ch})


# ── List row ──────────────────────────────────────────────────────────────────

class _Row(FloatLayout):
    """One list row: a bottom divider plus an optional purple "NEXT" highlight."""

    def __init__(self, *, show_divider: bool = True, show_next: bool = False, **kw):
        super().__init__(**kw)
        self.size_hint_y = None
        self.height = _sz(ROW_H)
        self._show_divider = show_divider
        self._show_next = show_next
        self._tab = None
        self._brd = None
        self._div = None

        # NEXT highlight tab fill — behind row content so the label sits on top.
        with self.canvas.before:
            if show_next:
                Color(*_HDR)
                self._tab = RoundedRectangle(radius=[_sz(8)])
        with self.canvas.after:
            if show_divider:
                Color(*_DIV)
                self._div = Rectangle()
            if show_next:
                Color(*_HDR)
                self._brd = Line(width=max(1.5, _sz(2.0)))
        if show_next:
            self._next_lbl = _lbl("NEXT", _F_MED, _ff(20.0), _WHITE,
                                  ha="center", va="middle",
                                  size_hint=(150 / CARD_W, 24 / ROW_H),
                                  pos_hint={"x": 6 / CARD_W,
                                            "top": 1 - (7 / ROW_H)})
            self.add_widget(self._next_lbl)
        self.bind(pos=self._sync, size=self._sync)
        Clock.schedule_once(self._sync, 0)

    def _sync(self, *_):
        w, h, x, y = self.width, self.height, self.x, self.y
        if self._div is not None:
            # Bottom divider spans the content width (stops before the scrollbar).
            dw = w * (987.51 / CARD_W)
            self._div.pos = (x, y)
            self._div.size = (dw, max(1.0, _sz(1.3)))
        if self._show_next and self._brd is not None:
            inset = max(1.5, _sz(2.0))
            bw = w * (983 / CARD_W)
            self._brd.rounded_rectangle = (
                x + inset, y + inset, bw, h - inset * 2, _sz(12))
            tab_w = w * (150 / CARD_W)
            tab_h = _sz(24)
            self._tab.pos = (x + _sz(3), y + h - tab_h - _sz(7))
            self._tab.size = (tab_w, tab_h)


# ── Scrollbar track (static) ────────────────────────────────────────────────--

class _Track(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            Color(*_TRACK)
            self._r = RoundedRectangle(radius=[_sz(7)])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._r.pos = self.pos
        self._r.size = self.size


# ── Card ──────────────────────────────────────────────────────────────────────

class _Card(FloatLayout):
    """White rounded card with a purple top header (rounded top, square bottom)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            # soft shadow
            Color(118/255, 129/255, 127/255, 0.18)
            self._sh = RoundedRectangle(radius=[_sz(CARD_R)])
            # card body
            Color(*_CARD)
            self._body = RoundedRectangle(radius=[_sz(CARD_R)])
            # purple header (rounded), then squared bottom strip
            Color(*_HDR)
            self._hdr = RoundedRectangle(radius=[_sz(CARD_R)])
            self._hdr_sq = Rectangle()
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        self._sh.pos = (x, y - _sz(3))
        self._sh.size = (w, h)
        self._body.pos = (x, y)
        self._body.size = (w, h)
        hh = h * (HDR_H / CARD_H)
        self._hdr.pos = (x, y + h - hh)
        self._hdr.size = (w, hh)
        # square off the rounded bottom corners of the header
        self._hdr_sq.pos = (x, y + h - hh)
        self._hdr_sq.size = (w, min(hh, _sz(CARD_R)))


# ── Screen ────────────────────────────────────────────────────────────────────

class MorningBriefScreen(BaseScreen):
    """Morning Brief — 3-section frosted-glass carousel."""

    slide = NumericProperty(0.0)   # fractional carousel position (0 / 1 / 2)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hdr_greeting = None
        self._hdr_subtitle = None
        self._cards: list[_Card] = []
        self._index = 0
        self._pending_index: int | None = None
        self._sections: list[dict] = []   # per-card ctx (content box, count, scroll)
        self._loading: bool = False        # True while skeletons are shown
        self._render_event = None          # pending debounced-render Clock event
        # Factual narration text for the voice agent, kept in lock-step with the
        # rendered cards so the spoken briefing always matches the on-screen data.
        self._voice_facts: dict[str, str] = {
            "schedule": "", "tasks": "", "emails": "", "ready": "",
        }
        self._build_ui()
        self.bind(slide=self._on_slide)

    def voice_brief_facts(self) -> dict:
        """Return the per-section facts currently rendered on the cards.

        Consumed by the Realtime voice session so the spoken briefing reads the
        exact same schedule / tasks / emails the user sees on screen.
        """
        return dict(self._voice_facts)

    # ── Top-level build ──────────────────────────────────────────────────────--

    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))

        # Background photo + 45 % white overlay (frosted look), both behind
        # every child so the cards / text render on top.
        bg_tex = None
        bg_src = _asset("bg.png")
        if bg_src:
            try:
                from kivy.core.image import Image as CoreImage
                bg_tex = CoreImage(bg_src).texture
            except Exception:
                bg_tex = None
        with root.canvas.before:
            Color(0.96, 0.96, 0.98, 1)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size, texture=bg_tex)
            Color(1, 1, 1, 0.45)
            self._overlay = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, v: self._sync_bg(w),
            size=lambda w, v: self._sync_bg(w),
        )

        self._build_header(root)
        self._build_status(root)

        # Three carousel cards.
        for kind, title, icon in (
            ("schedule", "Today's schedule", "icon_calendar.png"),
            ("tasks",    "Task Overview",    "icon_task.png"),
            ("emails",   "Emails",           "icon_mail.png"),
        ):
            card, ctx = self._build_card(kind, title, icon)
            self._cards.append(card)
            self._sections.append(ctx)
            root.add_widget(card)

        self._reposition()
        self.add_widget(root)

    def _sync_bg(self, w) -> None:
        self._bg_rect.pos = w.pos
        self._bg_rect.size = w.size
        self._overlay.pos = w.pos
        self._overlay.size = w.size

    # ── Header greeting (Figma 35,64 / 35,111 / 35,153) ────────────────────────

    def _build_header(self, root: FloatLayout) -> None:
        root.add_widget(_lbl(
            "Morning Brief", _F_BOLD, _ff(40.0), _BLACK, bold=True,
            **_ph(35, 64, 320, 48)))

        self._hdr_greeting = _lbl(
            "Good morning", _F_MED, _ff(36.0), _PURPLE,
            **_ph(35, 111, 520, 43))
        root.add_widget(self._hdr_greeting)

        self._hdr_subtitle = _lbl(
            "Here's your overview for today", _F_MED, _ff(24.0), _SUBTLE,
            **_ph(35, 153, 640, 29))
        root.add_widget(self._hdr_subtitle)

    # ── Top-right status cluster (wifi · battery · listening pill) ─────────────-

    def _build_status(self, root: FloatLayout) -> None:
        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) for consistent size/position/font.
        # This screen previously drew its own static "Listening" pill here
        # (never tied to actual session state); removed to avoid a duplicate,
        # inconsistent pill. self._status_pill is intentionally left unset.
        # (WiFi + battery indicators removed — not relevant for the desktop app.)
        return

    # ── Card builder ───────────────────────────────────────────────────────────

    def _build_card(self, kind: str, title: str, icon_file: str):
        card = _Card(**_ph(CARD_X, CARD_Y, CARD_W, CARD_H))

        # Header icon (≈ 27,15 · 57×57)
        icon_src = _asset(icon_file)
        if icon_src:
            card.add_widget(_img(icon_src, CARD_W, CARD_H, 27, 15, 57, 57))

        # Header title (SemiBold 32 white)
        card.add_widget(_lbl(
            title, _F_SB, _ff(32.0), _WHITE, va="middle",
            **_rel(99, 24, 360, 38)))

        # Static scrollbar track (987.51,86.97 · 14.49×397.65)
        track = _Track(**_rel(987.51, HDR_H, 14.49, CARD_H - HDR_H))
        card.add_widget(track)

        # Scroll list (full card width below the header)
        sv = ScrollView(
            size_hint=(1, (CARD_H - HDR_H) / CARD_H),
            pos_hint={"x": 0, "y": 0},
            do_scroll_x=False, do_scroll_y=True,
            scroll_type=["bars", "content"],
            bar_width=_sz(10.08),
            bar_margin=_sz(2.2),
            bar_color=list(_THUMB),
            bar_inactive_color=list(_THUMB),
        )
        content = GridLayout(cols=1, size_hint_y=None, spacing=0)
        content.bind(minimum_height=content.setter("height"))
        sv.add_widget(content)
        card.add_widget(sv)

        # Count badge (top-right of header)
        badge, badge_lbl = self._make_badge()
        card.add_widget(badge)

        ctx = {
            "kind": kind,
            "scroll": sv,
            "content": content,
            "badge": badge,
            "badge_lbl": badge_lbl,
        }
        return card, ctx

    def _make_badge(self):
        """Pending-count pill: dark-purple rounded badge with a white number."""
        bw, bh = 46.0, 46.0
        # right edge ≈ 963 design, vertically centred in the header
        badge = Widget(**_rel(963 - bw, (HDR_H - bh) / 2, bw, bh))
        with badge.canvas:
            c = Color(*_BADGE)
            r = RoundedRectangle(pos=badge.pos, size=badge.size,
                                 radius=[_sz(bh / 2)])
        lbl = _lbl("0", _F_MED, _ff(28.0), _WHITE, ha="center", va="middle",
                   size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        badge.add_widget(lbl)

        def _sync(w, *_):
            r.pos = w.pos
            r.size = w.size
            lbl.pos = w.pos
            lbl.size = w.size
        badge.bind(pos=_sync, size=_sync)
        return badge, lbl

    # ── Carousel mechanics ───────────────────────────────────────────────────--

    def _reposition(self, *_):
        yfrac = (FH - CARD_Y - CARD_H) / FH
        for i, card in enumerate(self._cards):
            dx = CARD_X + (i - self.slide) * CARD_PITCH
            card.pos_hint = {"x": dx / FW, "y": yfrac}

    def _on_slide(self, *_):
        self._reposition()

    def _go_to(self, index: int) -> None:
        index %= len(self._cards)
        if index == self._index:
            return
        self._index = index
        Animation.cancel_all(self, "slide")
        Animation(slide=float(index), duration=0.28, t="out_quad").start(self)

    def next_section(self) -> None:
        self._go_to(self._index + 1)

    def prev_section(self) -> None:
        self._go_to(self._index - 1)

    # ── Section control (voice agent / external) ───────────────────────────────

    _SECTION_NAMES = ("schedule", "tasks", "emails")
    _SECTION_ALIASES = {
        "schedule": 0, "calendar": 0, "meetings": 0, "meeting": 0, "today": 0,
        "tasks": 1, "task": 1, "todo": 1, "todos": 1, "to-do": 1,
        "emails": 2, "email": 2, "inbox": 2, "mail": 2,
    }

    def _section_index(self, name: str) -> int | None:
        n = (name or "").strip().lower()
        if n in self._SECTION_ALIASES:
            return self._SECTION_ALIASES[n]
        if n in ("next", "forward", "right"):
            return (self._index + 1) % len(self._cards)
        if n in ("previous", "prev", "back", "left"):
            return (self._index - 1) % len(self._cards)
        return None

    def set_active_section(self, name: str) -> None:
        """Switch the carousel to a named section (used by the voice agent).

        Applies immediately when this screen is already current; otherwise the
        section is remembered and applied on the next ``on_enter``.
        """
        idx = self._section_index(name)
        if idx is None:
            return
        self._pending_index = idx
        if self.manager and self.manager.current == self.name:
            self._apply_pending_section()

    def current_section_name(self) -> str:
        try:
            return self._SECTION_NAMES[self._index]
        except IndexError:
            return "schedule"

    def _apply_pending_section(self) -> None:
        if self._pending_index is not None:
            target = self._pending_index
            self._pending_index = None
            self._go_to(target)

    # ── Touch swipe (horizontal = carousel, vertical = list scroll) ────────────-

    _SWIPE_DX = max(40.0, DISPLAY_WIDTH * 0.06)

    def _in_card_region(self, touch) -> bool:
        # Carousel band roughly between the header text and the screen bottom.
        return touch.y < self.height * ((FH - CARD_Y) / FH)

    def on_touch_down(self, touch):
        if self._in_card_region(touch):
            touch.ud["mb_sx"] = touch.x
            touch.ud["mb_sy"] = touch.y
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        handled = super().on_touch_up(touch)
        sx = touch.ud.get("mb_sx")
        if sx is not None:
            dx = touch.x - sx
            dy = touch.y - touch.ud.get("mb_sy", touch.y)
            if abs(dx) > self._SWIPE_DX and abs(dx) > abs(dy) * 1.2:
                if dx < 0:
                    self.next_section()
                else:
                    self.prev_section()
                return True
        return handled

    # ── Row population ─────────────────────────────────────────────────────────

    def _clear(self, ctx: dict) -> None:
        ctx["content"].clear_widgets()

    def _set_count(self, ctx: dict, n: int) -> None:
        ctx["badge_lbl"].text = "99+" if n > 99 else str(max(0, n))

    def _empty(self, ctx: dict, message: str) -> None:
        self._clear(ctx)
        row = _Row(show_divider=False)
        row.add_widget(_lbl(
            message, _F_MED, _ff(25.0), _SUBTLE, ha="center", va="middle",
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        ctx["content"].add_widget(row)

    def _add_schedule_row(self, ctx: dict, time_s: str, title_s: str,
                          dur_s: str, is_next: bool, last: bool = False) -> _Row:
        row = _Row(show_next=is_next, show_divider=not last)
        row.add_widget(_lbl(time_s, _F_SB, _ff(30.25), _BLACK,
                            **_rel(45.38, ROW_H / 2 - 18, 175, 36, CARD_W, ROW_H)))
        row.add_widget(_lbl(title_s, _F_SB, _ff(30.88), _SUBTLE,
                            **_rel(231.28, ROW_H / 2 - 18, 560, 36, CARD_W, ROW_H)))
        row.add_widget(_lbl(dur_s, _F_MED, _ff(25.21), _SUBTLE,
                            **_rel(830.59, ROW_H / 2 - 15, 120, 30, CARD_W, ROW_H)))
        ctx["content"].add_widget(row)
        return row

    def _add_task_row(self, ctx: dict, label_s: str, title_s: str,
                      last: bool = False) -> None:
        row = _Row(show_divider=not last)
        row.add_widget(_lbl(label_s, _F_SB, _ff(30.25), _BLACK,
                            **_rel(45.38, ROW_H / 2 - 18, 170, 36, CARD_W, ROW_H)))
        row.add_widget(_lbl(title_s, _F_SB, _ff(30.88), _SUBTLE,
                            **_rel(231.28, ROW_H / 2 - 18, 700, 36, CARD_W, ROW_H)))
        ctx["content"].add_widget(row)

    def _add_email_row(self, ctx: dict, sender_s: str, subject_s: str,
                       time_s: str, last: bool = False) -> None:
        row = _Row(show_divider=not last)
        row.add_widget(_lbl(sender_s, _F_SB, _ff(30.25), _BLACK,
                            **_rel(45.38, ROW_H / 2 - 18, 260, 36, CARD_W, ROW_H)))
        row.add_widget(_lbl(subject_s, _F_SB, _ff(30.88), _SUBTLE,
                            **_rel(327, ROW_H / 2 - 18, 430, 36, CARD_W, ROW_H)))
        row.add_widget(_lbl(time_s, _F_MED, _ff(30.25), _SUBTLE,
                            **_rel(791, ROW_H / 2 - 18, 160, 36, CARD_W, ROW_H)))
        ctx["content"].add_widget(row)

    # ── Skeleton loading rows ─────────────────────────────────────────────────

    def _add_skeleton_row(self, ctx: dict, last: bool = False) -> None:
        """One grey-bar placeholder row shown while real data is loading."""
        row = _Row(show_divider=not last)
        for fx, fw in ((45.38, 140), (231.28, 360)):
            bar = Widget(
                size_hint=(fw / CARD_W, 14 / ROW_H),
                pos_hint={"x": fx / CARD_W, "center_y": 0.5},
            )
            with bar.canvas:
                Color(0.84, 0.84, 0.86, 1.0)
                br = RoundedRectangle(radius=[_sz(4)])
            bar.bind(
                pos=lambda w, _v, _r=br: setattr(_r, "pos", w.pos),
                size=lambda w, _v, _r=br: setattr(_r, "size", w.size),
            )
            row.add_widget(bar)
        ctx["content"].add_widget(row)

    def _show_loading_state(self) -> None:
        """Fill every card with 3 skeleton rows and a '–' badge while fetching."""
        self._loading = True
        self._voice_facts["ready"] = ""
        for ctx in self._sections:
            self._clear(ctx)
            for i in range(3):
                self._add_skeleton_row(ctx, last=(i == 2))
            ctx["badge_lbl"].text = "–"

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @staticmethod
    def _first_name(display_name: str | None) -> str:
        if not (display_name or "").strip():
            return "there"
        part = display_name.strip().split()[0]
        return part if part else "there"

    @staticmethod
    def _fmt_ampm(dt: datetime) -> str:
        h24, m = dt.hour, dt.minute
        am = "AM" if h24 < 12 else "PM"
        h12 = h24 % 12 or 12
        return f"{h12}:{m:02d} {am}"

    def on_enter(self) -> None:
        if self._pending_index is None:
            self._index = 0
            self.slide = 0.0
            self._reposition()
        else:
            self._index = self._pending_index
            self.slide = float(self._pending_index)
            self._pending_index = None
            self._reposition()

        self.app.ui_cache_subscribe("morning_brief_context", self._on_cache_update)
        self.app.ui_cache_subscribe("morning_brief_gmail", self._on_cache_update)

        cached_ctx   = self.app.ui_cache_get("morning_brief_context")
        cached_gmail = self.app.ui_cache_get("morning_brief_gmail")
        has_data     = isinstance(cached_ctx, dict) or isinstance(cached_gmail, dict)

        if has_data:
            # Render cached data immediately — may be stale but prevents blank flash.
            self._apply_briefing_data(
                cached_ctx  if isinstance(cached_ctx,   dict) else {},
                cached_gmail if isinstance(cached_gmail, dict) else {},
            )
        else:
            # No cached data at all (e.g. first boot, or cache cleared after account
            # switch) — show skeleton rows so the user sees "loading" not blank cards.
            self._show_loading_state()

        if (
            not self.app.ui_cache_is_fresh("morning_brief_context")
            or not self.app.ui_cache_is_fresh("morning_brief_gmail")
        ):
            Clock.schedule_once(lambda _dt: self._load_briefing_backend(), 0)

    def on_leave(self) -> None:
        self.app.ui_cache_unsubscribe("morning_brief_context", self._on_cache_update)
        self.app.ui_cache_unsubscribe("morning_brief_gmail", self._on_cache_update)
        if self._render_event is not None:
            self._render_event.cancel()
            self._render_event = None

    def _on_cache_update(self, _payload) -> None:
        """Any cache key update → debounced render so both keys are consumed together."""
        if self.manager and self.manager.current != self.name:
            return
        self._schedule_render()

    def _schedule_render(self) -> None:
        """Cancel any in-flight render and schedule a new one 50 ms out."""
        if self._render_event is not None:
            self._render_event.cancel()
        self._render_event = Clock.schedule_once(self._do_render, 0.05)

    def _do_render(self, _dt) -> None:
        self._render_event = None
        if self.manager and self.manager.current != self.name:
            return
        ctx   = self.app.ui_cache_get("morning_brief_context") or {}
        gmail = self.app.ui_cache_get("morning_brief_gmail")    or {}
        self._apply_briefing_data(ctx, gmail)

    def _load_briefing_backend(self) -> None:
        async def _go():
            async def _briefing():
                return await self.backend.get_briefing_context(days_ahead=1)

            async def _gmail():
                gf = getattr(self.backend, "fetch_gmail_recent", None)
                if gf is None:
                    return {}
                return await gf(max_results=40, days=_GMAIL_RECENT_DAYS, q="")

            results = await asyncio.gather(_briefing(), _gmail(), return_exceptions=True)
            data  = results[0] if not isinstance(results[0], BaseException) else {}
            gfeed = results[1] if not isinstance(results[1], BaseException) else {}

            # Always write both keys atomically before notifying the UI, so the
            # debounced renderer sees a consistent snapshot of both when it fires.
            if isinstance(data, dict):
                self.app.ui_cache_set("morning_brief_context", dict(data))
            if isinstance(gfeed, dict):
                self.app.ui_cache_set("morning_brief_gmail", dict(gfeed))
            # The cache-set calls above trigger _on_cache_update → _schedule_render,
            # but schedule one explicit render too in case neither value changed.
            Clock.schedule_once(
                lambda _dt: self._do_render(None),
                0.06,
            )
        run_async(_go())

    # ── Data → rows ──────────────────────────────────────────────────────────--

    def _apply_briefing_data(self, data: dict, gfeed: dict | None = None) -> None:
        try:
            self._apply_header(data)
            today_s = (data.get("today") or "").strip() or display_now().date().isoformat()
            self._apply_schedule(data, today_s)
            self._apply_tasks(data, today_s)
            self._apply_emails(gfeed or {})
            self._voice_facts["ready"] = "1"
        except Exception:
            logger.debug("apply briefing data failed", exc_info=True)

    def _apply_header(self, data: dict) -> None:
        dn = data.get("user_display_name")
        greet = (data.get("greeting") or "Hello").strip()
        if self._hdr_greeting:
            self._hdr_greeting.text = f"{greet}, {self._first_name(dn)}"
        today_s = (data.get("today") or "").strip() or display_now().date().isoformat()
        try:
            td = date.fromisoformat(today_s)
            months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
            nice = f"{td.day} {months[td.month - 1]}"
        except ValueError:
            nice = today_s
        if self._hdr_subtitle:
            self._hdr_subtitle.text = f"Here's your overview for today, {nice}"

    def _apply_schedule(self, data: dict, today_s: str) -> None:
        from datetime import timezone as _tz
        ctx = self._sections[0]
        meetings = ((data.get("days") or {}).get(today_s) or {}).get("meetings") or []
        now = display_now()   # timezone-aware in configured display TZ

        parsed = []   # (sort_key_dt, time_txt, title_txt, dur_txt, is_pending, sdt_local)
        for ev in meetings:
            start_s = ev.get("start") or ev.get("start_time") or ""
            sdt_local = None
            try:
                raw = datetime.fromisoformat(start_s.replace("Z", "+00:00"))
                if raw.tzinfo is None:
                    raw = raw.replace(tzinfo=_tz.utc)
                sdt_local = to_display_local(raw)
            except Exception:
                sdt_local = None

            time_txt  = self._fmt_ampm(sdt_local) if sdt_local else "—"
            title_txt = (ev.get("title") or "Untitled")[:48]
            dur       = int(ev.get("duration") or 0)
            dur_txt   = f"{max(1, dur // 60)} min" if dur > 0 else "—"

            is_pending = False
            if sdt_local is not None:
                try:
                    is_pending = sdt_local >= now
                except Exception:
                    is_pending = False

            sort_key = sdt_local if sdt_local is not None else datetime.min.replace(tzinfo=_tz.utc)
            parsed.append((sort_key, time_txt, title_txt, dur_txt, is_pending))

        # Always render meetings in chronological order.
        parsed.sort(key=lambda x: x[0])

        # First pending meeting gets the purple NEXT highlight.
        next_idx = None
        for i, p in enumerate(parsed):
            if p[4]:   # is_pending
                next_idx = i
                break

        pending_count = sum(1 for p in parsed if p[4])

        self._clear(ctx)
        self._loading = False
        if not parsed:
            self._empty(ctx, "No meetings today")
        else:
            next_row = None
            n = len(parsed)
            for i, (_sk, t, ti, du, _pend) in enumerate(parsed):
                r = self._add_schedule_row(
                    ctx, t, ti, du, is_next=(i == next_idx), last=(i == n - 1))
                if i == next_idx:
                    next_row = r
            if next_row is not None:
                Clock.schedule_once(lambda _dt, w=next_row: ctx["scroll"].scroll_to(w, padding=10), 0)
        self._set_count(ctx, pending_count)

        # Build the voice fact for this section from the SAME rendered data.
        if not parsed:
            self._voice_facts["schedule"] = "There are no meetings scheduled today."
        else:
            pend = [(t, ti) for (_sk, t, ti, _du, ip) in parsed if ip]
            if not pend:
                self._voice_facts["schedule"] = (
                    "All of today's meetings are already completed; none are pending."
                )
            else:
                listing = "; ".join(f"{ti} at {t}" for t, ti in pend)
                self._voice_facts["schedule"] = (
                    f"Pending meetings remaining today: {len(pend)}. "
                    f"Next upcoming meeting: {pend[0][1]} at {pend[0][0]}. "
                    f"All pending meetings in order: {listing}."
                )

    def _apply_tasks(self, data: dict, today_s: str) -> None:
        ctx = self._sections[1]
        rows = data.get("commitments") or []
        try:
            today_d = date.fromisoformat(today_s)
        except ValueError:
            today_d = display_now().date()

        items = []
        for r in rows:
            if (r.get("status") or "") not in ("active", "snoozed"):
                continue
            da = (r.get("due_at") or "").strip()
            if not da:
                continue
            try:
                if "T" in da:
                    dpart = datetime.fromisoformat(da.replace("Z", "+00:00")).date()
                else:
                    dpart = date.fromisoformat(da[:10])
            except Exception:
                continue
            if dpart != today_d:
                continue
            label, sort_key = "Today", (0, da)
            items.append((sort_key, label, (r.get("title") or "Task")[:46]))

        items.sort(key=lambda x: x[0])
        self._clear(ctx)
        self._loading = False
        if not items:
            self._empty(ctx, "No tasks due today")
        else:
            n = len(items)
            for i, (_k, label, title) in enumerate(items):
                self._add_task_row(ctx, label, title, last=(i == n - 1))
        self._set_count(ctx, len(items))

        # Build the voice fact for this section from the SAME rendered data.
        if not items:
            self._voice_facts["tasks"] = "There are no pending tasks due today."
        else:
            listing = "; ".join(title for (_k, _l, title) in items)
            self._voice_facts["tasks"] = (
                f"Pending tasks due today: {len(items)}. They are: {listing}."
            )

    def _apply_emails(self, gfeed: dict) -> None:
        ctx = self._sections[2]
        summary = summarize_gmail_feed_for_home(gfeed or {})
        connected = bool(summary.get("connected"))
        # fetch_gmail_recent() already returns device-mapped rows
        # (sender / subject / time / is_read) — consume them directly.
        msgs = (gfeed or {}).get("messages") if isinstance(gfeed, dict) else None
        unread = []
        if isinstance(msgs, list):
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                if m.get("is_read", True):
                    continue
                unread.append(m)

        self._clear(ctx)
        self._loading = False
        if not connected:
            self._empty(ctx, "Connect Gmail in settings")
            self._set_count(ctx, 0)
            self._voice_facts["emails"] = (
                "Gmail is not connected, so there are no emails to report."
            )
            return
        if not unread:
            self._empty(ctx, "No unread emails")
        else:
            n = len(unread)
            for i, m in enumerate(unread):
                sender = (m.get("sender") or "Unknown").strip() or "Unknown"
                self._add_email_row(
                    ctx,
                    f"{sender[:24]}:",
                    (m.get("subject") or "(no subject)")[:32],
                    (m.get("time") or "")[:12],
                    last=(i == n - 1),
                )
        self._set_count(ctx, len(unread))

        # Build the voice fact for this section from the SAME rendered data.
        if not unread:
            self._voice_facts["emails"] = "There are no unread emails."
        else:
            listing = "; ".join(
                f"{(m.get('sender') or 'Unknown').strip() or 'Unknown'} "
                f"about {(m.get('subject') or '(no subject)').strip() or '(no subject)'}"
                for m in unread
            )
            self._voice_facts["emails"] = (
                f"Unread emails: {len(unread)}. They are: {listing}."
            )
