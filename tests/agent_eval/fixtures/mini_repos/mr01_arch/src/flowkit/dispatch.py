"""Event dispatch."""

from __future__ import annotations


def handle(event: str) -> str:
    return f"ok:{event}"
