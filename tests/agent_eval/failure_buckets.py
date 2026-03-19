"""Primary failure bucket classification for benchmark outcomes (Stage 12.1 + Stage 13)."""

from __future__ import annotations

from typing import Any, Literal

FailureBucket = Literal[
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
]

_EDIT_GROUNDING_CODES = frozenset(
    {
        "symbol_not_found",
        "target_is_directory",
        "empty_patch",
        "no_changes_planned",
        "patch_anchor_not_found",
        "patch_apply_conflict",
        "invalid_patch_syntax",
        "non_source_target",
        "target_not_found",
        "patch_failed",
        "no_changes",
        "max_attempts_exceeded",
    }
)


def classify_failure_bucket(
    *,
    success: bool,
    structural_success: bool,
    validation_passed: bool,
    failure_class: str | None,
    loop_snapshot: dict[str, Any],
    validation_logs: list[dict[str, Any]],
    notes: str,
    index_ok: bool | None,
) -> FailureBucket:
    """
    Single primary bucket for failed runs. Successful tasks should not call this for scoring
    (return value is undefined); callers may pass success=True and get ``unknown``.
    """
    if success:
        return "unknown"

    et = loop_snapshot.get("edit_telemetry") if isinstance(loop_snapshot, dict) else None
    if not isinstance(et, dict):
        et = {}

    text = " ".join(
        [
            notes or "",
            failure_class or "",
            str(loop_snapshot.get("parent_goal_reason", "")),
            str(loop_snapshot.get("errors_encountered", "")),
        ]
    ).lower()
    vtext = ""
    for log in validation_logs:
        vtext += (log.get("stderr") or "") + (log.get("stdout") or "")
    vlow = vtext.lower()

    if index_ok is False or "index_failed" in (notes or ""):
        return "infra_or_stub_failure"

    if "recursionerror" in text or "reranker inference failed" in text:
        return "infra_or_stub_failure"

    et_early = loop_snapshot.get("edit_telemetry") if isinstance(loop_snapshot, dict) else None
    if not isinstance(et_early, dict):
        et_early = {}
    pr_early = et_early.get("patch_reject_reason")
    er_early = et_early.get("edit_failure_reason")
    if pr_early == "validation_tests_failed" or er_early == "test_failure":
        return "validation_regression"

    errs_list = loop_snapshot.get("errors_encountered") or []
    if not structural_success and isinstance(errs_list, list) and errs_list:
        joined = " ".join(str(e).lower() for e in errs_list)
        if "edit" in joined or "patch" in joined:
            return "edit_grounding_failure"

    if "exception" in (failure_class or "") or "traceback" in text:
        if "fixture" in text or "index_failed" in text:
            return "infra_or_stub_failure"
        return "unknown"

    edit_reason = et.get("edit_failure_reason")

    if isinstance(edit_reason, str) and edit_reason in _EDIT_GROUNDING_CODES:
        return "edit_grounding_failure"

    viable = et.get("search_viable_file_hits")
    attempted = et.get("attempted_target_files") or []
    if (
        not structural_success
        and isinstance(viable, int)
        and viable == 0
        and not (isinstance(attempted, list) and len(attempted) > 0)
    ):
        return "search_targeting_failure"

    if not structural_success and isinstance(attempted, list) and len(attempted) == 0:
        if "retriev" in text or ("empty" in text and "context" in text):
            return "retrieval_miss"
        if "ranked_context" in text and "[]" in str(loop_snapshot):
            return "retrieval_miss"
        if "goal_not" in text or "goal_not_met" in text:
            return "planner_wasted_motion"
        if "phase_validation_failed" in text or failure_class == "phase_validation_failed":
            return "bad_patch_shape"
        if "wrong file" in text or "file_target" in text:
            return "bad_file_targeting"

    if structural_success and not validation_passed:
        if "assert" in vlow or "failed" in vlow or "error" in vlow:
            return "validation_regression"
        if "syntax" in vlow or "indent" in vlow:
            return "bad_patch_shape"

    if "ambiguous" in text or "missing" in text and "context" in text:
        return "ambiguity_or_missing_context"

    if not validation_passed and validation_logs and structural_success:
        return "validation_regression"

    if not structural_success and edit_reason and edit_reason != "test_failure":
        return "edit_grounding_failure"

    if not validation_passed and validation_logs:
        return "validation_regression"

    return "unknown"
