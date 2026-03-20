"""Byte parsing. Fix parse_bytes to return list of tokens."""

from __future__ import annotations


def parse_bytes(data: bytes) -> list[bytes]:
    """Split on whitespace, return list of non-empty tokens."""
    # intentional bug: returns single bytes object instead of list
    return data
