"""Context gate before EXPLAIN: avoid LLM call when ranked_context is empty."""

import logging

from agent.contracts.error_codes import REASON_CODE_INSUFFICIENT_GROUNDING
from agent.memory.state import AgentState

logger = logging.getLogger(__name__)

# Re-export for step_dispatcher
__all__ = ["REASON_CODE_INSUFFICIENT_GROUNDING", "code_explain_grounding_ready", "ensure_context_before_explain"]


def code_explain_grounding_ready(step: dict, state: AgentState) -> tuple[bool, dict]:
    """Check grounding signals. Returns (ready, signals). Minimal stub for Stage 2 isolation."""
    return True, {"reason_code": REASON_CODE_INSUFFICIENT_GROUNDING}


def ensure_context_before_explain(step: dict, state: AgentState) -> tuple[bool, dict | None]:
    """
    Return (has_context, synthetic_search_step_or_none).
    Do NOT call the model.
    If ranked_context is empty, returns (False, synthetic SEARCH step).
    """
    ranked = state.context.get("ranked_context") or []
    if ranked:
        return True, None
    return False, {
        "action": "SEARCH",
        "description": step.get("description", ""),
        "id": step.get("id"),
    }
