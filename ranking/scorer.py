"""
ranking/scorer.py
-----------------
Scores every article on four dimensions:

  • relevance_score  (0-100) – how relevant to football
  • viral_score      (0-100) – social engagement potential
  • breaking_score   (0-100) – freshness / breaking-news indicator
  • engagement_score (0-100) – combined social metric

Articles are also tagged with category and extracted entities.
Processing is batched (BATCH_SIZE articles per AI call) to stay within
token limits and minimise API costs.

TESTING MODE: brand philosophy is "viral / controversial / engaging posters".
- Off-topic (non-football) articles are dropped BEFORE scoring (pre-filter).
- Scoring weights rebalanced to favour viral/controversial hooks.
"""

import os
import re
import json
import time
from config.logging_setup import get_logger
from ai.client import ask_ai

log = get_logger(__name__)

BATCH_SIZE = int(os.environ.get("SCORING_BATCH_SIZE", "8"))  # articles per scoring call

# Gap between scoring batches. Gemini free tier is 20 req/min ≈ 1 every 3s,
# but we pad to absorb bursts from any retries. Override via SCORING_BATCH_DELAY.
_BATCH_DELAY = int(os.environ.get("SCORING_BATCH_DELAY", "12"))

# TESTING MODE: weight viral & breaking higher than relevance/engagement.
# Brand philosophy: "the most important viral controversial engaging posts".
_W = dict(viral=0.50, relevance=0.15, breaking=0.25, engagement=0.10)

_SYSTEM = (
    "You are a world-class sports social-media strategist. "
    "You respond only with valid JSON."
)

# Use a high-quota model for scoring (500k tokens/day free tier)
# Content generation still uses the bigger model from .env
_SCORING_MODEL = os.environ.get("SCORING_MODEL", "llama-3.1-8b-instant")

# Max articles fed into the scorer — prevents burning daily quota on huge feeds.
# Override via env: SCORING_MAX_ARTICLES=16  (use a small number while testing).
_MAX_SCORE = int(os.environ.get("SCORING_MAX_ARTICLES", "80"))

# TESTING MODE: cheap pre-filter for non-football content. Runs BEFORE the
# expensive AI scoring call so we don't burn tokens on cricket / F1 / tennis
# bleed-through from RSS mirrors. Anything matching these words is kept
# (football), anything not matching is dropped.
_FOOTBALL_KEYWORDS = {
    "football", "soccer", "fifa", "world cup", "premier league", "champions league",
    "la liga", "serie a", "bundesliga", "ligue 1", "mls", "europa", "fifa world cup 2026",
    "goal", "penalty", "referee", "var", "offside", "hat-trick", "hat trick",
    "striker", "midfielder", "goalkeeper", "defender", "winger",
    "fc ", " united", " city", " real ", "barcelona", "madrid", "liverpool",
    "arsenal", "chelsea", "tottenham", "juventus", "milan", "inter", "psg",
    "bayern", "dortmund", "atletico", "ajax", "porto", "benfica",
    "messi", "ronaldo", "mbappe", "mbappé", "neymar", "haaland", "kane",
    "saka", "bellingham", "vinicius", "guardiola", "klopp", "mourinho",
    "ancelotti", "ten hag", "arteta", "simeone", "salah", "modric",
}

# Words that scream "not football" — instant drop. Be conservative; default is to KEEP.
_NON_FOOTBALL_KEYWORDS = {
    "cricket", "wicket", "ipl", "bcci", "test match", "odi", "t20",
    "formula 1", "formula one", "f1", "grand prix", "qualifying", "pole position",
    "verstappen", "hamilton", "norris", "leclerc",
    "tennis", "wimbledon", "roland garros", "us open", "french open", "australian open",
    "atp", "wta", "djokovic", "alcaraz", "sinner",
    "nba", "nfl", "nhl", "mlb", "rugby", "golf", "boxing", "ufc", "mma", "women"
}


def _is_football_article(art: dict) -> bool:
    """Cheap keyword pre-filter: keep football, drop obvious non-football."""
    text = f"{art.get('title', '')} {art.get('summary', '')}".lower()
    if any(kw in text for kw in _NON_FOOTBALL_KEYWORDS):
        return False
    return any(kw in text for kw in _FOOTBALL_KEYWORDS)


