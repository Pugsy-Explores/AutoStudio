from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from agent.memory.state import AgentState
from agent.observability.trace_logger import add_event_listener, remove_event_listener, start_trace, finish_trace
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop
from planner.planner import plan as planner_plan


def _make_docs_fixtures(root: Path) -> None:
    (root / "README.md").write_text("# Project\n\nREADME text.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "architecture.md").write_text("# Architecture\n\nArchitecture docs.\n", encoding="utf-8")


def _state(plan_dict: dict, project_root: Path, trace_id: str) -> AgentState:
    plan = {**plan_dict, "plan_id": plan_dict.get("plan_id") or "plan_acceptance_001"}
    return AgentState(
        instruction="acceptance",
        current_plan=plan,
        context={
            "project_root": str(project_root),
            "trace_id": trace_id,
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
        },
    )


def test_acceptance_docs_style_emits_docs_mode_and_runs_docs_lane(tmp_path: Path):
    _make_docs_fixtures(tmp_path)

    # Mock planner LLM to emit docs-mode steps.
    mock_response = (
        '{"steps": ['
        '{"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "Find docs", "query": "readme docs", "reason": "r"},'
        '{"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "Build docs context", "reason": "r"},'
        '{"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "Explain docs", "reason": "r"}'
        ']}'
    )

    events: list[dict] = []

    def listener(trace_id: str, event_type: str, payload: dict | None):
        events.append({"type": event_type, "payload": payload or {}})

    add_event_listener(listener)
    try:
        with patch("planner.planner.call_reasoning_model", return_value=mock_response):
            plan_dict = planner_plan("where are readmes and docs")

        # Assert planner output has docs mode on the required steps.
        steps = plan_dict.get("steps") or []
        assert [s.get("action") for s in steps] == ["SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN"]
        assert all(s.get("artifact_mode") == "docs" for s in steps)

        trace_id = start_trace("phase5c_docs", str(tmp_path), query="where are readmes and docs")
        state = _state(plan_dict, tmp_path, trace_id)

        # Avoid external model dependency: stub EXPLAIN model call.
        with patch(
            "agent.execution.step_dispatcher.call_reasoning_model",
            return_value="This is a sufficiently long explanation output for validation.",
        ):
            # Use AGENT mode to avoid goal-evaluator replans that can overwrite state.context["artifact_mode"]
            # after the three planned docs steps are complete.
            result = execution_loop(state, "where are readmes and docs", trace_id=trace_id, mode=ExecutionLoopMode.AGENT)
        finish_trace(trace_id)

        assert result.state.context.get("artifact_mode") == "docs"
        assert any(e["type"].startswith("docs_") for e in events), "docs lane should emit docs_* trace events"
    finally:
        remove_event_listener(listener)


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

