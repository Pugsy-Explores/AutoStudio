"""PR3+PR4+PR5: Stage 2 detection, subgoals, plan construction, evaluator, and phase loop tests."""

from unittest.mock import MagicMock, patch

import pytest

from agent.memory.state import AgentState
from agent.orchestrator.deterministic_runner import (
    _aggregate_parent_goal,
    _build_phase_context_handoff,
    _derive_phase_failure_class,
    run_hierarchical,
)
from agent.orchestrator.goal_evaluator import GoalEvaluator, is_explain_like_instruction
from agent.orchestrator.plan_resolver import (
    _build_two_phase_parent_plan,
    _derive_phase_subgoals,
    _is_two_phase_docs_code_intent,
    get_parent_plan,
)


# --- Detection heuristic tests ---


def test_fires_on_find_docs_and_explain():
    assert _is_two_phase_docs_code_intent("Find architecture docs and explain replanner flow") is True


def test_fires_on_show_readme_and_explain():
    assert _is_two_phase_docs_code_intent("Show me the README and explain how the planner works") is True


def test_fires_on_locate_docs_and_flow():
    assert _is_two_phase_docs_code_intent("Locate the architecture docs and describe the flow") is True


def test_does_not_fire_on_pure_docs():
    assert _is_two_phase_docs_code_intent("Find the architecture docs") is False


def test_does_not_fire_on_pure_code():
    assert _is_two_phase_docs_code_intent("Explain the replanner flow") is False


def test_does_not_fire_on_edit():
    assert _is_two_phase_docs_code_intent("Edit validate_plan to add a type hint") is False


def test_does_not_fire_on_symbol_only():
    assert _is_two_phase_docs_code_intent("validate_plan") is False


def test_does_not_fire_on_docs_implemented():
    assert _is_two_phase_docs_code_intent("find docs for the implemented approach") is False


def test_does_not_fire_on_no_discovery_verb():
    assert _is_two_phase_docs_code_intent("The docs explain the replanner flow") is False


def test_does_not_fire_on_blank_instruction():
    assert _is_two_phase_docs_code_intent("") is False
    assert _is_two_phase_docs_code_intent("   ") is False


# --- Subgoal derivation tests ---


def test_derive_subgoals_standard_pattern():
    phase0, phase1 = _derive_phase_subgoals("Find architecture docs and explain replanner flow")
    assert phase0 == "Find documentation artifacts relevant to: Find architecture docs and explain replanner flow"
    assert phase1 == "Replanner flow"


def test_derive_subgoals_no_connector_fallback():
    phase0, phase1 = _derive_phase_subgoals("Find docs flow explain")
    assert phase0 == "Find documentation artifacts relevant to: Find docs flow explain"
    assert phase1 == "Find docs flow explain"


def test_derive_subgoals_describe_variant():
    phase0, phase1 = _derive_phase_subgoals("find docs and describe the planner")
    assert phase0 == "Find documentation artifacts relevant to: find docs and describe the planner"
    assert phase1 == "The planner"


def test_derive_subgoals_show_how_variant():
    phase0, phase1 = _derive_phase_subgoals("Find README and show how validate_plan works")
    assert phase0.startswith("Find documentation artifacts relevant to:")
    assert phase1 == "Validate_plan works"


def test_derive_subgoals_short_tail_falls_back_to_full_instruction():
    phase0, phase1 = _derive_phase_subgoals("Find docs and explain it")
    assert phase0.startswith("Find documentation artifacts relevant to:")
    assert phase1 == "Find docs and explain it"


def test_derive_subgoals_blank_instruction():
    phase0, phase1 = _derive_phase_subgoals("")
    assert phase0 == "Find documentation artifacts relevant to: "
    assert phase1 == ""


def test_derive_subgoals_phase0_starts_with_find_documentation():
    phase0, _ = _derive_phase_subgoals("anything here")
    assert phase0.startswith("Find documentation")


def test_derive_subgoals_phase1_non_empty_when_source_present():
    _, phase1 = _derive_phase_subgoals("Find docs and explain the flow")
    assert len(phase1) > 0


