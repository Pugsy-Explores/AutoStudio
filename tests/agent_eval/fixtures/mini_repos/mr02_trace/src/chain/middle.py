from __future__ import annotations

from chain import tail


def forward(x: str) -> str:
    return tail.finish(x)
