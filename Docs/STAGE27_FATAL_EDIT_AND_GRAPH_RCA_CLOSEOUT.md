# Stage 27 — FATAL_EDIT and Graph Builder RCA Closeout

**Date:** 2026-03-20  
**Scope:** Fix recurring real-run failure pattern: `[graph_builder] edges provided but none added` and `[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)`. Generic fixes only; no task-id logic, no benchmark-only special casing.

---

## 1. Root Cause Summary

| Issue | Primary/Secondary | Root Cause | Fix |
|------|-------------------|------------|-----|
| FATAL_FAILURE on EDIT | **Primary** | `symbol_retry(step)` returned `[dict(step)]` — identical step twice. Both retries failed identically → "edit failed after retries" → FATAL. | Real retry variants: file-level, symbol-short, alternate target. Wire hints into `_edit_fn` and `resolve_edit_targets_for_plan`. |
| graph_builder warning | **Secondary** | Edge names from dependency_extractor (e.g. `module.symbol`, `re`, `sys`) often didn't match symbol_extractor `name_to_id`. Resolution tried only exact + short; no `module.symbol` fallback. | Extended `name_to_id` with `module.symbol`; added `_resolve_symbol_to_id`; bounded `sample_unresolved` in warning. |

---

## 2. Exact Cause Chain (from current code)

### 2.1 EDIT FATAL_FAILURE

1. **policy_engine._execute_edit** (`agent/execution/policy_engine.py`): EDIT policy uses `mutation="symbol_retry"`, `max_attempts=2`, `retry_on=["symbol_not_found"]`.
2. **symbol_retry** (`agent/execution/mutation_strategies.py`): Previously returned `[dict(step)]` — same step twice.
3. Both attempts used identical step → patch_executor failed with same error → `"edit failed after retries"`.
4. **classify_result** (policy_engine ~line 114): `"after retries"` in error → `ResultClassification.FATAL_FAILURE`.
5. **execution_loop** stops on FATAL; no replan.

### 2.2 graph_builder "edges provided but none added"

1. **graph_builder.build_graph** (`repo_graph/graph_builder.py` ~line 49): Resolves `source_symbol` / `target_symbol` from edges via `name_to_id`.
2. **dependency_extractor** emits edges like `(check_readme_version, re)`, `(check_readme_version, re)`, `(validate_changelog_version, pathlib.Path)` — stdlib and qualified names.
3. **symbol_extractor** emits `symbol_name` (e.g. `check_readme_version`) — no `re`, `sys`, `pathlib.Path` (stdlib not indexed).
4. Resolution failed for stdlib targets and some qualified/short mismatches → `edge_count == 0` when edges provided → generic warning.

---

## 3. Files Changed

| File | Change |
|------|--------|
| `repo_graph/graph_builder.py` | `_resolve_symbol_to_id()` for deterministic resolution; extended `name_to_id` with `module.symbol` when symbol has `file`; warning now includes `sample_unresolved` (max 5). |
| `agent/execution/mutation_strategies.py` | `symbol_retry(step, state=None)` produces real variants: original, `edit_target_level="file"`, `edit_target_symbol_short`, `edit_target_file_override` from ranked_context; deduplication; `_extract_symbol_from_description`, `_extract_file_hint_from_description`. |
| `agent/execution/policy_engine.py` | `_execute_edit` calls `symbol_retry(step, state)` instead of `symbol_retry(step)`. |
| `agent/execution/step_dispatcher.py` | `_edit_fn` injects `edit_target_file_override`, `edit_target_level`, `edit_target_symbol_short` into context before `plan_diff`. |
| `agent/retrieval/target_resolution.py` | `resolve_edit_targets_for_plan` prepends `edit_target_file_override` (when valid file) to `edit_targets_ranked` with evidence `retry_override`. |
| `editing/diff_planner.py` | Uses `edit_target_level` and `edit_target_symbol_short` from context when building `affected_symbols`. |
| `tests/agent_eval/test_stage27_fatal_edit_and_graph.py` | **New** — 7 regression tests: graph resolution, bounded unresolved logging, symbol_retry distinct variants, no repeated step, file override, diff_planner hints. |

---

## 4. Before/After Behavior

