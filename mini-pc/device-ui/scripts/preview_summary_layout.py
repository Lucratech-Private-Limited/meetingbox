"""Preview the Meeting Summary v2 dashboard layout.

Renders a static PNG that mirrors the runtime Kivy screen so layout
adjustments can be reviewed without booting the device-ui. Produces
a default 1260x800 preview plus optional multi-resolution renders
when called with any CLI argument.

By default it renders the ``overview`` tab; pass ``--tab=key_points``
(etc.) to preview a different tab.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from summary_layout import (  # noqa: E402
    ACCENT_BLUE,
    BACK_BTN,
    BG_RGB,
    CANVAS_H,
    CANVAS_W,
    CARD_BORDER,
    CARD_FILL,
    CARD_RADIUS,
    CONTENT_AREA,
    FOOTER_FS_RATIO,
    FOOTER_LEFT,
    FOOTER_RIGHT,
    FULL_TAB_CARD,
    META_CARD,
    META_DATE,
    META_DATE_FS_RATIO,
    META_EXPORT,
    META_FILE_ICON,
    META_PARTICIPANTS,
    META_RECORDED,
    META_SHARE,
    META_TITLE,
    META_TITLE_FS_RATIO,
    OV_ACTIONS_CARD,
    OV_AI_CARD,
    OV_DECISIONS_CARD,
    OV_KEY_CARD,
    PAGE_TITLE,
    PAGE_TITLE_FS_RATIO,
    PLAY_BORDER,
    PLAY_FILL,
    PLAY_RADIUS,
    PLAY_RECORDING,
    PLAY_RECORDING_FS_RATIO,
    PROG_FILL,
    PROG_RADIUS,
    PROG_TRACK_FILL,
    SECTION_BODY_FS_RATIO,
    SECTION_HINT_FS_RATIO,
    SECTION_TITLE_FS_RATIO,
    SIDEBAR_BORDER,
    SIDEBAR_CARD,
    SIDEBAR_FILL,
    TAB_ACTION_ITEMS,
    TAB_ACTIVE_BORDER,
    TAB_ACTIVE_FILL,
    TAB_ACTIVE_RADIUS,
    TAB_DECISIONS,
    TAB_FS_RATIO,
    TAB_KEY_POINTS,
    TAB_OVERVIEW,
    TAB_PARTICIPANTS,
    TAB_TRANSCRIPT,
    canvas_box,
    content_header,
    font_px,
    scaled_canvas,
)

ASSETS = ROOT / "assets" / "summary" / "figma"
OUT_BASENAME = "summary_layout_preview"

SCREEN_W, SCREEN_H = 1260, 800

_RESOLUTIONS: tuple[tuple[int, int, str], ...] = (
    (1260, 800, "design_1260x800"),
    (1280, 720, "hd_16x9_1280x720"),
    (1024, 600, "kiosk_1024x600"),
    (800, 480, "tiny_800x480"),
    (1920, 1080, "fhd_1920x1080"),
)

_SIDEBAR_TABS = (
    ("overview", "Overview", TAB_OVERVIEW),
    ("key_points", "Key Points", TAB_KEY_POINTS),
    ("action_items", "Action Items", TAB_ACTION_ITEMS),
    ("decisions", "Decisions Made", TAB_DECISIONS),
    ("participants", "Participants", TAB_PARTICIPANTS),
    ("transcript", "Transcript", TAB_TRANSCRIPT),
)


def _rect(box, cw, ch, ox, oy):
    x0 = int(ox + box["x"] * cw)
    y0 = int(oy + box["y_top"] * ch)
    x1 = int(ox + (box["x"] + box["w"]) * cw)
    y1 = int(oy + (box["y_top"] + box["h"]) * ch)
    return x0, y0, x1, y1


def _draw_card(draw, box, cw, ch, ox, oy, *, fill=CARD_FILL, border=CARD_BORDER, radius=CARD_RADIUS):
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    r = max(2, int(radius * min(cw / 1260.0, ch / 800.0)))
    fill_rgb = tuple(int(c * 255) for c in fill[:3])
    border_rgb = tuple(int(c * 255) for c in border[:3])
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill_rgb, outline=border_rgb, width=2)


def _draw_progress(draw, box, cw, ch, ox, oy, value: float):
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    r = max(1, int(PROG_RADIUS * min(cw / 1260.0, ch / 800.0)))
    track_rgb = tuple(int(c * 255) for c in PROG_TRACK_FILL[:3])
    fill_rgb = tuple(int(c * 255) for c in PROG_FILL[:3])
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=track_rgb)
    fw = max(0, int((x1 - x0) * max(0.0, min(1.0, value))))
    if fw > 0:
        draw.rounded_rectangle([x0, y0, x0 + fw, y1], radius=r, fill=fill_rgb)


def _draw_text(draw, box, cw, ch, ox, oy, text, fill, font, anchor="lm"):
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    if anchor == "lm":
        draw.text((x0, (y0 + y1) // 2), text, fill=fill, font=font, anchor="lm")
    elif anchor == "rm":
        draw.text((x1, (y0 + y1) // 2), text, fill=fill, font=font, anchor="rm")
    elif anchor == "ma":
        draw.text(((x0 + x1) // 2, y0), text, fill=fill, font=font, anchor="ma")


def _draw_wrap(draw, box, cw, ch, ox, oy, text, fill, font, max_lines: int = 3):
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    max_w = x1 - x0
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and cur:
        last = lines[-1]
        while draw.textlength(last + "...", font=font) > max_w and " " in last:
            last = last.rsplit(" ", 1)[0]
        lines[-1] = last + "..."
    line_h = max(font.size + 4, 1)
    for i, ln in enumerate(lines):
        draw.text((x0, y0 + i * line_h), ln, fill=fill, font=font, anchor="la")


def _paste(img, filename: str, box, cw, ch, ox, oy):
    path = ASSETS / filename
    if not path.is_file():
        return
    asset = Image.open(path).convert("RGBA")
    x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    asset = asset.resize((w, h), Image.Resampling.LANCZOS)
    img.paste(asset, (x0, y0), asset)


def _safe_font(name: str, size: int):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def _render(screen_w: int, screen_h: int, tab: str, out_path: Path) -> None:
    img = Image.new("RGB", (screen_w, screen_h), BG_RGB)
    cw, ch = scaled_canvas(screen_w, screen_h)
    ox = (screen_w - cw) / 2
    oy = (screen_h - ch) / 2
    draw = ImageDraw.Draw(img)

    # Header
    _paste(img, "btn_back.png", BACK_BTN, cw, ch, ox, oy)
    ft_title = _safe_font("arialbd.ttf", font_px(PAGE_TITLE_FS_RATIO, ch))
    _draw_text(draw, PAGE_TITLE, cw, ch, ox, oy, "Meeting Summary", (255, 255, 255), ft_title, "lm")

    # Meta card
    _draw_card(draw, META_CARD, cw, ch, ox, oy)
    _paste(img, "icon_file_box.png", META_FILE_ICON, cw, ch, ox, oy)
    ft_meta_title = _safe_font("arialbd.ttf", font_px(META_TITLE_FS_RATIO, ch))
    ft_meta_date = _safe_font("arial.ttf", font_px(META_DATE_FS_RATIO, ch))
    _draw_text(draw, META_TITLE, cw, ch, ox, oy, "Product Sync", (255, 255, 255), ft_meta_title, "lm")
    _draw_text(
        draw,
        META_DATE,
        cw,
        ch,
        ox,
        oy,
        "May 21, 11:00 AM  ·  45 min",
        (182, 186, 242),
        ft_meta_date,
        "lm",
    )
    _paste(img, "chip_participants.png", META_PARTICIPANTS, cw, ch, ox, oy)
    _paste(img, "chip_recorded.png", META_RECORDED, cw, ch, ox, oy)
    _paste(img, "btn_export.png", META_EXPORT, cw, ch, ox, oy)
    _paste(img, "btn_share.png", META_SHARE, cw, ch, ox, oy)

    # Sidebar
    _draw_card(draw, SIDEBAR_CARD, cw, ch, ox, oy, fill=SIDEBAR_FILL, border=SIDEBAR_BORDER)
    ft_tab = _safe_font("arialbd.ttf", font_px(TAB_FS_RATIO, ch))
    for tid, label, box in _SIDEBAR_TABS:
        if tid == tab:
            _draw_card(
                draw,
                box,
                cw,
                ch,
                ox,
                oy,
                fill=TAB_ACTIVE_FILL,
                border=TAB_ACTIVE_BORDER,
                radius=TAB_ACTIVE_RADIUS,
            )
            text_color = (255, 255, 255)
        else:
            text_color = (182, 186, 242)
        x0, y0, x1, y1 = _rect(box, cw, ch, ox, oy)
        pad = max(12, int((x1 - x0) * 0.06))
        glyph_w = max(12, int((y1 - y0) * 0.42))
        glyph_cx = x0 + pad + glyph_w / 2
        glyph_cy = (y0 + y1) / 2
        draw.ellipse(
            [glyph_cx - 3, glyph_cy - 3, glyph_cx + 3, glyph_cy + 3],
            fill=tuple(int(c * 255) for c in ACCENT_BLUE[:3]) if tid == tab else text_color,
        )
        draw.text((x0 + pad + glyph_w + 8, (y0 + y1) // 2), label, fill=text_color, font=ft_tab, anchor="lm")

    # Play recording pill
    _draw_card(
        draw,
        PLAY_RECORDING,
        cw,
        ch,
        ox,
        oy,
        fill=PLAY_FILL,
        border=PLAY_BORDER,
        radius=PLAY_RADIUS,
    )
    x0, y0, x1, y1 = _rect(PLAY_RECORDING, cw, ch, ox, oy)
    tri_h = (y1 - y0) * 0.4
    tri_w = tri_h * 0.85
    cx = x0 + max(16, (x1 - x0) * 0.08)
    cy = (y0 + y1) / 2
    accent = tuple(int(c * 255) for c in ACCENT_BLUE[:3])
    draw.polygon(
        [(cx, cy - tri_h / 2), (cx, cy + tri_h / 2), (cx + tri_w, cy)],
        fill=accent,
    )
    ft_play = _safe_font("arialbd.ttf", font_px(PLAY_RECORDING_FS_RATIO, ch))
    draw.text(
        (cx + tri_w + 10, cy),
        "Play Recording",
        fill=(255, 255, 255),
        font=ft_play,
        anchor="lm",
    )
    # Duration string omitted in the preview; the runtime pill only shows
    # a value when the meeting has an actual duration.

    # Content area — render the requested tab
    if tab == "overview":
        _render_overview(img, draw, cw, ch, ox, oy)
    elif tab == "key_points":
        _render_full_tab(img, draw, cw, ch, ox, oy, "Key Points", _SAMPLE_KEY_POINTS_BODY)
    elif tab == "action_items":
        _render_full_tab(img, draw, cw, ch, ox, oy, "Action Items", _SAMPLE_ACTIONS_BODY)
    elif tab == "decisions":
        _render_full_tab(img, draw, cw, ch, ox, oy, "Decisions Made", _SAMPLE_DECISIONS_BODY)
    elif tab == "participants":
        _render_full_tab(img, draw, cw, ch, ox, oy, "Participants", _SAMPLE_PARTICIPANTS_BODY)
    elif tab == "transcript":
        _render_full_tab(img, draw, cw, ch, ox, oy, "Transcript", _SAMPLE_TRANSCRIPT_BODY)

    # Footer
    ft_footer = _safe_font("arial.ttf", font_px(FOOTER_FS_RATIO, ch))
    _draw_text(
        draw,
        FOOTER_LEFT,
        cw,
        ch,
        ox,
        oy,
        "Created: May 21, 11:45 AM",
        (155, 162, 178),
        ft_footer,
        "lm",
    )
    _draw_text(
        draw,
        FOOTER_RIGHT,
        cw,
        ch,
        ox,
        oy,
        "Generated by AI",
        (155, 162, 178),
        ft_footer,
        "rm",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"Wrote {out_path} ({screen_w}x{screen_h}, tab={tab})")


def _render_overview(img, draw, cw, ch, ox, oy):
    white = (255, 255, 255)
    muted = (182, 186, 242)
    hint = (155, 162, 178)
    accent = tuple(int(c * 255) for c in ACCENT_BLUE[:3])

    ft_section = _safe_font("arialbd.ttf", font_px(SECTION_TITLE_FS_RATIO, ch))
    ft_body = _safe_font("arial.ttf", font_px(SECTION_BODY_FS_RATIO, ch))
    ft_hint = _safe_font("arial.ttf", font_px(SECTION_HINT_FS_RATIO, ch))

    # AI Summary
    _draw_card(draw, OV_AI_CARD, cw, ch, ox, oy)
    icon_box, title_box = content_header(OV_AI_CARD, icon_w=30.0, title_w=200.0)
    _paste(img, "ai_summary_icon.png", icon_box, cw, ch, ox, oy)
    _draw_text(draw, title_box, cw, ch, ox, oy, "AI Summary", white, ft_section, "lm")
    ai_body = canvas_box(
        OV_AI_CARD["x"] * CANVAS_W + 28.0,
        OV_AI_CARD["y_top"] * CANVAS_H + 62.0,
        OV_AI_CARD["w"] * CANVAS_W - 56.0,
        OV_AI_CARD["h"] * CANVAS_H - 72.0,
    )
    _draw_wrap(
        draw,
        ai_body,
        cw,
        ch,
        ox,
        oy,
        "The team aligned on the initial release plan, discussed budget constraints, and "
        "identified key risks. The launch is delayed by 2 weeks to accommodate the required "
        "changes and ensure quality.",
        hint,
        ft_body,
        max_lines=3,
    )

    # Key Topics
    _draw_card(draw, OV_KEY_CARD, cw, ch, ox, oy)
    ki_box, kt_box = content_header(OV_KEY_CARD, icon_w=26.0, title_w=200.0)
    _draw_text(draw, ki_box, cw, ch, ox, oy, "\u25c6", accent, ft_section, "lm")
    _draw_text(draw, kt_box, cw, ch, ox, oy, "Key Topics", white, ft_section, "lm")
    topic_area_x = OV_KEY_CARD["x"] * CANVAS_W + 28.0
    topic_area_y = OV_KEY_CARD["y_top"] * CANVAS_H + 58.0
    topic_area_w = OV_KEY_CARD["w"] * CANVAS_W - 56.0
    topic_area_h = OV_KEY_CARD["h"] * CANVAS_H - 70.0
    cell_w = (topic_area_w - 28.0) / 2.0
    cell_h = topic_area_h / 2.0
    NAME_H = 26.0
    BAR_OFFSET = NAME_H + 10.0
    BAR_H = 8.0
    sample_topics = [
        ("Product Strategy", 35),
        ("Engineering", 28),
        ("Marketing", 22),
        ("Operations", 15),
    ]
    for i, (name, val) in enumerate(sample_topics):
        col = i % 2
        row = i // 2
        x = topic_area_x + col * (cell_w + 28.0)
        y = topic_area_y + row * cell_h
        name_box = canvas_box(x, y, cell_w * 0.7, NAME_H)
        pct_box = canvas_box(x + cell_w * 0.7, y, cell_w * 0.3, NAME_H)
        bar_box = canvas_box(x, y + BAR_OFFSET, cell_w, BAR_H)
        _draw_text(draw, name_box, cw, ch, ox, oy, name, muted, ft_body, "lm")
        _draw_text(draw, pct_box, cw, ch, ox, oy, f"{val}%", hint, ft_hint, "rm")
        _draw_progress(draw, bar_box, cw, ch, ox, oy, val / 100.0)

    # Action Items (compact)
    _draw_card(draw, OV_ACTIONS_CARD, cw, ch, ox, oy)
    ai2_box, at_box = content_header(OV_ACTIONS_CARD, icon_w=26.0, title_w=240.0)
    _paste(img, "action_items_icon.png", ai2_box, cw, ch, ox, oy)
    _draw_text(draw, at_box, cw, ch, ox, oy, "Action Items", white, ft_section, "lm")
    va_box = canvas_box(
        OV_ACTIONS_CARD["x"] * CANVAS_W + OV_ACTIONS_CARD["w"] * CANVAS_W - 130.0,
        OV_ACTIONS_CARD["y_top"] * CANVAS_H + 20.0,
        110.0,
        28.0,
    )
    _draw_text(draw, va_box, cw, ch, ox, oy, "View all \u2192", accent, ft_hint, "rm")
    a_area_x = OV_ACTIONS_CARD["x"] * CANVAS_W + 28.0
    a_area_y = OV_ACTIONS_CARD["y_top"] * CANVAS_H + 60.0
    a_area_w = OV_ACTIONS_CARD["w"] * CANVAS_W - 56.0
    a_area_h = OV_ACTIONS_CARD["h"] * CANVAS_H - 70.0
    row_h_a = a_area_h / 3.0
    sample_actions = [
        ("Update client on timeline", "Rahul S.  ·  May 23"),
        ("Revise project plan", "Neha S.  ·  May 23"),
        ("Share new budget estimates", "Arjun M.  ·  May 23"),
    ]
    for i, (task, meta) in enumerate(sample_actions):
        ry = a_area_y + i * row_h_a
        dot_box = canvas_box(a_area_x, ry + row_h_a * 0.28, 8.0, 8.0)
        x0, y0, x1, y1 = _rect(dot_box, cw, ch, ox, oy)
        draw.ellipse([x0, y0, x1, y1], fill=accent)
        task_box = canvas_box(a_area_x + 18.0, ry, a_area_w - 18.0, row_h_a * 0.55)
        meta_box = canvas_box(a_area_x + 18.0, ry + row_h_a * 0.5, a_area_w - 18.0, row_h_a * 0.45)
        _draw_text(draw, task_box, cw, ch, ox, oy, task, white, ft_body, "lm")
        _draw_text(draw, meta_box, cw, ch, ox, oy, meta, hint, ft_hint, "lm")

    # Decisions Made (compact)
    _draw_card(draw, OV_DECISIONS_CARD, cw, ch, ox, oy)
    di_box, dt_box = content_header(OV_DECISIONS_CARD, icon_w=26.0, title_w=240.0)
    _paste(img, "decisions_icon.png", di_box, cw, ch, ox, oy)
    _draw_text(draw, dt_box, cw, ch, ox, oy, "Decisions Made", white, ft_section, "lm")
    vd_box = canvas_box(
        OV_DECISIONS_CARD["x"] * CANVAS_W + OV_DECISIONS_CARD["w"] * CANVAS_W - 130.0,
        OV_DECISIONS_CARD["y_top"] * CANVAS_H + 20.0,
        110.0,
        28.0,
    )
    _draw_text(draw, vd_box, cw, ch, ox, oy, "View all \u2192", accent, ft_hint, "rm")
    d_area_x = OV_DECISIONS_CARD["x"] * CANVAS_W + 28.0
    d_area_y = OV_DECISIONS_CARD["y_top"] * CANVAS_H + 60.0
    d_area_w = OV_DECISIONS_CARD["w"] * CANVAS_W - 56.0
    d_area_h = OV_DECISIONS_CARD["h"] * CANVAS_H - 70.0
    row_h_d = d_area_h / 3.0
    sample_decisions = [
        "Launch delayed by 2 weeks",
        "Focus on MVP for initial release",
        "Budget reallocated for priority features",
    ]
    for i, text in enumerate(sample_decisions):
        ry = d_area_y + i * row_h_d
        _paste(
            img,
            "decision_tick.png",
            canvas_box(d_area_x, ry + row_h_d * 0.25, 18.0, 18.0),
            cw,
            ch,
            ox,
            oy,
        )
        _draw_text(
            draw,
            canvas_box(d_area_x + 28.0, ry, d_area_w - 28.0, row_h_d * 0.85),
            cw,
            ch,
            ox,
            oy,
            text,
            muted,
            ft_body,
            "lm",
        )


def _render_full_tab(img, draw, cw, ch, ox, oy, title: str, body: str):
    white = (255, 255, 255)
    muted = (182, 186, 242)
    _draw_card(draw, FULL_TAB_CARD, cw, ch, ox, oy)
    _icon_box, title_box = content_header(FULL_TAB_CARD, icon_w=32.0, title_w=400.0)
    ft_section = _safe_font("arialbd.ttf", font_px(SECTION_TITLE_FS_RATIO, ch))
    ft_body = _safe_font("arial.ttf", font_px(SECTION_BODY_FS_RATIO, ch))
    _draw_text(draw, title_box, cw, ch, ox, oy, title, white, ft_section, "lm")
    body_box = canvas_box(
        FULL_TAB_CARD["x"] * CANVAS_W + 28.0,
        FULL_TAB_CARD["y_top"] * CANVAS_H + 72.0,
        FULL_TAB_CARD["w"] * CANVAS_W - 56.0,
        FULL_TAB_CARD["h"] * CANVAS_H - 92.0,
    )
    _draw_wrap(draw, body_box, cw, ch, ox, oy, body, muted, ft_body, max_lines=18)


_SAMPLE_KEY_POINTS_BODY = (
    "Product Strategy  35%   |   Engineering  28%   |   Marketing  22%   |   Operations  15%   "
    "—  Topic breakdown is sourced from the OpenAI summary response."
)

_SAMPLE_ACTIONS_BODY = (
    "Update client on timeline  ·  Rahul S.  ·  May 23   "
    "Revise project plan  ·  Neha S.  ·  May 23   "
    "Share new budget estimates  ·  Arjun M.  ·  May 23   "
    "Prepare MVP requirements doc  ·  Vivek K.  ·  May 24   "
    "(scrollable list of all action items from the summary)"
)

_SAMPLE_DECISIONS_BODY = (
    "Launch delayed by 2 weeks.   Focus on MVP for initial release.   "
    "Budget reallocated for priority features.   Weekly syncs to continue every Tuesday.   "
    "(scrollable list of all decisions from the summary)"
)

_SAMPLE_PARTICIPANTS_BODY = (
    "Rahul S., Neha S., Arjun M., Vivek K., …  (resolved from calendar attendees or, if "
    "unavailable, distinct action-item assignees)."
)

_SAMPLE_TRANSCRIPT_BODY = (
    "[00:00] Speaker 1: Welcome everyone, let's begin with the product update.   "
    "[00:18] Speaker 2: Thanks. We've finalised the MVP scope and identified the launch risks.   "
    "[01:02] Speaker 1: Great, let's discuss the timeline implications.   "
    "(scrollable transcript continues for the duration of the meeting)"
)


def main() -> None:
    tab = "overview"
    do_all = False
    for arg in sys.argv[1:]:
        if arg.startswith("--tab="):
            tab = arg.split("=", 1)[1]
        elif arg == "--all":
            do_all = True
    out = ASSETS / f"{OUT_BASENAME}.png"
    _render(SCREEN_W, SCREEN_H, tab, out)
    if do_all:
        for sw, sh, label in _RESOLUTIONS:
            _render(sw, sh, tab, ASSETS / f"{OUT_BASENAME}_{label}.png")
        for tid, _label, _box in _SIDEBAR_TABS:
            if tid == "overview":
                continue
            _render(SCREEN_W, SCREEN_H, tid, ASSETS / f"{OUT_BASENAME}_tab_{tid}.png")


if __name__ == "__main__":
    main()
