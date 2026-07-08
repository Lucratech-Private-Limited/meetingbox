"""Windows Voice Capture DSP (CWMAudioAEC) — OS-grade acoustic echo cancellation.

This is the desktop equivalent of the hardware/OS echo cancellation that phone
voice assistants (ChatGPT, Gemini) rely on. Instead of running our own software
canceller (Speex) over a manually-aligned far-end reference, we let the Windows
"Voice Capture DSP" media object capture the microphone AND reference the system
render itself, returning a clean, echo-cancelled, noise-suppressed 16 kHz mono
stream. With the assistant's own voice removed at the source, the realtime
session can stream continuously and let the server VAD handle barge-in — true
full-duplex.

Implementation notes:
  * SINGLE_CHANNEL_AEC + source mode: the DMO opens the default communications
    mic + render endpoints and does AEC/NS/AGC internally.
  * Output is 16 kHz / 16-bit / mono PCM. Callers resample to their pipeline
    rate (the realtime session captures at 24 kHz).
  * Windows-only and entirely optional: ``is_available()`` is False elsewhere
    and every entry point degrades gracefully so non-Windows builds and missing
    COM never break import or playback.

A standalone feasibility probe lives at packaging/windows/probe_win_aec.py.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# COM is imported lazily inside the capture thread so importing this module is
# always safe (and free) on non-Windows / headless builds.
_IS_WIN = sys.platform.startswith("win")

# DSP native output format.
_DSP_RATE = 16000
_DSP_CHANNELS = 1
_DSP_BYTES_PER_SAMPLE = 2


def is_available() -> bool:
    """True only when the OS AEC can plausibly be driven (Windows + comtypes)."""
    if not _IS_WIN:
        return False
    try:
        import comtypes  # noqa: F401
        return True
    except Exception:
        return False


class WindowsEchoCanceller:
    """Background capturer that emits OS-echo-cancelled mono PCM16 @ 16 kHz.

    Usage::

        aec = WindowsEchoCanceller(on_frames=lambda pcm16: ...)
        if aec.start():
            ...  # frames arrive on the callback thread until stop()
        aec.stop()

    The callback runs on the internal capture thread; keep it cheap (a queue
    put). ``output_rate`` reports the native DSP rate so callers know how to
    resample.
    """

    output_rate = _DSP_RATE
    output_channels = _DSP_CHANNELS

    def __init__(
        self,
        on_frames: Optional[Callable[[bytes], None]] = None,
        frame_ms: int = 20,
    ) -> None:
        self._on_frames = on_frames
        self._frame_ms = max(5, int(frame_ms))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._started = False
        self._start_ok = False
        self._keepalive = None  # continuous silent render stream (drives DSP clock)
        self.device_name: str = "Windows default (communications)"
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    def start(self, init_timeout_s: float = 3.0) -> bool:
        """Start capture. Returns True only once the DSP is producing audio."""
        if not is_available():
            self.last_error = "OS AEC unavailable (not Windows / comtypes missing)"
            return False
        if self._thread is not None:
            return self._start_ok
        # The DSP's processing pipeline is driven by the RENDER (playback) clock:
        # in source mode it emits NO captured frames unless something is playing.
        # Between assistant turns nothing renders, so the mic would go dead and
        # the user couldn't be heard. Keep a continuous silent render stream open
        # for the whole session so the DSP keeps clocking and capturing.
        self._open_keepalive_render()
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="rtv-os-aec", daemon=True
        )
        self._thread.start()
        # Wait until the thread has finished COM init (or failed).
        self._ready.wait(timeout=init_timeout_s)
        self._started = True
        if not self._start_ok:
            self._close_keepalive_render()
        return self._start_ok

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._close_keepalive_render()
        self._started = False

    # ------------------------------------------------------------------
    def _open_keepalive_render(self) -> None:
        if self._keepalive is not None:
            return
        try:
            import sounddevice as sd

            def _cb(outdata, frames, time_info, status):  # noqa: ANN001
                outdata.fill(0)

            # Default output endpoint (same one the DSP references and the app
            # plays through). 48 kHz mono shared-mode silence; WASAPI mixes it
            # with the assistant's real playback when it speaks.
            stream = sd.OutputStream(
                samplerate=48000, channels=1, dtype="int16", callback=_cb
            )
            stream.start()
            self._keepalive = stream
            logger.info("Realtime AEC: keep-alive render stream started (drives DSP clock)")
        except Exception:
            self._keepalive = None
            logger.debug("OS AEC keep-alive render failed", exc_info=True)

    def _close_keepalive_render(self) -> None:
        s = self._keepalive
        self._keepalive = None
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            import ctypes as C
            import comtypes
            from windows_aec_dmo import (
                CLSID_CWMAudioAEC,
                CLSCTX_INPROC_SERVER,
                DMO_OUTPUT_DATA_BUFFER,
                IMediaBuffer,
                IMediaObject,
                IPropertyStore,
                MediaBuffer,
                S_FALSE,
                S_OK,
                make_output_mediatype,
                set_prop_i4,
                PID_DEVICE_INDEXES,
                PID_DMO_SOURCE_MODE,
                PID_SYSTEM_MODE,
                SINGLE_CHANNEL_AEC,
                VT_BOOL,
            )
        except Exception as e:  # pragma: no cover
            self.last_error = f"COM import failed: {e}"
            logger.debug("OS AEC import failed", exc_info=True)
            self._start_ok = False
            self._ready.set()
            return

        com_inited = False
        mo = None
        try:
            comtypes.CoInitialize()
            com_inited = True
            mo = comtypes.CoCreateInstance(
                CLSID_CWMAudioAEC,
                interface=IMediaObject,
                clsctx=CLSCTX_INPROC_SERVER,
            )
            store = mo.QueryInterface(IPropertyStore)
            set_prop_i4(store, PID_SYSTEM_MODE, SINGLE_CHANNEL_AEC)
            set_prop_i4(store, PID_DMO_SOURCE_MODE, 0xFFFF, vt=VT_BOOL)  # VARIANT_TRUE
            dev = (0xFFFF << 16) | 0xFFFF  # default render + default capture
            if dev >= 0x80000000:
                dev -= 0x100000000
            set_prop_i4(store, PID_DEVICE_INDEXES, dev)
            try:
                store.Commit()  # commonly E_NOTIMPL; settings apply immediately
            except Exception:
                pass

            mt = make_output_mediatype(_DSP_RATE)
            mo.SetOutputType(0, C.pointer(mt), 0)
            mo.AllocateStreamingResources()

            mbuf = MediaBuffer(_DSP_RATE * _DSP_BYTES_PER_SAMPLE)  # ~1s capacity
            mbuf_ptr = mbuf.QueryInterface(IMediaBuffer)
            from ctypes import byref, c_void_p

            # The pipeline is live as soon as streaming resources are allocated.
            # Readiness must NOT wait for the first audio frame: in source mode
            # the DSP emits nothing during silence, so gating on a frame would
            # spuriously time out whenever the room is quiet at session start.
            logger.info(
                "Realtime AEC: Windows Voice Capture DSP started "
                "(SINGLE_CHANNEL_AEC, source mode, 16k mono)"
            )
            self._start_ok = True
            self._ready.set()

            idle_sleep = 0.004
            while not self._stop.is_set():
                mbuf.reset()
                odb = DMO_OUTPUT_DATA_BUFFER()
                odb.pBuffer = C.cast(mbuf_ptr, c_void_p)
                odb.dwStatus = 0
                odb.rtTimestamp = 0
                odb.rtTimelength = 0
                try:
                    mo.ProcessOutput(0, 1, byref(odb))
                except comtypes.COMError as ce:
                    if ce.hresult not in (S_OK, S_FALSE):
                        self.last_error = f"ProcessOutput HRESULT {ce.hresult:#x}"
                        logger.debug("OS AEC ProcessOutput error", exc_info=True)
                        break
                chunk = mbuf.read_bytes()
                if chunk:
                    cb = self._on_frames
                    if cb is not None:
                        try:
                            cb(chunk)
                        except Exception:
                            logger.debug("OS AEC on_frames raised", exc_info=True)
                else:
                    # No data ready yet; brief sleep keeps CPU low without
                    # adding meaningful capture latency.
                    time.sleep(idle_sleep)

            try:
                mo.FreeStreamingResources()
            except Exception:
                pass
        except Exception as e:  # pragma: no cover
            self.last_error = str(e)
            logger.debug("OS AEC run failed", exc_info=True)
        finally:
            # Unblock start() if we never produced a frame.
            if not self._ready.is_set():
                self._start_ok = False
                self._ready.set()
            mo = None
            if com_inited:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass
