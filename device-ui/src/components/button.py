"""
Button Components – PREMIUM APPLE STYLE

Features:
- Gradient backgrounds (simulated two-tone)
- Soft drop shadows
- Press-darken feedback
- Frosted-glass secondary buttons
"""

from kivy.uix.button import Button
from kivy.graphics import Color, RoundedRectangle, Line
from config import COLORS, FONT_SIZES, BORDER_RADIUS


# ---------------------------------------------------------------------------
# Base premium button (gradient + shadow)
# ---------------------------------------------------------------------------

class PremiumButton(Button):
    """Base button with gradient background and drop shadow."""

    def __init__(self, gradient_start=None, gradient_end=None, **kwargs):
        self.gradient_start = gradient_start or COLORS['primary_start']
        self.gradient_end = gradient_end or COLORS['primary_end']
        self._original_start = self.gradient_start
        self._original_end = self.gradient_end

        kwargs.setdefault('font_size', FONT_SIZES['medium'])
        kwargs.setdefault('bold', True)
        kwargs.setdefault('color', COLORS['white'])
        kwargs.setdefault('halign', 'center')
        kwargs.setdefault('valign', 'middle')

        super().__init__(**kwargs)

        # Strip Kivy default background
        self.background_color = (0, 0, 0, 0)
        self.background_normal = ''
        self.background_down = ''
        self.markup = True

        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    # -- rendering -----------------------------------------------------------

    def _draw(self, *_args):
        self.canvas.before.clear()
        with self.canvas.before:
            # Soft shadow (offset down-right)
            Color(*COLORS['shadow'])
            RoundedRectangle(
                pos=(self.x + 2, self.y - 4),
                size=self.size,
                radius=[BORDER_RADIUS],
            )

            # Bottom half – deeper colour
            Color(*self.gradient_end)
            RoundedRectangle(
                pos=self.pos,
                size=(self.width, self.height * 0.5),
                radius=[0, 0, BORDER_RADIUS, BORDER_RADIUS],
            )

            # Top half – brighter colour
            Color(*self.gradient_start)
            RoundedRectangle(
                pos=(self.x, self.y + self.height * 0.5),
                size=(self.width, self.height * 0.5),
                radius=[BORDER_RADIUS, BORDER_RADIUS, 0, 0],
            )

            # Subtle highlight across top edge
            Color(1, 1, 1, 0.12)
            RoundedRectangle(
                pos=(self.x, self.y + self.height - 2),
                size=(self.width, 2),
                radius=[BORDER_RADIUS, BORDER_RADIUS, 0, 0],
            )

    # -- press / release feedback -------------------------------------------

    def on_press(self):
        self.gradient_start = tuple(
            max(0, c - 0.08) if i < 3 else c
            for i, c in enumerate(self._original_start)
        )
        self.gradient_end = tuple(
            max(0, c - 0.08) if i < 3 else c
            for i, c in enumerate(self._original_end)
        )
        self._draw()

    def on_release(self):
        self.gradient_start = self._original_start
        self.gradient_end = self._original_end
        self._draw()


# ---------------------------------------------------------------------------
# Public button classes
# ---------------------------------------------------------------------------

class PrimaryButton(PremiumButton):
    """Primary action – blue gradient."""

    def __init__(self, **kwargs):
        super().__init__(
            gradient_start=COLORS['primary_start'],
            gradient_end=COLORS['primary_end'],
            **kwargs,
        )


class SecondaryButton(Button):
    """Frosted-glass secondary button (dark translucent surface + border)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('font_size', FONT_SIZES['medium'])
        kwargs.setdefault('bold', False)
        kwargs.setdefault('color', COLORS['white'])
        kwargs.setdefault('halign', 'center')
        kwargs.setdefault('valign', 'middle')

        super().__init__(**kwargs)

        self.background_color = (0, 0, 0, 0)
        self.background_normal = ''
        self.background_down = ''

        self._pressed = False
        self.bind(pos=self._draw, size=self._draw)

    def _draw(self, *_args):
        self.canvas.before.clear()
        with self.canvas.before:
            # Subtle shadow
            Color(*COLORS['shadow_light'])
            RoundedRectangle(
                pos=(self.x + 1, self.y - 2),
                size=self.size,
                radius=[BORDER_RADIUS],
            )

            # Semi-transparent surface
            bg = COLORS['gray_700'] if self._pressed else COLORS['surface_light']
            Color(*bg)
            RoundedRectangle(
                pos=self.pos,
                size=self.size,
                radius=[BORDER_RADIUS],
            )

            # Thin border
            Color(*COLORS['gray_600'])
            Line(
                rounded_rectangle=(
                    self.x, self.y,
                    self.width, self.height,
                    BORDER_RADIUS,
                ),
                width=1,
            )

    def on_press(self):
        self._pressed = True
        self._draw()

    def on_release(self):
        self._pressed = False
        self._draw()


class DangerButton(PremiumButton):
    """Red gradient danger button."""

    def __init__(self, **kwargs):
        super().__init__(
            gradient_start=(1.0, 0.30, 0.26, 1),
            gradient_end=(0.93, 0.24, 0.20, 1),
            **kwargs,
        )
