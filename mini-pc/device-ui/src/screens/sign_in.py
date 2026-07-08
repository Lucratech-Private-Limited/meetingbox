"""
Desktop onboarding — Google sign-in.

Shown after the splash screen on desktop (Windows/macOS) builds when no device
auth token is stored yet. Tapping "Continue with Google" opens the system
browser to the real Google sign-in (loopback OAuth), then self-pairs the device
and navigates to home. Placeholder branding; final assets can be dropped into
``assets/welcome`` later (LOGO.png) without code changes.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import webbrowser
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

import google_signin
from async_helper import run_async
from config import ASSETS_DIR, COLORS, FONT_SIZES, BACKEND_URL
from platform_compat import bring_app_to_foreground
from screens.base_screen import BaseScreen
from setup_finalize import post_setup_complete_safe, write_local_setup_complete_marker

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")

# How long to wait for the user to finish the Gmail/Calendar consent in the
# browser before continuing to home anyway (the account is already signed in).
_SERVICES_POLL_TIMEOUT = 150.0
_SERVICES_POLL_INTERVAL = 3.0


def _default_device_name() -> str:
    """A sensible auto room name for a desktop install (renameable later)."""
    try:
        host = (socket.gethostname() or "").split(".")[0].strip()
    except Exception:
        host = ""
    return f"Pepper AI - {host}" if host else "Pepper AI Desktop"


class GoogleSignInButton(Button):
    """White, Google-style sign-in button (placeholder styling)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("font_size", BaseScreen.suf(FONT_SIZES["medium"]))
        kwargs.setdefault("bold", True)
        kwargs.setdefault("markup", True)
        super().__init__(**kwargs)
        self.background_color = (0, 0, 0, 0)
        self.background_normal = ""
        self.background_down = ""
        self.color = (0.13, 0.13, 0.13, 1)
        # "G" placeholder uses Google's four brand colors via markup.
        self.text = (
            "[color=4285F4]G[/color][color=EA4335]o[/color]"
            "[color=FBBC05]o[/color][color=4285F4]g[/color]"
            "[color=34A853]l[/color][color=EA4335]e[/color]"
            "    [color=222222]Continue with Google[/color]"
        )
        self._pressed = False
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *_a):
        self.canvas.before.clear()
        with self.canvas.before:
            Color(0, 0, 0, 0.22)
            RoundedRectangle(pos=(self.x + 1, self.y - 2), size=self.size, radius=[dp(10)])
            shade = 0.92 if self._pressed else 1.0
            Color(shade, shade, shade, 1)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])

    def on_press(self):
        self._pressed = True
        self._draw()

    def on_release(self):
        self._pressed = False
        self._draw()


