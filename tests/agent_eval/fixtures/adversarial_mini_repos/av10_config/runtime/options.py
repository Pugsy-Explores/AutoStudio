"""Runtime options. Add max_retries() -> int for adversarial feature task."""

from __future__ import annotations


def get_backoff_sec() -> float:
    return 1.0
