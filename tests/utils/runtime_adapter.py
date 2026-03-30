"""Compatibility adapter from legacy orchestrator API to agent_v2 runtime."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from agent_v2.runtime.bootstrap import create_runtime
from agent_v2.cli_adapter import format_output


def _errors_from_state(state) -> list[str]:
    return [str(r.get("error")) for r in getattr(state, "step_results", []) if r.get("error")]


def _loop_output_from_state(state) -> dict[str, Any]:
    step_results = getattr(state, "step_results", []) or []
    completed_steps = [
        {"id": r.get("step_id"), "action": r.get("action"), "success": r.get("success", False)}
        for r in step_results
    ]
    return {
        "completed_steps": completed_steps,
        "patches_applied": 0,
        "files_modified": [],
        "errors_encountered": _errors_from_state(state),
        "tool_calls": len(step_results),
        "plan_result": getattr(state, "current_plan", None),
        "parent_goal_met": True,
        "phase_results": [],
        "start_time": None,
        "react_history": getattr(state, "history", []),
        "edit_telemetry": {},
    }


def run_agent(instruction: str, mode: str = "act"):
    runtime = create_runtime()
    out = runtime.run(instruction, mode=mode)
    if isinstance(out, dict) and "state" in out:
        return out["state"]
    return out


def run_and_get_output(instruction: str, mode: str = "act") -> dict[str, Any]:
    runtime = create_runtime()
    result = runtime.run(instruction, mode=mode)
    return format_output(result)


def run_and_get_plan(instruction: str):
    state = run_agent(instruction, mode="plan")
    return getattr(state, "current_plan", None)


def run_controller(instruction: str, project_root: str | None = None) -> dict[str, Any]:
    if project_root:
        os.environ["SERENA_PROJECT_DIR"] = str(Path(project_root).resolve())
    state = run_agent(instruction, mode="act")
    loop_output = _loop_output_from_state(state)
    return {
        "task_id": str(uuid.uuid4()),
        "instruction": instruction,
        "state": state,
        "completed_steps": len(loop_output["completed_steps"]),
        "files_modified": loop_output["files_modified"],
        "errors": loop_output["errors_encountered"],
        "retrieved_symbols": [],
        "termination_reason": state.context.get("termination_reason"),
        "loop_output": loop_output,
    }


def run_hierarchical(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
    mode: str = "act",
):
    del trace_id, similar_tasks, log_event_fn, retry_context, max_runtime_seconds
    if project_root:
        os.environ["SERENA_PROJECT_DIR"] = str(Path(project_root).resolve())
    state = run_agent(instruction, mode=mode)
    return state, _loop_output_from_state(state)


def run_deterministic(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
    mode: str = "act",
):
    return run_hierarchical(
        instruction,
        project_root,
        trace_id=trace_id,
        similar_tasks=similar_tasks,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
        max_runtime_seconds=max_runtime_seconds,
        mode=mode,
    )
