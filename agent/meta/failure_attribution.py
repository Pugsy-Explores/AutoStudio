"""Failure attribution layer: map existing signals to canonical failure_reason. No new metrics, no thresholds, no LLM."""

import logging

logger = logging.getLogger(__name__)

# Priority order (exact): SUCCESS < RETRIEVAL < NO_SIGNAL < SELECTION < EXPLORATION < GROUNDING < PLANNING < PLANNING_LOOP
SUCCESS = "SUCCESS"
RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
NO_SIGNAL_FAILURE = "NO_SIGNAL_FAILURE"
SELECTION_FAILURE = "SELECTION_FAILURE"
EXPLORATION_FAILURE = "EXPLORATION_FAILURE"
GROUNDING_FAILURE = "GROUNDING_FAILURE"
PLANNING_FAILURE = "PLANNING_FAILURE"
PLANNING_LOOP = "PLANNING_LOOP"

_CANONICAL = (
    SUCCESS,
    RETRIEVAL_FAILURE,
    NO_SIGNAL_FAILURE,
    SELECTION_FAILURE,
    EXPLORATION_FAILURE,
    GROUNDING_FAILURE,
    PLANNING_FAILURE,
    PLANNING_LOOP,
)


def classify_failure_reason(record: dict) -> str:
    """
    Map task record to one canonical failure_reason. One per task.
    Uses existing signals only; no new metrics, thresholds, or LLM.

    Priority: SUCCESS, RETRIEVAL_FAILURE, NO_SIGNAL_FAILURE, SELECTION_FAILURE,
    EXPLORATION_FAILURE, GROUNDING_FAILURE, PLANNING_FAILURE, PLANNING_LOOP.
    """
    if record.get("task_success") is True:
        return SUCCESS

    termination_reason = record.get("termination_reason") or record.get("terminal")
    if termination_reason == "LOOP_PROTECTION":
        return PLANNING_LOOP

    # NO_SIGNAL_FAILURE: retrieval worked but pool has no useful signal
    if (
        record.get("retrieval_empty") is False
        and record.get("pool_has_signal") is False
    ):
        return NO_SIGNAL_FAILURE

    errors = record.get("errors") or []
    if isinstance(errors, str):
        errors = [errors]
    err_text = " ".join(str(e).lower() for e in errors if e)

    edit_failure = record.get("edit_failure_reason") or ""

    # RETRIEVAL_FAILURE
    if any(
        x in err_text or x in edit_failure.lower()
        for x in ("empty", "retrieval", "0 results", "no results", "retrieve")
    ):
        return RETRIEVAL_FAILURE

    # SELECTION_FAILURE
    if any(x in err_text for x in ("selection", "context", "ranking", "ranked_context")):
        return SELECTION_FAILURE

    # EXPLORATION_FAILURE: exploration used but contributed no new tokens to answer
    if (
        record.get("exploration_used") is True
        and (record.get("exploration_used_new_token_count") or 0) == 0
    ):
        return EXPLORATION_FAILURE
    if any(x in err_text for x in ("exploration", "expand", "graph")):
        return EXPLORATION_FAILURE

    # GROUNDING_FAILURE
    if any(
        x in err_text or x in edit_failure.lower()
        for x in ("patch", "validation", "reject", "anchor", "grounding")
    ):
        return GROUNDING_FAILURE

    # PLANNING_FAILURE (NOT_FOUND, planner issues, default)
    return PLANNING_FAILURE


def ensure_failure_reason(record: dict, task_id: str | None = None) -> str:
    """
    Ensure record has failure_reason; compute via classify_failure_reason if missing.
    Logs [attribution] task_id=... reason=... once per task.
    """
    reason = record.get("failure_reason")
    if reason in _CANONICAL:
        canonical = reason
    else:
        canonical = classify_failure_reason(record)
        record["failure_reason"] = canonical
    tid = task_id or record.get("task_id") or record.get("id") or "?"
    logger.info("[attribution] task_id=%s reason=%s", tid, canonical)
    return canonical
