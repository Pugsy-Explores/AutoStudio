"""
Execution schemas — Schema 0 (ErrorType), Schema 3 (ExecutionResult), plus ExecutionStep and RetryState.

ErrorType is the single classification enum for failures across all layers (execution, plan, trace, replan).
Do not fork or redefine it per-layer.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ErrorType(str, Enum):
    tool_error = "tool_error"
    validation_error = "validation_error"
    not_found = "not_found"
    timeout = "timeout"
    tests_failed = "tests_failed"
    permission_error = "permission_error"
    unknown = "unknown"


class ExecutionStep(BaseModel):
    step_id: str
    action: Literal["search", "open_file", "edit", "run_tests", "shell", "finish"]
    arguments: dict
    reasoning: str


class ExecutionOutput(BaseModel):
    data: dict = {}
    summary: str
    # Full tool stream / body for debugging (shell stdout, test stdout+stderr, etc.). Kept out of summary.
    full_output: Optional[str] = Field(default=None, description="Unabridged tool text output when available")


class ExecutionError(BaseModel):
    type: ErrorType
    message: str
    details: dict = {}


class ExecutionMetadata(BaseModel):
    tool_name: str
    duration_ms: int
    timestamp: str


class ExecutionResult(BaseModel):
    """
    Bridge between tools and the plan system.
    error MUST be null when success=True.
    output.summary MUST always be present (short, LLM-facing).
    output.full_output MAY hold long tool streams (shell/tests/file body/search JSON) for debugging.
    """
    step_id: str
    success: bool
    status: Literal["success", "failure"]
    output: ExecutionOutput
    error: Optional[ExecutionError] = None
    metadata: ExecutionMetadata


class RetryState(BaseModel):
    """
    Optional convenience projection of per-step retry state for logging/UI.
    NOT a second source of truth — must mirror PlanStep.execution for that step.
    """
    step_id: str
    attempts: int
    max_attempts: int
    last_error_type: Optional[str] = None
    strategy: Literal["retry_same", "adjust_inputs", "abort"]
