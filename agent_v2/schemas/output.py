"""
Output schemas — ExecutionSummary, FinalOutput.

FinalOutput is the terminal artifact of a complete agent run.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ExecutionSummary(BaseModel):
    total_steps: int
    successful_steps: int
    failed_steps: int
    replans: int


class FinalOutput(BaseModel):
    status: Literal["success", "failure"]
    result: str
    plan_summary: str
    execution_summary: str
    errors: list[str]
