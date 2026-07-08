"""Rotating file logs — required under pythonw where stdout is gone."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from whisper_toggle.config import app_data_dir


def setup_logging(name: str = "whisper-toggle", level: int = logging.INFO) -> logging.Logger:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "whisper-toggle.log"

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Also mirror to stderr when a console exists
    if sys.stderr and hasattr(sys.stderr, "write"):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.info("logging to %s", log_path)
    return logger
