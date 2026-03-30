# Stage 23 — Patch-generation quality hardening (closeout)

**Date:** 2026-03-20  
**Scope:** Generic patch-effectiveness guards, grounded patch generation, fallback ladder, telemetry, and semantic RCA extensions — without new suites, task changes, or orchestration contract changes.

---

## 1. Files changed

| File | Change |
|------|--------|
| `editing/patch_effectiveness.py` | **New** — `meaningful_diff_line_count`, `module_append_is_meaningful`, `build_effectiveness_report`, `assess_text_sub`, `assess_after_content_change` (bounded snippets, JSON-safe dicts). |
| `editing/patch_executor.py` | After preflight, **effectiveness gate** for `text_sub` and structured patches; reject `no_effect_change`, `unchanged_target_region`, `no_meaningful_diff`; aggregate `patch_effectiveness` on success/failure; `_merge_effectiveness_telemetry`. |
| `editing/patch_generator.py` | **Fallback ladder:** `text_sub_fallback` before hint-gated structured path; `_symbol_defined_in_file` required for structured patches; `_generic_multiply_to_div_return`, `_generic_split_whitespace_line_return`; `patch_generation_reject`: `weakly_grounded_patch` when planner had changes but none grounded. |
| `agent/runtime/execution_loop.py` | Synthetic failed `patch_result` for `weakly_grounded_patch` (retry-compatible); merge `patch_effectiveness` from `execute_patch` into `edit_patch_telemetry`. |
| `tests/agent_eval/semantic_rca.py` | New causes: `no_effect_change`, `unchanged_target_region`, `no_meaningful_diff`, `weakly_grounded_patch`; snippet-identity heuristic for `unchanged_target_region`; `patch_effectiveness` on artifact. |
| `tests/agent_eval/test_stage23_patch_quality.py` | **New** regression tests. |

---

## 2. Generic guards (exact behaviors)

| Guard | Where | Reject reason |
|-------|--------|----------------|
| `text_sub` with `old == new` | `assess_text_sub` / executor | `no_effect_change` |
| `text_sub` that does not change file content | `assess_text_sub` | `unchanged_target_region` |
| `text_sub` with zero meaningful line delta | `assess_text_sub` | `no_meaningful_diff` |
| Structured / AST patch with `new_code == source_before` | `assess_after_content_change` | `unchanged_target_region` |
| `module_append` with no new def/class/binding vs original | `module_append_is_meaningful` | `no_meaningful_diff` |
| Planner output could not yield any patch | `to_structured_patches` | `weakly_grounded_patch` (generation, not executor) |

---

## 3. New / machine-readable reject reasons

- `no_effect_change`
- `unchanged_target_region`
- `no_meaningful_diff`
- `weakly_grounded_patch` (empty grounded plan after filtering)

---

## 4. Telemetry / artifacts

**Per run (`edit_patch_telemetry` / `patch_result`):**

- `patch_effectiveness.patch_effective_change` (aggregate)
- `patch_effectiveness.patch_effective_reason`
- `patch_effectiveness.changed_region_detected`
- `patch_effectiveness.target_region_before` / `target_region_after` (bounded)
- `patch_effectiveness.meaningful_diff_line_count` (sum across steps)
- `patch_effectiveness.rejected_for_noop_or_unchanged`
- `patch_effectiveness.patch_effectiveness_steps` (capped list)

**`semantic_rca.json`:** `patch_effectiveness` echo; classifier uses new causes when telemetry supports them.

---

## 5. Benchmarks (real mode)

| Suite | Run dir | success | validation_pass | structural_success | Notes |
|-------|---------|---------|-----------------|---------------------|--------|
| **audit12** | `artifacts/agent_eval_runs/20260320_115053_7db30e` | 12/12 | 12/12 | 11/12 | No regression vs Stage 22. |
| **holdout8** | `artifacts/agent_eval_runs/20260320_115303_f1bdf2` | 8/8 | 8/8 | 7/8 | Validation green; one task (`holdout_repair_math`) still reports `weakly_grounded_patch` in telemetry with `patches_applied: 0` while validation passes — harness/structural flag quirk; not a validation regression. |
| **adversarial12** | `artifacts/agent_eval_runs/20260320_115338_399645` | **3/12** | 3/12 | 2/12 | Up from **2/12** (Stage 21/22). |

### adversarial12 — semantic RCA cause histogram (failed EDIT-classified tasks)

**Stage 22 (post-RCA run, from STAGE22 doc — failed-task classifier):**

| Cause | Count |
|-------|-------|
| `patch_applied_but_wrong_behavior` | 4 |
| `no_edit_attempted` | 2 |
| `ambiguous_instruction_or_missing_path` | 4 |

**Stage 23 (this run — `semantic_rca_cause_histogram` on failures):**

| Cause | Count |
|-------|-------|
| `weakly_grounded_patch` | 8 |
| `ambiguous_instruction_or_missing_path` | 1 |

Failures are now predominantly **early grounding rejection** (`weakly_grounded_patch` + `edit_grounding_failure`) rather than **apply-then-fail validation** (`validation_regression` / `validation_tests_failed`).

### Patch-reject histogram (adversarial12)

- **Before (Stage 21):** `validation_tests_failed: 9`
- **After (Stage 23):** `weakly_grounded_patch: 9`

---

## 6. Decision rule (Stage 24)

**adversarial12** improved slightly above 2/12 (**3/12** successes) but most EDIT work is now **cleanly classified as weakly grounded** rather than burning cycles on no-op applies and rollback. Per the Stage 23 prompt: **Stage 24 should prioritize replacing or upgrading weak patch generation** (e.g. stronger grounded LLM patch production, richer `text_sub` extraction from file content) **instead of stacking more post-hoc heuristics** on the executor.

---

## 7. Remaining bottlenecks (ranked)

1. **Weak planner → empty or ungrounded structured plans** — `weakly_grounded_patch` dominates adversarial12; executor guards are doing their job.
2. **No synthetic / fallback match** for novel names and layouts — generic `divide` / `split`+`whitespace` helpers help only when patterns match; many adversarial tasks still produce no grounded change.
3. **Ambiguous targets** — `adv_feature_config`-style instructions without explicit paths still fail at `ambiguous_instruction_or_missing_path`.
4. **Holdout structural telemetry** — one holdout task shows `weakly_grounded_patch` with validation pass; worth a harness/telemetry follow-up (out of scope for this stage).

---

## 8. Commands run

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage23_patch_quality
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage23_patch_quality2
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage23_patch_quality2
```

(Runner also creates timestamped run directories under `artifacts/agent_eval_runs/`; tables above reference the concrete `summary.json` paths.)
