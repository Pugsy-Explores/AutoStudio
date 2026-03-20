"""
Stage 22: Semantic wrong-patch RCA and root-cause classification.

Conservative heuristic classifier for failed EDIT tasks. Does not overclaim.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from agent.retrieval.task_semantics import instruction_edit_target_paths

SemanticRcaCause = Literal[
    "wrong_target_file",
    "wrong_symbol_or_anchor",
    "patch_applied_but_behavior_unchanged",
    "patch_applied_but_wrong_behavior",
    "validation_scope_mismatch",
    "no_edit_attempted",
    "ambiguous_instruction_or_missing_path",
    "no_effect_change",
    "unchanged_target_region",
    "no_meaningful_diff",
    "weakly_grounded_patch",
    # Stage 24: grounded generation distinguishes
    "no_grounded_candidate_found",      # grounded layer ran but found zero evidence-backed candidates
    "grounded_candidate_wrong_behavior",  # grounded candidate applied but validation failed
    "ambiguous_target_resolution",        # no explicit target; planner/retrieval couldn't resolve file
    # Stage 25: target resolution and validation contamination
    "validation_script_selected_as_target",  # validation script was chosen instead of source file
    "ambiguous_module_descriptor",          # instruction had module descriptor but resolution failed
    "likely_import_shadowing_or_env_conflict",  # validation failed with stdlib shadow (io, logging)
    "source_target_inferred_but_patch_wrong_behavior",  # correct target, wrong patch
    # Stage 26: semantic patch quality
    "grounded_candidate_semantically_misaligned",  # semantic check rejected before apply
    "requested_symbol_not_implemented",   # Add fname() but patch doesn't define fname
    "requested_literal_not_realized",     # instruction says return X but patch doesn't produce X
    "correct_target_wrong_region",        # right file, wrong symbol/region
    "unknown",
]


def _merge_edit_telemetry(loop_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Get edit_telemetry from loop snapshot (supports hierarchical phase_results)."""
    et = loop_snapshot.get("edit_telemetry") if isinstance(loop_snapshot, dict) else None
    if isinstance(et, dict) and et:
        return et
    for pr in reversed(loop_snapshot.get("phase_results") or []):
        if not isinstance(pr, dict):
            continue
        lo = pr.get("loop_output")
        if isinstance(lo, dict):
            e2 = lo.get("edit_telemetry")
            if isinstance(e2, dict) and e2:
                return e2
    return et if isinstance(et, dict) else {}


def _instruction_has_explicit_path(instruction: str) -> bool:
    """True if instruction mentions an explicit file path for the edit target."""
    return bool(instruction_edit_target_paths(instruction or ""))


def _retrieved_top_paths(loop_snapshot: dict[str, Any]) -> list[str]:
    """Extract top retrieved paths from loop snapshot (from ranked_context or similar)."""
    paths: list[str] = []
    et = _merge_edit_telemetry(loop_snapshot)
    attempted = et.get("attempted_target_files")
    if isinstance(attempted, list):
        for p in attempted[:10]:
            if isinstance(p, str) and p.strip():
                paths.append(p.strip())
    return paths


