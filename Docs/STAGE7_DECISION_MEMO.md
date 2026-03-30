# Stage 7 — Architecture Decision Memo

**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` line  
**Precondition:** Stages 1–6 closed out — 173 tests passing on proof command  
**Status:** Decision memo only. No code was changed.

---

## 1. Current Repo Stage Map

| Repo stage | Status | What shipped | Key files changed |
|---|---|---|---|
| **Stage 1** | Complete | `parent_plan.py` schemas; `get_parent_plan`; `run_hierarchical` compat delegation | `agent/orchestrator/parent_plan.py` (new), `plan_resolver.py`, `deterministic_runner.py` |
| **Stage 2** | Complete | `_is_two_phase_docs_code_intent`; `_build_two_phase_parent_plan`; phase loop; context handoff; `GoalEvaluator.evaluate_with_reason(phase_subgoal=...)` | `plan_resolver.py`, `deterministic_runner.py`, `goal_evaluator.py` |
| **Stage 3** | Complete | Phase validation enforcement; trace/metadata observability; parent-retry **reporting** | `deterministic_runner.py` |
| **Stage 4** | Complete | Real parent-retry execution (`CONTINUE` / `RETRY` / `STOP`); `errors_encountered_merged`; invariant tests | `deterministic_runner.py` |
| **Stage 5** | Complete | `attempt_history` per phase; top-level `attempts_total` / `retries_used`; compat lock extension | `deterministic_runner.py` |
| **Stage 6** | Complete | `_derive_phase_subgoals` connector coverage (5→13 connectors); `two_phase_near_miss` trace on compat fallback | `plan_resolver.py` |

### 1.1 Roadmap-to-Repo Label Warning

**Roadmap stage labels (in `Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2) do not map 1:1 to repo stage numbers.** Roadmap Stage 3 ("Parent Policy and Escalation") covers repo Stages 3–4 for `CONTINUE`/`RETRY`/`STOP`, but `REPLAN` and `REQUEST_CLARIFICATION` — also listed under Roadmap Stage 3 — were explicitly deferred and remain unimplemented. Do not use roadmap stage numbers as repo stage numbers in code review or planning. When discussing implementation state, use repo stage numbers only.

---

## 2. Locked Invariants That Stage 7 Must Preserve

These are non-negotiable. Any Stage 7 candidate that violates one of these is rejected without further analysis.

| # | Invariant | Where enforced |
|---|---|---|
| **L1** | Compat path output is the exact `run_deterministic` delegation: `(state, loop_output)` with no hierarchical-only keys | `assert_compat_loop_output_has_no_hierarchical_keys` in `tests/hierarchical_test_locks.py` |
| **L2** | `loop_output["phase_count"]` == `len(phase_results)` == **executed** phases only (never planned phase count, never attempt count) | `_build_hierarchical_loop_output` in `deterministic_runner.py`; `TestStage4RetryInvariants` |
| **L3** | **One final phase_result row per phase.** `attempt_count` = total attempts for that phase. `attempt_history[-1]` matches final phase outcome fields | `_build_hierarchical_loop_output`; `TestStage5AttemptHistory` |
| **L4** | Handoff (`prior_phase_ranked_context`, `prior_phase_retrieved_symbols`, `prior_phase_files`) is built only from the **final successful** phase result after all retries | `_build_phase_context_handoff` in `deterministic_runner.py` |
| **L5** | `len(phases) != 2` raises `NotImplementedError` on the non-compat path in `run_hierarchical` unless explicitly re-approved as a separate architecture decision | `run_hierarchical` line 699–703 in `deterministic_runner.py` |
| **L6** | `HIERARCHICAL_LOOP_OUTPUT_KEYS` and `_PHASE_RESULT_FIELD_NAMES` in `tests/hierarchical_test_locks.py` are the authoritative key contracts; adding a new top-level compat key is a signal of wrong scope | `hierarchical_test_locks.py` (never modified after Stage 5) |

---

## 3. Candidate Evaluation

### Candidate A — REPLAN Parent-Policy Outcome

#### What problem it solves

