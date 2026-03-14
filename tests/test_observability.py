"""Tests for agent/observability: trace creation and content."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.orchestrator.agent_controller import run_controller


def test_run_controller_creates_trace_file(tmp_path):
    """run_controller creates a trace file in .agent_memory/traces/."""
    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "EXPLAIN", "description": "Done", "reason": "test"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "output": "Done"}
            result = run_controller("Test instruction", project_root=str(tmp_path))

    traces_dir = tmp_path / ".agent_memory" / "traces"
    assert traces_dir.exists(), "traces dir should exist"
    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1, "at least one trace file should exist"
    assert result["task_id"] in str(trace_files[0].name)


def test_trace_contains_plan(tmp_path):
    """Trace contains planner_decision event with plan."""
    plan_result = {"steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}]}
    with patch("agent.orchestrator.agent_controller.plan", return_value=plan_result):
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "output": "Done"}
            run_controller("Test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    events = data.get("events", [])
    planner_events = [e for e in events if e["type"] == "planner_decision"]
    assert len(planner_events) == 1
    assert "plan" in planner_events[0]["payload"]
    assert planner_events[0]["payload"]["plan"] == plan_result


def test_trace_contains_tool_calls(tmp_path):
    """Trace step_executed events include tool (chosen_tool)."""
    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "SEARCH", "description": "find foo"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            def capture_dispatch(step, state):
                state.context["chosen_tool"] = "search_code"
                return {"success": True, "output": {"results": []}}

            mock_dispatch.side_effect = capture_dispatch
            run_controller("Search test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    step_events = [e for e in data["events"] if e["type"] == "step_executed"]
    assert len(step_events) >= 1
    payload = step_events[0]["payload"]
    assert "tool" in payload
    assert "action" in payload
    assert "success" in payload


def test_trace_contains_errors_on_failure(tmp_path):
    """Trace can record error events (trace_logger). Controller logs errors when steps fail."""
    from agent.observability.trace_logger import finish_trace, log_event, start_trace

    trace_id = start_trace("test-task", str(tmp_path))
    log_event(trace_id, "error", {"step_id": 1, "action": "EXPLAIN", "error": "mock_error"})
    path = finish_trace(trace_id)

    assert path
    data = json.loads(Path(path).read_text())
    error_events = [e for e in data["events"] if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["payload"]["error"] == "mock_error"


def test_trace_contains_patch_results_on_edit(tmp_path):
    """Trace contains patch_result event when EDIT step applies patches."""
    (tmp_path / "foo.py").write_text("x = 1")
    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "EDIT", "description": "edit foo", "reason": "r1"}],
        }
        with patch("agent.orchestrator.agent_controller._run_edit_flow") as mock_edit:
            mock_edit.return_value = {
                "success": True,
                "output": {
                    "files_modified": ["foo.py"],
                    "patches_applied": 1,
                },
            }
            run_controller("Edit test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    patch_events = [e for e in data["events"] if e["type"] == "patch_result"]
    assert len(patch_events) >= 1
    payload = patch_events[0]["payload"]
    assert "patches_applied" in payload
    assert "files_modified" in payload
    assert payload["files_modified"] == ["foo.py"]


def test_trace_task_complete_has_summary(tmp_path):
    """Trace task_complete event includes errors and patches summary."""
    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "output": "Done"}
            run_controller("Test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    complete_events = [e for e in data["events"] if e["type"] == "task_complete"]
    assert len(complete_events) == 1
    payload = complete_events[0]["payload"]
    assert "task_id" in payload
    assert "completed_steps" in payload
    assert "errors" in payload
    assert "patches_applied" in payload
    assert "files_modified" in payload
