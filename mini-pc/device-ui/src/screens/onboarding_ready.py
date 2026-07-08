"""
Onboarding — You're all set (desktop first-run finale)

A friendly close to onboarding: an animated ring + checkmark drawn on a Kivy
Canvas (no external lib), a "You're all set" headline, a brief spoken
confirmation in Pepper's voice, then auto-advance to home.
"""

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Line
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES

READY_LINE = "You're all set. I'm ready whenever you are \u2014 just say Hey Pepper."
AUTO_HOME_DELAY = 4.5  # seconds before auto-advancing to home


class CheckmarkWidget(Widget):
    """A green success ring with a checkmark that draws itself in.

    ``ring`` (0..1) sweeps the circle; ``check`` (0..1) draws the tick. Both are
    plain NumericProperties so they can be driven by :class:`kivy.animation`.
    """

    ring = NumericProperty(0.0)
    check = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, ring=self._redraw, check=self._redraw)

    def _redraw(self, *_a):
        self.canvas.clear()
        cx, cy = self.center
        r = min(self.width, self.height) * 0.42
        with self.canvas:
            # Ring (animated sweep).
            Color(*COLORS["green"])
            if self.ring > 0:
                Line(
                    circle=(cx, cy, r, 0, 360 * self.ring),
                    width=max(2.0, r * 0.06),
                    cap="round",
                )
            # Checkmark: p1 -> elbow -> p2, revealed by ``check``.
            p1 = (cx - r * 0.42, cy + r * 0.02)
            elbow = (cx - r * 0.08, cy - r * 0.30)
            p2 = (cx + r * 0.46, cy + r * 0.34)
            pts = self._partial_check(p1, elbow, p2, self.check)
            if len(pts) >= 4:
                Line(points=pts, width=max(2.0, r * 0.07), cap="round", joint="round")

    @staticmethod
    def _partial_check(p1, elbow, p2, t):
        """Return a polyline for the tick revealed to fraction *t* (0..1).

        Split evenly across the two legs so the tick appears to be drawn.
        """
        if t <= 0:
            return []

        def lerp(a, b, f):
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

        if t <= 0.5:
            end = lerp(p1, elbow, t / 0.5)
            seg = [p1, end]
        else:
            seg = [p1, elbow, lerp(elbow, p2, (t - 0.5) / 0.5)]
        out = []
        for pt in seg:
            out.extend([pt[0], pt[1]])
        return out


class OnboardingReadyScreen(BaseScreen):
    """Checkmark finale; spoken confirmation; auto-advances to home."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._auto_event = None
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

        root.add_widget(Widget(size_hint=(1, 0.16)))

        check_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=sv(150))
        check_row.add_widget(Widget(size_hint=(1, 1)))
        self._check = CheckmarkWidget(size_hint=(None, None), size=(sv(150), sv(150)))
        check_row.add_widget(self._check)
        check_row.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(check_row)

        root.add_widget(Widget(size_hint=(1, None), height=sv(24)))

        self._title = Label(
            text="You're all set",
            font_size=sf(34),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(48),
            opacity=0,
        )
        self._title.bind(size=self._title.setter("text_size"))
        root.add_widget(self._title)

        self._sub = Label(
            text="Taking you to your home screen\u2026",
            font_size=sf(FONT_SIZES["medium"]),
            color=COLORS["gray_400"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(34),
            opacity=0,
        )
        self._sub.bind(size=self._sub.setter("text_size"))
        root.add_widget(self._sub)

        root.add_widget(Widget(size_hint=(1, 1)))

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        self._check.ring = 0.0
        self._check.check = 0.0
        self._title.opacity = 0
        self._sub.opacity = 0

        Animation(ring=1.0, duration=0.55, t="out_quad").start(self._check)
        # Draw the tick once the ring has mostly swept in.
        Clock.schedule_once(
            lambda _dt: Animation(check=1.0, duration=0.45, t="out_quad").start(self._check),
            0.4,
        )

        Clock.schedule_once(
            lambda _dt: Animation(opacity=1, duration=0.45).start(self._title), 0.45
        )
        Clock.schedule_once(
            lambda _dt: Animation(opacity=1, duration=0.45).start(self._sub), 0.7
        )

        # Speak the confirmation shortly after the checkmark lands.
        Clock.schedule_once(lambda _dt: self._narrate(READY_LINE), 0.6)

        self._auto_event = Clock.schedule_once(self._go_home, AUTO_HOME_DELAY)

    def on_leave(self):
        if self._auto_event is not None:
            self._auto_event.cancel()
            self._auto_event = None
        Animation.cancel_all(self._check)

    def on_touch_down(self, touch):
        # Tap to skip the wait.
        self._go_home(0)
        return True

    def _narrate(self, text: str):
        speak = getattr(self.app, "_speak_text_async", None)
        if callable(speak):
            try:
                speak(text)
            except Exception:
                pass

    def _go_home(self, _dt):
        if self._auto_event is not None:
            self._auto_event.cancel()
            self._auto_event = None
        self.goto("home", transition="fade")
