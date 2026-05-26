"""
ALSA-aware audio device pair selection for MeetingBox.

Resolves the best capture (mic) + playback (speaker) device pair by
inspecting ALSA hardware lists (`arecord -l`, `aplay -l`).

Priority:
  1. Combined USB/external device: same ALSA card appears in both
     capture and playback lists → use for both mic and speaker. This
     eliminates inter-device echo when a conference puck (Jabra, Poly,
     etc.) is connected.
  2. USB capture only (no playback on the same card) → use for mic,
     leave playback as ALSA default.
  3. No USB device found → None for both (existing PortAudio defaults).

Env overrides (highest priority, applied on top of the above):
  AUDIO_OUTPUT_DEVICE_NAME  — explicit ALSA device string for aplay -D
                               e.g. "plughw:1,0" or "plughw:CARD=Jabra,DEV=0"
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal ALSA card representation
# ---------------------------------------------------------------------------

@dataclass
class _AlsaCard:
    card_num: int
    short_name: str   # e.g. "OSM09", "PCH"
    long_name: str    # e.g. "OSM09", "HDA Intel PCH"
    dev_num: int
    dev_long: str     # e.g. "USB Audio", "ALC269VC Analog"

    @property
    def is_usb_like(self) -> bool:
        haystack = f"{self.short_name} {self.long_name} {self.dev_long}".lower()
        return "usb" in haystack or "uac" in haystack

    @property
    def alsa_device(self) -> str:
        """ALSA device string understood by both aplay -D and sounddevice."""
        return f"plughw:{self.card_num},{self.dev_num}"

    @property
    def display_name(self) -> str:
        return f"{self.long_name} / {self.dev_long} (card {self.card_num})"


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

@dataclass
class AudioDevicePair:
    """Resolved capture + playback device identifiers.

    capture  — sounddevice device: int index, ALSA string like "plughw:1,0",
               or None (let PortAudio pick its default).
    playback — ALSA device string for ``aplay -D``, or None (aplay default).
    is_combined — True when both come from the same physical card.
    """
    capture: int | str | None = None
    capture_name: str | None = None
    playback: str | None = None
    playback_name: str | None = None
    is_combined: bool = False


# ---------------------------------------------------------------------------
# ALSA list parsing
# ---------------------------------------------------------------------------

_CARD_RE = re.compile(
    r"^card\s+(\d+):\s+(\S+)\s+\[([^\]]*)\],\s+device\s+(\d+):\s+\S+\s+\[([^\]]*)\]",
    re.MULTILINE,
)


def _parse_alsa_list(cmd: list[str]) -> list[_AlsaCard]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        text = r.stdout + r.stderr
    except Exception:
        logger.debug("ALSA list cmd %s failed", cmd, exc_info=True)
        return []
    cards = []
    for m in _CARD_RE.finditer(text):
        cards.append(_AlsaCard(
            card_num=int(m.group(1)),
            short_name=m.group(2),
            long_name=m.group(3),
            dev_num=int(m.group(4)),
            dev_long=m.group(5),
        ))
    return cards


# ---------------------------------------------------------------------------
# sounddevice index lookup (best-effort)
# ---------------------------------------------------------------------------

def _sounddevice_index_for_card(card: _AlsaCard, sd) -> int | str:
    """
    Return a sounddevice device identifier for the given ALSA card.

    Tries to match by hw:N substring in sounddevice's enumerated names.
    Falls back to the ALSA plughw string — PortAudio's ALSA backend
    accepts device names even for cards not in query_devices().
    """
    if sd is None:
        return card.alsa_device
    hw_prefix = f"hw:{card.card_num},"
    try:
        for i, dev in enumerate(sd.query_devices()):
            name = dev.get("name") or ""
            if int(dev.get("max_input_channels") or 0) > 0 and hw_prefix in name:
                logger.debug(
                    "AudioPair: matched sounddevice index %s (%s) to card %s",
                    i, name, card.card_num,
                )
                return i
    except Exception:
        pass
    logger.info(
        "AudioPair: card %s (%s) not in sounddevice enumeration — "
        "using ALSA string %s directly",
        card.card_num, card.long_name, card.alsa_device,
    )
    return card.alsa_device


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def resolve_audio_pair(sd=None) -> AudioDevicePair:
    """Resolve the best capture + playback device pair.

    Call once at session startup. Pass the sounddevice module (or None).
    """
    pair = AudioDevicePair()

    capture_cards = _parse_alsa_list(["arecord", "-l"])
    playback_cards = _parse_alsa_list(["aplay", "-l"])

    usb_capture = [c for c in capture_cards if c.is_usb_like]
    usb_playback = [c for c in playback_cards if c.is_usb_like]
    playback_card_nums = {c.card_num for c in usb_playback}

    # Priority 1: combined device (same card in both lists)
    for cap in usb_capture:
        if cap.card_num in playback_card_nums:
            pb = next(c for c in usb_playback if c.card_num == cap.card_num)
            pair.capture = _sounddevice_index_for_card(cap, sd)
            pair.capture_name = cap.display_name
            pair.playback = pb.alsa_device
            pair.playback_name = pb.display_name
            pair.is_combined = True
            logger.info(
                "AudioPair: combined device on card %s — "
                "capture=%s playback=%s (%s)",
                cap.card_num, pair.capture, pair.playback, cap.long_name,
            )
            break

    # Priority 2: USB capture only, no matching playback
    if not pair.is_combined and usb_capture:
        cap = usb_capture[0]
        pair.capture = _sounddevice_index_for_card(cap, sd)
        pair.capture_name = cap.display_name
        logger.info(
            "AudioPair: USB capture-only on card %s — "
            "capture=%s playback=default (%s)",
            cap.card_num, pair.capture, cap.long_name,
        )

    # Env override always wins for playback
    out_override = (os.getenv("AUDIO_OUTPUT_DEVICE_NAME") or "").strip()
    if out_override:
        pair.playback = out_override
        pair.playback_name = f"(AUDIO_OUTPUT_DEVICE_NAME) {out_override}"
        logger.info("AudioPair: AUDIO_OUTPUT_DEVICE_NAME override → %s", out_override)

    # Always resolve to an explicit playback device.
    # ALSA's "default" PCM uses dmix, which fails inside Docker containers
    # ("unable to open slave"). Fall back to plughw:0,0 (first card, first
    # device) which bypasses dmix and accesses hardware directly.
    if not pair.playback:
        fallback = (os.getenv("AUDIO_OUTPUT_FALLBACK_DEVICE") or "plughw:0,0").strip()
        pair.playback = fallback
        pair.playback_name = f"(fallback) {fallback}"
        logger.info(
            "AudioPair: no USB playback device found — using fallback %s "
            "(ALSA default/dmix is broken in Docker; override with AUDIO_OUTPUT_DEVICE_NAME)",
            fallback,
        )

    return pair
