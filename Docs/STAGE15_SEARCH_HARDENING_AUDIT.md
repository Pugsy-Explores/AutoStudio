# Stage 15 — Hierarchical Search/Ranking Hardening Audit

**Date:** 2026-03-20  
**Scope:** Improve retrieval/search/ranking for hierarchical explain/docs tasks without changing orchestration policy.

---

## 1. Files Changed

| File | Change |
|------|--------|
| `tests/agent_eval/harness.py` | Added `query` and `description` to SEARCH_CANDIDATES step from phase subgoal; inserted BUILD_CONTEXT step between SEARCH_CANDIDATES and EXPLAIN in phase 0 |
| `agent/tools/build_context.py` | Added `retrieval_telemetry` (viable_source_hit_count, top_hit_paths, artifact_mode) for docs mode |
| `agent/orchestrator/deterministic_runner.py` | Included `retrieval_telemetry` from phase state in `_extract_phase_context_output` |
| `tests/agent_eval/runner.py` | Extracted `retrieval_telemetry` from first phase's context_output into `_audit` |
| `tests/agent_eval/test_stage15_search_hardening.py` | **New file** — 5 focused retrieval/ranking tests |

---

## 2. Root Cause Analysis for the 4 Failed Hierarchical Tasks

Investigation of `core12_mini_docs_version`, `core12_pin_requests_explain_trace`, `core12_pin_click_docs_code`, and `core12_pin_requests_httpbin_doc`:

### Pre-Stage 15 (conversation summary)

1. **Empty query for SEARCH_CANDIDATES** — Phase 0 steps had no `query` or `description`, so `search_candidates("", ...)` returned no candidates.
2. **Missing BUILD_CONTEXT** — Phase 0 had SEARCH_CANDIDATES → EXPLAIN but no BUILD_CONTEXT, so `ranked_context` was never populated before EXPLAIN.
3. **goal_reason: "no_success_signals"** — The goal evaluator never saw a successful EXPLAIN because context was empty.

### Post-Stage 15 (observed in core12_mini_docs_version transcript)

Retrieval is now working:

- `context_output.ranked_context`: README.md with snippet "Current release: **0.9.0**"
- `retrieval_telemetry.viable_source_hit_count`: 1
- `retrieval_telemetry.top_hit_paths`: [README.md]
- `retrieval_telemetry.artifact_mode`: "docs"

Phase 0 still fails with `goal_reason: "no_success_signals"`. The bottleneck has shifted from retrieval to **phase 0 goal semantics**:

- **Docs-consistency tasks** (core12_mini_docs_version, core12_pin_click_docs_code, core12_pin_requests_httpbin_doc) require an EDIT and validation to pass (e.g. `scripts/check_readme_version.py`).
- **Explain tasks** (core12_pin_requests_explain_trace) require an artifact file with substrings.
- Phase 0 is EXPLAIN-only; it cannot produce edits or validation success.
- The goal evaluator's `docs_lane_explain_succeeded` path should treat a successful EXPLAIN as goal_met for phase 0, but phase 0 success does not imply task success — these tasks need phase 1 to perform EDIT or artifact writes.
- Phase 1 in the harness has only EXPLAIN (no SEARCH, no EDIT), so even if phase 0 passed, phase 1 would not satisfy validation.

**Summary:** Retrieval/search hardening fixed the empty-context problem. The remaining failure is that hierarchical docs-consistency and explain tasks require EDIT or artifact output, but the two-phase design has phase 0 = explain, phase 1 = explain (no edit). The `first_failing_stage=SEARCH` classification is a side effect: when no edit is attempted, `infer_first_failing_stage` returns SEARCH because `attempted_target_files` is empty.

---

## 3. Exact Retrieval/Search Changes Made

### 3.1 Harness: SEARCH_CANDIDATES query and BUILD_CONTEXT

**Before:** Phase 0 steps had no `query` or `description` on SEARCH_CANDIDATES; no BUILD_CONTEXT step.

**After:**

- SEARCH_CANDIDATES step receives `query=sg0` and `description=sg0` from `_derive_phase_subgoals(instruction)`.
- BUILD_CONTEXT step inserted between SEARCH_CANDIDATES and EXPLAIN.
- Phase 0 flow: SEARCH_CANDIDATES (with query) → BUILD_CONTEXT → EXPLAIN.

### 3.2 Docs retrieval telemetry

