"""
Onboarding — Welcome (desktop first-run)

First screen after the splash on a fresh desktop install (no device token).
Introduces Pepper with an animated greeting, then hands off to the Google
sign-in step. Text/animation only — Pepper's first *spoken* line is deferred to
the post-login capabilities tour (see ``onboarding_capabilities``).

Placeholder-quality by design: copy, logo, and colours live here so they are
trivial to swap when final brand art arrives.
"""

from pathlib import Path

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from config import ASSETS_DIR, COLORS, FONT_SIZES
from components.button import PrimaryButton

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")

GREETING = "Hi, I'm Pepper, your AI executive assistant."
SUBLINE = "Let's get you started."


class OnboardingWelcomeScreen(BaseScreen):
    """Animated brand greeting; CTA continues to Google sign-in."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._entered = False
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(
            orientation="vertical",
            padding=[sv(40), sv(48), sv(40), sv(44)],
            spacing=sv(10),
        )
        self.make_dark_bg(root)

        root.add_widget(Widget(size_hint=(1, 0.22)))

        # Brand lockup (logo if present, else wordmark).
        brand_row = AnchorLayout(anchor_x="center", size_hint=(1, None), height=sv(64))
        if Path(LOGO_PATH).exists():
            self.brand = Image(
                source=LOGO_PATH,
                size_hint=(None, None),
                size=(sv(64), sv(64)),
                fit_mode="contain",
                opacity=0,
            )
        else:
            self.brand = Label(
                text="Pepper AI",
                font_size=sf(FONT_SIZES["title"]),
                bold=True,
                color=COLORS["blue"],
                size_hint=(None, None),
                size=(sv(320), sv(64)),
                halign="center",
                valign="middle",
                opacity=0,
            )
            self.brand.bind(size=self.brand.setter("text_size"))
        brand_row.add_widget(self.brand)
        root.add_widget(brand_row)

        root.add_widget(Widget(size_hint=(1, None), height=sv(26)))

        # Primary greeting line (large, animated).
        self.greeting = Label(
            text=GREETING,
            font_size=sf(34),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(96),
            opacity=0,
        )
        self.greeting.bind(size=self.greeting.setter("text_size"))
        root.add_widget(self.greeting)

        # Secondary line.
        self.subline = Label(
            text=SUBLINE,
            font_size=sf(FONT_SIZES["medium"]),
            color=COLORS["gray_400"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(34),
            opacity=0,
        )
        self.subline.bind(size=self.subline.setter("text_size"))
        root.add_widget(self.subline)

        root.add_widget(Widget(size_hint=(1, None), height=sv(40)))

        # CTA.
        cta_row = AnchorLayout(anchor_x="center", size_hint=(1, None), height=sv(64))
        self.cta = PrimaryButton(
            text="Get Started",
            size_hint=(None, None),
            size=(sv(260), sv(58)),
            opacity=0,
        )
        self.cta.bind(on_release=self._on_continue)
        cta_row.add_widget(self.cta)
        root.add_widget(cta_row)

        root.add_widget(Widget(size_hint=(1, 1)))

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        # Reset to hidden and play the staged entrance.
        for w in (self.brand, self.greeting, self.subline, self.cta):
            w.opacity = 0

        Animation(opacity=1, duration=0.5, t="out_quad").start(self.brand)

        # Greeting rises + fades in slightly after the brand.
        def _greet(_dt):
            self.greeting.opacity = 0
            Animation(opacity=1, duration=0.6, t="out_quad").start(self.greeting)
        Clock.schedule_once(_greet, 0.35)

        Clock.schedule_once(
            lambda _dt: Animation(opacity=1, duration=0.5).start(self.subline), 0.75
        )

        def _cta(_dt):
            Animation(opacity=1, duration=0.5).start(self.cta)
        Clock.schedule_once(_cta, 1.1)

    def on_leave(self):
        for w in (self.brand, self.greeting, self.subline, self.cta):
            Animation.cancel_all(w)

    def _on_continue(self, _inst):
        self.goto("sign_in", transition="slide_left")
