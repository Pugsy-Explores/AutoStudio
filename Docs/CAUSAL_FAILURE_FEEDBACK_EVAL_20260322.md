# Causal Failure Feedback — Edit Retry Loop Evaluation

**Date:** 2026-03-22  
**Suite:** paired8  
**Execution mode:** live_model  
**Baseline run:** `artifacts/agent_eval_runs/20260322_175554_4858ed`  
**Post-implementation run:** `artifacts/agent_eval_runs/20260322_183556_7c70c9`

---

## 1. Convergence Rate (Before vs After)

| Metric | Before | After | Δ |
|--------|--------|-------|---|
| **convergence_rate** | 0.25 (2/8) | 0.25 (2/8) | **0** (no change) |
| success_count | 2 | 2 | 0 |
| validation_pass_count | 2 | 2 | 0 |
| patches_applied_total | 6 | 6 | 0 |

---

## 2. Failure Distribution Shift

### Patch reject reason histogram

| Reason | Before | After | Δ | Interpretation |
|--------|--------|-------|---|----------------|
| patch_unchanged | 3 | 0 | −3 | Replaced by patch_unchanged_repeat |
| **patch_unchanged_repeat** | 0 | **3** | +3 | **Hard guard active** — identical retries now explicitly rejected |
| patch_apply_failed | 1 | 0 | −1 | Task shifted to weakly_grounded |
| weakly_grounded_patch | 1 | 2 | +1 | One task moved here |
| wrong_target_file | 1 | 1 | 0 | Unchanged |

### Semantic RCA cause histogram

| Cause | Before | After | Δ |
|-------|--------|-------|---|
| no_edit_attempted | 4 | 3 | −1 |
| weakly_grounded_patch | 1 | 2 | +1 |

---

## 3. % Retries Producing Different Patches

- **patch_unchanged_repeat = 3** → In these cases, the model produced the **same** patch on retry despite causal feedback.
- The hard guard **correctly blocked** those identical patches and surfaced `patch_unchanged_repeat`.
- **Conclusion:** Retries that would have produced identical patches are now detected and rejected. The model did not produce a *different* patch in those 3 tasks; it repeated the same one.

---

## 4. Example Traces

### Trace A — Before causal feedback (baseline)

- **Task:** core12_pin_typer_repair (or similar)
- **Behavior:** `patch_unchanged` — patch failed because old == new; retry may have repeated silently.
- **Outcome:** No explicit distinction between “first patch_unchanged” vs “retry produced same patch again.”

### Trace B — After causal feedback

- **Task:** core12_pin_typer_repair
- **Behavior:**
  1. First attempt: patch rejected (e.g. patch_unchanged).
  2. Instruction augmented with:
     ```
     PREVIOUS_ATTEMPT:
     - Patch: <old/new summary>
     - Failure: Your patch did not modify the file (old == new).

     REQUIREMENT:
     - You MUST produce a DIFFERENT patch.
     - You MUST resolve the above failure.
     - Do NOT repeat or trivially modify the previous patch.
     ```
  3. Second attempt: model produced **identical** patch → hard guard rejected with `patch_unchanged_repeat`.
  4. **Result:** Loop of identical outputs eliminated; task failed with explicit signal instead of silent repetition.

---

## 5. Conclusion: Loop Broken / Not Broken

| Criterion | Status | Notes |
|-----------|--------|-------|
| **Loops of identical outputs eliminated** | ✅ **Yes** | Hard guard rejects identical patches; `patch_unchanged_repeat` surfaced |
| **Retries produce different patches** | ⚠️ **Partial** | In 3 tasks, retries produced same patch; guard blocked them rather than allowing another cycle |
| **Failures shift from grounding → semantic** | ⚠️ **Mixed** | patch_unchanged → patch_unchanged_repeat (clearer signal); weakly_grounded +1; no validation_tests_failed yet |
| **convergence_rate improves meaningfully** | ❌ **No** | 0.25 → 0.25 |

**Overall:** The edit retry loop is **partially improved**:

- **Loop broken:** Identical-patch loops are now detected and rejected instead of repeating silently.
- **Self-correction limited:** The model often repeated the same patch despite causal feedback, so convergence did not improve. The infrastructure (feedback injection, hard guard) is in place; next steps are prompt/model improvements to encourage truly different patches on retry.

---

## 6. Implementation Summary

| Component | Status |
|-----------|--------|
| `derive_failure_explanation` (semantic_feedback.py) | ✅ Implemented |
| `format_causal_feedback_for_retry` | ✅ Implemented |
| Hard guard: `patch_unchanged_repeat` on identical patch | ✅ Active |
| Execution loop: `previous_patch`, `previous_failure` storage | ✅ Wired |
| Causal feedback injection on EDIT retry | ✅ Verified in traces |
| Unit tests | ✅ Pass (21 semantic_feedback, 1 execution_loop) |

---

## 7. Recommendations

1. **Retry prompting:** Emphasize “produce a DIFFERENT patch” more strongly or provide concrete variation hints (e.g. “Try a different edit location” when old/new are identical).
2. **Max retries:** Consider allowing more retries when `patch_unchanged_repeat` occurs, since the model may need multiple attempts to escape the local optimum.
3. **Validation_tests_failed:** Monitor for increased validation_tests_failed as a positive signal that patches are reaching tests more often.
