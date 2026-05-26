"""Meeting Summary screen — dashboard layout (v2).

Replaces the older single-page Figma 659:838 layout with a sidebar-driven
dashboard from the user's reference screenshot. Six tabs in the left rail
(Overview, Key Points, Action Items, Decisions Made, Participants,
Transcript) swap the right-hand content. All tab content is hydrated from
the existing meeting-detail response (``api_client.get_meeting_detail``):

    summary.summary         -> AI Summary card body
    summary.topics          -> Key Topics card / Key Points tab
    summary.action_items    -> Action Items (compact + full)
    summary.decisions       -> Decisions Made (compact + full)
    detail.segments         -> Transcript tab
    meeting.attendees / action_items[*].assignee
                            -> Participants tab (with fallbacks)

The view is uniformly scaled (``scaled_canvas``) so it works on any
display resolution and never overflows text — labels use ``shorten=True``
with multi-line wrapping where appropriate.

Public API preserved for ``main.py`` + ``processing.py``:

- ``__init__``
- ``set_meeting_data(meeting_id, summary_data)``
- ``on_enter`` / ``on_leave``
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle, Triangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from config import ASSETS_DIR
from screens.base_screen import BaseScreen
from summary_layout import (
    ACCENT_BLUE,
    BACK_BTN,
    BG_RGB,
    CANVAS_H,
    CANVAS_W,
    CARD_BORDER,
    CARD_FILL,
    CARD_RADIUS,
    COL_ACCENT,
    COL_HINT,
    COL_MUTED,
    COL_WHITE,
    CONTENT_AREA,
    FOOTER_FS_RATIO,
    FOOTER_LEFT,
    FOOTER_RIGHT,
    FULL_TAB_CARD,
    META_CARD,
    META_DATE,
    META_DATE_FS_RATIO,
    META_EXPORT,
    META_FILE_ICON,
    META_PARTICIPANTS,
    META_RECORDED,
    META_SHARE,
    META_TITLE,
    META_TITLE_FS_RATIO,
    OV_ACTIONS_CARD,
    OV_AI_CARD,
    OV_DECISIONS_CARD,
    OV_KEY_CARD,
    PAGE_TITLE,
    PAGE_TITLE_FS_RATIO,
    PLAY_BORDER,
    PLAY_FILL,
    PLAY_RADIUS,
    PLAY_RECORDING,
    PLAY_RECORDING_FS_RATIO,
    PROG_FILL,
    PROG_RADIUS,
    PROG_TRACK_FILL,
    SECTION_BODY_FS_RATIO,
    SECTION_HINT_FS_RATIO,
    SECTION_TITLE_FS_RATIO,
    SIDEBAR_BORDER,
    SIDEBAR_CARD,
    SIDEBAR_FILL,
    TAB_ACTION_ITEMS,
    TAB_ACTIVE_BORDER,
    TAB_ACTIVE_FILL,
    TAB_ACTIVE_RADIUS,
    TAB_DECISIONS,
    TAB_FS_RATIO,
    TAB_KEY_POINTS,
    TAB_OVERVIEW,
    TAB_PARTICIPANTS,
    TAB_TRANSCRIPT,
    canvas_box,
    content_header,
    font_px,
    kivy_hints,
    scaled_canvas,
)

logger = logging.getLogger(__name__)

_FIGMA = ASSETS_DIR / "summary" / "figma"
_BG = (BG_RGB[0] / 255, BG_RGB[1] / 255, BG_RGB[2] / 255, 1.0)
_FONT_BOLD = "42dot-Sans"

_TAB_IDS: tuple[str, ...] = (
    "overview",
    "key_points",
    "action_items",
    "decisions",
    "participants",
    "transcript",
)

_TAB_BOXES = {
    "overview": TAB_OVERVIEW,
    "key_points": TAB_KEY_POINTS,
    "action_items": TAB_ACTION_ITEMS,
    "decisions": TAB_DECISIONS,
    "participants": TAB_PARTICIPANTS,
    "transcript": TAB_TRANSCRIPT,
}

_TAB_LABELS = {
    "overview": "Overview",
    "key_points": "Key Points",
    "action_items": "Action Items",
    "decisions": "Decisions Made",
    "participants": "Participants",
    "transcript": "Transcript",
}


def _png(name: str) -> str:
    p = _FIGMA / name
    return str(p) if p.is_file() else ""


# ─────────────────────────────────────────────────────────────────────────
# Reusable widgets
# ─────────────────────────────────────────────────────────────────────────


class _ImgBtn(ButtonBehavior, Image):
    """Tappable PNG button."""


class _GradientCard(Widget):
    """Rounded card surface with subtle border."""

    def __init__(
        self,
        *,
        fill: tuple = CARD_FILL,
        border: tuple = CARD_BORDER,
        radius: float = CARD_RADIUS,
        border_width: float = 1.4,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fill = fill
        self._border = border
        self._radius = radius
        self._border_width = border_width
        with self.canvas:
            Color(*fill)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
            Color(*border)
            self._line = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=border_width,
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [self._radius]
        self._line.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self._radius,
        )


class _SidebarTab(ButtonBehavior, Widget):
    """A tab row in the sidebar — rounded pill when active, transparent otherwise.

    Visual layout:

        +--------------------------------------+
        | [glyph]  Label                       |
        +--------------------------------------+

    ``set_active`` toggles the pill background and bold/white text.
    """

    def __init__(
        self,
        *,
        label_text: str,
        on_click,
        glyph: Optional[str] = None,
        fs_ratio: float = TAB_FS_RATIO,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._fs_ratio = fs_ratio
        self._active = False
        with self.canvas.before:
            self._bg_color = Color(*TAB_ACTIVE_FILL)
            self._bg_color.a = 0.0  # invisible until active
            self._bg_rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[TAB_ACTIVE_RADIUS]
            )
            self._border_color = Color(*TAB_ACTIVE_BORDER)
            self._border_color.a = 0.0
            self._border_line = Line(
                rounded_rectangle=(
                    self.x,
                    self.y,
                    self.width,
                    self.height,
                    TAB_ACTIVE_RADIUS,
                ),
                width=1.2,
            )

        # Optional small glyph dot at the left edge — rendered as a small
        # filled circle if no PNG is available. Keeps the layout minimal
        # and avoids extra asset deps.
        self._glyph_label = Label(
            text=glyph or "",
            color=COL_MUTED,
            font_name=_FONT_BOLD,
            bold=True,
            halign="center",
            valign="middle",
            size_hint=(None, None),
        )
        self._glyph_label.bind(size=self._glyph_label.setter("text_size"))

        self._label = Label(
            text=label_text,
            color=COL_MUTED,
            font_name=_FONT_BOLD,
            bold=False,
            halign="left",
            valign="middle",
            shorten=True,
            shorten_from="right",
            size_hint=(None, None),
        )
        self._label.bind(size=self._label.setter("text_size"))

        self.add_widget(self._glyph_label)
        self.add_widget(self._label)
        self.bind(pos=self._sync, size=self._sync)
        if on_click:
            self.bind(on_release=lambda *_: on_click())

    def _sync(self, *_):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        self._bg_rect.radius = [TAB_ACTIVE_RADIUS]
        self._border_line.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            TAB_ACTIVE_RADIUS,
        )
        pad_x = max(8, int(self.width * 0.06))
        glyph_w = max(10, int(self.height * 0.42))
        self._glyph_label.pos = (self.x + pad_x, self.y)
        self._glyph_label.size = (glyph_w, self.height)
        self._label.pos = (self.x + pad_x + glyph_w + 8, self.y)
        self._label.size = (max(1, self.width - pad_x - glyph_w - 16), self.height)

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._bg_color.a = 1.0 if active else 0.0
        self._border_color.a = 1.0 if active else 0.0
        self._label.color = COL_WHITE if active else COL_MUTED
        self._label.bold = bool(active)


class _PlayRecordingPill(ButtonBehavior, Widget):
    """Bottom-of-sidebar pill: ▶ Play Recording  + duration string."""

    def __init__(self, *, duration_text: str = "", fs_ratio: float = PLAY_RECORDING_FS_RATIO, **kwargs):
        super().__init__(**kwargs)
        self._fs_ratio = fs_ratio
        with self.canvas.before:
            Color(*PLAY_FILL)
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[PLAY_RADIUS]
            )
            Color(*PLAY_BORDER)
            self._line = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, PLAY_RADIUS),
                width=1.2,
            )
            self._tri_color = Color(*ACCENT_BLUE)
            self._triangle = Triangle(points=[0, 0, 0, 0, 0, 0])
        self._label_main = Label(
            text="Play Recording",
            color=COL_WHITE,
            font_name=_FONT_BOLD,
            bold=True,
            halign="left",
            valign="middle",
            size_hint=(None, None),
            shorten=True,
            shorten_from="right",
        )
        self._label_main.bind(size=self._label_main.setter("text_size"))
        self._label_dur = Label(
            text=duration_text,
            color=COL_HINT,
            font_name=_FONT_BOLD,
            bold=False,
            halign="right",
            valign="middle",
            size_hint=(None, None),
        )
        self._label_dur.bind(size=self._label_dur.setter("text_size"))
        self.add_widget(self._label_main)
        self.add_widget(self._label_dur)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size
        self._rect.radius = [PLAY_RADIUS]
        self._line.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            PLAY_RADIUS,
        )
        # Triangle play glyph on the left side
        tri_h = self.height * 0.4
        tri_w = tri_h * 0.85
        cx = self.x + max(16, self.width * 0.08)
        cy = self.y + self.height / 2
        self._triangle.points = [
            cx, cy + tri_h / 2,
            cx, cy - tri_h / 2,
            cx + tri_w, cy,
        ]
        # Labels
        label_x = cx + tri_w + 10
        label_w = self.width - (label_x - self.x) - 8
        self._label_main.pos = (label_x, self.y)
        self._label_main.size = (max(1, label_w * 0.6), self.height)
        self._label_dur.pos = (label_x + label_w * 0.6, self.y)
        self._label_dur.size = (max(1, label_w * 0.4) - 4, self.height)

    def set_duration(self, text: str) -> None:
        self._label_dur.text = text or ""


class _ProgressBar(Widget):
    """Thin horizontal progress bar with a rounded track + filled portion."""

    def __init__(self, *, value: float = 0.0, **kwargs):
        super().__init__(**kwargs)
        self._value = max(0.0, min(1.0, value))
        with self.canvas:
            Color(*PROG_TRACK_FILL)
            self._track = RoundedRectangle(pos=self.pos, size=self.size, radius=[PROG_RADIUS])
            Color(*PROG_FILL)
            self._fill = RoundedRectangle(pos=self.pos, size=(0, 0), radius=[PROG_RADIUS])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._track.pos = self.pos
        self._track.size = self.size
        self._track.radius = [PROG_RADIUS]
        fw = max(0.0, min(self.width, self.width * self._value))
        self._fill.pos = self.pos
        self._fill.size = (fw, self.height)
        self._fill.radius = [PROG_RADIUS]

    def set_value(self, v: float) -> None:
        self._value = max(0.0, min(1.0, v))
        self._sync()


class _Dot(Widget):
    """Small filled circle used as a row bullet."""

    def __init__(self, *, color: tuple = COL_ACCENT, **kwargs):
        super().__init__(**kwargs)
        self._color = color
        with self.canvas:
            Color(*color)
            self._ell = RoundedRectangle(pos=self.pos, size=self.size, radius=[999])
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        d = min(self.width, self.height)
        cx = self.x + (self.width - d) / 2
        cy = self.y + (self.height - d) / 2
        self._ell.pos = (cx, cy)
        self._ell.size = (d, d)
        self._ell.radius = [d / 2]


class _AccentLink(ButtonBehavior, Label):
    """Clickable blue text link (used for "View all" jumps)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("color", COL_ACCENT)
        kwargs.setdefault("font_name", _FONT_BOLD)
        kwargs.setdefault("bold", True)
        kwargs.setdefault("halign", "right")
        kwargs.setdefault("valign", "middle")
        super().__init__(**kwargs)
        self.bind(size=self.setter("text_size"))


