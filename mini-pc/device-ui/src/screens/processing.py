"""Processing screen — Figma ``397:261`` (yJqcY4KovVjJ11vjysW533).

Shown immediately after the user taps Stop on the Recording screen. Layout
mirrors the Figma frame (892×573 inset within the 1024×600 device baseline):

- Header — back button (left), "Listening" pill (center-right), settings gear (right).
- Title row — green check badge + ``Recording complete`` headline + meeting
  title and duration.
- Hero — large glowing blue orb on the left half (animated soft pulse).
- ``Summarizing your meeting…`` headline below the orb with a one-line hint.
- Right card — three-stage checklist (Extracting key points / Identifying
  action items / Structuring summary) with per-row state icons.
- Bottom pill — ``We'll notify you when your summary is ready``. Becomes a
  tappable CTA once the transcript or summary is ready, navigating to
  ``summary_review``.

Public API preserved for ``main.py`` to call:

- ``on_enter`` / ``on_leave``
- ``on_processing_started(data)``
- ``on_backend_progress(progress, status, eta)``
- ``on_transcription_ready(meeting_id)``
- ``on_summary_ready(meeting_id, summary_data)``
- ``set_processing_status(text)``
"""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import (
    Color,
    Ellipse,
    Line,
    PopMatrix,
    PushMatrix,
    Rectangle,
    RoundedRectangle,
    Rotate,
)
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import (
    ASSETS_DIR,
    COLORS,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Asset locations
# ---------------------------------------------------------------------------

_FIGMA_DIR: Path = ASSETS_DIR / "processing" / "figma"


def _png(name: str) -> str:
    p = _FIGMA_DIR / name
    return str(p) if p.is_file() else ""


# ---------------------------------------------------------------------------
# Color palette (sampled from Figma node 397:261)
# ---------------------------------------------------------------------------

_BG = (0x01 / 255.0, 0x08 / 255.0, 0x1A / 255.0, 1)            # #01081A
_CARD_BG = (0x00 / 255.0, 0x0F / 255.0, 0x33 / 255.0, 1)        # #000F33
_CARD_BG_ALT = (0x00 / 255.0, 0x0A / 255.0, 0x26 / 255.0, 1)    # #000A26
_CARD_BORDER = (0x21 / 255.0, 0x28 / 255.0, 0x4B / 255.0, 1)    # #21284B
_TEXT_WHITE = (1, 1, 1, 1)
_TEXT_MUTED = (0xB6 / 255.0, 0xBA / 255.0, 0xF2 / 255.0, 1)     # #B6BAF2
_TEXT_HINT = (0x9B / 255.0, 0xA2 / 255.0, 0xB2 / 255.0, 1)      # #9BA2B2
_LISTENING_DOT = (0x00 / 255.0, 0x58 / 255.0, 0xF4 / 255.0, 1)  # #0058F4
_CHECK_RING = (0x40 / 255.0, 0x98 / 255.0, 0xFC / 255.0, 1)     # #4098FC
_DIVIDER = (0x02 / 255.0, 0x17 / 255.0, 0x4D / 255.0, 1)        # #02174D


# ---------------------------------------------------------------------------
# Layout reference
# ---------------------------------------------------------------------------

# Figma frame for 397:261 — width × height in design pixels. All Figma
# positions are mapped through ``_phint`` so the panel layout scales with
# the device while preserving relative placement.
_REF_W = 892
_REF_H = 573


def _hh(px: float) -> int:
    return max(1, int(round(float(px) * other_screen_horizontal_scale())))


def _hv(px: float) -> int:
    return max(1, int(round(float(px) * other_screen_vertical_scale())))


def _hf(fs: float) -> int:
    """Font size that tracks vertical scale, matched to the recording/idle screens."""
    return max(6, int(round(float(fs) * other_screen_vertical_scale())))


def _phint(left_px: float, top_px: float) -> dict:
    """Convert Figma top-left coordinates to a Kivy ``pos_hint`` dict.

    Figma uses top-down coordinates inside an 892×573 reference frame; Kivy
    uses bottom-up. Returning a dict with ``x`` and ``top`` (both fractions
    of the parent) lets the layout scale with the panel automatically.
    """
    return {
        "x": float(left_px) / _REF_W,
        "top": float(_REF_H - top_px) / _REF_H,
    }


def _size_hint_from(w_px: float, h_px: float) -> tuple:
    """Express a Figma rectangle as fractional ``size_hint`` of an 892×573 parent."""
    return (float(w_px) / _REF_W, float(h_px) / _REF_H)


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------


class _RoundCard(FloatLayout):
    """A FloatLayout that paints a rounded gradient-style background.

    Approximates the Figma vertical gradient #000F33 → #000A26 with a single
    flat fill plus a 1 px stroke; the difference is barely visible at 800 px
    height and avoids the cost of layered gradient draws on the device.
    """

    def __init__(self, *, radius: float = 21, fill=_CARD_BG, border=_CARD_BORDER, **kwargs):
        super().__init__(**kwargs)
        self._radius_px = radius
        with self.canvas.before:
            self._fill_color = Color(*fill)
            self._fill = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
            self._border_color = Color(*border)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, radius),
                width=1.2,
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._fill.pos = self.pos
        self._fill.size = self.size
        self._fill.radius = [self._radius_px]
        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self._radius_px,
        )


