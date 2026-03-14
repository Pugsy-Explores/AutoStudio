"""Tests for agent/orchestrator/replanner."""

from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.orchestrator.replanner import replan


def _make_state(instruction: str, steps: list[dict], completed: list[dict] | None = None) -> AgentState:
    plan = {"steps": steps}
    return AgentState(
        instruction=instruction,
        current_plan=plan,
        completed_steps=completed or [],
        step_results=[],
        context={},
    )


def test_replan_fallback_when_no_failed_step():
    """When failed_step and error are empty, returns remaining steps."""
    state = _make_state("Find foo", [{"id": 1, "action": "SEARCH", "description": "find foo", "reason": "r1"}])
    result = replan(state, failed_step=None, error=None)
    assert "steps" in result
    assert len(result["steps"]) == 1
    assert result["steps"][0]["action"] == "SEARCH"


def test_replan_fallback_returns_remaining_only():
    """Fallback excludes completed steps."""
    state = _make_state(
        "Do things",
        [
            {"id": 1, "action": "SEARCH", "description": "find", "reason": "r1"},
            {"id": 2, "action": "EDIT", "description": "edit", "reason": "r2"},
        ],
        completed=[{"id": 1, "action": "SEARCH", "description": "find", "reason": "r1"}],
    )
    result = replan(state, failed_step=None, error=None)
    assert len(result["steps"]) == 1
    assert result["steps"][0]["id"] == 2


def test_replan_llm_returns_valid_plan():
    """When LLM returns valid JSON, replanner uses it."""
    mock_response = '''{"steps": [
        {"id": 1, "action": "SEARCH", "description": "Locate login handler first", "reason": "Need to find before edit"},
        {"id": 2, "action": "EDIT", "description": "Update login handler", "reason": "Apply change"}
    ]}'''
    state = _make_state(
        "Update login",
        [{"id": 1, "action": "EDIT", "description": "Update login", "reason": "direct edit"}],
    )
    failed = {"id": 1, "action": "EDIT", "description": "Update login", "reason": "direct edit"}

    with patch("agent.orchestrator.replanner.call_reasoning_model") as mock:
        mock.return_value = mock_response
        result = replan(state, failed_step=failed, error="symbol_not_found")

    assert "steps" in result
    assert len(result["steps"]) == 2
    assert result["steps"][0]["action"] == "SEARCH"
    assert result["steps"][1]["action"] == "EDIT"


def test_replan_fallback_on_llm_exception():
    """When LLM raises, fallback to remaining steps."""
    state = _make_state("Edit foo", [{"id": 1, "action": "EDIT", "description": "edit", "reason": "r"}])
    failed = {"id": 1, "action": "EDIT", "description": "edit", "reason": "r"}

    with patch("agent.orchestrator.replanner.call_reasoning_model") as mock:
        mock.side_effect = RuntimeError("Connection refused")
        result = replan(state, failed_step=failed, error="patch failed")

    assert "steps" in result
    assert len(result["steps"]) == 1
    assert result["steps"][0]["action"] == "EDIT"


def test_replan_fallback_on_invalid_json():
    """When LLM returns invalid JSON, fallback to remaining steps."""
    state = _make_state("Edit foo", [{"id": 1, "action": "EDIT", "description": "edit", "reason": "r"}])
    failed = {"id": 1, "action": "EDIT", "description": "edit", "reason": "r"}

    with patch("agent.orchestrator.replanner.call_reasoning_model") as mock:
        mock.return_value = "not valid json at all"
        result = replan(state, failed_step=failed, error="error")

    assert "steps" in result
    assert len(result["steps"]) == 1
