"""Recording screen layout — Figma `863:626` (VelsLhL4YHeVRZSCEmCrGw).

Canvas: 1260 × 800.  Every value is the exact Figma absolute coordinate.

Layer reference (Figma node IDs):
  863:552  back button (round)
  863:594  recording-status group (red dot + "Recording..." + "Started at ...")
  863:598  meeting title group (people + title + participants + video + provider)
  863:554  listening pill
  863:635  centre Frame 19 — rings + side dots + timer + status text
  863:609  pause button (round)
  863:615  stop-recording pill
  863:613  settings button (round)
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


# Frame 19 origin (centre graphic container)
_F19_X = 389.0
_F19_Y = 105.0


def _f19(lx: float, ly: float, lw: float, lh: float) -> Box:
    """Frame-19-local px → canvas ratio box."""
    return canvas_box(_F19_X + lx, _F19_Y + ly, lw, lh)


# ── Header (top row) ──────────────────────────────────────────────────────
BACK_BTN = canvas_box(24.013, 21.188, 76.278, 76.278)

# Recording status group (124.305, 29.664) 189 × 58.90
# In the new Figma `863:626` the header carries only the back button and the
# recording status (red/grey dot + "Recording..." + "Started at …"). The
# previously-rendered meeting-title group (people icon + title + participants
# + video icon + provider) and the top-right Listening pill have been removed
# from the design.
REC_DOT = canvas_box(124.305, 36.726, 19.776, 19.776)
REC_LABEL = canvas_box(151.144, 29.663, 161.0, 34.0)
STARTED_LABEL = canvas_box(124.305, 63.564, 189.0, 25.0)

# ── Frame 19 (centre graphic, 420×420 at 389,105) ─────────────────────────
# Ellipse 18 strokes: dark/gradient sit at exact box; glow overflows by
# inset -16.86% top/bottom, -16.71% left/right (per Figma class).
_GLOW_INSET_X = 0.1671
_GLOW_INSET_Y = 0.1686
_RING_W = 219.845
_RING_H = 217.888

RING_GLOW_W = _RING_W * (1 + 2 * _GLOW_INSET_X)
RING_GLOW_H = _RING_H * (1 + 2 * _GLOW_INSET_Y)
RING_GLOW = _f19(
    101.973 - _RING_W * _GLOW_INSET_X,
    44.684 - _RING_H * _GLOW_INSET_Y,
    RING_GLOW_W,
    RING_GLOW_H,
)
RING_DARK = _f19(101.973, 46.664, _RING_W, _RING_H)
RING_GRADIENT = _f19(101.973, 44.684, _RING_W, _RING_H)

LEFT_VEC = _f19(52.0, 67.473, 36.975, 173.319)
RIGHT_VEC = _f19(331.030, 67.473, 36.975, 173.319)

# Voice wavebar (Group 46) — sits inside the orb, animates with mic input.
# Figma local coords inside Frame 19: (126.951, 111.039) 168.882 × 85.174
WAVEBAR = _f19(126.951, 111.039, 168.882, 85.174)

# Timer + status (centred inside Frame 19)
TIMER = _f19(89.0, 300.0, 243.0, 42.0)
STATUS = _f19(65.0, 346.0, 290.0, 34.0)

# ── Bottom controls ───────────────────────────────────────────────────────
BTN_PAUSE = canvas_box(146.906, 661.727, 101.704, 101.704)
STOP_PILL = canvas_box(285.336, 666.726, 646.951, 101.704)
BTN_SETTINGS = canvas_box(969.013, 661.726, 101.704, 101.704)

# ── Typography (Figma px on 800-tall canvas) ──────────────────────────────
TIMER_FS_RATIO = 35.0 / CANVAS_H
STATUS_FS_RATIO = 28.251 / CANVAS_H
REC_LABEL_FS_RATIO = 28.251 / CANVAS_H
STARTED_FS_RATIO = 21.188 / CANVAS_H

BG_RGB = (1, 8, 26)  # #01081A

# Text colours (Figma)
COL_WHITE = (1.0, 1.0, 1.0, 1.0)
COL_MUTED = (182 / 255, 186 / 255, 242 / 255, 1.0)   # #B6BAF2
COL_BLUE = (0.0, 107 / 255, 249 / 255, 1.0)          # #006BF9

# Status-dot fills (recording vs paused). The previously-exported
# `icon_rec_dot_red.png` is a solid-black PNG (the red was lost in the
# Figma export pipeline), so the dot is now drawn with Kivy primitives
# in two states. Colours sampled from the Figma reference plus a neutral
# grey for the paused state.
COL_REC_DOT_RED = (255 / 255, 59 / 255, 48 / 255, 1.0)   # #FF3B30
COL_REC_DOT_GREY = (130 / 255, 134 / 255, 150 / 255, 1.0)  # #828696

# The orb glow ring asset is a greyscale soft halo. We multiply its
# texture by this colour at runtime to produce the blue halo the Figma
# design calls for, without needing a re-export.
COL_GLOW_BLUE = (0.0, 107 / 255, 249 / 255, 1.0)         # #006BF9


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


def font_px(fs_ratio: float, canvas_height: float) -> int:
    return max(6, round(fs_ratio * canvas_height))
