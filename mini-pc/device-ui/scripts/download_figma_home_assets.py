"""One-off / refresh: download exported PNGs from Figma MCP asset URLs into assets/home/figma/."""
from __future__ import annotations

from pathlib import Path

# Populated from plugin-figma-figma get_design_context (node 390:187, file yJqcY4KovVjJ11vjysW533)
_ASSETS: dict[str, str] = {
    "hero_background.png": "https://www.figma.com/api/mcp/asset/61cd5ca7-7133-427b-81ad-ad96d2228c26",
    "icon_settings.png": "https://www.figma.com/api/mcp/asset/316256e5-ba54-46ad-b1d9-b4bed22c1c70",
    "icon_arrow.png": "https://www.figma.com/api/mcp/asset/c9ddc406-5808-48cf-9038-9985dd8c9a6f",
    "icon_keyboard.png": "https://www.figma.com/api/mcp/asset/2d323f2f-381d-4ea8-93ef-91148f1afd55",
    "icon_sun.png": "https://www.figma.com/api/mcp/asset/ffed1182-dbac-414b-8014-67a0c70151b2",
    "icon_calendar.png": "https://www.figma.com/api/mcp/asset/6be2523f-dd60-4a2f-8061-f1def05f7b27",
    "icon_start_mic.png": "https://www.figma.com/api/mcp/asset/afd4042f-5801-464e-9736-de15f83e0e9f",
    "icon_voice_orb_bar.png": "https://www.figma.com/api/mcp/asset/2dcd3e53-869b-42d0-baf8-8901a8cb9eb4",
    "icon_sparkle_layer.png": "https://www.figma.com/api/mcp/asset/795bee1a-5338-4421-9d4e-1f9534bd7f2f",
    "icon_file_document.png": "https://www.figma.com/api/mcp/asset/22e74a45-27a4-4462-9243-687d36aee8d6",
    "icon_sun_morning_brief.png": "https://www.figma.com/api/mcp/asset/2baaf373-7017-467c-9930-409189f64aa5",
    "icon_calendar_brief.png": "https://www.figma.com/api/mcp/asset/28a445dd-d002-467e-883c-b9921d367362",
    "icon_weather.png": "https://www.figma.com/api/mcp/asset/573cb79e-a2f1-4b54-b448-c838c425257e",
    "icon_email.png": "https://www.figma.com/api/mcp/asset/a050cf92-4e5f-483e-b1ce-9042fef4211a",
    "icon_email_card.png": "https://www.figma.com/api/mcp/asset/d1391a22-75fe-4bbd-9664-20ee0b8c9bc6",
    "icon_arrow_card.png": "https://www.figma.com/api/mcp/asset/e79c05b1-d8ef-4ad3-a831-255d453ccbc6",
    "icon_calendar_schedule.png": "https://www.figma.com/api/mcp/asset/6a40b87e-12fa-4cb5-b5a5-5c62aabdc3b3",
    "icon_task_check.png": "https://www.figma.com/api/mcp/asset/d29765dc-8c59-4a5d-b64d-9fc6bbbdd7ad",
    "icon_soundwave.png": "https://www.figma.com/api/mcp/asset/8f758b10-6944-4715-ada5-8c1ffd37d66b",
    "icon_listening_dot.png": "https://www.figma.com/api/mcp/asset/88a1e9fd-a29f-4e73-8bcb-fb8b3098e2fa",
    "avatar_mask_circle.png": "https://www.figma.com/api/mcp/asset/7293d65b-7d01-459a-8033-8600a3234464",
    "avatar_photo_1.png": "https://www.figma.com/api/mcp/asset/92a97a25-c56a-40a5-8473-a995aafeff95",
    "avatar_photo_2.png": "https://www.figma.com/api/mcp/asset/4407a01d-5531-4958-823e-0f831c3739cf",
}


def _download(url: str, dest: Path) -> None:
    import shutil
    import subprocess
    import sys

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


def main() -> None:
    root = Path(__file__).resolve().parent.parent / "assets" / "home" / "figma"
    root.mkdir(parents=True, exist_ok=True)
    for name, url in _ASSETS.items():
        dest = root / name
        try:
            _download(url, dest)
            n = dest.stat().st_size
            if n == 0:
                raise RuntimeError("empty file")
            print(f"OK {name} ({n} bytes)")
        except Exception as e:
            print(f"FAIL {name}: {e}")


if __name__ == "__main__":
    main()
