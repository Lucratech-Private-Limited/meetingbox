"""Generate a placeholder native-splash image for the Windows build.

Produces ``splash.png`` shown instantly by the PyInstaller bootloader while the
Kivy UI loads. Replace with final branded art later (keep the same filename).

Run:  python packaging/windows/make_splash.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "splash.png"
W, H = 640, 400
BG = (11, 11, 15, 255)          # near-black, matches the in-app splash
ACCENT = (90, 150, 255, 255)    # blue accent
WHITE = (245, 245, 248, 255)
GRAY = (150, 150, 160, 255)


def _font(size: int, bold: bool = False):
    candidates = (
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _centered(draw, text, font, y, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text(((W - w) / 2, y), text, font=font, fill=fill)


def main() -> None:
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Simple rounded accent square as a logo placeholder.
    cx, top = W // 2, 96
    s = 72
    draw.rounded_rectangle(
        [cx - s // 2, top, cx + s // 2, top + s],
        radius=18,
        fill=ACCENT,
    )
    draw.text(
        (cx - 13, top + 14),
        "P",
        font=_font(46, bold=True),
        fill=(11, 11, 15, 255),
    )

    _centered(draw, "Pepper AI", _font(40, bold=True), 210, WHITE)
    _centered(draw, "Starting\u2026", _font(20), 272, GRAY)

    img.save(OUT, "PNG")
    print(f"wrote {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
