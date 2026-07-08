"""Calendar event creation screen — Figma node 1126:147 "Create event" (1260 × 800 px).

Shown when the voice agent calls ``show_calendar_event``. Sits on top of the
voice-session transcription screen while the Realtime session is still live.
The user reviews the pre-filled event and taps Confirm (fires ``on_confirm``)
or Discard (fires ``on_discard``). Adding an attendee email reuses the same
contact-selection overlay as the email-drafting flow (``RecipientConfirmOverlay``).

Layout (Figma 1260 × 800 baseline):
  • Full-bleed vs_bg.png  +  rgba(255,255,255,0.45) white overlay
  • Top-right: Listening/Thinking/Talking pill (851,17) 222×47 + WiFi + battery
  • Card "Frame 22": (24,78) 1212×587  #FDFDFD  radius=38
      ┌──────────────────────────────────────────────────────────┐
      │  [calendar icon]  Create Event       [purple #6D48CC]     │ 87 px
      ├──────────────────────────────────────────────────────────┤
      │  [ Event Name ] ← grey pill   Marketing review            │
      │  [ Date ] grey pill <value> [ Time ] grey pill <val> [ Duration ] <val>│
      │  ┌──────────────────────────────────────────────────────┐│
      │  │ [ Attendees ] grey pill   [chip] [chip] [chip] …      ││  scroll
      │  └──────────────────────────────────────────────────────┘│
      └──────────────────────────────────────────────────────────┘
  • Discard (363,702) 236×66  #ED5B77  radius=50
  • Confirm (661,702) 236×66  #10C76D  radius=50

Public API (called by main.py):
    set_event_data(name, date, time, duration, attendees) – progressive merge fill
    add_attendee(label)                           – append one attendee chip
    remove_attendee(label)                        – remove one attendee chip
    set_attendees(labels)                         – replace attendee chips
    reset()                                       – clear to a fresh blank draft
    show_listening_state()                        – forward voice state → pill
    set_voice_session_state(state)                – forward state → pill
    update_amplitude(amp)                         – forward amplitude → pill waveform
"""
from __future__ import annotations

import logging
import time

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.stacklayout import StackLayout
from kivy.uix.widget import Widget

from components.live_wifi_icon import LiveWifiIcon as _WifiIcon
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen
from screens.home import _BatteryWidget, _VoiceStatePill  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Figma frame constants  (1260 × 800 px baseline)
# ──────────────────────────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0
_VS_DIR   = ASSETS_DIR / "voice-session" / "figma"

# Colours (from Figma node 1126:147)
_PURPLE      = (109/255, 72/255, 204/255, 1.0)   # #6D48CC  header bar
_WHITE       = (1.0, 1.0, 1.0, 1.0)
_CARD_BG     = (253/255, 253/255, 253/255, 1.0)  # #FDFDFD  main card
_LABEL_BG    = (158/255, 157/255, 162/255, 1.0)  # #9E9DA2  field label pill bg
_FIELD_BG    = (236/255, 236/255, 236/255, 1.0)  # #ECECEC  field container background
_CHIP_BG     = (254/255, 254/255, 254/255, 1.0)  # #FEFEFE  attendee chip background
_TEXT_VALUE  = (47/255, 47/255, 47/255, 1.0)     # #2F2F2F  field value text
_TEXT_MUTED  = (0.50, 0.50, 0.52, 1.0)           # placeholder text
_SCROLL_TRACK = (218/255, 218/255, 218/255, 1.0)  # #DADADA scrollbar track
_BTN_DISCARD = (237/255, 91/255, 119/255, 1.0)   # #ED5B77
_BTN_CONFIRM = (16/255, 199/255, 109/255, 1.0)   # #10C76D
_FONT_SB     = "42dot-SB"


# ──────────────────────────────────────────────────────────────────────────────
# Coordinate / scale helpers  (identical to voice_session.py / email_draft.py)
# ──────────────────────────────────────────────────────────────────────────────
def _x(px: float) -> float:
    return px / _FW


