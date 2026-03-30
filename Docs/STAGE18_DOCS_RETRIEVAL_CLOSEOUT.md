# Stage 18 — Docs-consistency retrieval & context plumbing (closeout)

## 1. Files changed

| Area | Files |
|------|--------|
| Task semantics | `agent/retrieval/task_semantics.py` |
| Patch generation | `editing/patch_generator.py` |
| Inner validation resolution | `agent/tools/validation_scope.py` |
| Benchmark harness | `tests/agent_eval/real_execution.py` |
| Hierarchical loop output | `agent/orchestrator/deterministic_runner.py` |
| Edit / loop telemetry | `agent/execution/step_dispatcher.py`, `agent/orchestrator/execution_loop.py` |
| Failure classification | `tests/agent_eval/failure_buckets.py` |
| Tests | `tests/agent_eval/test_stage18_docs_retrieval.py` |

## 2. Root causes fixed (blunt)

**A. Retrieval vs EDIT failure mode**  
Benchmark transcripts showed `instruction_path_inject` already populated `ranked_context` with the right `.md` / `.py` paths; `search_viable_file_hits == 0` was often because raw SEARCH ranked test/index noise, not because files were missing from disk. The real regression was **downstream of retrieval**.

**B. Multi-file patch plan → invalid AST / wrong inner test**  
`to_structured_patches` emitted one structured (non–`text_sub`) patch per `plan_diff` target. For docs tasks, sibling files (e.g. `scripts/check_readme_version.py`) shared instruction hints and received placeholder AST patches. That led to **`invalid_patch_syntax`** or spurious edits.

**C. Synthetic docs patches only on `.md` for httpbin/click**  
When `plan_diff` ranked `bench_requests_meta.py` / `bench_click_meta.py` first, synthetics that only handled `.md` did not run; the pipeline fell through to bad AST patches.

**D. Inner edit→test loop ignored non-pytest validation**  
`resolve_inner_loop_validation` only treated **`pytest`** commands as `test_cmd`. Harness set `AUTOSTUDIO_INNER_VALIDATION_CMD` to `python3 scripts/check_readme_version.py` / `benchmark_local/check_*.py`, but resolution returned **`resolved_validation_command: ""`**, so the loop still behaved like repo-wide pytest discovery and **failed after a correct `text_sub`**.

**E. Hierarchical `edit_telemetry` not at top level**  
`infer_first_failing_stage` / buckets used empty `edit_telemetry` on the parent `loop_output`, so failures looked like **SEARCH** when phase 2 had already reached **EDIT** with real `attempted_target_files`.

## 3. What changed (by layer)

**Retrieval / semantics**  
- Broadened `instruction_suggests_docs_consistency` (e.g. `consistency`, `documented`).  
- Added `validation_check_script_paths_in_instruction` and merged into `instruction_path_hints` so check scripts under `benchmark_local/` or `scripts/` are considered for path injection.

**Ranking / patch generation**  
- Docs-consistency: **first successful `_synthetic_repair` wins** — return a **single** `text_sub` change; do not emit sibling AST patches.  
- `_synthetic_docs_httpbin_align` / `_synthetic_docs_stability_align`: support **`.py`** targets (update `DEFAULT_HTTPBIN_BASE` / `CLICK_BENCH_API_STABILITY` to match the note), keeping behavior generic (path/content–driven).

**Inner validation (plumbing)**  
- `resolve_inner_loop_validation`: any non-empty `requested` inner command (including `python3 …/check_*.py`) sets `test_cmd` and `resolved_validation_command`.  
- `real_execution._pytest_inner_validation_cmd`: prefer pytest; else **first** validation command.

**Observability**  
- `edit_grounding_telemetry`: `instruction_path_injects`, `context_file_sample`.  
- `edit_telemetry.grounding` in `execution_loop`.  
- Hierarchical aggregate `loop_output` includes **`edit_telemetry` from the last phase**.  
- `failure_buckets`: `_merge_edit_telemetry_from_phases` for hierarchical snapshots.

## 4. Tests added

- `test_validation_check_script_paths_in_instruction_hints`  
- `test_resolve_inner_loop_validation_non_pytest_command`  
- `test_docs_consistency_single_synthetic_patch`  
- Existing filter / inject / `plan_diff` tests retained.

## 5. audit12 before / after (real mode)

| Metric | Before (Stage 17 run, `20260320_063048_71ba67`) | After (`20260320_064232_9b6161`) |
|--------|-----------------------------------------------|----------------------------------|
| total_tasks | 12 | 12 |
| success_count | 9 | **12** |
| validation_pass_count | 9 | **12** |
| structural_success_count | 8 | **11** |
| attempts_total_aggregate | 12 | 12 |
| retries_used_aggregate | 0 | 0 |
| replans_used_aggregate | 0 | 0 |
| failure_bucket_histogram | `{edit_grounding_failure: 3}` | `{}` |
| first_failing_stage_histogram | `{SEARCH: 3}` | `{}` |

*`core12_mini_explain_arch` remains structural_success=False by design (explain-only / no patch).*

## 6. Per-task outcomes (docs-consistency trio)

| task_id | Before | After |
|---------|--------|--------|
| core12_mini_docs_version | fail (edit_grounding / SEARCH) | **success** |
| core12_pin_click_docs_code | fail | **success** |
| core12_pin_requests_httpbin_doc | fail | **success** |

## 7. Remaining bottleneck (ranked)

1. **Structural goal vs validation** — one hierarchical explain task can still show `structural_success=False` while validation passes; separate from docs-consistency.  
2. **SEARCH BM25/query rewriter** — offline stub still yields weak queries (`{"steps": []}`); mitigated by instruction-path injection, not eliminated.  
3. **Reranker / graph** — optional; not on the critical path for these fixtures after Stage 18.

## Commands run

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_docs_retrieval_hardening
```
