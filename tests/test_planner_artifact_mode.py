from unittest.mock import patch

from planner.planner import plan
from planner.planner_utils import normalize_actions, validate_plan


def test_validate_plan_accepts_artifact_mode_docs():
    data = {
        "steps": [
            {"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r", "artifact_mode": "docs"},
            {"id": 2, "action": "BUILD_CONTEXT", "description": "Build docs context", "reason": "r", "artifact_mode": "docs"},
            {"id": 3, "action": "EXPLAIN", "description": "Explain", "reason": "r", "artifact_mode": "docs"},
        ]
    }
    data = normalize_actions(data)
    assert validate_plan(data) is True


def test_validate_plan_rejects_invalid_artifact_mode():
    data = {"steps": [{"id": 1, "action": "SEARCH", "description": "x", "reason": "r", "artifact_mode": "nope"}]}
    data = normalize_actions(data)
    assert validate_plan(data) is False


def test_planner_fallback_on_invalid_artifact_mode_from_llm():
    # LLM returns a plan that includes invalid artifact_mode.
    mock_response = (
        '{"steps": [{"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r",'
        ' "query": "readme", "artifact_mode": "nope"}]}'
    )
    with patch("planner.planner.call_reasoning_model", return_value=mock_response):
        out = plan("where are readmes and docs")
    assert out["steps"][0]["action"] == "SEARCH"
    assert "Validation failed" in (out.get("error") or "")


def test_planner_docs_shaped_fallback_when_retry_context_has_docs_lineage():
    # Invalid planner output, but retry_context contains an explicit docs-lane prior plan.
    mock_response = (
        '{"steps": [{"id": 1, "action": "SEARCH_CANDIDATES", "description": "Find docs", "reason": "r",'
        ' "query": "readme", "artifact_mode": "nope"}]}'
    )
    retry_context = {
        "previous_attempts": [
            {
                "plan": {
                    "plan_id": "plan_docs_prev",
                    "steps": [
                        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "d", "reason": "r"},
                    ],
                }
            }
        ]
    }
    with patch("planner.planner.call_reasoning_model", return_value=mock_response):
        out = plan("where are readmes and docs", retry_context=retry_context)

    steps = out.get("steps") or []
    assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
    assert all(s.get("artifact_mode") == "docs" for s in steps)
    assert "Validation failed" in (out.get("error") or "")


def test_planner_code_fallback_shape_when_no_docs_lineage():
    # Invalid planner output and no explicit docs lineage -> keep single SEARCH fallback.
    mock_response = '{"steps": [{"id": 1, "action": "SEARCH", "description": "x", "reason": "r", "artifact_mode": "nope"}]}'
    with patch("planner.planner.call_reasoning_model", return_value=mock_response):
        out = plan("some normal code request")
    steps = out.get("steps") or []
    assert len(steps) == 1
    assert steps[0].get("action") == "SEARCH"


def test_planner_no_json_fallback_is_docs_shaped_with_docs_lineage():
    retry_context = {
        "previous_attempts": [
            {
                "plan": {
                    "plan_id": "plan_docs_prev",
                    "steps": [
                        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "d", "reason": "r"},
                    ],
                }
            }
        ]
    }
    with patch("planner.planner.call_reasoning_model", return_value=""):
        out = plan("where are readmes and docs", retry_context=retry_context)
    steps = out.get("steps") or []
    assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
    assert all(s.get("artifact_mode") == "docs" for s in steps)
    assert "No JSON found" in (out.get("error") or "")


def test_planner_no_json_fallback_is_search_without_docs_lineage():
    with patch("planner.planner.call_reasoning_model", return_value=""):
        out = plan("normal code request")
    steps = out.get("steps") or []
    assert len(steps) == 1
    assert steps[0].get("action") == "SEARCH"
    assert "No JSON found" in (out.get("error") or "")


def test_planner_model_exception_fallback_is_docs_shaped_with_docs_lineage():
    retry_context = {
        "previous_attempts": [
            {
                "plan": {
                    "plan_id": "plan_docs_prev",
                    "steps": [
                        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "d", "reason": "r"},
                    ],
                }
            }
        ]
    }
    with patch("planner.planner.call_reasoning_model") as mock:
        mock.side_effect = RuntimeError("planner down")
        out = plan("where are readmes and docs", retry_context=retry_context)
    steps = out.get("steps") or []
    assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
    assert all(s.get("artifact_mode") == "docs" for s in steps)
    assert "planner down" in (out.get("error") or "")


def test_planner_invalid_json_fallback_is_docs_shaped_with_docs_lineage():
    retry_context = {
        "previous_attempts": [
            {
                "plan": {
                    "plan_id": "plan_docs_prev",
                    "steps": [
                        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "d", "reason": "r"},
                        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "d", "reason": "r"},
                    ],
                }
            }
        ]
    }
    # Has braces so _extract_json finds it, but json.loads fails.
    with patch("planner.planner.call_reasoning_model", return_value="{bad json}"):
        out = plan("where are readmes and docs", retry_context=retry_context)
    steps = out.get("steps") or []
    assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
    assert all(s.get("artifact_mode") == "docs" for s in steps)

