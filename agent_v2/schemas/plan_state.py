"""
Minimal read-only snapshot for PlannerV2 when ModeManager runs the controller loop.

Not a full execution mirror — executor owns PlanDocument and PlanStep.execution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agent_v2.schemas.plan import PlanDocument


class PlanStateCompletedStep(BaseModel):
    step_id: str
    summary: str = ""


class PlanState(BaseModel):
    """Planner-facing progress snapshot (bounded, no tool log dumps)."""

    completed_steps: list[PlanStateCompletedStep] = Field(default_factory=list)
    current_step_id: Optional[str] = None
    current_step_index: Optional[int] = None
    last_result_summary: str = ""


def plan_state_from_plan_document(
    plan: "PlanDocument",
    *,
    last_result_summary: str = "",
) -> PlanState:
    """Build a bounded snapshot from current plan execution state."""
    completed: list[PlanStateCompletedStep] = []
    ordered = sorted(plan.steps, key=lambda s: s.index)
    current_id: Optional[str] = None
    current_idx: Optional[int] = None
    for s in ordered:
        if s.execution.status == "completed":
            summ = ""
            if s.execution.last_result is not None:
                summ = str(s.execution.last_result.output_summary or "")
            completed.append(PlanStateCompletedStep(step_id=s.step_id, summary=summ))
        else:
            current_id = s.step_id
            current_idx = s.index
            break
    return PlanState(
        completed_steps=completed,
        current_step_id=current_id,
        current_step_index=current_idx,
        last_result_summary=last_result_summary,
    )
