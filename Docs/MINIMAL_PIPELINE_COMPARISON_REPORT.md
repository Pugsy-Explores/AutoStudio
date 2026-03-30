# Minimal Pipeline vs Baseline — 3 EDIT Tasks Comparison

**Tasks:** core12_mini_repair_calc, core12_pin_typer_repair, core12_mini_feature_flags  
**Date:** 2026-03-23

---

## 1. Convergence Rate

| Mode | Success | Total | Convergence |
|------|---------|-------|-------------|
| **Baseline** (full pipeline) | 1 | 3 | **33%** |
| **Minimal** (validation bypassed) | 1 | 3 | **33%** |

**Finding:** Convergence rate is **identical**. Minimal pipeline did not improve overall success.

---

## 2. Per-Task Outcomes

| Task | Baseline | Minimal |
|------|----------|---------|
| **core12_mini_repair_calc** | ✅ SUCCESS | ✅ SUCCESS |
| **core12_pin_typer_repair** | ❌ edit_grounding_failure, no_progress_repeat, patches=0 | ❌ edit_grounding_failure, patches=0, **files_modified: [bench_math.py]** |
| **core12_mini_feature_flags** | ❌ edit_grounding_failure, no_progress_repeat, patches=0 | ❌ unknown, patches=0, **files_modified: [store.py]** |

---

## 3. Failure Buckets

| Bucket | Baseline | Minimal |
|--------|----------|---------|
| edit_grounding_failure | 2 | 1 |
| unknown | 0 | 1 |
| (success) | 1 | 1 |

---

## 4. Patch Reject Reasons

| Reason | Baseline | Minimal |
|--------|----------|---------|
| no_progress_repeat | **2** | **0** |
| (none) | 1 | 3 |

**Finding:** `no_progress_repeat` (from check_structural_improvement) was blocking 2 tasks in baseline. Minimal pipeline bypasses this; those rejects disappear.

---

## 5. Key Observations

### 5.1 Patches Reached Disk (Minimal Only)

- **core12_pin_typer_repair:** `files_modified: [benchmark_local/bench_math.py]` — patch was applied but validation failed.
- **core12_mini_feature_flags:** `files_modified: [src/flags/store.py]` — patch was applied but validation failed.

In baseline, both failed with `no_progress_repeat` before any patch was applied.

### 5.2 Patch Apply Failure (Minimal)

Log excerpt:
```
[patch_executor] text_sub ast.parse failed: expected an indented block after function definition on line 5
[minimal_pipeline] patch_generated=True changes_count=1
[minimal_pipeline] patch_apply_ok=False
```

The model produced invalid syntax (`def beta_enabled() -> bool:\n    return False` — malformed). execute_patch rejected it. This is a **generation** error, not validation.

### 5.3 Root Cause Shift

| Task | Baseline Blocking Point | Minimal Blocking Point |
|------|-------------------------|-------------------------|
| core12_mini_repair_calc | — (success) | — (success) |
| core12_pin_typer_repair | check_structural_improvement (no_progress_repeat) | Patch applied → **tests failed** |
| core12_mini_feature_flags | check_structural_improvement (no_progress_repeat) | Patch applied → **tests failed** |

---

## 6. Conclusions

1. **Convergence unchanged** — 1/3 in both modes. Minimal pipeline did not raise success rate.

2. **Validation layer was blocking** — `no_progress_repeat` prevented patches from being applied in baseline for 2 tasks. With minimal, those patches were applied.

3. **Tests fail after apply** — For typer_repair and feature_flags, patches reached disk but validation tests failed. So the pipeline progressed further, but the edits were incorrect.

4. **Bottleneck split:**
   - **Structural check** — Was blocking; bypassing it allowed patches through.
   - **Generation quality** — Patches that passed were often wrong (tests failed). Some proposals had syntax errors (ast.parse failed).

5. **Recommendation:** Relax or refine `check_structural_improvement` so valid retries are not rejected as `no_progress_repeat`. The current logic may be too strict. At the same time, improve generation quality so applied patches pass tests.
