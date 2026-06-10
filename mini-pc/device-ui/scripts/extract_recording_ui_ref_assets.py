"""Crop PNG layers from UI_Ref_for_cursor/Recordingscreen onto 1260×800 coords.

These reference PNGs match Figma node 863:626 (Recording Active / Paused).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "UI_Ref_for_cursor" / "Recordingscreen"
ASSETS = ROOT / "assets" / "recording" / "figma"
UI = REF / "Assets"

# Source ref 1280×1024 → target canvas 1260×800
_SW, _SH = 1280.0, 1024.0
_TW, _TH = 1260.0, 800.0


def _s(v: float, axis: str) -> int:
    return int(round(v * (_TW / _SW if axis == "x" else _TH / _SH)))


def _crop_ref(name: str, box: tuple[int, int, int, int], out: str) -> None:
    src = REF / name
    im = Image.open(src).convert("RGBA")
    im.crop(box).save(ASSETS / out)
    print(f"OK {out}")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    # Copy handoff assets (already Figma exports)
    for src_name, dst in (
        ("Overlay.png", "rec_pill_recording.png"),
        ("PAUSED icon for top left.png", "rec_pill_paused.png"),
        ("Pause recording button.png", "btn_pause_recording.png"),
        ("end meetingbutton.png", "btn_end_meeting.png"),
        ("resume recording button.png", "btn_resume_recording.png"),
        ("mic mute icon.png", "icon_mic_mute.png"),
    ):
        s = UI / src_name
        if s.is_file():
            Image.open(s).convert("RGBA").save(ASSETS / dst)
            print(f"OK {dst}")

    # Settings circle — crop from Recording Active top-right
    _crop_ref("Recording Active.png", (1185, 28, 1255, 98), "btn_settings_circle.png")

    # Static waveform snapshot — centre of active screen
    _crop_ref("Recording Active.png", (503, 411, 776, 603), "waveform_static.png")

    # Paused centre glow line
    paused = REF / "Meeting Paused (S-04).jpg"
    if paused.is_file():
        pim = Image.open(paused).convert("RGBA")
        # horizontal glow band (~middle of paused content)
        pim.crop((340, 520, 940, 545)).save(ASSETS / "paused_glow_line.png")
        print("OK paused_glow_line.png")


if __name__ == "__main__":
    main()