`_parent_policy_decision_after_phase_attempt` (line 408, `deterministic_runner.py`) currently returns one of `CONTINUE`, `RETRY`, or `STOP`. `RETRY` re-executes the same phase steps with a fresh `AgentState`. REPLAN would instead call `_build_two_phase_parent_plan` (or `get_plan` for Phase 1) with the failed phase's failure context injected as `retry_context`, producing a *new plan* before re-executing. This matters when repeated execution of the same steps is unlikely to succeed (e.g., Phase 0 returned empty context because the wrong query was used — the plan itself is the problem, not the execution engine).

#### Required files to change

- **`agent/orchestrator/deterministic_runner.py`** — `_parent_policy_decision_after_phase_attempt` must gain a REPLAN branch. The retry loop in `run_hierarchical` (lines 734–892) must handle REPLAN: call `_build_two_phase_parent_plan` or `_build_replan_phase` with failure context, update `phase_plan` inside the loop, restart the attempt. This requires mutating or replacing `phase_plan` mid-loop, which is a structural change to the per-phase attempt block.
- **`agent/orchestrator/plan_resolver.py`** — a new `_build_replan_phase(phase_plan, failure_context)` function, or extending `_build_two_phase_parent_plan` to accept `retry_context` per phase.
- **`tests/test_two_phase_execution.py`** — new test class for REPLAN semantics.

#### Blast radius

**Large.** The only file currently frozen for execution logic is `deterministic_runner.py`. This candidate requires a structural change to the per-phase retry loop (the innermost `for attempt_number in range(...)` block). Every retry invariant test must be audited:

- Does `attempt_history` grow correctly across a REPLAN (new plan = new attempt, same phase)?
- Does `errors_encountered_merged` accumulate the failed-plan attempt's errors before REPLAN?
- Does `phase_result["attempt_count"]` increment across REPLAN'd attempts (it should, since each REPLAN is a new `execution_loop` invocation)?
- Does REPLAN count against `max_parent_retries` the same as RETRY, or is it a separate budget?
- What is `phase_result["loop_output"]` when REPLAN occurs — the first attempt, the replan'd attempt, or the final?

None of these are currently answered by invariants. Every answer requires new invariant tests and new behavior in `_build_hierarchical_loop_output`.

#### New invariants required

1. REPLAN consumes one retry budget unit (same counter as RETRY, or separate?). Must decide and lock.
2. `attempt_history` entry for a REPLAN attempt must record the *original* plan's failure, not the replan'd plan's steps.
3. `errors_encountered_merged` must include the failed-plan attempt's errors before the replan'd attempt runs.
4. `phase_result["loop_output"]` must be the final attempt's loop output (same as RETRY), but the plan attached to the phase result must be the replan'd plan — not the original.
5. If REPLAN itself produces an invalid plan (`validate_plan` fails), the fallback must be a terminal STOP, not infinite loop.

#### Compatibility risks

- `run_deterministic` is **not changed**, so compat path is unaffected at the delegation level.
- However, `_parent_policy_decision_after_phase_attempt` is called **only on the non-compat path**. Its signature change (returning REPLAN) does not touch compat. Compat invariants in `hierarchical_test_locks.py` are safe **only if** REPLAN logic is gated entirely within the non-compat branch.
- Risk: `_build_two_phase_parent_plan` is in `plan_resolver.py`. If a bug in the REPLAN code path calls `get_parent_plan` instead of a phase-specific replanner, it re-enters `run_hierarchical` recursively. This is a latent stack overflow risk.

#### Observability impact

- New `parent_policy_decision.decision = "REPLAN"` events required.
- New `phase_replanned` trace event required (similar to `phase_started`) when a replanned plan is substituted.
- `attempt_history` already records per-attempt state, so REPLAN attempts are observable if `attempt_count` increments correctly.
- **No changes to compat trace events.** REPLAN events are only emitted on the non-compat path.

#### Why now / why not now

**Why not now:**  
`RETRY` with `max_parent_retries > 0` on shipped `two_phase_docs_code` plans has never been exercised in production. It is tested only with manually crafted plans in `test_two_phase_execution.py`. The hardcoded `max_parent_retries: 0` in `_build_two_phase_parent_plan` means the Stage 4 retry machinery is **inert** on all production two-phase runs. REPLAN is a harder problem than RETRY. Shipping REPLAN before RETRY is even configurable in production is skipping a step.  

REPLAN also changes **parent-policy semantics**, not metadata. It is not a metadata-only addition. It requires a structural change to `deterministic_runner.py`'s retry loop — the highest-risk file in the codebase.

---

### Candidate B — REQUEST_CLARIFICATION Parent-Policy Outcome

