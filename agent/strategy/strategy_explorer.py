"""
Strategy explorer: produce alternative plan variants from retry hints and trajectory.
Used after retry_planner in the execution loop to suggest alternative strategies.
"""

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Retry strategy name -> (strategy_name, plan_steps)
STRATEGY_PLAN_MAP = {
    "rewrite_retrieval_query": ("broaden_retrieval", [{"action": "SEARCH", "description": "Expand query and re-retrieve"}]),
    "retry_edit_with_different_patch": ("alternative_edit", [{"action": "SEARCH", "description": "Find related symbols"}, {"action": "EDIT", "description": "Apply different patch approach"}]),
    "search_symbol_dependencies": ("dependency_search", [{"action": "SEARCH", "description": "Search for imports/dependencies"}]),
    "expand_search_scope": ("broaden_retrieval", [{"action": "SEARCH", "description": "Expand search scope"}]),
    "generate_new_plan": ("alternative_edit", [{"action": "EDIT", "description": "Retry with revised plan"}]),
}


def explore_strategies(
    goal: str,
    hints: Any,
    trajectory_history: list[dict],
    max_strategies: int = 3,
) -> list[dict]:
    """
    Generate alternative plan variants from hints and history.
    Each dict: {strategy_name, plan_steps, score}.
    """
    strategy = getattr(hints, "strategy", None) or ""
    name, steps = STRATEGY_PLAN_MAP.get(strategy, ("retry", [{"action": "EDIT", "description": "Retry with revised plan"}]))
    score = 0.8 if strategy in STRATEGY_PLAN_MAP else 0.5
    seen: set[str] = set()
    out: list[dict] = []
    candidate = {"strategy_name": name, "plan_steps": steps, "score": score}
    h = hashlib.sha256(str(steps).encode()).hexdigest()
    if h not in seen:
        seen.add(h)
        out.append(candidate)
    return out[:max_strategies]
