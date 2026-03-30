from __future__ import annotations

from agent_v2.planning.planner_action_mapper import (
    exploration_query_hash,
    is_duplicate_explore_proposal,
    planner_action_to_planner_decision,
)
from agent_v2.schemas.planner_decision import PlannerDecision


def test_planner_action_to_planner_decision_identity():
    d = PlannerDecision(type="explore", step=None, query="find foo", tool="explore")
    out = planner_action_to_planner_decision(d)
    assert out.type == "explore"
    assert out.query == "find foo"
    assert out is not d


def test_planner_decision_types_roundtrip():
    for t in ("explore", "act", "replan", "stop", "synthesize", "plan"):
        d = PlannerDecision(type=t, step=None, query=None, tool=None)
        assert planner_action_to_planner_decision(d).type == t


def test_duplicate_explore_detection():
    q = "same query"
    h = exploration_query_hash(q)
    assert is_duplicate_explore_proposal(h, q)
    assert not is_duplicate_explore_proposal(h, "other")
    assert not is_duplicate_explore_proposal(None, q)
