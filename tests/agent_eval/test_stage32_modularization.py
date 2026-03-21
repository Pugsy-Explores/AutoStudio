"""Stage 32 — Modularization regression tests.

Proves: integrity fields unchanged, suite aggregation unchanged,
success computation unchanged, execution mode routing unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agent_eval.integrity import REQUIRED_INTEGRITY_FIELDS, ensure_integrity_fields
from tests.agent_eval.execution_mode import (
    is_suite_loading_mode,
    resolve_execution_mode,
    uses_real_workspace,
)
from tests.agent_eval.success import compute_success, task_success
from tests.agent_eval.suite_loader import load_specs_for_mode
from tests.agent_eval.summary_aggregation import build_suite_label
from tests.agent_eval.runner import run_suite


def test_integrity_fields_unchanged():
    """Task outcome _audit must include all REQUIRED_INTEGRITY_FIELDS."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage32_integrity"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    outcome_path = run_dir / "tasks" / "core12_mini_repair_calc" / "outcome.json"
    import json

    data = json.loads(outcome_path.read_text())
    audit = data.get("_audit", {})
    for k in REQUIRED_INTEGRITY_FIELDS:
        assert k in audit, f"Missing _audit field: {k}"


def test_suite_aggregation_unchanged():
    """Runner summary must include integrity aggregation fields."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage32_agg"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    required = [
        "invalid_live_model_task_count",
        "zero_model_call_task_count",
        "offline_stubbed_task_count",
        "explain_stubbed_task_count",
        "plan_injection_task_count",
        "model_call_count_total",
        "small_model_call_count_total",
        "reasoning_model_call_count_total",
    ]
    for k in required:
        assert k in summary, f"Missing summary field: {k}"


def test_success_computation_unchanged():
    """compute_success behavior: structural_loop -> structural, explain_artifact -> explain_ok, else validation."""
    from tests.agent_eval.suites.core12 import CORE12_TASKS

    spec = next(s for s in CORE12_TASKS if s.task_id == "core12_mini_repair_calc")
    # validation_exit_code: success = validation_passed
    assert compute_success(spec, structural_success=True, validation_passed=False, explain_ok=None) is False
    assert compute_success(spec, structural_success=False, validation_passed=True, explain_ok=None) is True
    assert compute_success(spec, structural_success=False, validation_passed=False, explain_ok=None) is False

    explain_spec = next(s for s in CORE12_TASKS if s.task_id == "core12_pin_requests_explain_trace")
    # explain_artifact: success = explain_ok
    assert compute_success(
        explain_spec, structural_success=True, validation_passed=False, explain_ok=True
    ) is True
    assert compute_success(
        explain_spec, structural_success=True, validation_passed=True, explain_ok=False
    ) is False


def test_execution_mode_routing_unchanged():
    """resolve_execution_mode maps real->offline; uses_real_workspace true for offline/live_model/real."""
    assert resolve_execution_mode("real") == "offline"
    assert resolve_execution_mode("offline") == "offline"
    assert resolve_execution_mode("live_model") == "live_model"
    assert resolve_execution_mode("mocked") == "mocked"

    assert uses_real_workspace("offline") is True
    assert uses_real_workspace("live_model") is True
    assert uses_real_workspace("real") is True
    assert uses_real_workspace("mocked") is False

    assert is_suite_loading_mode("offline") is True
    assert is_suite_loading_mode("mocked") is False


def test_suite_loader_mode_specific():
    """load_specs_for_mode: core12+offline -> audit6; audit12+offline -> audit12."""
    specs_offline_core12 = load_specs_for_mode("core12", "offline")
    specs_audit12 = load_specs_for_mode("audit12", "offline")
    assert len(specs_offline_core12) == 6
    assert len(specs_audit12) == 12

    specs_mocked = load_specs_for_mode("audit6", "mocked")
    assert len(specs_mocked) == 6


def test_task_success_hierarchical():
    """task_success: hierarchical uses parent_goal_met; compat uses errors_encountered."""
    assert task_success({"parent_goal_met": True}, "hierarchical", None) is True
    assert task_success({"parent_goal_met": False}, "hierarchical", None) is False
    assert task_success({"errors_encountered": []}, "compat", None) is True
    assert task_success({"errors_encountered": ["x"]}, "compat", None) is False
    assert task_success({}, "compat", Exception("x")) is False


def test_ensure_integrity_fields_mocked():
    """ensure_integrity_fields sets defaults for mocked mode."""
    structural = {"structural_success": True}
    out = ensure_integrity_fields(structural, execution_mode="mocked")
    assert out["model_usage_audit"]["model_call_count"] == 0
    assert out["used_offline_stubs"] is False
    assert out["integrity_failure_reason"] == "mocked_mode"


def test_build_suite_label():
    """build_suite_label produces expected labels."""
    assert build_suite_label("audit12", "offline") == "audit12_offline"
    assert build_suite_label("live4", "live_model") == "live4_live"
    assert build_suite_label("audit6", "mocked") == "audit6"
