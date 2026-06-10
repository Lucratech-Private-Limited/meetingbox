"""Meeting Summary layout — Figma ``1036:254``
(dvqlN0JtWQODt6jYbTrbDG, "Copy").

Minimal light-theme summary page:

  * back button + purple "Meeting Name" title (top-left)
  * meta line — "Create time HH:MM AM  ·  32 min"
  * one big white rounded card containing a sparkle + "AI Summary" header and a
    single scrollable region with the AI summary narrative only

Canvas 1260 × 800; values are exact Figma absolute coordinates.

Layer reference (Figma node IDs):
  1038:27   Meeting Name           (#6D48CC, 45px SemiBold)
  1038:35   Create time 11:01Am    (#35393B, 30px)
  1038:36   32 min                 (#35393B, 30px)
  1038:21   white card             (rounded 38, white .9, soft shadow)
  1038:51   AI Summary             (#388CC3, 40px SemiBold)
  1053:127  summary body           (#2F2F2F, 30px)
"""

from __future__ import annotations

from typing import TypedDict


class Box(TypedDict):
    x: float
    y_top: float
    w: float
    h: float


CANVAS_W = 1260.0
CANVAS_H = 800.0


def canvas_box(lx: float, ly: float, lw: float, lh: float) -> Box:
    return dict(
        x=lx / CANVAS_W,
        y_top=ly / CANVAS_H,
        w=lw / CANVAS_W,
        h=lh / CANVAS_H,
    )


# ── Header (back button + title + meta) ───────────────────────────────────
BACK_BTN = canvas_box(35.0, 30.0, 48.0, 48.0)
TITLE = canvas_box(98.0, 22.0, 940.0, 58.0)
META = canvas_box(98.0, 92.0, 940.0, 40.0)
STATUS_BAR = canvas_box(1120.0, 24.0, 116.0, 30.0)

# ── Card ──────────────────────────────────────────────────────────────────
CARD = canvas_box(22.0, 184.0, 1216.0, 577.0)
_CARD_X = 22.0
_CARD_Y = 184.0
_CARD_W = 1216.0
_CARD_H = 577.0
CARD_RADIUS = 38.0

# ── Inside the card ───────────────────────────────────────────────────────
AI_SPARKLE = canvas_box(_CARD_X + 44.0, _CARD_Y + 36.0, 40.0, 40.0)
AI_HEADER = canvas_box(_CARD_X + 100.0, _CARD_Y + 32.0, 420.0, 52.0)
AI_SCROLL = canvas_box(
    _CARD_X + 40.0,
    _CARD_Y + 110.0,
    _CARD_W - 80.0,
    _CARD_H - 150.0,
)

# ── Typography (Figma px on the 800-tall canvas) ──────────────────────────
TITLE_FS_RATIO = 45.0 / CANVAS_H
META_FS_RATIO = 28.0 / CANVAS_H
AI_HEADER_FS_RATIO = 38.0 / CANVAS_H
AI_BODY_FS_RATIO = 27.0 / CANVAS_H

# ── Colours ───────────────────────────────────────────────────────────────
BG_TOP = (0.52, 0.55, 0.63, 1.0)
BG_BOT = (0.45, 0.48, 0.56, 1.0)

COL_TITLE = (109 / 255, 72 / 255, 204 / 255, 1.0)       # #6D48CC
COL_META = (53 / 255, 57 / 255, 59 / 255, 1.0)          # #35393B
COL_AI_HEADER = (56 / 255, 140 / 255, 195 / 255, 1.0)   # #388CC3
COL_BODY = (47 / 255, 47 / 255, 47 / 255, 1.0)          # #2F2F2F
COL_HINT = (53 / 255, 57 / 255, 59 / 255, 0.55)

CARD_FILL = (1.0, 1.0, 1.0, 0.92)
CARD_SHADOW = (118 / 255, 129 / 255, 127 / 255, 0.30)

SPARKLE_FILL = (56 / 255, 140 / 255, 195 / 255, 1.0)


def scaled_canvas(screen_w: float, screen_h: float) -> tuple[float, float]:
    if screen_w <= 0 or screen_h <= 0:
        return CANVAS_W, CANVAS_H
    scale = min(screen_w / CANVAS_W, screen_h / CANVAS_H)
    return CANVAS_W * scale, CANVAS_H * scale


def kivy_hints(box: Box) -> dict:
    return {
        "size_hint": (box["w"], box["h"]),
        "pos_hint": {"x": box["x"], "y": 1.0 - box["y_top"] - box["h"]},
    }


_FONT_MIN_PX = 10
_FONT_MAX_PX = 96


def font_px(fs_ratio: float, canvas_height: float) -> int:
    raw = round(fs_ratio * canvas_height)
    return max(_FONT_MIN_PX, min(_FONT_MAX_PX, raw))
