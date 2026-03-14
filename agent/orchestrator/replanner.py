"""Replanner: on failure return remaining steps. Stub; TODO: LLM-based replan with history."""

import logging

from agent.memory.state import AgentState

logger = logging.getLogger(__name__)


def replan(state: AgentState) -> dict:
    """
    On failure, return plan with only remaining (not yet completed) steps.
    Stub: log failure and return remaining steps unchanged.
    """
    print("[workflow] replanner")
    last = state.step_results[-1] if state.step_results else None
    if last:
        logger.warning(
            "Replan triggered: step_id=%s action=%s success=%s error=%s",
            last.step_id,
            last.action,
            last.success,
            last.error,
        )
    steps = state.current_plan.get("steps") or []
    completed_ids = {s.get("id") for s in state.completed_steps}
    remaining = [s for s in steps if isinstance(s, dict) and s.get("id") not in completed_ids]
    return {"steps": remaining}
