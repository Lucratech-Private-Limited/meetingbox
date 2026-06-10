"""Meeting Summary screen — Figma ``1036:254``
(dvqlN0JtWQODt6jYbTrbDG, "Copy").

Minimal light-theme summary page drawn with Kivy primitives:

  * a back button + purple "Meeting Name" title (top-left)
  * a meta line — "Create time HH:MM AM  ·  32 min"
  * one big white rounded card with a sparkle + "AI Summary" header and a single
    scrollable region that shows the AI summary narrative only

Meeting title, creation time, audio duration and the summary body all come from
the backend meeting-detail response (``api_client.get_meeting_detail``), with
the locally-passed ``summary_data`` used for the first paint.

Public API preserved for ``main.py``:

  * ``__init__``
  * ``set_meeting_data(meeting_id, summary_data)``
  * ``on_enter`` / ``on_leave``
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Line, Mesh, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from screens.base_screen import BaseScreen
from summary_layout import (
    AI_BODY_FS_RATIO,
    AI_HEADER,
    AI_HEADER_FS_RATIO,
    AI_SCROLL,
    AI_SPARKLE,
    BACK_BTN,
    BG_BOT,
    BG_TOP,
    CARD,
    CARD_FILL,
    CARD_RADIUS,
    CARD_SHADOW,
    COL_AI_HEADER,
    COL_BODY,
    COL_META,
    COL_TITLE,
    META,
    META_FS_RATIO,
    SPARKLE_FILL,
    STATUS_BAR,
    TITLE,
    TITLE_FS_RATIO,
    font_px,
    kivy_hints,
    scaled_canvas,
)
from ui_bg import attach_swirl_bg

logger = logging.getLogger(__name__)

_FONT = "42dot-Sans"
_IST = timezone(timedelta(hours=5, minutes=30))


# Section markers emitted by the backend report composer. The device only shows
# the narrative overview, so trailing DETAILED ACCOUNT / OPEN QUESTIONS /
# RISKS / CONCERNS sections are stripped. Mirrors the web parser.
_DETAILED_SPLIT = re.compile(
    r"(?:^|\r?\n(?:\r?\n)?)\*{0,2}DETAILED ACCOUNT\*{0,2}\s*\r?\n",
    re.IGNORECASE,
)
_OPEN_MARKER = re.compile(
    r"\r?\n\r?\n(?:---\r?\n)?\*{0,2}OPEN QUESTIONS\*{0,2}\s*\r?\n",
    re.IGNORECASE,
)
_RISKS_MARKER = re.compile(
    r"\r?\n\r?\n(?:---\r?\n)?\*{0,2}RISKS\s*/\s*CONCERNS\*{0,2}\s*\r?\n",
    re.IGNORECASE,
)


def _split_on_last_marker(text: str, marker: re.Pattern) -> tuple[str, str]:
    last = None
    for m in marker.finditer(text):
        last = m
    if last is None:
        return text, ""
    return text[: last.start()], text[last.end():]


def _summary_card_text(full_text: str) -> str:
    """Return the narrative overview portion of a composed report."""
    t = (full_text or "").strip()
    if not t:
        return ""
    before_risks, _ = _split_on_last_marker(t, _RISKS_MARKER)
    main_part, _ = _split_on_last_marker(before_risks, _OPEN_MARKER)
    m = _DETAILED_SPLIT.search(main_part)
    if m is None:
        out = main_part.strip()
    else:
        overview = main_part[: m.start()].strip()
        detailed = main_part[m.end():].strip()
        out = overview or detailed
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Widgets
# ─────────────────────────────────────────────────────────────────────────


class _BackChevronButton(ButtonBehavior, Widget):
    """Circular back button with a chevron icon (existing device style)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(1, 1, 1, 0.85)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[999])
            Color(53 / 255, 57 / 255, 59 / 255, 1)
            self._chev = Line(points=[0, 0, 0, 0, 0, 0], width=2.4, cap="round", joint="round")
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [min(self.width, self.height) / 2]
        cx = self.x + self.width * 0.5
        cy = self.y + self.height * 0.5
        dx = self.width * 0.12
        dy = self.height * 0.16
        self._chev.points = [cx + dx, cy + dy, cx - dx, cy, cx + dx, cy - dy]


