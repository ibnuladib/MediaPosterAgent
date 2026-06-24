"""
exports/exporter.py
-------------------
Save the final top articles to disk in four formats:

  • JSON      – full article dicts, pretty-printed
  • CSV       – flat spreadsheet of the same data
  • manifest  – small JSON describing the run (counts, top story, paths)
  • report    – standalone HTML report (poster cards, no JS framework)
"""

import json
import csv
from datetime import datetime
from pathlib import Path

from config.logging_setup import get_logger

log = get_logger(__name__)

EXPORTS_DIR = Path(__file__).resolve().parent.parent / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _run_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_json(articles: list[dict], run_id: str) -> Path:
    path = EXPORTS_DIR / f"articles_{run_id}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2, default=str)
    return path


def export_csv(articles: list[dict], run_id: str) -> Path:
    path = EXPORTS_DIR / f"articles_{run_id}.csv"
    flat_keys = [
        "rank_score", "viral_score", "relevance_score", "breaking_score",
        "engagement_score", "category", "source", "title",
        "headline_bangla", "subtext_bangla", "badge_type",
        "facebook_caption", "instagram_caption", "twitter_caption",
        "hashtags", "image_mood", "poster_portrait", "poster_square",
        "poster_landscape", "link",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=flat_keys, extrasaction="ignore")
        w.writeheader()
        for art in articles:
            row = dict(art)
            if isinstance(row.get("hashtags"), list):
                row["hashtags"] = " ".join(row["hashtags"])
            w.writerow(row)
    return path


def export_manifest(articles: list[dict], run_id: str) -> Path:
    """Small JSON describing the run — counts, top story, exported paths."""
    path = EXPORTS_DIR / f"manifest_{run_id}.json"
    payload = {
        "run_id":        run_id,
        "generated_at":  datetime.now().isoformat(timespec="seconds"),
        "count":         len(articles),
        "top_story": {
            "title":     articles[0]["title"] if articles else "",
            "viral":     articles[0].get("viral_score", 0) if articles else 0,
            "relevance": articles[0].get("relevance_score", 0) if articles else 0,
        } if articles else {},
        "categories": _category_breakdown(articles),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _category_breakdown(articles: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in articles:
        cat = a.get("category", "other") or "other"
        out[cat] = out.get(cat, 0) + 1
    return out


def export_html_report(articles: list[dict], run_id: str) -> Path:
    """Standalone HTML — one card per top article, no external assets."""
    path = EXPORTS_DIR / f"report_{run_id}.html"
    rows = "\n".join(_render_card(a) for a in articles)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Boishakhi TV — Run {run_id}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0c1220; color: #e8e8e8; margin: 0; padding: 32px; }}
    h1   {{ color: #f42a41; margin: 0 0 4px; }}
    p.sub{{ color: #8a93a6; margin: 0 0 32px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 24px; }}
    .card {{ background: #111a2e; border: 1px solid #1f2a44; border-radius: 12px; overflow: hidden; }}
    .card img {{ width: 100%; display: block; }}
    .meta {{ padding: 16px; }}
    .badge {{ display: inline-block; background: #f42a41; color: #fff; font-size: 11px; font-weight: 700; text-transform: uppercase; padding: 3px 8px; border-radius: 4px; margin-bottom: 8px; }}
    h2 {{ font-size: 18px; margin: 0 0 6px; line-height: 1.3; }}
    .source {{ font-size: 12px; color: #6c7591; }}
    .scores {{ font-size: 11px; color: #8a93a6; margin-top: 8px; }}
  </style>
</head>
<body>
  <h1>Boishakhi TV — Run {run_id}</h1>
  <p class="sub">{len(articles)} article(s) processed.</p>
  <div class="grid">
{rows}
  </div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path


def _render_card(a: dict) -> str:
    poster = a.get("poster_square") or a.get("poster_portrait") or a.get("poster_landscape") or ""
    badge  = (a.get("badge_type") or "news").upper()
    title  = a.get("headline_bangla") or a.get("title", "")
    src    = a.get("source", "")
    viral  = a.get("viral_score", 0)
    rel    = a.get("relevance_score", 0)
    img_tag = f'<img src="{poster}" alt="">' if poster else ""
    return f"""    <div class="card">
      {img_tag}
      <div class="meta">
        <span class="badge">{badge}</span>
        <h2>{title}</h2>
        <div class="source">{src}</div>
        <div class="scores">viral {viral} · relevance {rel}</div>
      </div>
    </div>"""


def export_all(articles: list[dict], run_id: str | None = None) -> dict[str, Path]:
    """
    Run all exporters and return a dict of {format: path}.
    """
    run_id = run_id or _run_slug()
    paths = {
        "json":     export_json(articles, run_id),
        "csv":      export_csv(articles, run_id),
        "manifest": export_manifest(articles, run_id),
        "report":   export_html_report(articles, run_id),
    }
    for fmt, p in paths.items():
        log.info("exported %-10s %s", fmt, p)
    return paths
