"""Unit tests for agent/meta: evaluator, critic, retry_planner, trajectory_store."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.memory.step_result import StepResult


# --- Evaluator ---


def test_evaluator_fatal_failure():
    from agent.meta.evaluator import evaluate, EVALUATION_STATUS_FAILURE

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    state.completed_steps = [{"id": 1, "action": "EDIT", "description": "fix"}]
    state.step_results = [
        StepResult(1, "EDIT", False, "", 0, classification="FATAL_FAILURE"),
    ]
    result = {"stop_reason": "action_selector_failed", "completed_steps": 1}
    ev = evaluate(result, state)
    assert ev.status == EVALUATION_STATUS_FAILURE
    assert "fatal" in ev.reason.lower()


def test_evaluator_all_steps_succeeded():
    from agent.meta.evaluator import evaluate, EVALUATION_STATUS_SUCCESS

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    state.completed_steps = [
        {"id": 1, "action": "SEARCH", "description": "find"},
        {"id": 2, "action": "EDIT", "description": "fix"},
    ]
    state.step_results = [
        StepResult(1, "SEARCH", True, "", 0),
        StepResult(2, "EDIT", True, "", 0, files_modified=["foo.py"]),
    ]
    result = {"stop_reason": None, "completed_steps": 2}
    ev = evaluate(result, state)
    assert ev.status == EVALUATION_STATUS_SUCCESS
    assert ev.score >= 0.9


def test_evaluator_partial_success():
    from agent.meta.evaluator import evaluate, EVALUATION_STATUS_PARTIAL

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    state.completed_steps = [
        {"id": 1, "action": "SEARCH", "description": "find"},
        {"id": 2, "action": "EDIT", "description": "fix"},
    ]
    state.step_results = [
        StepResult(1, "SEARCH", True, "", 0),
        StepResult(2, "EDIT", False, "", 0),
    ]
    result = {"stop_reason": "max_steps", "completed_steps": 2}
    ev = evaluate(result, state)
    assert ev.status == EVALUATION_STATUS_PARTIAL


def test_evaluator_no_steps():
    from agent.meta.evaluator import evaluate, EVALUATION_STATUS_FAILURE

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    result = {"stop_reason": "action_selector_failed", "completed_steps": 0}
    ev = evaluate(result, state)
    assert ev.status == EVALUATION_STATUS_FAILURE


# --- Critic ---


@patch("agent.models.model_client.call_small_model")
def test_critic_retrieval_miss(mock_call):
    mock_call.return_value = '{"failure_type": "retrieval_miss", "affected_step": 1, "suggestion": "Search for correct module"}'
    from agent.meta.critic import diagnose
    from agent.meta.evaluator import EvaluationResult

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    state.completed_steps = [{"id": 1, "action": "SEARCH", "description": "find"}]
    state.step_results = [StepResult(1, "SEARCH", False, "", 0)]
    state.context = {"tool_memories": [], "retrieved_files": [], "retrieved_symbols": []}
    eval_result = EvaluationResult("FAILURE", "no_successful_steps", 0.0)

    d = diagnose(state, eval_result)
    assert d.failure_type == "retrieval_miss"
    assert d.affected_step == 1
    assert "Search" in d.suggestion


@patch("agent.models.model_client.call_small_model")
def test_critic_fallback_on_parse_error(mock_call):
    mock_call.return_value = "not valid json"
    from agent.meta.critic import diagnose
    from agent.meta.evaluator import EvaluationResult

    state = AgentState(instruction="fix bug", current_plan={"steps": []})
    state.context = {}
    eval_result = EvaluationResult("FAILURE", "limits_hit_no_edits:max_steps", 0.2)

    d = diagnose(state, eval_result)
    assert d.failure_type in ("unknown", "timeout", "bad_patch")


# --- Retry Planner ---


@patch("agent.models.model_client.call_reasoning_model")
def test_retry_planner_rewrite_query(mock_call):
    mock_call.return_value = '{"strategy": "rewrite_retrieval_query", "rewrite_query": "step_dispatcher dispatch", "plan_override": null, "retrieve_files": []}'
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import plan_retry

    diagnosis = Diagnosis("retrieval_miss", 1, "Search for step_dispatcher")
    hints = plan_retry("fix bug", diagnosis)
    assert hints.strategy == "rewrite_retrieval_query"
    assert "step_dispatcher" in hints.rewrite_query


@patch("agent.models.model_client.call_reasoning_model")
def test_retry_planner_fallback(mock_call):
    mock_call.side_effect = RuntimeError("model unavailable")
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import plan_retry

    diagnosis = Diagnosis("retrieval_miss", None, "retry with different query")
    hints = plan_retry("fix bug", diagnosis)
    assert hints.strategy == "rewrite_retrieval_query"
    assert hints.rewrite_query


def test_retry_planner_strategy_from_diagnosis():
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import _strategy_from_diagnosis

    assert _strategy_from_diagnosis(Diagnosis("retrieval_miss", 1, "")) == "rewrite_retrieval_query"
    assert _strategy_from_diagnosis(Diagnosis("bad_plan", 1, "")) == "generate_new_plan"
    assert _strategy_from_diagnosis(Diagnosis("bad_patch", 1, "")) == "retry_edit_with_different_patch"
    assert _strategy_from_diagnosis(Diagnosis("missing_dependency", 1, "")) == "search_symbol_dependencies"
    assert _strategy_from_diagnosis(Diagnosis("timeout", 1, "")) == "expand_search_scope"


@patch("agent.models.model_client.call_reasoning_model")
def test_retry_planner_invalid_strategy_fallback(mock_call):
    """Invalid strategy from model falls back to generate_new_plan."""
    mock_call.return_value = '{"strategy": "invalid_strategy_xyz", "rewrite_query": "", "plan_override": null, "retrieve_files": []}'
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import FALLBACK_STRATEGY, plan_retry

    diagnosis = Diagnosis("retrieval_miss", 1, "retry")
    hints = plan_retry("fix bug", diagnosis)
    assert hints.strategy == FALLBACK_STRATEGY


# --- Trajectory Store ---


def test_trajectory_store_record_and_load():
    from agent.meta.trajectory_store import record_attempt, load_trajectory, finalize, list_trajectories
    from agent.meta.evaluator import EvaluationResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = AgentState(instruction="fix bug", current_plan={"steps": []})
        state.completed_steps = [{"id": 1, "action": "EDIT", "description": "fix"}]
        state.step_results = [StepResult(1, "EDIT", True, "", 0, files_modified=["a.py"])]
        eval_result = EvaluationResult("SUCCESS", "all_steps_succeeded", 1.0)

        record_attempt("t1", state, eval_result, project_root=str(root))
        traj = load_trajectory("t1", project_root=str(root))
        assert traj is not None
        assert traj["goal"] == "fix bug"
        assert len(traj["attempts"]) == 1
        assert traj["attempts"][0]["evaluation"]["status"] == "SUCCESS"

        finalize("t1", "SUCCESS", project_root=str(root))
        traj2 = load_trajectory("t1", project_root=str(root))
        assert traj2["final_status"] == "SUCCESS"

        ids = list_trajectories(project_root=str(root))
        assert "t1" in ids


def test_trajectory_store_record_with_diagnosis():
    from agent.meta.trajectory_store import record_attempt, load_trajectory
    from agent.meta.evaluator import EvaluationResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = AgentState(instruction="fix bug", current_plan={"steps": []})
        state.step_results = []
        eval_result = EvaluationResult("FAILURE", "no_successful_steps", 0.0)
        diagnosis = {"failure_type": "retrieval_miss", "affected_step": 1, "suggestion": "retry"}

        record_attempt("t2", state, eval_result, diagnosis=diagnosis, strategy="rewrite_retrieval_query", project_root=str(tmp))
        traj = load_trajectory("t2", project_root=str(tmp))
        assert traj["attempts"][0]["diagnosis"] == diagnosis
        assert traj["attempts"][0]["strategy"] == "rewrite_retrieval_query"
