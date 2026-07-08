"""Email view screen — single email display (Figma 1087:215, 1260 × 800 px).

Voice-agent driven: the user asks the voice agent to show a specific email and
the agent calls show_email_view, which routes to set_email() here.  The screen
is never navigated to empty — it is always opened with an email payload.

Layout (all Figma absolute px on the 1260 × 800 design frame):
  BACKGROUND : full frame  assets/brief/v2/bg.png  + 45 % white overlay
  STATUS BAR : Listening pill x=851 y=17 w=222 h=47  |  WiFi  |  Battery
  EMAIL CARD : x=22  y=77  1216×682  rgba(255,255,255,0.9)  radius=38
    SUBJECT  : x=45   y=36   w=1126 h=48   SemiBold 40 px  #2F2F2F  (shorten=right)
    DIVIDER  : y=118  x=45   w=1130              #9E9E9E
    AVATAR   : x=45   y=173  w=66   h=66   circle  #6D48CC
      INITIAL: centred in avatar   SemiBold 40 px  white
    SENDER   : x=137  y=184  w=377  h=43   SemiBold 36 px  #3C3C3C
    TO-ME    : x=137  y=239  w=83   h=38   SemiBold 32 px  #3C3C3C
    ARROW    : x=236  y=250  w=19   h=19   filled triangle  #3B3B3B
    TIME     : x=1050 y=226  w=113  h=36   SemiBold 30 px  #4A525F
    BODY SV  : x=0    y=290  w=1216 h=392  scrollable
      BODY   : x=91 left-pad  Medium 32 px  #4D5154

Public API (called from main.py):
    set_email(data)                – populate fields from a voice directive dict
    set_voice_session_state(state) – forward voice state → listening pill
    update_amplitude(amp)          – forward mic amplitude → waveform bars
"""

from __future__ import annotations

import logging
import time

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from components.live_wifi_icon import LiveWifiIcon
from config import ASSETS_DIR, DISPLAY_HEIGHT, DISPLAY_WIDTH
from screens.base_screen import BaseScreen
from screens.home import _VoiceStatePill  # noqa: PLC2701

logger = logging.getLogger(__name__)

# ── Design constants ──────────────────────────────────────────────────────────
_FW, _FH = 1260.0, 800.0
_CW, _CH = 1216.0, 682.0          # email card dimensions
_SCALE = min(DISPLAY_WIDTH / _FW, DISPLAY_HEIGHT / _FH)

# Body scroll area: starts at card-relative y=290 (from Figma top), height=392
_SCROLL_TOP_PX = 290.0             # px from top of card where body begins
_SCROLL_H_PX   = _CH - _SCROLL_TOP_PX   # 392 px

# Asset folder — shared with morning_brief
_V2 = ASSETS_DIR / "brief" / "v2"


def _asset(name: str) -> str:
    p = _V2 / name
    return str(p) if p.is_file() else ""


# ── Coordinate helpers ────────────────────────────────────────────────────────

def _ph(fx: float, fy: float, fw: float, fh: float) -> dict:
    """Figma absolute px → Kivy size_hint + pos_hint on the 1260×800 root."""
    return {
        "size_hint": (fw / _FW, fh / _FH),
        "pos_hint":  {"x": fx / _FW, "y": (_FH - fy - fh) / _FH},
    }


def _rel(fx: float, fy: float, fw: float, fh: float,
         cw: float = _CW, ch: float = _CH) -> dict:
    """Card-relative Figma px (top-left origin) → Kivy size+pos hints."""
    return {
        "size_hint": (fw / cw, fh / ch),
        "pos_hint":  {"x": fx / cw, "y": (ch - fy - fh) / ch},
    }


def _ff(fs: float) -> float:
    """Figma font px → device font px."""
    return max(6.0, fs * _SCALE)


def _sz(d: float) -> float:
    """Figma px → device px."""
    return max(1.0, d * _SCALE)


# ── Colours ───────────────────────────────────────────────────────────────────
_C_SUBJECT = (47/255,  47/255,  47/255,  1.0)   # #2F2F2F  subject line
_C_SENDER  = (60/255,  60/255,  60/255,  1.0)   # #3C3C3C  sender name / "to me"
_C_BODY    = (77/255,  81/255,  84/255,  1.0)   # #4D5154  email body
_C_TIME    = (74/255,  82/255,  95/255,  1.0)   # #4A525F  timestamp
_C_DIV     = (158/255, 158/255, 158/255, 1.0)   # #9E9E9E  divider
_C_AVATAR  = (109/255, 72/255,  204/255, 1.0)   # #6D48CC  avatar circle
_C_ARROW   = (59/255,  59/255,  59/255,  1.0)   # #3B3B3B  dropdown arrow
_C_WHITE   = (1.0, 1.0, 1.0, 1.0)

