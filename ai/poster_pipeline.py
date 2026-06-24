"""
ai/poster_pipeline.py
----------------------
Two-stage pipeline that chains context between calls.

Stage 1:
  Input  : raw article dict
  Prompt : loaded from prompts/stage1_content.md
  Output : Bangla content, social media captions, image brief, emphasis fragment
  → Writes keys into article dict

Stage 2:
  Input  : full Stage 1 output (passed as context)
  Prompt : loaded from prompts/stage2_typography.md
  Output : CSS-ready typography spec
  → Writes "typography_spec" key into article dict

The article dict is the shared context object that flows through both stages.

# ponytail: model param dropped from ask_ai() — AI_MODEL in .env (gemini-2.5-flash)
# is the single source of truth for all stages. Pass model="..." to override per-call
# if you bring Groq back later.
"""

import os
import re
import json
import time
from pathlib import Path

from ai.client import ask_ai
from config.logging_setup import get_logger

log = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# ── Load prompt templates once at import time ──────────────────────────────────

def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("Prompt file not found: %s", path)
        raise


_STAGE1_PROMPT = _load_prompt("stage1_content.md")
_STAGE2_PROMPT = _load_prompt("stage2_typography.md")

_STAGE1_SYSTEM = (
    "You are a Bangla sports content editor for Boishakhi TV. "
    "You respond only with valid JSON matching the schema in your instructions."
)
_STAGE2_SYSTEM = (
    "You are a professional Bengali broadcast typographer. "
    "You respond only with valid JSON matching the schema in your instructions."
)

_SIZES = {
    "square":    (1080, 1080),
    "portrait":  (1080, 1350),
    "landscape": (1080,  566),
}

# Default typography fallbacks (used when Stage 2 fails)
_DEFAULT_TYPO = {
    "headline": {
        "font_size_px": 64, "font_weight": 900, "line_height": 1.25,
        "color": "#ffffff", "emphasis_color": "#f42a41",
    },
    "subtext": {
        "font_size_px": 26, "font_weight": 400, "line_height": 1.65,
        "color": "#d8d8d8",
    },
    "badge": {
        "background_color": "#f42a41", "text_color": "#ffffff",
    },
    "layout": {
        "padding_horizontal_px": 52, "headline_margin_bottom_px": 16,
        "subtext_margin_bottom_px": 22, "accent_color": "#006a4e",
    },
}


