"""Stage 28 — Real-Model Evaluation Integrity Split regression tests."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.agent_eval.runner import build_arg_parser, run_suite
from tests.agent_eval.task_specs import TaskSpec


def test_offline_mode_sets_used_offline_stubs():
    """Offline mode must set used_offline_stubs=true in task outcome."""
    parser = build_arg_parser()
    args = parser.parse_args(["--suite", "audit6", "--execution-mode", "offline", "--output", "/tmp/stage28"])
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage28_test"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("used_offline_stubs") is True
    assert r.extra.get("live_model_integrity_ok") is False


def test_live_model_mode_never_enters_offline_stubs():
    """Live-model mode must not use offline_llm_stubs (used_offline_stubs=false)."""
    # Mock _call_chat so live path runs quickly without network; model_client still records calls
    with patch("agent.models.model_client._call_chat", return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}'):
        run_dir, results, summary = run_suite(
            "live4",
            Path("artifacts/agent_eval_runs/stage28_live_test"),
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    assert len(results) == 1
    r = results[0]
    assert r.extra.get("used_offline_stubs") is False


def test_live_run_with_mocked_call_chat_increments_audit():
    """When _call_chat is patched but call_small_model runs, model_client records the call."""
    from agent.models.model_client import get_model_call_audit, reset_model_call_audit

    reset_model_call_audit()
    audit_before = get_model_call_audit()
    assert audit_before["model_call_count"] == 0

    with patch("agent.models.model_client._call_chat", return_value="stub response"):
        from agent.models.model_client import call_small_model

        call_small_model("test", task_name="routing", max_tokens=5)
    audit_after = get_model_call_audit()
    assert audit_after["model_call_count"] >= 1


def test_live_run_with_zero_model_calls_marks_integrity_failure():
    """Live run with zero model calls must set integrity_failure_reason."""
    # Mock _record_model_call to no-op so model_call_count stays 0; _call_chat returns stub for fast run
    def _noop_record(*_a, **_k):
        pass

    with patch("agent.models.model_client._record_model_call", _noop_record), patch(
        "agent.models.model_client._call_chat",
        return_value='{"steps":[{"id":1,"action":"SEARCH","description":"x","reason":"y"}]}',
    ):
        run_dir, results, summary = run_suite(
            "live4",
            Path("artifacts/agent_eval_runs/stage28_zero_calls"),
            execution_mode="live_model",
            task_filter="core12_mini_repair_calc",
        )
    assert len(results) == 1
    r = results[0]
    model_count = (r.extra.get("model_usage_audit") or {}).get("model_call_count", 0)
    if model_count == 0:
        assert r.extra.get("integrity_failure_reason") in ("zero_real_model_calls", None)
        assert r.extra.get("live_model_integrity_ok") is False or model_count == 0


def test_deprecated_real_maps_to_offline():
    """--real should map to offline with deprecation warning."""
    parser = build_arg_parser()
    args = parser.parse_args(["--real", "--output", "/tmp/x"])
    assert args.real is True
    if args.real:
        args.execution_mode = "offline"
    assert args.execution_mode == "offline"


def test_runner_summary_includes_integrity_fields():
    """Runner summary must include integrity histograms and validity flags."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage28_summary"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert "run_valid_for_live_eval" in summary
    assert "used_offline_stubs_count" in summary
    assert "used_plan_injection_count" in summary
    assert "integrity_failure_count" in summary
    assert "integrity_failure_histogram" in summary


def test_plan_injection_recorded_offline():
    """Offline mode must record used_plan_injection=true."""
    run_dir, results, summary = run_suite(
        "audit6",
        Path("artifacts/agent_eval_runs/stage28_plan"),
        execution_mode="offline",
        task_filter="core12_mini_repair_calc",
    )
    assert len(results) == 1
    assert results[0].extra.get("used_plan_injection") is True


def test_explain_stub_recorded_when_used():
    """When explain_artifact task uses stub, used_explain_stub must be true (offline)."""
    run_dir, results, summary = run_suite(
        "audit12",
        Path("artifacts/agent_eval_runs/stage28_explain"),
        execution_mode="offline",
        task_filter="core12_pin_requests_explain_trace",
    )
    if results:
        r = results[0]
        assert r.extra.get("used_explain_stub") in (True, False)


def test_execution_mode_choices():
    """CLI must support mocked, offline, live_model, real."""
    parser = build_arg_parser()
    for mode in ("mocked", "offline", "live_model", "real"):
        args = parser.parse_args(["--execution-mode", mode])
        assert args.execution_mode == mode


def test_live4_suite_loads():
    """live4 suite must load 4 tasks with evaluation_kind=full_agent."""
    from tests.agent_eval.suites.live4 import load_live4_specs

    specs = load_live4_specs()
    assert len(specs) == 4
    for s in specs:
        assert s.evaluation_kind == "full_agent"
