"""Voice task creation screen — Figma node 3040:23 (1260 × 800 px).

Shown when the voice agent calls ``show_task_creation``. Sits on top of the
voice-session transcription screen while the Realtime session is still live.
The user reviews the pre-filled task and taps Confirm (fires ``on_confirm``)
or Discard (fires ``on_discard``).

Layout (Figma 1260 × 800 baseline):
  • Full-bleed vs_bg.png  +  rgba(255,255,255,0.45) white overlay
  • Top-right: Listening/Thinking/Talking pill (851,17) 222×47 + WiFi + battery
  • Card "Frame 22": (27,82) 1206×511  #FDFDFD  radius=38
      ┌────────────────────────────────────────────────────────┐
      │  [task icon]  Create Task            [purple #6D48CC]   │ 87 px
      ├────────────────────────────────────────────────────────┤
      │  [ Task ] ← grey pill                                   │
      │  <scrollable task text, max ~3 lines visible>           │
      │                                                         │
      │  [ Date ] ← grey pill    Sunday, Jun 14                 │
      └────────────────────────────────────────────────────────┘
  • Discard (363,702) 236×66  #ED5B77  radius=50
  • Confirm (661,702) 236×66  #10C76D  radius=50

Public API (called by main.py):
    set_task_data(title, description, due_date)  – populate fields
    show_listening_state()                        – forward voice state → pill
    set_voice_session_state(state)                – forward state → pill
    update_amplitude(amp)                         – forward amplitude → pill waveform
"""
from __future__ import annotations

import logging
import time

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen
from screens.home import _BatteryWidget, _VoiceStatePill  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Figma frame constants  (1260 × 800 px baseline)
# ──────────────────────────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0
_VS_DIR   = ASSETS_DIR / "voice-session" / "figma"

# Colours (from Figma node 3040:23)
_PURPLE      = (109/255, 72/255, 204/255, 1.0)   # #6D48CC  header bar + pill dot
_WHITE       = (1.0, 1.0, 1.0, 1.0)
_CARD_BG     = (253/255, 253/255, 253/255, 1.0)  # #FDFDFD  main card
_LABEL_BG    = (158/255, 157/255, 162/255, 1.0)  # #9E9DA2  "Task" / "Date" pill bg
_FIELD_BG    = (236/255, 236/255, 236/255, 1.0)  # #ECECEC  text area background
_TEXT_DARK   = (0.0, 0.0, 0.0, 1.0)              # task content text
_TEXT_MUTED  = (0.50, 0.50, 0.52, 1.0)           # "Unplanned" placeholder text
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
# WiFi icon  (reused verbatim from voice_session.py / email_draft.py)
# ──────────────────────────────────────────────────────────────────────────────
class _WifiIcon(Widget):
    _COL = (0.0, 0.0, 0.0, 1.0)

    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas:
            self._c    = Color(*self._COL)
            self._arc1 = Line(width=1.4)
            self._arc2 = Line(width=1.4)
            self._arc3 = Line(width=1.4)
            self._dotc = Color(*self._COL)
            self._dot  = Ellipse()
        self.bind(pos=self._redraw, size=self._redraw)
        Clock.schedule_once(self._redraw, 0)

    def _redraw(self, *_) -> None:
        w, h = self.size
        if w <= 1 or h <= 1:
            return
        cx = self.x + w / 2
        cy = self.y + h * 0.08
        for arc, frac in [(self._arc1, 0.30), (self._arc2, 0.58), (self._arc3, 0.86)]:
            r = h * frac
            arc.ellipse = (cx - r, cy - r, 2 * r, 2 * r, 45, 135)
        dr = h * 0.09
        self._dot.pos  = (cx - dr, cy - dr)
        self._dot.size = (dr * 2, dr * 2)


# ──────────────────────────────────────────────────────────────────────────────
# Pill button  (adapted from email_draft._PillButton for 66 px height)
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
            for _, alpha in _S:
                Color(0, 0, 0, alpha)
                self._shadow_layers.append(RoundedRectangle(radius=[self._pill_r]))
            Color(*bg_color)
            self._bg = RoundedRectangle(radius=[self._pill_r])
        self._shadow_spec = _S
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
        for layer, (half_e, _) in zip(self._shadow_layers, self._shadow_spec):
            layer.pos  = (self.x - half_e, self.y - 4 - half_e)
            layer.size = (self.width + 2 * half_e, self.height + 2 * half_e)
        self._bg.pos  = self.pos
        self._bg.size = self.size

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            if self._on_tap:
                self._on_tap()
            return True
        return super().on_touch_down(touch)


