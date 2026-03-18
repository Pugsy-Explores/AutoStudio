from unittest.mock import patch

import pytest

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState


def _state(dominant: str = "code") -> AgentState:
    return AgentState(
        instruction="x",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": ".",
            "trace_id": None,
            "tool_node": "START",
            "ranked_context": [],
            "dominant_artifact_mode": dominant,
            "lane_violations": [],
        },
    )


def test_dominant_docs_lane_search_is_fatal_lane_violation():
    state = _state("docs")
    step = {"id": 1, "action": "SEARCH", "description": "find foo", "reason": "r", "artifact_mode": "docs"}
    out = dispatch(step, state)
    assert out.get("classification") == "FATAL_FAILURE"
    assert "lane_violation" in (out.get("error") or "")


def test_dominant_docs_lane_explain_missing_artifact_mode_is_fatal():
    state = _state("docs")
    step = {"id": 1, "action": "EXPLAIN", "description": "Explain docs", "reason": "r"}  # missing artifact_mode
    out = dispatch(step, state)
    assert out.get("classification") == "FATAL_FAILURE"
    assert "lane_violation" in (out.get("error") or "")


def test_dominant_code_lane_docs_step_is_fatal():
    state = _state("code")
    step = {"id": 1, "action": "EXPLAIN", "artifact_mode": "docs", "description": "Explain from docs", "reason": "r"}
    out = dispatch(step, state)
    assert out.get("classification") == "FATAL_FAILURE"
    assert "lane_violation" in (out.get("error") or "")


def test_valid_docs_lane_steps_execute_when_stubbed():
    state = _state("docs")
    steps = [
        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "Find docs", "query": "readme", "reason": "r"},
        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "Build docs context", "reason": "r"},
        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "Explain docs", "reason": "r"},
    ]
    with (
        patch("agent.execution.step_dispatcher.search_candidates", return_value={"candidates": [{"file": "README.md"}]}),
        patch("agent.execution.step_dispatcher.build_context", return_value={"context_blocks": [{"file": "README.md", "snippet": "x"}]}),
        patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="This is a sufficiently long explanation output for validation."),
    ):
        for st in steps:
            out = dispatch(st, state)
            assert out.get("success") is True


def test_valid_code_lane_step_allows_default_artifact_mode():
    state = _state("code")
    step = {"id": 1, "action": "EXPLAIN", "description": "Explain X", "reason": "r"}  # defaults to code
    with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="This is a sufficiently long explanation output for validation."):
        # Also stub ensure_context_before_explain to avoid auto-search path.
        with patch("agent.execution.step_dispatcher.ensure_context_before_explain", return_value=(True, None)):
            out = dispatch(step, state)
    assert out.get("success") is True
