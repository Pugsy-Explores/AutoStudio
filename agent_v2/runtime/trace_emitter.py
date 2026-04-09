"""
Phase 9 — Structured trace emission (Trace / TraceStep) for CLI, Langfuse, and monitoring.

Records one TraceStep per plan step after final retry outcome (not per attempt).
Phase 13 — record_llm() appends LLM steps to the same ordered list (exploration/plan/arg-gen).

TraceEmitter only records facts; it does not decide retries or replans.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from agent_v2.schemas.execution import ErrorType, ExecutionResult
from agent_v2.schemas.execution_task import ExecutionTask
from agent_v2.schemas.plan import PlanStep
from agent_v2.observability.trace_text import truncate_trace_text
from agent_v2.schemas.trace import Trace, TraceError, TraceMetadata, TraceStep


class ExecutionLogEntry(BaseModel):
    """Per-attempt execution log for debugging and replay."""
    task_id: str
    attempt_number: int
    arguments: dict[str, Any] | None = None
    success: bool
    error_type: str | None = None
    error_message: str | None = None
    timestamp: str
    duration_ms: int


def extract_target_from_execution_task(task: ExecutionTask) -> str:
    """Best-effort target from ExecutionTask.input_hints and goal."""
    h = task.input_hints if isinstance(task.input_hints, dict) else {}
    for key in ("path", "query", "file", "instruction", "command"):
        val = h.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:500]
    g = (task.goal or "").strip()
    if g:
        return g[:500]
    return ""


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
    def __init__(self, log_dir: str | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir else None
        self.reset()

    def reset(self) -> None:
        self.trace_id: str = str(uuid.uuid4())
        self._steps: list[TraceStep] = []
        self._start_mono: float = time.perf_counter()
        self._execution_logs: list[ExecutionLogEntry] = []
        self._execution_log_dir: Path | None = None
        if self.log_dir is not None:
            self._execution_log_dir = self.log_dir / f"trace_{self.trace_id}"
            self._execution_log_dir.mkdir(parents=True, exist_ok=True)

    def record_execution_attempt(
        self,
        task: ExecutionTask,
        result: ExecutionResult,
        attempt_number: int,
        duration_ms: int,
    ) -> None:
        """
        Record a single execution attempt with immediate persistence.

        Persists to file immediately to survive crashes.
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        entry = ExecutionLogEntry(
            task_id=task.id,
            attempt_number=attempt_number,
            arguments=dict(task.arguments),  # Frozen arguments from snapshot
            success=result.success,
            error_type=str(result.error.type) if result.error else None,
            error_message=(result.error.message or "") if result.error else None,
            timestamp=timestamp,
            duration_ms=duration_ms,
        )

        # Keep in memory for in-session access
        self._execution_logs.append(entry)

        # Persist to file immediately (survives crashes)
        self._persist_execution_log_entry(entry)

    def _persist_execution_log_entry(self, entry: ExecutionLogEntry) -> None:
        """Persist a single execution log entry to file (immediate write)."""
        if self._execution_log_dir is None:
            return

        # Per-task log file
        log_file = self._execution_log_dir / f"{entry.task_id}.jsonl"

        try:
            # Append as JSONL (newline-delimited JSON) for atomic writes
            with open(log_file, "a") as f:
                f.write(entry.model_dump_json() + "\n")
        except Exception as e:
            # Logging failure shouldn't break execution
            logging.error(f"Failed to persist execution log for {entry.task_id}: {e}")

    def record_step(
        self,
        step: PlanStep,
        result: ExecutionResult,
        plan_step_index: int,
        *,
        execution_attempts: int | None = None,
    ) -> None:
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
        if execution_attempts is not None and execution_attempts >= 1:
            meta["attempts"] = int(execution_attempts)
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

    def record_execution_task(
        self,
        task: ExecutionTask,
        result: ExecutionResult,
        *,
        execution_attempts: int | None = None,
    ) -> None:
        """Append a TraceStep from runtime ExecutionTask (plan_step_index = emission order, not PlanStep.index)."""
        plan_step_index = len(self._steps) + 1
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
        if execution_attempts is not None and execution_attempts >= 1:
            meta["attempts"] = int(execution_attempts)
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
                    meta["snippet_preview"] = content.strip()[:160]
            if mode == "symbol_body":
                meta["read_source"] = "symbol"
            elif mode == "line_window":
                meta["read_source"] = "line"
            elif mode == "file_head":
                meta["read_source"] = "head"

        self._steps.append(
            TraceStep(
                step_id=task.id,
                plan_step_index=plan_step_index,
                action=str(task.tool),
                target=extract_target_from_execution_task(task),
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
