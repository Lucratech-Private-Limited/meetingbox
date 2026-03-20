"""
WiFi Setup Screen – Two-column layout

Left  : Step-by-step instructions + waiting indicator
Right : QR code pointing to http://192.168.4.1

The actual WiFi hotspot is managed by the host-side onboard service
(scripts/hotspot.sh + scripts/onboard_server.py). This screen reads the
SSID from the hotspot status file or falls back to a generated name.

Auto-advances to 'all_set' when the .setup_complete marker appears
(handled by the global _global_setup_check in main.py).
"""

import subprocess
from pathlib import Path

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.image import Image
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from config import (COLORS, FONT_SIZES, SPACING,
                    HOTSPOT_SSID_PREFIX, HOTSPOT_IP, SETUP_URL)

try:
    import qrcode
    from io import BytesIO
    from kivy.core.image import Image as CoreImage
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def _get_hotspot_ssid() -> str:
    """Read the active hotspot SSID from the system."""
    # Try multiple possible locations for the hotspot script
    for script_path in ["/opt/meetingbox/scripts/hotspot.sh",
                        "/home/meetingbox/meetingbox/scripts/hotspot.sh"]:
        try:
            result = subprocess.run(
                ["bash", script_path, "status"],
                capture_output=True, text=True, timeout=5,
            )
            parts = result.stdout.strip().split("|")
            if parts[0] == "active" and len(parts) >= 2:
                return parts[1]
        except Exception:
            continue

    # Fallback: derive from MAC address
    for iface in ["wlan0", "wlp1s0", "wlp2s0"]:
        try:
            mac = Path(f"/sys/class/net/{iface}/address").read_text().strip()
            suffix = mac.replace(":", "")[-4:].upper()
            return f"{HOTSPOT_SSID_PREFIX}{suffix}"
        except Exception:
            continue

    return f"{HOTSPOT_SSID_PREFIX}Setup"


class WiFiSetupScreen(BaseScreen):
    """WiFi setup screen shown during first-time configuration."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ssid_label = None
        self._dot_index = 0
        self._dot_event = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(
            orientation='horizontal',
            padding=SPACING['screen_padding'],
            spacing=SPACING['section_spacing'],
        )
        self.make_dark_bg(root)

        # --- LEFT column ---
        left = BoxLayout(
            orientation='vertical',
            size_hint=(0.55, 1),
            spacing=8,
        )

        s1h = Label(
            text='1. Connect to WiFi:',
            font_size=FONT_SIZES['body'],
            color=COLORS['white'],
            halign='left', valign='bottom',
            size_hint=(1, None), height=24,
        )
        s1h.bind(size=s1h.setter('text_size'))
        left.add_widget(s1h)

        ssid = _get_hotspot_ssid()
        self.ssid_label = Label(
            text=f'   {ssid}',
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['white'],
            halign='left', valign='top',
            size_hint=(1, None), height=22,
        )
        self.ssid_label.bind(size=self.ssid_label.setter('text_size'))
        left.add_widget(self.ssid_label)

        hint = Label(
            text='   (on your phone or laptop)',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='left',
            size_hint=(1, None), height=18,
        )
        hint.bind(size=hint.setter('text_size'))
        left.add_widget(hint)

        left.add_widget(Widget(size_hint=(1, None), height=8))

        s2h = Label(
            text='2. Open in browser:',
            font_size=FONT_SIZES['body'],
            color=COLORS['white'],
            halign='left', valign='bottom',
            size_hint=(1, None), height=24,
        )
        s2h.bind(size=s2h.setter('text_size'))
        left.add_widget(s2h)

        url_label = Label(
            text=f'   {SETUP_URL}',
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['blue'],
            halign='left',
            size_hint=(1, None), height=22,
        )
        url_label.bind(size=url_label.setter('text_size'))
        left.add_widget(url_label)

        s3h = Label(
            text='3. Select your WiFi network',
            font_size=FONT_SIZES['body'],
            color=COLORS['white'],
            halign='left', valign='bottom',
            size_hint=(1, None), height=24,
        )
        s3h.bind(size=s3h.setter('text_size'))
        left.add_widget(s3h)

        left.add_widget(Widget(size_hint=(1, 0.3)))

        # Waiting indicator (replaces the old "I'M CONNECTED" button)
        self.waiting_label = Label(
            text='Waiting for WiFi configuration...',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='left',
            size_hint=(1, None), height=20,
        )
        self.waiting_label.bind(size=self.waiting_label.setter('text_size'))
        left.add_widget(self.waiting_label)

        self.dots_label = Label(
            text='',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
            halign='left',
            size_hint=(1, None), height=16,
        )
        self.dots_label.bind(size=self.dots_label.setter('text_size'))
        left.add_widget(self.dots_label)

        left.add_widget(Widget(size_hint=(1, None), height=8))

        root.add_widget(left)

        # --- RIGHT column: QR code ---
        right = BoxLayout(
            orientation='vertical',
            size_hint=(0.45, 1),
        )

        qr_widget = self._generate_qr(SETUP_URL)
        right.add_widget(qr_widget)
        root.add_widget(right)

        self.add_widget(root)

    def on_enter(self):
        ssid = _get_hotspot_ssid()
        if self.ssid_label:
            self.ssid_label.text = f'   {ssid}'
        self._dot_index = 0
        self._dot_event = Clock.schedule_interval(self._animate_dots, 0.6)

    def on_leave(self):
        if self._dot_event:
            self._dot_event.cancel()
            self._dot_event = None

    def _animate_dots(self, _dt):
        self._dot_index = (self._dot_index + 1) % 4
        dots = '.' * self._dot_index
        self.dots_label.text = f'   {dots}'

    def _generate_qr(self, url: str):
        """Generate QR code image widget."""
        if HAS_QRCODE:
            try:
                qr = qrcode.QRCode(version=1, box_size=6, border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color='white', back_color='black')
                buf = BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                core_img = CoreImage(buf, ext='png')
                return Image(texture=core_img.texture, size_hint=(1, 1))
            except Exception:
                pass
        lbl = Label(
            text='[QR CODE]',
            font_size=FONT_SIZES['large'],
            color=COLORS['gray_500'],
        )
        return lbl
