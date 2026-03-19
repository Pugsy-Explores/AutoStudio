"""Tiny helper for benchmark repair task."""


def double(n: int) -> int:
    # intentional bug: should be n * 2
    return n + 2
