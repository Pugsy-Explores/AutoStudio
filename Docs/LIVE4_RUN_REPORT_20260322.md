# Live4 Run Report — 2026-03-22

**Run:** `live4` suite, execution mode `live_model`  
**Run directory:** `artifacts/agent_eval_runs/20260322_050417_3ed94f`  
**Duration:** 152.5 seconds  
**Timestamp:** 2026-03-22 05:04:17

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total tasks** | 4 |
| **Success** | 1 (25%) |
| **Validation passed** | 1 |
| **Structural success** | 0 |
| **Patches applied (total)** | 0 |

**Root cause:** All 3 failures are **EDIT grounding failures**. The agent reaches the EDIT stage with good retrieval context, but the patch validator rejects proposed edits as `weakly_grounded_patch` or `no_grounded_candidate_found`. No patches are applied in any failed task.

---

## Per-Task Results

| task_id | success | validation_passed | failure_bucket | first_failing_stage | patch_reject_reason |
|---------|---------|-------------------|----------------|---------------------|---------------------|
| core12_mini_repair_calc | ✗ | ✗ | edit_grounding_failure | EDIT | weakly_grounded_patch |
| core12_mini_repair_parse | ✗ | ✗ | edit_grounding_failure | EDIT | weakly_grounded_patch |
| core12_mini_feature_flags | ✓ | ✓ | — | — | weakly_grounded_patch* |
| core12_pin_typer_repair | ✗ | ✗ | edit_grounding_failure | EDIT | weakly_grounded_patch |

*core12_mini_feature_flags passed validation and modified files despite patch_reject_reason; grading mode or validation path may differ.

---

## Failure Analysis

### 1. Failure Buckets

```
failure_bucket_histogram:     {"edit_grounding_failure": 3}
patch_reject_reason_histogram: {"weakly_grounded_patch": 4}
first_failing_stage_histogram: {"EDIT": 3}
semantic_rca_cause_histogram:  {"no_grounded_candidate_found": 3}
```

All 3 failed tasks fail at the **EDIT** stage with `edit_grounding_failure`. The semantic RCA labels the root cause as `no_grounded_candidate_found`.

### 2. Retrieval Quality (Successful Context)

For **core12_mini_repair_calc** (representative failed task):

| Field | Value |
|-------|-------|
| has_impl_in_pool | true |
| final_has_signal | true |
| selection_loss | false |
| pool_has_signal | true |
| final_context_count | 6 |
| retrieval_empty | false |
| implementation_body_present_count | 6 |
| top_files | src/calc/ops.py, tests/test_ops.py |

Retrieval is functioning: the agent finds the implementation, tests, and related symbols. The problem occurs when turning that context into an accepted patch.

### 3. Edit Telemetry (core12_mini_repair_calc)

| Field | Value |
|-------|-------|
| generation_rejected_reason | no_grounded_candidate_found |
| edit_failure_reason | weakly_grounded_patch |
| patch_reject_reason | weakly_grounded_patch |
| chosen_target_file | (empty) |
| chosen_symbol | ops |
| edit_targets_ranked | [] |
| search_viable_file_hits | 5 |
| ranked_context_items | 6 |

The edit step has viable file hits and ranked context, but `edit_targets_ranked` is empty and `chosen_target_file` is blank. The grounding layer does not produce a grounded edit candidate that passes validation.

### 4. Edit Failure Stage Diagnostics

```
edit_failure_stage_histogram: {"UNKNOWN": 4}
```

`edit_failure_stage` is UNKNOWN for all 4 tasks because `answer_supported` is `null` for validation_exit_code tasks (it is set only for explain_artifact grading). The classifier expects `answer_supported is False` for EDIT_GROUNDING_FAILURE; when it is null, it falls through to UNKNOWN.

**Conclusion from diagnostics:** Retrieval and selection are healthy (has_impl_in_pool, final_has_signal). Failures occur in the edit-grounding / patch-validation layer.

---

## Pipeline Health

| Stage | Status |
|-------|--------|
| Planner | ✓ Working (SEARCH_CANDIDATES, BUILD_CONTEXT, EDIT plans accepted) |
| Execution | ✓ Working (tasks reach EDIT) |
| Retrieval | ✓ Working (pool has impl, final has signal) |
| Selection | ✓ Working (no selection_loss) |
| Edit grounding | ✗ **Failure** (weakly_grounded_patch, no_grounded_candidate_found) |

---

## Integrity

- **run_valid_for_live_eval:** true
- **invalid_live_model_task_count:** 0
- **zero_model_call_task_count:** 0
- **model_call_count_total:** 32 (16 small, 16 reasoning)
- **offline_stubbed_count:** 0
- **plan_injection_count:** 0

---

## Recommendations

1. **Root cause:** Focus on the edit-grounding / patch-validation path. The agent has the right context but fails to produce a grounded edit candidate that passes `weakly_grounded_patch` checks.

2. **Diagnostics:** Consider extending `classify_edit_failure` so that when `failure_reason == "GROUNDING_FAILURE"` or `edit_failure_reason == "weakly_grounded_patch"`, the stage is classified as EDIT_GROUNDING_FAILURE even when `answer_supported` is null.

3. **Investigations:**
   - Why is `edit_targets_ranked` empty when retrieval returns good context?
   - Why is `chosen_target_file` empty while `chosen_symbol` is set?
   - What grounding criteria cause `no_grounded_candidate_found`?

---

## Answer to Diagnostic Question

> "Are failures caused by retrieval, selection, or edit grounding?"

**Edit grounding.** Retrieval and selection are functioning; failures occur when generating or validating edits against the grounded context.