class SignInScreen(BaseScreen):
    """Google sign-in onboarding step for desktop builds."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._busy = False
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(
            orientation="vertical",
            padding=[sv(36), sv(40), sv(36), sv(36)],
            spacing=sv(14),
            size_hint=(1, 1),
        )
        self.make_dark_bg(root)

        root.add_widget(Widget(size_hint=(1, 0.18)))

        if Path(LOGO_PATH).exists():
            logo = Image(source=LOGO_PATH, size_hint=(1, None), height=sv(72), fit_mode="contain")
            root.add_widget(logo)
        else:
            logo = Label(
                text="Pepper AI",
                font_size=sf(FONT_SIZES["huge"]),
                bold=True,
                color=COLORS["white"],
                size_hint=(1, None),
                height=sv(56),
            )
            root.add_widget(logo)

        title = Label(
            text="Connect your Google account",
            font_size=sf(FONT_SIZES["title"]),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(40),
        )
        title.bind(size=title.setter("text_size"))
        root.add_widget(title)

        subtitle = Label(
            text=(
                "One step: sign in with Google and allow Gmail & Calendar "
                "access so Pepper can manage your day."
            ),
            font_size=sf(FONT_SIZES["body"]),
            color=COLORS["gray_300"],
            halign="center",
            valign="top",
            size_hint=(1, None),
            height=sv(44),
        )
        subtitle.bind(size=subtitle.setter("text_size"))
        root.add_widget(subtitle)

        root.add_widget(Widget(size_hint=(1, None), height=sv(8)))

        btn_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=sv(56))
        btn_row.add_widget(Widget(size_hint=(0.12, 1)))
        self._google_btn = GoogleSignInButton(size_hint=(0.76, 1))
        self._google_btn.bind(on_release=self._on_google)
        btn_row.add_widget(self._google_btn)
        btn_row.add_widget(Widget(size_hint=(0.12, 1)))
        root.add_widget(btn_row)

        root.add_widget(Widget(size_hint=(1, None), height=sv(10)))

        # Staged progress: 1) Sign in  2) Connect Gmail & Calendar  3) Done.
        self._stage = Label(
            text="",
            markup=True,
            font_size=sf(FONT_SIZES["small"]),
            color=COLORS["gray_400"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(22),
            opacity=0,
        )
        self._stage.bind(size=self._stage.setter("text_size"))
        root.add_widget(self._stage)

        self._status = Label(
            text="",
            font_size=sf(FONT_SIZES["small"]),
            color=COLORS["gray_300"],
            halign="center",
            valign="top",
            size_hint=(1, None),
            height=sv(54),
        )
        self._status.bind(size=self._status.setter("text_size"))
        root.add_widget(self._status)

        root.add_widget(Widget(size_hint=(1, 1)))

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        if not self._busy:
            self._set_status("")
            self._set_stage(0)
            if self._google_btn:
                self._google_btn.disabled = False

    _STAGE_LABELS = ("Sign in", "Connect Gmail & Calendar", "Done")

    def _set_stage(self, active: int):
        """Render the 1-2-3 stepper; *active* is 1-based (0 hides it)."""
        if not getattr(self, "_stage", None):
            return
        if active <= 0:
            self._stage.opacity = 0
            self._stage.text = ""
            return
        parts = []
        for i, label in enumerate(self._STAGE_LABELS, start=1):
            done = i < active
            cur = i == active
            if done:
                parts.append(f"[color=34C759]{i}. {label} \u2713[/color]")
            elif cur:
                parts.append(f"[color=ffffff][b]{i}. {label}[/b][/color]")
            else:
                parts.append(f"[color=8e8e93]{i}. {label}[/color]")
        self._stage.text = "     ".join(parts)
        self._stage.opacity = 1

    def _returned_to_app(self, status: str = ""):
        """Raise the app window (post-browser) and optionally update the status."""
        try:
            bring_app_to_foreground()
        except Exception:
            pass
        if status:
            self._set_status(status)

    def _set_status(self, text: str, error: bool = False):
        if not self._status:
            return
        self._status.text = text or ""
        self._status.color = (1.0, 0.42, 0.38, 1) if error else COLORS["gray_300"]

    def _on_google(self, _inst):
        if self._busy:
            return
        self._busy = True
        if self._google_btn:
            self._google_btn.disabled = True
        self._set_stage(1)
        self._set_status("Opening your browser to sign in with Google…")

        device_name = (getattr(self.app, "device_name", "") or "").strip() or _default_device_name()
        self.app.device_name = device_name

        async def _flow():
            try:
                token = await asyncio.to_thread(
                    google_signin.sign_in_with_google, BACKEND_URL
                )
            except google_signin.SignInError as exc:
                Clock.schedule_once(lambda _dt, m=str(exc): self._fail(m), 0)
                return
            except Exception as exc:  # defensive
                logger.exception("Google sign-in failed")
                Clock.schedule_once(
                    lambda _dt, m=str(exc): self._fail(m or "Sign-in failed."), 0
                )
                return

            # Account sign-in is done, but DON'T pull the app forward yet — the
            # user still has the Gmail/Calendar consent to complete in the
            # browser. Keep them in the browser flow; we only return to the app
            # once that second step finishes (see _succeed).
            Clock.schedule_once(
                lambda _dt: self._set_status("Signed in. Setting up your account…"),
                0,
            )
            try:
                data = await self.backend.finalize_google_signin(token, device_name)
            except Exception as exc:
                logger.exception("Device activation after sign-in failed")
                Clock.schedule_once(
                    lambda _dt, m=str(exc): self._fail(
                        "Signed in, but activating the device failed: " + (m or "unknown error")
                    ),
                    0,
                )
                return

            owner = ((data or {}).get("owner_email") or "").strip()
            self.app.paired_owner_email = owner

            # Connect Gmail + Calendar (the same combined consent the web app uses).
            # The consent's callback redirects to the hosted dashboard, not back to
            # us, so we open it and poll the device integrations endpoint to confirm.
            await self._connect_google_services(token)

            await post_setup_complete_safe(self.backend, "", "google_signin_desktop_v1")
            write_local_setup_complete_marker(
                "", device_name, "google_signin_desktop_v1",
                extra={"owner_email": owner} if owner else None,
            )
            Clock.schedule_once(lambda _dt: self._succeed(), 0)

        if run_async(_flow()) is None:
            self._fail("App is still starting up. Please try again in a moment.")

    async def _connect_google_services(self, user_jwt: str) -> None:
        """Open the combined Gmail+Calendar consent and wait until it connects.

        Best-effort: the device is already signed in and paired, so if the user
        cancels or it times out we still continue to home (they can connect later
        in Settings → Integrations).
        """
        try:
            auth_url = await self.backend.get_google_services_auth_url(user_jwt)
        except Exception as exc:
            logger.warning("Could not get Google services consent URL: %s", exc)
            return

        Clock.schedule_once(lambda _dt: self._set_stage(2), 0)
        Clock.schedule_once(
            lambda _dt: self._set_status(
                "Next, allow Gmail & Calendar access in your browser…"
            ),
            0,
        )
        # Brief beat so the first login's success page isn't yanked away the
        # instant it appears; the user stays in the browser and flows straight
        # into the Gmail/Calendar consent.
        await asyncio.sleep(0.5)
        try:
            webbrowser.open(auth_url, new=1, autoraise=True)
        except Exception:
            logger.warning("Could not open browser for Google services consent.")

        Clock.schedule_once(
            lambda _dt: self._set_status(
                "Waiting for Gmail & Calendar access — approve it in your browser…"
            ),
            0,
        )

        loop = asyncio.get_event_loop()
        deadline = loop.time() + _SERVICES_POLL_TIMEOUT
        while loop.time() < deadline:
            try:
                items = await self.backend.get_integrations()
                connected = {
                    (it.get("id") or "").lower()
                    for it in (items or [])
                    if it.get("connected")
                }
                if {"gmail", "calendar"}.issubset(connected):
                    return
            except Exception as exc:
                logger.debug("integrations poll error: %s", exc)
            await asyncio.sleep(_SERVICES_POLL_INTERVAL)
        logger.info("Google services consent not confirmed before timeout; continuing.")

    def _fail(self, message: str):
        self._busy = False
        if self._google_btn:
            self._google_btn.disabled = False
        self._set_status(message or "Sign-in failed. Please try again.", error=True)

    def _succeed(self):
        self._busy = False
        poll = getattr(self.app, "_setup_poll", None)
        if poll:
            try:
                poll.cancel()
            except Exception:
                pass
            self.app._setup_poll = None
        self._set_stage(3)
        # Consent is done (the browser was redirected to the hosted dashboard) —
        # bring the app forward so the capabilities tour plays here, not behind
        # the web page.
        self._returned_to_app("You're connected. Let me show you what I can do…")
        self.goto("onboarding_capabilities", transition="slide_left")
