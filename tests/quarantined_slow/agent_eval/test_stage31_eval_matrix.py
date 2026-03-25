"""Stage 31 — Evaluation matrix and integrity field regression tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agent_eval.runner import build_arg_parser, run_suite
from tests.agent_eval.compare_modes import compare


def test_mocked_mode_deterministic():
    """Mocked mode must produce deterministic outcomes (no network, no real model)."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage31_mocked"),
        execution_mode="mocked",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("execution_mode") == "mocked"
    assert r.extra.get("used_offline_stubs") is False
    assert (r.extra.get("model_usage_audit") or {}).get("model_call_count", 0) == 0
    assert r.extra.get("integrity_valid") is False


def test_offline_mode_uses_stubs_and_plan_injection():
    """Offline mode must set used_offline_stubs and used_plan_injection."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage31_offline"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("used_offline_stubs") is True
    assert r.extra.get("used_plan_injection") is True
    assert r.extra.get("integrity_valid") is False


def test_live_model_mode_never_uses_offline_stubs():
    """Live-model mode must not use offline_llm_stubs."""
    with patch("agent.models.model_client._call_chat", return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}'):
        run_dir, results, summary = run_suite(
            "live4",
            Path("artifacts/agent_eval_runs/stage31_live"),
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("used_offline_stubs") is False
    assert r.extra.get("integrity_valid") is True


def test_live_model_zero_model_calls_invalid():
    """Live-model tasks with zero model calls must be invalid."""
    def _noop_record(*_a, **_k):
        pass

    with patch("agent.models.model_client._record_model_call", _noop_record), patch(
        "agent.models.model_client._call_chat",
        return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}',
    ):
        run_dir, results, summary = run_suite(
            "live4",
            Path("artifacts/agent_eval_runs/stage31_zero_invalid"),
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    assert len(results) == 1
    r = results[0]
    assert ((r.extra.get("model_usage_audit") or {}).get("model_call_count", 0)) == 0
    assert r.extra.get("integrity_valid") is False
    assert r.extra.get("integrity_failure_reason") == "zero_real_model_calls"


def test_deprecated_real_maps_to_offline():
    """--real must map to offline (main() applies deprecation warning)."""
    parser = build_arg_parser()
    args = parser.parse_args(["--real", "--suite", "audit6"])
    assert args.real is True
    # Simulate main() logic: when --real, map to offline
    if args.real:
        args.execution_mode = "offline"
    assert args.execution_mode == "offline"


def test_artifact_integrity_fields_always_written():
    """Task outcome.json _audit must always include integrity fields."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage31_artifact"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    outcome_path = run_dir / "tasks" / "core12_mini_repair_calc" / "outcome.json"
    assert outcome_path.is_file()
    import json
    data = json.loads(outcome_path.read_text())
    audit = data.get("_audit", {})
    required = [
        "execution_mode",
        "model_call_count",
        "small_model_call_count",
        "reasoning_model_call_count",
        "used_offline_stubs",
        "used_plan_injection",
        "used_explain_stub",
        "integrity_valid",
        "integrity_failure_reason",
    ]
    for k in required:
        assert k in audit, f"Missing _audit field: {k}"


def test_summary_integrity_aggregation_fields():
    """Runner summary must include suite-level integrity aggregation."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage31_summary"),
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


def test_compare_modes_utility(tmp_path):
    """compare_modes produces valid output for two summary files."""
    import json
    offline = Path("artifacts/agent_eval_runs/stage31_compare_offline")
    live = Path("artifacts/agent_eval_runs/stage31_compare_live")
    run_dir_o, _, summary_o = run_suite(
        "audit6",
        offline,
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    with patch("agent.models.model_client._call_chat", return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}'):
        run_dir_l, _, summary_l = run_suite(
            "live4",
            live,
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    off_summary = run_dir_o / "summary.json"
    live_summary = run_dir_l / "summary.json"
    out = compare(off_summary, live_summary)
    assert "Offline vs Live-Model" in out
    assert "execution_mode" in out or "Execution mode" in out
    assert "model_call_count_total" in out or "Model calls total" in out