class _IconButton(ButtonBehavior, FloatLayout):
    """Circular icon button matching the back/settings buttons in the Figma frame."""

    def __init__(self, *, image_path: str, on_press_cb=None, **kwargs):
        super().__init__(**kwargs)
        self._cb = on_press_cb
        with self.canvas.before:
            self._bg_color = Color(*_CARD_BG_ALT)
            self._bg = Ellipse(pos=self.pos, size=self.size)
            self._border_color = Color(*_CARD_BORDER)
            self._border = Line(circle=(self.center_x, self.center_y, max(1, min(self.width, self.height) / 2)), width=1.0)
        self.bind(pos=self._sync_bg, size=self._sync_bg)
        if image_path:
            img = Image(
                source=image_path,
                size_hint=(0.55, 0.55),
                pos_hint={"center_x": 0.5, "center_y": 0.5},
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
            )
            self.add_widget(img)
        self.bind(on_release=self._dispatch)

    def _sync_bg(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        r = max(1, min(self.width, self.height) / 2)
        self._border.circle = (self.center_x, self.center_y, r)

    def _dispatch(self, *_):
        if callable(self._cb):
            self._cb()


class _ListeningPill(ButtonBehavior, FloatLayout):
    """Recording-screen-style listening pill (tap to toggle voice assistant)."""

    def __init__(self, *, on_press_cb=None, **kwargs):
        super().__init__(**kwargs)
        self._cb = on_press_cb
        with self.canvas.before:
            self._bg_color = Color(*_CARD_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[27])
            self._border_color = Color(*_CARD_BORDER)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, 27),
                width=1.0,
            )
        self.bind(pos=self._sync, size=self._sync)

        soundwave_p = _png("icon_soundwave.png")
        if soundwave_p:
            self._soundwave = Image(
                source=soundwave_p,
                size_hint=(None, None),
                size=(_hh(32), _hv(32)),
                pos_hint={"right": 0.94, "center_y": 0.5},
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
            )
            self.add_widget(self._soundwave)

        self.label = Label(
            text="Listening",
            font_size=_hf(20),
            bold=True,
            color=_TEXT_WHITE,
            halign="left",
            valign="middle",
            size_hint=(0.62, None),
            height=_hv(24),
            pos_hint={"x": 0.18, "center_y": 0.5},
        )
        self.label.bind(size=self.label.setter("text_size"))
        self.add_widget(self.label)

        dot = Widget(
            size_hint=(None, None),
            size=(_hh(14), _hv(14)),
            pos_hint={"x": 0.04, "center_y": 0.5},
        )
        with dot.canvas:
            self._dot_color = Color(*_LISTENING_DOT)
            self._dot = Ellipse(pos=dot.pos, size=dot.size)
        dot.bind(
            pos=lambda w, *_: setattr(self._dot, "pos", w.pos),
            size=lambda w, *_: setattr(self._dot, "size", w.size),
        )
        self.add_widget(dot)
        self.bind(on_release=self._dispatch)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [self.height / 2]
        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self.height / 2,
        )

    def _dispatch(self, *_):
        if callable(self._cb):
            self._cb()

    def set_listening(self, listening: bool):
        """Reflect the live voice-assistant state."""
        self.label.text = "Listening" if listening else "Voice off"
        self._dot_color.rgba = _LISTENING_DOT if listening else (0.36, 0.40, 0.55, 1)


