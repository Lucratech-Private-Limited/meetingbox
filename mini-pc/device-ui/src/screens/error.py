"""
Error Screen – General error display (480 × 320)

PRD §7.1 – Shows error type, message, recovery options.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from components.button import PrimaryButton, SecondaryButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING


class ErrorScreen(BaseScreen):
    """Error screen – PRD §7.1."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.error_type = 'Error'
        self.error_message = 'An error occurred'
        self.recovery_action = None
        self.recovery_text = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Status bar
        self.status_bar = StatusBar(
            status_text='ERROR',
            status_color=COLORS['yellow'],
            device_name='MeetingBox',
            show_settings=True,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.05)))

        # Warning icon
        icon = Label(
            text='⚠',
            font_size=52,
            color=COLORS['yellow'],
            halign='center',
            size_hint=(1, None), height=60,
        )
        root.add_widget(icon)

        # Error title
        self.type_label = Label(
            text=self.error_type,
            font_size=FONT_SIZES['large'],
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=30,
        )
        self.type_label.bind(size=self.type_label.setter('text_size'))
        root.add_widget(self.type_label)

        # Error message
        self.message_label = Label(
            text=self.error_message,
            font_size=FONT_SIZES['small'] + 2,
            color=COLORS['gray_500'],
            halign='center',
            valign='top',
            size_hint=(1, None), height=50,
        )
        self.message_label.bind(size=self.message_label.setter('text_size'))
        root.add_widget(self.message_label)

        root.add_widget(Widget(size_hint=(1, None), height=8))

        # Recovery button (conditionally visible)
        btn_col = BoxLayout(
            orientation='vertical',
            size_hint=(1, None), height=130,
            padding=[80, 0],
            spacing=SPACING['button_spacing'],
        )

        self.recovery_btn = PrimaryButton(
            text='TRY AGAIN',
            font_size=FONT_SIZES['medium'],
            size_hint=(1, None), height=55,
        )
        self.recovery_btn.bind(on_press=self._on_recovery)
        btn_col.add_widget(self.recovery_btn)

        self.home_btn = SecondaryButton(
            text='GO HOME',
            font_size=FONT_SIZES['medium'],
            size_hint=(1, None), height=55,
        )
        self.home_btn.bind(on_press=lambda _: self.goto('home'))
        btn_col.add_widget(self.home_btn)

        root.add_widget(btn_col)

        root.add_widget(Widget())

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    def set_error(self, error_type: str, message: str,
                  recovery_text: str = None, recovery_action=None):
        self.error_type = error_type
        self.error_message = message
        self.recovery_action = recovery_action
        self.recovery_text = recovery_text

        self.type_label.text = error_type
        self.message_label.text = message

        if recovery_text:
            self.recovery_btn.text = recovery_text
            self.recovery_btn.opacity = 1
            self.recovery_btn.disabled = False
        else:
            self.recovery_btn.text = 'TRY AGAIN'

    def _on_recovery(self, _inst):
        if self.recovery_action:
            self.recovery_action()
        else:
            self.goto('home')
