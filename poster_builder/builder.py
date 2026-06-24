"""
poster_builder/builder.py
--------------------------
Composites the final poster:
  1. Background image (from image_generation layer)
  2. Semi-transparent gradient overlay
  3. BREAKING NEWS badge
  4. Headline text (auto-wrapped)
  5. Summary snippet
  6. Source + date attribution
  7. FIFA 2027 branding bar

Produces all three format variants: portrait, square, landscape.
"""

import io
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config.logging_setup import get_logger

log = get_logger(__name__)

POSTERS_DIR = Path(__file__).resolve().parent.parent / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)

# ── Color palette ─────────────────────────────────────────────────────────────
C_BG_DARK    = (8,  20,  50,  230)   # deep navy, semi-transparent
C_GOLD       = (212, 175, 55,  255)   # FIFA gold
C_WHITE      = (255, 255, 255, 255)
C_RED_BADGE  = (220,  30,  30,  240)
C_GREY       = (180, 180, 180, 255)
C_OVERLAY    = (5,   15,  40,  180)   # dark overlay for readability

# ── Font paths (falls back to built-in PIL default) ───────────────────────────
_FONT_PATHS = {
    "bold":    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "oblique": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
}


def _font(style: str = "bold", size: int = 36) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_FONT_PATHS[style], size)
    except Exception:
        return ImageFont.load_default()


def _draw_gradient_overlay(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """Draw a bottom-heavy dark gradient so text is always readable."""
    for y in range(h):
        alpha = int(180 * (y / h) ** 1.5)
        draw.line([(0, y), (w, y)], fill=(5, 15, 40, alpha))


def _draw_text_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    max_width: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    line_spacing: int = 8,
) -> int:
    """Draw word-wrapped text; returns the y position after the last line."""
    words   = text.split()
    lines:  list[str] = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    cur_y = y
    for line in lines:
        draw.text((x, cur_y), line, font=font, fill=fill)
        bbox   = draw.textbbox((0, 0), line, font=font)
        cur_y += (bbox[3] - bbox[1]) + line_spacing

    return cur_y


def build_poster(
    article: dict,
    bg_image_path: Path | None,
    size: str = "square",
    output_path: Path | None = None,
) -> Path | None:
    """
    Composite one poster.

    Parameters
    ----------
    article        : enriched article dict (must have headline, poster_text, etc.)
    bg_image_path  : path to background PNG; uses solid colour fallback if None
    size           : "portrait" | "square" | "landscape"
    output_path    : where to save; auto-named under POSTERS_DIR if None

    Returns
    -------
    Path to the saved poster PNG, or None on failure.
    """
    from image_generation.generator import SIZES

    w, h = SIZES.get(size, SIZES["square"])

    # ── 1. Load / create background ───────────────────────────────────────────
    if bg_image_path and Path(bg_image_path).exists():
        bg = Image.open(bg_image_path).convert("RGBA").resize((w, h))
    else:
        bg = Image.new("RGBA", (w, h), color=(8, 20, 50, 255))

    # ── 2. Composite layer ────────────────────────────────────────────────────
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    _draw_gradient_overlay(draw, w, h)
    bg      = Image.alpha_composite(bg, overlay)
    draw    = ImageDraw.Draw(bg)

    margin   = int(w * 0.05)
    text_w   = w - margin * 2

    # ── 3. BREAKING NEWS badge ────────────────────────────────────────────────
    badge_font = _font("bold", max(18, w // 50))
    badge_text = "⚡ BREAKING NEWS"
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    bw = badge_bbox[2] - badge_bbox[0] + 24
    bh = badge_bbox[3] - badge_bbox[1] + 12
    badge_y = int(h * 0.60)
    draw.rectangle([margin, badge_y, margin + bw, badge_y + bh], fill=C_RED_BADGE)
    draw.text((margin + 12, badge_y + 6), badge_text, font=badge_font, fill=C_WHITE)

    # ── 4. Main headline ──────────────────────────────────────────────────────
    headline_font = _font("bold", max(28, w // 18))
    headline_y    = badge_y + bh + int(h * 0.02)
    headline_text = article.get("poster_text") or article.get("headline") or article["title"]
    headline_y    = _draw_text_wrapped(
        draw, headline_text.upper(),
        margin, headline_y, text_w,
        headline_font, C_GOLD, line_spacing=10
    )

    # ── 5. Short summary ──────────────────────────────────────────────────────
    summary_font = _font("regular", max(18, w // 30))
    summary_text = (article.get("ai_summary") or article.get("summary") or "")[:180]
    if summary_text:
        _draw_text_wrapped(
            draw, summary_text,
            margin, headline_y + int(h * 0.015), text_w,
            summary_font, C_WHITE, line_spacing=6
        )

    # ── 6. Bottom bar: source + hashtags ──────────────────────────────────────
    bar_h      = int(h * 0.08)
    bar_y      = h - bar_h
    draw.rectangle([0, bar_y, w, h], fill=(8, 20, 50, 220))

    attr_font  = _font("bold", max(14, w // 50))
    source     = article.get("source", "FIFA 2027")
    tags       = " ".join(f"#{t}" for t in article.get("hashtags", [])[:4])
    draw.text((margin, bar_y + bar_h // 4), source, font=attr_font, fill=C_GOLD)
    draw.text((margin, bar_y + bar_h * 5 // 8), tags, font=attr_font, fill=C_GREY)

    # ── 7. FIFA 2027 logo text (top-right) ────────────────────────────────────
    logo_font = _font("bold", max(16, w // 45))
    logo_text = "FIFA WORLD CUP 2027™"
    logo_bbox = draw.textbbox((0, 0), logo_text, font=logo_font)
    logo_x    = w - (logo_bbox[2] - logo_bbox[0]) - margin
    draw.text((logo_x, margin), logo_text, font=logo_font, fill=C_GOLD)

    # ── 8. Save ───────────────────────────────────────────────────────────────
    if output_path is None:
        import time as _t
        slug = "".join(c if c.isalnum() else "_" for c in article["title"][:30])
        output_path = POSTERS_DIR / f"{slug}_{size}_{int(_t.time())}.png"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(output_path, format="PNG", optimize=True)
    log.info("Poster saved → %s", output_path)
    return output_path


def build_all_formats(
    article: dict,
    bg_image_path: Path | None,
) -> dict[str, Path | None]:
    """Build portrait, square, and landscape variants of the same poster."""
    results: dict[str, Path | None] = {}
    for size in ("portrait", "square", "landscape"):
        results[size] = build_poster(article, bg_image_path, size=size)
    return results