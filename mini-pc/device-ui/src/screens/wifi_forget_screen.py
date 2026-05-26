"""Forget saved Wi-Fi profiles (local NetworkManager)."""

from kivy.clock import Clock
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView

from async_helper import run_async
from components.modal_dialog import ModalDialog
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES
from kivy.uix.behaviors import ButtonBehavior
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from screens.base_screen import BaseScreen
import wifi_nmcli_local


class _DelRow(ButtonBehavior, BoxLayout):
    def __init__(self, name: str, on_delete, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", 48)
        super().__init__(**kwargs)
        self.name = name
        with self.canvas.before:
            Color(*COLORS["surface"])
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[8])
        self.bind(pos=lambda *a: self._sync_bg(), size=lambda *a: self._sync_bg())
        lbl = Label(text=f"{name}\nTap to remove saved profile", font_size=13, color=COLORS["white"], halign="left")
        lbl.bind(size=lbl.setter("text_size"))
        self.add_widget(lbl)
        self._on_delete = on_delete
        self.bind(on_press=self._tap)

    def _sync_bg(self):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _tap(self, *a):
        self._on_delete(self.name)


class WiFiForgetScreen(BaseScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation="vertical")
        self.make_dark_bg(root)
        root.add_widget(
            StatusBar(
                status_text="Saved Wi-Fi",
                device_name="Saved Wi-Fi",
                back_button=True,
                on_back=self.go_back,
                show_settings=False,
            )
        )
        self.box = GridLayout(cols=1, spacing=8, size_hint_y=None, padding=[8, 8])
        self.box.bind(minimum_height=self.box.setter("height"))
        sc = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        sc.add_widget(self.box)
        root.add_widget(sc)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self.box.clear_widgets()
        names = wifi_nmcli_local.list_saved_wifi_connection_names()
        if not names:
            self.box.add_widget(
                Label(
                    text="No saved Wi‑Fi profiles.",
                    color=COLORS["gray_500"],
                    size_hint_y=None,
                    height=self.suv(40),
                )
            )
            return
        for n in names:
            self.box.add_widget(_DelRow(n, lambda nn=n: self._confirm_delete(nn)))

    def _confirm_delete(self, name: str):
        self.add_widget(
            ModalDialog(
                title="Remove network?",
                message=f"Delete saved profile:\n{name}?",
                confirm_text="DELETE",
                cancel_text="CANCEL",
                danger=True,
                on_confirm=lambda: self._execute_delete(name),
            )
        )

    def _execute_delete(self, name: str):
        res = wifi_nmcli_local.forget_wifi_connection(name)
        if not res.get("ok"):
            self.add_widget(
                ModalDialog(
                    title="Could not remove",
                    message=res.get("message", "Unknown error")[:400],
                    confirm_text="OK",
                    cancel_text="",
                )
            )
            return
        Clock.schedule_once(lambda *_: self.on_enter(), 0.1)

