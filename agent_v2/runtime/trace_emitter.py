"""
Phase 9 — Structured trace emission (Trace / TraceStep) for CLI, Langfuse, and monitoring.

Records one TraceStep per plan step after final retry outcome (not per attempt).
Phase 13 — record_llm() appends LLM steps to the same ordered list (exploration/plan/arg-gen).

TraceEmitter only records facts; it does not decide retries or replans.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Literal

from agent_v2.schemas.execution import ErrorType, ExecutionResult
from agent_v2.schemas.plan import PlanStep
from agent_v2.observability.trace_text import truncate_trace_text
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

        meta: dict = {}
        tool_name = ""
        if result.metadata is not None:
            tool_name = str(result.metadata.tool_name or "")
        if tool_name:
            meta["tool_name"] = tool_name
        # Phase 12.6.R5: visibility into bounded-read provenance (facts only).
        if tool_name == "read_snippet":
            data = result.output.data if result.output else {}
            mode = ""
            if isinstance(data, dict):
                mode = str(data.get("mode") or "")
                file_path = str(data.get("file_path") or "").strip()
                if file_path:
                    meta["file"] = file_path
                symbol = str(data.get("symbol") or "").strip()
                if symbol:
                    meta["symbol"] = symbol
                content = data.get("content")
                if isinstance(content, str) and content.strip():
                    # Minimal bounded preview for debug visibility.
                    meta["snippet_preview"] = content.strip()[:160]
            if mode == "symbol_body":
                meta["read_source"] = "symbol"
            elif mode == "line_window":
                meta["read_source"] = "line"
            elif mode == "file_head":
                meta["read_source"] = "head"

        self._steps.append(
            TraceStep(
                step_id=step.step_id,
                plan_step_index=plan_step_index,
                action=str(step.action),
                target=extract_target_from_plan_step(step),
                success=bool(result.success),
                error=err,
                duration_ms=dur,
                kind="tool",
                metadata=meta,
            )
        )

    def record_llm(
        self,
        *,
        task_name: str,
        prompt: str,
        output_text: str,
        latency_ms: int,
        system_prompt: str | None = None,
        model: str | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
    ) -> None:
        """Append an LLM call to the same ordered list as tool steps (Phase 13)."""
        sid = f"llm-{uuid.uuid4().hex[:12]}"
        sys_t = truncate_trace_text(system_prompt) if system_prompt else ""
        prompt_t = truncate_trace_text(prompt)
        out_t = truncate_trace_text(output_text)
        meta: dict = {
            "task_name": task_name,
            "latency_ms": latency_ms,
        }
        if model:
            meta["model"] = model
        if tokens_input is not None:
            meta["tokens_input"] = tokens_input
        if tokens_output is not None:
            meta["tokens_output"] = tokens_output
        self._steps.append(
            TraceStep(
                step_id=sid,
                plan_step_index=0,
                action=task_name,
                target=(model or "")[:200],
                success=True,
                error=None,
                duration_ms=max(0, int(latency_ms)),
                kind="llm",
                input={"prompt": prompt_t, "system_prompt": sys_t},
                output={"text": out_t},
                metadata=meta,
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
            tool_steps = [s for s in self._steps if s.kind == "tool"]
            if tool_steps:
                status = "success" if all(s.success for s in tool_steps) else "failure"
            else:
                status = "success"

        return Trace(
            trace_id=self.trace_id,
            instruction=instruction,
            plan_id=plan_id,
            steps=list(self._steps),
            status=status,
            metadata=TraceMetadata(total_steps=len(self._steps), total_duration_ms=total_ms),
        )


TraceEmitterFactory = Callable[[], TraceEmitter]