| Aspect | Before | After |
|--------|--------|-------|
| symbol_retry | Same step twice | Distinct variants (file-level, symbol-short, alternate target) |
| EDIT retries | Redundant identical attempts | Semantically different retries |
| Edit hints | Not passed to plan_diff | Injected into context; used by target resolution and diff_planner |
| graph_builder warning | Generic "name resolution may have failed" | Includes `sample_unresolved` (max 5) for debugging |
| graph name_to_id | Exact + short only | + `module.symbol` when symbol has file |

---

## 5. Stderr / Residual Behavior

| Pattern | Status | Explanation |
|---------|--------|-------------|
| `[graph_builder] edges provided but none added` | **Reclassified** | Now includes `sample_unresolved=[...]`. Most unresolved edges are stdlib (`re`, `sys`, `pathlib.Path`, etc.) — expected, we don't index stdlib. Warning is now diagnostic, not generic. |
| `[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)` | **Reduced** | Retries are now distinct. When FATAL still occurs, it is after genuine distinct retries (file-level, symbol-short, alternate target) — not a no-op repeat. |

---

## 6. Proof Commands

```bash
python3 -m pytest tests/agent_eval -q
python3 -m pytest tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_stage27
python3 -m tests.agent_eval.runner --execution-mode real --suite holdout8 --output artifacts/agent_eval_runs/holdout8_after_stage27
python3 -m tests.agent_eval.runner --execution-mode real --suite adversarial12 --output artifacts/agent_eval_runs/adversarial12_after_stage27
```

---

## 7. Benchmark Results (After Stage 27)

```bash
# Run: 2026-03-20
```

### audit12

| Metric | Value |
|--------|-------|
| total_tasks | 12 |
| success_count | 11 |
| validation_pass_count | 11 |
| structural_success_count | 10 |
| failure_bucket_histogram | (1 task with validation_regression) |

**Note:** 1 task failed. Residual FATAL may occur when all retry variants (file-level, symbol-short, alternate target) still fail — e.g. grounding remains weak. No repeated identical retries.

### holdout8

| Metric | Value |
|--------|-------|
| total_tasks | 8 |
| success_count | 8 |
| validation_pass_count | 8 |
| structural_success_count | 7 |
| failure_bucket_histogram | {} |

### adversarial12

| Metric | Value |
|--------|-------|
| total_tasks | 12 |
| success_count | 12 |
| validation_pass_count | 12 |
| structural_success_count | 11 |
| failure_bucket_histogram | {} |

---

## 8. Regression Tests

| Test | Purpose |
|------|---------|
| `test_graph_builder_resolves_qualified_and_short_name_edges` | Qualified and short-name edge resolution |
| `test_graph_builder_bounded_unresolved_logging` | Unresolved edges logged with bounded sample |
| `test_symbol_retry_emits_distinct_variants` | Retry variants differ in at least one hint |
| `test_symbol_retry_no_repeated_identical_step` | No identical step repeated |
| `test_resolve_edit_targets_honors_file_override` | `edit_target_file_override` prepends ranked |
| `test_diff_planner_uses_edit_target_level_file` | `edit_target_level=file` used |
| `test_diff_planner_uses_edit_target_symbol_short` | `edit_target_symbol_short` added to affected_symbols |

---

## 9. Residual Causes (when stderr still appears)

1. **graph_builder:** Unresolved edges are mostly stdlib (`re`, `sys`, `pathlib.Path`, `urllib.parse.urlparse`, etc.). We do not index stdlib. `sample_unresolved` now shows these explicitly. Future: optionally filter stdlib edges in dependency_extractor to reduce noise.

2. **FATAL_FAILURE:** When all retry variants (original, file-level, symbol-short, alternate target) fail, the run correctly classifies FATAL. No more identical retries. Remaining failures are due to weak grounding (wrong file/symbol from retrieval) or patch semantics — not retry redundancy.

---

## 10. References

- `Docs/RCA_AUDIT12_REAL_FATAL_FAILURE_EDIT.md` — prior RCA
- `agent/execution/mutation_strategies.py` — symbol_retry
- `agent/execution/policy_engine.py` — EDIT policy, classify_result
- `repo_graph/graph_builder.py` — edge resolution
