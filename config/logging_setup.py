"""
config/logging_setup.py
-----------------------
Configures rotating file + console logging for the entire project.
Call get_logger(__name__) in every module.
"""

import logging
import logging.handlers
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, creating handlers once on first call."""
    logger = logging.getLogger(name)

    if logger.handlers:          # already configured
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler (INFO+) ───────────────────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # ── Rotating file handler (DEBUG+, 5 MB × 3 backups) ────────────────────
    log_file = LOGS_DIR / "pipeline.log"
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger