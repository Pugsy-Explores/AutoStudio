"""
Thin task planner I/O — decision provider contract (no runtime execution).

TaskPlanner emits the same control-plane type as the runtime loop (`PlannerDecision`).
`PlannerAction` is a backward-compatible alias (isomorphic; no separate schema).

See Docs/architecture_freeze/full-planner-arch-freeze-impl.md §4.1.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

from agent_v2.schemas.planner_decision import PlannerDecision

# Control plane: TaskPlannerService.decide() returns this type (same as outer-loop branches).
PlannerAction = PlannerDecision


class PlannerDecisionSnapshot(BaseModel):
    """
    Inputs to a pure decide() — no raw code blobs; bounded summaries only.

    Built by PlannerTaskRuntime only.
    """

    instruction: str = ""
    rolling_conversation_summary: str = ""
    working_memory_fingerprint: str = ""
    last_exploration_confidence: Optional[str] = None
    last_exploration_gaps_count: int = 0
    last_exploration_query_hash: Optional[str] = None
    outer_iteration: int = 0
    # Bounded enrichment (optional; defaults keep small-model paths cheap).
    has_pending_plan_work: Optional[bool] = None
    last_executor_status: Optional[str] = None
    last_loop_outcome: str = ""
    act_controller_iteration_count: int = 0

    @field_validator(
        "instruction",
        "rolling_conversation_summary",
        "working_memory_fingerprint",
        "last_loop_outcome",
        mode="before",
    )
    @classmethod
    def _cap_text(cls, v: object) -> str:
        s = str(v or "").strip()
        return s[:8000]

    @field_validator("last_executor_status", mode="before")
    @classmethod
    def _cap_executor_status(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s[:256] if s else None
