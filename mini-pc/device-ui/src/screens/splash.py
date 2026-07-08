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

import sys

from screens.base_screen import BaseScreen
from async_helper import run_async
from config import (
    COLORS,
    FONT_SIZES,
    SPLASH_DURATION,
    USE_MOCK_BACKEND,
    get_device_auth_token,
)


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
            text='Pepper AI',
            font_size=self.suf(36),
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
        """Move to next screen based on setup state (server marker is authoritative)."""
        # TEMP (UI testing): skip the desktop login/pairing flow and land on Home
        # so the floating dock + panels can be exercised without a paired device.
        # Gated behind an env var (off by default) — launch with
        # MEETINGBOX_SKIP_LOGIN=1 to enable. Remove once pairing is verified.
        import os
        if os.getenv("MEETINGBOX_SKIP_LOGIN") == "1":
            self.goto('home', transition='fade')
            return
        # Desktop (Windows/macOS): the appliance pairing-code + Wi-Fi onboarding
        # is replaced by an on-device Google sign-in. If we already hold a device
        # token go straight home; otherwise show the sign-in step.
        if not sys.platform.startswith('linux'):
            if (get_device_auth_token() or '').strip():
                # We have a saved token, but it may have expired or been revoked.
                # Verify it against the backend before landing on home so the user
                # isn't stuck on a screen where every request silently fails.
                run_async(self._advance_desktop_with_token_check())
            else:
                self.goto('onboarding_welcome', transition='fade')
            return
        if USE_MOCK_BACKEND:
            if self.app.needs_setup():
                self.goto('welcome', transition='fade')
            else:
                self.goto('home', transition='fade')
            return
        run_async(self._advance_with_backend())

    async def _advance_desktop_with_token_check(self):
        """Desktop only: validate the saved device token, re-auth if it's dead."""
        try:
            status = await self.backend.validate_device_token()
        except Exception:
            status = 'unknown'

        def _go(_clk):
            if self.manager.current != 'splash':
                return
            if status == 'invalid':
                # Token expired/revoked: forget it and send the user back through
                # Google sign-in. "unknown" (offline/blip) keeps the token and
                # proceeds home so a network hiccup never forces a re-login.
                try:
                    from config import clear_stored_device_auth_token
                    clear_stored_device_auth_token()
                except Exception:
                    pass
                try:
                    self.backend.set_device_auth_header(None)
                except Exception:
                    pass
                self.goto('sign_in', transition='fade')
            else:
                self.goto('home', transition='fade')

        Clock.schedule_once(_go, 0)

    async def _advance_with_backend(self):
        need = self.app.needs_setup()
        try:
            info = await self.backend.get_system_info()
            if info.get('setup_complete') is False:
                self.app.clear_local_setup_markers_best_effort()
                need = True
            elif info.get('setup_complete') is True:
                need = False
        except Exception:
            pass

        def _go(_clk):
            if self.manager.current != 'splash':
                return
            if need:
                self.goto('welcome', transition='fade')
            else:
                self.goto('home', transition='fade')

        Clock.schedule_once(_go, 0)
