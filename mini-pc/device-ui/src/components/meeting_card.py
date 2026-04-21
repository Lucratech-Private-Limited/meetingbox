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
        kwargs.setdefault('height', _mc_suv(60))
        kwargs.setdefault('padding', [_mc_suh(SPACING['button_spacing']), _mc_suv(8)])
        kwargs.setdefault('spacing', _mc_suv(2))

        super().__init__(**kwargs)

        # Background (avoid canvas.before.clear on press — smoother scrolling)
        with self.canvas.before:
            self._bg_color = Color(*COLORS['surface'])
            self._bg = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[BORDER_RADIUS])
        self.bind(
            pos=lambda w, v: setattr(self._bg, 'pos', w.pos),
            size=lambda w, v: setattr(self._bg, 'size', w.size),
        )

        # Title
        title = Label(
            text=meeting['title'],
            font_size=_mc_suf(FONT_SIZES['medium']),
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
            color=COLORS['gray_500'],
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
        self._bg_color.rgba = COLORS['surface_light']

    def on_release(self):
        self._bg_color.rgba = COLORS['surface']