#### What problem it solves

When a two-phase run fails all retry budget for a phase, the current outcome is `STOP`. The caller receives `parent_goal_met: False` and `errors_encountered` with a `phase_N_goal_not_met` or `phase_N_failed:<class>` marker. There is no mechanism to signal to the upstream caller that the instruction was ambiguous or malformed in a way that requires user input. REQUEST_CLARIFICATION would be a terminal parent-policy outcome that carries a structured reason the instruction could not be completed, allowing the caller to surface a question to the user.

#### Required files to change

- **`agent/orchestrator/deterministic_runner.py`** — `_parent_policy_decision_after_phase_attempt` must return `REQUEST_CLARIFICATION` under some trigger condition. The per-phase loop must detect this and break with a clarification payload instead of a failed `phase_result`. `_build_hierarchical_loop_output` must handle the clarification terminal case.
- **`agent/orchestrator/parent_plan.py`** (or a new module) — a clarification payload schema: what fields does a clarification response carry? This is currently undefined.
- **`tests/test_two_phase_execution.py`** — new tests.
- **Caller contract at the boundary of `run_hierarchical`** — the return type `tuple[AgentState, dict]` must either (a) encode a clarification in `loop_output["clarification_requested"]` (a new key that `hierarchical_test_locks.py` must track), or (b) the caller must inspect a new field to distinguish clarification from a normal STOP failure. Either way this is a **caller-side contract change**.

#### Blast radius

**Large, and caller-contract-breaking.**  

The return type of `run_hierarchical` is documented as a drop-in replacement for `run_deterministic` (Roadmap §6.2, invariant I9). Any code calling `run_hierarchical` and inspecting `loop_output` currently assumes STOP is the worst outcome. REQUEST_CLARIFICATION is a new third terminal state that requires callers to handle a new output shape. This is a breaking change for all `run_hierarchical` callers not updated simultaneously.

Additionally, the **trigger condition** for REQUEST_CLARIFICATION is undefined. The roadmap (§1.4) states: "It is permitted only as a parent policy outcome in Stage 3 when all retry and replan budgets are exhausted." Since REPLAN (Candidate A) has not shipped, requesting clarification before retries and replans are exhausted contradicts the roadmap gate. Shipping REQUEST_CLARIFICATION before REPLAN violates the intended escalation order.

#### New invariants required

1. REQUEST_CLARIFICATION is terminal (like STOP); it must never trigger a retry.
2. `loop_output` must carry a structured clarification payload (new key). `HIERARCHICAL_LOOP_OUTPUT_KEYS` in `hierarchical_test_locks.py` must be extended.
3. `loop_output["phase_count"]` invariant (L2) must still hold: only executed phases, not the aborted clarification phase.
4. `phase_results` must still have one row per executed phase, but the last row may have a new `failure_class = "clarification_requested"`.
5. The clarification payload must never appear on the compat path (new key means `assert_compat_loop_output_has_no_hierarchical_keys` must be extended — this is a test file change in `hierarchical_test_locks.py`).

#### Compatibility risks

- **`hierarchical_test_locks.py` must change** to track the new key. This is the authoritative signal that the scope is too wide (see L6). Any Stage 7 candidate that requires changes to `hierarchical_test_locks.py` is adding new caller-visible contract surface. REQUEST_CLARIFICATION requires this.
- Caller contract change: every `run_hierarchical` consumer must be updated to handle clarification outcome.
- `run_deterministic` is not changed, so the compat delegation is unaffected.

#### Observability impact

- New `parent_policy_decision.decision = "REQUEST_CLARIFICATION"` event.
- New `clarification_requested` trace event with reason and instruction_preview.
- New `loop_output["clarification_requested"]` key visible to callers.
- No compat trace changes (same as Candidate A).

#### Why now / why not now

**Why not now:**  
REQUEST_CLARIFICATION depends on REPLAN being implemented first (roadmap gate: clarification only after all retry/replan budgets exhausted). The roadmap (§1.4) is explicit: "Any engineer who proposes adding a clarification path before Stage 3 is out of scope for this roadmap." Repo Stages 3–4 shipped `CONTINUE`/`RETRY`/`STOP` but not `REPLAN`. REQUEST_CLARIFICATION before REPLAN ships is a roadmap violation.

