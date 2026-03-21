"""
Stage 32 — Success computation.

Responsibilities:
- compute_success (grading_mode -> structural/validation/explain)
- _task_success (loop_output -> structural success)
- _failure_class_from
- _replan_observed
"""

from __future__ import annotations

from typing import Any

from tests.agent_eval.task_specs import TaskSpec


def task_success(loop_output: dict, path_mode: str, exc: BaseException | None) -> bool:
    """Determine structural success from loop output."""
    if exc is not None:
        return False
    if path_mode == "hierarchical":
        return bool(loop_output.get("parent_goal_met"))
    errs = loop_output.get("errors_encountered") or []
    return isinstance(errs, list) and len(errs) == 0


def failure_class_from(exc: BaseException | None, success: bool, loop_output: dict) -> str | None:
    """Infer failure class from exception and success."""
    if exc is not None:
        return "exception"
    if success:
        return None
    return "goal_or_parent_not_met"


def replan_observed(loop_output: dict) -> bool:
    """True if phase_results contain attempt_history with >1 attempt."""
    prs = loop_output.get("phase_results") or []
    if not isinstance(prs, list):
        return False
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        ah = pr.get("attempt_history") or []
        if isinstance(ah, list) and len(ah) > 1:
            return True
    return False


def compute_success(
    spec: TaskSpec,
    *,
    structural_success: bool,
    validation_passed: bool,
    explain_ok: bool | None,
) -> bool:
    """Compute final success from grading_mode and component flags."""
    if spec.grading_mode == "structural_loop":
        return structural_success
    if spec.grading_mode == "explain_artifact":
        return bool(explain_ok)
    return validation_passed


def count_replans(loop_snapshot: dict[str, Any]) -> int:
    """Count replans from phase_results attempt_history."""
    n = 0
    for pr in loop_snapshot.get("phase_results") or []:
        if not isinstance(pr, dict):
            continue
        ah = pr.get("attempt_history") or []
        if isinstance(ah, list) and len(ah) > 1:
            n += len(ah) - 1
    return n
