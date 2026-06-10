"""Download Figma assets for processing screen 397:261 (file VelsLhL4YHeVRZSCEmCrGw).

URLs come from ``get_design_context`` + ``get_screenshot`` on the live Figma
file. They expire ~7 days after issue — re-run after refreshing URLs if needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "processing" / "figma"

# Composite screenshots (round bg + border + icon/text baked in)
_SCREENSHOTS: dict[str, str] = {
    # Header
    "btn_back.png": "https://www.figma.com/api/mcp/asset/de130c54-eef6-40ab-9839-ecee3b1b59f5",
    "listening_pill.png": "https://www.figma.com/api/mcp/asset/d7440e35-b7d7-44ca-974a-79fd529ee1ff",
    "btn_settings.png": "https://www.figma.com/api/mcp/asset/5cd6e068-da4d-4a0d-8a4c-6942a940a245",
    # Status row
    "check_badge.png": "https://www.figma.com/api/mcp/asset/e30352f2-3de8-4fac-a306-ae390db93af1",
    # Right-side cards (full composites — match Figma 1:1)
    "steps_card.png": "https://www.figma.com/api/mcp/asset/a4c21c41-9305-47bd-9b04-569b06264013",
    "notify_bar.png": "https://www.figma.com/api/mcp/asset/78edbf20-e177-49f1-b703-9b37755090bc",
    # Centre orb + 4 ring stroke layers (rendered as raster screenshots so we
    # don't depend on the resvg-rendered SVG paths losing their gradients).
    "orb_glow.png": "https://www.figma.com/api/mcp/asset/c939954a-c05d-4574-895d-4ee489dfc375",
    "ring_glow.png": "https://www.figma.com/api/mcp/asset/30a2c47a-935a-46e4-8d49-2ed4ac9eb47f",
    "ring_lighten.png": "https://www.figma.com/api/mcp/asset/dd442231-30e9-4ae2-91eb-374f6fda41b0",
    "ring_solid.png": "https://www.figma.com/api/mcp/asset/74eac702-75b1-4783-bb15-64ddd60488b0",
    "ring_outer.png": "https://www.figma.com/api/mcp/asset/d30950d8-519d-4781-b2fc-2d6015f8218a",
}

# Individual asset URLs from get_design_context.
# (The dot separator is rendered locally via Pillow — the Figma SVG export is
# 6×6 and rasterises to a malformed corner blob.)
_ICONS: dict[str, str] = {}


def _curl(url: str, dest: Path) -> None:
    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    if curl and Path(curl).exists():
        subprocess.run([curl, "-sL", "-f", "-o", str(dest), url], check=True)
        return
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "MeetingBox-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        dest.write_bytes(r.read())


def _is_svg_text(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:200].lstrip("\ufeff")
    except Exception:
        return False
    return head.startswith("<svg") or head.startswith("<?xml")


def _rasterize_svg(path: Path, scale: float = 3.0) -> None:
    """Rewrite SVG-as-PNG files into real raster PNGs using resvg."""
    import re as _re

    try:
        import resvg  # type: ignore
    except ImportError as exc:  # noqa: BLE001
        print(f"!! resvg not installed — leave {path.name} as SVG ({exc})", file=sys.stderr)
        return
    raw = path.read_text(encoding="utf-8")
    sanitized = _re.sub(r"var\(\s*--[^,]+,\s*([^)]+)\)", r"\1", raw)
    try:
        opts = resvg.usvg.Options.default()
        tree = resvg.usvg.Tree.from_str(sanitized, opts)
        png = resvg.render(tree, (scale, 0.0, 0.0, scale, 0.0, 0.0))
    except Exception as exc:  # noqa: BLE001
        print(f"!! resvg render failed for {path.name}: {exc}", file=sys.stderr)
        return
    if len(png) < 32:
        print(f"!! tiny render for {path.name}", file=sys.stderr)
        return
    path.write_bytes(png)
    print(f"   rasterized {path.name} -> {len(png)} bytes (scale={scale})")


def _render_dot_separator(dest: Path, *, size: int = 48) -> None:
    """Render the small ``•`` separator (Ellipse 14, #B6BAF2 in Figma) as a
    bitmap PNG. Figma's own SVG export is 6×6 with a malformed gradient that
    rasterises to a 3-pixel corner blob, so we synthesise the asset locally.
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(182, 186, 242, 255))
    img.save(dest)
    print(f"   synthesised {dest.name} -> {dest.stat().st_size} bytes ({size}px)")


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
            continue
        if _is_svg_text(out):
            _rasterize_svg(out)

    # Locally synthesised glyphs (see _render_dot_separator for rationale).
    _render_dot_separator(_ASSETS / "dot_separator.png")


if __name__ == "__main__":
    main()
