"""
scraping/trends_fetcher.py
--------------------------
Uses pytrends (unofficial Google Trends API) to discover what sports topics
are spiking right now, then converts them into Google News RSS feed URLs that
the main RSS fetcher can consume.

TESTING MODE: football-only. Cricket / F1 / tennis Google News feeds and
non-football trend seeds are commented out.
"""

import time
import urllib.parse
from config.logging_setup import get_logger

log = get_logger(__name__)

# ── Static Google News RSS feeds (always included) ────────────────────────────
GOOGLE_NEWS_SOURCES: dict[str, str] = {
    "Google News FIFA 2026":   "https://news.google.com/rss/search?q=FIFA+World+Cup+2026&hl=en&gl=US&ceid=US:en",
    "Google News Football":    "https://news.google.com/rss/search?q=football+goal+match&hl=en&gl=US&ceid=US:en",
    "Google News Soccer Today":"https://news.google.com/rss/search?q=soccer+match+result+today&hl=en&gl=US&ceid=US:en",
    # Disabled — non-football
    # "Google News Cricket":     "https://news.google.com/rss/search?q=cricket+match+wicket&hl=en&gl=US&ceid=US:en",
    # "Google News F1":          "https://news.google.com/rss/search?q=formula+1+race+result&hl=en&gl=US&ceid=US:en",
    # "Google News Tennis":      "https://news.google.com/rss/search?q=tennis+match+result&hl=en&gl=US&ceid=US:en",
}

# Seeds to query Google Trends with — must stay football-only while testing
_TREND_SEEDS = [
    "FIFA World Cup 2026",
    "football",
    # "cricket",  # disabled
]

# Football-only allow list. If a trending query contains any of these, it's kept.
# Cricket / F1 / tennis / other sports terms removed for the testing phase.
_FOOTBALL_TERMS = {
    "football", "soccer", "fifa", "world cup", "premier league", "champions league",
    "la liga", "serie a", "bundesliga", "ligue 1", "mls",
    "goal", "match", "transfer", "manager", "stadium", "penalty", "referee",
    "var", "offside", "red card", "yellow card", "hat-trick", "hat trick",
    # player / team name fragments that are unmistakably football
    "fc ", " united", " city", " real ", "barcelona", "madrid", "liverpool",
    "arsenal", "chelsea", "tottenham", "juventus", "milan", "inter",
    "messi", "ronaldo", "mbappe", "mbappé", "neymar", "haaland", "kane",
    "saka", "bellingham", "vinicius", "guardiola", "klopp", "mourinho",
}


def _is_football_query(query: str) -> bool:
    q = query.lower()
    return any(term in q for term in _FOOTBALL_TERMS)


def _query_to_rss_url(query: str) -> str:
    encoded = urllib.parse.quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=en&gl=US&ceid=US:en"


def get_trending_sports_sources(max_dynamic: int = 8) -> dict[str, str]:
    """
    Return a dict of {label: rss_url} containing:
      • Static Google News football feeds (always present)
      • Dynamic feeds built from Google Trends rising queries (if pytrends works)

    Non-football queries are dropped so the pipeline stays football-only.

    Falls back gracefully to static-only on any error.
    """
    sources = dict(GOOGLE_NEWS_SOURCES)  # always include static feeds

    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        trending_queries: list[str] = []

        for seed in _TREND_SEEDS:
            try:
                pytrends.build_payload([seed], timeframe="now 1-d", geo="")
                related = pytrends.related_queries()
                df = related.get(seed, {}).get("rising")
                if df is not None and not df.empty:
                    for q in df["query"].head(5).tolist():
                        if _is_football_query(q) and q not in trending_queries:
                            trending_queries.append(q)
                time.sleep(1.5)   # avoid rate-limit
            except Exception as exc:
                log.warning("Trends seed '%s' failed: %s", seed, exc)
                continue

        # Also grab real-time trending searches (country = US) — football-filtered
        try:
            rt = pytrends.trending_searches(pn="united_states")
            for q in rt[0].tolist():
                if _is_football_query(q) and q not in trending_queries:
                    trending_queries.append(q)
        except Exception:
            pass

        for q in trending_queries[:max_dynamic]:
            label = f"Google Trends: {q[:45]}"
            sources[label] = _query_to_rss_url(q)
            log.info("  Trending → %s", q)

        log.info("Trends: %d dynamic feeds added", min(len(trending_queries), max_dynamic))

    except ImportError:
        log.warning("pytrends not installed — using static Google News feeds only")
    except Exception as exc:
        log.warning("Google Trends fetch failed (%s) — using static feeds only", exc)

    return sources
