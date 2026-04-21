"""Microphone test screen with local capture (sounddevice) plus backend/WS level stream."""

import logging
from collections import deque
from time import time

from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, RoundedRectangle
from kivy.clock import Clock

from async_helper import run_async
from screens.base_screen import BaseScreen
from components.status_bar import StatusBar
from config import (
    AUDIO_INPUT_DEVICE_INDEX,
    AUDIO_INPUT_DEVICE_NAME,
    COLORS,
    FONT_SIZES,
)

logger = logging.getLogger(__name__)

try:
    import numpy as np
    import sounddevice as sd
except ImportError:
    np = None
    sd = None


class _TestWaveform(Widget):
    """Mirrored bar waveform matching recording screen style."""

    NUM_BARS = 28
    BAR_WIDTH = 4
    BAR_SPACING = 4
    MAX_BAR_HEIGHT = 100

    def __init__(self, **kwargs):
        kwargs.setdefault('size_hint', (1, None))
        kwargs.setdefault('height', 200)
        super().__init__(**kwargs)
        self._levels = [2] * self.NUM_BARS
        self.bind(pos=self._draw, size=self._draw)

    def set_levels(self, levels: list):
        self._levels = levels
        self._draw()

    def _draw(self, *_args):
        self.canvas.clear()
        total_w = self.NUM_BARS * (self.BAR_WIDTH + self.BAR_SPACING)
        start_x = self.x + (self.width - total_w) / 2
        mid_y = self.center_y

        with self.canvas:
            for i, h in enumerate(self._levels):
                Color(*COLORS['blue'])
                bx = start_x + i * (self.BAR_WIDTH + self.BAR_SPACING)
                RoundedRectangle(
                    pos=(bx, mid_y - max(1, h / 2)),
                    size=(self.BAR_WIDTH, max(2, h)),
                    radius=[2],
                )


