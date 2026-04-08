"""Home screen matching the new reference idle layout (1024x600)."""

import re
from datetime import datetime
from pathlib import Path

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton
from config import ASSETS_DIR, COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

_CHIP_SIZE = 34
_ICON_SIZE = 16

# Calendar action titles often include "… - April 4th …" while we also format start on line 2.
_DATEISH_TAIL_RE = re.compile(
    r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}:\d{2}|"
    r"\b\d{1,2}(st|nd|rd|th)\b|\bam\b|\bpm\b)",
)


def _strip_redundant_calendar_suffix(title: str) -> str:
    """If title is 'Subject - …date-ish…', keep only Subject so line 2 is the single date line."""
    t = (title or "").strip()
    if not t:
        return t
    m = re.match(r"^(.*?)\s*[-–—]\s*(.+)$", t)
    if not m:
        return t
    head, tail = m.group(1).strip(), m.group(2).strip()
    if len(tail) < 4 or not _DATEISH_TAIL_RE.search(tail):
        return t
    return head if head else t


def _format_home_next_meeting(next_meeting) -> str:
    """Return 1–2 lines: title and local date/time (or all-day) for the home screen."""
    if not next_meeting:
        return "No executed calendar actions yet"
    title = _strip_redundant_calendar_suffix(
        (next_meeting.get("title") or "Calendar event").strip()
    )
    if not title:
        return "No executed calendar actions yet"
    start = (next_meeting.get("start") or "").strip()
    if not start:
        return title
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            line = dt.strftime("%a %b %d · %I:%M %p")
        else:
            d = datetime.strptime(start[:10], "%Y-%m-%d")
            line = d.strftime("%a %b %d (all day)")
        return f"{title}\n{line}"
    except (ValueError, OSError):
        return f"{title}\n{start}"


class _ImageButton(ButtonBehavior, Image):
    """Simple tappable image widget."""


class _IconChip(ButtonBehavior, FloatLayout):
    """Fixed-size circle chip with a centered tintable icon."""

    def __init__(self, icon_source: Path, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (_CHIP_SIZE, _CHIP_SIZE))
        super().__init__(**kwargs)

        with self.canvas.before:
            self._bg_color = Color(*COLORS["gray_800"])
            self._bg_circle = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        self.icon_img = Image(
            source=str(icon_source),
            color=COLORS["white"],
            size_hint=(None, None),
            size=(_ICON_SIZE, _ICON_SIZE),
            allow_stretch=True,
            keep_ratio=True,
        )
        self.add_widget(self.icon_img)
        self.bind(pos=self._center_icon, size=self._center_icon)
        Clock.schedule_once(lambda _: self._center_icon(), 0)

    def _sync_bg(self, *_args):
        self._bg_circle.pos = self.pos
        self._bg_circle.size = self.size

    def _center_icon(self, *_args):
        self.icon_img.center_x = self.center_x
        self.icon_img.center_y = self.center_y

    def set_icon_color(self, color):
        self.icon_img.color = color


