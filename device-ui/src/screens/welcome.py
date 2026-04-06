"""
Welcome Screen – First-time setup introduction

Trigger : Follows splash on first boot
Content : Logo, "MeetingBox AI" hero, text CTA, security footer
Action  : Tap button → Name room → WiFi Setup

Design ref: UI_Ref_for_cursor/Welcome_Screen/Frame 1.png
"""

from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.graphics import Color, Rectangle

from screens.base_screen import BaseScreen
from components.button import PrimaryButton
from config import COLORS, FONT_SIZES, ASSETS_DIR

WELCOME_DIR = ASSETS_DIR / 'welcome'
LOGO_PATH = str(WELCOME_DIR / 'LOGO.png')
ELLIPSE_PATHS = [
    str(WELCOME_DIR / 'Ellipse 1.png'),
    str(WELCOME_DIR / 'Ellipse 2.png'),
    str(WELCOME_DIR / 'Ellipse 3.png'),
]

# #0B0D11 — same near-black navy as the Figma design
WELCOME_BG = (0.043, 0.051, 0.067, 1)


class WelcomeScreen(BaseScreen):
    """Welcome / first-boot screen.

    Layout (all in FloatLayout so layers and independent positioning work):
      Layer 0: solid dark background
      Layer 1: three soft ellipse blobs — create the blue radial glow
      Layer 2: top-left header  (logo + brand name)
      Layer 3: hero block       (vertically centred — title / subtitle / CTA / shield)
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = FloatLayout()

        # ── Layer 0: solid background ──────────────────────────────────────
        with root.canvas.before:
            Color(*WELCOME_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._update_bg, size=self._update_bg)

        # ── Layer 1: ellipse glow overlays ─────────────────────────────────
        # Three overlapping radial blobs replicate the subtle blue ambient
        # light that the Figma design shows around the centre of the screen.
        blob_configs = [
            # (size_hint_w, size_hint_h, center_x, center_y, opacity)
            (1.4, 0.85, 0.5, 0.72, 0.30),   # upper-centre glow
            (0.9, 0.75, 0.2, 0.45, 0.18),   # left mid glow
            (0.9, 0.75, 0.8, 0.45, 0.18),   # right mid glow
        ]
        for i, path in enumerate(ELLIPSE_PATHS):
            if not Path(path).exists():
                continue
            sw, sh, cx, cy, op = blob_configs[i] if i < len(blob_configs) else (1, 1, 0.5, 0.5, 0.25)
            root.add_widget(Image(
                source=path,
                allow_stretch=True,
                keep_ratio=False,
                size_hint=(sw, sh),
                pos_hint={'center_x': cx, 'center_y': cy},
                opacity=op,
            ))

        # ── Layer 2: top-left header (logo + "MeetingBox") ─────────────────
        header = BoxLayout(
            orientation='horizontal',
            size_hint=(None, None),
            width=200,
            height=52,
            spacing=9,
            padding=[20, 16, 0, 0],
            pos_hint={'x': 0, 'top': 1},
        )
        if Path(LOGO_PATH).exists():
            header.add_widget(Image(
                source=LOGO_PATH,
                size_hint=(None, None),
                size=(26, 26),
                allow_stretch=True,
                keep_ratio=True,
            ))
        brand = Label(
            text='MeetingBox',
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
        )
        brand.bind(size=brand.setter('text_size'))
        header.add_widget(brand)
        root.add_widget(header)

        # ── Layer 3: hero content block (vertically centred) ───────────────
        # Heights:  title(78) + gap(14) + subtitle(28) + gap(32) +
        #           button(56) + gap(18) + footer text(28)  = 254 px
        HERO_H = 254

        hero = BoxLayout(
            orientation='vertical',
            size_hint=(1, None),
            height=HERO_H,
            pos_hint={'center_x': 0.5, 'center_y': 0.50},
            spacing=0,
            padding=[0, 0],
        )

        # "MeetingBox AI" — match Figma: ~64 px bold white, centred
        title = Label(
            text='MeetingBox AI',
            font_size=64,
            bold=True,
            color=COLORS['white'],
            halign='center',
            valign='middle',
            size_hint=(1, None),
            height=78,
        )
        title.bind(size=title.setter('text_size'))
        hero.add_widget(title)

        hero.add_widget(Widget(size_hint=(1, None), height=14))

        # Subtitle — lighter gray, centred
        subtitle = Label(
            text='Your meeting room that remembers everything.',
            font_size=18,
            color=COLORS['gray_400'],
            halign='center',
            valign='middle',
            size_hint=(1, None),
            height=28,
        )
        subtitle.bind(size=subtitle.setter('text_size'))
        hero.add_widget(subtitle)

        hero.add_widget(Widget(size_hint=(1, None), height=32))

        # CTA — text label on PrimaryButton (fixed 400×56, no stretched bitmap)
        btn_anchor = AnchorLayout(
            anchor_x='center',
            anchor_y='center',
            size_hint=(1, None),
            height=56,
        )
        cta = PrimaryButton(
            text='Start Your First Meeting',
            font_size=FONT_SIZES['large'],
            size_hint=(None, None),
            size=(400, 56),
        )
        cta.bind(on_press=self._on_continue)
        btn_anchor.add_widget(cta)
        hero.add_widget(btn_anchor)

        hero.add_widget(Widget(size_hint=(1, None), height=18))

        security_lbl = Label(
            text='Enterprise-grade security included',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='center',
            valign='middle',
            size_hint=(1, None),
            height=28,
        )
        security_lbl.bind(size=security_lbl.setter('text_size'))
        hero.add_widget(security_lbl)

        root.add_widget(hero)
        self.add_widget(root)

    def _update_bg(self, widget, _value):
        if hasattr(self, '_bg_rect') and widget:
            self._bg_rect.pos = widget.pos
            self._bg_rect.size = widget.size

    def _on_continue(self, _inst):
        self.goto('room_name', transition='slide_left')
