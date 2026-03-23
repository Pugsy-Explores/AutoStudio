# SYSTEM PATCH — REMOVE HIDDEN EXECUTION BLOCKERS (FINAL REACT ALIGNMENT)

Surgical correction to achieve true execution-driven ReAct loop.

---

## 1. Changes Made (File + Function Level)

### agent/runtime/execution_loop.py

| Location | Change |
|----------|--------|
| **no_changes path** (~L423) | When tests fail: retry (inject RAW_TEST_OUTPUT + NO_CHANGES_RETRY), `continue` instead of return failure |
| **already_correct path** (~L469) | When tests fail: retry (inject RAW_TEST_OUTPUT + ALREADY_CORRECT_RETRY), `continue` instead of return failure |
| **MAX_PATCH_FILES / MAX_PATCH_LINES** (~L447) | Remove early return; log warning only, proceed with execution |
| **Patch apply failure feedback** (~L688) | Append `RAW_FAILURE_OUTPUT` (patch_apply error) to instruction |
| **Test failure feedback** (~L847) | Append `RAW_TEST_OUTPUT` (stdout/stderr) to instruction; always set (not only when fb_text) |

### agent/execution/policy_engine.py

| Location | Change |
|----------|--------|
| **_execute_search** (~L466) | When REACT_MODE: skip rewriter; use `queries_to_try = [retrieval_input]` only |

---

## 2. Before vs After Behavior

| Scenario | Before | After |
|----------|--------|--------|
| **weakly_grounded_patch** | (already fixed) Always execute_patch | Same |
| **REACT_MODE SEARCH** | query_variants + rewriter on retry | retrieval_input only, no rewrite |
| **REACT_MODE EDIT** | symbol_retry (mutated steps) | retry_same (model output = executed) |
| **no_changes + tests fail** | Return failure | Retry with raw test output injected |
| **already_correct + tests fail** | Return failure | Retry with raw test output injected |
| **MAX_PATCH over limit** | Reject before execute_patch | Log warning, proceed |
| **Retry instruction** | format_stateful_feedback only | + RAW_FAILURE_OUTPUT / RAW_TEST_OUTPUT (actual stdout/stderr, patch error) |

---

## 3. Confirmation Checklist

| Check | Status |
|-------|--------|
| Execution always attempted (no pre-execution block) | ✓ |
| No policy mutation in REACT_MODE (query_variants, symbol_retry, rewriter disabled) | ✓ |
| Raw failures passed to model (RAW_TEST_OUTPUT, RAW_FAILURE_OUTPUT) | ✓ |
| no_changes / already_correct + test fail → retry (not immediate fail) | ✓ |
| MAX_PATCH limits → warn only, no block | ✓ |

---

## 4. Final EDIT Loop

```
generate_patch (plan_diff → to_structured_patches)
  → execute_patch (always)
  → [patch fail] validate_project skipped; rollback; inject RAW_FAILURE_OUTPUT; retry
  → [patch ok] validate_project
  → run_tests
  → [pass] success
  → [fail] inject RAW_TEST_OUTPUT + fb_text; rollback; retry
  → until max_attempts
```

No condition blocks execution before execute_patch. No synthetic failures before execution.

---

## 5. Remaining Edge Cases

- **patch_executor limits**: `editing/patch_executor.py` still has MAX_FILES_PER_EDIT and MAX_PATCH_LINES (200). These can reject inside execute_patch. Per "surgical" scope, not modified; execution loop no longer blocks.
- **syntax_error**: validate_project failure still returns immediately (does not retry). Catastrophic syntax crash path; kept as-is per original spec.
- **REACT_MODE**: Opt-in via `REACT_MODE=1`. Default 0 preserves existing mutation behavior.

---

## 6. Exact Fixes (5 Surgical)

| Fix | Change |
|-----|--------|
| **1 — enforce execution path** | Dispatcher: EDIT bypasses policy_engine; calls `_edit_fn` directly. No pre-execution decision layer. |
| **2 — critic after execution only** | ✓ Already correct: `_critic_and_retry` only at patch-fail (L692) and test-fail (L852). |
| **3 — remove pre-execution gating** | ✓ Already correct: no `if not confident: call_critic()`. `confident` used for logging only. |
| **4 — relax retry constraint** | `semantic_feedback.py`: "Do NOT modify same location" → "Avoid identical patches; modifying same location is allowed if needed" |
| **5 — assert execution happened** | `StepResult.executed`, `run_edit_test_fix_loop` returns `executed`, `_edit_fn` passes it; dispatcher and executor assert `executed or is_precondition` for EDIT. |

---

## 7. Root Cause Fix — Policy Engine Bypass (Items 7–12)

**Root cause:** Policy engine had a pre-execution decision layer (symbol_retry, retry_on) that could route EDIT before execution.

**Fix:**
1. **Dispatcher:** EDIT now calls `_edit_fn` directly, bypassing `_policy_engine.execute_with_policy`. No policy layer between Planner and Execution.
2. **policy_engine._execute_edit:** Simplified to single `_edit_fn` call. No conditional routing, no symbol_retry, no critic before execution. (Retained for any alternate callers.)
3. **Hard invariant:** Dispatcher asserts `executed or is_precondition` after EDIT; fails loudly if neither.
4. **Planner fallback:** Edit-intent fallback produces SEARCH + EDIT steps; EDIT steps reach execution via dispatch → _edit_fn.

**Architecture:** Planner → Execution → Critic (was: Planner → Policy → Critic → maybe execution).
