# Stage 7 closeout report

**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` line (hierarchical orchestration, post–Stage 6)

---

## Scope completed

**Stage 7** activates the **already-implemented** Stage 4/5 parent-retry machinery for **production** `two_phase_docs_code` plans by replacing the hardcoded `max_parent_retries: 0` in `_build_two_phase_parent_plan` with a **config-driven, coerced** budget. **No** new retry engine, **no** `deterministic_runner.py` execution rewrite, **no** new `loop_output` keys.

---

## Exact runtime delta

| Before Stage 7 | After Stage 7 |
|----------------|---------------|
| `_build_two_phase_parent_plan` set `retry_policy.max_parent_retries` to **0** on both phases | Same builder sets `max_parent_retries` to **`_coerce_max_parent_retries(TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE)`**, default **1** |

Default behavior change: shipped two-phase plans now allow **one** parent-level retry per phase (`1 + max_parent_retries` attempts = 2 tries per phase when the budget is 1), matching what Stage 4 already enforced when `max_parent_retries > 0`.

---

## Files changed in Stage 7 (implementation)

| Area | File |
|------|------|
| Config | `config/agent_config.py` — `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE = 1` |
| Planning | `agent/orchestrator/plan_resolver.py` — `_coerce_max_parent_retries`, `_budget` applied to Phase 0 and Phase 1 `retry_policy` |
| Tests | `tests/test_two_phase_execution.py` — `TestStage7RetryBudgetConfiguration`, `TestStage7CloseoutInvariants` |

**Unchanged:** `agent/orchestrator/deterministic_runner.py`, `run_deterministic`, `execution_loop.py`, `replanner.py`, `step_dispatcher.py`, `tests/hierarchical_test_locks.py`.

---

## Proof commands (recorded at closeout)

```bash
python3 -m pytest tests/test_two_phase_execution.py -q
```

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

## Proof results (current checkout)

| Command | Result |
|---------|--------|
| `tests/test_two_phase_execution.py` only | **161 passed** |
| Three-file hierarchical slice | **184 passed** |

Re-run after checkout if unrelated tests are added (counts may drift).

---

## Locked semantics (Stage 7 audit)

| Invariant | Status |
|-----------|--------|
| **Compat path** — `run_hierarchical` with `compatibility_mode=True` delegates only to `run_deterministic`; same `(state, loop_output)` | Unchanged (`test_compat_path_unaffected_by_retry_budget_config`, `assert_compat_loop_output_has_no_hierarchical_keys`) |
| **No new hierarchical `loop_output` keys** | Confirmed — `HIERARCHICAL_LOOP_OUTPUT_KEYS` unchanged; Stage 7 adds no top-level keys |
| **Invalid budget** — non-int, `bool`, negative → **0** retries | `_coerce_max_parent_retries`; tests with `-1`, `"1"`, `True` |
| **Same budget on both phases** | Single `_budget` variable; `test_shipped_parent_plan_equal_retry_budget_both_phases` |
| **Stage 4/5 observability when retries run** — `attempt_history`, `retries_used`, `errors_encountered_merged` | Exercised with plans from `_build_two_phase_parent_plan` (Phase 0 retry, Phase 1 retry, `phase_count` vs `attempts_total`) |

---

## Relation to Stage 4 / Stage 5

- **Stages 4–5** implemented parent retry **execution**, merged errors, and attempt history in `deterministic_runner.py`.
- **Stage 7** does **not** duplicate that logic. It is **configuration-only** at plan construction: the shipped `two_phase_docs_code` `ParentPlan` now carries a non-zero default retry budget so production runs can hit the existing code paths.

---

## Why Stage 7 mattered

Before Stage 7, every real `two_phase_docs_code` plan had `max_parent_retries: 0`, so the Stage 4 retry loop never ran in production for those plans (only in tests using `_make_two_phase_parent_plan_with_retry_policy` or similar). Stage 7 **wires the default** so retries and observability are **reachable** without hand-crafted plans.

---

## Explicit non-goals (preserved)

| Non-goal | Notes |
|----------|--------|
| **REPLAN** | Not shipped; parent policy remains `CONTINUE` / `RETRY` / `STOP` only |
| **REQUEST_CLARIFICATION** | Not a parent-level outcome |
| **≥ 3 phases** | `len(phases) != 2` guard unchanged |
| **Execution engine rewrite** | `deterministic_runner` retry loop unchanged |
| **Widen `_is_two_phase_docs_code_intent`** | Not in Stage 7 scope |

---

## Closeout tests (`TestStage7CloseoutInvariants`)

Adds contract locks not duplicated elsewhere: equal budget both phases, **Phase 1** retry using only `_build_two_phase_parent_plan`, and `phase_count` vs `attempts_total` after Phase 0 retry.

---

*End of Stage 7 closeout report.*
