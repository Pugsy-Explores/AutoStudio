"""PR3+PR4+PR5: Stage 2 detection, subgoals, plan construction, evaluator, and phase loop tests."""

from unittest.mock import MagicMock, patch

import pytest

from agent.memory.state import AgentState
from config.agent_config import (
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0,
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1,
)
from agent.orchestrator.deterministic_runner import (
    _aggregate_parent_goal,
    _build_hierarchical_loop_output,
    _build_phase_context_handoff,
    _derive_phase_failure_class,
    _extract_phase_context_output,
    _get_max_parent_retries,
    _parent_policy_decision_after_phase_attempt,
    run_hierarchical,
)
from agent.orchestrator.goal_evaluator import GoalEvaluator, is_explain_like_instruction
from agent.routing.docs_intent import is_two_phase_docs_code_intent as _is_two_phase_docs_code_intent
from agent.orchestrator.plan_resolver import (
    _build_two_phase_parent_plan,
    _derive_phase_subgoals,
    get_parent_plan,
)
from tests.hierarchical_test_locks import assert_compat_loop_output_has_no_hierarchical_keys


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


def _make_two_phase_parent_plan_with_retry_policy(max_parent_retries: int = 0):
    """Two-phase parent plan with explicit retry_policy on each phase (Stage 3 metadata wiring)."""
    p = _make_two_phase_parent_plan()
    for ph in p["phases"]:
        ph["retry_policy"] = {"max_parent_retries": max_parent_retries}
    return p


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
    """_aggregate_parent_goal returns (True, 'all_phases_succeeded') when all success and goal_met."""
    ok, reason = _aggregate_parent_goal([
        {"success": True, "goal_met": True},
        {"success": True, "goal_met": True},
    ])
    assert ok is True
    assert reason == "all_phases_succeeded"


def test_aggregate_parent_goal_first_failed():
    """_aggregate_parent_goal returns (False, 'phase_0_failed') when first fails."""
    ok, reason = _aggregate_parent_goal([{"goal_met": False}, {"goal_met": True}])
    assert ok is False
    assert reason == "phase_0_failed"


# --- Stage 3: PhaseValidationContract enforcement tests ---


def _make_two_phase_parent_plan_with_validation(
    phase0_validation=None,
    phase1_validation=None,
):
    """2-phase parent plan with explicit validation contracts for Stage 3 tests."""
    default_validation = {
        "require_ranked_context": True,
        "require_explain_success": True,
        "min_candidates": 1,
    }
    p0_val = phase0_validation if phase0_validation is not None else default_validation.copy()
    p1_val = phase1_validation if phase1_validation is not None else default_validation.copy()
    return {
        "parent_plan_id": "pplan_stage3",
        "instruction": "Find docs and explain flow",
        "decomposition_type": "two_phase_docs_code",
        "compatibility_mode": False,
        "phases": [
            {
                "phase_id": "phase_s3_0",
                "phase_index": 0,
                "subgoal": "Find documentation artifacts relevant to: Find docs and explain flow",
                "lane": "docs",
                "steps": [{"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs"}, {"id": 2, "action": "EXPLAIN", "artifact_mode": "docs"}],
                "plan_id": "plan_p0",
                "validation": p0_val,
                "retry_policy": {"max_parent_retries": 0},
            },
            {
                "phase_id": "phase_s3_1",
                "phase_index": 1,
                "subgoal": "Explain flow",
                "lane": "code",
                "steps": [{"id": 1, "action": "EXPLAIN", "description": "Explain flow"}],
                "plan_id": "plan_p1",
                "validation": p1_val,
                "retry_policy": {"max_parent_retries": 0},
            },
        ],
    }


class TestStage3PhaseValidationContract:
    """Stage 3: PhaseValidationContract runtime enforcement."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_docs_phase_fails_when_ranked_context_required_but_empty(
        self, mock_get_parent, mock_exec
    ):
        """Phase 0: goal_met=True, ranked_context=[], validation requires it -> phase fails."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(1)
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 1
        pr0 = out["phase_results"][0]
        assert pr0["success"] is False
        assert pr0["goal_met"] is True
        assert pr0["failure_class"] == "phase_validation_failed"
        assert "phase_0_failed:phase_validation_failed" in out.get("errors_encountered", [])

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_docs_phase_fails_when_min_candidates_not_met(
        self, mock_get_parent, mock_exec
    ):
        """Phase 0: ranked_context has fewer than min_candidates -> phase fails."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation(
            phase0_validation={"require_ranked_context": True, "require_explain_success": True, "min_candidates": 3}
        )
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(1)
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"a": 1}, {"b": 2}]
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 1
        pr0 = out["phase_results"][0]
        assert pr0["success"] is False
        assert pr0["goal_met"] is True
        assert pr0["failure_class"] == "phase_validation_failed"
        assert "phase_0_failed:phase_validation_failed" in out.get("errors_encountered", [])

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_docs_phase_fails_when_explain_required_but_missing(
        self, mock_get_parent, mock_exec
    ):
        """Phase 0: goal_met=True but explain success not present -> phase fails."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(1)
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"x": 1}]
            s.context["explain_success"] = False
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "goal_not_satisfied", {})

            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 1
        pr0 = out["phase_results"][0]
        assert pr0["success"] is False
        assert pr0["goal_met"] is True
        assert pr0["failure_class"] == "phase_validation_failed"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_code_phase_can_fail_after_goal_met_if_ranked_context_missing(
        self, mock_get_parent, mock_exec
    ):
        """Phase 0 passes; Phase 1 goal_met=True but ranked_context missing -> Phase 1 fails."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(state.context.get("current_phase_index", 0))
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = []
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 2
        pr0, pr1 = out["phase_results"]
        assert pr0["success"] is True
        assert pr1["success"] is False
        assert pr1["goal_met"] is True
        assert pr1["failure_class"] == "phase_validation_failed"
        assert "phase_1_failed:phase_validation_failed" in out.get("errors_encountered", [])

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_not_enforced_in_compatibility_mode(self, mock_get_parent, mock_run_det):
        """Compatibility mode delegates to run_deterministic; no validation."""
        mock_get_parent.return_value = {
            "parent_plan_id": "pplan_compat",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (AgentState(instruction="x", current_plan={}, context={}), {"completed_steps": 0})

        run_hierarchical("Explain the replanner flow", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        mock_run_det.assert_called_once()

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_contract_success_path_when_all_requirements_met(
        self, mock_get_parent, mock_exec
    ):
        """Both phases: goal_met, ranked_context, explain_success, min_candidates -> both succeed."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(1)
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"c": 1}]
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 2
        assert len(out["phase_results"]) == 2
        assert all(pr.get("success") for pr in out["phase_results"])
        assert all(pr.get("goal_met") for pr in out["phase_results"])
        assert not any("phase_validation_failed" in str(e) for e in out.get("errors_encountered", []))

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_failure_emits_trace_event(self, mock_get_parent, mock_exec):
        """On validation failure, trace event phase_validation_failed is emitted."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        phase_validation_events = [e for e in trace_events if e[0] == "phase_validation_failed"]
        assert len(phase_validation_events) == 1
        evt_name, payload = phase_validation_events[0]
        assert evt_name == "phase_validation_failed"
        assert "phase_id" in payload
        assert "phase_index" in payload
        assert "lane" in payload
        assert "validation_contract" in payload
        assert "validation_failure_reasons" in payload

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_apply_parent_stage2_policy_stops_after_validation_failure(self, mock_get_parent, mock_exec):
        """STOP happens when success=False after validation failure."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(1)
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})

            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 1


# --- Stage 3: Hierarchical observability / failure semantics tests ---


