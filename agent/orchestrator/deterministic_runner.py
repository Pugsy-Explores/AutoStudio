"""Deterministic execution loop: plan -> execution_loop (goal evaluator, no step retries). Single source of truth for Mode 1."""

import logging

from agent.memory.state import AgentState
from agent.observability.trace_logger import log_event
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop
from agent.orchestrator.plan_resolver import get_plan

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
    Run deterministic loop: get_plan -> execution_loop (goal evaluator on plan exhaustion, no step retries).
    Returns (state, loop_output) where loop_output has completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result, start_time.

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

    result = execution_loop(
        state,
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
        mode=ExecutionLoopMode.DETERMINISTIC,
    )

    assert result.loop_output is not None, "run_deterministic expects loop_output from execution_loop"
    return result.state, result.loop_output
