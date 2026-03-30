"""Tiny helper for benchmark repair task."""


def double(n: int) -> int:
    # intentional bug: should be n * 2
    return n + 2


def halve(n: int) -> int:
    # intentional bug: should be n // 2
    return n  # wrong: returns n instead of n//2
