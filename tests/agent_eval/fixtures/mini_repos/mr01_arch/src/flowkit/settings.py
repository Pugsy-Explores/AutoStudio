"""Configuration loading."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    timeout: int


def load() -> Settings:
    return Settings(timeout=30)
