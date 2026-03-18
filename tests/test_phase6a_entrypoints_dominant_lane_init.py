from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_run_agent_initializes_dominant_lane_docs_for_docs_structured_plan():
    from agent.orchestrator import agent_loop as m

    docs_plan = {
        "plan_id": "p_docs",
        "steps": [
            {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", "description": "Find docs", "query": "readme", "reason": "r"},
            {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", "description": "Build", "reason": "r"},
            {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", "description": "Explain", "reason": "r"},
        ],
    }

    def fake_execution_loop(state, *_a, **_k):
        return SimpleNamespace(state=state, loop_output=None)

    with (
        pytest.warns(DeprecationWarning),
        patch.object(m, "get_plan", return_value=docs_plan),
        patch.object(m, "execution_loop", side_effect=fake_execution_loop),
        patch.object(m, "start_trace", return_value="t"),
        patch.object(m, "finish_trace", return_value=None),
        patch.object(m, "log_event", return_value=None),
    ):
        state = m.run_agent("where are readmes and docs")
    assert state.context.get("dominant_artifact_mode") == "docs"
    assert state.context.get("lane_violations") == []


def test_run_agent_initializes_dominant_lane_code_for_normal_plan():
    from agent.orchestrator import agent_loop as m

    code_plan = {
        "plan_id": "p_code",
        "steps": [{"id": 1, "action": "SEARCH", "description": "Find StepExecutor", "reason": "r"}],
    }

    def fake_execution_loop(state, *_a, **_k):
        return SimpleNamespace(state=state, loop_output=None)

    with (
        pytest.warns(DeprecationWarning),
        patch.object(m, "get_plan", return_value=code_plan),
        patch.object(m, "execution_loop", side_effect=fake_execution_loop),
        patch.object(m, "start_trace", return_value="t"),
        patch.object(m, "finish_trace", return_value=None),
        patch.object(m, "log_event", return_value=None),
    ):
        state = m.run_agent("where is StepExecutor implemented")
    assert state.context.get("dominant_artifact_mode") == "code"
    assert state.context.get("lane_violations") == []


def test_run_autonomous_initializes_dominant_lane_code_before_retrieve():
    import agent.autonomous.agent_loop as m

    captured = {"context": None}

    class FakeAgentState:
        def __init__(self, instruction, current_plan, context, **kwargs):
            captured["context"] = dict(context)
            self.instruction = instruction
            self.current_plan = current_plan
            self.context = context
            self.completed_steps = []
            self.step_results = []

        def record(self, *_a, **_k):
            return None

    def boom_retrieve(*_a, **_k):
        raise RuntimeError("stop_after_state_init")

    with (
        patch.object(m, "AgentState", FakeAgentState),
        patch.object(m, "retrieve", side_effect=boom_retrieve),
        patch.object(m, "start_trace", return_value="t"),
        patch.object(m, "finish_trace", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="stop_after_state_init"):
            m.run_autonomous("goal", project_root=".")

    assert captured["context"] is not None
    assert captured["context"].get("dominant_artifact_mode") == "code"
    assert captured["context"].get("lane_violations") == []


def test_workspace_from_goal_initializes_dominant_lane_code():
    from agent.roles.workspace import AgentWorkspace

    ws = AgentWorkspace.from_goal("goal", project_root=".", trace_id="t")
    assert ws.state.context.get("dominant_artifact_mode") == "code"
    assert ws.state.context.get("lane_violations") == []

