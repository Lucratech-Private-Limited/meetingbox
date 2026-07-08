"""
Floating visual indicator for the local Tony voice assistant.
"""

from kivy.animation import Animation
from kivy.graphics import Color, Ellipse, RoundedRectangle
from kivy.properties import ListProperty, NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import COLORS, FONT_SIZES, other_screen_horizontal_scale, other_screen_vertical_scale


def _suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


def _suf(fs):
    v = other_screen_vertical_scale()
    return max(6, int(round(float(fs) * v)))


class _OrbWidget(Widget):
    pulse_scale = NumericProperty(1.0)
    glow_alpha = NumericProperty(0.0)
    orb_color = ListProperty(list(COLORS["blue"]))

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (_suv(56), _suv(56)))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._glow_color = Color(0.22, 0.53, 0.98, 0.0)
            self._glow = Ellipse()
            self._orb_color = Color(*self.orb_color)
            self._orb = Ellipse()
            self._core_color = Color(1, 1, 1, 0.10)
            self._core = Ellipse()
        self.bind(
            pos=self._redraw,
            size=self._redraw,
            pulse_scale=self._redraw,
            glow_alpha=self._redraw,
            orb_color=self._apply_orb_color,
        )
        self._redraw()

    def _apply_orb_color(self, *_args):
        self._orb_color.rgba = self.orb_color
        self._glow_color.rgba = [self.orb_color[0], self.orb_color[1], self.orb_color[2], self.glow_alpha]

    def _redraw(self, *_args):
        cx, cy = self.center
        base = min(self.width, self.height)
        glow = base * (1.05 + 0.22 * self.pulse_scale)
        orb = base * 0.86
        core = orb * 0.42
        self._glow_color.rgba = [self.orb_color[0], self.orb_color[1], self.orb_color[2], self.glow_alpha]
        self._glow.pos = (cx - glow / 2, cy - glow / 2)
        self._glow.size = (glow, glow)
        self._orb.pos = (cx - orb / 2, cy - orb / 2)
        self._orb.size = (orb, orb)
        self._core.pos = (cx - core / 2, cy - core / 2)
        self._core.size = (core, core)


class VoiceAssistantIndicator(BoxLayout):
    pulse_scale = NumericProperty(1.0)
    glow_alpha = NumericProperty(0.0)

    def __init__(self, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (_suh(236), _suv(74)))
        kwargs.setdefault("spacing", _suh(10))
        kwargs.setdefault("padding", [_suh(10), _suv(9), _suh(14), _suv(9)])
        super().__init__(**kwargs)
        self.opacity = 0.0

        with self.canvas.before:
            self._bg_color = Color(0.07, 0.10, 0.16, 0.92)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[_suv(18)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        orb_wrap = FloatLayout(size_hint=(None, None), size=(_suv(56), _suv(56)))
        self.orb = _OrbWidget(pos=(0, 0))
        orb_wrap.add_widget(self.orb)

        text_col = BoxLayout(orientation="vertical", spacing=_suv(2))
        self.title_label = Label(
            text="Tony",
            font_size=_suf(FONT_SIZES["small"] + 1),
            color=COLORS["white"],
            bold=True,
            halign="left",
            valign="middle",
        )
        self.title_label.bind(size=self.title_label.setter("text_size"))
        text_col.add_widget(self.title_label)

        self.subtitle_label = Label(
            text='Say "Hey Tony"',
            font_size=_suf(FONT_SIZES["tiny"] + 1),
            color=COLORS["gray_300"],
            halign="left",
            valign="middle",
        )
        self.subtitle_label.bind(size=self.subtitle_label.setter("text_size"))
        text_col.add_widget(self.subtitle_label)

        self._orb_text = Label(
            text="T",
            font_size=_suf(FONT_SIZES["medium"]),
            bold=True,
            color=COLORS["white"],
            size_hint=(None, None),
            size=orb_wrap.size,
            pos=(0, 0),
            halign="center",
            valign="middle",
        )
        self._orb_text.bind(size=self._orb_text.setter("text_size"))
        orb_wrap.add_widget(self._orb_text)
        self.add_widget(orb_wrap)
        self.add_widget(text_col)

    def _sync_bg(self, *_args):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _sync_orb_text(self, *_args):
        self._orb_text.size = self.orb.size
        self._orb_text.pos = self.orb.pos

    def _stop_animations(self):
        Animation.cancel_all(self)
        Animation.cancel_all(self.orb)

    def _set_palette(self, rgba):
        self.orb.orb_color = list(rgba)

    def set_state(self, state: str, message: str | None = None):
        self._stop_animations()
        if state == "hidden":
            self.opacity = 0.0
            self.glow_alpha = 0.0
            self.orb.glow_alpha = 0.0
            return

        self.opacity = 1.0
        if state == "idle":
            self._bg_color.rgba = (0.07, 0.10, 0.16, 0.92)
            self.title_label.text = "Tony"
            self.subtitle_label.text = message or 'Say "Hey Tony"'
            self._set_palette(COLORS["blue"])
            anim = Animation(glow_alpha=0.12, pulse_scale=1.02, duration=1.4) + Animation(
                glow_alpha=0.04, pulse_scale=0.98, duration=1.4
            )
        elif state == "wake":
            self._bg_color.rgba = (0.08, 0.13, 0.20, 0.96)
            self.title_label.text = "Listening"
            self.subtitle_label.text = message or 'Heard "Hey Tony"'
            self._set_palette((0.26, 0.72, 0.98, 1))
            anim = Animation(glow_alpha=0.32, pulse_scale=1.18, duration=0.38) + Animation(
                glow_alpha=0.10, pulse_scale=0.98, duration=0.42
            )
        elif state == "starting":
            self._bg_color.rgba = (0.07, 0.15, 0.11, 0.96)
            self.title_label.text = "Starting"
            self.subtitle_label.text = message or "Starting meeting"
            self._set_palette(COLORS["green"])
            anim = Animation(glow_alpha=0.28, pulse_scale=1.14, duration=0.40) + Animation(
                glow_alpha=0.12, pulse_scale=1.00, duration=0.36
            )
        elif state == "speaking":
            self._bg_color.rgba = (0.07, 0.15, 0.11, 0.96)
            self.title_label.text = "Tony"
            self.subtitle_label.text = message or "Meeting start"
            self._set_palette(COLORS["green"])
            anim = Animation(glow_alpha=0.34, pulse_scale=1.20, duration=0.30) + Animation(
                glow_alpha=0.10, pulse_scale=1.00, duration=0.30
            )
        else:
            self._bg_color.rgba = (0.18, 0.08, 0.08, 0.96)
            self.title_label.text = "Voice"
            self.subtitle_label.text = message or "Unavailable"
            self._set_palette(COLORS["red"])
            anim = Animation(glow_alpha=0.18, pulse_scale=1.08, duration=0.45) + Animation(
                glow_alpha=0.04, pulse_scale=1.00, duration=0.45
            )

        anim.repeat = True
        anim.start(self)
        self.orb.glow_alpha = self.glow_alpha
        self.orb.pulse_scale = self.pulse_scale

    def on_glow_alpha(self, *_args):
        self.orb.glow_alpha = self.glow_alpha

    def on_pulse_scale(self, *_args):
        self.orb.pulse_scale = self.pulse_scale
