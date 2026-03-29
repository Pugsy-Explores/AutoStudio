"""
Single envelope for planner.plan — avoids branching on Union types at call sites.

Runtime always builds PlannerPlanContext; PlannerV2 normalizes legacy FinalExplorationSchema | ReplanContext.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from agent_v2.schemas.exploration import QueryIntent
from agent_v2.schemas.replan import ReplanContext


class ExplorationInsufficientContext(BaseModel):
    """Structured signal when exploration metadata is weak or incomplete (planner adapts)."""

    message: str
    termination_reason: str = ""


class PlannerPlanContext(BaseModel):
    """
    Unified planner input from orchestration.

    - Exploration path: set ``exploration``; optionally ``insufficiency`` when metadata is weak.
    - Execution replan path: set ``replan`` (failure / insufficiency from prior plan execution).

    ``exploration`` is typed as Any so tests may pass mocks; runtime always supplies
    ``FinalExplorationSchema``.
    """

    exploration: Optional[Any] = None
    insufficiency: Optional[ExplorationInsufficientContext] = None
    replan: Optional[ReplanContext] = None
    session: Optional[Any] = Field(
        default=None,
        description="Optional SessionMemory snapshot for planner prompt + explore streak.",
    )
    # Copy of canonical ``state.context['query_intent']`` (or exploration mirror) at boundary.
    query_intent: Optional[QueryIntent] = None
    # Advisory cap for planner EXPLORE decisions (derived from query_intent); not engine-enforced.
    exploration_budget: Optional[int] = None

    @model_validator(mode="after")
    def _one_primary_mode(self) -> "PlannerPlanContext":
        if self.replan is not None:
            return self
        if self.exploration is None and self.insufficiency is None:
            raise ValueError(
                "PlannerPlanContext requires at least one of: exploration, insufficiency, replan"
            )
        return self
