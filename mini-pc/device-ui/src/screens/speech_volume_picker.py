"""
Volume Picker Screen – continuous slider that controls both assistant TTS
amplitude and the device's real system speaker volume (ALSA / PulseAudio).
"""

import logging
import re
import shutil
import subprocess

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.slider import Slider

from async_helper import run_async
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System-volume helpers (best-effort, no crash if ALSA/PA absent)
# ---------------------------------------------------------------------------

def _set_system_volume(pct: int) -> None:
    """Set ALSA or PulseAudio speaker volume to *pct* percent (0-100)."""
    pct = max(0, min(100, pct))
    for cmd in (
        ["amixer", "-q", "set", "Master", f"{pct}%", "unmute"],
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
    ):
        exe = shutil.which(cmd[0])
        if exe:
            try:
                subprocess.run(
                    [exe, *cmd[1:]],
                    check=False, timeout=3,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
            return  # stop after first available tool


def _get_system_volume() -> int | None:
    """Return current system speaker volume (0-100) or None if unavailable."""
    if shutil.which("amixer"):
        try:
            out = subprocess.check_output(
                ["amixer", "get", "Master"],
                timeout=3, stderr=subprocess.DEVNULL,
            ).decode(errors="ignore")
            m = re.search(r"\[(\d+)%\]", out)
            if m:
                return max(0, min(100, int(m.group(1))))
        except Exception:
            pass
    if shutil.which("pactl"):
        try:
            out = subprocess.check_output(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                timeout=3, stderr=subprocess.DEVNULL,
            ).decode(errors="ignore")
            m = re.search(r"(\d+)%", out)
            if m:
                return max(0, min(100, int(m.group(1))))
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

class SpeechVolumePickerScreen(BaseScreen):
    """Volume control screen with a continuous slider (0 – 100 %)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._volume = 85
        self._save_timer = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text="Volume",
            device_name="Volume",
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        pad_h = self.suh(SPACING["screen_padding"])

        content = BoxLayout(
            orientation="vertical",
            padding=[pad_h, self.suv(20), pad_h, self.suv(8)],
            spacing=self.suv(14),
            size_hint=(1, 1),
        )

        # Description label
        desc = Label(
            text="Controls the assistant voice volume and the device speaker volume.",
            font_size=self.suf(FONT_SIZES.get("small", 13)),
            color=COLORS["gray_500"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=self.suv(38),
        )
        desc.bind(size=desc.setter("text_size"))
        content.add_widget(desc)

        # Large percentage readout
        self.vol_label = Label(
            text="85%",
            font_size=self.suf(FONT_SIZES.get("xlarge", 28)),
            color=COLORS["white"],
            bold=True,
            size_hint=(1, None),
            height=self.suv(52),
        )
        content.add_widget(self.vol_label)

        # Slider — tall touch target for a small touchscreen
        self.slider = Slider(
            min=0, max=100,
            value=85, step=1,
            size_hint=(1, None),
            height=self.suv(64),
        )
        self.slider.bind(value=self._on_slider_change)
        content.add_widget(self.slider)

        # Min/max hint row
        hint_row = BoxLayout(size_hint=(1, None), height=self.suv(22))
        lbl_min = Label(
            text="0%  (mute)", font_size=self.suf(11),
            color=COLORS["gray_500"], halign="left",
        )
        lbl_max = Label(
            text="100%  (max)", font_size=self.suf(11),
            color=COLORS["gray_500"], halign="right",
        )
        lbl_min.bind(size=lbl_min.setter("text_size"))
        lbl_max.bind(size=lbl_max.setter("text_size"))
        hint_row.add_widget(lbl_min)
        hint_row.add_widget(lbl_max)
        content.add_widget(hint_row)

        root.add_widget(content)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_enter(self):
        async def _load():
            vol = None
            # 1. Read from backend settings
            try:
                settings = await self.backend.get_settings()
                raw = settings.get("assistant_speech_volume", 85)
                if isinstance(raw, str):
                    raw = int(float(raw.strip()))
                vol = max(0, min(100, int(raw)))
            except Exception:
                pass

            # 2. Prefer the live system volume so the slider shows reality
            sys_vol = _get_system_volume()
            if sys_vol is not None:
                vol = sys_vol

            final = vol if vol is not None else 85

            def _apply(_dt):
                self._volume = final
                self.slider.value = final
                self.vol_label.text = f"{final}%"

            Clock.schedule_once(_apply, 0)

        run_async(_load())

    # ------------------------------------------------------------------
    # Slider handler
    # ------------------------------------------------------------------

    def _on_slider_change(self, _slider, value):
        v = int(value)
        self.vol_label.text = f"{v}%"
        self._volume = v
        # Apply TTS amplitude immediately
        app = self.app
        if app:
            app.assistant_speech_volume = v
        # Debounce system-volume call and backend save to 400 ms
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = Clock.schedule_once(
            lambda _dt, _v=v: self._commit_volume(_v), 0.4
        )

    def _commit_volume(self, pct: int):
        self._save_timer = None
        _set_system_volume(pct)

        async def _save():
            try:
                await self.backend.update_settings({"assistant_speech_volume": pct})
            except Exception:
                pass

        run_async(_save())
