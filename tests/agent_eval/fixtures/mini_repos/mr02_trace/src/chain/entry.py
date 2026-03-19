from __future__ import annotations

from chain import middle


def run(seed: str) -> str:
    return middle.forward(seed)
