"""Preview the recording screen layout (Figma 863:626) on 1260×800."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import math  # noqa: E402
import random  # noqa: E402

from frame19_layout import (  # noqa: E402
    BACK_BTN,
    BG_RGB,
    BTN_PAUSE,
    BTN_SETTINGS,
    COL_GLOW_BLUE,
    COL_REC_DOT_RED,
    LEFT_VEC,
    REC_DOT,
    REC_LABEL,
    REC_LABEL_FS_RATIO,
    RIGHT_VEC,
    RING_DARK,
    RING_GLOW,
    RING_GRADIENT,
    STARTED_FS_RATIO,
    STARTED_LABEL,
    STATUS,
    STATUS_FS_RATIO,
    STOP_PILL,
    TIMER,
    TIMER_FS_RATIO,
    WAVEBAR,
    font_px,
    scaled_canvas,
)

ASSETS = ROOT / "assets" / "recording" / "figma"
OUT = ASSETS / "recording_layout_preview.png"
SCREEN_W, SCREEN_H = 1260, 800

# Layered PNG composite, back → front. The glow ring is handled separately
# below so it can be tinted blue at preview-time (matches the runtime
# ``Image.color = COL_GLOW_BLUE`` we apply in ``screens/recording.py``).
# ``icon_rec_dot_red.png`` is intentionally omitted — the recording dot is
# drawn here with ``ImageDraw.ellipse`` (the bundled PNG is solid black).
_LAYERS: tuple[tuple[str, dict], ...] = (
    ("frame19_ring_dark.png", RING_DARK),
    ("frame19_ring_gradient.png", RING_GRADIENT),
    ("frame19_vector_left.png", LEFT_VEC),
    ("frame19_vector_right.png", RIGHT_VEC),
    ("btn_back.png", BACK_BTN),
    ("btn_pause.png", BTN_PAUSE),
    ("stop_recording_pill.png", STOP_PILL),
    ("btn_settings.png", BTN_SETTINGS),
)


def _rgba_255(color: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Convert a Kivy 0-1 RGBA tuple to PIL's 0-255 tuple."""
    r, g, b, a = color
    return int(r * 255), int(g * 255), int(b * 255), int(a * 255)


def _tinted_glow(path: Path, tint_rgba: tuple[int, int, int, int]) -> Image.Image:
    """Load the greyscale glow PNG and multiply its RGB by ``tint_rgba``.

    Pillow doesn't expose Kivy's ``Image.color`` (which multiplies the
    sampled texel by the colour), so we replicate it manually: keep the
    halo's alpha channel intact while replacing its RGB with the tint
    scaled by the original brightness. The result is the navy → blue
    falloff you see at runtime.
    """
    src = Image.open(path).convert("RGBA")
    r_src, g_src, b_src, a_src = src.split()
    tr, tg, tb, ta = tint_rgba
    luma = Image.eval(
        Image.merge("RGB", (r_src, g_src, b_src)).convert("L"),
        lambda v: v,
    )
    r_out = luma.point(lambda v, t=tr: int(v * t / 255))
    g_out = luma.point(lambda v, t=tg: int(v * t / 255))
    b_out = luma.point(lambda v, t=tb: int(v * t / 255))
    a_out = a_src.point(lambda v, t=ta: int(v * t / 255))
    return Image.merge("RGBA", (r_out, g_out, b_out, a_out))


def _rect(box, cw, ch, ox, oy):
    x0 = int(ox + box["x"] * cw)
    y0 = int(oy + box["y_top"] * ch)
    x1 = int(ox + (box["x"] + box["w"]) * cw)
    y1 = int(oy + (box["y_top"] + box["h"]) * ch)
    return x0, y0, x1, y1


