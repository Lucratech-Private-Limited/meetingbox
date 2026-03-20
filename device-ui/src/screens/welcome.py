"""
Welcome Screen – First-time setup introduction

Trigger : Follows splash on first boot
Content : Logo, welcome text, CONTINUE button
Action  : Tap CONTINUE → WiFi Setup Screen
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle

from screens.base_screen import BaseScreen
from components.button import PrimaryButton
from config import COLORS, FONT_SIZES, SPACING


class WelcomeScreen(BaseScreen):
    """Welcome / first-boot screen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical', padding=[SPACING['screen_padding'], 0])
        self.make_dark_bg(root)

        # Top spacer
        root.add_widget(Widget(size_hint=(1, 0.1)))

        # Logo
        logo = Label(
            text='MeetingBox',
            font_size=28,
            bold=True,
            color=COLORS['white'],
            size_hint=(1, None),
            height=80,
            halign='center',
            valign='middle',
        )
        root.add_widget(logo)

        # Title
        title = Label(
            text='Welcome to MeetingBox AI!',
            font_size=FONT_SIZES['large'],
            bold=True,
            color=COLORS['white'],
            size_hint=(1, None),
            height=36,
            halign='center',
        )
        title.bind(size=title.setter('text_size'))
        root.add_widget(title)

        # Subtitle
        subtitle = Label(
            text="First, let's connect to your WiFi",
            font_size=FONT_SIZES['body'],
            color=COLORS['gray_500'],
            size_hint=(1, None),
            height=28,
            halign='center',
        )
        subtitle.bind(size=subtitle.setter('text_size'))
        root.add_widget(subtitle)

        # Spacer
        root.add_widget(Widget(size_hint=(1, 0.15)))

        # CONTINUE button
        btn_row = BoxLayout(
            size_hint=(1, None), height=60,
            padding=[80, 0],
        )
        continue_btn = PrimaryButton(
            text='CONTINUE  →',
            font_size=FONT_SIZES['large'],
        )
        continue_btn.bind(on_press=self._on_continue)
        btn_row.add_widget(continue_btn)
        root.add_widget(btn_row)

        # Bottom spacer
        root.add_widget(Widget(size_hint=(1, 0.15)))

        self.add_widget(root)

    def _on_continue(self, _inst):
        self.goto('wifi_setup', transition='slide_left')
