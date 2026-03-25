"""
Phase 9 — Structured trace emission (Trace / TraceStep) for CLI, Langfuse, and monitoring.

Records one TraceStep per plan step after final retry outcome (not per attempt).
TraceEmitter only records facts; it does not decide retries or replans.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Literal

from agent_v2.schemas.execution import ErrorType, ExecutionResult
from agent_v2.schemas.plan import PlanStep
from agent_v2.schemas.trace import Trace, TraceError, TraceMetadata, TraceStep


def extract_target_from_plan_step(step: PlanStep) -> str:
    """Best-effort target from PlanStep.inputs and goal (strict PlanStep; no duck typing)."""
    inp = step.inputs if isinstance(step.inputs, dict) else {}
    for key in ("path", "query", "file", "instruction", "command"):
        val = inp.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:500]
    g = (step.goal or "").strip()
    if g:
        return g[:500]
    return ""


class TraceEmitter:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.trace_id: str = str(uuid.uuid4())
        self._steps: list[TraceStep] = []
        self._start_mono: float = time.perf_counter()

    def record_step(self, step: PlanStep, result: ExecutionResult, plan_step_index: int) -> None:
        """
        Append a TraceStep after the plan step's final outcome (post-retry).

        plan_step_index: same numbering as PlanStep.index (typically 1..N).
        """
        err: TraceError | None = None
        if not result.success:
            if result.error is not None:
                err = TraceError(
                    type=result.error.type,
                    message=(result.error.message or "").strip() or result.error.type.value,
                )
            else:
                err = TraceError(type=ErrorType.unknown, message="unknown failure")

        dur = 0
        if result.metadata is not None:
            dur = int(result.metadata.duration_ms or 0)

        self._steps.append(
            TraceStep(
                step_id=step.step_id,
                plan_step_index=plan_step_index,
                action=str(step.action),
                target=extract_target_from_plan_step(step),
                success=bool(result.success),
                error=err,
                duration_ms=dur,
            )
        )

    def build_trace(self, instruction: str, plan_id: str) -> Trace:
        total_ms = sum(s.duration_ms for s in self._steps)
        wall_ms = int((time.perf_counter() - self._start_mono) * 1000)
        if total_ms == 0 and wall_ms > 0:
            total_ms = wall_ms

        if not self._steps:
            status: Literal["success", "failure"] = "failure"
        else:
            status = "success" if all(s.success for s in self._steps) else "failure"

        return Trace(
            trace_id=self.trace_id,
            instruction=instruction,
            plan_id=plan_id,
            steps=list(self._steps),
            status=status,
            metadata=TraceMetadata(total_steps=len(self._steps), total_duration_ms=total_ms),
        )


TraceEmitterFactory = Callable[[], TraceEmitter]