def _build_scoring_prompt(batch: list[dict]) -> str:
    lines = []
    for i, art in enumerate(batch, start=1):
        line = f"{i}. [{art['source']}] {art['title']}"
        if art.get("summary"):
            line += f"\n   {art['summary'][:150]}"
        lines.append(line)

    return f"""You are scoring FOOTBALL news articles for a viral-first social-media pipeline.

Articles from "FIFA Inside" sources (source label starts with "FIFA Inside") are
official FIFA content covering rankings, analytics, women's football, transfers and
governance. Treat them as authoritative and score their relevance_score at least
10 points higher than non-official coverage of the same topic.

For EACH article return a JSON object with EXACTLY these fields:
  "index"          : article number (integer, 1-based)
  "relevance_score": 0-100  (how relevant to football / FIFA; +10 for FIFA Inside)
  "viral_score"    : 0-100  (social-media viral potential — blockbuster transfer, shocking result, controversy, ranking shake-up)
  "breaking_score" : 0-100  (how breaking / time-sensitive)
  "engagement_score": 0-100 (comment / share / reaction potential)
  "why"            : one sentence explaining the top viral hook
  "category"       : one of: transfer | injury | result | announcement | ranking |
                              squad | controversy | logistics | stadium | analytics | other
  "entities"       : object with keys teams[], players[], countries[], stage

Scoring guide (viral_score) — BE AGGRESSIVE, this is for posters:
  80-100 : blockbuster transfer, shocking result, major controversy, ranking shake-up, viral player moment
  50-79  : notable match, star player injury, squad announcement, ranking update
  20-49  : routine report, minor update, press-conference filler
  0-19   : off-topic or not football-related

Return ONLY a valid JSON array. No markdown. No extra text.

Articles:
{chr(10).join(lines)}"""


def _parse_batch(response_text: str, batch: list[dict]) -> list[dict]:
    """Attach scores from AI response to article dicts; fallback on parse error."""
    clean = re.sub(r"```(?:json)?|```", "", response_text).strip().strip("`")

    try:
        scored_list = json.loads(clean)
    except json.JSONDecodeError as exc:
        log.warning("Score JSON parse error: %s – assigning default scores", exc)
        for art in batch:
            art.setdefault("relevance_score",  0)
            art.setdefault("viral_score",       0)
            art.setdefault("breaking_score",    0)
            art.setdefault("engagement_score",  0)
            art.setdefault("why",               "")
            art.setdefault("category",          "other")
            art.setdefault("entities",          {})
        return batch

    lookup = {item["index"]: item for item in scored_list if "index" in item}

    for i, art in enumerate(batch, start=1):
        ai = lookup.get(i, {})
        art["relevance_score"]  = int(ai.get("relevance_score",  0))
        art["viral_score"]      = int(ai.get("viral_score",       0))
        art["breaking_score"]   = int(ai.get("breaking_score",    0))
        art["engagement_score"] = int(ai.get("engagement_score",  0))
        art["why"]              = ai.get("why",      "")
        art["category"]         = ai.get("category", "other").lower()
        art["entities"]         = ai.get("entities", {})
        base = (
            art["viral_score"]       * _W["viral"]
            + art["relevance_score"] * _W["relevance"]
            + art["breaking_score"]  * _W["breaking"]
            + art["engagement_score"] * _W["engagement"]
        )
        # Official FIFA Inside source: +10 priority boost, capped at 100
        if art.get("priority"):
            base += 10
        art["rank_score"] = int(min(base, 100))

    return batch


def score_articles(articles: list[dict]) -> list[dict]:
    """
    Score all articles in batches and return them sorted by rank_score desc.

    TESTING MODE: pre-filters obvious non-football content before scoring,
    so we don't waste tokens on cricket / F1 / tennis bleed-through.

    Parameters
    ----------
    articles : list of article dicts from the scraping layer

    Returns
    -------
    list – same articles enriched with score fields, sorted best-first
    """
    # ── Pre-filter: keep football, drop everything else ────────────────────
    pre = [a for a in articles if _is_football_article(a)]
    dropped = len(articles) - len(pre)
    if dropped:
        log.info("Football pre-filter: kept %d / dropped %d non-football",
                 len(pre), dropped)
    articles = pre

    if not articles:
        log.warning("No football articles after pre-filter.")
        return []

    # Priority (FIFA Inside) articles are always scored; non-priority capped newest-first
    priority   = [a for a in articles if a.get("priority")]
    non_priority = sorted(
        [a for a in articles if not a.get("priority")],
        key=lambda a: a.get("published_ts", 0), reverse=True,
    )
    slots = max(0, _MAX_SCORE - len(priority))
    articles = priority + non_priority[:slots]

    if len(priority) or slots < len(non_priority):
        log.info(
            "Scoring cap: %d priority + %d non-priority = %d total",
            len(priority), len(articles) - len(priority), len(articles),
        )

    all_scored: list[dict] = []
    n_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE

    for b in range(n_batches):
        start = b * BATCH_SIZE
        batch = articles[start : start + BATCH_SIZE]
        log.info("Scoring batch %d/%d (%d articles)", b + 1, n_batches, len(batch))

        prompt   = _build_scoring_prompt(batch)
        response = ask_ai(prompt, system=_SYSTEM, model=_SCORING_MODEL)
        scored   = _parse_batch(response, batch)
        all_scored.extend(scored)

        if b < n_batches - 1:
            time.sleep(_BATCH_DELAY)

    all_scored.sort(key=lambda a: a.get("rank_score", 0), reverse=True)

    if all_scored:
        top = all_scored[0]
        log.info(
            "Top story (rank=%d): %s | viral=%d relevance=%d",
            top["rank_score"], top["title"][:60],
            top["viral_score"], top["relevance_score"],
        )

    return all_scored