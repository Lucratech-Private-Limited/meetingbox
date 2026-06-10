"""Email Drafting Screen — Figma System States_3, _6, _7 (1260 × 800 px).

Shown when the voice agent emits a ``show_email_draft`` directive. The page
reuses the same background / chrome as ``voice_session.py`` and adds a white
email-compose card in the centre.

Layout (Figma 1260 × 800 baseline):
  • Full-bleed vs_bg.png  +  rgba(255,255,255,0.45) white overlay
  • Top-right: Listening/Thinking/Talking pill + WiFi + battery cluster
  • Card "Frame 22": x=22  y=80  1216×567  rgba(255,255,255,0.9)  radius=38
      ┌──────────────────────────────────────────────────┐
      │  To :    [ chip ]  [ chip ]  …                   │
      │ ─────────────────────────────────────────────    │
      │  Cc :    [ chip ]  …           (hidden when empty)│
      │ ─────────────────────────────────────────────    │
      │  Subject: [ chip ]                               │
      │ ─────────────────────────────────────────────    │
      │  <scrollable body text>                          │
      └──────────────────────────────────────────────────┘
  • Three action buttons below the card (y=688, h=60, radius=50):
        Discard  x=272  w=176  #ED5B77
        Save as Draft  x=497  w=265  #4DA6DE
        Send  x=812  w=175  #10C76D

Public API (called by main.py):
    set_draft(data)                – merge update; safe to call repeatedly
    reset()                        – clear all fields
    show_listening_state()         – forward voice state → pill
    set_voice_session_state(state) – forward state → pill
    update_amplitude(amp)          – forward amplitude → pill waveform
"""

from __future__ import annotations

import logging
import math
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
# Colours (Figma fills)
# ──────────────────────────────────────────────────────────────────────────────
_C_LABEL    = (0.427, 0.282, 0.800, 1.0)   # #6D48CC  To/Cc/Subject labels
_C_SEP      = (0.620, 0.620, 0.620, 1.0)   # #9E9E9E  separator lines
_C_CHIP_BG  = (0.925, 0.925, 0.925, 1.0)   # #ECECEC  chip background
_C_CHIP_TXT = (0.208, 0.224, 0.231, 1.0)   # #35393B  chip text
_C_BODY_TXT = (0.184, 0.184, 0.184, 1.0)   # #2F2F2F  body text
_C_PH_TXT   = (0.620, 0.620, 0.620, 0.70)  # placeholder text

_C_BTN_DISCARD = (0.929, 0.357, 0.467, 1.0)  # #ED5B77
_C_BTN_SAVE    = (0.302, 0.651, 0.871, 1.0)  # #4DA6DE
_C_BTN_SEND    = (0.063, 0.780, 0.427, 1.0)  # #10C76D

_FONT_SB = "42dot-SB"
_FONT_MD = "42dot-Med"


# ──────────────────────────────────────────────────────────────────────────────
# WiFi icon  (reused verbatim from voice_session.py)
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
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_recipients(items) -> list[str]:
    """Return a list of display strings from a directive recipients value."""
    out: list[str] = []
    for it in items or []:
        if isinstance(it, dict):
            name  = (it.get("name") or "").strip()
            email = (it.get("email") or "").strip()
            out.append(f"{name} <{email}>" if (name and email) else email or name)
        else:
            s = str(it or "").strip()
            if s:
                out.append(s)
    return [p for p in out if p]


# ──────────────────────────────────────────────────────────────────────────────
# Recipient chip widget
# ──────────────────────────────────────────────────────────────────────────────
class _Chip(BoxLayout):
    """A pill-shaped label displaying one email / name."""

    def __init__(self, text: str, **kw):
        super().__init__(
            orientation="horizontal",
            size_hint=(None, None),
            height=_ff(58),
            padding=[_ff(30), _ff(10)],
            **kw,
        )
        with self.canvas.before:
            Color(*_C_CHIP_BG)
            self._bg = RoundedRectangle(radius=[_ff(36)])
        self.bind(pos=self._sync, size=self._sync)

        lbl = Label(
            text=text,
            font_name=_FONT_MD,
            font_size=_ff(32),
            color=_C_CHIP_TXT,
            halign="center",
            valign="middle",
            size_hint=(None, 1),
        )
        lbl.bind(texture_size=lambda l, ts: setattr(l, "width", ts[0]))
        self.add_widget(lbl)
        Clock.schedule_once(lambda _dt: self._update_width(), 0)

    def _update_width(self):
        # size the chip to its content + padding
        for child in self.children:
            if isinstance(child, Label):
                self.width = child.width + _ff(60)
                break

    def _sync(self, *_):
        self._bg.pos  = self.pos
        self._bg.size = self.size


