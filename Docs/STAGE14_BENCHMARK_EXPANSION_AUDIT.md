# Stage 14 — Benchmark Expansion and Execution-Quality Audit

## 1. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/suites/audit12.py` | **New** — audit12 suite (all 12 core12 tasks) |
| `tests/agent_eval/runner.py` | audit12 support, extended per-task accounting, summary aggregates |
| `tests/agent_eval/harness.py` | `first_failing_stage` inference, extra in outcome |
| `tests/agent_eval/failure_buckets.py` | `infer_first_failing_stage()` helper |
| `tests/agent_eval/test_stage14_benchmark_infra.py` | **New** — runner, artifact schema, failure-bucket, suite-definition tests |

## 2. Expanded Suite Definition

### audit12
All 12 core12 tasks in real mode:

| task_id | layer | orchestration_path | grading_mode |
|---------|-------|--------------------|--------------|
| core12_mini_explain_arch | mini_repo | hierarchical | validation_exit_code |
| core12_mini_trace_flow | mini_repo | hierarchical | validation_exit_code |
| core12_mini_repair_calc | mini_repo | compat | validation_exit_code |
| core12_mini_repair_parse | mini_repo | compat | validation_exit_code |
| core12_mini_feature_flags | mini_repo | compat | validation_exit_code |
| core12_mini_docs_version | mini_repo | hierarchical | validation_exit_code |
| core12_pin_requests_explain_trace | pinned_repo | hierarchical | explain_artifact |
| core12_pin_click_docs_code | pinned_repo | hierarchical | validation_exit_code |
| core12_pin_typer_repair | pinned_repo | compat | validation_exit_code |
| core12_pin_typer_feature | pinned_repo | compat | validation_exit_code |
| core12_pin_requests_httpbin_doc | pinned_repo | hierarchical | validation_exit_code |
| core12_pin_click_multifile | pinned_repo | compat | validation_exit_code |

**Stretch tasks:** None added. All 12 tasks use existing fixtures; no new pinned repos.

## 3. Proof Commands

```bash
# All agent_eval tests
python3 -m pytest tests/agent_eval -q

# audit12 real run
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_real
```

## 4. audit12 Real-Run Summary

**Run:** `artifacts/agent_eval_runs/20260320_053641_d34708`

| Metric | Value |
|--------|-------|
| total_tasks | 12 |
| success_count | 8 |
| validation_pass_count | 8 |
| structural_success_count | 6 |
| attempts_total_aggregate | 6 |
| retries_used_aggregate | 0 |
| replans_used_aggregate | 0 |

### failure_bucket_histogram
| Bucket | Count |
|--------|-------|
| planner_wasted_motion | 4 |

### patch_reject_reason_histogram
(empty — no patch rejections observed)

### validation_scope_kind_histogram
| Kind | Count |
|------|-------|
| exact | 2 |
| repo_wide | 1 |
| benchmark_local | 3 |

### first_failing_stage_histogram
| Stage | Count |
|-------|-------|
| SEARCH | 4 |

## 5. Per-Task Outcome Table

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage |
|---------|---------|-------------------|--------------------|---------------|---------------------|
| core12_mini_explain_arch | ✓ | ✓ | ✗ | — | — |
| core12_mini_trace_flow | ✓ | ✓ | ✗ | — | — |
| core12_mini_repair_calc | ✓ | ✓ | ✓ | — | — |
| core12_mini_repair_parse | ✓ | ✓ | ✓ | — | — |
| core12_mini_feature_flags | ✓ | ✓ | ✓ | — | — |
| core12_mini_docs_version | ✗ | ✗ | ✗ | planner_wasted_motion | SEARCH |
| core12_pin_requests_explain_trace | ✗ | ✗ | ✗ | planner_wasted_motion | SEARCH |
| core12_pin_click_docs_code | ✗ | ✗ | ✗ | planner_wasted_motion | SEARCH |
| core12_pin_typer_repair | ✓ | ✓ | ✓ | — | — |
| core12_pin_typer_feature | ✓ | ✓ | ✓ | — | — |
| core12_pin_requests_httpbin_doc | ✗ | ✗ | ✗ | planner_wasted_motion | SEARCH |
| core12_pin_click_multifile | ✓ | ✓ | ✓ | — | — |

## 6. Ranked Next Bottleneck

**Primary bottleneck: search/ranking for hierarchical (explain/docs) tasks**

All 4 failures share:
- **failure_bucket:** planner_wasted_motion
- **first_failing_stage:** SEARCH
- **orchestration_path:** hierarchical (two-phase docs/code)
- **patch_reject_reason:** none (no edit phase reached)

Compat tasks (6/6) all passed. Hierarchical tasks: 2 passed (explain_arch, trace_flow — validation_exit_code; tests pass without edits), 4 failed.

Run logs show `reranker inference failed — using retriever-score ordering: RecursionError` and `rank_bm25 not installed` / `rank_bm25 import failed with RecursionError`. Retrieval fallback was used; ranking quality is degraded. The hierarchical path uses SEARCH_CANDIDATES with artifact_mode; the planner phase did not achieve its goal before the phase ended, and the first failing stage is SEARCH — retrieval/search did not yield usable context for the explain/docs subgoals.

**Principal-engineer conclusion:** The benchmark data points to search/ranking as the highest-yield bottleneck. All failures occur before EDIT; no patch_reject_reason or validation_regression. The hierarchical explain/docs tasks require retrieval that surfaces the right files and symbols for the planner to produce viable steps. With reranker/BM25 unavailable and fallback ordering, retrieval quality is insufficient for these tasks. Hardening search and ranking (reranker stability, BM25 availability, fallback ordering quality) will directly address the measured failures without touching orchestration.

## 7. Stage 15 Recommendation

**Recommendation: search/ranking hardening**

**Justification from benchmark data:**
- 4/4 failures have first_failing_stage = SEARCH
- 0 edit_grounding failures (patch_reject_reason_histogram empty)
- 0 validation_regression failures
- failure_bucket = planner_wasted_motion for all 4 — phase did not complete because retrieval did not provide usable context
- Run logs show reranker RecursionError and BM25 unavailable; retrieval fallback in use
- Compat path (SEARCH → EDIT) succeeds for all 6 compat tasks; hierarchical path (SEARCH_CANDIDATES → EXPLAIN) fails for 4/6 hierarchical tasks that require artifact production or docs alignment

**Not recommended this stage:**
- **orchestration change** — frozen per Stage 14; no evidence that policy changes would fix the failures
- **patch generation hardening** — no patch rejections observed
- **validation targeting hardening** — no validation_regression; validation commands ran correctly for compat tasks
- **edit-loop recovery hardening** — edit loop was never entered for failures
