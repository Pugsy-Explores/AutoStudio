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
EXPLORATION_MAX_STEPS: int = _int_env("AGENT_V2_EXPLORATION_MAX_STEPS", 5)
EXPLORATION_MAX_BACKTRACKS: int = _int_env("AGENT_V2_EXPLORATION_MAX_BACKTRACKS", 2)
EXPLORATION_MAX_ITEMS: int = _int_env("AGENT_V2_EXPLORATION_MAX_ITEMS", 6)
# Consecutive steps with no new evidence key (file, symbol, read_source) or duplicate-queue skips → stalled
EXPLORATION_STAGNATION_STEPS: int = _int_env("AGENT_V2_EXPLORATION_STAGNATION_STEPS", 3)
ENABLE_EXPLORATION_ENGINE_V2: bool = _int_env("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", 1) == 1

# Phase 12.6.F — exploration scoper (K = prompt budget only; selector applies final batch limit)
EXPLORATION_SCOPER_K: int = _int_env("AGENT_V2_EXPLORATION_SCOPER_K", 20)
EXPLORATION_SCOPER_SKIP_BELOW: int = _int_env("AGENT_V2_EXPLORATION_SCOPER_SKIP_BELOW", 5)
# Phase 12.6.F — default on (1); set AGENT_V2_ENABLE_EXPLORATION_SCOPER=0 to disable
ENABLE_EXPLORATION_SCOPER: bool = _int_env("AGENT_V2_ENABLE_EXPLORATION_SCOPER", 1) == 1

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
