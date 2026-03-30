"""Shared CLI environment setup: project root, optional ``.env`` merge, and logging.

Shell exports always win over ``.env`` (``override=False``). CLI logging flags are applied
after ``bootstrap_cli_env`` so they override ``.env`` for ``LOG_LEVEL`` / ``LOG_FORMAT``.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from config.logging_config import configure_logging


def bootstrap_cli_env(project_root: Path | str | None) -> None:
    """Set ``SERENA_PROJECT_DIR`` from ``project_root`` and load ``<root>/.env`` if present."""
    if project_root is not None:
        os.environ["SERENA_PROJECT_DIR"] = str(Path(project_root).resolve())
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    env_file = Path(root) / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def logging_cli_parent_parser() -> argparse.ArgumentParser:
    """Return a fragment parser (``add_help=False``) for ``parents=[...]`` on subparsers.

    Allows ``autostudio run --debug`` as well as ``autostudio --debug run`` (top-level flags).
    """
    p = argparse.ArgumentParser(add_help=False)
    register_logging_cli_arguments(p)
    return p


def register_logging_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--debug``, ``--log-level``, ``--log-format`` (maps to ``LOG_LEVEL`` / ``LOG_FORMAT``)."""
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Set LOG_LEVEL=DEBUG (verbose library logging)",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Root log level (sets LOG_LEVEL; wins over --debug when both are set)",
    )
    parser.add_argument(
        "--log-format",
        dest="log_format",
        default=None,
        metavar="FMT",
        help="Log format (sets LOG_FORMAT), e.g. '%%(levelname)s %%(name)s: %%(message)s'",
    )


def configure_cli_logging(args: argparse.Namespace) -> None:
    """Apply logging flags from argparse to os.environ and reconfigure the root logger."""
    log_level = getattr(args, "log_level", None)
    if log_level is not None:
        os.environ["LOG_LEVEL"] = str(log_level).upper()
    elif getattr(args, "debug", False):
        os.environ["LOG_LEVEL"] = "DEBUG"
    log_fmt = getattr(args, "log_format", None)
    if log_fmt is not None:
        os.environ["LOG_FORMAT"] = log_fmt
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    lvl = getattr(logging, level_name, logging.INFO)
    fmt = os.environ.get("LOG_FORMAT")
    configure_logging(level=lvl, fmt=fmt)
