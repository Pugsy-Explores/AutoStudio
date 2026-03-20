# Stage 19 — Benchmark Hardening and Anti-Overfit Expansion — Closeout

## 1. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/suites/holdout8.py` | **New** — holdout8 suite definition (8 tasks) |
| `tests/agent_eval/fixtures/holdout_mini_repos/` | **New** — 8 mini repos (mh01_math through mh08_api) |
| `tests/agent_eval/runner.py` | Added holdout8 suite loading and real-mode support |
| `tests/agent_eval/test_stage19_anti_overfit.py` | **New** — anti-overfit coverage tests |
| `Docs/STAGE19_BENCHMARK_HARDENING_CLOSEOUT.md` | **New** — this closeout |

**audit12 unchanged** — no modifications to `suites/audit12.py` or `suites/core12.py`.

---

## 2. New Suite Definition and Exact Task Inventory

**Suite name:** `holdout8`

| # | task_id | Type | repo_path | Validation |
|---|---------|------|-----------|------------|
| 1 | holdout_repair_math | repair | holdout_mini_repos/mh01_math | pytest tests/test_math_ops.py |
| 2 | holdout_repair_validator | repair | holdout_mini_repos/mh06_validator | scripts/run_verify.py |
| 3 | holdout_feature_config | small feature | holdout_mini_repos/mh02_config | pytest tests/test_config.py |
| 4 | holdout_feature_logger | small feature | holdout_mini_repos/mh07_logger | pytest tests/test_logger.py |
| 5 | holdout_docs_changelog | docs-consistency | holdout_mini_repos/mh03_changelog | scripts/validate_changelog_version.py |
| 6 | holdout_docs_api | docs-consistency | holdout_mini_repos/mh08_api | scripts/verify_api_docs.py |
| 7 | holdout_explain_trace | explain-artifact | holdout_mini_repos/mh04_trace | HO/trace_output.md + substrings |
| 8 | holdout_multifile_prefix | multi-file edit | holdout_mini_repos/mh05_multifile | pytest tests/test_prefix.py |

---

## 3. Why These Tasks Are a Real Holdout (Not Reworded Clones)

- **New fixture repos:** All 8 tasks use `holdout_mini_repos/mh*`, not `mini_repos/mr*` or `pinned_repos/*`. Zero path overlap with audit12.
- **Different module layout:** `math_utils`, `config`, `valid`, `logging_utils`, `pkg_a`/`pkg_b`, `lib/version`, `spec/api_spec` — none appear in core12.
- **Different validation patterns:** `scripts/validate_changelog_version.py`, `scripts/run_verify.py`, `scripts/verify_api_docs.py` — not `scripts/check_*.py` or `benchmark_local/check_*.py`.
- **Different instruction wording:** e.g. "safe_div", "enable_debug", "log_level", "CHANGELOG", "RELEASE_VERSION", "API_BASE", "SHARED_PREFIX" — distinct from core12 phrasing.
- **Different artifact path:** `HO/trace_output.md` vs `benchmark_local/artifacts/explain_out.txt`.

---

## 4. Anti-Overfit Checks Added

| Test | Purpose |
|------|---------|
| `test_no_task_id_branching_in_harness` | Fails if harness/real_execution contain hardcoded audit12 task_ids |
| `test_harness_uses_semantic_tags_not_task_id` | Asserts phase plan selection uses tags/grading_mode, not task_id |
| `test_holdout_uses_non_check_validation_commands` | Asserts holdout uses validate_*, verify_*, run_*, pytest — not only check_* |
| `test_holdout_validation_commands_are_diverse` | Asserts holdout validation is not solely scripts/check_*.py |
| `test_holdout_repo_paths_distinct_from_core12` | Asserts no path overlap between holdout and core12 |
| `test_holdout_task_ids_distinct_from_audit12` | Asserts no task_id overlap |
| `test_holdout_instruction_wording_differs` | Asserts holdout has task-specific wording (safe_div, changelog, etc.) |
| `test_holdout8_loads_and_validates` | Schema validation for holdout8 |
| `test_holdout8_task_types_balanced` | Asserts repair, feature, docs, explain, multi-file coverage |

---

## 5. audit12 Regression Results After Changes

```
suite: audit12_real
run_dir: artifacts/agent_eval_runs/20260320_065403_8282f9
total_tasks: 12
success_count: 12
validation_pass_count: 12
structural_success_count: 11
attempts_total_aggregate: 12
retries_used_aggregate: 0
replans_used_aggregate: 0
failure_bucket_histogram: {}
first_failing_stage_histogram: {}
```

**audit12 remains green (12/12 success).** No compat regressions.

---

## 6. Holdout Suite Real-Run Summary

```
suite: holdout8_real
run_dir: artifacts/agent_eval_runs/20260320_065437_3fa091
total_tasks: 8
success_count: 1
validation_pass_count: 1
structural_success_count: 1
attempts_total_aggregate: 6
retries_used_aggregate: 0
replans_used_aggregate: 0
failure_bucket_histogram: {"edit_grounding_failure": 7}
first_failing_stage_histogram: {"EDIT": 7}
```

---

## 7. Per-Task Outcomes for Holdout Suite

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage |
|---------|---------|-------------------|--------------------|----------------|---------------------|
| holdout_repair_math | false | false | false | edit_grounding_failure | EDIT |
| holdout_repair_validator | false | false | false | edit_grounding_failure | EDIT |
| holdout_feature_config | false | false | false | edit_grounding_failure | EDIT |
| holdout_feature_logger | false | false | false | edit_grounding_failure | EDIT |
| holdout_docs_changelog | false | false | false | edit_grounding_failure | EDIT |
| holdout_docs_api | false | false | false | edit_grounding_failure | EDIT |
| holdout_explain_trace | **true** | **true** | **true** | — | — |
| holdout_multifile_prefix | false | false | false | edit_grounding_failure | EDIT |

All 7 failures share `patch_reject_reason: invalid_patch_syntax`.

---

## 8. Ranked Next Bottleneck (Holdout Failures Only)

**Primary:** `edit_grounding_failure` — `invalid_patch_syntax`

The patch generator (or offline stub output) produces patches that fail `ast.parse` pre-check. Logs show: `[patch_executor] ast.parse pre-check failed: invalid syntax`. This occurs for all EDIT-step holdout tasks. The explain-artifact task (holdout_explain_trace) succeeds because it uses WRITE_ARTIFACT, not EDIT.

**Implication:** The agent’s edit path is brittle when applied to new fixture layouts (math_utils, config, valid, etc.). Audit12’s structure was effectively “tuned for” by prior stages; holdout’s different layout exposes patch-generation weakness.

---

## 9. Generalization vs Benchmark Overfitting

**Conclusion: Progress appears benchmark-shaped; transfer to holdout is weak.**

- **audit12:** 12/12 success — strong on the regression suite.
- **holdout8:** 1/8 success — only the explain-artifact task (WRITE_ARTIFACT) passes; all EDIT tasks fail with `invalid_patch_syntax`.

The holdout suite successfully distinguishes transfer from overfitting. The agent does not generalize well to new repos, new module names, and new validation scripts. The next improvement target is patch generation / edit grounding for diverse codebases, not further tuning on audit12.
