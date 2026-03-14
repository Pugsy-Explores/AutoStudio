"""Step execution result: success, output, error, latency."""

from dataclasses import dataclass
from typing import Any


@dataclass
class StepResult:
    step_id: int
    action: str
    success: bool
    output: str | dict
    latency_seconds: float
    error: str | None = None
