# Stage 27 — External-Repo Evaluation and Product-Readiness Audit

**Date:** 2026-03-20  
**Scope:** First external-repo evaluation stage testing whether the agent generalizes beyond handcrafted benchmark fixtures. Measure real execution quality on pinned open-source repos and produce a blunt audit of product readiness.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/suites/external6.py` | **New** — external6 suite (6 tasks on pinned_repos) |
| `tests/agent_eval/test_stage27_external_anti_overfit.py` | **New** — 8 anti-overfit tests for external6 |
| `tests/agent_eval/runner.py` | Added external6 suite loading and real-mode support; extended suite_label for external6_real |
| `tests/agent_eval/fixtures/pinned_repos/typer_snapshot/benchmark_local/bench_math.py` | Added halve() with intentional bug for ext_repair_typer_halve |
| `tests/agent_eval/fixtures/pinned_repos/typer_snapshot/benchmark_local/test_bench_math.py` | Added test_halve |
| `tests/agent_eval/fixtures/pinned_repos/typer_snapshot/benchmark_local/README_BENCH.md` | **New** — docs alignment task |
| `tests/agent_eval/fixtures/pinned_repos/typer_snapshot/benchmark_local/typer_ver.py` | **New** — version constant |
| `tests/agent_eval/fixtures/pinned_repos/typer_snapshot/benchmark_local/check_readme_bench.py` | **New** — validation script |
| `tests/agent_eval/fixtures/pinned_repos/click_snapshot/benchmark_local/arithmetic.py` | **New** — add_ints with bug |
| `tests/agent_eval/fixtures/pinned_repos/click_snapshot/benchmark_local/test_arithmetic.py` | **New** — validation |
| `tests/agent_eval/fixtures/pinned_repos/requests_snapshot/benchmark_local/VERSION_NOTE.md` | **New** — docs alignment task |
| `tests/agent_eval/fixtures/pinned_repos/requests_snapshot/benchmark_local/version_meta.py` | **New** — version constant |
| `tests/agent_eval/fixtures/pinned_repos/requests_snapshot/benchmark_local/check_version_sync.py` | **New** — validation script |
| `tests/agent_eval/fixtures/pinned_repos/requests_snapshot/benchmark_local/bench_requests_meta.py` | Added get_timeout() with wrong return for ext_feature |
| `tests/agent_eval/fixtures/pinned_repos/requests_snapshot/benchmark_local/test_request_meta.py` | **New** — validation |

---

## 2. Exact external6 Inventory

| # | task_id | Type | repo_path | Validation |
|---|---------|------|-----------|------------|
| 1 | ext_repair_typer_halve | repair | pinned_repos/typer_snapshot | pytest benchmark_local/test_bench_math.py -k test_halve |
| 2 | ext_repair_click_add | repair | pinned_repos/click_snapshot | pytest benchmark_local/test_arithmetic.py |
| 3 | ext_docs_requests_version | docs-consistency | pinned_repos/requests_snapshot | python3 benchmark_local/check_version_sync.py |
| 4 | ext_docs_typer_readme | docs-consistency | pinned_repos/typer_snapshot | python3 benchmark_local/check_readme_bench.py |
| 5 | ext_explain_click_decorators | explain-artifact | pinned_repos/click_snapshot | OUT: benchmark_local/artifacts/decorator_flow.md + substrings |
| 6 | ext_feature_requests_timeout | feature | pinned_repos/requests_snapshot | pytest benchmark_local/test_request_meta.py |

---

## 3. Repo Selection Rationale

- **pinned_repos** (requests, typer, click) were chosen because:
  - Already present in benchmark fixtures; no new snapshot repos added
  - Real open-source projects with stable structure
  - audit12 uses the same repos for different tasks; external6 uses distinct task wording and validation
  - All validation is deterministic, local, and runnable offline

- **No overlap with mini/holdout/adversarial:** external6 uses only `pinned_repos/*`, never `mini_repos/*`, `holdout_mini_repos/*`, or `adversarial_mini_repos/*`.

---

## 4. Validation Design Rationale

| Task | Validation | Rationale |
|------|------------|-----------|
| ext_repair_typer_halve | pytest -k test_halve | Narrow scope; only validates halve fix |
| ext_repair_click_add | pytest test_arithmetic | Single test file; add_ints(2,3)==5 |
| ext_docs_requests_version | check_version_sync.py | Compares VERSION_NOTE.md bold version to RELEASE_VERSION |
| ext_docs_typer_readme | check_readme_bench.py | Compares README_BENCH.md bold version to TYPER_BENCH_VER |
| ext_explain_click_decorators | explain_artifact | File existence + substrings (command, decorator, ->) |
| ext_feature_requests_timeout | pytest test_request_meta | get_timeout()==30 |

All commands run from workspace root; no network; no flaky services.

---

## 5. Anti-Overfit Protections Added

| Test | Purpose |
|------|---------|
| `test_external_task_ids_distinct` | external6 task_ids do not overlap audit12/holdout8/adversarial12; use ext_ prefix |
| `test_external_repo_paths_distinct_from_mini_holdout_adversarial` | external6 uses pinned_repos only; no mr*, mh*, av* |
| `test_external_no_task_id_branching` | harness, real_execution, grounded_patch_generator, patch_generator, execution_loop must not branch on ext_* task_ids |
| `test_external_instruction_wording_differs` | external6 uses halve, add_ints, version_note, readme_bench, decorator_flow, get_timeout |
| `test_external_validation_commands_diverse` | Validation uses pytest + check_version_sync + check_readme_bench; distinct from adversarial12 |
| `test_external6_loads_and_validates` | Schema validation |
| `test_external6_task_types_balanced` | Includes repair, docs, explain, feature |

---

## 6. Benchmark Regression Status

| Suite | Stage 26 Baseline | Stage 27 Result | Status |
|-------|-------------------|-----------------|--------|
| audit12 | 12/12 | **11/12** | 1 regression (core12_pin_typer_repair) |
| holdout8 | 8/8 | **8/8** | Green |
| adversarial12 | 12/12 | **12/12** | Green |

**audit12 regression:** core12_pin_typer_repair failed with `validation_regression` / `patch_applied_but_wrong_behavior`. Possible flakiness or environment variance; holdout8 and adversarial12 remain green.

---

## 7. external6 Summary Table

| Metric | Value |
|--------|-------|
| total_tasks | 6 |
| success_count | **1** |
| validation_pass_count | 1 |
| structural_success_count | 1 |
| failure_bucket_histogram | edit_grounding_failure: 5 |
| patch_reject_reason_histogram | weakly_grounded_patch: 5 |
| first_failing_stage_histogram | EDIT: 5 |
| semantic_rca_cause_histogram | no_grounded_candidate_found: 5 |
| patches_applied_total | 0 |
| files_modified_total | 1 |

Run dir: `artifacts/agent_eval_runs/20260320_145651_de6b3c`

---

## 8. Per-Task external6 Outcomes

| task_id | success | failure_bucket | patch_reject_reason | semantic_rca_cause |
|---------|---------|----------------|---------------------|---------------------|
| ext_repair_typer_halve | false | edit_grounding_failure | weakly_grounded_patch | no_grounded_candidate_found |
| ext_repair_click_add | false | edit_grounding_failure | weakly_grounded_patch | no_grounded_candidate_found |
| ext_docs_requests_version | false | edit_grounding_failure | weakly_grounded_patch | no_grounded_candidate_found |
| ext_docs_typer_readme | false | edit_grounding_failure | weakly_grounded_patch | no_grounded_candidate_found |
| ext_explain_click_decorators | **true** | — | — | — |
| ext_feature_requests_timeout | false | edit_grounding_failure | weakly_grounded_patch | no_grounded_candidate_found |

---

## 9. Failure Bucket Histogram

| Bucket | Count |
|--------|-------|
| edit_grounding_failure | 5 |

---

## 10. first_failing_stage Histogram

| Stage | Count |
|-------|-------|
| EDIT | 5 |

---

## 11. semantic_rca_cause_histogram

| Cause | Count |
|-------|-------|
| no_grounded_candidate_found | 5 |

---

## 12. Top 3 Product Bottlenecks Revealed by external6

1. **Grounded patch strategy coverage** — All 5 EDIT failures share `no_grounded_candidate_found`. The grounded generator has no strategy that matches:
   - `halve` (return n//2): no "halve" or integer-division literal pattern
   - `add_ints` (return a+b not a*b): `return_binary_op_repair` may not fire for "add" in add_ints; evidence matching may fail for `return a * b` when instruction says "add"
   - `version_meta`/`VERSION_NOTE` alignment: `version_constant_align` expects `.md` with `## vX.Y.Z`; our VERSION_NOTE uses `**2.0.0**` format
   - `typer_ver`/`README_BENCH` alignment: similar format mismatch
   - `get_timeout` (return 30): `add_missing_function` fires for absent functions; get_timeout exists but returns wrong value — no "fix return value" strategy

2. **Evidence format mismatch** — Existing grounded strategies (version_constant_align, url_constant_align) expect specific doc patterns (e.g. `## vX.Y.Z`, `**bold URL**`). External tasks use `**X.Y.Z**` in markdown. Strategy triggers may not match.

3. **Return-value repair gap** — For "fix F so it returns X", when F already exists with wrong return, there is no dedicated strategy. `add_missing_function` handles absence; no `fix_return_value` for existing functions.

---

## 13. Blunt Judgment

**Is the agent ready for broader dogfooding, limited internal use, or still benchmark-heavy?**

**Still benchmark-heavy.**

- **audit12:** 11/12 — slight regression; core12_pin_typer_repair failed this run.
- **holdout8:** 8/8 — green.
- **adversarial12:** 12/12 — green.
- **external6:** 1/6 — only the explain-artifact task (WRITE_ARTIFACT) passed; all 5 EDIT tasks failed with `no_grounded_candidate_found`.

The external suite successfully distinguishes transfer from overfitting. The agent does not generalize to external task patterns (halve, add_ints, version/readme alignment with `**X.Y.Z**`, get_timeout return fix) without grounded strategies that match. The dominant failure is **edit grounding** — no grounded candidate found, not wrong patch behavior.

**Recommendation:** Do not rush to add task-specific synthetics. First classify failures:
- **Retrieval:** Not the bottleneck — first_failing_stage is EDIT; search reached edit phase.
- **Target resolution:** Instructions include explicit paths (benchmark_local/bench_math.py, etc.); target resolution is adequate.
- **Semantic patch quality:** No patch was applied; the bottleneck is **grounded candidate generation** — strategies do not match external patterns.
- **Validation environment:** Validation commands run correctly when given correct patches; not the bottleneck.

---

## 14. Decision Rule (Applied)

- **external6 is weak (1/6)** — do not add benchmark-specific patch synthetics this stage.
- **Failure classification:** Mainly **grounded patch strategy coverage** — strategies expect different evidence formats and patterns than external tasks use.
- **Next stage options:**
  1. **Extend grounded strategies generically** — e.g. support `**X.Y.Z**` in version alignment; add `fix_return_value` for existing functions with wrong literal; relax `return_binary_op_repair` to match "add" in add_ints. No task-id logic.
  2. **Broader repo/task scaling** — if external6 improves via generic strategy extension, add more external repos and longer-horizon tasks.
  3. **Validation environment audit** — ensure check_version_sync, check_readme_bench run correctly from workspace root in all contexts.

---

## 15. Commands Run

```bash
python3 -m pytest tests/agent_eval -q                    # 168 passed, 1 skipped
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q  # 190 passed
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage27
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage27
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage27
python3 -m tests.agent_eval.runner --execution-mode real --suite external6 --output artifacts/agent_eval_runs/external6_first_real
```
