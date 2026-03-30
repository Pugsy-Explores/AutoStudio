# Stage 32 — Benchmark/Eval Stack Modularization Closeout

## Summary

Stage 32 modularizes the benchmark/eval stack so benchmark plumbing is clearly separated from core agent behavior. Responsibilities are split into dedicated modules; `runner.py` and `harness.py` are reduced in logic density.

---

## 1. New Module Boundaries

| Module | Responsibility |
|--------|----------------|
| **execution_mode.py** | ExecutionMode type; `resolve_execution_mode` (real→offline); `uses_real_workspace`; `is_suite_loading_mode` |
| **integrity.py** | `ensure_integrity_fields` (mocked defaults); `build_extra_integrity`; `REQUIRED_INTEGRITY_FIELDS` |
| **success.py** | `task_success`; `failure_class_from`; `replan_observed`; `compute_success`; `count_replans` |
| **suite_loader.py** | `load_suite`; `load_specs_for_mode` (mode-specific suite loading) |
| **task_audit.py** | `build_task_audit_dict` (outcome.json `_audit` with integrity fields) |
| **summary_aggregation.py** | `aggregate_integrity_metrics`; `aggregate_histograms`; `build_per_task_outcomes`; `build_suite_label` |

---

## 2. Logic Moved Where

| Logic | From | To |
|-------|------|-----|
| Execution mode resolution | runner.py (inline) | execution_mode.resolve_execution_mode |
| uses_real_workspace check | harness.py (inline) | execution_mode.uses_real_workspace |
| Suite loading by mode | runner.py (large if/elif) | suite_loader.load_specs_for_mode |
| task_success, failure_class_from, replan_observed | harness.py | success.py |
| compute_success, count_replans | harness.py | success.py |
| ensure_integrity_fields | harness.py | integrity.py |
| build_extra_integrity | harness.py (inline dict) | integrity.build_extra_integrity |
| _audit dict construction | runner.py (inline) | task_audit.build_task_audit_dict |
| Integrity aggregation | runner.py (inline) | summary_aggregation.aggregate_integrity_metrics |
| Histogram aggregation | runner.py (inline) | summary_aggregation.aggregate_histograms |
| per_task_outcomes | runner.py (inline) | summary_aggregation.build_per_task_outcomes |
| suite_label | runner.py (nested if/else) | summary_aggregation.build_suite_label |

---

## 3. Files Changed

| File | Changes |
|------|---------|
| **tests/agent_eval/execution_mode.py** | **New** |
| **tests/agent_eval/integrity.py** | **New** |
| **tests/agent_eval/success.py** | **New** |
| **tests/agent_eval/suite_loader.py** | **New** |
| **tests/agent_eval/task_audit.py** | **New** |
| **tests/agent_eval/summary_aggregation.py** | **New** |
| **tests/agent_eval/harness.py** | Imports from new modules; removed ~60 lines |
| **tests/agent_eval/runner.py** | Imports from new modules; removed ~120 lines |
| **tests/agent_eval/real_execution.py** | Imports from success.py instead of harness |
| **tests/agent_eval/test_stage32_modularization.py** | **New** — 8 regression tests |

---

## 4. Behavior Unchanged

- mocked/offline/live_model semantics
- Stage 31 integrity fields in outcome.json and summary.json
- deprecated --real → offline
- All 29 Stage 12.1 / 28 / 31 tests pass
- All 8 Stage 32 regression tests pass

---

## 5. Remaining Benchmark-Only Logic (Future Isolation)

The following logic remains in harness/runner and is benchmark-specific. Future stages may isolate further:

| Location | Logic | Notes |
|----------|-------|-------|
| harness.py | `_compat_get_plan`, `_compat_parent_plan`, `_parent_plan_for_spec`, `_two_phase_parent_plan`, `_build_phase_1_steps` | Plan injection for compat/hierarchical; orchestration_path routing |
| harness.py | `_is_docs_consistency_task`, `_is_explain_artifact_task` | Task semantics for phase shape |
| harness.py | `_STDLIB_SHADOW_DIRS`, `_transform_pytest_cmd_for_shadowing` | Adversarial-repo pytest workaround |
| runner.py | semantic_rca_cause_histogram, task_type_histogram, instruction_explicit_path_count | Summary enrichment; could move to summary_aggregation |
| runner.py | semantic_rca.json write for failed EDIT tasks | Per-task artifact; could move to task_audit or artifact writer |
| real_execution.py | `offline_llm_stubs`, plan injection, `_compat_plan_dict_for_audit` | Entire benchmark scaffolding |

---

## 6. Regression Tests

- `test_integrity_fields_unchanged` — _audit includes all REQUIRED_INTEGRITY_FIELDS
- `test_suite_aggregation_unchanged` — summary includes integrity aggregation fields
- `test_success_computation_unchanged` — compute_success by grading_mode
- `test_execution_mode_routing_unchanged` — resolve_execution_mode, uses_real_workspace
- `test_suite_loader_mode_specific` — load_specs_for_mode returns correct counts
- `test_task_success_hierarchical` — task_success for hierarchical/compat
- `test_ensure_integrity_fields_mocked` — mocked defaults
- `test_build_suite_label` — suite label format

---

## 7. Production Impact

- **None.** All changes are under `tests/agent_eval/`. No core agent paths modified.
