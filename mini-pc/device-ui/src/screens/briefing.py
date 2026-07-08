"""Tony/Jarvis-style assistant screen for the device UI."""

from __future__ import annotations

import re

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from async_helper import run_async
from components.button import PrimaryButton, SecondaryButton
from config import COLORS, FONT_SIZES, SPACING, display_now
from screens.base_screen import BaseScreen

_BULLET_RE = re.compile(r"^\s*[-•]\s+")

_QUICK_ACTIONS = (
    (
        "Morning Briefing",
        "Calendar, inbox, and meeting memory",
        "Give me my executive morning briefing for today.",
    ),
    (
        "Calendar Focus",
        "What is coming up next",
        "What's on my calendar today and what should I prepare for?",
    ),
    (
        "Inbox Scan",
        "Unread and recent mail attention list",
        "Show urgent unread emails and summarize what needs attention.",
    ),
    (
        "Meeting Memory",
        "Recent follow-ups and decisions",
        "What follow-ups are open from recent meetings?",
    ),
)


def _clean_for_device(text: str) -> str:
    """Keep assistant output readable on a room display; no markdown decoration."""
    t = (text or "").strip()
    if not t:
        return "No assistant response returned."
    t = t.replace("**", "")
    lines = []
    for raw in t.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        line = _BULLET_RE.sub("• ", line)
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out[:5200]


