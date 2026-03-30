"""Post-synthesis answer validation contract (planner loop feedback)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ValidationConfidence = Literal["low", "medium", "high"]


class AnswerValidationResult(BaseModel):
    """Structured verdict after answer synthesis; drives PlannerPlanContext.validation_feedback."""

    is_complete: bool
    issues: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    confidence: ValidationConfidence = "low"
    # One-line human trace for logs, evals, and prompt iteration (not a second verdict).
    validation_reason: str = Field(default="", max_length=800)
