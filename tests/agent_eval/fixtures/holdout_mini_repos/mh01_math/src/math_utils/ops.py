"""Math utilities. safe_div has intentional bug for holdout benchmark."""

from __future__ import annotations


def safe_div(a: float, b: float) -> float:
    """Return a/b; benchmark expects 10/2 == 5.0."""
    # intentional bug: returns a*b instead of a/b
    return a * b
