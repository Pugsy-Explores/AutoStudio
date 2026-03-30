"""
Stage 1 schemas for Hierarchical Phased Orchestration.
Pure data definitions. Zero execution logic.
"""

from __future__ import annotations

import secrets
from typing import TypedDict


class PhaseValidationContract(TypedDict):
    require_ranked_context: bool
    require_explain_success: bool
    min_candidates: int


class PhaseRetryPolicy(TypedDict):
    max_parent_retries: int


class PhasePlan(TypedDict):
    phase_id: str
    phase_index: int
    subgoal: str
    lane: str
    steps: list
    plan_id: str
    validation: PhaseValidationContract
    retry_policy: PhaseRetryPolicy


class ParentPlan(TypedDict):
    parent_plan_id: str
    instruction: str
    decomposition_type: str
    phases: list[PhasePlan]
    compatibility_mode: bool


class PhaseResult(TypedDict):
    phase_id: str
    phase_index: int
    success: bool
    failure_class: str | None
    goal_met: bool
    goal_reason: str
    completed_steps: int
    context_output: dict
    attempt_count: int
    loop_output: dict


def new_phase_id() -> str:
    """Returns 'phase_' + 8 lowercase hex chars."""
    return "phase_" + secrets.token_hex(4)


def new_parent_plan_id() -> str:
    """Returns 'pplan_' + 8 lowercase hex chars."""
    return "pplan_" + secrets.token_hex(4)


def make_compatibility_parent_plan(flat_plan: dict, instruction: str) -> ParentPlan:
    """Wrap a flat plan in a single-phase compatibility ParentPlan."""
    from planner.planner_utils import is_explicit_docs_lane_by_structure

    lane = "docs" if is_explicit_docs_lane_by_structure(flat_plan) else "code"
    steps = flat_plan.get("steps", [])
    plan_id = flat_plan.get("plan_id", "")
    subgoal = (instruction or "")[:200]

    validation: PhaseValidationContract = {
        "require_ranked_context": False,
        "require_explain_success": False,
        "min_candidates": 0,
    }
    retry_policy: PhaseRetryPolicy = {"max_parent_retries": 0}

    phase: PhasePlan = {
        "phase_id": new_phase_id(),
        "phase_index": 0,
        "subgoal": subgoal,
        "lane": lane,
        "steps": steps,
        "plan_id": plan_id,
        "validation": validation,
        "retry_policy": retry_policy,
    }

    return {
        "parent_plan_id": new_parent_plan_id(),
        "instruction": instruction or "",
        "decomposition_type": "compatibility",
        "phases": [phase],
        "compatibility_mode": True,
    }


def validate_parent_plan_schema(parent_plan: ParentPlan) -> bool:
    """
    Returns True if parent_plan satisfies the schema. Returns False for malformed inputs.
    Never raises. Does not call validate_plan().
    """
    try:
        if not isinstance(parent_plan, dict):
            return False

        # Parent-level required keys
        for key in ("parent_plan_id", "instruction", "decomposition_type", "phases", "compatibility_mode"):
            if key not in parent_plan:
                return False

        phases = parent_plan.get("phases")
        if not isinstance(phases, list) or len(phases) == 0:
            return False

        phase_keys = (
            "phase_id",
            "phase_index",
            "subgoal",
            "lane",
            "steps",
            "plan_id",
            "validation",
            "retry_policy",
        )
        validation_keys = ("require_ranked_context", "require_explain_success", "min_candidates")
        retry_keys = ("max_parent_retries",)

        for phase in phases:
            if not isinstance(phase, dict):
                return False
            for key in phase_keys:
                if key not in phase:
                    return False
            if phase.get("lane") not in ("docs", "code"):
                return False
            if not isinstance(phase.get("steps"), list):
                return False
            val = phase.get("validation")
            if not isinstance(val, dict):
                return False
            for k in validation_keys:
                if k not in val:
                    return False
            rp = phase.get("retry_policy")
            if not isinstance(rp, dict):
                return False
            for k in retry_keys:
                if k not in rp:
                    return False

        return True
    except Exception:
        return False
