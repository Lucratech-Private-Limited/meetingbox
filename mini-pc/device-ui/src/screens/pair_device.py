"""
Link device — pairing code and dashboard QR; device name still comes from setup when claiming.
"""

from io import BytesIO
from pathlib import Path

import httpx
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, COLORS, FONT_SIZES, DASHBOARD_PUBLIC_URL
from screens.base_screen import BaseScreen

try:
    import qrcode
    from kivy.core.image import Image as CoreImage

    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
SCREEN_BG = (0.043, 0.051, 0.067, 1)


def _field_label(text: str) -> Label:
    lb = Label(
        text=text,
        font_size=BaseScreen.suf(FONT_SIZES["small"]),
        bold=True,
        color=COLORS["white"],
        halign="left",
        valign="middle",
        size_hint=(1, None),
        height=22,
    )
    lb.bind(size=lb.setter("text_size"))
    return lb


def _text_input(**kwargs) -> TextInput:
    defaults = dict(
        multiline=False,
        size_hint=(1, None),
        height=48,
        font_size=BaseScreen.suf(FONT_SIZES["medium"]),
        padding=[14, 12],
        background_normal="",
        background_active="",
        background_color=(0.16, 0.21, 0.30, 1),
        foreground_color=COLORS["white"],
        hint_text_color=COLORS["gray_600"],
        cursor_color=COLORS["white"],
    )
    defaults.update(kwargs)
    return TextInput(**defaults)


def _make_qr_image_widget(url: str, px: int = 116):
    """Return a Kivy Image with a QR for ``url``, or a placeholder label."""
    if HAS_QRCODE:
        try:
            qr = qrcode.QRCode(version=1, box_size=3, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="white", back_color="black")
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            core_img = CoreImage(buf, ext="png")
            return Image(
                texture=core_img.texture,
                size=(px, px),
                size_hint=(None, None),
            )
        except Exception:
            pass
    return Label(
        text="[QR]",
        font_size=BaseScreen.suf(FONT_SIZES["small"]),
        color=COLORS["blue"],
        size_hint=(None, None),
        size=(px, px),
    )


class PairDeviceScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._code_input = None
        self._link_btn = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(
            orientation="vertical",
            padding=[24, 12, 24, 16],
            spacing=0,
            size_hint=(1, 1),
        )
        self.make_dark_bg(root)

        header = BoxLayout(orientation="horizontal", size_hint=(1, None), height=56, spacing=12)
        if Path(LOGO_PATH).exists():
            header.add_widget(
                Image(source=LOGO_PATH, size_hint=(None, 1), width=36, fit_mode="contain")
            )
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text="MeetingBox",
            font_size=self.suf(FONT_SIZES["title"]),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint_x=1,
        )
        brand.bind(size=brand.setter("text_size"))
        header.add_widget(brand)
        root.add_widget(header)

        scroll = ScrollView(
            size_hint=(1, 1),
            do_scroll_x=False,
            bar_width=6,
        )
        body = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=8,
            padding=[0, 4, 0, 8],
        )
        body.bind(minimum_height=body.setter("height"))

        title = Label(
            text="Link this MeetingBox",
            font_size=self.suf(FONT_SIZES["huge"]),
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=40,
        )
        title.bind(size=title.setter("text_size"))
        body.add_widget(title)

        sub = Label(
            text=(
                "Sign in on the dashboard (scan the QR code), open Settings → Devices, "
                "and generate a pairing code. Enter it below."
            ),
            font_size=self.suf(FONT_SIZES["small"]),
            color=COLORS["gray_300"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=44,
        )
        sub.bind(size=sub.setter("text_size"))
        body.add_widget(sub)

        body.add_widget(Widget(size_hint=(1, None), height=8))

        qr_caption = Label(
            text="SCAN OR OPEN WEB DASHBOARD",
            font_size=self.suf(FONT_SIZES["small"]),
            bold=True,
            color=COLORS["gray_500"],
            halign="center",
            size_hint=(1, None),
            height=22,
        )
        qr_caption.bind(size=qr_caption.setter("text_size"))
        body.add_widget(qr_caption)

        dash_http = DASHBOARD_PUBLIC_URL
        qr_row = AnchorLayout(size_hint=(1, None), height=124)
        qr_row.add_widget(_make_qr_image_widget(dash_http, 116))
        body.add_widget(qr_row)

        url_lbl = Label(
            text=dash_http,
            font_size=self.suf(FONT_SIZES["tiny"]),
            color=COLORS["gray_600"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=20,
        )
        url_lbl.bind(size=url_lbl.setter("text_size"))
        body.add_widget(url_lbl)

        body.add_widget(Widget(size_hint=(1, None), height=16))
        body.add_widget(_field_label("Pairing code"))
        self._code_input = _text_input(hint_text="6-digit code from web")
        body.add_widget(self._code_input)

        body.add_widget(Widget(size_hint=(1, None), height=16))

        self._link_btn = PrimaryButton(
            text="Link device",
            size_hint=(1, None),
            height=52,
            font_size=self.suf(FONT_SIZES["medium"]),
        )
        self._link_btn.bind(on_press=self._on_link)
        body.add_widget(self._link_btn)

        body.add_widget(Widget(size_hint=(1, None), height=12))

        scroll.add_widget(body)
        root.add_widget(scroll)

        footer = BoxLayout(orientation="horizontal", size_hint=(1, None), height=56, spacing=12)
        back_btn = SecondaryButton(
            text="Back",
            size_hint=(None, 1),
            width=100,
            font_size=self.suf(FONT_SIZES["medium"]),
        )
        back_btn.bind(on_press=lambda *_: self.go_back())
        footer.add_widget(back_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        if self._code_input:
            self._code_input.text = ""

    def _on_link(self, _inst):
        name = (getattr(self.app, "device_name", None) or "").strip()
        if not name:
            self.add_widget(
                ModalDialog(
                    title="Room name",
                    message="Go back in setup and choose a room name first.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        code = (self._code_input.text or "").replace(" ", "").strip()
        if len(code) < 6 or len(code) > 8:
            self.add_widget(
                ModalDialog(
                    title="Pairing code",
                    message="Enter the 6-character code from the web app.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return

        if self._link_btn:
            self._link_btn.disabled = True

        async def _run():
            try:
                data = await self.backend.claim_device(code, device_name=name)
            except httpx.HTTPStatusError as e:
                msg = "Could not link device."
                try:
                    body = e.response.json()
                    d = body.get("detail")
                    if isinstance(d, str):
                        msg = d
                except Exception:
                    pass

                def _show_err(*_a):
                    if self._link_btn:
                        self._link_btn.disabled = False
                    self.add_widget(
                        ModalDialog(
                            title="Could not link",
                            message=msg,
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

                Clock.schedule_once(_show_err, 0)
                return
            except ValueError as e:

                def _show_val(*_a):
                    if self._link_btn:
                        self._link_btn.disabled = False
                    self.add_widget(
                        ModalDialog(
                            title="Could not link",
                            message=str(e) or "Invalid response from server.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

                Clock.schedule_once(_show_val, 0)
                return
            except Exception as e:

                def _show_ex(*_a):
                    if self._link_btn:
                        self._link_btn.disabled = False
                    self.add_widget(
                        ModalDialog(
                            title="Could not link",
                            message=str(e) or "Link failed.",
                            confirm_text="OK",
                            cancel_text="",
                        )
                    )

                Clock.schedule_once(_show_ex, 0)
                return

            dev = data.get("device") or {}
            dname = dev.get("device_name") or name
            self.app.device_name = dname
            self.app.paired_owner_email = (data.get("owner_email") or "").strip()

            def _ok(*_a):
                if self._link_btn:
                    self._link_btn.disabled = False
                self.goto("meetingbox_ready", transition="slide_left")

            Clock.schedule_once(_ok, 0)

        run_async(_run())
