"""Export Frame 19 PNG assets only (863:635 children) for Kivy.

Converts the two side-vector SVGs to PNG. Requires ``npx`` (resvg-js-cli).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "recording" / "figma"

# Frame 19 only — nodes 863:636 and 863:637
_EXPORTS: tuple[tuple[str, int], ...] = (
    ("frame19_vector_left.svg", 74),
    ("frame19_vector_right.svg", 74),
)


def _resvg(svg: Path, png: Path, fit_width: int) -> None:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise RuntimeError("npx not found — install Node.js to export PNGs")
    subprocess.run(
        [npx, "--yes", "@resvg/resvg-js-cli", "--fit-width", str(fit_width), str(svg), str(png)],
        check=True,
    )


def main() -> None:
    _ASSETS.mkdir(parents=True, exist_ok=True)
    for svg_name, width in _EXPORTS:
        svg = _ASSETS / svg_name
        if not svg.is_file():
            print(f"SKIP missing {svg}")
            continue
        png = _ASSETS / svg_name.replace(".svg", ".png")
        try:
            _resvg(svg, png, width)
            print(f"OK {png.name} ({png.stat().st_size} bytes)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {svg_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