class MicTestScreen(BaseScreen):
    """Microphone test — levels from local PyAudio/sounddevice and/or meetingbox audio service via WS."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._wave_event = None
        self._rms_history = deque(maxlen=_TestWaveform.NUM_BARS)
        self._last_level_ts = 0.0
        self._local_stream = None
        self._enter_ts = 0.0
        self._got_level = False
        for _ in range(_TestWaveform.NUM_BARS):
            self._rms_history.append(0.0)
        self._build_ui()

    def _build_ui(self):
        root = BoxLayout(orientation='vertical')
        self.make_dark_bg(root)

        self.status_bar = StatusBar(
            status_text='Microphone Test',
            device_name='Microphone Test',
            back_button=True,
            on_back=self.go_back,
            show_settings=False,
        )
        root.add_widget(self.status_bar)

        root.add_widget(Widget(size_hint=(1, 0.06)))

        instr = Label(
            text='Speak to test your microphone',
            font_size=self.suf(FONT_SIZES['medium']),
            color=COLORS['white'],
            halign='center',
            size_hint=(1, None), height=28,
        )
        instr.bind(size=instr.setter('text_size'))
        root.add_widget(instr)

        root.add_widget(Widget(size_hint=(1, 0.05)))

        self.waveform = _TestWaveform()
        root.add_widget(self.waveform)

        root.add_widget(Widget(size_hint=(1, 0.05)))

        self.level_label = Label(
            text='Detecting…',
            font_size=self.suf(FONT_SIZES['small'] + 2),
            bold=True,
            color=COLORS['gray_500'],
            halign='center',
            size_hint=(1, None), height=24,
        )
        root.add_widget(self.level_label)

        root.add_widget(Widget())

        footer = self.build_footer()
        root.add_widget(footer)

        self.add_widget(root)

    def _resolve_sounddevice_input_device(self):
        """PortAudio device index, or None for host default."""
        if sd is None:
            return None
        idx_s = (AUDIO_INPUT_DEVICE_INDEX or "").strip()
        if idx_s.isdigit():
            return int(idx_s)
        name_sub = (AUDIO_INPUT_DEVICE_NAME or "").strip()
        if name_sub:
            low = name_sub.lower()
            for i, dev in enumerate(sd.query_devices()):
                if int(dev.get("max_input_channels") or 0) > 0 and low in (
                    dev.get("name") or ""
                ).lower():
                    return i
        return None

    def _samplerates_to_try(self, device_id):
        out = []
        if device_id is not None:
            try:
                info = sd.query_devices(device_id)
                dflt = int(float(info.get("default_samplerate") or 0))
                if dflt > 0:
                    out.append(dflt)
            except Exception:
                pass
        for sr in (48000, 44100, 32000, 22050, 16000, 8000):
            if sr not in out:
                out.append(sr)
        return out

    def _show_mic_error(self, message: str):
        self.level_label.text = message
        self.level_label.color = COLORS["red"]

    def on_enter(self):
        self._enter_ts = time()
        self._got_level = False
        self._last_level_ts = 0.0
        self.level_label.text = 'Starting microphone test...'
        self.level_label.color = COLORS['gray_400']

        if sd is not None and np is not None:
            Clock.schedule_once(lambda _dt: self._open_local_input_stream(), 0)
        else:
            logger.warning("Mic test: sounddevice/numpy unavailable — using backend WS levels only")

        # Always start backend mic test + tick timer so bars work via
        # WebSocket mic_test_level events even without local sounddevice.
        run_async(self._notify_backend_start())
        if self._wave_event:
            self._wave_event.cancel()
        self._wave_event = Clock.schedule_interval(self._tick, 0.1)

    def on_leave(self):
        if self._wave_event:
            self._wave_event.cancel()
            self._wave_event = None
        self._close_local_stream()
        run_async(self._notify_backend_stop())

    def _open_local_input_stream(self):
        if sd is None or np is None:
            return
        self._close_local_stream()
        device_id = self._resolve_sounddevice_input_device()

        def callback(indata, frames, t_info, status):
            if status and str(status):
                logger.debug("sounddevice status: %s", status)
            try:
                block = np.asarray(indata, dtype=np.float64).reshape(-1)
                if block.size == 0:
                    return
                rms = float(np.sqrt(np.mean(np.square(block))))
                # float32 ~[-1,1]; gain bumped slightly for quiet USB mics
                level = min(1.0, rms * 24.0)
                Clock.schedule_once(lambda dt, lv=level: self._apply_level(lv), 0)
            except Exception:
                logger.exception("Mic test: callback error")

        last_err = None
        for sr in self._samplerates_to_try(device_id):
            try:
                kwargs = dict(
                    channels=1,
                    samplerate=sr,
                    blocksize=1024,
                    dtype="float32",
                    callback=callback,
                )
                if device_id is not None:
                    kwargs["device"] = device_id
                self._local_stream = sd.InputStream(**kwargs)
                self._local_stream.start()
                logger.info(
                    "Mic test: local stream started (device=%s samplerate=%s)",
                    device_id,
                    sr,
                )
                return
            except Exception as e:
                last_err = e
                self._close_local_stream()
                continue

        logger.warning(
            "Mic test: local capture failed (%s) — check /dev/snd, audio group, AUDIO_INPUT_DEVICE_*",
            last_err,
        )
        Clock.schedule_once(
            lambda *_: self._show_mic_error(
                f"Cannot open microphone ({last_err}). "
                "Rebuild UI with /dev/snd and retry."
            ),
            0,
        )

    def _close_local_stream(self):
        if self._local_stream is not None:
            try:
                self._local_stream.stop()
                self._local_stream.close()
            except Exception:
                pass
            self._local_stream = None

    async def _notify_backend_start(self):
        try:
            await self.backend.start_mic_test()
        except Exception as e:
            logger.debug("mic-test/start HTTP optional: %s", e)

    async def _notify_backend_stop(self):
        try:
            await self.backend.stop_mic_test()
        except Exception:
            pass

    def _apply_level(self, level: float):
        self.on_mic_test_level(level)

    def on_mic_test_level(self, level: float):
        gated = 0.0 if level < 0.006 else min(1.0, float(level))
        self._rms_history.append(gated)
        self._last_level_ts = time()
        self._got_level = True

    def _tick(self, _dt):
        if self._last_level_ts and (time() - self._last_level_ts > 0.25):
            self._rms_history = deque([v * 0.82 for v in self._rms_history], maxlen=_TestWaveform.NUM_BARS)

        levels = [max(2, int(v * _TestWaveform.MAX_BAR_HEIGHT))
                  for v in self._rms_history]
        self.waveform.set_levels(levels)

        elapsed = time() - self._enter_ts
        if not self._got_level and elapsed > 2.5:
            self.level_label.text = 'No microphone input detected'
            self.level_label.color = COLORS['red']
            return

        if not self._got_level:
            self.level_label.text = 'Listening…'
            self.level_label.color = COLORS['gray_400']
            return

        peak = max(self._rms_history) if self._rms_history else 0.0
        if peak > 0.15:
            self.level_label.text = 'Input Level: Good'
            self.level_label.color = COLORS['green']
        elif peak > 0.03:
            self.level_label.text = 'Input Level: Low'
            self.level_label.color = COLORS['yellow']
        else:
            self.level_label.text = 'Input Level: No Sound'
            self.level_label.color = COLORS['gray_500']
