"""
WiFi from home — same Figma “Connect to WiFi” list as onboarding (wifi_setup).

Status bar stays for back navigation; body is shared via wifi_figma_ui.build_figma_wifi_column.
"""

import logging
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

from async_helper import run_async
import wifi_nmcli_local
from network_util import linux_ethernet_ready

from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from components.status_bar import StatusBar
from config import ASSETS_DIR, BORDER_RADIUS, COLORS, DISPLAY_HEIGHT, DISPLAY_WIDTH, FONT_SIZES
from screens.base_screen import BaseScreen
from screens.wifi_figma_ui import (
    FigmaListDivider,
    FigmaWifiNetworkRow,
    build_figma_wifi_column,
    is_open_wifi as _is_open,
)
from screens.wifi_setup import present_wifi_password_flow

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")

# Home path: status bar steals height. Portrait (DISPLAY_HEIGHT > DISPLAY_WIDTH, e.g. 600×1024)
# already uses lower horizontal scale from config — don’t double-shrink.
_HOME_WIFI_FIGMA_LAYOUT_SCALE = (
    1.0 if DISPLAY_HEIGHT > DISPLAY_WIDTH else 0.88
)


class WiFiScreen(BaseScreen):
    """Wi‑Fi settings from home — Figma list UI aligned with WiFiSetupScreen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.networks: list = []
        self._connecting_ssid: Optional[str] = None
        self._scan_anim_event = None
        self._scan_dots = 0
        self._row_widgets: list = []
        self._wifi_connected_ready = False
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text="WiFi",
            device_name="WiFi",
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        refs = build_figma_wifi_column(
            LOGO_PATH, layout_scale=_HOME_WIFI_FIGMA_LAYOUT_SCALE)
        self._wifi_figma_layout_scale = refs["layout_scale"]
        self._scan_status_lbl = refs["scan_status_lbl"]
        self._list = refs["list_grid"]
        self._next_btn = refs["next_btn"]
        self._next_btn.bind(on_press=self._on_footer_next)
        refs["back_btn"].bind(on_press=lambda *_: self.go_back())
        refs["add_link"].bind(on_press=lambda *_: self._show_manual_network_dialog())
        refs["rescan_btn"].bind(on_press=lambda *_: self._load_networks(rescan=True))
        root.add_widget(refs["root"])

        self.add_widget(root)
        self._sync_next_btn()

    def _sync_next_btn(self):
        self._next_btn.disabled = not self._wifi_connected_ready
        self._next_btn.opacity = 1.0 if self._wifi_connected_ready else 0.4

    def _on_footer_next(self, *_):
        if self._wifi_connected_ready:
            self.go_back()

    def on_enter(self):
        self._connecting_ssid = None
        self._wifi_connected_ready = False
        self._sync_next_btn()
        self._load_networks(rescan=True)

    def on_leave(self):
        self._connecting_ssid = None
        self._stop_scan_anim()
        self._cleanup_rows()

    def _stop_scan_anim(self):
        if self._scan_anim_event:
            self._scan_anim_event.cancel()
            self._scan_anim_event = None

    def _start_scan_anim(self):
        self._scan_dots = 0
        self._scan_status_lbl.text = "Scanning."
        self._scan_anim_event = Clock.schedule_interval(self._tick_scan_anim, 0.4)

    def _tick_scan_anim(self, *_):
        self._scan_dots = (self._scan_dots + 1) % 4
        self._scan_status_lbl.text = "Scanning" + "." * max(1, self._scan_dots)

    def _load_networks(self, rescan: bool = False):
        self._start_scan_anim()

        async def _load():
            nets: list = []
            info: dict = {}
            try:
                if wifi_nmcli_local.has_nmcli():
                    nets = wifi_nmcli_local.scan_wifi_networks(rescan=rescan)
            except Exception as e:
                logger.warning("Local WiFi scan failed: %s", e)
            if not nets:
                try:
                    raw = await self.backend.get_wifi_networks()
                    if isinstance(raw, list):
                        nets = raw
                except Exception as e:
                    logger.warning("WiFi scan (backend): %s", e)
            for n in nets:
                if not isinstance(n, dict):
                    continue
                if "signal_strength" not in n and n.get("signal") is not None:
                    try:
                        n["signal_strength"] = int(n["signal"])
                    except (TypeError, ValueError):
                        n["signal_strength"] = 0
            try:
                info = await self.backend.get_system_info()
            except Exception as e:
                logger.debug("system info for WiFi screen: %s", e)
            self.networks = nets

            def _apply(_dt):
                self._apply_networks(nets, info)

            Clock.schedule_once(_apply, 0)

        run_async(_load())

    def _apply_networks(self, nets, info: dict):
        self._stop_scan_anim()
        self.networks = nets or []
        ssid = (info.get("wifi_ssid") or "").strip()
        self._wifi_connected_ready = bool(ssid)
        self._sync_next_btn()
        if ssid:
            self._scan_status_lbl.text = f"Connected · {ssid}"
        elif linux_ethernet_ready():
            self._scan_status_lbl.text = (
                "Wired LAN active — add a network manually or scan after unplugging Ethernet."
            )
        else:
            count = len([n for n in self.networks if n.get("ssid")])
            self._scan_status_lbl.text = (
                f"{count} network{'s' if count != 1 else ''} found"
                if count
                else "No networks found"
            )
        self._populate()

    def _cleanup_rows(self):
        for w in self._row_widgets:
            if hasattr(w, "cleanup"):
                w.cleanup()
        self._row_widgets.clear()

    def _populate(self):
        self._cleanup_rows()
        self._list.clear_widgets()

        def _key(n):
            return (0 if n.get("connected") else 1, -(n.get("signal_strength") or 0))

        first = True
        for net in sorted(self.networks, key=_key):
            if not (net.get("ssid") or "").strip():
                continue
            if not first:
                self._list.add_widget(
                    FigmaListDivider(layout_scale=self._wifi_figma_layout_scale))
            first = False
            row = FigmaWifiNetworkRow(
                net,
                self._connecting_ssid or "",
                self,
                layout_scale=self._wifi_figma_layout_scale,
            )
            row.bind(on_press=lambda inst, n=net: self._on_row_tap(n))
            self._list.add_widget(row)
            self._row_widgets.append(row)

        if not self._list.children:
            hint = wifi_nmcli_local.empty_scan_hint()
            lbl = Label(
                text=hint,
                font_size=self.suf(FONT_SIZES["small"]),
                color=COLORS["gray_500"],
                halign="left",
                valign="top",
                size_hint=(1, None),
                height=self.suv(100),
            )
            lbl.bind(size=lbl.setter("text_size"))
            self._list.add_widget(lbl)

    def _on_row_tap(self, net: dict):
        if self._connecting_ssid:
            return
        ssid = (net.get("ssid") or "").strip()
        if not ssid:
            return
        if net.get("connected"):
            self._wifi_connected_ready = True
            self._sync_next_btn()
            self._populate()
            return
        if _is_open(net.get("security") or ""):
            self._connect_to_network(ssid, None)
        else:
            present_wifi_password_flow(
                self, ssid, lambda pw: self._connect_to_network(ssid, pw))

    def _show_manual_network_dialog(self):
        """Connect by SSID without scanning (works when Ethernet is plugged in)."""
        overlay = FloatLayout()
        with overlay.canvas.before:
            Color(*COLORS["overlay"])
            ov = Rectangle(pos=overlay.pos, size=overlay.size)
        overlay.bind(
            pos=lambda w, *_: setattr(ov, "pos", w.pos),
            size=lambda w, *_: setattr(ov, "size", w.size),
        )

        card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(self.suh(420), self.suv(300)),
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=self.suh(16),
            spacing=self.suv(8),
        )
        with card.canvas.before:
            Color(*COLORS["surface"])
            cbg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
        card.bind(
            pos=lambda w, *_: setattr(cbg, "pos", w.pos),
            size=lambda w, *_: setattr(cbg, "size", w.size),
        )

        card.add_widget(
            Label(
                text="Add Wi‑Fi network",
                font_size=self.suf(FONT_SIZES["title"]),
                bold=True,
                color=COLORS["white"],
                halign="left",
                size_hint=(1, None),
                height=self.suv(26),
            )
        )

        ssid_in = TextInput(
            hint_text="Network name (SSID)",
            multiline=False,
            font_size=self.suf(FONT_SIZES["body"]),
            size_hint=(1, None),
            height=self.suv(40),
            background_color=COLORS["surface_light"],
            foreground_color=COLORS["white"],
            hint_text_color=COLORS["gray_600"],
            cursor_color=COLORS["white"],
        )
        card.add_widget(ssid_in)

        spin = Spinner(
            text="WPA2 Personal",
            values=("Open", "WPA2 Personal", "WPA3 Personal"),
            size_hint=(1, None),
            height=self.suv(40),
            background_color=COLORS["gray_800"],
            color=COLORS["white"],
        )
        card.add_widget(spin)

        pwd_in = TextInput(
            hint_text="Password (if required)",
            password=True,
            multiline=False,
            font_size=self.suf(FONT_SIZES["body"]),
            size_hint=(1, None),
            height=self.suv(40),
            background_color=COLORS["surface_light"],
            foreground_color=COLORS["white"],
            hint_text_color=COLORS["gray_600"],
            cursor_color=COLORS["white"],
        )
        card.add_widget(pwd_in)

        def on_sec(spinner, txt):
            pwd_in.disabled = txt == "Open"
            pwd_in.opacity = 0.5 if pwd_in.disabled else 1.0

        spin.bind(text=on_sec)
        on_sec(spin, spin.text)

        row = BoxLayout(size_hint=(1, None), height=self.suv(48), spacing=self.suh(10))

        def dismiss(*_a):
            if overlay.parent:
                overlay.parent.remove_widget(overlay)

        def do_add(*_a):
            name = ssid_in.text.strip()
            if not name:
                self.add_widget(
                    ModalDialog(
                        title="SSID required",
                        message="Enter the Wi‑Fi network name.",
                        confirm_text="OK",
                        cancel_text="",
                    )
                )
                return
            sec = spin.text
            if sec != "Open":
                if not pwd_in.text.strip():
                    self.add_widget(
                        ModalDialog(
                            title="Password required",
                            message="Enter the password or choose Open.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )
                    return
            dismiss()
            if sec == "Open":
                self._connect_to_network(name, None)
            else:
                self._connect_to_network(name, pwd_in.text.strip())

        cancel = SecondaryButton(text="Cancel", size_hint=(0.5, 1))
        go = PrimaryButton(text="Connect", size_hint=(0.5, 1))
        cancel.bind(on_press=dismiss)
        go.bind(on_press=do_add)
        row.add_widget(cancel)
        row.add_widget(go)
        card.add_widget(row)

        overlay.add_widget(card)
        self.add_widget(overlay)

    def _connect_to_network(self, ssid, password=None):
        self._connecting_ssid = ssid
        self._scan_status_lbl.text = f"Connecting to {ssid}…"
        self._populate()

        async def _run():
            result: dict = {"status": "failed", "message": ""}
            try:
                if wifi_nmcli_local.has_nmcli():
                    result = wifi_nmcli_local.connect_wifi_network(ssid, password)
                if result.get("status") != "connected":
                    try:
                        result = await self.backend.connect_wifi(
                            ssid, password=password
                        )
                    except Exception as be:
                        logger.warning("WiFi connect (backend): %s", be)
                        result = {"status": "failed", "message": str(be)[:200]}
            except Exception as e:
                logger.warning("WiFi connect: %s", e)
                result = {"status": "failed", "message": str(e)[:200]}

            def _done(*_):
                self._connecting_ssid = None
                if result.get("status") == "connected":
                    self._wifi_connected_ready = True
                    self._sync_next_btn()
                    self._scan_status_lbl.text = f"Connected to {ssid}"
                    self._load_networks(rescan=False)
                else:
                    self._scan_status_lbl.text = "Connection failed"
                    self._populate()
                    msg = (result.get("message") or "").strip() or (
                        "Could not connect. Check the password and NetworkManager "
                        "permissions on this device."
                    )
                    self.add_widget(
                        ModalDialog(
                            title="Could not connect",
                            message=msg[:400],
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

            Clock.schedule_once(_done, 0)

        run_async(_run())
