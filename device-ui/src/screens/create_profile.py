"""
Create profile — onboarding after Wi‑Fi connected (Frame 13 ref).

User ID + display name + password + confirm password; profiles saved locally
for multiple users on one device.
"""

import re
from pathlib import Path

from kivy.graphics import Color, Ellipse, Rectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import ASSETS_DIR, COLORS, FONT_SIZES
from profile_store import add_profile, display_initials
from screens.base_screen import BaseScreen

WELCOME_DIR = ASSETS_DIR / "welcome"
LOGO_PATH = str(WELCOME_DIR / "LOGO.png")
SCREEN_BG = (0.043, 0.051, 0.067, 1)

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{2,64}$")


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


class CreateProfileScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._initials_label = None
        self._name_input = None
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
            text="Create your profile",
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
                "Receive meeting summaries after each session.\n"
                "Your name will be visible to participants."
            ),
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_400"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=44,
        )
        sub.bind(size=sub.setter("text_size"))
        body.add_widget(sub)

        body.add_widget(Widget(size_hint=(1, None), height=4))

        avatar_wrap = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            height=100,
            padding=[0, 4, 0, 4],
        )
        avatar_row = BoxLayout(orientation="horizontal", size_hint=(1, 1))
        avatar_row.add_widget(Widget())
        av = BoxLayout(size_hint=(None, None), size=(88, 88))
        with av.canvas.before:
            Color(*COLORS["primary_start"])
            self._av_ellipse = Ellipse(pos=av.pos, size=av.size)
        av.bind(
            pos=lambda w, *_: setattr(self._av_ellipse, "pos", w.pos),
            size=lambda w, *_: setattr(self._av_ellipse, "size", w.size),
        )
        self._initials_label = Label(
            text="?",
            font_size=28,
            bold=True,
            color=COLORS["white"],
            halign="center",
            valign="middle",
            size_hint=(1, 1),
        )
        av.add_widget(self._initials_label)
        avatar_row.add_widget(av)
        avatar_row.add_widget(Widget())
        avatar_wrap.add_widget(avatar_row)
        body.add_widget(avatar_wrap)

        body.add_widget(_field_label("User ID"))
        self._user_id_input = _text_input(hint_text="e.g. trilok.r")
        body.add_widget(self._user_id_input)

        body.add_widget(Widget(size_hint=(1, None), height=4))
        body.add_widget(_field_label("Enter your name"))
        self._name_input = _text_input(hint_text="e.g. Trilok Ratan")
        self._name_input.bind(text=self._on_name_changed)
        body.add_widget(self._name_input)

        body.add_widget(Widget(size_hint=(1, None), height=4))
        body.add_widget(_field_label("Enter a password"))
        self._pw_input = _text_input(hint_text="••••••••", password=True)
        body.add_widget(self._pw_input)

        body.add_widget(Widget(size_hint=(1, None), height=4))
        body.add_widget(_field_label("Confirm password"))
        self._pw2_input = _text_input(hint_text="••••••••", password=True)
        body.add_widget(self._pw2_input)

        body.add_widget(Widget(size_hint=(1, None), height=16))

        create_btn = PrimaryButton(
            text="Create profile",
            size_hint=(1, None),
            height=52,
            font_size=FONT_SIZES["medium"],
        )
        create_btn.bind(on_press=self._on_create)
        body.add_widget(create_btn)

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

    def _on_name_changed(self, _inst, text):
        if self._initials_label:
            self._initials_label.text = display_initials(text or "")

    def on_enter(self):
        if self._user_id_input:
            self._user_id_input.text = ""
        if self._name_input:
            self._name_input.text = ""
        if self._pw_input:
            self._pw_input.text = ""
        if self._pw2_input:
            self._pw2_input.text = ""
        if self._initials_label:
            self._initials_label.text = "?"

    def _on_create(self, _inst):
        uid = (self._user_id_input.text or "").strip()
        name = (self._name_input.text or "").strip()
        pw = self._pw_input.text or ""
        pw2 = self._pw2_input.text or ""

        if not _USER_ID_RE.match(uid):
            self.add_widget(
                ModalDialog(
                    title="User ID",
                    message="Use 2–64 letters, numbers, or . _ - only.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        if not name:
            self.add_widget(
                ModalDialog(
                    title="Name",
                    message="Please enter your name.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        if len(pw) < 6:
            self.add_widget(
                ModalDialog(
                    title="Password",
                    message="Password must be at least 6 characters.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        if pw != pw2:
            self.add_widget(
                ModalDialog(
                    title="Passwords do not match",
                    message="Re-enter the same password in both fields.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return

        ok, err = add_profile(uid, name, pw)
        if not ok:
            self.add_widget(
                ModalDialog(
                    title="Could not save profile",
                    message=err or "Try again.",
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return

        self.app.current_user_id = uid
        self.app.current_display_name = name
        self.goto("meetingbox_ready", transition="slide_left")
