"""
Pick the PortAudio (sounddevice) capture device index for the device UI.

When AUDIO_INPUT_DEVICE_INDEX / AUDIO_INPUT_DEVICE_NAME are unset, we prefer an
external USB-class microphone so built-in laptop/tablet mics are not used by default.
"""

from __future__ import annotations

import logging
import os
import subprocess

from config import AUDIO_INPUT_DEVICE_INDEX, AUDIO_INPUT_DEVICE_NAME
from platform_compat import has_linux_audio_tools

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


def _bluetooth_like_name(name: str) -> bool:
    low = (name or "").lower()
    return any(k in low for k in (
        "bluetooth", "bluez", "a2dp", "hsp", "hfp",
        "headset", "hands-free", "hands free",
    ))


def _external_like_name(name: str) -> bool:
    """True for any non-built-in device: USB/UAC or Bluetooth."""
    return _usb_like_name(name) or _bluetooth_like_name(name)


def _is_combined_device(dev: dict) -> bool:
    """True when a PortAudio device exposes both input AND output channels (mic+speaker)."""
    return (
        int(dev.get("max_input_channels") or 0) > 0
        and int(dev.get("max_output_channels") or 0) > 0
    )


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


def _is_loopback_like_name(name: str) -> bool:
    """True for output-loopback "inputs" that capture system audio, not a mic.

    On Windows these enumerate as input-capable devices but actually return
    whatever is playing (the assistant's own voice), which the server then
    transcribes as "random words". Never auto-select them.
    """
    low = (name or "").lower()
    return any(
        k in low
        for k in (
            "stereo mix",
            "what u hear",
            "wave out",
            "loopback",
            "voicemeeter out",   # virtual-cable outputs used as inputs
            "cable output",
            "primary sound capture",
        )
    )


def _capture_devices(sd) -> list[tuple[int, dict]]:
    out: list[tuple[int, dict]] = []
    for idx, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        if _is_loopback_like_name(dev.get("name") or ""):
            logger.info(
                "Skipping loopback-style capture device [%s]: %s (captures system audio, not a mic)",
                idx, dev.get("name") or "",
            )
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


def _first_bluetooth_combined_capture(sd) -> int | None:
    """Return the first Bluetooth device that has BOTH input and output channels (mic+speaker)."""
    try:
        all_devs = list(enumerate(sd.query_devices()))
        for idx, dev in all_devs:
            nm = dev.get("name") or ""
            if _bluetooth_like_name(nm) and _is_combined_device(dev):
                logger.info(
                    "Auto-selected Bluetooth combined mic+speaker capture [%s]: %s", idx, nm
                )
                return idx
        logger.debug("No Bluetooth combined mic+speaker capture device found")
    except Exception:
        logger.exception("Bluetooth combined capture device search failed")
    return None


def _first_usb_combined_capture(sd) -> int | None:
    """Return the first USB device that has BOTH input and output channels (mic+speaker)."""
    try:
        all_devs = list(enumerate(sd.query_devices()))
        for idx, dev in all_devs:
            nm = dev.get("name") or ""
            if _usb_like_name(nm) and _is_combined_device(dev):
                logger.info(
                    "Auto-selected USB combined mic+speaker capture [%s]: %s", idx, nm
                )
                return idx
        logger.debug("No USB combined mic+speaker capture device found")
    except Exception:
        logger.exception("USB combined capture device search failed")
    return None


def _first_bluetooth_capture(sd) -> int | None:
    """Return the first Bluetooth input-capable device."""
    try:
        for idx, dev in _capture_devices(sd):
            nm = dev.get("name") or ""
            if _bluetooth_like_name(nm):
                logger.info("Auto-selected Bluetooth capture device [%s]: %s", idx, nm)
                return idx
        logger.debug("No Bluetooth capture device found")
    except Exception:
        logger.exception("Bluetooth capture device search failed")
    return None


# ---------------------------------------------------------------------------
# PulseAudio / PipeWire Bluetooth detection
#
# Bluetooth devices managed by PipeWire/PulseAudio do not reliably appear
# in PortAudio's device list.  We detect them via 'pactl list sources short'
# (name pattern: bluez_input.*) and then route capture through the 'pulse'
# virtual ALSA device so PortAudio reaches the BT mic via PulseAudio.
# ---------------------------------------------------------------------------

_BT_PULSE_KEYWORDS = ("bluez", "bluetooth", "a2dp", "hsp", "hfp")


def _pulse_bt_source_name() -> str | None:
    """Return the first Bluetooth source name from PulseAudio/PipeWire, or None."""
    if not has_linux_audio_tools():
        return None
    try:
        r = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                low = name.lower()
                if any(k in low for k in _BT_PULSE_KEYWORDS) and ".monitor" not in name:
                    return name
    except Exception:
        logger.debug("pactl list sources failed in mic resolver", exc_info=True)
    return None


def _pulse_set_default_source(name: str) -> None:
    try:
        subprocess.run(
            ["pactl", "set-default-source", name],
            capture_output=True, timeout=4, check=False,
        )
    except Exception:
        logger.debug("pactl set-default-source failed", exc_info=True)


