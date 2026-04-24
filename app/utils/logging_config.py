"""
Logging configuration module.

This module configures logging for the application, ensuring proper log
formatting and handling.
"""

import logging
import os
import sys
from collections import deque
from typing import Optional


recent_log_buffer: deque = deque(maxlen=50)


class _RecentLogHandler(logging.Handler):
    """Keeps the last N WARNING+ log records in memory for bug reports."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            recent_log_buffer.append(self.format(record))
        except Exception:
            pass


def configure_logging(app_name: str = "app",
                     log_level: Optional[str] = None,
                     log_file: Optional[str] = None) -> None:
    """
    Configure application logging.
    
    Args:
        app_name: Name of the application
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file
    """
    if log_level is None:
        # Default to INFO in production, DEBUG in development
        log_level = "DEBUG" if os.environ.get("FLASK_ENV") == "development" else "INFO"
    
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(numeric_level)
    
    # Remove existing handlers to avoid duplicate logs
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Create file handler if log_file is provided
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    # Specific loggers
    sqlalchemy_logger = logging.getLogger('sqlalchemy.engine')
    sqlalchemy_logger.setLevel(logging.WARNING)  # Set to logging.DEBUG to log all SQL queries
    
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.INFO)
    
    # Flask app logger
    app_logger = logging.getLogger(app_name)
    app_logger.setLevel(numeric_level)

    # In-memory buffer for bug reports — WARNING and above only
    buf_handler = _RecentLogHandler()
    buf_handler.setLevel(logging.WARNING)
    buf_handler.setFormatter(formatter)
    logger.addHandler(buf_handler)

    # Log startup message
    logging.info(f"Logging configured with level: {log_level}")