# ─────────────────────────────────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────────────────────────────────


class SummaryReviewScreen(BaseScreen):
    """Dashboard-style meeting summary review."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.meeting_id: Optional[str] = None
        self._summary_data: dict = {}
        self._segments: list[dict] = []
        self._meeting_title = "Meeting"
        self._duration_min = 0
        self._participant_count = 0
        self._attendees: list[str] = []
        self._action_items: list[dict] = []
        self._decisions: list[str] = []
        self._topics: list[dict] = []
        self._sidebar_tabs: dict[str, _SidebarTab] = {}
        self._tab_widgets: dict[str, list[Widget]] = {tid: [] for tid in _TAB_IDS}
        self._active_tab = "overview"
        # Resize-time font scaling: a list of (label, fs_ratio) pairs that
        # we apply on every layout pass.
        self._scaled_labels: list[tuple[Label, float]] = []
        self._build_ui()

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        self._root = FloatLayout(size_hint=(1, 1))
        with self._root.canvas.before:
            Color(*_BG)
            self._bg = Rectangle(pos=self._root.pos, size=self._root.size)
        self._root.bind(
            pos=lambda w, _v: setattr(self._bg, "pos", w.pos),
            size=self._on_root_resize,
        )

        anchor = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        self._root.add_widget(anchor)
        self._canvas = FloatLayout(size_hint=(None, None))
        anchor.add_widget(self._canvas)

        self._build_header()
        self._build_meta_card()
        self._build_sidebar()
        self._build_footer()

        # Build the Overview tab eagerly so the first paint shows real content.
        self._show_tab("overview")

        self.add_widget(self._root)
        Clock.schedule_once(lambda _dt: self._on_root_resize(self._root, self._root.size), 0)

    def _build_header(self):
        self._add_img_btn("btn_back.png", BACK_BTN, on_release=lambda *_: self._on_back())
        self._add_label(
            "Meeting Summary",
            PAGE_TITLE,
            PAGE_TITLE_FS_RATIO,
            COL_WHITE,
            bold=True,
            halign="left",
        )

    def _build_meta_card(self):
        self._canvas.add_widget(_GradientCard(**kivy_hints(META_CARD)))
        self._add_image("icon_file_box.png", META_FILE_ICON)
        self.meta_title_label = self._add_label(
            "Product Sync",
            META_TITLE,
            META_TITLE_FS_RATIO,
            COL_WHITE,
            bold=True,
            halign="left",
        )
        self.meta_date_label = self._add_label(
            "—",
            META_DATE,
            META_DATE_FS_RATIO,
            COL_MUTED,
            halign="left",
        )
        self._add_image("chip_participants.png", META_PARTICIPANTS)
        self._add_image("chip_recorded.png", META_RECORDED)
        self._add_img_btn("btn_export.png", META_EXPORT, on_release=lambda *_: self._on_export())
        self._add_img_btn("btn_share.png", META_SHARE, on_release=lambda *_: self._on_share())

    def _build_sidebar(self):
        self._canvas.add_widget(
            _GradientCard(
                fill=SIDEBAR_FILL,
                border=SIDEBAR_BORDER,
                radius=CARD_RADIUS,
                **kivy_hints(SIDEBAR_CARD),
            )
        )

        for tid in _TAB_IDS:
            tab = _SidebarTab(
                label_text=_TAB_LABELS[tid],
                glyph="•",
                on_click=lambda tid=tid: self._show_tab(tid),
                **kivy_hints(_TAB_BOXES[tid]),
            )
            self._scaled_labels.append((tab._label, TAB_FS_RATIO))
            self._scaled_labels.append((tab._glyph_label, TAB_FS_RATIO))
            self._sidebar_tabs[tid] = tab
            self._canvas.add_widget(tab)

        self.play_pill = _PlayRecordingPill(
            duration_text="",
            **kivy_hints(PLAY_RECORDING),
        )
        self._scaled_labels.append((self.play_pill._label_main, PLAY_RECORDING_FS_RATIO))
        self._scaled_labels.append((self.play_pill._label_dur, SECTION_HINT_FS_RATIO))
        self.play_pill.bind(on_release=lambda *_: self._on_play_recording())
        self._canvas.add_widget(self.play_pill)

    def _build_footer(self):
        self.footer_left_label = self._add_label(
            "Created: —",
            FOOTER_LEFT,
            FOOTER_FS_RATIO,
            COL_HINT,
            halign="left",
        )
        self.footer_right_label = self._add_label(
            "Generated by AI",
            FOOTER_RIGHT,
            FOOTER_FS_RATIO,
            COL_HINT,
            halign="right",
        )

    # ------------------------------------------------------------------
    # Tab switching
    # ------------------------------------------------------------------
    def _show_tab(self, tab_id: str) -> None:
        if tab_id not in _TAB_IDS:
            return
        # Remove currently-shown tab widgets (defensively skip None / unparented).
        for w in self._tab_widgets[self._active_tab]:
            if w is not None and w.parent is self._canvas:
                self._canvas.remove_widget(w)
        self._active_tab = tab_id

        widgets = self._tab_widgets[tab_id]
        if not widgets:
            widgets = [w for w in self._build_tab(tab_id) if w is not None]
            self._tab_widgets[tab_id] = widgets
        for w in widgets:
            if w is not None and w.parent is None:
                self._canvas.add_widget(w)

        for tid, tab in self._sidebar_tabs.items():
            tab.set_active(tid == tab_id)

        Clock.schedule_once(
            lambda _dt: self._on_root_resize(self._root, self._root.size), 0
        )

    def _build_tab(self, tab_id: str) -> list[Widget]:
        builder = {
            "overview": self._build_overview,
            "key_points": self._build_key_points,
            "action_items": self._build_action_items_full,
            "decisions": self._build_decisions_full,
            "participants": self._build_participants,
            "transcript": self._build_transcript,
        }[tab_id]
        return builder()

    # ------------------------------------------------------------------
    # Overview tab
    # ------------------------------------------------------------------
    def _build_overview(self) -> list[Widget]:
        widgets: list[Widget] = []

        # ─ AI Summary card ─
        ai_card = _GradientCard(**kivy_hints(OV_AI_CARD))
        widgets.append(ai_card)
        ai_icon_box, ai_title_box = content_header(OV_AI_CARD, icon_w=30.0, title_w=200.0)
        ai_icon = self._make_image("ai_summary_icon.png", ai_icon_box)
        if ai_icon is not None:
            widgets.append(ai_icon)
        widgets.append(
            self._make_label(
                "AI Summary",
                ai_title_box,
                SECTION_TITLE_FS_RATIO,
                COL_WHITE,
                bold=True,
                halign="left",
            )
        )
        ai_body_box = canvas_box(
            OV_AI_CARD["x"] * CANVAS_W + 28.0,
            OV_AI_CARD["y_top"] * CANVAS_H + 62.0,
            OV_AI_CARD["w"] * CANVAS_W - 56.0,
            OV_AI_CARD["h"] * CANVAS_H - 72.0,
        )
        self.ov_summary_text = self._make_label(
            "—",
            ai_body_box,
            SECTION_BODY_FS_RATIO,
            COL_HINT,
            halign="left",
            max_lines=3,
            shorten=True,
        )
        widgets.append(self.ov_summary_text)

        # ─ Key Topics card ─
        key_card = _GradientCard(**kivy_hints(OV_KEY_CARD))
        widgets.append(key_card)
        ki_box, kt_box = content_header(OV_KEY_CARD, icon_w=26.0, title_w=200.0)
        widgets.append(self._make_label("◆", ki_box, SECTION_TITLE_FS_RATIO, COL_ACCENT, bold=True))
        widgets.append(
            self._make_label(
                "Key Topics",
                kt_box,
                SECTION_TITLE_FS_RATIO,
                COL_WHITE,
                bold=True,
                halign="left",
            )
        )
        # 2x2 grid of topic rows inside the Key Topics card. Each cell has
        # a name row on top (with the percentage right-aligned) and a thin
        # progress bar near the bottom of the cell, with explicit pixel
        # offsets so the bar never visually merges with the name baseline.
        topic_area_x = OV_KEY_CARD["x"] * CANVAS_W + 28.0
        topic_area_y = OV_KEY_CARD["y_top"] * CANVAS_H + 58.0
        topic_area_w = OV_KEY_CARD["w"] * CANVAS_W - 56.0
        topic_area_h = OV_KEY_CARD["h"] * CANVAS_H - 70.0
        cell_w = (topic_area_w - 28.0) / 2.0
        cell_h = topic_area_h / 2.0
        # Fixed pixel offsets within each cell, in canvas coordinates.
        NAME_H = 26.0
        BAR_OFFSET = NAME_H + 10.0  # 10px gap between name baseline and bar
        BAR_H = 8.0
        self._ov_key_topic_rows: list[dict] = []
        for i in range(4):
            col = i % 2
            row = i // 2
            cx = topic_area_x + col * (cell_w + 28.0)
            cy = topic_area_y + row * cell_h
            name_box = canvas_box(cx, cy, cell_w * 0.7, NAME_H)
            pct_box = canvas_box(cx + cell_w * 0.7, cy, cell_w * 0.3, NAME_H)
            bar_box = canvas_box(cx, cy + BAR_OFFSET, cell_w, BAR_H)

            name_lbl = self._make_label("—", name_box, SECTION_BODY_FS_RATIO, COL_MUTED, halign="left")
            pct_lbl = self._make_label("", pct_box, SECTION_HINT_FS_RATIO, COL_HINT, halign="right")
            bar = _ProgressBar(value=0.0, **kivy_hints(bar_box))

            widgets.extend([name_lbl, pct_lbl, bar])
            self._ov_key_topic_rows.append({"name": name_lbl, "pct": pct_lbl, "bar": bar})

        # ─ Action Items (compact, half-width) ─
        actions_card = _GradientCard(**kivy_hints(OV_ACTIONS_CARD))
        widgets.append(actions_card)
        ai2_box, at_box = content_header(OV_ACTIONS_CARD, icon_w=26.0, title_w=240.0)
        ai2_icon = self._make_image("action_items_icon.png", ai2_box)
        if ai2_icon is not None:
            widgets.append(ai2_icon)
        widgets.append(
            self._make_label(
                "Action Items",
                at_box,
                SECTION_TITLE_FS_RATIO,
                COL_WHITE,
                bold=True,
                halign="left",
            )
        )
        # "View all →" link in the top right of the card
        view_all_actions = canvas_box(
            OV_ACTIONS_CARD["x"] * CANVAS_W + OV_ACTIONS_CARD["w"] * CANVAS_W - 130.0,
            OV_ACTIONS_CARD["y_top"] * CANVAS_H + 20.0,
            110.0,
            28.0,
        )
        link_a = _AccentLink(text="View all →", **kivy_hints(view_all_actions))
        self._scaled_labels.append((link_a, SECTION_HINT_FS_RATIO))
        link_a.bind(on_release=lambda *_: self._show_tab("action_items"))
        widgets.append(link_a)

        # Up to 3 compact rows: bullet + task on top + assignee/date underneath
        a_area_x = OV_ACTIONS_CARD["x"] * CANVAS_W + 28.0
        a_area_y = OV_ACTIONS_CARD["y_top"] * CANVAS_H + 60.0
        a_area_w = OV_ACTIONS_CARD["w"] * CANVAS_W - 56.0
        a_area_h = OV_ACTIONS_CARD["h"] * CANVAS_H - 70.0
        row_h_a = a_area_h / 3.0
        self._ov_action_rows: list[dict] = []
        for i in range(3):
            ry = a_area_y + i * row_h_a
            dot = _Dot(color=COL_ACCENT, **kivy_hints(canvas_box(a_area_x, ry + row_h_a * 0.28, 8.0, 8.0)))
            task = self._make_label(
                "",
                canvas_box(a_area_x + 18.0, ry, a_area_w - 18.0, row_h_a * 0.55),
                SECTION_BODY_FS_RATIO,
                COL_WHITE,
                halign="left",
            )
            meta = self._make_label(
                "",
                canvas_box(a_area_x + 18.0, ry + row_h_a * 0.5, a_area_w - 18.0, row_h_a * 0.45),
                SECTION_HINT_FS_RATIO,
                COL_HINT,
                halign="left",
            )
            widgets.extend([dot, task, meta])
            self._ov_action_rows.append({"dot": dot, "task": task, "meta": meta})

        # ─ Decisions Made (compact, half-width) ─
        decisions_card = _GradientCard(**kivy_hints(OV_DECISIONS_CARD))
        widgets.append(decisions_card)
        di_box, dt_box = content_header(OV_DECISIONS_CARD, icon_w=26.0, title_w=240.0)
        di_icon = self._make_image("decisions_icon.png", di_box)
        if di_icon is not None:
            widgets.append(di_icon)
        widgets.append(
            self._make_label(
                "Decisions Made",
                dt_box,
                SECTION_TITLE_FS_RATIO,
                COL_WHITE,
                bold=True,
                halign="left",
            )
        )
        view_all_decisions = canvas_box(
            OV_DECISIONS_CARD["x"] * CANVAS_W + OV_DECISIONS_CARD["w"] * CANVAS_W - 130.0,
            OV_DECISIONS_CARD["y_top"] * CANVAS_H + 20.0,
            110.0,
            28.0,
        )
        link_d = _AccentLink(text="View all →", **kivy_hints(view_all_decisions))
        self._scaled_labels.append((link_d, SECTION_HINT_FS_RATIO))
        link_d.bind(on_release=lambda *_: self._show_tab("decisions"))
        widgets.append(link_d)

        d_area_x = OV_DECISIONS_CARD["x"] * CANVAS_W + 28.0
        d_area_y = OV_DECISIONS_CARD["y_top"] * CANVAS_H + 60.0
        d_area_w = OV_DECISIONS_CARD["w"] * CANVAS_W - 56.0
        d_area_h = OV_DECISIONS_CARD["h"] * CANVAS_H - 70.0
        row_h_d = d_area_h / 3.0
        self._ov_decision_rows: list[dict] = []
        for i in range(3):
            ry = d_area_y + i * row_h_d
            tick = self._make_image(
                "decision_tick.png",
                canvas_box(d_area_x, ry + row_h_d * 0.25, 18.0, 18.0),
            )
            text = self._make_label(
                "",
                canvas_box(d_area_x + 28.0, ry, d_area_w - 28.0, row_h_d * 0.85),
                SECTION_BODY_FS_RATIO,
                COL_MUTED,
                halign="left",
                max_lines=2,
                shorten=True,
            )
            if tick is not None:
                widgets.append(tick)
            widgets.append(text)
            self._ov_decision_rows.append({"tick": tick, "text": text})

        self._render_overview_data()
        return widgets

    def _render_overview_data(self) -> None:
        if not getattr(self, "ov_summary_text", None):
            return
        data = self._summary_data or {}
        summary = data.get("summary")
        if isinstance(summary, dict):
            summary_text = (summary.get("summary") or "").strip()
        else:
            summary_text = (summary or "").strip()
        self.ov_summary_text.text = summary_text or "Summary will appear here once processing finishes."

        topics = self._topics
        placeholders = ("Product Strategy", "Engineering", "Marketing", "Operations")
        for i, row in enumerate(self._ov_key_topic_rows):
            if i < len(topics):
                t = topics[i]
                row["name"].text = (t.get("name") or "—").strip()
                row["name"].color = COL_WHITE
                v = max(0, min(100, int(t.get("value", 0))))
                row["pct"].text = f"{v}%"
                row["bar"].set_value(v / 100.0)
            else:
                row["name"].text = placeholders[i]
                row["name"].color = COL_HINT
                row["pct"].text = "—"
                row["bar"].set_value(0.0)

        for i, row in enumerate(self._ov_action_rows):
            if i < len(self._action_items):
                item = self._action_items[i]
                row["dot"].opacity = 1.0
                row["task"].opacity = 1.0
                row["meta"].opacity = 1.0
                row["task"].text = item.get("task", "")
                assignee = item.get("assignee") or ""
                due = self._format_short_date(item.get("due_date") or "")
                meta = "  ·  ".join([s for s in (assignee, due) if s])
                row["meta"].text = meta
            else:
                row["dot"].opacity = 0.0
                row["task"].opacity = 0.0
                row["meta"].opacity = 0.0

        for i, row in enumerate(self._ov_decision_rows):
            if i < len(self._decisions):
                if row["tick"] is not None:
                    row["tick"].opacity = 1.0
                row["text"].opacity = 1.0
                row["text"].text = self._decisions[i]
            else:
                if row["tick"] is not None:
                    row["tick"].opacity = 0.0
                row["text"].opacity = 0.0

    # ------------------------------------------------------------------
    # Full-card tabs (Key Points / Action Items / Decisions / Participants / Transcript)
    # ------------------------------------------------------------------
    def _build_full_card(
        self,
        *,
        icon_filename: Optional[str],
        title_text: str,
    ) -> tuple[list[Widget], ScrollView, BoxLayout]:
        widgets: list[Widget] = []
        widgets.append(_GradientCard(**kivy_hints(FULL_TAB_CARD)))
        icon_box, title_box = content_header(FULL_TAB_CARD, icon_w=32.0, title_w=400.0)
        if icon_filename:
            img = self._make_image(icon_filename, icon_box)
            if img is not None:
                widgets.append(img)
        widgets.append(
            self._make_label(
                title_text,
                title_box,
                SECTION_TITLE_FS_RATIO,
                COL_WHITE,
                bold=True,
                halign="left",
            )
        )

        # ScrollView spans the card interior below the header.
        sv_box = canvas_box(
            FULL_TAB_CARD["x"] * CANVAS_W + 20.0,
            FULL_TAB_CARD["y_top"] * CANVAS_H + 72.0,
            FULL_TAB_CARD["w"] * CANVAS_W - 40.0,
            FULL_TAB_CARD["h"] * CANVAS_H - 92.0,
        )
        scroll = ScrollView(
            do_scroll_x=False,
            do_scroll_y=True,
            scroll_type=["bars", "content"],
            bar_width=6,
            **kivy_hints(sv_box),
        )
        container = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=8,
            padding=(4, 4, 4, 4),
        )
        container.bind(minimum_height=container.setter("height"))
        scroll.add_widget(container)
        widgets.append(scroll)
        return widgets, scroll, container

    def _make_row_label(
        self,
        text: str,
        *,
        bold: bool = False,
        color: tuple = COL_MUTED,
        halign: str = "left",
    ) -> Label:
        lbl = Label(
            text=text,
            color=color,
            font_name=_FONT_BOLD,
            bold=bold,
            halign=halign,
            valign="middle",
            shorten=False,
            size_hint_y=None,
            size_hint_x=1,
        )
        lbl.bind(
            width=lambda w, *_: setattr(w, "text_size", (w.width - 8, None)),
            texture_size=lambda w, ts: setattr(w, "height", max(28, ts[1] + 6)),
        )
        return lbl

    def _build_key_points(self) -> list[Widget]:
        widgets, _scroll, container = self._build_full_card(
            icon_filename=None,
            title_text="Key Points",
        )
        if not self._topics:
            ph = self._make_row_label(
                "Key topics will appear here once the AI report finishes generating.",
                color=COL_HINT,
            )
            container.add_widget(ph)
        else:
            for t in self._topics:
                row = BoxLayout(orientation="horizontal", size_hint_y=None, height=58)
                left = BoxLayout(orientation="vertical", spacing=4)
                name = self._make_row_label((t.get("name") or "—").strip(), bold=True, color=COL_WHITE)
                left.add_widget(name)
                bar_wrap = BoxLayout(size_hint_y=None, height=10)
                pb = _ProgressBar(value=max(0, min(100, int(t.get("value", 0)))) / 100.0)
                bar_wrap.add_widget(pb)
                left.add_widget(bar_wrap)
                row.add_widget(left)
                pct = self._make_row_label(
                    f"{int(t.get('value', 0))}%",
                    halign="right",
                    color=COL_HINT,
                )
                pct.size_hint_x = None
                pct.width = 80
                row.add_widget(pct)
                container.add_widget(row)
        return widgets

    def _build_action_items_full(self) -> list[Widget]:
        widgets, _scroll, container = self._build_full_card(
            icon_filename="action_items_icon.png",
            title_text="Action Items",
        )
        if not self._action_items:
            container.add_widget(
                self._make_row_label("No action items captured for this meeting.", color=COL_HINT)
            )
            return widgets
        for i, item in enumerate(self._action_items):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=72,
                padding=(4, 6, 4, 6),
                spacing=10,
            )
            check = _ImgBtn(
                source=_png(
                    "action_check_done.png" if item.get("completed") else "action_check_pending.png"
                ),
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
                size_hint=(None, None),
                size=(32, 32),
                pos_hint={"center_y": 0.5},
            )
            check.bind(on_release=lambda _w, idx=i: self._toggle_action(idx))
            row.add_widget(check)

            mid = BoxLayout(orientation="vertical", spacing=2)
            task = self._make_row_label(item.get("task", ""), bold=True, color=COL_WHITE)
            sub_parts: list[str] = []
            if item.get("assignee"):
                sub_parts.append(str(item["assignee"]))
            if item.get("due_date"):
                sd = self._format_short_date(str(item["due_date"]))
                if sd:
                    sub_parts.append(sd)
            if item.get("type"):
                sub_parts.append(str(item["type"]).replace("_", " ").title())
            sub = self._make_row_label("  ·  ".join(sub_parts), color=COL_HINT)
            mid.add_widget(task)
            mid.add_widget(sub)
            row.add_widget(mid)
            container.add_widget(row)
        return widgets

    def _build_decisions_full(self) -> list[Widget]:
        widgets, _scroll, container = self._build_full_card(
            icon_filename="decisions_icon.png",
            title_text="Decisions Made",
        )
        if not self._decisions:
            container.add_widget(
                self._make_row_label("No decisions were recorded for this meeting.", color=COL_HINT)
            )
            return widgets
        for d in self._decisions:
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                padding=(2, 6, 2, 6),
                spacing=10,
            )
            row.bind(minimum_height=row.setter("height"))
            tick_src = _png("decision_tick.png")
            if tick_src:
                tick = Image(
                    source=tick_src,
                    allow_stretch=True,
                    keep_ratio=True,
                    fit_mode="contain",
                    size_hint=(None, None),
                    size=(22, 22),
                    pos_hint={"top": 1},
                )
                row.add_widget(tick)
            else:
                row.add_widget(_Dot(color=COL_ACCENT, size_hint=(None, None), size=(10, 10)))
            text = self._make_row_label(d, color=COL_WHITE)
            row.add_widget(text)
            container.add_widget(row)
        return widgets

    def _build_participants(self) -> list[Widget]:
        widgets, _scroll, container = self._build_full_card(
            icon_filename=None,
            title_text="Participants",
        )
        names = self._resolve_participants()
        if not names:
            container.add_widget(
                self._make_row_label(
                    f"{self._participant_count or 0} participants — names unavailable.",
                    color=COL_HINT,
                )
            )
            return widgets
        for name in names:
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=58,
                padding=(2, 6, 2, 6),
                spacing=12,
            )
            avatar_src = _png("action_avatar.png")
            if avatar_src:
                avatar = Image(
                    source=avatar_src,
                    allow_stretch=True,
                    keep_ratio=True,
                    fit_mode="contain",
                    size_hint=(None, None),
                    size=(40, 40),
                    pos_hint={"center_y": 0.5},
                )
                row.add_widget(avatar)
            row.add_widget(self._make_row_label(name, bold=True, color=COL_WHITE))
            container.add_widget(row)
        return widgets

    def _build_transcript(self) -> list[Widget]:
        widgets, _scroll, container = self._build_full_card(
            icon_filename=None,
            title_text="Transcript",
        )
        if not self._segments:
            container.add_widget(
                self._make_row_label(
                    "Transcript is not available yet.",
                    color=COL_HINT,
                )
            )
            return widgets
        for seg in self._segments:
            try:
                start = float(seg.get("start_time", 0))
            except (TypeError, ValueError):
                start = 0.0
            mm = int(start) // 60
            ss = int(start) % 60
            speaker = seg.get("speaker_id") or "Speaker"
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            line = f"[{mm:02d}:{ss:02d}] {speaker}: {text}"
            container.add_widget(self._make_row_label(line, color=COL_MUTED))
        return widgets

    # ------------------------------------------------------------------
    # Helpers — image/label adders on the fixed canvas
    # ------------------------------------------------------------------
    def _add_image(self, filename: str, box: dict) -> Image | None:
        img = self._make_image(filename, box)
        if img is not None:
            self._canvas.add_widget(img)
        return img

    def _make_image(self, filename: str, box: dict) -> Image | None:
        src = _png(filename)
        if not src:
            return None
        return Image(
            source=src,
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
            **kivy_hints(box),
        )

    def _add_img_btn(self, filename: str, box: dict, *, on_release) -> _ImgBtn | None:
        src = _png(filename)
        if not src:
            return None
        btn = _ImgBtn(
            source=src,
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
            **kivy_hints(box),
        )
        btn.bind(on_release=on_release)
        self._canvas.add_widget(btn)
        return btn

    def _add_label(
        self,
        text: str,
        box: dict,
        fs_ratio: float,
        color: tuple,
        *,
        bold: bool = False,
        halign: str = "center",
        max_lines: int = 1,
        shorten: bool = True,
    ) -> Label:
        lbl = self._make_label(text, box, fs_ratio, color, bold=bold, halign=halign, max_lines=max_lines, shorten=shorten)
        self._canvas.add_widget(lbl)
        return lbl

    def _make_label(
        self,
        text: str,
        box: dict,
        fs_ratio: float,
        color: tuple,
        *,
        bold: bool = False,
        halign: str = "center",
        max_lines: int = 1,
        shorten: bool = True,
    ) -> Label:
        lbl = Label(
            text=text,
            font_name=_FONT_BOLD,
            bold=bold,
            color=color,
            halign=halign,
            valign="middle" if max_lines == 1 else "top",
            markup=False,
            shorten=shorten,
            shorten_from="right",
            max_lines=max_lines,
            **kivy_hints(box),
        )
        lbl.bind(size=lbl.setter("text_size"))
        self._scaled_labels.append((lbl, fs_ratio))
        return lbl

    def _on_root_resize(self, _root, size):
        self._bg.size = size
        w, h = scaled_canvas(size[0], size[1])
        self._canvas.size = (w, h)
        for lbl, ratio in self._scaled_labels:
            if lbl is None:
                continue
            try:
                lbl.font_size = font_px(ratio, h)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_meeting_data(self, meeting_id: str, summary_data: dict):
        """Bind summary content + trigger backend hydration for missing fields."""
        new_meeting = meeting_id and meeting_id != self.meeting_id
        self.meeting_id = meeting_id
        self._summary_data = dict(summary_data or {})
        # For a new meeting always start the user on the Overview tab.
        if new_meeting and self._active_tab != "overview":
            self._show_tab("overview")
        self._apply_local_data()
        self._fetch_meeting_detail()

    def on_enter(self):
        self._apply_local_data()

    def on_leave(self):
        pass

    # ------------------------------------------------------------------
    # Data binding
    # ------------------------------------------------------------------
    def _apply_local_data(self):
        data = self._summary_data or {}
        title = (data.get("title") or self._meeting_title or "Meeting").strip() or "Meeting"
        self._meeting_title = title
        self.meta_title_label.text = title

        summary = data.get("summary")
        if isinstance(summary, dict):
            summary_text = summary.get("summary") or ""
        else:
            summary_text = summary or ""

        # Date / duration in the meta sub-line
        self.meta_date_label.text = self._format_meta_line(data)
        # Footer "Created: …"
        self.footer_left_label.text = "Created: " + self._format_created_line(data)
        # Play Recording pill — show duration like "12:34" if we have one.
        if hasattr(self, "play_pill") and self.play_pill is not None:
            duration_seconds = 0
            try:
                duration_seconds = max(0, int(float(data.get("duration") or 0)))
            except (TypeError, ValueError):
                duration_seconds = 0
            if duration_seconds > 0:
                mm = duration_seconds // 60
                ss = duration_seconds % 60
                self.play_pill.set_duration(f"{mm:02d}:{ss:02d}")
            else:
                self.play_pill.set_duration("")

        # Action items
        raw_actions = data.get("action_items") or data.get("actions") or []
        self._action_items = list(self._coerce_action_items(raw_actions))

        # Decisions
        raw_decisions = data.get("decisions") or []
        self._decisions = [str(d).strip() for d in raw_decisions if str(d).strip()]

        # Topics
        self._topics = list(self._coerce_topics(data.get("topics") or []))

        # Refresh the on-screen content. Overview widgets are already built
        # (they're created once in __init__) so we update their labels
        # in-place via _render_overview_data — no destroy/rebuild.
        self._refresh_after_data_change()

    def _refresh_after_data_change(self) -> None:
        """Apply the latest ``_summary_data`` to existing widgets.

        - Overview tab: refresh its labels / rows in place via
          ``_render_overview_data`` (no widget destruction).
        - Other tabs: their list contents depend on the new data, so we
          invalidate their cached widget trees. The widgets get rebuilt
          lazily the next time the user clicks that sidebar tab. If a
          non-Overview tab is currently active, we rebuild it now.
        """
        # Always update Overview labels — they were built in __init__ and
        # the widget refs (self.ov_summary_text, self._ov_action_rows, …)
        # remain valid even when Overview isn't the active tab.
        try:
            self._render_overview_data()
        except Exception:  # noqa: BLE001
            logger.exception("Overview data refresh failed")

        active = self._active_tab
        # Invalidate every non-Overview tab so the next visit rebuilds with
        # fresh data. Detach them from the canvas first if they happen to
        # be attached (shouldn't normally happen, but guards against the
        # rare case of multiple refreshes interleaving).
        for tid in _TAB_IDS:
            if tid == "overview":
                continue
            for w in self._tab_widgets.get(tid, ()):
                if w is not None and w.parent is self._canvas:
                    self._canvas.remove_widget(w)
            self._tab_widgets[tid] = []

        if active != "overview":
            # Rebuild the currently-shown non-Overview tab right now so the
            # user sees fresh data without having to click the tab again.
            try:
                widgets = [w for w in self._build_tab(active) if w is not None]
            except Exception:  # noqa: BLE001
                logger.exception("Tab %s rebuild failed", active)
                widgets = []
            self._tab_widgets[active] = widgets
            for w in widgets:
                if w.parent is None:
                    self._canvas.add_widget(w)

        Clock.schedule_once(
            lambda _dt: self._on_root_resize(self._root, self._root.size), 0
        )

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
            self._segments = list(detail.get("segments") or [])
            # The flattened detail merges meeting + summary fields, but the
            # summary block may also live under .summary. Try both shapes.
            block = detail.get("summary")
            if isinstance(block, dict):
                merged = {**(self._summary_data or {}), **block}
            else:
                merged = dict(self._summary_data or {})
            for k in (
                "title",
                "duration",
                "started_at",
                "participant_count",
                "attendee_count",
                "attendees",
                "generated_at",
            ):
                if detail.get(k) is not None and merged.get(k) in (None, ""):
                    merged[k] = detail[k]
            self._summary_data = merged

            def _apply(_dt):
                self._apply_local_data()

            Clock.schedule_once(_apply, 0)

        run_async(_run())

    # ------------------------------------------------------------------
    # Coercions
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_action_items(raw):
        for a in raw or []:
            if isinstance(a, dict):
                task = (a.get("task") or a.get("description") or "").strip()
                if not task:
                    continue
                yield {
                    "task": task,
                    "assignee": (a.get("assignee") or "").strip(),
                    "due_date": (a.get("due_date") or "").strip(),
                    "type": (a.get("type") or "").strip(),
                    "completed": bool(a.get("completed", False)),
                }
            else:
                s = str(a).strip()
                if s:
                    yield {
                        "task": s,
                        "assignee": "",
                        "due_date": "",
                        "type": "",
                        "completed": False,
                    }

    @staticmethod
    def _coerce_topics(raw):
        for t in raw or []:
            if isinstance(t, dict):
                name = (t.get("name") or t.get("topic") or "").strip()
                if not name:
                    continue
                try:
                    value = int(round(float(t.get("value") or t.get("percentage") or 0)))
                except (TypeError, ValueError):
                    value = 0
                yield {"name": name, "value": max(0, min(100, value))}
            elif isinstance(t, str) and t.strip():
                yield {"name": t.strip(), "value": 0}

    def _resolve_participants(self) -> list[str]:
        """Best-effort participant list from the merged meeting data."""
        data = self._summary_data or {}
        attendees = data.get("attendees")
        if isinstance(attendees, list):
            names: list[str] = []
            for a in attendees:
                if isinstance(a, dict):
                    n = (a.get("name") or a.get("email") or "").strip()
                else:
                    n = str(a).strip()
                if n:
                    names.append(n)
            if names:
                return names
        # Fallback: distinct action-item assignees
        seen: set[str] = set()
        out: list[str] = []
        for ai in self._action_items:
            a = (ai.get("assignee") or "").strip()
            if a and a.lower() not in seen:
                seen.add(a.lower())
                out.append(a)
        return out

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    def _format_meta_line(self, data: dict) -> str:
        date_part = ""
        started = data.get("started_at") or data.get("start_time")
        if started:
            try:
                if isinstance(started, (int, float)):
                    dt = datetime.fromtimestamp(float(started))
                else:
                    dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                date_part = dt.strftime("%b %d, %I:%M %p").lstrip("0").replace(" 0", " ")
            except Exception:  # noqa: BLE001
                date_part = str(started)
        duration_min = 0
        if data.get("duration"):
            try:
                duration_min = max(0, int(float(data["duration"]) / 60))
            except Exception:  # noqa: BLE001
                duration_min = 0
        self._duration_min = duration_min
        duration_part = f"{duration_min} min" if duration_min else ""
        return "  ·  ".join(p for p in (date_part, duration_part) if p) or "—"

    def _format_created_line(self, data: dict) -> str:
        raw = data.get("generated_at") or data.get("started_at")
        if not raw:
            return "—"
        try:
            if isinstance(raw, (int, float)):
                dt = datetime.fromtimestamp(float(raw))
            else:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.strftime("%b %d, %I:%M %p").lstrip("0").replace(" 0", " ")
        except Exception:  # noqa: BLE001
            return str(raw)

    @staticmethod
    def _format_short_date(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%b %d").lstrip("0").replace(" 0", " ")
        except Exception:  # noqa: BLE001
            return raw[:14]

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_back(self):
        self.goto("home", transition="fade")

    def _on_export(self):
        logger.info("Export pressed for meeting %s", self.meeting_id)

    def _on_share(self):
        logger.info("Share pressed for meeting %s", self.meeting_id)

    def _on_play_recording(self):
        logger.info("Play Recording pressed for meeting %s", self.meeting_id)

    def _toggle_action(self, idx: int):
        if 0 <= idx < len(self._action_items):
            item = self._action_items[idx]
            item["completed"] = not bool(item.get("completed"))
            # Re-render the action_items tab so the checkbox icon swaps.
            if self._active_tab == "action_items":
                self._refresh_after_data_change()
