"""
Check for Updates Screen – PRD §5.16

States: Checking → Up to Date OR Update Available
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.button import PrimaryButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING


class UpdateCheckScreen(BaseScreen):
    """Check for updates screen – PRD §5.16."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._update_info = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Header
        self.status_bar = StatusBar(
            status_text='Check for Updates',
            device_name='Check for Updates',
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.12)))

        # Icon / status area
        self.icon_label = Label(
            text='',
            font_size=48,
            color=COLORS['green'],
            halign='center',
            size_hint=(1, None), height=60,
        )
        root.add_widget(self.icon_label)

        # Title
        self.title_label = Label(
            text='Checking for updates…',
            font_size=FONT_SIZES['large'],
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=30,
        )
        root.add_widget(self.title_label)

        # Details
        self.detail_label = Label(
            text='',
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['gray_500'],
            halign='center',
            valign='top',
            size_hint=(1, None), height=60,
        )
        self.detail_label.bind(size=self.detail_label.setter('text_size'))
        root.add_widget(self.detail_label)

        # Install button (hidden initially)
        btn_row = BoxLayout(
            size_hint=(1, None), height=60,
            padding=[100, 0],
        )
        self.install_btn = PrimaryButton(
            text='INSTALL UPDATE',
            font_size=FONT_SIZES['medium'],
            opacity=0,
            disabled=True,
        )
        self.install_btn.bind(on_press=self._on_install)
        btn_row.add_widget(self.install_btn)
        root.add_widget(btn_row)

        root.add_widget(Widget())

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def on_enter(self):
        self.icon_label.text = ''
        self.title_label.text = 'Checking for updates…'
        self.detail_label.text = ''
        self.install_btn.opacity = 0
        self.install_btn.disabled = True
        self._check_updates()

    def _check_updates(self):
        async def _check():
            try:
                result = await self.backend.check_for_updates()
                self._update_info = result
                Clock.schedule_once(lambda _dt: self._show_result(result), 0)
            except Exception:
                Clock.schedule_once(lambda _dt: self._show_error(), 0)
        run_async(_check())

    def _show_result(self, result):
        if result.get('update_available'):
            self.icon_label.text = ''
            self.title_label.text = 'Update Available!'
            self.title_label.color = COLORS['blue']
            cur = result.get('current_version', '?')
            new = result.get('latest_version', '?')
            notes = result.get('release_notes', '')
            detail = f'Current: {cur}\nNew: {new}'
            if notes:
                detail += f'\n\nWhat\'s new:\n{notes}'
            self.detail_label.text = detail
            self.install_btn.opacity = 1
            self.install_btn.disabled = False
        else:
            self.icon_label.text = '✓'
            self.icon_label.color = COLORS['green']
            self.title_label.text = "You're Up to Date!"
            self.title_label.color = COLORS['white']
            cur = result.get('current_version', '?')
            self.detail_label.text = f'Current version: {cur}\nLast checked: Just now'

    def _show_error(self):
        self.icon_label.text = '⚠'
        self.icon_label.color = COLORS['yellow']
        self.title_label.text = 'Check Failed'
        self.detail_label.text = 'Could not check for updates.\nPlease try again later.'

    def _on_install(self, _inst):
        self.goto('update_install', transition='fade')
