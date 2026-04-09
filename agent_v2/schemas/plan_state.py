"""
Planner-facing progress snapshot. Execution progress comes from executor metadata on AgentState.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agent_v2.schemas.plan import PlanDocument


class PlanStateCompletedStep(BaseModel):
    step_id: str
    summary: str = ""


class PlanState(BaseModel):
    """Bounded snapshot for PlannerV2 (no tool log dumps)."""

    completed_steps: list[PlanStateCompletedStep] = Field(default_factory=list)
    current_step_id: Optional[str] = None
    current_step_index: Optional[int] = None
    last_result_summary: str = ""


def plan_state_from_plan_document(
    plan: "PlanDocument",
    *,
    last_result_summary: str = "",
    state: Any | None = None,
) -> PlanState:
    """Derive plan progress from ``state.metadata`` executor fields (no context DAG)."""
    completed: list[PlanStateCompletedStep] = []

    done: set[str] = set()
    if state is not None:
        md = getattr(state, "metadata", None)
        if isinstance(md, dict):
            ep = md.get("executor_dag_plan_id")
            if ep is not None and str(ep) == str(plan.plan_id):
                raw_done = md.get("executor_dag_completed_ids")
                if isinstance(raw_done, list):
                    done = {str(x) for x in raw_done}

    current_id: Optional[str] = None
    current_idx: Optional[int] = None

    for s in plan.steps:
        if s.step_id in done:
            completed.append(PlanStateCompletedStep(step_id=s.step_id, summary=""))
        else:
            current_id = s.step_id
            current_idx = None
            break

    summ_out = last_result_summary
    if not summ_out and completed:
        summ_out = completed[-1].summary

    return PlanState(
        completed_steps=completed,
        current_step_id=current_id,
        current_step_index=current_idx,
        last_result_summary=summ_out,
    )