class TestHierarchicalObservability:
    """Stage 3: Observability and failure semantics for hierarchical execution."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_success_goal_and_failure_fields(
        self, mock_get_parent, mock_exec
    ):
        """phase_completed payload includes phase_id, phase_index, lane, subgoal, success, goal_met, goal_reason, failure_class, completed_steps."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        phase_completed = [e for e in trace_events if e[0] == "phase_completed"]
        assert len(phase_completed) >= 2
        for _, payload in phase_completed:
            assert "phase_id" in payload
            assert "phase_index" in payload
            assert "lane" in payload
            assert "subgoal" in payload
            assert "success" in payload
            assert "goal_met" in payload
            assert "goal_reason" in payload
            assert "failure_class" in payload
            assert "completed_steps" in payload

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_includes_reason_success(self, mock_get_parent, mock_exec):
        """parent_policy_decision payload includes phase_index, decision, decision_reason; success -> phase_succeeded."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        policy_events = [e for e in trace_events if e[0] == "parent_policy_decision"]
        assert len(policy_events) >= 2
        first_success = next((p for _, p in policy_events if p.get("decision") == "CONTINUE"), None)
        assert first_success is not None
        assert first_success.get("decision_reason") == "phase_succeeded"
        assert "phase_index" in first_success
        assert "decision" in first_success

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_validation_failure_reason(self, mock_get_parent, mock_exec):
        """parent_policy_decision: validation failure -> decision_reason=phase_failed."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        stop_events = [p for _, p in trace_events if p.get("decision") == "STOP"]
        assert len(stop_events) >= 1
        assert stop_events[0].get("decision_reason") == "phase_failed"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_goal_failure_reason(self, mock_get_parent, mock_exec):
        """parent_policy_decision: goal not met -> decision_reason=goal_not_met."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        stop_events = [p for _, p in trace_events if p.get("decision") == "STOP"]
        assert len(stop_events) >= 1
        assert stop_events[0].get("decision_reason") == "goal_not_met"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_goal_aggregation_event_includes_overall_success_and_reason(
        self, mock_get_parent, mock_exec
    ):
        """parent_goal_aggregation payload includes all_succeeded, aggregation_reason, phase_count."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        agg_events = [e for e in trace_events if e[0] == "parent_goal_aggregation"]
        assert len(agg_events) == 1
        _, payload = agg_events[0]
        assert "all_succeeded" in payload
        assert "aggregation_reason" in payload
        assert "phase_count" in payload

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_context_handoff_event_reports_pruned_flag_and_item_count(
        self, mock_get_parent, mock_exec
    ):
        """phase_context_handoff payload includes from_phase_index, to_phase_index, ranked_context_items, pruned."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"x": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        handoff_events = [e for e in trace_events if e[0] == "phase_context_handoff"]
        assert len(handoff_events) == 1
        _, payload = handoff_events[0]
        assert "from_phase_index" in payload
        assert "to_phase_index" in payload
        assert "ranked_context_items" in payload
        assert "pruned" in payload

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_contains_parent_plan_metadata(self, mock_get_parent, mock_exec):
        """loop_output includes parent_plan_id, phase_count, parent_goal_met, parent_goal_reason."""
        parent = _make_two_phase_parent_plan()
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out.get("parent_plan_id") == parent["parent_plan_id"]
        assert out.get("phase_count") == 2
        assert "parent_goal_met" in out
        assert "parent_goal_reason" in out

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_errors_encountered_prefers_failure_class_string(
        self, mock_get_parent, mock_exec
    ):
        """On validation failure: phase_0_failed:phase_validation_failed. On goal failure: phase_0_goal_not_met."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture_val(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture_val

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "phase_0_failed:phase_validation_failed" in out.get("errors_encountered", [])

        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture_goal(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = capture_goal

        state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "phase_0_goal_not_met" in out.get("errors_encountered", [])

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_last_phase_state_returned_on_phase1_failure(self, mock_get_parent, mock_exec):
        """When phase 1 fails, returned state must be phase 1 final state."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        phase1_state_marker = object()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = []
                s.context["explain_success"] = True
                s.context["_phase1_marker"] = phase1_state_marker
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            returned_state, _ = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert returned_state.context.get("_phase1_marker") is phase1_state_marker

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_last_phase_state_returned_on_phase0_failure(self, mock_get_parent, mock_exec):
        """When phase 0 fails, returned state must be phase 0 final state."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        phase0_state_marker = object()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            s.context["_phase0_marker"] = phase0_state_marker
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            returned_state, _ = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert returned_state.context.get("_phase0_marker") is phase0_state_marker


class TestParentRetryMetadata:
    """Stage 3: parent-level retry_policy metadata in trace and loop_output (no retry execution)."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_retry_metadata(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) == 2
        for pl in completed:
            assert pl["attempt_count"] == 1
            assert pl["max_parent_retries"] == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_includes_retry_metadata(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        policy = [p for e, p in trace_events if e == "parent_policy_decision"]
        assert len(policy) == 2
        for pl in policy:
            assert "phase_index" in pl
            assert pl["attempt_count"] == 1
            assert pl["max_parent_retries"] == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_results_preserve_attempt_count_and_retry_policy_source(self, mock_get_parent, mock_exec):
        parent = _make_two_phase_parent_plan_with_retry_policy(0)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for i, pr in enumerate(out["phase_results"]):
            assert pr["attempt_count"] == 1
            assert _get_max_parent_retries(parent["phases"][i]) == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_includes_parent_retry_metadata(self, mock_get_parent, mock_exec):
        parent = _make_two_phase_parent_plan_with_retry_policy(0)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["parent_plan_id"] == parent["parent_plan_id"]
        assert out["phase_count"] == 2
        assert out["max_parent_retries"] == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_missing_retry_policy_defaults_max_parent_retries_to_zero_in_events_and_output(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        for e, pl in trace_events:
            if e == "phase_completed":
                assert pl.get("max_parent_retries") == 0
                assert pl.get("attempt_count") == 1
            if e == "parent_policy_decision":
                assert pl.get("max_parent_retries") == 0
                assert pl.get("attempt_count") == 1

        assert out.get("max_parent_retries") == 0


class TestParentRetryExecution:
    """Stage 4: real parent-level retries (hierarchical path only; compatibility unchanged)."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase0_fails_then_succeeds_on_retry_then_phase1_runs(self, mock_get_parent, mock_exec):
        """max_parent_retries=1 allows one extra attempt; phase_results has one row per phase with final attempt_count."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": ["phase0_err"], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 3
        assert len(out["phase_results"]) == 2
        assert out["phase_count"] == 2
        assert out["phase_results"][0]["attempt_count"] == 2
        assert out["phase_results"][0]["success"] is True
        assert "phase0_err" in out["errors_encountered"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase0_retries_exhausted_stops_without_phase1(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (False, "goal_not_met", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 2
        assert len(out["phase_results"]) == 1
        assert out["phase_count"] == 1
        assert out["phase_results"][0]["attempt_count"] == 2
        assert out["phase_results"][0]["success"] is False
        assert out["parent_goal_met"] is False

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_retry_uses_fresh_agent_state_per_attempt(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)
        phase0_state_ids: list = []

        def capture(state, instruction, **kw):
            if state.context.get("current_phase_index") == 0:
                phase0_state_ids.append(id(state))
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"d": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(phase0_state_ids) == 2
        assert phase0_state_ids[0] != phase0_state_ids[1]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_and_parent_policy_emit_per_attempt(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)
        trace_events: list = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) == 3
        assert completed[0]["phase_index"] == 0 and completed[0]["attempt_count"] == 1
        assert completed[1]["phase_index"] == 0 and completed[1]["attempt_count"] == 2
        policies = [p for e, p in trace_events if e == "parent_policy_decision"]
        assert any(p.get("decision") == "RETRY" for p in policies)
        assert policies[-1].get("decision") == "CONTINUE"


class TestStage4RetryInvariants:
    """Lock Stage 4 parent-retry contracts: aggregation, metadata, compat, handoff, validation."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_a_single_phase_result_row_per_phase_with_retry(self, mock_get_parent, mock_exec):
        """A: After retry, exactly one phase_result for phase 0; attempt_count reflects total tries."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        rows_p0 = [pr for pr in out["phase_results"] if pr.get("phase_index") == 0]
        assert len(rows_p0) == 1
        assert rows_p0[0]["attempt_count"] == 2

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_b_one_synthetic_failure_marker_after_exhaustion(self, mock_get_parent, mock_exec):
        """B: Terminal failed phase adds phase_0_goal_not_met once, not once per attempt."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (False, "goal_not_met", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        errs = out["errors_encountered"]
        assert errs.count("phase_0_goal_not_met") == 1

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_c_merged_attempt_errors_preserved_when_final_retry_succeeds(
        self, mock_get_parent, mock_exec
    ):
        """C: errors_encountered_merged carries per-attempt loop errors; top-level list retains them after success."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)
        attempt_n = [0]

        def capture(state, instruction, **kw):
            attempt_n[0] += 1
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            e = "loop_err_try_1" if attempt_n[0] == 1 else "loop_err_try_2"
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [e], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "loop_err_try_1" in out["errors_encountered"]
        assert "loop_err_try_2" in out["errors_encountered"]
        m = out["phase_results"][0].get("errors_encountered_merged")
        assert isinstance(m, list)
        assert "loop_err_try_1" in m and "loop_err_try_2" in m

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_d_phase_count_equals_len_phase_results_after_success_and_exhaustion(
        self, mock_get_parent, mock_exec
    ):
        """D: loop_output phase_count always matches len(phase_results) (executed phases only)."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def cap_ok(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = cap_ok

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out_ok = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_ok["phase_count"] == len(out_ok["phase_results"]) == 2

        def cap_fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = cap_fail

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (False, "goal_not_met", {})
            _s, out_fail = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_fail["phase_count"] == len(out_fail["phase_results"]) == 1

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_e_compat_returns_exact_deterministic_output_no_hierarchical_keys(
        self, mock_get_parent, mock_run_det
    ):
        """E: Compatibility path returns the same object as run_deterministic; no hierarchical or retry keys."""
        loop_out = {
            "completed_steps": 2,
            "patches_applied": 0,
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 0,
            "plan_result": {"steps": []},
            "start_time": 1.0,
        }
        st = AgentState(instruction="x", current_plan={"steps": []}, context={})
        mock_get_parent.return_value = {
            "parent_plan_id": "pplan_c",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (st, loop_out)

        rs, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out is loop_out
        assert_compat_loop_output_has_no_hierarchical_keys(out)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_phase_results_always_include_errors_encountered_merged_list(
        self, mock_get_parent, mock_exec
    ):
        """Guard: per-phase errors_encountered_merged is always present on hierarchical phase_results (even attempt_count==1)."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": ["loop_only"], "tool_calls": 1})

        mock_exec.side_effect = capture

        _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "phase_results" in out
        for pr in out["phase_results"]:
            assert isinstance(pr.get("errors_encountered_merged"), list)
            assert "loop_only" in pr["errors_encountered_merged"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_f_parent_retry_metadata_matches_outcome_success_and_exhaustion(
        self, mock_get_parent, mock_exec
    ):
        """F: Top-level parent_retry* fields align with executed outcome (success vs exhausted)."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def cap_ok(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = cap_ok

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out_ok = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_ok["parent_retry_eligible"] is False
        assert out_ok["parent_retry_reason"] == "all_phases_succeeded"
        assert out_ok["parent_retry"]["eligible"] is False
        assert out_ok["parent_retry"]["reason"] == "all_phases_succeeded"

        def cap_fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = cap_fail

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (False, "goal_not_met", {})
            _s, out_fail = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_fail["parent_retry_eligible"] is False
        assert out_fail["parent_retry_reason"] == "max_parent_retries_exhausted"
        assert out_fail["parent_retry"]["eligible"] is False

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_g_phase_validation_on_phase_result_reflects_final_attempt_only(
        self, mock_get_parent, mock_exec
    ):
        """G: After validation failure then success on retry, stored phase_validation matches final attempt."""
        plan = _make_two_phase_parent_plan_with_validation()
        plan["phases"][0]["retry_policy"] = {"max_parent_retries": 1}
        mock_get_parent.return_value = plan
        phase0_try = [0]

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                phase0_try[0] += 1
                if phase0_try[0] == 1:
                    s.context["ranked_context"] = []
                else:
                    s.context["ranked_context"] = [{"doc": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = []
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        pv = out["phase_results"][0].get("phase_validation")
        assert isinstance(pv, dict)
        assert pv.get("passed") is True
        assert pv.get("failure_reasons") == []

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_invariant_h_handoff_to_phase1_uses_final_successful_phase0_context_only(
        self, mock_get_parent, mock_exec
    ):
        """H: prior_phase_ranked_context for phase 1 comes from final successful phase 0 attempt."""
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)
        phase1_prior = {}
        p0_try = [0]

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                p0_try[0] += 1
                s.context["ranked_context"] = (
                    [{"marker": "A"}] if p0_try[0] == 1 else [{"marker": "B"}]
                )
            elif state.context.get("current_phase_index") == 1:
                phase1_prior["prior"] = list(state.context.get("prior_phase_ranked_context") or [])
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["phase_results"][0]["attempt_count"] == 2
        assert phase1_prior["prior"] == [{"marker": "B"}]


class TestStage5AttemptHistory:
    """Stage 5: per-phase attempt_history + top-level attempts_total / retries_used (hierarchical only)."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_result_has_attempt_history_length_matches_attempt_count(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        p0 = out["phase_results"][0]
        assert len(p0["attempt_history"]) == p0["attempt_count"] == 2
        p1 = out["phase_results"][1]
        assert len(p1["attempt_history"]) == p1["attempt_count"] == 1

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_attempt_history_last_row_matches_final_phase_result_fields(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for pr in out["phase_results"]:
            last = pr["attempt_history"][-1]
            assert last["success"] == pr["success"]
            assert last["goal_met"] == pr["goal_met"]
            assert last["goal_reason"] == pr["goal_reason"]
            assert last["failure_class"] == pr["failure_class"]
            assert last["phase_validation"] == pr["phase_validation"]
            assert last["parent_retry"] == pr["parent_retry"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_attempts_total_and_retries_used_aggregate(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["attempts_total"] == 3
        assert out["retries_used"] == 1

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_attempt_history_preserves_failed_attempt_errors_when_final_succeeds(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(1)
        n = [0]

        def capture(state, instruction, **kw):
            n[0] += 1
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
            err = "fail_try" if n[0] == 1 else "ok_try"
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [err], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        h = out["phase_results"][0]["attempt_history"]
        assert h[0]["errors_encountered"] == ["fail_try"]
        assert h[1]["errors_encountered"] == ["ok_try"]

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_compat_output_excludes_stage5_top_level_keys(self, mock_get_parent, mock_run_det):
        loop_out = {
            "completed_steps": 1,
            "patches_applied": 0,
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 0,
            "plan_result": None,
            "start_time": 0.0,
        }
        st = AgentState(instruction="x", current_plan={"steps": []}, context={})
        mock_get_parent.return_value = {
            "parent_plan_id": "p",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (st, loop_out)
        _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)
        assert_compat_loop_output_has_no_hierarchical_keys(out)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_attempt_history_entry_required_keys_and_types(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": ["e"], "tool_calls": 1})

        mock_exec.side_effect = capture

        _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for pr in out["phase_results"]:
            assert isinstance(pr["attempt_history"], list)
            assert len(pr["attempt_history"]) == 1
            row = pr["attempt_history"][0]
            for k in (
                "attempt_count",
                "success",
                "goal_met",
                "goal_reason",
                "failure_class",
                "errors_encountered",
                "phase_validation",
                "parent_retry",
            ):
                assert k in row
            assert isinstance(row["errors_encountered"], list)
            assert isinstance(row["phase_validation"], dict)
            assert isinstance(row["parent_retry"], dict)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_two_phase_no_retries_attempts_total_equals_phase_count(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["phase_count"] == 2
        assert out["attempts_total"] == 2
        assert out["retries_used"] == 0


class TestParentRetryEligibilitySignaling:
    """Stage 3: parent-level retry eligibility signaling (metadata only; no retries executed)."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_parent_retry_eligibility_success_case(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) == 2
        for pl in completed:
            assert pl.get("parent_retry_eligible") is False
            assert pl.get("parent_retry_reason") == "phase_succeeded"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_parent_retry_eligibility_validation_failure_case(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) >= 1
        pl = completed[0]
        assert pl.get("parent_retry_eligible") is False
        assert pl.get("parent_retry_reason") == "max_parent_retries_exhausted"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_parent_retry_eligibility_goal_failure_case(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = fail

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) == 1
        pl = completed[0]
        assert pl.get("parent_retry_eligible") is False
        assert pl.get("parent_retry_reason") == "max_parent_retries_exhausted"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_includes_retry_eligibility_fields(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        policy = [p for e, p in trace_events if e == "parent_policy_decision"]
        assert len(policy) >= 2
        for pl in policy:
            assert "parent_retry_eligible" in pl
            assert "parent_retry_reason" in pl

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_results_include_parent_retry_metadata(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        exec_calls = []

        def capture(state, instruction, **kw):
            exec_calls.append(state.context.get("current_phase_index", 0))
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            if state.context.get("current_phase_index") == 0:
                s.context["ranked_context"] = [{"doc": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = []
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(exec_calls) == 2
        pr0, pr1 = out["phase_results"]
        assert pr0["parent_retry_eligible"] is False
        assert pr0["parent_retry_reason"] == "phase_succeeded"
        assert pr1["parent_retry_eligible"] is False
        assert pr1["parent_retry_reason"] == "max_parent_retries_exhausted"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_includes_parent_retry_summary_fields(
        self, mock_get_parent, mock_exec
    ):
        parent_ok = _make_two_phase_parent_plan_with_retry_policy(0)
        mock_get_parent.return_value = parent_ok

        def capture_ok(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture_ok

        _state, out_ok = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_ok.get("parent_retry_eligible") is False
        assert out_ok.get("parent_retry_reason") == "all_phases_succeeded"

        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()

        def capture_fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture_fail

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out_fail = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out_fail.get("parent_retry_eligible") is False
        assert out_fail.get("parent_retry_reason") == "max_parent_retries_exhausted"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_missing_retry_policy_still_reports_parent_retry_exhausted_on_failure(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = fail

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        pr0 = out["phase_results"][0]
        assert pr0.get("parent_retry_eligible") is False
        assert pr0.get("parent_retry_reason") == "max_parent_retries_exhausted"
        assert out.get("parent_retry_eligible") is False
        assert out.get("parent_retry_reason") == "max_parent_retries_exhausted"


class TestParentRetryMetadataConsolidation:
    """Stage 3: normalized parent_retry object alongside legacy scalar fields."""

    @staticmethod
    def _assert_phase_event_parent_retry_shape(payload):
        assert "parent_retry" in payload
        pr = payload["parent_retry"]
        assert set(pr.keys()) >= {"eligible", "reason", "attempt_count", "max_parent_retries"}
        assert pr["eligible"] == payload["parent_retry_eligible"]
        assert pr["reason"] == payload["parent_retry_reason"]
        assert pr["attempt_count"] == payload["attempt_count"]
        assert pr["max_parent_retries"] == payload["max_parent_retries"]
        assert "parent_retry_eligible" in payload
        assert "parent_retry_reason" in payload
        assert "attempt_count" in payload
        assert "max_parent_retries" in payload

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_parent_retry_metadata_object(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) == 2
        for pl in completed:
            self._assert_phase_event_parent_retry_shape(pl)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_includes_parent_retry_metadata_object(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        policy = [p for e, p in trace_events if e == "parent_policy_decision"]
        assert len(policy) == 2
        for pl in policy:
            self._assert_phase_event_parent_retry_shape(pl)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_result_includes_parent_retry_metadata_object(self, mock_get_parent, mock_exec):
        parent = _make_two_phase_parent_plan_with_retry_policy(0)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for i, pr in enumerate(out["phase_results"]):
            assert "parent_retry" in pr
            obj = pr["parent_retry"]
            assert obj["eligible"] == pr["parent_retry_eligible"]
            assert obj["reason"] == pr["parent_retry_reason"]
            assert obj["attempt_count"] == pr["attempt_count"]
            assert obj["max_parent_retries"] == _get_max_parent_retries(parent["phases"][i])

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_includes_parent_retry_metadata_object(
        self, mock_get_parent, mock_exec
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(0)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "parent_retry" in out
        top = out["parent_retry"]
        assert set(top.keys()) == {"eligible", "reason", "max_parent_retries", "phase_count"}
        assert top["eligible"] == out["parent_retry_eligible"]
        assert top["reason"] == out["parent_retry_reason"]
        assert top["max_parent_retries"] == out["max_parent_retries"]
        assert top["phase_count"] == out["phase_count"]
        assert "parent_retry_eligible" in out
        assert "parent_retry_reason" in out
        assert "max_parent_retries" in out

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_retry_metadata_object_matches_scalar_fields_on_failure(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        pr0 = out["phase_results"][0]
        obj_pr = pr0["parent_retry"]
        assert obj_pr["eligible"] == pr0["parent_retry_eligible"]
        assert obj_pr["reason"] == pr0["parent_retry_reason"]
        assert obj_pr["attempt_count"] == pr0["attempt_count"]
        assert obj_pr["max_parent_retries"] == _get_max_parent_retries(mock_get_parent.return_value["phases"][0])

        completed = [p for e, p in trace_events if e == "phase_completed"]
        pl0 = completed[0]
        o_c = pl0["parent_retry"]
        assert o_c["eligible"] == pl0["parent_retry_eligible"]
        assert o_c["reason"] == pl0["parent_retry_reason"]
        assert o_c["attempt_count"] == pl0["attempt_count"]
        assert o_c["max_parent_retries"] == pl0["max_parent_retries"]

        policy = [p for e, p in trace_events if e == "parent_policy_decision"]
        pol0 = policy[0]
        o_p = pol0["parent_retry"]
        assert o_p["eligible"] == pol0["parent_retry_eligible"]
        assert o_p["reason"] == pol0["parent_retry_reason"]
        assert o_p["attempt_count"] == pol0["attempt_count"]
        assert o_p["max_parent_retries"] == pol0["max_parent_retries"]

        top = out["parent_retry"]
        assert top["eligible"] == out["parent_retry_eligible"]
        assert top["reason"] == out["parent_retry_reason"]
        assert top["max_parent_retries"] == out["max_parent_retries"]
        assert top["phase_count"] == out["phase_count"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_retry_metadata_object_defaults_safely_when_retry_policy_missing(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def fail(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = fail

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        pr0 = out["phase_results"][0]
        obj = pr0["parent_retry"]
        assert obj["eligible"] is False
        assert obj["reason"] == "max_parent_retries_exhausted"
        assert obj["max_parent_retries"] == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_retry_metadata_object_top_level_all_succeeded_reason(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["parent_retry"]["reason"] == "all_phases_succeeded"


class TestStage3PhaseValidationMetadataConsolidation:
    """Stage 3: normalized phase_validation object alongside legacy scalar fields."""

    @staticmethod
    def _assert_phase_validation_shape(pv: dict):
        assert set(pv.keys()) == {"passed", "failure_reasons", "goal_met", "goal_reason"}
        assert isinstance(pv["failure_reasons"], list)
        assert all(isinstance(x, str) for x in pv["failure_reasons"])

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_completed_event_includes_phase_validation_object(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        completed = [p for e, p in trace_events if e == "phase_completed"]
        assert len(completed) >= 1
        pl = completed[0]
        assert "phase_validation" in pl
        self._assert_phase_validation_shape(pl["phase_validation"])
        assert "success" in pl
        assert "goal_met" in pl
        assert "goal_reason" in pl
        assert "failure_class" in pl

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_parent_policy_decision_event_includes_phase_validation_object(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        policy = [p for e, p in trace_events if e == "parent_policy_decision"]
        assert len(policy) >= 1
        pl = policy[0]
        assert "phase_validation" in pl
        self._assert_phase_validation_shape(pl["phase_validation"])
        assert "decision" in pl
        assert "decision_reason" in pl
        assert "attempt_count" in pl
        assert "max_parent_retries" in pl

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_result_includes_phase_validation_object(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for pr in out["phase_results"]:
            assert "phase_validation" in pr
            pv = pr["phase_validation"]
            self._assert_phase_validation_shape(pv)
            assert pv["passed"] is True
            assert pv["failure_reasons"] == []
            assert "success" in pr
            assert "goal_met" in pr

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_loop_output_includes_phase_validation_summary_object(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "phase_validation" in out
        summary = out["phase_validation"]
        assert set(summary.keys()) == {"all_passed", "failed_phase_indexes", "failure_reason_counts", "phase_count"}
        assert summary["phase_count"] == out["phase_count"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_object_matches_failure_case(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        pr0 = out["phase_results"][0]
        pv = pr0["phase_validation"]
        assert pv["passed"] is False
        assert pv["goal_met"] is True
        assert pv["goal_reason"] == pr0["goal_reason"]

        emitted = next(p for e, p in trace_events if e == "phase_validation_failed")
        assert pv["failure_reasons"] == emitted["validation_failure_reasons"]

        pl_done = next(p for e, p in trace_events if e == "phase_completed")
        assert pl_done["phase_validation"] == pv
        pol = next(p for e, p in trace_events if e == "parent_policy_decision")
        assert pol["phase_validation"] == pv

        top = out["phase_validation"]
        assert top["all_passed"] is False
        assert top["failed_phase_indexes"] == [0]
        assert set(top["failure_reason_counts"].keys()) == set(pv["failure_reasons"])
        for k, c in top["failure_reason_counts"].items():
            assert c == pv["failure_reasons"].count(k)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_object_defaults_to_passed_when_no_validation_contract(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for pr in out["phase_results"]:
            pv = pr["phase_validation"]
            assert pv["passed"] is True
            assert pv["failure_reasons"] == []

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase_validation_summary_counts_reasons_and_failed_indexes(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation(
            phase0_validation={
                "require_ranked_context": True,
                "require_explain_success": True,
                "min_candidates": 3,
            },
        )
        trace_events = []

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        def log_fn(trace_id, event, payload):
            trace_events.append((event, payload))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=log_fn)

        emitted = next(p for e, p in trace_events if e == "phase_validation_failed")
        reasons = emitted["validation_failure_reasons"]
        assert "missing_ranked_context" in reasons
        assert "min_candidates_not_met" in reasons

        top = out["phase_validation"]
        assert top["failed_phase_indexes"] == [0]
        for r in reasons:
            assert top["failure_reason_counts"].get(r) == reasons.count(r)


class TestStage3CloseoutInvariants:
    """Regression locks: hierarchical loop_output shape; loop_output['phase_count'] is executed count (len(phase_results)), not planned parent-plan total."""

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_success_output_contains_expected_metadata_blocks(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        for key in (
            "phase_results",
            "parent_retry",
            "phase_validation",
            "parent_plan_id",
            "phase_count",
            "parent_goal_met",
            "parent_goal_reason",
        ):
            assert key in out

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_phase_result_contains_expected_metadata_blocks(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_retry_policy(0)

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        pr0 = out["phase_results"][0]
        assert "parent_retry" in pr0
        assert "phase_validation" in pr0
        for key in ("success", "goal_met", "goal_reason", "attempt_count"):
            assert key in pr0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_failure_output_keeps_metadata_blocks_present(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan_with_validation()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = []
            s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.return_value = (True, "docs_lane_explain_succeeded", {})
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert "parent_retry" in out
        assert "phase_validation" in out
        pr0 = out["phase_results"][0]
        assert "parent_retry" in pr0
        assert "phase_validation" in pr0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_metadata_objects_are_dicts_not_scalars(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert isinstance(out["parent_retry"], dict)
        assert isinstance(out["phase_validation"], dict)
        pr0 = out["phase_results"][0]
        assert isinstance(pr0["parent_retry"], dict)
        assert isinstance(pr0["phase_validation"], dict)

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_hierarchical_phase_count_matches_phase_results_length_on_stop_after_phase0(
        self, mock_get_parent, mock_exec
    ):
        """Planned 2 phases, but STOP after phase 0: one executed result; loop_output phase_count is 1 (executed), not 2 (planned)."""
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def fail_phase0(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = fail_phase0

        _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert len(out["phase_results"]) == 1
        assert out["phase_count"] == 1
        assert out["phase_count"] == len(out["phase_results"])
        assert out["phase_validation"]["phase_count"] == out["phase_count"]


# --- Stage 3: Defensive behavior / schema hardening ---


class TestHierarchicalDefensiveBehavior:
    """Hierarchical execution resilience for malformed or partial phase outputs."""

    def test_extract_phase_context_output_defaults_missing_fields_to_empty_lists(self):
        state = AgentState(
            instruction="x",
            current_plan={"plan_id": "p", "steps": []},
            context={},
            step_results=[
                type("SR", (), {"action": "EXPLAIN", "success": True})(),
            ],
        )
        out = _extract_phase_context_output(state)
        assert set(out.keys()) == {
            "ranked_context",
            "retrieved_symbols",
            "retrieved_files",
            "files_modified",
            "patches_applied",
        }
        assert out["ranked_context"] == []
        assert out["retrieved_symbols"] == []
        assert out["retrieved_files"] == []
        assert out["files_modified"] == []
        assert out["patches_applied"] == 0
        assert isinstance(out["patches_applied"], int)

    def test_extract_phase_context_output_patches_applied_is_int_count(self):
        sr_int = type("SR", (), {"patch_size": 3})()
        sr_zero = type("SR", (), {"patch_size": 0})()
        sr_list = type("SR", (), {"patch_size": [1, 2, 3]})()
        sr_missing = type("SR", (), {})()
        sr_bad = type("SR", (), {"patch_size": "not_a_patch"})()
        state = AgentState(
            instruction="x",
            current_plan={"plan_id": "p", "steps": []},
            context={},
            step_results=[sr_int, sr_zero, sr_list, sr_missing, sr_bad],
        )
        out = _extract_phase_context_output(state)
        assert isinstance(out["patches_applied"], int)
        assert out["patches_applied"] == 6

    def test_extract_phase_context_output_files_modified_dedupes_but_patches_are_counted(self):
        sr1 = type(
            "SR",
            (),
            {
                "files_modified": ["a.py", "b.py", "a.py"],
                "patch_size": ["x", "y", "x"],
            },
        )()
        sr2 = type(
            "SR",
            (),
            {
                "files_modified": ["b.py", "c.py"],
                "patch_size": ["y", "z"],
            },
        )()
        state = AgentState(
            instruction="x",
            current_plan={"plan_id": "p", "steps": []},
            context={},
            step_results=[sr1, sr2],
        )
        out = _extract_phase_context_output(state)
        assert out["files_modified"] == ["a.py", "b.py", "c.py"]
        assert out["patches_applied"] == 5

    def test_build_phase_context_handoff_handles_missing_context_output_keys(self):
        handoff, pruned = _build_phase_context_handoff({"context_output": {}})
        assert handoff["prior_phase_ranked_context"] == []
        assert handoff["prior_phase_retrieved_symbols"] == []
        assert handoff["prior_phase_files"] == []
        assert pruned is False

    def test_build_phase_context_handoff_prunes_only_ranked_context(self):
        from config.agent_config import MAX_CONTEXT_CHARS

        big = [{"x": "y" * (MAX_CONTEXT_CHARS // 2 + 100)}]
        sym = ["s1", "s2"]
        files = ["f1.py"]
        phase_result = {
            "context_output": {
                "ranked_context": big,
                "retrieved_symbols": sym,
                "retrieved_files": files,
            },
        }
        handoff, pruned = _build_phase_context_handoff(phase_result)
        assert handoff["prior_phase_retrieved_symbols"] == sym
        assert handoff["prior_phase_files"] == files
        assert pruned is True
        assert len(handoff["prior_phase_ranked_context"]) < len(big)

    def test_derive_phase_failure_class_timeout(self):
        lr = MagicMock()
        lr.state = AgentState(instruction="x", current_plan={}, context={})
        lr.loop_output = {"errors_encountered": ["max_task_runtime_exceeded"]}
        assert _derive_phase_failure_class(lr, False) == "timeout"

    def test_derive_phase_failure_class_limit_exceeded_on_max_steps(self):
        lr = MagicMock()
        lr.state = AgentState(instruction="x", current_plan={}, context={})
        lr.loop_output = {"errors_encountered": ["max_steps"]}
        assert _derive_phase_failure_class(lr, False) == "limit_exceeded"

    def test_derive_phase_failure_class_limit_exceeded_on_max_tool_calls(self):
        lr = MagicMock()
        lr.state = AgentState(instruction="x", current_plan={}, context={})
        lr.loop_output = {"errors_encountered": ["max_tool_calls"]}
        assert _derive_phase_failure_class(lr, False) == "limit_exceeded"

    def test_derive_phase_failure_class_goal_not_satisfied_default(self):
        lr = MagicMock()
        lr.state = AgentState(instruction="x", current_plan={}, context={})
        lr.loop_output = {"errors_encountered": []}
        assert _derive_phase_failure_class(lr, False) == "goal_not_satisfied"

    def test_build_hierarchical_loop_output_handles_missing_loop_output_dict(self):
        phase_results = [
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {"files_modified": [], "patches_applied": 0},
                "loop_output": None,
            },
        ]
        out = _build_hierarchical_loop_output(
            phase_results,
            0.0,
            None,
            parent_plan_id="p1",
            phase_count=1,
            parent_goal_met=True,
            parent_goal_reason="all_phases_succeeded",
        )
        assert isinstance(out["errors_encountered"], list)
        assert out["tool_calls"] == 0
        assert out["parent_plan_id"] == "p1"
        assert out["phase_count"] == 1

    def test_build_hierarchical_loop_output_sums_patch_counts_across_phases(self):
        phase_results = [
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {"files_modified": [], "patches_applied": 4},
                "loop_output": {},
            },
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {"files_modified": [], "patches_applied": 7},
                "loop_output": {},
            },
        ]
        out = _build_hierarchical_loop_output(
            phase_results,
            0.0,
            None,
            parent_plan_id="p1",
            phase_count=2,
            parent_goal_met=True,
            parent_goal_reason="all_phases_succeeded",
        )
        assert out["patches_applied"] == 11
        assert isinstance(out["patches_applied"], int)

    def test_build_hierarchical_loop_output_tolerates_legacy_list_patches_applied(self):
        phase_results = [
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {"files_modified": [], "patches_applied": ["a", "b", "c"]},
                "loop_output": {},
            },
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {"files_modified": [], "patches_applied": 2},
                "loop_output": {},
            },
        ]
        out = _build_hierarchical_loop_output(
            phase_results,
            0.0,
            None,
            parent_plan_id="p1",
            phase_count=2,
            parent_goal_met=True,
            parent_goal_reason="all_phases_succeeded",
        )
        assert out["patches_applied"] == 5

    def test_build_hierarchical_loop_output_deduplicates_files_only_patches_summed(self):
        phase_results = [
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {
                    "files_modified": ["a.py", "b.py"],
                    "patches_applied": 2,
                },
                "loop_output": {},
            },
            {
                "completed_steps": 1,
                "success": True,
                "goal_met": True,
                "failure_class": None,
                "context_output": {
                    "files_modified": ["b.py", "c.py"],
                    "patches_applied": 3,
                },
                "loop_output": {},
            },
        ]
        out = _build_hierarchical_loop_output(
            phase_results,
            0.0,
            None,
            parent_plan_id="p1",
            phase_count=2,
            parent_goal_met=True,
            parent_goal_reason="all_phases_succeeded",
        )
        assert out["files_modified"] == ["a.py", "b.py", "c.py"]
        assert out["patches_applied"] == 5

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_handles_execution_loop_returning_none_loop_output(
        self, mock_get_parent, mock_exec
    ):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            r = MagicMock()
            r.state = s
            r.loop_output = None
            return r

        mock_exec.side_effect = capture

        state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert isinstance(out, dict)
        assert isinstance(out.get("errors_encountered"), list)
        assert out.get("tool_calls", 0) == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_handles_missing_completed_steps_on_state(self, mock_get_parent, mock_exec):
        mock_get_parent.return_value = _make_two_phase_parent_plan()

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = None
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["phase_results"][0]["completed_steps"] == 0


# --- Stage 6: connector coverage + two_phase_near_miss trace ---


class TestStage6DetectionConnectors:
    """Stage 6: _derive_phase_subgoals connectors + get_parent_plan near-miss (plan_resolver only)."""

    def test_derive_phase_subgoals_comma_then_explain(self):
        src = "find architecture docs, then explain the replanner flow"
        phase0, phase1 = _derive_phase_subgoals(src)
        assert phase0 == "Find documentation artifacts relevant to: " + src[:150]
        assert phase1 == "The replanner flow"

    def test_derive_phase_subgoals_then_explain_no_comma(self):
        _, phase1 = _derive_phase_subgoals("find the README then explain the dispatch loop")
        assert phase1 == "The dispatch loop"

    def test_derive_phase_subgoals_and_tell_me_about(self):
        _, phase1 = _derive_phase_subgoals(
            "locate the setup docs and tell me about the configuration"
        )
        assert phase1 == "The configuration"

    def test_derive_phase_subgoals_and_tell_me_how(self):
        _, phase1 = _derive_phase_subgoals(
            "find the docs and tell me how authentication works"
        )
        assert phase1 == "Authentication works"

    def test_derive_phase_subgoals_and_walk_me_through(self):
        _, phase1 = _derive_phase_subgoals(
            "find architecture docs and walk me through the plugin system"
        )
        assert phase1 == "The plugin system"

    def test_derive_phase_subgoals_before_explaining(self):
        _, phase1 = _derive_phase_subgoals(
            "find the README before explaining the worker flow"
        )
        assert phase1 == "The worker flow"

    def test_derive_phase_subgoals_standalone_comma_explain(self):
        _, phase1 = _derive_phase_subgoals("locate the docs, explain the replanner flow")
        assert phase1 == "The replanner flow"

    def test_derive_phase_subgoals_fallback_unchanged(self):
        phase0, phase1 = _derive_phase_subgoals("Find docs flow explain")
        assert phase0 == "Find documentation artifacts relevant to: Find docs flow explain"
        assert phase1 == "Find docs flow explain"

    def test_derive_phase_subgoals_short_fragment_not_split(self):
        phase0, phase1 = _derive_phase_subgoals("Find docs and explain it")
        assert phase0.startswith("Find documentation artifacts relevant to:")
        assert phase1 == "Find docs and explain it"

    def test_get_parent_plan_emits_near_miss_when_docs_and_discovery_no_code_marker(self):
        log_events = []

        def log_fn(trace_id, event, payload):
            log_events.append((event, payload))

        parent = get_parent_plan("find the README", trace_id="t-near", log_event_fn=log_fn)
        assert parent["compatibility_mode"] is True
        near = [e for e in log_events if e[0] == "two_phase_near_miss"]
        assert len(near) == 1
        assert near[0][1]["reason"] == "docs_and_discovery_but_no_code_marker"
        assert "instruction_preview" in near[0][1]

    def test_get_parent_plan_no_near_miss_when_two_phase_fires(self, monkeypatch):
        def mock_plan(subgoal):
            return {
                "steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal, "reason": "mocked"}],
                "plan_id": "plan_mock",
            }

        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", mock_plan)
        log_events = []

        def log_fn(trace_id, event, payload):
            log_events.append((event, payload))

        parent = get_parent_plan(
            "Find architecture docs and explain replanner flow",
            trace_id="t2",
            log_event_fn=log_fn,
        )
        assert parent["compatibility_mode"] is False
        assert not any(e[0] == "two_phase_near_miss" for e in log_events)
        assert any(e[0] == "parent_plan_created" for e in log_events)

    def test_get_parent_plan_no_near_miss_when_pure_code(self):
        log_events = []

        def log_fn(trace_id, event, payload):
            log_events.append((event, payload))

        parent = get_parent_plan(
            "Explain the replanner flow", trace_id="t3", log_event_fn=log_fn
        )
        assert parent["compatibility_mode"] is True
        assert not any(e[0] == "two_phase_near_miss" for e in log_events)

    def test_existing_connectors_still_work_after_extension(self):
        cases = [
            ("Find architecture docs and explain something long enough", "Something long enough"),
            ("Find docs and describe something long enough here", "Something long enough here"),
            ("Find README and show how validate_plan works end to end", "Validate_plan works end to end"),
            ("Find docs and summarize the routing architecture in detail", "The routing architecture in detail"),
            (
                "Find architecture docs and walk through the entire flow carefully",
                "The entire flow carefully",
            ),
        ]
        for instruction, expected_phase1 in cases:
            _, phase1 = _derive_phase_subgoals(instruction)
            assert phase1 == expected_phase1, instruction


# --- Stage 7: configurable two-phase parent retry budget (plan_resolver only) ---


def _stage7_mock_plan(subgoal):
    return {
        "steps": [{"id": 1, "action": "EXPLAIN", "description": subgoal, "reason": "mocked"}],
        "plan_id": "plan_stage7_mock",
    }


class TestStage7RetryBudgetConfiguration:
    """Stage 7: per-phase config constants drive _build_two_phase_parent_plan retry_policy (Stage 8: distinct PHASE_0 / PHASE_1)."""

    def test_build_two_phase_plan_default_budget_applied_to_phase0(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 1

    def test_build_two_phase_plan_default_budget_applied_to_phase1(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 1

    def test_build_two_phase_plan_invalid_budget_coerces_to_zero(self, monkeypatch):
        for bad_value in (-1, "1", True):
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0",
                bad_value,
            )
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1",
                bad_value,
            )
            monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
            parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
            assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 0
            assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 0

    def test_build_two_phase_plan_zero_budget_preserves_stage6_behavior(self, monkeypatch):
        monkeypatch.setattr(
            "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0",
            0,
        )
        monkeypatch.setattr(
            "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1",
            0,
        )
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 0
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 0

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_two_phase_uses_configured_retry_budget_for_phase0(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        """Production two-phase plan from _build_two_phase_parent_plan: Phase 0 fail then succeed, then Phase 1."""
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 1
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(
                s, {"completed_steps": 1, "errors_encountered": ["phase0_err"], "tool_calls": 1}
            )

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 3
        assert len(out["phase_results"]) == 2
        assert out["phase_results"][0]["attempt_count"] == 2
        assert out["phase_results"][0]["success"] is True
        assert "phase0_err" in out["errors_encountered"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_two_phase_retry_updates_attempt_history_and_retries_used(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        pr0 = out["phase_results"][0]
        assert pr0["attempt_count"] == 2
        assert len(pr0["attempt_history"]) == 2
        assert out["retries_used"] == 1
        assert out["attempts_total"] == 3

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_two_phase_retry_preserves_errors_encountered_merged(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )
        attempt_n = [0]

        def capture(state, instruction, **kw):
            attempt_n[0] += 1
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            e = "loop_err_try_1" if attempt_n[0] == 1 else "loop_err_try_2"
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [e], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        merged = out["phase_results"][0].get("errors_encountered_merged")
        assert isinstance(merged, list)
        assert "loop_err_try_1" in merged and "loop_err_try_2" in merged
        assert out["phase_results"][0]["success"] is True

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_compat_path_unaffected_by_retry_budget_config(self, mock_get_parent, mock_run_det):
        loop_out = {
            "completed_steps": 1,
            "patches_applied": 0,
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 0,
            "plan_result": {"steps": []},
            "start_time": 1.0,
        }
        st = AgentState(instruction="Explain code", current_plan={"steps": []}, context={})
        mock_get_parent.return_value = {
            "parent_plan_id": "pplan_compat_s7",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (st, loop_out)

        _rs, out = run_hierarchical("Explain code", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out is loop_out
        assert_compat_loop_output_has_no_hierarchical_keys(out)


class TestStage7CloseoutInvariants:
    """Locks shipped two-phase retry-budget contract; complements TestStage7RetryBudgetConfiguration."""

    def test_shipped_parent_plan_equal_retry_budget_both_phases(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        r0 = parent["phases"][0]["retry_policy"]["max_parent_retries"]
        r1 = parent["phases"][1]["retry_policy"]["max_parent_retries"]
        assert r0 == TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0 == 1
        assert r1 == TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1 == 1
        assert r0 == r1

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_phase1_shipped_plan_retry_without_hand_crafted_parent_plan(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        """Phase 0 succeeds on first try; Phase 1 fails then succeeds — parent from _build_two_phase_parent_plan only."""
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": ["p1_try1"], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (True, "docs_lane_explain_succeeded", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 3
        assert out["phase_count"] == len(out["phase_results"]) == 2
        pr1 = out["phase_results"][1]
        assert pr1["attempt_count"] == 2
        assert pr1["success"] is True
        assert pr1["attempt_history"][-1]["success"] is pr1["success"]
        assert pr1["attempt_history"][-1]["goal_met"] is pr1["goal_met"]
        merged = pr1.get("errors_encountered_merged")
        assert isinstance(merged, list)
        assert "p1_try1" in merged

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_closeout_phase_count_executed_phases_not_loop_attempts(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        """After Phase 0 retry (2 attempts) + Phase 1 (1 attempt): phase_count is 2, attempts_total is 3 (not equal)."""
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out["phase_count"] == 2
        assert len(out["phase_results"]) == 2
        assert out["attempts_total"] == 3
        assert out["phase_count"] != out["attempts_total"]


# --- Stage 8: per-phase retry budgets (plan_resolver only) ---


class TestStage8PerPhaseRetryBudgets:
    """Distinct PHASE_0 / PHASE_1 config drives retry_policy.max_parent_retries per phase."""

    def test_build_two_phase_plan_phase0_budget_applied_from_config(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 3)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 1)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 3
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 1

    def test_build_two_phase_plan_phase1_budget_applied_from_config(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 1)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 4)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 1
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 4

    def test_build_two_phase_plan_invalid_phase0_budget_coerces_to_zero(self, monkeypatch):
        for bad_value in (-1, "1", True):
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0",
                bad_value,
            )
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1",
                1,
            )
            monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
            parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
            assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 0
            assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 1

    def test_build_two_phase_plan_invalid_phase1_budget_coerces_to_zero(self, monkeypatch):
        for bad_value in (-1, "1", True):
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0",
                1,
            )
            monkeypatch.setattr(
                "agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1",
                bad_value,
            )
            monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
            parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
            assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 1
            assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 0

    def test_build_two_phase_plan_asymmetric_budgets_supported(self, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 2)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 5)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        parent = _build_two_phase_parent_plan("Find architecture docs and explain replanner flow")
        assert parent["phases"][0]["retry_policy"]["max_parent_retries"] == 2
        assert parent["phases"][1]["retry_policy"]["max_parent_retries"] == 5

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_respects_phase0_budget_when_phase0_needs_retry(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        """max_parent_retries=2 on phase 0 → up to 3 attempts before phase 1."""
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 2)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 1)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(
                s, {"completed_steps": 1, "errors_encountered": ["phase0_err"], "tool_calls": 1}
            )

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 4
        assert len(out["phase_results"]) == 2
        assert out["phase_results"][0]["attempt_count"] == 3
        assert out["phase_results"][0]["success"] is True
        assert out["retries_used"] == 2
        assert "phase0_err" in out["errors_encountered"]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_run_hierarchical_respects_phase1_budget_when_phase1_needs_retry(
        self, mock_get_parent, mock_exec, monkeypatch
    ):
        """max_parent_retries=2 on phase 1 → up to 3 attempts on phase 1 after phase 0 succeeds."""
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 1)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 2)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.plan", _stage7_mock_plan)
        mock_get_parent.return_value = _build_two_phase_parent_plan(
            "Find architecture docs and explain replanner flow"
        )

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            s.context["ranked_context"] = [{"doc": 1}]
            if state.context.get("current_phase_index") == 0:
                s.context["explain_success"] = True
            return _make_loop_result(
                s, {"completed_steps": 1, "errors_encountered": ["p1_try"], "tool_calls": 1}
            )

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (True, "docs_lane_explain_succeeded", {}),
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _state, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert mock_exec.call_count == 4
        assert len(out["phase_results"]) == 2
        pr1 = out["phase_results"][1]
        assert pr1["attempt_count"] == 3
        assert pr1["success"] is True
        assert out["retries_used"] == 2

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_compat_path_unaffected_by_asymmetric_retry_budgets(self, mock_get_parent, mock_run_det, monkeypatch):
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0", 0)
        monkeypatch.setattr("agent.orchestrator.plan_resolver.TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1", 99)
        loop_out = {
            "completed_steps": 1,
            "patches_applied": 0,
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 0,
            "plan_result": {"steps": []},
            "start_time": 1.0,
        }
        st = AgentState(instruction="Explain code", current_plan={"steps": []}, context={})
        mock_get_parent.return_value = {
            "parent_plan_id": "pplan_compat_s8",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (st, loop_out)

        _rs, out = run_hierarchical("Explain code", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert out is loop_out
        assert_compat_loop_output_has_no_hierarchical_keys(out)


# --- Stage 10: REPLAN parent-policy outcome ---


class TestStage10ReplanExecution:
    """REPLAN on consecutive same failure_class; shared retry budget; two-phase path only."""

    def test_policy_first_failure_retry_not_replan(self):
        """No previous failure class -> RETRY, never REPLAN."""
        pr = {
            "success": False,
            "goal_met": False,
            "failure_class": "goal_not_satisfied",
        }
        phase_plan = {"phase_index": 0, "retry_policy": {"max_parent_retries": 2}}
        d, r = _parent_policy_decision_after_phase_attempt(pr, phase_plan, 1, None)
        assert d == "RETRY"
        assert r == "parent_retry_scheduled"

    def test_policy_second_same_failure_class_replan(self):
        """Second failure with same class as previous -> REPLAN when attempts remain."""
        pr = {
            "success": False,
            "goal_met": False,
            "failure_class": "goal_not_satisfied",
        }
        phase_plan = {"phase_index": 0, "retry_policy": {"max_parent_retries": 2}}
        d, r = _parent_policy_decision_after_phase_attempt(
            pr, phase_plan, 2, "goal_not_satisfied",
        )
        assert d == "REPLAN"
        assert r == "replan_scheduled"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_attempt1_fail_emits_retry_not_replan(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(1)
        mock_get_parent.return_value = parent
        events = []

        def log_fn(tid, name, payload=None):
            events.append((name, payload or {}))

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": ["e1"], "tool_calls": 0})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t1", log_event_fn=log_fn)

        ppd = [p for n, p in events if n == "parent_policy_decision" and p.get("phase_index") == 0]
        assert ppd[0]["decision"] == "RETRY"
        assert ppd[0]["decision_reason"] == "parent_retry_scheduled"

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_attempt2_same_failure_class_emits_replan(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent
        events = []

        def log_fn(tid, name, payload=None):
            events.append((name, payload or {}))

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            idx = state.context.get("current_phase_index", 0)
            if idx == 0:
                s.context["ranked_context"] = []
                s.context["explain_success"] = False
            else:
                s.context["ranked_context"] = [{"f": 1}]
                s.context["explain_success"] = True
            return _make_loop_result(
                s,
                {"completed_steps": 0, "errors_encountered": ["e"], "tool_calls": 0},
            )

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t2", log_event_fn=log_fn)

        ppd = [p for n, p in events if n == "parent_policy_decision" and p.get("phase_index") == 0]
        assert ppd[0]["decision"] == "RETRY"
        assert ppd[1]["decision"] == "REPLAN"
        assert ppd[1]["decision_reason"] == "replan_scheduled"
        replanned = [p for n, p in events if n == "phase_replanned"]
        assert len(replanned) == 1
        assert replanned[0]["previous_failure_class"] == "goal_not_satisfied"
        assert replanned[0]["attempt_count"] == 2

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_replan_then_phase_succeeds_and_phase1_runs(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            idx = state.context.get("current_phase_index", 0)
            if idx == 0:
                s.context["ranked_context"] = [{"d": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = [{"c": 1}]
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t3", log_event_fn=lambda *a: None)

        assert len(out["phase_results"]) == 2
        assert out["phase_results"][0]["success"] is True
        assert out["phase_results"][0]["attempt_count"] == 3

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_replan_then_exhaust_budget_stops(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            s.context["ranked_context"] = []
            s.context["explain_success"] = False
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": ["x"], "tool_calls": 0})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t4", log_event_fn=lambda *a: None)

        assert len(out["phase_results"]) == 1
        pr0 = out["phase_results"][0]
        assert pr0["success"] is False
        assert pr0["attempt_count"] == 3

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_attempt_history_length_matches_attempt_count(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            idx = state.context.get("current_phase_index", 0)
            if idx == 0:
                s.context["ranked_context"] = [{"d": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = [{"c": 1}]
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t5", log_event_fn=lambda *a: None)

        pr0 = out["phase_results"][0]
        assert pr0["attempt_count"] == len(pr0["attempt_history"])
        assert len(pr0["attempt_history"]) == 3
        assert "plan_id" in pr0["attempt_history"][0]

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_errors_merged_across_retry_and_replan_success(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent
        outs = [
            {"completed_steps": 0, "errors_encountered": ["err_a"], "tool_calls": 0},
            {"completed_steps": 0, "errors_encountered": ["err_b"], "tool_calls": 0},
            {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1},
            {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1},
        ]
        oi = [0]

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            idx = state.context.get("current_phase_index", 0)
            lo = outs[oi[0]]
            oi[0] += 1
            if idx == 0:
                if oi[0] <= 2:
                    s.context["ranked_context"] = []
                    s.context["explain_success"] = False
                else:
                    s.context["ranked_context"] = [{"d": 1}]
                    s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = [{"c": 1}]
                s.context["explain_success"] = True
            return _make_loop_result(s, lo)

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t6", log_event_fn=lambda *a: None)

        merged = out["phase_results"][0]["errors_encountered_merged"]
        assert "err_a" in merged and "err_b" in merged

    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_one_phase_result_row_per_phase(
        self, mock_get_parent, mock_exec,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = [(state.current_plan.get("plan_id", "p"), 1)]
            s.step_results = [type("SR", (), {"action": "EXPLAIN", "success": True})()]
            idx = state.context.get("current_phase_index", 0)
            if idx == 0:
                s.context["ranked_context"] = [{"d": 1}]
                s.context["explain_success"] = True
            else:
                s.context["ranked_context"] = [{"c": 1}]
                s.context["explain_success"] = True
            return _make_loop_result(s, {"completed_steps": 1, "errors_encountered": [], "tool_calls": 1})

        mock_exec.side_effect = capture

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
                (True, "docs_lane_explain_succeeded", {}),
                (True, "docs_lane_explain_succeeded", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t7", log_event_fn=lambda *a: None)

        assert len(out["phase_results"]) == 2

    @patch("agent.orchestrator.deterministic_runner._build_replan_phase")
    @patch("agent.orchestrator.deterministic_runner.execution_loop")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_invalid_replan_terminal_stop(
        self, mock_get_parent, mock_exec, mock_replan,
    ):
        parent = _make_two_phase_parent_plan_with_retry_policy(2)
        mock_get_parent.return_value = parent
        mock_replan.side_effect = ValueError("replan_boom")

        def capture(state, instruction, **kw):
            s = AgentState(instruction=instruction, current_plan=state.current_plan, context=dict(state.context))
            s.completed_steps = []
            s.step_results = []
            s.context["ranked_context"] = []
            s.context["explain_success"] = False
            return _make_loop_result(s, {"completed_steps": 0, "errors_encountered": [], "tool_calls": 0})

        mock_exec.side_effect = capture
        events = []

        def log_fn(tid, name, payload=None):
            events.append((name, payload or {}))

        with patch("agent.orchestrator.deterministic_runner.GoalEvaluator") as mock_ge:
            mock_ge.return_value.evaluate_with_reason.side_effect = [
                (False, "goal_not_met", {}),
                (False, "goal_not_met", {}),
            ]
            _st, out = run_hierarchical("Find docs and explain flow", "/tmp", trace_id="t8", log_event_fn=log_fn)

        fails = [p for n, p in events if n == "phase_replan_failed"]
        assert len(fails) == 1
        assert "replan_boom" in fails[0].get("reason", "")
        assert len(out["phase_results"]) == 1
        assert out["phase_results"][0]["success"] is False

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_stage10_compat_path_unchanged(self, mock_get_parent, mock_run_det):
        loop_out = {
            "completed_steps": 1,
            "patches_applied": 0,
            "files_modified": [],
            "errors_encountered": [],
            "tool_calls": 0,
            "plan_result": {"steps": []},
            "start_time": 1.0,
        }
        st = AgentState(instruction="Explain code", current_plan={"steps": []}, context={})
        mock_get_parent.return_value = {
            "parent_plan_id": "pplan_compat_s10",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_det.return_value = (st, loop_out)

        _rs, out = run_hierarchical("Explain code", "/tmp", trace_id="t9", log_event_fn=lambda *a: None)

        assert out is loop_out
        assert_compat_loop_output_has_no_hierarchical_keys(out)
