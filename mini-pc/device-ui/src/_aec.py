"""Speex-based acoustic echo cancellation (AEC) for the realtime voice path.

The OpenAI Realtime audio that we send to `aplay` is also picked up by the
USB microphone as a delayed, attenuated, room-coloured copy. Without AEC
the server VAD treats this echo as user speech and triggers barge-in,
killing playback mid-sentence. With AEC the echo is subtracted from the
mic before we forward it to the server, so the user can actually
interrupt the agent with their own voice without spurious self-trips.

We bind libspeexdsp via ctypes (no Python wheel build needed). The
library only has to be present at runtime as `libspeexdsp.so.1`
(provided by the Debian package `libspeexdsp1`).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)


def _bundled_lib_dirs() -> list[str]:
    """Directories we ship the speex DSP library in, for source and frozen runs.

    On Linux the appliance gets libspeexdsp from the OS package (libspeexdsp1);
    on the Windows/macOS desktop port there's no system copy, so we vendor a
    self-contained DLL/dylib next to the code and into the PyInstaller bundle.
    """
    dirs: list[str] = []
    # PyInstaller one-folder: binaries land in sys._MEIPASS (the _internal dir).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
        dirs.append(os.path.join(meipass, "vendor", "windows"))
    here = os.path.dirname(os.path.abspath(__file__))
    dirs.append(os.path.join(here, "vendor", "windows"))
    dirs.append(here)
    return dirs


def _load_libspeexdsp() -> ctypes.CDLL | None:
    candidates: list[str] = []

    # 1) Vendored copy bundled with the app (required on Windows/macOS where no
    #    system libspeexdsp exists). Try absolute paths first so we never depend
    #    on PATH / the loader search order.
    if sys.platform.startswith("win"):
        lib_names = ("libspeexdsp.dll", "libspeexdsp-1.dll", "speexdsp.dll")
    elif sys.platform == "darwin":
        lib_names = ("libspeexdsp.dylib", "libspeexdsp.1.dylib")
    else:
        lib_names = ("libspeexdsp.so.1", "libspeexdsp.so")
    for d in _bundled_lib_dirs():
        for name in lib_names:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                candidates.append(p)

    # 2) System-installed copy (Linux appliance / dev machines with it on PATH).
    found = ctypes.util.find_library("speexdsp")
    if found:
        candidates.append(found)
    candidates.extend([
        "libspeexdsp.so.1",
        "libspeexdsp.so",
        "/usr/lib/x86_64-linux-gnu/libspeexdsp.so.1",
        "/usr/lib/aarch64-linux-gnu/libspeexdsp.so.1",
    ])

    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
            logger.info("libspeexdsp loaded from %s", name)
            return lib
        except OSError:
            continue
    return None


_lib = _load_libspeexdsp()

if _lib is not None:
    _lib.speex_echo_state_init.restype = ctypes.c_void_p
    _lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    _lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
    _lib.speex_echo_cancellation.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int16),
        ctypes.POINTER(ctypes.c_int16),
    ]
    _lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    _lib.speex_echo_ctl.restype = ctypes.c_int

    _lib.speex_preprocess_state_init.restype = ctypes.c_void_p
    _lib.speex_preprocess_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
    _lib.speex_preprocess_state_destroy.argtypes = [ctypes.c_void_p]
    _lib.speex_preprocess_run.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16)]
    _lib.speex_preprocess_run.restype = ctypes.c_int
    _lib.speex_preprocess_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    _lib.speex_preprocess_ctl.restype = ctypes.c_int


# Speex echo ctl IDs (from speex/speex_echo.h)
SPEEX_ECHO_SET_SAMPLING_RATE = 24

# Speex preprocess ctl IDs (from speex/speex_preprocess.h)
SPEEX_PREPROCESS_SET_DENOISE = 0
SPEEX_PREPROCESS_SET_AGC = 2
SPEEX_PREPROCESS_SET_ECHO_STATE = 24
SPEEX_PREPROCESS_SET_ECHO_SUPPRESS = 28
SPEEX_PREPROCESS_SET_ECHO_SUPPRESS_ACTIVE = 30


def is_available() -> bool:
    return _lib is not None


class SpeexAEC:
    """Echo canceller + residual-echo / noise suppressor for PCM16 mono frames.

    Both near-end (mic) and far-end (playback reference) must be at the same
    sample rate. We use 24 kHz to match what we forward to OpenAI and what
    we feed to `aplay`.

    `frame_size` and `filter_length` are in *samples*. The filter length sets
    the echo tail the canceller can model — set it to cover the worst-case
    speaker→air→mic latency plus reverberation (~200 ms is plenty for a
    desktop puck speaker).
    """

    def __init__(
        self,
        frame_size: int = 480,           # 20 ms at 24 kHz
        filter_length: int = 4800,       # 200 ms tail
        sample_rate: int = 24000,
    ) -> None:
        if _lib is None:
            raise RuntimeError("libspeexdsp is not available")

        self.frame_size = int(frame_size)
        self.sample_rate = int(sample_rate)
        self._lock = threading.Lock()

        self._echo_state = _lib.speex_echo_state_init(self.frame_size, int(filter_length))
        if not self._echo_state:
            raise RuntimeError("speex_echo_state_init failed")

        sr = ctypes.c_int(self.sample_rate)
        _lib.speex_echo_ctl(
            self._echo_state, SPEEX_ECHO_SET_SAMPLING_RATE, ctypes.byref(sr)
        )

        self._pre_state = _lib.speex_preprocess_state_init(self.frame_size, self.sample_rate)
        if not self._pre_state:
            _lib.speex_echo_state_destroy(self._echo_state)
            self._echo_state = None
            raise RuntimeError("speex_preprocess_state_init failed")

        # Denoise on; AGC off (Realtime API already normalises levels).
        on = ctypes.c_int(1)
        off = ctypes.c_int(0)
        _lib.speex_preprocess_ctl(
            self._pre_state, SPEEX_PREPROCESS_SET_DENOISE, ctypes.byref(on)
        )
        _lib.speex_preprocess_ctl(
            self._pre_state, SPEEX_PREPROCESS_SET_AGC, ctypes.byref(off)
        )

        # Tie preprocessor to the echo state so residual-echo suppression runs.
        _lib.speex_preprocess_ctl(
            self._pre_state, SPEEX_PREPROCESS_SET_ECHO_STATE, self._echo_state
        )
        # Aggressive residual suppression — value is in dB (negative attenuation).
        suppress = ctypes.c_int(-45)
        suppress_active = ctypes.c_int(-55)
        _lib.speex_preprocess_ctl(
            self._pre_state,
            SPEEX_PREPROCESS_SET_ECHO_SUPPRESS,
            ctypes.byref(suppress),
        )
        _lib.speex_preprocess_ctl(
            self._pre_state,
            SPEEX_PREPROCESS_SET_ECHO_SUPPRESS_ACTIVE,
            ctypes.byref(suppress_active),
        )

    def cancel(self, near: bytes, far: bytes) -> bytes:
        """Process one frame. `near` and `far` must each be `frame_size * 2` bytes."""
        nbytes = self.frame_size * 2
        if len(near) != nbytes or len(far) != nbytes:
            raise ValueError(
                f"AEC frame must be {nbytes} bytes (got near={len(near)}, far={len(far)})"
            )
        FrameT = ctypes.c_int16 * self.frame_size
        out = FrameT()
        near_arr = FrameT.from_buffer_copy(near)
        far_arr = FrameT.from_buffer_copy(far)
        with self._lock:
            if self._echo_state is None or self._pre_state is None:
                return near
            _lib.speex_echo_cancellation(self._echo_state, near_arr, far_arr, out)
            _lib.speex_preprocess_run(self._pre_state, out)
        return bytes(out)

    def close(self) -> None:
        with self._lock:
            if self._pre_state is not None:
                _lib.speex_preprocess_state_destroy(self._pre_state)
                self._pre_state = None
            if self._echo_state is not None:
                _lib.speex_echo_state_destroy(self._echo_state)
                self._echo_state = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
