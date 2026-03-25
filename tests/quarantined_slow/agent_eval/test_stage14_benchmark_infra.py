"""Stage 14 — focused benchmark infrastructure tests (runner, artifact schema, failure buckets, suites)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.agent_eval.failure_buckets import (
    FailureBucket,
    classify_failure_bucket,
    infer_first_failing_stage,
)
from tests.agent_eval.suites.audit12 import load_audit12_specs
from tests.agent_eval.suites.audit6 import AUDIT6_TASK_IDS, load_audit6_specs
from tests.agent_eval.suites.core12 import CORE12_TASKS, load_core12
from tests.agent_eval.task_specs import validate_suite


def test_audit12_loads_twelve_tasks():
    specs = load_audit12_specs()
    assert len(specs) == 12
    ids = [s.task_id for s in specs]
    assert set(ids) == {t.task_id for t in CORE12_TASKS}


def test_external6_loads_via_runner():
    """external6 suite loads through public suite loader."""
    from tests.agent_eval.suite_loader import load_specs_for_mode

    specs = load_specs_for_mode("external6", "mocked")
    assert len(specs) == 6
    assert all(s.task_id.startswith("ext_") for s in specs)


def test_audit6_subset_of_audit12():
    audit6 = {s.task_id for s in load_audit6_specs()}
    audit12 = {s.task_id for s in load_audit12_specs()}
    assert audit6.issubset(audit12)
    assert len(audit6) == 6
    assert audit6 == set(AUDIT6_TASK_IDS)


def test_validate_suite_audit12():
    validate_suite(load_audit12_specs())


def test_infer_first_failing_stage_success():
    assert infer_first_failing_stage(
        success=True,
        structural_success=True,
        validation_passed=True,
        loop_snapshot={},
    ) is None


def test_infer_first_failing_stage_validate():
    assert infer_first_failing_stage(
        success=False,
        structural_success=True,
        validation_passed=False,
        loop_snapshot={},
    ) == "VALIDATE"


def test_infer_first_failing_stage_edit():
    assert infer_first_failing_stage(
        success=False,
        structural_success=False,
        validation_passed=False,
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "patch_anchor_not_found",
                "attempted_target_files": ["src/foo.py"],
            }
        },
    ) == "EDIT"


def test_infer_first_failing_stage_search():
    assert infer_first_failing_stage(
        success=False,
        structural_success=False,
        validation_passed=False,
        loop_snapshot={
            "edit_telemetry": {
                "search_viable_file_hits": 0,
                "attempted_target_files": [],
            }
        },
    ) == "SEARCH"


def test_classify_failure_bucket_returns_valid_bucket():
    bucket = classify_failure_bucket(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_class="goal_or_parent_not_met",
        loop_snapshot={"edit_telemetry": {"search_viable_file_hits": 0, "attempted_target_files": []}},
        validation_logs=[],
        notes="",
        index_ok=True,
    )
    assert bucket in (
        "search_targeting_failure",
        "edit_grounding_failure",
        "validation_regression",
        "infra_or_stub_failure",
        "retrieval_miss",
        "bad_file_targeting",
        "bad_patch_shape",
        "planner_wasted_motion",
        "ambiguity_or_missing_context",
        "harness_or_fixture_issue",
        "unknown",
    )


def test_runner_summary_aggregation(run_suite_core12_mocked):
    _run_dir, _results, summary = run_suite_core12_mocked
    assert "total_tasks" in summary
    assert summary["total_tasks"] == 12
    assert "success_count" in summary
    assert "validation_pass_count" in summary
    assert "structural_success_count" in summary
    assert "attempts_total_aggregate" in summary
    assert "retries_used_aggregate" in summary
    assert "replans_used_aggregate" in summary
    assert "failure_bucket_histogram" in summary
    assert "patch_reject_reason_histogram" in summary
    assert "validation_scope_kind_histogram" in summary
    assert "first_failing_stage_histogram" in summary
    assert "per_task_outcomes" in summary
    assert len(summary["per_task_outcomes"]) == 12


def test_artifact_schema_per_task(tmp_path, monkeypatch):
    """Schema check only needs one task's outcome.json; use task_filter to run 1 task."""
    import shutil

    import tests.agent_eval.runner as rmod

    fx_src = Path(__file__).resolve().parent / "fixtures"
    fx_dst = tmp_path / "tests" / "agent_eval" / "fixtures"
    fx_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fx_src, fx_dst)
    monkeypatch.chdir(tmp_path)

    run_dir, _results, _summary = rmod.run_suite(
        "core12",
        Path("artifacts/agent_eval_runs/latest"),
        repo_root=tmp_path,
        task_filter="core12_mini_repair_calc",
    )
    first_task = next(Path(run_dir).glob("tasks/*/outcome.json"))
    payload = json.loads(first_task.read_text(encoding="utf-8"))
    assert "task_id" in payload
    assert "success" in payload
    assert "validation_passed" in payload
    audit = payload.get("_audit", {})
    assert "structural_success" in audit
    assert "failure_bucket" in audit
    assert "first_failing_stage" in audit
    assert "edit_telemetry" in audit
    assert "patch_reject_reason" in audit
    assert "validation_scope_kind" in audit
    assert "retrieval_fallback_flags" in audit
    assert "target_selection_telemetry" in audit
    assert "selected_validation_command" in audit
