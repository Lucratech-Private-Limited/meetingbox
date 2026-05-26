"""Screen brightness 0–100% — sysfs via hardware helpers."""

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.slider import Slider

from async_helper import run_async
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from hardware import get_brightness_pct, set_brightness_pct
from screens.base_screen import BaseScreen


class BrightnessSliderScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._value = 100
        self._debounce = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        self.status_bar = StatusBar(
            status_text="Brightness",
            device_name="Brightness",
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)
        pad_h = self.suh(SPACING["screen_padding"])
        content = BoxLayout(
            orientation="vertical",
            padding=[pad_h, self.suv(16), pad_h, self.suv(8)],
            spacing=self.suv(12),
        )
        self.val_lbl = Label(
            text="100%",
            font_size=self.suf(FONT_SIZES.get("xlarge", 28)),
            bold=True,
            color=COLORS["white"],
            size_hint=(1, None),
            height=self.suv(48),
        )
        content.add_widget(self.val_lbl)
        self.slider = Slider(min=1, max=100, step=1, value=100)
        self.slider.bind(value=self._on_change)
        content.add_widget(self.slider)
        root.add_widget(content)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        async def _load():
            v = None
            try:
                s = await self.backend.get_settings()
                br = str(s.get("brightness", "high")).strip().lower()
                if br.isdigit():
                    v = max(1, min(100, int(br)))
            except Exception:
                pass
            if v is None:
                v = get_brightness_pct()
            final = max(1, min(100, v if v is not None else 100))

            def _apply(_dt):
                self._value = final
                self.slider.value = final
                self.val_lbl.text = f"{final}%"

            Clock.schedule_once(_apply, 0)

        run_async(_load())

    def _on_change(self, _w, val):
        v = max(1, min(100, int(val)))
        self.val_lbl.text = f"{v}%"
        self._value = v
        set_brightness_pct(v)
        if self._debounce:
            self._debounce.cancel()
        self._debounce = Clock.schedule_once(self._persist, 0.35)

    def _persist(self, _dt):
        self._debounce = None
        v = self._value

        async def _save():
            try:
                await self.backend.update_settings({"brightness": str(v)})
            except Exception:
                pass

        run_async(_save())
