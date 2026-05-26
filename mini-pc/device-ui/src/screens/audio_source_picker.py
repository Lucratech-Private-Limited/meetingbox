"""Choose PulseAudio default capture source."""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, RoundedRectangle

from components.status_bar import StatusBar
from config import COLORS, SPACING
from hardware import list_pulse_sources, set_default_source
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


class _Row(ButtonBehavior, BoxLayout):
    def __init__(self, title: str, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", 52)
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*COLORS["surface"])
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[8])
        self.bind(pos=self._sync, size=self._sync)
        self.add_widget(
            Label(
                text=title,
                font_size=14,
                color=COLORS["white"],
                halign="left",
                valign="middle",
            )
        )

    def _sync(self, *a):
        self._bg.pos = self.pos
        self._bg.size = self.size


class AudioSourcePickerScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Input device",
                device_name="Input device",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        self.container = GridLayout(cols=1, spacing=6, size_hint_y=None)
        self.container.bind(minimum_height=self.container.setter("height"))
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        sc.add_widget(self.container)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self.container.clear_widgets()
        self.container.add_widget(
            Label(
                text="Loading devices…",
                color=COLORS["gray_500"],
                size_hint_y=None,
                height=self.suv(56),
            )
        )

        import threading

        def _fetch():
            srcs = list_pulse_sources()

            def _apply(_dt):
                self.container.clear_widgets()
                if not srcs:
                    self.container.add_widget(
                        Label(
                            text="No input devices found.\nCheck PulseAudio / USB mic.",
                            color=COLORS["gray_500"],
                            size_hint_y=None,
                            height=self.suv(80),
                        )
                    )
                    return
                for name, desc in srcs:
                    row = _Row(f"{desc}\n[{name}]", size_hint_y=None, height=self.suv(56))
                    row.bind(on_press=lambda inst, n=name: self._pick(n))
                    self.container.add_widget(row)

            Clock.schedule_once(_apply, 0)

        threading.Thread(target=_fetch, daemon=True).start()

    def _pick(self, src_name: str):
        set_default_source(src_name)
        self.go_back()

