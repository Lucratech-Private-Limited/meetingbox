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
from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
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
        """Attach the premium appliance background to *widget*.

        Most screens call this helper, so this is the global visual pass: a
        deeper navy base plus two soft blue/violet glows. It keeps the UI calm
        and executive without requiring bitmap assets on every page.
        """
        with widget.canvas.before:
            Color(0.035, 0.050, 0.085, 1)
            bg = Rectangle(pos=widget.pos, size=widget.size)
            Color(0.10, 0.34, 0.70, 0.20)
            glow_a = Ellipse(pos=(widget.x - 80, widget.y + widget.height - 220), size=(360, 360))
            Color(0.52, 0.32, 0.92, 0.12)
            glow_b = Ellipse(pos=(widget.x + widget.width - 260, widget.y - 140), size=(420, 420))
        widget.bind(
            pos=lambda w, v: setattr(bg, 'pos', w.pos),
            size=lambda w, v: setattr(bg, 'size', w.size),
        )
        widget.bind(pos=lambda w, v: setattr(glow_a, 'pos', (w.x - 80, w.y + w.height - 220)))
        widget.bind(size=lambda w, v: setattr(glow_a, 'pos', (w.x - 80, w.y + w.height - 220)))
        widget.bind(pos=lambda w, v: setattr(glow_b, 'pos', (w.x + w.width - 260, w.y - 140)))
        widget.bind(size=lambda w, v: setattr(glow_b, 'pos', (w.x + w.width - 260, w.y - 140)))
        return bg

    def attach_card_bg(self, widget, radius=None, color=None, border=True):
        """Attach a reusable glass-card background to any layout/widget."""
        r = radius if radius is not None else self.suv(24)
        fill = color or (0.12, 0.16, 0.23, 0.82)
        with widget.canvas.before:
            Color(0, 0, 0, 0.18)
            shadow = RoundedRectangle(pos=(widget.x + 1, widget.y - 3), size=widget.size, radius=[r])
            Color(*fill)
            bg = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[r])
        def _sync(w, *_):
            shadow.pos = (w.x + 1, w.y - 3)
            shadow.size = w.size
            bg.pos = w.pos
            bg.size = w.size
        widget.bind(pos=_sync, size=_sync)
        return bg

    def build_footer(self):
        """Footer bar removed. Returns a zero-height placeholder so existing
        callers that do ``layout.add_widget(self.build_footer())`` are safe."""
        return Widget(size_hint=(1, None), height=0)

    def update_footer(
        self,
        wifi_ok=True,
        free_gb=0,
        privacy_mode=False,
        wired_lan_ok=False,
        local_ip=None,
    ):
        """Update footer labels if footer exists.

        *local_ip* — optional LAN IPv4 for the **host** (not the container
        bridge), from :func:`local_network.get_primary_ipv4`; shown between
        link and storage on the home screen.
        """
        if not hasattr(self, '_footer_left'):
            return
        ip = (local_ip or "").strip() if local_ip is not None else ""
        if ip in ("", "—"):
            ip = ""
        ip_seg = f"   IP: {ip}" if ip else ""

        if privacy_mode:
            self._footer_left.text = (
                f'Local Mode{ip_seg}   Storage: {free_gb:.0f}GB free'
            )
            return
        if wifi_ok:
            link = 'WiFi: ✓'
        elif wired_lan_ok:
            link = 'LAN: ✓'
        else:
            link = 'WiFi: ✗'
        self._footer_left.text = (
            f'{link}{ip_seg}   Storage: {free_gb:.0f}GB free'
        )

    # ------------------------------------------------------------------
    # Lifecycle hooks (override in subclasses)
    # ------------------------------------------------------------------

    def on_enter(self):
        pass

    def on_leave(self):
        pass
