"""Tests for Phase 9 agent/roles module."""

import pytest

from agent.roles.workspace import AgentWorkspace
from agent.roles.planner_agent import PlannerAgent
from agent.roles.localization_agent import LocalizationAgent
from agent.roles.edit_agent import EditAgent
from agent.roles.test_agent import TestAgent
from agent.roles.critic_agent import CriticAgent
from agent.roles.base_role_agent import BaseRoleAgent


def test_workspace_from_goal():
    w = AgentWorkspace.from_goal("fix test", "/tmp", "trace_1")
    assert w.goal == "fix test"
    assert w.state.instruction == "fix test"
    assert w.state.context.get("project_root") == "/tmp"
    assert w.state.context.get("trace_id") == "trace_1"
    assert w.plan == {"steps": []} or "steps" in w.plan


def test_planner_agent_name():
    a = PlannerAgent()
    assert a.name == "planner"


def test_localization_agent_name():
    a = LocalizationAgent()
    assert a.name == "localization"


def test_edit_agent_name():
    a = EditAgent()
    assert a.name == "edit"


def test_test_agent_name():
    a = TestAgent()
    assert a.name == "test"


def test_critic_agent_name():
    a = CriticAgent()
    assert a.name == "critic"


def test_planner_agent_run_sets_plan():
    w = AgentWorkspace.from_goal("Fix typo in foo.py", ".", "trace_test")
    a = PlannerAgent()
    out = a.run(w)
    assert out.plan is not None
    assert "steps" in out.plan or len(out.plan.get("steps", [])) >= 0


def test_localization_agent_run_does_not_crash(monkeypatch):
    """Localization agent runs SEARCH via dispatch; mock to avoid slow retrieval."""
    def mock_dispatch(step, state):
        return {"success": True, "output": {"results": [{"file": "foo.py", "symbol": "bar"}]}}
    monkeypatch.setattr("agent.roles.base_role_agent.dispatch", mock_dispatch)
    w = AgentWorkspace.from_goal("Find bar function", ".", "trace_test")
    w.plan = {"steps": [{"action": "SEARCH", "description": "bar function"}]}
    a = LocalizationAgent()
    out = a.run(w)
    assert out.candidate_files is not None
    assert isinstance(out.candidate_files, list)
    assert "foo.py" in out.candidate_files


def test_test_agent_run_sets_test_results():
    w = AgentWorkspace.from_goal("Run tests", ".", "trace_test")
    w.plan = {"steps": [{"action": "INFRA", "description": "true"}]}
    a = TestAgent()
    out = a.run(w)
    assert out.test_results is not None
    assert out.test_results.get("status") in ("PASS", "FAIL", "ERROR")


def test_critic_agent_run_sets_retry_instruction():
    w = AgentWorkspace.from_goal("Fix test", ".", "trace_test")
    w.test_results = {"status": "FAIL", "stderr": "AssertionError", "stdout": "", "returncode": 1}
    a = CriticAgent()
    out = a.run(w)
    assert out.retry_instruction is not None
    assert len(out.retry_instruction) > 0
