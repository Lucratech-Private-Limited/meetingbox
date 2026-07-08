"""Download Figma assets for recording screen 863:626 (file VelsLhL4YHeVRZSCEmCrGw).

URLs come from get_design_context + get_screenshot on the live Figma file.
They expire ~7 days after issue — re-run after refreshing URLs if needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "recording" / "figma"

# Whole-element screenshots (round bg + border + icon/text baked in)
_SCREENSHOTS: dict[str, str] = {
    "btn_back.png": "https://www.figma.com/api/mcp/asset/3ad1fdb8-69f1-4d4e-a2ab-a8c835d3e75a",
    "listening_pill.png": "https://www.figma.com/api/mcp/asset/19325e8b-8d6f-4f0d-ab80-32c362411bc4",
    "btn_settings.png": "https://www.figma.com/api/mcp/asset/8a7dd436-f9ba-46d7-b629-db2c530fda34",
    "stop_recording_pill.png": "https://www.figma.com/api/mcp/asset/05f48a95-319f-4a1f-b545-8875c35d13d2",
    "btn_pause.png": "https://www.figma.com/api/mcp/asset/572f09cd-4c82-4630-b25e-059665bf3d54",
    "icon_people.png": "https://www.figma.com/api/mcp/asset/47347e21-1bb0-444b-892c-0e311ea54238",
    "icon_video.png": "https://www.figma.com/api/mcp/asset/eb8a1319-8361-459f-a063-dd86f7a387dc",
}

# Individual asset URLs from get_design_context
_ICONS: dict[str, str] = {
    "icon_rec_dot_red.png": "https://www.figma.com/api/mcp/asset/d77ea4f2-7d06-4ea6-8817-65a418ce6783",
    # Frame 19 layers (refreshed for new file)
    "frame19_vector_left.png": "https://www.figma.com/api/mcp/asset/0d0c6047-e230-427f-8a8d-c4ee683f3de6",
    "frame19_vector_right.png": "https://www.figma.com/api/mcp/asset/f56b0f88-3716-437e-86e7-df9e81c24e77",
    "frame19_ring_glow.png": "https://www.figma.com/api/mcp/asset/53477b7a-7257-461b-aa1d-f1ae17d444a0",
    "frame19_ring_dark.png": "https://www.figma.com/api/mcp/asset/b8f4696b-b7af-402b-86b3-65cd35227f27",
    "frame19_ring_gradient.png": "https://www.figma.com/api/mcp/asset/f9fa70f0-292d-41e3-8dad-41cd84cac7a3",
}


def _curl(url: str, dest: Path) -> None:
    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    if curl and Path(curl).exists():
        subprocess.run([curl, "-sL", "-f", "-o", str(dest), url], check=True)
        return
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingBox-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())


def main() -> None:
    _ASSETS.mkdir(parents=True, exist_ok=True)
    for name, url in {**_SCREENSHOTS, **_ICONS}.items():
        out = _ASSETS / name
        try:
            _curl(url, out)
            n = out.stat().st_size
            if n == 0:
                raise RuntimeError("empty response")
            print(f"OK {name} ({n} bytes)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
