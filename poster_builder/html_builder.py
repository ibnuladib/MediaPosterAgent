"""
poster_builder/html_builder.py
-------------------------------
html2image-based poster renderer.

Layer stack (bottom → top):
  1. AI-generated background image
  2. Dark gradient overlay          (readability)
  3. Boishakhi TV template PNG      (brand: header, footer, frame, corner — from assets/)
  4. Bengali typography             (Noto Sans Bengali — loaded from system path)

Typography parameters come from Stage 2 (ai/poster_pipeline.py → article["typography_spec"]).
Emphasis fragment from Stage 1 is highlighted inline in the headline.

Public API:
  build_poster(article, bg_image_path, size, output_path) -> Path | None
  build_all_formats(article, bg_image_path) -> dict[str, Path | None]
"""

import base64
import time
import re
from pathlib import Path

import requests
from html2image import Html2Image

from config.logging_setup import get_logger

log = get_logger(__name__)

POSTERS_DIR = Path(__file__).resolve().parent.parent / "posters"
POSTERS_DIR.mkdir(parents=True, exist_ok=True)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"


# ── Cross-platform Chrome + Bengali font resolution ────────────────────────────
# ponytail: one resolver per dependency. Override via env if your install lives
# somewhere weird (portable Chrome, custom font dir). Detection order:
#   1. env var (CHROME_EXECUTABLE / BENGALI_FONT_BOLD / BENGALI_FONT_REGULAR)
#   2. well-known install path for the current OS
#   3. None — caller logs a warning and proceeds (HTML falls back to system font)

import os
import platform

