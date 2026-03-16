"""Deterministic execution loop: plan -> StepExecutor.execute_step -> validate -> record. Single source of truth for Mode 1."""

import logging
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError, ThreadPoolExecutor

from agent.execution.executor import StepExecutor
from agent.execution.policy_engine import ResultClassification
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.observability.trace_logger import log_event
from agent.orchestrator.goal_evaluator import GoalEvaluator
from agent.orchestrator.plan_resolver import get_plan
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step
from config.agent_config import (
    MAX_LOOP_ITERATIONS,
    MAX_REPLAN_ATTEMPTS,
    MAX_STEP_TIMEOUT_SECONDS,
    MAX_STEPS,
    MAX_TASK_RUNTIME_SECONDS,
    MAX_TOOL_CALLS,
)

logger = logging.getLogger(__name__)


def run_deterministic(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> tuple[AgentState, dict]:
    """
    Run deterministic loop: get_plan -> while not finished: next_step -> executor.execute_step -> validate -> record.
    Returns (state, loop_output) where loop_output has completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result.

    Phase 5: retry_context (previous_attempts, critic_feedback) is passed to get_plan when provided.
    """
    log_fn = log_event_fn or log_event
    plan_result = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
    )
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
    errors_encountered: list = []
    replan_count = 0
    tool_calls = 0
    iteration_count = 0
    executor = StepExecutor()
    goal_evaluator = GoalEvaluator()

    while not state.is_finished():
        iteration_count += 1
        if iteration_count >= MAX_LOOP_ITERATIONS:
            logger.warning("[deterministic_runner] max loop iterations exceeded")
            errors_encountered.append("max_loop_iterations")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_loop_iterations"})
            break
        if len(state.completed_steps) >= MAX_STEPS:
            logger.warning("[deterministic_runner] max steps reached")
            errors_encountered.append("max_steps")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_steps"})
            break
        if tool_calls >= MAX_TOOL_CALLS:
            logger.warning("[deterministic_runner] max tool calls reached")
            errors_encountered.append("max_tool_calls")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_tool_calls"})
            break
        if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
            logger.warning("[deterministic_runner] max task runtime exceeded")
            errors_encountered.append("max_task_runtime_exceeded")
            if trace_id:
                log_fn(trace_id, "error", {"type": "max_task_runtime_exceeded"})
            break

        step = state.next_step()
        if step is None:
            # Plan exhausted: evaluate goal before exiting (Phase 4 closed-loop).
            goal_met = goal_evaluator.evaluate(instruction, state)
            if trace_id:
                log_fn(
                    trace_id,
                    "goal_evaluation",
                    {
                        "goal_met": goal_met,
                        "completed_steps": len(state.completed_steps),
                        "instruction_preview": (instruction or "")[:200],
                    },
                )
            if goal_met:
                if trace_id:
                    log_fn(trace_id, "goal_completed", {"completed_steps": len(state.completed_steps)})
                break
            # Goal not satisfied: record and replan if under budget.
            errors_encountered.append("goal_not_satisfied")
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[deterministic_runner] goal unresolved after max replan attempts")
                if trace_id:
                    log_fn(
                        trace_id,
                        "goal_unresolved",
                        {"replan_count": replan_count, "completed_steps": len(state.completed_steps)},
                    )
                break
            if trace_id:
                log_fn(trace_id, "goal_not_satisfied", {"replan_count": replan_count})
            new_plan = replan(state, failed_step=None, error="goal_not_satisfied")
            state.update_plan(new_plan)
            continue

        step_id = step.get("id", "?")
        action = (step.get("action") or "EXPLAIN").upper()
        state.context["current_step_id"] = step_id
        logger.info("[deterministic_runner] step executed step_id=%s action=%s", step_id, action)

        tool_calls += 1
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(executor.execute_step, step, state)
                step_result = future.result(timeout=MAX_STEP_TIMEOUT_SECONDS)
        except FuturesTimeoutError:
            logger.warning("[deterministic_runner] step timeout step_id=%s action=%s", step_id, action)
            if trace_id:
                log_fn(trace_id, "step_timeout", {"step_id": step_id, "action": action})
            step_result = StepResult(
                step_id=step.get("id", 0),
                action=action,
                success=False,
                output="",
                latency_seconds=0.0,
                error="step_timeout",
                classification=ResultClassification.RETRYABLE_FAILURE.value,
            )

        classification = step_result.classification
        if isinstance(classification, ResultClassification):
            classification = classification.value

        chosen_tool = state.context.get("chosen_tool", "")
        if trace_id:
            log_fn(
                trace_id,
                "step_executed",
                {
                    "step_id": step_id,
                    "action": action,
                    "tool": chosen_tool,
                    "success": step_result.success,
                },
            )

        if classification == ResultClassification.FATAL_FAILURE.value:
            err = step_result.error or "fatal failure"
            errors_encountered.append(err)
            if trace_id:
                log_fn(trace_id, "fatal_failure", {"step_id": step_id, "action": action, "error": str(err)})
            logger.warning(
                "[deterministic_runner] fatal failure encountered, stopping loop (step_id=%s action=%s)",
                step_id,
                action,
            )
            break

        if step_result.success:
            out = step_result.output
            if isinstance(out, dict):
                pm = step_result.patch_size or out.get("patches_applied")
                files_mod = step_result.files_modified or out.get("files_modified", []) or []
                if (pm or files_mod) and trace_id:
                    log_fn(
                        trace_id,
                        "patch_result",
                        {
                            "step_id": step_id,
                            "patches_applied": pm
                            if isinstance(pm, int)
                            else len(pm)
                            if isinstance(pm, list)
                            else 0,
                            "files_modified": files_mod,
                        },
                    )
        else:
            err = step_result.error or "unknown"
            errors_encountered.append(err)
            if trace_id:
                log_fn(
                    trace_id,
                    "error",
                    {
                        "step_id": step_id,
                        "action": action,
                        "error": str(err),
                        "classification": classification,
                    },
                )
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[deterministic_runner] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            new_plan = replan(state, failed_step=step, error=step_result.error or "")
            state.update_plan(new_plan)
            continue

        valid, validation_feedback = validate_step(step, step_result, state=state)
        if not valid:
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[deterministic_runner] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            err = step_result.error
            out_str = str(step_result.output or "")[:400] if step_result.output else ""
            error_msg = validation_feedback or str(err) or out_str or "Validation failed"
            new_plan = replan(state, failed_step=step, error=error_msg)
            state.update_plan(new_plan)
            continue

        state.record(step, step_result)
        replan_count = 0

    # Derive aggregates from AgentState to ensure single source of truth
    completed_steps = list(state.completed_steps)
    patch_count = 0
    files_modified: list = []
    for sr in state.step_results:
        pm = getattr(sr, "patch_size", None)
        if isinstance(pm, int):
            patch_count += pm
        elif isinstance(pm, list):
            patch_count += len(pm)
        fm = getattr(sr, "files_modified", None) or []
        if isinstance(fm, list):
            files_modified.extend(fm)

    loop_output = {
        "completed_steps": completed_steps,
        "patches_applied": patch_count,
        "files_modified": files_modified,
        "errors_encountered": errors_encountered,
        "tool_calls": tool_calls,
        "plan_result": plan_result,
        "start_time": start_time,
    }
    return state, loop_output