class _RotatingIcon(Widget):
    """Image that rotates in place — used for the active-stage spinner.

    Kivy's ``Image`` doesn't expose a built-in rotation property without
    KV magic, so we wrap a child ``Image`` in a ``Rotate`` instruction.
    """

    def __init__(self, *, image_path: str, **kwargs):
        super().__init__(**kwargs)
        self._angle = 0.0
        self._img_path = image_path
        self._img = Image(
            source=image_path,
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
        )
        with self.canvas.before:
            PushMatrix()
            self._rot = Rotate(angle=0, origin=(self.center_x, self.center_y))
        with self.canvas.after:
            PopMatrix()
        self.add_widget(self._img)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._img.pos = self.pos
        self._img.size = self.size
        self._rot.origin = (self.center_x, self.center_y)

    def set_angle(self, angle: float):
        self._angle = angle % 360.0
        self._rot.angle = self._angle

    def set_source(self, path: str):
        if path and path != self._img_path:
            self._img_path = path
            self._img.source = path
            self._img.reload()


class _StepRow(FloatLayout):
    """Single row inside the right-side checklist (icon left, text middle, status icon right)."""

    STATE_PENDING = "pending"
    STATE_ACTIVE = "active"
    STATE_DONE = "done"

    def __init__(self, *, label: str, **kwargs):
        super().__init__(**kwargs)
        self._state = self.STATE_PENDING
        self._row_label_text = label

        # Left mini-tile (36×36 in Figma) — purely decorative; same dark frame.
        self._left_tile = _png("icon_step_pending.png")
        self._left_active = _png("icon_step_active.png")
        self._left_done = _png("icon_step_done.png")

        self.left_icon = Image(
            source=self._left_tile or "",
            size_hint=(None, None),
            allow_stretch=True,
            keep_ratio=True,
            fit_mode="contain",
        )
        self.add_widget(self.left_icon)

        self.label = Label(
            text=label,
            font_size=_hf(18),
            bold=True,
            color=_TEXT_WHITE,
            halign="left",
            valign="middle",
            size_hint=(None, None),
        )
        self.label.bind(size=self.label.setter("text_size"))
        self.add_widget(self.label)

        # Status (right) icon — tick-circle when done, spinner when active, blank when pending.
        self.status_icon = _RotatingIcon(
            image_path=_png("icon_loading.png") or "",
            size_hint=(None, None),
        )
        # Default state is pending → status icon hidden until set_state flips it.
        self.status_icon.opacity = 0.0
        self.add_widget(self.status_icon)

        self.bind(pos=self._layout_children, size=self._layout_children)

    def _layout_children(self, *_):
        h = self.height
        if h <= 0:
            return
        # Left tile — 36×36 anchored to left
        size_left = (_hh(36), _hv(36))
        self.left_icon.size = size_left
        self.left_icon.pos = (self.x + _hh(0), self.y + (h - size_left[1]) / 2)

        # Label
        label_w = _hh(220)
        self.label.size = (label_w, _hv(24))
        self.label.pos = (self.x + _hh(58), self.y + (h - _hv(24)) / 2)

        # Right status icon — 36×36 right-aligned
        size_right = (_hh(36), _hv(36))
        self.status_icon.size = size_right
        self.status_icon.pos = (
            self.right - size_right[0] - _hh(0),
            self.y + (h - size_right[1]) / 2,
        )

    def set_state(self, state: str):
        if state not in (self.STATE_PENDING, self.STATE_ACTIVE, self.STATE_DONE):
            return
        if state == self._state:
            return
        self._state = state
        if state == self.STATE_DONE:
            if self._left_done:
                self.left_icon.source = self._left_done
            self.label.color = _TEXT_WHITE
            self.status_icon.set_source(_png("icon_tick_circle.png") or "")
            self.status_icon.opacity = 1.0
            self.status_icon.set_angle(0)
        elif state == self.STATE_ACTIVE:
            if self._left_active:
                self.left_icon.source = self._left_active
            self.label.color = _TEXT_WHITE
            self.status_icon.set_source(_png("icon_loading.png") or "")
            self.status_icon.opacity = 1.0
        else:  # pending
            if self._left_tile:
                self.left_icon.source = self._left_tile
            self.label.color = _TEXT_MUTED
            self.status_icon.opacity = 0.0
        try:
            self.left_icon.reload()
        except Exception:
            pass

    def state(self) -> str:
        return self._state

    def is_active(self) -> bool:
        return self._state == self.STATE_ACTIVE


