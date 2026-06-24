# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Bengali sports-news → social-media-poster pipeline ("Boishakhi TV" branded). It scrapes football news, AI-scores articles, generates Bangla content + image prompts, then composites 3-format posters (1080×1080 square, 1080×1350 portrait, 1080×566 landscape). End-to-end orchestration lives in `main.py`.

The README.md has the full architecture diagram, env-var reference, and output-file inventory. Read it first.

## Commands

```bash
# Install
pip install -r requirements.txt

# Run once
python main.py

# Text-only run (skip image gen + poster composition)
python main.py --skip-images

# Recurring schedule (interval from SCHEDULE_INTERVAL_MINUTES in .env)
python main.py --schedule

# Regenerate the Jupyter notebook from cells/cell*.py sources
python generate_notebook.py

# Open notebook
jupyter notebook news_agent.ipynb

# Wrapper script that activates .venv and runs main.py (Linux only)
./run_news_agent.sh
```

There is no test suite, linter, or formatter configured — verify changes by running the pipeline and inspecting `exports/` + `posters/` + `logs/pipeline.log`.

## Two-stage AI design (the part that breaks if you don't understand it)

`ai/poster_pipeline.py` chains two Groq calls per article. The article dict itself is the shared context that flows between them:

1. **Stage 1** — `llama-3.3-70b-versatile`, prompt from `prompts/stage1_content.md`. Returns JSON: Bangla headline/subtext, badge type, emphasis fragment, social captions, hashtags, and an image generation prompt. Writes everything into the article dict and stashes the raw JSON under `_stage1_context`.
2. **Stage 2** — `llama-3.1-8b-instant`, prompt from `prompts/stage2_typography.md`. Receives Stage 1's `poster_content` + `image_brief.mood` as context, returns CSS-ready typography parameters (font sizes, weights, badge/accent colors). Writes into `article["typography_spec"]`.

**Both calls must return strict JSON.** `poster_pipeline._parse_json()` strips markdown fences, then falls back to scanning for the first complete `{...}` block. If parsing fails it raises — Stage 1 failures trigger `_apply_stage1_fallbacks()`, Stage 2 failures fall back to `_DEFAULT_TYPO`.

`emphasis_fragment` from Stage 1 must be a verbatim substring of `headline_bangla` — `poster_builder/html_builder.py:_highlight_emphasis()` highlights the first occurrence in red (or the Stage-2-chosen emphasis_color).

## Poster rendering pipeline

`poster_builder/__init__.py` exports `build_poster` / `build_all_formats` from `html_builder.py` (NOT `builder.py`). `builder.py` is a legacy PIL-only renderer kept for reference; the pipeline uses `html_builder.py`.

`html_builder.py` builds an HTML doc with 4 stacked layers, then renders it with `html2image` driving headless Chrome:

1. AI-generated background image (or branded mock gradient)
2. Bottom-heavy dark gradient overlay for text legibility
3. `assets/boishakhi_template{,_portrait,_landscape}.png` brand frame
4. Text content (badge, accent bar, headline, subtext, source line)

**Hardcoded Linux paths** — these will not work on Windows/macOS without changes:
- `_CHROME = "/usr/bin/google-chrome"`
- `_FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf"`
- `_FONT_REG  = "/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf"`
- `poster_builder/html_builder.py:33-38`

`generate_poster.py` (repo root) is a standalone one-off demo using the same `html2image` approach — not part of the pipeline.

## Image generation backends

`image_generation/generator.py` routes via `IMAGE_BACKEND` env var:
- `mock` — branded gradient PNG, no API key. **Default.**
- `pollinations` — free, no API key, requests ≤1024px and Pillow-resizes to target.
- `stability` — Stability AI SD3, needs `STABILITY_API_KEY`.
- `replicate` — Flux 1.1 Pro, needs `REPLICATE_API_KEY`.
- `gemini` — Imagen 4, needs `GEMINI_API_KEY`.

The pipeline generates one square background per article; `html_builder.build_all_formats()` composites it into all three poster formats.

## Article dict lifecycle

Articles flow as plain dicts through the entire pipeline. Key fields added at each stage:
- Scraping: `title`, `link`, `summary`, `source`, `image_url`, `published`
- `ranking/scorer.py`: `relevance_score`, `viral_score`, `breaking_score`, `engagement_score`, `rank_score`, `category`, `why`
- Stage 1 (`ai/poster_pipeline.py`): `headline_bangla`, `subtext_bangla`, `badge_type`, `emotional_tone`, `emphasis_fragment`, `image_prompt`, `negative_prompt`, social captions, hashtags, `_stage1_context`
- Stage 2: `typography_spec` (dict with `headline`, `subtext`, `badge`, `layout` keys)
- Image gen: `bg_image_path`
- Poster build: `poster_portrait`, `poster_square`, `poster_landscape`

Filter step in `main.py:114-117`: keep `viral_score >= MIN_VIRAL_SCORE`, take first `MAX_ARTICLES_PER_RUN`.

## Adding news sources

Edit `scraping/sources.py` (`RSS_SOURCES` and `HTML_SOURCES` dicts). FIFA Inside is scraped separately via `scraping/fifa_scraper.py:scrape_fifa_inside()` and prepended so dedup keeps the official article over RSS mirrors (`main.py:94-95`). Google Trends pulls dynamic sources via `scraping/trends_fetcher.py:get_trending_sports_sources()`.

## Adding an AI provider

`ai/client.py` is the single entry point. Add a `_ask_<provider>()` function and a branch in `ask_ai()`. Never import a provider SDK elsewhere — keep the SDK surface contained.

## Switching providers / models

`.env` only. No code changes needed.

```env
AI_PROVIDER=groq              # groq | claude | gemini
AI_MODEL=llama-3.3-70b-versatile
```

## Output directories (auto-created)

- `posters/` — generated PNG posters (3 per article)
- `exports/` — `articles_*.json`, `articles_*.csv`, `manifest_*.json`, `report_*.html`
- `news/` — `article_cache.json` (URL dedup, 7-day TTL) + `last_run.json`
- `logs/` — rotating `pipeline.log` (5 MB × 3 backups, DEBUG+)
- `assets/` — brand template PNGs + `make_template.py` to regenerate them
