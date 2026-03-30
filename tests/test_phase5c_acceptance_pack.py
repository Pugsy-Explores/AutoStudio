from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from planner.planner import plan as planner_plan


def test_acceptance_code_style_does_not_emit_docs_mode(tmp_path: Path):
    # Mock planner LLM to emit code-style steps (no artifact_mode).
    mock_response = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH_CANDIDATES", "description": "Locate StepExecutor", "query": "class StepExecutor", "reason": "r"},'
        '{"id": 2, "action": "BUILD_CONTEXT", "description": "Build context", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "description": "Explain StepExecutor", "reason": "r"}'
        ']}'
    )
    with patch("planner.planner.call_reasoning_model", return_value=mock_response):
        plan_dict = planner_plan("where is StepExecutor implemented")

    steps = plan_dict.get("steps") or []
    assert all("artifact_mode" not in s for s in steps)


def test_acceptance_planner_fallback_branches_lane_aware_with_explicit_docs_lineage():
    # Force a no-JSON planner response; lane selection must come only from retry_context lineage.
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
        out_docs = planner_plan("where are readmes and docs", retry_context=retry_context)
    steps = out_docs.get("steps") or []
    assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
    assert all(s.get("artifact_mode") == "docs" for s in steps)

    with patch("planner.planner.call_reasoning_model", return_value=""):
        out_code = planner_plan("normal code request", retry_context=None)
    steps2 = out_code.get("steps") or []
    assert len(steps2) == 1
    assert steps2[0].get("action") == "SEARCH"

