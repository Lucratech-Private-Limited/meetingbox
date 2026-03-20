"""
Splash Screen – Brand introduction during boot

Duration : 2 seconds (auto-advance)
Background: Pure black
Content  : Centred MeetingBox logo text (white/blue)
Transition: Fade-out to Welcome or Home
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, Rectangle
from kivy.animation import Animation
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES, SPLASH_DURATION


class SplashScreen(BaseScreen):
    """Splash screen shown on every boot."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')

        # Pure black background
        with root.canvas.before:
            Color(*COLORS['black'])
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, v: setattr(self._bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._bg, 'size', w.size),
        )

        # Logo text (centred)
        self.logo_label = Label(
            text='MeetingBox AI',
            font_size=36,
            bold=True,
            color=COLORS['white'],
            halign='center',
            valign='middle',
            opacity=0,  # start invisible for fade-in
        )
        root.add_widget(self.logo_label)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        # Fade-in animation
        self.logo_label.opacity = 0
        anim = Animation(opacity=1, duration=0.5)
        anim.start(self.logo_label)

        # Auto-advance after SPLASH_DURATION
        Clock.schedule_once(self._advance, SPLASH_DURATION)

    def on_leave(self):
        Clock.unschedule(self._advance)

    def _advance(self, _dt):
        """Move to next screen based on setup state."""
        if self.app.needs_setup():
            self.goto('welcome', transition='fade')
        else:
            self.goto('home', transition='fade')
