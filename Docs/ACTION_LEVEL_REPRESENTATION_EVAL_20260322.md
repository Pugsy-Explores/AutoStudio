# Action-Level Representation — Edit Retry Eval

**Date:** 2026-03-22  
**Suite:** paired8  
**Execution mode:** live_model  
**Stateful baseline:** `artifacts/agent_eval_runs/20260322_185855_f44c64`  
**Action-level run:** `artifacts/agent_eval_runs/20260322_192725_e2130b`

---

## 1. Updated Convergence Rate

| Metric | Stateful (before) | Action-level (after) | Δ |
|--------|-------------------|----------------------|---|
| **convergence_rate** | 0.25 (2/8) | 0.25 (2/8) | **0** |
| success_count | 2 | 2 | 0 |
| patches_applied_total | 6 | 6 | 0 |
| model_call_count_total | 72 | 72 | 0 |

---

## 2. no_progress_repeat Delta

| Reason | Stateful | Action-level | Δ |
|--------|----------|--------------|---|
| no_progress_repeat | 5 | 5 | **0** |
| weakly_grounded_patch | 1 | 1 | 0 |

---

## 3. % Retries Producing Different Patches

- **no_progress_repeat = 5** — In 5 tasks, retries still produced patches that matched `attempted_patches` (patch_signature equality).
- Action summaries are now human-readable (e.g. `Edited code in benchmark_local/bench_math.py: return n → return n // 2`) but the model continued to repeat in these cases.
- **Conclusion:** Representation change implemented; convergence unchanged for this run.

---

## 4. Example Trace — Different Second Attempt (core12_mini_feature_flags)

**First attempt (failed):**
```
- Edited store in src/flags/store.py:  → def beta_enabled() -> bool:
    return F
- Known failures: The OLD snippet does not exist in the current file content.
```

**Second attempt:** Model produced an `insert` with `target_node: "function_body_start"` and `code: "def beta_enabled() -> bool:\n    return False"`. Different code (`return False` vs `return F`), but still rejected (no_progress_repeat or weakly_grounded). Final outcome: patch_reject_reason `no_progress_repeat` for this task.

**benchmark_local/bench_math.py example:**
```
- Previous attempts:
  - Edited code in benchmark_local/bench_math.py: return n → return n // 2
```
Model sees an interpretable summary; in this trace it still repeated the same transformation on retry.

---

## 5. Implementation Verification

| Change | Status |
|--------|--------|
| `summarize_patch_action` in semantic_feedback.py | ✅ |
| `attempted_actions` in failure_state | ✅ |
| Retry prompt uses action summaries | ✅ Verified in traces |
| "Do NOT modify the same location or apply the same transformation again" | ✅ Added |
| patch_signature enforcement unchanged | ✅ |
| Unit tests | ✅ 31 passed |

---

## 6. Conclusion

- **Retry prompt:** Now shows human-readable action summaries (e.g. "Edited code in X: old → new") instead of opaque signatures.
- **Convergence:** No change (0.25).
- **no_progress_repeat:** Still 5; model continues to emit identical patches in several tasks.
- **Recommendation:** Representation change is in place. Next steps: stronger variation prompting, or earlier strategy explorer when stagnation is detected.
