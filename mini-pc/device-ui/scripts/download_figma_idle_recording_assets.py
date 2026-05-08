"""Download Figma asset PNGs for the new Idle (338:60) and Recording (408:657) screens.

Mirrors the pattern used in ``download_figma_home_assets.py``: try ``curl`` first
(reliable on Windows where ``urllib`` was returning empty bodies), fall back to
``urllib`` with a UA header. The Figma MCP asset URLs expire ~7 days after they
were emitted, so re-run the script (after refreshing the URLs from
``get_design_context``) if any asset returns empty.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_IDLE_ASSETS: dict[str, str] = {
    # Calendar pictogram next to "Next up"
    "icon_calendar.png": "https://www.figma.com/api/mcp/asset/da195b92-33c9-4216-9609-3fb9a672e562",
    # Sun pictogram next to weather block
    "icon_sun.png": "https://www.figma.com/api/mcp/asset/86e39d71-abed-4726-9681-2121c68f15ad",
    # Full-bleed mountain/lake background
    "background_landscape.png": "https://www.figma.com/api/mcp/asset/e03853b8-ca0b-491e-9593-101b934b1280",
    # Mic orb inside the Start Recording card
    "mic_orb.png": "https://www.figma.com/api/mcp/asset/b3ef0206-20a6-41ac-bb20-4195c29e8e0e",
}

_REC_ASSETS: dict[str, str] = {
    # Header back arrow (in the round button on the top-left)
    "icon_back_arrow.png": "https://www.figma.com/api/mcp/asset/9e371555-fdf2-45d6-b381-d6bab8b4316e",
    # Soundwave glyph in the Listening pill
    "icon_soundwave.png": "https://www.figma.com/api/mcp/asset/e6c7e14f-3e0e-4c6a-a722-0e3d06f280ef",
    # Recording-status red dot (Listening pill)
    "icon_listening_dot.png": "https://www.figma.com/api/mcp/asset/519392ab-3b40-4c52-83be-d42e503e5fa7",
    # People (3 participants) glyph next to the meeting title
    "icon_people.png": "https://www.figma.com/api/mcp/asset/26622bf8-512f-4a14-9de4-e17a86395092",
    # Video / camera glyph next to "Google Meet"
    "icon_video.png": "https://www.figma.com/api/mcp/asset/aaed8fa3-1ae6-4cac-b4c9-6d4999d3bec3",
    # Recording red dot next to "Recording..."
    "icon_recording_dot.png": "https://www.figma.com/api/mcp/asset/79dce985-46ea-416c-90b0-47194f4d75f4",
    # Settings gear inside the bottom-right circular control
    "icon_settings_gear.png": "https://www.figma.com/api/mcp/asset/34e374b3-68e7-40b5-9d04-dc6abf9de6a5",
    # Big circular waveform / orbital ring backdrop behind the level meter
    "wave_circle_bg.png": "https://www.figma.com/api/mcp/asset/fac56474-ff45-4fd1-a95a-3857fb07b638",
}


def _download(url: str, dest: Path) -> None:
    curl = shutil.which("curl")
    if not curl and sys.platform == "win32":
        curl = r"C:\Windows\System32\curl.exe"
    if curl and Path(curl).exists():
        subprocess.run([curl, "-sL", "-f", "-o", str(dest), url], check=True)
        return
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "MeetingBox-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())


def _pull(group_dir: Path, assets: dict[str, str]) -> None:
    group_dir.mkdir(parents=True, exist_ok=True)
    for name, url in assets.items():
        dest = group_dir / name
        try:
            _download(url, dest)
            n = dest.stat().st_size
            if n == 0:
                raise RuntimeError("empty file")
            print(f"OK {dest.relative_to(group_dir.parent.parent)} ({n} bytes)")
        except Exception as exc:  # noqa: BLE001 — best-effort; report and move on
            print(f"FAIL {dest}: {exc}")


def main() -> None:
    base = Path(__file__).resolve().parent.parent / "assets"
    _pull(base / "idle", _IDLE_ASSETS)
    _pull(base / "recording", _REC_ASSETS)


if __name__ == "__main__":
    main()