def _pulse_portaudio_device_index(sd) -> int | None:
    """Find a virtual PortAudio device that routes through PulseAudio/PipeWire.

    PortAudio's ALSA backend exposes virtual devices for the configured ALSA
    PCMs.  Preference order: 'pipewire' (needs pipewire-alsa), 'pulse'
    (needs libasound2-plugins), 'default' (always present but only routes
    through PA/PW when one of the plugin packages is installed).
    """
    try:
        candidates: dict[str, int] = {}
        for idx, dev in enumerate(sd.query_devices()):
            nm = (dev.get("name") or "").strip().lower()
            if int(dev.get("max_input_channels") or 0) <= 0:
                continue
            for key in ("pipewire", "pulse", "default"):
                if nm == key or nm.startswith(f"{key} ") or nm.startswith(f"{key}:"):
                    candidates.setdefault(key, idx)
        for key in ("pipewire", "pulse", "default"):
            if key in candidates:
                logger.debug(
                    "Found PA/PW-routing PortAudio device %r at index %s",
                    key, candidates[key],
                )
                return candidates[key]
    except Exception:
        logger.debug("PulseAudio virtual device search failed", exc_info=True)
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


def _strict_usb_enabled() -> bool:
    """Match audio_capture.py's MEETINGBOX_USB_MIC_STRICT default.

    On the Linux appliance this defaults ON (only an external USB/BT mic is
    acceptable). On desktop OSes (Windows/macOS) it defaults OFF so the PC's
    own default/built-in microphone is used for testing.
    """
    default = "1" if has_linux_audio_tools() else "0"
    v = (os.getenv("MEETINGBOX_USB_MIC_STRICT") or default).strip().lower()
    return v not in ("0", "false", "no", "off")


def resolve_sounddevice_capture_device_index(sd) -> int | None:
    """
    Return a PortAudio device index for input, or None to use the host default device.

    Precedence:
    1. AUDIO_INPUT_DEVICE_INDEX (integer override)
    2. AUDIO_INPUT_DEVICE_NAME (substring match, case-insensitive)
    3. If MEETINGBOX_AUTO_SELECT_USB_MIC is not disabled:
       a. Bluetooth combined mic+speaker (highest — paired BT audio device)
       b. USB/UAC combined mic+speaker (conference puck with built-in speaker)
       c. Bluetooth mic-only
       d. USB/UAC mic-only
    4. (Only if MEETINGBOX_USB_MIC_STRICT=0) first built-in-like capture device
    5. (Only if MEETINGBOX_USB_MIC_STRICT=0) first available capture device
    6. None — PortAudio default (when strict mode finds no external device,
       or device enumeration failed)
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

    # 3a. Bluetooth via PulseAudio/PipeWire — must come before PortAudio enumeration
    # because BT devices managed by PipeWire often do NOT appear in PortAudio's
    # device list at all; they only appear as pactl sources (bluez_input.*).
    bt_pulse_src = _pulse_bt_source_name()
    if bt_pulse_src is not None:
        _pulse_set_default_source(bt_pulse_src)
        pulse_idx = _pulse_portaudio_device_index(sd)
        if pulse_idx is not None:
            logger.info(
                "Auto-selected Bluetooth via PulseAudio: source=%s → PortAudio[%s] (pulse)",
                bt_pulse_src, pulse_idx,
            )
            return pulse_idx
        # 'pulse' virtual device not in PortAudio — return None so PortAudio
        # uses its host default, which now routes to the BT mic via PulseAudio.
        logger.info(
            "Bluetooth PulseAudio source=%s set as default; no 'pulse' PortAudio "
            "device found — using host default (PulseAudio will route to BT mic)",
            bt_pulse_src,
        )
        return None

    # 3b. Bluetooth combined mic+speaker visible in PortAudio enumeration
    bt_combined = _first_bluetooth_combined_capture(sd)
    if bt_combined is not None:
        return bt_combined

    # 3c. USB/UAC combined mic+speaker (e.g. Jabra, Poly conference puck)
    usb_combined = _first_usb_combined_capture(sd)
    if usb_combined is not None:
        return usb_combined

    # 3d. Bluetooth mic-only visible in PortAudio enumeration
    bt_only = _first_bluetooth_capture(sd)
    if bt_only is not None:
        return bt_only

    # 3e. USB/UAC mic-only
    usb = _first_usb_like_capture(sd)
    if usb is not None:
        return usb

    # 3f. ALSA-based external capture fallback. PortAudio enumerates devices
    # ONCE at process start and caches the list, so a USB/Bluetooth mic that
    # only became ready AFTER init (common inside containers, where /dev/snd
    # and PipeWire settle a moment after launch) is absent from
    # sd.query_devices() for the whole life of the process — even though the
    # card is present in `arecord -l`. audio_device_resolve reads ALSA / pactl
    # live (immune to that cache) and returns an ALSA device string that
    # PortAudio can still open via device=. Consult it before giving up on an
    # external mic so the always-on wake listener matches the realtime path.
    if has_linux_audio_tools():
        try:
            from audio_device_resolve import resolve_audio_pair

            alsa_capture = resolve_audio_pair(sd).capture
            if alsa_capture is not None:
                logger.info(
                    "Auto-selected external capture via ALSA scan "
                    "(PortAudio enumeration missed it): %s",
                    alsa_capture,
                )
                return alsa_capture
        except Exception:
            logger.debug("ALSA-based external capture fallback failed", exc_info=True)

    # No external mic visible. In strict mode (default) do NOT fall back to
    # the built-in/HDMI mic — return None so callers report "no external mic"
    # instead of silently capturing the wrong device.
    if _strict_usb_enabled():
        logger.warning(
            "MEETINGBOX_USB_MIC_STRICT=1 and no external (USB/Bluetooth) capture "
            "device found; returning None so callers do not fall back to a built-in mic.",
        )
        return None

    built_in = _first_built_in_capture(sd)
    if built_in is not None:
        return built_in

    return _first_any_capture(sd)
