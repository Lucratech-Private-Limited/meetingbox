"""Processing / "Summarizing" screen — Figma ``1036:16``
(dvqlN0JtWQODt6jYbTrbDG, "Copy").

Shown immediately after a recording stops:

  * "Recording Complete" + "Meeting Name · 32 min" header
  * the supplied centre orb GIF from ``assets/figma/processing_orb.gif``
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
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import ASSETS_DIR
from processing_layout import (
    BG_BOT,
    BG_TOP,
    COL_BATT_GREEN,
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

logger = logging.getLogger(__name__)

_FONT = "42dot-Sans"
_PROCESSING_ORB_GIF = str(ASSETS_DIR / "figma" / "processing_orb.gif")

# Seconds the user stays on this screen before being returned home.
_RETURN_COUNTDOWN_S = 3

_DEFAULT_STAGES = (
    "Extracting key points...",
    "Identifying action items...",
    "Structuring summary...",
)


class _StatusBar(Widget):
    """Top-right wifi + green battery glyphs from the processing Figma frame."""

    _GAP = (0.937, 0.945, 0.955, 1.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            self._wedges: list[Ellipse] = []
            for col in (COL_TEXT, self._GAP, COL_TEXT, self._GAP):
                Color(*col)
                self._wedges.append(Ellipse(pos=(0, 0), size=(0, 0), angle_start=-50, angle_end=50))
            Color(*COL_TEXT)
            self._wifi_dot = Ellipse(pos=(0, 0), size=(0, 0))
            self._batt = Line(rounded_rectangle=(0, 0, 0, 0, 2), width=1.8)
            self._batt_tip = Line(points=[], width=1.8, cap="round")
            Color(*COL_BATT_GREEN)
            self._batt_fill = RoundedRectangle(pos=(0, 0), size=(0, 0), radius=[2])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        x, y, w, h = self.x, self.y, self.width, self.height
        if w <= 0 or h <= 0:
            return
        self._batt.width = max(1.2, h * 0.055)
        self._batt_tip.width = max(1.2, h * 0.055)

        cx = x + w * 0.18
        base = y + h * 0.08
        big_r = h * 0.60
        for ell, k in zip(self._wedges, (1.0, 0.72, 0.50, 0.27)):
            r = big_r * k
            ell.pos = (cx - r, base - r)
            ell.size = (2 * r, 2 * r)
        d = big_r * 0.34
        self._wifi_dot.pos = (cx - d / 2, base - d / 2)
        self._wifi_dot.size = (d, d)

        bw = w * 0.30
        bh = h * 0.42
        bx = x + w - bw - w * 0.10
        by = y + (h - bh) / 2.0
        self._batt.rounded_rectangle = (bx, by, bw, bh, 3)
        self._batt_tip.points = [bx + bw + 2.5, by + bh * 0.3, bx + bw + 2.5, by + bh * 0.7]
        pad = max(1.5, bh * 0.12)
        self._batt_fill.pos = (bx + pad, by + pad)
        self._batt_fill.size = (max(1.0, (bw - 2 * pad) * 0.92), bh - 2 * pad)


class _ProcessingOrbGif(Image):
    def __init__(self, **kwargs):
        super().__init__(
            source=_PROCESSING_ORB_GIF,
            fit_mode="contain",
            anim_delay=0.05,
            anim_loop=0,
            **kwargs,
        )


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

        self._canvas.add_widget(_StatusBar(**kivy_hints(STATUS_BAR)))

        self.orb = _ProcessingOrbGif(**kivy_hints(ORB))
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

        self._stage_event = Clock.schedule_interval(self._rotate_stage, 0.9)

        self._countdown_left = _RETURN_COUNTDOWN_S
        self._update_countdown_label()
        self._countdown_event = Clock.schedule_interval(self._tick_countdown, 1.0)

    def on_leave(self):
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