def classify_wrong_patch_root_cause(
    *,
    success: bool,
    structural_success: bool,
    validation_passed: bool,
    failure_bucket: str | None,
    loop_snapshot: dict[str, Any],
    validation_logs: list[dict[str, Any]],
    instruction: str,
) -> SemanticRcaCause:
    """
    Heuristic classifier for failed EDIT tasks. Conservative; returns unknown when unsure.
    """
    if success:
        return "unknown"

    et = _merge_edit_telemetry(loop_snapshot)
    patches_applied = et.get("patches_applied") or et.get("patches_applied_this_attempt") or 0
    patch_apply_ok = et.get("patch_apply_ok")
    pr = et.get("patch_reject_reason")
    ef = et.get("edit_failure_reason")
    pe = et.get("patch_effectiveness") if isinstance(et.get("patch_effectiveness"), dict) else {}
    per = pe.get("patch_effective_reason") if isinstance(pe, dict) else None

    # Stage 24: grounded generation — specific sub-causes before broader buckets.
    gen_reject = et.get("generation_rejected_reason")
    grounded_count = et.get("grounded_candidate_count")
    grounded_strategy = et.get("patch_candidate_strategy")

    # If grounded layer explicitly reported no candidates, use the finer cause.
    if gen_reject == "no_grounded_candidate_found" or (
        grounded_count == 0 and (ef == "weakly_grounded_patch" or pr == "weakly_grounded_patch")
    ):
        return "no_grounded_candidate_found"

    # Stage 26: semantic rejection before apply
    sem_reject = et.get("candidate_rejected_semantic_reason")
    if sem_reject:
        if sem_reject == "requested_symbol_not_implemented":
            return "requested_symbol_not_implemented"
        if sem_reject == "requested_literal_not_realized":
            return "requested_literal_not_realized"
        if sem_reject in ("rename_missing_old_or_new_evidence", "align_candidate_modifies_neither"):
            return "grounded_candidate_semantically_misaligned"
    # Grounded candidate was found and applied, but validation still failed.
    if grounded_strategy and (
        failure_bucket == "validation_regression" or pr == "validation_tests_failed"
    ):
        # Stage 25: import shadowing is env/import issue, not patch quality
        if et.get("likely_stdlib_shadowing"):
            return "likely_import_shadowing_or_env_conflict"
        # When target was inferred from validation imports, use finer cause
        tr = et.get("target_resolution") or {}
        chosen = (et.get("chosen_target_file") or "").replace("\\", "/")
        inferred = list((tr.get("inferred_sources") or {}).keys())
        if chosen and inferred and any(chosen.endswith("/" + i) or i in chosen for i in inferred):
            return "source_target_inferred_but_patch_wrong_behavior"
        return "grounded_candidate_wrong_behavior"

    # Stage 25: validation script was selected as edit target (contamination)
    tr = et.get("target_resolution") or {}
    chosen = (et.get("chosen_target_file") or et.get("chosen_edit_target") or "").replace("\\", "/")
    val_scripts = [v.replace("\\", "/") for v in (tr.get("validation_scripts") or [])]
    def _chosen_is_validation_script() -> bool:
        if not chosen:
            return False
        return chosen in val_scripts or any(chosen.endswith("/" + vs) or vs in chosen for vs in val_scripts)
    if _chosen_is_validation_script() and (ef == "weakly_grounded_patch" or pr == "weakly_grounded_patch"):
        return "validation_script_selected_as_target"

    # Stage 25: import shadowing when validation failed and telemetry says so
    if pr == "validation_tests_failed" and et.get("likely_stdlib_shadowing"):
        return "likely_import_shadowing_or_env_conflict"

    # Stage 23: patch quality / grounding rejects (before broader wrong-behavior bucket)
    if ef == "weakly_grounded_patch" or pr == "weakly_grounded_patch":
        return "weakly_grounded_patch"
    for key in (pr, per):
        if key in ("no_effect_change", "unchanged_target_region", "no_meaningful_diff"):
            return key  # type: ignore[return-value]

    chosen_file = et.get("chosen_target_file")
    chosen_symbol = et.get("chosen_symbol")
    attempted = et.get("attempted_target_files") or []
    explicit_paths = instruction_edit_target_paths(instruction or "")

    # No edit attempted at all
    if not structural_success and (not attempted or (isinstance(attempted, list) and len(attempted) == 0)):
        if not _instruction_has_explicit_path(instruction) and not explicit_paths:
            return "ambiguous_instruction_or_missing_path"
        return "no_edit_attempted"

    # Edit grounding failure: patch never applied
    if not structural_success and not patch_apply_ok:
        if pr in ("validation_tests_failed",) or ef == "test_failure":
            # Patch applied but validation failed - handled below
            pass
        else:
            if ef in ("symbol_not_found", "patch_anchor_not_found", "target_not_found"):
                return "wrong_symbol_or_anchor"
            if ef in ("patch_anchor_not_found", "target_not_found") or "wrong file" in str(pr or "").lower():
                return "wrong_target_file"
            return "no_edit_attempted"

    # Patch applied but validation failed (dominant adversarial12 mode)
    # When we rollback after validation failure, structural_success is False but patch_reject_reason
    # is validation_tests_failed — treat as patch_applied_but_wrong_behavior
    if (failure_bucket == "validation_regression" or pr == "validation_tests_failed") and (
        structural_success or patches_applied > 0 or patch_apply_ok
    ):
        if _edit_rca_snippets_identical(et):
            return "unchanged_target_region"
        return "patch_applied_but_wrong_behavior"

    if structural_success and not validation_passed and patches_applied > 0:
        touched_val = et.get("patch_touched_validation_path")
        val_cmd = et.get("resolved_validation_command") or et.get("validation_command")
        val_path = _extract_validation_path(val_cmd)

        # Validation scope mismatch: we edited files the test doesn't exercise
        if touched_val is False and chosen_file and val_path:
            # Modified file is not the test file; test may exercise different module
            if chosen_file != val_path and not _path_overlaps(chosen_file, val_path):
                return "validation_scope_mismatch"

        # Explicit path in instruction but we chose a different file
        if explicit_paths and chosen_file:
            if not any(_path_overlaps(chosen_file, p) for p in explicit_paths):
                return "wrong_target_file"

        # We have no strong signal - could be wrong behavior or wrong symbol
        if _edit_rca_snippets_identical(et):
            return "unchanged_target_region"
        return "patch_applied_but_wrong_behavior"

    # Ambiguous instruction / target resolution failure
    if not _instruction_has_explicit_path(instruction) and not chosen_file and not structural_success:
        # Stage 24: distinguish "instruction ambiguous" from "target couldn't be resolved"
        if not attempted:
            return "ambiguous_target_resolution"
        return "ambiguous_instruction_or_missing_path"

    return "unknown"


