"""
Trace schemas — TraceStep, Trace.

Every agent action must be traceable. TraceStep.error uses the shared ErrorType enum
(not a plain string) per SCHEMAS.md normative contract.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from .execution import ErrorType


class TraceError(BaseModel):
    type: ErrorType
    message: str


class TraceStep(BaseModel):
    step_id: str
    plan_step_index: int
    action: str
    target: str
    success: bool
    error: Optional[TraceError] = None
    duration_ms: int


class TraceMetadata(BaseModel):
    total_steps: int
    total_duration_ms: int


class Trace(BaseModel):
    trace_id: str
    instruction: str
    plan_id: str
    steps: list[TraceStep]
    status: Literal["success", "failure"]
    metadata: TraceMetadata
