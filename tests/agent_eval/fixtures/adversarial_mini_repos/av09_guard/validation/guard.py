"""Validation guard. Fix validate_input so it returns True for non-empty strings."""

from __future__ import annotations


def validate_input(s: str) -> bool:
    # intentional bug: returns False for non-empty
    return len(s) == 0
