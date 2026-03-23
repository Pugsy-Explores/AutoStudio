# Stateful Failure Representation — Edit Retry Flow Evaluation

**Date:** 2026-03-22  
**Suite:** paired8  
**Execution mode:** live_model  
**Causal feedback baseline:** `artifacts/agent_eval_runs/20260322_183556_7c70c9`  
**Stateful run:** `artifacts/agent_eval_runs/20260322_185855_f44c64`

---

## 1. Convergence Rate Delta

| Metric | Causal Feedback (before) | Stateful (after) | Δ |
|--------|--------------------------|------------------|---|
| **convergence_rate** | 0.25 (2/8) | 0.25 (2/8) | **0** (unchanged) |
| success_count | 2 | 2 | 0 |
| patches_applied_total | 6 | 6 | 0 |
| model_call_count_total | 84 | 72 | −12 |

---

## 2. % Retries Producing New Patches

- **no_progress_repeat = 5** (up from patch_unchanged_repeat 3 in causal feedback run)
- The stateful check now generalizes: any patch whose signature is in `attempted_patches` is rejected, not just the immediate previous patch
- Model call count dropped (72 vs 84): retries may terminate earlier when stagnation is detected
- **Conclusion:** Retries that produce previously-seen patches are now blocked across all attempts; the model often repeated despite FAILURE_STATE injection

---

## 3. Stagnation vs Progress Ratio

### Patch reject reason histogram

| Reason | Causal Feedback | Stateful | Δ |
|--------|-----------------|----------|---|
| patch_unchanged | 0 | 0 | - |
| patch_unchanged_repeat | 3 | 0 | −3 (replaced by no_progress_repeat) |
| **no_progress_repeat** | 0 | **5** | +5 |
| patch_apply_failed | 0 | 0 | - |
| weakly_grounded_patch | 2 | 1 | −1 |
| wrong_target_file | 1 | 0 | −1 |

- **Stagnation signal:** no_progress_repeat surfaced in 5 tasks; repeated patches are blocked before execution
- **Progress:** wrong_target_file and one weakly_grounded case moved to no_progress_repeat (model produced same patch after different failures)

---

## 4. Example Trace — State Evolution

### Trace: core12_pin_typer_repair (typer benchmark)

**Attempt 1:** Patch applied; tests failed.
```
Tests failed: benchmark_local/test_bench_math.py::test_double: assert 3 == 6; test_halve: assert 4 == 2
```

**Attempt 2 (retry):** FAILURE_STATE injected:
```
FAILURE_STATE:
- Known failures:
  - Tests failed: benchmark_local/test_bench_math.py::test_double: assert 3 == 6; ...
- Previous attempts (signatures):
  - benchmark_local/bench_math.py||return n->return n // 2
- Stagnation count: 0

REQUIREMENT:
- You MUST produce a patch that is different from previous attempts.
- You MUST address at least one of the known failures.
- Avoid repeating previously attempted changes.
```

**Attempt 3:** Model produced same or previously-attempted patch → rejected with `no_progress_repeat`; FAILURE_STATE updated with stagnation_count; loop eventually exits.

---

## 5. Classification

| Criterion | Status |
|-----------|--------|
| **Still stuck** | ⚠️ Partially — convergence unchanged |
| **Partially improving** | ✅ Yes — repeated patches eliminated across all retries |
| **Converging** | ❌ No — convergence_rate unchanged |

**Overall:** **Partially improving**

- **Stateful representation:** failure_state accumulates across attempts; FAILURE_STATE block is injected on retry
- **Repeated patches eliminated:** no_progress_repeat blocks any patch whose signature is in attempted_patches
- **Stagnation detection:** stagnation_count increments on repeats; termination when dominant + stagnation >= MAX_STAGNATION
- **Convergence:** Still 0.25; model often repeats patches despite stateful feedback

---

## 6. Implementation Summary

| Component | Status |
|-----------|--------|
| `context["failure_state"]` (failures, attempted_patches, stagnation_count) | ✅ Implemented |
| `_update_failure_state` (accumulate, detect stagnation) | ✅ Implemented |
| `format_stateful_feedback_for_retry` | ✅ Implemented |
| `check_structural_improvement(..., attempted_patches)` | ✅ Returns no_progress_repeat |
| Retry termination on stagnation + dominant | ✅ Implemented |
| Unit tests | ✅ Pass (test_failure_state_accumulates, test_reject_repeated_patch_no_progress, test_stagnation_terminates_no_progress) |

---

## 7. Failure Distribution Shift (Expected vs Actual)

| Category | Expected | Actual |
|----------|----------|--------|
| patch_unchanged_repeat → 0 | ✓ | **0** (generalized to no_progress_repeat) |
| no_progress_repeat visible | ✓ | **5** |
| validation_tests_failed ↑ | ? | 0 (no new validation failures surfaced) |
| patches_applied_total ↑ | ? | **6** (unchanged) |
| convergence_rate ↑ | ? | **0.25** (unchanged) |

---

## 8. Recommendations

1. **Prompt tuning:** Emphasize variation more strongly; consider "Try a different edit location or approach" when stagnation_count > 0
2. **MAX_STAGNATION:** Consider raising from 2 to 3 to allow more exploration before termination
3. **Patch diversity:** Track % of retries that produce new patches; if low, consider plan-level replanning (e.g. strategy explorer) earlier
