"""Capture human feedback and route through critic -> retry planner -> improved patch."""

import logging

from agent.meta.critic import diagnose
from agent.meta.evaluator import EvaluationResult
from agent.meta.retry_planner import plan_retry
from agent.observability.trace_logger import log_event
from agent.roles.workspace import AgentWorkspace

logger = logging.getLogger(__name__)


def apply_feedback(
    comment: str,
    workspace: AgentWorkspace,
    trace_id: str | None = None,
) -> AgentWorkspace:
    """
    Apply developer feedback: critic -> retry planner -> updated workspace.

    The developer comment is treated as evaluation reason; critic produces diagnosis,
    retry planner produces hints; workspace is updated with retry_instruction.

    Args:
        comment: Developer feedback (e.g. "Retry should be exponential backoff")
        workspace: Current AgentWorkspace
        trace_id: Optional trace ID for log_event

    Returns:
        Updated AgentWorkspace with retry_instruction set
    """
    if trace_id:
        log_event(trace_id, "developer_feedback", {"comment": comment[:200]})

    evaluation = EvaluationResult(
        status="FAILURE",
        reason=f"developer_feedback: {comment}",
        score=0.0,
    )
    try:
        diagnosis = diagnose(workspace.state, evaluation)
        hints = plan_retry(workspace.goal, diagnosis)
        retry_instruction = hints.plan_override or hints.rewrite_query or diagnosis.suggestion or comment
        workspace.retry_instruction = retry_instruction
        logger.info("[developer_feedback] retry_instruction=%s", retry_instruction[:100])
    except Exception as e:
        logger.warning("[developer_feedback] apply_feedback failed: %s", e)
        workspace.retry_instruction = comment

    return workspace
