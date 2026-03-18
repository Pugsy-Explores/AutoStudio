"""Tests for agent/orchestrator/replanner."""

from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.models.model_types import ModelType
from agent.orchestrator.replanner import replan


def _make_state(instruction: str, steps: list[dict], completed: list | None = None) -> AgentState:
    plan_id = "test_plan_001"
    plan = {"plan_id": plan_id, "steps": steps}
    # Phase 4: completed_steps store (plan_id, step_id) tuples.
    completed_steps: list[tuple[str, int]] = []
    if completed:
        for item in completed:
            if isinstance(item, tuple) and len(item) == 2:
                completed_steps.append((str(item[0]), int(item[1])))
            elif isinstance(item, dict) and "id" in item:
                completed_steps.append((plan_id, int(item.get("id"))))
    return AgentState(
        instruction=instruction,
        current_plan=plan,
        completed_steps=completed_steps,
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
    mock_response = '{"steps": [{"id": 1, "action": "SEARCH", "description": "Locate login handler first", "reason": "Need to find before edit"}, {"id": 2, "action": "EDIT", "description": "Update login handler", "reason": "Apply change"}]}'
    state = _make_state(
        "Update login",
        [{"id": 1, "action": "EDIT", "description": "Update login", "reason": "direct edit"}],
    )
    failed = {"id": 1, "action": "EDIT", "description": "Update login", "reason": "direct edit"}

    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_response),
    ):
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


def test_replanner_preserves_docs_artifact_mode_when_omitted_by_llm():
    # Current plan is docs-mode; replanner LLM returns retrieval steps without artifact_mode.
    state = _make_state(
        "where are readmes and docs",
        [
            {"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r", "artifact_mode": "docs"},
            {"id": 2, "action": "BUILD_CONTEXT", "description": "Build docs context", "reason": "r", "artifact_mode": "docs"},
            {"id": 3, "action": "EXPLAIN", "description": "Explain docs", "reason": "r", "artifact_mode": "docs"},
        ],
    )
    failed = {"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r", "artifact_mode": "docs"}
    mock_response = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH_CANDIDATES", "description": "Retry docs scan", "reason": "r", "query": "readme docs"},'
        '{"id": 2, "action": "BUILD_CONTEXT", "description": "Build docs context", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "description": "Explain docs", "reason": "r"}'
        ']}'
    )
    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_response),
    ):
        result = replan(state, failed_step=failed, error="some error")

    steps = result.get("steps") or []
    assert steps[0].get("artifact_mode") == "docs"
    assert steps[1].get("artifact_mode") == "docs"
    assert steps[2].get("artifact_mode") == "docs"


def test_replanner_does_not_preserve_docs_mode_for_mixed_plan_noise():
    # Mixed plan: one docs step exists, but other docs-compatible steps are code-lane (missing artifact_mode).
    # This should NOT count as docs-lane by structure.
    state = _make_state(
        "mixed",
        [
            {"id": 1, "action": "SEARCH_CANDIDATES", "description": "docs step", "reason": "r", "artifact_mode": "docs"},
            {"id": 2, "action": "BUILD_CONTEXT", "description": "code lane build", "reason": "r"},  # missing artifact_mode => code
            {"id": 3, "action": "EXPLAIN", "description": "code lane explain", "reason": "r"},  # missing artifact_mode => code
        ],
    )
    failed = {"id": 2, "action": "BUILD_CONTEXT", "description": "code lane build", "reason": "r"}
    mock_response = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH_CANDIDATES", "description": "Retry", "reason": "r", "query": "x"},'
        '{"id": 2, "action": "BUILD_CONTEXT", "description": "Build", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "description": "Explain", "reason": "r"}'
        ']}'
    )
    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_response),
    ):
        result = replan(state, failed_step=failed, error="boom")

    steps = result.get("steps") or []
    assert all("artifact_mode" not in s for s in steps), "docs mode should not be preserved for mixed/noisy plan"


def test_replanner_respects_explicit_artifact_mode_from_llm_and_never_sets_docs_on_edit_search():
    # Docs lineage via failed_step, but replanner explicitly sets artifact_mode="code" on EXPLAIN and includes SEARCH/EDIT.
    state = _make_state(
        "docs",
        [
            {"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r", "artifact_mode": "docs"},
            {"id": 2, "action": "BUILD_CONTEXT", "description": "Build docs context", "reason": "r", "artifact_mode": "docs"},
            {"id": 3, "action": "EXPLAIN", "description": "Explain docs", "reason": "r", "artifact_mode": "docs"},
        ],
    )
    failed = {"id": 3, "action": "EXPLAIN", "description": "Explain docs", "reason": "r", "artifact_mode": "docs"}
    mock_response = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH", "description": "Locate something", "reason": "r"},'
        '{"id": 2, "action": "EDIT", "description": "Do edit", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "artifact_mode": "code", "description": "Explain in code lane", "reason": "r"}'
        ']}'
    )
    with (
        patch("agent.orchestrator.replanner.get_model_for_task", return_value=ModelType.REASONING),
        patch("agent.orchestrator.replanner.call_reasoning_model", return_value=mock_response),
    ):
        result = replan(state, failed_step=failed, error="boom")

    steps = result.get("steps") or []
    assert steps[0].get("action") == "SEARCH"
    assert "artifact_mode" not in steps[0]
    assert steps[1].get("action") == "EDIT"
    assert "artifact_mode" not in steps[1]
    assert steps[2].get("action") == "EXPLAIN"
    assert steps[2].get("artifact_mode") == "code"
