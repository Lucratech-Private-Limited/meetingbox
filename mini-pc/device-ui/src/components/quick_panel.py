"""
QuickPanel — swipe-down quick-settings overlay for MeetingBox device UI.

Floats above all screens as a FloatLayout sibling in root_layout (main.py).
Triggered by a downward swipe starting within the top 40 px of the screen.

All icons are canvas-drawn (components/icons.py) — no emoji or Unicode
symbols that require font fallback.

Layout (top → bottom):
  ┌──────────────────────────────────────────────────┐
  │  [bat icon] 87%   [wifi dot][bt dot]   14:30  X │  header
  ├──────────────────────────────────────────────────┤
  │  [vol icon]  Volume   ════════●═══════  75 %    │  sliders
  │  [bri icon]  Bright   ════════════●══  80 %    │
  ├──────────────────────────────────────────────────┤
  │ ┌──────────────┐  ┌──────────────┐              │  2×2 tiles
  │ │ [wifi icon]  │  │ [bt icon]    │              │
  │ │ Wi-Fi   [●] │  │ Bluetooth[●] │              │
  │ └──────────────┘  └──────────────┘              │
  │ ┌──────────────┐  ┌──────────────┐              │
  │ │ [plane icon] │  │ [gear icon]  │              │
  │ │ Airplane[●] │  │ Settings  →  │              │
  │ └──────────────┘  └──────────────┘              │
  ├──────────────────────────────────────────────────┤
  │  Wi-Fi Networks ───────────────────── [+]        │  expandable
  │  Bluetooth Devices ─────────────────  [+]        │  expandable
  ├──────────────────────────────────────────────────┤
  │  [lock icon] Lock  [restart]  [power icon] Off   │  footer
  └──────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, Optional

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.widget import Widget

import hardware
import wifi_nmcli_local
import bluetooth_local
from config import COLORS, DISPLAY_HEIGHT, DISPLAY_WIDTH
from components.toggle_switch import ToggleSwitch
from components.icons import Icon

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scaling helpers  (reference: 600 px tall, 1024 px wide screen)
# ---------------------------------------------------------------------------

def _sv(px: float) -> int:
    return max(1, int(round(px * DISPLAY_HEIGHT / 600)))


def _sh(px: float) -> int:
    return max(1, int(round(px * DISPLAY_WIDTH / 1024)))


def _sf(fs: float) -> int:
    ratio = min(DISPLAY_HEIGHT / 600, DISPLAY_WIDTH / 1024)
    return max(6, int(round(fs * ratio)))


# ---------------------------------------------------------------------------
# _lbl helper
# ---------------------------------------------------------------------------

def _lbl(text: str, fs: int, color, halign: str = "left",
         bold: bool = False, **kw) -> Label:
    lbl = Label(
        text=text,
        font_size=fs,
        color=color,
        halign=halign,
        valign="middle",
        bold=bold,
        **kw,
    )
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


# ---------------------------------------------------------------------------
# _QuickTile — a Control-Center-style icon tile with toggle or action
# ---------------------------------------------------------------------------

_TILE_ACTIVE_BG   = (0.08, 0.30, 0.72, 0.95)   # blue when on
_TILE_INACTIVE_BG = (0.16, 0.18, 0.22, 0.95)   # dark when off


class _QuickTile(ButtonBehavior, FloatLayout):
    """Square-ish tile: large icon top-left, name bottom-left, toggle/arrow top-right."""

    def __init__(self, kind: str, label: str, active: bool = False,
                 mode: str = "toggle",
                 on_change: Optional[Callable[[bool], None]] = None,
                 **kwargs):
        kwargs.setdefault("size_hint", (1, 1))
        super().__init__(**kwargs)

        self._kind = kind
        self._active = active
        self._mode = mode
        self._on_change = on_change

        icon_size = _sv(32)
        pad = _sv(10)

        # Background card
        with self.canvas.before:
            self._bg_col = Color(*(_TILE_ACTIVE_BG if active else _TILE_INACTIVE_BG))
            self._bg = RoundedRectangle(pos=self.pos, size=self.size,
                                        radius=[_sv(10)])
        self.bind(pos=self._sync_bg, size=self._sync_bg)

        # Icon (top-left)
        icon_col = (1.0, 1.0, 1.0, 0.95) if active else (0.65, 0.65, 0.70, 0.9)
        self._icon = Icon(
            kind=kind,
            color=icon_col,
            size_hint=(None, None),
            size=(icon_size, icon_size),
            pos_hint={"x": 0, "top": 1},
        )
        self._icon.bind(parent=lambda *_: self._reposition_icon())
        self.bind(pos=self._reposition_icon, size=self._reposition_icon)
        self.add_widget(self._icon)

        # Label (bottom-left)
        self._lbl = _lbl(
            label, _sf(11), (1.0, 1.0, 1.0, 0.9) if active else (0.65, 0.65, 0.70, 0.85),
            halign="left",
            size_hint=(None, None),
            size=(_sh(80), _sv(18)),
        )
        self.bind(pos=self._reposition_label, size=self._reposition_label)
        self.add_widget(self._lbl)

        # Right indicator: ToggleSwitch or arrow label
        if mode == "toggle":
            self._toggle = ToggleSwitch(
                active=active,
                on_toggle=self._on_toggle,
                size_hint=(None, None),
                size=(_sh(38), _sv(22)),
            )
            self.bind(pos=self._reposition_toggle, size=self._reposition_toggle)
            self.add_widget(self._toggle)
            self.bind(on_press=lambda *_: self._tap_toggle())
        else:
            arrow = _lbl(
                "->", _sf(12), COLORS["gray_500"],
                halign="right",
                size_hint=(None, None),
                size=(_sh(28), _sv(18)),
            )
            self.bind(pos=self._reposition_arrow(arrow),
                      size=self._reposition_arrow(arrow))
            self.add_widget(arrow)
            self._arrow_lbl = arrow

    # -- Positioning --------------------------------------------------------

    def _sync_bg(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _reposition_icon(self, *_):
        pad = _sv(8)
        self._icon.pos = (self.x + pad, self.top - _sv(32) - pad)

    def _reposition_label(self, *_):
        pad = _sv(8)
        self._lbl.pos = (self.x + pad, self.y + pad)
        self._lbl.size = (self.width - pad * 2, _sv(18))

    def _reposition_toggle(self, *_):
        if hasattr(self, "_toggle"):
            pad = _sv(6)
            self._toggle.pos = (
                self.right - self._toggle.width - pad,
                self.top - self._toggle.height - pad,
            )

    def _reposition_arrow(self, arrow: Label):
        def _do(*_):
            pad = _sv(6)
            arrow.pos = (self.right - arrow.width - pad, self.top - arrow.height - pad)
        return _do

    # -- Toggle logic -------------------------------------------------------

    def _tap_toggle(self):
        if self._mode != "toggle":
            return
        self.set_active(not self._active)
        if self._on_change:
            self._on_change(self._active)

    def _on_toggle(self, val: bool):
        self._active = val
        self._update_visuals()
        if self._on_change:
            self._on_change(val)

    def set_active(self, val: bool):
        self._active = val
        if hasattr(self, "_toggle"):
            self._toggle._active = val
            self._toggle._draw()
        self._update_visuals()

    def _update_visuals(self):
        self._bg_col.rgba = _TILE_ACTIVE_BG if self._active else _TILE_INACTIVE_BG
        icon_col = (1.0, 1.0, 1.0, 0.95) if self._active else (0.65, 0.65, 0.70, 0.90)
        self._icon.set_color(icon_col)
        txt_col = (1.0, 1.0, 1.0, 0.9) if self._active else (0.65, 0.65, 0.70, 0.85)
        self._lbl.color = txt_col


# ---------------------------------------------------------------------------
# _SliderRow — icon + label + Slider + value label
# ---------------------------------------------------------------------------

class _SliderRow(BoxLayout):
    def __init__(self, kind: str, label: str, initial: float = 75,
                 on_change: Optional[Callable[[float], None]] = None, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", _sv(40))
        kwargs.setdefault("spacing", _sh(8))
        super().__init__(**kwargs)

        self._on_change = on_change
        self._debounce = None

        icon_sz = _sv(20)
        self._icon = Icon(
            kind=kind,
            color=(0.80, 0.80, 0.85, 0.9),
            size_hint=(None, None),
            size=(icon_sz, icon_sz),
            pos_hint={"center_y": 0.5},
        )
        self.add_widget(self._icon)

        self.add_widget(_lbl(
            label, _sf(11), COLORS["gray_300"],
            halign="left",
            size_hint=(None, 1),
            width=_sh(68),
        ))

        self._slider = Slider(min=0, max=100, value=initial, size_hint=(1, 1))
        self._slider.bind(value=self._slider_changed)
        self.add_widget(self._slider)

        self._val_lbl = _lbl(
            f"{int(initial)}%", _sf(11), COLORS["gray_400"],
            halign="right",
            size_hint=(None, 1),
            width=_sh(34),
        )
        self.add_widget(self._val_lbl)

    def _slider_changed(self, _, val: float):
        self._val_lbl.text = f"{int(val)}%"
        if self._debounce:
            self._debounce.cancel()
        v = int(val)
        if self._on_change:
            self._debounce = Clock.schedule_once(
                lambda _dt: self._on_change(v), 0.15
            )

    def set_value(self, val: float):
        self._slider.unbind(value=self._slider_changed)
        self._slider.value = val
        self._val_lbl.text = f"{int(val)}%"
        self._slider.bind(value=self._slider_changed)


# ---------------------------------------------------------------------------
# _NetRow / _BtRow / _SectionHeader (expandable list items)
# ---------------------------------------------------------------------------

class _NetRow(ButtonBehavior, BoxLayout):
    def __init__(self, ssid: str, signal: int, secured: bool,
                 connected: bool, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", _sv(38))
        kwargs.setdefault("padding", [_sh(10), _sv(3)])
        kwargs.setdefault("spacing", _sh(6))
        super().__init__(**kwargs)

        # Signal strength: 4 bars using thin rectangles
        sig_widget = _SignalBars(signal, size_hint=(None, 1), width=_sh(22))
        self.add_widget(sig_widget)

        lock_sfx = " [L]" if secured else ""
        conn_sfx = "  Connected" if connected else ""
        col = COLORS["blue"] if connected else COLORS["white"]
        self.add_widget(_lbl(f"{ssid}{lock_sfx}{conn_sfx}", _sf(11), col, size_hint=(1, 1)))

        with self.canvas.before:
            self._bgc = Color(*COLORS["surface"])
            self._bgr = RoundedRectangle(pos=self.pos, size=self.size, radius=[_sv(6)])
        self.bind(pos=self._sb, size=self._sb)
        self.bind(on_press=lambda *_: setattr(self._bgc, "rgba", COLORS["gray_700"]))
        self.bind(on_release=lambda *_: setattr(self._bgc, "rgba", COLORS["surface"]))

    def _sb(self, *_):
        self._bgr.pos = self.pos; self._bgr.size = self.size


class _BtRow(ButtonBehavior, BoxLayout):
    def __init__(self, name: str, mac: str, paired: bool, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", _sv(38))
        kwargs.setdefault("padding", [_sh(10), _sv(3)])
        kwargs.setdefault("spacing", _sh(6))
        super().__init__(**kwargs)

        self.mac = mac
        bt_icon = Icon("bluetooth", color=(0.6, 0.6, 0.9, 0.9),
                       size_hint=(None, 1), width=_sh(16))
        self.add_widget(bt_icon)

        self.add_widget(_lbl(name or mac, _sf(11), COLORS["white"], size_hint=(1, 1)))
        action = "REMOVE" if paired else "PAIR"
        self.add_widget(_lbl(action, _sf(10), COLORS["blue"], halign="right",
                             size_hint=(None, 1), width=_sh(50)))

        with self.canvas.before:
            self._bgc = Color(*COLORS["surface"])
            self._bgr = RoundedRectangle(pos=self.pos, size=self.size, radius=[_sv(6)])
        self.bind(pos=self._sb, size=self._sb)
        self.bind(on_press=lambda *_: setattr(self._bgc, "rgba", COLORS["gray_700"]))
        self.bind(on_release=lambda *_: setattr(self._bgc, "rgba", COLORS["surface"]))

    def _sb(self, *_):
        self._bgr.pos = self.pos; self._bgr.size = self.size


class _SectionHeader(ButtonBehavior, BoxLayout):
    def __init__(self, title: str, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", _sv(34))
        kwargs.setdefault("padding", [_sh(4), _sv(4)])
        kwargs.setdefault("spacing", _sh(4))
        super().__init__(**kwargs)

        lbl = _lbl(title, _sf(11), COLORS["gray_400"], halign="left",
                   bold=True, size_hint=(1, 1))
        self.add_widget(lbl)
        self._arrow = _lbl("+", _sf(13), COLORS["gray_500"], halign="right",
                           size_hint=(None, 1), width=_sh(22))
        self.add_widget(self._arrow)

        with self.canvas.before:
            Color(*COLORS["gray_800"])
            self._line = Rectangle(pos=(self.x, self.y + 1), size=(self.width, 1))
        self.bind(pos=self._sl, size=self._sl)

    def _sl(self, *_):
        self._line.pos = (self.x, self.y + 1)
        self._line.size = (self.width, 1)

    def set_expanded(self, val: bool):
        self._arrow.text = "-" if val else "+"


# ---------------------------------------------------------------------------
# _SignalBars — four small rectangles showing Wi-Fi signal
# ---------------------------------------------------------------------------

class _SignalBars(Widget):
    def __init__(self, signal: int, **kwargs):
        super().__init__(**kwargs)
        self._bars = max(0, min(4, signal // 25))
        self.bind(pos=self._draw, size=self._draw)
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        w, h = self.width, self.height
        bw = max(2, w / 5.5)
        gap = max(1, w / 9)
        total = 4 * bw + 3 * gap
        ox = self.x + (w - total) / 2
        with self.canvas:
            for i in range(4):
                bh = h * (0.30 + 0.18 * i)
                bx = ox + i * (bw + gap)
                by = self.y
                if i < self._bars:
                    Color(0.22, 0.53, 0.98, 0.95)
                else:
                    Color(0.35, 0.35, 0.38, 0.6)
                Rectangle(pos=(bx, by), size=(bw, bh))


# ---------------------------------------------------------------------------
# _FooterBtn
# ---------------------------------------------------------------------------

class _FooterBtn(ButtonBehavior, BoxLayout):
    def __init__(self, kind: str, label: str, danger: bool = False, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("size_hint", (1, 1))
        kwargs.setdefault("padding", [_sh(4), _sv(5)])
        kwargs.setdefault("spacing", _sv(3))
        super().__init__(**kwargs)

        icon_col = (1.0, 0.35, 0.30, 0.95) if danger else (0.85, 0.85, 0.88, 0.9)
        icon_sz = _sv(20)
        self._icon = Icon(kind=kind, color=icon_col,
                          size_hint=(None, None), size=(icon_sz, icon_sz),
                          pos_hint={"center_x": 0.5})
        self.add_widget(self._icon)

        txt_col = (1.0, 0.35, 0.30, 1.0) if danger else COLORS["white"]
        lbl = _lbl(label, _sf(11), txt_col, halign="center")
        lbl.bold = True
        self.add_widget(lbl)

        with self.canvas.before:
            self._bgc = Color(*COLORS["surface_light"])
            self._bgr = RoundedRectangle(pos=self.pos, size=self.size, radius=[_sv(8)])
        self.bind(pos=self._sb, size=self._sb)
        self.bind(on_press=lambda *_: setattr(self._bgc, "rgba", COLORS["gray_700"]))
        self.bind(on_release=lambda *_: setattr(self._bgc, "rgba", COLORS["surface_light"]))

    def _sb(self, *_):
        self._bgr.pos = self.pos; self._bgr.size = self.size


# ---------------------------------------------------------------------------
# QuickPanel
# ---------------------------------------------------------------------------

PANEL_H = min(int(DISPLAY_HEIGHT * 0.68), 410)


class QuickPanel(FloatLayout):
    """Full-screen overlay.  Add to root_layout in main.py.

    Call show() / hide() to animate.
    Set panel.app = <App instance> for Settings navigation.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (1, 1)
        self._visible = False
        self._wifi_expanded = False
        self._bt_expanded = False
        self._vol_debounce = None
        self._bri_debounce = None
        self.app: Optional[object] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build_ui(self):
        with self.canvas.before:
            self._scrim_col = Color(0, 0, 0, 0)
            self._scrim_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_scrim, size=self._sync_scrim)

        self._card = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            height=PANEL_H,
        )
        with self._card.canvas.before:
            Color(0.09, 0.09, 0.11, 0.97)
            self._card_bg = RoundedRectangle(
                pos=self._card.pos, size=self._card.size,
                radius=[0, 0, _sv(16), _sv(16)],
            )
        self._card.bind(
            pos=lambda _, v: setattr(self._card_bg, "pos", v),
            size=lambda _, v: setattr(self._card_bg, "size", v),
        )

        self._card.add_widget(self._make_header())
        self._card.add_widget(self._make_scroll())
        self._card.add_widget(self._make_footer())

        self._card.pos = (0, Window.height)
        self.add_widget(self._card)

    def _sync_scrim(self, *_):
        self._scrim_rect.pos = self.pos
        self._scrim_rect.size = self.size

    # -- Header ----------------------------------------------------------

    def _make_header(self) -> BoxLayout:
        row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=_sv(46),
            padding=[_sh(14), _sv(6), _sh(14), _sv(4)],
            spacing=_sh(8),
        )

        # Battery icon + percent text
        bat_box = BoxLayout(orientation="horizontal",
                            size_hint=(None, 1), width=_sh(80), spacing=_sh(4))
        self._header_bat_icon = Icon(
            "battery", color=(0.75, 0.75, 0.78, 0.9),
            size_hint=(None, None), size=(_sh(24), _sv(12)),
            pos_hint={"center_y": 0.5},
        )
        bat_box.add_widget(self._header_bat_icon)
        self._battery_lbl = _lbl("  --%", _sf(11), COLORS["gray_300"],
                                  halign="left", size_hint=(1, 1))
        bat_box.add_widget(self._battery_lbl)
        row.add_widget(bat_box)

        # WiFi + BT status dots
        self._status_dots = _StatusDots(size_hint=(None, 1), width=_sh(30))
        row.add_widget(self._status_dots)

        # Time (center)
        self._time_lbl = _lbl(
            self._now_str(), _sf(14), COLORS["white"],
            halign="center", bold=True, size_hint=(1, 1),
        )
        row.add_widget(self._time_lbl)

        # Close — icon + label
        close_box = ButtonBehavior.__new__(ButtonBehavior)
        # Use a simple Label styled as a close button
        close_lbl = _lbl("[ X ]", _sf(11), COLORS["gray_400"],
                          halign="right", size_hint=(None, 1), width=_sh(40))
        close_lbl.bind(on_touch_down=lambda w, t: (self.hide(), True)
                       if w.collide_point(*t.pos) else None)
        row.add_widget(close_lbl)

        with row.canvas.after:
            Color(*COLORS["gray_800"])
            self._hdr_line = Rectangle(pos=(row.x, row.y), size=(row.width, 1))
        row.bind(pos=self._sl_hdr, size=self._sl_hdr)
        return row

    def _sl_hdr(self, row, *_):
        self._hdr_line.pos = (row.x, row.y)
        self._hdr_line.size = (row.width, 1)

    # -- Scroll content --------------------------------------------------

    def _make_scroll(self) -> ScrollView:
        scroll = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        inner = BoxLayout(
            orientation="vertical", size_hint_y=None,
            padding=[_sh(10), _sv(6), _sh(10), _sv(4)],
            spacing=_sv(6),
        )
        inner.bind(minimum_height=inner.setter("height"))

        # Sliders
        self._vol_row = _SliderRow(
            "volume", "Volume", initial=75,
            on_change=self._on_vol_change,
            size_hint_y=None, height=_sv(40),
        )
        inner.add_widget(self._vol_row)

        self._bri_row = _SliderRow(
            "brightness", "Bright", initial=80,
            on_change=self._on_bri_change,
            size_hint_y=None, height=_sv(40),
        )
        inner.add_widget(self._bri_row)

        # 2×2 tile grid
        tile_grid = BoxLayout(
            orientation="vertical", size_hint_y=None,
            height=_sv(90) * 2 + _sv(6),
            spacing=_sv(6),
        )
        row1 = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=_sv(90), spacing=_sh(8))
        self._wifi_tile = _QuickTile(
            "wifi", "Wi-Fi", active=True, mode="toggle",
            on_change=self._on_wifi_toggle,
        )
        self._bt_tile = _QuickTile(
            "bluetooth", "Bluetooth", active=False, mode="toggle",
            on_change=self._on_bt_toggle,
        )
        row1.add_widget(self._wifi_tile)
        row1.add_widget(self._bt_tile)
        tile_grid.add_widget(row1)

        row2 = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=_sv(90), spacing=_sh(8))
        self._airplane_tile = _QuickTile(
            "airplane", "Airplane", active=False, mode="toggle",
            on_change=self._on_airplane_toggle,
        )
        settings_tile = _QuickTile(
            "settings", "Settings", active=False, mode="action",
        )
        settings_tile.bind(on_release=lambda *_: self._on_settings_tap())
        row2.add_widget(self._airplane_tile)
        row2.add_widget(settings_tile)
        tile_grid.add_widget(row2)
        inner.add_widget(tile_grid)

        # Wi-Fi section
        self._wifi_sec_hdr = _SectionHeader("Wi-Fi Networks")
        self._wifi_sec_hdr.bind(on_press=lambda *_: self._toggle_wifi_section())
        inner.add_widget(self._wifi_sec_hdr)
        self._wifi_list = BoxLayout(orientation="vertical", size_hint_y=None,
                                    height=0, spacing=_sv(3))
        inner.add_widget(self._wifi_list)

        # BT section
        self._bt_sec_hdr = _SectionHeader("Bluetooth Devices")
        self._bt_sec_hdr.bind(on_press=lambda *_: self._toggle_bt_section())
        inner.add_widget(self._bt_sec_hdr)
        self._bt_list = BoxLayout(orientation="vertical", size_hint_y=None,
                                  height=0, spacing=_sv(3))
        inner.add_widget(self._bt_list)

        scroll.add_widget(inner)
        return scroll

    # -- Footer ----------------------------------------------------------

    def _make_footer(self) -> BoxLayout:
        row = BoxLayout(
            orientation="horizontal", size_hint_y=None,
            height=_sv(58), padding=[_sh(10), _sv(6)], spacing=_sh(8),
        )
        with row.canvas.before:
            Color(*COLORS["gray_800"])
            self._footer_sep = Rectangle(pos=(row.x, row.top - 1), size=(row.width, 1))
        row.bind(pos=self._sl_footer, size=self._sl_footer)

        lock_btn = _FooterBtn("lock", "Lock")
        lock_btn.bind(on_release=lambda *_: self._on_lock())
        row.add_widget(lock_btn)

        restart_btn = _FooterBtn("power", "Restart")
        restart_btn.bind(on_release=lambda *_: self._on_restart())
        row.add_widget(restart_btn)

        power_btn = _FooterBtn("power", "Power Off", danger=True)
        power_btn.bind(on_release=lambda *_: self._on_poweroff())
        row.add_widget(power_btn)
        return row

    def _sl_footer(self, row, *_):
        if hasattr(self, "_footer_sep"):
            self._footer_sep.pos = (row.x, row.top - 1)
            self._footer_sep.size = (row.width, 1)

    # ------------------------------------------------------------------
    # Show / Hide
    # ------------------------------------------------------------------

    def show(self):
        if self._visible:
            return
        self._visible = True
        self._time_lbl.text = self._now_str()
        self._card.pos = (0, Window.height)
        Animation(a=0.55, d=0.20).start(self._scrim_col)
        target_y = max(0, Window.height - PANEL_H)
        Animation(y=target_y, d=0.25, t="out_cubic").start(self._card)
        Clock.schedule_once(lambda _dt: self._refresh(), 0.12)

    def hide(self):
        if not self._visible:
            return
        Animation(a=0, d=0.15).start(self._scrim_col)
        anim = Animation(y=Window.height, d=0.20)
        anim.bind(on_complete=lambda *_: setattr(self, "_visible", False))
        anim.start(self._card)

    # ------------------------------------------------------------------
    # Touch routing
    # ------------------------------------------------------------------

    def on_touch_down(self, touch):
        if not self._visible:
            return False
        if not self._card.collide_point(*touch.pos):
            self.hide()
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        return super().on_touch_move(touch) if self._visible else False

    def on_touch_up(self, touch):
        return super().on_touch_up(touch) if self._visible else False

    # ------------------------------------------------------------------
    # Data refresh (background thread → main thread)
    # ------------------------------------------------------------------

    def _refresh(self):
        threading.Thread(target=self._fetch_status, daemon=True).start()

    def _fetch_status(self):
        batt    = _safe(hardware.get_battery_info,              {"percent": None, "charging": None})
        wifi_on = _safe(wifi_nmcli_local.get_wifi_radio_enabled, None)
        bt_on   = _safe(bluetooth_local.get_power_state,         None)
        vol     = _safe(hardware.get_sink_volume_pct,             None)
        bri     = _safe(hardware.get_brightness_pct,             None)

        def _apply(_dt):
            pct = batt.get("percent")
            chg = batt.get("charging")
            if pct is not None:
                self._battery_lbl.text = f"  {pct}%{'+'  if chg else ''}"
                lv = pct / 100
                self._header_bat_icon.set_level(lv)
                bat_col = (
                    (0.22, 0.80, 0.35, 0.9) if lv > 0.50 else
                    (0.95, 0.65, 0.10, 0.9) if lv > 0.20 else
                    (0.95, 0.25, 0.20, 0.9)
                )
                self._header_bat_icon.set_color(bat_col)
            else:
                self._battery_lbl.text = "  AC"

            self._status_dots.set_states(bool(wifi_on), bool(bt_on))
            self._wifi_tile.set_active(bool(wifi_on))
            self._bt_tile.set_active(bool(bt_on))

            if vol is not None:
                self._vol_row.set_value(vol)
            if bri is not None:
                self._bri_row.set_value(bri)

        Clock.schedule_once(_apply, 0)

    # ------------------------------------------------------------------
    # Slider callbacks
    # ------------------------------------------------------------------

    def _on_vol_change(self, val: int):
        threading.Thread(
            target=lambda: hardware.set_sink_volume_pct(val), daemon=True
        ).start()

    def _on_bri_change(self, val: int):
        threading.Thread(
            target=lambda: hardware.set_brightness_pct(val), daemon=True
        ).start()

    # ------------------------------------------------------------------
    # Tile toggle callbacks
    # ------------------------------------------------------------------

    def _on_wifi_toggle(self, state: bool):
        threading.Thread(
            target=lambda: wifi_nmcli_local.set_wifi_radio(state), daemon=True
        ).start()
        self._status_dots.set_wifi(state)

    def _on_bt_toggle(self, state: bool):
        threading.Thread(
            target=lambda: bluetooth_local.set_power(state), daemon=True
        ).start()
        self._status_dots.set_bt(state)

    def _on_airplane_toggle(self, state: bool):
        cmd = "off" if state else "on"
        threading.Thread(target=lambda: _run_nmcli_radio(cmd), daemon=True).start()
        if state:
            self._wifi_tile.set_active(False)
            self._bt_tile.set_active(False)
            self._status_dots.set_states(False, False)

    def _on_settings_tap(self):
        self.hide()
        if self.app and hasattr(self.app, "screen_manager"):
            self.app.screen_manager.current = "settings"

    # ------------------------------------------------------------------
    # Expandable Wi-Fi section
    # ------------------------------------------------------------------

    def _toggle_wifi_section(self):
        self._wifi_expanded = not self._wifi_expanded
        self._wifi_sec_hdr.set_expanded(self._wifi_expanded)
        if self._wifi_expanded:
            self._wifi_list.clear_widgets()
            self._wifi_list.height = _sv(34)
            self._wifi_list.add_widget(_lbl("Scanning...", _sf(11), COLORS["gray_500"],
                                            size_hint_y=None, height=_sv(34)))
            threading.Thread(target=self._load_wifi, daemon=True).start()
        else:
            self._wifi_list.clear_widgets()
            self._wifi_list.height = 0

    def _load_wifi(self):
        try:
            nets = wifi_nmcli_local.scan_wifi_networks(rescan=False)
        except Exception:
            nets = []

        def _apply(_dt):
            if not self._wifi_expanded:
                return
            self._wifi_list.clear_widgets()
            if not nets:
                self._wifi_list.height = _sv(34)
                self._wifi_list.add_widget(_lbl("No networks found", _sf(11),
                                                COLORS["gray_500"], size_hint_y=None,
                                                height=_sv(34)))
                return
            shown = nets[:5]
            for net in shown:
                row = _NetRow(
                    ssid=net.get("ssid", ""),
                    signal=net.get("signal_strength", 0),
                    secured=(net.get("security", "open") != "open"),
                    connected=net.get("connected", False),
                )
                row.bind(on_release=lambda r, n=net: self._on_wifi_tap(n))
                self._wifi_list.add_widget(row)
            self._wifi_list.height = _sv(38) * len(shown)

        Clock.schedule_once(_apply, 0)

    def _on_wifi_tap(self, net: dict):
        if net.get("connected"):
            return
        ssid = net.get("ssid", "")
        if net.get("security", "open") != "open":
            self._ask_wifi_password(ssid)
        else:
            threading.Thread(
                target=lambda: wifi_nmcli_local.connect_wifi_network(ssid, None),
                daemon=True,
            ).start()

    def _ask_wifi_password(self, ssid: str):
        try:
            from components.text_input_dialog import TextInputDialog
        except ImportError:
            return

        def _connect(pwd: str):
            threading.Thread(
                target=lambda: wifi_nmcli_local.connect_wifi_network(ssid, pwd or None),
                daemon=True,
            ).start()

        dlg = TextInputDialog(
            title=f"Connect: {ssid}",
            message="Enter Wi-Fi password",
            placeholder="Password",
            confirm_text="CONNECT",
            on_confirm=_connect,
        )
        parent = (self.app.root_layout
                  if self.app and hasattr(self.app, "root_layout") else self.parent)
        if parent:
            parent.add_widget(dlg)

    # ------------------------------------------------------------------
    # Expandable Bluetooth section
    # ------------------------------------------------------------------

    def _toggle_bt_section(self):
        self._bt_expanded = not self._bt_expanded
        self._bt_sec_hdr.set_expanded(self._bt_expanded)
        if self._bt_expanded:
            self._bt_list.clear_widgets()
            self._bt_list.height = _sv(34)
            self._bt_list.add_widget(_lbl("Scanning...", _sf(11), COLORS["gray_500"],
                                          size_hint_y=None, height=_sv(34)))
            threading.Thread(target=self._load_bt, daemon=True).start()
        else:
            self._bt_list.clear_widgets()
            self._bt_list.height = 0

    def _load_bt(self):
        try:
            paired = bluetooth_local.list_paired_devices()
        except Exception:
            paired = []
        try:
            nearby = bluetooth_local.scan_and_list_nearby(scan_seconds=5)
        except Exception:
            nearby = []

        paired_macs = {d.get("mac", "").upper() for d in paired}
        all_devs = list(paired)
        seen = set(paired_macs)
        for d in nearby:
            mac = d.get("mac", "").upper()
            if mac and mac not in seen:
                all_devs.append(d)
                seen.add(mac)

        def _apply(_dt):
            if not self._bt_expanded:
                return
            self._bt_list.clear_widgets()
            if not all_devs:
                self._bt_list.height = _sv(34)
                self._bt_list.add_widget(_lbl("No devices found", _sf(11),
                                              COLORS["gray_500"], size_hint_y=None,
                                              height=_sv(34)))
                return
            shown = all_devs[:5]
            for dev in shown:
                mac = dev.get("mac", "")
                row = _BtRow(name=dev.get("name", mac), mac=mac,
                             paired=(mac.upper() in paired_macs))
                is_paired = mac.upper() in paired_macs
                row.bind(on_release=lambda r, d=dev, p=is_paired: self._on_bt_tap(d, p))
                self._bt_list.add_widget(row)
            self._bt_list.height = _sv(38) * len(shown)

        Clock.schedule_once(_apply, 0)

    def _on_bt_tap(self, dev: dict, is_paired: bool):
        mac = dev.get("mac", "")
        if not mac:
            return
        fn = bluetooth_local.remove_device if is_paired else bluetooth_local.pair_device
        threading.Thread(target=lambda: fn(mac), daemon=True).start()

    # ------------------------------------------------------------------
    # Footer callbacks
    # ------------------------------------------------------------------

    def _on_lock(self):
        self.hide()
        if self.app and hasattr(self.app, "screen_manager"):
            Clock.schedule_once(
                lambda _dt: setattr(self.app.screen_manager, "current", "idle"), 0.25
            )

    def _on_restart(self):
        self.hide()
        threading.Thread(target=hardware.request_system_reboot, daemon=True).start()

    def _on_poweroff(self):
        self.hide()
        threading.Thread(target=hardware.request_system_poweroff, daemon=True).start()

    @staticmethod
    def _now_str() -> str:
        return datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# _StatusDots — two small canvas circles (WiFi, BT) for the panel header