# ──────────────────────────────────────────────────────────────────────────────
# VoiceTaskCreationScreen
# ──────────────────────────────────────────────────────────────────────────────
class VoiceTaskCreationScreen(BaseScreen):
    """Task confirmation screen shown before a voice-initiated task is saved.

    The voice agent calls ``show_task_creation`` → the device navigates here
    with pre-filled title / due date.  The user taps Confirm or Discard.
    """

    def __init__(self, on_confirm=None, on_discard=None, **kw):
        self._on_confirm_cb  = on_confirm
        self._on_discard_cb  = on_discard
        self._voice_pill:    _VoiceStatePill | None = None
        self._battery:       _BatteryWidget  | None = None
        self._task_lbl:      Label | None = None
        self._date_lbl:      Label | None = None
        self._listening:     bool  = False
        self._amplitude:     float = 0.0
        self._voice_tick_ev: object | None = None
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

        # ── 3. Main card  (27,82) 1206×511  #FDFDFD  radius=38 ───────────────
        card_kw = {
            "size_hint": (_sw(1206), _sh(511)),
            "pos_hint":  {"x": _x(27), "y": _y(82, 511)},
        }
        card = Widget(**card_kw)
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

        # ── 4. Purple header bar  abs(27,82) 1206×87  #6D48CC ─────────────────
        # Top corners match card radius; bottom corners are flat.
        hdr = Widget(
            size_hint=(_sw(1206), _sh(87)),
            pos_hint={"x": _x(27), "y": _y(82, 87)},
        )
        with hdr.canvas:
            Color(*_PURPLE)
            self._hdr_bg = RoundedRectangle(
                pos=hdr.pos, size=hdr.size,
                radius=[_ff(38)],
            )
        hdr.bind(
            pos=lambda w, _: setattr(self._hdr_bg, "pos",  w.pos),
            size=lambda w, _: setattr(self._hdr_bg, "size", w.size),
        )
        root.add_widget(hdr)

        # ── 5. "Create Task" heading text  abs(127,113) 174×38 ────────────────
        root.add_widget(Label(
            text="Create Task",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_WHITE,
            halign="left",
            valign="middle",
            size_hint=(_sw(174), _sh(38)),
            pos_hint={"x": _x(127), "y": _y(113, 38)},
        ))

        # ── 6. Task field box  abs(55,209) 1151×195  #ECECEC  radius=27 ───────
        task_box = Widget(
            size_hint=(_sw(1151), _sh(195)),
            pos_hint={"x": _x(55), "y": _y(209, 195)},
        )
        with task_box.canvas:
            Color(*_FIELD_BG)
            self._task_box_bg = RoundedRectangle(
                pos=task_box.pos, size=task_box.size, radius=[_ff(27)]
            )
        task_box.bind(
            pos=lambda w, _: setattr(self._task_box_bg, "pos",  w.pos),
            size=lambda w, _: setattr(self._task_box_bg, "size", w.size),
        )
        root.add_widget(task_box)

        # ── 6a. "Task" grey label pill  abs(40,209) 181×45  #9E9DA2 ──────────
        # Extends slightly left of the task box (Figma: x=-15 relative to box).
        task_pill_bg = Widget(
            size_hint=(_sw(181), _sh(45)),
            pos_hint={"x": _x(40), "y": _y(209, 45)},
        )
        with task_pill_bg.canvas:
            Color(*_LABEL_BG)
            self._task_pill_rect = RoundedRectangle(
                pos=task_pill_bg.pos, size=task_pill_bg.size,
                radius=[_ff(22)],
            )
        task_pill_bg.bind(
            pos=lambda w, _: setattr(self._task_pill_rect, "pos",  w.pos),
            size=lambda w, _: setattr(self._task_pill_rect, "size", w.size),
        )
        root.add_widget(task_pill_bg)
        root.add_widget(Label(
            text="Task",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_WHITE,
            halign="center",
            valign="middle",
            size_hint=(_sw(181), _sh(45)),
            pos_hint={"x": _x(40), "y": _y(209, 45)},
        ))

        # ── 6b. Scrollable task text  abs(120,265) 1022×114 ───────────────────
        # Scroll is hidden; up to ~3 lines shown; user can swipe to read more.
        task_scroll = ScrollView(
            size_hint=(_sw(1022), _sh(114)),
            pos_hint={"x": _x(120), "y": _y(265, 114)},
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=0,
            always_overscroll=False,
        )
        task_inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            padding=[0, _ff(2), 0, _ff(2)],
        )
        task_inner.bind(minimum_height=task_inner.setter("height"))

        self._task_lbl = Label(
            text="",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_DARK,
            halign="left",
            valign="top",
            size_hint_x=1,
            size_hint_y=None,
            height=_ff(40),
        )
        self._task_lbl.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, ts: setattr(w, "height", ts[1] + _ff(4)),
        )
        task_inner.add_widget(self._task_lbl)
        task_scroll.add_widget(task_inner)
        root.add_widget(task_scroll)

        # ── 7. Date field box  abs(55,462) 431×55  #ECECEC  radius=27 ─────────
        date_box = Widget(
            size_hint=(_sw(431), _sh(55)),
            pos_hint={"x": _x(55), "y": _y(462, 55)},
        )
        with date_box.canvas:
            Color(*_FIELD_BG)
            self._date_box_bg = RoundedRectangle(
                pos=date_box.pos, size=date_box.size, radius=[_ff(27)]
            )
        date_box.bind(
            pos=lambda w, _: setattr(self._date_box_bg, "pos",  w.pos),
            size=lambda w, _: setattr(self._date_box_bg, "size", w.size),
        )
        root.add_widget(date_box)

        # ── 7a. "Date" grey label pill  abs(55,462) 148×55  #9E9DA2 ──────────
        date_pill_bg = Widget(
            size_hint=(_sw(148), _sh(55)),
            pos_hint={"x": _x(55), "y": _y(462, 55)},
        )
        with date_pill_bg.canvas:
            Color(*_LABEL_BG)
            self._date_pill_rect = RoundedRectangle(
                pos=date_pill_bg.pos, size=date_pill_bg.size,
                radius=[_ff(27)],
            )
        date_pill_bg.bind(
            pos=lambda w, _: setattr(self._date_pill_rect, "pos",  w.pos),
            size=lambda w, _: setattr(self._date_pill_rect, "size", w.size),
        )
        root.add_widget(date_pill_bg)
        root.add_widget(Label(
            text="Date",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_WHITE,
            halign="center",
            valign="middle",
            size_hint=(_sw(148), _sh(55)),
            pos_hint={"x": _x(55), "y": _y(462, 55)},
        ))

        # ── 7b. Date value text  to the right of the pill ─────────────────────
        self._date_lbl = Label(
            text="Unplanned",
            font_name=_FONT_SB,
            font_size=_ff(32),
            color=_TEXT_MUTED,
            halign="left",
            valign="middle",
            size_hint=(_sw(283), _sh(55)),
            pos_hint={"x": _x(203), "y": _y(462, 55)},
        )
        root.add_widget(self._date_lbl)

        # ── 8. Discard button  abs(363,702) 236×66  #ED5B77 ──────────────────
        self._discard_btn = _PillButton(
            "Discard", _BTN_DISCARD, self._tap_discard,
            size_hint=(_sw(236), _sh(66)),
            pos_hint={"x": _x(363), "y": _y(702, 66)},
        )
        root.add_widget(self._discard_btn)

        # ── 9. Confirm button  abs(661,702) 236×66  #10C76D ──────────────────
        self._confirm_btn = _PillButton(
            "Confirm", _BTN_CONFIRM, self._tap_confirm,
            size_hint=(_sw(236), _sh(66)),
            pos_hint={"x": _x(661), "y": _y(702, 66)},
        )
        root.add_widget(self._confirm_btn)

        # ── 10. WiFi icon  abs(1109,31) 29×20 ────────────────────────────────
        root.add_widget(_WifiIcon(
            size_hint=(_sw(29), _sh(20)),
            pos_hint={"x": _x(1109), "y": _y(31, 20)},
        ))

        # ── 11. Battery indicator  abs(1175,30) 47×21 ─────────────────────────
        self._battery = _BatteryWidget(
            size_hint=(_sw(47), _sh(21)),
            pos_hint={"x": _x(1175), "y": _y(30, 21)},
        )
        root.add_widget(self._battery)

        # ── 12. Voice-state pill  abs(851,17) 222×47 ──────────────────────────
        self._voice_pill = _VoiceStatePill(
            size_hint=(_sw(222), _sh(47)),
            pos_hint={"x": _x(851), "y": _y(17, 47)},
        )
        self._voice_pill.opacity = 1.0
        root.add_widget(self._voice_pill)

        self.add_widget(root)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_task_data(
        self,
        title: str,
        description: str | None = None,
        due_date: str | None = None,
    ) -> None:
        """Populate the card with pre-filled voice data."""
        if self._task_lbl is not None:
            body = (title or "").strip()
            if description and description.strip():
                body = f"{body}\n{description.strip()}" if body else description.strip()
            self._task_lbl.text = body

        if self._date_lbl is not None:
            if due_date and due_date.strip():
                try:
                    from datetime import datetime
                    dt = datetime.strptime(due_date.strip(), "%Y-%m-%d")
                    self._date_lbl.text  = dt.strftime("%A, %b %-d")
                    self._date_lbl.color = _TEXT_DARK
                except ValueError:
                    self._date_lbl.text  = due_date.strip()
                    self._date_lbl.color = _TEXT_DARK
            else:
                self._date_lbl.text  = "Unplanned"
                self._date_lbl.color = _TEXT_MUTED

    # ── Button handlers ───────────────────────────────────────────────────────

    def _tap_confirm(self) -> None:
        if self._on_confirm_cb:
            self._on_confirm_cb()

    def _tap_discard(self) -> None:
        if self._on_discard_cb:
            self._on_discard_cb()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_enter(self) -> None:
        self._listening = False
        self._amplitude = 0.0
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
