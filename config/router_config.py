"""Instruction router configuration."""

import os


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


ENABLE_INSTRUCTION_ROUTER = _bool_env("ENABLE_INSTRUCTION_ROUTER", "0")
ROUTER_TYPE = os.getenv("ROUTER_TYPE", "").strip().lower()
ROUTER_CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.7"))
