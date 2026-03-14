"""Agent loop: instruction -> plan -> execute steps -> optional replan -> return state."""

import logging

from agent.execution.executor import StepExecutor
from agent.memory.state import AgentState
from agent.orchestrator.replanner import replan
from agent.orchestrator.validator import validate_step
from planner.planner import plan

logger = logging.getLogger(__name__)


def run_agent(instruction: str) -> AgentState:
    """
    Run full pipeline: plan(instruction) -> create state -> execute steps;
    on step failure or validation failure, replan and continue until finished.
    Returns final AgentState.
    """
    plan_result = plan(instruction)
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
        },
    )

    executor = StepExecutor()

    while not state.is_finished():
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

        if not result.success or not validate_step(step, result):
            new_plan = replan(state)
            state.update_plan(new_plan)

    return state


def _output_summary(output) -> str:
    """One-line summary of step output for logging."""
    if isinstance(output, dict):
        keys = list(output.keys())[:5]
        return "output_keys=" + ",".join(str(k) for k in keys)
    s = str(output)
    return "output=" + (s[:80] + "..." if len(s) > 80 else s)
