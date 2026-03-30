# Stage 21 — Adversarial Transfer Benchmark Expansion and Generalization Audit — Closeout

**Date:** 2026-03-20  
**Scope:** Test whether Stage 20's edit-path improvements generalize beyond audit12 and holdout8 via a new adversarial suite.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/fixtures/adversarial_mini_repos/` | **New** — 12 mini repos (av01_ratios through av12_artifact) |
| `tests/agent_eval/suites/adversarial12.py` | **New** — adversarial12 suite definition (12 tasks) |
| `tests/agent_eval/runner.py` | Added adversarial12 suite loading; extended summary with task_type_histogram, instruction_explicit_path_count |
| `tests/agent_eval/test_stage21_adversarial_anti_overfit.py` | **New** — 8 anti-overfit tests for adversarial12 |
| `Docs/STAGE21_ADVERSARIAL_TRANSFER_AUDIT.md` | **New** — this closeout |

**audit12 and holdout8 unchanged** — no modifications to task definitions.

---

## 2. Exact Task Inventory (adversarial12)

| # | task_id | Type | repo_path | Validation |
|---|---------|------|-----------|------------|
| 1 | adv_repair_ratios | repair | adversarial_mini_repos/av01_ratios | pytest tests/test_ratios.py |
| 2 | adv_repair_parse | repair | adversarial_mini_repos/av02_parse | pytest tests/test_parser.py |
| 3 | adv_repair_guard | repair | adversarial_mini_repos/av09_guard | bin/assert_guard.py |
| 4 | adv_feature_defaults | feature | adversarial_mini_repos/av03_defaults | pytest tests/test_defaults.py |
| 5 | adv_feature_severity | feature | adversarial_mini_repos/av04_severity | pytest tests/test_levels.py |
| 6 | adv_feature_config | feature | adversarial_mini_repos/av10_config | pytest tests/test_options.py |
| 7 | adv_docs_release | docs-consistency | adversarial_mini_repos/av05_release | scripts/assert_release_match.py |
| 8 | adv_docs_spec | docs-consistency | adversarial_mini_repos/av06_spec | scripts/assert_spec_match.py |
| 9 | adv_docs_version | docs-consistency | adversarial_mini_repos/av11_changelog2 | bin/assert_version_sync.py |
| 10 | adv_explain_flow | explain-artifact | adversarial_mini_repos/av07_flow | OUT/flow_diagram.md |
| 11 | adv_explain_artifact | explain-artifact | adversarial_mini_repos/av12_artifact | OUT/flow_diagram.md |
| 12 | adv_multifile_const | multi-file | adversarial_mini_repos/av08_const | pytest tests/test_const.py |

---

## 3. Why adversarial12 Is Materially Different

