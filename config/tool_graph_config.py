"""Tool graph configuration."""

import os


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


ENABLE_TOOL_GRAPH = _bool_env("ENABLE_TOOL_GRAPH", "1")
