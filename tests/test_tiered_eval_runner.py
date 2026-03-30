"""Unit tests for tiered eval harness (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.runner import (
    EvalTask,
    PipelineCapture,
    default_dataset_path,
    load_dataset,
    run_tiered_eval,
    score_state_progress,
    score_task,
    score_validation_gain,
)

_ROOT = Path(__file__).resolve().parents[1]


def test_load_sample_dataset():
    tasks = load_dataset(default_dataset_path())
    assert len(tasks) >= 1
    assert all(1 <= t["tier"] <= 4 for t in tasks)


def test_score_decision_with_ground_truth():
    task: EvalTask = {
        "id": "x",
        "tier": 2,
        "module": "decision",
        "instruction": "i",
        "expected_behavior": "",
        "expected_signals": [],
        "ground_truth": {"decision_type": "explore"},
    }
    cap: PipelineCapture = {"decision": {"type": "explore", "tool": "search"}}
    row = score_task(task, cap)
    assert row["decision_accuracy"] == 1.0


def test_score_exploration_recall():
    task: EvalTask = {
        "id": "e",
        "tier": 3,
        "module": "exploration",
        "instruction": "i",
        "expected_behavior": "",
        "expected_signals": ["agent_v2/runtime/planner_task_runtime.py"],
    }
    cap: PipelineCapture = {
        "exploration": {"items": [{"path": "agent_v2/runtime/planner_task_runtime.py"}]}
    }
    row = score_task(task, cap)
    assert row["retrieval_recall"] == 1.0


def test_run_tiered_eval_aggregate():
    tasks = load_dataset(default_dataset_path())
    report = run_tiered_eval(tasks[:2], executor=None)
    assert report.metrics.n_tasks == 2
    assert report.raw_captures == [None, None]
    assert hasattr(report.metrics, "state_progress_score")
    assert hasattr(report.metrics, "validation_gain")


def test_score_state_progress_improving():
    cap: PipelineCapture = {
        "state_progress": {
            "findings_count_before": 1,
            "findings_count_after": 4,
            "open_questions_before": 3,
            "open_questions_after": 1,
            "confidence_before": "low",
            "confidence_after": "high",
        }
    }
    assert score_state_progress(cap) == 1.0


def test_score_validation_gain_completeness_and_markers():
    task: EvalTask = {
        "id": "vg",
        "tier": 4,
        "module": "validator",
        "instruction": "",
        "expected_behavior": "",
        "expected_signals": [],
        "ground_truth": {
            "expect_validation_gain": True,
            "post_loop_must_contain": ["validate_answer"],
        },
    }
    cap: PipelineCapture = {
        "validation_gain": {
            "completeness_before": 0.2,
            "completeness_after": 0.85,
            "answer_before": "The handler is in tests only.",
            "answer_after": "See validate_answer in agent_v2/validation/answer_validator.py.",
        }
    }
    s = score_validation_gain(task, cap)
    assert s > 0.8


def test_score_validation_gain_penalizes_regression_when_expected():
    task: EvalTask = {
        "id": "vg2",
        "tier": 4,
        "module": "validator",
        "instruction": "",
        "expected_behavior": "",
        "expected_signals": [],
        "ground_truth": {"expect_validation_gain": True},
    }
    cap: PipelineCapture = {
        "validation_gain": {"completeness_before": 0.6, "completeness_after": 0.3}
    }
    assert score_validation_gain(task, cap) == 0.0


def test_module_name_validation():
    bad = _ROOT / "eval" / "datasets" / "_bad_task.json"
    bad.write_text('[{"id":"b","tier":1,"module":"nope","instruction":""}]', encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="module"):
            load_dataset(bad)
    finally:
        bad.unlink(missing_ok=True)


def test_live_executor_safe_errors_on_invalid_workspace(monkeypatch):
    from eval.live_executor import live_executor_safe

    monkeypatch.setenv("AUTOSTUDIO_EVAL_PROJECT_ROOT", "/nonexistent_autostudio_eval_path_zz")
    task: EvalTask = {
        "id": "bad_ws",
        "tier": 1,
        "module": "planner",
        "instruction": "smoke",
        "expected_behavior": "",
        "expected_signals": [],
    }
    cap = live_executor_safe(task)
    lm = cap.get("loop_meta", {})
    assert lm.get("error_type") == "FileNotFoundError"
    assert lm.get("total_iterations") == 0
    assert lm.get("validation_failures") == 0
    assert lm.get("steps") == []
    assert cap.get("state", {}).get("progression") == []


def test_score_state_progress_from_state_progression():
    cap = {
        "state": {
            "progression": [
                {
                    "phase": "post_exploration",
                    "exploration": {
                        "evidence_count": 1,
                        "knowledge_gaps_count": 3,
                        "confidence": "low",
                    },
                },
                {
                    "phase": "post_validation",
                    "exploration": {
                        "evidence_count": 4,
                        "knowledge_gaps_count": 1,
                        "confidence": "high",
                    },
                },
            ]
        }
    }
    s = score_state_progress(cap)
    assert s == 1.0


def test_loop_efficiency_uses_iteration_steps_list():
    task: EvalTask = {
        "id": "loop",
        "tier": 2,
        "module": "planner",
        "instruction": "",
        "expected_behavior": "",
        "expected_signals": [],
    }
    cap = {
        "loop_meta": {
            "steps": [
                {"iteration": 1, "decision": "a", "validation": "v", "state_summary": "s"},
                {"iteration": 2, "decision": "b", "validation": "v", "state_summary": "s"},
            ],
            "total_iterations": 2,
        }
    }
    row = score_task(task, cap)
    assert row["loop_efficiency"] > 0.0
