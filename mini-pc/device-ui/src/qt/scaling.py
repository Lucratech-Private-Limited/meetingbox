"""Figma-relative scaling for tablet layouts (logical frame inside the window).

The layout never uses fixed px for structure: every margin and font derives from
``scale = logical_width / DESIGN_W`` where *logical_width* is the bounded width
that preserves the aspect ratio when letterboxing inside the resized window."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesignFrame:
    """Reference canvas from Figma (width × height px at 1×)."""

    w: int
    h: int


# Idle — same 1024×600 baseline as ``screens/idle.py`` / Figma #338:60 kiosk frame
IDLE_FRAME = DesignFrame(1024, 600)
# Home — primary layout proportions from codebase comments (390:187 baseline on 1024×600 canvas)
HOME_FRAME = DesignFrame(1024, 600)


def logical_width(win_w: int, win_h: int, frame: DesignFrame) -> int:
    """Return the widest width that preserves ``frame`` aspect ratio inside the window."""
    candidate = int(win_h * frame.w / frame.h)
    return min(win_w, max(1, candidate))


def scale_from_window(win_w: int, win_h: int, frame: DesignFrame) -> float:
    """Unitless multiplier: multiply design-pixel values to get screen px."""
    lw = logical_width(win_w, win_h, frame)
    return lw / frame.w


def sp(design_px: float, scale: float) -> int:
    """Scaled integer px used for layouts, margins, min sizes."""
    return max(1, int(round(float(design_px) * scale)))


def spf(design_px: float, scale: float) -> float:
    """Scaled float px (fonts / precise spacing)."""
    return max(1.0, float(design_px) * scale)

