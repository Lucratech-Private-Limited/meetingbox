"""
Link device — name the appliance and enter a pairing code from the web app
(Settings → Devices, while signed in with Google).
"""

from pathlib import Path

import httpx
from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, COLORS, FONT_SIZES
from screens.base_screen import BaseScreen

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
SCREEN_BG = (0.043, 0.051, 0.067, 1)


def _field_label(text: str) -> Label:
    lb = Label(
        text=text,
        font_size=FONT_SIZES["small"],
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
        font_size=FONT_SIZES["medium"],
        padding=[14, 12],
        background_normal="",
        background_active="",
        background_color=COLORS["surface_light"],
        foreground_color=COLORS["white"],
        hint_text_color=COLORS["gray_600"],
        cursor_color=COLORS["white"],
    )
    defaults.update(kwargs)
    return TextInput(**defaults)


class PairDeviceScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name_input = None
        self._code_input = None
        self._link_btn = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical", padding=[20, 8, 20, 12], spacing=0)
        with root.canvas.before:
            Color(*SCREEN_BG)
            self._root_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._root_bg, "pos", w.pos),
            size=lambda w, *_: setattr(self._root_bg, "size", w.size),
        )

        header = BoxLayout(orientation="horizontal", size_hint=(1, None), height=48, spacing=10)
        if Path(LOGO_PATH).exists():
            header.add_widget(
                Image(source=LOGO_PATH, size_hint=(None, 1), width=36, fit_mode="contain")
            )
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text="MeetingBox",
            font_size=FONT_SIZES["title"],
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
            spacing=6,
            padding=[0, 4, 0, 8],
        )
        body.bind(minimum_height=body.setter("height"))

        title = Label(
            text="Link this device",
            font_size=FONT_SIZES["huge"],
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
                "On your computer, open MeetingBox while signed in with Google.\n"
                "Go to Settings → Devices → Generate code, then enter that code here."
            ),
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_400"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=52,
        )
        sub.bind(size=sub.setter("text_size"))
        body.add_widget(sub)

        body.add_widget(Widget(size_hint=(1, None), height=4))
        body.add_widget(_field_label("Device name"))
        self._name_input = _text_input(hint_text="e.g. Conference Room A")
        body.add_widget(self._name_input)

        body.add_widget(Widget(size_hint=(1, None), height=4))
        body.add_widget(_field_label("Pairing code"))
        self._code_input = _text_input(hint_text="6-digit code from web")
        body.add_widget(self._code_input)

        body.add_widget(Widget(size_hint=(1, None), height=16))

        self._link_btn = PrimaryButton(
            text="Link device",
            size_hint=(1, None),
            height=52,
            font_size=FONT_SIZES["medium"],
        )
        self._link_btn.bind(on_press=self._on_link)
        body.add_widget(self._link_btn)

        body.add_widget(Widget(size_hint=(1, None), height=12))

        scroll.add_widget(body)
        root.add_widget(scroll)

        footer = BoxLayout(orientation="horizontal", size_hint=(1, None), height=48, spacing=10)
        back_btn = SecondaryButton(
            text="Back",
            size_hint=(None, 1),
            width=100,
            font_size=FONT_SIZES["medium"],
        )
        back_btn.bind(on_press=lambda *_: self.go_back())
        footer.add_widget(back_btn)
        footer.add_widget(Widget(size_hint=(1, 1)))
        root.add_widget(footer)

        self.add_widget(root)

    def on_enter(self):
        if self._name_input:
            self._name_input.text = ""
        if self._code_input:
            self._code_input.text = ""

    def _on_link(self, _inst):
        name = (self._name_input.text or "").strip()
        code = (self._code_input.text or "").replace(" ", "").strip()

        if not name:
            self.add_widget(
                ModalDialog(
                    title="Device name",
                    message="Please enter a name for this device.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
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
