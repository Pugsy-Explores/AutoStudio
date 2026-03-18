from pathlib import Path


def test_phase6a_acceptance_pack_runs_and_passes(tmp_path: Path):
    from scripts.phase6a_acceptance_pack import run

    report = run(str(tmp_path))
    assert report.get("phase") == "6A"
    assert report.get("pass") is True
    checks = report.get("checks") or {}
    assert checks.get("planner_mixed_lane_rejected") is True
    assert checks.get("runtime_lane_violation_fatal") is True
    assert checks.get("docs_task_lane_consistent") is True
    assert checks.get("code_task_lane_consistent") is True
    assert checks.get("replan_cannot_switch_lane") is True
    assert checks.get("trace_fields_present_docs") is True
    assert checks.get("trace_fields_present_code") is True

