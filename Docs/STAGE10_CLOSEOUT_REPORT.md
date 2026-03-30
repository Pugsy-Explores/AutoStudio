# Stage 10 closeout report

**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` (hierarchical orchestration — REPLAN parent-policy outcome)

---

## Shipped scope for Stage 10

- **REPLAN** added on the **non-compat two-phase** path only (`run_hierarchical` when `compatibility_mode=False` and exactly two phases).
- **`parent_policy_decision`** may now include **`decision == "REPLAN"`** with **`decision_reason == "replan_scheduled"`** (alongside existing `CONTINUE`, `RETRY`, `STOP`).
- **`_build_replan_phase(phase_plan, failure_context)`** added in **`agent/orchestrator/plan_resolver.py`** — phase-scoped replanning only; does not call `get_parent_plan()` or `run_hierarchical()`.
- **Docs lane:** replan **reseeds** from **`_docs_seed_plan`** and **deterministically widens** the first **`SEARCH_CANDIDATES`** query when `failure_class` is **`phase_validation_failed`** or **`goal_not_satisfied`**.
- **Code lane:** replan via **`plan(subgoal)`** + **`validate_plan`**, preserving phase identity fields and retry policy as implemented.
- **Same shared parent retry budget** as **RETRY**: `retry_policy.max_parent_retries` caps total parent attempts (`1 + max_parent_retries` executions per phase); no separate replan budget.
- **`phase_replanned`** trace when a new phase plan is substituted successfully (payload includes `parent_plan_id`, `phase_index`, `attempt_count`, `previous_failure_class`, `old_plan_id`, `new_plan_id`, lane, `subgoal_preview`).
- **`phase_replan_failed`** trace when replan **raises**, returns malformed data, or fails validation — followed by **terminal STOP** for that phase (no infinite loop).
- **`attempt_history`** rows may include **`plan_id`** per attempt (Stage 10 observability).

---

## Files changed in Stage 10

| Area | File |
|------|------|
| Orchestration / policy / traces | `agent/orchestrator/deterministic_runner.py` |
| Phase-scoped replan helper | `agent/orchestrator/plan_resolver.py` |
| Tests | `tests/test_two_phase_execution.py` |

---

## Tests added (Stage 10 — exact names)

All live under class **`TestStage10ReplanExecution`** in `tests/test_two_phase_execution.py`:

1. `test_policy_first_failure_retry_not_replan`
2. `test_policy_second_same_failure_class_replan`
3. `test_stage10_attempt1_fail_emits_retry_not_replan`
4. `test_stage10_attempt2_same_failure_class_emits_replan`
5. `test_stage10_replan_then_phase_succeeds_and_phase1_runs`
6. `test_stage10_replan_then_exhaust_budget_stops`
7. `test_stage10_attempt_history_length_matches_attempt_count`
8. `test_stage10_errors_merged_across_retry_and_replan_success`
9. `test_stage10_one_phase_result_row_per_phase`
10. `test_stage10_invalid_replan_terminal_stop`
11. `test_stage10_compat_path_unchanged`

---

## Proof commands and recorded counts

```bash
python3 -m pytest tests/test_two_phase_execution.py -q
```

**Recorded result:** **180 passed**

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

**Recorded result:** **203 passed**

Re-run after checkout if unrelated tests are added (counts may drift).

---

## Locked semantics (preserved)

| Invariant | Status |
|-----------|--------|
| **Compat path** — `run_hierarchical(..., compatibility_mode=True)` delegates **exactly** to `run_deterministic`; same `(state, loop_output)` | Unchanged |
| **No new top-level hierarchical `loop_output` keys** | Unchanged |
| **`phase_count == len(phase_results)`** — **executed** phases only | Unchanged |
| **One final `phase_result` row per phase** | Unchanged |
| **`phase_result["loop_output"]` / `context_output`** — **final attempt only** | Unchanged |
| **`errors_encountered_merged`** — accumulates across **all** attempts including pre-replan | Unchanged |
| **REPLAN consumes the same `max_parent_retries` budget** as RETRY | Locked in Stage 10 |
| **No recursion** from `_build_replan_phase` into `get_parent_plan` / `run_hierarchical` | Locked |

---

## Explicit non-goals (Stage 10)

| Non-goal | Notes |
|----------|-------|
| **REQUEST_CLARIFICATION** | Not implemented |
| **Retrieval merge** using `prior_phase_ranked_context` | No changes to execution/retrieval pipeline |
| **≥ 3 phases** | `NotImplementedError` guard unchanged |
| **`run_deterministic`** | Not modified |
| **`execution_loop.py`, `replanner.py`, `step_dispatcher.py`** | Not modified |
| **`tests/hierarchical_test_locks.py`** | Not modified |

---

## Why Stage 10 mattered

- **RETRY alone repeats the same phase plan** — when the plan is wrong for the instruction (e.g. docs seed query misses the artifact class), another attempt with identical steps often **does not** improve outcomes.
- **REPLAN** targets **repeated, consecutive failures with the same `failure_class`** within a phase — a minimal signal that the **plan-quality ceiling** may have been reached under the same-plan retry policy, and that a **fresh plan** (still under the shared parent budget) is the next proportional step before expanding scope to clarification or retrieval redesign.

---

## Known limits

- **REPLAN trigger is heuristic:** same **`failure_class`** on **consecutive** failed attempts in the same phase. Different failures, flaky classes, or non-consecutive patterns are **not** covered by this rule.
- **Docs-lane replan widening** is **deterministic and intentionally narrow** (query suffix / reseed behavior) — not a full retrieval redesign.
- **Code-lane replan** still depends on **`plan(subgoal)`** quality; REPLAN does not upgrade the planner or router.

---

## Stage 11 recommendation

Author a **`Docs/STAGE11_DECISION_MEMO.md`** (or equivalent) that **compares**:

1. **REQUEST_CLARIFICATION** — terminal caller-visible outcome, roadmap ordering vs REPLAN, likely **`hierarchical_test_locks.py` / caller contract** impact.  
2. **Retrieval-context consumption** — merging **`prior_phase_ranked_context`** (and related handoff keys) into Phase 1 retrieval/ranking — **frozen-module** touch risk (`execution_loop` / `step_dispatcher` / `replanner` per project policy).

**Bias:** prefer the **smallest blast-radius** slice that still advances the architecture materially (config-only or plan-construction-only if possible); **do not** bundle clarification + retrieval in one PR without explicit approval.

---

*End of Stage 10 closeout report.*
