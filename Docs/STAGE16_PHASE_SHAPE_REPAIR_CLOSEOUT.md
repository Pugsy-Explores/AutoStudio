# Stage 16 — Hierarchical Phase-Shape Repair Closeout

**Date:** 2026-03-20  
**Scope:** Repair phase 1 plan construction for docs-consistency and explain-artifact tasks.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `planner/planner_utils.py` | Added WRITE_ARTIFACT to ALLOWED_ACTIONS |
| `tests/agent_eval/harness.py` | Added `_is_docs_consistency_task`, `_is_explain_artifact_task`, `_build_phase_1_steps`; phase 1 shape varies by task class |
| `agent/execution/step_dispatcher.py` | Added `_write_artifact_fn` and WRITE_ARTIFACT action handler |
| `agent/execution/executor.py` | Extract files_modified from WRITE_ARTIFACT output for StepResult |
| `tests/agent_eval/real_execution.py` | Lengthened _stub_explain_text to pass validator (>= 40 chars) |
| `tests/agent_eval/test_stage16_phase_shape_repair.py` | **New file** — 8 focused phase-shape tests |

---

## 2. Exact Phase-Shape Defect That Caused the 4 Failures

**Defect:** Phase 1 was EXPLAIN-only for all hierarchical tasks. Docs-consistency tasks require EDIT + validation; explain-artifact tasks require writing an artifact file. The two-phase harness had:

- Phase 0: SEARCH_CANDIDATES → BUILD_CONTEXT → EXPLAIN (docs lane)
- Phase 1: EXPLAIN only (code lane)

Phase 1 could never produce edits or artifact files, so docs-consistency and explain-artifact tasks could not succeed.

---

## 3. Plan-Construction Changes Made

### 3.1 Task classification (generic semantics)

- **docs-consistency:** `tags` contains ("docs", "consistency") — requires EDIT + validation
- **explain-artifact:** `grading_mode == "explain_artifact"` — requires artifact file with substrings

### 3.2 Phase 1 steps by task class

| Task class | Phase 1 steps |
|------------|--------------|
| docs-consistency | SEARCH → EDIT |
| explain-artifact | SEARCH → EXPLAIN → WRITE_ARTIFACT (artifact_path from spec.expected_artifacts[0]) |
| default (other hierarchical) | EXPLAIN only |

### 3.3 WRITE_ARTIFACT action

- Reads content from last EXPLAIN step in state.step_results
- Writes to project_root/artifact_path
- Returns files_modified for goal evaluator
- Creates parent dirs if needed

### 3.4 Validator stub fix

- `_stub_explain_text` was 39 chars; `_is_valid_explain` rejects outputs < 40 chars
- Lengthened stub so phase 0 EXPLAIN passes validation and phase 1 can run

---

## 4. Tests Added

| Test | Purpose |
|------|---------|
| `test_docs_consistency_phase1_has_edit_steps` | Phase 1 includes SEARCH + EDIT for docs-consistency |
| `test_explain_artifact_phase1_has_write_artifact` | Phase 1 includes SEARCH + EXPLAIN + WRITE_ARTIFACT |
| `test_compat_plan_unchanged` | Compat tasks retain single-phase plan |
| `test_hierarchical_invariants_two_phases` | Exactly 2 phases, docs then code lanes |
| `test_no_new_compat_loop_output_keys` | Compat path has no hierarchical keys |
| `test_default_phase1_when_no_spec` | spec=None → EXPLAIN-only (backwards compat) |
| `test_build_phase_1_steps_docs_consistency` | _build_phase_1_steps returns SEARCH+EDIT |
| `test_build_phase_1_steps_explain_artifact` | _build_phase_1_steps returns WRITE_ARTIFACT with path |

---

## 5. audit12 Before/After Summary

| Metric | Before (Stage 15) | After (Stage 16) |
|--------|-------------------|------------------|
| total_tasks | 12 | 12 |
| success_count | 8 | 8 |
| validation_pass_count | 8 | 8 |
| structural_success_count | 6 | **8** |
| attempts_total_aggregate | 6 | 12 |
| retries_used_aggregate | 0 | 0 |
| replans_used_aggregate | 0 | 0 |
| failure_bucket_histogram | planner_wasted_motion: 4 | edit_grounding_failure: 3, unknown: 1 |
| first_failing_stage_histogram | SEARCH: 4 | SEARCH: 3, VALIDATE: 1 |

**Result:** structural_success_count improved from 6 to 8. Phase 1 now executes for all 4 previously failing tasks. Two tasks (core12_mini_docs_version, core12_pin_requests_explain_trace) now reach phase 1 and complete structural flow; explain-artifact creates the artifact file.

---

## 6. Per-Task Outcomes for the 4 Previously Failing Tasks

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage |
|---------|---------|-------------------|--------------------|---------------|---------------------|
| core12_mini_docs_version | false | false | **true** | edit_grounding_failure | SEARCH |
| core12_pin_requests_explain_trace | false | false | **true** | unknown | VALIDATE |
| core12_pin_click_docs_code | false | false | false | edit_grounding_failure | SEARCH |
| core12_pin_requests_httpbin_doc | false | false | false | edit_grounding_failure | SEARCH |

**core12_mini_docs_version:** Phase 1 SEARCH+EDIT now runs. Structural success; validation fails (EDIT grounding or patch quality).

**core12_pin_requests_explain_trace:** Phase 1 SEARCH+EXPLAIN+WRITE_ARTIFACT runs. Artifact file created (`benchmark_local/artifacts/explain_out.txt`). Validation fails because stub content lacks required substrings (Session.request, hooks, ->).

**core12_pin_click_docs_code, core12_pin_requests_httpbin_doc:** Phase 1 runs but EDIT step fails (edit_grounding_failure — diff planner or retrieval does not produce valid patches).

---

## 7. Remaining Bottleneck After Stage 16, Ranked Honestly

1. **EDIT grounding / diff planner** — docs-consistency tasks reach phase 1 and run EDIT, but patches fail (wrong targets, empty changes, or validation rejection). Offline stubs limit what the diff planner can produce.

2. **Explain-artifact content quality** — artifact is written; validation fails because stub output lacks required substrings. Real model would need to produce Session.request, hooks, -> in the explanation.

3. **Phase 0 validator** — was blocking phase 1 (stub < 40 chars). Fixed by lengthening stub.

4. **Retrieval for phase 1 SEARCH** — edit_grounding_failure suggests SEARCH may return suboptimal results for docs/code alignment queries. May need query shaping or retrieval tuning for docs-consistency.

---

## Constraints Respected

- No 3+ phases
- No new parent-policy outcomes
- No REQUEST_CLARIFICATION
- No widening _is_two_phase_docs_code_intent
- No changes to hierarchical_test_locks.py
- No compat behavior changes
- No retrieval handoff merge
- No task-id-specific hacks
