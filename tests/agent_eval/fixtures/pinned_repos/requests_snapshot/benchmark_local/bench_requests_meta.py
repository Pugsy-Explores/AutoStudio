"""Offline benchmark constant (must match HTTPBIN_NOTE wording)."""

DEFAULT_HTTPBIN_BASE = "https://example.invalid"


def get_timeout() -> int:
    """Return default request timeout in seconds (benchmark: implement returning 30)."""
    return 0  # intentional: should return 30
