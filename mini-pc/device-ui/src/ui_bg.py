"""Shared light-theme background + gradient helpers for the recording flow.

The new Meeting-BOX recording / processing / summary screens (Figma "Copy"
file) share a full-bleed slate wash background and purple vertical gradients.
Rather than ship exported bitmaps, we synthesise the gradients as tiny 1-px-wide
Kivy textures so the look is identical from a 7" panel to a 24" display and no
asset files are required.
"""

from __future__ import annotations

from pathlib import Path

from kivy.graphics import Color, Rectangle
from kivy.graphics.texture import Texture

try:
    from config import ASSETS_DIR as _ASSETS_DIR
except Exception:  # noqa: BLE001
    _ASSETS_DIR = None

# Shared light paper-swirl background exported from Figma (node "Layer 35 1").
# Used by the recording / processing / summary screens on top of a near-white
# wash, matching the "Copy" design file.
SWIRL_BG_PATH = (
    str(Path(_ASSETS_DIR) / "figma" / "bg_swirl.png") if _ASSETS_DIR else ""
)


def vertical_gradient_texture(top: tuple, bottom: tuple, n: int = 256) -> Texture:
    """A 1×``n`` RGBA texture that fades ``top`` (top edge) → ``bottom`` (bottom).

    Kivy texture row 0 is the *bottom* of the rectangle it is mapped onto, so we
    write the bottom colour first and the top colour last.
    """
    tex = Texture.create(size=(1, n), colorfmt="rgba")
    buf = bytearray()
    for i in range(n):
        t = i / (n - 1) if n > 1 else 1.0  # 0 = bottom row, 1 = top row
        for c in range(4):
            top_c = top[c] if c < len(top) else 1.0
            bot_c = bottom[c] if c < len(bottom) else 1.0
            v = bot_c * (1.0 - t) + top_c * t
            buf.append(int(max(0.0, min(1.0, v)) * 255))
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    return tex


def attach_gradient_bg(widget, top: tuple, bottom: tuple) -> Rectangle:
    """Paint a full-bleed vertical gradient behind *widget* and keep it sized.

    Returns the backing :class:`~kivy.graphics.Rectangle` (mostly for tests).
    """
    tex = vertical_gradient_texture(top, bottom)
    with widget.canvas.before:
        Color(1, 1, 1, 1)
        rect = Rectangle(pos=widget.pos, size=widget.size, texture=tex)
    widget.bind(
        pos=lambda w, _v: setattr(rect, "pos", w.pos),
        size=lambda w, _v: setattr(rect, "size", w.size),
    )
    return rect


def attach_swirl_bg(
    widget,
    fallback_top: tuple,
    fallback_bottom: tuple,
    *,
    overlay=(1.0, 1.0, 1.0, 0.40),
    image_path: str | None = None,
) -> Rectangle:
    """Paint the light Figma paper-swirl behind *widget* with a white wash.

    Falls back to a vertical gradient when the bitmap is missing so the
    screens still render on a fresh checkout. The image is stretched to fill
    (the artwork is a soft, near-white abstract so minor aspect skew at odd
    panel ratios is imperceptible). A translucent white *overlay* lightens it
    to match the Figma ``rgba(255,255,255,0.45)`` wash.
    """
    path = image_path if image_path is not None else SWIRL_BG_PATH
    if not path or not Path(path).is_file():
        return attach_gradient_bg(widget, fallback_top, fallback_bottom)

    from kivy.core.image import Image as CoreImage

    tex = CoreImage(path).texture
    with widget.canvas.before:
        Color(1, 1, 1, 1)
        rect = Rectangle(pos=widget.pos, size=widget.size, texture=tex)
        Color(*overlay)
        wash = Rectangle(pos=widget.pos, size=widget.size)

    def _sync(w, _v):
        rect.pos = w.pos
        rect.size = w.size
        wash.pos = w.pos
        wash.size = w.size

    widget.bind(pos=_sync, size=_sync)
    return rect
