"""Rasterize email-flow Figma SVG assets into bitmap PNGs for Kivy.

Source: Meeting BOX AI file ``dvqlN0JtWQODt6jYbTrbDG``.
  - icon_mail  → ``tabler:mail-filled`` (node 3027:2021), the white envelope in
    the recipient-picker pop-up header (frame ``System States_4``, 3027:1967).

The SVGs are pulled with the Framelink Figma MCP ``download_figma_images`` tool
into ``assets/email/figma/<name>.svg``. Kivy cannot render SVG reliably at
runtime, so this script converts each SVG to a PNG via the ``resvg-js`` CLI
(same toolchain as ``scripts/export_recording_frame19_pngs.py``). Requires
``npx`` (Node.js).

Run from the device-ui directory::

    python scripts/download_email_assets.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "email" / "figma"

# svg name → render width (3× the 68px Figma frame keeps edges crisp on kiosk).
_EXPORTS: tuple[tuple[str, int], ...] = (
    ("icon_mail.svg", 204),
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
