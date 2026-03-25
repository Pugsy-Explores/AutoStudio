"""
Phase 10 — centralized numeric limits for agent_v2 (single source of truth).

Env overrides use AGENT_V2_* prefix to avoid colliding with legacy agent config.
"""
from __future__ import annotations

import os

from agent_v2.schemas.policies import ExecutionPolicy


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Plan / exploration (architecture freeze §3.1)
MAX_PLAN_STEPS: int = _int_env("AGENT_V2_MAX_PLAN_STEPS", 8)
EXPLORATION_STEPS: int = _int_env("AGENT_V2_EXPLORATION_STEPS", 5)

# Retry / replan
MAX_RETRIES: int = _int_env("AGENT_V2_MAX_RETRIES", 2)
MAX_REPLANS: int = _int_env("AGENT_V2_MAX_REPLANS", 2)

# Executor guards (Phase 10 Step 8)
MAX_EXECUTOR_DISPATCHES: int = _int_env("AGENT_V2_MAX_EXECUTOR_DISPATCHES", 20)
MAX_RUNTIME_SECONDS: int = _int_env("AGENT_V2_MAX_RUNTIME_SECONDS", 600)


def get_execution_policy() -> ExecutionPolicy:
    """Build ExecutionPolicy from module defaults + environment."""
    return ExecutionPolicy(
        max_steps=MAX_PLAN_STEPS,
        max_retries_per_step=MAX_RETRIES,
        max_replans=MAX_REPLANS,
        max_executor_dispatches=MAX_EXECUTOR_DISPATCHES,
        max_runtime_seconds=MAX_RUNTIME_SECONDS,
    )
