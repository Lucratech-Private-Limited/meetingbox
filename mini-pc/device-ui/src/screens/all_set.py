"""
You're All Set Screen

Confirmation after successful setup.
Shows dashboard URL + QR code so the user knows where to go next.
Duration: 10 seconds (auto-advance) or tap to skip.
"""

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.image import Image
from kivy.uix.widget import Widget
from kivy.animation import Animation
from kivy.clock import Clock

from screens.base_screen import BaseScreen
from config import COLORS, FONT_SIZES, DASHBOARD_PUBLIC_URL, ALL_SET_DURATION

try:
    import qrcode
    from io import BytesIO
    from kivy.core.image import Image as CoreImage
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


class AllSetScreen(BaseScreen):
    """You're All Set -- post-setup success screen with dashboard URL."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._auto_event = None
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        root.add_widget(Widget(size_hint=(1, 0.08)))

        # Green checkmark
        self.check_label = Label(
            text='\u2713',
            font_size=48,
            bold=True,
            color=COLORS['green'],
            halign='center',
            size_hint=(1, None), height=60,
            opacity=0,
        )
        root.add_widget(self.check_label)

        # Main message
        self.msg_label = Label(
            text="You're All Set!",
            font_size=22,
            bold=True,
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=32,
            opacity=0,
        )
        root.add_widget(self.msg_label)

        root.add_widget(Widget(size_hint=(1, None), height=12))

        # Dashboard URL prompt
        self.url_prompt = Label(
            text='Create your account at:',
            font_size=FONT_SIZES['body'],
            color=COLORS['gray_400'],
            halign='center',
            size_hint=(1, None), height=22,
            opacity=0,
        )
        root.add_widget(self.url_prompt)

        self.url_label = Label(
            text=DASHBOARD_PUBLIC_URL,
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['blue'],
            halign='center',
            size_hint=(1, None), height=24,
            opacity=0,
        )
        root.add_widget(self.url_label)

        root.add_widget(Widget(size_hint=(1, None), height=8))

        # QR code
        qr_container = BoxLayout(
            orientation='vertical',
            size_hint=(None, None),
            size=(100, 100),
            pos_hint={'center_x': 0.5},
        )
        qr_widget = self._generate_qr(DASHBOARD_PUBLIC_URL)
        qr_container.add_widget(qr_widget)
        self.qr_container = qr_container
        self.qr_container.opacity = 0
        root.add_widget(qr_container)

        root.add_widget(Widget(size_hint=(1, None), height=8))

        self.tap_hint = Label(
            text='Tap anywhere to continue',
            font_size=FONT_SIZES['tiny'],
            color=COLORS['gray_600'],
            halign='center',
            size_hint=(1, None), height=16,
            opacity=0,
        )
        root.add_widget(self.tap_hint)

        root.add_widget(Widget(size_hint=(1, 0.05)))
        self.add_widget(root)

    def on_enter(self):
        self.check_label.opacity = 0
        self.msg_label.opacity = 0
        self.url_prompt.opacity = 0
        self.url_label.opacity = 0
        self.qr_container.opacity = 0
        self.tap_hint.opacity = 0

        Animation(opacity=1, duration=0.5).start(self.check_label)

        anim_msg = Animation(opacity=0, duration=0.3) + Animation(opacity=1, duration=0.3)
        anim_msg.start(self.msg_label)

        anim_url = Animation(opacity=0, duration=0.6) + Animation(opacity=1, duration=0.4)
        anim_url.start(self.url_prompt)
        anim_url2 = Animation(opacity=0, duration=0.7) + Animation(opacity=1, duration=0.4)
        anim_url2.start(self.url_label)

        anim_qr = Animation(opacity=0, duration=0.9) + Animation(opacity=1, duration=0.4)
        anim_qr.start(self.qr_container)

        anim_hint = Animation(opacity=0, duration=1.5) + Animation(opacity=1, duration=0.5)
        anim_hint.start(self.tap_hint)

        self._auto_event = Clock.schedule_once(self._go_home, ALL_SET_DURATION)

    def on_leave(self):
        if self._auto_event:
            self._auto_event.cancel()
            self._auto_event = None

    def on_touch_down(self, touch):
        self._go_home(0)
        return True

    def _go_home(self, _dt):
        if self._auto_event:
            self._auto_event.cancel()
            self._auto_event = None
        self.goto('home', transition='fade')

    def _generate_qr(self, url: str):
        """Generate a small QR code image widget."""
        if HAS_QRCODE:
            try:
                qr = qrcode.QRCode(version=1, box_size=4, border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color='white', back_color='black')
                buf = BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                core_img = CoreImage(buf, ext='png')
                return Image(texture=core_img.texture, size_hint=(1, 1))
            except Exception:
                pass
        return Label(
            text='[QR]',
            font_size=FONT_SIZES['small'],
            color=COLORS['gray_500'],
        )
