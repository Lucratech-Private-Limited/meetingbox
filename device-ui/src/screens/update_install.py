"""
Installing Update Screen – PRD §5.17

Progress bar, stage text, warning not to unplug.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING


class UpdateInstallScreen(BaseScreen):
    """Firmware update installation screen – PRD §5.17."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Header (no back button – cannot cancel)
        header = StatusBar(
            status_text='Installing Update',
            device_name='Installing Update',
            back_button=False,
            show_settings=False,
        )
        root.add_widget(header)

        root.add_widget(Widget(size_hint=(1, 0.1)))

        # Title
        self.title_label = Label(
            text='Installing update…',
            font_size=FONT_SIZES['medium'],
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        root.add_widget(self.title_label)

        root.add_widget(Widget(size_hint=(1, None), height=12))

        # Progress bar
        pb_row = BoxLayout(
            size_hint=(1, None), height=20,
            padding=[40, 0],
        )
        self.progress_bar = ProgressBar(max=100, value=0, size_hint=(1, 1))
        pb_row.add_widget(self.progress_bar)
        root.add_widget(pb_row)

        self.pct_label = Label(
            text='0%',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_400'],
            halign='center',
            size_hint=(1, None), height=18,
        )
        root.add_widget(self.pct_label)

        # Stage
        self.stage_label = Label(
            text='Downloading update files',
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['gray_500'],
            halign='center',
            size_hint=(1, None), height=24,
        )
        root.add_widget(self.stage_label)

        root.add_widget(Widget(size_hint=(1, None), height=8))

        # Warning
        warn = Label(
            text='Do not unplug the device',
            font_size=FONT_SIZES['small'] + 2,
            bold=True,
            color=COLORS['yellow'],
            halign='center',
            size_hint=(1, None), height=24,
        )
        root.add_widget(warn)

        # ETA
        self.eta_label = Label(
            text='Estimated time: calculating…',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='center',
            size_hint=(1, None), height=20,
        )
        root.add_widget(self.eta_label)

        root.add_widget(Widget())

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        self.progress_bar.value = 0
        self.pct_label.text = '0%'
        self.stage_label.text = 'Downloading update files'
        self._start_install()

    def _start_install(self):
        async def _install():
            try:
                await self.backend.install_update()
            except Exception:
                Clock.schedule_once(
                    lambda _dt: self.app.show_error_screen(
                        'Update Failed',
                        'Could not install firmware update.'), 0)
        run_async(_install())

    # Called from main app via WebSocket events
    def on_progress_update(self, progress: int, stage: str = '', eta: int = 0):
        self.progress_bar.value = progress
        self.pct_label.text = f'{progress}%'
        if stage:
            self.stage_label.text = stage
        if eta:
            if eta < 60:
                self.eta_label.text = 'Estimated time: less than 1 minute'
            else:
                self.eta_label.text = f'Estimated time: {eta // 60} minutes'
