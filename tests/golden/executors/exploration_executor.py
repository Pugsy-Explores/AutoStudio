"""
Exploration executor — wrap exploration layer only.
Calls exploration function with minimal selected context. No full pipeline.
"""

from typing import Any, Dict, List


def run_exploration(input_: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call exploration tools only. Input must have: pool (list of candidates), candidate_id.
    Returns metrics; structure is empty (exploration is a pure function, no execution structure).
    """
    from agent.retrieval.exploration_tools import expand_from_node

    pool: List[dict] = input_.get("pool") or []
    candidate_id = input_.get("candidate_id") or ""

    if not pool or not candidate_id:
        return {
            "structure": {},
            "metrics": {
                "exploration_used": False,
                "exploration_added_count": 0,
                "exploration_effective": False,
            },
        }

    expanded = expand_from_node(candidate_id, pool)
    count = len(expanded) if isinstance(expanded, list) else 0
    exploration_effective = count > 0

    return {
        "structure": {},
        "metrics": {
            "exploration_used": True,
            "exploration_added_count": count,
            "exploration_effective": exploration_effective,
        },
    }