class _BottomPill(ButtonBehavior, FloatLayout):
    """Bottom notification pill — becomes a CTA once summary/transcript ready."""

    def __init__(self, *, on_press_cb=None, **kwargs):
        super().__init__(**kwargs)
        self._cb = on_press_cb
        self._enabled = False

        with self.canvas.before:
            self._bg_color = Color(*_CARD_BG)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[27])
            self._border_color = Color(*_CARD_BORDER)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, 27),
                width=1.0,
            )
        self.bind(pos=self._sync, size=self._sync)

        bell_p = _png("icon_bell.png")
        if bell_p:
            self._bell = Image(
                source=bell_p,
                size_hint=(None, None),
                size=(_hh(36), _hv(36)),
                pos_hint={"x": 0.04, "center_y": 0.5},
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
            )
            self.add_widget(self._bell)

        self.label = Label(
            text="We'll notify you when your summary is ready.",
            font_size=_hf(18),
            bold=True,
            color=_TEXT_HINT,
            halign="left",
            valign="middle",
            size_hint=(0.86, None),
            height=_hv(24),
            pos_hint={"x": 0.14, "center_y": 0.5},
        )
        self.label.bind(size=self.label.setter("text_size"))
        self.add_widget(self.label)
        self.bind(on_release=self._dispatch)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [self.height / 2]
        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            self.height / 2,
        )

    def _dispatch(self, *_):
        if self._enabled and callable(self._cb):
            self._cb()

    def set_enabled(self, enabled: bool, label_text: Optional[str] = None):
        self._enabled = bool(enabled)
        if label_text is not None:
            self.label.text = label_text
        if enabled:
            self.label.color = _TEXT_WHITE
            self._bg_color.rgba = _CARD_BG
            self._border_color.rgba = (
                _CHECK_RING[0],
                _CHECK_RING[1],
                _CHECK_RING[2],
                0.55,
            )
        else:
            self.label.color = _TEXT_HINT
            self._bg_color.rgba = _CARD_BG
            self._border_color.rgba = _CARD_BORDER


# ---------------------------------------------------------------------------
# Processing screen
# ---------------------------------------------------------------------------


