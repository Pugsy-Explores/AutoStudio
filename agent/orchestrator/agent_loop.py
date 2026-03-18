"""
Agent loop: instruction -> plan -> execution_loop (step retries, no goal evaluator) -> return state.

Per docs (AGENT_LOOP_WORKFLOW.md, phase.md) and best practices:
- Router decides, planner plans, dispatcher executes
- Termination: task complete, max replan, max runtime, iteration limit
- Plan loosely: replan on failure rather than fail

Phase 3: run_agent() delegates to shared execution_loop() with enable_step_retries=True,
enable_goal_evaluator=False.
"""

import logging
import os
import uuid
import warnings
from pathlib import Path

from agent.memory.state import AgentState
from agent.observability.trace_logger import finish_trace, log_event, start_trace
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop
from agent.orchestrator.plan_resolver import get_plan
from planner.planner_utils import is_explicit_docs_lane_by_structure

logger = logging.getLogger(__name__)


# Deprecated entrypoint: use run_controller() instead.
def run_agent(instruction: str) -> AgentState:
    """
    Backward-compatible entrypoint: runs execution_loop with step retries and no goal evaluator.
    Aligned with run_deterministic() on config limits, no recording of failed steps, no undo_last_step.
    Prefer run_controller(instruction) for new code.
    """
    warnings.warn(
        "run_agent() is deprecated. Use run_controller() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    project_root = os.environ.get("SERENA_PROJECT_DIR") or str(Path.cwd())
    task_id = str(uuid.uuid4())
    trace_id = start_trace(task_id, project_root, query=instruction)

    try:
        plan_result = get_plan(instruction, trace_id=trace_id, log_event_fn=log_event)
        dominant_artifact_mode = "docs" if is_explicit_docs_lane_by_structure(plan_result) else "code"
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
                # Phase 6A: single-lane per task. Set once at entrypoint.
                "dominant_artifact_mode": dominant_artifact_mode,
                "lane_violations": [],
            },
        )
        # Emit once (matches deterministic runner trace pattern).
        log_event(trace_id, "dominant_artifact_mode", {"dominant_artifact_mode": dominant_artifact_mode, "plan_id": plan_result.get("plan_id")})

        result = execution_loop(
            state,
            instruction,
            trace_id=trace_id,
            log_event_fn=log_event,
            retry_context=None,
            mode=ExecutionLoopMode.AGENT,
        )
        return result.state
    finally:
        finish_trace(trace_id)
