"""
Onboarding — Capabilities tour (desktop first-run, post-login)

After Google sign-in, Pepper introduces herself out loud (her real OpenAI voice
via the app speak path, with an OS-TTS fallback) while a matching capability
card is shown. 2-3 cards auto-advance with narration; the user can swipe / tap
"Next" to move faster, or "Skip" to jump straight to the finish.

Placeholder-quality: every icon (emoji) and line of copy lives in ``CARDS`` so
final art and wording can be swapped in one place.
"""

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES
from components.button import PrimaryButton

# (emoji icon placeholder, on-screen title, first-person caption = spoken line)
CARDS = [
    (
        "\U0001F399",  # studio microphone
        "Just say \u201cHey Pepper\u201d",
        "Just say \u201cHey Pepper\u201d and I'll listen \u2014 hands-free, anytime.",
    ),
    (
        "\U0001F4C5",  # calendar
        "Your calendar, handled",
        "I can check your calendar and schedule meetings for you.",
    ),
    (
        "\u2709",  # envelope
        "Inbox on autopilot",
        "I'll keep an eye on your inbox and draft replies in your voice.",
    ),
]

CARD_DURATION = 5.5  # seconds each card is shown before auto-advancing
SWIPE_MIN_DX = 60    # px horizontal travel to register a swipe


class OnboardingCapabilitiesScreen(BaseScreen):
    """Narrated, swipeable capability cards; advances to onboarding_ready."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._index = 0
        self._auto_event = None
        self._narrate_event = None
        self._touch_start_x = None
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(
            orientation="vertical",
            padding=[sv(32), sv(24), sv(32), sv(32)],
            spacing=sv(8),
        )
        self.make_dark_bg(root)

        # Top bar: "Skip" on the right.
        top = AnchorLayout(anchor_x="right", size_hint=(1, None), height=sv(36))
        self._skip = Button(
            text="Skip",
            font_size=sf(FONT_SIZES["small"]),
            color=COLORS["gray_400"],
            background_color=(0, 0, 0, 0),
            background_normal="",
            background_down="",
            size_hint=(None, None),
            size=(sv(80), sv(36)),
        )
        self._skip.bind(on_release=lambda _i: self._finish())
        top.add_widget(self._skip)
        root.add_widget(top)

        root.add_widget(Widget(size_hint=(1, 0.14)))

        # Card body (fades between entries).
        self._icon = Label(
            text="",
            font_size=sf(72),
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(110),
        )
        root.add_widget(self._icon)

        root.add_widget(Widget(size_hint=(1, None), height=sv(10)))

        self._title = Label(
            text="",
            font_size=sf(FONT_SIZES["title"]),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(44),
        )
        self._title.bind(size=self._title.setter("text_size"))
        root.add_widget(self._title)

        self._caption = Label(
            text="",
            font_size=sf(FONT_SIZES["medium"]),
            color=COLORS["gray_300"],
            halign="center",
            valign="top",
            size_hint=(1, None),
            height=sv(70),
        )
        self._caption.bind(size=self._caption.setter("text_size"))
        root.add_widget(self._caption)

        # Group the fading elements so we can animate them together.
        self._card_widgets = (self._icon, self._title, self._caption)

        root.add_widget(Widget(size_hint=(1, 1)))

        # Progress dots.
        self._dots_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=sv(18),
            spacing=sv(10),
        )
        self._dots_row.add_widget(Widget(size_hint=(1, 1)))
        self._dots = []
        for _ in CARDS:
            dot = Label(text="\u25CF", font_size=sf(12), color=COLORS["gray_600"],
                        size_hint=(None, 1), width=sv(14))
            self._dots.append(dot)
            self._dots_row.add_widget(dot)
        self._dots_row.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(self._dots_row)

        root.add_widget(Widget(size_hint=(1, None), height=sv(18)))

        # Next / Get started CTA.
        cta_row = AnchorLayout(anchor_x="center", size_hint=(1, None), height=sv(60))
        self._cta = PrimaryButton(
            text="Next",
            size_hint=(None, None),
            size=(sv(240), sv(56)),
        )
        self._cta.bind(on_release=lambda _i: self._advance(user=True))
        cta_row.add_widget(self._cta)
        root.add_widget(cta_row)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        self._index = 0
        # Delay the first spoken line so Pepper doesn't start talking while the
        # app is still coming to the foreground behind the browser — the card
        # appears immediately, the voice follows once we're clearly back in-app.
        self._show_card(0, speak=True, speak_delay=0.9)

    def on_leave(self):
        self._cancel_auto()
        self._cancel_narrate()
        for w in self._card_widgets:
            Animation.cancel_all(w)

    def _cancel_auto(self):
        if self._auto_event is not None:
            self._auto_event.cancel()
            self._auto_event = None

    def _cancel_narrate(self):
        if self._narrate_event is not None:
            self._narrate_event.cancel()
            self._narrate_event = None

    def _show_card(self, index: int, speak: bool, speak_delay: float = 0.0):
        self._cancel_auto()
        self._cancel_narrate()
        self._index = index
        icon, title, caption = CARDS[index]

        def _apply(_dt=None):
            self._icon.text = icon
            self._title.text = title
            self._caption.text = caption
            for w in self._card_widgets:
                Animation(opacity=1, duration=0.28).start(w)
            self._update_dots()
            self._cta.text = "Get started" if index == len(CARDS) - 1 else "Next"
            if speak:
                if speak_delay > 0:
                    self._narrate_event = Clock.schedule_once(
                        lambda _dt: self._narrate(caption), speak_delay
                    )
                else:
                    self._narrate(caption)
            # Auto-advance after the card has had time to be read/heard.
            self._auto_event = Clock.schedule_once(
                lambda _dt: self._advance(user=False), CARD_DURATION
            )

        # Fade current out, then swap + fade in.
        if any(w.opacity > 0 for w in self._card_widgets):
            fade = Animation(opacity=0, duration=0.18)
            fade.bind(on_complete=lambda *_a: _apply())
            for w in self._card_widgets:
                fade.start(w)
        else:
            for w in self._card_widgets:
                w.opacity = 0
            _apply()

    def _update_dots(self):
        for i, dot in enumerate(self._dots):
            dot.color = COLORS["blue"] if i == self._index else COLORS["gray_600"]

    def _narrate(self, text: str):
        speak = getattr(self.app, "_speak_text_async", None)
        if callable(speak):
            try:
                speak(text)
            except Exception:
                pass

    def _advance(self, user: bool):
        self._cancel_auto()
        if self._index >= len(CARDS) - 1:
            self._finish()
            return
        self._show_card(self._index + 1, speak=True)

    def _back(self):
        self._cancel_auto()
        if self._index > 0:
            self._show_card(self._index - 1, speak=True)

    def _finish(self):
        self._cancel_auto()
        self.goto("onboarding_ready", transition="slide_left")

    # ------------------------------------------------------------------
    # Swipe support (left = next, right = previous)
    # ------------------------------------------------------------------
    def on_touch_down(self, touch):
        self._touch_start_x = touch.x
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        start = self._touch_start_x
        self._touch_start_x = None
        if start is not None:
            dx = touch.x - start
            if dx <= -SWIPE_MIN_DX:
                self._advance(user=True)
                return True
            if dx >= SWIPE_MIN_DX:
                self._back()
                return True
        return super().on_touch_up(touch)
