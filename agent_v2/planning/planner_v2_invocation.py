"""
Deterministic rules for when PlannerV2 (`call_planner_with_context`) may run.

See pugsy_ai/task-planner-as-main-runtime-plan.md §0.2.
"""

from __future__ import annotations

from typing import Literal

from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.planner_decision import PlannerDecision

PlannerV2CallContext = Literal[
    "bootstrap",
    "task_decision",
    "post_exploration_merge",
    "failure_or_insufficiency_replan",
    "progress_refresh",
]


def plan_document_valid_for_v2_gate(plan_doc: PlanDocument | None) -> bool:
    """
    True when a PlanDocument exists with at least one step (schema-valid artifact).

    When False, bootstrap / initial materialization is allowed per §0.2.
    """
    if plan_doc is None:
        return False
    steps = plan_doc.steps or []
    if not steps:
        return False
    return True


def plan_document_has_runnable_work(plan_doc: PlanDocument | None) -> bool:
    """True when there is at least one step not yet completed (executor may run)."""
    if plan_doc is None:
        return False
    steps = plan_doc.steps or []
    return any(s.execution.status != "completed" for s in steps)


def should_call_planner_v2(
    *,
    context: PlannerV2CallContext,
    decision: PlannerDecision | None = None,
    plan_valid: bool = True,
) -> bool:
    """
    Return True iff PlannerV2 may be invoked for this call site.

    - bootstrap: first plan materialization when no valid plan exists.
    - task_decision: gate using TaskPlanner / outer-loop decision only.
    - post_exploration_merge: merge new exploration into PlanDocument (always plan work).
    - failure_or_insufficiency_replan: replan after failure or insufficiency (replan context).
    - progress_refresh: refresh plan after executor progress (plan continuation).

    For explore/act/synthesize, only task_decision is used; those types return False.
    """
    if context == "bootstrap":
        return not plan_valid

    if context == "post_exploration_merge":
        return True

    if context == "failure_or_insufficiency_replan":
        return True

    if context == "progress_refresh":
        return True

    # task_decision
    if decision is None:
        raise ValueError("task_decision context requires decision")
    if decision.type in ("explore", "act", "synthesize"):
        return False
    if decision.type in ("plan", "replan"):
        return True
    if decision.type == "stop":
        return False
    return not plan_valid
