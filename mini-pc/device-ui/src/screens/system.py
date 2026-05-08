"""Premium system health screen."""

from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from async_helper import run_async

from components.button import PrimaryButton, SecondaryButton
from components.status_bar import StatusBar
from config import COLORS, FONT_SIZES, SPACING
from screens.base_screen import BaseScreen


class SystemScreen(BaseScreen):
    """System info – premium appliance health."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.system_info = {}
        self._build_ui()

    def _build_ui(self):
        sv = self.suv
        sf = self.suf
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)
        self.status_bar = StatusBar(status_text='System', device_name='System Health', back_button=True, on_back=self.go_back, show_settings=False)
        root.add_widget(self.status_bar)

        body = BoxLayout(orientation='vertical', padding=[sv(SPACING['screen_padding']), sv(14)], spacing=sv(12))
        hero = BoxLayout(orientation='vertical', size_hint=(1, None), height=sv(96), padding=[sv(18), sv(14)], spacing=sv(4))
        self.attach_card_bg(hero, radius=sv(26), color=(0.10, 0.15, 0.24, 0.88))
        title = Label(text='Appliance health', font_size=sf(FONT_SIZES['large']), bold=True, color=COLORS['white'], halign='left', valign='bottom', size_hint=(1, .55))
        title.bind(size=title.setter('text_size'))
        hero.add_widget(title)
        self.subtitle = Label(text='Loading system information…', font_size=sf(FONT_SIZES['small']), color=COLORS['gray_300'], halign='left', valign='top', size_hint=(1, .45))
        self.subtitle.bind(size=self.subtitle.setter('text_size'))
        hero.add_widget(self.subtitle)
        body.add_widget(hero)

        scroll = ScrollView(do_scroll_x=False)
        self.grid = GridLayout(cols=2, spacing=sv(10), size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter('height'))
        scroll.add_widget(self.grid)
        body.add_widget(scroll)

        actions = BoxLayout(orientation='horizontal', size_hint=(1, None), height=sv(56), spacing=sv(10))
        refresh = SecondaryButton(text='Refresh', font_size=sf(FONT_SIZES['small']))
        refresh.bind(on_release=lambda *_: self._load_info())
        actions.add_widget(refresh)
        self.update_btn = PrimaryButton(text='Check Updates', font_size=sf(FONT_SIZES['small']))
        self.update_btn.bind(on_press=self._on_update)
        actions.add_widget(self.update_btn)
        body.add_widget(actions)
        root.add_widget(body)
        root.add_widget(self.build_footer())
        self.add_widget(root)

    def on_enter(self):
        self._load_info()

    def _load_info(self):
        self.subtitle.text = 'Refreshing local health and storage details…'
        async def _load():
            try:
                info = await self.backend.get_system_info()
                self.system_info = info
                Clock.schedule_once(lambda _: self._populate(), 0)
            except Exception:
                Clock.schedule_once(lambda _: self._populate_error(), 0)
        run_async(_load())

    def _metric(self, title, value, accent=False):
        card = BoxLayout(orientation='vertical', size_hint_y=None, height=self.suv(86), padding=[self.suv(14), self.suv(10)], spacing=self.suv(2))
        self.attach_card_bg(card, radius=self.suv(22), color=(0.08, 0.11, 0.18, 0.78))
        t = Label(text=title, font_size=self.suf(FONT_SIZES['tiny']), bold=True, color=COLORS['blue'] if accent else COLORS['gray_500'], halign='left', valign='bottom', size_hint=(1, .38))
        t.bind(size=t.setter('text_size'))
        card.add_widget(t)
        v = Label(text=str(value), font_size=self.suf(FONT_SIZES['medium']), bold=True, color=COLORS['white'], halign='left', valign='top', size_hint=(1, .62), shorten=True)
        v.bind(size=v.setter('text_size'))
        card.add_widget(v)
        return card

    def _populate_error(self):
        self.subtitle.text = 'Unable to load system information.'
        self.grid.clear_widgets()
        self.grid.add_widget(self._metric('Status', 'Backend unavailable', True))

    def _populate(self):
        i = self.system_info or {}
        su = i.get('storage_used', 0) / (1024**3)
        st = i.get('storage_total', 1) / (1024**3)
        sf = st - su
        up_s = i.get('uptime', 0)
        up_d = up_s // 86400
        up_h = (up_s % 86400) // 3600
        sig = i.get('wifi_signal', 0)
        bars = '▂▄▆█'[:max(1, sig // 25)]
        self.subtitle.text = f"{sf:.0f}GB free · uptime {up_d}d {up_h}h · firmware {i.get('firmware_version', '?')}"
        self.grid.clear_widgets()
        metrics = [
            ('Device', i.get('device_name', '?'), True),
            ('IP Address', i.get('ip_address', '?'), False),
            ('Network', f"{i.get('wifi_ssid', 'N/A')} {bars}", False),
            ('Storage', f'{su:.0f}/{st:.0f}GB · {sf:.0f}GB free', True),
            ('Meetings', i.get('meetings_count', 0), False),
            ('Uptime', f'{up_d}d {up_h}h', False),
        ]
        for title, value, accent in metrics:
            self.grid.add_widget(self._metric(title, value, accent))

    def _on_update(self, _inst):
        self.goto('update_check', transition='slide_left')
