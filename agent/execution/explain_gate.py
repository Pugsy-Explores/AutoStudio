"""Context gate before EXPLAIN: avoid LLM call when ranked_context is empty."""

import logging

from agent.memory.state import AgentState

logger = logging.getLogger(__name__)


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
