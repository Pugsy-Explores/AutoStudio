"""Handler processes requests."""

from __future__ import annotations


def process(payload: str) -> str:
    """Calls response.build."""
    from response.builder import build
    return build(payload)
