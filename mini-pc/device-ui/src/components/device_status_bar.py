from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget

import hardware
import network_util
from components.live_wifi_icon import LiveWifiIcon


class _BatteryIcon(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._level = 1.0
        with self.canvas:
            Color(0.20, 0.22, 0.23, 0.90)
            self._body = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[2])
            self._nub = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[1])
            self._fill_color = Color(0.22, 0.80, 0.35, 0.95)
            self._fill = RoundedRectangle(pos=(0, 0), size=(1, 1), radius=[1])
        self.bind(pos=self._draw, size=self._draw)

    def set_percent(self, percent: int | float | None) -> None:
        try:
            self._level = max(0.0, min(1.0, float(percent) / 100.0))
        except (TypeError, ValueError):
            self._level = 1.0
        self._draw()

    def _draw(self, *_):
        x, y = self.pos
        w, h = self.size
        if w <= 0 or h <= 0:
            return
        nub_w = max(2.0, w * 0.085)
        body_w = max(1.0, w - nub_w - 1)
        self._body.pos = (x, y)
        self._body.size = (body_w, h)
        self._body.radius = [max(1.0, h * 0.22)]
        nub_h = h * 0.5
        self._nub.pos = (x + body_w + 1, y + (h - nub_h) / 2)
        self._nub.size = (nub_w, nub_h)
        self._nub.radius = [max(1.0, nub_w * 0.3)]
        level = self._level
        self._fill_color.rgba = (
            (0.22, 0.80, 0.35, 0.95) if level > 0.50 else
            (0.95, 0.65, 0.10, 0.95) if level > 0.20 else
            (0.95, 0.25, 0.20, 0.95)
        )
        pad = max(1.5, h * 0.12)
        self._fill.pos = (x + pad, y + pad)
        self._fill.size = (max(0.0, (body_w - 2 * pad) * level), max(0.0, h - 2 * pad))
        self._fill.radius = [max(1.0, (h - 2 * pad) * 0.22)]


class DeviceStatusBar(FloatLayout):
    """Hardware-aware top-right status icons.

    Shows a live WiFi icon (signal-strength aware) for wifi-only devices.
    Shows battery only when the host reports a real battery through
    /sys/class/power_supply.
    """

    def __init__(self, *, debug_location: str = "DeviceStatusBar", **kwargs):
        super().__init__(**kwargs)
        self._debug_location = debug_location
        self._wifi_icon: LiveWifiIcon | None = None
        self._battery_icon: _BatteryIcon | None = None
        self._status_event = None
        # WiFi + battery indicators are not relevant for the desktop app, so the
        # status bar renders nothing and does not poll hardware. Kept as an
        # (empty) widget so the screens that embed it need no changes.

    def refresh(self) -> None:
        threading.Thread(target=self._fetch_status, daemon=True).start()

    def _fetch_status(self) -> None:
        battery = hardware.get_battery_info()
        ethernet_ready = network_util.linux_ethernet_ready()
        wifi_radio = False
        try:
            import wifi_nmcli_local
            wifi_radio = bool(wifi_nmcli_local.get_wifi_radio_enabled())
        except Exception:  # noqa: BLE001
            wifi_radio = False
        Clock.schedule_once(
            lambda _dt: self._apply_status(battery, ethernet_ready, wifi_radio),
            0,
        )

    def _apply_status(self, battery: dict, ethernet_ready: bool, wifi_radio: bool) -> None:
        has_battery = battery.get("percent") is not None
        show_wifi = bool(wifi_radio and not ethernet_ready)
        show_battery = bool(has_battery)
        self.clear_widgets()

        items = []
        if show_wifi:
            wifi = LiveWifiIcon(
                color=(0.0, 0.0, 0.0, 1.0),
                size_hint=(0.32, 0.72),
            )
            items.append(wifi)
        if show_battery:
            batt = _BatteryIcon(size_hint=(0.42, 0.70))
            batt.set_percent(battery.get("percent"))
            items.append(batt)

        gap = 0.10
        total_w = sum(float(w.size_hint_x or 0) for w in items) + gap * max(0, len(items) - 1)
        x = max(0.0, 1.0 - total_w)
        for item in items:
            item.pos_hint = {"x": x, "center_y": 0.5}
            self.add_widget(item)
            x += float(item.size_hint_x or 0) + gap

    def on_parent(self, *_):
        if self.parent is None and self._status_event is not None:
            self._status_event.cancel()
            self._status_event = None
