"""System speaker / notification playback level via PulseAudio default sink."""

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.slider import Slider

from async_helper import run_async
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from hardware import set_sink_volume_pct
from screens.base_screen import BaseScreen


class NotificationVolumePickerScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._value = 80
        self._debounce = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Speaker volume",
                device_name="Speaker volume",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad_h = self.suh(SPACING["screen_padding"])
        content = BoxLayout(
            orientation="vertical",
            padding=[pad_h, self.suv(12), pad_h, self.suv(8)],
            spacing=self.suv(10),
        )
        hint = Label(
            text="PulseAudio defaultsink · meeting chimes use this volume",
            font_size=self.suf(FONT_SIZES.get("small", 13)),
            color=COLORS["gray_500"],
            halign="left",
            size_hint=(1, None),
            height=self.suv(40),
        )
        hint.bind(size=hint.setter("text_size"))
        content.add_widget(hint)
        self.vol_lbl = Label(
            text="80%",
            font_size=self.suf(FONT_SIZES.get("xlarge", 28)),
            bold=True,
            color=COLORS["white"],
            size_hint=(1, None),
            height=self.suv(44),
        )
        content.add_widget(self.vol_lbl)
        self.slider = Slider(min=0, max=100, step=1, value=80)
        self.slider.bind(value=self._on_change)
        content.add_widget(self.slider)
        root.add_widget(content)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        async def _load():
            v = 80
            try:
                s = await self.backend.get_settings()
                rv = s.get("system_output_volume")
                if rv is not None:
                    v = int(rv)
                    v = max(0, min(100, v))
            except Exception:
                pass

            def _apply(_dt):
                self._value = v
                self.slider.value = v
                self.vol_lbl.text = f"{v}%"
                set_sink_volume_pct(v)

            Clock.schedule_once(_apply, 0)

        run_async(_load())

    def _on_change(self, _w, val):
        v = max(0, min(100, int(val)))
        self.vol_lbl.text = f"{v}%"
        self._value = v
        set_sink_volume_pct(v)
        if self._debounce:
            self._debounce.cancel()
        self._debounce = Clock.schedule_once(self._persist, 0.35)

    def _persist(self, _dt):
        self._debounce = None
        v = self._value

        async def _save():
            try:
                await self.backend.update_settings({"system_output_volume": v})
            except Exception:
                pass

        run_async(_save())

