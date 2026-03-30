"""
Stage 32 — Suite summary aggregation.

Responsibilities:
- aggregate_integrity_metrics(results, execution_mode)
- aggregate_histograms(results)
- build_per_task_outcomes(results)
- build_suite_label(suite_name, execution_mode)
"""

from __future__ import annotations

from typing import Any


def aggregate_integrity_metrics(results: list, execution_mode: str) -> dict[str, Any]:
    """Aggregate integrity metrics from task outcomes (Stage 28 + 31)."""
    live_model_task_count = sum(1 for r in results if (r.extra or {}).get("execution_mode") == "live_model")
    tasks_with_model_calls = sum(
        1 for r in results
        if ((r.extra or {}).get("model_usage_audit") or {}).get("model_call_count", 0) > 0
    )
    tasks_without_model_calls = sum(
        1 for r in results
        if ((r.extra or {}).get("model_usage_audit") or {}).get("model_call_count", 0) == 0
        and (r.extra or {}).get("execution_mode") == "live_model"
    )
    integrity_failure_count = sum(
        1 for r in results
        if (r.extra or {}).get("live_model_integrity_ok") is False
        and (r.extra or {}).get("execution_mode") == "live_model"
    )
    integrity_failure_hist: dict[str, int] = {}
    for r in results:
        if (r.extra or {}).get("execution_mode") == "live_model":
            reason = (r.extra or {}).get("integrity_failure_reason") or "unknown"
            integrity_failure_hist[reason] = integrity_failure_hist.get(reason, 0) + 1
    used_offline_stubs_count = sum(1 for r in results if (r.extra or {}).get("used_offline_stubs") is True)
    used_plan_injection_count = sum(1 for r in results if (r.extra or {}).get("used_plan_injection") is True)
    run_valid_for_live_eval = (
        execution_mode == "live_model"
        and live_model_task_count > 0
        and integrity_failure_count == 0
        and tasks_with_model_calls == live_model_task_count
    )
    invalid_live_model_task_count = sum(
        1 for r in results
        if (r.extra or {}).get("execution_mode") == "live_model"
        and (r.extra or {}).get("integrity_valid") is not True
    )
    zero_model_call_task_count = sum(
        1 for r in results
        if ((r.extra or {}).get("model_usage_audit") or {}).get("model_call_count", 0) == 0
    )
    offline_stubbed_task_count = used_offline_stubs_count
    explain_stubbed_task_count = sum(1 for r in results if (r.extra or {}).get("used_explain_stub") is True)
    plan_injection_task_count = used_plan_injection_count
    model_call_count_total = sum(
        ((r.extra or {}).get("model_usage_audit") or {}).get("model_call_count", 0) for r in results
    )
    small_model_call_count_total = sum(
        ((r.extra or {}).get("model_usage_audit") or {}).get("small_model_call_count", 0) for r in results
    )
    reasoning_model_call_count_total = sum(
        ((r.extra or {}).get("model_usage_audit") or {}).get("reasoning_model_call_count", 0) for r in results
    )
    return {
        "live_model_task_count": live_model_task_count,
        "tasks_with_model_calls": tasks_with_model_calls,
        "tasks_without_model_calls": tasks_without_model_calls,
        "integrity_failure_count": integrity_failure_count,
        "integrity_failure_histogram": integrity_failure_hist,
        "used_offline_stubs_count": used_offline_stubs_count,
        "used_plan_injection_count": used_plan_injection_count,
        "run_valid_for_live_eval": run_valid_for_live_eval,
        "invalid_live_model_task_count": invalid_live_model_task_count,
        "zero_model_call_task_count": zero_model_call_task_count,
        "offline_stubbed_task_count": offline_stubbed_task_count,
        "explain_stubbed_task_count": explain_stubbed_task_count,
        "plan_injection_task_count": plan_injection_task_count,
        "model_call_count_total": model_call_count_total,
        "small_model_call_count_total": small_model_call_count_total,
        "reasoning_model_call_count_total": reasoning_model_call_count_total,
    }


