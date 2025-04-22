"""
logger.py - Provides function to maintain an app logger to record warnings and errors.
"""

import logging
from logging.handlers import RotatingFileHandler

from config import LOG_FILE_PATH, LOG_LEVEL_STR


def setup_logger():
    """Set up the logger for the application."""
    logger = logging.getLogger("app_logger")

    # Convert string level to logging level constant
    log_level = getattr(logging, LOG_LEVEL_STR, logging.INFO)

    logger.setLevel(log_level)

    if not logger.handlers:
        # Set up the RotatingFileHandler
        handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=1_000_000, backupCount=5)

        # Create a formatter and set it for the handler
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(funcName)s: %(message)s")

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
