"""
Status Bar Component – Premium Dark Theme

Top bar (44 px) with:
  Left  – coloured status dot + text (e.g. ● READY)
  Centre – device / room name
  Right  – ⚙️ settings gear icon
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle, Line
from kivy.animation import Animation
from kivy.app import App

from config import COLORS, FONT_SIZES, STATUS_BAR_HEIGHT


class _GearButton(ButtonBehavior, Label):
    """Tappable gear icon."""
    pass


class StatusBar(BoxLayout):
    """
    Persistent status bar shown at the top of most screens.

    Parameters
    ----------
    status_text   : str   – e.g. 'READY', 'RECORDING'
    status_color  : tuple – RGBA for the dot
    device_name   : str   – centre label
    pulsing       : bool  – animate the dot (recording)
    show_settings : bool  – show ⚙️ icon on the right
    back_button   : bool  – show ← BACK instead of dot+text
    on_back       : callable – callback when back is tapped
    """

    def __init__(self, status_text='READY', status_color=None,
                 device_name='MeetingBox', pulsing=False,
                 show_settings=True, back_button=False, on_back=None,
                 **kwargs):

        kwargs.setdefault('size_hint', (1, None))
        kwargs.setdefault('height', STATUS_BAR_HEIGHT)
        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('padding', [16, 8])
        kwargs.setdefault('spacing', 8)

        super().__init__(**kwargs)

        self._status_color = status_color or COLORS['green']
        self.pulsing = pulsing
        self._back_button = back_button
        self._on_back = on_back

        # Dark background
        with self.canvas.before:
            Color(*COLORS['background'])
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)

        # Bottom separator
        with self.canvas.after:
            Color(*COLORS['gray_800'])
            self.border_line = Line(
                points=[self.x, self.y, self.x + self.width, self.y],
                width=1,
            )

        self.bind(pos=self._update_bg, size=self._update_bg)

        # --- LEFT: back button OR dot + status ---
        if back_button:
            back_label = _GearButton(
                text='←  BACK',
                font_size=FONT_SIZES['medium'],
                color=COLORS['white'],
                bold=True,
                halign='left',
                size_hint=(0.3, 1),
            )
            back_label.bind(size=back_label.setter('text_size'))
            if on_back:
                back_label.bind(on_press=lambda *_: on_back())
            self.add_widget(back_label)

            # centre title
            self.device_label = Label(
                text=device_name,
                font_size=FONT_SIZES['title'],
                color=COLORS['white'],
                bold=True,
                halign='center',
                size_hint=(0.5, 1),
            )
            self.device_label.bind(size=self.device_label.setter('text_size'))
            self.add_widget(self.device_label)

            # right spacer (or gear)
            if show_settings:
                gear = self._make_gear()
                self.add_widget(gear)
            else:
                self.add_widget(Widget(size_hint=(0.2, 1)))

        else:
            # dot + status text
            status_container = BoxLayout(
                orientation='horizontal',
                size_hint=(0.35, 1),
                spacing=8,
            )

            self.status_dot = Label(
                text='●',
                font_size=FONT_SIZES['small'],
                color=self._status_color,
                size_hint=(None, 1),
                width=20,
            )
            status_container.add_widget(self.status_dot)

            self.status_label = Label(
                text=status_text,
                font_size=FONT_SIZES['medium'],
                color=COLORS['white'],
                bold=True,
                halign='left',
                size_hint=(1, 1),
            )
            self.status_label.bind(size=self.status_label.setter('text_size'))
            status_container.add_widget(self.status_label)

            self.add_widget(status_container)

            # centre: device name
            self.device_label = Label(
                text=device_name,
                font_size=FONT_SIZES['small'],
                color=COLORS['gray_400'],
                halign='center',
                size_hint=(0.4, 1),
            )
            self.device_label.bind(size=self.device_label.setter('text_size'))
            self.add_widget(self.device_label)

            # right: gear icon
            if show_settings:
                gear = self._make_gear()
                self.add_widget(gear)
            else:
                self.add_widget(Widget(size_hint=(0.15, 1)))

        if self.pulsing and not back_button:
            self.start_pulse()

    # -- helpers --------------------------------------------------------

    def _make_gear(self):
        gear = _GearButton(
            text='⚙',
            font_size=FONT_SIZES['title'],
            color=COLORS['gray_500'],
            size_hint=(0.15, 1),
        )
        gear.bind(on_press=self._on_gear_pressed)
        return gear

    def _on_gear_pressed(self, *_args):
        app = App.get_running_app()
        if app:
            app.goto_screen('settings', transition='slide_left')

    def _update_bg(self, *_args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self.border_line.points = [
            self.x, self.y,
            self.x + self.width, self.y,
        ]

    def start_pulse(self):
        if hasattr(self, 'status_dot'):
            anim = (Animation(opacity=0.3, duration=0.8)
                    + Animation(opacity=1, duration=0.8))
            anim.repeat = True
            anim.start(self.status_dot)

    def stop_pulse(self):
        if hasattr(self, 'status_dot'):
            Animation.cancel_all(self.status_dot)
            self.status_dot.opacity = 1

    # -- public properties ----------------------------------------------

    @property
    def status_text(self):
        return self.status_label.text if hasattr(self, 'status_label') else ''

    @status_text.setter
    def status_text(self, value):
        if hasattr(self, 'status_label'):
            self.status_label.text = value

    @property
    def status_color(self):
        return self._status_color

    @status_color.setter
    def status_color(self, value):
        self._status_color = value
        if hasattr(self, 'status_dot'):
            self.status_dot.color = value
