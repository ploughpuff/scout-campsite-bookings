"""
logger.py - Provides function to maintain an app logger to record warnings and errors.
"""

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger():
    """Set up the logger for the application."""
    logger = logging.getLogger("app_logger")
    logger.setLevel(logging.INFO)

    # Ensure the logs directory exists
    if not os.path.exists("logs"):
        os.makedirs("logs")

    log_file_path = os.path.join("logs", "app.log")

    if not logger.handlers:
        # Set up the RotatingFileHandler
        handler = RotatingFileHandler(log_file_path, maxBytes=1_000_000, backupCount=5)

        # Create a formatter and set it for the handler
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s in %(funcName)s: %(message)s"
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
