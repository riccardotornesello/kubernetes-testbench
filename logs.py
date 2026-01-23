"""
Logging Utilities Module

Provides colored console logging functions for better user experience.
Uses ANSI color codes to distinguish different log levels visually.
"""
import logging
from enum import Enum


class LogColors(str, Enum):
    """ANSI color codes for terminal output formatting."""
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def log_info(message: str):
    """Log an informational message with blue color and info icon."""
    logging.info(f"ℹ️ {LogColors.OKBLUE}INFO{LogColors.ENDC}\t{message}")


def log_success(message: str):
    """Log a success message with green color and check mark icon."""
    logging.info(f"✅ {LogColors.OKGREEN}SUCCESS{LogColors.ENDC}\t{message}")


def log_warning(message: str):
    """Log a warning message with yellow color and warning icon."""
    logging.warning(f"⚠️ {LogColors.WARNING}WARNING{LogColors.ENDC}\t{message}")


def log_error(message: str):
    """Log an error message with red color and error icon."""
    logging.error(f"❌ {LogColors.FAIL}ERROR{LogColors.ENDC}\t{message}")
