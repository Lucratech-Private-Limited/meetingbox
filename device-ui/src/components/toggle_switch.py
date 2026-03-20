"""
Toggle Switch Component – iOS-style

ON  = Blue background, knob right
OFF = Gray background, knob left
"""

from kivy.uix.widget import Widget
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, RoundedRectangle, Ellipse
from kivy.animation import Animation

from config import COLORS


class ToggleSwitch(ButtonBehavior, Widget):
    """
    iOS-style toggle switch.

    Properties
    ----------
    active : bool – current state
    on_toggle : callable(bool) – callback when toggled
    """

    def __init__(self, active=False, on_toggle=None, **kwargs):
        kwargs.setdefault('size_hint', (None, None))
        kwargs.setdefault('size', (52, 30))
        super().__init__(**kwargs)

        self._active = active
        self._on_toggle = on_toggle

        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, value):
        self._active = value
        self._draw()

    def _draw(self, *_args):
        self.canvas.clear()
        w, h = self.size
        x, y = self.pos
        radius = h / 2

        with self.canvas:
            # Track
            if self._active:
                Color(*COLORS['blue'])
            else:
                Color(*COLORS['gray_700'])
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[radius])

            # Knob
            knob_d = h - 4
            knob_y = y + 2
            if self._active:
                knob_x = x + w - knob_d - 2
            else:
                knob_x = x + 2
            Color(*COLORS['white'])
            Ellipse(pos=(knob_x, knob_y), size=(knob_d, knob_d))

    def on_press(self):
        self._active = not self._active
        self._draw()
        if self._on_toggle:
            self._on_toggle(self._active)
