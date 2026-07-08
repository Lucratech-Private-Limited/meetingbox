"""Calendar Meeting Detail screen — pixel-perfect from Figma 685:1753 (1260 × 800 px).

Shows details for a Google Calendar event: time, meeting link, reminder, calendar,
invitees, notes, action items, and attachments.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget

from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH, display_now, to_display_local
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

# ── Design frame ──────────────────────────────────────────────────────────────
FW, FH = 1260.0, 800.0

# ── Asset paths ───────────────────────────────────────────────────────────────
_CAL = ASSETS_DIR / "calendar" / "figma"

def _asset(name: str) -> str:
    p = _CAL / name
    return str(p) if p.is_file() else ""


# ── Colours (exact Figma hex) ─────────────────────────────────────────────────
_BG      = (1/255,   8/255,  26/255, 1.0)   # #01081A
_WHITE   = (1.0, 1.0, 1.0, 1.0)
_MUTED   = (182/255, 186/255, 242/255, 1.0)  # #B6BAF2
_BLUE_A  = (0.0, 107/255, 249/255, 1.0)     # #006BF9
_BDR_CARD = (63/255,  66/255,  83/255, 1.0)  # #3F4253
_ICON_BG  = (1/255,  11/255,  38/255, 1.0)  # #010B26
_ICON_BDR = (63/255, 66/255,  83/255, 1.0)  # #3F4253
_GREEN   = (25/255, 211/255, 133/255, 1.0)  # #19D385
_GREEN_B = (100/255, 150/255, 114/255, 1.0) # #649672 (border)
_BTDAY   = (4/255,  132/255, 255/255, 1.0)  # #0484FF

# Card gradients
_CARD_T  = (2/255,  18/255,  60/255, 1.0)   # #02123C
_CARD_B  = (0.0,   10/255,  38/255, 1.0)    # #000A26

_FSB = "42dot-SB"
_FB  = "42dot-Sans"

_IST = timezone(timedelta(hours=5, minutes=30))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
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


# ── Card widget ───────────────────────────────────────────────────────────────

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


class _ImgBtn(ButtonBehavior, Image):
    pass


# ── CalendarMeetingDetailScreen ────────────────────────────────────────────────

class CalendarMeetingDetailScreen(BaseScreen):
    """Meeting details page matching Figma node 685:1753."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._meeting: dict = {}
        self._root_layout: FloatLayout | None = None
        self._content_widgets: list = []
        self._build_ui()

    def set_meeting(self, m: dict) -> None:
        """Call before navigating to this screen to populate it with meeting data."""
        self._meeting = dict(m) if m else {}

    # ── Build shell ────────────────────────────────────────────────────────────

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
        self.add_widget(root)

    # ── Header (static — back button, title, date) ────────────────────────────

    def _build_header(self, root: FloatLayout) -> None:
        # Back button circle  24.02, 21.19  76.28×76.28
        back_src = _asset("btn_back.png")
        if back_src:
            back = _ImgBtn(source=back_src, fit_mode="contain",
                           **_ph(24.02, 21.19, 76.28, 76.28))
        else:
            back = _TapCard(ct=_ICON_BG, cb=_ICON_BG, bdr=_ICON_BDR,
                            r=_ff(38), **_ph(24.02, 21.19, 76.28, 76.28))
            back.add_widget(_lbl("<", _FB, _ff(36), _WHITE,
                                 ha="center", va="middle",
                                 size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        back.bind(on_release=lambda *_: self.go_back())
        root.add_widget(back)

        # Title label (dynamic — updated in on_enter)
        self._title_lbl = _lbl(
            "", _FB, _ff(30), _WHITE,
            va="middle",
            **_ph(140.0, 23.0, 700.0, 40.0))
        root.add_widget(self._title_lbl)

        # Calendar icon  140, 62
        _cal_icon_path = ASSETS_DIR / "home" / "figma" / "icon_calendar_row.png"
        if not _cal_icon_path.is_file():
            _cal_icon_path = ASSETS_DIR / "brief" / "figma" / "icon_calendar.png"
        if _cal_icon_path.is_file():
            root.add_widget(Image(source=str(_cal_icon_path), fit_mode="contain",
                                  **_ph(140.0, 62.0, 31.84, 31.84)))

        # Date string  179, 64  (dynamic)
        self._date_lbl = _lbl(
            "", _FSB, _ff(25), _BLUE_A,
            va="middle",
            **_ph(179.0, 62.0, 500.0, 34.0))
        root.add_widget(self._date_lbl)

    # ── Content area (rebuilt on_enter) ───────────────────────────────────────

    def _clear_content(self) -> None:
        root = self._root_layout
        if root is None:
            return
        for w in self._content_widgets:
            root.remove_widget(w)
        self._content_widgets.clear()

    def _rebuild_content(self) -> None:
        self._clear_content()
        m = self._meeting
        if not m:
            return

        root = self._root_layout

        # ── Update header labels ──────────────────────────────────────────────
        self._title_lbl.text = m.get("title", "") or "(No title)"

        start_dt = _parse_dt(m.get("start", ""))
        now = display_now()
        if start_dt:
            # Determine if today or other day
            if start_dt.date() == now.date():
                day_prefix = "Today"
            else:
                day_prefix = start_dt.strftime("%A")
            date_str = f"{day_prefix}  {start_dt.strftime('%A, %b %d')}"
        else:
            date_str = ""
        self._date_lbl.text = date_str

        # ── Left main info card  18, 120  734×660 ────────────────────────────
        LCX, LCY, LCW, LCH = 18.0, 120.0, 734.0, 660.0
        left_card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                          r=_ff(22.6),
                          **_ph(LCX, LCY, LCW, LCH))
        self._fill_left_card(left_card, m, LCW, LCH, start_dt, now)
        root.add_widget(left_card)
        self._content_widgets.append(left_card)

        # ── Right column cards ────────────────────────────────────────────────
        RCX = 767.0   # left edge of right column cards
        RCW = 476.0   # width of all right column cards

        # Card 1 — Invitees  767, 119  476×245
        inv_card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                         r=_ff(22.6),
                         **_ph(RCX, 119.0, RCW, 245.0))
        self._fill_invitees_card(inv_card, m, RCW, 245.0)
        root.add_widget(inv_card)
        self._content_widgets.append(inv_card)

        # Card 2 — Notes  767, 374  476×144
        notes_card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                           r=_ff(22.6),
                           **_ph(RCX, 374.0, RCW, 144.0))
        self._fill_notes_card(notes_card, m, RCW, 144.0)
        root.add_widget(notes_card)
        self._content_widgets.append(notes_card)

        # Card 3 — Action Items  767, 528  476×134
        ai_card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                        r=_ff(22.6),
                        **_ph(RCX, 528.0, RCW, 134.0))
        self._fill_action_items_card(ai_card, m, RCW, 134.0)
        root.add_widget(ai_card)
        self._content_widgets.append(ai_card)

        # Card 4 — Attachments  767, 672  476×108
        att_card = _Card(ct=_CARD_T, cb=_CARD_B, bdr=_BDR_CARD,
                         r=_ff(22.6),
                         **_ph(RCX, 672.0, RCW, 108.0))
        self._fill_attachments_card(att_card, m, RCW, 108.0)
        root.add_widget(att_card)
        self._content_widgets.append(att_card)

    # ── Left card contents ────────────────────────────────────────────────────
    # Layout (y coords relative to card top, card height = 660):
    #   Row 1 – Time:      icon at (38, 67)  text at (136, 67)
    #   Row 2 – Meet link: icon at (38, 209) text at (136, 209)
    #   Row 3 – Reminder:  icon at (38, 348) text at (136, 341)
    #   Row 4 – Calendar:  icon at (38, 484) text at (136, 478)

    def _fill_left_card(self, card: _Card, m: dict, cw: float, ch: float,
                        start_dt, now) -> None:

        # ── Row 1: Time ───────────────────────────────────────────────────────
        # Clock icon  38, 67  70×70 (icon circle)
        self._icon_circle(card, cw, ch, 38.0, 67.0, "icon_clock.png")

        end_dt = _parse_dt(m.get("end", ""))
        dur_min = (m.get("duration") or 0) // 60
        if not dur_min and start_dt and end_dt:
            dur_min = max(0, int((end_dt - start_dt).total_seconds() / 60))

        if start_dt and end_dt:
            time_range = (f"{start_dt.strftime('%I:%M %p').lstrip('0')}"
                          f" - {end_dt.strftime('%I:%M %p').lstrip('0')}")
        elif start_dt:
            time_range = start_dt.strftime("%I:%M %p").lstrip("0")
        else:
            time_range = "--:-- - --:--"

        card.add_widget(_lbl(
            time_range, _FB, _ff(30), _WHITE, va="middle",
            size_hint=(560 / cw, 38 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 67 - 38) / ch}))

        dur_str = f"{dur_min} min" if dur_min else ""
        card.add_widget(_lbl(
            dur_str, _FB, _ff(28), _MUTED, va="middle",
            size_hint=(300 / cw, 34 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 111 - 34) / ch}))

        # Status badge  537, 97  148×40  rounded
        state = self._meeting_state(m, now)
        self._status_badge(card, cw, ch, 537.0, 97.0, state)

        # ── Row 2: Meeting link ───────────────────────────────────────────────
        # Video icon  38, 209
        self._icon_circle(card, cw, ch, 38.0, 209.0, "icon_meeting.png")

        hangout = (m.get("hangoutLink") or "").strip()
        location = (m.get("location") or "").strip()
        platform_name = "Google Meet" if hangout else ("Online" if location else "In-person")
        link_text = hangout or location or ""

        card.add_widget(_lbl(
            platform_name, _FB, _ff(30), _WHITE, va="middle",
            size_hint=(500 / cw, 38 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 209 - 38) / ch}))

        if link_text:
            # Blue clickable link
            card.add_widget(_lbl(
                link_text, _FB, _ff(26), _BLUE_A, va="middle",
                size_hint=(530 / cw, 34 / ch),
                pos_hint={"x": 136 / cw, "y": (ch - 253 - 34) / ch}))

            # Copy button circle  right side  ~643, 224  70×70
            copy_src = _asset("icon_copy.png")
            copy_btn = self._icon_circle_widget(cw, ch, 625.0, 220.0)
            if copy_src:
                copy_btn.add_widget(Image(
                    source=copy_src, fit_mode="contain",
                    size_hint=(0.7, 0.7), pos_hint={"x": 0.15, "y": 0.15}))
            card.add_widget(copy_btn)
        else:
            card.add_widget(_lbl(
                "No meeting link", _FB, _ff(26), _MUTED, va="middle",
                size_hint=(530 / cw, 34 / ch),
                pos_hint={"x": 136 / cw, "y": (ch - 253 - 34) / ch}))

        # ── Row 3: Reminder ───────────────────────────────────────────────────
        self._icon_circle(card, cw, ch, 38.0, 348.0, "icon_bell.png")

        rem = m.get("reminder_minutes")
        if rem is not None:
            rem_text = f"{rem} minutes before"
        else:
            rem_text = "No reminder set"

        card.add_widget(_lbl(
            "Reminder", _FB, _ff(30), _WHITE, va="middle",
            size_hint=(400 / cw, 38 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 341 - 38) / ch}))

        card.add_widget(_lbl(
            rem_text, _FB, _ff(28), _MUTED, va="middle",
            size_hint=(500 / cw, 34 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 385 - 34) / ch}))

        # Arrow  right  646, 371
        self._arrow(card, cw, ch, 646.0, 371.0)

        # ── Row 4: Calendar ───────────────────────────────────────────────────
        self._icon_circle(card, cw, ch, 38.0, 484.0, "icon_clock.png")

        cal_src = _asset("icon_clock.png")
        _cal_icon_path = ASSETS_DIR / "home" / "figma" / "icon_calendar_row.png"
        if not _cal_icon_path.is_file():
            _cal_icon_path = ASSETS_DIR / "brief" / "figma" / "icon_calendar.png"
        if _cal_icon_path.is_file():
            # Replace icon circle with calendar icon
            icon_w, icon_h = 70.63, 70.63
            ix, iy = 38.0, 484.0
            card.add_widget(Image(
                source=str(_cal_icon_path), fit_mode="contain",
                size_hint=(icon_w / cw, icon_h / ch),
                pos_hint={"x": ix / cw, "y": (ch - iy - icon_h) / ch}))

        cal_name = (m.get("calendar_name") or "Work").strip()
        card.add_widget(_lbl(
            "Calendar", _FB, _ff(30), _WHITE, va="middle",
            size_hint=(400 / cw, 38 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 478 - 38) / ch}))

        card.add_widget(_lbl(
            cal_name, _FB, _ff(28), _BLUE_A, va="middle",
            size_hint=(500 / cw, 34 / ch),
            pos_hint={"x": 136 / cw, "y": (ch - 521 - 34) / ch}))

        # Arrow  right  646, 507
        self._arrow(card, cw, ch, 646.0, 507.0)

    # ── Icon circle helpers ───────────────────────────────────────────────────

    def _icon_circle(self, card: FloatLayout, cw: float, ch: float,
                     ix: float, iy: float, icon_name: str) -> None:
        iw, ih = 70.63, 70.63
        ic = FloatLayout(
            size_hint=(iw / cw, ih / ch),
            pos_hint={"x": ix / cw, "y": (ch - iy - ih) / ch})
        with ic.canvas.before:
            Color(*_ICON_BG)
            self._ic_bg = RoundedRectangle(
                pos=ic.pos, size=ic.size, radius=[_ff(16.21)])
            Color(*_ICON_BDR[:3], 0.9)
            self._ic_ln = Line(
                rounded_rectangle=(ic.x, ic.y, ic.width, ic.height, _ff(16.21)),
                width=0.95)

        def _upd_ic(w, *_):
            self._ic_bg.pos = w.pos
            self._ic_bg.size = w.size
            self._ic_bg.radius = [_ff(16.21)]
            self._ic_ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(16.21))
        ic.bind(pos=_upd_ic, size=_upd_ic)

        src = _asset(icon_name)
        if src:
            ic.add_widget(Image(source=src, fit_mode="contain",
                                size_hint=(0.7, 0.7),
                                pos_hint={"x": 0.15, "y": 0.15}))
        card.add_widget(ic)

    def _icon_circle_widget(self, cw: float, ch: float,
                            ix: float, iy: float) -> FloatLayout:
        iw, ih = 70.63, 70.63
        ic = FloatLayout(
            size_hint=(iw / cw, ih / ch),
            pos_hint={"x": ix / cw, "y": (ch - iy - ih) / ch})
        with ic.canvas.before:
            Color(*_ICON_BG)
            bg = RoundedRectangle(pos=ic.pos, size=ic.size, radius=[_ff(16.21)])
            Color(*_ICON_BDR[:3], 0.9)
            ln = Line(rounded_rectangle=(ic.x, ic.y, ic.width, ic.height, _ff(16.21)),
                      width=0.95)

        def _upd(w, *_):
            bg.pos = w.pos; bg.size = w.size; bg.radius = [_ff(16.21)]
            ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(16.21))
        ic.bind(pos=_upd, size=_upd)
        return ic

    def _arrow(self, card: FloatLayout, cw: float, ch: float,
               ax: float, ay: float) -> None:
        """Small right-facing arrow icon."""
        AW, AH = 19.78, 39.55
        arr_src = ""
        for _p in [
            _CAL / "icon_arrow_details.png",
            _CAL / "icon_nav_left_arrow.png",
            ASSETS_DIR / "brief" / "figma" / "icon_arrow_right.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow_card.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow.png",
        ]:
            if _p.is_file():
                arr_src = str(_p)
                break
        if arr_src:
            card.add_widget(Image(
                source=arr_src, fit_mode="contain",
                size_hint=(AW / cw, AH / ch),
                pos_hint={"x": ax / cw, "y": (ch - ay - AH) / ch}))
        else:
            card.add_widget(_lbl(
                ">", _FB, _ff(28), _MUTED, ha="center", va="middle",
                size_hint=(24 / cw, 36 / ch),
                pos_hint={"x": ax / cw, "y": (ch - ay - 36) / ch}))

    def _status_badge(self, card: FloatLayout, cw: float, ch: float,
                      bx: float, by: float, state: str) -> None:
        BW, BH = 148.0, 40.0
        badge = FloatLayout(
            size_hint=(BW / cw, BH / ch),
            pos_hint={"x": bx / cw, "y": (ch - by - BH) / ch})

        if state == "active":
            text_color = _GREEN
            border_color = _GREEN_B
            dot_color = _GREEN
            label = "Active"
        elif state == "past":
            text_color = _MUTED
            border_color = _MUTED
            dot_color = _MUTED
            label = "Past"
        else:
            text_color = _GREEN
            border_color = _GREEN_B
            dot_color = _GREEN
            label = "Upcoming"

        with badge.canvas.before:
            Color(*border_color[:3], 0.8)
            _bl = Line(
                rounded_rectangle=(badge.x, badge.y, badge.width, badge.height, _ff(18)),
                width=0.95)

        def _upd_b(w, *_):
            _bl.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(18))
        badge.bind(pos=_upd_b, size=_upd_b)

        # Green dot
        dot = Widget(
            size_hint=(12 / BW, 12 / BH),
            pos_hint={"x": 16 / BW, "y": (BH / 2 - 6) / BH})
        with dot.canvas:
            Color(*dot_color)
            _de = Ellipse(pos=dot.pos, size=dot.size)
        dot.bind(
            pos=lambda w, v: setattr(_de, "pos", v),
            size=lambda w, v: setattr(_de, "size", v))
        badge.add_widget(dot)

        badge.add_widget(_lbl(
            label, _FSB, _ff(20), text_color, va="middle",
            size_hint=(100 / BW, 26 / BH),
            pos_hint={"x": 35 / BW, "y": (BH / 2 - 13) / BH}))

        card.add_widget(badge)

    # ── Right column card contents ────────────────────────────────────────────

    def _fill_invitees_card(self, card: _Card, m: dict,
                            cw: float, ch: float) -> None:
        """People icon + title + count badge + invitee rows."""
        # People icon circle  19, 7  70×70
        icon_w = 48.03
        ic = self._small_icon_circle(cw, ch, 19.0, 7.0, icon_w, "icon_people.png")
        card.add_widget(ic)

        # Title
        title = m.get("title", "") or "(No title)"
        card.add_widget(_lbl(
            title, _FB, _ff(25), _WHITE, va="middle",
            size_hint=(320 / cw, 34 / ch),
            pos_hint={"x": 73.59 / cw, "y": (ch - 16.59 - 34) / ch}))

        # Count badge
        attendees = m.get("attendees") or []
        n_att = len(attendees)
        self._count_badge(card, cw, ch, 420.0, 16.59, str(n_att) if n_att else "0")

        # Arrow
        self._right_arrow(card, cw, ch)

        if not attendees:
            card.add_widget(_lbl(
                "No invitees", _FSB, _ff(20), _MUTED, va="middle",
                size_hint=(300 / cw, 30 / ch),
                pos_hint={"x": 20 / cw, "y": (ch - 80 - 30) / ch}))
            return

        # Build up to 3 attendee rows
        row_y = 54.71  # y of first avatar row inside card
        for idx, att in enumerate(attendees[:3]):
            name = att.get("name") or att.get("email") or "Unknown"
            is_organizer = att.get("organizer") or (att.get("email") == m.get("organizer_email"))
            is_self = att.get("self") or False

            # Avatar circle  22, row_y  46×46
            av = self._avatar_circle(cw, ch, 22.0, row_y, is_self)
            card.add_widget(av)

            # Name
            if is_self:
                display_name = "You"
            else:
                display_name = name
            card.add_widget(_lbl(
                display_name, _FB, _ff(20), _WHITE, va="middle",
                size_hint=(250 / cw, 28 / ch),
                pos_hint={"x": 94.59 / cw, "y": (ch - row_y - 28) / ch}))

            if is_organizer:
                card.add_widget(_lbl(
                    "Organizer", _FSB, _ff(20), _BLUE_A, va="middle",
                    size_hint=(200 / cw, 24 / ch),
                    pos_hint={"x": 95.59 / cw, "y": (ch - (row_y + 30) - 24) / ch}))
                row_y += 70
            else:
                row_y += 55

    def _fill_notes_card(self, card: _Card, m: dict,
                         cw: float, ch: float) -> None:
        """File icon + Notes title + bullet items from description."""
        # File icon  18, 9  32×32
        file_src = _asset("icon_notes.png")
        if file_src:
            card.add_widget(Image(source=file_src, fit_mode="contain",
                                  size_hint=(31.84 / cw, 31.84 / ch),
                                  pos_hint={"x": 18.59 / cw, "y": (ch - 9.59 - 31.84) / ch}))
        else:
            card.add_widget(_lbl(
                "≡", _FB, _ff(28), _MUTED, ha="center", va="middle",
                size_hint=(31.84 / cw, 31.84 / ch),
                pos_hint={"x": 18.59 / cw, "y": (ch - 9.59 - 31.84) / ch}))

        card.add_widget(_lbl(
            "Notes", _FB, _ff(25), _WHITE, va="middle",
            size_hint=(200 / cw, 34 / ch),
            pos_hint={"x": 63.59 / cw, "y": (ch - 11.59 - 34) / ch}))

        # Parse description into bullet lines
        desc = (m.get("description") or "").strip()
        if desc:
            lines = [l.strip() for l in desc.split("\n") if l.strip()][:3]
        else:
            lines = []

        if not lines:
            card.add_widget(_lbl(
                "No notes", _FSB, _ff(20), _MUTED, va="middle",
                size_hint=(350 / cw, 28 / ch),
                pos_hint={"x": 18.59 / cw, "y": (ch - 50 - 28) / ch}))
        else:
            bullet_y = 41.59
            for line in lines:
                card.add_widget(_lbl(
                    f"• {line}", _FB, _ff(20), _MUTED, va="middle",
                    size_hint=(420 / cw, 26 / ch),
                    pos_hint={"x": 18.59 / cw, "y": (ch - bullet_y - 26) / ch}))
                bullet_y += 30.0

    def _fill_action_items_card(self, card: _Card, m: dict,
                                cw: float, ch: float) -> None:
        """Checkmark icon + Action Items + count + items from summary."""
        # Checkmark icon
        chk_src = _asset("icon_tick.png")
        if chk_src:
            card.add_widget(Image(source=chk_src, fit_mode="contain",
                                  size_hint=(24 / cw, 24 / ch),
                                  pos_hint={"x": 21.56 / cw, "y": (ch - 15.59 - 24) / ch}))
        else:
            card.add_widget(_lbl(
                "✓", _FB, _ff(24), _BLUE_A, ha="center", va="middle",
                size_hint=(24 / cw, 30 / ch),
                pos_hint={"x": 21.56 / cw, "y": (ch - 15.59 - 30) / ch}))

        # Gather action items: prefer summary action_items, fall back to empty
        action_items = []
        summary = m.get("summary") or {}
        if isinstance(summary, dict):
            ai_raw = summary.get("action_items") or []
            for ai in ai_raw:
                if isinstance(ai, dict):
                    action_items.append(ai.get("task") or ai.get("text") or str(ai))
                elif isinstance(ai, str):
                    action_items.append(ai)

        n_ai = len(action_items)
        card.add_widget(_lbl(
            "Action Items", _FB, _ff(25), _WHITE, va="middle",
            size_hint=(280 / cw, 34 / ch),
            pos_hint={"x": 68.59 / cw, "y": (ch - 15.59 - 34) / ch}))

        if n_ai > 0:
            self._count_badge(card, cw, ch, 405.0, 11.59, str(n_ai))
        self._right_arrow(card, cw, ch)

        if not action_items:
            card.add_widget(_lbl(
                "No action items", _FSB, _ff(20), _MUTED, va="middle",
                size_hint=(350 / cw, 28 / ch),
                pos_hint={"x": 20 / cw, "y": (ch - 65 - 28) / ch}))
        else:
            row_y = 59.59
            for item in action_items[:2]:
                # Small bullet circle
                dot = Widget(
                    size_hint=(23 / cw, 23 / ch),
                    pos_hint={"x": 27.59 / cw, "y": (ch - row_y - 23) / ch})
                with dot.canvas:
                    Color(*_MUTED[:3], 0.6)
                    _de = Ellipse(pos=dot.pos, size=dot.size)
                    Color(*_MUTED[:3], 0.8)
                    _dl = Line(ellipse=(dot.x, dot.y, dot.width, dot.height), width=1.1)
                dot.bind(
                    pos=lambda w, v, e=_de, l=_dl: (
                        setattr(e, "pos", v),
                        setattr(l, "ellipse", (v[0], v[1], w.width, w.height))),
                    size=lambda w, v, e=_de, l=_dl: (
                        setattr(e, "size", v),
                        setattr(l, "ellipse", (w.x, w.y, v[0], v[1]))))
                card.add_widget(dot)

                card.add_widget(_lbl(
                    item, _FB, _ff(20), _MUTED, va="middle",
                    size_hint=(370 / cw, 28 / ch),
                    pos_hint={"x": 68.59 / cw, "y": (ch - row_y - 28) / ch}))
                row_y += 32.0

    def _fill_attachments_card(self, card: _Card, m: dict,
                               cw: float, ch: float) -> None:
        """Attachment icon + title + count + file list."""
        att_src = _asset("icon_attachment.png")
        if att_src:
            card.add_widget(Image(source=att_src, fit_mode="contain",
                                  size_hint=(52.06 / cw, 52.06 / ch),
                                  pos_hint={"x": 21.56 / cw, "y": (ch - 6.56 - 52.06) / ch}))
        else:
            card.add_widget(_lbl(
                "⊘", _FB, _ff(26), _MUTED, ha="center", va="middle",
                size_hint=(40 / cw, 40 / ch),
                pos_hint={"x": 21.56 / cw, "y": (ch - 12 - 40) / ch}))

        attachments = m.get("attachments") or []
        n_att = len(attachments)

        card.add_widget(_lbl(
            "Attachments", _FB, _ff(25), _WHITE, va="middle",
            size_hint=(280 / cw, 34 / ch),
            pos_hint={"x": 73.59 / cw, "y": (ch - 12.59 - 34) / ch}))

        if n_att > 0:
            self._count_badge(card, cw, ch, 405.0, 11.59, str(n_att))
        self._right_arrow(card, cw, ch)

        if not attachments:
            card.add_widget(_lbl(
                "No attachments", _FSB, _ff(20), _MUTED, va="middle",
                size_hint=(350 / cw, 28 / ch),
                pos_hint={"x": 20 / cw, "y": (ch - 60 - 28) / ch}))
        else:
            file_title = (attachments[0].get("title") or "Attachment").strip()
            # Note icon
            note_src = _asset("icon_notes.png")
            if note_src:
                card.add_widget(Image(source=note_src, fit_mode="contain",
                                      size_hint=(24 / cw, 24 / ch),
                                      pos_hint={"x": 35.59 / cw, "y": (ch - 62.59 - 24) / ch}))
            card.add_widget(_lbl(
                file_title, _FB, _ff(20), _MUTED, va="middle",
                size_hint=(370 / cw, 28 / ch),
                pos_hint={"x": 73.59 / cw, "y": (ch - 58.59 - 28) / ch}))

    # ── Small icon circle (for right column header icons) ─────────────────────

    def _small_icon_circle(self, cw: float, ch: float, ix: float, iy: float,
                           icon_w: float, icon_name: str) -> FloatLayout:
        iw = ih = 70.63
        ic = FloatLayout(
            size_hint=(iw / cw, ih / ch),
            pos_hint={"x": ix / cw, "y": (ch - iy - ih) / ch})
        with ic.canvas.before:
            Color(*_ICON_BG)
            bg = RoundedRectangle(pos=ic.pos, size=ic.size, radius=[_ff(16.21)])
            Color(*_ICON_BDR[:3], 0.9)
            ln = Line(rounded_rectangle=(ic.x, ic.y, ic.width, ic.height, _ff(16.21)),
                      width=0.95)

        def _upd(w, *_):
            bg.pos = w.pos; bg.size = w.size; bg.radius = [_ff(16.21)]
            ln.rounded_rectangle = (w.x, w.y, w.width, w.height, _ff(16.21))
        ic.bind(pos=_upd, size=_upd)

        src = _asset(icon_name)
        if src:
            ic.add_widget(Image(source=src, fit_mode="contain",
                                size_hint=(0.68, 0.68),
                                pos_hint={"x": 0.16, "y": 0.16}))
        return ic

    def _count_badge(self, card: FloatLayout, cw: float, ch: float,
                     bx: float, by: float, count: str) -> None:
        """Blue translucent circle with count number."""
        BD = 30.0
        badge = FloatLayout(
            size_hint=(BD / cw, BD / ch),
            pos_hint={"x": bx / cw, "y": (ch - by - BD) / ch})
        with badge.canvas.before:
            Color(0.0, 107/255, 249/255, 0.2)
            _bg = Ellipse(pos=badge.pos, size=badge.size)
            Color(*_ICON_BDR[:3], 0.6)
            _bl = Line(ellipse=(badge.x, badge.y, badge.width, badge.height), width=0.6)

        def _upd_bd(w, *_):
            _bg.pos = w.pos; _bg.size = w.size
            _bl.ellipse = (w.x, w.y, w.width, w.height)
        badge.bind(pos=_upd_bd, size=_upd_bd)

        badge.add_widget(_lbl(
            count, _FSB, _ff(20), _BLUE_A, ha="center", va="middle",
            size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        card.add_widget(badge)

    def _right_arrow(self, card: FloatLayout, cw: float, ch: float) -> None:
        """Right-aligned arrow at right edge of card."""
        AW, AH = 19.78, 39.55
        arr_src = ""
        for _p in [
            _CAL / "icon_arrow_details.png",
            _CAL / "icon_nav_left_arrow.png",
            ASSETS_DIR / "brief" / "figma" / "icon_arrow_right.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow_card.png",
            ASSETS_DIR / "home"  / "figma" / "icon_arrow.png",
        ]:
            if _p.is_file():
                arr_src = str(_p)
                break
        ax = cw - AW - 24.0
        ay = 11.59
        if arr_src:
            card.add_widget(Image(
                source=arr_src, fit_mode="contain",
                size_hint=(AW / cw, AH / ch),
                pos_hint={"x": ax / cw, "y": (ch - ay - AH) / ch}))
        else:
            card.add_widget(_lbl(
                ">", _FB, _ff(24), _MUTED, ha="center", va="middle",
                size_hint=(24 / cw, 32 / ch),
                pos_hint={"x": ax / cw, "y": (ch - ay - 32) / ch}))

    def _avatar_circle(self, cw: float, ch: float, ix: float, iy: float,
                       is_self: bool) -> FloatLayout:
        """46×46 avatar circle — blue bg for 'You', icon for others."""
        AW = AH = 46.0
        av = FloatLayout(
            size_hint=(AW / cw, AH / ch),
            pos_hint={"x": ix / cw, "y": (ch - iy - AH) / ch})

        if is_self:
            with av.canvas.before:
                Color(0.0, 107/255, 249/255, 0.2)
                _ab = Ellipse(pos=av.pos, size=av.size)
                Color(*_ICON_BDR[:3], 0.6)
                _al = Line(ellipse=(av.x, av.y, av.width, av.height), width=0.6)

            def _upd_av(w, *_):
                _ab.pos = w.pos; _ab.size = w.size
                _al.ellipse = (w.x, w.y, w.width, w.height)
            av.bind(pos=_upd_av, size=_upd_av)

            person_src = _asset("icon_person.png")
            if person_src:
                av.add_widget(Image(source=person_src, fit_mode="contain",
                                    size_hint=(0.65, 0.65),
                                    pos_hint={"x": 0.175, "y": 0.175}))
            else:
                av.add_widget(_lbl("Y", _FB, _ff(18), _BLUE_A, ha="center", va="middle",
                                   size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        else:
            with av.canvas.before:
                Color(*_ICON_BG)
                _ab = Ellipse(pos=av.pos, size=av.size)
                Color(*_ICON_BDR[:3], 0.6)
                _al = Line(ellipse=(av.x, av.y, av.width, av.height), width=0.6)

            def _upd_av2(w, *_):
                _ab.pos = w.pos; _ab.size = w.size
                _al.ellipse = (w.x, w.y, w.width, w.height)
            av.bind(pos=_upd_av2, size=_upd_av2)

            person_src = _asset("icon_person.png")
            if person_src:
                av.add_widget(Image(source=person_src, fit_mode="contain",
                                    size_hint=(0.65, 0.65),
                                    pos_hint={"x": 0.175, "y": 0.175}))

        return av

    # ── Meeting state helper ──────────────────────────────────────────────────

    @staticmethod
    def _meeting_state(m: dict, now) -> str:
        def _p(iso):
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

        start = _p(m.get("start", ""))
        end   = _p(m.get("end", ""))
        if start is None or end is None:
            return "upcoming"
        if now >= end:
            return "past"
        if now >= start:
            return "active"
        return "upcoming"

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_enter(self) -> None:
        Clock.schedule_once(lambda _dt: self._rebuild_content(), 0)

    def on_leave(self) -> None:
        pass
