import logging
from enum import Enum


class LogColors(str, Enum):
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
    logging.info(f"ℹ️ {LogColors.OKBLUE}INFO{LogColors.ENDC}\t{message}")


def log_success(message: str):
    logging.info(f"✅ {LogColors.OKGREEN}SUCCESS{LogColors.ENDC}\t{message}")


def log_warning(message: str):
    logging.warning(f"⚠️ {LogColors.WARNING}WARNING{LogColors.ENDC}\t{message}")


def log_error(message: str):
    logging.error(f"❌ {LogColors.FAIL}ERROR{LogColors.ENDC}\t{message}")
