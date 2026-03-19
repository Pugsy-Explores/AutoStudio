"""PR2: Stage 1 compatibility tests for run_hierarchical."""

from unittest.mock import MagicMock, patch

import pytest

from agent.memory.state import AgentState
from agent.orchestrator.deterministic_runner import run_hierarchical

from tests.hierarchical_test_locks import assert_compat_loop_output_has_no_hierarchical_keys


def _spy_log_fn():
    """Return (log_fn, events) where events collects (trace_id, event_name, payload)."""
    events = []

    def log_fn(trace_id, event_name, payload=None):
        events.append((trace_id, event_name, payload or {}))

    return log_fn, events


def _mock_state():
    return AgentState(instruction="x", current_plan={"steps": []}, context={})


def _mock_loop_output(completed_steps=0, errors=None):
    return {
        "completed_steps": completed_steps,
        "patches_applied": 0,
        "files_modified": [],
        "errors_encountered": errors or [],
        "tool_calls": 0,
        "plan_result": None,
        "start_time": 0.0,
    }


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.plan_resolver.get_plan")
def test_run_hierarchical_emits_parent_plan_created_event(mock_get_plan, mock_run_deterministic):
    """Trace contains 'parent_plan_created' event with compatibility_mode=True."""
    mock_get_plan.return_value = {
        "steps": [{"action": "EXPLAIN", "description": "x", "reason": "y"}],
        "plan_id": "plan_1",
    }
    mock_run_deterministic.return_value = (_mock_state(), _mock_loop_output())

    log_fn, events = _spy_log_fn()
    run_hierarchical(
        "explain validate_plan",
        "/tmp",
        trace_id="trace-1",
        log_event_fn=log_fn,
    )

    parent_created = [e for e in events if e[1] == "parent_plan_created"]
    assert len(parent_created) == 1
    assert parent_created[0][2].get("compatibility_mode") is True


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_emits_run_hierarchical_start_event(mock_get_parent_plan, mock_run_deterministic):
    """Trace contains 'run_hierarchical_start' event."""
    mock_get_parent_plan.return_value = {
        "parent_plan_id": "pplan_xyz",
        "compatibility_mode": True,
        "phases": [{}],
    }
    mock_run_deterministic.return_value = (_mock_state(), _mock_loop_output())

    log_fn, events = _spy_log_fn()
    run_hierarchical(
        "do something",
        "/tmp",
        trace_id="trace-2",
        log_event_fn=log_fn,
    )

    start_events = [e for e in events if e[1] == "run_hierarchical_start"]
    assert len(start_events) == 1
    assert start_events[0][2].get("parent_plan_id") == "pplan_xyz"
    assert start_events[0][2].get("phase_count") == 1


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_compatibility_returns_same_type(mock_get_parent_plan, mock_run_deterministic):
    """Returns (AgentState, dict)."""
    state = _mock_state()
    loop_output = _mock_loop_output()
    mock_get_parent_plan.return_value = {
        "parent_plan_id": "pplan_1",
        "compatibility_mode": True,
        "phases": [{}],
    }
    mock_run_deterministic.return_value = (state, loop_output)

    result_state, result_output = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert isinstance(result_state, AgentState)
    assert isinstance(result_output, dict)


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_compatibility_completed_steps_matches(mock_get_parent_plan, mock_run_deterministic):
    """completed_steps from run_hierarchical matches run_deterministic output."""
    state = _mock_state()
    loop_output = _mock_loop_output(completed_steps=5)
    mock_get_parent_plan.return_value = {
        "parent_plan_id": "pplan_1",
        "compatibility_mode": True,
        "phases": [{}],
    }
    mock_run_deterministic.return_value = (state, loop_output)

    _, output = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert output["completed_steps"] == 5


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_compatibility_errors_encountered_matches(mock_get_parent_plan, mock_run_deterministic):
    """errors_encountered from run_hierarchical matches run_deterministic output."""
    state = _mock_state()
    errors = ["max_steps"]
    loop_output = _mock_loop_output(errors=errors)
    mock_get_parent_plan.return_value = {
        "parent_plan_id": "pplan_1",
        "compatibility_mode": True,
        "phases": [{}],
    }
    mock_run_deterministic.return_value = (state, loop_output)

    _, output = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert output["errors_encountered"] == errors


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.plan_resolver.get_plan")
def test_run_hierarchical_compatibility_code_lane_instruction(mock_get_plan, mock_run_deterministic):
    """Code instruction -> compatibility_mode=True in parent_plan_created event."""
    mock_get_plan.return_value = {
        "steps": [{"action": "SEARCH", "description": "x", "reason": "y"}],
        "plan_id": "plan_1",
    }
    mock_run_deterministic.return_value = (_mock_state(), _mock_loop_output())

    log_fn, events = _spy_log_fn()
    run_hierarchical(
        "find validate_plan",
        "/tmp",
        trace_id="t",
        log_event_fn=log_fn,
    )

    parent_created = [e for e in events if e[1] == "parent_plan_created"]
    assert len(parent_created) == 1
    assert parent_created[0][2].get("compatibility_mode") is True
    assert parent_created[0][2].get("phase_count") == 1