def _draw_text(draw, box, cw, ch, ox, oy, text, fill, font, anchor="lm"):
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    if anchor == "lm":
        draw.text((x0, (y0 + y1) // 2), text, fill=fill, font=font, anchor="lm")
    elif anchor == "ma":
        draw.text(((x0 + x1) // 2, y0), text, fill=fill, font=font, anchor="ma")


def _draw_wavebar(
    draw,
    box,
    cw,
    ch,
    ox,
    oy,
    *,
    level: float = 0.7,
    n_bars: int = 21,
    color: tuple = (0, 107, 249),
    seed: int = 7,
) -> None:
    """Approximate the runtime ``_Wavebar`` widget at a given amplitude.

    Mirrors the bell-shaped envelope + jitter used in
    ``screens/recording.py`` so the preview looks like a mid-speech frame.
    """
    rng = random.Random(seed)
    jitter = [rng.uniform(0.65, 1.0) for _ in range(n_bars)]
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    bar_w = max(1.0, (w * 0.45) / n_bars)
    total_bars = bar_w * n_bars
    gap = (w - total_bars) / max(1, n_bars - 1)
    max_h = h * 0.96
    min_h = max(2.0, h * 0.10)
    cy = y0 + h / 2.0
    centre = (n_bars - 1) / 2.0
    radius = max(1, int(bar_w / 2.0))
    for i in range(n_bars):
        d = (i - centre) / centre
        bell = max(0.0, math.cos(d * math.pi / 2.0))
        amp = level * (0.35 + 0.65 * bell) * jitter[i]
        bar_h = min_h + (max_h - min_h) * amp
        bx0 = x0 + i * (bar_w + gap)
        bx1 = bx0 + bar_w
        by0 = cy - bar_h / 2.0
        by1 = cy + bar_h / 2.0
        draw.rounded_rectangle(
            [bx0, by0, bx1, by1], radius=radius, fill=color
        )


def main() -> None:
    img = Image.new("RGB", (SCREEN_W, SCREEN_H), BG_RGB)
    cw, ch = scaled_canvas(SCREEN_W, SCREEN_H)
    ox = (SCREEN_W - cw) / 2
    oy = (SCREEN_H - ch) / 2

    # Glow halo — pre-tint blue so the preview matches runtime.
    glow_path = ASSETS / "frame19_ring_glow.png"
    if glow_path.is_file():
        glow = _tinted_glow(glow_path, _rgba_255(COL_GLOW_BLUE))
        x0, y0, x1, y1 = _rect(RING_GLOW, cw, ch, ox, oy)
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        glow = glow.resize((w, h), Image.Resampling.LANCZOS)
        img.paste(glow, (x0, y0), glow)
    else:
        print("SKIP frame19_ring_glow.png")

    for name, box in _LAYERS:
        path = ASSETS / name
        if not path.is_file():
            print(f"SKIP {name}")
            continue
        asset = Image.open(path).convert("RGBA")
        x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        asset = asset.resize((w, h), Image.Resampling.LANCZOS)
        img.paste(asset, (x0, y0), asset)

    draw = ImageDraw.Draw(img)
    try:
        ft_timer = ImageFont.truetype("arialbd.ttf", font_px(TIMER_FS_RATIO, ch))
        ft_status = ImageFont.truetype("arialbd.ttf", font_px(STATUS_FS_RATIO, ch))
        ft_rec = ImageFont.truetype("arialbd.ttf", font_px(REC_LABEL_FS_RATIO, ch))
        ft_started = ImageFont.truetype("arial.ttf", font_px(STARTED_FS_RATIO, ch))
    except OSError:
        ft_timer = ft_status = ft_rec = ft_started = ImageFont.load_default()

    # Voice wavebar (Group 46) — simulated mid-speech amplitude.
    _draw_wavebar(draw, WAVEBAR, cw, ch, ox, oy, level=0.75)

    # Recording status dot — solid red ellipse drawn over the bg (the
    # PNG export is solid black and unusable, see ``_StatusDot`` in
    # ``screens/recording.py``).
    dot_x0, dot_y0, dot_x1, dot_y1 = _rect(REC_DOT, cw, ch, ox, oy)
    draw.ellipse([dot_x0, dot_y0, dot_x1, dot_y1], fill=_rgba_255(COL_REC_DOT_RED))

    _draw_text(draw, TIMER, cw, ch, ox, oy, "00 : 12 : 45", (255, 255, 255), ft_timer, "ma")
    _draw_text(draw, STATUS, cw, ch, ox, oy, "Recording in progress", (182, 186, 242), ft_status, "ma")
    _draw_text(draw, REC_LABEL, cw, ch, ox, oy, "Recording...", (255, 255, 255), ft_rec, "lm")
    _draw_text(draw, STARTED_LABEL, cw, ch, ox, oy, "Started at 11:01 AM", (182, 186, 242), ft_started, "lm")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
