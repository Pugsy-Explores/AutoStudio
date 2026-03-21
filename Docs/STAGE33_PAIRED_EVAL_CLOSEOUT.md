# Stage 33 — Live-Model Representative Evaluation and Offline-vs-Live Gap Audit Closeout

## Summary

Stage 33 adds a paired evaluation flow: the same task set runs in both offline and live_model modes, with comparison artifacts that measure whether offline results are a good proxy for live-model behavior.

---

## 1. Paired Suite Definition

**`tests/agent_eval/suites/paired4.py`**

- 4 tasks: `core12_mini_repair_calc`, `core12_mini_repair_parse`, `core12_mini_feature_flags`, `core12_pin_typer_repair`
- Same task IDs as live4 subset
- `load_paired4_specs(evaluation_kind=...)` — `execution_regression` for offline, `full_agent` for live_model
- `suite_loader` supports `paired4` for both modes

---

## 2. Comparison/Reporting Code

**`tests/agent_eval/paired_comparison.py`**

| Function | Responsibility |
|----------|----------------|
| `compute_summary_deltas(offline_summary, live_summary)` | Deltas for success_count, validation_pass_count, structural_success_count, model_call_count_total, retries, replans |
| `_per_task_deltas(offline_per_task, live_per_task)` | Per-task success/validation/structural deltas |
| `_failure_bucket_deltas(...)` | Failure bucket histogram deltas |
| `_semantic_rca_deltas(offline_run_dir, live_run_dir, task_ids)` | Semantic RCA cause histogram deltas from task semantic_rca.json |
| `_integrity_validity(offline_summary, live_summary)` | Integrity validity comparison |
| `derive_judgment(deltas, per_task_deltas)` | Blunt judgment: `offline_is_predictive` / `offline_is_partially_predictive` / `offline_is_misleading` |
| `build_comparison_artifact(offline_run_dir, live_run_dir)` | Full JSON + markdown report |

**`tests/agent_eval/run_paired.py`**

- `run_paired(output_arg, task_filter=..., mock_live_model=...)` — runs paired4 in offline + live_model, writes comparison.json and comparison.md
- CLI: `python3 -m tests.agent_eval.run_paired --output artifacts/agent_eval_runs/paired_latest`

---

## 3. Judgment Logic (Evidence-Based)

| Judgment | Condition |
|----------|-----------|
| **offline_is_predictive** | `abs(success_delta) <= 1` and `flips_off_to_fail <= 1` and `flips_off_to_ok <= 1` |
| **offline_is_misleading** | `success_delta <= -2` or `flips_off_to_fail >= 2` |
| **offline_is_partially_predictive** | Otherwise |

No score tuning; no benchmark-specific heuristics.

---

## 4. Runner Changes

- `run_suite(..., output_dir: Path | None = None)` — when set, writes to that directory instead of a timestamped subdir (used by run_paired)

---

## 5. Regression Tests

| Test | Coverage |
|------|----------|
| `test_paired_mode_comparison_output` | comparison.json and comparison.md structure |
| `test_integrity_enforcement_for_live_model` | live_model never uses offline stubs; integrity_valid |
| `test_same_task_set_comparison` | paired4 loads same task IDs for offline and live_model |
| `test_summary_delta_computation` | compute_summary_deltas structure |
| `test_derive_judgment_predictive` | judgment when deltas small |
| `test_derive_judgment_misleading` | judgment when live much worse |
| `test_derive_judgment_partially_predictive` | judgment when live better than offline |
| `test_per_task_deltas_structure` | per-task delta fields |
| `test_paired4_suite_label` | suite_label for paired4 |
| `test_run_paired_integration` | run_paired end-to-end (mocked live) |

---

## 6. Blunt Judgment (Initial Evidence)

**At Stage 33 completion, the judgment is derived from the comparison logic only.** No live-model runs have been executed in CI; the judgment is computed from whatever runs exist.

To obtain a real judgment:

- Run `python3 -m tests.agent_eval.run_paired --output artifacts/agent_eval_runs/paired_latest` with a configured live model
- Inspect `comparison.json` and `comparison.md`
- The `judgment` field will be one of:
  - **offline_is_predictive** — offline is a good proxy
  - **offline_is_partially_predictive** — offline is a rough proxy; expect some variance
  - **offline_is_misleading** — offline is not a reliable proxy; do not use for live evaluation decisions

---

## 7. Production Impact

- **None.** All changes are under `tests/agent_eval/`. No core agent paths modified.
