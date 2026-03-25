"""
Trace schemas — TraceStep, Trace.

Every agent action must be traceable. TraceStep.error uses the shared ErrorType enum
(not a plain string) per SCHEMAS.md normative contract.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .execution import ErrorType


class TraceError(BaseModel):
    type: ErrorType
    message: str


class TraceStep(BaseModel):
    """
    kind=\"tool\" — plan step execution (Phase 9).
    kind=\"llm\" — reasoning/small model call interleaved in the same timeline (Phase 13).
    kind=\"diff\" — patch artifact after successful edit (Phase 14; observability only).
    kind=\"memory\" — distilled memory record emitted to trace (Phase 16; observability only).
    """

    step_id: str
    plan_step_index: int
    action: str
    target: str
    success: bool
    error: Optional[TraceError] = None
    duration_ms: int
    kind: Literal["tool", "llm", "diff", "memory"] = "tool"
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


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