Additionally, this candidate changes **parent-policy semantics** (new terminal state, new `loop_output` key, caller contract change), not metadata only. It has the broadest caller-side blast radius of the three candidates.

---

### Candidate C — Configurable Retry Budgets for Shipped Two-Phase Plans

#### What problem it solves

`_build_two_phase_parent_plan` in `plan_resolver.py` (lines 350–413) hardcodes `"retry_policy": {"max_parent_retries": 0}` on both Phase 0 and Phase 1. The Stage 4 retry machinery — `_parent_policy_decision_after_phase_attempt`, the `for attempt_number in range(1, max_attempts + 1)` loop in `run_hierarchical`, `errors_encountered_merged`, `attempt_history` — is fully implemented, test-covered, and **completely inert on all production two-phase runs** because the budget is always zero.

Configurable retry budgets make the Stage 4 machinery active for `two_phase_docs_code` plans by allowing the budget to be set at plan-construction time. The execution path does not change. The only change is what value `_build_two_phase_parent_plan` puts in `retry_policy.max_parent_retries`.

#### Required files to change

- **`agent/orchestrator/plan_resolver.py`** — `_build_two_phase_parent_plan` accepts a `retry_budget: dict | None` parameter (or reads from a config constant) and uses it to populate `retry_policy.max_parent_retries` on Phase 0 and Phase 1. Reasonable default: `{0: 1, 1: 1}` (one retry per phase) or a single scalar applied to both phases.
- **A config location** — either a new constant in `config/agent_config.py` (e.g., `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE: int = 1`) or a minimal new `config/hierarchical_config.py`. This keeps the budget out of the planner's production call path and makes it independently tunable without touching execution logic.
- **`tests/test_two_phase_execution.py`** — new test class (`TestStage7RetryBudgetConfiguration`) confirming that the budget flows from the config/parameter to `phase_plan["retry_policy"]["max_parent_retries"]` on both phases, and that the existing retry execution tests still pass with the configured budget.

#### Blast radius

**Minimal.**  

- `deterministic_runner.py` is **not changed**. Zero lines of execution logic change.
- `execution_loop.py`, `replanner.py`, `step_dispatcher.py` are **not changed**.
- `hierarchical_test_locks.py` is **not changed** (no new keys; retry budget lives in the phase plan dict, not in `loop_output`).
- `tests/test_run_hierarchical_compatibility.py` is **not changed** (compat path never reads `retry_policy`).
- The change is purely in `plan_resolver.py` plan-construction time. It is the same class of change as Stage 6 (connector extension in `_derive_phase_subgoals`): additive, single-file production change, no execution semantics altered.

The only new behavior: when a two-phase plan is constructed with `max_parent_retries > 0`, the retry loop already written in `run_hierarchical` will now execute more than one attempt per phase in production (instead of only in hand-crafted tests). This is the intended activation of Stage 4 machinery.

#### New invariants required

1. `_build_two_phase_parent_plan` must validate that the supplied retry budget is a non-negative integer. Invalid input (negative, non-int) must fall back to 0.
2. `retry_policy.max_parent_retries` on the constructed phase must match the configured budget exactly.
3. The configured budget must not leak to the compat path (compat path never calls `_build_two_phase_parent_plan`; this is trivially preserved).
4. Setting the budget to 0 (the current default) must produce behavior identical to the current Stage 6 state. This is a regression safeguard: test with budget=0 and verify test counts match recorded Stage 6 counts.

These are narrow and already partially covered by existing retry tests with manually crafted plans.

#### Compatibility risks

**None on compat path.** `run_hierarchical` with `compatibility_mode=True` still delegates only to `run_deterministic`. `_build_two_phase_parent_plan` is not called on the compat path. `HIERARCHICAL_LOOP_OUTPUT_KEYS` and `_PHASE_RESULT_FIELD_NAMES` in `hierarchical_test_locks.py` are unchanged.

**Minor risk on non-compat path:** A retry budget of 1 on Phase 0 means Phase 0 can now run twice in production if it fails. This activates the `errors_encountered_merged` aggregation across two attempts, the `attempt_history` growth to two entries, and the `retries_used` counter becoming non-zero. These are all correct behaviors already tested — Stage 4 and Stage 5 tests cover multi-attempt scenarios — but they will now be observable in production traces where they were previously always absent.

