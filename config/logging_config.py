"""Logging configuration."""

import logging
import os
import sys


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "%(message)s")

# ANSI codes for terminal color (only used when stderr is a TTY)
_RED = "\033[31m"
_RESET = "\033[0m"


class _ColoredFormatter(logging.Formatter):
    """Formatter that highlights ERROR and CRITICAL messages in red."""

    def __init__(self, fmt: str, use_color: bool = True, **kwargs) -> None:
        super().__init__(fmt, **kwargs)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if self._use_color and record.levelno >= logging.ERROR:
            return f"{_RED}{msg}{_RESET}"
        return msg


def configure_logging(
    level: str | int | None = None,
    fmt: str | None = None,
    datefmt: str | None = None,
    use_color: bool | None = None,
) -> None:
    """Configure root logger with optional error highlighting in red.

    Args:
        level: Log level (default: LOG_LEVEL from env).
        fmt: Log format string (default: LOG_FORMAT from env).
        datefmt: Date format for %(asctime)s (optional).
        use_color: Whether to color ERROR/CRITICAL in red. Default: True when stderr is a TTY.
    """
    lvl = level if level is not None else getattr(logging, LOG_LEVEL, logging.INFO)
    format_str = fmt if fmt is not None else LOG_FORMAT
    if use_color is None:
        use_color = sys.stderr.isatty()

    root = logging.getLogger()
    root.setLevel(lvl)
    # Remove existing handlers to avoid duplicates
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stderr)
    formatter = _ColoredFormatter(format_str, use_color=use_color, datefmt=datefmt)
    handler.setFormatter(formatter)
    root.addHandler(handler)
