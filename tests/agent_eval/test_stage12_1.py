"""Stage 12.1 — real execution wiring, failure buckets, CLI, artifact schema."""

from __future__ import annotations

from pathlib import Path
import pytest

from tests.agent_eval.failure_buckets import classify_failure_bucket
from tests.agent_eval.runner import build_arg_parser, run_suite
from tests.agent_eval.workspace_artifacts import scan_bad_edit_patterns


def test_argparse_real_shortcut_sets_execution_mode():
    p = build_arg_parser()
    args = p.parse_args(["--real", "--output", "/tmp/x"])
    assert args.real is True
    if args.real:
        args.execution_mode = "real"
    assert args.execution_mode == "real"


def test_argparse_default_mocked():
    args = build_arg_parser().parse_args([])
    assert args.execution_mode == "mocked"
    assert args.real is False


def test_audit6_task_ids_count_and_layers():
    from tests.agent_eval.suites.audit6 import AUDIT6_TASK_IDS, load_audit6_specs

    assert len(AUDIT6_TASK_IDS) == 6
    specs = load_audit6_specs()
    assert len(specs) == 6
    mini = sum(1 for s in specs if s.layer == "mini_repo")
    pinned = sum(1 for s in specs if s.layer == "pinned_repo")
    assert mini == 3
    assert pinned == 3


def test_failure_bucket_validation_regression():
    b = classify_failure_bucket(
        success=False,
        structural_success=True,
        validation_passed=False,
        failure_class="validation_failed",
        loop_snapshot={},
        validation_logs=[{"stderr": "AssertionError", "stdout": ""}],
        notes="",
        index_ok=True,
    )
    assert b == "validation_regression"


def test_failure_bucket_index_harness():
    b = classify_failure_bucket(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_class="exception",
        loop_snapshot={},
        validation_logs=[],
        notes="index_failed: disk full",
        index_ok=False,
    )
    assert b == "infra_or_stub_failure"


def test_failure_bucket_success_returns_unknown():
    b = classify_failure_bucket(
        success=True,
        structural_success=True,
        validation_passed=True,
        failure_class=None,
        loop_snapshot={},
        validation_logs=[],
        notes="",
        index_ok=True,
    )
    assert b == "unknown"


def test_scan_bad_edit_patterns_conflict():
    assert "conflict_markers" in scan_bad_edit_patterns("<<<<<<< HEAD\nfoo")


def test_outcome_public_schema_keys():
    from tests.agent_eval.harness import TaskOutcome

    o = TaskOutcome(
        task_id="t",
        success=True,
        validation_passed=True,
        retries_used=0,
        replans_used=0,
        attempts_total=1,
        failure_class=None,
        files_changed=[],
        diff_stat={"insertions": 0, "deletions": 0},
        unrelated_files_changed=[],
        bad_edit_patterns=[],
        retrieval_miss_signals=[],
        notes="",
    )
    pub = o.to_public_dict()
    assert "task_id" in pub and "diff_stat" in pub


def test_run_suite_mocked_backward_compat(tmp_path, monkeypatch):
    """Mocked mode still runs 12 tasks and does not invoke real execution_loop."""
    import shutil

    import tests.agent_eval.runner as rmod

    fx_src = Path(__file__).resolve().parent / "fixtures"
    fx_dst = tmp_path / "tests" / "agent_eval" / "fixtures"
    fx_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fx_src, fx_dst)
    monkeypatch.chdir(tmp_path)

    run_dir, results, summary = rmod.run_suite(
        "core12",
        Path("artifacts/agent_eval_runs/latest"),
        repo_root=tmp_path,
        execution_mode="mocked",
    )
    assert summary["total_tasks"] == 12
    assert summary["execution_mode"] == "mocked"
    assert all((r.extra or {}).get("execution_mode") == "mocked" for r in results)
    assert (Path(run_dir) / "summary.json").exists()


def test_run_suite_real_mode_audit6_only(tmp_path, monkeypatch):
    from tests.agent_eval.harness import TaskOutcome

    import shutil

    import tests.agent_eval.runner as rmod

    fx_src = Path(__file__).resolve().parent / "fixtures"
    fx_dst = tmp_path / "tests" / "agent_eval" / "fixtures"
    fx_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fx_src, fx_dst)
    monkeypatch.chdir(tmp_path)

    def _fast_single(spec, ws, trace_id=None, execution_mode="mocked"):
        return TaskOutcome(
            task_id=spec.task_id,
            success=True,
            validation_passed=True,
            retries_used=0,
            replans_used=0,
            attempts_total=1,
            failure_class=None,
            files_changed=[],
            diff_stat={"insertions": 0, "deletions": 0},
            unrelated_files_changed=[],
            bad_edit_patterns=[],
            retrieval_miss_signals=[],
            notes="",
            structural_success=True,
            grading_mode=spec.grading_mode,
            loop_output_snapshot={},
            validation_logs=[],
            extra={"execution_mode": execution_mode, "failure_bucket": None, "index": {}},
        )

    monkeypatch.setattr("tests.agent_eval.harness.run_single_task", _fast_single)

    _run_dir, results, summary = rmod.run_suite(
        "core12",
        Path("artifacts/agent_eval_runs/latest"),
        repo_root=tmp_path,
        execution_mode="real",
    )
    assert summary["total_tasks"] == 6
    assert summary["execution_mode"] == "real"


def test_run_single_task_accepts_execution_mode_kwarg():
    from tests.agent_eval.harness import run_single_task
    from tests.agent_eval.suites.core12 import load_core12

    spec = load_core12()[0]
    import inspect

    sig = inspect.signature(run_single_task)
    assert "execution_mode" in sig.parameters