**If the budget is supplied as a parameter to `_build_two_phase_parent_plan` vs a config constant:** A parameter is safer (callers control the budget, easier to test, no global state) but requires `get_parent_plan` to pass it through. A config constant is simpler but requires a config file change. Recommendation: use a config constant for the initial implementation; parameter injection can be added later if per-call variability is needed.

#### Observability impact

**No new trace events.** The existing `phase_completed`, `parent_policy_decision` (with `decision = "RETRY"`), and `attempt_count` fields already carry full retry observability. Setting `max_parent_retries > 0` causes `parent_policy_decision.decision = "RETRY"` to appear in the trace for Phase 0 or Phase 1 when they fail and a retry is scheduled — exactly what Stage 4 was designed to emit. `attempts_total` and `retries_used` in `loop_output` become non-zero. No new keys, no new trace event names.

**Positive observability change:** The `two_phase_near_miss` event from Stage 6 and the `phase_completed.parent_retry_eligible` field (currently always `False` for production two-phase runs because budget is 0) will start reflecting real retry eligibility once the budget is non-zero.

#### Why now / why not now

**Why now:**  
Stage 4 and Stage 5 built full retry execution and observability infrastructure. Stage 6 hardened plan quality. The machinery is correct and tested. The activation cost is one config constant and one parameter plumbed through `_build_two_phase_parent_plan`. The blast radius is smaller than any previous stage since Stage 3. This is the natural next step to make Phase 0 failures recoverable in production without changing any execution semantics.

**Why the other candidates are not yet appropriate:**  
REPLAN requires structural changes to `deterministic_runner.py` and new invariants before RETRY is even active in production. REQUEST_CLARIFICATION is roadmap-gated on REPLAN. Both are strictly larger scope than activating the already-built retry machinery.

---

## 4. Recommendation

**Stage 7 = Candidate C: Configurable Retry Budgets for Shipped Two-Phase Plans.**

### 4.1 Chosen Slice

Add a `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE` constant (initial value `1`) and use it in `_build_two_phase_parent_plan` to populate `retry_policy.max_parent_retries` on both Phase 0 and Phase 1. No other production file changes.

This activates the Stage 4 retry machinery for all production `two_phase_docs_code` runs without touching `deterministic_runner.py`, `execution_loop.py`, `replanner.py`, `step_dispatcher.py`, or `hierarchical_test_locks.py`.

### 4.2 Why the Other Two Are Deferred

**REPLAN is deferred because:**
- It requires a structural change to the per-phase attempt loop in `deterministic_runner.py` — the highest-risk file.
- It introduces new invariants (REPLAN attempt accounting, `attempt_history` across a plan substitution, `errors_encountered_merged` pre-replan) that are currently unspecified.
- RETRY with non-zero budget has never run in production. It is wrong to implement plan-level replanning before budget-controlled same-plan retrying is activated and validated in production.
- Deferred to Stage 8, gated on Stage 7 production observation.

**REQUEST_CLARIFICATION is deferred because:**
- Roadmap gate: "permitted only as a parent policy outcome in Stage 3 when all retry and replan budgets are exhausted" (Roadmap §1.4). REPLAN is not yet shipped. Shipping REQUEST_CLARIFICATION before REPLAN contradicts the roadmap gate.
- Requires `hierarchical_test_locks.py` changes (new `loop_output` key). That is the definitive signal of too-wide scope.
- Changes caller contract for all `run_hierarchical` consumers.
- Deferred to Stage 9+, gated on REPLAN being implemented and validated.

### 4.3 Smallest Viable Implementation Scope

Two changes only:

1. **Config constant:** add `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE: int = 1` to `config/agent_config.py` (or a new `config/hierarchical_config.py`).

2. **`_build_two_phase_parent_plan` in `plan_resolver.py`:** read the constant and write it to `retry_policy.max_parent_retries` on both phase dicts instead of hardcoding `0`. Add a guard: `max(0, int(budget))` to reject negative/invalid values.

### 4.4 Likely Files to Touch

| File | Change type |
|---|---|
| `agent/orchestrator/plan_resolver.py` | Extend `_build_two_phase_parent_plan` to read budget constant |
| `config/agent_config.py` (or new `config/hierarchical_config.py`) | Add `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE` constant |
| `tests/test_two_phase_execution.py` | New test class `TestStage7RetryBudgetConfiguration` |

### 4.5 Likely Tests to Add First (Test-First)

