"""Ratio utilities. Fix normalize_ratios so it returns correct ratio."""

from __future__ import annotations


def normalize_ratios(a: float, b: float) -> float:
    """Return a/b when b nonzero. Benchmark expects 12/4 == 3.0."""
    # intentional bug: returns a*b instead of a/b
    return a * b
