"""Recording screen layout — Figma ``1031:58`` (dvqlN0JtWQODt6jYbTrbDG, "Copy").

New light-theme recording design. Canvas: 1260 × 800, every value is the exact
Figma absolute coordinate on that reference canvas. The screen is drawn entirely
with Kivy primitives (no PNG assets) so it scales crisply from a 7" panel to a
24" display and needs no exported bitmaps:

  * full-bleed slate wash background (``Layer 35 1`` swirl + 45 % white overlay
    flatten to ≈ #7C8499)
  * centre orb: dark-navy disc + purple ring + 7-bar voice waveform that reacts
    to live mic levels (lavender → deep-purple vertical gradient, rounded caps)
  * top-centre status: red/grey dot + "Recording...." / "Recording Paused"
    + "Started at hh:mm AM"
  * purple timer ``HH:MM:SS``
  * bottom controls: round Pause/Play capsule (in-place toggle) + "Stop
    Recording" capsule

Layer reference (Figma node IDs):
  1031:66  Group 194  centre orb + waveform bars
  1031:75  Group 195  status group (dot + Recording.... + Started at …)
  1031:79  timer 00:01:21
  1031:84  Frame 23   pause button (round)
  1031:88  Frame 22   play button (round, same slot, hidden until paused)
  1031:80  Frame 22   stop-recording capsule
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


# ── Top status bar (wifi + battery) — Group 203 ───────────────────────────
STATUS_BAR = canvas_box(1129.0, 30.0, 91.13, 21.0)

# ── Centre orb (Group 194) ────────────────────────────────────────────────
# 258×258 disc centred at (631, 355).
ORB = canvas_box(502.0, 226.0, 258.0, 258.0)

# Voice waveform — bounding box of the 7 Figma bars (548..714 × 287..423),
# centred on the orb. The bars themselves are generated dynamically.
WAVEBAR = canvas_box(548.0, 287.0, 166.0, 136.0)

# ── Top-centre status group (Group 195) ───────────────────────────────────
# Figma centres the caption at x≈647 and floats the dot at a fixed x=501.
REC_DOT = canvas_box(501.0, 113.0, 16.97, 16.97)
REC_LABEL = canvas_box(447.0, 95.0, 400.0, 50.0)        # centred ≈ 647
STARTED_LABEL = canvas_box(431.0, 152.0, 400.0, 27.0)   # centred ≈ 631

# ── Timer (1031:79) ───────────────────────────────────────────────────────
TIMER = canvas_box(431.0, 500.0, 400.0, 66.0)           # centred ≈ 631

# ── Bottom controls ───────────────────────────────────────────────────────
BTN_PAUSE = canvas_box(351.0, 638.0, 88.0, 88.0)        # Frame 23 (pause)
BTN_PLAY = canvas_box(351.0, 638.0, 88.0, 88.0)         # Frame 22 (play, same slot)
STOP_PILL = canvas_box(479.0, 638.5, 430.0, 88.0)

# ── Typography (Figma px on the 800-tall canvas) ──────────────────────────
REC_LABEL_FS_RATIO = 40.0 / CANVAS_H
STARTED_FS_RATIO = 23.0 / CANVAS_H
TIMER_FS_RATIO = 55.0 / CANVAS_H
STOP_FS_RATIO = 40.0 / CANVAS_H

# ── Colours (sampled from Figma design context) ───────────────────────────
# Background: light paper-swirl bitmap + rgba(255,255,255,0.40) wash. The
# gradient values below are only the fallback when the bitmap is missing.
BG_TOP = (0.965, 0.965, 0.972, 1.0)
BG_BOT = (0.902, 0.910, 0.925, 1.0)

COL_TEXT = (53 / 255, 57 / 255, 59 / 255, 1.0)          # #35393B
COL_PURPLE = (109 / 255, 72 / 255, 204 / 255, 1.0)      # #6D48CC
COL_REC_RED = (254 / 255, 36 / 255, 0 / 255, 1.0)       # #FE2400
COL_REC_GREY = (130 / 255, 134 / 255, 150 / 255, 1.0)   # paused dot
COL_BATT_GREEN = (52 / 255, 199 / 255, 89 / 255, 1.0)   # battery fill (Group 203)

# Waveform vertical gradient (top → bottom).
WAVE_TOP = (164 / 255, 143 / 255, 210 / 255, 1.0)       # #A48FD2
WAVE_BOT = (109 / 255, 73 / 255, 195 / 255, 1.0)        # #6D49C3

# Orb — light translucent glow disc with a soft purple ring (matches Figma,
# where the orb interior is near-transparent over the light background).
ORB_FILL = (1.0, 1.0, 1.0, 0.22)
ORB_RING = (138 / 255, 110 / 255, 205 / 255, 0.85)      # #8A6ECD

# Capsule buttons (pause / play / stop).
PILL_FILL = (244 / 255, 245 / 255, 247 / 255, 1.0)      # #F4F5F7
PILL_BORDER = (1.0, 1.0, 1.0, 1.0)
PILL_SHADOW = (118 / 255, 129 / 255, 127 / 255, 0.30)   # rgba(118,129,127,.3)

COL_WHITE = (1.0, 1.0, 1.0, 1.0)
COL_MUTED = COL_TEXT


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
