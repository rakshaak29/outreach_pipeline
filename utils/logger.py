"""
utils/logger.py
---------------
Centralized logging configuration for the Automated Outreach Pipeline.

Sets up:
  - A StreamHandler (console) with coloured level names via a custom formatter.
  - A RotatingFileHandler writing to data/pipeline.log.

Usage:
    from utils.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import os
from logging.handlers import RotatingFileHandler

# ── constants ────────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
LOG_FILE = os.path.join(LOG_DIR, "pipeline.log")
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per log file
BACKUP_COUNT = 3

# ANSI colour codes for console output
_COLOURS: dict[str, str] = {
    "DEBUG": "\033[36m",       # cyan
    "INFO": "\033[32m",        # green
    "WARNING": "\033[33m",     # yellow
    "ERROR": "\033[31m",       # red
    "CRITICAL": "\033[35m",    # magenta
    "RESET": "\033[0m",
}


class _ColouredFormatter(logging.Formatter):
    """Formatter that adds ANSI colour to level names in console output."""

    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelname, _COLOURS["RESET"])
        reset = _COLOURS["RESET"]
        record.levelname = f"{colour}{record.levelname:<8}{reset}"
        return super().format(record)


def _setup_root_logger() -> None:
    """Configure the root logger exactly once."""
    root = logging.getLogger()
    if root.handlers:
        return  # already configured; skip

    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    root.setLevel(log_level)

    # ── Console handler ──────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        _ColouredFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    )
    root.addHandler(console_handler)

    # ── File handler ─────────────────────────────────────────────────────────
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # always capture everything to file
    file_handler.setFormatter(
        logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)
    )
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Return a named logger.  Calling this function also guarantees that the
    root logger has been configured (idempotent).

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance.
    """
    _setup_root_logger()
    return logging.getLogger(name)
