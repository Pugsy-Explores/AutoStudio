"""
Plan schemas — Schema 1 (PlanDocument) and Schema 2 (PlanStep).

PlanDocument is the control plane of the entire system.
PlanStep carries both planner-owned fields (type, action, goal, I/O, dependencies)
and runtime-owned blocks (execution, failure) initialized by the planner but mutated
exclusively by the executor during a run.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .execution import ErrorType


class PlanStepLastResult(BaseModel):
    success: Optional[bool] = None
    error: Optional[str] = None
    output_summary: Optional[str] = None


class PlanStepExecution(BaseModel):
    """
    Runtime-owned block. Planner sets initial values (status=pending, attempts=0,
    max_attempts from policy). Only PlanExecutor mutates status/attempts during a run.
    """
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    attempts: int = 0
    max_attempts: int = 2
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    last_result: PlanStepLastResult = PlanStepLastResult()


class PlanStepFailure(BaseModel):
    """
    Shared by planner (sets initial strategy/recoverability) and executor
    (sets failure_type and replan_required after exhaustion).
    """
    is_recoverable: bool = True
    failure_type: Optional[ErrorType] = None
    retry_strategy: Literal["retry_same", "adjust_inputs", "abort"] = "retry_same"
    replan_required: bool = False


class PlanStep(BaseModel):
    step_id: str
    index: int
    type: Literal["explore", "analyze", "modify", "validate", "finish"]
    goal: str
    action: Literal["search", "open_file", "edit", "run_tests", "shell", "finish"]
    inputs: dict = {}
    outputs: dict = {}
    dependencies: list[str] = []
    execution: PlanStepExecution = PlanStepExecution()
    failure: PlanStepFailure = PlanStepFailure()


class PlanSource(BaseModel):
    type: Literal["file", "search", "other"]
    ref: str
    summary: str


class PlanRisk(BaseModel):
    risk: str
    impact: Literal["low", "medium", "high"]
    mitigation: str


class PlanMetadata(BaseModel):
    created_at: str
    version: int = 1


class PlannerControllerOutput(BaseModel):
    """
    Structured decision from the planner LLM (ModeManager interprets; no guessing).
    action is the single source of truth for explore vs continue vs replan.
    """

    action: Literal["continue", "replan", "explore"] = "continue"
    next_step_instruction: str = ""
    exploration_query: str = ""


class PlanDocument(BaseModel):
    """
    Single source of truth for execution. Defines what to do, in what order,
    and the intent behind each step. Executor must follow it; cannot invent new steps.
    """
    plan_id: str
    instruction: str
    understanding: str
    sources: list[PlanSource]
    steps: list[PlanStep]
    risks: list[PlanRisk]
    completion_criteria: list[str]
    metadata: PlanMetadata
    controller: Optional[PlannerControllerOutput] = Field(
        default=None,
        description="Parsed from planner JSON when present; used by controller loop.",
    )
