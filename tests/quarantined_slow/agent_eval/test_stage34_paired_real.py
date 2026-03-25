"""Stage 34 — Real paired evaluation regression tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agent_eval.paired_comparison import (
    build_multi_live_comparison_artifact,
    compute_agreement_rate,
    compute_evidence_quality,
    compute_live_variability,
    compute_outcome_matrix,
    compute_representative_agreement_rate,
    derive_decision_recommendation,
    derive_policy_support,
    derive_usefulness_judgment,
)
from tests.agent_eval.runner import run_suite
from tests.agent_eval.run_paired_real import run_paired_real


def test_compute_agreement_rate():
    """Agreement rate: fraction of tasks where offline and live agree."""
    off = [{"task_id": "a", "success": True}, {"task_id": "b", "success": False}]
    live = [{"task_id": "a", "success": True}, {"task_id": "b", "success": True}]
    assert compute_agreement_rate(off, live) == 0.5
    live2 = [{"task_id": "a", "success": True}, {"task_id": "b", "success": False}]
    assert compute_agreement_rate(off, live2) == 1.0


def test_compute_live_variability():
    """Live variability: std, per-task agreement across runs."""
    summaries = [
        {"success_count": 2, "task_ids": ["a", "b"], "per_task_outcomes": [
            {"task_id": "a", "success": True}, {"task_id": "b", "success": True},
        ]},
        {"success_count": 2, "task_ids": ["a", "b"], "per_task_outcomes": [
            {"task_id": "a", "success": True}, {"task_id": "b", "success": True},
        ]},
        {"success_count": 1, "task_ids": ["a", "b"], "per_task_outcomes": [
            {"task_id": "a", "success": True}, {"task_id": "b", "success": False},
        ]},
    ]
    v = compute_live_variability(summaries)
    assert v["live_run_count"] == 3
    assert v["success_count_mean"] == 5 / 3
    assert v["success_count_std"] > 0
    assert v["min_task_agreement"] < 1.0  # task b differs across runs


def test_derive_decision_recommendation_offline_primary():
    """Recommendation: offline_primary when predictive and high agreement."""
    rec, ev = derive_decision_recommendation(
        "offline_is_predictive",
        agreement_rate=0.9,
        live_variability={"success_count_std": 0, "live_run_count": 1, "min_task_agreement": 1.0},
        per_task_deltas=[],
    )
    assert rec == "offline_primary"


def test_derive_decision_recommendation_live_unstable():
    """Recommendation: live_too_unstable when high variance across runs."""
    rec, ev = derive_decision_recommendation(
        "offline_is_partially_predictive",
        agreement_rate=0.5,
        live_variability={"success_count_std": 1.5, "live_run_count": 3, "min_task_agreement": 0.66},
        per_task_deltas=[],
    )
    assert rec == "live_too_unstable_to_gate"


def test_build_multi_live_artifact(tmp_path):
    """Multi-live comparison produces decision recommendation."""
    offline_dir = tmp_path / "offline"
    live1 = tmp_path / "live1"
    live2 = tmp_path / "live2"
    run_suite("paired4", tmp_path, execution_mode="offline", task_filter="core12_mini_repair_calc", output_dir=offline_dir)
    with patch("agent.models.model_client._call_chat", return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}'):
        run_suite("paired4", tmp_path, execution_mode="live_model", task_filter="core12_mini_repair_calc", output_dir=live1)
        run_suite("paired4", tmp_path, execution_mode="live_model", task_filter="core12_mini_repair_calc", output_dir=live2)
    artifact, md = build_multi_live_comparison_artifact(offline_dir, [live1, live2])
    assert "decision_recommendation" in artifact
    assert artifact["decision_recommendation"] in (
        "offline_primary",
        "offline_primary_selective_live_gate",
        "live_primary",
        "live_too_unstable_to_gate",
    )
    assert "agreement_rate" in artifact
    assert "live_variability" in artifact
    assert "Decision Questions" in md


def test_paired8_loads():
    """paired8 loads 8 tasks covering repair, feature, docs_consistency, explain_artifact, multi_file."""
    from tests.agent_eval.suite_loader import load_specs_for_mode

    specs = load_specs_for_mode("paired8", "offline")
    assert len(specs) == 8
    ids = {s.task_id for s in specs}
    assert "core12_mini_repair_calc" in ids
    assert "core12_pin_requests_explain_trace" in ids
    assert "core12_pin_click_multifile" in ids


def test_outcome_matrix():
    """Outcome matrix distinguishes pass_pass, fail_fail, off_pass_live_fail, off_fail_live_pass."""
    off = [
        {"task_id": "a", "success": True},
        {"task_id": "b", "success": False},
        {"task_id": "c", "success": True},
        {"task_id": "d", "success": False},
    ]
    live = [
        {"task_id": "a", "success": True},
        {"task_id": "b", "success": False},
        {"task_id": "c", "success": False},
        {"task_id": "d", "success": True},
    ]
    m = compute_outcome_matrix(off, live)
    assert m["pass_pass"] == 1
    assert m["fail_fail"] == 1
    assert m["offline_pass_live_fail"] == 1
    assert m["offline_fail_live_pass"] == 1


def test_evidence_quality():
    """Evidence quality includes task_count, nontrivial_success_count, task_type_coverage."""
    eq = compute_evidence_quality(
        task_count=8,
        live_run_count=4,
        outcome_matrix={"pass_pass": 2, "fail_fail": 4, "offline_pass_live_fail": 1, "offline_fail_live_pass": 1},
        task_type_agreement={"repair": {}, "feature": {}, "docs_consistency": {}},
    )
    assert eq["task_count"] == 8
    assert eq["live_repeat_count"] == 4
    assert eq["nontrivial_success_count"] == 4
    assert eq["pass_pass_count"] == 2
    assert eq["fail_fail_count"] == 4


def test_representative_agreement_all_fail():
    """Representative agreement is None when all fail/fail."""
    m = {"pass_pass": 0, "fail_fail": 4, "offline_pass_live_fail": 0, "offline_fail_live_pass": 0}
    assert compute_representative_agreement_rate(m) is None


def test_representative_agreement_nontrivial():
    """Representative agreement excludes fail_fail."""
    m = {"pass_pass": 2, "fail_fail": 2, "offline_pass_live_fail": 1, "offline_fail_live_pass": 0}
    assert compute_representative_agreement_rate(m) == 2 / 3


def test_usefulness_judgment_insufficient():
    """Usefulness: insufficient_evidence when task_count < 4."""
    j, expl = derive_usefulness_judgment(
        "offline_is_predictive",
        {"task_count": 1, "live_repeat_count": 3, "nontrivial_success_count": 0, "task_type_coverage": 0},
        1.0,
    )
    assert j == "insufficient_evidence"
    assert "task_count" in expl


def test_usefulness_judgment_all_fail_fail():
    """Usefulness: insufficient_evidence when all fail/fail with few tasks."""
    j, expl = derive_usefulness_judgment(
        "offline_is_predictive",
        {"task_count": 5, "live_repeat_count": 3, "nontrivial_success_count": 0, "task_type_coverage": 0.4},
        1.0,
    )
    assert j == "insufficient_evidence"
    assert "fail/fail" in expl or "nontrivial" in expl


def test_usefulness_judgment_predictive_but_low():
    """Usefulness: predictive_but_low_evidence when task_count small."""
    j, _ = derive_usefulness_judgment(
        "offline_is_predictive",
        {"task_count": 5, "live_repeat_count": 3, "nontrivial_success_count": 1, "task_type_coverage": 0.4},
        0.9,
    )
    assert j == "predictive_but_low_evidence"


def test_policy_support():
    """Policy support: strongly_supported only for predictive_and_useful."""
    assert derive_policy_support("predictive_and_useful") == "strongly_supported"
    assert derive_policy_support("predictive_but_low_evidence") == "provisionally_supported"
    assert derive_policy_support("insufficient_evidence") == "provisionally_supported"
    assert derive_policy_support("misleading") == "provisionally_supported"


def test_run_paired_real_mocked(tmp_path):
    """run_paired_real with mocked live produces comparison (1 offline + 3 live)."""
    with patch("agent.models.model_client._call_chat", return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}'):
        artifact, md, code = run_paired_real(
            tmp_path, suite_name="paired4", live_repeats=3, task_filter="core12_mini_repair_calc"
        )
    assert code == 0
    assert (tmp_path / "comparison.json").is_file()
    assert (tmp_path / "comparison.md").is_file()
    assert "decision_recommendation" in artifact
    assert "gating_policy" in artifact
    assert "outcome_matrix" in artifact
    assert "evidence_quality" in artifact
    assert "usefulness_judgment" in artifact
    assert "policy_support" in artifact
    assert len(artifact.get("live_run_dirs", [])) == 3
