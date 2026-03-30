"""Configuration defaults."""

# Stage 12 fixture: intentional bug — default should be 30, not -1
load_timeout: int = -1


def get_timeout_seconds() -> int:
    return load_timeout