class _AssistantOrb(FloatLayout):
    """Soft pulsing orb: cheap, calm visual state without external assets."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        self._phase = 0.0
        with self.canvas.before:
            self._outer_color = Color(0.22, 0.53, 0.98, 0.18)
            self._outer = Ellipse(pos=self.pos, size=self.size)
            self._ring_color = Color(0.55, 0.78, 1.0, 0.34)
            self._ring = Line(circle=(self.center_x, self.center_y, self.width / 2.3), width=1.4)
            self._inner_color = Color(0.22, 0.53, 0.98, 0.82)
            self._inner = Ellipse(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)
        self._event = Clock.schedule_interval(self._tick, 1 / 24)

    def _sync(self, *_args):
        pad = min(self.width, self.height) * 0.22
        self._outer.pos = self.pos
        self._outer.size = self.size
        self._inner.pos = (self.x + pad, self.y + pad)
        self._inner.size = (max(1, self.width - pad * 2), max(1, self.height - pad * 2))
        self._ring.circle = (self.center_x, self.center_y, max(1, self.width / 2.35))

    def _tick(self, dt):
        self._phase = (self._phase + dt) % 2.4
        pulse = 0.5 + 0.5 * abs(1.2 - self._phase) / 1.2
        self._outer_color.a = 0.11 + pulse * 0.12
        self._ring_color.a = 0.22 + pulse * 0.18


class BriefingScreen(BaseScreen):
    """Executive assistant panel: briefing + calendar/inbox/memory quick actions."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._loading = False
        self._last_prompt = ""
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(
            orientation="vertical",
            padding=[sv(SPACING["screen_padding"]), sv(14), sv(SPACING["screen_padding"]), 0],
            spacing=sv(12),
        )
        self.make_dark_bg(root)

        header = BoxLayout(orientation="horizontal", size_hint=(1, None), height=sv(76), spacing=sv(12))
        orb = _AssistantOrb(size=(sv(58), sv(58)))
        header.add_widget(orb)

        title_col = BoxLayout(orientation="vertical", spacing=sv(2))
        self.kicker = Label(
            text="TONY ASSISTANT",
            font_size=sf(FONT_SIZES["tiny"]),
            color=COLORS["blue"],
            bold=True,
            halign="left",
            valign="bottom",
            size_hint=(1, 0.34),
        )
        self.kicker.bind(size=self.kicker.setter("text_size"))
        title_col.add_widget(self.kicker)
        self.title = Label(
            text="Executive command panel",
            font_size=sf(FONT_SIZES["large"]),
            color=COLORS["white"],
            bold=True,
            halign="left",
            valign="middle",
            size_hint=(1, 0.66),
        )
        self.title.bind(size=self.title.setter("text_size"))
        title_col.add_widget(self.title)
        header.add_widget(title_col)

        back = SecondaryButton(text="Back", size_hint=(None, None), width=sv(106), height=sv(52))
        back.bind(on_release=lambda *_: self.go_back())
        header.add_widget(back)
        root.add_widget(header)

        action_grid = BoxLayout(orientation="vertical", size_hint=(1, None), height=sv(142), spacing=sv(8))
        row1 = BoxLayout(orientation="horizontal", spacing=sv(8))
        row2 = BoxLayout(orientation="horizontal", spacing=sv(8))
        rows = (row1, row2)
        for idx, (title, detail, prompt) in enumerate(_QUICK_ACTIONS):
            btn = SecondaryButton(
                text=f"[b]{title}[/b]\n[size={max(9, sf(FONT_SIZES['tiny']))}]{detail}[/size]",
                markup=True,
                halign="center",
                valign="middle",
                font_size=sf(FONT_SIZES["small"]),
                size_hint=(0.5, 1),
            )
            btn.bind(on_release=lambda _btn, p=prompt, t=title: self.run_assistant(p, t))
            rows[idx // 2].add_widget(btn)
        action_grid.add_widget(row1)
        action_grid.add_widget(row2)
        root.add_widget(action_grid)

        card = BoxLayout(orientation="vertical", padding=[sv(18), sv(16)], spacing=sv(10))
        with card.canvas.before:
            Color(*COLORS["surface"])
            self._card_bg = RoundedRectangle(pos=card.pos, size=card.size, radius=[sv(24)])
        card.bind(pos=lambda w, *_: setattr(self._card_bg, "pos", w.pos))
        card.bind(size=lambda w, *_: setattr(self._card_bg, "size", w.size))

        status_row = BoxLayout(orientation="horizontal", size_hint=(1, None), height=sv(30), spacing=sv(8))
        self.status_label = Label(
            text="Ready · choose a briefing card",
            font_size=sf(FONT_SIZES["small"]),
            color=COLORS["gray_300"],
            halign="left",
            valign="middle",
        )
        self.status_label.bind(size=self.status_label.setter("text_size"))
        status_row.add_widget(self.status_label)
        self.refresh_btn = SecondaryButton(text="Refresh", size_hint=(None, None), width=sv(104), height=sv(30), font_size=sf(FONT_SIZES["tiny"]))
        self.refresh_btn.bind(on_release=lambda *_: self.run_assistant(self._last_prompt or _QUICK_ACTIONS[0][2], "Refresh"))
        status_row.add_widget(self.refresh_btn)
        card.add_widget(status_row)

        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False)
        self.response_label = Label(
            text=(
                "Good day. I can brief your calendar, scan your inbox, and recall recent meeting follow-ups.\n\n"
                "Tap a card above to begin. Writes like email/calendar changes still require approval on web."
            ),
            markup=False,
            font_size=sf(FONT_SIZES["body"]),
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            line_height=1.18,
        )
        self.response_label.bind(
            width=lambda w, width: setattr(w, "text_size", (width, None)),
            texture_size=lambda w, size: setattr(w, "height", size[1]),
        )
        scroll.add_widget(self.response_label)
        card.add_widget(scroll)
        root.add_widget(card)

        bottom = BoxLayout(orientation="horizontal", size_hint=(1, None), height=sv(56), spacing=sv(10))
        meeting_btn = SecondaryButton(text="Meetings", size_hint=(0.34, 1))
        meeting_btn.bind(on_release=lambda *_: self.goto("meetings", transition="slide_left"))
        bottom.add_widget(meeting_btn)
        settings_btn = SecondaryButton(text="Settings", size_hint=(0.34, 1))
        settings_btn.bind(on_release=lambda *_: self.goto("settings", transition="slide_left"))
        bottom.add_widget(settings_btn)
        home_btn = PrimaryButton(text="Home", size_hint=(0.32, 1))
        home_btn.bind(on_release=lambda *_: self.goto("home", transition="slide_right"))
        bottom.add_widget(home_btn)
        root.add_widget(bottom)

        hint = Label(
            text="Assistant cards run real backend actions · writes still need web approval.",
            font_size=sf(FONT_SIZES["tiny"]),
            color=COLORS["gray_500"],
            halign="center",
            valign="middle",
            size_hint=(1, None),
            height=sv(22),
        )
        hint.bind(size=hint.setter("text_size"))
        root.add_widget(hint)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self.title.text = f"Tony · {display_now().strftime('%A')}"

    def run_assistant(self, prompt: str, label: str):
        if self._loading:
            return
        self._loading = True
        self._last_prompt = prompt
        self.refresh_btn.disabled = True
        self.status_label.text = f"Thinking · {label}…"
        self.response_label.text = "Preparing a calm, executive-ready answer…"

        async def _fetch():
            try:
                data = await self.backend.post_assistant_intent(prompt)
                text = _clean_for_device(data.get("assistant_message") or "")
                agent = str(data.get("routed_agent_id") or "assistant").replace("_", " ")
                pending = len(data.get("pending_actions") or [])
                suffix = f" · {pending} approval pending" if pending else ""

                def _apply(_dt):
                    self.status_label.text = f"Ready · {agent}{suffix}"
                    self.response_label.text = text
                    self._loading = False
                    self.refresh_btn.disabled = False

                Clock.schedule_once(_apply, 0)
            except Exception as exc:
                msg = str(exc).strip()[:260] or "Unable to reach the assistant."

                def _error(_dt):
                    self.status_label.text = "Assistant unavailable"
                    self.response_label.text = (
                        "I couldn't reach the assistant service from this device yet.\n\n"
                        f"Reason: {msg}\n\n"
                        "Check that the device is paired, internet is available, and backend URL is reachable."
                    )
                    self._loading = False
                    self.refresh_btn.disabled = False

                Clock.schedule_once(_error, 0)

        run_async(_fetch())
