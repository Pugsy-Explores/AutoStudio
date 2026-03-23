# Minimal Edit Pipeline — Deep RCA Report

**Date:** 2026-03-23  
**Suite:** paired8 (core12_mini_repair_calc and full suite runs)  
**Mode:** `ENABLE_MINIMAL_EDIT_PIPELINE=1`

---

## 1. Executive Summary

| Metric | Finding |
|--------|---------|
| **core12_mini_repair_calc** | **SUCCESS** (validation_passed, patches_applied=6) |
| **Bottleneck Hypothesis** | Generation/grounding when validation layers block; minimal mode confirms patches can flow when layers are bypassed |
| **Key Signal** | Patches applied successfully when syntax/verification/structural checks are skipped |

---

## 2. Run Evidence

### 2.1 core12_mini_repair_calc (live_model_1)

**outcome.json:**
- `success`: true  
- `validation_passed`: true  
- `patches_applied`: 6  
- `edit_failure_reason`: null  
- `patch_reject_reason`: null  

**edit_telemetry.json:**
- `patches_applied`: 6  
- `changed_files_count`: 1  
- `edit_failure_reason`: null  
- `chosen_target_file`: src/calc/ops.py  

**semantic_rca.json (intermediate/prior attempt):**
- `failure_bucket`: edit_grounding_failure  
- `patch_applied`: false  
- `validation_failure_summary`: `assert 7 == 6` (multiply still returned 7)  
- Indicates at least one EDIT attempt failed before final success  

---

## 3. Interpretation — Three Cases

### Case A: patch_apply_success ↑ and tests fail → validation was blocking

**Evidence:** Minimal pipeline run shows success for core12_mini_repair_calc. With validation layers bypassed, patches flowed through and tests passed.

**Conclusion:** For this task, validation layers were **not** the primary blocker. The system reached success with the minimal path.

### Case B: patch_apply still fails → generation/grounding is root cause

**Evidence:** Previous runs (comparison.json, semantic_rca) show `edit_grounding_failure`, `no_grounded_candidate_found`, `patch_applied: false`. When grounding fails, minimal pipeline cannot help—there is no valid patch to apply.

**Conclusion:** For tasks that fail with `weakly_grounded_patch` or `no_valid_patch_candidate`, the bottleneck is **generation/grounding**, not validation.

### Case C: tests pass → system is fundamentally correct

**Evidence:** core12_mini_repair_calc succeeded with minimal pipeline. Model produced correct patch `{"old": "return a * b + 1", "new": "return a * b"}` and it was applied.

**Conclusion:** For well-grounded, simple repair tasks, the pipeline is capable of success. Validation layers may add safety but are not required for basic correctness in these cases.

---

## 4. Root Cause Analysis

### 4.1 Validation vs. Generation

| Failure Type | Likely Root Cause | Minimal Pipeline Effect |
|--------------|-------------------|-------------------------|
| `patch_apply_failed` | syntax_validation, patch_verification, or execute_patch reject | **Bypassed** — patches reach disk |
| `weakly_grounded_patch` | to_structured_patches, generate_edit_proposals | **No change** — no patch to apply |
| `no_changes` | plan_diff produces empty changes | **No change** |
| `validation_tests_failed` | Patch applied but tests fail | **Exposed** — clear signal when apply works, tests don’t |
| `target_not_found` | OLD snippet not in file (stale context) | **Bypass helps** — no pre-apply verification |

### 4.2 Observed Flow (from terminal logs)

1. Model produces valid patch: `{"action": "text_sub", "old": "return a * b + 1", "new": "return a * b"}`  
2. Full file content in subsequent calls shows `return a * b` — patch was applied  
3. Model then proposes noop-style edits (`return a * b * 1` → `return a * b`) — file already correct  
4. Plan had multiple EDIT steps (2 for ops, 4 for tests); 6 patches applied total  

### 4.3 [minimal_pipeline] Logging

`logger.info` for `[minimal_pipeline]` did not appear in stdout (logging config). `print()` was added so future runs will show:

```
[minimal_pipeline] patch_generated=True changes_count=1
[minimal_pipeline] patch_apply_ok=True
[minimal_pipeline] tests_passed=True
```

---

## 5. Recommendations

### 5.1 For Diagnosis

1. **Run full paired8 with minimal pipeline** and compare to baseline:
   - `convergence_rate` (minimal vs. full)
   - `patch_apply_success` (expect ↑ if validation was blocking)
   - `validation_tests_failed` (expect new bucket when apply succeeds but tests fail)

2. **Per-task failure buckets:**  
   If `edit_grounding_failure` dominates, focus on generation/retrieval. If `patch_apply_failed` drops with minimal mode, focus on validation.

### 5.2 For Production

1. **Reintroduce layers incrementally** in this order:
   - `validate_syntax_plan` (syntax correctness)
   - `verify_patch_plan` (targeting, locality)
   - `check_structural_improvement` (retry quality)
   - `failure_state` / stagnation logic

2. **Relax or tune** `patch_verification` `has_effect` for inserts when `code in full_file_content` — can reject valid duplicates.

3. **Relax** `is_instruction_satisfied` for no-op paths — current heuristic can reject valid completions when tests pass.

### 5.3 Metrics to Track

| Metric | Purpose |
|--------|---------|
| `patch_apply_success` | % of EDIT steps where patch reached disk |
| `validation_tests_failed` | % where apply ok but tests fail |
| `edit_grounding_failure` | % where no grounded patch produced |
| `convergence_rate` | % of tasks reaching validation pass |

---

## 6. Final Verdict

- **Minimal pipeline** is functioning as intended: patch → apply → test, with validation/verification bypassed.  
- **core12_mini_repair_calc** succeeded under minimal mode.  
- **Primary bottleneck** varies by task:
  - **Grounding failures** → generation/retrieval (plan_diff, edit_proposals, context).  
  - **Validation failures** → syntax/verification layers; minimal mode relieves these.  
- **Next step:** Run full paired8 with `ENABLE_MINIMAL_EDIT_PIPELINE=1` and compare `convergence_rate` and failure-bucket distribution to baseline.
