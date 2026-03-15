"""Step execution result: success, output, error, latency, classification."""

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
    classification: str | None = None  # SUCCESS | RETRYABLE_FAILURE | FATAL_FAILURE
    files_modified: list[str] | None = None  # For EDIT steps: paths touched
    patch_size: int | None = None  # For EDIT steps: lines changed