class ProcessingScreen(BaseScreen):
    """Figma 397:261 — post-meeting processing/summary state."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._meeting_id: Optional[str] = None
        self._meeting_title: str = "Meeting"
        self._meeting_duration_min: int = 0
        self._summary_data: Optional[dict] = None
        self._summary_ready: bool = False
        self._transcript_ready: bool = False
        self._failed_summary_message: str = ""
        self._started_ts: Optional[float] = None

        # Animation state
        self._spin_event = None
        self._pulse_event = None
        self._spin_angle: float = 0.0
        self._pulse_t: float = 0.0
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = FloatLayout(size_hint=(1, 1))
        with root.canvas.before:
            Color(*_BG)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._bg, "size", w.size),
        )

        # ------------------------------------------------------------------
        # Header: back (17,15) — listening pill (570,15) — settings (821,15)
        # ------------------------------------------------------------------
        self.back_btn = _IconButton(
            image_path=_png("btn_back.png"),
            on_press_cb=self._on_back,
            size_hint=_size_hint_from(54, 54),
            pos_hint=_phint(17, 15 + 54),
        )
        root.add_widget(self.back_btn)

        self.listening_pill = _ListeningPill(
            on_press_cb=self._on_toggle_voice,
            size_hint=_size_hint_from(214, 54),
            pos_hint=_phint(570, 15 + 54),
        )
        root.add_widget(self.listening_pill)

        self.settings_btn = _IconButton(
            image_path=_png("btn_settings.png"),
            on_press_cb=self._on_settings,
            size_hint=_size_hint_from(54, 54),
            pos_hint=_phint(821, 15 + 54),
        )
        root.add_widget(self.settings_btn)

        # ------------------------------------------------------------------
        # Title row: green check + "Recording complete" + meeting + duration
        # Figma group: 287×57 at (71, 108)
        # ------------------------------------------------------------------
        title_row = FloatLayout(
            size_hint=_size_hint_from(420, 57),
            pos_hint=_phint(71, 108 + 57),
        )

        check_p = _png("header_check_badge.png")
        if check_p:
            self.title_check = Image(
                source=check_p,
                size_hint=(None, None),
                size=(_hh(33), _hv(33)),
                pos_hint={"x": 0.0, "y": 0.0},
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
            )
            title_row.add_widget(self.title_check)

        self.title_label = Label(
            text="Recording complete",
            font_size=_hf(26),
            bold=True,
            color=_TEXT_WHITE,
            halign="left",
            valign="top",
            size_hint=(None, None),
            size=(_hh(280), _hv(31)),
            pos_hint={"x": 42 / 420, "top": 1.0},
        )
        self.title_label.bind(size=self.title_label.setter("text_size"))
        title_row.add_widget(self.title_label)

        # Sub-row: meeting title • duration
        meeting_strip = BoxLayout(
            orientation="horizontal",
            size_hint=(None, None),
            size=(_hh(280), _hv(24)),
            spacing=_hh(8),
            pos_hint={"x": 42 / 420, "y": 0.0},
        )
        self.meeting_title_label = Label(
            text="Meeting",
            font_size=_hf(20),
            bold=True,
            color=_TEXT_MUTED,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=_hh(180),
        )
        self.meeting_title_label.bind(size=self.meeting_title_label.setter("text_size"))
        meeting_strip.add_widget(self.meeting_title_label)

        dot = Widget(size_hint=(None, 1), width=_hh(10))
        with dot.canvas:
            Color(*_TEXT_MUTED)
            dot_circ = Ellipse(pos=(0, 0), size=(_hh(4), _hv(4)))

        def _sync_dot(w, *_):
            dot_circ.size = (_hh(4), _hv(4))
            dot_circ.pos = (w.center_x - _hh(2), w.center_y - _hv(2))

        dot.bind(pos=_sync_dot, size=_sync_dot)
        meeting_strip.add_widget(dot)

        self.duration_label = Label(
            text="--",
            font_size=_hf(20),
            bold=True,
            color=_TEXT_MUTED,
            halign="left",
            valign="middle",
            size_hint=(None, 1),
            width=_hh(80),
        )
        self.duration_label.bind(size=self.duration_label.setter("text_size"))
        meeting_strip.add_widget(self.duration_label)
        title_row.add_widget(meeting_strip)
        root.add_widget(title_row)

        # ------------------------------------------------------------------
        # Glow orb hero (left center) — 286×286 at (67, 170)
        # The exported PNG already includes the layered blur; rendering it
        # as one image keeps the Kivy graph cheap.
        # ------------------------------------------------------------------
        orb_p = _png("glow_orb_outer.png")
        if orb_p:
            self.glow_orb = Image(
                source=orb_p,
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
                size_hint=_size_hint_from(286, 286),
                pos_hint=_phint(67, 170 + 286),
            )
            root.add_widget(self.glow_orb)
        else:
            # Fallback: filled circle — same colour as the orb's core.
            self.glow_orb = Widget(
                size_hint=_size_hint_from(286, 286),
                pos_hint=_phint(67, 170 + 286),
            )
            with self.glow_orb.canvas:
                Color(0x00 / 255, 0x95 / 255, 0xFF / 255, 1)
                self._orb_fallback = Ellipse(pos=self.glow_orb.pos, size=self.glow_orb.size)
            self.glow_orb.bind(
                pos=lambda w, *_: setattr(self._orb_fallback, "pos", w.pos),
                size=lambda w, *_: setattr(self._orb_fallback, "size", w.size),
            )
            root.add_widget(self.glow_orb)

        # ------------------------------------------------------------------
        # Headline + sub at the bottom-left corner of the orb area
        # Headline at (35, 460), 349×31, 26px bold
        # Sub at (78, 501), 264×24, 20px semi-bold #B6BAF2
        # ------------------------------------------------------------------
        self.headline_label = Label(
            text="Summarizing your meeting…",
            font_size=_hf(26),
            bold=True,
            color=_TEXT_WHITE,
            halign="left",
            valign="middle",
            size_hint=_size_hint_from(420, 36),
            pos_hint=_phint(35, 460 + 36),
        )
        self.headline_label.bind(size=self.headline_label.setter("text_size"))
        root.add_widget(self.headline_label)

        self.subtitle_label = Label(
            text="This may take a few seconds",
            font_size=_hf(20),
            bold=True,
            color=_TEXT_MUTED,
            halign="left",
            valign="middle",
            size_hint=_size_hint_from(420, 28),
            pos_hint=_phint(78, 501 + 28),
        )
        self.subtitle_label.bind(size=self.subtitle_label.setter("text_size"))
        root.add_widget(self.subtitle_label)

        # ------------------------------------------------------------------
        # Right card "20" — 453×178 at (409, 185), 21 radius
        # 3 step rows at row-y 19 / 71 / 123 with two faint dividers between them.
        # ------------------------------------------------------------------
        step_card = _RoundCard(
            radius=21,
            fill=_CARD_BG,
            border=_CARD_BORDER,
            size_hint=_size_hint_from(453, 178),
            pos_hint=_phint(409, 185 + 178),
        )

        # Two horizontal divider lines, semi-transparent
        for div_y_top in (62, 114):
            divider = Widget(
                size_hint=(None, None),
                size=(_hh(404), _hv(2)),
                pos_hint={
                    "x": 16 / 453,
                    "top": (178 - div_y_top) / 178,
                },
            )
            with divider.canvas:
                Color(*_DIVIDER, 0.6)
                line_rect = Rectangle(pos=divider.pos, size=divider.size)
            divider.bind(
                pos=lambda w, _r=line_rect: setattr(_r, "pos", w.pos),
                size=lambda w, _r=line_rect: setattr(_r, "size", w.size),
            )
            step_card.add_widget(divider)

        self.step_extract = _StepRow(
            label="Extracting key points",
            size_hint=_size_hint_from(437, 36),
            pos_hint={"x": 16 / 453, "top": (178 - 19) / 178},
        )
        self.step_actions = _StepRow(
            label="Identifying action items",
            size_hint=_size_hint_from(437, 36),
            pos_hint={"x": 16 / 453, "top": (178 - 71) / 178},
        )
        self.step_summary = _StepRow(
            label="Structuring summary",
            size_hint=_size_hint_from(437, 36),
            pos_hint={"x": 16 / 453, "top": (178 - 123) / 178},
        )
        # Re-anchor the step rows so they fill the inner card width and live
        # at their vertical positions; FloatLayout in FloatLayout uses
        # fractional hints relative to the parent card.
        for step in (self.step_extract, self.step_actions, self.step_summary):
            step_card.add_widget(step)

        root.add_widget(step_card)
        self.step_card = step_card

        # ------------------------------------------------------------------
        # Bottom pill at (403, 384), 466×54, 34 radius
        # Tappable once transcript or summary ready -> summary_review.
        # ------------------------------------------------------------------
        self.notify_pill = _BottomPill(
            on_press_cb=self._open_summary,
            size_hint=_size_hint_from(466, 54),
            pos_hint=_phint(403, 384 + 54),
        )
        root.add_widget(self.notify_pill)

        self.add_widget(root)

        # Default progress: stage 1 active, others pending.
        self.step_extract.set_state(_StepRow.STATE_ACTIVE)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        self._started_ts = time.monotonic()
        self._summary_data = None
        self._summary_ready = False
        mid = getattr(self.app, "current_session_id", None)
        self._meeting_id = mid

        cache = {}
        try:
            cache = getattr(self.app, "_processing_summary_cache", {}) or {}
        except Exception:
            cache = {}
        cached = cache.get(mid) if mid else None
        if isinstance(cached, dict) and cached.get("ok") is True and mid:
            self._summary_data = cached.get("summary") or {}
            self._summary_ready = True
        elif isinstance(cached, dict) and cached.get("ok") is False and mid:
            # Failed path handled after transcript flags below
            self._failed_summary_message = str(cached.get("error") or "")
        else:
            self._failed_summary_message = ""

        done_for = getattr(self.app, "_transcription_done_for_session", None)
        self._transcript_ready = bool(mid and done_for == mid)

        # Reset visuals to the initial Figma state.
        self.headline_label.text = "Summarizing your meeting…"
        self.subtitle_label.text = "This may take a few seconds"
        self.notify_pill.set_enabled(
            False, "We'll notify you when your summary is ready."
        )
        self.duration_label.text = self._format_duration(self._meeting_duration_min)
        self.meeting_title_label.text = self._meeting_title or "Meeting"

        self.step_extract.set_state(_StepRow.STATE_ACTIVE)
        self.step_actions.set_state(_StepRow.STATE_PENDING)
        self.step_summary.set_state(_StepRow.STATE_PENDING)

        if self._summary_ready:
            self.step_extract.set_state(_StepRow.STATE_DONE)
            self.step_actions.set_state(_StepRow.STATE_DONE)
            self.step_summary.set_state(_StepRow.STATE_DONE)
            self.headline_label.text = "Analysis complete!"
            self.subtitle_label.text = (
                "Your meeting highlights, transcript, and action items are ready."
            )
            self._enable_summary_cta(text="Tap to view meeting summary")
        elif self._failed_summary_message and self._transcript_ready:
            self.on_summary_failed(mid, self._failed_summary_message)
        elif self._transcript_ready:
            # Came back to processing screen after transcription finished —
            # reflect what we already know; summary may still be in flight.
            self.step_extract.set_state(_StepRow.STATE_DONE)
            self.step_actions.set_state(_StepRow.STATE_ACTIVE)
            self.subtitle_label.text = "Transcription done. Building meeting report…"
            self._enable_summary_cta()

        # Reflect live voice-assistant state on the listening pill.
        self._sync_listening_pill()

        self._start_animations()

    def on_leave(self):
        self._stop_animations()

    # ------------------------------------------------------------------
    # Public API — called from main.py WS dispatchers + summary poller
    # ------------------------------------------------------------------

    def on_processing_started(self, data):
        title = (data or {}).get("title") or self._meeting_title or "Meeting"
        title = str(title).strip() or "Meeting"
        duration = int(((data or {}).get("duration") or 0) / 60)
        self._meeting_title = title
        self._meeting_duration_min = duration
        self.meeting_title_label.text = title
        self.duration_label.text = self._format_duration(duration)

    def on_backend_progress(self, progress: int, status: str, eta: int):
        """Drive the 3-stage checklist from a 0-100 progress value."""
        if status:
            self.set_processing_status(status)
        p = max(0, min(100, int(progress or 0)))
        if p < 34:
            self._set_active_stage(0)
        elif p < 67:
            self._set_active_stage(1)
        elif not self._summary_ready:
            self._set_active_stage(2)

    def on_transcription_ready(self, meeting_id: str):
        """Transcript saved server-side — summary is still being built."""
        try:
            if meeting_id:
                self.app._transcript_cta_satisfied_meeting_id = meeting_id
        except Exception:
            pass
        if meeting_id:
            self._meeting_id = meeting_id
        self._transcript_ready = True
        # Stage 1 = done, stage 2 = active, stage 3 = pending.
        self._set_active_stage(1)
        self.step_extract.set_state(_StepRow.STATE_DONE)
        self.subtitle_label.text = "Transcription done. Building meeting report…"
        self._enable_summary_cta()
        try:
            cache = getattr(self.app, "_processing_summary_cache", {}) or {}
            ent = cache.get(meeting_id)
            if isinstance(ent, dict) and ent.get("ok") is False:
                self.on_summary_failed(meeting_id, str(ent.get("error") or ""))
        except Exception:
            pass

    def on_summary_ready(self, meeting_id: str, summary_data: dict):
        self._meeting_id = meeting_id
        self._summary_data = summary_data or {}
        self._summary_ready = True
        for step in (self.step_extract, self.step_actions, self.step_summary):
            step.set_state(_StepRow.STATE_DONE)

        self.headline_label.text = "Analysis complete!"
        self.subtitle_label.text = (
            "Your meeting highlights, transcript, and action items are ready."
        )
        self._enable_summary_cta(text="Tap to view meeting summary")

    def on_summary_failed(self, meeting_id: str, detail: str):
        """Full report failed — keep transcript path usable."""
        if meeting_id:
            self._meeting_id = meeting_id
        self._summary_ready = False
        self._summary_data = {}
        # Progress: transcription done; report step skipped or failed.
        self.step_extract.set_state(_StepRow.STATE_DONE)
        self.step_actions.set_state(_StepRow.STATE_DONE)
        self.step_summary.set_state(_StepRow.STATE_DONE)
        self.headline_label.text = "Transcript ready"
        self.subtitle_label.text = (detail or "Full report could not be generated.")[:240]
        if self._transcript_ready:
            self._enable_summary_cta(text="Tap to view transcript & actions")
        else:
            self.notify_pill.set_enabled(
                False,
                "Waiting for transcript — you can retry from Meetings if this takes too long.",
            )
        if not text:
            return
        low = text.lower()
        if "transcribing" in low:
            self._set_active_stage(0)
            self.subtitle_label.text = text
        elif "transcription done" in low or "building" in low:
            self._set_active_stage(1)
            self.subtitle_label.text = text
        elif "structuring" in low or "key points" in low or "action item" in low:
            self._set_active_stage(2)
            self.subtitle_label.text = text
        else:
            # Generic update — surface it as the subtitle hint.
            self.subtitle_label.text = text

    # ------------------------------------------------------------------
    # Helpers — interaction
    # ------------------------------------------------------------------

    def _on_back(self):
        self.goto("home", transition="fade")

    def _on_settings(self):
        self.goto("settings", transition="fade")

    def _on_toggle_voice(self):
        app = self.app
        try:
            new_paused = not getattr(app, "user_voice_paused", False)
            app.user_voice_paused = new_paused
            if hasattr(app, "_sync_voice_assistant_state"):
                app._sync_voice_assistant_state()
        except Exception as e:
            logger.debug("Failed to toggle voice on processing screen: %s", e)
        self._sync_listening_pill()

    def _sync_listening_pill(self):
        try:
            paused = bool(getattr(self.app, "user_voice_paused", False))
            self.listening_pill.set_listening(not paused)
        except Exception:
            pass

    def _open_summary(self):
        if not self._meeting_id:
            logger.info("Summary CTA pressed but meeting_id is not set")
            return
        if not (self._transcript_ready or self._summary_ready):
            logger.info(
                "Summary CTA pressed before transcript was ready (meeting_id=%s)",
                self._meeting_id,
            )
            return
        try:
            scr = self.app.screen_manager.get_screen("summary_review")
        except Exception as e:
            logger.warning("summary_review screen missing: %s", e)
            return
        payload = self._summary_data if self._summary_ready else {}
        if hasattr(scr, "set_meeting_data"):
            try:
                scr.set_meeting_data(self._meeting_id, payload or {})
            except Exception as e:
                logger.warning("set_meeting_data failed: %s", e)
        self.goto("summary_review", transition="fade")

    def _enable_summary_cta(self, text: Optional[str] = None):
        if text is None:
            text = "Transcript ready — tap to view summary"
        self.notify_pill.set_enabled(True, text)

    def _set_active_stage(self, idx: int):
        rows = (self.step_extract, self.step_actions, self.step_summary)
        for i, row in enumerate(rows):
            if self._summary_ready:
                row.set_state(_StepRow.STATE_DONE)
                continue
            if i < idx:
                row.set_state(_StepRow.STATE_DONE)
            elif i == idx:
                row.set_state(_StepRow.STATE_ACTIVE)
            else:
                row.set_state(_StepRow.STATE_PENDING)

    @staticmethod
    def _format_duration(min_value: int) -> str:
        m = max(0, int(min_value or 0))
        if m <= 0:
            return "--"
        return f"{m} min"

    # ------------------------------------------------------------------
    # Animations — orb pulse + active-stage spinner
    # ------------------------------------------------------------------

    def _start_animations(self):
        self._stop_animations()
        self._spin_event = Clock.schedule_interval(self._tick_spin, 1.0 / 30.0)
        self._pulse_event = Clock.schedule_interval(self._tick_pulse, 1.0 / 20.0)

    def _stop_animations(self):
        if self._spin_event:
            self._spin_event.cancel()
            self._spin_event = None
        if self._pulse_event:
            self._pulse_event.cancel()
            self._pulse_event = None

    def _tick_spin(self, dt: float):
        # Rotate at 360°/1.2 s — same cadence as the recording-screen waveform pulse.
        self._spin_angle = (self._spin_angle - 360.0 * dt / 1.2) % 360.0
        for row in (self.step_extract, self.step_actions, self.step_summary):
            if row.is_active():
                row.status_icon.set_angle(self._spin_angle)

    def _tick_pulse(self, dt: float):
        # Gentle scale breathing on the orb: ±2% over ~2 s.
        if not hasattr(self, "glow_orb") or self.glow_orb is None:
            return
        self._pulse_t = (self._pulse_t + dt) % (2.0 * math.pi)
        # Avoid rebuilding size_hint constantly; nudge opacity for a soft pulse.
        amp = 0.5 + 0.5 * math.sin(self._pulse_t * math.pi)  # 0..1
        self.glow_orb.opacity = 0.85 + 0.15 * amp