# Inter-call delays (seconds) — tune via STAGE1_DELAY / STAGE2_DELAY in .env
_STAGE1_DELAY = float(os.environ.get("STAGE1_DELAY", "4"))
_STAGE2_DELAY = float(os.environ.get("STAGE2_DELAY", "2"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()


def _parse_json(raw: str, stage: str) -> dict:
    """
    Robustly extract the first complete JSON object from an LLM response.
    Handles markdown fences, leading prose, and trailing commentary.
    String-aware: skips `{` / `}` that appear inside JSON string literals.
    """
    clean = _strip_fences(raw)

    # Try direct parse first (ideal case: pure JSON)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # String-aware brace scan: find first '{', track depth, but skip
    # everything between matching unescaped double quotes.
    start = clean.find("{")
    if start != -1:
        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, len(clean)):
            ch = clean[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                return json.loads(clean[start: end + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError(f"Stage {stage} returned non-JSON. Preview: {raw[:300]}")


# ── Stage 1: Content & Image Brief ────────────────────────────────────────────

def run_stage1(article: dict) -> dict:
    """
    Run Stage 1 content orchestration on a single article.
    Enriches the article dict in-place and returns it.
    """
    news_input = (
        f"Title   : {article.get('title', '')}\n"
        f"Source  : {article.get('source', '')}\n"
        f"Summary : {article.get('summary', '')[:400]}\n"
        f"Category: {article.get('category', '')}\n"
        f"Why viral: {article.get('why', '')}"
    )

    user_message = f"{_STAGE1_PROMPT}\n\n---\n\nProcess this article:\n\n{news_input}"

    try:
        raw  = ask_ai(user_message, system=_STAGE1_SYSTEM,
                      max_tokens=4096, model="llama-3.3-70b-versatile")
        data = _parse_json(raw, "1")

        pc = data.get("poster_content", {})
        sc = data.get("social_content", {})
        ib = data.get("image_brief",   {})
        ca = data.get("copyright_audit", {})

        # ── Poster-facing fields ───────────────────────────────────────────────
        article["headline_bangla"]    = pc.get("headline_bangla", article.get("title", ""))
        article["subtext_bangla"]     = pc.get("subtext_bangla",  article.get("summary", "")[:120])
        article["badge_type"]         = pc.get("badge_type",      "breaking")
        article["emotional_tone"]     = pc.get("emotional_tone",  "urgent")
        article["emphasis_fragment"]  = pc.get("emphasis_fragment", "")
        article["copyright_audit"]    = ca

        # ── Social media fields (backward-compatible keys) ─────────────────────
        article["headline"]          = sc.get("poster_text",      article.get("title", ""))
        article["poster_text"]       = sc.get("poster_text",      article.get("title", "")[:40])
        article["ai_summary"]        = article["subtext_bangla"]
        article["facebook_caption"]  = sc.get("facebook_caption",  "")
        article["instagram_caption"] = sc.get("instagram_caption", "")
        article["twitter_caption"]   = sc.get("twitter_caption",   "")
        article["hashtags"]          = sc.get("hashtags",          [])
        article["seo_keywords"]      = sc.get("seo_keywords",      [])
        article["meta_description"]  = sc.get("meta_description",  "")

        # ── Image brief fields ─────────────────────────────────────────────────
        article["image_prompt"]      = ib.get("generation_prompt", "")
        article["negative_prompt"]   = ib.get("negative_prompt",   "")
        article["image_mood"]        = ib.get("mood",              "dark_dramatic")
        article["dominant_colors"]   = ib.get("dominant_colors",   ["#071428"])

        # Store full Stage 1 output for Stage 2 context passing
        article["_stage1_context"] = data

        log.info("Stage 1 OK  badge=%s  tone=%s  emphasis='%s'",
                 article["badge_type"], article["emotional_tone"],
                 article["emphasis_fragment"][:20])

    except Exception as exc:
        log.warning("Stage 1 failed for '%s': %s — using fallbacks",
                    article.get("title", "")[:50], exc)
        _apply_stage1_fallbacks(article)

    return article


def _apply_stage1_fallbacks(article: dict) -> None:
    article.setdefault("headline_bangla",   article.get("title", ""))
    article.setdefault("subtext_bangla",    article.get("summary", "")[:120])
    article.setdefault("badge_type",        "breaking")
    article.setdefault("emotional_tone",    "urgent")
    article.setdefault("emphasis_fragment", "")
    article.setdefault("image_prompt", (
        f"{article.get('category','sport')} sports atmosphere, "
        "cinematic stadium lights, abstract energy, dark dramatic"
    ))
    article.setdefault("negative_prompt", "text, watermark, real people, blurry")
    article.setdefault("image_mood",      "dark_dramatic")
    article.setdefault("dominant_colors", ["#071428"])
    article.setdefault("_stage1_context", {})
    # Backward compat
    article.setdefault("poster_text",   article["headline_bangla"][:40])
    article.setdefault("ai_summary",    article["subtext_bangla"])
    article.setdefault("hashtags",      [])


# ── Stage 2: Typography Spec ──────────────────────────────────────────────────

def run_stage2(article: dict, size: str = "square") -> dict:
    """
    Run Stage 2 typography orchestration.
    Uses Stage 1 output as context. Enriches article with "typography_spec".
    """
    w, h = _SIZES.get(size, _SIZES["square"])

    stage1_ctx = article.get("_stage1_context", {})
    pc         = stage1_ctx.get("poster_content", {})
    ib         = stage1_ctx.get("image_brief",   {})

    user_message = (
        f"{_STAGE2_PROMPT}\n\n---\n\n"
        f"Canvas: {w}×{h} px\n\n"
        f"Stage 1 poster_content:\n{json.dumps(pc, ensure_ascii=False, indent=2)}\n\n"
        f"Stage 1 image_brief mood: {ib.get('mood','dark_dramatic')}\n"
        f"Headline word count: {len(article.get('headline_bangla','').split())}"
    )

    try:
        raw  = ask_ai(user_message, system=_STAGE2_SYSTEM,
                      max_tokens=512, model="llama-3.1-8b-instant")
        typo = _parse_json(raw, "2")
        article["typography_spec"] = typo
        log.info("Stage 2 OK  headline_px=%s  emphasis_color=%s",
                 typo.get("headline", {}).get("font_size_px", "?"),
                 typo.get("headline", {}).get("emphasis_color", "?"))

    except Exception as exc:
        log.warning("Stage 2 failed for '%s': %s — using default typography",
                    article.get("title", "")[:50], exc)
        article["typography_spec"] = _DEFAULT_TYPO.copy()

    return article


# ── Batch wrappers ────────────────────────────────────────────────────────────

def run_stage1_batch(articles: list[dict], delay: float | None = None) -> list[dict]:
    """Run Stage 1 on all articles sequentially."""
    delay = _STAGE1_DELAY if delay is None else delay
    for i, art in enumerate(articles, start=1):
        log.info("Stage 1  %d/%d: %s", i, len(articles), art["title"][:55])
        run_stage1(art)
        if i < len(articles):
            time.sleep(delay)
    return articles


def run_stage2_batch(articles: list[dict], size: str = "square",
                     delay: float | None = None) -> list[dict]:
    """Run Stage 2 on all articles (uses Stage 1 context already in article dicts)."""
    delay = _STAGE2_DELAY if delay is None else delay
    for i, art in enumerate(articles, start=1):
        log.info("Stage 2  %d/%d: %s", i, len(articles), art["title"][:55])
        run_stage2(art, size=size)
        if i < len(articles):
            time.sleep(delay)
    return articles
