"""Benchmark arithmetic helper (external6 repair task)."""


def add_ints(a: int, b: int) -> int:
    # intentional bug: should return a + b
    return a * b
