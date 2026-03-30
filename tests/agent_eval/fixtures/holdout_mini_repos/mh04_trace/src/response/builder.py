"""Response builder."""

from __future__ import annotations


def build(payload: str) -> str:
    """Final step in flow."""
    return f"ok:{payload}"