def _y(top: float, h: float) -> float:
    return max(0.0, (_FH - top - h) / _FH)


def _sw(px: float) -> float:
    return px / _FW


def _sh(px: float) -> float:
    return px / _FH


def _ff(fs: float) -> int:
    s = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)
    return max(6, round(fs * s))


def _fp(name: str) -> str:
    p = _VS_DIR / name
    return str(p) if p.is_file() else ""


# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# Pill button  (identical to voice_task_creation._PillButton, 66 px height)
# ──────────────────────────────────────────────────────────────────────────────
class _PillButton(BoxLayout):
    def __init__(self, text: str, bg_color: tuple, on_tap, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self._on_tap = on_tap
        # radius = half button height so the ends are always a true semicircle
        self._pill_r = _ff(33)
        # Figma boxShadow: 0px 4px 4px rgba(0,0,0,0.25)
        _S = [(0, 0.09), (1, 0.07), (2, 0.05), (3, 0.04), (4, 0.03), (5, 0.02)]
        with self.canvas.before:
            self._shadow_layers = []
            self._shadow_colors = []
            for _, alpha in _S:
                self._shadow_colors.append(Color(0, 0, 0, alpha))
                self._shadow_layers.append(RoundedRectangle(radius=[self._pill_r]))
            Color(*bg_color)
            self._bg = RoundedRectangle(radius=[self._pill_r])
        self._shadow_spec = _S
        self._press_depth = 0.0
        self.bind(pos=self._sync, size=self._sync)
        lbl = Label(
            text=text,
            font_name=_FONT_SB,
            font_size=_ff(40),
            bold=False,
            color=_WHITE,
            halign="center",
            valign="middle",
        )
        lbl.bind(size=lbl.setter("text_size"))
        self.add_widget(lbl)

    def _sync(self, *_) -> None:
        # On press the shadow drops a touch further for the native "lift" cue.
        extra = 2.0 * self._press_depth
        for layer, (half_e, _) in zip(self._shadow_layers, self._shadow_spec):
            layer.pos  = (self.x - half_e, self.y - 4 - extra - half_e)
            layer.size = (self.width + 2 * half_e, self.height + 2 * half_e)
        self._bg.pos  = self.pos
        self._bg.size = self.size

    def set_press_shadow(self, depth: float) -> None:
        """Slightly deepen the drop shadow during the genie press (depth 0→1)."""
        depth = 0.0 if depth < 0.0 else 1.0 if depth > 1.0 else depth
        self._press_depth = depth
        for col, (_, base_alpha) in zip(self._shadow_colors, self._shadow_spec):
            col.a = base_alpha * (1.0 + 0.6 * depth)
        self._sync()

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self._on_tap:
                self._on_tap()
            return True
        return super().on_touch_down(touch)


# ──────────────────────────────────────────────────────────────────────────────
# Attendee chip  — #FEFEFE rounded pill (Figma Frame 34/35/36…, h=56 radius=27)
# ──────────────────────────────────────────────────────────────────────────────
class _AttendeeChip(BoxLayout):
    def __init__(self, text: str, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("height", _ff(56))
        super().__init__(
            orientation="horizontal",
            padding=[_ff(22), 0],
            **kw,
        )
        with self.canvas.before:
            Color(*_CHIP_BG)
            self._bg = RoundedRectangle(radius=[_ff(27)])
        self.bind(pos=self._sync, size=self._sync)
        lbl = Label(
            text=text,
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_VALUE,
            halign="center",
            valign="middle",
            size_hint=(None, 1),
        )
        lbl.bind(texture_size=lambda l, ts: setattr(l, "width", ts[0]))
        self.add_widget(lbl)
        self._lbl = lbl
        Clock.schedule_once(lambda _dt: self._fit(), 0)

    def _fit(self) -> None:
        self.width = self._lbl.width + _ff(44)

    def _sync(self, *_) -> None:
        self._bg.pos  = self.pos
        self._bg.size = self.size


# ──────────────────────────────────────────────────────────────────────────────
# CalendarEventCreationScreen
# ──────────────────────────────────────────────────────────────────────────────
class CalendarEventCreationScreen(BaseScreen):
    """Event confirmation screen shown before a voice-initiated calendar event
    is created. The voice agent calls ``show_calendar_event`` → the device
    navigates here with pre-filled fields. The user taps Confirm or Discard.
    """

    def __init__(self, on_confirm=None, on_discard=None, **kw):
        self._on_confirm_cb = on_confirm
        self._on_discard_cb = on_discard
        self._voice_pill:    _VoiceStatePill | None = None
        self._battery:       _BatteryWidget  | None = None
        self._name_lbl:      Label | None = None
        self._date_lbl:      Label | None = None
        self._time_lbl:      Label | None = None
        self._duration_lbl:  Label | None = None
        self._att_stack:     StackLayout | None = None
        self._att_spacer:    Widget | None = None
        self._attendees:     list[str] = []
        self._listening:     bool  = False
        self._amplitude:     float = 0.0
        self._voice_tick_ev: object | None = None
        # Card widget reference + guard so the confirm/discard fly-away plays once.
        self._card = None
        self._flyaway_committed = False
        super().__init__(**kw)
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:  # noqa: PLR0915
        root = FloatLayout()

        # ── 1. Background image (same as voice_session) ───────────────────────
        bg_src = _fp("vs_bg.png")
        if bg_src:
            root.add_widget(Image(
                source=bg_src,
                size_hint=(1, 1),
                pos_hint={"x": 0, "y": 0},
                fit_mode="fill",
                allow_stretch=True,
                keep_ratio=False,
            ))

        # ── 2. Semi-transparent white overlay ─────────────────────────────────
        ov = Widget(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        with ov.canvas:
            _ovc = Color(1, 1, 1, 0.45)   # noqa: F841
            _ovr = Rectangle(pos=ov.pos, size=ov.size)
        ov.bind(
            pos=lambda w, p: setattr(_ovr, "pos",  p),
            size=lambda w, s: setattr(_ovr, "size", s),
        )
        root.add_widget(ov)

        # ── 3. Main card  (24,78) 1212×587  #FDFDFD  radius=38 ────────────────
        card = Widget(
            size_hint=(_sw(1212), _sh(587)),
            pos_hint={"x": _x(24), "y": _y(78, 587)},
        )
        with card.canvas:
            Color(*_CARD_BG)
            self._card_bg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[_ff(38)]
            )
        card.bind(
            pos=lambda w, _: setattr(self._card_bg, "pos",  w.pos),
            size=lambda w, _: setattr(self._card_bg, "size", w.size),
        )
        root.add_widget(card)
        # Reference kept so the action fly-away overlay can snapshot the card.
        self._card = card

        # ── 4. Purple header bar  abs(24,78) 1212×87  #6D48CC ─────────────────
        # Rounded at the top (matches card radius), flat at the bottom.
        hdr = Widget(
            size_hint=(_sw(1212), _sh(87)),
            pos_hint={"x": _x(24), "y": _y(78, 87)},
        )
        with hdr.canvas:
            Color(*_PURPLE)
            self._hdr_round = RoundedRectangle(pos=hdr.pos, size=hdr.size, radius=[_ff(38)])
            self._hdr_fill  = Rectangle()

        def _sync_hdr(widget, *_):
            r = _ff(38)
            self._hdr_round.pos  = widget.pos
            self._hdr_round.size = widget.size
            self._hdr_fill.pos   = (widget.x, widget.y)
            self._hdr_fill.size  = (widget.width, r)

        hdr.bind(pos=_sync_hdr, size=_sync_hdr)
        Clock.schedule_once(lambda _dt: _sync_hdr(hdr), 0)
        root.add_widget(hdr)

        # ── 4b. Calendar icon  abs(52,93) 57×57 ───────────────────────────────
        cal_icon_src = _fp("vc_calendar_icon.png")
        if cal_icon_src:
            root.add_widget(Image(
                source=cal_icon_src,
                size_hint=(_sw(57), _sh(57)),
                pos_hint={"x": _x(52), "y": _y(93, 57)},
                fit_mode="contain",
                allow_stretch=True,
                keep_ratio=True,
            ))

        # ── 5. "Create Event" heading text  abs(124,103) 186×38 ───────────────
        root.add_widget(Label(
            text="Create Event",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_WHITE,
            halign="center",
            valign="middle",
            size_hint=(_sw(186), _sh(38)),
            pos_hint={"x": _x(124), "y": _y(103, 38)},
        ))

        # ── 6. Event Name field  abs(55,182) 1151×61  #ECECEC  radius=27 ──────
        self._add_field_box(root, 55, 182, 1151, 61)
        self._add_label_pill(root, 55, 182, 228, 61, "Event Name")
        self._name_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(40),
            color=_TEXT_VALUE,
            halign="left",
            valign="middle",
            size_hint=(_sw(880), _sh(48)),
            pos_hint={"x": _x(315), "y": _y(188, 48)},
            shorten=True,
            shorten_from="right",
        )
        self._name_lbl.bind(size=self._name_lbl.setter("text_size"))
        root.add_widget(self._name_lbl)

        # ── 7. Date field  abs(55,268) 428×56  #ECECEC  radius=27 ─────────────
        self._add_field_box(root, 55, 268, 428, 56)
        self._add_label_pill(root, 55, 268, 124, 56, "Date")
        self._date_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_VALUE,
            halign="left",
            valign="middle",
            size_hint=(_sw(290), _sh(56)),
            pos_hint={"x": _x(193), "y": _y(268, 56)},
            shorten=True,
            shorten_from="right",
        )
        self._date_lbl.bind(size=self._date_lbl.setter("text_size"))
        root.add_widget(self._date_lbl)

        # ── 8. Time field  abs(550,268) 258×56  #ECECEC  radius=27 ────────────
        self._add_field_box(root, 550, 268, 258, 56)
        self._add_label_pill(root, 550, 268, 124, 56, "Time")
        self._time_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_VALUE,
            halign="left",
            valign="middle",
            size_hint=(_sw(120), _sh(56)),
            pos_hint={"x": _x(688), "y": _y(268, 56)},
            shorten=True,
            shorten_from="right",
        )
        self._time_lbl.bind(size=self._time_lbl.setter("text_size"))
        root.add_widget(self._time_lbl)

        # ── 8b. Duration field  abs(840,268) 366×56  #ECECEC  radius=27 ───────
        self._add_field_box(root, 840, 268, 366, 56)
        self._add_label_pill(root, 840, 268, 184, 56, "Duration")
        self._duration_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_VALUE,
            halign="left",
            valign="middle",
            size_hint=(_sw(160), _sh(56)),
            pos_hint={"x": _x(1038), "y": _y(268, 56)},
            shorten=True,
            shorten_from="right",
        )
        self._duration_lbl.bind(size=self._duration_lbl.setter("text_size"))
        root.add_widget(self._duration_lbl)

        # ── 9. Attendees container  abs(55,349) 1140×295  #ECECEC  radius=27 ──
        self._add_field_box(root, 55, 349, 1140, 295)
        self._add_label_pill(root, 55, 349, 228, 61, "Attendees")

        # Scrollbar track (decorative) — Figma #DADADA on the right edge
        track = Widget(
            size_hint=(_sw(12), _sh(265)),
            pos_hint={"x": _x(1169), "y": _y(364, 265)},
        )
        with track.canvas:
            Color(*_SCROLL_TRACK)
            _tr = RoundedRectangle(pos=track.pos, size=track.size, radius=[_ff(6)])
        track.bind(
            pos=lambda w, _: setattr(_tr, "pos",  w.pos),
            size=lambda w, _: setattr(_tr, "size", w.size),
        )
        root.add_widget(track)

        # Scrollable chip flow inside the container.  A leading spacer the size
        # of the "Attendees" label pill keeps the first chips clear of the label.
        att_scroll = ScrollView(
            size_hint=(_sw(1095), _sh(275)),
            pos_hint={"x": _x(75), "y": _y(359, 275)},
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=0,
            always_overscroll=False,
        )
        self._att_stack = StackLayout(
            orientation="lr-tb",
            size_hint=(1, None),
            spacing=[_ff(16), _ff(14)],
            padding=[0, 0, 0, _ff(8)],
        )
        self._att_stack.bind(minimum_height=self._att_stack.setter("height"))
        # Leading spacer: label pill width (228) minus the scroll left inset (20)
        # plus a small gap so chips begin to the right of the "Attendees" label.
        self._att_spacer = Widget(
            size_hint=(None, None),
            width=_ff(228 - 20 + 18),
            height=_ff(56),
        )
        self._att_stack.add_widget(self._att_spacer)
        att_scroll.add_widget(self._att_stack)
        root.add_widget(att_scroll)

        # ── 10. Discard button  abs(363,702) 236×66  #ED5B77 ──────────────────
        self._discard_btn = _PillButton(
            "Discard", _BTN_DISCARD, self._tap_discard,
            size_hint=(_sw(236), _sh(66)),
            pos_hint={"x": _x(363), "y": _y(702, 66)},
        )
        root.add_widget(self._discard_btn)

        # ── 11. Confirm button  abs(661,702) 236×66  #10C76D ──────────────────
        self._confirm_btn = _PillButton(
            "Confirm", _BTN_CONFIRM, self._tap_confirm,
            size_hint=(_sw(236), _sh(66)),
            pos_hint={"x": _x(661), "y": _y(702, 66)},
        )
        root.add_widget(self._confirm_btn)

        # (WiFi + battery indicators removed — not relevant for the desktop app.)

        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) for consistent size/position/font.
        # self._voice_pill stays None here on purpose; existing
        # `if self._voice_pill:` guards below no-op safely.

        self.add_widget(root)

    # ── Small builders ──────────────────────────────────────────────────────

    def _add_field_box(self, root, x, top, w, h) -> None:
        box = Widget(
            size_hint=(_sw(w), _sh(h)),
            pos_hint={"x": _x(x), "y": _y(top, h)},
        )
        with box.canvas:
            Color(*_FIELD_BG)
            rect = RoundedRectangle(pos=box.pos, size=box.size, radius=[_ff(27)])
        box.bind(
            pos=lambda wdg, _: setattr(rect, "pos",  wdg.pos),
            size=lambda wdg, _: setattr(rect, "size", wdg.size),
        )
        root.add_widget(box)

    def _add_label_pill(self, root, x, top, w, h, text) -> None:
        pill = Widget(
            size_hint=(_sw(w), _sh(h)),
            pos_hint={"x": _x(x), "y": _y(top, h)},
        )
        with pill.canvas:
            Color(*_LABEL_BG)
            rect = RoundedRectangle(pos=pill.pos, size=pill.size, radius=[_ff(27)])
        pill.bind(
            pos=lambda wdg, _: setattr(rect, "pos",  wdg.pos),
            size=lambda wdg, _: setattr(rect, "size", wdg.size),
        )
        root.add_widget(pill)
        root.add_widget(Label(
            text=text,
            font_name=_FONT_SB,
            font_size=_ff(36),
            color=_WHITE,
            halign="center",
            valign="middle",
            size_hint=(_sw(w), _sh(h)),
            pos_hint={"x": _x(x), "y": _y(top, h)},
        ))

    # ── Public API ────────────────────────────────────────────────────────────

    def set_event_data(
        self,
        name: str | None = None,
        date: str | None = None,
        time_str: str | None = None,
        duration_minutes: int | str | None = None,
        attendees: list | None = None,
        attendees_mode: str = "replace",
    ) -> None:
        """Progressive merge update from a ``show_calendar_event`` directive.

        Only the keys provided are updated — missing fields keep their value.
        """
        if name is not None and self._name_lbl is not None:
            self._name_lbl.text = name.strip()

        if date is not None and self._date_lbl is not None:
            self._date_lbl.text = self._fmt_date(date)

        if time_str is not None and self._time_lbl is not None:
            self._time_lbl.text = (time_str or "").strip()

        if duration_minutes is not None and self._duration_lbl is not None:
            self._duration_lbl.text = self._fmt_duration(duration_minutes)

        if attendees is not None:
            if str(attendees_mode or "replace").strip().lower() == "append":
                for item in attendees:
                    self.add_attendee(str(item))
            else:
                self.set_attendees(attendees)

    def add_attendee(self, label: str) -> None:
        """Append a single attendee chip (e.g. after contact selection)."""
        label = (label or "").strip()
        if not label or label in self._attendees:
            return
        self._attendees.append(label)
        self._render_attendees()

    def remove_attendee(self, label: str) -> bool:
        """Remove a single attendee chip if present."""
        key = (label or "").strip().lower()
        if not key:
            return False
        for idx, item in enumerate(list(self._attendees)):
            if item.strip().lower() == key:
                self._attendees.pop(idx)
                self._render_attendees()
                return True
        return False

    def set_attendees(self, labels: list) -> None:
        """Replace the attendee chip list."""
        self._attendees = [str(x).strip() for x in (labels or []) if str(x).strip()]
        self._render_attendees()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_date(date: str | None) -> str:
        s = (date or "").strip()
        if not s:
            return ""
        try:
            from datetime import datetime
            dt = datetime.strptime(s, "%Y-%m-%d")
            return dt.strftime("%A, %b %d")
        except ValueError:
            return s

    @staticmethod
    def _fmt_duration(duration_minutes: int | str | None) -> str:
        raw = str(duration_minutes or "").strip()
        if not raw:
            return ""
        try:
            mins = max(0, int(raw))
            if mins <= 0:
                return ""
            return f"{mins} min"
        except ValueError:
            return raw

    def _render_attendees(self) -> None:
        if self._att_stack is None:
            return
        self._att_stack.clear_widgets()
        if self._att_spacer is not None:
            self._att_stack.add_widget(self._att_spacer)
        for label in self._attendees:
            self._att_stack.add_widget(_AttendeeChip(label))

    def reset(self) -> None:
        """Reset to a blank event draft (fresh workflow)."""
        if self._name_lbl is not None:
            self._name_lbl.text = ""
        if self._date_lbl is not None:
            self._date_lbl.text = ""
        if self._time_lbl is not None:
            self._time_lbl.text = ""
        if self._duration_lbl is not None:
            self._duration_lbl.text = ""
        self._attendees = []
        self._render_attendees()

    # ── Button handlers ───────────────────────────────────────────────────────

    def _tap_confirm(self) -> None:
        if self._on_confirm_cb:
            self._on_confirm_cb()

    def _tap_discard(self) -> None:
        if self._on_discard_cb:
            self._on_discard_cb()

    # ── Genie action animation hooks (driven by main.py) ──────────────────────

    def _action_btn(self, action: str):
        # "send" == Confirm (create the event).
        return {"send": self._confirm_btn, "discard": self._discard_btn}.get(action)

    def flash_button(self, action: str) -> None:
        btn = self._action_btn(action)
        if btn is None:
            return
        Animation.cancel_all(btn, "opacity")
        (Animation(opacity=0.45, duration=0.08, t="out_quad")
         + Animation(opacity=1.0, duration=0.08, t="in_quad")).start(btn)

    def prepare_genie(self, action: str) -> None:
        keep = self._action_btn(action) if action == "discard" else None
        for b in (self._confirm_btn, self._discard_btn):
            if b is None or b is keep:
                continue
            Animation.cancel_all(b, "opacity")
            Animation(opacity=0.0, duration=0.4, t="out_quad").start(b)

    def restore_action_visuals(self) -> None:
        if self._card is not None:
            try:
                from components.action_flyaway import _cleanup_minimize
                _cleanup_minimize(self._card)
            except Exception:
                pass
            self._card.opacity = 1
        for b in (self._confirm_btn, self._discard_btn):
            if b is not None:
                Animation.cancel_all(b, "opacity")
                b.opacity = 1

    def genie_snapshot(self):
        """Texture + window-rect for the genie to fly.

        The card's visible content (header, fields, text) lives as *siblings* of
        the white card panel rather than as its children, so exporting the panel
        alone yields a blank block. Instead export the whole screen and crop the
        texture to the card's rectangle, giving the exact pixels the user sees.
        Returns ``(texture, (x, y, w, h))`` or ``None`` on failure.
        """
        card = self._card
        if card is None:
            return None
        try:
            w, h = float(card.width), float(card.height)
            if w <= 0 or h <= 0:
                return None
            core = self.export_as_image()
            full = getattr(core, "texture", None)
            if full is None:
                return None
            x, y = card.to_window(card.x, card.y)
            # ``export_as_image`` is FBO-backed (top-left origin) while
            # ``to_window`` yields bottom-left window coords, so flip Y to crop
            # the right rows — otherwise the crop slides down into the button
            # row and clips the header off the top.
            ty = full.height - y - h
            region = full.get_region(int(round(x)), int(round(ty)),
                                     int(round(w)), int(round(h)))
            return region, (float(x), float(y), w, h)
        except Exception:
            logger.debug("calendar genie_snapshot failed", exc_info=True)
            return None

    def genie_target(self, action: str):
        """Top-right corner for Confirm; the Discard CTA otherwise."""
        if action == "send":
            return (float(Window.width), float(Window.height))
        btn = self._action_btn(action)
        if btn is not None:
            return tuple(btn.to_window(btn.center_x, btn.center_y))
        return (float(Window.width), float(Window.height))

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_enter(self) -> None:
        self._listening = False
        self._amplitude = 0.0
        # Fresh event review → allow the fly-away to play again.
        self._flyaway_committed = False
        self.restore_action_visuals()
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
            self._voice_pill.opacity = 1.0

    def on_leave(self) -> None:
        self._stop_voice_tick()
        self._listening = False
        self._amplitude = 0.0

    # ── Voice-state API  (same signature as voice_session.py) ─────────────────

    def show_listening_state(self) -> None:
        self._listening = True
        self._amplitude = 0.0
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
            self._voice_pill.opacity = 1.0
        self._start_voice_tick()

    def set_voice_session_state(self, state: str) -> None:
        if state == "listening":
            self.show_listening_state()
        elif state == "thinking":
            self._listening = False
            if self._voice_pill:
                self._voice_pill.set_state_text("Thinking")
                self._voice_pill.opacity = 1.0
            self._stop_voice_tick()
        elif state == "speaking":
            self._listening = False
            if self._voice_pill:
                self._voice_pill.set_state_text("Talking")
                self._voice_pill.opacity = 1.0
            self._stop_voice_tick()
        # "idle" — stay on screen; navigation is handled by main.py callbacks

    def update_amplitude(self, amp: float) -> None:
        if self._listening:
            self._amplitude = amp

    # ── Voice-state tick  (mirrors voice_session.py) ─────────────────────────

    def _start_voice_tick(self) -> None:
        if self._voice_tick_ev is None and self._voice_pill is not None:
            self._voice_tick_ev = Clock.schedule_interval(self._voice_tick, 1 / 30)

    def _stop_voice_tick(self) -> None:
        if self._voice_tick_ev is not None:
            self._voice_tick_ev.cancel()
            self._voice_tick_ev = None
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), 0.0)

    def _voice_tick(self, _dt) -> None:
        if self._voice_pill is not None:
            self._voice_pill.update_bars(time.monotonic(), self._amplitude)
