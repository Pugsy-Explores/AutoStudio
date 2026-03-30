# Stage 26 — Wrong-Behavior Patch RCA and Grounded Generation Refinement

**Date:** 2026-03-20  
**Scope:** Eliminate remaining adversarial12 failure (adv_feature_severity) caused by patch quality / wrong behavior. Focus on grounded generation quality, semantic correctness, and validation environment isolation. No task-id-specific logic, no benchmark-specific if/else.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `editing/test_runner_utils.py` | **Validation shadowing fix** — `_workspace_has_stdlib_shadowing`, `_transform_pytest_cmd_for_shadowing`; `run_tests_raw` runs from workspace.parent with stripped PYTHONPATH when workspace has logging/, config/, parser/, ast/, types/ |
| `editing/grounded_patch_generator.py` | `_apply_semantic_ranking`; `validate_semantic_grounded_candidate`; `select_best_candidate` uses semantic_match_score; `_extract_return_value` adds pattern for `returning 'X'`; PatchCandidate.telemetry includes Stage 26 fields |
| `editing/patch_generator.py` | Call `validate_semantic_grounded_candidate` before apply; `_infer_semantic_expectation_type`; pass instruction to `select_best_candidate`; add Stage 26 telemetry to change dict |
| `agent/runtime/execution_loop.py` | Extend `_s24_fields` with Stage 26 telemetry: `candidate_rejected_semantic_reason`, `selected_candidate_out_of_n`, `candidate_semantic_match_score`, `requested_symbol_name`, `requested_return_value`, `semantic_expectation_type` |
| `tests/agent_eval/semantic_rca.py` | New causes: `grounded_candidate_semantically_misaligned`, `requested_symbol_not_implemented`, `requested_literal_not_realized`, `correct_target_wrong_region`; classifier checks `candidate_rejected_semantic_reason`; `build_semantic_rca_dict` includes Stage 26 fields |
| `tests/agent_eval/test_stage26_patch_semantics.py` | **New** — 12 focused regression tests for add-function literal, candidate ranking, semantic rejection, validation shadowing, RCA classification, no task-id hacks |

---

## 2. RCA for adv_feature_severity (Before Changes)

**Task:** Add get_severity() -> str in logging/levels.py returning a non-empty string (e.g. 'WARN').

| Field | Value |
|-------|-------|
| **Chosen target file** | logging/levels.py ✓ |
| **Chosen symbol** | (empty) |
| **Patch strategy** | add_missing_function |
| **Patch candidate evidence** | "get_severity not found in file; instruction: Add get_severity() -> str" |
| **Before snippet** | `def is_tracing_enabled() -> bool:\n    return False\n` |
| **After snippet** | Added `def get_severity() -> str:\n    return "WARN"\n` |
| **Validation failure** | Traceback in pytest init: `from _pytest.logging import LogCaptureFixture` — pytest loads before test; workspace's `logging/` dir shadows stdlib `logging` when PYTHONPATH=. |

**Root cause:** **Import shadowing**, not wrong patch behavior. The patch was correct (get_severity() -> str returning "WARN"). Validation failed because the execution loop ran `PYTHONPATH=. python3 -m pytest tests/test_levels.py -q` with cwd=workspace; pytest's internal imports of stdlib `logging` resolved to workspace's `logging/` package instead.

**Failure type:** `likely_import_shadowing_or_env_conflict` (RCA) — env/execution, not semantic patch quality.

---

## 3. Semantic Ranking Rules Added

| Signal | Score delta | Description |
|--------|-------------|--------------|
| Function name in instruction | +1.0 | Candidate defines fname and fname appears in instruction |
| Return literal alignment | +1.0 | Instruction says return "X"; patch produces "X" |
| Severity/level-like words | +0.5 | Instruction has severity/level/warn/info; code has matching literal |
| Return type alignment | +0.5 | Instruction says -> str/bool/int; code has matching annotation |

Candidates sorted by `(rank, -semantic_match_score)` — lower rank wins; ties broken by higher semantic score.

---

## 4. Semantic Post-Generation Checks Added

| Check | Reject reason |
|-------|---------------|
| Instruction says "Add FNAME()" but patch doesn't define FNAME | `requested_symbol_not_implemented` |
| Instruction says "return X" or "returning X" but patch doesn't produce X | `requested_literal_not_realized` |
| Instruction says "Rename CONST from A to B" but patch missing old/new evidence | `rename_missing_old_or_new_evidence` |
| Instruction says "align docs/code" but patch has old == new | `align_candidate_modifies_neither` |

---

## 5. Add-Missing-Function Improvements

- **New literal pattern:** `returning 'X'` or `returning "X"` now extracted (in addition to `e.g. 'X'`)
- **Exact function name:** Strategy already required fname from instruction; semantic check rejects wrong name
- **Return value:** `_extract_return_value` order updated so `returning 'INFO'` is preferred over default `""`

