"""
Phase 10 — centralized numeric limits for agent_v2 (single source of truth).

Env overrides use AGENT_V2_* prefix to avoid colliding with legacy agent config.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_v2.runtime.phase1_tool_exposure import ALLOWED_PLAN_STEP_ACTIONS
from agent_v2.schemas.policies import ExecutionPolicy


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def use_file_conversation_memory() -> bool:
    """
    When True (default), conversation turns persist under ``.agent_memory/sessions/``.

    Env ``AGENT_V2_USE_FILE_CONVERSATION_MEMORY``: set to 0/false/off to use
    ``InMemoryConversationMemoryStore`` only (process-local, pre–Phase 5.2 behavior).
    """
    return _bool_env("AGENT_V2_USE_FILE_CONVERSATION_MEMORY", True)


def get_conversation_sessions_dir() -> str:
    """
    Root directory for per-session JSON files (Phase 5.2).

    Env ``AGENT_V2_CONVERSATION_SESSIONS_DIR``: optional override (expanded, resolved).
    Default: ``.agent_memory/sessions`` under the current working directory.
    """
    raw = os.environ.get("AGENT_V2_CONVERSATION_SESSIONS_DIR")
    if raw is not None and str(raw).strip():
        return str(Path(str(raw).strip()).expanduser().resolve())
    return str(Path(".agent_memory/sessions").resolve())


def get_semantic_memory_dir() -> Path:
    """
    Root directory for semantic facts (Phase 5.3); facts live at ``facts.jsonl`` under this path.

    Env ``AGENT_V2_SEMANTIC_MEMORY_DIR``: optional override (expanded, resolved).
    Default: ``.agent_memory/semantic`` under the current working directory.
    """
    raw = os.environ.get("AGENT_V2_SEMANTIC_MEMORY_DIR")
    if raw is not None and str(raw).strip():
        return Path(str(raw).strip()).expanduser().resolve()
    return Path(".agent_memory/semantic").resolve()


def get_agent_v2_episodic_log_dir() -> str | None:
    """
    Root directory for TraceEmitter JSONL execution logs (Phase 5.1 episodic memory).

    Env ``AGENT_V2_EPISODIC_LOG_DIR``: unset → default ``.agent_memory/episodic`` (resolved cwd);
    set to empty string → disable on-disk execution logs (returns None).
    """
    raw = os.environ.get("AGENT_V2_EPISODIC_LOG_DIR")
    if raw is not None and not str(raw).strip():
        return None
    if raw is None:
        rel = Path(".agent_memory/episodic")
    else:
        rel = Path(str(raw).strip())
    return str(rel.expanduser().resolve())


def enable_episodic_injection() -> bool:
    """
    Phase 5.5a — inject last episodic execution failures into planner context prompts.

    Env ``AGENT_V2_ENABLE_EPISODIC_FAILURE_INJECTION``: set to 0/false/off to disable (default: on).
    """
    return _bool_env("AGENT_V2_ENABLE_EPISODIC_FAILURE_INJECTION", True)


def enable_semantic_injection() -> bool:
    """
    Phase 5.5b — inject token-matched semantic facts into planner context prompts.

    Env ``AGENT_V2_ENABLE_SEMANTIC_INJECTION``: set to 1/true/on to enable (default: off).
    """
    return _bool_env("AGENT_V2_ENABLE_SEMANTIC_INJECTION", False)


# Plan / exploration (architecture freeze §3.1)
MAX_PLAN_STEPS: int = _int_env("AGENT_V2_MAX_PLAN_STEPS", 8)
EXPLORATION_STEPS: int = _int_env("AGENT_V2_EXPLORATION_STEPS", 5)
EXPLORATION_MAX_STEPS: int = _int_env("AGENT_V2_EXPLORATION_MAX_STEPS", 5)
EXPLORATION_MAX_BACKTRACKS: int = _int_env("AGENT_V2_EXPLORATION_MAX_BACKTRACKS", 2)
# Exploration refactor: max REFINE cycles (mapper-driven); separate from legacy backtracks.
EXPLORATION_MAX_REFINE_CYCLES: int = _int_env("AGENT_V2_EXPLORATION_MAX_REFINE_CYCLES", 2)
EXPLORATION_MAX_ITEMS: int = _int_env("AGENT_V2_EXPLORATION_MAX_ITEMS", 6)
# Consecutive steps with no new evidence key (file, symbol, read_source) or duplicate-queue skips → stalled
EXPLORATION_STAGNATION_STEPS: int = _int_env("AGENT_V2_EXPLORATION_STAGNATION_STEPS", 3)
ENABLE_EXPLORATION_ENGINE_V2: bool = _int_env("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", 1) == 1
# Optional LLM synthesis for FinalExplorationSchema (key_insights / objective_coverage only; default off).
ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS: bool = (
    _int_env("AGENT_V2_ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS", 0) == 1
)
# V1 user-facing answer from exploration (post-exploration, pre-planner). Default on; set AGENT_V2_ENABLE_ANSWER_SYNTHESIS=0 to disable; see Docs/agent_v2_answer_synthesis_audit_and_spec.md
ENABLE_ANSWER_SYNTHESIS: bool = _int_env("AGENT_V2_ENABLE_ANSWER_SYNTHESIS", 1) == 1
# Evidence rows passed to answer synthesizer prompt (7B: ideal 3–6; cap at 8).
ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS: int = max(
    1, min(8, _int_env("AGENT_V2_ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS", 8))
)
ANSWER_SYNTHESIS_IDEAL_EVIDENCE_ITEMS: int = 6

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
# Discovery parallelism: nested pools previously allowed up to 3×4 parallel SEARCH runs (multi‑GB agent RSS).
# Defaults favor memory; raise for throughput (e.g. batch=4, pool=3).
DISCOVERY_SEARCH_BATCH_MAX_WORKERS: int = max(
    1, min(16, _int_env("AGENT_V2_DISCOVERY_SEARCH_BATCH_MAX_WORKERS", 1))
)
DISCOVERY_QUERY_POOL_MAX_WORKERS: int = max(1, _int_env("AGENT_V2_DISCOVERY_QUERY_POOL_MAX_WORKERS", 3))
# Discovery: file-level merge → optional cross-encoder rerank → post-rerank cap (before scoper).
EXPLORATION_DISCOVERY_RERANK_ENABLED: bool = (
    _int_env("AGENT_V2_EXPLORATION_DISCOVERY_RERANK_ENABLED", 1) == 1
)
EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX: int = _int_env(
    "AGENT_V2_EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX",
    DISCOVERY_MERGE_TOP_K,
)
EXPLORATION_DISCOVERY_POST_RERANK_TOP_K: int = _int_env(
    "AGENT_V2_EXPLORATION_DISCOVERY_POST_RERANK_TOP_K",
    18,
)
# -1 → use config.retrieval_config.RERANK_MIN_CANDIDATES at runtime
EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES: int = _int_env(
    "AGENT_V2_EXPLORATION_DISCOVERY_RERANK_MIN_CANDIDATES",
    -1,
)
EXPLORATION_DISCOVERY_RERANK_USE_FUSION: bool = (
    _int_env("AGENT_V2_EXPLORATION_DISCOVERY_RERANK_USE_FUSION", 1) == 1
)
EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS: int = _int_env(
    "AGENT_V2_EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS",
    _int_env("AGENT_V2_EXPLORATION_SNIPPET_MAX_CHARS", 8000),
)
EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD: int = _int_env(
    "AGENT_V2_EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD_PCT", 20
)
# Initial discovery-only query-intent re-parse (extra LLM + duplicate SEARCH). Default 0: rely on first intent + loop refine.
EXPLORATION_MAX_QUERY_RETRIES: int = _int_env("AGENT_V2_EXPLORATION_MAX_QUERY_RETRIES", 0)

# Multi-repo retrieval evaluation (indexed clones + path roots). JSON list overrides defaults.
EXPLORATION_TEST_REPOS_JSON: str | None = (
    os.environ.get("AGENT_V2_EXPLORATION_TEST_REPOS_JSON", "").strip() or None
)
# When 1, resolved git/path repo directories are appended to multi-root retrieval (with RETRIEVAL_EXTRA_PROJECT_ROOTS).
APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL: bool = (
    _int_env("AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL", 0) == 1
)

EXPLORATION_TEST_REPOS: list[dict[str, str]] = [
    {"name": "local_repo", "path": "agent_v2"},
    {
        "name": "mini_projects",
        "git": "https://github.com/Python-World/python-mini-projects.git",
    },
    {
        "name": "concurrency_repo",
        "git": "https://github.com/kevinknights29/Concurrent-and-Parallel-Programming-in-Python.git",
    },
]


def exploration_test_repos_resolved() -> list[dict[str, Any]]:
    """Exploration harness repo specs: env JSON or embedded default (no hardcoded clone paths)."""
    # Read env at call time — module-level EXPLORATION_TEST_REPOS_JSON is fixed at first import;
    # harnesses set AGENT_V2_EXPLORATION_TEST_REPOS_JSON after startup.
    raw = os.environ.get("AGENT_V2_EXPLORATION_TEST_REPOS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    if EXPLORATION_TEST_REPOS_JSON:
        return json.loads(EXPLORATION_TEST_REPOS_JSON)
    return list(EXPLORATION_TEST_REPOS)
# Selector/scoper/read/expand limits
# Keep selector batch candidate breadth at or above 2 globally.
EXPLORATION_SELECTOR_TOP_K: int = max(
    2, _int_env("AGENT_V2_EXPLORATION_SELECTOR_TOP_K", 10)
)
EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K: int = _int_env(
    "AGENT_V2_EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K", 16
)
# Symbol-aware selection: deterministic outlines + batched graph context for analyzer (default on).
ENABLE_SYMBOL_AWARE_EXPLORATION: bool = (
    _int_env("AGENT_V2_ENABLE_SYMBOL_AWARE_EXPLORATION", 1) == 1
)
# Max outline entries per file shown to the batch selector after deterministic rank (plan: 10–15).
EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR: int = max(
    1, min(30, _int_env("AGENT_V2_EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR", 12))
)
# Cap total ``code`` payload in selector ``outline_for_prompt`` (full bodies + signatures + trim notice).
MAX_SELECTOR_CODE_CHARS: int = max(
    1000, _int_env("AGENT_V2_MAX_SELECTOR_CODE_CHARS", 45000)
)
# Analyzer context caps for deterministic selector-symbol expansion integration.
MAX_ANALYZER_CONTEXT_CHARS: int = max(
    1000, _int_env("AGENT_V2_MAX_ANALYZER_CONTEXT_CHARS", 45000)
)
MAX_ANALYZER_SYMBOL_CONTEXT_CHARS: int = max(
    500, _int_env("AGENT_V2_MAX_ANALYZER_SYMBOL_CONTEXT_CHARS", 22500)
)
# Callers/callees per symbol side in analyzer SYMBOL RELATIONSHIPS block.
EXPLORATION_SYMBOL_GRAPH_CONTEXT_K: int = max(
    1, _int_env("AGENT_V2_EXPLORATION_SYMBOL_GRAPH_CONTEXT_K", 5)
)
EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_CHARS: int = max(
    200, _int_env("AGENT_V2_EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_CHARS", 4000)
)
# Cap how many validated selector symbol names are passed to the graph batch per target.
EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES: int = max(
    1, min(20, _int_env("AGENT_V2_EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES", 3))
)
# Terminal INFO lines for symbol-aware phases (outlines, batch select, sqlite graph, inspect/analyze).
EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS: bool = (
    _int_env("AGENT_V2_EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS", 1) == 1
)
# Symbol graph SQLite paths (same env keys as config.repo_graph_config; centralized for agent_v2).
try:
    from config.repo_graph_config import INDEX_SQLITE as EXPLORATION_REPO_GRAPH_INDEX_SQLITE
    from config.repo_graph_config import SYMBOL_GRAPH_DIR as EXPLORATION_REPO_SYMBOL_GRAPH_DIR
except ImportError:
    EXPLORATION_REPO_SYMBOL_GRAPH_DIR = ".symbol_graph"
    EXPLORATION_REPO_GRAPH_INDEX_SQLITE = "index.sqlite"
EXPLORATION_SNIPPET_MAX_CHARS: int = _int_env("AGENT_V2_EXPLORATION_SNIPPET_MAX_CHARS", 8000)
EXPLORATION_READ_WINDOW: int = _int_env("AGENT_V2_EXPLORATION_READ_WINDOW", 80)
EXPLORATION_READ_SYMBOL_PADDING_LINES: int = _int_env(
    "AGENT_V2_EXPLORATION_READ_SYMBOL_PADDING_LINES", 5
)
EXPLORATION_READ_MAX_CHARS: int = _int_env("AGENT_V2_EXPLORATION_READ_MAX_CHARS", 12000)
EXPLORATION_READ_HEAD_MAX_LINES: int = _int_env("AGENT_V2_EXPLORATION_READ_HEAD_MAX_LINES", 200)
EXPLORATION_EXPAND_MAX_NODES: int = _int_env("AGENT_V2_EXPLORATION_EXPAND_MAX_NODES", 10)
EXPLORATION_EXPAND_MAX_DEPTH: int = _int_env("AGENT_V2_EXPLORATION_EXPAND_MAX_DEPTH", 1)
EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K: int = _int_env(
    "AGENT_V2_EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K", 8
)
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
# ACT controller inner loop (TaskPlanner decisions); bounds iterations before forced synthesize+stop.
MAX_ACT_CONTROLLER_ITERATIONS: int = max(
    1, _int_env("AGENT_V2_MAX_ACT_CONTROLLER_ITERATIONS", 256)
)
# TaskPlanner loop: shadow compare (TaskPlanner vs PlanDocument) and authoritative control (default off).
TASK_PLANNER_SHADOW_LOOP: bool = _bool_env("AGENT_V2_TASK_PLANNER_SHADOW_LOOP")
TASK_PLANNER_AUTHORITATIVE_LOOP: bool = _bool_env("AGENT_V2_TASK_PLANNER_AUTHORITATIVE_LOOP")
# When authoritative + this flag: prefer steps-only planner JSON (require_controller_json=False on PlannerV2 calls).
PLANNER_PLAN_BODY_ONLY_WHEN_TASK_PLANNER_AUTHORITATIVE: bool = _bool_env(
    "AGENT_V2_PLANNER_PLAN_BODY_ONLY_WHEN_TASK_PLANNER_AUTHORITATIVE"
)
# Post-synthesis answer validation in ACT controller loop (default on).
ENABLE_ANSWER_VALIDATION: bool = _int_env("AGENT_V2_ENABLE_ANSWER_VALIDATION", 1) == 1
MAX_ANSWER_VALIDATION_ROUNDS_PER_TASK: int = max(
    1, _int_env("AGENT_V2_MAX_ANSWER_VALIDATION_ROUNDS_PER_TASK", 8)
)
# Optional LLM second pass after rules validation (default off; set AGENT_V2_ENABLE_ANSWER_VALIDATION_LLM=1).
ENABLE_ANSWER_VALIDATION_LLM: bool = _int_env("AGENT_V2_ENABLE_ANSWER_VALIDATION_LLM", 0) == 1
# Phase B: require explicit planner `tool` in JSON (no inference from step). Env: PLANNER_STRICT_TOOL=1 or AGENT_V2_PLANNER_STRICT_TOOL=1
PLANNER_STRICT_TOOL: bool = _bool_env("PLANNER_STRICT_TOOL") or _bool_env("AGENT_V2_PLANNER_STRICT_TOOL")

# Chat-aware planning (thin task planner, stop policy, synthesis skip)
ENABLE_THIN_TASK_PLANNER: bool = _bool_env("AGENT_V2_ENABLE_THIN_TASK_PLANNER")
ENABLE_EXPLORATION_STOP_POLICY: bool = _bool_env("AGENT_V2_ENABLE_EXPLORATION_STOP_POLICY")
SKIP_ANSWER_SYNTHESIS_WHEN_SUFFICIENT: bool = _bool_env("AGENT_V2_SKIP_ANSWER_SYNTHESIS_WHEN_SUFFICIENT")

# Executor guards (Phase 10 Step 8)
MAX_EXECUTOR_DISPATCHES: int = _int_env("AGENT_V2_MAX_EXECUTOR_DISPATCHES", 20)
MAX_RUNTIME_SECONDS: int = _int_env("AGENT_V2_MAX_RUNTIME_SECONDS", 600)
# Cap tool output embedded in planner prompts (e.g. open_file) so small-context endpoints do not 400.
PLANNER_PROMPT_MAX_LAST_RESULT_CHARS: int = _int_env(
    "AGENT_V2_PLANNER_PROMPT_MAX_LAST_RESULT_CHARS", 8000
)


@dataclass(frozen=True)
class PlannerConfig:
    allowed_actions_read_only: frozenset[str]
    """Steps allowed when task_mode=read_only (validator); excludes run_tests/shell."""

    allowed_actions_plan_safe: frozenset[str]
    """Steps allowed when task_mode=plan_safe (iterative plan execution); excludes edit."""

    strict_tool: bool


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
    max_act_controller_iterations: int
    task_planner_shadow_loop: bool
    task_planner_authoritative_loop: bool
    planner_plan_body_only_when_task_planner_authoritative: bool
    enable_answer_validation: bool
    max_answer_validation_rounds_per_task: int
    enable_answer_validation_llm: bool


@dataclass(frozen=True)
class ChatPlanningConfig:
    """Chat-aware planning flags (Docs/architecture_freeze/full-planner-arch-freeze-impl.md)."""

    enable_thin_task_planner: bool
    enable_exploration_stop_policy: bool
    skip_answer_synthesis_when_sufficient: bool


@dataclass(frozen=True)
class AgentV2Config:
    planner: PlannerConfig
    exploration: ExplorationConfig
    pytest: PytestConfig
    planner_loop: PlannerLoopConfig
    chat_planning: ChatPlanningConfig


def _build_config() -> AgentV2Config:
    allow_partial = _int_env("AGENT_V2_EXPLORATION_ALLOW_PARTIAL_FOR_PLAN_MODE", 0) == 1
    ignore_dirs_raw = os.environ.get("AGENT_V2_PYTEST_IGNORE_DIRS", "artifacts")
    ignore_dirs = tuple(x.strip() for x in ignore_dirs_raw.split(",") if x.strip())
    return AgentV2Config(
        planner=PlannerConfig(
            # No behavior change: keep current read-only policy defaults.
            allowed_actions_read_only=frozenset({"search", "open_file", "finish"}),
            allowed_actions_plan_safe=frozenset(
                {"search", "open_file", "run_tests", "shell", "finish"}
            ),
            strict_tool=PLANNER_STRICT_TOOL,
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
            max_act_controller_iterations=MAX_ACT_CONTROLLER_ITERATIONS,
            task_planner_shadow_loop=TASK_PLANNER_SHADOW_LOOP,
            task_planner_authoritative_loop=TASK_PLANNER_AUTHORITATIVE_LOOP,
            planner_plan_body_only_when_task_planner_authoritative=(
                PLANNER_PLAN_BODY_ONLY_WHEN_TASK_PLANNER_AUTHORITATIVE
            ),
            enable_answer_validation=ENABLE_ANSWER_VALIDATION,
            max_answer_validation_rounds_per_task=MAX_ANSWER_VALIDATION_ROUNDS_PER_TASK,
            enable_answer_validation_llm=ENABLE_ANSWER_VALIDATION_LLM,
        ),
        chat_planning=ChatPlanningConfig(
            enable_thin_task_planner=ENABLE_THIN_TASK_PLANNER,
            enable_exploration_stop_policy=ENABLE_EXPLORATION_STOP_POLICY,
            skip_answer_synthesis_when_sufficient=SKIP_ANSWER_SYNTHESIS_WHEN_SUFFICIENT,
        ),
    )


_CONFIG = _build_config()


def get_config() -> AgentV2Config:
    return _CONFIG


def get_project_root() -> str:
    return os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()


def exploration_symbol_graph_lookup_enabled() -> bool:
    """Whether symbol-relationships block may query the repo graph (mirrors ENABLE_GRAPH_LOOKUP)."""
    try:
        from config.retrieval_config import ENABLE_GRAPH_LOOKUP
    except ImportError:
        return True
    return bool(ENABLE_GRAPH_LOOKUP)


def validate_config(config: AgentV2Config) -> None:
    if config.planner_loop.max_sub_explorations_per_task < 0:
        raise ValueError("config.planner_loop.max_sub_explorations_per_task must be >= 0")
    if config.planner_loop.max_planner_controller_calls < 1:
        raise ValueError("config.planner_loop.max_planner_controller_calls must be >= 1")
    if config.planner_loop.max_act_controller_iterations < 1:
        raise ValueError("config.planner_loop.max_act_controller_iterations must be >= 1")
    if config.planner_loop.max_answer_validation_rounds_per_task < 1:
        raise ValueError("config.planner_loop.max_answer_validation_rounds_per_task must be >= 1")
    if config.exploration.max_steps < 1:
        raise ValueError("config.exploration.max_steps must be >= 1")
    if not config.planner.allowed_actions_read_only:
        raise ValueError("config.planner.allowed_actions_read_only must not be empty")
    write_actions = {"edit", "run_tests", "shell"}
    if set(config.planner.allowed_actions_read_only) & write_actions:
        raise ValueError("config.planner.allowed_actions_read_only must exclude write actions")
    if not config.planner.allowed_actions_plan_safe:
        raise ValueError("config.planner.allowed_actions_plan_safe must not be empty")
    if "edit" in config.planner.allowed_actions_plan_safe:
        raise ValueError("config.planner.allowed_actions_plan_safe must exclude 'edit'")
    bad_ps = set(config.planner.allowed_actions_plan_safe) - ALLOWED_PLAN_STEP_ACTIONS
    if bad_ps:
        raise ValueError(
            f"config.planner.allowed_actions_plan_safe contains unknown actions: {bad_ps}"
        )
    if not isinstance(config.pytest.ignore_dirs, tuple):
        raise ValueError("config.pytest.ignore_dirs must be a tuple")
    if DISCOVERY_SYMBOL_CAP < 1 or DISCOVERY_REGEX_CAP < 1 or DISCOVERY_TEXT_CAP < 1:
        raise ValueError("discovery channel caps must be >= 1")
    if DISCOVERY_MERGE_TOP_K < 1:
        raise ValueError("DISCOVERY_MERGE_TOP_K must be >= 1")
    if EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX < 1:
        raise ValueError("EXPLORATION_DISCOVERY_PRERERANK_POOL_MAX must be >= 1")
    if EXPLORATION_DISCOVERY_POST_RERANK_TOP_K < 1:
        raise ValueError("EXPLORATION_DISCOVERY_POST_RERANK_TOP_K must be >= 1")
    if EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS < 1:
        raise ValueError("EXPLORATION_DISCOVERY_SNIPPET_MERGE_MAX_CHARS must be >= 1")
    if EXPLORATION_SELECTOR_TOP_K < 1:
        raise ValueError("EXPLORATION_SELECTOR_TOP_K must be >= 1")
    if EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K < 1:
        raise ValueError("EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K must be >= 1")
    if EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR < 1:
        raise ValueError("EXPLORATION_OUTLINE_TOP_K_FOR_SELECTOR must be >= 1")
    if EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES < 1:
        raise ValueError("EXPLORATION_SYMBOL_RELATIONSHIPS_MAX_NAMES must be >= 1")
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
    if EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K < 1:
        raise ValueError("EXPLORATION_PENDING_EXPANSION_SYMBOLS_TOP_K must be >= 1")
    if EXPLORATION_ROUTING_SIMPLE_MAX_LINES < 1:
        raise ValueError("EXPLORATION_ROUTING_SIMPLE_MAX_LINES must be >= 1")
    if EXPLORATION_ROUTING_COMPLEX_MAX_LINES < 1:
        raise ValueError("EXPLORATION_ROUTING_COMPLEX_MAX_LINES must be >= 1")
    if EXPLORATION_CONTEXT_MAX_TOTAL_LINES < 1:
        raise ValueError("EXPLORATION_CONTEXT_MAX_TOTAL_LINES must be >= 1")
    if EXPLORATION_CONTEXT_TOP_K_RANGES < 1:
        raise ValueError("EXPLORATION_CONTEXT_TOP_K_RANGES must be >= 1")
    if ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS < 1 or ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS > 8:
        raise ValueError("ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS must be in 1..8")


def get_execution_policy() -> ExecutionPolicy:
    """Build ExecutionPolicy from module defaults + environment."""
    return ExecutionPolicy(
        max_steps=MAX_PLAN_STEPS,
        max_retries_per_step=MAX_RETRIES,
        max_replans=MAX_REPLANS,
        max_executor_dispatches=MAX_EXECUTOR_DISPATCHES,
        max_runtime_seconds=MAX_RUNTIME_SECONDS,
    )
