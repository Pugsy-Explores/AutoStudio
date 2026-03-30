"""Pipeline orchestration."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b


def multiply(a: int, b: int) -> int:
    # Stage 12 fixture: intentional bug (2*3 should be 6)
    return a * b + 1


def process(x: int) -> int:
    return x + 1


def transform(x: int) -> int:
    return x * 2


def run(seed: int) -> int:
    """Main pipeline: process then transform."""
    a = process(seed)
    return transform(a)


def benchmark_ok_marker() -> str:
    """Reserved for benchmark feature tasks."""
    return "pending"
