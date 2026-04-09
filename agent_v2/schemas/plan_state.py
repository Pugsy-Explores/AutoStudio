"""
Planner-facing progress snapshot. Execution progress comes from DAG runtime in AgentState.context.
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
    """Derive plan progress from ``state.context`` DAG snapshot (dag_completed_step_ids / dag_graph_tasks)."""
    completed: list[PlanStateCompletedStep] = []
    ordered = sorted(plan.steps, key=lambda s: s.index)

    ctx: dict[str, Any] = {}
    if state is not None:
        raw = getattr(state, "context", None)
        if isinstance(raw, dict):
            ctx = raw

    done: set[str] = set()
    raw_done = ctx.get("dag_completed_step_ids")
    if isinstance(raw_done, list):
        done = {str(x) for x in raw_done}

    raw_tasks: dict[str, Any] = {}
    rt = ctx.get("dag_graph_tasks")
    if isinstance(rt, dict):
        raw_tasks = rt

    current_id: Optional[str] = None
    current_idx: Optional[int] = None

    for s in ordered:
        if s.step_id in done:
            summ = ""
            row = raw_tasks.get(s.step_id)
            if isinstance(row, dict):
                rtime = row.get("runtime")
                if isinstance(rtime, dict):
                    lr = rtime.get("last_result")
                    if isinstance(lr, dict):
                        out = lr.get("output")
                        if isinstance(out, dict):
                            summ = str(out.get("summary") or "")
            completed.append(PlanStateCompletedStep(step_id=s.step_id, summary=summ))
        else:
            current_id = s.step_id
            current_idx = s.index
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
