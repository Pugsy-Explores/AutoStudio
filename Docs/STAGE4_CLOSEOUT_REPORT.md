# Stage 4 closeout report

## Scope completed

Hierarchical phased orchestration **Stage 4** is closed out: **real parent-level retries** on the non-compatibility hierarchical path only, with locked aggregation and observability semantics, plus invariant tests and documentation. No changes to `run_deterministic()`, `execution_loop`, `replanner`, or `step_dispatcher`.

## Production code touched in Stage 4

- `agent/orchestrator/deterministic_runner.py` — parent retry loop around each phase (fresh `AgentState` per attempt), `_parent_policy_decision_after_phase_attempt`, `errors_encountered_merged` on each `phase_result`, hierarchical aggregation of merged attempt errors.

## What stayed frozen (do not regress)

- **Compatibility path:** `run_hierarchical(..., compatibility_mode=True)` delegates **only** to `run_deterministic` and returns its `(state, loop_output)` with **no** hierarchical-only keys on `loop_output`.
- **`run_deterministic`:** Unchanged.
- **`phase_count` (hierarchical `loop_output`):** Counts **executed phases** only — `len(phase_results)` — not planned phase total and not retry attempts.
- **`phase_results`:** **One row per phase**, **final outcome only**; `attempt_count` is the total tries for that phase.
- **Handoff:** Built only from the **final successful** phase result for the prior phase (after retries, if any).

## Retry semantics (exact)

- For each phase, `retry_policy.max_parent_retries` (non-negative integer) allows up to **`1 + max_parent_retries`** execution attempts for that phase.
- **Attempt 1** always runs. Each attempt uses a **new** `AgentState` from `_build_phase_agent_state` (no reuse of mutable phase state across attempts).
- On **success** (`goal_met` and phase validation pass): stop retrying that phase; append one `phase_result`, then continue to the next phase or finish.
- On **failure** with attempts remaining: **`parent_policy_decision` = `RETRY`**, `decision_reason` = `parent_retry_scheduled`, then rerun **only** that phase.
- On **failure** with **no** attempts remaining: terminal **`STOP`** with the same failure reasons as Stage 2/3 (`goal_not_met`, `phase_failed`, etc.), append one failed `phase_result`, stop the hierarchical run (later phases do not run).

## `phase_results` shape

Each entry is a **single consolidated** `PhaseResult`-style dict for that phase:

- **`success` / `goal_met` / `failure_class` / `phase_validation`:** Reflect the **final** attempt only.
- **`attempt_count`:** Total parent attempts executed for that phase.
- **`loop_output`:** The execution loop output from the **final** attempt.
- **`errors_encountered_merged` (hierarchical, per phase row):** Present on each `phase_results[i]` dict (not on compatibility `loop_output`). Concatenation of `errors_encountered` from **each** attempt’s `loop_output` for that phase, used when building top-level `errors_encountered`. With a single attempt, the list still exists and matches that attempt’s loop errors.

## `phase_count` semantics

- **`loop_output["phase_count"]`** == **`len(phase_results)`** == number of phases that produced a final row (whether success or terminal failure).
- Not equal to the number of `execution_loop` invocations when retries occur.

### Disambiguation: planned vs executed phase counts

- **Trace / telemetry** (e.g. `run_hierarchical_start`): the `phase_count` field in the payload is the **planned** number of phases from the parent plan (`len(parent_plan["phases"])`), not “executed so far.”
- **`loop_output["phase_count"]` (hierarchical):** **executed** phases only — same as `len(phase_results)` — and can be less than the planned total when the run stops early.
- Do not compare trace `phase_count` to `loop_output["phase_count"]` without reading which surface you are on.

## Event semantics

- **`phase_completed`:** Emitted **once per attempt** (same `phase_index`, increasing `attempt_count`).
- **`parent_policy_decision`:** Emitted **once per attempt**; values include **`CONTINUE`** (phase succeeded), **`RETRY`** (another parent attempt will run), or **`STOP`** (terminal for that phase).
- Existing event **names** are unchanged from Stage 3.

## Top-level `errors_encountered` aggregation

- For each phase, includes **all** strings from **`errors_encountered_merged`** for that phase (every attempt’s loop errors).
- **Synthetic** markers `phase_<i>_goal_not_met` or `phase_<i>_failed:<failure_class>` are appended **at most once per failed phase** (terminal outcome), not once per failed attempt.

## Compatibility invariants

- Returned `loop_output` must not contain hierarchical-only keys, including:  
  `phase_results`, `phase_validation`, `parent_retry`, `parent_plan_id`, `phase_count`, `parent_goal_met`, `parent_goal_reason`, `parent_retry_eligible`, `parent_retry_reason`, `max_parent_retries`, or top-level `errors_encountered_merged`.
- Lock tests: `tests/test_run_hierarchical_compatibility.py` (`TestStage3CompatibilityInvariants`), `tests/test_two_phase_execution.py` (`TestStage4RetryInvariants`), and shared key list `tests/hierarchical_test_locks.py`.

## Tests used as proof

- `tests/test_parent_plan_schema.py`
- `tests/test_run_hierarchical_compatibility.py`
- `tests/test_two_phase_execution.py`

## Proof command

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

## Proof result (recorded at Stage 4 closeout)

**153 passed** (full proof command above).

```bash
python3 -m pytest tests/test_two_phase_execution.py -q
```

**130 passed** (recorded at Stage 4 release-readiness pass).

## Explicit non-goals (Stage 4)

- No Stage 5 features (e.g. three-phase decomposition, new planner contracts).
- No changes to `execution_loop`, `replanner`, `step_dispatcher`, or `run_deterministic`.
- No parallel phase execution.
- No replanning of the parent plan from the orchestrator (retries re-execute the **same** phase plan/steps).

## Relation to Stage 3

Stage 3 added **reporting-only** parent retry eligibility. Stage 4 makes eligibility and **`RETRY`** decisions **real** for hierarchical runs when `max_parent_retries > 0`. Stage 3 **phase validation** enforcement and summaries remain in force; final `phase_validation` on each `phase_result` reflects the **final** attempt.

**Top-level vs per-phase retry metadata:** `phase_results[i]["parent_retry*"]` describe that phase’s last attempt and eligibility. Top-level `parent_retry`, `parent_retry_eligible`, and `parent_retry_reason` on `loop_output` summarize the **whole run** (e.g. `all_phases_succeeded` vs `max_parent_retries_exhausted`). Do not assume scalar equality between a phase row and the top-level summary.

## Next work

Further work should branch from the line that contains this report; do not relax the compatibility or `phase_count` contracts without an explicit design revision.
