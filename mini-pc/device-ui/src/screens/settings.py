"""
Settings Screen – Scrollable comprehensive list (480 × 320)

PRD §5.11 – Sections: DEVICE, NETWORK, STORAGE, SYSTEM,
PRIVACY, DISPLAY, AUDIO, INTEGRATIONS, MAINTENANCE, SUPPORT.
"""

import asyncio
import logging

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.gridlayout import GridLayout
from kivy.effects.scroll import ScrollEffect
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.clock import Clock
from async_helper import run_async

from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from components.settings_item import SettingsItem
from components.modal_dialog import ModalDialog
from components.text_input_dialog import TextInputDialog
from config import (COLORS, FONT_SIZES, SPACING, DEVICE_MODEL,
                    DASHBOARD_URL)
from hardware import request_system_poweroff, request_system_reboot
from network_util import linux_ethernet_ready
from weather_client import get_weather_client

logger = logging.getLogger(__name__)


class SettingsScreen(BaseScreen):
    """Scrollable settings screen – PRD §5.11."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_ui()

    def _section_header(self, text):
        """Create an uppercase gray section header label."""
        lbl = Label(
            text=text,
            font_size=self.suf(FONT_SIZES['small']),
            bold=True,
            color=COLORS['gray_500'],
            halign='left',
            valign='bottom',
            size_hint_y=None,
            height=self.suv(28),
            padding=[self.suh(16), 0],
        )
        lbl.bind(size=lbl.setter('text_size'))
        return lbl

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        # Header
        self.status_bar = StatusBar(
            status_text='Settings',
            device_name='Settings',
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        # Scrollable items
        scroll = ScrollView(
            do_scroll_x=False,
            scroll_distance=12,
            effect_cls=ScrollEffect,
            smooth_scroll_end=0,
            always_overscroll=False,
        )
        self.container = GridLayout(
            cols=1,
            size_hint_x=1,
            spacing=self.suv(SPACING['list_item_spacing']),
            padding=[self.suh(SPACING['screen_padding']), self.suv(8)],
            size_hint_y=None,
        )
        self.container.bind(minimum_height=self.container.setter('height'))

        # ---- DEVICE ----
        self.container.add_widget(self._section_header('DEVICE'))

        self.device_name_item = SettingsItem(
            title='Device Name',
            subtitle='MeetingBox',
            mode='arrow',
            on_press=lambda _: self._show_device_name_dialog(),
        )
        self.container.add_widget(self.device_name_item)

        self.model_item = SettingsItem(
            title='Model / Serial',
            subtitle=f'{DEVICE_MODEL}\nSerial: Loading…',
            mode='info',
        )
        self.model_item.height = self.suv(70)
        self.container.add_widget(self.model_item)

        # ---- NETWORK ----
        self.container.add_widget(self._section_header('NETWORK'))

        self.wifi_item = SettingsItem(
            title='WiFi',
            subtitle='Loading…',
            mode='arrow',
            on_press=lambda _: self.goto('wifi', transition='slide_left'),
        )
        self.container.add_widget(self.wifi_item)

        # ---- STORAGE ----
        self.container.add_widget(self._section_header('STORAGE'))

        self.storage_item = SettingsItem(
            title='Storage',
            subtitle='Loading…',
            mode='info',
        )
        self.container.add_widget(self.storage_item)

        self.auto_delete_item = SettingsItem(
            title='Auto-delete old meetings',
            subtitle='Never',
            mode='arrow',
            on_press=lambda _: self.goto('auto_delete_picker', transition='slide_left'),
        )
        self.container.add_widget(self.auto_delete_item)

        # ---- SYSTEM ----
        self.container.add_widget(self._section_header('SYSTEM'))

        self.firmware_item = SettingsItem(
            title='Firmware Version',
            subtitle='Loading…',
            mode='info',
        )
        self.container.add_widget(self.firmware_item)

        self.update_item = SettingsItem(
            title='Check for Updates',
            subtitle='',
            mode='arrow',
            on_press=lambda _: self.goto('update_check', transition='slide_left'),
        )
        self.container.add_widget(self.update_item)

        self.uptime_item = SettingsItem(
            title='Uptime',
            subtitle='Loading…',
            mode='info',
        )
        self.container.add_widget(self.uptime_item)

        # ---- PRIVACY ----
        self.container.add_widget(self._section_header('PRIVACY'))

        self.privacy_item = SettingsItem(
            title='Privacy Mode',
            subtitle='All processing happens locally',
            mode='toggle',
            active=False,
            on_toggle=self._on_privacy_toggled,
        )
        self.container.add_widget(self.privacy_item)

        self.auto_record_item = SettingsItem(
            title='Auto-start from calendar',
            subtitle='Start recording when a meeting is scheduled',
            mode='toggle',
            active=False,
            on_toggle=self._on_auto_record_toggled,
        )
        self.container.add_widget(self.auto_record_item)

        # ---- DISPLAY ----
        self.container.add_widget(self._section_header('DISPLAY'))

        self.brightness_item = SettingsItem(
            title='Screen Brightness',
            subtitle='High',
            mode='arrow',
            on_press=lambda _: self.goto('brightness_picker', transition='slide_left'),
        )
        self.container.add_widget(self.brightness_item)

        # Replaces the old "Screen Timeout" (display-off) entry. Opens the
        # idle-timeout picker so users can set how long until the lock-screen
        # idle UI takes over.
        self.idle_timeout_item = SettingsItem(
            title='Idle Screen',
            subtitle='After 30 seconds',
            mode='arrow',
            on_press=lambda _: self.goto('idle_timeout_picker', transition='slide_left'),
        )
        self.container.add_widget(self.idle_timeout_item)

        # Weather location used by the home/idle screens. Stored locally on
        # the device (no backend involvement) — IP-detected by default,
        # editable here.
        self.weather_location_item = SettingsItem(
            title='Weather Location',
            subtitle='Auto-detect from IP',
            mode='arrow',
            on_press=lambda _: self._show_weather_location_dialog(),
        )
        self.container.add_widget(self.weather_location_item)

        # ---- AUDIO ----
        self.container.add_widget(self._section_header('AUDIO'))

        self.speech_volume_item = SettingsItem(
            title='Assistant voice volume',
            subtitle='85%',
            mode='arrow',
            on_press=lambda _: self.goto('speech_volume_picker', transition='slide_left'),
        )
        self.container.add_widget(self.speech_volume_item)

        self.mic_test_item = SettingsItem(
            title='Microphone Test',
            subtitle='',
            mode='arrow',
            on_press=lambda _: self.goto('mic_test', transition='slide_left'),
        )
        self.container.add_widget(self.mic_test_item)

        self.voice_assistant_enabled_item = SettingsItem(
            title='Voice assistant',
            subtitle='Wake word + cloud Q&A (main toggle)',
            mode='toggle',
            active=True,
            on_toggle=self._on_voice_assistant_enabled_toggled,
        )
        self.container.add_widget(self.voice_assistant_enabled_item)

        self.voice_realtime_item = SettingsItem(
            title='Realtime voice mode',
            subtitle='OpenAI Realtime — coming soon',
            mode='toggle',
            active=False,
            on_toggle=self._on_voice_realtime_toggled,
        )
        self.container.add_widget(self.voice_realtime_item)

        self.wake_phrase_item = SettingsItem(
            title='Wake phrase',
            subtitle='hey buddy',
            mode='arrow',
            on_press=lambda _: self._show_wake_phrase_dialog(),
        )
        self.container.add_widget(self.wake_phrase_item)

        # ---- INTEGRATIONS ----
        self.container.add_widget(self._section_header('INTEGRATIONS'))

        self.gmail_item = SettingsItem(
            title='Gmail',
            subtitle=f'Configure at {DASHBOARD_URL}',
            mode='info',
        )
        self.container.add_widget(self.gmail_item)

        self.calendar_item = SettingsItem(
            title='Calendar',
            subtitle=f'Configure at {DASHBOARD_URL}',
            mode='info',
        )
        self.container.add_widget(self.calendar_item)

        # ---- MAINTENANCE ----
        self.container.add_widget(self._section_header('MAINTENANCE'))

        self.unpair_account_item = SettingsItem(
            title='Unpair from account',
            subtitle='Link again with a new pairing code',
            mode='arrow',
            on_press=lambda _: self._show_unpair_account_dialog(),
        )
        self.container.add_widget(self.unpair_account_item)

        self.restart_item = SettingsItem(
            title='Restart Device',
            subtitle='',
            mode='arrow',
            on_press=lambda _: self._show_restart_dialog(),
        )
        self.container.add_widget(self.restart_item)

        self.poweroff_item = SettingsItem(
            title='Power Off',
            subtitle='',
            mode='arrow',
            on_press=lambda _: self._show_poweroff_dialog(),
        )
        self.container.add_widget(self.poweroff_item)

        self.reset_item = SettingsItem(
            title='Factory Reset',
            subtitle='',
            mode='arrow',
            on_press=lambda _: self._show_factory_reset_dialog(),
        )
        self.container.add_widget(self.reset_item)

        # ---- SUPPORT ----
        self.container.add_widget(self._section_header('SUPPORT'))

        self.support_item = SettingsItem(
            title='Help',
            subtitle='support.meetingbox.com',
            mode='info',
        )
        self.container.add_widget(self.support_item)

        # Bottom padding
        self.container.add_widget(Widget(size_hint_y=None, height=self.suv(20)))

        scroll.add_widget(self.container)
        root.add_widget(scroll)

        # Footer
        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_enter(self):
        self._load_system_info()
        # Sync privacy and auto_record toggles from app state
        privacy = getattr(self.app, 'privacy_mode', False)
        self.privacy_item.toggle.active = privacy
        auto_record = getattr(self.app, 'auto_record', False)
        self.auto_record_item.toggle.active = auto_record
        vae = getattr(self.app, "voice_assistant_enabled", True)
        self.voice_assistant_enabled_item.toggle.active = bool(vae)
        vra = getattr(self.app, "voice_realtime_assistant", False)
        self.voice_realtime_item.toggle.active = bool(vra)
        wk = getattr(self.app, "voice_wake_phrase_display", "hey buddy")
        self.wake_phrase_item.subtitle_label.text = (wk or "hey buddy").lower()
        try:
            sv = int(getattr(self.app, "assistant_speech_volume", 85))
        except (TypeError, ValueError):
            sv = 85
        self.speech_volume_item.subtitle_label.text = f'{max(0, min(100, sv))}%'

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _load_system_info(self):
        async def _fetch():
            try:
                results = await asyncio.gather(
                    self.backend.get_system_info(),
                    self.backend.get_settings(),
                    self.backend.get_integrations(),
                    return_exceptions=True,
                )
                info = results[0] if not isinstance(results[0], Exception) else {}
                settings = (
                    results[1] if not isinstance(results[1], Exception) else {}
                )
                integrations = (
                    results[2] if not isinstance(results[2], Exception) else []
                )

                wifi_ssid = info.get('wifi_ssid', 'N/A')
                sig = int(info.get('wifi_signal', 0) or 0)
                ip = info.get('ip_address', '?')
                wifi_text = f'{wifi_ssid}  ({sig}%)\nIP: {ip}'

                su = info.get('storage_used', 0) / (1024 ** 3)
                st = info.get('storage_total', 1) / (1024 ** 3)
                sf = st - su
                mc = info.get('meetings_count', 0)
                storage_text = f'{su:.0f}/{st:.0f}GB used · {sf:.0f}GB free\n{mc} meetings'

                fw = info.get('firmware_version', '?')
                serial = info.get('serial_number', 'MB-00000000')
                up_s = info.get('uptime', 0)
                up_d = up_s // 86400
                up_h = (up_s % 86400) // 3600

                ad = settings.get('auto_delete_days', 'never')
                ad_labels = {'never': 'Never', '30': 'After 30 days',
                             '60': 'After 60 days', '90': 'After 90 days'}
                br = settings.get('brightness', 'high')
                br_labels = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
                idle = settings.get('idle_screen_timeout', '30')
                idle_labels = {
                    '30': 'After 30 seconds',
                    '60': 'After 1 minute',
                    '120': 'After 2 minutes',
                    '300': 'After 5 minutes',
                    'never': 'Never',
                }

                gmail_status = f'Configure at {DASHBOARD_URL}'
                cal_status = f'Configure at {DASHBOARD_URL}'
                for integ in integrations:
                    iid = (integ.get('id') or '').lower()
                    iname = (integ.get('name') or '').lower()
                    connected = bool(integ.get('connected'))
                    email = (integ.get('email') or '').strip()
                    acct = f' · {email}' if email else ''
                    if iid == 'gmail' or 'gmail' in iname or 'mail' in iname:
                        gmail_status = (
                            f'Connected{acct}' if connected else f'Not connected · use {DASHBOARD_URL}'
                        )
                    elif iid == 'calendar' or 'calendar' in iname:
                        cal_status = (
                            f'Connected{acct}' if connected else f'Not connected · use {DASHBOARD_URL}'
                        )

                def _update(_dt):
                    self.wifi_item.subtitle_label.text = wifi_text
                    self.storage_item.subtitle_label.text = storage_text
                    self.firmware_item.subtitle_label.text = fw
                    self.model_item.subtitle_label.text = (
                        f'{DEVICE_MODEL}\nSerial: {serial}')
                    self.uptime_item.subtitle_label.text = f'{up_d}d {up_h}h'
                    name = info.get('device_name', 'MeetingBox')
                    self.device_name_item.subtitle_label.text = name
                    self.app.device_name = name

                    self.auto_delete_item.subtitle_label.text = ad_labels.get(ad, ad)
                    self.brightness_item.subtitle_label.text = br_labels.get(br, br)
                    self.idle_timeout_item.subtitle_label.text = idle_labels.get(idle, f'After {idle}s')

                    weather_loc = get_weather_client().location
                    if weather_loc:
                        self.weather_location_item.subtitle_label.text = (
                            f"{weather_loc['city']} (auto)"
                        )
                    else:
                        self.weather_location_item.subtitle_label.text = 'Auto-detect from IP'

                    auto_rec = settings.get('auto_record', False)
                    self.app.auto_record = auto_rec
                    self.auto_record_item.toggle.active = auto_rec

                    self.gmail_item.subtitle_label.text = gmail_status
                    self.calendar_item.subtitle_label.text = cal_status

                    try:
                        vae = settings.get("voice_assistant_enabled", True)
                        if isinstance(vae, str):
                            vae = str(vae).strip().lower() in ("1", "true", "yes", "on")
                        self.voice_assistant_enabled_item.toggle.active = bool(vae)
                        self.app.voice_assistant_enabled = bool(vae)

                        vra = settings.get("voice_realtime_assistant", False)
                        if isinstance(vra, str):
                            vra = str(vra).strip().lower() in ("1", "true", "yes", "on")
                        self.voice_realtime_item.toggle.active = bool(vra)
                        wk = (settings.get("voice_wake_phrase") or "hey buddy").strip().lower()
                        self.wake_phrase_item.subtitle_label.text = wk
                        self.app.voice_realtime_assistant = bool(vra)
                        self.app.voice_wake_phrase_display = (
                            wk[:1].upper() + wk[1:] if wk else "Hey buddy"
                        )
                        if hasattr(self.app, "voice_assistant") and self.app.voice_assistant:
                            self.app.voice_assistant.apply_server_settings(
                                wake_phrase=wk,
                                enabled=bool(vae),
                            )
                        if hasattr(self.app, "_sync_voice_assistant_state"):
                            Clock.schedule_once(lambda _dt: self.app._sync_voice_assistant_state(), 0)
                    except Exception:
                        pass

                    try:
                        sv = settings.get("assistant_speech_volume", 85)
                        if isinstance(sv, str):
                            sv = int(float(sv.strip()))
                        else:
                            sv = int(sv)
                        sv = max(0, min(100, sv))
                        self.app.assistant_speech_volume = sv
                        self.speech_volume_item.subtitle_label.text = f'{sv}%'
                    except Exception:
                        pass

                    wifi_ok = bool(info.get('wifi_ssid'))
                    privacy = getattr(self.app, 'privacy_mode', False)
                    self.update_footer(
                        wifi_ok=wifi_ok,
                        free_gb=sf,
                        privacy_mode=privacy,
                        wired_lan_ok=linux_ethernet_ready(),
                    )

                Clock.schedule_once(_update, 0)
            except Exception:
                pass

        run_async(_fetch())

    # ------------------------------------------------------------------
    # Device name info dialog
    # ------------------------------------------------------------------
    def _show_device_name_dialog(self):
        dialog = ModalDialog(
            title='Device Name',
            message=(f'To change your device name,\n'
                     f'visit {DASHBOARD_URL} on your\n'
                     f'phone or laptop.'),
            confirm_text='OK',
            cancel_text='',
        )
        self.add_widget(dialog)

    # ------------------------------------------------------------------
    # Privacy toggle
    # ------------------------------------------------------------------
    def _on_privacy_toggled(self, active):
        self.app.privacy_mode = active
        async def _save():
            try:
                await self.backend.update_settings({'privacy_mode': active})
            except Exception:
                pass
        run_async(_save())

    def _on_auto_record_toggled(self, active):
        self.app.auto_record = active
        async def _save():
            try:
                await self.backend.update_settings({'auto_record': active})
            except Exception:
                pass
        run_async(_save())

    def _on_voice_assistant_enabled_toggled(self, active):
        self.app.voice_assistant_enabled = bool(active)
        if not active:
            self.app._voice_cloud_qa_budget = 0
            self.app._realtime_launch_permitted = False
        if hasattr(self.app, "voice_assistant") and self.app.voice_assistant:
            self.app.voice_assistant.apply_server_settings(enabled=bool(active))
            if active:
                self.app.voice_assistant.start()
        if hasattr(self.app, "_sync_voice_assistant_state"):
            self.app._sync_voice_assistant_state()

        async def _save():
            try:
                await self.backend.update_settings({"voice_assistant_enabled": active})
            except Exception:
                pass

        run_async(_save())

    def _on_voice_realtime_toggled(self, active):
        self.app.voice_realtime_assistant = bool(active)
        if not active and hasattr(self.app, "_realtime_launch_permitted"):
            self.app._realtime_launch_permitted = False

        async def _save():
            try:
                await self.backend.update_settings({'voice_realtime_assistant': active})
            except Exception:
                pass

        run_async(_save())

    def _show_wake_phrase_dialog(self):
        dialog = TextInputDialog(
            title='Wake phrase',
            message='Phrase to wake the assistant (spoken naturally, e.g. hey buddy).',
            initial_value=self.wake_phrase_item.subtitle_label.text or 'hey buddy',
            placeholder='hey buddy',
            on_confirm=self._apply_wake_phrase,
        )
        self.add_widget(dialog)

    def _apply_wake_phrase(self, value: str):
        wk = (value or '').strip().lower() or 'hey buddy'
        self.wake_phrase_item.subtitle_label.text = wk
        disp = wk[:1].upper() + wk[1:] if wk else 'Hey buddy'
        self.app.voice_wake_phrase_display = disp
        if hasattr(self.app, "voice_assistant") and self.app.voice_assistant:
            self.app.voice_assistant.apply_server_settings(wake_phrase=wk)
        if hasattr(self.app, "_sync_voice_assistant_state"):
            self.app._sync_voice_assistant_state()

        async def _save():
            try:
                await self.backend.update_settings({'voice_wake_phrase': wk})
            except Exception:
                pass

        run_async(_save())

    # ------------------------------------------------------------------
    # Restart dialog
    # ------------------------------------------------------------------
    def _show_unpair_account_dialog(self):
        dialog = ModalDialog(
            title='Unpair this device?',
            message=('This device will disconnect from your MeetingBox\n'
                     'account. Gmail stays linked in the web dashboard.\n'
                     'You will enter a new pairing code to reconnect.'),
            confirm_text='UNPAIR',
            cancel_text='CANCEL',
            danger=True,
            border_color=COLORS['red'],
            on_confirm=self._execute_unpair_account,
        )
        self.add_widget(dialog)

    def _execute_unpair_account(self):
        async def _run():
            try:
                await self.backend.unpair_self()
            except Exception:
                pass
            Clock.schedule_once(
                lambda _dt: self.app.on_account_unpaired(remote=False), 0)
        run_async(_run())

    def _show_restart_dialog(self):
        dialog = ModalDialog(
            title='Restart Device?',
            message='The device will restart and be ready\nto use again in about 30 seconds.',
            confirm_text='RESTART',
            cancel_text='CANCEL',
            on_confirm=self._do_restart,
        )
        self.add_widget(dialog)

    def _do_restart(self):
        """Local reboot first (nsenter helper in Docker, systemctl on bare metal); API as fallback."""

        async def _restart():
            local_ok = request_system_reboot()
            api_ok = False
            if not local_ok:
                try:
                    resp = await self.backend.update_settings({'action': 'restart'})
                    api_ok = bool(resp.get('host_reboot_initiated'))
                except Exception as e:
                    logger.warning('restart API fallback failed: %s', e)
            if not local_ok and not api_ok:
                Clock.schedule_once(lambda *_: self._show_power_error('restart'), 0)

        fut = run_async(_restart())
        if fut is None:
            Clock.schedule_once(lambda *_: self._show_power_error('restart'), 0)

    def _show_power_error(self, op: str):
        if op == 'restart':
            title = 'Restart failed'
            message = (
                'This device could not restart automatically. Power-cycle it or '
                'ask your admin to allow systemctl reboot or passwordless sudo '
                'for the MeetingBox user.'
            )
        else:
            title = 'Power off failed'
            message = (
                'Could not shut down automatically. Hold the power button or '
                'unplug the device. Your admin may need to allow systemctl '
                'poweroff or passwordless sudo.'
            )
        self.add_widget(ModalDialog(
            title=title,
            message=message,
            confirm_text='OK',
            cancel_text='',
        ))

    def _show_poweroff_dialog(self):
        dialog = ModalDialog(
            title='Power Off?',
            message='The device will turn off completely.\nUnplug to start again if it does not wake on LAN.',
            confirm_text='POWER OFF',
            cancel_text='CANCEL',
            danger=True,
            border_color=COLORS['red'],
            on_confirm=self._do_poweroff,
        )
        self.add_widget(dialog)

    def _do_poweroff(self):
        async def _off():
            local_ok = request_system_poweroff()
            api_ok = False
            if not local_ok:
                try:
                    resp = await self.backend.update_settings({'action': 'poweroff'})
                    api_ok = bool(resp.get('host_poweroff_initiated'))
                except Exception as e:
                    logger.warning('poweroff API fallback failed: %s', e)
            if not local_ok and not api_ok:
                Clock.schedule_once(lambda *_: self._show_power_error('poweroff'), 0)

        fut = run_async(_off())
        if fut is None:
            Clock.schedule_once(lambda *_: self._show_power_error('poweroff'), 0)

    # ------------------------------------------------------------------
    # Factory reset dialog
    # ------------------------------------------------------------------
    def _show_factory_reset_dialog(self):
        dialog = ModalDialog(
            title='⚠  Factory Reset',
            message=('This will permanently delete:\n'
                     '• All recordings and transcripts\n'
                     '• All settings and configurations\n'
                     '• All connected integrations\n\n'
                     'This action cannot be undone.'),
            confirm_text='RESET',
            cancel_text='CANCEL',
            danger=True,
            border_color=COLORS['red'],
            on_confirm=self._do_factory_reset,
        )
        self.add_widget(dialog)

    def _do_factory_reset(self):
        # Second confirmation
        dialog2 = ModalDialog(
            title='Final Confirmation',
            message='Reset to factory settings?\nThis cannot be undone.',
            confirm_text='YES, RESET',
            cancel_text='CANCEL',
            danger=True,
            on_confirm=self._execute_factory_reset,
        )
        self.add_widget(dialog2)

    def _execute_factory_reset(self):
        async def _reset():
            try:
                await self.backend.update_settings({'action': 'factory_reset'})
                request_system_reboot()
                Clock.schedule_once(
                    lambda _dt: self.app.reenter_onboarding_after_remote_reset(), 0)
            except Exception:
                pass
        run_async(_reset())

    # ------------------------------------------------------------------
    # Weather location dialog
    # ------------------------------------------------------------------
    def _show_weather_location_dialog(self):
        """Modal text-input dialog to override the auto-detected city.

        Empty input → keep current; non-empty input → resolve via Open-Meteo
        geocoding (handled by ``WeatherClient.set_city``). A failure surfaces
        a follow-up dialog so users know we couldn't find that place.
        """
        wc = get_weather_client()
        cur = wc.location
        cur_city = (cur and cur.get('city')) or ''

        dialog = TextInputDialog(
            title='Weather Location',
            message=('Enter a city name (e.g. "Bangalore" or '
                     '"London, UK"). Leave blank to keep auto-detect.'),
            initial_value=cur_city,
            placeholder='City name',
            on_confirm=self._apply_weather_location,
        )
        self.add_widget(dialog)

    def _apply_weather_location(self, value: str):
        text = (value or '').strip()
        if not text:
            return  # treat empty as cancel — auto-detect stays in effect

        wc = get_weather_client()

        async def _resolve():
            resolved = await wc.set_city(text)
            if resolved is None:
                Clock.schedule_once(
                    lambda _dt: self._show_weather_resolve_failed(text), 0)
                return
            Clock.schedule_once(
                lambda _dt: self._show_weather_resolved(resolved), 0)

        run_async(_resolve())

    def _show_weather_resolved(self, loc: dict):
        self.weather_location_item.subtitle_label.text = (
            f"{loc.get('city', '?')}"
        )

    def _show_weather_resolve_failed(self, text: str):
        self.add_widget(ModalDialog(
            title='City not found',
            message=(f'Could not find weather data for "{text}".\n\n'
                     'Try the city name in English, or include the country '
                     '(e.g. "Bengaluru, IN").'),
            confirm_text='OK',
            cancel_text='',
        ))
