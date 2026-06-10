"""
Canvas-drawn icon widgets for MeetingBox device UI.

All icons use Kivy graphics primitives (Line, Ellipse, Triangle, Rectangle)
so they render correctly regardless of font support on the device.

Available icons (set via `kind` kwarg):
  'wifi'       – three arcs + dot
  'bluetooth'  – classic BT diamond symbol
  'battery'    – rectangle body + nub + fill level
  'volume'     – speaker cone + two sound-wave arcs
  'brightness' – circle + 8 radiating rays
  'airplane'   – triangle fuselage + wing line
  'settings'   – circle + 6 gear teeth
  'lock'       – padlock body + shackle arc
  'power'      – broken circle + center line

Usage:
    icon = Icon(kind='wifi', size=(24, 24), color=(1, 1, 1, 0.9))
    icon.set_color(COLORS['blue'])     # change color at runtime
    icon.set_level(0.75)               # battery / volume fill, 0-1
"""

from __future__ import annotations

import math
from typing import Tuple

from kivy.graphics import Color, Ellipse, Line, Rectangle, Triangle
from kivy.uix.widget import Widget

RGBA = Tuple[float, float, float, float]


class Icon(Widget):
    """Generic canvas-drawn icon widget.

    Parameters
    ----------
    kind    : str   – one of the supported icon kinds (see module docstring)
    color   : tuple – initial RGBA color, default white
    level   : float – fill ratio 0-1, used by 'battery' and 'volume'
    """

    def __init__(
        self,
        kind: str,
        color: RGBA = (1.0, 1.0, 1.0, 0.9),
        level: float = 1.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._kind = kind
        self._color = color
        self._level = max(0.0, min(1.0, level))
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_color(self, rgba: RGBA):
        self._color = rgba
        self._redraw()

    def set_level(self, level: float):
        """Set fill ratio (0-1). Redraws immediately."""
        self._level = max(0.0, min(1.0, level))
        self._redraw()

    # ------------------------------------------------------------------
    # Draw dispatch
    # ------------------------------------------------------------------

    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < 1 or self.height < 1:
            return

        cx = self.center_x
        cy = self.center_y
        m = min(self.width, self.height)   # icon bounding square
        lw = max(1.2, m * 0.09)           # stroke width

        with self.canvas:
            Color(*self._color)
            fn = {
                "wifi":       self._wifi,
                "bluetooth":  self._bluetooth,
                "battery":    self._battery,
                "volume":     self._volume,
                "brightness": self._brightness,
                "airplane":   self._airplane,
                "settings":   self._settings,
                "lock":       self._lock,
                "power":      self._power,
            }.get(self._kind)
            if fn:
                fn(cx, cy, m, lw)

    # ------------------------------------------------------------------
    # Individual icon drawers
    # ------------------------------------------------------------------

    def _wifi(self, cx, cy, m, lw):
        """Three arcs + center dot."""
        # Center dot
        d = max(3, m * 0.13)
        Ellipse(pos=(cx - d / 2, cy - m * 0.42), size=(d, d))
        # Three arcs (small → large), centered on that dot
        dot_cy = cy - m * 0.42 + d / 2
        for r in (m * 0.22, m * 0.36, m * 0.50):
            Line(
                ellipse=(cx - r, dot_cy - r, r * 2, r * 2, 40, 140),
                width=lw,
            )

    def _bluetooth(self, cx, cy, m, lw):
        """Classic Bluetooth B-diamond symbol.

        Vertical spine + two right-pointing chevrons (top & bottom half).
        """
        half_h = m * 0.44
        half_w = m * 0.26
        top    = cy + half_h
        bot    = cy - half_h
        mid    = cy
        right  = cx + half_w

        # Vertical center spine
        Line(points=[cx, top, cx, bot], width=lw)
        # Upper half: center-top → right-center-upper → center-mid
        Line(points=[cx, top, right, cy + half_h * 0.45, cx, mid], width=lw)
        # Lower half: center-mid → right-center-lower → center-bot
        Line(points=[cx, mid, right, cy - half_h * 0.45, cx, bot], width=lw)

    def _battery(self, cx, cy, m, lw):
        """Rectangle body + positive nub + fill rect."""
        bw = m * 0.84
        bh = m * 0.40
        bx = cx - bw / 2
        by = cy - bh / 2
        nub_w = m * 0.07
        nub_h = bh * 0.42
        body_w = bw - nub_w

        # Body outline
        Line(rectangle=(bx, by, body_w, bh), width=lw)
        # Positive nub (right side)
        Rectangle(pos=(bx + body_w, cy - nub_h / 2), size=(nub_w, nub_h))
        # Fill
        if self._level > 0.02:
            pad = lw * 1.2
            fill_w = max(0, (body_w - pad * 2) * self._level)
            Rectangle(
                pos=(bx + pad, by + pad),
                size=(fill_w, bh - pad * 2),
            )

    def _volume(self, cx, cy, m, lw):
        """Speaker triangle + two sound-wave arcs."""
        # Speaker cone (triangle)
        sx  = cx - m * 0.40
        tw  = m * 0.28
        th  = m * 0.38
        Triangle(points=[
            sx,        cy,
            sx + tw,   cy + th / 2,
            sx + tw,   cy - th / 2,
        ])
        # Sound-wave arcs
        base_x = sx + tw + m * 0.04
        for r in (m * 0.22, m * 0.36):
            arc_cx = base_x + r * 0.08
            Line(
                ellipse=(arc_cx - r, cy - r, r * 2, r * 2, 315, 405),
                width=lw,
            )

    def _brightness(self, cx, cy, m, lw):
        """Circle + 8 short radiating rays."""
        r_c = m * 0.20
        Line(circle=(cx, cy, r_c, 32), width=lw)
        r1, r2 = m * 0.30, m * 0.44
        for angle_deg in range(0, 360, 45):
            rad = math.radians(angle_deg)
            Line(points=[
                cx + math.cos(rad) * r1, cy + math.sin(rad) * r1,
                cx + math.cos(rad) * r2, cy + math.sin(rad) * r2,
            ], width=lw)

    def _airplane(self, cx, cy, m, lw):
        """Simplified airplane silhouette (body + two wings)."""
        # Fuselage line (diagonal, bottom-left to top-right)
        Line(points=[cx - m*0.30, cy - m*0.28, cx + m*0.30, cy + m*0.28],
             width=lw * 1.6)
        # Main wing
        Line(points=[
            cx - m*0.10, cy + m*0.04,
            cx + m*0.10, cy + m*0.04,
            cx + m*0.24, cy + m*0.22,
        ], width=lw)
        # Tail fin
        Line(points=[
            cx - m*0.26, cy - m*0.20,
            cx - m*0.14, cy - m*0.10,
            cx - m*0.04, cy - m*0.10,
        ], width=lw)

    def _settings(self, cx, cy, m, lw):
        """Gear: center circle + 6 rectangular teeth."""
        r_inner = m * 0.22
        r_outer = m * 0.44
        Line(circle=(cx, cy, r_inner, 32), width=lw)
        for i in range(6):
            rad = math.radians(i * 60)
            Line(points=[
                cx + math.cos(rad) * r_inner,
                cy + math.sin(rad) * r_inner,
                cx + math.cos(rad) * r_outer,
                cy + math.sin(rad) * r_outer,
            ], width=lw * 2.2)

    def _lock(self, cx, cy, m, lw):
        """Padlock body rectangle + shackle arc."""
        bw, bh = m * 0.56, m * 0.40
        bx = cx - bw / 2
        by = cy - m * 0.42
        Line(rectangle=(bx, by, bw, bh), width=lw)
        # Shackle
        r = m * 0.22
        Line(
            ellipse=(cx - r, by + bh - r * 0.5, r * 2, r * 2, 0, 180),
            width=lw,
        )

    def _power(self, cx, cy, m, lw):
        """Power symbol: broken circle + vertical line to top."""
        r = m * 0.38
        # Circle arc leaving a gap at the top (30° gap each side)
        Line(ellipse=(cx - r, cy - r, r * 2, r * 2, 210, 510), width=lw * 1.3)
        # Vertical line from center to top
        Line(points=[cx, cy, cx, cy + r * 1.05], width=lw * 1.5)