class _RoundTextChip(ButtonBehavior, FloatLayout):
    """Circular chip with a text symbol (matches Wi‑Fi / mic chrome)."""

    def __init__(self, symbol: str, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (_CHIP_SIZE, _CHIP_SIZE))
        super().__init__(**kwargs)
        with self.canvas.before:
            self._bg_color = Color(*COLORS["gray_800"])
            self._bg_circle = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)
        self._lbl = Label(
            text=symbol,
            font_size=FONT_SIZES["title"],
            color=COLORS["white"],
            halign="center",
            valign="middle",
        )
        self._lbl.bind(size=self._lbl.setter("text_size"))
        self.add_widget(self._lbl)
        self.bind(pos=self._center_lbl, size=self._center_lbl)

    def _sync_bg(self, *_args):
        self._bg_circle.pos = self.pos
        self._bg_circle.size = self.size

    def _center_lbl(self, *_args):
        self._lbl.center_x = self.center_x
        self._lbl.center_y = self.center_y


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
        left = BoxLayout(orientation="horizontal", size_hint=(0.50, 1), spacing=6)
        icon_holder = AnchorLayout(size_hint=(None, 1), width=38)
        self.room_icon = Image(
            source=str(self._room_icon),
            size_hint=(None, None),
            size=(26, 26),
            allow_stretch=True,
            keep_ratio=True,
        )
        icon_holder.add_widget(self.room_icon)
        left.add_widget(icon_holder)
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

        self.wifi_chip = _IconChip(self._wifi_icon)
        self.wifi_chip.bind(on_press=lambda *_: self.goto("wifi", transition="slide_left"))
        right.add_widget(self.wifi_chip)

        self.mic_chip = _IconChip(self._mic_icon)
        self.mic_chip.bind(on_press=lambda *_: self.goto("mic_test", transition="slide_left"))
        right.add_widget(self.mic_chip)

        gear_path = ASSETS_DIR / "recording" / "setteing gear icon.png"
        if gear_path.exists():
            self.settings_btn = _IconChip(gear_path)
        else:
            self.settings_btn = _RoundTextChip("⚙")
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
            text="Loading next meeting…",
            font_size=FONT_SIZES["medium"],
            color=COLORS["gray_400"],
            size_hint=(1, None),
            height=52,
            halign="center",
            valign="middle",
        )
        self.upcoming_label.bind(size=self.upcoming_label.setter("text_size"))
        root.add_widget(self.upcoming_label)
        root.add_widget(Widget(size_hint=(1, None), height=4))

        stats_col = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            height=66,
            spacing=8,
            padding=[0, 0],
        )

        def _stat_row(dot_color, initial_text, attr_prefix):
            row = BoxLayout(
                orientation="horizontal",
                size_hint=(1, None),
                height=28,
                padding=[SPACING["screen_padding"], 0],
            )
            row.add_widget(Widget())
            badge = BoxLayout(
                orientation="horizontal",
                size_hint=(None, None),
                width=280,
                height=28,
                spacing=6,
                padding=[10, 0],
            )
            with badge.canvas.before:
                Color(*COLORS["surface"])
                r = RoundedRectangle(pos=badge.pos, size=badge.size, radius=[12])

            def _sync_badge_pos(w, *_):
                r.pos = w.pos

            def _sync_badge_size(w, *_):
                r.size = w.size

            badge.bind(pos=_sync_badge_pos, size=_sync_badge_size)
            badge.add_widget(
                Label(
                    text="●",
                    color=dot_color,
                    font_size=FONT_SIZES["small"],
                    size_hint=(None, 1),
                    width=10,
                )
            )
            lbl = Label(
                text=initial_text,
                color=COLORS["gray_500"],
                font_size=FONT_SIZES["small"],
                halign="left",
                valign="middle",
            )
            lbl.bind(size=lbl.setter("text_size"))
            setattr(self, f"pending_{attr_prefix}_label", lbl)
            badge.add_widget(lbl)
            row.add_widget(badge)
            row.add_widget(Widget())
            stats_col.add_widget(row)

        _stat_row(COLORS["yellow"], "Today: — pending", "today")
        _stat_row(COLORS["gray_600"], "All open: — pending", "total")
        root.add_widget(stats_col)

        root.add_widget(Widget())

        btn_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=88,
            padding=[0, 0, 0, 22],
        )
        btn_row.add_widget(Widget())
        if self._start_button_asset.exists():
            self.start_btn = _ImageButton(
                source=str(self._start_button_asset),
                allow_stretch=True,
                keep_ratio=True,
                size_hint=(None, None),
                height=70,
                width=440,
            )
        else:
            self.start_btn = PrimaryButton(
                text="Start Meeting",
                font_size=FONT_SIZES["large"],
                halign="center",
                size_hint=(None, None),
                height=70,
                width=440,
            )
        self.start_btn.bind(on_press=self._on_start_recording)
        btn_row.add_widget(self.start_btn)
        btn_row.add_widget(Widget())
        root.add_widget(btn_row)

        root.add_widget(Widget(size_hint=(1, None), height=10))
        root.add_widget(self.build_footer())

        self.add_widget(root)

    def on_enter(self):
        self.room_label.text = getattr(self.app, "device_name", "MeetingBox")
        self._update_clock_labels()
        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(lambda _dt: self._update_clock_labels(), 1.0)
        self._load_system_status()
        self._load_home_summary()

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

    def _load_home_summary(self):
        async def _fetch():
            try:
                data = await self.backend.get_home_summary()
                today_n = int(data.get("pending_actions_today") or 0)
                total_n = int(data.get("pending_actions_total") or 0)
                next_m = data.get("next_meeting")
                upcoming = _format_home_next_meeting(next_m)

                def _apply(_dt):
                    self.upcoming_label.text = upcoming
                    self.pending_today_label.text = f"Today: {today_n} pending"
                    self.pending_total_label.text = f"All open: {total_n} pending"

                Clock.schedule_once(_apply, 0)
            except Exception:
                def _fallback(_dt):
                    self.upcoming_label.text = _format_home_next_meeting(None)
                    self.pending_today_label.text = "Today: — pending"
                    self.pending_total_label.text = "All open: — pending"

                Clock.schedule_once(_fallback, 0)

        run_async(_fetch())
