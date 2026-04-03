"""
WiFi Setup – on-device scan, connect, and finish onboarding.

Design ref: UI_Ref_for_cursor/Wifi_Setup_screen/WIFI_Setup_screen.png

- Lists networks from GET /api/device/wifi/scan
- Rescan, add network manually (security type + SSID + password)
- Next enabled only after successful WiFi connection; then navigate
  to a WiFi success confirmation screen.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from screens.base_screen import BaseScreen
from components.button import PrimaryButton, SecondaryButton
from components.modal_dialog import ModalDialog
from config import COLORS, FONT_SIZES, ASSETS_DIR, BORDER_RADIUS
from async_helper import run_async

logger = logging.getLogger(__name__)

WELCOME_DIR = ASSETS_DIR / 'welcome'
LOGO_PATH = str(WELCOME_DIR / 'LOGO.png')
WIFI_BG = (0.043, 0.051, 0.067, 1)


def _format_security(sec: str) -> str:
    s = (sec or '').strip()
    if not s or s == '--':
        return 'OPEN'
    return s.upper().replace('_', ' ')


def _is_open_network(security: str) -> bool:
    s = (security or '').lower()
    return not s or s in ('open', '--', 'none', '')


class _AddNetworkLink(ButtonBehavior, Label):
    """Tappable '+ Add Network Manually' label."""
    pass


class _WiFiRow(ButtonBehavior, BoxLayout):
    """One network row in the setup list."""

    def __init__(self, net: dict, connecting_ssid: str, **kwargs):
        self.net = net
        ssid = net.get('ssid') or ''
        sec = net.get('security') or ''
        connected = bool(net.get('connected'))
        busy = connecting_ssid == ssid

        kwargs.setdefault('orientation', 'horizontal')
        kwargs.setdefault('size_hint_y', None)
        kwargs.setdefault('height', 68)
        kwargs.setdefault('padding', [12, 8])
        kwargs.setdefault('spacing', 10)
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(*COLORS['surface_light'])
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*COLORS['gray_800'])
            self._sep = Line(width=1)
        self.bind(
            pos=lambda w, *_: setattr(self._bg, 'pos', w.pos),
            size=lambda w, *_: setattr(self._bg, 'size', w.size),
        )
        self.bind(pos=self._update_sep, size=self._update_sep)

        # Signal / status icon (left)
        if busy:
            icon_txt = '⟳'
            icon_color = COLORS['blue']
        elif connected:
            icon_txt = '◉'
            icon_color = COLORS['blue']
        else:
            sig = int(net.get('signal_strength') or 0)
            if sig >= 70:
                icon_txt = '▂▄▆'
            elif sig >= 40:
                icon_txt = '▂▄'
            else:
                icon_txt = '▂'
            icon_color = COLORS['gray_500']

        icon = Label(
            text=icon_txt,
            font_size=FONT_SIZES['medium'],
            color=icon_color,
            size_hint=(None, 1),
            width=36,
            halign='center',
        )
        self.add_widget(icon)

        mid = BoxLayout(orientation='vertical', spacing=2, size_hint=(1, 1))
        title = Label(
            text=ssid,
            font_size=FONT_SIZES['medium'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
            size_hint=(1, None),
            height=22,
        )
        title.bind(size=title.setter('text_size'))
        mid.add_widget(title)

        if busy:
            sub = 'Connecting…'
            sub_color = COLORS['blue']
        else:
            sub = _format_security(sec)
            sub_color = COLORS['gray_500']
        sub_l = Label(
            text=sub,
            font_size=FONT_SIZES['small'],
            color=sub_color,
            halign='left',
            size_hint=(1, None),
            height=18,
        )
        sub_l.bind(size=sub_l.setter('text_size'))
        mid.add_widget(sub_l)
        self.add_widget(mid)

        if not busy:
            if _is_open_network(sec):
                right_txt = '○'
            else:
                right_txt = '🔒'
            right = Label(
                text=right_txt,
                font_size=FONT_SIZES['small'],
                color=COLORS['gray_400'],
                size_hint=(None, 1),
                width=28,
            )
            self.add_widget(right)

    def _update_sep(self, *_args):
        inset = 0.5
        self._sep.points = [
            self.x + inset, self.y + inset,
            self.x + self.width - inset, self.y + inset,
        ]


class WiFiSetupScreen(BaseScreen):
    """First-boot WiFi: scan, connect, then continue to success page."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.networks = []
        self._connecting_ssid = None
        self._ready_for_next = False
        self._connected_ssid = ''
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(
            orientation='vertical',
            padding=[20, 10, 20, 14],
            spacing=0,
            size_hint=(1, 1),
        )
        root.canvas.before.clear()
        with root.canvas.before:
            Color(*WIFI_BG)
            self._root_bg = RoundedRectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda w, *_: setattr(self._root_bg, 'pos', w.pos),
            size=lambda w, *_: setattr(self._root_bg, 'size', w.size),
        )

        # Header
        header = BoxLayout(
            orientation='horizontal', size_hint=(1, None), height=52, spacing=12)
        if Path(LOGO_PATH).exists():
            header.add_widget(Image(
                source=LOGO_PATH, size_hint=(None, 1), width=40, fit_mode='contain'))
        else:
            header.add_widget(Widget(size_hint=(None, 1), width=8))
        brand = Label(
            text='MeetingBox',
            font_size=FONT_SIZES['title'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            valign='middle',
            size_hint_x=1,
        )
        brand.bind(size=brand.setter('text_size'))
        header.add_widget(brand)
        root.add_widget(header)

        root.add_widget(Widget(size_hint=(1, None), height=6))

        title = Label(
            text='Connect to WiFi',
            font_size=FONT_SIZES['huge'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            size_hint=(1, None),
            height=40,
        )
        title.bind(size=title.setter('text_size'))
        root.add_widget(title)

        sub = Label(
            text='Required for calendar sync and email delivery.',
            font_size=FONT_SIZES['body'],
            color=COLORS['gray_400'],
            halign='left',
            size_hint=(1, None),
            height=28,
        )
        sub.bind(size=sub.setter('text_size'))
        root.add_widget(sub)

        root.add_widget(Widget(size_hint=(1, None), height=10))

        # List card
        card = BoxLayout(orientation='vertical', size_hint=(1, 1), spacing=0)
        with card.canvas.before:
            Color(*COLORS['surface'])
            self._card_bg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
        card.bind(
            pos=lambda w, *_: setattr(self._card_bg, 'pos', w.pos),
            size=lambda w, *_: setattr(self._card_bg, 'size', w.size),
        )

        scroll = ScrollView(do_scroll_x=False, size_hint=(1, 1))
        self._list = GridLayout(cols=1, spacing=0, size_hint_y=None, padding=[8, 8, 8, 0])
        self._list.bind(minimum_height=self._list.setter('height'))
        scroll.add_widget(self._list)
        card.add_widget(scroll)

        actions = BoxLayout(
            orientation='horizontal',
            size_hint=(1, None),
            height=40,
            padding=[8, 4],
            spacing=16,
        )
        add_link = _AddNetworkLink(
            text='+ Add Network Manually',
            font_size=FONT_SIZES['small'],
            color=COLORS['blue'],
            halign='left',
            valign='middle',
            size_hint=(0.55, 1),
        )
        add_link.bind(size=add_link.setter('text_size'))
        add_link.bind(on_press=lambda *_: self._show_manual_dialog())
        actions.add_widget(add_link)

        rescan = SecondaryButton(
            text='↻  Rescan',
            size_hint=(0.45, 1),
            font_size=FONT_SIZES['small'],
        )
        rescan.bind(on_press=lambda *_: self._load_networks(rescan=True))
        actions.add_widget(rescan)
        card.add_widget(actions)

        root.add_widget(card)

        root.add_widget(Widget(size_hint=(1, None), height=10))

        foot = BoxLayout(orientation='horizontal', size_hint=(1, None), height=52, spacing=12)
        back_btn = SecondaryButton(
            text='Back', size_hint=(None, 1), width=100,
            font_size=FONT_SIZES['medium'])
        back_btn.bind(on_press=self._on_back)
        foot.add_widget(back_btn)
        foot.add_widget(Widget(size_hint=(1, 1)))
        self._next_btn = PrimaryButton(
            text='Next',
            size_hint=(None, 1), width=120,
            font_size=FONT_SIZES['medium'],
        )
        self._next_btn.bind(on_press=self._on_next)
        self._next_btn.disabled = True
        self._next_btn.opacity = 0.45
        foot.add_widget(self._next_btn)
        root.add_widget(foot)

        self.add_widget(root)

    def on_enter(self):
        self._connecting_ssid = None
        self._ready_for_next = False
        self._connected_ssid = ''
        self._sync_next_button()
        self._load_networks(rescan=True)

    def on_leave(self):
        self._connecting_ssid = None

    def _sync_next_button(self):
        self._next_btn.disabled = not self._ready_for_next
        self._next_btn.opacity = 1.0 if self._ready_for_next else 0.45

    def _set_ready(self, ssid: str):
        self._ready_for_next = True
        self._connected_ssid = ssid or ''
        self._sync_next_button()

    def _load_networks(self, rescan: bool = False):
        async def _load():
            try:
                nets = self._scan_local_wifi(rescan=rescan)
                Clock.schedule_once(lambda *_: self._apply_networks(nets), 0)
            except Exception as e:
                logger.warning('Local WiFi scan failed, trying backend: %s', e)
                try:
                    nets = await self.backend.get_wifi_networks()
                    Clock.schedule_once(lambda *_: self._apply_networks(nets), 0)
                except Exception as be:
                    logger.warning('Backend WiFi scan failed: %s', be)
                    Clock.schedule_once(
                        lambda *_: self.add_widget(ModalDialog(
                            title='Scan failed',
                            message='Could not scan networks. Check WiFi adapter and NetworkManager.',
                            confirm_text='OK',
                            cancel_text='',
                        )), 0)

        run_async(_load())

    def _apply_networks(self, nets):
        self.networks = nets or []
        for n in self.networks:
            if n.get('connected') and n.get('ssid'):
                self._set_ready(n['ssid'])
                break
        self._populate_list()

    def _populate_list(self):
        self._list.clear_widgets()
        # Connected / stronger signals first
        def sort_key(x):
            return (0 if x.get('connected') else 1, -(x.get('signal_strength') or 0))

        for net in sorted(self.networks, key=sort_key):
            if not net.get('ssid'):
                continue
            row = _WiFiRow(net, self._connecting_ssid or '')
            row.bind(on_press=lambda inst, n=net: self._on_row_pressed(n))
            self._list.add_widget(row)
        if not self._list.children:
            empty = Label(
                text='No networks found. Tap Rescan.',
                font_size=FONT_SIZES['small'],
                color=COLORS['gray_500'],
                size_hint=(1, None),
                height=44,
                halign='left',
                valign='middle',
            )
            empty.bind(size=empty.setter('text_size'))
            self._list.add_widget(empty)

    def _on_row_pressed(self, net: dict):
        if self._connecting_ssid:
            return
        ssid = net.get('ssid')
        if not ssid:
            return
        if net.get('connected'):
            self._set_ready(ssid)
            self._populate_list()
            return
        sec = net.get('security') or ''
        if _is_open_network(sec):
            self._connect(ssid, None)
        else:
            self._show_password_dialog(ssid)

    def _show_password_dialog(self, ssid: str):
        overlay = FloatLayout()

        with overlay.canvas.before:
            Color(*COLORS['overlay'])
            ov = Rectangle(pos=overlay.pos, size=overlay.size)
        overlay.bind(
            pos=lambda w, *_: setattr(ov, 'pos', w.pos),
            size=lambda w, *_: setattr(ov, 'size', w.size),
        )

        card = BoxLayout(
            orientation='vertical',
            size_hint=(None, None),
            size=(400, 230),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            padding=16,
            spacing=10,
        )
        with card.canvas.before:
            Color(*COLORS['surface'])
            cbg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
        card.bind(
            pos=lambda w, *_: setattr(cbg, 'pos', w.pos),
            size=lambda w, *_: setattr(cbg, 'size', w.size),
        )

        title = Label(
            text=f'Connect to {ssid}',
            font_size=FONT_SIZES['title'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            size_hint=(1, None),
            height=28,
        )
        title.bind(size=title.setter('text_size'))
        card.add_widget(title)

        pwd = TextInput(
            hint_text='Password',
            password=True,
            multiline=False,
            font_size=FONT_SIZES['body'],
            size_hint=(1, None),
            height=44,
            background_color=COLORS['surface_light'],
            foreground_color=COLORS['white'],
            hint_text_color=COLORS['gray_600'],
            cursor_color=COLORS['white'],
        )
        card.add_widget(pwd)

        row = BoxLayout(size_hint=(1, None), height=48, spacing=10)
        cancel = SecondaryButton(text='Cancel', size_hint=(0.5, 1))
        go = PrimaryButton(text='Connect', size_hint=(0.5, 1))

        def dismiss(*_a):
            if overlay.parent:
                overlay.parent.remove_widget(overlay)

        def do_connect(*_a):
            pw = pwd.text.strip()
            if not pw:
                self.add_widget(ModalDialog(
                    title='Password required',
                    message='Enter the WiFi password.',
                    confirm_text='OK',
                    cancel_text='',
                ))
                return
            dismiss()
            self._connect(ssid, pw)

        cancel.bind(on_press=dismiss)
        go.bind(on_press=do_connect)
        row.add_widget(cancel)
        row.add_widget(go)
        card.add_widget(row)

        overlay.add_widget(card)
        self.add_widget(overlay)

    def _show_manual_dialog(self):
        overlay = FloatLayout()
        with overlay.canvas.before:
            Color(*COLORS['overlay'])
            ov = Rectangle(pos=overlay.pos, size=overlay.size)
        overlay.bind(
            pos=lambda w, *_: setattr(ov, 'pos', w.pos),
            size=lambda w, *_: setattr(ov, 'size', w.size),
        )

        card = BoxLayout(
            orientation='vertical',
            size_hint=(None, None),
            size=(420, 300),
            pos_hint={'center_x': 0.5, 'center_y': 0.5},
            padding=16,
            spacing=8,
        )
        with card.canvas.before:
            Color(*COLORS['surface'])
            cbg = RoundedRectangle(
                pos=card.pos, size=card.size, radius=[BORDER_RADIUS])
        card.bind(
            pos=lambda w, *_: setattr(cbg, 'pos', w.pos),
            size=lambda w, *_: setattr(cbg, 'size', w.size),
        )

        card.add_widget(Label(
            text='Add network',
            font_size=FONT_SIZES['title'],
            bold=True,
            color=COLORS['white'],
            halign='left',
            size_hint=(1, None),
            height=26,
        ))

        ssid_in = TextInput(
            hint_text='Network name (SSID)',
            multiline=False,
            font_size=FONT_SIZES['body'],
            size_hint=(1, None),
            height=40,
            background_color=COLORS['surface_light'],
            foreground_color=COLORS['white'],
            hint_text_color=COLORS['gray_600'],
            cursor_color=COLORS['white'],
        )
        card.add_widget(ssid_in)

        spin = Spinner(
            text='WPA2 Personal',
            values=('Open', 'WPA2 Personal', 'WPA3 Personal'),
            size_hint=(1, None),
            height=40,
            background_color=COLORS['gray_800'],
            color=COLORS['white'],
        )
        card.add_widget(spin)

        pwd_in = TextInput(
            hint_text='Password (if required)',
            password=True,
            multiline=False,
            font_size=FONT_SIZES['body'],
            size_hint=(1, None),
            height=40,
            background_color=COLORS['surface_light'],
            foreground_color=COLORS['white'],
            hint_text_color=COLORS['gray_600'],
            cursor_color=COLORS['white'],
        )
        card.add_widget(pwd_in)

        def on_sec(spinner, txt):
            pwd_in.disabled = txt == 'Open'
            pwd_in.opacity = 0.5 if pwd_in.disabled else 1.0

        spin.bind(text=on_sec)
        on_sec(spin, spin.text)

        row = BoxLayout(size_hint=(1, None), height=48, spacing=10)

        def dismiss(*_a):
            if overlay.parent:
                overlay.parent.remove_widget(overlay)

        def do_add(*_a):
            name = ssid_in.text.strip()
            if not name:
                return
            sec = spin.text
            if sec != 'Open':
                pw = pwd_in.text.strip()
                if not pw:
                    self.add_widget(ModalDialog(
                        title='Password required',
                        message='Enter the network password or choose Open.',
                        confirm_text='OK',
                        cancel_text='',
                    ))
                    return
            dismiss()
            if sec == 'Open':
                self._connect(name, None)
            else:
                self._connect(name, pwd_in.text.strip())

        cancel = SecondaryButton(text='Cancel', size_hint=(0.5, 1))
        go = PrimaryButton(text='Connect', size_hint=(0.5, 1))
        cancel.bind(on_press=dismiss)
        go.bind(on_press=do_add)
        row.add_widget(cancel)
        row.add_widget(go)
        card.add_widget(row)

        overlay.add_widget(card)
        self.add_widget(overlay)

    def _connect(self, ssid: str, password: Optional[str]):
        self._connecting_ssid = ssid
        self._populate_list()

        async def _run():
            try:
                if self._has_local_nmcli():
                    result = self._connect_local_wifi(ssid, password=password)
                else:
                    result = await self.backend.connect_wifi(ssid, password=password)
                ok = result.get('status') == 'connected'
                msg = result.get('message', '')

                def _done(*_a):
                    self._connecting_ssid = None
                    if ok:
                        self._set_ready(ssid)
                        self._load_networks(rescan=False)
                    else:
                        self._populate_list()
                        self.add_widget(ModalDialog(
                            title='Could not connect',
                            message=msg or 'Check the password and try again.',
                            confirm_text='OK',
                            cancel_text='',
                        ))

                Clock.schedule_once(_done, 0)
            except Exception as e:
                logger.warning('connect_wifi error: %s', e)

                def _fail(*_a):
                    self._connecting_ssid = None
                    self._populate_list()
                    self.add_widget(ModalDialog(
                        title='Connection error',
                        message=str(e)[:200],
                        confirm_text='OK',
                        cancel_text='',
                    ))

                Clock.schedule_once(_fail, 0)

        run_async(_run())

    # ------------------------------------------------------------------
    # Local WiFi control (native UI path)
    # ------------------------------------------------------------------

    def _has_local_nmcli(self) -> bool:
        return shutil.which('nmcli') is not None

    def _nmcli_run(self, args: list, timeout: float = 30):
        """
        Run nmcli. If NetworkManager denies the action (PolicyKit), retry with
        `sudo -n nmcli` so a passwordless sudo rule can allow WiFi on kiosk users.
        """
        cmd = ['nmcli'] + args
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        combined = ((res.stderr or '') + (res.stdout or '')).lower()
        priv = any(
            s in combined
            for s in (
                'insufficient privileges',
                'not authorized',
                'permission denied',
                'not allowed to',
                'polkit',
            )
        )
        if res.returncode != 0 and priv and shutil.which('sudo'):
            res2 = subprocess.run(
                ['sudo', '-n', 'nmcli'] + args,
                capture_output=True, text=True, timeout=timeout,
            )
            sudo_msg = ((res2.stderr or '') + (res2.stdout or '')).lower()
            # If sudo itself failed because it cannot prompt, keep original
            # NetworkManager error so users don't confuse sudo password with WiFi password.
            if (
                res2.returncode != 0 and
                any(
                    s in sudo_msg for s in (
                        'a password is required',
                        'password is required',
                        'terminal is required',
                        'no tty present',
                        'sudo: a password',
                    )
                )
            ):
                return res
            return res2
        return res

    def _detect_wifi_iface(self) -> Optional[str]:
        if not self._has_local_nmcli():
            return None
        try:
            res = self._nmcli_run(
                ['-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'],
                timeout=6,
            )
            for line in res.stdout.splitlines():
                parts = line.split(':')
                if len(parts) >= 3 and parts[1] == 'wifi':
                    return parts[0]
        except Exception:
            return None
        return None

    def _scan_local_wifi(self, rescan: bool = False) -> list[dict]:
        if not self._has_local_nmcli():
            raise RuntimeError('nmcli not available')
        if rescan:
            try:
                self._nmcli_run(['device', 'wifi', 'rescan'], timeout=10)
            except Exception:
                pass

        res = self._nmcli_run(
            ['-m', 'multiline', '-f', 'SSID,SIGNAL,SECURITY,IN-USE',
             'device', 'wifi', 'list'],
            timeout=15,
        )
        nets: list[dict] = []
        cur: dict[str, str] = {}

        def flush_current():
            ssid = (cur.get('SSID') or '').strip()
            if not ssid:
                return
            signal_raw = (cur.get('SIGNAL') or '0').strip()
            sec_raw = (cur.get('SECURITY') or '').strip()
            in_use = (cur.get('IN-USE') or '').strip()
            try:
                signal = int(signal_raw) if signal_raw else 0
            except ValueError:
                signal = 0
            nets.append({
                'ssid': ssid,
                'signal_strength': signal,
                'security': sec_raw or 'open',
                'connected': in_use == '*',
            })

        for line in res.stdout.splitlines():
            if ':' not in line:
                continue
            k, v = line.split(':', 1)
            key = k.strip()
            val = v.strip()

            # nmcli multiline output is not always separated by blank lines.
            # If a key repeats for the next AP, flush the current record first.
            if key == 'SSID' and 'SSID' in cur:
                flush_current()
                cur = {}

            cur[key] = val

        flush_current()
        return nets

    def _connect_local_wifi(self, ssid: str, password: Optional[str]) -> dict:
        iface = self._detect_wifi_iface()
        args = ['device', 'wifi', 'connect', ssid]
        if password:
            args += ['password', password]
        if iface:
            args += ['ifname', iface]
        res = self._nmcli_run(args, timeout=30)
        if res.returncode == 0:
            return {'status': 'connected', 'message': f'Connected to {ssid}'}
        msg = (res.stderr or '').strip() or (res.stdout or '').strip() or 'Connection failed'
        ml = msg.lower()
        if 'password' not in ml and '802' not in ml:
            if any(
                s in ml
                for s in ('sudo', 'privileges', 'not authorized', 'polkit')
            ):
                msg += (
                    '\n\nWiFi needs NetworkManager permission on this device. '
                    'See scripts/sudoers.meetingbox-nmcli.example or scripts/polkit/'
                )
        return {'status': 'failed', 'message': msg}

    def _on_back(self, _inst):
        self.go_back()

    def _on_next(self, _inst):
        if not self._ready_for_next:
            return
        self.app.connected_wifi_ssid = self._connected_ssid or ''
        self.app.setup_network_is_ethernet = False
        self.goto('wifi_connected', transition='fade')
