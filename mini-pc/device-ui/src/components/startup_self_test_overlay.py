"""
Full-screen dimmed overlay that runs startup diagnostics and shows results.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton
from config import COLORS, FONT_SIZES, BORDER_RADIUS

logger = logging.getLogger(__name__)


class StartupSelfTestOverlay(FloatLayout):
    """
    Blocking-style overlay over the root layout during boot diagnostics.
    User dismisses with Continue after checks complete.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._app: Any | None = None

        with self.canvas.before:
            Color(*COLORS["overlay"])
            self._overlay_bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(
            pos=lambda w, v: setattr(self._overlay_bg, "pos", w.pos),
            size=lambda w, v: setattr(self._overlay_bg, "size", w.size),
        )

        self._card = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            width=420,
            height=380,
            pos_hint={"center_x": 0.5, "center_y": 0.5},
            padding=16,
            spacing=10,
        )

        with self._card.canvas.before:
            Color(*COLORS["surface"])
            self._card_bg = RoundedRectangle(
                pos=self._card.pos, size=self._card.size, radius=[BORDER_RADIUS]
            )
        self._card.bind(
            pos=lambda w, v: setattr(self._card_bg, "pos", w.pos),
            size=lambda w, v: setattr(self._card_bg, "size", w.size),
        )

        self._title_label = Label(
            text="System check",
            font_size=FONT_SIZES["title"],
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=32,
        )
        self._title_label.bind(size=self._title_label.setter("text_size"))
        self._card.add_widget(self._title_label)

        self._subtitle_label = Label(
            text="Running unobstructed functionality tests…",
            font_size=FONT_SIZES["small"] + 2,
            color=COLORS["gray_400"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=80,
        )
        self._subtitle_label.bind(size=self._subtitle_label.setter("text_size"))
        self._card.add_widget(self._subtitle_label)

        sep = Widget(size_hint=(1, None), height=1)
        with sep.canvas:
            Color(*COLORS["gray_700"])
            _sr = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(
            pos=lambda w, v: setattr(_sr, "pos", w.pos),
            size=lambda w, v: setattr(_sr, "size", w.size),
        )
        self._card.add_widget(sep)

        self._scroll = ScrollView(
            size_hint=(1, 1),
            do_scroll_x=False,
            bar_width=6,
            scroll_type=["bars", "content"],
        )
        self._log_label = Label(
            text="",
            font_size=FONT_SIZES["small"],
            color=COLORS["gray_500"],
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        self._log_label.bind(
            texture_size=lambda *_: setattr(
                self._log_label, "height", self._log_label.texture_size[1] + 12
            )
        )
        self._log_label.bind(width=self._reflow_log_width)
        self._scroll.add_widget(self._log_label)
        self._card.add_widget(self._scroll)

        self._confirm_btn = PrimaryButton(
            text="Please wait…",
            size_hint=(1, None),
            height=52,
            font_size=FONT_SIZES["medium"],
            disabled=True,
            opacity=0.65,
        )
        self._confirm_btn.bind(on_press=self._on_continue)
        self._card.add_widget(self._confirm_btn)

        self.add_widget(self._card)
        self.bind(size=self._sync_geometry)

    def _sync_geometry(self, *_args):
        if not self.width or not self.height:
            return
        self._card.width = min(540, max(340, self.width * 0.9))
        self._card.height = min(460, max(260, self.height * 0.82))
        tw = self._card.width - 32
        if tw > 1:
            self._subtitle_label.text_size = (tw, None)
            self._log_label.text_size = (tw - 12, None)

    def _reflow_log_width(self, instance, width):
        if width and width > 1:
            instance.text_size = (width - 8, None)

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        return True

    def _on_continue(self, *_args):
        if self._confirm_btn.disabled:
            return
        self.dismiss()

    def dismiss(self):
        if self.parent:
            self.parent.remove_widget(self)

    def _safe_ui(self, fn: Callable[[], None]) -> None:
        Clock.schedule_once(lambda *_: fn(), 0)

    def _append_log(self, line: str) -> None:
        def _do():
            self._log_label.text += line + "\n"

        self._safe_ui(_do)

    def _set_status(self, text: str) -> None:
        self._safe_ui(lambda: setattr(self._subtitle_label, "text", text))

    def run(self, app: Any) -> None:
        self._app = app

        async def _runner():
            from startup_self_test import execute_startup_checks

            results = await execute_startup_checks(
                app,
                self._set_status,
                self._append_log,
            )
            failures = sum(1 for r in results if not r.ok)

            def _finish():
                self._subtitle_label.text = (
                    "All checks passed — you can continue."
                    if failures == 0
                    else f"Finished — {failures} check(s) need attention."
                )
                self._confirm_btn.disabled = False
                self._confirm_btn.opacity = 1.0
                self._confirm_btn.text = "Continue"

            self._safe_ui(_finish)

        self._safe_ui(lambda: setattr(self._confirm_btn, "opacity", 0.65))

        fut = run_async(_runner())
        if fut is None:

            def _fatal():
                self._subtitle_label.text = "Diagnostics could not start (async loop)."
                self._confirm_btn.disabled = False
                self._confirm_btn.opacity = 1.0
                self._confirm_btn.text = "Dismiss"

            self._safe_ui(_fatal)
            logger.error("startup self-test: run_async returned None")
