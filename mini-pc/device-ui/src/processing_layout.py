"""Processing screen layout — Figma ``397:261`` (VelsLhL4YHeVRZSCEmCrGw).

Canvas: 1260 × 800.  Every value is the exact Figma absolute coordinate (or the
derived position for stroke layers that overflow their bounding box).

Layer reference (Figma node IDs)::

    414:1232  back button (round)
    414:1266  Listening pill (composite)
    415:53    settings button (round)
    399:447   green check badge
    399:432   "Recording complete" headline
    399:434   "Product Sync" meeting title
    399:438   separator dot
    399:436   "32min" duration
    399:456   Ellipse 17 — outer radial glow (orb)
    399:454   Ellipse 16 stroke — soft outer glow (inset -6.64%)
    399:455   Ellipse 16 stroke — wide diffuse glow (inset -39.34%)
    399:451   Ellipse 16 stroke — solid bright rim (no inset)
    399:452   Ellipse 16 stroke — outer rim highlight (inset -1.9% / -3.79%)
    399:477   "Summarizing your meeting…" headline
    399:480   "This may take a few seconds" subtitle
    399:466   3-stage step list card (composite)
    407:639   "We'll notify you…" bottom pill (composite)
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
    """Figma absolute px on 1260×800 → ratio box."""
    return dict(
        x=lx / CANVAS_W,
        y_top=ly / CANVAS_H,
        w=lw / CANVAS_W,
        h=lh / CANVAS_H,
    )


# ── Header (top row) ──────────────────────────────────────────────────────
BACK_BTN = canvas_box(24.013, 21.188, 76.278, 76.278)
LISTENING_PILL = canvas_box(805.157, 21.188, 302.287, 76.278)
SETTINGS_BTN = canvas_box(1159.708, 21.188, 76.278, 76.278)


# ── "Recording complete" status row (Group 45) ────────────────────────────
CHECK_BADGE = canvas_box(100.291, 153.968, 46.614, 46.614)
HEADLINE_LABEL = canvas_box(159.617, 152.555, 360.0, 44.0)
TITLE_LABEL = canvas_box(159.617, 199.170, 180.0, 34.0)
DOT_SEPARATOR = canvas_box(348.900, 216.120, 5.65, 5.65)
DURATION_LABEL = canvas_box(370.088, 199.170, 100.0, 34.0)


# ── Centre orb (left half) ────────────────────────────────────────────────
# Stroke layers — coords account for Figma's per-layer overflow insets.
_RING_ORIGIN_X = 146.906
_RING_ORIGIN_Y = 292.399
_RING_SIZE = 298.049

ORB_GLOW = canvas_box(0.0, 0.0, 927.0, 800.0)
RING_GLOW = canvas_box(
    _RING_ORIGIN_X - _RING_SIZE * 0.0664,
    _RING_ORIGIN_Y - _RING_SIZE * 0.0664,
    _RING_SIZE * 1.1328,
    _RING_SIZE * 1.1328,
)
RING_LIGHTEN = canvas_box(
    _RING_ORIGIN_X - _RING_SIZE * 0.3934,
    _RING_ORIGIN_Y - _RING_SIZE * 0.3934,
    _RING_SIZE * 1.7868,
    _RING_SIZE * 1.7868,
)
RING_SOLID = canvas_box(_RING_ORIGIN_X, _RING_ORIGIN_Y, _RING_SIZE, _RING_SIZE)
RING_OUTER = canvas_box(
    _RING_ORIGIN_X - _RING_SIZE * 0.019,
    _RING_ORIGIN_Y,
    _RING_SIZE * 1.038,
    _RING_SIZE * 1.0379,
)


# ── Bottom-left captions (under orb) ──────────────────────────────────────
HEADLINE_BOTTOM = canvas_box(49.439, 649.776, 540.0, 44.0)
SUBTITLE_BOTTOM = canvas_box(49.439, 707.691, 540.0, 34.0)


# ── Right-side cards ──────────────────────────────────────────────────────
STEPS_CARD = canvas_box(577.735, 261.323, 639.888, 251.435)
NOTIFY_BAR = canvas_box(569.260, 542.422, 658.251, 76.278)


# ── Typography (Figma px on 800-tall canvas) ──────────────────────────────
HEADLINE_FS_RATIO = 36.726 / CANVAS_H   # "Recording complete", "Summarizing…"
TITLE_FS_RATIO = 28.251 / CANVAS_H      # "Product Sync"
DURATION_FS_RATIO = 28.251 / CANVAS_H
SUBTITLE_FS_RATIO = 28.251 / CANVAS_H


# ── Colours (sampled from Figma) ──────────────────────────────────────────
BG_RGB = (1, 8, 26)  # #01081A

COL_WHITE = (1.0, 1.0, 1.0, 1.0)
COL_MUTED = (182 / 255, 186 / 255, 242 / 255, 1.0)   # #B6BAF2
COL_HINT = (155 / 255, 162 / 255, 178 / 255, 1.0)    # #9BA2B2 (notify bar text)
COL_BLUE = (0 / 255, 149 / 255, 255 / 255, 1.0)      # #0095FF (ring stroke)


def scaled_canvas(screen_w: float, screen_h: float) -> tuple[float, float]:
    """Aspect-preserving canvas size for the current screen.

    Uses ``min(sw/cw, sh/ch)`` so design proportions (circles, ring radii,
    composite aspect ratios) survive on any monitor. The remaining screen
    area is filled by the root background colour so the UI always covers
    the whole display.
    """
    if screen_w <= 0 or screen_h <= 0:
        return CANVAS_W, CANVAS_H
    scale = min(screen_w / CANVAS_W, screen_h / CANVAS_H)
    return CANVAS_W * scale, CANVAS_H * scale


def kivy_hints(box: Box) -> dict:
    return {
        "size_hint": (box["w"], box["h"]),
        "pos_hint": {"x": box["x"], "y": 1.0 - box["y_top"] - box["h"]},
    }


# Minimum/maximum font size guards. The min keeps text readable on tiny
# panels (e.g. 800×480) and the cap stops the headline from looking
# cartoonish on 4K displays where the canvas height is huge.
_FONT_MIN_PX = 10
_FONT_MAX_PX = 96


def font_px(fs_ratio: float, canvas_height: float) -> int:
    raw = round(fs_ratio * canvas_height)
    return max(_FONT_MIN_PX, min(_FONT_MAX_PX, raw))
