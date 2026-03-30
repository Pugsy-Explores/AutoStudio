"""
Replan schemas — Schema 4b (ReplanContext), 4c (PlannerInput), Schema 5 (ReplanRequest),
Schema 6 (ReplanResult).

ReplanContext is the minimal planner-consumable input for replanning after failure.
PlannerInput is the union type for the planner's context input.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, field_validator

try:
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias

from .execution import ErrorType
from .exploration import QueryIntent
from .final_exploration import FinalExplorationSchema


class ReplanFailureError(BaseModel):
    type: ErrorType
    message: str


class ReplanFailureContext(BaseModel):
    step_id: str
    error: ReplanFailureError
    attempts: int
    last_output_summary: str


class ReplanCompletedStep(BaseModel):
    step_id: str
    summary: str


class ReplanExplorationSummary(BaseModel):
    key_findings: list[str]
    knowledge_gaps: list[str]
    overall: str


class ReplanContext(BaseModel):
    """
    Schema 4b — minimal planner-consumable input for replanning.
    failure_context MUST be complete (same semantics as ReplanRequest.failure_context).
    completed_steps MAY be empty when failure occurs before any step completed.

    trigger: failure = tool/step failure path; insufficiency = evidence/gaps without tool error.
    """
    failure_context: ReplanFailureContext
    completed_steps: list[ReplanCompletedStep]
    exploration_summary: Optional[ReplanExplorationSummary] = None
    trigger: Literal["failure", "insufficiency"] = "failure"
    query_intent: Optional[QueryIntent] = None
    # Optional control-plane handoff (TaskPlanner / runtime metadata); does not replace failure_context.
    task_control_last_outcome: Optional[str] = None
    explore_block_details: Optional[dict[str, Any]] = None

    @field_validator("task_control_last_outcome", mode="before")
    @classmethod
    def _cap_task_control(cls, v: object) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s[:2048] if s else None

    @field_validator("explore_block_details", mode="before")
    @classmethod
    def _cap_ebd(cls, v: object) -> dict[str, Any] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            return None
        out: dict[str, Any] = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= 16:
                break
            ks = str(k)[:64]
            if isinstance(val, (int, float, bool)):
                out[ks] = val
            else:
                out[ks] = str(val)[:512]
        return out or None


PlannerInput: TypeAlias = Union[FinalExplorationSchema, ReplanContext]


class ReplanOriginalPlan(BaseModel):
    plan_id: str
    failed_step_id: str
    current_step_index: int


class ReplanPartialResult(BaseModel):
    step_id: str
    result_summary: str


class ReplanExecutionContext(BaseModel):
    completed_steps: list[ReplanCompletedStep]
    partial_results: list[ReplanPartialResult]


class ReplanExplorationContext(BaseModel):
    key_findings: list[str]
    knowledge_gaps: list[str]


class ReplanConstraints(BaseModel):
    max_steps: int
    preserve_completed: bool


class ReplanMetadata(BaseModel):
    timestamp: str
    replan_attempt: int


class ReplanRequest(BaseModel):
    """
    Schema 5 — control handoff between execution → planning.
    MUST only be created after step failure.
    failure_context MUST be present and complete.
    replan_attempt MUST increment per cycle.
    """
    replan_id: str
    instruction: str
    original_plan: ReplanOriginalPlan
    failure_context: ReplanFailureContext
    execution_context: ReplanExecutionContext
    exploration_context: ReplanExplorationContext
    constraints: ReplanConstraints
    metadata: ReplanMetadata
    query_intent: Optional[QueryIntent] = None


class ReplanNewPlan(BaseModel):
    plan_id: str


class ReplanChanges(BaseModel):
    type: Literal["partial_update", "full_replacement"]
    summary: str
    modified_steps: list[str]
    added_steps: list[str]
    removed_steps: list[str]


class ReplanReasoning(BaseModel):
    failure_analysis: str
    strategy: str


class ReplanValidation(BaseModel):
    is_valid: bool
    issues: list[str]


class ReplanResult(BaseModel):
    """
    Schema 6 — output of replanning.
    new_plan is None when status='failed'; required (non-None) when status='success'.
    Full PlanDocument is returned separately (not embedded here) to avoid duplication.
    """
    replan_id: str
    status: Literal["success", "failed"]
    new_plan: Optional[ReplanNewPlan] = None
    changes: ReplanChanges
    reasoning: ReplanReasoning
    validation: ReplanValidation
    metadata: ReplanMetadata
