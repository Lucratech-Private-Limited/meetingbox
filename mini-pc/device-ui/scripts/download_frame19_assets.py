"""Download Frame 19 assets from Figma MCP URLs and convert SVG → PNG for Kivy.

Re-run after Figma edits (refresh URLs from get_design_context on node 863:635).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "recording" / "figma"

# Figma MCP asset URLs — node 863:635 (Frame 19), fetched 2026-05-22
_ASSET_URLS: dict[str, str] = {
    "frame19_vector_left": "https://www.figma.com/api/mcp/asset/ea36b4db-71a8-4c41-a957-d291bcb2f1f7",
    "frame19_vector_right": "https://www.figma.com/api/mcp/asset/e76cb516-080d-45ee-9de8-0dfd8805be1a",
    "frame19_ellipse17": "https://www.figma.com/api/mcp/asset/7cd11abc-e554-4681-8fc9-7451335ea404",
    "frame19_ring_glow": "https://www.figma.com/api/mcp/asset/9b76d054-bd5b-4f61-95a1-f6dfd41ece87",
    "frame19_ring_dark": "https://www.figma.com/api/mcp/asset/c90d7fce-f2f6-4adb-8b8a-ba4deec207d4",
    "frame19_ring_gradient": "https://www.figma.com/api/mcp/asset/d364ff41-058c-44a9-92e4-04e550b76dc0",
}

# Target PNG width (2× Figma box width for crisp rendering)
_PNG_WIDTH: dict[str, int] = {
    "frame19_vector_left": 74,
    "frame19_vector_right": 74,
    "frame19_ellipse17": 572,
    "frame19_ring_glow": 440,
    "frame19_ring_dark": 440,
    "frame19_ring_gradient": 440,
}


def _download(url: str) -> bytes:
    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    if curl and Path(curl).exists():
        r = subprocess.run([curl, "-sL", "-f", url], capture_output=True, check=True)
        return r.stdout
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingBox-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _resvg(svg: Path, png: Path, fit_width: int) -> None:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise RuntimeError("npx not found — install Node.js to convert SVG → PNG")
    subprocess.run(
        [npx, "--yes", "@resvg/resvg-js-cli", "--fit-width", str(fit_width), str(svg), str(png)],
        check=True,
    )


def main() -> None:
    _ASSETS.mkdir(parents=True, exist_ok=True)
    for stem, url in _ASSET_URLS.items():
        png = _ASSETS / f"{stem}.png"
        try:
            data = _download(url)
            if not data:
                raise RuntimeError("empty response")
            if data[:4] == b"\x89PNG":
                png.write_bytes(data)
                print(f"OK {png.name} (PNG, {len(data)} bytes)")
                continue
            if data.lstrip()[:4] == b"<svg" or b"<svg" in data[:200]:
                svg = _ASSETS / f"{stem}.svg"
                svg.write_bytes(data)
                _resvg(svg, png, _PNG_WIDTH[stem])
                print(f"OK {png.name} (SVG->PNG, {png.stat().st_size} bytes)")
                continue
            raise RuntimeError(f"unknown format: {data[:40]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {stem}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
