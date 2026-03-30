"""Stage 12 benchmark harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.evals.agent_eval_harness import (
    TaskRunResult,
    aggregate_results,
    default_artifact_root,
    run_full_benchmark,
    run_single_benchmark_case,
    write_task_artifact,
)
from tests.evals.benchmark_cases import (
    BenchmarkCase,
    BENCHMARK_CASES,
    fixtures_root,
    load_benchmark_cases,
    validate_all_cases,
    validate_benchmark_case,
)


def test_benchmark_case_schema_validation_all():
    validate_all_cases()


def test_benchmark_case_schema_validation_each():
    for c in BENCHMARK_CASES:
        validate_benchmark_case(c)


def test_task_loader_returns_twelve_unique_ids():
    cases = load_benchmark_cases()
    assert len(cases) == 12
    ids = [c.task_id for c in cases]
    assert len(ids) == len(set(ids))
    for c in cases:
        root = fixtures_root() / c.fixture_relative
        assert root.is_dir(), c.task_id


def test_artifact_directory_creation(tmp_path):
    """Harness creates run_dir/tasks/ when writing per-task JSON artifacts."""
    run_dir = tmp_path / "eval_run"
    r = TaskRunResult(
        task_id="artifact_dir_smoke",
        instruction="x",
        path_mode="compat",
        success=True,
        loop_output_snapshot={},
        attempts_total=None,
        retries_used=None,
        phase_results=None,
        exception_text=None,
        started_at=0.0,
        finished_at=0.0,
        failure_class=None,
        replan_observed=False,
        retrieval_miss_note=None,
    )
    p = write_task_artifact(run_dir, r)
    assert p.parent.name == "tasks"
    assert p.parent.parent.resolve() == run_dir.resolve()
    assert p.exists()


def test_score_aggregation_mocked():
    r1 = MagicMock()
    r1.success = True
    r1.path_mode = "compat"
    r1.attempts_total = 1
    r1.retries_used = 0
    r1.failure_class = None
    r1.task_id = "a"
    r1.replan_observed = False
    r1.retrieval_miss_note = None

    r2 = MagicMock()
    r2.success = False
    r2.path_mode = "hierarchical"
    r2.attempts_total = 2
    r2.retries_used = 1
    r2.failure_class = "goal_or_parent_not_met"
    r2.task_id = "b"
    r2.replan_observed = True
    r2.retrieval_miss_note = None

    s = aggregate_results([r1, r2])
    assert s.total_tasks == 2
    assert s.pass_count == 1
    assert s.fail_count == 1
    assert s.compat_tasks == 1
    assert s.hierarchical_tasks == 1
    assert s.average_attempts_total == 1.5
    assert s.average_retries_used == 0.5
    assert s.failure_class_histogram.get("goal_or_parent_not_met") == 1
    assert "b" in s.tasks_requiring_replan


def test_compat_path_benchmark_case_runs():
    case = next(c for c in BENCHMARK_CASES if c.path_mode == "compat")
    out = run_single_benchmark_case(case)
    assert out.path_mode == "compat"
    assert out.success is True
    assert out.exception_text is None
    assert "completed_steps" in out.loop_output_snapshot or out.loop_output_snapshot


def test_two_phase_benchmark_case_runs():
    case = next(c for c in BENCHMARK_CASES if c.path_mode == "hierarchical")
    out = run_single_benchmark_case(case)
    assert out.path_mode == "hierarchical"
    assert out.success is True
    assert out.loop_output_snapshot.get("parent_goal_met") is True or out.success


def test_smoke_single_benchmark_run():
    case = BENCHMARK_CASES[0]
    out = run_single_benchmark_case(case)
    assert out.task_id == case.task_id
    assert out.exception_text is None


def test_write_task_artifact_roundtrip(tmp_path):
    r = TaskRunResult(
        task_id="t1",
        instruction="inst",
        path_mode="compat",
        success=True,
        loop_output_snapshot={"a": 1},
        attempts_total=1,
        retries_used=0,
        phase_results=None,
        exception_text=None,
        started_at=0.0,
        finished_at=1.0,
        failure_class=None,
        replan_observed=False,
        retrieval_miss_note=None,
    )

    p = write_task_artifact(tmp_path, r)
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["task_id"] == "t1"
    assert data["path_used"] == "compat"


def test_default_artifact_root_under_repo():
    root = Path(__file__).resolve().parents[2]
    ar = default_artifact_root(root)
    assert ar == root / "artifacts" / "agent_eval"


def test_full_benchmark_corpus_writes_artifacts(tmp_path):
    """Runs all 12 tasks with mocks; writes artifacts under tmp_path."""
    run_dir = tmp_path / "benchmark_run"
    results, summary, written = run_full_benchmark(run_dir=run_dir)
    assert len(results) == 12
    assert summary.total_tasks == 12
    assert summary.pass_count == 12
    assert summary.compat_tasks == 6
    assert summary.hierarchical_tasks == 6
    assert (written / "summary.json").exists()
    assert len(list((written / "tasks").glob("*.json"))) == 12


def test_invalid_case_rejected():
    bad = BenchmarkCase(
        task_id="",
        category="x",
        instruction="y",
        fixture_relative="mini_projects/sample_app",
        path_mode="compat",
        evaluation_hook="structural_loop_output_ok",
    )
    with pytest.raises(ValueError):
        validate_benchmark_case(bad)
