"""
Stage 32 — Execution mode handling.

Responsibilities:
- ExecutionMode type and constants
- resolve_execution_mode (real -> offline deprecation)
- uses_real_workspace (offline/live_model/real)
- suite loading mode detection
"""

from __future__ import annotations

from typing import Literal

ExecutionMode = Literal["mocked", "offline", "live_model", "real"]

# Modes that use special suite loading (audit6 for core12, etc.)
SUITE_LOADING_MODES: tuple[str, ...] = ("real", "offline", "live_model")


def resolve_execution_mode(mode: str) -> str:
    """Map deprecated 'real' to 'offline'. Other modes unchanged."""
    return "offline" if mode == "real" else mode


def uses_real_workspace(mode: str) -> bool:
    """True when mode runs real execution_loop (offline, live_model, real)."""
    return mode in ("offline", "live_model", "real")


def is_suite_loading_mode(mode: str) -> bool:
    """True when mode uses special suite loading (real/offline/live_model)."""
    return mode in SUITE_LOADING_MODES
