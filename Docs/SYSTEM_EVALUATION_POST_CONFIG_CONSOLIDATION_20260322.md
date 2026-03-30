# System Evaluation — Post-Config Consolidation

**Date:** 2026-03-22  
**Suite:** paired8  
**Execution mode:** live_model  
**Task timeout:** 180s  
**Run directory:** `artifacts/agent_eval_runs/20260322_175554_4858ed`  
**Duration:** 527.9s (~8.8 min)

---

## 1. Metrics

| Metric | Value |
|--------|-------|
| **convergence_rate** | 0.25 (2/8) |
| **patches_applied_total** | 6 |
| **structural_success_count** | 1 |
| **validation_pass_count** | 2 |
| **success_count** | 2 |
| **total_tasks** | 8 |

---

## 2. Delta vs Previous Baseline

| Metric | Previous | Current | Δ |
|--------|----------|---------|---|
| convergence_rate | 0.25 | 0.25 | **0** (no change) |
| dominant failures | edit_grounding_failure | edit_grounding_failure | **unchanged** |

---

## 3. Failure Breakdown

### Failure bucket histogram
| Bucket | Count | % of failures |
|--------|-------|---------------|
| edit_grounding_failure | 5 | 83.3% |
| unknown | 1 | 16.7% |

### Patch reject reason histogram
| Reason | Count |
|--------|-------|
| patch_unchanged | 3 |
| patch_apply_failed | 1 |
| weakly_grounded_patch | 1 |
| wrong_target_file | 1 |

### First failing stage
| Stage | Count |
|-------|-------|
| EDIT | 5 |
| SEARCH | 1 |

### Semantic RCA cause histogram
| Cause | Count |
|-------|-------|
| no_edit_attempted | 4 |
| weakly_grounded_patch | 1 |

---

## 4. Shift in Failure Distribution (Expected vs Actual)

| Category | Expected | Actual |
|----------|----------|--------|
| edit_grounding_failure ↓↓↓ | Decrease | **Still dominant (5/6)** ✗ |
| patch_unchanged ↓↓↓ | Decrease | **Still high (3)** ✗ |
| patch_apply_failed ↓↓↓ | Decrease | **Present (1)** ✗ |
| validation_tests_failed ↑ | Increase (good) | **0** ✗ |

**Conclusion:** No improvement in failure distribution. Grounding failures remain dominant.

---

## 5. Representative Traces

### Trace 1 — Success: core12_mini_repair_calc

**Instruction:** Repair src/calc/ops.py so multiply(2, 3) == 6 and tests pass.

**Result:** Full success. Patches applied: 6. Structural success: true.

**Trace summary:**
- Target: `src/calc/ops.py`, symbol `multiply`
- Patch strategy: model_generated (text_sub)
- Patch applied successfully; validation passed
- Edit telemetry: patch_parse_ok=true, patch_apply_ok=true, patch_reject_reason=null

---

### Trace 2 — Failure: core12_mini_feature_flags (edit_grounding_failure)

**Instruction:** Add beta_enabled() -> bool in src/flags/store.py; returns False by default.

**Result:** Failed. failure_bucket: edit_grounding_failure. patch_reject_reason: patch_apply_failed.

**Trace summary:**
- Target: `src/flags/store.py`, symbol `is_verbose` (wrong symbol — task asks for new function `beta_enabled`)
- Patch strategy: model_generated (structured)
- patch_parse_ok=true, patch_apply_ok=false
- edit_failure_reason: patch_apply_failed
- Model targeted existing symbol `is_verbose` instead of adding new module-level function

---

## 6. Conclusion

### System health: **UNSTABLE**

**Classification criteria:**
- convergence_rate ≥ 0.6 → **NO** (0.25)
- patches_applied_total significantly higher → **NO** (6 total; 1 task contributed 6, 1 task 0; 6 others had 0)

### Next bottleneck: **Generation still broken**

Grounding failures remain dominant (5/6 failures). Key issues:
1. **patch_unchanged** (3) — Model producing no-op patches or patches that don't match current file state
2. **patch_apply_failed** (1) — Insert/symbol targeting wrong location (e.g. is_verbose vs new function)
3. **weakly_grounded_patch** (1) — Patch not properly grounded to evidence
4. **wrong_target_file** (1) — Edit applied to wrong file

### Recommendations

1. **Edit proposal prompt:** Improve guidance for "add new function" vs "modify existing symbol". Pass explicit module-level insert hints when instruction asks for a new function.
2. **Patch unchanged:** Re-read file before retry; reject no-op patches (old == new) before applying.
3. **Target resolution:** Ensure edit_binding targets the correct file when instruction names a specific module/function.
4. **Evidence consistency:** Continue to enforce evidence ↔ full_content consistency (this appears to be holding for calc/parse; feature/docs tasks still fail).

---

## Artifacts

- **Summary:** `artifacts/agent_eval_runs/20260322_175554_4858ed/summary.json`
- **Outcomes:** `artifacts/agent_eval_runs/20260322_175554_4858ed/tasks/<task_id>/outcome.json`
- **Run command:** `python3 -m tests.agent_eval.runner --suite paired8 --execution-mode live_model --task-timeout 180`
