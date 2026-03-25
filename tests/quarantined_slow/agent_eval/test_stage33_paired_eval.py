"""Stage 33 — Paired offline/live_model evaluation regression tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agent_eval.runner import run_suite
from tests.agent_eval.run_paired import run_paired
from tests.agent_eval.paired_comparison import (
    build_comparison_artifact,
    compute_summary_deltas,
    derive_judgment,
    _per_task_deltas,
)
from tests.agent_eval.suite_loader import load_specs_for_mode
from tests.agent_eval.summary_aggregation import build_suite_label


def test_paired_mode_comparison_output(tmp_path):
    """Paired run produces comparison.json and comparison.md with expected structure."""
    offline_dir = tmp_path / "offline"
    live_dir = tmp_path / "live_model"
    run_dir_o, _, summary_o = run_suite(
        "paired4",
        tmp_path / "out",
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
        output_dir=offline_dir,
    )
    with patch(
        "agent.models.model_client._call_chat",
        return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}',
    ):
        run_dir_l, _, summary_l = run_suite(
            "paired4",
            tmp_path / "out",
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
            output_dir=live_dir,
        )
    artifact, markdown = build_comparison_artifact(offline_dir, live_dir)
    assert "judgment" in artifact
    assert artifact["judgment"] in (
        "offline_is_predictive",
        "offline_is_partially_predictive",
        "offline_is_misleading",
    )
    assert "summary_deltas" in artifact
    assert "per_task_deltas" in artifact
    assert "failure_bucket_deltas" in artifact
    assert "semantic_rca_cause_deltas" in artifact
    assert "integrity" in artifact
    assert "same_task_set" in artifact
    assert artifact["same_task_set"] is True
    assert "Offline vs Live-Model" in markdown or "Gap Audit" in markdown
    assert artifact["judgment"] in markdown


def test_integrity_enforcement_for_live_model():
    """Live-mode run must enforce integrity (no offline stubs)."""
    with patch(
        "agent.models.model_client._call_chat",
        return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}',
    ):
        run_dir, results, summary = run_suite(
            "paired4",
            Path("artifacts/agent_eval_runs/stage33_integrity"),
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("used_offline_stubs") is False
    assert r.extra.get("integrity_valid") is True


def test_same_task_set_comparison():
    """paired4 loads same task IDs for offline and live_model."""
    specs_off = load_specs_for_mode("paired4", "offline")
    specs_live = load_specs_for_mode("paired4", "live_model")
    ids_off = {s.task_id for s in specs_off}
    ids_live = {s.task_id for s in specs_live}
    assert ids_off == ids_live
    assert len(specs_off) == 4
    assert len(specs_live) == 4


def test_summary_delta_computation():
    """compute_summary_deltas produces correct delta structure."""
    off = {
        "success_count": 2,
        "validation_pass_count": 2,
        "structural_success_count": 3,
        "model_call_count_total": 0,
        "attempts_total_aggregate": 4,
        "retries_used_aggregate": 1,
    }
    live = {
        "success_count": 2,
        "validation_pass_count": 2,
        "structural_success_count": 2,
        "model_call_count_total": 8,
        "attempts_total_aggregate": 5,
        "retries_used_aggregate": 2,
    }
    deltas = compute_summary_deltas(off, live)
    assert deltas["success_count"]["offline"] == 2
    assert deltas["success_count"]["live"] == 2
    assert deltas["success_count"]["delta"] == 0
    assert deltas["model_call_count_total"]["delta"] == 8
    assert deltas["retries_used_aggregate"]["delta"] == 1


def test_derive_judgment_predictive():
    """derive_judgment returns offline_is_predictive when deltas are small."""
    deltas = {
        "success_count": {"offline": 3, "live": 3, "delta": 0},
    }
    per_task = [
        {"task_id": "a", "success_offline": True, "success_live": True},
        {"task_id": "b", "success_offline": False, "success_live": False},
        {"task_id": "c", "success_offline": True, "success_live": True},
    ]
    assert derive_judgment(deltas, per_task) == "offline_is_predictive"


def test_derive_judgment_misleading():
    """derive_judgment returns offline_is_misleading when live much worse."""
    deltas = {
        "success_count": {"offline": 3, "live": 1, "delta": -2},
    }
    per_task = [
        {"task_id": "a", "success_offline": True, "success_live": False},
        {"task_id": "b", "success_offline": True, "success_live": False},
        {"task_id": "c", "success_offline": True, "success_live": True},
    ]
    assert derive_judgment(deltas, per_task) == "offline_is_misleading"


def test_derive_judgment_partially_predictive():
    """derive_judgment returns offline_is_partially_predictive when live better than offline (flips_off_to_ok=2)."""
    deltas = {
        "success_count": {"offline": 1, "live": 3, "delta": 2},
    }
    per_task = [
        {"task_id": "a", "success_offline": False, "success_live": True},
        {"task_id": "b", "success_offline": False, "success_live": True},
        {"task_id": "c", "success_offline": True, "success_live": True},
    ]
    assert derive_judgment(deltas, per_task) == "offline_is_partially_predictive"


def test_per_task_deltas_structure():
    """_per_task_deltas produces correct delta fields."""
    off_per = [
        {"task_id": "t1", "success": True, "validation_passed": True, "structural_success": True}
    ]
    live_per = [
        {"task_id": "t1", "success": False, "validation_passed": False, "structural_success": True}
    ]
    rows = _per_task_deltas(off_per, live_per)
    assert len(rows) == 1
    assert rows[0]["task_id"] == "t1"
    assert rows[0]["success_offline"] is True
    assert rows[0]["success_live"] is False
    assert rows[0]["success_delta"] == -1


def test_paired4_suite_label():
    """paired4 produces correct suite labels for offline and live_model."""
    assert build_suite_label("paired4", "offline") == "paired4_offline"
    assert build_suite_label("paired4", "live_model") == "paired4_live"


def test_run_paired_integration(tmp_path):
    """run_paired produces comparison artifact (mocked live)."""
    offline_dir, live_dir, artifact, markdown = run_paired(
        tmp_path,
        task_filter="core12_mini_repair_calc",
        mock_live_model=True,
    )
    assert offline_dir.is_dir()
    assert live_dir.is_dir()
    assert (tmp_path / "comparison.json").is_file()
    assert (tmp_path / "comparison.md").is_file()
    assert "judgment" in artifact
    assert artifact["same_task_set"] is True
