"""
ALSA-aware audio device pair selection for MeetingBox.

Resolves the best capture (mic) + playback (speaker) device pair by
inspecting ALSA hardware lists (`arecord -l`, `aplay -l`).

Priority:
  1. Combined Bluetooth mic+speaker: same ALSA card appears in both
     capture and playback lists, AND is Bluetooth-like → highest priority
     because the user explicitly paired a combined BT audio device.
  2. Combined USB/external mic+speaker: same ALSA card in both lists,
     USB/UAC-class. Eliminates echo for conference pucks (Jabra, Poly, etc.)
  3. Bluetooth capture only (no matching playback card) → use for mic.
  4. USB capture only (no playback on the same card) → use for mic,
     leave playback as ALSA default.
  5. No external device found → None for both (existing PortAudio defaults).

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

from platform_compat import has_linux_audio_tools

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
    def is_bluetooth_like(self) -> bool:
        haystack = f"{self.short_name} {self.long_name} {self.dev_long}".lower()
        return any(k in haystack for k in (
            "bluetooth", "bluez", "a2dp", "hsp", "hfp",
            "headset", "hands-free", "hands free", "bt ",
        ))

    @property
    def is_usb_like(self) -> bool:
        haystack = f"{self.short_name} {self.long_name} {self.dev_long}".lower()
        return "usb" in haystack or "uac" in haystack

    @property
    def is_external_like(self) -> bool:
        """True for any non-built-in audio device (USB or Bluetooth)."""
        return self.is_usb_like or self.is_bluetooth_like

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
# PulseAudio / PipeWire Bluetooth detection
#
# Bluetooth audio devices on Linux are managed by PipeWire/PulseAudio and
# are NOT listed by `arecord -l` / `aplay -l`. They only appear as
# PulseAudio sources (bluez_input.*) and sinks (bluez_output.*).
# We query pactl directly to find them and set them as the PulseAudio
# default before any ALSA-level resolution runs.
# ---------------------------------------------------------------------------

_BT_PULSE_KEYWORDS = ("bluez", "bluetooth", "a2dp", "hsp", "hfp")


def _is_bt_pulse_name(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in _BT_PULSE_KEYWORDS)


def _pulse_bt_source_names() -> list[str]:
    """Return Bluetooth source names from PulseAudio/PipeWire (e.g. bluez_input.*)."""
    try:
        r = subprocess.run(
            ["pactl", "list", "sources", "short"],
            capture_output=True, text=True, timeout=5,
        )
        names = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                if _is_bt_pulse_name(name) and ".monitor" not in name:
                    names.append(name)
        return names
    except Exception:
        logger.debug("pactl list sources failed", exc_info=True)
        return []


def _pulse_bt_sink_names() -> list[str]:
    """Return Bluetooth sink names from PulseAudio/PipeWire (e.g. bluez_output.*)."""
    try:
        r = subprocess.run(
            ["pactl", "list", "sinks", "short"],
            capture_output=True, text=True, timeout=5,
        )
        names = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                name = parts[1].strip()
                if _is_bt_pulse_name(name):
                    names.append(name)
        return names
    except Exception:
        logger.debug("pactl list sinks failed", exc_info=True)
        return []


def _pulse_set_default_source(name: str) -> None:
    try:
        subprocess.run(
            ["pactl", "set-default-source", name],
            capture_output=True, timeout=4, check=False,
        )
    except Exception:
        logger.debug("pactl set-default-source failed", exc_info=True)


def _pulse_set_default_sink(name: str) -> None:
    try:
        subprocess.run(
            ["pactl", "set-default-sink", name],
            capture_output=True, timeout=4, check=False,
        )
    except Exception:
        logger.debug("pactl set-default-sink failed", exc_info=True)


def _pick_pulse_pcm(cmd: str) -> str:
    """Return the best ALSA PCM name that routes through PulseAudio/PipeWire.

    Probes ``arecord -L`` / ``aplay -L`` for which virtual device names
    are actually registered with ALSA inside this environment.  This
    matters because:

      * ``pulse``    requires ``libasound2-plugins`` (alsa-plugins-pulse)
      * ``pipewire`` requires ``pipewire-alsa``
      * ``default``  is always present but only routes through PulseAudio
                     when one of the two plugin packages above is installed

    Order of preference: pipewire → pulse → default.
    """
    try:
        r = subprocess.run([cmd, "-L"], capture_output=True, text=True, timeout=5)
        pcms = {line.strip() for line in r.stdout.splitlines() if line and not line.startswith(" ")}
    except Exception:
        logger.debug("%s -L probe failed", cmd, exc_info=True)
        pcms = set()
    for name in ("pipewire", "pulse", "default"):
        if name in pcms:
            logger.info("Selected ALSA PCM %r for routing via PulseAudio/PipeWire (%s)", name, cmd)
            return name
    logger.warning(
        "Neither 'pipewire', 'pulse' nor 'default' found in %s -L output; "
        "falling back to 'default' (BT routing may not work — install "
        "libasound2-plugins or pipewire-alsa).", cmd,
    )
    return "default"


# ---------------------------------------------------------------------------
# ALSA list parsing
# ---------------------------------------------------------------------------

_CARD_RE = re.compile(
    r"^card\s+(\d+):\s+(\S+)\s+\[([^\]]*)\],\s+device\s+(\d+):\s+.*?\[([^\]]*)\]",
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

    # Non-Linux (Windows/macOS): ALSA/PulseAudio CLI tools do not exist. Use
    # PortAudio defaults for both capture and playback (capture=None lets the
    # caller fall back to the host default mic; playback is handled by the
    # cross-platform audio_output helper, not aplay).
    if not has_linux_audio_tools():
        return pair

    # Priority 0: Bluetooth via PulseAudio/PipeWire.
    # BT devices are NOT listed by `arecord -l` — they only exist as
    # PulseAudio sources/sinks.  When found, we set them as the PulseAudio
    # defaults and route capture+playback through the 'pulse' ALSA PCM so
    # that arecord/aplay reach the BT device transparently.
    #
    # Important: many BT speakerphones (e.g. AM-W45) only expose A2DP-sink
    # over BT, not HFP/HSP — so there's a bluez_output.* sink but no
    # bluez_input.* source.  We still route playback through PulseAudio so
    # the BT speaker is used even when the mic side falls back to a USB
    # or built-in device.
    bt_sources = _pulse_bt_source_names()
    bt_sinks = _pulse_bt_sink_names()
    bt_speaker_routed = False

    if bt_sources:
        src = bt_sources[0]
        _pulse_set_default_source(src)
        capture_pcm = _pick_pulse_pcm("arecord")
        pair.capture = capture_pcm
        pair.capture_name = f"(Bluetooth/PulseAudio via {capture_pcm}) {src}"
        if bt_sinks:
            snk = bt_sinks[0]
            _pulse_set_default_sink(snk)
            playback_pcm = _pick_pulse_pcm("aplay")
            pair.playback = playback_pcm
            pair.playback_name = f"(Bluetooth/PulseAudio via {playback_pcm}) {snk}"
            pair.is_combined = True
            bt_speaker_routed = True
            logger.info(
                "AudioPair [Priority 0]: Bluetooth mic+speaker via PulseAudio — "
                "source=%s sink=%s → capture=%s playback=%s",
                src, snk, capture_pcm, playback_pcm,
            )
        else:
            logger.info(
                "AudioPair [Priority 0]: Bluetooth mic-only via PulseAudio — "
                "source=%s → capture=%s",
                src, capture_pcm,
            )
        # Skip ALSA scanning — BT via PulseAudio takes full priority.
        # Still apply the env override for playback if set.
        out_override = (os.getenv("AUDIO_OUTPUT_DEVICE_NAME") or "").strip()
        if out_override:
            pair.playback = out_override
            pair.playback_name = f"(AUDIO_OUTPUT_DEVICE_NAME) {out_override}"
            logger.info("AudioPair: AUDIO_OUTPUT_DEVICE_NAME override → %s", out_override)
        if not pair.playback:
            fallback = (os.getenv("AUDIO_OUTPUT_FALLBACK_DEVICE") or "plughw:0,0").strip()
            pair.playback = fallback
            pair.playback_name = f"(fallback) {fallback}"
        return pair

    # Sub-priority: BT speaker WITHOUT BT mic (A2DP-only device, e.g. AM-W45
    # over Bluetooth).  We still want the BT speaker routed via PulseAudio
    # even though the mic will fall through to the next priority tier (USB
    # or built-in).  Capture resolution continues below.
    if bt_sinks:
        snk = bt_sinks[0]
        _pulse_set_default_sink(snk)
        playback_pcm = _pick_pulse_pcm("aplay")
        pair.playback = playback_pcm
        pair.playback_name = f"(Bluetooth speaker via {playback_pcm}, A2DP-only) {snk}"
        bt_speaker_routed = True
        logger.info(
            "AudioPair [Priority 0b]: Bluetooth speaker-only via PulseAudio — "
            "sink=%s → playback=%s (mic will use next priority tier)",
            snk, playback_pcm,
        )

    capture_cards = _parse_alsa_list(["arecord", "-l"])
    playback_cards = _parse_alsa_list(["aplay", "-l"])

    bt_capture = [c for c in capture_cards if c.is_bluetooth_like]
    usb_capture = [c for c in capture_cards if c.is_usb_like and not c.is_bluetooth_like]
    bt_playback = [c for c in playback_cards if c.is_bluetooth_like]
    usb_playback = [c for c in playback_cards if c.is_usb_like and not c.is_bluetooth_like]

    bt_playback_card_nums = {c.card_num for c in bt_playback}
    usb_playback_card_nums = {c.card_num for c in usb_playback}

    # Priority 1: Bluetooth combined mic+speaker (highest — user paired a BT audio device)
    for cap in bt_capture:
        if cap.card_num in bt_playback_card_nums:
            pb = next(c for c in bt_playback if c.card_num == cap.card_num)
            pair.capture = _sounddevice_index_for_card(cap, sd)
            pair.capture_name = cap.display_name
            pair.playback = pb.alsa_device
            pair.playback_name = pb.display_name
            pair.is_combined = True
            logger.info(
                "AudioPair: Bluetooth combined mic+speaker on card %s — "
                "capture=%s playback=%s (%s)",
                cap.card_num, pair.capture, pair.playback, cap.long_name,
            )
            break

    # Priority 2: USB combined mic+speaker (same card in both lists)
    if not pair.is_combined:
        for cap in usb_capture:
            if cap.card_num in usb_playback_card_nums:
                pb = next(c for c in usb_playback if c.card_num == cap.card_num)
                pair.capture = _sounddevice_index_for_card(cap, sd)
                pair.capture_name = cap.display_name
                pair.playback = pb.alsa_device
                pair.playback_name = pb.display_name
                pair.is_combined = True
                logger.info(
                    "AudioPair: USB combined mic+speaker on card %s — "
                    "capture=%s playback=%s (%s)",
                    cap.card_num, pair.capture, pair.playback, cap.long_name,
                )
                break

    # Priority 3: Bluetooth capture only, no matching playback
    if not pair.is_combined and bt_capture:
        cap = bt_capture[0]
        pair.capture = _sounddevice_index_for_card(cap, sd)
        pair.capture_name = cap.display_name
        logger.info(
            "AudioPair: Bluetooth capture-only on card %s — "
            "capture=%s playback=default (%s)",
            cap.card_num, pair.capture, cap.long_name,
        )

    # Priority 4: USB capture only, no matching playback
    if not pair.is_combined and not pair.capture and usb_capture:
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