- `build_context` (docs mode) sets `state.context["retrieval_telemetry"]` with:
  - `viable_source_hit_count`: len(candidates)
  - `top_hit_paths`: paths from context_blocks[:8]
  - `artifact_mode`: "docs"

### 3.3 Phase context output

- `_extract_phase_context_output` includes `retrieval_telemetry` from `phase_state.context` when present.

### 3.4 Benchmark audit

- Runner extracts `retrieval_telemetry` from the first phase's `context_output` and adds it to `_audit` for per-task inspection.

---

## 4. Tests Added

| Test | Purpose |
|------|---------|
| `test_two_phase_plan_has_subgoal_as_query_for_search_candidates` | SEARCH_CANDIDATES has non-empty query from subgoal |
| `test_docs_search_returns_candidates_with_nonempty_query` | Docs retriever returns candidates with real query |
| `test_docs_search_returns_empty_with_empty_query` | Empty query returns no candidates (regression guard) |
| `test_build_docs_context_prefers_source_docs_over_junk` | No `.symbol_graph` or `__pycache__` in results |
| `test_phase0_has_build_context_step` | Phase 0 includes BUILD_CONTEXT |

---

## 5. audit12 Before/After Summary

| Metric | Before (Stage 14) | After (Stage 15) |
|--------|-------------------|------------------|
| total_tasks | 12 | 12 |
| success_count | 8 | 8 |
| validation_pass_count | 8 | 8 |
| structural_success_count | 6 | 6 |
| attempts_total_aggregate | — | 6 |
| retries_used_aggregate | 0 | 0 |
| replans_used_aggregate | 0 | 0 |
| failure_bucket_histogram | planner_wasted_motion: 4 | planner_wasted_motion: 4 |
| first_failing_stage_histogram | SEARCH: 4 | SEARCH: 4 |

**Result:** No change in success rate. Retrieval hardening fixed the empty-context path; the remaining failures are due to phase design and goal semantics, not retrieval.

---

## 6. Per-Task Outcomes for the 4 Previously Failing Tasks

| task_id | success | validation_passed | structural_success | failure_bucket | first_failing_stage |
|---------|---------|-------------------|--------------------|----------------|---------------------|
| core12_mini_docs_version | false | false | false | planner_wasted_motion | SEARCH |
| core12_pin_requests_explain_trace | false | false | false | planner_wasted_motion | SEARCH |
| core12_pin_click_docs_code | false | false | false | planner_wasted_motion | SEARCH |
| core12_pin_requests_httpbin_doc | false | false | false | planner_wasted_motion | SEARCH |

**core12_mini_docs_version** (representative): Phase 0 retrieval works (ranked_context with README.md, viable_source_hit_count=1). Phase 0 fails with goal_reason `no_success_signals`. Task requires EDIT + validation; phase 0 is EXPLAIN-only.

---

## 7. Top Remaining Bottleneck After Stage 15

**Phase design for docs-consistency and explain tasks.**

The two-phase harness has:

- Phase 0: SEARCH_CANDIDATES → BUILD_CONTEXT → EXPLAIN (docs lane)
- Phase 1: EXPLAIN only (code lane)

Docs-consistency tasks (core12_mini_docs_version, core12_pin_click_docs_code, core12_pin_requests_httpbin_doc) need:

1. Phase 0: gather docs context (now working).
2. Phase 1: SEARCH + EDIT to align files and pass validation.

Explain tasks (core12_pin_requests_explain_trace) need:

1. Phase 0: gather context and explain.
2. Phase 1: write the artifact file (or equivalent).

Phase 1 currently has no SEARCH, no EDIT, and no artifact write. Fixing this would require changes to the harness phase structure and/or goal semantics, which are out of scope for Stage 15 (no orchestration-policy changes).

**Ranked bottlenecks:**

1. **Phase 1 lacks EDIT/artifact steps** — docs-consistency and explain tasks cannot succeed without them.
2. **Phase 0 goal semantics** — `docs_lane_explain_succeeded` may not be firing as intended, or phase 0 success does not advance to a phase 1 that can complete the task.
3. **first_failing_stage classification** — SEARCH is inferred when no edit is attempted; the real failure is phase design, not retrieval.

---

## Constraints Respected

- No changes to `deterministic_runner.py` policy semantics
- No new parent-policy outcomes
- No REQUEST_CLARIFICATION
- No retrieval handoff merge
- No changes to `hierarchical_test_locks.py`
- No benchmark-specific task-id hacks
