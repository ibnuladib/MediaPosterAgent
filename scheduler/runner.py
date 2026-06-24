"""
scheduler/runner.py
-------------------
Wraps the full pipeline in a scheduled loop.

Usage:
    python main.py --schedule        # run every N minutes (from .env)
    python main.py                   # run once and exit
"""

import os
import schedule
import time
from config.logging_setup import get_logger

log = get_logger(__name__)

SCHEDULE_INTERVAL_MINUTES = int(os.environ.get("SCHEDULE_INTERVAL_MINUTES", "60"))


def run_with_schedule(pipeline_fn, interval_minutes: int | None = None) -> None:
    """
    Run `pipeline_fn` immediately, then on a schedule.

    Parameters
    ----------
    pipeline_fn       : zero-argument callable that runs the full pipeline
    interval_minutes  : override the .env value if provided
    """
    mins = interval_minutes or SCHEDULE_INTERVAL_MINUTES
    log.info("Scheduler starting – interval: %d minutes", mins)

    # Run once immediately
    pipeline_fn()

    # Schedule recurring runs
    schedule.every(mins).minutes.do(pipeline_fn)
    log.info("Next run in %d minutes. Press Ctrl+C to stop.", mins)

    while True:
        schedule.run_pending()
        time.sleep(30)