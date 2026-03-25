"""
AgentState — aggregate runtime state container.

All runtime information must live in AgentState. No hidden state, no globals.
Only imports from agent_v2.schemas and stdlib/pydantic — no orchestration, dispatcher,
or tool implementation imports.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .context import ContextWindow
from .execution import ExecutionResult
from .exploration import ExplorationResult
from .plan import PlanDocument
from .policies import ExecutionPolicy, FailurePolicy
from .trace import Trace


class AgentState(BaseModel):
    """
    Single source of truth for all runtime state.

    current_plan: active PlanDocument being executed
    current_plan_steps: optional denormalized steps (JSON) for UI / trace
    plan_index: index of the step currently being executed
    exploration_results: output of the exploration phase
    step_results: ordered list of ExecutionResults produced so far
    history: raw turn history (message dicts) for LLM context building
    trace: live trace being built during execution
    context_window: ranked + pruned context passed to the model
    replan_count: number of replans triggered this session
    execution_policy: policy governing step limits and retry budget
    failure_policy: policy governing failure handling behaviour
    metadata: cross-cutting counters and executor hints (e.g. failure_streak, last_error)
    """
    session_id: str
    instruction: str

    current_plan: Optional[PlanDocument] = None
    current_plan_steps: Optional[list[dict[str, Any]]] = None
    plan_index: int = 0

    exploration_results: Optional[ExplorationResult] = None

    step_results: list[ExecutionResult] = []

    history: list[dict] = []

    metadata: dict[str, Any] = Field(default_factory=dict)

    trace: Optional[Trace] = None

    context_window: Optional[ContextWindow] = None

    replan_count: int = 0

    execution_policy: Optional[ExecutionPolicy] = None
    failure_policy: Optional[FailurePolicy] = None