Write all tests in `TestStage7RetryBudgetConfiguration` **before** changing `plan_resolver.py`:

| Test name | What it asserts |
|---|---|
| `test_build_two_phase_plan_default_budget_is_nonzero` | After Stage 7, `_build_two_phase_parent_plan(...)` produces phases where `retry_policy["max_parent_retries"] == TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE` (expected: 1) |
| `test_build_two_phase_plan_budget_applied_to_phase_0` | Phase 0 `retry_policy["max_parent_retries"]` equals configured constant |
| `test_build_two_phase_plan_budget_applied_to_phase_1` | Phase 1 `retry_policy["max_parent_retries"]` equals configured constant |
| `test_build_two_phase_plan_zero_budget_unchanged_from_stage6` | When constant is 0, `retry_policy["max_parent_retries"] == 0` (backward compat) |
| `test_run_hierarchical_two_phase_retries_phase0_on_failure` | Mocked execution: Phase 0 fails attempt 1, succeeds attempt 2; `phase_result["attempt_count"] == 2`; `retries_used == 1` |
| `test_run_hierarchical_two_phase_retry_attempt_history_length` | After Phase 0 retry: `len(phase_result["attempt_history"]) == 2` and `attempt_history[-1]["success"] is True` |
| `test_run_hierarchical_two_phase_retry_errors_accumulated` | `errors_encountered_merged` contains errors from both attempts after retry |
| `test_compat_path_unaffected_by_budget_config` | `run_hierarchical` with compat-mode instruction still returns output with no hierarchical keys (uses `assert_compat_loop_output_has_no_hierarchical_keys`) |

All existing 173 tests must pass before any production code is written.

---

## 5. Rollback Plan

Stage 7 is a single-file production change (`plan_resolver.py`) plus a config constant. Rollback:

1. Revert `config/agent_config.py` (or `config/hierarchical_config.py`) constant deletion — remove `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE`.
2. Revert `_build_two_phase_parent_plan` in `plan_resolver.py` to hardcode `"max_parent_retries": 0` on both phase retry policies.
3. Remove `TestStage7RetryBudgetConfiguration` from `tests/test_two_phase_execution.py` (or mark all new tests `xfail`).
4. Run proof command: `python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q` — must return 173 passed (the Stage 6 count).

No changes to `deterministic_runner.py`, `hierarchical_test_locks.py`, `execution_loop.py`, `replanner.py`, or `step_dispatcher.py` are made in Stage 7, so there is nothing to roll back in those files.

---

## 6. Do Not Do Yet

The following are explicitly out of scope for Stage 7 and must not be included in the same PR:

| Non-goal | Why deferred |
|---|---|
| **≥ 3 phase decomposition** | `len(phases) != 2` guard is locked (L5); requires explicit re-approval; Roadmap Stage 4 gate |
| **Retrieval consumption of `prior_phase_ranked_context`** | Requires changes to `execution_loop.py` or `step_dispatcher.py` — frozen files; no Stage 7 justification |
| **Widening `_is_two_phase_docs_code_intent` code markers aggressively** | Detection precision is more important than recall; false positives route single-intent instructions through a wasted Phase 0; Stage 6 audit confirmed no widening needed |
| **Compat-path metadata additions (new keys in `HIERARCHICAL_LOOP_OUTPUT_KEYS`)** | Any PR that touches `hierarchical_test_locks.py` to add new keys is a signal that scope has exceeded the smallest viable slice |
| **REPLAN parent-policy outcome** | Requires `deterministic_runner.py` structural change; deferred to Stage 8, gated on Stage 7 production observation |
| **REQUEST_CLARIFICATION parent-policy outcome** | Roadmap-gated on REPLAN; deferred to Stage 9+ |
| **Per-phase independent retry budgets** | Phase 0 and Phase 1 can have different budgets (e.g., Phase 0 gets 2 retries, Phase 1 gets 1). Not needed in Stage 7; a single constant applied to both phases is sufficient. Add per-phase configurability only if production data shows phases have different retry needs |
| **Changes to `parent_plan.py` TypedDicts or schema fields** | Schema unchanged; no new fields required for retry budget activation |
| **Budget injection via `get_parent_plan` parameters** | `get_parent_plan` signature is stable; do not add `retry_budget` as a call-site parameter in Stage 7; config constant is sufficient |

---

*End of Stage 7 decision memo.*
