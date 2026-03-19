"""
Shared execution loop used by run_agent() and run_deterministic().

Phase 3: Single implementation for iteration limits, tool execution, validation,
replan, and state.record. Behavior is controlled by mode (ExecutionLoopMode) so
only valid combinations exist and future extensions add new enum values.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from config.agent_config import (
    MAX_LOOP_ITERATIONS,
    MAX_REPLAN_ATTEMPTS,
    MAX_STEP_TIMEOUT_SECONDS,
    MAX_STEPS,
    MAX_TASK_RUNTIME_SECONDS,
    MAX_TOOL_CALLS,
)
from agent.execution.executor import StepExecutor
from agent.execution.policy_engine import ResultClassification
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.orchestrator.goal_evaluator import GoalEvaluator
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step

logger = logging.getLogger(__name__)

# Step retries before replan (run_agent only; deterministic does not retry same step).
MAX_STEP_RETRIES = 2


class ExecutionLoopMode(str, Enum):
    """
    Execution loop mode. Avoids separate booleans so no invalid combination
    (e.g. goal_evaluator + step_retries both True) can be passed.
    """

    DETERMINISTIC = "deterministic"  # Goal evaluator on plan exhaustion; no step retries; returns loop_output.
    AGENT = "agent"  # No goal evaluator; step retries before replan; loop_output is None.


@dataclass
class LoopResult:
    """Result of execution_loop. state always set; loop_output is None when not in deterministic mode."""

    state: AgentState
    loop_output: dict | None


def _output_summary(output) -> str:
    """One-line summary of step output for logging."""
    if isinstance(output, dict):
        keys = list(output.keys())[:5]
        return "output_keys=" + ",".join(str(k) for k in keys)
    s = str(output)
    return "output=" + (s[:80] + "..." if len(s) > 80 else s)


def execution_loop(
    state: AgentState,
    instruction: str,
    *,
    trace_id=None,
    log_event_fn=None,
    retry_context=None,
    mode: ExecutionLoopMode = ExecutionLoopMode.AGENT,
) -> LoopResult:
    """
    Shared step-execution loop. Used by run_agent and run_deterministic.

    - mode=DETERMINISTIC: goal evaluator when plan exhausted; no step retries;
      loop_output populated.
    - mode=AGENT: no goal evaluator; step retries before replan; loop_output None.

    Returns LoopResult(state, loop_output). loop_output is None when mode is AGENT;
    otherwise dict with completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result, start_time.
    """
    _ = retry_context  # Reserved for future use; get_plan(retry_context) is caller's responsibility.
    enable_goal_evaluator = mode == ExecutionLoopMode.DETERMINISTIC
    enable_step_retries = mode == ExecutionLoopMode.AGENT

    log_fn = log_event_fn or (lambda *args, **kwargs: None)
    start_time = time.perf_counter()
    replan_count = 0
    step_retry_count = 0
    iteration = 0
    tool_call_count = 0
    errors_encountered: list = [] if enable_goal_evaluator else None  # Only collect in deterministic mode.
    executor = StepExecutor()
    goal_evaluator = GoalEvaluator() if enable_goal_evaluator else None

    state.context.setdefault("execution_limits", {})
    state.context["execution_limits"].update({
        "max_steps": MAX_STEPS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_runtime_seconds": MAX_TASK_RUNTIME_SECONDS,
        "max_step_timeout_seconds": MAX_STEP_TIMEOUT_SECONDS,
        "max_replan_attempts": MAX_REPLAN_ATTEMPTS,
        "max_step_retries": MAX_STEP_RETRIES,
    })
    if trace_id:
        log_fn(trace_id, "execution_limits", state.context["execution_limits"])

    while not state.is_finished():
        iteration += 1
        if iteration > MAX_LOOP_ITERATIONS:
            logger.warning("[execution_loop] max iterations exceeded, stopping")
            if errors_encountered is not None:
                errors_encountered.append("max_loop_iterations")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_loop_iterations"})
            break
        if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
            logger.warning("[execution_loop] max task runtime exceeded, stopping")
            if errors_encountered is not None:
                errors_encountered.append("max_task_runtime_exceeded")
            if trace_id:
                log_fn(trace_id, "error", {"type": "max_task_runtime_exceeded"})
            break
        if len(state.completed_steps) >= MAX_STEPS:
            logger.warning("[execution_loop] max steps (%s) exceeded, stopping", MAX_STEPS)
            if errors_encountered is not None:
                errors_encountered.append("max_steps")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_steps"})
            break
        if tool_call_count >= MAX_TOOL_CALLS:
            logger.warning("[execution_loop] max tool calls (%s) exceeded, stopping", MAX_TOOL_CALLS)
            if errors_encountered is not None:
                errors_encountered.append("max_tool_calls")
            if trace_id:
                log_fn(trace_id, "limit_reached", {"type": "max_tool_calls"})
            break

        step = state.next_step()
        if step is None:
            if enable_goal_evaluator and goal_evaluator is not None:
                goal_met = goal_evaluator.evaluate(instruction, state)
                current_plan_id = state.current_plan_id
                if trace_id:
                    log_fn(
                        trace_id,
                        "goal_evaluation",
                        {
                            "plan_id": current_plan_id,
                            "goal_met": goal_met,
                            "completed_steps": len(state.completed_steps),
                            "instruction_preview": (instruction or "")[:200],
                        },
                    )
                if goal_met:
                    if trace_id:
                        log_fn(
                            trace_id,
                            "goal_completed",
                            {"plan_id": current_plan_id, "completed_steps": len(state.completed_steps)},
                        )
                    break
                errors_encountered.append("goal_not_satisfied")
                replan_count += 1
                if replan_count >= MAX_REPLAN_ATTEMPTS:
                    logger.warning("[execution_loop] goal unresolved after max replan attempts")
                    if trace_id:
                        log_fn(
                            trace_id,
                            "goal_unresolved",
                            {
                                "plan_id": current_plan_id,
                                "replan_count": replan_count,
                                "completed_steps": len(state.completed_steps),
                            },
                        )
                    break
                if trace_id:
                    log_fn(trace_id, "goal_not_satisfied", {"replan_count": replan_count})
                new_plan = replan(state, failed_step=None, error="goal_not_satisfied")
                state.update_plan(new_plan)
                continue
            break

        step_id = step.get("id", "?")
        action = (step.get("action") or "EXPLAIN").upper()
        description = (step.get("description") or "")[:80]
        current_plan_id = state.current_plan_id
        logger.info("[execution_loop] step_id=%s action=%s %s", step_id, action, description)

        state.context["current_step_id"] = step.get("id")
        tool_call_count += 1

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(executor.execute_step, step, state)
            try:
                result = future.result(timeout=MAX_STEP_TIMEOUT_SECONDS)
            except FuturesTimeoutError:
                logger.warning(
                    "[execution_loop] step %s timed out after %ss", step_id, MAX_STEP_TIMEOUT_SECONDS
                )
                result = StepResult(
                    step_id=step.get("id", 0),
                    action=action,
                    success=False,
                    output="",
                    latency_seconds=MAX_STEP_TIMEOUT_SECONDS,
                    error=f"Step timed out after {MAX_STEP_TIMEOUT_SECONDS}s",
                    classification=ResultClassification.RETRYABLE_FAILURE.value,
                )
                if trace_id:
                    log_fn(
                        trace_id,
                        "step_timeout",
                        {"plan_id": current_plan_id, "step_id": step_id, "action": action},
                    )

        classification = result.classification or ResultClassification.SUCCESS.value
        if isinstance(classification, ResultClassification):
            classification = classification.value
        chosen_tool = state.context.get("chosen_tool", "")

        if trace_id:
            log_fn(
                trace_id,
                "step_executed",
                {
                    "plan_id": current_plan_id,
                    "step_id": step_id,
                    "action": action,
                    "tool": chosen_tool,
                    "success": result.success,
                    "error": getattr(result, "error", None),
                    "classification": classification,
                    "dominant_artifact_mode": state.context.get("dominant_artifact_mode", "code"),
                    "step_artifact_mode": step.get("artifact_mode") if isinstance(step, dict) else None,
                },
            )

        out_summary = _output_summary(result.output)
        logger.info(
            "Step %s completed in %.3fs success=%s %s",
            step_id,
            result.latency_seconds,
            result.success,
            out_summary,
        )

        if classification == ResultClassification.FATAL_FAILURE.value:
            err = result.error or "fatal failure"
            if errors_encountered is not None:
                errors_encountered.append(err)
            if trace_id:
                log_fn(
                    trace_id,
                    "fatal_failure",
                    {"plan_id": current_plan_id, "step_id": step_id, "action": action, "error": str(err)},
                )
            logger.warning(
                "[execution_loop] FATAL_FAILURE, stopping (step_id=%s action=%s)",
                step_id,
                action,
            )
            break

        if not result.success:
            if enable_step_retries and step_retry_count < MAX_STEP_RETRIES:
                step_retry_count += 1
                logger.info(
                    "[execution_loop] step failed, retrying (%s/%s)",
                    step_retry_count,
                    MAX_STEP_RETRIES,
                )
                continue
            step_retry_count = 0
            if errors_encountered is not None:
                errors_encountered.append(result.error or "unknown")
            if trace_id:
                log_fn(
                    trace_id,
                    "error",
                    {
                        "plan_id": current_plan_id,
                        "step_id": step_id,
                        "action": action,
                        "error": str(result.error or ""),
                        "classification": classification,
                    },
                )
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[execution_loop] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            error_msg = result.error or str(result.output)[:300] if result.output else "Step failed"
            new_plan = replan(state, failed_step=step, error=error_msg)
            state.update_plan(new_plan)
            continue

        if result.success and enable_goal_evaluator:
            out = result.output
            if isinstance(out, dict):
                pm = getattr(result, "patch_size", None) or out.get("patches_applied")
                files_mod = getattr(result, "files_modified", None) or out.get("files_modified", []) or []
                if (pm or files_mod) and trace_id:
                    log_fn(
                        trace_id,
                        "patch_result",
                        {
                            "plan_id": current_plan_id,
                            "step_id": step_id,
                            "patches_applied": (
                                pm
                                if isinstance(pm, int)
                                else len(pm)
                                if isinstance(pm, list)
                                else 0
                            ),
                            "files_modified": files_mod,
                        },
                    )

        valid, validation_feedback = validate_step(step, result, state=state)
        if not valid:
            if enable_step_retries and step_retry_count < MAX_STEP_RETRIES:
                step_retry_count += 1
                logger.info(
                    "[execution_loop] validation failed, retrying (%s/%s)",
                    step_retry_count,
                    MAX_STEP_RETRIES,
                )
                continue
            step_retry_count = 0
            replan_count += 1
            if replan_count >= MAX_REPLAN_ATTEMPTS:
                logger.warning("[execution_loop] max replan attempts exceeded, stopping")
                if trace_id:
                    log_fn(trace_id, "error", {"type": "max_replan_attempts_exceeded"})
                break
            error_msg = (
                result.error
                or validation_feedback
                or (str(result.output)[:300] if result.output else "Validation failed")
            )
            new_plan = replan(state, failed_step=step, error=error_msg)
            state.update_plan(new_plan)
            continue

        state.record(step, result)
        replan_count = 0
        step_retry_count = 0

    state.context["execution_counts"] = {
        "steps_completed": len(state.completed_steps),
        "tool_calls": tool_call_count,
        "replan_count": replan_count,
    }
    if trace_id:
        log_fn(trace_id, "execution_counts", state.context["execution_counts"])

    loop_output = None
    if enable_goal_evaluator and errors_encountered is not None:
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
        fm_distinct = [x for x in dict.fromkeys(files_modified) if isinstance(x, str)]
        loop_output = {
            "completed_steps": completed_steps,
            "patches_applied": patch_count,
            "files_modified": files_modified,
            "errors_encountered": errors_encountered,
            "tool_calls": tool_call_count,
            "plan_result": state.current_plan,
            "start_time": start_time,
            "edit_telemetry": {
                "attempted_target_files": state.context.get("search_target_candidates"),
                "chosen_target_file": state.context.get("edit_target_file"),
                "chosen_symbol": state.context.get("edit_target_symbol"),
                "edit_failure_reason": state.context.get("edit_failure_reason"),
                "search_viable_file_hits": state.context.get("search_viable_file_hits"),
                "search_viable_raw_hits": state.context.get("search_viable_raw_hits"),
                "patches_applied": patch_count,
                "changed_files_count": len(fm_distinct),
                **(state.context.get("edit_patch_telemetry") or {}),
                "bm25_available": state.context.get("bm25_available"),
                "reranker_failed": state.context.get("reranker_failed"),
                "reranker_failed_fallback_used": state.context.get("reranker_failed_fallback_used"),
            },
        }

    return LoopResult(state=state, loop_output=loop_output)
