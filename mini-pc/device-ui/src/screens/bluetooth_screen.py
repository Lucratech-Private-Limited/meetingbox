"""
Bluetooth screen — list nearby/paired devices, toggle power, pair new device.
Runs bluetoothctl directly inside the device-ui container (bluetooth_local.py)
so it talks to the host BlueZ daemon via the mounted D-Bus socket.
"""

import logging

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from components.settings_item import SettingsItem
from components.status_bar import StatusBar
from components.text_input_dialog import TextInputDialog
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen
import bluetooth_local

logger = logging.getLogger(__name__)


class BluetoothScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._scanning = False
        self._paired_macs: set[str] = set()
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        self.status_bar = StatusBar(
            status_text="Bluetooth Devices",
            device_name="Bluetooth Devices",
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        pad = self.suh(SPACING["screen_padding"])
        inner = BoxLayout(
            orientation="vertical",
            padding=[pad, self.suv(8), pad, self.suv(8)],
            spacing=self.suv(8),
        )

        self.status_lbl = Label(
            text="Nearby & paired devices",
            font_size=self.suf(FONT_SIZES["small"]),
            bold=True,
            color=COLORS["gray_500"],
            halign="left",
            size_hint_y=None,
            height=self.suv(24),
        )
        self.status_lbl.bind(size=self.status_lbl.setter("text_size"))
        inner.add_widget(self.status_lbl)

        self.device_grid = GridLayout(cols=1, spacing=self.suv(6), size_hint_y=None)
        self.device_grid.bind(minimum_height=self.device_grid.setter("height"))
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        sc.add_widget(self.device_grid)
        inner.add_widget(sc)

        pair_row = BoxLayout(size_hint=(1, None), height=self.suv(52), spacing=self.suh(10))
        self.pair_btn = PrimaryButton(text="Pair by MAC", size_hint=(1, 1))
        self.pair_btn.bind(on_press=lambda *_: self._show_pair_dialog())
        self.scan_btn = SecondaryButton(text="Scan", size_hint=(None, 1), width=self.suh(100))
        self.scan_btn.bind(on_press=lambda *_: self._start_scan())
        pair_row.add_widget(self.pair_btn)
        pair_row.add_widget(self.scan_btn)
        inner.add_widget(pair_row)

        root.add_widget(inner)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self._load_paired()

    def _load_paired(self):
        """Show currently paired devices immediately (fast, no scan)."""
        def _fetch():
            try:
                paired = bluetooth_local.list_paired_devices()
            except Exception as e:
                logger.warning("list_paired_devices failed: %s", e)
                paired = []

            def _apply(_dt):
                self._paired_macs = {d["mac"] for d in paired}
                self._render_devices(paired, scanning=False)

            Clock.schedule_once(_apply, 0)

        import threading
        threading.Thread(target=_fetch, daemon=True).start()

    def _start_scan(self):
        """Scan ~7 s for nearby devices then refresh the list."""
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.disabled = True
        self.status_lbl.text = "Scanning (7 s)…"

        def _fetch():
            try:
                devices = bluetooth_local.scan_and_list_nearby(scan_seconds=7)
            except Exception as e:
                logger.warning("scan_and_list_nearby failed: %s", e)
                devices = []

            def _apply(_dt):
                self._scanning = False
                self.scan_btn.disabled = False
                # Update paired MAC set from fresh scan results that overlap with known paired
                try:
                    paired = bluetooth_local.list_paired_devices()
                    self._paired_macs = {d["mac"] for d in paired}
                except Exception:
                    pass
                self._render_devices(devices, scanning=False)

            Clock.schedule_once(_apply, 0)

        import threading
        threading.Thread(target=_fetch, daemon=True).start()

    def _render_devices(self, devices: list, scanning: bool):
        self.device_grid.clear_widgets()
        if not devices:
            self.status_lbl.text = "No devices found — tap Scan to search"
            self.device_grid.add_widget(
                Label(
                    text="No Bluetooth devices found.\nTap 'Scan' to search for nearby devices.",
                    color=COLORS["gray_500"],
                    halign="left",
                    size_hint_y=None,
                    height=self.suv(56),
                    font_size=self.suf(FONT_SIZES.get("small", 13)),
                )
            )
        else:
            self.status_lbl.text = f"{len(devices)} device(s) found"
            for dev in devices:
                name = dev.get("name", dev.get("mac", "Unknown"))
                mac = dev.get("mac", "")
                row = SettingsItem(
                    title=name,
                    subtitle=mac,
                    mode="arrow",
                    on_press=lambda _, m=mac, n=name: self._device_options(m, n),
                )
                self.device_grid.add_widget(row)

    def _show_pair_dialog(self):
        self.add_widget(
            TextInputDialog(
                title="Pair device",
                message="Enter the Bluetooth MAC address (e.g. AA:BB:CC:DD:EE:FF).",
                placeholder="AA:BB:CC:DD:EE:FF",
                confirm_text="PAIR",
                on_confirm=self._execute_pair,
            )
        )

    def _execute_pair(self, mac: str):
        mac = (mac or "").strip()
        if not mac:
            return

        def _pair():
            result = bluetooth_local.pair_device(mac)
            ok = result.get("ok", False)
            msg = result.get("message", "")

            def _done(_dt):
                if ok:
                    self._load_paired()
                else:
                    self.add_widget(
                        ModalDialog(
                            title="Pairing failed",
                            message=msg[:400] or "Could not pair device.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

            Clock.schedule_once(_done, 0)

        import threading
        threading.Thread(target=_pair, daemon=True).start()

    def _device_options(self, mac: str, name: str):
        is_paired = mac in self._paired_macs
        if is_paired:
            self.add_widget(
                ModalDialog(
                    title=name,
                    message=f"MAC: {mac}\n\nThis device is paired. Connect to it now?",
                    confirm_text="CONNECT",
                    cancel_text="REMOVE",
                    on_confirm=lambda: self._execute_connect(mac),
                    on_cancel=lambda: self._execute_remove(mac),
                )
            )
        else:
            self.add_widget(
                ModalDialog(
                    title=name,
                    message=f"MAC: {mac}\n\nTap Pair to pair this device.",
                    confirm_text="PAIR",
                    cancel_text="CLOSE",
                    on_confirm=lambda: self._execute_pair(mac),
                )
            )

    def _execute_connect(self, mac: str):
        def _connect():
            result = bluetooth_local.connect_device(mac)
            ok = result.get("ok", False)
            msg = result.get("message", "")

            def _done(_dt):
                if not ok:
                    self.add_widget(
                        ModalDialog(
                            title="Connection failed",
                            message=msg[:400] or "Could not connect to device.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

            Clock.schedule_once(_done, 0)

        import threading
        threading.Thread(target=_connect, daemon=True).start()

    def _execute_remove(self, mac: str):
        def _remove():
            bluetooth_local.remove_device(mac)

            def _done(_dt):
                self._paired_macs.discard(mac)
                self._load_paired()

            Clock.schedule_once(_done, 0)

        import threading
        threading.Thread(target=_remove, daemon=True).start()
