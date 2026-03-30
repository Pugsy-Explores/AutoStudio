"""Stage 12 — software-agent benchmark harness (fixtures + runner + evaluation)."""

from __future__ import annotations

from pathlib import Path

__all__ = ["get_fixtures_root"]


def get_fixtures_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
