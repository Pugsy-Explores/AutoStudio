"""Client sends requests."""

from __future__ import annotations


def send(payload: str) -> str:
    """Entry point. Calls handler.process."""
    from handler.core import process
    return process(payload)
