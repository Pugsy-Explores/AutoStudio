"""
Agent loop: instruction -> plan -> execute steps -> validate -> optional replan -> return state.

Per docs (AGENT_LOOP_WORKFLOW.md, phase.md) and best practices:
- Router decides, planner plans, dispatcher executes
- Termination: task complete, max replan, max runtime, iteration limit
- Plan loosely: replan on failure rather than fail
"""

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path

from config.agent_config import MAX_STEP_TIMEOUT_SECONDS
from agent.execution.executor import StepExecutor
from agent.execution.policy_engine import ResultClassification
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.orchestrator.plan_resolver import get_plan
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step

logger = logging.getLogger(__name__)

# Termination conditions (per Phase 4 roadmap)
MAX_REPLAN_ATTEMPTS = 3  # Prevent infinite replan when same step keeps failing
MAX_STEP_RETRIES = 2  # Retry same step before triggering replan
MAX_STEPS = 20  # Hard step count per task
MAX_TOOL_CALLS = 50  # Max tool invocations per task
MAX_TASK_RUNTIME_SECONDS = 60  # 60 seconds wall clock
MAX_LOOP_ITERATIONS = 100  # Stall detection: prevent runaway agents


def run_agent(instruction: str) -> AgentState:
    """
    Run full pipeline: get_plan (router + planner) -> create state -> execute loop.

    Flow per AGENT_LOOP_WORKFLOW.md:
    - get_plan: instruction router (when enabled) or planner
    - Execute step -> validate -> on failure: undo, replan, continue
    - Termination: no more steps, max replan exceeded, max runtime, max iterations
    """
    project_root = os.environ.get("SERENA_PROJECT_DIR") or str(Path.cwd())
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, project_root, query=instruction)

    try:
        plan_result = get_plan(instruction, trace_id=trace_id, log_event_fn=log_event)
        state = AgentState(
            instruction=instruction,
            current_plan=plan_result,
            context={
                "project_root": project_root,
                "trace_id": trace_id,
                "instruction": instruction,
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

        executor = StepExecutor()
        start_time = time.perf_counter()
        replan_count = 0
        step_retry_count = 0
        iteration = 0
        tool_call_count = 0

        # Log limits to trace for observability
        state.context["execution_limits"] = {
            "max_steps": MAX_STEPS,
            "max_tool_calls": MAX_TOOL_CALLS,
            "max_runtime_seconds": MAX_TASK_RUNTIME_SECONDS,
            "max_step_timeout_seconds": MAX_STEP_TIMEOUT_SECONDS,
            "max_replan_attempts": MAX_REPLAN_ATTEMPTS,
            "max_step_retries": MAX_STEP_RETRIES,
        }
        log_event(trace_id, "execution_limits", state.context["execution_limits"])

        while not state.is_finished():
            iteration += 1
            if iteration > MAX_LOOP_ITERATIONS:
                logger.warning("[agent_loop] max iterations exceeded, stopping")
                break
            if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
                logger.warning("[agent_loop] max task runtime exceeded, stopping")
                break
            if len(state.completed_steps) >= MAX_STEPS:
                logger.warning("[agent_loop] max steps (%s) exceeded, stopping", MAX_STEPS)
                break
            if tool_call_count >= MAX_TOOL_CALLS:
                logger.warning("[agent_loop] max tool calls (%s) exceeded, stopping", MAX_TOOL_CALLS)
                break

            step = state.next_step()
            if step is None:
                break

            step_id = step.get("id", "?")
            action = step.get("action", "?")
            description = step.get("description", "")[:80]
            logger.info("Executing Step %s: %s - %s", step_id, action, description)

            state.context["current_step_id"] = step.get("id")
            tool_call_count += 1

            # Per-step timeout: prevent a single slow tool call from consuming full task budget
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(executor.execute_step, step, state)
                try:
                    result = future.result(timeout=MAX_STEP_TIMEOUT_SECONDS)
                except FuturesTimeoutError:
                    logger.warning("[agent_loop] step %s timed out after %ss", step_id, MAX_STEP_TIMEOUT_SECONDS)
                    result = StepResult(
                        step_id=step.get("id", 0),
                        action=step.get("action", "?"),
                        success=False,
                        output="",
                        latency_seconds=MAX_STEP_TIMEOUT_SECONDS,
                        error=f"Step timed out after {MAX_STEP_TIMEOUT_SECONDS}s",
                        classification=ResultClassification.FATAL_FAILURE.value,
                    )
                    log_event(trace_id, "step_timeout", {"step_id": step_id, "action": action})

            log_event(
                trace_id,
                "step_executed",
                {
                    "step_id": step_id,
                    "action": action,
                    "tool": state.context.get("chosen_tool", ""),
                    "success": result.success,
                    "error": result.error,
                    "classification": result.classification or "SUCCESS",
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

            valid, validation_feedback = validate_step(step, result, state=state)
            classification = result.classification or ResultClassification.SUCCESS.value
            if classification == ResultClassification.FATAL_FAILURE.value:
                logger.warning("[agent_loop] FATAL_FAILURE classification, stopping without replan")
                break
            if result.success and valid:
                state.record(step, result)
                replan_count = 0
                step_retry_count = 0
            else:
                # Retry same step before replanning (Phase 4: max_step_retries)
                if step_retry_count < MAX_STEP_RETRIES:
                    step_retry_count += 1
                    logger.info("[agent_loop] step failed, retrying (%s/%s)", step_retry_count, MAX_STEP_RETRIES)
                    continue  # Re-execute same step (do not record, do not replan)
                step_retry_count = 0
                state.record(step, result)
                replan_count += 1
                if replan_count >= MAX_REPLAN_ATTEMPTS:
                    logger.warning("[agent_loop] max replan attempts exceeded, stopping")
                    break
                state.undo_last_step()
                error_msg = (
                    result.error
                    or validation_feedback
                    or (str(result.output)[:300] if result.output else "Validation failed")
                )
                new_plan = replan(state, failed_step=step, error=error_msg)
                state.update_plan(new_plan)

        state.context["execution_counts"] = {
            "steps_completed": len(state.completed_steps),
            "tool_calls": tool_call_count,
            "replan_count": replan_count,
        }
        log_event(trace_id, "execution_counts", state.context["execution_counts"])
        return state
    finally:
        finish_trace(trace_id)


def _output_summary(output) -> str:
    """One-line summary of step output for logging."""
    if isinstance(output, dict):
        keys = list(output.keys())[:5]
        return "output_keys=" + ",".join(str(k) for k in keys)
    s = str(output)
    return "output=" + (s[:80] + "..." if len(s) > 80 else s)
