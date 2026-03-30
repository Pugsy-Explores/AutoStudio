"""
Phase 1 tool exposure — single source aligned with Docs/agent_v2_phase1_tool_contract_audit.md.

Planner-facing tool ids (prompts / engine.tool) map to PlanStep.action values consumed by
PlanValidator and PlanExecutor. No analyze_code; search_web not in phase 1.
"""
from __future__ import annotations

from typing import Final

# All valid engine.tool values (orchestration + act + terminal).
PHASE_1_PLANNER_TOOL_IDS: Final[frozenset[str]] = frozenset(
    {
        "explore",
        "open_file",
        "search_code",
        "run_shell",
        "edit",
        "run_tests",
        "none",
    }
)

# Act-only planner tools (subset of PHASE_1_PLANNER_TOOL_IDS).
PLANNER_ACT_TOOL_IDS: Final[frozenset[str]] = frozenset(
    {"open_file", "search_code", "run_shell", "edit", "run_tests"}
)

# Planner tool id → PlanStep.action (executor / argument layer).
PLANNER_TOOL_TO_PLAN_STEP_ACTION: Final[dict[str, str]] = {
    "search_code": "search",
    "open_file": "open_file",
    "run_shell": "shell",
    "edit": "edit",
    "run_tests": "run_tests",
}

# Inverse: PlanStep.action → planner engine.tool (for tool_execution logs and UI).
PLAN_STEP_ACTION_TO_PLANNER_TOOL: Final[dict[str, str]] = {
    v: k for k, v in PLANNER_TOOL_TO_PLAN_STEP_ACTION.items()
}

# Valid PlanStep.action values for validator (work + terminal).
ALLOWED_PLAN_STEP_ACTIONS: Final[frozenset[str]] = (
    frozenset(PLANNER_TOOL_TO_PLAN_STEP_ACTION.values()) | {"finish"}
)

# PlanStep.action → legacy ReAct uppercase action (shell excluded — separate dispatch path).
PLAN_STEP_TO_LEGACY_REACT_ACTION: Final[dict[str, str]] = {
    "search": "SEARCH",
    "open_file": "READ",
    "edit": "EDIT",
    "run_tests": "RUN_TEST",
}