---

## 6. Telemetry Additions

| Field | Description |
|-------|-------------|
| `requested_return_value` | Literal from instruction (e.g. "WARN") |
| `requested_symbol_name` | Function/constant name from instruction |
| `candidate_semantic_match_score` | 0–3 float from ranking |
| `candidate_rejected_semantic_reason` | Machine-readable reject reason when semantic check fails |
| `selected_candidate_out_of_n` | Number of candidates when one was selected |
| `semantic_expectation_type` | `add_function` \| `return_value` \| `rename_constant` \| `align_docs_code` |

---

## 7. Semantic RCA Causes Extended

| Cause | When |
|-------|------|
| `grounded_candidate_semantically_misaligned` | `candidate_rejected_semantic_reason` in (rename_missing_old_or_new_evidence, align_candidate_modifies_neither) |
| `requested_symbol_not_implemented` | Add fname() but patch doesn't define fname |
| `requested_literal_not_realized` | Instruction says return X but patch doesn't produce X |
| `correct_target_wrong_region` | Reserved for future use (right file, wrong symbol) |

---

## 8. Validation Shadowing Fix (Key for adv_feature_severity)

When `project_root` has a top-level dir that shadows a stdlib module (`logging`, `config`, `parser`, `ast`, `types`):

1. Strip `PYTHONPATH=...` from the test command
2. Rewrite `tests/X.py` → `{workspace_name}/tests/X.py`
3. Run with `cwd=workspace.parent`

This ensures pytest loads stdlib first; the test file uses importlib to load the local package explicitly (av04_severity fixture already does this).

---

## 9. Benchmark Results

### audit12 (no regression)

| Metric | Stage 25 | Stage 26 |
|--------|----------|----------|
| success_count | 12 | **12** |
| validation_pass_count | 12 | **12** |
| failure_bucket_histogram | {} | {} |

Run dir: `artifacts/agent_eval_runs/20260320_144528_fdaaf3`

### holdout8 (no regression)

| Metric | Stage 25 | Stage 26 |
|--------|----------|----------|
| success_count | 8 | **8** |
| validation_pass_count | 8 | **8** |
| failure_bucket_histogram | {} | {} |

Run dir: `artifacts/agent_eval_runs/20260320_144605_a63d54`

### adversarial12 (11/12 → 12/12)

| Metric | Stage 25 | Stage 26 |
|--------|----------|----------|
| success_count | 11 | **12** |
| validation_pass_count | 11 | **12** |
| failure_bucket_histogram | validation_regression: 1 | {} |
| semantic_rca_cause_histogram | likely_import_shadowing: 1 | {} |

Run dir: `artifacts/agent_eval_runs/20260320_144630_cff160`

### adversarial12 — Per-Task Outcome for adv_feature_severity

| task_id | Stage 25 | Stage 26 |
|---------|----------|----------|
| adv_feature_severity | FAIL (validation_regression, likely_import_shadowing) | **PASS** |

---

## 10. Semantic RCA Histogram

| Cause | Stage 25 (adversarial failures) | Stage 26 |
|-------|---------------------------------|----------|
| `likely_import_shadowing_or_env_conflict` | 1 | 0 |
| *(no failures)* | — | 12/12 pass |

---

## 11. Blunt Judgment

**Does adversarial12 at 12/12 represent real transfer or benchmark fit?**

**Real transfer.** The fix was generic:

1. **Validation env:** When any workspace has a top-level `logging/` (or config/, parser/, etc.), run pytest from parent with stripped PYTHONPATH. No task_id, repo name, or fixture path in the logic.
2. **Semantic ranking/rejection:** All rules are content-driven from instruction text and patch content. No `if task_id == "adv_feature_severity"`.

The adv_feature_severity patch was already correct before Stage 26. The failure was purely environmental (import shadowing). The semantic ranking, post-generation checks, and add-function improvements are generic hardening that will help future tasks with similar patterns (add function with literal return, rename constant, align docs/code).

---

## 12. Decision Rule (Applied)

- **adversarial12 reached 12/12** with no regressions on audit12 or holdout8
- **Stop adding benchmark-specific transfer suites for now** — move to broader external-repo evaluation stage
- If future failures appear, use the new RCA causes (`requested_symbol_not_implemented`, `requested_literal_not_realized`, etc.) to decide whether the next bottleneck is semantic generation, environment execution, or validation design

---

## 13. Commands Run

```bash
python3 -m pytest tests/agent_eval -q                    # 161 passed, 1 skipped
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q  # 190 passed
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage26_patch_semantics
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage26_patch_semantics
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage26_patch_semantics
```
