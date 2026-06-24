"""
scraping/html_scraper.py
------------------------
Fallback HTML scraper for sources that don't provide a reliable RSS feed.
Uses requests + BeautifulSoup to extract headline links from known page layouts.
"""

import os
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed

from config.logging_setup import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "15"))
MAX_RETRIES     = int(os.environ.get("MAX_RETRIES",             "3"))
RETRY_DELAY     = int(os.environ.get("RETRY_DELAY_SECONDS",     "5"))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FIFA2027Bot/1.0; "
        "+https://github.com/your-org/fifa2027)"
    )
}


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_fixed(RETRY_DELAY))
def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def scrape_html_articles(sources: dict, max_per_source: int = 10) -> list[dict]:
    """
    Scrape article links and titles from HTML pages.

    Strategy: look for <a> tags whose href contains 'news', 'article', or
    'story', with non-empty text longer than 20 chars.  This is a
    best-effort heuristic that works on most editorial sites.

    Parameters
    ----------
    sources         : dict {source_label: base_url}
    max_per_source  : cap on articles extracted per source

    Returns
    -------
    list of article dicts (same schema as rss_fetcher output)
    """
    all_articles: list[dict] = []

    for source_name, base_url in sources.items():
        log.info("Scraping HTML  %-20s  %s", source_name, base_url)
        try:
            resp = _get(base_url)
            soup = BeautifulSoup(resp.text, "lxml")

            seen_links: set[str] = set()
            count = 0

            for tag in soup.find_all("a", href=True):
                href: str = tag["href"]
                text: str = tag.get_text(strip=True)

                # Filter: must look like an article link and have enough text
                if len(text) < 20:
                    continue
                if not any(kw in href for kw in ("/news", "/article", "/story", "/post")):
                    continue
                if href in seen_links:
                    continue

                seen_links.add(href)
                # Resolve relative URLs
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(base_url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"

                all_articles.append({
                    "title":     text,
                    "summary":   "",
                    "full_text": "",
                    "link":      href,
                    "source":    source_name,
                    "published": "",
                })
                count += 1
                if count >= max_per_source:
                    break

            log.info("  → %d articles", count)

        except Exception as exc:
            log.error("%s: HTML scrape FAILED – %s", source_name, exc)

    return all_articles