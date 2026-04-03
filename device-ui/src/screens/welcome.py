"""
Welcome Screen – First-time setup introduction

Trigger : Follows splash on first boot
Content : Logo, welcome text, CTA button, security footer
Action  : Tap button → Name room → WiFi Setup

Design ref: UI_Ref_for_cursor/Welcome_Screen/Frame 1.png
"""

from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, Rectangle

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES, SPACING, ASSETS_DIR

# Welcome screen assets
WELCOME_DIR = ASSETS_DIR / 'welcome'
LOGO_PATH = str(WELCOME_DIR / 'LOGO.png')
BUTTON_PATH = str(WELCOME_DIR / 'Button.png')
SHIELD_PATH = str(WELCOME_DIR / 'shield.png')
ELLIPSE_PATHS = [
    str(WELCOME_DIR / 'Ellipse 1.png'),
    str(WELCOME_DIR / 'Ellipse 2.png'),
    str(WELCOME_DIR / 'Ellipse 3.png'),
]

# Design colors (from ref: #0B0D11 bg, #4A90E2 button, #9CA3AF secondary)
WELCOME_BG = (0.043, 0.051, 0.067, 1)  # #0B0D11


class _ImageButton(ButtonBehavior, Image):
    """Tappable image button (for CTA using Button.png asset)."""
    pass


class WelcomeScreen(BaseScreen):
    """Welcome / first-boot screen – MeetingBox AI intro with gradient overlays."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = FloatLayout()
        # Dark base background
        with root.canvas.before:
            Color(*WELCOME_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        # Ellipse gradient overlays (layered for ambient effect)
        for path in ELLIPSE_PATHS:
            if Path(path).exists():
                img = Image(
                    source=path,
                    allow_stretch=True,
                    keep_ratio=False,
                    size_hint=(1, 1),
                    pos_hint={'x': 0, 'y': 0},
                    opacity=0.35,
                )
                root.add_widget(img)

        # Content layout (scrollable so CTA stays reachable on short displays)
        scroll = ScrollView(
            do_scroll_x=False,
            size_hint=(1, 1),
            bar_width=8,
            pos_hint={'x': 0, 'y': 0},
        )
        content = BoxLayout(
            orientation='vertical',
            padding=[SPACING['screen_padding'], 0],
            size_hint_y=None,
        )
        content.bind(minimum_height=content.setter('height'))

        # Header: logo + "MeetingBox" (top-left)
        header = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=56,
            spacing=12,
            padding=[0, 8],
        )
        logo_img = Image(
            source=LOGO_PATH,
            size_hint=(None, 1),
            width=40,
            allow_stretch=True,
            keep_ratio=True,
        )
        header.add_widget(logo_img)
        brand = Label(
            text='MeetingBox',
            font_size=FONT_SIZES['title'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
            size_hint_x=0.6,
        )
        brand.bind(size=brand.setter('text_size'))
        header.add_widget(brand)
        content.add_widget(header)

        # Top spacer (fixed — avoids eating the whole viewport on small heights)
        content.add_widget(Widget(size_hint=(1, None), height=28))

        # Hero: title + subtitle (centered)
        title = Label(
            text='MeetingBox AI',
            font_size=FONT_SIZES['huge'],
            bold=True,
            color=COLORS['white'],
            size_hint=(1, None),
            height=48,
            halign='center',
            valign='middle',
        )
        title.bind(size=title.setter('text_size'))
        content.add_widget(title)

        subtitle = Label(
            text="Your meeting room that remembers everything.",
            font_size=FONT_SIZES['body'],
            color=COLORS['gray_400'],
            size_hint=(1, None),
            height=32,
            halign='center',
            valign='middle',
        )
        subtitle.bind(size=subtitle.setter('text_size'))
        content.add_widget(subtitle)

        content.add_widget(Widget(size_hint=(1, None), height=36))

        # CTA button (uses Button.png asset with "Start Your First Meeting")
        btn_wrap = BoxLayout(size_hint=(1, None), height=70, padding=[80, 0])
        cta_btn = _ImageButton(
            source=BUTTON_PATH,
            allow_stretch=True,
            keep_ratio=True,
        )
        cta_btn.bind(on_press=self._on_continue)
        btn_wrap.add_widget(cta_btn)
        content.add_widget(btn_wrap)

        content.add_widget(Widget(size_hint=(1, None), height=24))

        # Footer: shield + "Enterprise-grade security included" (centered)
        footer_wrap = AnchorLayout(
            anchor_x='center',
            anchor_y='center',
            size_hint=(1, None),
            height=40,
        )
        shield_img = Image(
            source=SHIELD_PATH,
            allow_stretch=True,
            keep_ratio=True,
            size_hint=(None, None),
            size=(280, 32),
        )
        footer_wrap.add_widget(shield_img)
        content.add_widget(footer_wrap)

        content.add_widget(Widget(size_hint=(1, None), height=24))

        scroll.add_widget(content)
        root.add_widget(scroll)
        self.add_widget(root)

    def _update_bg(self, widget, value):
        if hasattr(self, '_bg_rect') and widget:
            self._bg_rect.pos = widget.pos
            self._bg_rect.size = widget.size

    def _on_continue(self, _inst):
        self.goto('room_name', transition='slide_left')
