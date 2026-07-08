"""Windows / macOS local text-to-speech fallback.

The Linux appliance speaks local replies through ``espeak-ng`` / ``piper`` /
``mimic3`` (all ALSA + ``aplay`` based). None of those exist on Windows, so we
fall back to the OS speech engine via :mod:`pyttsx3`:

* Windows → SAPI5 voices (built in, no install needed)
* macOS   → NSSpeechSynthesizer

A fresh engine is created per utterance. Reusing a single ``pyttsx3`` engine
across ``runAndWait()`` calls is fragile (the SAPI run loop can dead-lock or
raise "run loop already started"), and local replies are short and infrequent,
so the per-call cost is negligible. The SAPI5 driver uses COM, which must be
initialised on whichever (non-main) thread drives playback — we do that
defensively and ignore failures.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_SUPPORTED = sys.platform.startswith("win") or sys.platform == "darwin"


def is_available() -> bool:
    """True when pyttsx3 can be imported on a supported desktop OS."""
    if not _SUPPORTED:
        return False
    try:
        import pyttsx3  # noqa: F401
        return True
    except Exception:
        return False


def _co_initialize():
    """Initialise COM for SAPI on the current thread (Windows only)."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import comtypes  # type: ignore

        comtypes.CoInitialize()
        return comtypes
    except Exception:
        return None


def speak(text: str, volume: int = 85, rate: int | None = None) -> bool:
    """Speak ``text`` synchronously. Returns True on success.

    ``volume`` is 0–100 (mapped to pyttsx3's 0.0–1.0). ``rate`` is words per
    minute; when ``None`` the engine default is used. Any failure returns
    False so the caller can fall through to another engine.
    """
    phrase = (text or "").strip()
    if not phrase or not _SUPPORTED:
        return False

    com = _co_initialize()
    engine = None
    try:
        import pyttsx3

        engine = pyttsx3.init()
        try:
            vol = max(0.0, min(1.0, (int(volume) if volume is not None else 85) / 100.0))
        except (TypeError, ValueError):
            vol = 0.85
        engine.setProperty("volume", vol)
        if rate is not None:
            try:
                engine.setProperty("rate", int(rate))
            except Exception:
                pass
        engine.say(phrase)
        engine.runAndWait()
        return True
    except Exception as exc:  # noqa: BLE001 - TTS is best-effort
        logger.debug("Windows TTS (pyttsx3) failed: %s", exc)
        return False
    finally:
        try:
            if engine is not None:
                engine.stop()
        except Exception:
            pass
        if com is not None:
            try:
                com.CoUninitialize()
            except Exception:
                pass