# ──────────────────────────────────────────────────────────────────────────────
# A single field row inside the card
# ──────────────────────────────────────────────────────────────────────────────
class _FieldRow(BoxLayout):
    """Horizontal row: label + horizontally scrollable chip area."""

    _ROW_H = _ff(82)

    def __init__(self, label_text: str, label_width: float | None = None, **kw):
        super().__init__(
            orientation="horizontal",
            size_hint=(1, None),
            height=self._ROW_H,
            padding=[_ff(25), 0],
            spacing=_ff(16),
            **kw,
        )
        self._lbl = Label(
            text=label_text,
            font_name=_FONT_SB,
            font_size=_ff(35),
            color=_C_LABEL,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=label_width if label_width is not None else _ff(120),
        )
        # Keep the label on a single line (only constrain wrap height, not width)
        # so longer labels like "Subject:" don't break across two lines.
        self._lbl.bind(
            height=lambda l, h: setattr(l, "text_size", (l.width, h)),
            width=lambda l, w: setattr(l, "text_size", (w, l.height)),
        )
        self.add_widget(self._lbl)

        # Chip container scrolls horizontally if many chips
        self._chip_box = BoxLayout(
            orientation="horizontal",
            size_hint=(None, 1),
            spacing=_ff(12),
            padding=[0, _ff(12), 0, _ff(12)],
        )
        self._chip_box.bind(minimum_width=self._chip_box.setter("width"))

        self._scroll = ScrollView(
            do_scroll_x=True,
            do_scroll_y=False,
            bar_width=0,
            size_hint=(1, 1),
        )
        self._scroll.add_widget(self._chip_box)
        self.add_widget(self._scroll)

        # Placeholder shown when no chips
        self._placeholder = Label(
            text="…",
            font_name=_FONT_MD,
            font_size=_ff(32),
            color=_C_PH_TXT,
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        )
        self._placeholder.bind(size=self._placeholder.setter("text_size"))
        self._chip_box.add_widget(self._placeholder)

    def set_chips(self, texts: list[str]) -> None:
        self._chip_box.clear_widgets()
        if not texts:
            self._chip_box.add_widget(self._placeholder)
            return
        for t in texts:
            self._chip_box.add_widget(_Chip(t))


# ──────────────────────────────────────────────────────────────────────────────
# Separator line
# ──────────────────────────────────────────────────────────────────────────────
class _Sep(Widget):
    def __init__(self, **kw):
        kw.setdefault("size_hint", (1, None))
        kw.setdefault("height", 1)
        super().__init__(**kw)
        with self.canvas:
            Color(*_C_SEP)
            self._r = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda *_: setattr(self._r, "pos", self.pos),
            size=lambda *_: setattr(self._r, "size", self.size),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Action pill button
# ──────────────────────────────────────────────────────────────────────────────
class _PillButton(BoxLayout):
    def __init__(self, text: str, bg_color: tuple, on_tap, **kw):
        kw.setdefault("size_hint", (None, None))
        super().__init__(**kw)
        self._on_tap = on_tap
        self._enabled = True
        with self.canvas.before:
            # shadow
            Color(0, 0, 0, 0.25)
            self._shad = RoundedRectangle(radius=[_ff(50)])
            Color(*bg_color)
            self._bg = RoundedRectangle(radius=[_ff(50)])
        self.bind(pos=self._sync, size=self._sync)
        lbl = Label(
            text=text,
            font_name=_FONT_SB,
            font_size=_ff(35),
            bold=False,
            color=(1, 1, 1, 1),
            halign="center",
            valign="middle",
        )
        lbl.bind(size=lbl.setter("text_size"))
        self.add_widget(lbl)

    def _sync(self, *_):
        self._shad.pos  = (self.x + 1, self.y - 4)
        self._shad.size = (self.width + 2, self.height + 5)
        self._bg.pos    = self.pos
        self._bg.size   = self.size

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._bg.group  # keep reference
        for instr in self.canvas.before.children:
            if isinstance(instr, Color) and instr is not None:
                pass
        # dim the button by changing its label alpha
        for child in self.children:
            if isinstance(child, Label):
                child.color = (1, 1, 1, 1.0 if enabled else 0.4)

    def on_touch_down(self, touch):
        if self._enabled and self.collide_point(*touch.pos):
            if self._on_tap:
                self._on_tap()
            return True
        return super().on_touch_down(touch)


# ──────────────────────────────────────────────────────────────────────────────
# EmailDraftScreen
# ──────────────────────────────────────────────────────────────────────────────
class EmailDraftScreen(BaseScreen):
    """Full-screen email draft review, Figma System States_3/_6/_7.

    Navigated to when the voice agent first emits ``show_email_draft``.
    Returns to ``home`` on terminal states (sent / saved / discarded) or when
    the voice session ends.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._voice_pill: _VoiceStatePill | None = None
        self._battery:    _BatteryWidget  | None = None
        self._listening   = False
        self._amplitude   = 0.0
        self._voice_tick_ev: object | None = None
        self._auto_back_ev:  object | None = None
        # Lifecycle state of the current draft (drafting/ready/sending/sent/saved/discarded).
        self._state = "drafting"

        # Draft state store — missing keys are preserved across updates
        self._fields: dict = {
            "to": [], "cc": [], "bcc": [], "subject": "", "body": "",
        }

        # Callbacks set by main.py
        self.on_send       = None
        self.on_save_draft = None
        self.on_discard    = None

        self._row_to:      _FieldRow | None = None
        self._row_cc:      _FieldRow | None = None
        self._sep_cc:      _Sep | None = None
        self._row_subject: _FieldRow | None = None
        self._body_label:  Label | None = None
        self._send_btn:    _PillButton | None = None
        self._save_btn:    _PillButton | None = None
        self._discard_btn: _PillButton | None = None

        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout()

        # 1 · Background image (vs_bg.png)
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

        # 2 · Semi-transparent white overlay  rgba(255,255,255,0.45)
        ov = Widget(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        with ov.canvas:
            _ovc = Color(1, 1, 1, 0.45)  # noqa: F841
            _ovr = Rectangle(pos=ov.pos, size=ov.size)
        ov.bind(
            pos=lambda w, p: setattr(_ovr, "pos", p),
            size=lambda w, s: setattr(_ovr, "size", s),
        )
        root.add_widget(ov)

        # 3 · Email card  Frame 22: x=22 y=80  1216×567
        card = BoxLayout(
            orientation="vertical",
            size_hint=(_sw(1216), _sh(567)),
            pos_hint={"x": _x(22), "y": _y(80, 567)},
            padding=[0, 0, 0, 0],
            spacing=0,
        )
        with card.canvas.before:
            # shadow
            Color(0, 0, 0, 0.12)
            _csh = RoundedRectangle(radius=[_ff(38)])
            # fill rgba(255,255,255,0.9)
            Color(1, 1, 1, 0.9)
            _cbg = RoundedRectangle(radius=[_ff(38)])
        def _sync_card(*_):
            _csh.pos  = (card.x + 1, card.y - 6)
            _csh.size = (card.width, card.height + 5)
            _cbg.pos  = card.pos
            _cbg.size = card.size
        card.bind(pos=_sync_card, size=_sync_card)

        # — inner content box with padding —
        inner = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            padding=[_ff(25), _ff(16), _ff(25), _ff(16)],
            spacing=0,
        )

        # To row
        self._row_to = _FieldRow("To:")
        inner.add_widget(self._row_to)
        inner.add_widget(_Sep())

        # Cc row (initially hidden)
        self._row_cc  = _FieldRow("Cc:")
        self._sep_cc  = _Sep()
        self._row_cc.height  = 0
        self._row_cc.opacity = 0
        self._sep_cc.height  = 0
        self._sep_cc.opacity = 0
        inner.add_widget(self._row_cc)
        inner.add_widget(self._sep_cc)

        # Subject row — wider label so "Subject:" stays on one line
        self._row_subject = _FieldRow("Subject:", label_width=_ff(210))
        inner.add_widget(self._row_subject)
        inner.add_widget(_Sep())

        # Body — scrollable
        body_scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=_ff(4),
            bar_color=(0.427, 0.282, 0.8, 0.7),
            bar_inactive_color=(0.6, 0.6, 0.6, 0.4),
            size_hint=(1, 1),
        )
        self._body_label = Label(
            text="",
            font_name=_FONT_MD,
            font_size=_ff(30),
            color=_C_PH_TXT,
            halign="left",
            valign="top",
            size_hint=(1, None),
            padding=(_ff(8), _ff(16)),
        )
        self._body_label.bind(
            width=lambda lbl, w: setattr(lbl, "text_size", (w - _ff(16), None)),
            texture_size=lambda lbl, ts: setattr(lbl, "height", ts[1] + _ff(32)),
        )
        body_scroll.add_widget(self._body_label)
        inner.add_widget(body_scroll)

        card.add_widget(inner)
        root.add_widget(card)

        # 4 · Action buttons below card  (y=688, h=60)
        btn_y = _y(688, 60)
        self._discard_btn = _PillButton(
            "Discard", _C_BTN_DISCARD, self._tap_discard,
            size_hint=(_sw(176), _sh(60)),
            pos_hint={"x": _x(272), "y": btn_y},
        )
        self._save_btn = _PillButton(
            "Save as Draft", _C_BTN_SAVE, self._tap_save,
            size_hint=(_sw(265), _sh(60)),
            pos_hint={"x": _x(497), "y": btn_y},
        )
        self._send_btn = _PillButton(
            "Send", _C_BTN_SEND, self._tap_send,
            size_hint=(_sw(175), _sh(60)),
            pos_hint={"x": _x(812), "y": btn_y},
        )
        root.add_widget(self._discard_btn)
        root.add_widget(self._save_btn)
        root.add_widget(self._send_btn)

        # 5 · Voice-state pill  (910, 17)  222 × 47
        self._voice_pill = _VoiceStatePill(
            size_hint=(_sw(222), _sh(47)),
            pos_hint={"x": _x(910), "y": _y(17, 47)},
        )
        self._voice_pill.opacity = 1.0
        root.add_widget(self._voice_pill)

        # 6 · WiFi icon  (1147, 31)  29 × 20
        root.add_widget(_WifiIcon(
            size_hint=(_sw(29), _sh(20)),
            pos_hint={"x": _x(1147), "y": _y(31, 20)},
        ))

        # 7 · Battery  (1191, 30)  47 × 21
        self._battery = _BatteryWidget(
            size_hint=(_sw(47), _sh(21)),
            pos_hint={"x": _x(1191), "y": _y(30, 21)},
        )
        root.add_widget(self._battery)

        self.add_widget(root)

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

    # ── Public API ────────────────────────────────────────────────────────────

    def set_draft(self, data: dict) -> None:
        """Merge update from ``show_email_draft`` directive (progressive fill).

        Missing keys keep their previous value — the backend drives each field
        one at a time and we never wipe things that were already set.
        """
        if not isinstance(data, dict):
            return

        # Recipients: list or string acceptable
        for key in ("to", "cc", "bcc"):
            if key in data:
                raw = data[key]
                if isinstance(raw, list):
                    self._fields[key] = _parse_recipients(raw)
                elif isinstance(raw, str) and raw.strip():
                    self._fields[key] = [raw.strip()]
                else:
                    self._fields[key] = []

        if "subject" in data:
            self._fields["subject"] = str(data.get("subject") or "").strip()
        if "body" in data:
            self._fields["body"] = str(data.get("body") or "")

        self._refresh_ui()

        state = str(data.get("state") or "drafting").strip().lower()
        self._apply_state(state)

    def reset(self) -> None:
        """Clear all draft fields and return to blank state."""
        self._cancel_auto_back()
        self._state = "drafting"
        self._fields = {"to": [], "cc": [], "bcc": [], "subject": "", "body": ""}
        self._refresh_ui()
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
        self._set_buttons_enabled(True)

    # ── Voice-state API (mirrors VoiceSessionScreen / HomeScreen) ─────────────

    def show_listening_state(self) -> None:
        self._listening = True
        self._amplitude = 0.0
        if self._voice_pill:
            self._voice_pill.set_state_text("Listening")
            self._voice_pill.opacity = 1.0
        self._start_voice_tick()

    def hide_listening_state(self) -> None:
        self._listening = False
        self._stop_voice_tick()

    def set_voice_session_state(self, state: str) -> None:
        lbl = {"listening": "Listening", "thinking": "Thinking", "speaking": "Talking"}.get(state)
        if lbl and self._voice_pill:
            self._voice_pill.set_state_text(lbl)
            self._voice_pill.opacity = 1.0
        if state == "listening":
            self._listening = True
            self._start_voice_tick()
        else:
            self._listening = False
            self._stop_voice_tick()

    def update_amplitude(self, amp: float) -> None:
        if self._listening:
            self._amplitude = amp

    # ── Internal UI refresh ───────────────────────────────────────────────────

    def _refresh_ui(self) -> None:
        if self._row_to:
            self._row_to.set_chips(self._fields["to"])

        cc = self._fields["cc"]
        if self._row_cc and self._sep_cc:
            if cc:
                self._row_cc.height  = _FieldRow._ROW_H
                self._row_cc.opacity = 1
                self._sep_cc.height  = 1
                self._sep_cc.opacity = 1
                self._row_cc.set_chips(cc)
            else:
                self._row_cc.height  = 0
                self._row_cc.opacity = 0
                self._sep_cc.height  = 0
                self._sep_cc.opacity = 0

        if self._row_subject:
            subj = self._fields["subject"]
            self._row_subject.set_chips([subj] if subj else [])

        if self._body_label:
            body = self._fields["body"]
            if body:
                self._body_label.text  = body
                self._body_label.color = _C_BODY_TXT
            else:
                self._body_label.text  = "Drafting…"
                self._body_label.color = _C_PH_TXT

    @property
    def draft_is_terminal(self) -> bool:
        """True once the draft has been sent, saved, or discarded."""
        return self._state in ("sent", "saved", "discarded")

    def _apply_state(self, state: str) -> None:
        self._state = state
        terminal = {
            "sent":      "Sent ✓",
            "saved":     "Saved to drafts",
            "discarded": "Discarded",
        }
        if state in terminal:
            if self._voice_pill:
                self._voice_pill.set_state_text(terminal[state])
            self._set_buttons_enabled(False)
            self._schedule_auto_back()
        elif state == "ready":
            self._set_buttons_enabled(True)
        elif state == "sending":
            self._set_buttons_enabled(False)
        # "drafting" → buttons remain enabled, pill shows Listening

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self._send_btn, self._save_btn, self._discard_btn):
            if btn:
                btn.set_enabled(enabled)

    # ── Auto-return to home on terminal state ─────────────────────────────────

    def _schedule_auto_back(self) -> None:
        self._cancel_auto_back()
        self._auto_back_ev = Clock.schedule_once(self._go_home, 2.6)

    def _cancel_auto_back(self) -> None:
        if self._auto_back_ev is not None:
            self._auto_back_ev.cancel()
            self._auto_back_ev = None

    def _go_home(self, _dt=None) -> None:
        self._cancel_auto_back()
        try:
            app = self.app
            sm = app.screen_manager if app else None
            if sm and sm.current == "email_draft":
                # The voice session is still live — return to the audio-agent
                # screen (not home) so the user sees the send/save confirmation
                # in the transcript. Home happens only when the session ends.
                target = "voice_session" if sm.has_screen("voice_session") else "home"
                app.goto_screen(target)
        except Exception:
            pass

    # ── Button tap handlers ───────────────────────────────────────────────────

    def _tap_send(self) -> None:
        if self.on_send:
            self.on_send()

    def _tap_save(self) -> None:
        if self.on_save_draft:
            self.on_save_draft()

    def _tap_discard(self) -> None:
        if self.on_discard:
            self.on_discard()

    # ── Voice waveform tick ───────────────────────────────────────────────────

    def _start_voice_tick(self) -> None:
        if self._voice_tick_ev is None:
            self._voice_tick_ev = Clock.schedule_interval(self._tick, 1 / 30)

    def _stop_voice_tick(self) -> None:
        if self._voice_tick_ev:
            self._voice_tick_ev.cancel()
            self._voice_tick_ev = None

    def _tick(self, dt: float) -> None:
        if self._voice_pill:
            t = time.monotonic()
            self._voice_pill.update_bars(t, self._amplitude)
