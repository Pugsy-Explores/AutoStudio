"""Validator. Fix is_valid so it returns True for non-empty strings."""

from __future__ import annotations


def is_valid(s: str) -> bool:
    # intentional bug: returns False for non-empty
    return len(s) == 0