@patch("agent.orchestrator.deterministic_runner.run_deterministic")
@patch("agent.orchestrator.plan_resolver.get_plan")
def test_run_hierarchical_compatibility_docs_lane_instruction(mock_get_plan, mock_run_deterministic):
    """Docs instruction -> compatibility_mode=True, phase_count=1."""
    mock_get_plan.return_value = {
        "steps": [
            {"action": "SEARCH_CANDIDATES", "artifact_mode": "docs"},
            {"action": "BUILD_CONTEXT", "artifact_mode": "docs"},
            {"action": "EXPLAIN", "artifact_mode": "docs"},
        ],
        "plan_id": "plan_2",
    }
    mock_run_deterministic.return_value = (_mock_state(), _mock_loop_output())

    log_fn, events = _spy_log_fn()
    run_hierarchical(
        "find readme in docs",
        "/tmp",
        trace_id="t",
        log_event_fn=log_fn,
    )

    parent_created = [e for e in events if e[1] == "parent_plan_created"]
    assert len(parent_created) == 1
    assert parent_created[0][2].get("compatibility_mode") is True
    assert parent_created[0][2].get("phase_count") == 1


@patch("agent.orchestrator.deterministic_runner.get_parent_plan")
def test_run_hierarchical_notimplemented_on_noncompat(mock_get_parent_plan):
    """If get_parent_plan returns compatibility_mode=False with len(phases) != 2 -> raises NotImplementedError."""
    mock_get_parent_plan.return_value = {
        "parent_plan_id": "pplan_1",
        "compatibility_mode": False,
        "phases": [{}, {}, {}],  # 3 phases: Stage 2 supports only 2 phases
    }

    with pytest.raises(NotImplementedError) as exc_info:
        run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

    assert "phases" in str(exc_info.value).lower()


class TestStage3CompatibilityInvariants:
    """Lock: compat path is pure delegation to run_deterministic — same (state, loop_output) as the mock; no hierarchical-only loop_output keys."""

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_compat_loop_output_has_no_hierarchical_only_keys(
        self, mock_get_parent_plan, mock_run_deterministic
    ):
        """No phase_validation, parent_retry, phase_results, errors_encountered_merged (top-level), etc."""
        state = _mock_state()
        loop_output = _mock_loop_output()
        mock_get_parent_plan.return_value = {
            "parent_plan_id": "pplan_1",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_deterministic.return_value = (state, loop_output)

        _s, out = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert_compat_loop_output_has_no_hierarchical_keys(out)

    @patch("agent.orchestrator.deterministic_runner.run_deterministic")
    @patch("agent.orchestrator.deterministic_runner.get_parent_plan")
    def test_compat_mode_state_and_loop_output_still_match_run_deterministic_exactly(
        self, mock_get_parent_plan, mock_run_deterministic
    ):
        state = _mock_state()
        loop_output = _mock_loop_output(completed_steps=3)
        mock_get_parent_plan.return_value = {
            "parent_plan_id": "pplan_1",
            "compatibility_mode": True,
            "phases": [{}],
        }
        mock_run_deterministic.return_value = (state, loop_output)

        result_state, result_output = run_hierarchical("x", "/tmp", trace_id="t", log_event_fn=lambda *a: None)

        assert result_state is state
        assert result_output == loop_output
        assert result_output is loop_output
