"""
Meeting Card Component – Dark Theme

Card showing meeting summary in list.
"""

from datetime import datetime, timedelta
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, RoundedRectangle
from config import (
    BORDER_RADIUS,
    COLORS,
    FONT_SIZES,
    SPACING,
    display_now,
    other_screen_horizontal_scale,
    other_screen_vertical_scale,
    to_display_local,
)


def _mc_suv(px):
    v = other_screen_vertical_scale()
    return max(1, int(round(float(px) * v)))


def _mc_suh(px):
    h = other_screen_horizontal_scale()
    return max(1, int(round(float(px) * h)))


def _mc_suf(fs):
    v = other_screen_vertical_scale()
    return max(6, int(round(float(fs) * v)))


class MeetingCard(ButtonBehavior, BoxLayout):
    """
    Dark-themed meeting card.

    Shows: title, time ago + duration, pending actions.
    """

    def __init__(self, meeting: dict, **kwargs):
        self.meeting = meeting

        kwargs.setdefault('orientation', 'vertical')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', _mc_suv(86))
        kwargs.setdefault('padding', [_mc_suh(16), _mc_suv(12)])
        kwargs.setdefault('spacing', _mc_suv(5))

        super().__init__(**kwargs)

        # Background (avoid canvas.before.clear on press — smoother scrolling)
        with self.canvas.before:
            self._shadow_color = Color(0, 0, 0, 0.18)
            self._shadow = RoundedRectangle(pos=(self.x + 1, self.y - _mc_suv(3)), size=self.size, radius=[BORDER_RADIUS])
            self._bg_color = Color(0.13, 0.17, 0.24, 0.92)
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=self._sync_bg,
            size=self._sync_bg,
        )

        # Title
        title = Label(
            text=meeting['title'],
            font_size=_mc_suf(FONT_SIZES['medium'] + 1),
            color=COLORS['white'],
            bold=True,
            halign='left', valign='top',
            size_hint=(1, 0.45),
        )
        title.bind(size=title.setter('text_size'))
        self.add_widget(title)

        # Metadata
        meta = Label(
            text=self._format_meta(),
            font_size=_mc_suf(FONT_SIZES['small']),
            color=COLORS['gray_300'],
            halign='left', valign='top',
            size_hint=(1, 0.3),
        )
        meta.bind(size=meta.setter('text_size'))
        self.add_widget(meta)

        # Pending actions
        pending = meeting.get('pending_actions', 0)
        if pending > 0:
            pa = Label(
                text=f"⚡ {pending} pending action{'s' if pending > 1 else ''}",
                font_size=_mc_suf(FONT_SIZES['small']),
                color=COLORS['yellow'],
                halign='left', valign='top',
                size_hint=(1, 0.25),
            )
            pa.bind(size=pa.setter('text_size'))
            self.add_widget(pa)


    def _sync_bg(self, *_args):
        self._shadow.pos = (self.x + 1, self.y - _mc_suv(3))
        self._shadow.size = self.size
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _format_meta(self) -> str:
        start = to_display_local(
            datetime.fromisoformat(
                self.meeting['start_time'].replace('Z', '+00:00')
            )
        )
        now = display_now()
        delta = now - start
        if delta < timedelta(hours=1):
            ago = f"{int(delta.total_seconds() / 60)} min ago"
        elif delta < timedelta(days=1):
            ago = f"{int(delta.total_seconds() / 3600)} hr ago"
        else:
            ago = f"{delta.days} days ago"
        dur = self.meeting.get('duration', 0)
        if dur:
            return f"{ago} · {dur // 60} min"
        return ago

    def on_press(self):
        self._bg_color.rgba = (0.18, 0.24, 0.34, 0.98)

    def on_release(self):
        self._bg_color.rgba = (0.13, 0.17, 0.24, 0.92)
