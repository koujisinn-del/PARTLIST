from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .runtime_paths import app_data_root


LOGGER_NAME = "material_drawing_generator"


def log_directory() -> Path:
    path = app_data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_directory() / "运行日志.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return setup_logging()