_F_SB = "42dot-SB"    # SemiBold 600
_F_MD = "42dot-Med"   # Medium 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lbl(text: str, font: str, size: float, color: tuple,
         ha: str = "left", va: str = "middle", **kw) -> Label:
    lw = Label(text=text, font_name=font, font_size=size, color=color,
               halign=ha, valign=va, **kw)
    lw.bind(size=lw.setter("text_size"))
    return lw


# ── Screen ────────────────────────────────────────────────────────────────────

class EmailsScreen(BaseScreen):
    """Single-email read-only view, pixel-matched to Figma node 1087:215."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._voice_pill: _VoiceStatePill | None = None
        self._listening   = False
        self._amplitude   = 0.0
        self._voice_tick_ev = None

        # Displayed email state
        self._subject        = ""
        self._sender_name    = ""
        self._sender_initial = ""
        self._time_str       = ""
        self._recipient_lbl  = "to me"
        self._body           = ""

        # Label refs updated by set_email()
        self._lbl_subject: Label | None = None
        self._lbl_sender:  Label | None = None
        self._lbl_initial: Label | None = None
        self._lbl_time:    Label | None = None
        self._lbl_to_me:   Label | None = None
        self._lbl_body:    Label | None = None

        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = FloatLayout()

        # 1 · Full-bleed background image
        bg_src = _asset("bg.png")
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
            Color(1, 1, 1, 0.45)
            _ovr = Rectangle(pos=ov.pos, size=ov.size)
        ov.bind(
            pos=lambda w, p: setattr(_ovr, "pos",  p),
            size=lambda w, s: setattr(_ovr, "size", s),
        )
        root.add_widget(ov)

        # 3 · Email card
        self._build_card(root)

        # 4 · Status cluster (pill + wifi + battery)
        self._build_status(root)

        self.add_widget(root)

    def _build_card(self, root: FloatLayout) -> None:
        """Email card  x=22  y=77  1216×682  rgba(255,255,255,0.9)  radius=38."""
        card = FloatLayout(**_ph(22, 77, _CW, _CH))

        with card.canvas.before:
            # Subtle drop-shadow
            Color(0, 0, 0, 0.10)
            _shad = RoundedRectangle(radius=[_sz(40)])
            # Card fill
            Color(1, 1, 1, 0.9)
            _bg = RoundedRectangle(radius=[_sz(38)])

        def _sync_card(w, *_):
            _shad.pos  = (w.x + _sz(2), w.y - _sz(4))
            _shad.size = (w.width + _sz(2), w.height + _sz(5))
            _bg.pos    = w.pos
            _bg.size   = w.size

        card.bind(pos=_sync_card, size=_sync_card)

        # ── Subject  x=45  y=36  w=1126  h=48  SemiBold 40  #2F2F2F ─────────
        # Single-line; shorten with ellipsis so long subjects never push into
        # the divider at y=118.
        self._lbl_subject = _lbl(
            "", _F_SB, _ff(40), _C_SUBJECT, ha="left", va="middle",
            shorten=True, shorten_from="right",
            **_rel(45, 36, 1126, 48))
        card.add_widget(self._lbl_subject)

        # ── Divider  y=118  x=45  w=1130  h≈1.5 ─────────────────────────────
        div = Widget(**_rel(45, 118, 1130, 2))
        with div.canvas:
            Color(*_C_DIV)
            _dr = Rectangle(pos=div.pos, size=div.size)
        div.bind(
            pos=lambda w, p: setattr(_dr, "pos",  p),
            size=lambda w, s: setattr(_dr, "size", s),
        )
        card.add_widget(div)

        # ── Sender avatar circle  x=45  y=173  66×66  #6D48CC ───────────────
        av = Widget(**_rel(45, 173, 66, 66))
        with av.canvas:
            Color(*_C_AVATAR)
            _ae = Ellipse(pos=av.pos, size=av.size)
        av.bind(
            pos=lambda w, p: setattr(_ae, "pos",  p),
            size=lambda w, s: setattr(_ae, "size", s),
        )
        card.add_widget(av)

        # Initial letter centred in avatar
        self._lbl_initial = _lbl(
            "", _F_SB, _ff(40), _C_WHITE, ha="center", va="middle",
            **_rel(45, 173, 66, 66))
        card.add_widget(self._lbl_initial)

        # ── Sender name  x=137  y=184  w=700  h=43  SemiBold 36  #3C3C3C ───
        # Widened to 700 px (time label is at x=1050) so long names fit.
        self._lbl_sender = _lbl(
            "", _F_SB, _ff(36), _C_SENDER, ha="left",
            shorten=True, shorten_from="right",
            **_rel(137, 184, 700, 43))
        card.add_widget(self._lbl_sender)

        # ── "to me"  x=137  y=239  w=83  h=38  SemiBold 32  #3C3C3C ─────────
        self._lbl_to_me = _lbl(
            "to me", _F_SB, _ff(32), _C_SENDER, ha="left",
            **_rel(137, 239, 83, 38))
        card.add_widget(self._lbl_to_me)

        # ── Dropdown arrow ▼  x=236  y=250  19×19 ───────────────────────────
        arrow = _lbl(
            "▼", _F_MD, _ff(14), _C_ARROW, ha="center", va="middle",
            **_rel(236, 250, 19, 19))
        card.add_widget(arrow)

        # ── Time  x=1050  y=214  w=155  h=48  SemiBold 30  #4A525F ──────────
        # shorten=True prevents wrapping if a future format produces a longer string.
        self._lbl_time = _lbl(
            "", _F_SB, _ff(30), _C_TIME, ha="left",
            shorten=True, shorten_from="right",
            **_rel(1050, 214, 155, 48))
        card.add_widget(self._lbl_time)

        # ── Scrollable body  card-relative top=290  h=392 ────────────────────
        # pos_hint y=0 → aligns to bottom of card; size_hint_y = 392/682
        sv = ScrollView(
            size_hint=(1.0, _SCROLL_H_PX / _CH),
            pos_hint={"x": 0, "y": 0},
            do_scroll_x=False,
            do_scroll_y=True,
            scroll_type=["content"],
            bar_width=0,
        )
        body_container = GridLayout(
            cols=1,
            size_hint_y=None,
            padding=[_sz(91), _sz(21), _sz(41), _sz(24)],
        )
        body_container.bind(minimum_height=body_container.setter("height"))

        self._lbl_body = Label(
            text="",
            font_name=_F_MD,
            font_size=_ff(32),
            color=_C_BODY,
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        # Wrap at label width; grow height to fit all text
        self._lbl_body.bind(
            width=lambda l, w: setattr(l, "text_size", (w, None)),
            texture_size=lambda l, ts: setattr(l, "height", ts[1]),
        )
        body_container.add_widget(self._lbl_body)
        sv.add_widget(body_container)
        card.add_widget(sv)

        root.add_widget(card)

    def _build_status(self, root: FloatLayout) -> None:
        """Top-right status cluster matching Figma Group 207."""
        # Voice-state pill: rendered exclusively by the global VoiceControlBar
        # (see voice_control_bar.py) for consistent size/position/font.
        # self._voice_pill stays None here on purpose; existing
        # `if self._voice_pill:` guards elsewhere no-op safely.

        # (WiFi + battery indicators removed — not relevant for the desktop app.)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_email(self, data: dict) -> None:
        """Populate the screen with email data from a show_email_view directive.

        Accepted keys (all optional; absent keys leave existing values):
            subject, sender_name, sender_email, sender_initial,
            time, recipient_label, body
        """
        if "subject" in data:
            self._subject = str(data.get("subject") or "")
        if "sender_name" in data:
            self._sender_name = str(data.get("sender_name") or "")
        if "sender_initial" in data:
            self._sender_initial = str(data.get("sender_initial") or "")
        if "time" in data:
            self._time_str = str(data.get("time") or "")
        if "recipient_label" in data:
            self._recipient_lbl = str(data.get("recipient_label") or "to me")
        if "body" in data:
            raw = str(data.get("body") or "")
            self._body = raw.replace("\r\n", "\n").replace("\r", "\n")

        # Derive initial from name when not supplied
        if not self._sender_initial and self._sender_name:
            self._sender_initial = self._sender_name[0].upper()

        Clock.schedule_once(lambda _dt: self._refresh_labels(), 0)

    def _refresh_labels(self) -> None:
        if self._lbl_subject:
            self._lbl_subject.text = self._subject
        if self._lbl_sender:
            self._lbl_sender.text  = self._sender_name
        if self._lbl_initial:
            self._lbl_initial.text = self._sender_initial
        if self._lbl_time:
            self._lbl_time.text    = self._time_str
        if self._lbl_to_me:
            self._lbl_to_me.text   = self._recipient_lbl
        if self._lbl_body:
            self._lbl_body.text    = self._body

    # ── Voice state ───────────────────────────────────────────────────────────

    def set_voice_session_state(self, state: str) -> None:
        lbl_map = {
            "listening": "Listening",
            "thinking":  "Thinking",
            "speaking":  "Talking",
        }
        lbl = lbl_map.get(state)
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

    def _start_voice_tick(self) -> None:
        if self._voice_tick_ev is None:
            self._voice_tick_ev = Clock.schedule_interval(self._voice_tick, 1 / 30)

    def _stop_voice_tick(self) -> None:
        if self._voice_tick_ev is not None:
            self._voice_tick_ev.cancel()
            self._voice_tick_ev = None

    def _voice_tick(self, _dt: float) -> None:
        if self._voice_pill:
            self._voice_pill.update_bars(time.monotonic(), self._amplitude)

    def on_leave(self, *args) -> None:
        self._stop_voice_tick()
        if self._voice_pill:
            self._voice_pill.opacity = 0.0
