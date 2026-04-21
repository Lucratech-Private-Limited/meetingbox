"""
Base Screen Class

All screens inherit from this to get common functionality:
- Dark background
- Access to app / backend
- Navigation with history stack
- Lifecycle hooks
"""

from kivy.uix.screenmanager import Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, Rectangle
from kivy.app import App

from config import (
    COLORS,
    DISPLAY_WIDTH,
    FONT_SIZES,
    FOOTER_HEIGHT,
    SPACING,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)


class BaseScreen(Screen):
    """
    Base class for all MeetingBox screens.

    Provides:
    - Dark background canvas
    - Access to app instance and backend client
    - Navigation helpers (goto, go_back with stack)
    - Persistent footer builder
    - Lifecycle hooks
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # --- Display-relative sizing (20% larger than home; see config OTHER_CONTENT_SCALE) ---
    @staticmethod
    def suv(px):
        v = other_screen_vertical_scale()
        return max(1, int(round(float(px) * v)))

    @staticmethod
    def suh(px):
        h = other_screen_horizontal_scale()
        return max(1, int(round(float(px) * h)))

    @staticmethod
    def suf(fs):
        v = other_screen_vertical_scale()
        return max(6, int(round(float(fs) * v)))

    @property
    def app(self):
        return App.get_running_app()

    @property
    def backend(self):
        return self.app.backend

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def goto(self, screen_name: str, transition='fade'):
        """Navigate to another screen with optional transition type."""
        self.app.goto_screen(screen_name, transition=transition)

    def go_back(self):
        """Go back to previous screen in navigation stack."""
        self.app.go_back()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def make_dark_bg(self, widget):
        """Attach a dark background rectangle to *widget*."""
        with widget.canvas.before:
            Color(*COLORS['background'])
            bg = Rectangle(pos=widget.pos, size=widget.size)
        widget.bind(
            pos=lambda w, v: setattr(bg, 'pos', w.pos),
            size=lambda w, v: setattr(bg, 'size', w.size),
        )
        return bg

    def build_footer(self):
        """
        Create the persistent footer bar (20 px).

        Returns a BoxLayout with WiFi / Storage / Dashboard labels
        that can be appended to the bottom of any screen layout.
        """
        footer = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=self.suv(FOOTER_HEIGHT),
            padding=[self.suh(SPACING['screen_padding']), 0],
        )
        with footer.canvas.before:
            Color(*COLORS['background'])
            _fb = Rectangle(pos=footer.pos, size=footer.size)
        footer.bind(
            pos=lambda w, v: setattr(_fb, 'pos', w.pos),
            size=lambda w, v: setattr(_fb, 'size', w.size),
        )

        self._footer_left = Label(
            text='WiFi: ✓   Storage: …GB free',
            font_size=self.suf(FONT_SIZES['tiny']),
            color=COLORS['gray_500'],
            halign='left',
            valign='middle',
            size_hint=(0.5, 1),
        )
        self._footer_left.bind(size=self._footer_left.setter('text_size'))
        footer.add_widget(self._footer_left)

        sep = Label(
            text='|',
            font_size=self.suf(FONT_SIZES['tiny']),
            color=COLORS['gray_700'],
            size_hint=(None, 1),
            width=self.suh(16),
        )
        footer.add_widget(sep)

        self._footer_right = Label(
            text='Dashboard: meetingbox.local',
            font_size=self.suf(FONT_SIZES['tiny']),
            color=COLORS['gray_500'],
            halign='right',
            valign='middle',
            size_hint=(0.5, 1),
        )
        self._footer_right.bind(size=self._footer_right.setter('text_size'))
        footer.add_widget(self._footer_right)

        return footer

    def update_footer(
        self,
        wifi_ok=True,
        free_gb=0,
        privacy_mode=False,
        wired_lan_ok=False,
    ):
        """Update footer labels if footer exists."""
        if not hasattr(self, '_footer_left'):
            return
        if privacy_mode:
            self._footer_left.text = f'Local Mode   Storage: {free_gb:.0f}GB free'
            return
        if wifi_ok:
            link = 'WiFi: ✓'
        elif wired_lan_ok:
            link = 'LAN: ✓'
        else:
            link = 'WiFi: ✗'
        self._footer_left.text = f'{link}   Storage: {free_gb:.0f}GB free'

    # ------------------------------------------------------------------
    # Lifecycle hooks (override in subclasses)
    # ------------------------------------------------------------------

    def on_enter(self):
        pass

    def on_leave(self):
        pass
