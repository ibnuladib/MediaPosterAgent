"""
scripts/clean_artifacts.py
---------------------------
Delete generated runtime artifacts. Run before a fresh pipeline test
or whenever the project folder gets heavy.

Keeps:
  - news/article_cache.json   (URL dedup memory — losing it forces re-processing)
  - .gitkeep placeholders
  - assets/, prompts/, code

Removes:
  - posters/*.png             (regenerated every run, biggest contributor)
  - exports/*.json / *.csv / *.html
  - logs/*.log, logs/*.log.*
  - news/last_run.json        (just a summary of the previous run)

Usage:
  python scripts/clean_artifacts.py
"""

from pathlib import Path
import os

ROOT     = Path(__file__).resolve().parent.parent
TARGETS  = [
    ROOT / "posters"  / "*.png",
    ROOT / "exports"  / "*.json",
    ROOT / "exports"  / "*.csv",
    ROOT / "exports"  / "*.html",
    ROOT / "logs"     / "*.log",
    ROOT / "logs"     / "*.log.*",
    ROOT / "news"     / "last_run.json",
]
# Files we keep on purpose (list separately so we never nuke them by accident)
KEEP     = [
    ROOT / "news" / "article_cache.json",
]

def main() -> None:
    removed_bytes = 0
    removed_count = 0
    skipped = []

    for pattern in TARGETS:
        for path in ROOT.glob(str(pattern.relative_to(ROOT))):
            if any(path == k or path.is_relative_to(k) for k in KEEP):
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                removed_bytes += size
                removed_count += 1
            except OSError as exc:
                # File locked by another process (e.g. logger still has it open)
                skipped.append((path, str(exc)))

    def _fmt(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    print(f"Removed {removed_count} files  ({_fmt(removed_bytes)})")
    for path, reason in skipped:
        print(f"  SKIPPED (in use): {path.relative_to(ROOT)}  — {reason}")
    for k in KEEP:
        if k.exists():
            print(f"  kept:            {k.relative_to(ROOT)}")


if __name__ == "__main__":
    main()