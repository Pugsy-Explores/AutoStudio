"""
Tests for planner evaluation: structural validation, action coverage, step format.
Run with: pytest tests/test_planner_eval.py -v
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from planner.planner_eval import (
    DEFAULT_DATASET_PATH,
    check_action_coverage,
    evaluate_datapoint,
    extract_actions,
    is_subsequence,
    load_dataset,
    run_eval,
    validate_structure,
)


def test_validate_structure_valid():
    """Valid plan with id, action, description, reason passes."""
    plan_dict = {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Locate X", "reason": "Need context"},
            {"id": 2, "action": "EXPLAIN", "description": "Explain flow", "reason": "User asked"},
        ]
    }
    assert validate_structure(plan_dict) is True


def test_validate_structure_invalid_action():
    """Invalid action fails validation."""
    plan_dict = {
        "steps": [
            {"id": 1, "action": "INVALID", "description": "x", "reason": "y"},
        ]
    }
    assert validate_structure(plan_dict) is False


def test_validate_structure_missing_keys():
    """Missing required keys fails."""
    plan_dict = {"steps": [{"id": 1, "action": "SEARCH"}]}  # missing description, reason
    assert validate_structure(plan_dict) is False


def test_extract_actions():
    """Extract ordered actions from plan."""
    plan_dict = {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "x", "reason": "y"},
            {"id": 2, "action": "EXPLAIN", "description": "z", "reason": "w"},
        ]
    }
    assert extract_actions(plan_dict) == ["SEARCH", "EXPLAIN"]


def test_check_action_coverage():
    """Coverage: expected actions must be subset of predicted."""
    assert check_action_coverage(["SEARCH"], ["SEARCH", "EXPLAIN"]) is True
    assert check_action_coverage(["EDIT"], ["SEARCH"]) is False


def test_is_subsequence():
    """Order must be preserved."""
    assert is_subsequence(["SEARCH", "EXPLAIN"], ["SEARCH", "EXPLAIN"]) is True
    assert is_subsequence(["SEARCH", "EXPLAIN"], ["EXPLAIN", "SEARCH"]) is False
    assert is_subsequence(["SEARCH"], ["SEARCH", "EDIT", "EXPLAIN"]) is True


def test_evaluate_datapoint():
    """Full evaluation of a datapoint."""
    expected = [{"action": "SEARCH"}, {"action": "EXPLAIN"}]
    predicted = {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Locate X", "reason": "r1"},
            {"id": 2, "action": "EXPLAIN", "description": "Explain", "reason": "r2"},
        ]
    }
    result = evaluate_datapoint(expected, predicted)
    assert result["structural_valid"] is True
    assert result["action_coverage"] is True
    assert result["order_correct"] is True


def test_load_dataset():
    """Dataset loads and has expected structure."""
    data = load_dataset(limit=3)
    assert len(data) == 3
    for item in data:
        assert "instruction" in item
        assert "expected_steps" in item
        assert isinstance(item["expected_steps"], list)


def test_run_eval_mocked():
    """Run eval with mocked plan() - no LLM calls."""
    def mock_plan(instruction: str) -> dict:
        # Return structurally valid plan matching common patterns
        if "Explain" in instruction or "explain" in instruction:
            return {
                "steps": [
                    {"id": 1, "action": "SEARCH", "description": "Locate", "reason": "r"},
                    {"id": 2, "action": "EXPLAIN", "description": "Explain", "reason": "r"},
                ]
            }
        if "SEARCH" in instruction or "Where" in instruction or "Find" in instruction:
            return {
                "steps": [{"id": 1, "action": "SEARCH", "description": "Locate", "reason": "r"}]
            }
        if "EDIT" in instruction or "Add" in instruction or "Refactor" in instruction:
            return {
                "steps": [{"id": 1, "action": "EDIT", "description": "Edit", "reason": "r"}]
            }
        if "INFRA" in instruction or "docker" in instruction or "Redis" in instruction:
            return {
                "steps": [{"id": 1, "action": "INFRA", "description": "Infra", "reason": "r"}]
            }
        return {
            "steps": [{"id": 1, "action": "EXPLAIN", "description": "Default", "reason": "r"}]
        }

    with patch("planner.planner_eval.plan", side_effect=mock_plan):
        metrics = run_eval(limit=5, verbose=False)

    assert "structural_valid_rate" in metrics
    assert "action_coverage_accuracy" in metrics
    assert "dependency_order_accuracy" in metrics
    assert metrics["total"] == 5
    assert 0 <= metrics["structural_valid_rate"] <= 1
    assert 0 <= metrics["action_coverage_accuracy"] <= 1