# ---------------------------------------------------------------------------

class _StatusDots(Widget):
    _ON  = COLORS["blue"]
    _OFF = COLORS["gray_700"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wifi = False
        self._bt = False
        self.bind(pos=self._draw, size=self._draw)

    def set_states(self, wifi: bool, bt: bool):
        self._wifi = wifi
        self._bt = bt
        self._draw()

    def set_wifi(self, v: bool):
        self._wifi = v
        self._draw()

    def set_bt(self, v: bool):
        self._bt = v
        self._draw()

    def _draw(self, *_):
        self.canvas.clear()
        d = max(5, int(min(self.width / 2.6, self.height * 0.42)))
        cx, cy = self.center_x, self.center_y
        gap = d + 3
        with self.canvas:
            Color(*(self._ON if self._wifi else self._OFF))
            Ellipse(pos=(cx - gap, cy - d / 2), size=(d, d))
            Color(*(self._ON if self._bt else self._OFF))
            Ellipse(pos=(cx + 2, cy - d / 2), size=(d, d))


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _safe(fn: Callable, default):
    try:
        return fn()
    except Exception:
        return default


def _run_nmcli_radio(state: str):
    import shutil as _sh, subprocess as _sp
    exe = _sh.which("nmcli")
    if not exe:
        return
    try:
        r = _sp.run(["nmcli", "radio", "all", state], capture_output=True, timeout=8)
        if r.returncode != 0 and _sh.which("sudo"):
            _sp.run(["sudo", "-n", "/usr/bin/nmcli", "radio", "all", state],
                    capture_output=True, timeout=8)
    except Exception:
        pass
