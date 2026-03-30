# Stage 5 closeout report

## Scope completed

**Stage 5** adds **bounded parent-retry observability** for hierarchical runs: structured **`attempt_history`** on each final `phase_result` row, plus top-level **`attempts_total`** and **`retries_used`**, without changing Stage 4 retry **policy** or **compat** delegation.

## Production code touched

- `agent/orchestrator/deterministic_runner.py` — `_snapshot_phase_attempt_for_history`, append per-attempt snapshots to `attempt_history`, attach to each final `phase_result`; `_build_hierarchical_loop_output` adds `attempts_total` and `retries_used`.

## Shipped metadata

### Per `phase_result` (hierarchical only)

- **`attempt_history`:** `list[dict]`, one entry per execution attempt for that phase, in order.
- Each entry includes at minimum:
  - **`attempt_count`** — 1-based index for that attempt
  - **`success`**, **`goal_met`**, **`goal_reason`**, **`failure_class`**
  - **`errors_encountered`** — that attempt’s `loop_output["errors_encountered"]` only (not cross-attempt merge)
  - **`phase_validation`** — normalized metadata dict for that attempt (same shape as on the rolling `phase_result` during that attempt)
  - **`parent_retry`** — normalized metadata dict for that attempt

**Invariant:** `attempt_history[-1]` matches the final outcome fields on the parent `phase_result` for: `success`, `goal_met`, `goal_reason`, `failure_class`, `phase_validation`, `parent_retry`.

**Length:** `len(attempt_history) == phase_result["attempt_count"]` (always ≥ 1).

### Top-level `loop_output` (hierarchical only)

- **`attempts_total`:** Sum of `attempt_count` over all `phase_results` (= total `execution_loop` invocations for the hierarchical run).
- **`retries_used`:** Sum over phases of `(attempt_count - 1)` = `attempts_total - len(phase_results)` when each phase has valid `attempt_count`.

## What stayed frozen

- **`run_deterministic`**, **`execution_loop`**, **`replanner`**, **`step_dispatcher`:** unchanged.
- **Retry decisions:** `_parent_policy_decision_after_phase_attempt` semantics unchanged (`CONTINUE` / `RETRY` / `STOP`).
- **`phase_results`:** still **one final row per phase**; **`errors_encountered_merged`** unchanged.
- **`phase_count`:** executed phases only (`len(phase_results)`).
- **Handoff:** only from final successful phase output.
- **Compatibility path:** still returns the exact `run_deterministic` `loop_output` object with **no** hierarchical-only keys (see `tests/hierarchical_test_locks.py`).

## Explicit non-goals (Stage 5)

- No new planner / `get_plan` calls.
- No change to `max_parent_retries` interpretation or attempt budget.
- No new trace event **names** required for Stage 5 (existing `phase_completed` / `parent_policy_decision` per attempt remain).

## Compatibility invariants

Extended **`HIERARCHICAL_LOOP_OUTPUT_KEYS`** with **`attempts_total`** and **`retries_used`**. Extended per-phase forbidden top-level names with **`attempt_history`** (must not appear on compat `loop_output`).

Lock helper: `tests/hierarchical_test_locks.py` — `assert_compat_loop_output_has_no_hierarchical_keys`.

## Tests used as proof

- `tests/test_parent_plan_schema.py`
- `tests/test_run_hierarchical_compatibility.py`
- `tests/test_two_phase_execution.py` (includes `TestStage5AttemptHistory`)

## Proof command

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

## Proof result (recorded at Stage 5 closeout)

**160 passed** (full proof command above).

```bash
python3 -m pytest tests/test_two_phase_execution.py -q
```

**137 passed**.

## Relation to Stage 4

Stage 4 implemented real retries and **`errors_encountered_merged`**. Stage 5 adds **inspectable per-attempt rows** in **`attempt_history`** and run-level **`attempts_total` / `retries_used`** for dashboards and debugging, without altering when retries run.