class _Sparkle(Widget):
    """A filled four-point star (the AI Summary glyph)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(*SPARKLE_FILL)
            self._mesh = Mesh(mode="triangle_fan")
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        cx = self.x + self.width / 2.0
        cy = self.y + self.height / 2.0
        outer = min(self.width, self.height) / 2.0
        inner = outer * 0.32
        # Angular order: right, ne, up, nw, left, sw, down, se.
        pts = [
            (cx + outer, cy),
            (cx + inner, cy + inner),
            (cx, cy + outer),
            (cx - inner, cy + inner),
            (cx - outer, cy),
            (cx - inner, cy - inner),
            (cx, cy - outer),
            (cx + inner, cy - inner),
        ]
        verts = [cx, cy, 0.0, 0.0]
        for px, py in pts:
            verts += [px, py, 0.0, 0.0]
        verts += [pts[0][0], pts[0][1], 0.0, 0.0]
        self._mesh.vertices = verts
        self._mesh.indices = list(range(len(verts) // 4))


class _Card(Widget):
    """White rounded card with a soft drop shadow."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(*CARD_SHADOW)
            self._shadow = RoundedRectangle(pos=self.pos, size=self.size, radius=[CARD_RADIUS])
            Color(*CARD_FILL)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[CARD_RADIUS])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        off = max(2.0, self.height * 0.01)
        self._shadow.pos = (self.x, self.y - off)
        self._shadow.size = self.size
        self._shadow.radius = [CARD_RADIUS]
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [CARD_RADIUS]


