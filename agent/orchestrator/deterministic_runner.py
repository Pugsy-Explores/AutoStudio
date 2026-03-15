"""Deterministic execution loop: plan -> dispatch -> validate -> record. Single source of truth for Mode 1."""

import logging
import time

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.observability.trace_logger import log_event
from agent.orchestrator.plan_resolver import get_plan
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step
from config.agent_config import MAX_REPLAN_ATTEMPTS, MAX_TASK_RUNTIME_SECONDS

logger = logging.getLogger(__name__)


def run_deterministic(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
) -> tuple[AgentState, dict]:
    """
    Run deterministic loop: get_plan -> while not finished: next_step -> dispatch -> validate -> record.
    Returns (state, loop_output) where loop_output has completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result.
    """
    log_fn = log_event_fn or log_event
    plan_result = get_plan(instruction, trace_id=trace_id, log_event_fn=log_fn)
    if trace_id:
        log_fn(trace_id, "planner_decision", {"plan": plan_result})

    state = AgentState(
        instruction=instruction,
        current_plan=plan_result,
        context={
            "tool_node": "START",
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
            "project_root": project_root,
            "instruction": instruction,
            "trace_id": trace_id,
            "similar_past_tasks": similar_tasks or [],
        },
    )

    start_time = time.perf_counter()
    completed_steps: list = []
    patches_applied: list = []
    files_modified: list = []
    errors_encountered: list = []
    replan_count = 0
    tool_calls = 0

    while not state.is_finished():
        if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
            logger.warning("[deterministic_runner] max task runtime exceeded")
            errors_encountered.append("max_task_runtime_exceeded")
            if trace_id:
                log_fn(trace_id, "error", {"type": "max_task_runtime_exceeded"})
            break

        step = state.next_step()
        if step is None:
            break

        step_id = step.get("id", "?")
        action = (step.get("action") or "EXPLAIN").upper()
        state.context["current_step_id"] = step_id
        logger.info("[deterministic_runner] step executed step_id=%s action=%s", step_id, action)

        tool_calls += 1
        result = dispatch(step, state)

        chosen_tool = state.context.get("chosen_tool", "")
        if trace_id:
            log_fn(
                trace_id,
                "step_executed",
                {
                    "step_id": step_id,
                    "action": action,
                    "tool": chosen_tool,
                    "success": result.get("success", False),
                },
            )

        success = result.get("success", False)
        if success:
            completed_steps.append(step)
            out = result.get("output", {})
            if isinstance(out, dict):
                pm = out.get("patches_applied")
                if isinstance(pm, list):
                    patches_applied.extend(pm)
                elif isinstance(pm, int):
                    patches_applied.append(pm)
                files_modified.extend(out.get("files_modified", []) or [])
                if (pm or out.get("files_modified")) and trace_id:
                    log_fn(
                        trace_id,
                        "patch_result",
                        {
                            "step_id": step_id,
                            "patches_applied": pm if isinstance(pm, int) else len(pm) if isinstance(pm, list) else 0,
                            "files_modified": out.get("files_modified", []),
                        },
                    )
        else:
            err = result.get("error", "unknown")
            errors_encountered.append(err)
            if trace_id:
                log_fn(trace_id, "error", {"step_id": step_id, "action": action, "error": str(err)})
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[deterministic_runner] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            new_plan = replan(state, failed_step=step, error=result.get("error", ""))
            state.update_plan(new_plan)
            continue

        step_result = _result_to_step_result(step, result)
        valid, validation_feedback = validate_step(step, step_result, state=state)
        if not valid:
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[deterministic_runner] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            err = getattr(step_result, "error", None) or result.get("error", "")
            out_str = str(step_result.output or "")[:400] if step_result.output else ""
            error_msg = validation_feedback or str(err) or out_str or "Validation failed"
            new_plan = replan(state, failed_step=step, error=error_msg)
            state.update_plan(new_plan)
            continue

        state.record(step, step_result)
        replan_count = 0

    loop_output = {
        "completed_steps": completed_steps,
        "patches_applied": patches_applied,
        "files_modified": files_modified,
        "errors_encountered": errors_encountered,
        "tool_calls": tool_calls,
        "plan_result": plan_result,
        "start_time": start_time,
    }
    return state, loop_output


def _result_to_step_result(step: dict, result: dict) -> StepResult:
    """Convert dispatch result to StepResult for state.record."""
    output = result.get("output", "")
    files_modified = None
    patch_size = None
    if step.get("action") == "EDIT" and isinstance(output, dict):
        files_modified = output.get("files_modified")
        patch_size = output.get("patches_applied")

    return StepResult(
        step_id=step.get("id", 0),
        action=step.get("action", "EXPLAIN"),
        success=result.get("success", False),
        output=output,
        latency_seconds=0,
        error=result.get("error"),
        files_modified=files_modified,
        patch_size=patch_size,
    )
