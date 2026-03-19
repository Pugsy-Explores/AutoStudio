# Stage 12.2 — First Execution-Quality Audit (audit6, real harness)

**Run timestamp directory:** `artifacts/agent_eval_runs/20260320_035239_b124e7`  
**Suite label (from `summary.json`):** `core12_audit6_real`  
**Date:** 2026-03-20

## 1. Files changed

- `Docs/STAGE12_2_FIRST_EXECUTION_QUALITY_AUDIT.md` (this report)

No orchestration, planner, retrieval, or benchmark task definitions were modified for this slice.

## 2. Proof command results

### `python3 -m pytest tests/agent_eval -q`

```
19 passed in 18.62s
```
Exit code: **0**

### `python3 -m tests.agent_eval.runner --execution-mode real --suite audit6 --output artifacts/agent_eval_runs/audit6_first_real`

Exit code: **0**  
Emitted JSON summary included `success_count: 0`, `total_tasks: 6`.  
Stderr ended with: `Run directory: .../artifacts/agent_eval_runs/20260320_035239_b124e7`

## 3. Exact benchmark run command

```bash
python3 -m tests.agent_eval.runner --execution-mode real --suite audit6 --output artifacts/agent_eval_runs/audit6_first_real
```

**Note:** The runner always writes under `artifacts/agent_eval_runs/<YYYYMMDD_HHMMSS>_<runid>/`. The `--output` value above is **not** used as the run directory unless it resolves to the `latest` symlink target (see `tests/agent_eval/runner.py`). Per-task artifacts for this audit live under `20260320_035239_b124e7/`.

## 4. Suite summary (from `summary.json` + per-task `outcome.json`)

| Metric | Value |
|--------|--------|
| **total_tasks** | 6 |
| **success_count** | 0 |
| **validation_pass_count** | 0 |
| **retries_used (total)** | *Not summable — all tasks `null` in `outcome.json`* |
| **replans_used (total)** | 0 (each task `0`) |
| **attempts_total (total)** | *Not summable — all tasks `null` in `outcome.json`* |
| **failure_bucket_histogram** | `validation_regression`: **6** |

## 5. Per-task table

| task_id | success | failure_class | retries_used | replans_used | changed_files | unrelated_files_changed | primary failure_bucket |
|---------|---------|---------------|--------------|--------------|---------------|-------------------------|------------------------|
| core12_mini_repair_calc | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |
| core12_mini_repair_parse | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |
| core12_mini_feature_flags | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |
| core12_pin_typer_repair | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |
| core12_pin_typer_feature | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |
| core12_pin_click_multifile | failure | goal_or_parent_not_met | null | 0 | 0 | 0 | validation_regression |

`failure_bucket` is `validation_regression` for all six because `classify_failure_bucket` falls through to “validation failed with logs” (`failure_buckets.py`: `not validation_passed and validation_logs`), even though `structural_success` is false — see limitations below.

## 6. Top bad-edit patterns (failed tasks, from emitted artifacts)

Harness field `bad_edit_patterns` is **empty for all six** (`scan_bad_edit_patterns` only flags conflict markers / suspicious `pass` density; empty diffs produce no hits).

From **`loop_output_snapshot.json`** (same shape on all six tasks):

1. **`patches_applied`: 0** — no successful patch application recorded.
2. **`errors_encountered`: `["edit failed after retries"]`** — EDIT path exhausted without a successful apply.
3. **`files_modified`: []** — no recorded file modifications in loop output.
4. **`completed_steps`** — only a single completed step entry per run (compat plan is SEARCH → EDIT; loop stops after failed EDIT).
5. **Empty git diff** — `changed_files.txt` is empty; no `bad_edit_patterns` from diff heuristics.

**Same-run stderr (not stored in per-task JSON):** repeated `[patch_executor] apply_patch error: Symbol not found` and `[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)` — consistent with zero patches and edit failure.

## 7. Top retrieval-miss signals (failed tasks)

Per-task **`retrieval_miss_signals`** in `outcome.json`: **empty for all six** (`retrieval_miss_signals_from_loop` expects `phase_results` / `context_output` structure; compat `loop_output_snapshot` does not populate those lists).

Qualitative signals from **the same benchmark process stderr** (not in `outcome.json`):

- Query rewriter offline stub returns `{"steps": []}`; SEARCH still returns hits, but top paths include **`.symbol_graph` and `tests` directories** (`[Errno 21] Is a directory` on expand for several tasks).
- Typer pin tasks: **`reranker inference failed — using retriever-score ordering: RecursionError: maximum recursion depth exceeded`**.
- Optional: `[bm25] rank_bm25 not installed` (ordering/embed path caveat).

## 8. Ranked recommendation — next product bottleneck

1. **Better code search / file targeting** — Highest yield: empty rewriter output and directory-level “hits” starve EDIT of anchorable symbols; patch apply fails with “Symbol not found.”
2. **Edit-loop hardening** — Second: resilient behavior when ranked context is weak (read-before-patch, broader file anchors), so the loop does not always end at zero patches.
3. **Planner policy tuning** — Lower until retrieval supplies credible file/symbol targets for EDIT.
4. **Retrieval merge** — Defer as a structured follow-on after baseline targeting is sane in this harness.
5. **Clarification** — Not indicated by these failures (tasks are specific; failure is execution/tooling, not intent ambiguity).

## 9. Principal-engineer conclusion — single recommended Stage 13

**Recommend Stage 13: “Real-benchmark retrieval + EDIT grounding”** — make SEARCH/rewrite under offline benchmark conditions produce ranked **source files and symbols** (not index roots or directories), and tighten EDIT/patch application so the first applied patch is anchored to retrieved content; until then, audit6 real mode will keep scoring 0/6 regardless of downstream validation quality because no edits land.

## 10. Measurement limitations (offline stubs / missing counters)

- **`retries_used` / `attempts_total`:** Always **`null`** in outcomes; compat `loop_output` does not expose these keys to the harness (`real_execution.py` reads `loop_out.get(...)`).
- **`replans_used`:** Always **0**; no multi-attempt phase history in captured snapshots.
- **`failure_bucket`:** All **`validation_regression`** despite **`structural_success: false`** — classifier uses “has validation logs + failed validation” branch, so the bucket name **overstates** “test regression after a good edit” and **understates** “never reached a successful edit.”
- **`retrieval_miss_signals`:** **Unpopulated** for compat runs; absence does **not** imply retrieval succeeded.
- **Offline LLM stubs** (`real_execution.offline_llm_stubs`): fixed canned JSON (e.g. empty rewriter steps) **dominates** behavior; results measure **current stubbed pipeline + real execution_loop**, not production model quality.
- **`--output artifacts/agent_eval_runs/audit6_first_real`:** Does **not** place artifacts under that path; only the timestamped `run_dir` (and optional `latest` symlink) is used.

---

*Artifacts: `artifacts/agent_eval_runs/20260320_035239_b124e7/{summary.json,summary.md,tasks/*,workspaces/*}`.*
