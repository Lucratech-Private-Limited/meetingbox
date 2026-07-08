"""Preview the processing screen layout (Figma 397:261) on 1260×800."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from processing_layout import (  # noqa: E402
    BACK_BTN,
    BG_RGB,
    CANVAS_H,
    CANVAS_W,
    CHECK_BADGE,
    DOT_SEPARATOR,
    DURATION_FS_RATIO,
    DURATION_LABEL,
    HEADLINE_BOTTOM,
    HEADLINE_FS_RATIO,
    HEADLINE_LABEL,
    LISTENING_PILL,
    NOTIFY_BAR,
    ORB_GLOW,
    RING_GLOW,
    RING_LIGHTEN,
    RING_OUTER,
    RING_SOLID,
    SETTINGS_BTN,
    STEPS_CARD,
    SUBTITLE_BOTTOM,
    SUBTITLE_FS_RATIO,
    TITLE_FS_RATIO,
    TITLE_LABEL,
    font_px,
    scaled_canvas,
)

ASSETS = ROOT / "assets" / "processing" / "figma"
OUT = ASSETS / "processing_layout_preview.png"

# Default render at the Figma reference resolution. ``--all`` (or any extra
# argv) additionally renders the layout at every entry in ``_RESOLUTIONS`` so
# we can verify the screen behaves on tiny kiosk panels and big HDMI monitors.
SCREEN_W, SCREEN_H = 1260, 800

_RESOLUTIONS: tuple[tuple[int, int, str], ...] = (
    (1260, 800, "design_1260x800"),
    (1280, 720, "hd_16x9_1280x720"),
    (1024, 600, "kiosk_1024x600"),
    (800, 480, "tiny_800x480"),
    (1920, 1080, "fhd_1920x1080"),
    (600, 1024, "portrait_600x1024"),
)

_LAYERS_PROCESSING: tuple[tuple[str, dict], ...] = (
    ("orb_glow.png", ORB_GLOW),
    ("ring_glow.png", RING_GLOW),
    ("ring_lighten.png", RING_LIGHTEN),
    ("ring_solid.png", RING_SOLID),
    ("ring_outer.png", RING_OUTER),
    ("btn_back.png", BACK_BTN),
    ("listening_pill.png", LISTENING_PILL),
    ("btn_settings.png", SETTINGS_BTN),
    ("check_badge.png", CHECK_BADGE),
    ("dot_separator.png", DOT_SEPARATOR),
    ("steps_card.png", STEPS_CARD),
    ("notify_bar.png", NOTIFY_BAR),
)

# Same set minus notify_bar — replaced at runtime by the "View Meeting
# Summary" CTA button (drawn separately below).
_LAYERS_READY: tuple[tuple[str, dict], ...] = tuple(
    layer for layer in _LAYERS_PROCESSING if layer[0] != "notify_bar.png"
)


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


def _draw_view_summary_btn(draw, box, cw, ch, ox, oy, font) -> None:
    """Mirror the runtime ``_ViewSummaryButton`` widget for static previews."""
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    radius = max(2, int(38.139 * min(cw / 1260.0, ch / 800.0)))
    draw.rounded_rectangle(
        [x0, y0, x1, y1], radius=radius, fill=(0, 107, 249), outline=(63, 66, 83), width=2
    )
    draw.text(
        ((x0 + x1) // 2, (y0 + y1) // 2),
        "View Meeting Summary",
        fill=(255, 255, 255),
        font=font,
        anchor="mm",
    )


def _render(screen_w: int, screen_h: int, out_path: Path, *, ready: bool = False) -> None:
    """Render the processing screen layout at the given screen size."""
    img = Image.new("RGB", (screen_w, screen_h), BG_RGB)
    cw, ch = scaled_canvas(screen_w, screen_h)
    ox = (screen_w - cw) / 2
    oy = (screen_h - ch) / 2

    layers = _LAYERS_READY if ready else _LAYERS_PROCESSING
    for name, box in layers:
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
        ft_h = ImageFont.truetype("arialbd.ttf", font_px(HEADLINE_FS_RATIO, ch))
        ft_title = ImageFont.truetype("arial.ttf", font_px(TITLE_FS_RATIO, ch))
        ft_dur = ImageFont.truetype("arial.ttf", font_px(DURATION_FS_RATIO, ch))
        ft_sub = ImageFont.truetype("arial.ttf", font_px(SUBTITLE_FS_RATIO, ch))
        ft_cta = ImageFont.truetype("arialbd.ttf", font_px(HEADLINE_FS_RATIO, ch))
    except OSError:
        ft_h = ft_title = ft_dur = ft_sub = ft_cta = ImageFont.load_default()

    if ready:
        _draw_view_summary_btn(draw, NOTIFY_BAR, cw, ch, ox, oy, ft_cta)

    _draw_text(draw, HEADLINE_LABEL, cw, ch, ox, oy, "Recording complete", (255, 255, 255), ft_h, "lm")
    _draw_text(draw, TITLE_LABEL, cw, ch, ox, oy, "Product Sync", (182, 186, 242), ft_title, "lm")
    _draw_text(draw, DURATION_LABEL, cw, ch, ox, oy, "32min", (182, 186, 242), ft_dur, "lm")
    if ready:
        _draw_text(draw, HEADLINE_BOTTOM, cw, ch, ox, oy, "Analysis complete!", (255, 255, 255), ft_h, "lm")
        _draw_text(
            draw,
            SUBTITLE_BOTTOM,
            cw, ch, ox, oy,
            "Your meeting highlights, transcript, and action items are ready.",
            (182, 186, 242),
            ft_sub,
            "lm",
        )
    else:
        _draw_text(draw, HEADLINE_BOTTOM, cw, ch, ox, oy, "Summarizing your meeting...", (255, 255, 255), ft_h, "lm")
        _draw_text(draw, SUBTITLE_BOTTOM, cw, ch, ox, oy, "This may take a few seconds", (182, 186, 242), ft_sub, "lm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"Wrote {out_path} ({screen_w}x{screen_h})")


def main() -> None:
    _render(SCREEN_W, SCREEN_H, OUT)
    # Always render the "summary ready" variant next to the default one so
    # the CTA placement is obvious at a glance.
    _render(SCREEN_W, SCREEN_H, ASSETS / "processing_layout_preview_ready.png", ready=True)
    if len(sys.argv) > 1:
        for sw, sh, tag in _RESOLUTIONS:
            _render(sw, sh, ASSETS / f"processing_layout_preview_{tag}.png")


if __name__ == "__main__":
    main()
