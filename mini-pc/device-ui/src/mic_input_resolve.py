"""
Pick the PortAudio (sounddevice) capture device index for the device UI.

When AUDIO_INPUT_DEVICE_INDEX / AUDIO_INPUT_DEVICE_NAME are unset, we prefer an
external USB-class microphone so built-in laptop/tablet mics are not used by default.
"""

from __future__ import annotations

import logging
import os

from config import AUDIO_INPUT_DEVICE_INDEX, AUDIO_INPUT_DEVICE_NAME

logger = logging.getLogger(__name__)


def _usb_autopick_disabled() -> bool:
    v = (os.getenv("MEETINGBOX_AUTO_SELECT_USB_MIC") or "1").strip().lower()
    return v in ("0", "false", "no", "off")


def _usb_like_name(name: str) -> bool:
    low = (name or "").lower()
    if "usb" in low:
        return True
    # Some Class-2 listings use UAC rather than the word "USB"
    if "uac" in low:
        return True
    return False


def _built_in_like_name(name: str) -> bool:
    low = (name or "").lower()
    return any(
        k in low
        for k in (
            "built-in",
            "builtin",
            "internal",
            "integrated",
            "array mic",
            "mic array",
            "pdm",
            "dmic",
            "onboard",
        )
    )


def _capture_devices(sd) -> list[tuple[int, dict]]:
    out: list[tuple[int, dict]] = []
    for idx, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        out.append((idx, dev))
    return out


def _match_name_substring(sd, name_sub: str) -> int | None:
    try:
        for idx, dev in _capture_devices(sd):
            if name_sub in ((dev.get("name") or "").lower()):
                return idx
    except Exception:
        logger.exception("Audio device enumeration failed (name=%r)", name_sub)
    return None


def _first_usb_like_capture(sd) -> int | None:
    try:
        for idx, dev in _capture_devices(sd):
            nm = dev.get("name") or ""
            if _usb_like_name(nm):
                logger.info("Auto-selected USB-like capture device [%s]: %s", idx, nm)
                return idx
        logger.debug("No USB-like capture device found")
    except Exception:
        logger.exception("USB-like capture device search failed")
    return None


def _first_built_in_capture(sd) -> int | None:
    try:
        for idx, dev in _capture_devices(sd):
            nm = dev.get("name") or ""
            if _built_in_like_name(nm):
                logger.info("Auto-selected built-in capture device [%s]: %s", idx, nm)
                return idx
    except Exception:
        logger.exception("Built-in capture device search failed")
    return None


def _first_any_capture(sd) -> int | None:
    try:
        devices = _capture_devices(sd)
        if devices:
            idx, dev = devices[0]
            logger.info(
                "Auto-selected first capture device [%s]: %s",
                idx,
                dev.get("name") or "",
            )
            return idx
    except Exception:
        logger.exception("Capture device fallback search failed")
    return None


def capture_device_fallback_candidates(sd, preferred: int | None) -> list[int | None]:
    """
    Candidate input devices to try when opening capture streams.
    Order:
      1) preferred (if provided)
      2) PortAudio default input index (if available)
      3) all other input-capable device indices
      4) None (let PortAudio host default resolve implicitly)
    """
    out: list[int | None] = []

    def _push(v: int | None) -> None:
        if v in out:
            return
        out.append(v)

    if preferred is not None:
        _push(preferred)

    try:
        inp_idx = sd.default.device[0]
        if isinstance(inp_idx, int) and inp_idx >= 0:
            _push(inp_idx)
    except Exception:
        pass

    try:
        for idx, _dev in _capture_devices(sd):
            _push(idx)
    except Exception:
        logger.debug("Could not enumerate capture fallback devices", exc_info=True)

    _push(None)
    return out


def resolve_sounddevice_capture_device_index(sd) -> int | None:
    """
    Return a PortAudio device index for input, or None to use the host default device.

    Precedence:
    1. AUDIO_INPUT_DEVICE_INDEX (integer)
    2. AUDIO_INPUT_DEVICE_NAME (substring match, case-insensitive)
    3. If MEETINGBOX_AUTO_SELECT_USB_MIC is not disabled: first capture device whose
       name suggests USB / UAC (see _usb_like_name)
    4. First built-in-like capture device
    5. First available capture device
    6. None — PortAudio default (only if enumeration failed)
    """
    if sd is None:
        return None

    idx_s = (AUDIO_INPUT_DEVICE_INDEX or "").strip()
    if idx_s.isdigit():
        return int(idx_s)

    name_sub = (AUDIO_INPUT_DEVICE_NAME or "").strip().lower()
    if name_sub:
        return _match_name_substring(sd, name_sub)

    if _usb_autopick_disabled():
        return None

    usb = _first_usb_like_capture(sd)
    if usb is not None:
        return usb

    built_in = _first_built_in_capture(sd)
    if built_in is not None:
        return built_in

    return _first_any_capture(sd)
