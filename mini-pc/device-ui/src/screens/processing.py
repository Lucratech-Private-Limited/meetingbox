"""
Processing screen aligned to Figma "Processing Complete (S-05)".

Flow:
- User presses End Meeting -> app navigates here.
- While backend runs, stage list updates from progress/status events.
- When summary is ready, CTA enables and user can open meeting summary.
"""

import logging
import math
import time

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from config import (
    ASSETS_DIR,
    COLORS,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    FONT_SIZES,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
)
from screens.base_screen import BaseScreen

logger = logging.getLogger(__name__)

_BG = (13 / 255.0, 17 / 255.0, 23 / 255.0, 1)
_BORDER = (30 / 255.0, 41 / 255.0, 59 / 255.0, 1)
_MUTED = (148 / 255.0, 163 / 255.0, 184 / 255.0, 1)
_SUCCESS = (34 / 255.0, 197 / 255.0, 94 / 255.0, 1)
_CTA = (74 / 255.0, 143 / 255.0, 217 / 255.0, 1)

# Vertical stack height in design pixels — must match the sum built in _build_ui:
# hero_h + card_wrap + spacer(2) + cta_h + link(20) + 4*col_sp(6) (see _build_ui).
_PROCESSING_BODY_STACK_DESIGN_PX = 552


def _compute_processing_layout_fit() -> float:
    """Only shrink *vertical* spacing when the body column would overflow (e.g. 800px height)."""
    v = other_screen_vertical_scale()
    # Chrome uses the same design px as _build_ui header/footer (before fit).
    chrome = int(round((52 + 48 + 20) * v))
    avail = max(240, DISPLAY_HEIGHT - chrome)
    need = _PROCESSING_BODY_STACK_DESIGN_PX * v
    if need <= avail:
        return 1.0
    # Floor ~0.58: keeps touch targets readable; stack estimate is conservative.
    return max(0.58, min(1.0, float(avail) / float(max(need, 1))))


_PROC_ASSETS = ASSETS_DIR / "processing"
_REC_ASSETS = ASSETS_DIR / "recording"
_HEADER_GEAR_PROC = _PROC_ASSETS / "header_gear.png"
_HEADER_PROFILE_RING = _PROC_ASSETS / "header_profile_ring.png"
_SUMMARY_CTA_IMG = _PROC_ASSETS / "view_meeting_summary.png"


class _CanvasHeaderGlyph(Widget):
    """Help (?) and user bust when ``header_help.png`` / ``header_user.png`` are missing."""

    def __init__(self, kind: str, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (22, 22))
        super().__init__(**kwargs)
        self._kind = kind
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(lambda *_: self._draw(), 0)

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        rr = min(self.width, self.height) * 0.48
        if rr < 2:
            return
        c = (0.78, 0.82, 0.86, 1)
        lw = max(1.8, rr * 0.12)
        with self.canvas:
            Color(*c)
            if self._kind == "gear":
                ir = rr * 0.2
                orad = rr * 0.42
                Line(circle=(cx, cy, ir), width=lw)
                for i in range(6):
                    a = (math.pi / 3.0) * i
                    x0 = cx + math.cos(a) * ir * 0.9
                    y0 = cy + math.sin(a) * ir * 0.9
                    x1 = cx + math.cos(a) * orad
                    y1 = cy + math.sin(a) * orad
                    Line(points=[x0, y0, x1, y1], width=lw, cap="round")
            elif self._kind == "help":
                # Arc (partial circle) + stem + dot
                Line(circle=(cx, cy + rr * 0.06, rr * 0.28, 70, 305), width=lw)
                Line(points=[cx, cy + rr * 0.1, cx, cy - rr * 0.4], width=lw, cap="round")
                Ellipse(pos=(cx - rr * 0.08, cy - rr * 0.54), size=(rr * 0.16, rr * 0.16))
            else:
                # Head + shoulders (U)
                Ellipse(pos=(cx - rr * 0.32, cy - rr * 0.02), size=(rr * 0.64, rr * 0.66))
                Line(
                    points=[
                        cx - rr * 0.52,
                        cy + rr * 0.52,
                        cx - rr * 0.32,
                        cy - rr * 0.02,
                        cx + rr * 0.32,
                        cy - rr * 0.02,
                        cx + rr * 0.52,
                        cy + rr * 0.52,
                    ],
                    width=lw,
                    cap="round",
                    joint="round",
                )


