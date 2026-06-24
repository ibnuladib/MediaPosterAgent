"""
storage/cache.py
----------------
Persistent JSON cache for processed articles.
Prevents re-scoring articles that have already been handled.

Uses a single JSON file: news/article_cache.json
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from config.logging_setup import get_logger

NEWS_DIR = Path(__file__).resolve().parent.parent / "news"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

log = get_logger(__name__)

_CACHE_FILE = NEWS_DIR / "article_cache.json"
_MAX_AGE_DAYS = 7   # entries older than this are pruned automatically


def _load() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(cache: dict) -> None:
    _CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_seen_urls() -> set[str]:
    """Return the set of all previously processed article URLs."""
    return set(_load().keys())


def save_articles(articles: list[dict]) -> None:
    """Persist processed articles to the cache (keyed by URL)."""
    cache = _load()
    now   = datetime.utcnow().isoformat()

    for art in articles:
        link = art.get("link", "")
        if not link:
            continue
        cache[link] = {
            "title":     art.get("title", ""),
            "source":    art.get("source", ""),
            "cached_at": now,
            "rank_score": art.get("rank_score", 0),
        }

    # Prune old entries
    cutoff = (datetime.utcnow() - timedelta(days=_MAX_AGE_DAYS)).isoformat()
    cache  = {k: v for k, v in cache.items()
              if v.get("cached_at", "") >= cutoff}

    _save(cache)
    log.debug("Cache updated: %d entries", len(cache))


def save_run_metadata(metadata: dict) -> None:
    """Save metadata about the last pipeline run."""
    meta_file = NEWS_DIR / "last_run.json"
    meta_file.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )