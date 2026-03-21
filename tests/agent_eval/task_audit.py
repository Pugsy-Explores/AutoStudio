"""
Stage 32 — Task audit enrichment.

Responsibilities:
- build_task_audit_dict(outcome, spec) — build _audit for outcome.json
"""

from __future__ import annotations

from typing import Any

def build_task_audit_dict(outcome: Any, spec: Any) -> dict[str, Any]:
    """Build _audit dict for outcome.json. Includes integrity fields (Stage 31)."""
    et = outcome.extra.get("edit_telemetry") or {}
    mau = (outcome.extra.get("model_usage_audit") or {}) if isinstance(outcome.extra.get("model_usage_audit"), dict) else {}

    retrieval_telemetry = None
    for pr in (outcome.loop_output_snapshot.get("phase_results") or []):
        if isinstance(pr, dict):
            co = pr.get("context_output") or {}
            if isinstance(co, dict) and "retrieval_telemetry" in co:
                retrieval_telemetry = co.get("retrieval_telemetry")
                break

    retrieval_fallback = []
    if isinstance(et, dict):
        if et.get("reranker_failed"):
            retrieval_fallback.append("reranker_failed")
        if et.get("reranker_failed_fallback_used"):
            retrieval_fallback.append("reranker_fallback_used")
        if et.get("bm25_available") is False and et.get("reranker_failed"):
            retrieval_fallback.append("bm25_unavailable")

    val_cmd = None
    if outcome.validation_logs:
        val_cmd = outcome.validation_logs[0].get("command")

    audit: dict[str, Any] = {
        "structural_success": outcome.structural_success,
        "grading_mode": outcome.grading_mode,
        "exception": outcome.extra.get("exception"),
        "index": outcome.extra.get("index"),
        "failure_bucket": outcome.extra.get("failure_bucket"),
        "first_failing_stage": outcome.extra.get("first_failing_stage"),
        "execution_mode": outcome.extra.get("execution_mode"),
        "model_call_count": mau.get("model_call_count", 0),
        "small_model_call_count": mau.get("small_model_call_count", 0),
        "reasoning_model_call_count": mau.get("reasoning_model_call_count", 0),
        "used_offline_stubs": outcome.extra.get("used_offline_stubs"),
        "used_plan_injection": outcome.extra.get("used_plan_injection"),
        "used_explain_stub": outcome.extra.get("used_explain_stub"),
        "integrity_valid": outcome.extra.get("integrity_valid"),
        "integrity_failure_reason": outcome.extra.get("integrity_failure_reason"),
        "retrieval_telemetry": retrieval_telemetry,
        "edit_telemetry": et,
        "patch_reject_reason": et.get("patch_reject_reason") if isinstance(et, dict) else None,
        "validation_scope_kind": et.get("validation_scope_kind") if isinstance(et, dict) else None,
        "changed_files_count": et.get("changed_files_count") if isinstance(et, dict) else None,
        "patches_applied": et.get("patches_applied") if isinstance(et, dict) else None,
        "files_modified": outcome.files_changed,
        "retrieval_fallback_flags": retrieval_fallback,
        "target_selection_telemetry": (
            {
                "attempted_target_files": et.get("attempted_target_files") if isinstance(et, dict) else None,
                "chosen_target_file": et.get("chosen_target_file") if isinstance(et, dict) else None,
                "search_viable_file_hits": et.get("search_viable_file_hits") if isinstance(et, dict) else None,
            }
            if isinstance(et, dict)
            else None
        ),
        "selected_validation_command": val_cmd,
        # Stage 39/40: live routing-contract eval reads this from outcome.json
        "plan_resolution_telemetry": outcome.extra.get("plan_resolution_telemetry"),
    }
    return audit
