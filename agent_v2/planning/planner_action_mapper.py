"""
Exploration query hashing + identity bridge for legacy import paths.

TaskPlanner emits `PlannerDecision` directly; no interpretive mapping.
"""

from __future__ import annotations

import hashlib

from agent_v2.schemas.planner_decision import PlannerDecision


def planner_action_to_planner_decision(decision: PlannerDecision) -> PlannerDecision:
    """
    Identity: TaskPlanner and runtime share `PlannerDecision`.

    Kept for backward-compatible imports; returns a deep copy.
    """
    return decision.model_copy(deep=True)


def exploration_query_hash(query: str) -> str:
    """Stable short hash for duplicate explore detection."""
    n = (query or "").strip()
    return hashlib.sha256(n.encode("utf-8")).hexdigest()[:32]


def is_duplicate_explore_proposal(
    last_hash: str | None,
    proposed_query: str | None,
) -> bool:
    """True for identical consecutive explore queries (anti-pattern)."""
    if not last_hash or not proposed_query:
        return False
    return hashlib.sha256(proposed_query.strip().encode("utf-8")).hexdigest()[:32] == last_hash
