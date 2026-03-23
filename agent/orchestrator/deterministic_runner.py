"""
ReAct execution runner. Creates minimal state and runs execution_loop; model selects all actions.
"""

import logging

from agent.memory.state import AgentState
from agent.observability.trace_logger import log_event
from agent.orchestrator.execution_loop import execution_loop

logger = logging.getLogger(__name__)


def run_hierarchical(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
) -> tuple[AgentState, dict]:
    """
    ReAct execution: minimal state, direct execution_loop. Model selects all actions.
    Returns (state, loop_output).
    """
    log_fn = log_event_fn or log_event

    context = {
        "tool_node": "START",
        "project_root": project_root,
        "trace_id": trace_id,
        "similar_past_tasks": similar_tasks or [],
    }
    state = AgentState(
        instruction=instruction,
        current_plan={"steps": [], "plan_id": "react"},
        context=context,
    )
    loop_result = execution_loop(
        state,
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        max_runtime_seconds=max_runtime_seconds,
    )
    loop_output = getattr(loop_result, "loop_output", None) or {}
    return loop_result.state, loop_output
