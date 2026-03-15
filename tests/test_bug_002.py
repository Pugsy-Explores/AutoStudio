"""Regression test for BUG-002: Planner generates invalid step.

Ensures normalize_actions maps hallucinated/invalid actions to allowed set
and validate_plan rejects plans with invalid actions before normalization.
"""

import pytest

from planner.planner_utils import normalize_actions, validate_plan


def test_normalize_actions_maps_invalid_to_explain():
    """Invalid/hallucinated actions should be mapped to EXPLAIN."""
    plan = {
        "steps": [
            {"id": 1, "action": "FIND", "description": "find something"},
            {"id": 2, "action": "WRITE", "description": "write code"},
            {"id": 3, "action": "unknown_tool", "description": "bad"},
        ]
    }
    out = normalize_actions(plan)
    assert out["steps"][0]["action"] == "EXPLAIN"
    assert out["steps"][1]["action"] == "EXPLAIN"
    assert out["steps"][2]["action"] == "EXPLAIN"


def test_normalize_actions_preserves_valid():
    """Valid actions should remain unchanged."""
    plan = {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "search"},
            {"id": 2, "action": "edit", "description": "edit"},
            {"id": 3, "action": "EXPLAIN", "description": "explain"},
        ]
    }
    out = normalize_actions(plan)
    assert out["steps"][0]["action"] == "SEARCH"
    assert out["steps"][1]["action"] == "EDIT"
    assert out["steps"][2]["action"] == "EXPLAIN"


def test_validate_plan_accepts_normalized_plan():
    """validate_plan should accept plan with all allowed actions."""
    plan = {"steps": [{"action": "SEARCH"}, {"action": "EXPLAIN"}]}
    assert validate_plan(plan) is True


def test_validate_plan_rejects_invalid_action():
    """validate_plan should reject plan with invalid action before normalization."""
    plan = {"steps": [{"action": "SEARCH"}, {"action": "FIND"}]}
    assert validate_plan(plan) is False
