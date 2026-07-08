"""Download Figma assets for meeting summary screen 659:838 (file
VelsLhL4YHeVRZSCEmCrGw).

URLs come from ``get_design_context`` + ``get_screenshot`` on the live Figma
file. They expire ~7 days after issue — re-run after refreshing URLs if needed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "summary" / "figma"

# Composite screenshots (button bgs / chip bgs / round icon containers etc.)
_SCREENSHOTS: dict[str, str] = {
    # Header
    "btn_back.png": "https://www.figma.com/api/mcp/asset/e77fcc2d-aeae-4645-aa7f-28ed851ae63e",
    # Meta card
    "icon_file_box.png": "https://www.figma.com/api/mcp/asset/800c4c07-ecf6-4733-8dce-368bfaf8d72e",
    "chip_participants.png": "https://www.figma.com/api/mcp/asset/0d36b128-8d20-4f78-9b0b-0e73f24cad16",
    "chip_recorded.png": "https://www.figma.com/api/mcp/asset/5a6b19de-0cb5-40dc-a5ca-0845824e5a25",
    "btn_export.png": "https://www.figma.com/api/mcp/asset/4c61757f-974e-40e6-9ed8-50bde7481a8c",
    "btn_share.png": "https://www.figma.com/api/mcp/asset/56754ed0-d083-4413-b3d4-19da8252854d",
    # AI Summary card
    "ai_summary_icon.png": "https://www.figma.com/api/mcp/asset/deb0996d-cb11-4edd-8021-dda36c682141",
    "ai_summary_image.png": "https://www.figma.com/api/mcp/asset/dfc0f4e6-a7cc-46aa-b1ff-a0d15cac9805",
    # Action items card
    "action_check_done.png": "https://www.figma.com/api/mcp/asset/b5979add-f31a-4494-93e2-794c106efe05",
    "action_check_pending.png": "https://www.figma.com/api/mcp/asset/beda9336-ddab-493c-8bc5-8557e00af5b3",
    "action_avatar.png": "https://www.figma.com/api/mcp/asset/6d793fba-8517-4588-95f3-84bba9be603d",
    "action_items_icon.png": "https://www.figma.com/api/mcp/asset/a417d843-8ee9-4f08-a1ff-99c6415a0862",
    # Decisions card
    "decisions_icon.png": "https://www.figma.com/api/mcp/asset/d3de56c2-d294-47c6-a714-1da6a05037ad",
    "decision_tick.png": "https://www.figma.com/api/mcp/asset/65525d9e-0d6d-4d2a-8f48-afc2585217a2",
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


def main() -> None:
    _ASSETS.mkdir(parents=True, exist_ok=True)
    for name, url in _SCREENSHOTS.items():
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


if __name__ == "__main__":
    main()