class _MiniStatus(Widget):
    """Compact top-right wifi + battery glyphs."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            Color(53 / 255, 57 / 255, 59 / 255, 1)
            self._w1 = Line(width=2.0, cap="round")
            self._w2 = Line(width=2.0, cap="round")
            self._dot = RoundedRectangle(size=(0, 0), radius=[999])
            self._batt = Line(width=2.0, joint="round")
            Color(0.20, 0.78, 0.35, 1)
            self._batt_fill = RoundedRectangle(size=(0, 0), radius=[2])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        h = self.height
        wifi_cx = self.x + h * 0.55
        base_y = self.y + h * 0.30
        # Upward-opening wifi arcs (0° = top, clockwise) + base dot.
        self._w1.circle = (wifi_cx, base_y, h * 0.40, 305, 415)
        self._w2.circle = (wifi_cx, base_y, h * 0.24, 305, 415)
        d = h * 0.12
        self._dot.pos = (wifi_cx - d / 2, base_y - d / 2)
        self._dot.size = (d, d)
        # Battery on the right.
        bw = h * 1.2
        bh = h * 0.48
        bx = self.x + self.width - bw - h * 0.2
        by = self.y + (h - bh) / 2.0
        self._batt.rounded_rectangle = (bx, by, bw, bh, 3)
        self._batt_fill.pos = (bx + 2, by + 2)
        self._batt_fill.size = (max(0, bw * 0.7 - 2), bh - 4)


class _ScrollText(ScrollView):
    """Wrapping multi-line label inside a vertical ScrollView."""

    def __init__(self, *, fs_ratio: float, color, font_name: str, **kwargs):
        super().__init__(
            do_scroll_x=False,
            do_scroll_y=True,
            bar_width=6,
            bar_color=(0.42, 0.45, 0.52, 0.55),
            bar_inactive_color=(0.42, 0.45, 0.52, 0.20),
            scroll_type=["bars", "content"],
            **kwargs,
        )
        self._label = Label(
            text="",
            font_name=font_name,
            color=color,
            halign="left",
            valign="top",
            markup=False,
            size_hint=(1, None),
        )
        self._fs_ratio = fs_ratio
        self._label._fs_ratio = fs_ratio  # noqa: SLF001
        self.add_widget(self._label)
        self.bind(size=self._sync_width)
        self._label.bind(texture_size=self._sync_height)

    def _sync_width(self, *_args):
        self._label.text_size = (self.width, None)

    def _sync_height(self, *_args):
        self._label.height = max(self._label.texture_size[1], self.height)

    @property
    def text(self) -> str:
        return self._label.text

    @text.setter
    def text(self, value: str) -> None:
        self._label.text = value or ""

    @property
    def font_size(self):
        return self._label.font_size

    @font_size.setter
    def font_size(self, value):
        self._label.font_size = value


# ─────────────────────────────────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────────────────────────────────


class SummaryReviewScreen(BaseScreen):
    """Minimal meeting-summary review page."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id: Optional[str] = None
        self._summary_data: dict = {}
        self._meeting_title = "Meeting"
        self._scaled_labels: list[tuple[Label, float]] = []
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self._root = FloatLayout(size_hint=(1, 1))
        attach_swirl_bg(self._root, BG_TOP, BG_BOT)
        self._root.bind(size=self._on_root_resize)

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        back_btn = _BackChevronButton(**kivy_hints(BACK_BTN))
        back_btn.bind(on_release=lambda *_: self._on_back())
        self._canvas.add_widget(back_btn)

        self._canvas.add_widget(_MiniStatus(**kivy_hints(STATUS_BAR)))

        self.title_label = self._add_label(
            "Meeting Name", TITLE, TITLE_FS_RATIO, COL_TITLE, bold=True, halign="left",
        )
        self.meta_label = self._add_label(
            "Create time —", META, META_FS_RATIO, COL_META, halign="left",
        )

        self._canvas.add_widget(_Card(**kivy_hints(CARD)))
        self._canvas.add_widget(_Sparkle(**kivy_hints(AI_SPARKLE)))
        self._add_label(
            "AI Summary", AI_HEADER, AI_HEADER_FS_RATIO, COL_AI_HEADER, bold=True, halign="left",
        )

        self.summary_scroll = _ScrollText(
            fs_ratio=AI_BODY_FS_RATIO,
            color=COL_BODY,
            font_name=_FONT,
            **kivy_hints(AI_SCROLL),
        )
        self._scaled_labels.append((self.summary_scroll._label, AI_BODY_FS_RATIO))  # noqa: SLF001
        self._canvas.add_widget(self.summary_scroll)

        self.add_widget(self._root)
        Clock.schedule_once(lambda _dt: self._on_root_resize(self._root, self._root.size), 0)

    def _add_label(self, text, box, fs_ratio, color, *, bold=False, halign="center"):
        lbl = Label(
            text=text,
            font_name=_FONT,
            bold=bold,
            color=color,
            halign=halign,
            valign="middle",
            shorten=True,
            shorten_from="right",
            max_lines=1,
            font_hinting="light",
            font_kerning=True,
            **kivy_hints(box),
        )
        lbl.bind(size=lbl.setter("text_size"))
        self._scaled_labels.append((lbl, fs_ratio))
        self._canvas.add_widget(lbl)
        return lbl

    def _on_root_resize(self, _root, size):
        w, h = scaled_canvas(size[0], size[1])
        self._canvas.size = (w, h)
        for lbl, ratio in self._scaled_labels:
            if lbl is None:
                continue
            try:
                lbl.font_size = font_px(ratio, h)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------- lifecycle
    def set_meeting_data(self, meeting_id: str, summary_data: dict):
        self.meeting_id = meeting_id
        self._summary_data = dict(summary_data or {})
        self._apply_local_data()
        self._fetch_meeting_detail()

    def on_enter(self):
        self._apply_local_data()

    def on_leave(self):
        pass

    # --------------------------------------------------------------- data
    def _apply_local_data(self):
        data = self._summary_data or {}
        title = (data.get("title") or self._meeting_title or "Meeting").strip() or "Meeting"
        self._meeting_title = title
        self.title_label.text = title
        self.meta_label.text = self._format_meta_line(data)
        self.summary_scroll.text = (
            self._overview_summary_text()
            or "Summary will appear here once processing finishes."
        )

    def _overview_summary_text(self) -> str:
        data = self._summary_data or {}
        summary = data.get("summary")
        if isinstance(summary, dict):
            summary_text = (summary.get("summary") or "").strip()
        else:
            summary_text = (summary or "").strip()
        return _summary_card_text(summary_text)

    def _fetch_meeting_detail(self):
        if not self.meeting_id:
            return

        async def _run():
            try:
                detail = await self.backend.get_meeting_detail(self.meeting_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_meeting_detail failed: %s", exc)
                return
            if not isinstance(detail, dict):
                return
            block = detail.get("summary")
            if isinstance(block, dict):
                merged = {**(self._summary_data or {}), **block}
            else:
                merged = dict(self._summary_data or {})
            for k in ("title", "duration", "started_at", "generated_at"):
                if detail.get(k) is not None and merged.get(k) in (None, ""):
                    merged[k] = detail[k]
            self._summary_data = merged
            Clock.schedule_once(lambda _dt: self._apply_local_data(), 0)

        run_async(_run())

    # ----------------------------------------------------------- formatting
    @staticmethod
    def _to_ist_datetime(raw) -> datetime:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).astimezone(_IST)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_IST)

    def _format_meta_line(self, data: dict) -> str:
        created = ""
        raw = data.get("generated_at") or data.get("started_at")
        if raw:
            try:
                dt = self._to_ist_datetime(raw)
                created = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:  # noqa: BLE001
                created = ""
        created_part = f"Create time {created}" if created else "Create time —"

        duration_part = ""
        try:
            seconds = max(0, int(float(data.get("duration") or 0)))
        except (TypeError, ValueError):
            seconds = 0
        if seconds >= 60:
            duration_part = f"{seconds // 60} min"
        elif seconds > 0:
            duration_part = f"{seconds} sec"

        if duration_part:
            return f"{created_part}    \u2022    {duration_part}"
        return created_part

    # ------------------------------------------------------------- handlers
    def _on_back(self):
        self.goto("home", transition="fade")
