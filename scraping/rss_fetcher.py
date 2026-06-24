"""
scraping/rss_fetcher.py
-----------------------
Fetches and normalises articles from all RSS sources.
Inherits retry / timeout from .env.
"""

import os
import re
import time
import feedparser
from tenacity import retry, stop_after_attempt, wait_fixed

from config.logging_setup import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))
MAX_RETRIES     = int(os.environ.get("MAX_RETRIES",             "3"))
RETRY_DELAY     = int(os.environ.get("RETRY_DELAY_SECONDS",     "5"))


def _clean_html(text: str) -> str:
    """Strip HTML tags and normalise whitespace."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_fixed(RETRY_DELAY))
def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    """Parse one RSS feed, retrying up to MAX_RETRIES times on error."""
    return feedparser.parse(url, request_headers={"User-Agent": "FIFA2027Bot/1.0"})


def fetch_rss_articles(sources: dict) -> list[dict]:
    """
    Fetch every RSS feed in `sources` and return a flat list of article dicts.

    Each dict contains:
        title, summary, link, source, published, full_text (= summary here)
    """
    all_articles: list[dict] = []

    for source_name, feed_url in sources.items():
        log.info("Fetching  %-20s  %s", source_name, feed_url)
        try:
            feed = _fetch_feed(feed_url)

            if feed.bozo:
                log.warning("%s: feed had parse warnings (%s)", source_name, feed.bozo_exception)

            count = 0
            for entry in feed.entries:
                summary = _clean_html(getattr(entry, "summary", "") or "")
                content = ""
                # Some feeds include full <content:encoded> blocks
                if hasattr(entry, "content") and entry.content:
                    content = _clean_html(entry.content[0].get("value", ""))

                # Extract thumbnail from media_thumbnail, media_content, or enclosures
                image_url = ""
                if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
                    image_url = entry.media_thumbnail[0].get("url", "")
                elif hasattr(entry, "media_content") and entry.media_content:
                    image_url = entry.media_content[0].get("url", "")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    enc = entry.enclosures[0]
                    if enc.get("type", "").startswith("image"):
                        image_url = enc.get("href", "")

                # published_parsed is a time.struct_time; convert to epoch for sorting
                parsed = getattr(entry, "published_parsed", None)
                pub_ts = time.mktime(parsed) if parsed else 0.0

                all_articles.append({
                    "title":      (entry.get("title") or "No title").strip(),
                    "summary":    summary,
                    "full_text":  content or summary,
                    "link":       entry.get("link", ""),
                    "source":     source_name,
                    "published":  getattr(entry, "published", ""),
                    "published_ts": pub_ts,
                    "image_url":  image_url,
                })
                count += 1

            log.info("  → %d articles", count)

        except Exception as exc:
            log.error("%s: FAILED after retries – %s", source_name, exc)

        time.sleep(0.3)   # polite crawl delay

    log.info("Total fetched: %d articles from %d sources",
             len(all_articles), len(sources))
    return all_articles