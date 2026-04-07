"""
Processing Screen – transcription / meeting report (no fake progress bar).

Shows a loading indicator and status text. Optional ETA line only when the
backend sends a positive `eta` on `processing_progress` WebSocket events.
"""

import logging

from kivy.clock import Clock
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES

logger = logging.getLogger(__name__)

_SPINNER_FRAMES = ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷")


class ProcessingScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._spin_event = None
        self._spin_idx = 0
        self._eta_seconds = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text="PROCESSING",
            status_color=COLORS["yellow"],
            device_name="MeetingBox",
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.06)))

        mid = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 0.35))
        col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=420,
            height=200,
            spacing=12,
        )

        self.spinner_label = Label(
            text=_SPINNER_FRAMES[0],
            font_size=44,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=52,
        )
        self.spinner_label.bind(size=self.spinner_label.setter("text_size"))
        col.add_widget(self.spinner_label)

        self.status_label = Label(
            text="Transcribing and building your meeting report…",
            font_size=FONT_SIZES["medium"],
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=32,
        )
        self.status_label.bind(size=self.status_label.setter("text_size"))
        col.add_widget(self.status_label)

        self.pb_row = BoxLayout(size_hint=(1, None), height=22, padding=[48, 0], opacity=0)
        self.progress_bar = ProgressBar(max=100, value=0, size_hint=(1, 1))
        self.pb_row.add_widget(self.progress_bar)
        col.add_widget(self.pb_row)

        self.pct_label = Label(
            text="",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_400"],
            halign="center",
            size_hint=(1, None),
            height=0,
            opacity=0,
        )
        col.add_widget(self.pct_label)

        mid.add_widget(col)
        root.add_widget(mid)

        self.meeting_label = Label(
            text="Meeting: Untitled\nDuration: 0 minutes",
            font_size=FONT_SIZES["small"] + 2,
            color=COLORS["gray_500"],
            halign="center",
            size_hint=(1, None),
            height=40,
        )
        self.meeting_label.bind(size=self.meeting_label.setter("text_size"))
        root.add_widget(self.meeting_label)

        self.eta_label = Label(
            text="",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_500"],
            halign="center",
            size_hint=(1, None),
            height=22,
        )
        self.eta_label.bind(size=self.eta_label.setter("text_size"))
        root.add_widget(self.eta_label)

        root.add_widget(Widget())

        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def _start_spinner(self):
        self._stop_spinner()
        self._spin_idx = 0
        self.spinner_label.text = _SPINNER_FRAMES[0]
        self._spin_event = Clock.schedule_interval(self._tick_spinner, 0.08)

    def _stop_spinner(self):
        if self._spin_event:
            self._spin_event.cancel()
            self._spin_event = None

    def _tick_spinner(self, _dt):
        self._spin_idx = (self._spin_idx + 1) % len(_SPINNER_FRAMES)
        self.spinner_label.text = _SPINNER_FRAMES[self._spin_idx]

    def on_processing_started(self, data):
        title = data.get("title", "Untitled")
        dur = data.get("duration", 0) // 60
        self.meeting_label.text = f"Meeting: {title}\nDuration: {dur} minutes"

    def set_processing_status(self, text: str):
        if text:
            self.status_label.text = text

    def on_backend_progress(self, progress: int, status: str, eta: int):
        """Real pipeline updates from WebSocket. Progress bar only if ETA is known."""
        if status:
            self.status_label.text = status

        eta = int(eta or 0)
        self._eta_seconds = eta if eta > 0 else None

        if eta > 0:
            self.eta_label.opacity = 1
            if eta < 60:
                self.eta_label.text = "Estimated time remaining: less than 1 minute"
            else:
                self.eta_label.text = f"Estimated time remaining: {eta // 60} min"
        else:
            self.eta_label.text = ""
            self.eta_label.opacity = 0

        show_bar = eta > 0 and progress is not None and 0 <= int(progress) <= 100
        if show_bar:
            self.pb_row.opacity = 1
            self.pct_label.opacity = 1
            self.pct_label.size_hint_y = None
            self.pct_label.height = 18
            self.progress_bar.value = max(0, min(100, int(progress)))
            self.pct_label.text = f"{int(progress)}%"
        else:
            self.pb_row.opacity = 0
            self.pct_label.opacity = 0
            self.pct_label.height = 0
            self.pct_label.text = ""
            self.progress_bar.value = 0

    def on_enter(self):
        self.status_bar.device_label.text = getattr(self.app, "device_name", "MeetingBox")
        privacy = getattr(self.app, "privacy_mode", False)
        if privacy:
            self.status_bar.status_text = "PROCESSING (Privacy)"

        self._eta_seconds = None
        self.status_label.text = "Transcribing audio…"
        self.eta_label.text = ""
        self.eta_label.opacity = 0
        self.pb_row.opacity = 0
        self.pct_label.opacity = 0
        self.pct_label.height = 0
        self.progress_bar.value = 0
        self._start_spinner()

    def on_leave(self):
        self._stop_spinner()
