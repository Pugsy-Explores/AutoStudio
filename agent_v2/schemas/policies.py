"""
Policy schemas — ExecutionPolicy, FailurePolicy.

ExecutionPolicy.max_retries_per_step is the ONLY policy source for per-step retry budget.
When a PlanDocument is loaded, each PlanStep.execution.max_attempts MUST be set from
ExecutionPolicy (same value for all steps unless a future per-step override is added to SCHEMAS).
"""
from __future__ import annotations

from pydantic import BaseModel


class ExecutionPolicy(BaseModel):
    """
    max_steps — max plan steps (validator + replanner constraints).
    max_executor_dispatches — hard cap on tool dispatches per PlanExecutor.run (Phase 10).
    max_runtime_seconds — wall-clock budget for PlanExecutor.run (Phase 10).
    """

    max_steps: int
    max_retries_per_step: int
    max_replans: int
    max_executor_dispatches: int = 20
    max_runtime_seconds: int = 600


class FailurePolicy(BaseModel):
    replan_on_failure: bool
    abort_on_unrecoverable: bool
