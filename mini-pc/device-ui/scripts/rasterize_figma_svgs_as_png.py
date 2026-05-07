"""Rewrite assets/home/figma/*.png that are actually SVG into real raster PNGs.

Figma MCP asset URLs are often SVG data saved with a ``.png`` name. Kivy loads
those inconsistently (soft edges, wrong tone). This script rasterizes each
file using the ``resvg`` Python bindings (pure Rust resvg, no system cairo).

Run from repo after pulling or refreshing Figma exports::

    pip install resvg
    python scripts/rasterize_figma_svgs_as_png.py
"""
from __future__ import annotations

import re
from pathlib import Path

import resvg

_ROOT = Path(__file__).resolve().parent.parent / "assets" / "home" / "figma"

# 3× viewBox size → enough pixels for ``fit_mode='contain'`` on kiosk panels.
_SCALE = 3.0


def _sanitize_svg(s: str) -> str:
    return re.sub(r"var\(\s*--[^,]+,\s*([^)]+)\)", r"\1", s)


def _is_svg_text(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:200].lstrip("\ufeff")
    except Exception:
        return False
    return head.startswith("<svg") or head.startswith("<?xml")


def _render_one(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")
    if not _is_svg_text(path):
        return False
    sanitized = _sanitize_svg(raw)
    try:
        opts = resvg.usvg.Options.default()
        tree = resvg.usvg.Tree.from_str(sanitized, opts)
    except Exception as exc:
        print(f"skip parse {path.name}: {exc}")
        return False
    tr = (_SCALE, 0.0, 0.0, _SCALE, 0.0, 0.0)
    try:
        png = resvg.render(tree, tr)
    except Exception as exc:
        print(f"skip render {path.name}: {exc}")
        return False
    if len(png) < 32:
        print(f"skip tiny {path.name}")
        return False
    path.write_bytes(png)
    print(f"OK {path.name} ({len(png)} bytes)")
    return True


def main() -> None:
    if not _ROOT.is_dir():
        raise SystemExit(f"missing {_ROOT}")
    n = 0
    for p in sorted(_ROOT.glob("*.png")):
        if _is_svg_text(p):
            if _render_one(p):
                n += 1
    print(f"rasterized {n} pseudo-PNG SVGs to bitmap PNG")


if __name__ == "__main__":
    main()
