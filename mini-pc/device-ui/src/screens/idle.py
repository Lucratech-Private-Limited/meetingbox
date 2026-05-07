"""Idle screen — Figma `338:60` (yJqcY4KovVjJ11vjysW533).

Shown after the configurable inactivity period (default 30 s) when the device
is on home/non-recording screens. Tapping anywhere returns to home; the Start
Recording card on the lower-right starts a new meeting in place. Time, date,
greeting, weather and "next up" all refresh live so the screen never looks
stale.

Layout: full-screen background; foreground is a vertical flex column (top band,
stretch center, bottom band). Top band is a horizontal row (datetime | gap |
weather). Bottom band is a horizontal row (schedule | gap | recording card) so
the two bottom regions never overlap.
"""

from __future__ import annotations

import logging
from datetime import datetime

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from async_helper import run_async
from config import (
    ASSETS_DIR,
    COLORS,
    display_now,
    home_layout_horizontal_scale,
    home_layout_vertical_scale,
    to_display_local,
)
from screens.base_screen import BaseScreen
from weather_client import get_weather_client

logger = logging.getLogger(__name__)

_IDLE_DIR = ASSETS_DIR / "idle"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Figma baseline 1024×600 (used for margin constants above).
_IDLE_PAD_LEFT = 46
_IDLE_PAD_TOP = 35
_IDLE_PAD_RIGHT = 48
_IDLE_PAD_BOTTOM = 43
# Maximum width for left-column blocks so content stays readable.
_ZONE_TOP_LEFT_W = 480
# Schedule column ≈ 38% of 1024 design width; keeps center clear for background.
_ZONE_SCHEDULE_W = 390


def _idle_uniform_scale() -> float:
    """Single scale for both axes so proportions match Figma on real panels.

    ``home_layout_*`` intentionally uses different width vs height ratios when the
    physical panel aspect ratio differs from 1024×600. On the idle float layout that
    made boxes, glyphs, and icon squares subtly mis-sized relative to ``pos_hint``,
    which reads as “nothing lines up” on kiosk hardware.
    """
    return min(home_layout_horizontal_scale(), home_layout_vertical_scale())


def _hf(fs):
    """Font size scaled with the same idle uniform factor as lengths."""
    return max(6, int(round(float(fs) * _idle_uniform_scale())))


def _idu(px):
    """Design px → physical px using idle uniform scale (width and height agree)."""
    return max(1, int(round(float(px) * _idle_uniform_scale())))


def _hh(px):  # kept for readability at call sites migrating from split scales
    return _idu(px)


def _hv(px):
    return _idu(px)


def _idle_png(name: str) -> str:
    p = _IDLE_DIR / name
    return str(p) if p.is_file() else ""


def _greeting(name: str | None) -> str:
    hour = display_now().hour
    if hour < 12:
        head = "Good morning"
    elif hour < 17:
        head = "Good afternoon"
    else:
        head = "Good evening"
    nm = (name or "").strip()
    if nm:
        first = nm.split()[0]
        return f"{head}, {first}"
    return head


def _format_meeting_line(next_meeting: dict | None) -> tuple[str, str, int]:
    """Return ``(time_str, title_str, more_count)`` for the Next Up block.

    ``more_count`` is reserved for future use — backend ``get_home_summary``
    only ships pending action counts today, so we surface the upcoming title
    and time and leave "+N more" to mirror today's outstanding actions.
    """
    if not next_meeting:
        return ("--:-- --", "No meetings today", 0)
    title = (next_meeting.get("title") or "Calendar event").strip() or "Calendar event"
    start = (next_meeting.get("start") or "").strip()
    if not start:
        return ("Time not set", title, 0)
    try:
        if "T" in start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            time_str = to_display_local(dt).strftime("%I:%M %p").lstrip("0")
        else:
            d = datetime.strptime(start[:10], "%Y-%m-%d")
            time_str = d.strftime("%b %d · all day")
    except (TypeError, ValueError):
        time_str = start
    return (time_str, title, 0)


# ---------------------------------------------------------------------------
# Start Recording card (image-driven, tappable)
# ---------------------------------------------------------------------------

