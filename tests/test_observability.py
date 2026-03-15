"""Tests for agent/observability: trace creation and content."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.observability.trace_logger import (
    finish_trace,
    log_event,
    log_stage,
    start_trace,
    trace_stage,
)
from agent.orchestrator.agent_controller import run_controller

ROOT = Path(__file__).resolve().parent.parent


def test_run_controller_creates_trace_file(tmp_path):
    """run_controller creates a trace file in .agent_memory/traces/."""
    with patch("agent.orchestrator.agent_controller.get_plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "EXPLAIN", "description": "Done", "reason": "test"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {
                "success": True,
                "output": "StepExecutor in agent/execution/executor.py handles step execution.",
            }
            result = run_controller("Test instruction", project_root=str(tmp_path))

    traces_dir = tmp_path / ".agent_memory" / "traces"
    assert traces_dir.exists(), "traces dir should exist"
    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1, "at least one trace file should exist"
    assert result["task_id"] in str(trace_files[0].name)


def test_trace_contains_plan(tmp_path):
    """Trace contains planner_decision event with plan."""
    plan_result = {"steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}]}
    with patch("agent.orchestrator.agent_controller.get_plan", return_value=plan_result):
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {
                "success": True,
                "output": "StepExecutor in agent/execution/executor.py handles step execution.",
            }
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
    with patch("agent.orchestrator.agent_controller.get_plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "SEARCH", "description": "find foo"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            def capture_dispatch(step, state):
                state.context["chosen_tool"] = "search_code"
                return {"success": True, "output": {"results": [{"file": "foo.py", "snippet": "x"}]}}

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


def test_log_stage_appends_to_trace(tmp_path):
    """start_trace -> log_stage -> finish_trace produces trace file with stages array."""
    trace_id = start_trace("test-task", str(tmp_path))
    log_stage(trace_id, "planner", 100.5, step_id=None, summary={"number_of_steps": 2})
    path = finish_trace(trace_id)
    assert path
    data = json.loads(Path(path).read_text())
    stages = data.get("stages", [])
    assert len(stages) == 1
    assert stages[0]["stage"] == "planner"
    assert stages[0]["latency_ms"] == 100.5
    assert stages[0]["summary"]["number_of_steps"] == 2


def test_log_stage_structure(tmp_path):
    """Each stage entry has step_id, stage, latency_ms, summary."""
    trace_id = start_trace("test-task", str(tmp_path))
    log_stage(trace_id, "retrieval", 85.0, step_id=1, summary={"query": "foo", "results": 5})
    path = finish_trace(trace_id)
    data = json.loads(Path(path).read_text())
    s = data["stages"][0]
    assert "step_id" in s
    assert s["stage"] == "retrieval"
    assert "latency_ms" in s
    assert "summary" in s
    assert s["summary"]["results"] == 5


def test_trace_stage_context_manager(tmp_path):
    """trace_stage context manager populates summary and logs stage."""
    trace_id = start_trace("test-task", str(tmp_path))
    with trace_stage(trace_id, "planner") as summary:
        summary["actions"] = ["SEARCH", "EXPLAIN"]
    path = finish_trace(trace_id)
    data = json.loads(Path(path).read_text())
    stages = data["stages"]
    assert len(stages) == 1
    assert stages[0]["stage"] == "planner"
    assert stages[0]["summary"]["actions"] == ["SEARCH", "EXPLAIN"]
    assert "latency_ms" in stages[0]


def test_trace_includes_query_when_provided(tmp_path):
    """start_trace(..., query='foo') stores query in output JSON."""
    trace_id = start_trace("test-task", str(tmp_path), query="Explain StepExecutor")
    finish_trace(trace_id)
    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    assert data.get("query") == "Explain StepExecutor"


def test_replay_script_loads_trace(tmp_path):
    """replay_trace.py loads fixture trace and exits 0."""
    fixture = ROOT / "tests" / "fixtures" / "trace_sample.json"
    if not fixture.exists():
        pytest.skip("fixture trace_sample.json not found")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "replay_trace.py"), str(fixture), "--mode", "print"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Trace:" in result.stdout
    assert "planner" in result.stdout or "retrieval" in result.stdout


def test_controller_trace_has_planner_stage(tmp_path):
    """run_controller produces trace with planner stage (plan_resolver uses trace_stage)."""
    plan_result = {"steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}]}
    with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", False):
        with patch("planner.planner.plan") as mock_plan_fn:
            mock_plan_fn.return_value = plan_result
            with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
                mock_dispatch.return_value = {
                    "success": True,
                    "output": "StepExecutor in agent/execution/executor.py handles step execution.",
                }
                run_controller("Test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    data = json.loads(trace_file.read_text())
    stages = data.get("stages", [])
    planner_stages = [s for s in stages if s.get("stage") == "planner"]
    assert len(planner_stages) >= 1, "trace should have at least one planner stage"


def test_trace_does_not_exceed_size(tmp_path):
    """Trace file stays under ~500 KB."""
    plan_result = {"steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}]}
    with patch("agent.orchestrator.agent_controller.get_plan") as mock_plan:
        mock_plan.return_value = plan_result
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {
                "success": True,
                "output": "StepExecutor in agent/execution/executor.py handles step execution.",
            }
            run_controller("Test", project_root=str(tmp_path))

    trace_file = next((tmp_path / ".agent_memory" / "traces").glob("*.json"))
    size_bytes = trace_file.stat().st_size
    assert size_bytes < 600 * 1024, f"trace file {size_bytes} bytes should be under ~500 KB"


def test_trace_contains_errors_on_failure(tmp_path):
    """Trace can record error events (trace_logger). Controller logs errors when steps fail."""
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
    with patch("agent.orchestrator.agent_controller.get_plan") as mock_plan:
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
    with patch("agent.orchestrator.agent_controller.get_plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {
                "success": True,
                "output": "StepExecutor in agent/execution/executor.py handles step execution.",
            }
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