def aggregate_histograms(results: list) -> dict[str, Any]:
    """Aggregate failure_bucket, patch_reject_reason, etc. histograms."""
    bucket_hist: dict[str, int] = {}
    reject_hist: dict[str, int] = {}
    validation_scope_hist: dict[str, int] = {}
    first_failing_stage_hist: dict[str, int] = {}
    patches_applied_total = 0
    files_modified_total = 0
    attempts_agg = 0
    retries_agg = 0
    replans_agg = 0
    for r in results:
        b = (r.extra or {}).get("failure_bucket")
        if b:
            bucket_hist[str(b)] = bucket_hist.get(str(b), 0) + 1
        ffs = (r.extra or {}).get("first_failing_stage")
        if ffs:
            first_failing_stage_hist[str(ffs)] = first_failing_stage_hist.get(str(ffs), 0) + 1
        et = (r.extra or {}).get("edit_telemetry") or {}
        if isinstance(et, dict):
            vsk = et.get("validation_scope_kind")
            if vsk:
                validation_scope_hist[str(vsk)] = validation_scope_hist.get(str(vsk), 0) + 1
            pr = et.get("patch_reject_reason")
            if pr:
                reject_hist[str(pr)] = reject_hist.get(str(pr), 0) + 1
            try:
                patches_applied_total += int(et.get("patches_applied") or 0)
            except (TypeError, ValueError):
                pass
            try:
                files_modified_total += int(et.get("changed_files_count") or 0)
            except (TypeError, ValueError):
                pass
        if isinstance(r.attempts_total, int):
            attempts_agg += r.attempts_total
        if isinstance(r.retries_used, int):
            retries_agg += r.retries_used
        if isinstance(r.replans_used, int):
            replans_agg += r.replans_used
    return {
        "failure_bucket_histogram": bucket_hist,
        "patch_reject_reason_histogram": reject_hist,
        "validation_scope_kind_histogram": validation_scope_hist,
        "first_failing_stage_histogram": first_failing_stage_hist,
        "patches_applied_total": patches_applied_total,
        "files_modified_total": files_modified_total,
        "attempts_total_aggregate": attempts_agg,
        "retries_used_aggregate": retries_agg,
        "replans_used_aggregate": replans_agg,
    }


def build_per_task_outcomes(results: list) -> list[dict[str, Any]]:
    """Build per_task_outcomes list for summary."""
    per_task = []
    for r in results:
        et = (r.extra or {}).get("edit_telemetry") or {}
        pt = {
            "task_id": r.task_id,
            "success": r.success,
            "validation_passed": r.validation_passed,
            "structural_success": r.structural_success,
            "attempts_total": r.attempts_total,
            "retries_used": r.retries_used,
            "replans_used": r.replans_used,
            "failure_bucket": (r.extra or {}).get("failure_bucket"),
            "first_failing_stage": (r.extra or {}).get("first_failing_stage"),
            "files_modified": r.files_changed,
        }
        if isinstance(et, dict):
            pt["patch_reject_reason"] = et.get("patch_reject_reason")
            pt["validation_scope_kind"] = et.get("validation_scope_kind")
            pt["changed_files_count"] = et.get("changed_files_count")
            pt["patches_applied"] = et.get("patches_applied")
        per_task.append(pt)
    return per_task


def build_suite_label(suite_name: str, execution_mode: str) -> str:
    """Build suite label for summary (e.g. audit12_offline, live4_live)."""
    offline_like = execution_mode in ("real", "offline")
    if offline_like and suite_name == "audit12":
        return "audit12_offline"
    if offline_like and suite_name == "holdout8":
        return "holdout8_offline"
    if offline_like and suite_name == "adversarial12":
        return "adversarial12_offline"
    if offline_like and suite_name == "external6":
        return "external6_offline"
    if execution_mode == "live_model" and suite_name == "live4":
        return "live4_live"
    if execution_mode == "live_model" and suite_name == "paired4":
        return "paired4_live"
    if offline_like and suite_name == "paired4":
        return "paired4_offline"
    if execution_mode == "live_model" and suite_name == "paired8":
        return "paired8_live"
    if offline_like and suite_name == "paired8":
        return "paired8_offline"
    return f"{suite_name}_offline" if offline_like else suite_name
