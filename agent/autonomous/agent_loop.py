"""
Autonomous agent loop (Mode 2): goal -> observe -> select action -> execute -> repeat.

Reuses: dispatcher, retrieval pipeline, editing pipeline, trace_logger, policy_engine.
Enforces: max_steps, max_tool_calls, max_runtime, max_edits.
"""

import logging
import os
import uuid
from pathlib import Path

from agent.autonomous.action_selector import select_next_action
from agent.autonomous.goal_manager import GoalManager
from agent.autonomous.state_observer import observe
from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.observability.trace_logger import finish_trace, log_event, start_trace

logger = logging.getLogger(__name__)


def run_autonomous(
    goal: str,
    project_root: str | None = None,
    *,
    max_steps: int = 20,
    max_tool_calls: int = 50,
    max_runtime_seconds: float = 60,
    max_edits: int = 10,
) -> dict:
    """
    Run autonomous loop: observe -> select -> dispatch -> evaluate.
    Returns summary dict with goal, completed_steps, tool_calls, stop_reason, etc.
    """
    root = Path(project_root or os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, str(root), query=goal)

    goal_manager = GoalManager(
        goal,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        max_runtime_seconds=max_runtime_seconds,
        max_edits=max_edits,
    )

    state = AgentState(
        instruction=goal,
        current_plan={"steps": []},
        context={
            "project_root": str(root),
            "trace_id": trace_id,
            "instruction": goal,
            "tool_node": "START",
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
        },
    )

    log_event(trace_id, "autonomous_start", {"goal": goal, "limits": goal_manager.get_limits_dict()})

    try:
        while True:
            should_stop, reason = goal_manager.should_stop()
            if should_stop:
                log_event(trace_id, "autonomous_stop", {"reason": reason, "counts": goal_manager.get_counts_dict()})
                break

            observation = observe(
                goal=goal,
                project_root=str(root),
                completed_steps=state.completed_steps,
                step_results=state.step_results,
                context=state.context,
            )

            step = select_next_action(observation)
            if step is None:
                log_event(trace_id, "action_selector_failed", {"observation_goal": goal})
                break

            step_id = len(state.completed_steps) + 1
            step["id"] = step.get("id") or step_id
            state.context["current_step_id"] = step_id

            goal_manager.record_tool_call()
            result_raw = dispatch(step, state)

            success = result_raw.get("success", False)
            goal_manager.record_step(step.get("action", ""), success)

            result = _raw_to_step_result(step, result_raw)
            state.record(step, result)

            log_event(
                trace_id,
                "autonomous_step",
                {
                    "step_id": step_id,
                    "action": step.get("action"),
                    "success": success,
                    "error": result_raw.get("error"),
                },
            )

            # Optional: evaluate goal achieved (e.g. run tests for "Fix failing test")
            # For now we rely on limits; goal_achieved can be set by external evaluator
            # goal_manager.set_goal_achieved(...)

        return {
            "task_id": task_id,
            "goal": goal,
            "completed_steps": len(state.completed_steps),
            "tool_calls": goal_manager.get_counts_dict()["tool_calls"],
            "stop_reason": goal_manager.get_stop_reason() or "action_selector_failed",
            "counts": goal_manager.get_counts_dict(),
        }
    finally:
        finish_trace(trace_id)


def _raw_to_step_result(step: dict, raw: dict) -> StepResult:
    """Convert dispatch result to StepResult."""
    output = raw.get("output", "")
    files_modified = None
    patch_size = None
    if step.get("action") == "EDIT" and isinstance(output, dict):
        files_modified = output.get("files_modified")
        patch_size = output.get("patches_applied")

    return StepResult(
        step_id=step.get("id", 0),
        action=step.get("action", "EXPLAIN"),
        success=raw.get("success", False),
        output=output,
        latency_seconds=0,
        error=raw.get("error"),
        classification=raw.get("classification"),
        files_modified=files_modified,
        patch_size=patch_size,
    )