class _StartRecordingCard(ButtonBehavior, BoxLayout):
    """Gradient blue card with mic orb + label + subtitle.

    Uses ``BoxLayout`` (not ``FloatLayout``) so the mic + text row is laid out
    inside the card bounds. A ``FloatLayout`` child with only ``size_hint`` can
    end up with the wrong origin and draw over the schedule column instead.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint", (None, None))
        super().__init__(**kwargs)
        with self.canvas.before:
            # Approximate the Figma gradient (#0038b6 → #002376) with a flat
            # mid-blue plus a slight overlay; Kivy doesn't ship a built-in
            # vertical gradient on RoundedRectangle, and a 2-tone mid-blue
            # reads close enough at thumb size.
            Color(0.000, 0.220, 0.715, 1.0)  # ~#0038b6
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[_hv(30)])
            Color(0.000, 0.137, 0.460, 0.55)  # ~#002376 darker overlay
            self._bg_dark = RoundedRectangle(
                pos=(self.x, self.y),
                size=(self.width, self.height * 0.55),
                radius=[0, 0, _hv(30), _hv(30)],
            )
            # 3-px solid border #034ee2
            Color(0.012, 0.306, 0.886, 1.0)
            self._border = Line(
                rounded_rectangle=(self.x, self.y, self.width, self.height, _hv(30)),
                width=max(1.5, float(_hv(3))),
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_args):
        radius = _hv(30)
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._bg.radius = [radius]
        self._bg_dark.pos = (self.x, self.y)
        self._bg_dark.size = (self.width, self.height * 0.55)
        self._bg_dark.radius = [0, 0, radius, radius]
        self._border.rounded_rectangle = (
            self.x,
            self.y,
            self.width,
            self.height,
            radius,
        )
        self._border.width = max(1.5, float(_hv(3)))


# ---------------------------------------------------------------------------
# Idle Screen
# ---------------------------------------------------------------------------

class IdleScreen(BaseScreen):
    """Lock-screen-style idle UI shown after inactivity (see ``main.py`` timer)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._clock_event = None
        self._home_summary_event = None
        self._weather = get_weather_client()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = FloatLayout(size_hint=(1, 1))
        # Solid #010c25 fallback in case the background image fails to load.
        with root.canvas.before:
            Color(0.004, 0.047, 0.145, 1)
            self._bg_rect = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=self._sync_bg, size=self._sync_bg)

        bg_path = _idle_png("background_landscape.png")
        if bg_path:
            root.add_widget(
                Image(
                    source=bg_path,
                    size_hint=(1, 1),
                    pos_hint={"x": 0, "y": 0},
                    fit_mode="cover",
                    allow_stretch=True,
                    keep_ratio=False,
                )
            )

        # Foreground: vertical flex (top row | stretch | bottom row). Schedule and card are siblings.
        pl, pt = _idu(_IDLE_PAD_LEFT), _idu(_IDLE_PAD_TOP)
        pr, pb = _idu(_IDLE_PAD_RIGHT), _idu(_IDLE_PAD_BOTTOM)

        tl_w = _idu(_ZONE_TOP_LEFT_W)
        gr_h = _idu(28)
        gap_greet_clock = _idu(10)
        clk_h = _idu(120)
        gap_clock_date = _idu(4)
        date_h = _idu(40)
        tl_inner_h = (
            gr_h + gap_greet_clock + clk_h + gap_clock_date + date_h
        )

        top_left_stack = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(tl_w, tl_inner_h),
            spacing=0,
        )
        self.greeting_label = Label(
            text=_greeting(getattr(self.app, "current_display_name", None)),
            font_size=_hf(20),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=gr_h,
        )
        self.greeting_label.bind(size=self.greeting_label.setter("text_size"))
        top_left_stack.add_widget(self.greeting_label)
        top_left_stack.add_widget(
            Widget(size_hint=(1, None), height=gap_greet_clock),
        )

        clock_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=clk_h,
            spacing=_idu(10),
        )
        self.time_label = Label(
            text="--:--",
            font_size=_hf(100),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint=(None, None),
            size=(_hh(290), clk_h),
        )
        self.time_label.bind(size=self.time_label.setter("text_size"))
        clock_row.add_widget(self.time_label)
        am_col = AnchorLayout(
            size_hint=(None, None),
            size=(_hh(88), clk_h),
        )
        self.ampm_label = Label(
            text="",
            font_size=_hf(35),
            bold=True,
            color=(0.714, 0.729, 0.949, 1),
            halign="left",
            valign="middle",
            size_hint=(None, None),
            size=(_hh(80), _hv(44)),
        )
        self.ampm_label.bind(size=self.ampm_label.setter("text_size"))
        am_col.add_widget(self.ampm_label)
        clock_row.add_widget(am_col)
        top_left_stack.add_widget(clock_row)
        top_left_stack.add_widget(
            Widget(size_hint=(1, None), height=gap_clock_date),
        )

        self.date_label = Label(
            text="",
            font_size=_hf(30),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=date_h,
        )
        self.date_label.bind(size=self.date_label.setter("text_size"))
        top_left_stack.add_widget(self.date_label)

        wx_w = max(_idu(200), _hh(216))
        wx_body_h = _hv(112)
        top_band_h = max(tl_inner_h, wx_body_h)

        wx_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(wx_w, wx_body_h),
            spacing=_idu(8),
        )
        wt_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=_hv(64),
            spacing=_idu(20),
        )
        wx_src = _idle_png("icon_sun.png")
        if not wx_src:
            _alt = ASSETS_DIR / "home" / "figma" / "icon_weather.png"
            wx_src = str(_alt) if _alt.is_file() else ""
        self.weather_icon = Image(
            source=wx_src,
            size_hint=(None, None),
            size=(_hv(64), _hv(64)),
            fit_mode="contain",
            allow_stretch=True,
        )
        wt_row.add_widget(self.weather_icon)

        wt_right = AnchorLayout(
            anchor_x="right",
            anchor_y="center",
            size_hint=(1, None),
            height=_hv(64),
        )
        self.temp_label = Label(
            text="--°C",
            font_size=_hf(35),
            bold=True,
            color=COLORS["white"],
            halign="right",
            valign="middle",
            size_hint=(None, None),
            size=(wx_w - _hv(64) - _idu(20), _hv(48)),
        )
        self.temp_label.bind(size=self.temp_label.setter("text_size"))
        wt_right.add_widget(self.temp_label)
        wt_row.add_widget(wt_right)
        wx_col.add_widget(wt_row)

        self.condition_label = Label(
            text="--",
            font_size=_hf(28),
            color=(0.714, 0.729, 0.949, 1),
            halign="right",
            valign="middle",
            size_hint=(1, None),
            height=_hv(40),
        )
        self.condition_label.bind(size=self.condition_label.setter("text_size"))
        wx_col.add_widget(self.condition_label)

        tl_band = AnchorLayout(
            anchor_x="left",
            anchor_y="top",
            size_hint=(None, None),
            size=(tl_w, top_band_h),
        )
        tl_band.add_widget(top_left_stack)

        tr_band = AnchorLayout(
            anchor_x="right",
            anchor_y="top",
            size_hint=(None, None),
            size=(wx_w, top_band_h),
        )
        tr_band.add_widget(wx_col)

        top_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            spacing=0,
            height=top_band_h,
        )
        top_row.add_widget(tl_band)
        top_row.add_widget(Widget(size_hint=(1, 1)))
        top_row.add_widget(tr_band)

        sched_w = _idu(_ZONE_SCHEDULE_W)
        row_time_h = max(_idu(38), _hv(38))
        title_h = _idu(52)
        sched_stack = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            spacing=_idu(12),
        )

        self.next_label = Label(
            text="Next up",
            font_size=_hf(28),
            bold=True,
            color=(0, 0.420, 0.976, 1),
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=_hv(38),
        )
        self.next_label.bind(size=self.next_label.setter("text_size"))
        sched_stack.add_widget(self.next_label)

        sched_time_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=row_time_h,
            spacing=_idu(19),
        )
        cal_path = _idle_png("icon_calendar.png")
        if cal_path:
            cal_img = Image(
                source=cal_path,
                size_hint=(None, None),
                size=(_hv(34), _hv(34)),
                fit_mode="contain",
                allow_stretch=True,
            )
        else:
            cal_img = Widget(size_hint=(None, None), size=(_hv(34), _hv(34)))
        sched_time_row.add_widget(cal_img)

        self.next_time_label = Label(
            text="--:-- --",
            font_size=_hf(28),
            bold=True,
            color=(0, 0.420, 0.976, 1),
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=row_time_h,
        )
        self.next_time_label.bind(size=self.next_time_label.setter("text_size"))
        sched_time_row.add_widget(self.next_time_label)
        sched_stack.add_widget(sched_time_row)

        self.next_title_label = Label(
            text="--",
            font_size=_hf(28),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=title_h,
            text_size=(max(1, sched_w - 4), title_h),
            shorten=True,
            shorten_from="right",
            split_str=" ",
        )
        self.next_title_label.bind(size=self.next_title_label.setter("text_size"))
        sched_stack.add_widget(self.next_title_label)

        self.more_label = Label(
            text="",
            font_size=_hf(28),
            bold=True,
            color=(0, 0.420, 0.976, 1),
            halign="left",
            valign="middle",
            size_hint=(1, None),
            height=_hv(38),
        )
        self.more_label.bind(size=self.more_label.setter("text_size"))
        sched_stack.add_widget(self.more_label)

        sh = (
            _hv(38)
            + _idu(12)
            + row_time_h
            + _idu(12)
            + title_h
            + _idu(12)
            + _hv(38)
        )
        sched_stack.size = (sched_w, sh)

        card_pl, card_pt, card_pr, card_pb = (
            _idu(27),
            _idu(32),
            _idu(36),
            _idu(32),
        )
        mic_slot_w = _hv(101)
        spacing_h = _idu(18)
        card_w, card_h = _hh(414), _hv(167)
        text_w = max(
            _hh(160),
            card_w - card_pl - card_pr - mic_slot_w - spacing_h,
        )
        title_h = _hv(40)
        sub_h = _hv(52)
        text_stack_h = title_h + _hv(8) + sub_h

        card = _StartRecordingCard(
            orientation="horizontal",
            size=(card_w, card_h),
            size_hint=(None, None),
            padding=[card_pl, card_pt, card_pr, card_pb],
            spacing=spacing_h,
        )
        card.bind(on_release=self._on_start_recording)

        mic_path = _idle_png("mic_orb.png")

        mic_slot = AnchorLayout(
            size_hint=(None, 1),
            width=mic_slot_w,
            anchor_x="center",
            anchor_y="center",
        )
        if mic_path:
            mic_slot.add_widget(
                Image(
                    source=mic_path,
                    size_hint=(None, None),
                    size=(_hv(101), _hv(101)),
                    fit_mode="contain",
                    allow_stretch=True,
                ),
            )
        card.add_widget(mic_slot)

        text_slot = AnchorLayout(
            size_hint=(1, 1),
            anchor_x="left",
            anchor_y="center",
        )
        text_col = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(text_w, text_stack_h),
            spacing=_hv(8),
        )
        cta_title = Label(
            text="Start Recording",
            font_size=_hf(30),
            bold=True,
            color=COLORS["white"],
            halign="left",
            valign="bottom",
            size_hint=(1, None),
            height=title_h,
            text_size=(text_w, title_h),
        )
        cta_sub = Label(
            text='Tap or say "start recording"',
            font_size=_hf(18),
            color=COLORS["white"],
            halign="left",
            valign="top",
            size_hint=(1, None),
            height=sub_h,
            text_size=(text_w, sub_h),
        )
        text_col.add_widget(cta_title)
        text_col.add_widget(cta_sub)
        text_slot.add_widget(text_col)
        card.add_widget(text_slot)

        self._cta_card = card

        bottom_band_h = max(sh, card_h)
        sched_slot = AnchorLayout(
            anchor_x="left",
            anchor_y="bottom",
            size_hint=(None, None),
            size=(sched_w, bottom_band_h),
        )
        sched_slot.add_widget(sched_stack)

        card_slot = AnchorLayout(
            anchor_x="right",
            anchor_y="bottom",
            size_hint=(None, None),
            size=(card_w, bottom_band_h),
        )
        card_slot.add_widget(card)

        bottom_row = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=bottom_band_h,
            spacing=_idu(24),
        )
        bottom_row.add_widget(sched_slot)
        bottom_row.add_widget(Widget(size_hint=(1, 1)))
        bottom_row.add_widget(card_slot)

        content = BoxLayout(
            orientation="vertical",
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0},
            padding=[pl, pt, pr, pb],
            spacing=0,
        )
        content.add_widget(top_row)
        content.add_widget(Widget(size_hint=(1, 1)))
        content.add_widget(bottom_row)

        root.add_widget(content)
        self.add_widget(root)

    def _sync_bg(self, widget, *_args):
        self._bg_rect.pos = widget.pos
        self._bg_rect.size = widget.size

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_enter(self):
        self._update_clock()
        self._update_weather(self._weather.snapshot)
        self._weather.subscribe(self._update_weather)
        # Idle screen typically lives long; refresh the home summary on enter
        # and once a minute so "Next up" stays correct.
        self._refresh_home_summary()
        if self._clock_event:
            self._clock_event.cancel()
        self._clock_event = Clock.schedule_interval(lambda _dt: self._update_clock(), 1.0)
        if self._home_summary_event:
            self._home_summary_event.cancel()
        self._home_summary_event = Clock.schedule_interval(
            lambda _dt: self._refresh_home_summary(), 60.0
        )

    def on_leave(self):
        if self._clock_event:
            self._clock_event.cancel()
            self._clock_event = None
        if self._home_summary_event:
            self._home_summary_event.cancel()
            self._home_summary_event = None
        self._weather.unsubscribe(self._update_weather)

    # ------------------------------------------------------------------
    # Touch anywhere → home (taps on the recording card start a meeting instead)
    # ------------------------------------------------------------------
    def on_touch_up(self, touch):
        # Children first (card ButtonBehavior may fire on_release).
        if super().on_touch_up(touch):
            return True
        lx, ly = self.to_widget(touch.x, touch.y)
        if not self.collide_point(lx, ly):
            return False
        cc = getattr(self, "_cta_card", None)
        if cc is not None:
            cx, cy = cc.to_widget(touch.x, touch.y)
            if cc.collide_point(cx, cy):
                self._on_start_recording(None)
                return True
        try:
            self.goto("home", transition="fade")
        except Exception:  # noqa: BLE001
            logger.debug("idle: goto(home) failed", exc_info=True)
        return True

    # ------------------------------------------------------------------
    # Live data
    # ------------------------------------------------------------------
    def _update_clock(self):
        now = display_now()
        self.greeting_label.text = _greeting(getattr(self.app, "current_display_name", None))
        self.time_label.text = now.strftime("%I:%M").lstrip("0") or "12:00"
        self.ampm_label.text = now.strftime("%p")
        self.date_label.text = now.strftime("%A, %B ") + str(now.day)

    def _update_weather(self, snapshot) -> None:
        if snapshot is None:
            return
        # snapshot is a WeatherSnapshot dataclass
        try:
            temp = float(snapshot.temp_c)
            self.temp_label.text = f"{temp:.0f}°C"
            self.condition_label.text = snapshot.label or "--"
            self._set_weather_icon(snapshot.icon)
        except Exception:  # noqa: BLE001
            logger.debug("idle: bad weather snapshot %r", snapshot, exc_info=True)

    def _set_weather_icon(self, key: str) -> None:
        # Today we only ship a sun PNG in the idle bundle. Other codes fall
        # back to the cloud icon shipped with the home assets so we never end
        # up with a blank tile. Adding more icons later is a drop-in.
        cloud_path = ASSETS_DIR / "home" / "figma" / "icon_weather.png"
        sun_path = _IDLE_DIR / "icon_sun.png"
        if key == "sun" and sun_path.is_file():
            self.weather_icon.source = str(sun_path)
        elif cloud_path.is_file():
            self.weather_icon.source = str(cloud_path)
        elif sun_path.is_file():
            self.weather_icon.source = str(sun_path)

    def _refresh_home_summary(self):
        async def _fetch():
            try:
                data = await self.backend.get_home_summary()
            except Exception as exc:  # noqa: BLE001
                logger.debug("idle: home summary fetch failed: %s", exc)
                return
            time_str, title, _ = _format_meeting_line(data.get("next_meeting"))
            today_n = int(data.get("pending_actions_today") or 0)

            def _apply(_dt):
                self.next_time_label.text = time_str
                self.next_title_label.text = title or "--"
                self.more_label.text = f"+{max(0, today_n)} more" if today_n else ""

            Clock.schedule_once(_apply, 0)

        run_async(_fetch())

    # ------------------------------------------------------------------
    # Start Recording CTA
    # ------------------------------------------------------------------
    def _on_start_recording(self, _inst):
        try:
            self.app.start_recording()
        except Exception:  # noqa: BLE001
            logger.exception("idle: start_recording failed")
