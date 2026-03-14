"""Tests for agent/orchestrator/agent_controller."""

from pathlib import Path
from unittest.mock import patch

import pytest

from agent.orchestrator.agent_controller import run_controller


def test_run_controller_returns_summary(tmp_path):
    """run_controller returns task summary with task_id and instruction."""
    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": "Done", "reason": "test"},
            ],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            mock_dispatch.return_value = {"success": True, "output": "Done"}
            result = run_controller("Test instruction", project_root=str(tmp_path))

    assert "task_id" in result
    assert "instruction" in result
    assert result["instruction"] == "Test instruction"
    assert "completed_steps" in result
    assert "errors" in result


def test_run_controller_edit_flow_mocked(tmp_path):
    """run_controller handles EDIT step with mocked flow."""
    def mock_dispatch(step, state):
        action = (step.get("action") or "").upper()
        if action == "SEARCH":
            return {"success": True, "output": {"results": [{"file": "a.py", "snippet": "def foo"}]}}
        return {"success": True, "output": {}}

    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [
                {"id": 1, "action": "SEARCH", "description": "find foo", "reason": "r1"},
                {"id": 2, "action": "EDIT", "description": "modify foo", "reason": "r2"},
            ],
        }
        with patch("agent.orchestrator.agent_controller.dispatch", side_effect=mock_dispatch):
            with patch("agent.orchestrator.agent_controller._run_edit_flow") as mock_edit:
                mock_edit.return_value = {
                    "success": True,
                    "output": {"files_modified": [], "patches_applied": 0},
                }
                result = run_controller("Edit foo", project_root=str(tmp_path))

    assert "task_id" in result
    assert mock_edit.called
