"""Dispatcher fault_hooks: env-driven open_file failures (Phase 6/7 test support)."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from agent_v2.runtime.dispatcher import Dispatcher
from agent_v2.runtime.fault_hooks import (
    _META_INJECT_COUNT,
    maybe_inject_open_file_fault_raw,
    synthetic_open_file_failure,
)
from agent_v2.runtime.plan_executor import PlanExecutor
from agent_v2.runtime.replanner import Replanner
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
)
from agent_v2.state.agent_state import AgentState


def _dispatcher(execute_fn):
    """Avoid lazy primitives import (circular import in some test orders)."""
    return Dispatcher(
        execute_fn=execute_fn,
        shell=MagicMock(),
        editor=MagicMock(),
        browser=MagicMock(),
    )


def _open_step(sid: str = "s2") -> dict:
    return {
        "id": 1,
        "step_id": sid,
        "_react_action_raw": "open_file",
        "action": "READ",
        "_react_args": {"path": "x.py"},
    }


def test_maybe_inject_returns_none_without_env():
    st = AgentState(instruction="t")
    st.context = {}
    assert maybe_inject_open_file_fault_raw("open_file", _open_step(), st) is None


def test_maybe_inject_ignores_non_open_file():
    st = AgentState(instruction="t")
    st.context = {}
    step = {"_react_action_raw": "search", "step_id": "s1", "action": "SEARCH"}
    assert maybe_inject_open_file_fault_raw("search", step, st) is None


@dataclass
class _ScratchState:
    """Like ExplorationRunner scratch state — must not receive plan-executor faults."""

    context: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


def test_maybe_inject_skips_non_agent_state(monkeypatch):
    monkeypatch.setenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", "1")
    scratch = _ScratchState()
    assert maybe_inject_open_file_fault_raw("open_file", _open_step(), scratch) is None


def test_synthetic_open_file_failure_shape():
    d = synthetic_open_file_failure()
    assert d["success"] is False
    assert "error" in d


@pytest.mark.parametrize(
    "env_name",
    ["AGENT_V2_FAULT_OPEN_FILE_ONCE", "AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN"],
)
def test_dispatcher_open_file_once_skips_execute_fn_first_call(monkeypatch, env_name):
    monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", raising=False)
    monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", raising=False)
    monkeypatch.setenv(env_name, "1")

    calls: list[int] = []

    def execute_fn(step, state):
        calls.append(1)
        return {"success": True, "output": {"file_path": "/ok.py"}, "error": None}

    d = _dispatcher(execute_fn)
    st = AgentState(instruction="t")
    st.context = {}
    st.metadata = {}

    r1 = d.execute(_open_step(), st)
    assert r1.success is False
    assert calls == []

    if env_name == "AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN":
        r2 = d.execute(_open_step(), st)
        assert r2.success is False
        assert calls == []
        st.metadata["replan_attempt"] = 1
        r3 = d.execute(_open_step(), st)
        assert r3.success is True
        assert calls == [1]
    else:
        r2 = d.execute(_open_step(), st)
        assert r2.success is True
        assert calls == [1]

    assert int(st.metadata.get(_META_INJECT_COUNT, 0)) >= 1


def test_hard_second_step_id_succeeds_when_first_bound(monkeypatch):
    monkeypatch.setenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", "1")

    calls: list[str] = []

    def execute_fn(step, state):
        calls.append(str(step.get("step_id")))
        return {"success": True, "output": {"file_path": "/ok.py"}, "error": None}

    d = _dispatcher(execute_fn)
    st = AgentState(instruction="t")
    st.context = {}
    st.metadata = {}

    assert d.execute(_open_step("first"), st).success is False
    assert d.execute(_open_step("second"), st).success is True
    assert calls == ["second"]


def test_plan_executor_retries_open_file_after_once_fault(monkeypatch):
    monkeypatch.setenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", "1")
    monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", raising=False)

    def execute_fn(step, state):
        act = (step.get("_react_action_raw") or "").lower()
        if act == "search":
            return {"success": True, "output": {"results": [{"file": "a.py"}]}, "error": None}
        return {"success": True, "output": {"file_path": "a.py"}, "error": None}

    dispatch = _dispatcher(execute_fn)
    arg_gen = MagicMock()

    def _gen(step, state):
        if step.action == "search":
            return {"query": "q"}
        return {"path": "a.py"}

    arg_gen.generate.side_effect = _gen
    plan = PlanDocument(
        plan_id="pfault",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="s1",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={"query": "q"},
                execution=PlanStepExecution(),
            ),
            PlanStep(
                step_id="s2",
                index=2,
                type="analyze",
                goal="g",
                action="open_file",
                dependencies=["s1"],
                inputs={"path": "a.py"},
                execution=PlanStepExecution(),
            ),
            PlanStep(
                step_id="s3",
                index=3,
                type="finish",
                goal="done",
                action="finish",
                dependencies=["s2"],
                execution=PlanStepExecution(),
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )
    state = AgentState(instruction="i")
    state.current_plan = plan.model_dump(mode="json")
    ex = PlanExecutor(dispatch, arg_gen)
    out = ex.run(plan, state)
    assert out["status"] == "success"
    open_step = next(s for s in plan.steps if s.action == "open_file")
    assert open_step.execution.attempts >= 2
    assert int(state.metadata.get(_META_INJECT_COUNT, 0)) >= 1


def test_plan_executor_hard_open_triggers_replan_then_succeeds(monkeypatch):
    monkeypatch.setenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", "1")
    monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", raising=False)

    def execute_fn(step, state):
        act = (step.get("_react_action_raw") or "").lower()
        if act == "search":
            return {"success": True, "output": {"results": []}, "error": None}
        return {"success": True, "output": {"file_path": "a.py"}, "error": None}

    dispatch = _dispatcher(execute_fn)

    def _gen(step, state):
        if step.action == "search":
            return {"query": "q"}
        return {"path": "a.py"}

    arg_gen = MagicMock()
    arg_gen.generate.side_effect = _gen

    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    meta = PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1)
    risk = PlanRisk(risk="r", impact="low", mitigation="m")

    initial = PlanDocument(
        plan_id="p_init",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="xs",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={"query": "q"},
                execution=PlanStepExecution(max_attempts=policy.max_retries_per_step),
            ),
            PlanStep(
                step_id="xo",
                index=2,
                type="analyze",
                goal="g",
                action="open_file",
                dependencies=["xs"],
                inputs={"path": "a.py"},
                execution=PlanStepExecution(max_attempts=policy.max_retries_per_step),
            ),
            PlanStep(
                step_id="xf",
                index=3,
                type="finish",
                goal="done",
                action="finish",
                dependencies=["xo"],
                execution=PlanStepExecution(max_attempts=policy.max_retries_per_step),
            ),
        ],
        risks=[risk],
        completion_criteria=["c"],
        metadata=meta,
    )
    recovery = PlanDocument(
        plan_id="p_rec",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="rs",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={"query": "q"},
                execution=PlanStepExecution(max_attempts=policy.max_retries_per_step),
            ),
            PlanStep(
                step_id="rf",
                index=2,
                type="finish",
                goal="done",
                action="finish",
                dependencies=["rs"],
                execution=PlanStepExecution(max_attempts=policy.max_retries_per_step),
            ),
        ],
        risks=[risk],
        completion_criteria=["c"],
        metadata=meta,
    )

    mock_planner = MagicMock()
    mock_planner.plan.return_value = recovery
    replanner = Replanner(mock_planner, policy=policy)
    ex = PlanExecutor(dispatch, arg_gen, replanner=replanner, policy=policy)

    state = AgentState(instruction="task")
    state.current_plan = initial.model_dump(mode="json")
    state.context["exploration_result"] = {"summary": {"key_findings": [], "knowledge_gaps": []}}

    out = ex.run(initial, state)
    assert out["status"] == "success"
    assert state.metadata.get("replan_attempt") == 1
    mock_planner.plan.assert_called_once()
    assert int(state.metadata.get(_META_INJECT_COUNT, 0)) >= 2
