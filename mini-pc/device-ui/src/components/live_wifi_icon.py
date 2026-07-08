"""Live canvas WiFi icon that reflects current signal strength.

Draws three concentric arcs + dot (same geometry as the static _WifiIcon
used across voice/task screens).  Arc opacity follows signal level:

    arc3 (outermost)  full opacity when signal >= 66 %
    arc2 (middle)     full opacity when signal >= 33 %
    arc1 (innermost)  full opacity when signal > 0 and connected
    dot               full opacity when connected, dimmed otherwise

A background thread polls wifi_nmcli_local.get_current_wifi_signal()
every POLL_INTERVAL seconds so the icon stays current without blocking
the UI thread.

Usage::

    from components.live_wifi_icon import LiveWifiIcon

    icon = LiveWifiIcon(
        color=(0, 0, 0, 1),          # black; default
        size_hint=(0.023, 0.025),
        pos_hint={"x": 0.89, "y": 0.94},
    )
    root.add_widget(icon)
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line
from kivy.uix.widget import Widget

_POLL_INTERVAL = 10.0   # seconds between background signal polls
_DIM_ALPHA     = 0.20   # opacity for unlit / inactive arcs


class LiveWifiIcon(Widget):
    """Canvas WiFi icon that dims arcs to reflect real signal strength."""

    def __init__(self, *, color: tuple = (0.0, 0.0, 0.0, 1.0), **kw):
        super().__init__(**kw)
        self._signal: int | None = None   # 0-100, or None = not connected
        r, g, b = color[0], color[1], color[2]
        self._rgb = (r, g, b)

        with self.canvas:
            self._c1   = Color(r, g, b, _DIM_ALPHA)
            self._arc1 = Line(width=1.4)        # innermost
            self._c2   = Color(r, g, b, _DIM_ALPHA)
            self._arc2 = Line(width=1.4)
            self._c3   = Color(r, g, b, _DIM_ALPHA)
            self._arc3 = Line(width=1.4)        # outermost
            self._cdot = Color(r, g, b, _DIM_ALPHA)
            self._dot  = Ellipse()

        self.bind(pos=self._redraw, size=self._redraw)
        Clock.schedule_once(self._redraw, 0)

        # Kick off first poll immediately, then repeat
        Clock.schedule_once(lambda _dt: self._poll(), 0)
        self._ev = Clock.schedule_interval(lambda _dt: self._poll(), _POLL_INTERVAL)

    # ── signal polling ────────────────────────────────────────────────────────

    def _poll(self) -> None:
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        # Wired LAN takes precedence: hide the WiFi icon entirely when ethernet
        # is up so the indicator never misrepresents the active connection.
        ethernet = False
        try:
            import network_util  # noqa: PLC0415
            ethernet = bool(network_util.linux_ethernet_ready())
        except Exception:  # noqa: BLE001
            ethernet = False

        signal: int | None = None
        if not ethernet:
            try:
                import wifi_nmcli_local  # noqa: PLC0415
                signal = wifi_nmcli_local.get_current_wifi_signal()
            except Exception:  # noqa: BLE001
                pass
        Clock.schedule_once(lambda _dt: self._on_signal(signal, ethernet), 0)

    def _on_signal(self, signal: int | None, ethernet: bool = False) -> None:
        self._signal = signal
        self.opacity = 0.0 if ethernet else 1.0
        self._redraw()

    # ── drawing ───────────────────────────────────────────────────────────────

    def _lit_count(self) -> int:
        """Number of arcs (0-3) to display at full brightness."""
        s = self._signal
        if s is None:
            return 0
        if s >= 66:
            return 3
        if s >= 33:
            return 2
        if s > 0:
            return 1
        return 0

    def _redraw(self, *_) -> None:
        w, h = self.size
        if w <= 1 or h <= 1:
            return

        # Arc pivot: bottom-centre of the icon bounding box
        cx = self.x + w / 2
        cy = self.y + h * 0.08

        lit   = self._lit_count()
        r0, g0, b0 = self._rgb

        arcs = [
            (self._c1, self._arc1, 0.30),   # i=0 innermost — lit when signal > 0
            (self._c2, self._arc2, 0.58),   # i=1 middle     — lit when signal >= 33
            (self._c3, self._arc3, 0.86),   # i=2 outermost  — lit when signal >= 66
        ]
        for i, (col, arc, frac) in enumerate(arcs):
            r = h * frac
            # Kivy ellipse angles: 0° = top, increasing clockwise. A -45°→45°
            # sweep arches over the top of the dot = upright WiFi fan (∩ shape).
            arc.ellipse = (cx - r, cy - r, 2 * r, 2 * r, -45, 45)
            col.rgba = (r0, g0, b0, 1.0 if i < lit else _DIM_ALPHA)

        # Dot: bright when connected, dimmed when offline
        dot_alpha = 1.0 if (self._signal is not None and self._signal > 0) else _DIM_ALPHA
        self._cdot.rgba = (r0, g0, b0, dot_alpha)
        dr = h * 0.09
        self._dot.pos  = (cx - dr, cy - dr)
        self._dot.size = (dr * 2, dr * 2)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_parent(self, *_) -> None:
        """Cancel polling timer when the widget is removed from the tree."""
        if self.parent is None and self._ev is not None:
            self._ev.cancel()
            self._ev = None