def _find_chrome() -> str | None:
    """Locate a headless-capable Chrome/Chromium binary."""
    env = os.environ.get("CHROME_EXECUTABLE")
    if env and Path(env).exists():
        return env

    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        candidates += [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
        ]
    else:  # Linux / macOS
        candidates += [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    for c in candidates:
        if c and c.exists():
            return str(c)
    return None


def _find_bengali_font(weight: str) -> str | None:
    """
    Locate a Bangla-capable TTF. `weight` is 'bold' or 'regular'.
    On Windows we use Nirmala (ships with the OS). On Linux we look for
    Noto Sans Bengali. Returns the first path that exists, else None.
    """
    env_key = "BENGALI_FONT_BOLD" if weight == "bold" else "BENGALI_FONT_REGULAR"
    env = os.environ.get(env_key)
    if env and Path(env).exists():
        return env

    system = platform.system()
    if system == "Windows":
        # Nirmala = Microsoft's Bangla/Indic font, ships with Windows.
        # 'B' suffix is Bold; no suffix is Regular. Also try 'S' (Semilight).
        win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if weight == "bold":
            return str(win_fonts / "NirmalaB.ttf") if (win_fonts / "NirmalaB.ttf").exists() else None
        # regular: prefer Nirmala.ttf, else NirmalaS.ttf
        for name in ("Nirmala.ttf", "NirmalaS.ttf"):
            p = win_fonts / name
            if p.exists():
                return str(p)
        return None

    # Linux / macOS — Noto Sans Bengali
    noto_dir = Path("/usr/share/fonts/truetype/noto")
    candidates = [
        noto_dir / ("NotoSansBengali-Bold.ttf"     if weight == "bold" else "NotoSansBengali-Regular.ttf"),
        noto_dir / ("NotoSansBengali-Bold.ttf"     if weight == "bold" else "NotoSansBengali.ttf"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


_CHROME = _find_chrome()
_FONT_BOLD = _find_bengali_font("bold")
_FONT_REG  = _find_bengali_font("regular")

if not _CHROME:
    log.warning("Chrome/Chromium not found. Set CHROME_EXECUTABLE env var or install Chrome. Posters will fail.")
else:
    log.info("Using Chrome: %s", _CHROME)
if not (_FONT_BOLD and _FONT_REG):
    log.warning("Bengali font not found (bold=%s, reg=%s). Bangla text may render as boxes. "
                "Set BENGALI_FONT_BOLD / BENGALI_FONT_REGULAR env vars.", _FONT_BOLD, _FONT_REG)

# CSS font-family used in the rendered HTML. We declare this name and back it
# with an @font-face for whichever TTF we found. Even when @font-face fails,
# this exact name is also a system-installed Bengali font on Windows
# (Nirmala), so the body stack still renders Bangla correctly.
_FONT_FAMILY = "NotoSansBengali"

_SIZES = {
    "square":    (1080, 1080),
    "portrait":  (1080, 1350),
    "landscape": (1080,  566),
}

_BADGE_LABELS = {
    "goal":         "গোল আলার্ট",
    "injury":       "ইনজুরি সংবাদ",
    "transfer":     "ট্রান্সফার",
    "match":        "ম্যাচ আপডেট",
    "breaking":     "ব্রেকিং নিউজ",
    "result":       "ম্যাচ ফলাফল",
    "squad":        "স্কোয়াড নিউজ",
    "announcement": "ঘোষণা",
    "live":         "লাইভ আপডেট",
}

# Template PNG per size (falls back to square template if size variant missing)
_TEMPLATE_PATHS = {
    "square":    _ASSETS / "boishakhi_template.png",
    "portrait":  _ASSETS / "boishakhi_template_portrait.png",
    "landscape": _ASSETS / "boishakhi_template_landscape.png",
}


# ── Asset → base64 data URI ───────────────────────────────────────────────────

def _file_to_uri(path: Path, mime: str) -> str:
    try:
        data = Path(path).read_bytes()
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        return ""


def _to_image_uri(source: str | Path | None) -> str:
    if not source:
        return ""
    try:
        p = Path(source)
        if p.exists():
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            return _file_to_uri(p, mime)
        resp = requests.get(str(source), timeout=20)
        resp.raise_for_status()
        mime = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
        return f"data:{mime};base64,{base64.b64encode(resp.content).decode()}"
    except Exception as exc:
        log.warning("Could not load image (%s): %s", source, exc)
        return ""


# ── HTML builder ──────────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _highlight_emphasis(headline: str, fragment: str, color: str) -> str:
    """Wrap the emphasis fragment in a colored <span>. Case-sensitive, first occurrence."""
    if not fragment or fragment not in headline:
        return _escape(headline)
    safe_h   = _escape(headline)
    safe_f   = _escape(fragment)
    # Replace ONLY the first occurrence so multi-word fragments work correctly
    return safe_h.replace(safe_f,
        f'<span class="emph" style="color:{color}">{safe_f}</span>',
        1
    )


def _build_html(article: dict, bg_uri: str, template_uri: str,
                font_bold_uri: str, font_reg_uri: str,
                w: int, h: int) -> str:

    # ── Content extraction ────────────────────────────────────────────────────
    headline   = (article.get("headline_bangla") or article.get("poster_text")
                  or article.get("headline") or article.get("title", ""))
    subtext    = (article.get("subtext_bangla")  or article.get("ai_summary")
                  or article.get("summary") or "")
    if isinstance(subtext, list):
        subtext = " ".join(subtext)
    subtext = str(subtext)[:230]

    badge_type  = article.get("badge_type", "breaking")
    badge_label = _BADGE_LABELS.get(badge_type, "ব্রেকিং নিউজ")
    source      = article.get("source", "")
    tags        = " ".join(f"#{t}" for t in article.get("hashtags", [])[:4])
    emphasis    = article.get("emphasis_fragment", "")

    # ── Typography spec (from Stage 2, or calculated defaults) ────────────────
    typo   = article.get("typography_spec", {})
    hl     = typo.get("headline", {})
    st     = typo.get("subtext",  {})
    badge  = typo.get("badge",    {})
    layout = typo.get("layout",   {})

    pad           = layout.get("padding_horizontal_px",     max(44, w // 18))
    hl_px         = hl.get("font_size_px",                  max(46, w // 14))
    hl_weight     = hl.get("font_weight",                   900)
    hl_lh         = hl.get("line_height",                   1.25)
    hl_color      = hl.get("color",                         "#ffffff")
    emph_color    = hl.get("emphasis_color",                "#f42a41")
    hl_mb         = layout.get("headline_margin_bottom_px", max(14, h // 62))

    st_px         = st.get("font_size_px",  max(22, w // 32))
    st_weight     = st.get("font_weight",   400)
    st_lh         = st.get("line_height",   1.65)
    st_color      = st.get("color",         "#d8d8d8")
    st_mb         = layout.get("subtext_margin_bottom_px", max(20, h // 50))

    badge_bg      = badge.get("background_color", "#f42a41")
    badge_tc      = badge.get("text_color",        "#ffffff")
    accent_color  = layout.get("accent_color",     "#006a4e")

    # These are computed from canvas proportions, not AI-driven
    badge_px      = max(17, w // 44)
    badge_mb      = max(16, h // 55)
    accent_mb     = max(12, h // 60)

    # Bottom padding must clear the template footer bar (h // 17 ≈ 63px) + gap
    footer_clearance = max(70, h // 15)
    # Top content area starts after template header (h // 14 ≈ 77px)
    header_clearance = max(80, h // 13)

    # ── @font-face block ──────────────────────────────────────────────────────
    font_face = ""
    if font_bold_uri:
        font_face += (
            f"@font-face {{ font-family:'NotoSansBengali';"
            f" src:url('{font_bold_uri}') format('truetype');"
            f" font-weight:700 900; font-display:block; }}\n  "
        )
    if font_reg_uri:
        font_face += (
            f"@font-face {{ font-family:'NotoSansBengali';"
            f" src:url('{font_reg_uri}') format('truetype');"
            f" font-weight:400 600; font-display:block; }}\n  "
        )

    # ── Background CSS ────────────────────────────────────────────────────────
    bg_css = (
        f'url("{bg_uri}") center/cover no-repeat'
        if bg_uri
        else "linear-gradient(160deg,#071a10 0%,#0a1a0f 50%,#0e0810 100%)"
    )

    # ── Headline HTML with emphasis highlight ─────────────────────────────────
    headline_html = _highlight_emphasis(headline, emphasis, emph_color)

    return f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<style>
  {font_face}

  *, *::before, *::after {{ margin:0; padding:0; box-sizing:border-box; }}

  body {{
    width:{w}px; height:{h}px;
    overflow:hidden; background:#0a0e14;
    font-family:'{_FONT_FAMILY}','Noto Sans Bengali','Nirmala','Vrinda','Mangal',sans-serif;
    -webkit-font-smoothing:antialiased;
    text-rendering:optimizeLegibility;
  }}

  /* ── Layer 1: AI background ── */
  .l-bg {{
    position:absolute; inset:0;
    background:{bg_css};
    filter:brightness(45%);
    z-index:1;
  }}

  /* ── Layer 2: Gradient (bottom-heavy for text legibility) ── */
  .l-gradient {{
    position:absolute; inset:0;
    background:linear-gradient(
      to top,
      rgba(0,0,0,.94) 0%,
      rgba(0,0,0,.62) 36%,
      rgba(0,0,0,.14) 70%,
      rgba(0,0,0,0)   100%
    );
    z-index:2;
  }}

  /* ── Layer 3: Boishakhi TV template PNG ── */
  .l-template {{
    position:absolute; inset:0;
    background:{f'url("{template_uri}") center/cover no-repeat' if template_uri else 'none'};
    z-index:3;
    pointer-events:none;
  }}

  /* ── Layer 4: Text content ── */
  .l-content {{
    position:absolute;
    top:{header_clearance}px;
    left:0; right:0;
    bottom:{footer_clearance}px;
    padding:0 {pad}px {pad}px {pad}px;
    display:flex;
    flex-direction:column;
    justify-content:flex-end;
    z-index:4;
  }}

  /* Breaking news badge */
  .badge {{
    display:inline-flex; align-items:center; gap:9px;
    background:{badge_bg}; color:{badge_tc};
    font-size:{badge_px}px; font-weight:900;
    letter-spacing:1px;
    padding:7px 18px 7px 14px; border-radius:4px;
    margin-bottom:{badge_mb}px; width:fit-content;
  }}
  .badge-dot {{
    width:{max(7,badge_px//2)}px; height:{max(7,badge_px//2)}px;
    background:{badge_tc}; border-radius:50%; flex-shrink:0;
  }}

  /* Green accent rule */
  .accent {{
    width:{max(60,w//10)}px; height:4px;
    background:{accent_color}; border-radius:2px;
    margin-bottom:{accent_mb}px;
  }}

  /* Bengali headline — emphasis fragment highlighted by Stage 2 color */
  .headline {{
    color:{hl_color};
    font-size:{hl_px}px;
    font-weight:{hl_weight};
    line-height:{hl_lh};
    margin-bottom:{hl_mb}px;
    max-width:{int(w*.92)}px;
    text-shadow:2px 2px 16px rgba(0,0,0,1),0 0 50px rgba(0,0,0,.8);
    word-break:break-word;
  }}
  .emph {{ font-style:normal; }}

  /* Bengali subtext */
  .subtext {{
    color:{st_color};
    font-size:{st_px}px;
    font-weight:{st_weight};
    line-height:{st_lh};
    max-width:{int(w*.90)}px;
    margin-bottom:{st_mb}px;
    text-shadow:1px 1px 10px rgba(0,0,0,1);
    word-break:break-word;
  }}

  /* Source line (inside content area, above footer template) */
  .source-line {{
    color:rgba(255,255,255,.50);
    font-size:{max(12,w//58)}px;
    font-weight:400;
    letter-spacing:.5px;
  }}
</style>
</head>
<body>
  <div class="l-bg"></div>
  <div class="l-gradient"></div>
  <div class="l-template"></div>
  <div class="l-content">
    <div class="badge"><span class="badge-dot"></span>{badge_label}</div>
    <div class="accent"></div>
    <div class="headline">{headline_html}</div>
    {"<div class='subtext'>" + _escape(subtext) + "</div>" if subtext else ""}
    {"<div class='source-line'>" + _escape(source) + ("  " + _escape(tags) if tags else "") + "</div>" if source or tags else ""}
  </div>
</body>
</html>"""


# ── Public renderers ───────────────────────────────────────────────────────────

def build_poster(
    article: dict,
    bg_image_path: Path | str | None,
    size: str = "square",
    output_path: Path | None = None,
) -> Path | None:
    w, h = _SIZES.get(size, _SIZES["square"])

    if output_path is None:
        slug = "".join(c if c.isalnum() else "_"
                       for c in article.get("title", "untitled")[:35])
        output_path = POSTERS_DIR / f"{slug}_{size}_{int(time.time())}.png"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load assets as base64 URIs (no network calls at render time)
    bg_uri       = _to_image_uri(bg_image_path)
    template_uri = _file_to_uri(_TEMPLATE_PATHS.get(size, _TEMPLATE_PATHS["square"]), "image/png")
    font_bold    = _file_to_uri(_FONT_BOLD, "font/truetype")
    font_reg     = _file_to_uri(_FONT_REG,  "font/truetype")

    html = _build_html(article, bg_uri, template_uri, font_bold, font_reg, w, h)

    try:
        hti = Html2Image(
            output_path=str(output_path.parent),
            browser_executable=_CHROME,
            custom_flags=[
                "--no-sandbox", "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
                "--font-render-hinting=none",
            ],
        )
        hti.browser.use_new_headless = True
        hti.screenshot(html_str=html, save_as=output_path.name, size=(w, h))

        if output_path.exists():
            log.info("Poster saved → %s  (%d KB)", output_path,
                     output_path.stat().st_size // 1024)
            return output_path

        log.error("html2image produced no file for '%s'",
                  article.get("title", "")[:50])
        return None

    except Exception as exc:
        log.error("Poster render failed for '%s': %s",
                  article.get("title", "")[:50], exc)
        return None


def build_all_formats(
    article: dict,
    bg_image_path: Path | str | None,
) -> dict[str, Path | None]:
    """Build square, portrait, and landscape variants."""
    # Pre-load shared assets once
    bg_uri    = _to_image_uri(bg_image_path)
    font_bold = _file_to_uri(_FONT_BOLD, "font/truetype")
    font_reg  = _file_to_uri(_FONT_REG,  "font/truetype")

    results: dict[str, Path | None] = {}

    for size, (w, h) in _SIZES.items():
        slug = "".join(c if c.isalnum() else "_"
                       for c in article.get("title", "untitled")[:35])
        out          = POSTERS_DIR / f"{slug}_{size}_{int(time.time())}.png"
        template_uri = _file_to_uri(
            _TEMPLATE_PATHS.get(size, _TEMPLATE_PATHS["square"]), "image/png"
        )
        html = _build_html(article, bg_uri, template_uri, font_bold, font_reg, w, h)

        try:
            hti = Html2Image(
                output_path=str(out.parent),
                browser_executable=_CHROME,
                custom_flags=[
                    "--no-sandbox", "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-software-rasterizer",
                    "--font-render-hinting=none",
                ],
            )
            hti.browser.use_new_headless = True
            hti.screenshot(html_str=html, save_as=out.name, size=(w, h))
            results[size] = out if out.exists() else None

        except Exception as exc:
            log.error("Poster render failed (%s / '%s'): %s",
                      size, article.get("title", "")[:40], exc)
            results[size] = None

    return results
