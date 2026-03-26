"""
Phase 10 — centralized numeric limits for agent_v2 (single source of truth).

Env overrides use AGENT_V2_* prefix to avoid colliding with legacy agent config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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
# Optional LLM synthesis for FinalExplorationSchema (key_insights / objective_coverage only; default off).
ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS: bool = (
    _int_env("AGENT_V2_ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS", 0) == 1
)

# Phase 12.6.F — exploration scoper (K = prompt budget only; selector applies final batch limit)
EXPLORATION_SCOPER_K: int = _int_env("AGENT_V2_EXPLORATION_SCOPER_K", 20)
EXPLORATION_SCOPER_SKIP_BELOW: int = _int_env("AGENT_V2_EXPLORATION_SCOPER_SKIP_BELOW", 5)
# Phase 12.6.F — default on (1); set AGENT_V2_ENABLE_EXPLORATION_SCOPER=0 to disable
ENABLE_EXPLORATION_SCOPER: bool = _int_env("AGENT_V2_ENABLE_EXPLORATION_SCOPER", 1) == 1
# Discovery channel/query budgets and merge cap.
DISCOVERY_SYMBOL_CAP: int = _int_env("AGENT_V2_DISCOVERY_SYMBOL_CAP", 8)
DISCOVERY_REGEX_CAP: int = _int_env("AGENT_V2_DISCOVERY_REGEX_CAP", 6)
DISCOVERY_TEXT_CAP: int = _int_env("AGENT_V2_DISCOVERY_TEXT_CAP", 6)
DISCOVERY_MERGE_TOP_K: int = _int_env("AGENT_V2_DISCOVERY_MERGE_TOP_K", 50)
EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD: int = _int_env(
    "AGENT_V2_EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD_PCT", 20
)
EXPLORATION_MAX_QUERY_RETRIES: int = _int_env("AGENT_V2_EXPLORATION_MAX_QUERY_RETRIES", 1)
# Selector/scoper/read/expand limits
EXPLORATION_SELECTOR_TOP_K: int = _int_env("AGENT_V2_EXPLORATION_SELECTOR_TOP_K", 10)
EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K: int = _int_env(
    "AGENT_V2_EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K", 16
)
EXPLORATION_SNIPPET_MAX_CHARS: int = _int_env("AGENT_V2_EXPLORATION_SNIPPET_MAX_CHARS", 8000)
EXPLORATION_READ_WINDOW: int = _int_env("AGENT_V2_EXPLORATION_READ_WINDOW", 80)
EXPLORATION_READ_SYMBOL_PADDING_LINES: int = _int_env(
    "AGENT_V2_EXPLORATION_READ_SYMBOL_PADDING_LINES", 5
)
EXPLORATION_READ_MAX_CHARS: int = _int_env("AGENT_V2_EXPLORATION_READ_MAX_CHARS", 12000)
EXPLORATION_READ_HEAD_MAX_LINES: int = _int_env("AGENT_V2_EXPLORATION_READ_HEAD_MAX_LINES", 200)
EXPLORATION_EXPAND_MAX_NODES: int = _int_env("AGENT_V2_EXPLORATION_EXPAND_MAX_NODES", 10)
EXPLORATION_EXPAND_MAX_DEPTH: int = _int_env("AGENT_V2_EXPLORATION_EXPAND_MAX_DEPTH", 1)
# Adaptive MPA routing/context budgets
EXPLORATION_ROUTING_SIMPLE_MAX_LINES: int = _int_env(
    "AGENT_V2_EXPLORATION_ROUTING_SIMPLE_MAX_LINES", 250
)
EXPLORATION_ROUTING_COMPLEX_MAX_LINES: int = _int_env(
    "AGENT_V2_EXPLORATION_ROUTING_COMPLEX_MAX_LINES", 350
)
EXPLORATION_CONTEXT_MAX_TOTAL_LINES: int = _int_env(
    "AGENT_V2_EXPLORATION_CONTEXT_MAX_TOTAL_LINES", 300
)
EXPLORATION_CONTEXT_TOP_K_RANGES: int = _int_env(
    "AGENT_V2_EXPLORATION_CONTEXT_TOP_K_RANGES", 6
)
ENABLE_GAP_DRIVEN_EXPANSION: bool = _int_env("AGENT_V2_ENABLE_GAP_DRIVEN_EXPANSION", 1) == 1
ENABLE_GAP_QUALITY_FILTER: bool = _int_env("AGENT_V2_ENABLE_GAP_QUALITY_FILTER", 1) == 1
ENABLE_REFINE_COOLDOWN: bool = _int_env("AGENT_V2_ENABLE_REFINE_COOLDOWN", 1) == 1
ENABLE_UTILITY_STOP: bool = _int_env("AGENT_V2_ENABLE_UTILITY_STOP", 1) == 1
EXPLORATION_UTILITY_NO_IMPROVEMENT_STREAK: int = _int_env(
    "AGENT_V2_EXPLORATION_UTILITY_NO_IMPROVEMENT_STREAK", 2
)

# Retry / replan
MAX_RETRIES: int = _int_env("AGENT_V2_MAX_RETRIES", 2)
MAX_REPLANS: int = _int_env("AGENT_V2_MAX_REPLANS", 2)

# Planner–Explorer controller loop (ModeManager ACT path; default on). Set AGENT_V2_PLANNER_CONTROLLER_LOOP=0 to disable.
PLANNER_CONTROLLER_LOOP: bool = _int_env("AGENT_V2_PLANNER_CONTROLLER_LOOP", 1) == 1
MAX_SUB_EXPLORATIONS_PER_TASK: int = _int_env("AGENT_V2_MAX_SUB_EXPLORATIONS_PER_TASK", 2)
MAX_PLANNER_CONTROLLER_CALLS: int = _int_env("AGENT_V2_MAX_PLANNER_CONTROLLER_CALLS", 16)

# Executor guards (Phase 10 Step 8)
MAX_EXECUTOR_DISPATCHES: int = _int_env("AGENT_V2_MAX_EXECUTOR_DISPATCHES", 20)
MAX_RUNTIME_SECONDS: int = _int_env("AGENT_V2_MAX_RUNTIME_SECONDS", 600)


@dataclass(frozen=True)
class PlannerConfig:
    allowed_actions_read_only: frozenset[str]


@dataclass(frozen=True)
class ExplorationConfig:
    max_steps: int
    allow_partial_for_plan_mode: bool


@dataclass(frozen=True)
class PytestConfig:
    ignore_dirs: tuple[str, ...]


@dataclass(frozen=True)
class PlannerLoopConfig:
    """ModeManager ACT controller loop (planner returns structured controller JSON); executor stays a pure step runner."""

    controller_loop_enabled: bool
    max_sub_explorations_per_task: int
    max_planner_controller_calls: int


@dataclass(frozen=True)
class AgentV2Config:
    planner: PlannerConfig
    exploration: ExplorationConfig
    pytest: PytestConfig
    planner_loop: PlannerLoopConfig


def _build_config() -> AgentV2Config:
    allow_partial = _int_env("AGENT_V2_EXPLORATION_ALLOW_PARTIAL_FOR_PLAN_MODE", 0) == 1
    ignore_dirs_raw = os.environ.get("AGENT_V2_PYTEST_IGNORE_DIRS", "artifacts")
    ignore_dirs = tuple(x.strip() for x in ignore_dirs_raw.split(",") if x.strip())
    return AgentV2Config(
        planner=PlannerConfig(
            # No behavior change: keep current read-only policy defaults.
            allowed_actions_read_only=frozenset({"search", "open_file", "finish"}),
        ),
        exploration=ExplorationConfig(
            max_steps=EXPLORATION_MAX_STEPS,
            allow_partial_for_plan_mode=allow_partial,
        ),
        pytest=PytestConfig(ignore_dirs=ignore_dirs),
        planner_loop=PlannerLoopConfig(
            controller_loop_enabled=PLANNER_CONTROLLER_LOOP,
            max_sub_explorations_per_task=MAX_SUB_EXPLORATIONS_PER_TASK,
            max_planner_controller_calls=MAX_PLANNER_CONTROLLER_CALLS,
        ),
    )


_CONFIG = _build_config()


def get_config() -> AgentV2Config:
    return _CONFIG


def get_project_root() -> str:
    return os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()


def validate_config(config: AgentV2Config) -> None:
    if config.planner_loop.max_sub_explorations_per_task < 0:
        raise ValueError("config.planner_loop.max_sub_explorations_per_task must be >= 0")
    if config.planner_loop.max_planner_controller_calls < 1:
        raise ValueError("config.planner_loop.max_planner_controller_calls must be >= 1")
    if config.exploration.max_steps < 1:
        raise ValueError("config.exploration.max_steps must be >= 1")
    if not config.planner.allowed_actions_read_only:
        raise ValueError("config.planner.allowed_actions_read_only must not be empty")
    write_actions = {"edit", "run_tests", "shell"}
    if set(config.planner.allowed_actions_read_only) & write_actions:
        raise ValueError("config.planner.allowed_actions_read_only must exclude write actions")
    if not isinstance(config.pytest.ignore_dirs, tuple):
        raise ValueError("config.pytest.ignore_dirs must be a tuple")
    if DISCOVERY_SYMBOL_CAP < 1 or DISCOVERY_REGEX_CAP < 1 or DISCOVERY_TEXT_CAP < 1:
        raise ValueError("discovery channel caps must be >= 1")
    if DISCOVERY_MERGE_TOP_K < 1:
        raise ValueError("DISCOVERY_MERGE_TOP_K must be >= 1")
    if EXPLORATION_SELECTOR_TOP_K < 1:
        raise ValueError("EXPLORATION_SELECTOR_TOP_K must be >= 1")
    if EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K < 1:
        raise ValueError("EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K must be >= 1")
    if EXPLORATION_SNIPPET_MAX_CHARS < 1:
        raise ValueError("EXPLORATION_SNIPPET_MAX_CHARS must be >= 1")
    if EXPLORATION_READ_WINDOW < 1:
        raise ValueError("EXPLORATION_READ_WINDOW must be >= 1")
    if EXPLORATION_READ_SYMBOL_PADDING_LINES < 0:
        raise ValueError("EXPLORATION_READ_SYMBOL_PADDING_LINES must be >= 0")
    if EXPLORATION_READ_MAX_CHARS < 1:
        raise ValueError("EXPLORATION_READ_MAX_CHARS must be >= 1")
    if EXPLORATION_READ_HEAD_MAX_LINES < 1:
        raise ValueError("EXPLORATION_READ_HEAD_MAX_LINES must be >= 1")
    if EXPLORATION_EXPAND_MAX_NODES < 1:
        raise ValueError("EXPLORATION_EXPAND_MAX_NODES must be >= 1")
    if EXPLORATION_EXPAND_MAX_DEPTH < 1:
        raise ValueError("EXPLORATION_EXPAND_MAX_DEPTH must be >= 1")
    if EXPLORATION_ROUTING_SIMPLE_MAX_LINES < 1:
        raise ValueError("EXPLORATION_ROUTING_SIMPLE_MAX_LINES must be >= 1")
    if EXPLORATION_ROUTING_COMPLEX_MAX_LINES < 1:
        raise ValueError("EXPLORATION_ROUTING_COMPLEX_MAX_LINES must be >= 1")
    if EXPLORATION_CONTEXT_MAX_TOTAL_LINES < 1:
        raise ValueError("EXPLORATION_CONTEXT_MAX_TOTAL_LINES must be >= 1")
    if EXPLORATION_CONTEXT_TOP_K_RANGES < 1:
        raise ValueError("EXPLORATION_CONTEXT_TOP_K_RANGES must be >= 1")


def get_execution_policy() -> ExecutionPolicy:
    """Build ExecutionPolicy from module defaults + environment."""
    return ExecutionPolicy(
        max_steps=MAX_PLAN_STEPS,
        max_retries_per_step=MAX_RETRIES,
        max_replans=MAX_REPLANS,
        max_executor_dispatches=MAX_EXECUTOR_DISPATCHES,
        max_runtime_seconds=MAX_RUNTIME_SECONDS,
    )