def _extract_validation_path(cmd: str | None) -> str | None:
    if not cmd or not isinstance(cmd, str):
        return None
    m = re.search(r"[\w./\\]+\.py", cmd)
    return m.group(0).replace("\\", "/") if m else None


def _edit_rca_snippets_identical(et: dict[str, Any]) -> bool:
    """True when before/after RCA snippets match for some edited file (no visible edit effect)."""
    before = et.get("edit_rca_before_snippets")
    after = et.get("edit_rca_after_snippets")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    for k, vb in before.items():
        if not isinstance(k, str) or not isinstance(vb, str):
            continue
        va = after.get(k)
        if isinstance(va, str) and va == vb and vb.strip():
            return True
    return False


def _path_overlaps(a: str, b: str) -> bool:
    """True if paths refer to same file (normalized)."""
    if not a or not b:
        return False
    an = a.replace("\\", "/").strip()
    bn = b.replace("\\", "/").strip()
    return an == bn or an.endswith("/" + bn) or bn.endswith("/" + an)


def build_semantic_rca_dict(
    *,
    task_id: str,
    task_type: str,
    instruction: str,
    success: bool,
    structural_success: bool,
    validation_passed: bool,
    failure_bucket: str | None,
    first_failing_stage: str | None,
    loop_snapshot: dict[str, Any],
    validation_logs: list[dict[str, Any]],
    edit_telemetry: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build compact semantic_rca.json artifact for failed EDIT tasks.
    """
    et = edit_telemetry or _merge_edit_telemetry(loop_snapshot)
    cause = classify_wrong_patch_root_cause(
        success=success,
        structural_success=structural_success,
        validation_passed=validation_passed,
        failure_bucket=failure_bucket,
        loop_snapshot=loop_snapshot,
        validation_logs=validation_logs or [],
        instruction=instruction,
    )

    val_cmd = et.get("resolved_validation_command") or et.get("validation_command")
    val_summary = None
    for log in validation_logs or []:
        if isinstance(log, dict):
            s = (log.get("stdout") or "") + (log.get("stderr") or "")
            if s.strip():
                val_summary = s[:500].strip()
                break

    return {
        "task_id": task_id,
        "task_type": task_type,
        "instruction": instruction[:500],
        "explicit_path_present": bool(instruction_edit_target_paths(instruction or "")),
        "retrieved_top_paths": _retrieved_top_paths(loop_snapshot),
        "chosen_edit_target": et.get("chosen_target_file"),
        "chosen_symbol": et.get("chosen_symbol"),
        "patch_strategy": et.get("patch_strategies"),
        "patch_plan_summary": et.get("patch_plan_summary"),
        "validation_command": val_cmd,
        "reject_reason": et.get("patch_reject_reason"),
        "failure_bucket": failure_bucket,
        "first_failing_stage": first_failing_stage,
        "patch_applied": bool(
            et.get("patch_apply_ok")
            and (
                (et.get("patches_applied") or 0) > 0
                or (et.get("patch_reject_reason") == "validation_tests_failed" or failure_bucket == "validation_regression")
            )
        ),
        "validation_failed_after_apply": structural_success and not validation_passed,
        "target_likely_mismatched": cause in ("wrong_target_file", "wrong_symbol_or_anchor", "validation_scope_mismatch"),
        "guessed_root_cause": cause,
        "validation_failure_summary": val_summary,
        "patch_effectiveness": et.get("patch_effectiveness"),
        # Stage 24: grounded generation telemetry
        "grounded_candidate_count": et.get("grounded_candidate_count"),
        "selected_candidate_rank": et.get("selected_candidate_rank"),
        "patch_candidate_strategy": et.get("patch_candidate_strategy"),
        "patch_candidate_evidence_type": et.get("patch_candidate_evidence_type"),
        "patch_candidate_evidence_excerpt": et.get("patch_candidate_evidence_excerpt"),
        "generation_rejected_reason": et.get("generation_rejected_reason"),
        # Stage 25: target resolution and import telemetry
        "target_resolution": et.get("target_resolution"),
        "chosen_target_file": et.get("chosen_target_file"),
        "likely_stdlib_shadowing": et.get("likely_stdlib_shadowing"),
        "module_names_in_validation_error": et.get("module_names_in_validation_error"),
        # Stage 26: semantic patch telemetry
        "requested_return_value": et.get("requested_return_value"),
        "requested_symbol_name": et.get("requested_symbol_name"),
        "candidate_semantic_match_score": et.get("candidate_semantic_match_score"),
        "candidate_rejected_semantic_reason": et.get("candidate_rejected_semantic_reason"),
        "selected_candidate_out_of_n": et.get("selected_candidate_out_of_n"),
        "semantic_expectation_type": et.get("semantic_expectation_type"),
    }
