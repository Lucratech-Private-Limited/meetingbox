"""
Welcome Screen – First-time setup introduction

Trigger : Follows splash on first boot
Content : Logo, "MeetingBox AI" hero, Button.png CTA (native aspect, rounded),
          shield crop + text footer
Action  : Tap CTA → Name room → WiFi Setup

Design ref: UI_Ref_for_cursor/Welcome_Screen/Frame 1.png

Exports: No API token is required to run the app. For sharper PNGs from Figma,
export assets at 1× or 2× and replace files under assets/welcome/. A personal
Figma access token is only needed for automated REST export scripts — not for users.
"""

from pathlib import Path

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, Rectangle

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES, ASSETS_DIR, DISPLAY_WIDTH

WELCOME_DIR = ASSETS_DIR / 'welcome'
LOGO_PATH = str(WELCOME_DIR / 'LOGO.png')
BUTTON_PATH = str(WELCOME_DIR / 'Button.png')
ELLIPSE_PATHS = [
    str(WELCOME_DIR / 'Ellipse 1.png'),
    str(WELCOME_DIR / 'Ellipse 2.png'),
    str(WELCOME_DIR / 'Ellipse 3.png'),
]

# #0B0D11 — same near-black navy as the Figma design
WELCOME_BG = (0.043, 0.051, 0.067, 1)

# Button.png natural size: 412 × 94 px.
# Render at 70 px tall → 412/94 × 70 ≈ 307 px wide (≈ 30 % of 1024).
_CTA_TARGET_H = 70


class _ImageButton(ButtonBehavior, Image):
    """Tappable image — preserves natural aspect ratio, never stretches."""
    pass


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
            width=self.suh(200),
            height=self.suv(52),
            spacing=self.suh(9),
            padding=[self.suh(20), self.suv(16), 0, 0],
            pos_hint={'x': 0, 'top': 1},
        )
        if Path(LOGO_PATH).exists():
            header.add_widget(Image(
                source=LOGO_PATH,
                size_hint=(None, None),
                size=(self.suv(26), self.suv(26)),
                allow_stretch=True,
                keep_ratio=True,
            ))
        brand = Label(
            text='MeetingBox',
            font_size=self.suf(FONT_SIZES['medium']),
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
        #           button(70) + gap(16) + footer(26)  = 264 px
        HERO_H = self.suv(264)
        cta_h = self.suv(_CTA_TARGET_H)

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
            font_size=self.suf(64),
            bold=True,
            color=COLORS['white'],
            halign='center',
            valign='middle',
            size_hint=(1, None),
            height=self.suv(78),
        )
        title.bind(size=title.setter('text_size'))
        hero.add_widget(title)

        hero.add_widget(Widget(size_hint=(1, None), height=self.suv(14)))

        # Subtitle — lighter gray, centred
        subtitle = Label(
            text='Your meeting room that remembers everything.',
            font_size=self.suf(18),
            color=COLORS['gray_400'],
            halign='center',
            valign='middle',
            size_hint=(1, None),
            height=self.suv(28),
        )
        subtitle.bind(size=subtitle.setter('text_size'))
        hero.add_widget(subtitle)

        hero.add_widget(Widget(size_hint=(1, None), height=self.suv(32)))

        # CTA — design Button.png (rounded pill + baked label); size from texture aspect
        btn_anchor = AnchorLayout(
            anchor_x='center',
            anchor_y='center',
            size_hint=(1, None),
            height=cta_h,
        )
        cta = _ImageButton(
            source=BUTTON_PATH,
            size_hint=(None, None),
            size=(self.suh(260), cta_h),
            allow_stretch=False,
            keep_ratio=True,
            fit_mode='contain',
        )

        def _sync_cta_size(img, *args):
            if not img.texture or img.texture.width < 2:
                return
            tw, th = img.texture.size
            nh = cta_h
            nw = max(1, int(tw * nh / th))
            max_w = int(DISPLAY_WIDTH * 0.90)
            if nw > max_w:
                nw = max_w
                nh = max(1, int(th * nw / tw))
            img.size = (nw, nh)
            btn_anchor.height = max(btn_anchor.height, nh)

        cta.bind(texture=_sync_cta_size)
        cta.bind(on_press=self._on_continue)
        btn_anchor.add_widget(cta)
        Clock.schedule_once(lambda _dt: _sync_cta_size(cta), 0)
        hero.add_widget(btn_anchor)

        hero.add_widget(Widget(size_hint=(1, None), height=self.suv(16)))

        # Security line: Unicode shield + text — both are Labels, no image overlap
        security_row = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=self.suv(26),
            spacing=self.suh(6),
        )
        security_row.add_widget(Widget(size_hint=(1, 1)))
        shield_icon = Label(
            text='🛡',
            font_size=self.suf(14),
            color=COLORS['gray_500'],
            size_hint=(None, 1),
            width=self.suh(20),
            halign='center',
            valign='middle',
        )
        security_row.add_widget(shield_icon)
        security_lbl = Label(
            text='Enterprise-grade security included',
            font_size=self.suf(FONT_SIZES['small']),
            color=COLORS['gray_500'],
            halign='left',
            valign='middle',
            size_hint=(None, 1),
        )
        security_lbl.bind(texture_size=lambda inst, ts: setattr(inst, 'width', ts[0]))
        security_row.add_widget(security_lbl)
        security_row.add_widget(Widget(size_hint=(1, 1)))
        hero.add_widget(security_row)

        root.add_widget(hero)
        self.add_widget(root)

    def _update_bg(self, widget, _value):
        if hasattr(self, '_bg_rect') and widget:
            self._bg_rect.pos = widget.pos
            self._bg_rect.size = widget.size

    def _on_continue(self, _inst):
        self.goto('room_name', transition='slide_left')
