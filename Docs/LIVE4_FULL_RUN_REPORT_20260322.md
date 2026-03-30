# Live4 Full Run Report — All Edit Tasks

**Date:** 2026-03-22  
**Suite:** live4 (4 tasks, live model)  
**Task timeout:** 180s  
**Duration:** 165.95s (~2.8 min)  
**Run directory:** `artifacts/agent_eval_runs/20260322_155102_cc6f53`  
**Log file:** `docs/live4_full_run_20260322_155102.txt`

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total tasks** | 4 |
| **success_count** | 3 (75%) |
| **validation_pass_count** | 3 (75%) |
| **structural_success_count** | 2 (50%) |
| **patches_applied_total** | 13 |
| **files_modified_total** | 2 |
| **Model calls** | 37 (8 small, 29 reasoning) |

---

## Per-Task Results

| Task | Success | Validation | Structural | Patches | Reject Reason |
|------|---------|------------|------------|---------|---------------|
| core12_mini_repair_calc | ✓ | ✓ | ✓ | 6 | — |
| core12_mini_repair_parse | ✓ | ✓ | ✓ | 6 | — |
| core12_mini_feature_flags | ✓ | ✓ | ✗ | 0 | no_meaningful_diff |
| core12_pin_typer_repair | ✗ | ✗ | ✗ | 1 | validation_tests_failed |

---

## Task Details

### ✓ core12_mini_repair_calc
**Instruction:** Repair src/calc/ops.py so multiply(2, 3) == 6 and tests pass.  
**Result:** Full success. Patch applied, validation passed, structural success.

### ✓ core12_mini_repair_parse
**Instruction:** Fix tokenize() in src/parse/split.py to split on whitespace.  
**Result:** Full success. Patch applied, validation passed, structural success.

### ✓ core12_mini_feature_flags (validation pass, no structural)
**Instruction:** Add beta_enabled() -> bool in src/flags/store.py; returns False by default.  
**Result:** Validation passed (tests run ok), but structural_success=false.  
**Reject:** `no_meaningful_diff` — model produced insert patch targeting `is_verbose`; patch was rejected. File was modified (diff_stat: +3 lines) but patches_applied=0 suggests the insert was rejected by the executor (e.g. wrong symbol, no meaningful change). Semantic RCA: `unchanged_target_region`.

### ✗ core12_pin_typer_repair
**Instruction:** Fix benchmark_local/bench_math.double so double(3) == 6.  
**Result:** Failed. failure_bucket: `validation_regression`, first_failing_stage: EDIT.  
**Reject:** `validation_tests_failed` — 1 patch applied (to test_bench_math.py) but tests failed.  
**Root cause:** Model edited the **test file** (test_bench_math.py), adding `def test_double(x): assert double(x) == x * 2`, instead of fixing `double()` in **bench_math.py**. Task requires fixing the source (double function), not the test. Target resolution chose bench_math.py but the patch was applied to test_bench_math.py — wrong-file edit.

---

## Failure Bucket Histogram

| Bucket | Count |
|--------|-------|
| validation_regression | 1 |

---

## Patch Reject Histogram

| Reason | Count |
|--------|-------|
| no_meaningful_diff | 1 |
| validation_tests_failed | 1 |

---

## Pipeline Health

| Stage | Status |
|-------|--------|
| Intent routing | ✓ |
| Planner | ✓ |
| Search/retrieval | ✓ |
| Edit proposal (evidence consistency) | ✓ No target_not_found |
| Patch execution | ✓ 13 patches applied |
| Validation | 3/4 passed |

---

## Evidence Consistency Fix — Verified

No `target_not_found` or `STATE_INCONSISTENCY` in this run. The evidence ↔ file consistency invariant is holding; repair tasks (calc, parse) now succeed.

---

## Recommendations

1. **core12_pin_typer_repair:** Improve target resolution so the edit step edits the source file (bench_math.py), not the test. The instruction explicitly names `benchmark_local/bench_math.double`; ensure the plan and edit_binding target that file.

2. **core12_mini_feature_flags:** The insert patch was rejected as `no_meaningful_diff`. The model targeted symbol `is_verbose`; the task requires adding a new function at module level. Consider passing module-level insert hints when the instruction asks to "add a new function."

---

## Artifacts

- **Log:** `docs/live4_full_run_20260322_155102.txt`
- **Summary:** `artifacts/agent_eval_runs/20260322_155102_cc6f53/summary.json`
- **Outcomes:** `artifacts/agent_eval_runs/20260322_155102_cc6f53/tasks/<task_id>/outcome.json`