| Dimension | audit12 | holdout8 | adversarial12 |
|-----------|---------|----------|----------------|
| Function names | multiply, tokenize, double, beta_enabled, describe_app | safe_div, is_valid, enable_debug, log_level | normalize_ratios, parse_bytes, cfg_verbose, get_severity, validate_input |
| Constant names | SUFFIX, APP_VERSION, CLICK_BENCH_API_STABILITY | SHARED_PREFIX, RELEASE_VERSION, API_BASE | BASE_URI, BUILD_NUMBER, DEFAULT_ENDPOINT, CURRENT_VERSION |
| Repo layout | mini_repos/mr*, pinned_repos/* | holdout_mini_repos/mh* | adversarial_mini_repos/av* |
| Module paths | src/calc, src/parse, src/flags, benchmark_local | src/math_utils, src/valid, pkg_a | core/, io/, cfg/, impl/, mod_a/, validation/, runtime/ |
| Docs targets | README, DECORATORS_NOTE, HTTPBIN_NOTE | CHANGELOG, lib/version, API.md | RELEASE_NOTES, SPEC.md, VERSION_HISTORY |
| Validation scripts | check_readme_version, check_docs_code | run_verify, validate_changelog_version | assert_release_match, assert_spec_match, assert_guard, assert_version_sync |
| Artifact paths | benchmark_local/artifacts/explain_out.txt | HO/trace_output.md | OUT/flow_diagram.md |
| Intent-only instruction | — | — | adv_feature_config: "runtime options module" (no explicit path) |

---

## 4. Anti-Overfit Checks Added

| Test | Purpose |
|------|---------|
| `test_adversarial_no_task_id_branching` | Fails if harness/real_execution branch on adversarial task_ids |
| `test_adversarial_repo_paths_distinct` | No path overlap with audit12 or holdout8 |
| `test_adversarial_task_ids_distinct` | No task_id overlap |
| `test_adversarial_avoids_stage20_synthetic_names` | Instructions must not use safe_div, enable_debug, log_level, SHARED_PREFIX, etc. |
| `test_adversarial_validation_commands_diverse` | Validation uses pytest, scripts/, bin/ — not one pattern |
| `test_adversarial_task_types_balanced` | Includes repair, feature, docs, explain, multi-file |
| `test_adversarial12_loads_and_validates` | Schema validation |
| `test_adversarial_instruction_wording_differs` | Distinct phrasing (normalize_ratios, parse_bytes, cfg_verbose, etc.) |

---

## 5. audit12 Before/After (No Regression)

| Metric | Before (Stage 20) | After (Stage 21) |
|--------|-------------------|------------------|
| total_tasks | 12 | 12 |
| success_count | 12 | **12** |
| validation_pass_count | 12 | **12** |
| structural_success_count | 11 | **11** |
| failure_bucket_histogram | {} | {} |

**audit12 stays green.** No regression.

---

## 6. holdout8 Before/After (No Regression)

| Metric | Before (Stage 20) | After (Stage 21) |
|--------|-------------------|------------------|
| total_tasks | 8 | 8 |
| success_count | 8 | **8** |
| validation_pass_count | 8 | **8** |
| structural_success_count | 8 | **8** |
| failure_bucket_histogram | {} | {} |

**holdout8 stays green.** No regression.

---

## 7. adversarial12 Results

| Metric | Value |
|--------|-------|
| total_tasks | 12 |
| success_count | **2** |
| validation_pass_count | 2 |
| structural_success_count | 2 |
| failure_bucket_histogram | validation_regression: 9, edit_grounding_failure: 1 |
| patch_reject_reason_histogram | validation_tests_failed: 9 |
| first_failing_stage_histogram | EDIT: 10 |
| patches_applied_total | 0 |
| files_modified_total | 2 |
| task_type_histogram | repair: 3, feature: 3, docs: 3, explain: 2, multi_file: 1 |
| instruction_explicit_path_count | 8 |

### Per-Task Outcomes

| task_id | success | failure_bucket | patch_reject_reason |
|---------|---------|----------------|---------------------|
| adv_repair_ratios | false | validation_regression | validation_tests_failed |
| adv_repair_parse | false | validation_regression | validation_tests_failed |
| adv_repair_guard | false | validation_regression | validation_tests_failed |
| adv_feature_defaults | false | validation_regression | validation_tests_failed |
| adv_feature_severity | false | validation_regression | validation_tests_failed |
| adv_feature_config | false | edit_grounding_failure | — |
| adv_docs_release | false | validation_regression | validation_tests_failed |
| adv_docs_spec | false | validation_regression | validation_tests_failed |
| adv_docs_version | false | validation_regression | validation_tests_failed |
| adv_explain_flow | **true** | — | — |
| adv_explain_artifact | **true** | — | — |
| adv_multifile_const | false | validation_regression | validation_tests_failed |

---

## 8. Ranked Next Bottleneck (adversarial12 Failures Only)

1. **validation_regression (9/10 EDIT failures)** — Patches are applied (patch_apply_ok: true) but produce wrong results; validation tests fail; rollback. The agent produces syntactically valid patches that do not correctly fix the bug. Root cause: no synthetic patterns match adversarial names (normalize_ratios, parse_bytes, cfg_verbose, get_severity, validate_input, BASE_URI, BUILD_NUMBER, DEFAULT_ENDPOINT, CURRENT_VERSION). The pipeline falls through to placeholder or wrong patches.

2. **edit_grounding_failure (1)** — adv_feature_config: instruction says "runtime options module" without explicit path; retrieval/plan may not resolve to runtime/options.py; empty or wrong plan.

3. **invalid_patch_syntax is not dominant** — Unlike pre–Stage 20 holdout8, adversarial12 failures are not primarily invalid_patch_syntax. Patches apply but are semantically wrong.

---

## 9. Explicit Judgment

**Still benchmark-shaped.**

- **audit12:** 12/12 — regression suite green.
- **holdout8:** 8/8 — Stage 20 synthetics cover holdout patterns.
- **adversarial12:** 2/12 — only explain-artifact tasks (WRITE_ARTIFACT) pass; all EDIT tasks fail.

The adversarial suite successfully distinguishes transfer from overfitting. Stage 20's synthetics (safe_div, is_valid, enable_debug, log_level, SHARED_PREFIX, changelog/version, api/base) do not generalize to adversarial names and layouts. The dominant failure is **validation_regression** (wrong patches), not invalid_patch_syntax.

**Decision:** Stop adding synthetics for now. Do RCA on the dominant failure mode (wrong semantic patches) before expanding further. The next stage should focus on why applied patches produce incorrect fixes, not on adding more pattern-specific synthetics.

---

## 10. Commands Run

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage21
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage21
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage21
```
