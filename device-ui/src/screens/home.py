"""Home screen matching the new reference idle layout (1024x600)."""

from datetime import datetime
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton
from config import ASSETS_DIR, COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen


class _LabelButton(ButtonBehavior, Label):
    """Simple tappable label."""


class _ImageButton(ButtonBehavior, Image):
    """Simple tappable image widget."""


class _IconChip(ButtonBehavior, BoxLayout):
    """Circular background chip + tintable icon."""

    def __init__(self, bg_source: Path, icon_source: Path, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (34, 34))
        super().__init__(**kwargs)

        self.bg_img = Image(
            source=str(bg_source),
            allow_stretch=True,
            keep_ratio=False,
        )
        self.icon_img = Image(
            source=str(icon_source),
            color=COLORS["white"],
            size_hint=(None, None),
            size=(15, 15),
        )

        self.add_widget(self.bg_img)
        self.add_widget(self.icon_img)
        self.bind(pos=self._layout, size=self._layout)
        self._layout()

    def _layout(self, *_args):
        self.bg_img.pos = self.pos
        self.bg_img.size = self.size
        self.icon_img.center_x = self.center_x
        self.icon_img.center_y = self.center_y

    def set_icon_color(self, color):
        self.icon_img.color = color


class HomeScreen(BaseScreen):
    """Reference-style home screen with live clock and start button."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_event = None
        self._wifi_ok = False
        self._mic_connected = True

        self._home_assets = ASSETS_DIR / "home"
        self._room_icon = self._home_assets / "Overlay.png"
        self._start_button_asset = self._home_assets / "Button.png"
        self._chip_bg = self._home_assets / "Button_1.png"
        self._wifi_icon = self._home_assets / "Container.png"
        self._mic_icon = self._home_assets / "Container_1.png"

        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(0.04, 0.06, 0.10, 1)
            self._bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, _: setattr(self._bg, "pos", w.pos),
            size=lambda w, _: setattr(self._bg, "size", w.size),
        )

        top = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=62,
            padding=[SPACING["screen_padding"], 12],
            spacing=10,
        )
        left = BoxLayout(orientation="horizontal", size_hint=(0.50, 1), spacing=8)
        self.room_icon = Image(
            source=str(self._room_icon),
            size_hint=(None, None),
            size=(24, 24),
        )
        left.add_widget(self.room_icon)
        self.room_label = Label(
            text="MeetingBox",
            font_size=FONT_SIZES["medium"],
            color=COLORS["white"],
            bold=True,
            halign="left",
            valign="middle",
        )
        self.room_label.bind(size=self.room_label.setter("text_size"))
        left.add_widget(self.room_label)
        top.add_widget(left)

        right = BoxLayout(orientation="horizontal", size_hint=(0.50, 1), spacing=10)
        right.add_widget(Widget())

        self.top_time_label = Label(
            text="--:--",
            font_size=FONT_SIZES["body"],
            color=COLORS["white"],
            size_hint=(None, None),
            size=(86, 34),
            halign="center",
            valign="middle",
        )
        self.top_time_label.bind(size=self.top_time_label.setter("text_size"))
        with self.top_time_label.canvas.before:
            Color(*COLORS["surface"])
            self._time_bg = RoundedRectangle(
                pos=self.top_time_label.pos, size=self.top_time_label.size, radius=[14]
            )
        self.top_time_label.bind(
            pos=lambda w, _: setattr(self._time_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._time_bg, "size", w.size),
        )
        right.add_widget(self.top_time_label)

        self.wifi_chip = _IconChip(self._chip_bg, self._wifi_icon)
        self.wifi_chip.bind(on_press=lambda *_: self.goto("wifi", transition="slide_left"))
        right.add_widget(self.wifi_chip)

        self.mic_chip = _IconChip(self._chip_bg, self._mic_icon)
        self.mic_chip.bind(on_press=lambda *_: self.goto("mic_test", transition="slide_left"))
        right.add_widget(self.mic_chip)

        self.settings_btn = _LabelButton(
            text="⚙",
            font_size=FONT_SIZES["title"],
            color=COLORS["gray_400"],
            size_hint=(None, 1),
            width=28,
            halign="center",
            valign="middle",
        )
        self.settings_btn.bind(size=self.settings_btn.setter("text_size"))
        self.settings_btn.bind(on_press=lambda *_: self.goto("settings", transition="slide_left"))
        right.add_widget(self.settings_btn)
        top.add_widget(right)
        root.add_widget(top)

        root.add_widget(Widget(size_hint=(1, None), height=18))

        self.big_time_label = Label(
            text="--:--",
            font_size=104,
            bold=True,
            color=COLORS["white"],
            size_hint=(1, None),
            height=124,
            halign="center",
            valign="middle",
        )
        self.big_time_label.bind(size=self.big_time_label.setter("text_size"))
        root.add_widget(self.big_time_label)

        self.date_label = Label(
            text="",
            font_size=FONT_SIZES["body"],
            color=COLORS["gray_400"],
            size_hint=(1, None),
            height=26,
            halign="center",
            valign="middle",
        )
        self.date_label.bind(size=self.date_label.setter("text_size"))
        root.add_widget(self.date_label)

        self.upcoming_label = Label(
            text="No Upcoming Meetings",
            font_size=FONT_SIZES["medium"],
            color=COLORS["gray_400"],
            size_hint=(1, None),
            height=30,
            halign="center",
            valign="middle",
        )
        self.upcoming_label.bind(size=self.upcoming_label.setter("text_size"))
        root.add_widget(self.upcoming_label)
        root.add_widget(Widget(size_hint=(1, None), height=4))

        badge_wrap = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=32,
            padding=[SPACING["screen_padding"], 0],
        )
        badge_wrap.add_widget(Widget())
        badge = BoxLayout(
            orientation="horizontal",
            size_hint=(None, None),
            width=240,
            height=28,
            spacing=6,
            padding=[10, 0],
        )
        with badge.canvas.before:
            Color(*COLORS["surface"])
            self._badge_bg = RoundedRectangle(pos=badge.pos, size=badge.size, radius=[12])
        badge.bind(
            pos=lambda w, _: setattr(self._badge_bg, "pos", w.pos),
            size=lambda w, _: setattr(self._badge_bg, "size", w.size),
        )
        badge.add_widget(
            Label(
                text="●",
                color=COLORS["gray_600"],
                font_size=FONT_SIZES["small"],
                size_hint=(None, 1),
                width=10,
            )
        )
        action_label = Label(
            text="No open action items",
            color=COLORS["gray_500"],
            font_size=FONT_SIZES["small"],
            halign="left",
            valign="middle",
        )
        action_label.bind(size=action_label.setter("text_size"))
        badge.add_widget(action_label)
        badge_wrap.add_widget(badge)
        badge_wrap.add_widget(Widget())
        root.add_widget(badge_wrap)

        root.add_widget(Widget())

        root.add_widget(self.build_footer())
        root.add_widget(Widget(size_hint=(1, None), height=8))

        btn_wrap = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            height=94,
            padding=[180, 10],
        )
        if self._start_button_asset.exists():
            self.start_btn = _ImageButton(
                source=str(self._start_button_asset),
                allow_stretch=True,
                keep_ratio=False,
                size_hint=(1, None),
                height=62,
            )
        else:
            self.start_btn = PrimaryButton(
                text="Start Meeting",
                font_size=FONT_SIZES["large"],
                halign="center",
            )
        self.start_btn.bind(on_press=self._on_start_recording)
        btn_wrap.add_widget(self.start_btn)
        root.add_widget(btn_wrap)
        root.add_widget(Widget(size_hint=(1, None), height=8))

        self.add_widget(root)

    def on_enter(self):
        self.room_label.text = getattr(self.app, "device_name", "MeetingBox")
        self._update_clock_labels()
        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(lambda _dt: self._update_clock_labels(), 1.0)
        self._load_system_status()

    def on_leave(self):
        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None

    def _on_start_recording(self, _inst):
        self.app.start_recording()

    def _update_clock_labels(self):
        now = datetime.now()
        self.top_time_label.text = now.strftime("%H:%M")
        self.big_time_label.text = now.strftime("%H:%M")
        self.date_label.text = f"{now.strftime('%A, %B')} {now.day}"

    def _load_system_status(self):
        async def _fetch():
            try:
                info = await self.backend.get_system_info()
                free_gb = (info["storage_total"] - info["storage_used"]) / (1024 ** 3)
                wifi_ok = bool(info.get("wifi_ssid"))
                mic_connected = bool(
                    info.get(
                        "microphone_connected",
                        info.get("mic_connected", info.get("audio_input_available", True)),
                    )
                )
                privacy = getattr(self.app, "privacy_mode", False)

                def _apply(_dt):
                    self._wifi_ok = wifi_ok
                    self._mic_connected = mic_connected
                    self.wifi_chip.set_icon_color(COLORS["white"] if wifi_ok else COLORS["gray_500"])
                    self.mic_chip.set_icon_color(COLORS["green"] if mic_connected else COLORS["red"])
                    self.update_footer(wifi_ok=wifi_ok, free_gb=free_gb, privacy_mode=privacy)

                Clock.schedule_once(_apply, 0)
            except Exception:
                pass

        run_async(_fetch())