# --- Plan construction tests (PR4) ---


def test_build_two_phase_structure(monkeypatch):
    """Returns ParentPlan with 2 phases, compatibility_mode=False, decomposition_type=two_phase_docs_code."""
    def mock_plan(subgoal):
        return {
            "steps": [
                {"id": 1, "action": "EXPLAIN", "description": subgoal, "reason": "mocked"},
            ],
            "plan_id": "plan_mock",
        }
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
    assert len(parent["phases"]) == 2
    assert parent["compatibility_mode"] is False
    assert parent["decomposition_type"] == "two_phase_docs_code"


def test_build_two_phase_phase0_lane_docs(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    parent = _build_two_phase_parent_plan("Find docs and explain the flow")
    assert parent["phases"][0]["lane"] == "docs"


def test_build_two_phase_phase1_lane_code(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    parent = _build_two_phase_parent_plan("Find docs and explain the flow")
    assert parent["phases"][1]["lane"] == "code"


def test_build_two_phase_phase0_validate_plan_passes(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    from planner.planner_utils import validate_plan

    parent = _build_two_phase_parent_plan("Find docs and explain the flow")
    assert validate_plan({"steps": parent["phases"][0]["steps"]}) is True


def test_build_two_phase_phase1_validate_plan_passes(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    from planner.planner_utils import validate_plan

    parent = _build_two_phase_parent_plan("Find docs and explain the flow")
    assert validate_plan({"steps": parent["phases"][1]["steps"]}) is True


def test_build_two_phase_phase0_subgoal_is_phase_scoped(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    parent = _build_two_phase_parent_plan("Find docs and explain the flow")
    assert parent["phases"][0]["subgoal"].startswith("Find documentation")


def test_build_two_phase_phase1_subgoal_is_phase_scoped(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    instruction = "Find architecture docs and explain replanner flow"
    parent = _build_two_phase_parent_plan(instruction)
    phase1_subgoal = parent["phases"][1]["subgoal"]
    assert phase1_subgoal != instruction
    assert "replanner" in phase1_subgoal.lower() or "flow" in phase1_subgoal.lower()


def test_build_two_phase_parent_instruction_preserved(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    instruction = "Find architecture docs and explain replanner flow"
    parent = _build_two_phase_parent_plan(instruction)
    assert parent["instruction"] == instruction


def test_get_parent_plan_mixed_fires_two_phase(monkeypatch):
    def mock_plan(subgoal):
        return {"steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)

    parent = get_parent_plan("Find architecture docs and explain replanner flow")
    assert parent["compatibility_mode"] is False
    assert len(parent["phases"]) == 2


def test_get_parent_plan_pure_code_stays_compat():
    parent = get_parent_plan("Explain the replanner flow")
    assert parent["compatibility_mode"] is True
    assert len(parent["phases"]) == 1


def test_get_parent_plan_pure_docs_stays_compat():
    parent = get_parent_plan("Find the architecture docs")
    assert parent["compatibility_mode"] is True
    assert len(parent["phases"]) == 1


def test_build_two_phase_fallback_on_bad_planner_output(monkeypatch):
    """When plan() returns steps that fail validate_plan, get_parent_plan falls back to compat mode."""
    def mock_plan_bad(subgoal):
        return {"steps": [{"id": 1, "action": "INVALID_ACTION", "description": subgoal}], "plan_id": "p"}
    monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan_bad)

    log_events = []
    def log_fn(trace_id, event, payload):
        log_events.append((event, payload))

    parent = get_parent_plan(
        "Find architecture docs and explain replanner flow",
        trace_id="t1",
        log_event_fn=log_fn,
    )
    assert parent["compatibility_mode"] is True
    assert len(parent["phases"]) == 1
    assert any(e[0] == "two_phase_fallback" for e in log_events)


# --- Evaluator tests (PR4) ---


def test_evaluate_with_reason_no_phase_subgoal_backward_compat():
    """Without phase_subgoal, output is identical to pre-feature behavior."""
    evaluator = GoalEvaluator()
    state = AgentState(
        instruction="explain something",
        current_plan={"plan_id": "p", "steps": []},
        context={},
        step_results=[
            type("SR", (), {"action": "EXPLAIN", "success": True, "patch_size": 0, "files_modified": []})(),
        ],
    )
    a = evaluator.evaluate_with_reason("explain something", state)
    b = evaluator.evaluate_with_reason("explain something", state)
    assert a == b
    assert a[0] is True
    assert a[1] == "explain_like_explain_succeeded"


def test_evaluate_with_reason_phase_subgoal_used_for_explain_like():
    """With phase_subgoal, is_explain_like is evaluated against phase_subgoal."""
    evaluator = GoalEvaluator()
    state = AgentState(
        instruction="Find docs and explain flow",
        current_plan={"plan_id": "p", "steps": []},
        context={},
        step_results=[
            type("SR", (), {"action": "EXPLAIN", "success": True, "patch_size": 0, "files_modified": []})(),
        ],
    )
    met, reason, signals = evaluator.evaluate_with_reason(
        "Find docs and explain flow",
        state,
        phase_subgoal="Explain replanner flow",
    )
    assert met is True
    assert is_explain_like_instruction("Explain replanner flow") is True


def test_evaluate_with_reason_phase_subgoal_none_uses_instruction():
    """When phase_subgoal is None, instruction is used (same as no kwarg)."""
    evaluator = GoalEvaluator()
    state = AgentState(
        instruction="how does X work",
        current_plan={"plan_id": "p", "steps": []},
        context={},
        step_results=[
            type("SR", (), {"action": "EXPLAIN", "success": True, "patch_size": 0, "files_modified": []})(),
        ],
    )
    a = evaluator.evaluate_with_reason("how does X work", state)
    b = evaluator.evaluate_with_reason("how does X work", state, phase_subgoal=None)
    assert a == b


def test_evaluate_phase_0_docs_lane_success():
    """Phase 0 docs lane: dominant_artifact_mode=docs + EXPLAIN success -> goal_met."""
    evaluator = GoalEvaluator()
    state = AgentState(
        instruction="Find documentation artifacts relevant to: Find docs",
        current_plan={"plan_id": "p", "steps": []},
        context={"dominant_artifact_mode": "docs"},
        step_results=[
            type("SR", (), {"action": "EXPLAIN", "success": True, "patch_size": 0, "files_modified": []})(),
        ],
    )
    met, reason, _ = evaluator.evaluate_with_reason(
        "Find docs",
        state,
        phase_subgoal="Find documentation artifacts relevant to: Find docs",
    )
    assert met is True
    assert reason == "docs_lane_explain_succeeded"


# --- PR5: Phase loop integration tests ---


def _make_two_phase_parent_plan():
    """Minimal 2-phase parent plan for mocking."""
    return {
        "parent_plan_id": "pplan_abc",
        "instruction": "Find docs and explain flow",
        "decomposition_type": "two_phase_docs_code",
        "compatibility_mode": False,
        "phases": [
            {
                "phase_id": "phase_01",
                "phase_index": 0,
                "subgoal": "Find documentation artifacts relevant to: Find docs and explain flow",
                "lane": "docs",
                "steps": [{"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs"}, {"id": 2, "action": "EXPLAIN", "artifact_mode": "docs"}],
                "plan_id": "plan_p0",
            },
            {
                "phase_id": "phase_02",
                "phase_index": 1,
                "subgoal": "Explain flow",
                "lane": "code",
                "steps": [{"id": 1, "action": "EXPLAIN", "description": "Explain flow"}],
                "plan_id": "plan_p1",
            },
        ],
    }


def _make_loop_result(state: AgentState, loop_output: dict):
    """Minimal LoopResult-like object."""
    r = MagicMock()
    r.state = state
    r.loop_output = loop_output
    return r


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_executes_phase0_then_phase1(mock_get_parent, mock_exec):
    """Two execution_loop calls in order; phase_results has 2 entries."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    calls = []

    def capture_exec(state, instruction, **kw):
        calls.append((instruction, state.context.get("dominant_artifact_mode")))
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture_exec

    state, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert len(calls) == 2
    assert len(out["phase_results"]) == 2


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_phase0_uses_docs_lane(mock_get_parent, mock_exec):
    """Phase 0 state has dominant_artifact_mode=docs."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    phase_states = []

    def capture_state(state, instruction, **kw):
        phase_states.append((instruction, dict(state.context)))
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture_state

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert phase_states[0][1].get("dominant_artifact_mode") == "docs"


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_phase1_uses_code_lane(mock_get_parent, mock_exec):
    """Phase 1 state has dominant_artifact_mode=code."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    phase_states = []

    def capture_state(state, instruction, **kw):
        phase_states.append((instruction, dict(state.context)))
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture_state

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert phase_states[1][1].get("dominant_artifact_mode") == "code"


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_execution_loop_receives_phase_subgoal(mock_get_parent, mock_exec):
    """execution_loop called with phase subgoal, not parent instruction."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    calls = []

    def capture(state, instruction, **kw):
        calls.append(instruction)
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture

    run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert "Find documentation artifacts" in calls[0]
    assert "Explain flow" in calls[1]
    assert "Find docs and explain flow" != calls[0] and "Find docs and explain flow" != calls[1]


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_phase1_state_has_no_docs_lane(mock_get_parent, mock_exec):
    """Phase 1 state has code lane, not docs."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    phase_states = []

    def capture(state, instruction, **kw):
        phase_states.append(state.context.get("dominant_artifact_mode"))
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert phase_states[1] == "code"


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_phase1_receives_handoff(mock_get_parent, mock_exec):
    """Phase 1 state has prior_phase_ranked_context from Phase 0."""
    parent = _make_two_phase_parent_plan()
    mock_get_parent.return_value = parent
    phase1_context = {}

    def capture(state, instruction, **kw):
        if state.context.get("current_phase_index") == 1:
            phase1_context.update(state.context)
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        s.context = dict(s.context)
        s.context["ranked_context"] = [{"file": "README.md", "snippet": "x"}] if state.context.get("current_phase_index") == 0 else []
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert "prior_phase_ranked_context" in phase1_context or "prior_phase_retrieved_symbols" in phase1_context


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_handoff_not_present_in_phase0(mock_get_parent, mock_exec):
    """Phase 0 state does not have prior_phase_ranked_context."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    phase0_context = {}

    def capture(state, instruction, **kw):
        if state.context.get("current_phase_index") == 0:
            phase0_context.update(state.context)
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = capture

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert "prior_phase_ranked_context" not in phase0_context


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_stop_on_phase0_failure(mock_get_parent, mock_exec):
    """Phase 0 goal_met=False -> only 1 execution_loop call; errors_encountered non-empty."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    call_count = [0]

    def fail_phase0(state, instruction, **kw):
        call_count[0] += 1
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = []
        s.step_results = []
        return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": ["goal_not_satisfied"], "tool_calls": 1})

    mock_exec.side_effect = fail_phase0

    state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert call_count[0] == 1
    assert len(out.get("errors_encountered", [])) > 0


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_phase1_not_called_on_phase0_fail(mock_get_parent, mock_exec):
    """execution_loop called exactly once when Phase 0 fails."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    call_count = [0]

    def fail_phase0(state, instruction, **kw):
        call_count[0] += 1
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = []
        s.step_results = []
        return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

    mock_exec.side_effect = fail_phase0

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert call_count[0] == 1


@patch("agent.orchestrator.deterministic_runner.GoalEvaluator")
@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_goal_evaluator_called_with_phase_subgoal(mock_get_parent, mock_exec, mock_ge_cls):
    """GoalEvaluator.evaluate_with_reason called with phase_subgoal=phase_plan['subgoal']."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()
    mock_eval = MagicMock(return_value=(True, "ok", {}))
    mock_ge_cls.return_value.evaluate_with_reason = mock_eval

    def succeed(state, instruction, **kw):
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = succeed

    run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    calls = mock_eval.call_args_list
    assert len(calls) >= 2
    assert calls[0][1].get("phase_subgoal") == "Find documentation artifacts relevant to: Find docs and explain flow"
    assert calls[1][1].get("phase_subgoal") == "Explain flow"


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_aggregated_output_both_succeed(mock_get_parent, mock_exec):
    """Both phases succeed -> phase_results has 2 entries, all_phases_succeeded."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()

    def succeed(state, instruction, **kw):
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = succeed

    state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert len(out["phase_results"]) == 2
    assert all(pr.get("success") for pr in out["phase_results"])
    assert all(pr.get("goal_met") for pr in out["phase_results"])


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_returns_agentstate_and_dict(mock_get_parent, mock_exec):
    """Return type is (AgentState, dict)."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()

    def succeed(state, instruction, **kw):
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = succeed

    state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert isinstance(state, AgentState)
    assert isinstance(out, dict)


@patch("agent.orchestrator.deterministic_runner.execution_loop")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_two_phase_loop_output_has_completed_steps(mock_get_parent, mock_exec):
    """loop_output completed_steps == sum of both phases."""
    mock_get_parent.return_value = _make_two_phase_parent_plan()

    def succeed(state, instruction, **kw):
        s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
        s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
        s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
        return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

    mock_exec.side_effect = succeed

    state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert out["completed_steps"] == 2


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_still_compat_for_single_intent(mock_get_parent, mock_run_det):
    """Pure code instruction still takes compat path."""
    mock_get_parent.return_value = {
        "parent_plan_id": "pplan_1",
        "compatibility_mode": True,
        "phases": [{}],
    }
    mock_run_det.return_value = (AgentState(instruction="x", current_plan={}, context={}), {"completed_steps": 0})

    run_hierarchical("Explain the replanner flow", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    mock_run_det.assert_called_once()


def test_phase_context_handoff_pruned_when_large():
    """Large ranked_context from Phase 0 is pruned before handoff."""
    phase_result = {
        "context_output": {
            "ranked_context": [{"x": "a" * 20000}] * 5,
            "retrieved_symbols": [],
            "retrieved_files": [],
        },
    }
    handoff, pruned = _build_phase_context_handoff(phase_result)
    assert pruned is True
    assert len(handoff["prior_phase_ranked_context"]) < 5


def test_phase_failure_class_derived_correctly():
    """Phase state with lane_violations -> failure_class=lane_violation."""
    r1 = MagicMock()
    r1.state = AgentState(instruction="x", current_plan={}, context={"lane_violations": [{}]})
    r1.loop_output = {}
    assert _derive_phase_failure_class(r1, False) == "lane_violation"

    r2 = MagicMock()
    r2.state = AgentState(instruction="x", current_plan={}, context={"termination_reason": "stall_detected"})
    r2.loop_output = {}
    assert _derive_phase_failure_class(r2, False) == "stall_detected"

    r3 = MagicMock()
    r3.state = AgentState(instruction="x", current_plan={}, context={})
    r3.loop_output = {"errors_encountered": ["max_task_runtime_exceeded"]}
    assert _derive_phase_failure_class(r3, False) == "timeout"


def test_run_hierarchical_notimplemented_when_phases_not_two():
    """NotImplementedError when len(phases) != 2."""
    with patch("agent.orchestrator.deterministic_runner.get_parent_plan") as mock_get:
        mock_get.return_value = {
            "parent_plan_id": "pplan_1",
            "compatibility_mode": False,
            "phases": [{}, {}, {}],
        }
        with pytest.raises(NotImplementedError) as exc:
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)
        assert "phases" in str(exc.value).lower()


def test_aggregate_parent_goal_all_succeeded():
    """_aggregate_parent_goal returns (True, 'all_phases_succeeded') when all goal_met."""
    ok, reason = _aggregate_parent_goal([{"goal_met": True}, {"goal_met": True}])
    assert ok is True
    assert reason == "all_phases_succeeded"


def test_aggregate_parent_goal_first_failed():
    """_aggregate_parent_goal returns (False, 'phase_0_failed') when first fails."""
    ok, reason = _aggregate_parent_goal([{"goal_met": False}, {"goal_met": True}])
    assert ok is False
    assert reason == "phase_0_failed"
