"""
Runtime control plane for one planner–executor tick (separate from PlanDocument steps).

Orchestration MUST branch only on PlannerDecision — not raw PlanDocument.controller.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .plan import PlanStep

PlannerDecisionType = Literal["explore", "act", "replan", "stop"]


class PlannerDecision(BaseModel):
    """
    Single structured decision for the outer loop (explore / act / replan / stop).

    - ``act`` with ``step is None``: executor selects the next runnable step (current contract).
    - ``explore``: use ``query`` (may be empty string if planner emitted blank; caller may coerce).
    """

    type: PlannerDecisionType
    step: Optional[PlanStep] = Field(
        default=None,
        description="Set when executing a specific step; None means delegate to executor.",
    )
    query: Optional[str] = Field(default=None, description="Sub-exploration query when type==explore.")
    tool: Optional[str] = Field(
        default=None,
        description="Planner tool id from PlanDocument.engine.tool when present (telemetry).",
    )
