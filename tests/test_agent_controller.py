"""Tests for agent/orchestrator/agent_controller."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.orchestrator.agent_controller import run_controller


def test_run_controller_returns_summary(tmp_path):
    """run_controller returns task summary with task_id and instruction."""
    from agent.memory.state import AgentState

    def fake_run_deterministic(instruction, project_root, **kwargs):
        state = AgentState(
            instruction=instruction,
            current_plan={"steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}]},
            context={"project_root": project_root},
        )
        loop_output = {
            "completed_steps": [{"id": 1, "action": "EXPLAIN", "description": "Done"}],
            "patches_applied": [],
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 1,
            "plan_result": {"steps": []},
            "start_time": 0,
        }
        return state, loop_output

    with patch("repo_graph.repo_map_builder.build_repo_map", lambda *a, **k: None):
        with patch("agent.memory.task_index.search_similar_tasks", lambda *a, **k: []):
            with patch("agent.orchestrator.agent_controller.run_deterministic", side_effect=fake_run_deterministic):
                with patch("agent.orchestrator.agent_controller.save_task", lambda *a, **k: None):
                    result = run_controller("Test instruction", project_root=str(tmp_path))

    assert "task_id" in result
    assert "instruction" in result
    assert result["instruction"] == "Test instruction"
    assert "completed_steps" in result
    assert "errors" in result


def test_run_controller_edit_flow_mocked(tmp_path):
    """run_controller handles EDIT step via dispatch (all tools through dispatch)."""
    def mock_dispatch(step, state):
        action = (step.get("action") or "").upper()
        if action == "SEARCH":
            return {"success": True, "output": {"results": [{"file": "a.py", "snippet": "def foo"}]}}
        if action == "EDIT":
            return {"success": True, "output": {"files_modified": [], "patches_applied": 0}}
        return {"success": True, "output": {}}

    with patch("repo_graph.repo_map_builder.build_repo_map", lambda *a, **k: None):
        with patch("agent.memory.task_index.search_similar_tasks", lambda *a, **k: []):
            with patch("agent.orchestrator.deterministic_runner.get_plan") as mock_plan:
                mock_plan.return_value = {
                    "steps": [
                        {"id": 1, "action": "SEARCH", "description": "find foo", "reason": "r1"},
                        {"id": 2, "action": "EDIT", "description": "modify foo", "reason": "r2"},
                    ],
                }
                with patch("agent.orchestrator.deterministic_runner.dispatch", side_effect=mock_dispatch) as mock_dispatch_patch:
                    result = run_controller("Edit foo", project_root=str(tmp_path))

    assert "task_id" in result
    assert mock_dispatch_patch.called


def test_run_controller_with_instruction_router_skips_planner_for_search(tmp_path):
    """When instruction router is enabled and routes to CODE_SEARCH, planner (plan) is not called."""
    from agent.routing.instruction_router import RouterDecision

    with patch("repo_graph.repo_map_builder.build_repo_map", lambda *a, **k: None):
        with patch("agent.memory.task_index.search_similar_tasks", lambda *a, **k: []):
            with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", True):
                with patch("agent.routing.instruction_router.route_instruction") as mock_route:
                    mock_route.return_value = RouterDecision(category="CODE_SEARCH", confidence=0.9)
                    with patch("planner.planner.plan") as mock_planner:
                        with patch("agent.orchestrator.deterministic_runner.dispatch") as mock_dispatch:
                            mock_dispatch.return_value = {
                                "success": True,
                                "output": {"results": [{"file": "a.py", "snippet": "x"}]},
                            }
                            result = run_controller(
                                "Find where password hashing is implemented",
                                project_root=str(tmp_path),
                            )
    assert mock_route.called
    assert not mock_planner.called, "Planner should be skipped when router returns CODE_SEARCH"
    assert "task_id" in result
