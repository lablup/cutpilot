"""Generate the CutPilot demo-video title card.

Colors + type match the review UI at ui/index.html and the pitch deck in
scripts/make_pitch_deck.py. Prefers Inter / JetBrains Mono TTFs if dropped
into scripts/assets/fonts/; otherwise falls back to the macOS system stack
(Helvetica Neue, Menlo) — same fallback chain the UI's CSS already uses.

Run:    python scripts/make_title_card.py
Writes: deliverables/title_card.png  (1920x1080, overwrites)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from scripts.make_pitch_deck import (
    CARD,
    HAIRLINE,
    INK_100,
    INK_400,
    INK_500,
    INK_900,
    PAGE,
    RED,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = REPO_ROOT / "scripts" / "assets" / "fonts"
OUT_DIR = REPO_ROOT / "deliverables"
OUT_PATH = OUT_DIR / "title_card.png"

W, H = 1920, 1080


def _rgb(c) -> tuple[int, int, int]:
    """python-pptx RGBColor → Pillow (r, g, b)."""
    return (c[0], c[1], c[2])


# Halo color for the brand dot: rgba(252, 63, 29, 0.12) composited on #ffffff
# → (252*0.12 + 255*0.88, 63*0.12 + 255*0.88, 29*0.12 + 255*0.88)
HALO = (254, 232, 228)

# Font candidates. Each entry: (path, ttc_index). First one that loads wins.
SANS_REGULAR = [
    (FONTS_DIR / "Inter-Regular.ttf", 0),
    (Path("/System/Library/Fonts/HelveticaNeue.ttc"), 0),
]
SANS_MEDIUM = [
    (FONTS_DIR / "Inter-Medium.ttf", 0),
    (Path("/System/Library/Fonts/HelveticaNeue.ttc"), 10),  # Medium
]
SANS_BOLD = [
    (FONTS_DIR / "Inter-Bold.ttf", 0),
    (Path("/System/Library/Fonts/HelveticaNeue.ttc"), 1),  # Bold
]
SANS_EXTRABOLD = [
    (FONTS_DIR / "Inter-ExtraBold.ttf", 0),
    (FONTS_DIR / "Inter-Bold.ttf", 0),
    (Path("/System/Library/Fonts/HelveticaNeue.ttc"), 1),  # Bold — best we have
]
MONO_REGULAR = [
    (FONTS_DIR / "JetBrainsMono-Regular.ttf", 0),
    (Path("/System/Library/Fonts/Menlo.ttc"), 0),
]
MONO_BOLD = [
    (FONTS_DIR / "JetBrainsMono-Bold.ttf", 0),
    (Path("/System/Library/Fonts/Menlo.ttc"), 1),
]


def _load_font(candidates: list[tuple[Path, int]], size: int) -> ImageFont.FreeTypeFont:
    for path, index in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size, index=index)
            except OSError:
                continue
    # Last-ditch fallback — keeps the script from crashing on alien systems.
    return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _brand_dot(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int = 14) -> None:
    halo_r = r + 10
    draw.ellipse((cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r), fill=HALO)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_rgb(RED))


def _chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
) -> int:
    pad_x, pad_y = 20, 12
    tw = _text_w(draw, text, font)
    ascent, descent = font.getmetrics()
    th = ascent + descent
    w = tw + pad_x * 2
    h = th + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=_rgb(INK_900))
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=_rgb(CARD))
    return w


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (W, H), _rgb(PAGE))
    draw = ImageDraw.Draw(img)

    # Fonts
    tag_font = _load_font(MONO_REGULAR, 24)
    hero_font = _load_font(SANS_EXTRABOLD, 300)
    sub_font = _load_font(SANS_BOLD, 56)
    event_font = _load_font(MONO_REGULAR, 30)
    team_font = _load_font(SANS_BOLD, 44)
    contrib_font = _load_font(SANS_REGULAR, 28)
    chip_font = _load_font(MONO_BOLD, 20)

    margin_x = 96

    # --- top bar: event tag + hairline ------------------------------------
    tag = "Demo · Seoul 2026"
    tag_w = _text_w(draw, tag, tag_font)
    draw.text((W - margin_x - tag_w, 70), tag, font=tag_font, fill=_rgb(INK_500))
    draw.rectangle((margin_x, 132, W - margin_x, 133), fill=_rgb(HAIRLINE))

    # --- hero: CutPilot wordmark with brand dot ---------------------------
    hero_y = 220
    dot_r = 42
    dot_cx = margin_x + dot_r
    dot_cy = hero_y + 160  # vertical-align roughly with capital-letter midline
    _brand_dot(draw, dot_cx, dot_cy, r=dot_r)
    hero_text_x = dot_cx + dot_r + 50
    draw.text((hero_text_x, hero_y), "CutPilot", font=hero_font, fill=_rgb(INK_900))

    # subhead — tagline under the wordmark
    sub_y = hero_y + 330
    draw.text(
        (margin_x, sub_y),
        "Long-form video, three vertical clips.",
        font=sub_font,
        fill=_rgb(INK_400),
    )

    # red accent bar
    accent_y = sub_y + 90
    draw.rectangle(
        (margin_x, accent_y, margin_x + 180, accent_y + 10),
        fill=_rgb(RED),
    )

    # --- event + team ------------------------------------------------------
    event_y = accent_y + 50
    draw.text(
        (margin_x, event_y),
        "NVIDIA Nemotron Developer Days · Seoul 2026",
        font=event_font,
        fill=_rgb(INK_500),
    )

    team_y = event_y + 60
    draw.text(
        (margin_x, team_y),
        "Team Lablup · Sergey Leksikov",
        font=team_font,
        fill=_rgb(INK_900),
    )
    draw.text(
        (margin_x, team_y + 60),
        "with Minjae Kim",
        font=contrib_font,
        fill=_rgb(INK_500),
    )

    # --- chip row (stack) --------------------------------------------------
    chip_y = H - 150
    chip_x = margin_x
    chips = [
        "Whisper NIM",
        "Nemotron Nano 2 VL",
        "Nemotron Text",
        "NeMo Agent Toolkit",
        "NVIDIA Brev H100",
    ]
    gap = 14
    for text in chips:
        chip_x += _chip(draw, chip_x, chip_y, text, chip_font) + gap

    # --- footer hairline + wordmark ---------------------------------------
    draw.rectangle((margin_x, H - 72, W - margin_x, H - 71), fill=_rgb(INK_100))
    footer_font = _load_font(MONO_REGULAR, 18)
    draw.text(
        (margin_x, H - 52),
        "cutpilot  ·  agentic long-video → 3 vertical highlights",
        font=footer_font,
        fill=_rgb(INK_400),
    )
    site = "sergey@lablup.com"
    site_w = _text_w(draw, site, footer_font)
    draw.text((W - margin_x - site_w, H - 52), site, font=footer_font, fill=_rgb(INK_400))

    img.save(OUT_PATH, format="PNG", optimize=True)
    return OUT_PATH


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")