class _HeroCheckCircle(Widget):
    """Figma S-05 hero: faux-blur green glow + large light-green disc with a 4px
    ring, and an inner solid green disc carrying the white check."""

    def __init__(self, size_px: int, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (size_px, size_px))
        super().__init__(**kwargs)
        # State controls the inner glyph (done=filled disc + check, active=spinner).
        self._state = "done"
        self._spin_angle = 0.0
        self.bind(pos=self._draw, size=self._draw)
        Clock.schedule_once(lambda *_: self._draw(), 0)

    def set_state(self, state: str):
        self._state = state if state in ("active", "done") else "done"
        self._draw()

    def set_spin_angle(self, angle: float):
        if self._state != "active":
            return
        self._spin_angle = angle % 360.0
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) / 2.0
        if r < 4:
            return
        with self.canvas:
            # Faux blur glow — layered fading ellipses (~50px blur in Figma).
            for i, (factor, alpha) in enumerate(((1.55, 0.06), (1.30, 0.10), (1.10, 0.16))):
                gr = r * factor
                Color(34 / 255.0, 197 / 255.0, 94 / 255.0, alpha)
                Ellipse(pos=(cx - gr, cy - gr), size=(gr * 2, gr * 2))

            # Outer light-green disc (Figma: rgba(34,197,94,0.1))
            Color(34 / 255.0, 197 / 255.0, 94 / 255.0, 0.10)
            Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))

            # 4px border ring (Figma: rgba(34,197,94,0.3))
            border_w = max(2.0, r * 0.08)
            Color(34 / 255.0, 197 / 255.0, 94 / 255.0, 0.30)
            Line(circle=(cx, cy, r - border_w / 2.0), width=border_w)

            # Inner disc (Figma inner 50px vs outer 128px → ratio ~0.39)
            ir = r * 0.42
            if self._state == "done":
                Color(*_SUCCESS, 1.0)
                Ellipse(pos=(cx - ir, cy - ir), size=(ir * 2, ir * 2))
                # Large white check
                Color(1, 1, 1, 1)
                lw = max(2.5, ir * 0.22)
                x0, y0 = cx - ir * 0.48, cy - ir * 0.04
                x1, y1 = cx - ir * 0.10, cy - ir * 0.42
                x2, y2 = cx + ir * 0.52, cy + ir * 0.34
                Line(points=[x0, y0, x1, y1, x2, y2], width=lw, cap="round", joint="round")
            else:
                # Spinner while analysis is still running — matches the stage spinner.
                Color(*_SUCCESS, 0.15)
                Line(circle=(cx, cy, ir), width=max(2.0, ir * 0.14))
                start = self._spin_angle
                end = (self._spin_angle + 110.0) % 360.0
                Color(*_SUCCESS, 1.0)
                Line(
                    circle=(cx, cy, ir, start, end),
                    width=max(2.5, ir * 0.18),
                    cap="round",
                )


class _StageMark(Widget):
    """Stage icon with three clearly distinct states.

    - pending: muted hollow ring (no fill, no check)
    - active: faint track ring + rotating green arc (animated spinner)
    - done:   filled green disc + white check (Figma S-05)
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (22, 22))
        super().__init__(**kwargs)
        self._state = "pending"
        self._spin_angle = 0.0
        self.bind(pos=self._draw, size=self._draw)

    def set_state(self, state: str):
        if state not in ("pending", "active", "done"):
            state = "pending"
        self._state = state
        self._draw()

    def set_spin_angle(self, angle: float):
        # Only redraw while actively spinning; pending/done are static.
        if self._state != "active":
            return
        self._spin_angle = angle % 360.0
        self._draw()

    def is_active(self) -> bool:
        return self._state == "active"

    def _draw(self, *_):
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) * 0.46
        if r < 2:
            return
        with self.canvas:
            if self._state == "pending":
                Color(*_MUTED, 0.55)
                Line(circle=(cx, cy, r * 0.82), width=max(1.3, r * 0.12))
            elif self._state == "active":
                # Track ring (muted)
                Color(*_MUTED, 0.30)
                Line(circle=(cx, cy, r * 0.82), width=max(1.4, r * 0.14))
                # Rotating green arc (~110°)
                start = self._spin_angle
                end = (self._spin_angle + 110.0) % 360.0
                Color(*_SUCCESS, 0.95)
                Line(
                    circle=(cx, cy, r * 0.82, start, end),
                    width=max(1.6, r * 0.18),
                    cap="round",
                )
                # Small dot at arc leading tip — extra affordance of "running"
                lead_rad = math.radians(90.0 - end)
                dx = cx + math.cos(lead_rad) * r * 0.82
                dy = cy + math.sin(lead_rad) * r * 0.82
                dot_r = max(1.4, r * 0.14)
                Color(*_SUCCESS, 1.0)
                Ellipse(pos=(dx - dot_r, dy - dot_r), size=(dot_r * 2, dot_r * 2))
            else:
                # Filled green disc
                Color(*_SUCCESS, 1.0)
                Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))
                # White check
                Color(1, 1, 1, 1)
                lw = max(1.8, r * 0.22)
                x0, y0 = cx - r * 0.42, cy - r * 0.04
                x1, y1 = cx - r * 0.10, cy - r * 0.36
                x2, y2 = cx + r * 0.44, cy + r * 0.28
                Line(points=[x0, y0, x1, y1, x2, y2], width=lw, cap="round", joint="round")


class _TextLink(ButtonBehavior, Label):
    def on_press(self):
        self.opacity = 0.60

    def on_release(self):
        self.opacity = 1.0


class _SummaryImageButton(ButtonBehavior, Image):
    """Figma-exported View Meeting Summary pill (single PNG)."""


class _StageRow(BoxLayout):
    """Single timeline stage row with indicator, optional connector, title, subtitle."""

    def __init__(self, title: str, subtitle: str, show_connector: bool, parent_screen, **kwargs):
        ps = parent_screen
        row_h = ps.pv(68 if show_connector else 44)
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", row_h)
        kwargs.setdefault("spacing", ps.ph(14))
        super().__init__(**kwargs)
        self._show_connector = show_connector
        self._state = "pending"
        self._screen = parent_screen

        mark_sz = ps.ph(22)
        left = BoxLayout(
            orientation="vertical",
            size_hint=(None, 1),
            width=max(mark_sz, ps.ph(24)),
            spacing=0,
        )
        self.mark = _StageMark(size=(mark_sz, mark_sz))
        mark_anchor = AnchorLayout(size_hint=(1, None), height=mark_sz, anchor_x="center", anchor_y="top")
        mark_anchor.add_widget(self.mark)
        left.add_widget(mark_anchor)
        if show_connector:
            self.connector = Widget(size_hint=(1, 1))
            with self.connector.canvas:
                self._connector_color = Color(*_BORDER[:3], 0.45)
                self._connector_line = Rectangle(pos=self.connector.pos, size=self.connector.size)
            self.connector.bind(
                pos=lambda w, *_: setattr(
                    self._connector_line, "pos", (w.center_x - 0.5, w.y + ps.pv(2))
                ),
                size=lambda w, *_: setattr(
                    self._connector_line,
                    "size",
                    (1, max(1, w.height - ps.pv(6))),
                ),
            )
            left.add_widget(self.connector)
        self.add_widget(left)

        txt = BoxLayout(orientation="vertical", size_hint=(1, 1), spacing=ps.pv(2))
        self.title = Label(
            text=title,
            font_size=ps.pf(16),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=ps.pv(22),
        )
        self.title.bind(size=self.title.setter("text_size"))
        txt.add_widget(self.title)
        self.subtitle = Label(
            text=subtitle,
            font_size=ps.pf(FONT_SIZES["small"]),
            color=_MUTED,
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=ps.pv(20),
        )
        self.subtitle.bind(size=self.subtitle.setter("text_size"))
        txt.add_widget(self.subtitle)
        self.add_widget(txt)

    def set_state(self, state: str):
        self._state = state
        self.mark.set_state(state)
        if self._show_connector:
            if state in ("done", "active"):
                self._connector_color.rgba = (34 / 255.0, 197 / 255.0, 94 / 255.0, 0.30)
            else:
                self._connector_color.rgba = (*_BORDER[:3], 0.45)


class ProcessingScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._started_ts = None
        self._meeting_id = None
        self._summary_data = None
        self._summary_ready = False
        self._transcript_ready = False
        self._pulse_event = None
        self._pulse_alpha = 0.20
        self._pulse_dir = 1
        self._spin_event = None
        self._spin_angle = 0.0
        self._layout_fit = _compute_processing_layout_fit()
        self._build_ui()

    def pv(self, px: float) -> int:
        v = other_screen_vertical_scale()
        return max(1, int(round(float(px) * v * self._layout_fit)))

    def ph(self, px: float) -> int:
        # Horizontal size follows display width only — do not apply _layout_fit or the
        # column looks like a tiny island on 1280×800 (Figma fills most of the width).
        h = other_screen_horizontal_scale()
        return max(1, int(round(float(px) * h)))

    def pf(self, fs: float) -> int:
        # Type size tracks vertical scale like other screens; optional slight tie to fit
        # so fonts don't outgrow a compressed column, but stay closer to Figma on 800px.
        v = other_screen_vertical_scale()
        t = max(self._layout_fit, 0.82) if self._layout_fit < 1.0 else 1.0
        return max(6, int(round(float(fs) * v * t)))

    def _sync_cta_chevron(self, inst, *args):
        ch = getattr(self, "_cta_chevron", None)
        if ch is None or inst is None:
            return
        pad = self.ph(20)
        tri_h = self.pv(10)
        x2 = inst.right - pad
        x0 = x2 - self.ph(12)
        cy = inst.center_y
        ch.points = [x0, cy - tri_h, x2, cy, x0, cy + tri_h]

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        with root.canvas.before:
            Color(*_BG)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._bg_rect, "pos", w.pos),
            size=lambda w, *_: setattr(self._bg_rect, "size", w.size),
        )

        # Header
        header = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=self.pv(52),
            padding=[self.ph(24), self.pv(10), self.ph(24), self.pv(10)],
        )
        with header.canvas.after:
            Color(*_BORDER)
            self._header_line = Rectangle(pos=(header.x, header.y), size=(header.width, 1))
        header.bind(
            pos=lambda w, *_: setattr(self._header_line, "pos", (w.x, w.y)),
            size=lambda w, *_: setattr(self._header_line, "size", (w.width, 1)),
        )
        left = BoxLayout(orientation="horizontal", size_hint=(None, 1), width=self.ph(220), spacing=self.ph(10))
        _logo_path = ASSETS_DIR / "welcome" / "LOGO.png"
        if _logo_path.is_file():
            logo = Image(
                source=str(_logo_path),
                size_hint=(None, 1),
                width=self.ph(36),
                fit_mode="contain",
                allow_stretch=True,
            )
        else:
            logo = Label(
                text="MB",
                font_size=self.pf(14),
                bold=True,
                color=_CTA,
                halign="center",
                valign="middle",
                size_hint=(None, 1),
                width=self.ph(36),
            )
            logo.bind(size=logo.setter("text_size"))
        left.add_widget(logo)
        brand = Label(
            text="MeetingBox",
            font_size=self.pf(FONT_SIZES["medium"]),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        )
        brand.bind(size=brand.setter("text_size"))
        left.add_widget(brand)
        header.add_widget(left)
        header.add_widget(Widget())
        right = BoxLayout(orientation="horizontal", size_hint=(None, 1), width=self.ph(146), spacing=self.ph(10))
        try:
            _PROC_ASSETS.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.debug("Could not create %s", _PROC_ASSETS)
        _gear_src = (
            _HEADER_GEAR_PROC
            if _HEADER_GEAR_PROC.is_file()
            else (_REC_ASSETS / "setteing gear icon.png")
        )
        _header_specs = (
            (_gear_src, "gear"),
            (_PROC_ASSETS / "header_help.png", "help"),
            (_PROC_ASSETS / "header_user.png", "user"),
        )
        _cell_sz = (self.ph(40), self.pv(40))
        for idx, (img_path, glyph) in enumerate(_header_specs):
            # Third control: Figma exports ring (Button…) + user glyph (Container…) stacked.
            if idx == 2 and _HEADER_PROFILE_RING.is_file():
                cell = FloatLayout(size_hint=(None, None), size=_cell_sz)
                ring = Image(
                    source=str(_HEADER_PROFILE_RING),
                    size_hint=(1, 1),
                    pos_hint={"x": 0, "y": 0},
                    allow_stretch=True,
                    keep_ratio=True,
                    fit_mode="contain",
                )
                cell.add_widget(ring)
                user_p = _PROC_ASSETS / "header_user.png"
                if user_p.is_file():
                    cell.add_widget(
                        Image(
                            source=str(user_p),
                            size_hint=(None, None),
                            size=(self.ph(22), self.pv(22)),
                            pos_hint={"center_x": 0.5, "center_y": 0.5},
                            fit_mode="contain",
                            allow_stretch=True,
                        )
                    )
                else:
                    cell.add_widget(
                        _CanvasHeaderGlyph(
                            glyph,
                            size_hint=(None, None),
                            size=(self.ph(22), self.pv(22)),
                            pos_hint={"center_x": 0.5, "center_y": 0.5},
                        )
                    )
                right.add_widget(cell)
                continue

            cell = AnchorLayout(
                size_hint=(None, None),
                size=_cell_sz,
                anchor_x="center",
                anchor_y="center",
            )
            with cell.canvas.before:
                Color(*COLORS["surface"])
                cell_bg = RoundedRectangle(pos=cell.pos, size=cell.size, radius=[999])
            cell.bind(
                pos=lambda w, _bg=cell_bg: setattr(_bg, "pos", w.pos),
                size=lambda w, _bg=cell_bg: setattr(_bg, "size", w.size),
            )
            if idx == 2:
                with cell.canvas.after:
                    Color(74 / 255.0, 143 / 255.0, 217 / 255.0, 0.22)
                    cell_border = Line(
                        circle=(
                            cell.center_x,
                            cell.center_y,
                            max(1, min(cell.width, cell.height) / 2 - 2),
                        ),
                        width=1.8,
                    )
                cell.bind(
                    pos=lambda w, _bd=cell_border: setattr(
                        _bd,
                        "circle",
                        (w.center_x, w.center_y, max(1, min(w.width, w.height) / 2 - 2)),
                    ),
                    size=lambda w, _bd=cell_border: setattr(
                        _bd,
                        "circle",
                        (w.center_x, w.center_y, max(1, min(w.width, w.height) / 2 - 2)),
                    ),
                )
            if img_path.is_file():
                img = Image(
                    source=str(img_path),
                    size_hint=(None, None),
                    size=(self.ph(22), self.pv(22)),
                    fit_mode="contain",
                    allow_stretch=True,
                )
                cell.add_widget(img)
            else:
                logger.debug(
                    "Processing header: missing %s — vector fallback (optional: %s)",
                    img_path.name,
                    _PROC_ASSETS,
                )
                cell.add_widget(
                    _CanvasHeaderGlyph(
                        glyph,
                        size=(self.ph(22), self.pv(22)),
                    )
                )
            right.add_widget(cell)
        header.add_widget(right)
        root.add_widget(header)

        body = AnchorLayout(anchor_x="center", anchor_y="center", size_hint=(1, 1))
        side = self.ph(20)
        col_w = max(self.ph(560), min(DISPLAY_WIDTH - 2 * side, self.ph(1024)))
        col_sp = self.pv(6)
        card_pad_v = self.pv(12)
        r1, r2, r3 = self.pv(68), self.pv(68), self.pv(44)
        card_sp = self.pv(6)
        card_inner_h = card_pad_v * 2 + r1 + r2 + r3 + card_sp * 2
        card_wrap_h = card_inner_h + self.pv(2)

        sub_h = self.pv(50)
        hero_h = self.pv(232)
        cta_h = self.pv(68) if _SUMMARY_CTA_IMG.is_file() else self.pv(54)
        link_h = self.pv(22)
        col_h = (
            hero_h
            + card_wrap_h
            + self.pv(2)
            + cta_h
            + link_h
            + 4 * col_sp
        )
        col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=col_w,
            height=col_h,
            spacing=col_sp,
        )

        # Hero
        hero = AnchorLayout(size_hint=(1, None), height=hero_h, anchor_x="center", anchor_y="center")
        with hero.canvas.before:
            Color(34 / 255.0, 197 / 255.0, 94 / 255.0, 0.18)
            self._hero_glow = Ellipse(
                pos=(hero.center_x - self.ph(140), hero.center_y - self.pv(90)),
                size=(self.ph(280), self.pv(180)),
            )
        hero.bind(
            pos=lambda w, *_: setattr(
                self._hero_glow,
                "pos",
                (w.center_x - self.ph(140), w.center_y - self.pv(90)),
            ),
            size=lambda w, *_: setattr(self._hero_glow, "size", (self.ph(280), self.pv(180))),
        )
        hero_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=col_w,
            height=hero_h,
            spacing=self.pv(4),
        )

        check_sz = self.ph(112)
        check_wrap = AnchorLayout(size_hint=(1, None), height=self.pv(100), anchor_x="center", anchor_y="center")
        check = _HeroCheckCircle(size_px=check_sz)
        check_wrap.add_widget(check)
        hero_col.add_widget(check_wrap)

        self.success_badge = Label(
            text="Success",
            font_size=self.pf(FONT_SIZES["tiny"]),
            color=_SUCCESS,
            halign="center",
            valign="middle",
            size_hint=(None, None),
            size=(self.ph(64), self.pv(18)),
        )
        self.success_badge.bind(size=self.success_badge.setter("text_size"))
        success_badge_wrap = AnchorLayout(size_hint=(1, None), height=self.pv(20))
        with success_badge_wrap.canvas.before:
            Color(34 / 255.0, 197 / 255.0, 94 / 255.0, 0.10)
            self._success_badge_bg = RoundedRectangle(
                pos=(0, 0), size=self.success_badge.size, radius=[999]
            )
        # Figma: inset 1px ring of rgba(34,197,94,0.2) around the pill.
        with success_badge_wrap.canvas.after:
            self._success_badge_border_col = Color(
                34 / 255.0, 197 / 255.0, 94 / 255.0, 0.28
            )
            self._success_badge_border = Line(
                rounded_rectangle=(
                    0,
                    0,
                    self.success_badge.width,
                    self.success_badge.height,
                    min(self.success_badge.width, self.success_badge.height) / 2.0,
                ),
                width=1.1,
            )
        success_badge_wrap.bind(
            pos=lambda w, *_: setattr(
                self._success_badge_bg,
                "pos",
                (
                    w.center_x - self.success_badge.width / 2,
                    w.center_y - self.success_badge.height / 2,
                ),
            ),
            size=lambda w, *_: setattr(
                self._success_badge_bg,
                "size",
                self.success_badge.size,
            ),
        )
        def _sync_success_badge_border(*_a):
            w = self.success_badge.width
            h = self.success_badge.height
            bx = success_badge_wrap.center_x - w / 2.0
            by = success_badge_wrap.center_y - h / 2.0
            self._success_badge_border.rounded_rectangle = (
                bx,
                by,
                w,
                h,
                min(w, h) / 2.0,
            )

        self.success_badge.bind(
            size=lambda *_: (
                setattr(self._success_badge_bg, "size", self.success_badge.size),
                _sync_success_badge_border(),
            ),
            pos=lambda *_: (
                setattr(
                    self._success_badge_bg,
                    "pos",
                    (
                        success_badge_wrap.center_x - self.success_badge.width / 2,
                        success_badge_wrap.center_y - self.success_badge.height / 2,
                    ),
                ),
                _sync_success_badge_border(),
            ),
        )
        success_badge_wrap.bind(
            pos=lambda *_: _sync_success_badge_border(),
            size=lambda *_: _sync_success_badge_border(),
        )
        success_badge_wrap.add_widget(self.success_badge)
        hero_col.add_widget(success_badge_wrap)

        self.title_label = Label(
            text="Preparing Analysis...",
            font_size=self.pf(42),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=self.pv(46),
        )
        self.title_label.bind(size=self.title_label.setter("text_size"))
        hero_col.add_widget(self.title_label)

        self.subtitle_label = Label(
            text="Please wait while transcript and action items are prepared.",
            font_size=self.pf(FONT_SIZES["body"] + 1),
            color=_MUTED,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sub_h,
        )
        def _subtitle_text_width(*_a):
            self.subtitle_label.text_size = (self.subtitle_label.width, None)

        self.subtitle_label.bind(size=_subtitle_text_width)
        self.subtitle_label.bind(width=_subtitle_text_width)
        hero_col.add_widget(self.subtitle_label)

        hero.add_widget(hero_col)
        col.add_widget(hero)

        # Stage card
        card_wrap = AnchorLayout(size_hint=(1, None), height=card_wrap_h, anchor_x="center", anchor_y="center")
        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=min(self.ph(672), col_w - self.ph(16)),
            height=card_inner_h,
            padding=[self.ph(18), card_pad_v, self.ph(18), card_pad_v],
            spacing=card_sp,
        )
        with card.canvas.before:
            Color(15 / 255.0, 23 / 255.0, 42 / 255.0, 0.50)
            self._card_bg = RoundedRectangle(pos=card.pos, size=card.size, radius=[16])
        with card.canvas.after:
            Color(*_BORDER)
            self._card_border = Line(
                rounded_rectangle=(card.x, card.y, card.width, card.height, 16),
                width=1.1,
            )
        card.bind(
            pos=lambda w, *_: setattr(self._card_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._card_bg, "size", w.size),
        )
        card.bind(
            pos=lambda w, *_: setattr(self._card_border, "rounded_rectangle", (w.x, w.y, w.width, w.height, 16)),
            size=lambda w, *_: setattr(self._card_border, "rounded_rectangle", (w.x, w.y, w.width, w.height, 16)),
        )

        self.stage_1 = _StageRow("Transcribing", "Voice data converted to text format", True, self)
        self.stage_2 = _StageRow("Analysing", "Key insights and action items extracted", True, self)
        self.stage_3 = _StageRow("Ready", "Summary generated and dashboard updated", False, self)
        card.add_widget(self.stage_1)
        card.add_widget(self.stage_2)
        card.add_widget(self.stage_3)
        card_wrap.add_widget(card)
        col.add_widget(card_wrap)

        col.add_widget(Widget(size_hint=(1, None), height=self.pv(2)))

        # CTA — Figma bitmap when present, else drawn pill + label
        cta_wrap = AnchorLayout(size_hint=(1, None), height=cta_h, anchor_x="center", anchor_y="center")
        wcap = min(self.ph(448), col_w - self.ph(24))
        if _SUMMARY_CTA_IMG.is_file():
            self.summary_btn = _SummaryImageButton(
                source=str(_SUMMARY_CTA_IMG),
                size_hint=(None, None),
                size=(wcap, self.pv(56)),
                allow_stretch=True,
                keep_ratio=True,
                fit_mode="contain",
                opacity=0.60,
            )
            # Soft drop shadow beneath the pill (Figma: 0 10 15 -3 rgba(74,143,217,0.2)).
            with self.summary_btn.canvas.before:
                self._cta_img_shadow_col = Color(
                    74 / 255.0, 143 / 255.0, 217 / 255.0, 0.22 * self.summary_btn.opacity
                )
                self._cta_img_shadow = RoundedRectangle(
                    pos=(self.summary_btn.x - self.ph(4), self.summary_btn.y - self.pv(8)),
                    size=(self.summary_btn.width + self.ph(8), self.summary_btn.height),
                    radius=[999],
                )

            def _sync_cta_img_shadow(*_a):
                self._cta_img_shadow.pos = (
                    self.summary_btn.x - self.ph(4),
                    self.summary_btn.y - self.pv(8),
                )
                self._cta_img_shadow.size = (
                    self.summary_btn.width + self.ph(8),
                    self.summary_btn.height,
                )

            def _sync_cta_img_shadow_opacity(*_a):
                self._cta_img_shadow_col.rgba = (
                    74 / 255.0,
                    143 / 255.0,
                    217 / 255.0,
                    0.22 * self.summary_btn.opacity,
                )

            def _fit_summary_cta_texture(*_a):
                tw, th = self.summary_btn.texture_size
                if not tw or not th:
                    return
                self.summary_btn.width = wcap
                ar = float(th) / float(tw)
                h = int(round(float(wcap) * ar))
                self.summary_btn.height = min(max(self.pv(48), h), self.pv(68))
                _sync_cta_img_shadow()

            self.summary_btn.bind(texture_size=_fit_summary_cta_texture)
            self.summary_btn.bind(pos=_sync_cta_img_shadow, size=_sync_cta_img_shadow)
            self.summary_btn.bind(opacity=_sync_cta_img_shadow_opacity)
            self.summary_btn.bind(on_press=self._open_summary)
        else:
            self.summary_btn = Button(
                text="View Meeting Summary",
                font_size=self.pf(FONT_SIZES["medium"] + 2),
                bold=True,
                color=COLORS["white"],
                size_hint=(None, None),
                size=(wcap, self.pv(52)),
                background_normal="",
                background_down="",
                background_color=(0, 0, 0, 0),
                opacity=0.60,
            )
            with self.summary_btn.canvas.before:
                self._cta_color = Color(*_CTA, self.summary_btn.opacity)
                self._cta_bg = RoundedRectangle(
                    pos=self.summary_btn.pos,
                    size=self.summary_btn.size,
                    radius=[999],
                )
            with self.summary_btn.canvas.after:
                self._cta_shadow_color = Color(74 / 255.0, 143 / 255.0, 217 / 255.0, 0.24)
                self._cta_shadow = RoundedRectangle(
                    pos=(self.summary_btn.x, self.summary_btn.y - self.pv(2)),
                    size=self.summary_btn.size,
                    radius=[999],
                )
                self._cta_chv_col = Color(1, 1, 1, self.summary_btn.opacity)
                self._cta_chevron = Line(
                    width=max(2.0, float(self.pv(2))),
                    cap="round",
                    joint="round",
                )
            self.summary_btn.bind(
                pos=self._sync_cta_chevron,
                size=self._sync_cta_chevron,
            )
            self.summary_btn.bind(
                pos=lambda w, *_: setattr(self._cta_bg, "pos", w.pos),
                size=lambda w, *_: setattr(self._cta_bg, "size", w.size),
                opacity=lambda _, a: setattr(self._cta_color, "rgba", (*_CTA[:3], a)),
            )
            self.summary_btn.bind(
                pos=lambda w, *_: setattr(self._cta_shadow, "pos", (w.x, w.y - self.pv(2))),
                size=lambda w, *_: setattr(self._cta_shadow, "size", w.size),
                opacity=lambda _, a: setattr(
                    self._cta_shadow_color,
                    "rgba",
                    (74 / 255.0, 143 / 255.0, 217 / 255.0, 0.24 * a),
                ),
            )
            self.summary_btn.bind(
                opacity=lambda _, a: setattr(self._cta_chv_col, "rgba", (1, 1, 1, a)),
            )
            self.summary_btn.bind(on_press=self._open_summary)
            Clock.schedule_once(lambda *_: self._sync_cta_chevron(self.summary_btn), 0)
        cta_wrap.add_widget(self.summary_btn)
        col.add_widget(cta_wrap)

        self.home_link = _TextLink(
            text="Back to Home",
            font_size=self.pf(FONT_SIZES["small"]),
            color=_MUTED,
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=link_h,
        )
        self.home_link.bind(size=self.home_link.setter("text_size"))
        self.home_link.bind(on_press=lambda *_: self.goto("home", transition="fade"))
        col.add_widget(self.home_link)

        body.add_widget(col)
        root.add_widget(body)

        # Footer
        footer = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=self.pv(48),
            padding=[self.ph(20), self.pv(6), self.ph(20), self.pv(6)],
        )
        with footer.canvas.before:
            Color(*_BG)
            self._footer_bg = Rectangle(pos=footer.pos, size=footer.size)
            Color(*_BORDER)
            self._footer_top = Rectangle(pos=(footer.x, footer.top - 1), size=(footer.width, 1))
        footer.bind(
            pos=lambda w, *_: setattr(self._footer_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._footer_bg, "size", w.size),
        )
        footer.bind(
            pos=lambda w, *_: setattr(self._footer_top, "pos", (w.x, w.top - 1)),
            size=lambda w, *_: setattr(self._footer_top, "size", (w.width, 1)),
        )
        left_footer = BoxLayout(
            orientation="horizontal",
            size_hint=(0.6, 1),
            spacing=self.ph(8),
        )
        dot = Widget(size_hint=(None, None), size=(self.ph(8), self.pv(8)))
        with dot.canvas:
            Color(*_SUCCESS)
            self._footer_dot = Ellipse(pos=dot.pos, size=dot.size)
        dot.bind(
            pos=lambda w, *_: setattr(self._footer_dot, "pos", w.pos),
            size=lambda w, *_: setattr(self._footer_dot, "size", w.size),
        )
        dot_holder = AnchorLayout(size_hint=(None, 1), width=self.ph(12), anchor_x="center", anchor_y="center")
        dot_holder.add_widget(dot)
        left_footer.add_widget(dot_holder)
        self.footer_left = Label(
            text="SYSTEM ONLINE",
            font_size=self.pf(FONT_SIZES["tiny"]),
            bold=True,
            color=_MUTED,
            halign="left",
            valign="middle",
            size_hint=(1, 1),
        )
        self.footer_left.bind(size=self.footer_left.setter("text_size"))
        left_footer.add_widget(self.footer_left)
        footer.add_widget(left_footer)
        self.footer_right = Label(
            text="Analysis in progress...",
            font_size=self.pf(FONT_SIZES["small"]),
            color=_MUTED,
            halign="right",
            valign="middle",
            size_hint=(0.4, 1),
        )
        self.footer_right.bind(size=self.footer_right.setter("text_size"))
        footer.add_widget(self.footer_right)
        root.add_widget(footer)

        # Keep a handle to the hero check widget so we can swap its state between
        # "spinner while working" and "filled check when done".
        self._hero_check = check

        self.add_widget(root)
        self._set_stage(0, "active")
        self._set_stage(1, "pending")
        self._set_stage(2, "pending")
        self._hero_check.set_state("active")

    def _set_stage(self, idx: int, state: str):
        row = (self.stage_1, self.stage_2, self.stage_3)[idx]
        row.set_state(state)

    def _set_stage_progress(self, active_idx: int, ready: bool = False):
        for i in range(3):
            if ready:
                self._set_stage(i, "done")
                continue
            if i < active_idx:
                self._set_stage(i, "done")
            elif i == active_idx:
                self._set_stage(i, "active")
            else:
                self._set_stage(i, "pending")
        # Hero mirrors stage state — keep the animated ring until Ready is done.
        if hasattr(self, "_hero_check") and self._hero_check is not None:
            self._hero_check.set_state("done" if ready else "active")

    def _start_pulse(self):
        self._stop_pulse()
        self._pulse_event = Clock.schedule_interval(self._tick_pulse, 0.08)

    def _stop_pulse(self):
        if self._pulse_event:
            self._pulse_event.cancel()
            self._pulse_event = None

    def _start_spin(self):
        self._stop_spin()
        self._spin_event = Clock.schedule_interval(self._tick_spin, 1.0 / 30.0)

    def _stop_spin(self):
        if self._spin_event:
            self._spin_event.cancel()
            self._spin_event = None

    def _tick_spin(self, dt):
        self._spin_angle = (self._spin_angle + 360.0 * dt / 1.2) % 360.0
        for row in (self.stage_1, self.stage_2, self.stage_3):
            if row.mark.is_active():
                row.mark.set_spin_angle(self._spin_angle)
        if hasattr(self, "_hero_check") and self._hero_check is not None:
            self._hero_check.set_spin_angle(self._spin_angle)

    def _tick_pulse(self, _dt):
        if self._summary_ready:
            self.success_badge.opacity = 1.0
            return
        self._pulse_alpha += 0.03 * self._pulse_dir
        if self._pulse_alpha >= 1.0:
            self._pulse_alpha = 1.0
            self._pulse_dir = -1
        elif self._pulse_alpha <= 0.35:
            self._pulse_alpha = 0.35
            self._pulse_dir = 1
        self.success_badge.opacity = self._pulse_alpha

    def on_processing_started(self, data):
        title = (data.get("title") or "Untitled").strip()
        dur_min = int((data.get("duration", 0) or 0) / 60)
        self.subtitle_label.text = (
            f"Meeting '{title}' ({dur_min} min) is being transcribed and analysed."
        )

    def on_transcription_ready(self, meeting_id: str):
        """Transcript is saved server-side — enable View Meeting Summary CTA."""
        try:
            if meeting_id:
                self.app._transcript_cta_satisfied_meeting_id = meeting_id
        except Exception:
            pass
        if meeting_id:
            self._meeting_id = meeting_id
        self._transcript_ready = True
        self.summary_btn.disabled = False
        self.summary_btn.opacity = 1.0
        self.set_processing_status("Transcription done. Building meeting report…")

    def set_processing_status(self, text: str):
        if not text:
            return
        low = text.lower()
        if "transcription done" in low or "building" in low:
            self._set_stage_progress(1)
        elif "updating report" in low or "finishing report" in low:
            self._set_stage_progress(1)
        elif "transcribing" in low:
            self._set_stage_progress(0)

    def on_backend_progress(self, progress: int, status: str, eta: int):
        if status:
            self.set_processing_status(status)
        eta = int(eta or 0)
        if eta > 0:
            if eta < 60:
                self.footer_right.text = "Analysis took less than 1 min"
            else:
                self.footer_right.text = f"Analysis ETA {eta // 60} min"

        p = max(0, min(100, int(progress or 0)))
        if p < 34:
            self._set_stage_progress(0)
        elif p < 84:
            self._set_stage_progress(1)
        elif not self._summary_ready:
            self._set_stage_progress(2)

    def on_summary_ready(self, meeting_id: str, summary_data: dict):
        self._meeting_id = meeting_id
        self._summary_data = summary_data or {}
        self._summary_ready = True
        self._set_stage_progress(2, ready=True)
        self._stop_pulse()
        self._stop_spin()
        self.success_badge.opacity = 1.0
        self.title_label.text = "Analysis Complete!"
        self.subtitle_label.text = (
            "Your meeting highlights, transcript, and AI-generated action\n"
            "items are now ready for review."
        )
        elapsed = max(1, int(time.monotonic() - (self._started_ts or time.monotonic())))
        mins, secs = divmod(elapsed, 60)
        self.footer_right.text = f"Analysis took {mins}m {secs:02d}s"
        self.summary_btn.disabled = False
        self.summary_btn.opacity = 1.0

    def _open_summary(self, _inst):
        if not self._meeting_id:
            logger.info("Summary CTA pressed but meeting_id is not set")
            return
        if not (self._transcript_ready or self._summary_ready):
            logger.info(
                "Summary CTA pressed before transcript was ready (meeting_id=%s)",
                self._meeting_id,
            )
            try:
                self.footer_right.text = "Still transcribing — please wait…"
            except Exception:
                pass
            return
        try:
            scr = self.app.screen_manager.get_screen("summary_review")
        except Exception as e:
            logger.warning("summary_review screen missing: %s", e)
            return
        payload = self._summary_data if self._summary_ready else {}
        if hasattr(scr, "set_meeting_data"):
            try:
                scr.set_meeting_data(self._meeting_id, payload or {})
            except Exception as e:
                logger.warning("set_meeting_data failed: %s", e)
        self.goto("summary_review", transition="fade")

    def on_enter(self):
        self._started_ts = time.monotonic()
        self._summary_data = None
        self._summary_ready = False
        mid = getattr(self.app, "current_session_id", None)
        self._meeting_id = mid
        done_for = getattr(self.app, "_transcription_done_for_session", None)
        self._transcript_ready = bool(mid and done_for == mid)
        self.title_label.text = "Preparing Analysis..."
        self.subtitle_label.text = "Please wait while transcript and action items are prepared."
        self.footer_right.text = "Analysis in progress..."
        # CTA appears once transcription is complete (WS or app flag).
        if self._transcript_ready:
            self.summary_btn.disabled = False
            self.summary_btn.opacity = 1.0
            self.set_processing_status("Transcription done. Building meeting report…")
        else:
            self.summary_btn.disabled = True
            self.summary_btn.opacity = 0.0
            self._set_stage_progress(0)
        self._pulse_alpha = 0.20
        self._pulse_dir = 1
        self._spin_angle = 0.0
        self._start_pulse()
        self._start_spin()

    def on_leave(self):
        self._stop_pulse()
        self._stop_spin()
