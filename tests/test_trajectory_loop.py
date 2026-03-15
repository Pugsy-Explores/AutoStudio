"""Unit tests for agent/meta/trajectory_loop.py.

Test cases:
- success without retry: first attempt succeeds, loop exits immediately
- retry resolves failure: first fails, second succeeds
- max_retry_stop: all attempts fail, loop stops at max_retries
- trajectory stored correctly: verify trajectory file has attempt, start_time, end_time, etc.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.autonomous.goal_manager import GoalManager
from agent.memory.state import AgentState
from agent.meta.evaluator import EvaluationResult, EVALUATION_STATUS_FAILURE, EVALUATION_STATUS_SUCCESS


def _make_state(goal: str = "fix bug", project_root: str = "/tmp") -> AgentState:
    return AgentState(
        instruction=goal,
        current_plan={"steps": []},
        context={
            "project_root": project_root,
            "trace_id": "trace-1",
            "instruction": goal,
            "retrieved_files": [],
            "retrieved_symbols": [],
        },
    )


def _make_result(goal: str = "fix bug", completed: int = 1) -> dict:
    return {
        "task_id": "task-1",
        "goal": goal,
        "completed_steps": completed,
        "tool_calls": 2,
        "stop_reason": "goal_achieved",
        "counts": {"steps_completed": completed, "tool_calls": 2, "edits_count": 0, "elapsed_seconds": 1.0},
    }


@patch("agent.autonomous.agent_loop._evaluate_and_record")
@patch("agent.autonomous.agent_loop._run_single_attempt")
def test_success_without_retry(mock_run, mock_eval):
    """First attempt succeeds; loop exits immediately, no critic/retry."""
    from agent.meta.trajectory_loop import TrajectoryLoop

    goal = "fix bug"
    root = "/tmp"
    task_id = "t1"
    trace_id = "trace-1"
    state = _make_state(goal, root)
    goal_manager = GoalManager(goal, max_steps=5, max_tool_calls=10, max_runtime_seconds=60, max_edits=5)

    result = _make_result(goal, 1)
    eval_success = EvaluationResult(EVALUATION_STATUS_SUCCESS, "all_steps_succeeded", 1.0)

    mock_run.return_value = (result, state)
    mock_eval.return_value = eval_success

    loop = TrajectoryLoop()
    out_result, out_state, out_eval = loop.run_with_retries(
        goal, root, task_id, trace_id, goal_manager, state, max_retries=3, success_criteria=None
    )

    assert mock_run.call_count == 1
    assert out_eval.status == EVALUATION_STATUS_SUCCESS
    assert out_result["evaluation"]["status"] == "SUCCESS"
    assert out_result["attempts"] == 1


@patch("agent.autonomous.agent_loop._critic_and_plan")
@patch("agent.autonomous.agent_loop._evaluate_and_record")
@patch("agent.autonomous.agent_loop._run_single_attempt")
def test_retry_resolves_failure(mock_run, mock_eval, mock_critic):
    """First attempt fails, second succeeds; critic and retry planner called."""
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import RetryHints
    from agent.meta.trajectory_loop import TrajectoryLoop

    goal = "fix bug"
    root = "/tmp"
    task_id = "t1"
    trace_id = "trace-1"
    state = _make_state(goal, root)
    goal_manager = GoalManager(goal, max_steps=5, max_tool_calls=10, max_runtime_seconds=60, max_edits=5)

    result = _make_result(goal, 0)
    eval_fail = EvaluationResult(EVALUATION_STATUS_FAILURE, "no_successful_steps", 0.0)
    eval_success = EvaluationResult(EVALUATION_STATUS_SUCCESS, "all_steps_succeeded", 1.0)
    diagnosis = Diagnosis("retrieval_miss", 1, "Search for correct module", evidence="", suggested_strategy="")
    hints = RetryHints("rewrite_retrieval_query", "step_dispatcher", None, [])

    mock_run.return_value = (result, state)
    mock_eval.side_effect = [eval_fail, eval_success]
    mock_critic.return_value = (diagnosis, hints)

    loop = TrajectoryLoop()
    out_result, out_state, out_eval = loop.run_with_retries(
        goal, root, task_id, trace_id, goal_manager, state, max_retries=3, success_criteria=None
    )

    assert mock_run.call_count == 2
    assert mock_critic.call_count == 1
    assert out_eval.status == EVALUATION_STATUS_SUCCESS
    assert out_result["attempts"] == 2


@patch("agent.autonomous.agent_loop._critic_and_plan")
@patch("agent.autonomous.agent_loop._evaluate_and_record")
@patch("agent.autonomous.agent_loop._run_single_attempt")
def test_max_retry_stop(mock_run, mock_eval, mock_critic):
    """All attempts fail; loop stops at max_retries."""
    from agent.meta.critic import Diagnosis
    from agent.meta.retry_planner import RetryHints
    from agent.meta.trajectory_loop import TrajectoryLoop

    goal = "fix bug"
    root = "/tmp"
    task_id = "t1"
    trace_id = "trace-1"
    state = _make_state(goal, root)
    goal_manager = GoalManager(goal, max_steps=5, max_tool_calls=10, max_runtime_seconds=60, max_edits=5)

    result = _make_result(goal, 0)
    eval_fail = EvaluationResult(EVALUATION_STATUS_FAILURE, "no_successful_steps", 0.0)
    diagnosis = Diagnosis("retrieval_miss", 1, "retry", evidence="", suggested_strategy="")
    hints = RetryHints("rewrite_retrieval_query", "retry", None, [])

    mock_run.return_value = (result, state)
    mock_eval.return_value = eval_fail
    mock_critic.return_value = (diagnosis, hints)

    loop = TrajectoryLoop()
    out_result, out_state, out_eval = loop.run_with_retries(
        goal, root, task_id, trace_id, goal_manager, state, max_retries=3, success_criteria=None
    )

    assert mock_run.call_count == 3
    assert mock_critic.call_count == 2  # after attempt 0 and 1, not after attempt 2
    assert out_eval.status == EVALUATION_STATUS_FAILURE
    assert out_result["attempts"] == 3


def test_trajectory_stored_correctly():
    """Verify trajectory file has attempt, start_time, end_time, steps, evaluation, diagnosis, strategy."""
    from agent.meta.trajectory_store import load_trajectory, record_attempt
    from agent.memory.step_result import StepResult

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = _make_state("fix bug", str(root))
        state.completed_steps = [{"id": 1, "action": "EDIT", "description": "fix"}]
        state.step_results = [StepResult(1, "EDIT", True, "", 0, files_modified=["a.py"])]
        eval_result = EvaluationResult(EVALUATION_STATUS_SUCCESS, "all_steps_succeeded", 1.0)

        import time

        start = time.time()
        record_attempt(
            "traj-1",
            state,
            eval_result,
            diagnosis={"failure_type": "unknown", "suggestion": "retry"},
            strategy="generate_new_plan",
            project_root=str(root),
            start_time=start,
        )
        traj = load_trajectory("traj-1", project_root=str(root))

    assert traj is not None
    assert traj["goal"] == "fix bug"
    assert len(traj["attempts"]) == 1
    rec = traj["attempts"][0]
    assert rec["attempt"] == 0
    assert "start_time" in rec
    assert "end_time" in rec
    assert rec["start_time"] is not None
    assert rec["end_time"] is not None
    assert rec["end_time"] >= rec["start_time"]
    assert rec["evaluation"]["status"] == "SUCCESS"
    assert rec["diagnosis"]["failure_type"] == "unknown"
    assert rec["strategy"] == "generate_new_plan"
    assert len(rec["steps"]) == 1
    assert rec["steps"][0]["action"] == "EDIT"
