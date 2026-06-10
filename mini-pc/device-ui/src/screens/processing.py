"""Processing / "Summarizing" screen — Figma ``1036:16``
(dvqlN0JtWQODt6jYbTrbDG, "Copy").

Shown immediately after a recording stops. Drawn entirely with Kivy primitives:

  * "Recording Complete" + "Meeting Name · 32 min" header
  * a centre orb with a calm, breathing concentric-ring animation
  * "Summarizing your meeting..." / "This may take a few seconds" captions
  * a rotating stage line
  * a bottom countdown — "Back to home screen in N seconds" — that returns to
    the home screen after 3 s. The "summary ready" notification is surfaced on
    the home screen (see ``home.py``); the summary keeps generating in the
    background via the app's existing summary poll.

Public API preserved for ``main.py`` (all safe to call while hidden):
on_enter / on_leave, on_processing_started, set_processing_status,
on_backend_progress, on_transcription_ready, on_summary_ready,
on_summary_failed.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from processing_layout import (
    BG_BOT,
    BG_TOP,
    COL_COUNTDOWN,
    COL_HEADLINE,
    COL_MUTED,
    COL_PURPLE,
    COL_TEXT,
    COUNTDOWN,
    COUNTDOWN_FS_RATIO,
    HEADLINE,
    HEADLINE_FS_RATIO,
    META,
    META_FS_RATIO,
    ORB,
    ORB_CONCENTRIC,
    ORB_FILL,
    ORB_RING,
    RING_BOT,
    RING_TOP,
    STAGE,
    STAGE_FS_RATIO,
    STATUS_BAR,
    SUBTITLE,
    SUBTITLE_FS_RATIO,
    SUMMARIZING,
    SUMMARIZING_FS_RATIO,
    font_px,
    kivy_hints,
    scaled_canvas,
)
from screens.base_screen import BaseScreen
from ui_bg import vertical_gradient_texture

logger = logging.getLogger(__name__)

_FONT = "42dot-Sans"

# Seconds the user stays on this screen before being returned home.
_RETURN_COUNTDOWN_S = 3

_DEFAULT_STAGES = (
    "Extracting key points...",
    "Identifying action items...",
    "Structuring summary...",
)


class _PulseOrb(Widget):
    """Light glow disc with faint concentric rings, a soft purple rim and a
    calm centre waveform — a code-drawn stand-in for the Figma "summarising"
    orb (the design intends a supplied GIF here; this keeps it on-brand and
    animated without a bitmap).
    """

    _N_RINGS = 4
    _N_BARS = 12
    _PERIOD_S = 2.6

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._phase = 0.0
        self._event = None
        self._bar_phase = [i * 0.5 for i in range(self._N_BARS)]
        self._tex = vertical_gradient_texture(RING_TOP, RING_BOT)
        with self.canvas:
            Color(*ORB_FILL)
            self._disc = Ellipse(pos=self.pos, size=self.size)
            # Faint static concentric rings (nested circles in the design).
            self._concentric = []
            for _ in range(self._N_RINGS):
                Color(*ORB_CONCENTRIC)
                self._concentric.append(Line(circle=(0, 0, 0), width=1.4))
            # Soft outer purple rim.
            self._rim_color = Color(*ORB_RING)
            self._rim = Line(circle=(0, 0, 0), width=2.0)
            # Centre waveform bars (purple gradient, rounded).
            self._bar_color = Color(1, 1, 1, 1)
            self._bars = [
                RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[5], texture=self._tex)
                for _ in range(self._N_BARS)
            ]
        self.bind(pos=self._redraw, size=self._redraw)

    def start(self) -> None:
        if self._event is None:
            self._event = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self) -> None:
        if self._event is not None:
            self._event.cancel()
            self._event = None

    def _tick(self, dt: float) -> None:
        self._phase = (self._phase + dt * 2.4) % math.tau
        self._redraw()

    def _redraw(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        cx, cy = x + w / 2.0, y + h / 2.0
        R = min(w, h) / 2.0
        self._disc.pos = self.pos
        self._disc.size = self.size
        for i, ring in enumerate(self._concentric):
            ring.width = max(1.0, w * 0.006)
            ring.circle = (cx, cy, R * (0.40 + 0.18 * i))
        rim_w = max(1.5, w * 0.014)
        self._rim.width = rim_w
        self._rim.circle = (cx, cy, R - rim_w / 2.0)

        # Centre waveform: a calm sine-driven set of bars within the orb.
        n = self._N_BARS
        span = R * 1.05
        bar_w = max(2.0, span / (n * 1.7))
        gap = (span - bar_w * n) / max(1, n - 1)
        centre = (n - 1) / 2.0
        max_h = R * 0.95
        start_x = cx - span / 2.0
        for i, rect in enumerate(self._bars):
            bell = max(0.18, math.cos(((i - centre) / centre) * math.pi / 2.0))
            wob = 0.5 + 0.5 * math.sin(self._phase + self._bar_phase[i])
            bar_h = max(bar_w, max_h * bell * (0.45 + 0.55 * wob))
            bx = start_x + i * (bar_w + gap)
            rect.pos = (bx, cy - bar_h / 2.0)
            rect.size = (bar_w, bar_h)
            rect.radius = [bar_w / 2.0]


class ProcessingScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._meeting_id: Optional[str] = None
        self._meeting_title = "Meeting"
        self._meeting_duration_seconds = 0
        self._stage_index = 0
        self._countdown_event = None
        self._stage_event = None
        self._countdown_left = _RETURN_COUNTDOWN_S
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self._root = FloatLayout(size_hint=(1, 1))
        from ui_bg import attach_swirl_bg

        attach_swirl_bg(self._root, BG_TOP, BG_BOT)
        self._root.bind(size=self._on_root_resize)

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        self.orb = _PulseOrb(**kivy_hints(ORB))
        self._canvas.add_widget(self.orb)

        self.headline_status = self._add_label(
            "Recording Complete", HEADLINE, HEADLINE_FS_RATIO, COL_HEADLINE, bold=True, shorten=False,
        )
        self.meta_label = self._add_label("Meeting", META, META_FS_RATIO, COL_TEXT)
        self.summarizing_label = self._add_label(
            "Summarizing your meeting...", SUMMARIZING, SUMMARIZING_FS_RATIO, COL_HEADLINE, bold=True, shorten=False,
        )
        self.subtitle_label = self._add_label(
            "This may take a few seconds", SUBTITLE, SUBTITLE_FS_RATIO, COL_MUTED, shorten=False,
        )
        self.stage_label = self._add_label(
            _DEFAULT_STAGES[0], STAGE, STAGE_FS_RATIO, COL_PURPLE, shorten=False,
        )
        self.countdown_label = self._add_label(
            "Back to home screen in 3 seconds", COUNTDOWN, COUNTDOWN_FS_RATIO, COL_COUNTDOWN, shorten=False,
        )

        self.add_widget(self._root)
        Clock.schedule_once(lambda _dt: self._on_root_resize(self._root, self._root.size), 0)

    # --------------------------------------------------------------- helpers
    def _add_label(self, text, box, fs_ratio, color, *, bold=False, shorten=True):
        lbl = Label(
            text=text,
            font_name=_FONT,
            bold=bold,
            color=color,
            halign="center",
            valign="middle",
            shorten=shorten,
            shorten_from="right",
            max_lines=1,
            **kivy_hints(box),
        )
        lbl.bind(size=lbl.setter("text_size"))
        lbl._fs_ratio = fs_ratio  # noqa: SLF001
        self._canvas.add_widget(lbl)
        return lbl

    def _on_root_resize(self, _root, size):
        w, h = scaled_canvas(size[0], size[1])
        self._canvas.size = (w, h)
        for lbl in (
            self.headline_status,
            self.meta_label,
            self.summarizing_label,
            self.subtitle_label,
            self.stage_label,
            self.countdown_label,
        ):
            if lbl is not None:
                lbl.font_size = font_px(lbl._fs_ratio, h)  # noqa: SLF001

    # ------------------------------------------------------------- lifecycle
    def on_enter(self):
        # NOTE: on_enter can fire twice per navigation — once manually from
        # main.goto_screen() and once auto-dispatched by Kivy's ScreenManager.
        # Cancel any timers from a prior call before re-scheduling so we never
        # leak an orphaned countdown Clock event. A leaked countdown keeps
        # ticking past zero and calls goto("home") every second forever, which
        # yanks the user off whatever screen they open next (the "blank" bug).
        self._cancel_timers()

        self._meeting_id = getattr(self.app, "current_session_id", None)
        self.headline_status.text = "Recording Complete"
        self.summarizing_label.text = "Summarizing your meeting..."
        self.subtitle_label.text = "This may take a few seconds"
        self._stage_index = 0
        self.stage_label.text = _DEFAULT_STAGES[0]
        self.meta_label.text = self._meta_text()

        self.orb.start()
        self._stage_event = Clock.schedule_interval(self._rotate_stage, 0.9)

        self._countdown_left = _RETURN_COUNTDOWN_S
        self._update_countdown_label()
        self._countdown_event = Clock.schedule_interval(self._tick_countdown, 1.0)

    def on_leave(self):
        self.orb.stop()
        self._cancel_timers()

    def _cancel_timers(self) -> None:
        for ev_name in ("_countdown_event", "_stage_event"):
            ev = getattr(self, ev_name, None)
            if ev is not None:
                ev.cancel()
                setattr(self, ev_name, None)

    # ------------------------------------------------------------- countdown
    def _update_countdown_label(self) -> None:
        n = max(0, self._countdown_left)
        unit = "second" if n == 1 else "seconds"
        self.countdown_label.text = f"Back to home screen in {n} {unit}"

    def _tick_countdown(self, _dt) -> None:
        self._countdown_left -= 1
        if self._countdown_left <= 0:
            self._update_countdown_label()
            ev = self._countdown_event
            if ev is not None:
                ev.cancel()
                self._countdown_event = None
            self.goto("home", transition="fade")
            return
        self._update_countdown_label()

    def _rotate_stage(self, _dt) -> None:
        self._stage_index = (self._stage_index + 1) % len(_DEFAULT_STAGES)
        self.stage_label.text = _DEFAULT_STAGES[self._stage_index]

    # ------------------------------------------------------------------
    # Public API — called from main.py (kept compatible; the screen still
    # auto-returns home, the summary surfaces there when ready)
    # ------------------------------------------------------------------
    def on_processing_started(self, data):
        title = str((data or {}).get("title") or self._meeting_title or "Meeting").strip() or "Meeting"
        duration = int((data or {}).get("duration") or 0)
        self._meeting_title = title
        self._meeting_duration_seconds = duration
        self.meta_label.text = self._meta_text()

    def set_processing_status(self, text: str) -> None:
        msg = (text or "").strip()
        if msg and getattr(self, "stage_label", None) is not None:
            self.stage_label.text = msg

    def on_backend_progress(self, progress: int, status: str, eta: int, stage: str | None = None):
        del progress, eta
        label = (stage or status or "").strip()
        if label:
            self.set_processing_status(label.replace("_", " ").capitalize())

    def on_transcription_ready(self, meeting_id: str):
        if meeting_id:
            self._meeting_id = meeting_id

    def on_summary_ready(self, meeting_id: str, summary_data: dict):
        del summary_data
        if meeting_id:
            self._meeting_id = meeting_id

    def on_summary_failed(self, meeting_id: str, detail: str):
        del detail
        if meeting_id:
            self._meeting_id = meeting_id

    # ------------------------------------------------------------------
    def _meta_text(self) -> str:
        parts = [self._meeting_title or "Meeting"]
        dur = self._format_duration(self._meeting_duration_seconds)
        if dur != "--":
            parts.append(dur)
        return "    ·    ".join(parts)

    @staticmethod
    def _format_duration(seconds_value: int) -> str:
        total = max(0, int(seconds_value or 0))
        if total <= 0:
            return "--"
        minutes = total // 60
        if minutes >= 1:
            return f"{minutes} min"
        return f"{total} sec"
