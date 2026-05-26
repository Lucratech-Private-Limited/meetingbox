"""
Storage breakdown — shows usage by category and offers a Clear Cache button.
"""

import logging
import shutil


def _read_ram_gb() -> tuple[float, float] | None:
    """Return (used_gb, total_gb) from /proc/meminfo, or None on failure."""
    try:
        total_kb = avail_kb = 0
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
        if total_kb:
            used_kb = total_kb - avail_kb
            return used_kb / (1024 ** 2), total_kb / (1024 ** 2)
    except Exception:
        pass
    return None

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import DangerButton
from components.modal_dialog import ModalDialog
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)


def _fmt(gb: float) -> str:
    if gb < 0.001:
        return "< 1 MB"
    if gb < 1.0:
        return f"{gb * 1024:.0f} MB"
    return f"{gb:.2f} GB"


class StorageBreakdownScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Storage",
                device_name="Storage",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(8), pad, self.suv(8)],
            spacing=self.suv(8),
        )

        self.disk_item = SettingsItem(title="Disk", subtitle="Reading…", mode="info")
        self.ram_item = SettingsItem(title="RAM", subtitle="Reading…", mode="info")
        self.recordings_item = SettingsItem(title="Recordings", subtitle="Loading…", mode="info")
        self.transcripts_item = SettingsItem(title="Transcripts cache", subtitle="Loading…", mode="info")
        self.app_cache_item = SettingsItem(title="App cache", subtitle="Loading…", mode="info")
        for w in (self.disk_item, self.ram_item, self.recordings_item, self.transcripts_item, self.app_cache_item):
            inner.add_widget(w)
        try:
            _du = shutil.disk_usage('/')
            self.disk_item.subtitle_label.text = (
                f"{_fmt(_du.used / (1024**3))} used · "
                f"{_fmt(_du.free / (1024**3))} free · "
                f"{_fmt(_du.total / (1024**3))} total"
            )
        except Exception:
            self.disk_item.subtitle_label.text = "Unavailable"
        ram = _read_ram_gb()
        if ram:
            used_gb, total_gb = ram
            free_gb = total_gb - used_gb
            self.ram_item.subtitle_label.text = (
                f"{_fmt(used_gb)} used · "
                f"{_fmt(free_gb)} free · "
                f"{_fmt(total_gb)} total"
            )
        else:
            self.ram_item.subtitle_label.text = "Unavailable"

        self.clear_btn = DangerButton(
            text="CLEAR CACHE",
            size_hint=(1, None),
            height=self.suv(50),
            opacity=0.5,
            disabled=True,
        )
        self.clear_btn.bind(on_press=lambda *_: self._confirm_clear())
        inner.add_widget(Widget(size_hint_y=None, height=self.suv(8)))
        inner.add_widget(self.clear_btn)

        root.add_widget(inner)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        async def _fetch():
            try:
                data = await self.backend.storage_breakdown()

                def _apply(_dt):
                    self.recordings_item.subtitle_label.text = _fmt(data.get("recordings_gb", 0))
                    self.transcripts_item.subtitle_label.text = _fmt(data.get("transcripts_cache_gb", 0))
                    self.app_cache_item.subtitle_label.text = _fmt(data.get("app_cache_gb", 0))
                    self.clear_btn.disabled = False
                    self.clear_btn.opacity = 1.0

                Clock.schedule_once(_apply, 0)
            except Exception as e:
                logger.debug("Storage breakdown: %s", e)

        run_async(_fetch())

    def _confirm_clear(self):
        self.add_widget(
            ModalDialog(
                title="Clear app cache?",
                message="Temporary files and segment cache will be deleted.\nRecordings are not affected.",
                confirm_text="CLEAR",
                cancel_text="CANCEL",
                on_confirm=self._do_clear,
            )
        )

    def _do_clear(self):
        async def _clear():
            try:
                await self.backend.clear_cache()

                def _done(_dt):
                    self.on_enter()

                Clock.schedule_once(_done, 0.3)
            except Exception as e:
                logger.warning("clear_cache: %s", e)

        run_async(_clear())
