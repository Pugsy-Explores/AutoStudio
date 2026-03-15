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
from pathlib import Path

from agent.execution.executor import StepExecutor
from agent.memory.state import AgentState
from agent.orchestrator.plan_resolver import get_plan
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step

logger = logging.getLogger(__name__)

# Termination conditions (per Agentic Engineering Guide, ROUTING_ARCHITECTURE_REPORT)
MAX_REPLAN_ATTEMPTS = 5  # Prevent infinite replan when same step keeps failing
MAX_TASK_RUNTIME_SECONDS = 15 * 60  # 15 minutes
MAX_LOOP_ITERATIONS = 100  # Stall detection: prevent runaway agents


def run_agent(instruction: str) -> AgentState:
    """
    Run full pipeline: get_plan (router + planner) -> create state -> execute loop.

    Flow per AGENT_LOOP_WORKFLOW.md:
    - get_plan: instruction router (when enabled) or planner
    - Execute step -> validate -> on failure: undo, replan, continue
    - Termination: no more steps, max replan exceeded, max runtime, max iterations
    """
    plan_result = get_plan(instruction)
    project_root = os.environ.get("SERENA_PROJECT_DIR") or str(Path.cwd())
    state = AgentState(
        instruction=instruction,
        current_plan=plan_result,
        context={
            "project_root": project_root,
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
    iteration = 0

    while not state.is_finished():
        iteration += 1
        if iteration > MAX_LOOP_ITERATIONS:
            logger.warning("[agent_loop] max iterations exceeded, stopping")
            break
        if time.perf_counter() - start_time > MAX_TASK_RUNTIME_SECONDS:
            logger.warning("[agent_loop] max task runtime exceeded, stopping")
            break

        step = state.next_step()
        if step is None:
            break

        step_id = step.get("id", "?")
        action = step.get("action", "?")
        description = step.get("description", "")[:80]
        logger.info("Executing Step %s: %s - %s", step_id, action, description)

        print("[workflow] executor")
        result = executor.execute_step(step, state)
        state.record(step, result)

        out_summary = _output_summary(result.output)
        logger.info(
            "Step %s completed in %.3fs success=%s %s",
            step_id,
            result.latency_seconds,
            result.success,
            out_summary,
        )

        valid, validation_feedback = validate_step(step, result, state=state)
        if not result.success or not valid:
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
        else:
            replan_count = 0

    return state


def _output_summary(output) -> str:
    """One-line summary of step output for logging."""
    if isinstance(output, dict):
        keys = list(output.keys())[:5]
        return "output_keys=" + ",".join(str(k) for k in keys)
    s = str(output)
    return "output=" + (s[:80] + "..." if len(s) > 80 else s)
