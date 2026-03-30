# Stage 9 — Architecture Decision Memo

**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` line  
**Precondition:** Stage 8 closed — per-phase independent retry budgets ship (`TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0`, `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1`); proof baseline **184** tests on hierarchical slice (`test_parent_plan_schema` + `test_run_hierarchical_compatibility` + `test_two_phase_execution`).  
**Status:** Decision memo only. No code change implied by this document.

---

## 1. Repo Stage Map (Stages 1–8 Complete)

| Repo stage | Status | What shipped | Primary touchpoints |
|------------|--------|--------------|---------------------|
| **Stage 1** | Complete | `parent_plan.py`; `get_parent_plan`; `run_hierarchical` compat → `run_deterministic` | `parent_plan.py`, `plan_resolver.py`, `deterministic_runner.py` |
| **Stage 2** | Complete | `_is_two_phase_docs_code_intent`; `_build_two_phase_parent_plan`; phase loop; handoff; `GoalEvaluator.evaluate_with_reason(phase_subgoal=...)` | `plan_resolver.py`, `deterministic_runner.py`, `goal_evaluator.py` |
| **Stage 3** | Complete | Phase validation enforcement; trace/reporting for parent retry | `deterministic_runner.py` |
| **Stage 4** | Complete | Real parent retry (`CONTINUE` / `RETRY` / `STOP`); `errors_encountered_merged` | `deterministic_runner.py` |
| **Stage 5** | Complete | `attempt_history`; `attempts_total` / `retries_used`; lock extension | `deterministic_runner.py`, `hierarchical_test_locks.py` |
| **Stage 6** | Complete | `_derive_phase_subgoals` connectors (5→13); `two_phase_near_miss` trace | `plan_resolver.py` |
| **Stage 7** | Complete | Config `_coerce_max_parent_retries`; single `_budget` for both phases in `_build_two_phase_parent_plan` (`TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE = 1`) | `config/agent_config.py`, `plan_resolver.py` |
| **Stage 8** | Complete | Split config into `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0 = 1` and `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1 = 1`; `_budget_phase_0` / `_budget_phase_1` in `_build_two_phase_parent_plan`; asymmetric-budget invariant tests | `config/agent_config.py`, `plan_resolver.py`, `tests/test_two_phase_execution.py` |

### 1.1 Roadmap vs Repo Warning (required)

**`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2 "Stage 3 — Parent Policy and Escalation"** lists the full parent policy (`CONTINUE` / `RETRY` / `REPLAN` / `REQUEST_CLARIFICATION` / `STOP`). **Repo** Stages 3–4 delivered `CONTINUE` / `RETRY` / `STOP` only. **`REPLAN` and `REQUEST_CLARIFICATION` are still not implemented** anywhere in `deterministic_runner.py` — `_parent_policy_decision_after_phase_attempt` returns only `CONTINUE`, `RETRY`, or `STOP`.

Roadmap **§95–101 "Stage 4 — Broader Decomposition Patterns"** (3+ phases) remains a **separate re-approval gate** — unrelated to repo stage numbers.

Do **not** use roadmap stage numbers as repo stage labels in review.

---

## 2. Locked Invariants (Stage 9 Must Preserve)

Any Stage 9 slice that violates one of these must be rejected unless explicitly re-approved as a broader architecture change.

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **L1** | Compat: `run_hierarchical(..., compatibility_mode=True)` returns **exactly** `run_deterministic`'s `(state, loop_output)` with **no** hierarchical-only top-level keys | `assert_compat_loop_output_has_no_hierarchical_keys` in `tests/hierarchical_test_locks.py` |
| **L2** | `loop_output["phase_count"]` == `len(phase_results)` == **executed** phases only | `_build_hierarchical_loop_output` in `deterministic_runner.py` |
| **L3** | One **final** `phase_result` row per phase; `attempt_count` = attempts for that phase; `attempt_history[-1]` matches final fields | Stage 4/5 tests, `TestStage7CloseoutInvariants` |
| **L4** | Handoff built only from **final successful** phase result (after retries) | `_build_phase_context_handoff` |
| **L5** | `len(phases) != 2` on non-compat path → `NotImplementedError` unless explicitly re-approved | `run_hierarchical` in `deterministic_runner.py` |
| **L6** | `HIERARCHICAL_LOOP_OUTPUT_KEYS` / `_PHASE_RESULT_FIELD_NAMES` are the contract; **new compat-visible top-level keys** = scope smell | `hierarchical_test_locks.py` |

---

## 3. Post-Stage-8 Facts

- **`TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0 = 1`** and **`TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1 = 1`** are both live in `config/agent_config.py`. Default behavior: both phases allow **one** parent-level retry (2 attempts each), independently tunable.
- **`_build_two_phase_parent_plan`** reads `_budget_phase_0` and `_budget_phase_1` separately, each coerced via `_coerce_max_parent_retries`. Stage 7's single-scalar `_budget` is gone.
- **`deterministic_runner.py` was not modified in Stage 8.** `_parent_policy_decision_after_phase_attempt` still returns only `CONTINUE`, `RETRY`, or `STOP`. Per-phase retry execution uses `_get_max_parent_retries(phase_plan)` — already per-phase at read time.
- **`hierarchical_test_locks.py` was not modified in Stage 8.** `HIERARCHICAL_LOOP_OUTPUT_KEYS` and `_PHASE_RESULT_FIELD_NAMES` are unchanged since Stage 5.
- **`prior_phase_ranked_context` / `prior_phase_retrieved_symbols` / `prior_phase_files`** are injected per roadmap §5.5; roadmap §5.5 states `execution_loop`, `step_dispatcher`, and `replanner` **ignore** these keys today — retrieval merge is explicitly future work.
- **Production retry traces for `two_phase_docs_code` plans with non-zero budgets did not exist before Stage 7 merged.** Stage 7 and Stage 8 were both specified on the same calendar day (2026-03-20). No production retry failure analysis has been performed against live `RETRY` outcomes.
- **Stage 7 "per-phase independent retry budgets" deferral is now closed.** Stage 8 is the delivered item. The next open deferral items in the stage memos are: REPLAN (deferred from Stage 7 and Stage 8), REQUEST_CLARIFICATION (deferred from Stage 7 and Stage 8, roadmap-gated on REPLAN), and retrieval context merge (deferred from Stage 7 and Stage 8).

---

## 4. Candidate Evaluation

### Candidate A — REPLAN Parent-Policy Outcome

#### What problem it solves

`RETRY` re-runs the **same** `PhasePlan["steps"]` with a fresh `AgentState`. Failures caused by a **wrong or underspecified plan** — e.g., Phase 0 used a seed query that returned empty context because the instruction mapped to a niche docs artifact not matched by `"readme docs"` or `"architecture docs"` — will not be resolved by repeating identical steps. **REPLAN** would call a phase-rebuilding helper (e.g., `_build_replan_phase(phase_plan, failure_context)`) before spending another parent attempt, substituting a new plan into the loop.

#### Exact files that would change

- **`agent/orchestrator/deterministic_runner.py`** — **mandatory and high-risk**: `_parent_policy_decision_after_phase_attempt` must gain a `REPLAN` branch. The inner `for attempt_number in range(1, max_attempts + 1)` block (lines 734–892) must handle `REPLAN` by replacing `phase_plan` mid-loop and restarting the attempt with a new plan. This is a **structural change** to the per-phase attempt block, not a metadata tweak. Every retry invariant (L2, L3, L4) must be audited post-change.
- **`agent/orchestrator/plan_resolver.py`** — new `_build_replan_phase(phase_plan, failure_context)` or an extension to `_build_two_phase_parent_plan` that accepts per-phase `retry_context`. Must not re-enter `run_hierarchical` recursively.
- **`tests/test_two_phase_execution.py`** — new test class covering REPLAN semantics: `attempt_history` across plan substitution, `errors_encountered_merged` accumulation, `phase_result["loop_output"]` identity, terminal STOP on invalid replan.

**Explicit:** This candidate **cannot** be completed in `plan_resolver.py` + config alone. `deterministic_runner.py` changes are mandatory.

#### Blast radius

**High.** Same surface area as Stages 4/5 (retry loop). `deterministic_runner.py` is the highest-risk file. Every existing retry invariant test must be re-audited after modifying the per-phase attempt block.

#### New invariants required

None of these are currently answered by any existing invariant test or closeout document:

- Does REPLAN consume the same `max_parent_retries` budget unit as RETRY, or a separate `max_parent_replans` counter?
- Does `attempt_history` grow across a plan-substitution boundary? (Current: one row per `execution_loop` invocation — REPLAN would be a new invocation with a different `phase_plan`. Must the history row record the original plan's identity, the replan'd plan's identity, or both?)
- Does `errors_encountered_merged` accumulate across REPLAN attempts? (It must, consistent with Stage 4 semantics — but this must be explicitly locked.)
- What is `phase_result["loop_output"]` when REPLAN occurs before success: the final attempt's output (consistent with today) or a list? Must be locked.
- If `_build_replan_phase` itself raises or produces an invalid plan, the fallback must be terminal `STOP`, not an infinite loop. Recursion guard required.
- REPLAN decision must never be emitted on the compat path. Gate must be explicit.

#### Compatibility risks

Compat path: **low** if REPLAN logic is entirely inside `if not parent_plan["compatibility_mode"]`. Risk: if `_build_replan_phase` calls `get_parent_plan` instead of a phase-scoped helper, it re-enters `run_hierarchical` recursively. This is a **latent stack overflow** risk requiring an explicit guard.

#### Observability impact

New `parent_policy_decision.decision = "REPLAN"` events; new `phase_replanned` trace event when a plan is substituted. `attempt_history` already records per-attempt state — REPLAN attempts are observable if `attempt_count` increments correctly. No compat trace changes.

#### Why now / why not now

**Why not now:** Stages 7 and 8 were both specified and closed on the same calendar day. Production retry traces for non-zero `max_parent_retries` do not exist yet. The case for REPLAN is entirely speculative — there is no observed failure class in production where same-plan `RETRY` demonstrably fails to converge. Implementing retry-loop surgery on `deterministic_runner.py` without evidence of a real plan-quality failure pattern is premature and carries disproportionate risk. Additionally, the design questions above are **not pre-answered** — they require a dedicated pre-coding decisions document before any code is written (per the precedent of `HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`).

**When to revisit:** After production traces show that Phase 0 `parent_policy_decision.decision = "RETRY"` fires with the **same `failure_class`** across multiple consecutive attempts (indicating the plan is the problem, not the execution), for a measurable fraction of two-phase runs.

---

### Candidate B — REQUEST_CLARIFICATION Parent-Policy Outcome

#### What problem it solves

Terminal `STOP` after exhausted retries gives `errors_encountered` and `parent_goal_met: False` but no first-class "ask the user" channel. Product may want a structured clarification request instead of a silent failure — e.g., "Phase 0 failed: no docs artifacts found; the instruction may need to specify the docs location."

#### Exact files that would change

- **`agent/orchestrator/deterministic_runner.py`** — `_parent_policy_decision_after_phase_attempt` must return `REQUEST_CLARIFICATION` under a trigger condition (currently undefined). The per-phase loop must detect this and break with a clarification payload.
- **`agent/orchestrator/parent_plan.py`** (or new module) — clarification payload schema: fields are currently undefined.
- **`tests/test_two_phase_execution.py`** — new tests.
- **`tests/hierarchical_test_locks.py`** — **mandatory** if a new top-level key (e.g. `clarification_requested`) is added to `loop_output`; this is a **contract expansion** (L6 warning).
- **All callers of `run_hierarchical`** — must distinguish clarification from normal `STOP` failure; caller contract breaks for any consumer not updated simultaneously.

#### Blast radius

**Very high** — caller-contract-breaking. Any new top-level `loop_output` key visible to hierarchical callers requires `hierarchical_test_locks.py` modification (L6 warning from Stage 7 memo). The trigger condition is currently undefined.

#### New invariants required

- REQUEST_CLARIFICATION is terminal; never triggers retry.
- `loop_output` must carry a structured clarification payload (new key). `HIERARCHICAL_LOOP_OUTPUT_KEYS` in `hierarchical_test_locks.py` must be extended.
- `loop_output["phase_count"]` invariant (L2) must still hold.
- The clarification payload must never appear on the compat path; `assert_compat_loop_output_has_no_hierarchical_keys` must be extended.

#### Compatibility risks

**`hierarchical_test_locks.py` must change** — this is the L6 signal that scope is too wide. Caller contract change for all `run_hierarchical` consumers. `run_deterministic` unchanged.

#### Observability impact

New `parent_policy_decision.decision = "REQUEST_CLARIFICATION"` event; new `clarification_requested` trace event; new `loop_output["clarification_requested"]` key visible to callers.

#### Why now / why not now

**Why not now:** Roadmap §1.4 is explicit — clarification is permitted only after all retry **and** replan budgets are exhausted. REPLAN has not shipped. Shipping REQUEST_CLARIFICATION before REPLAN violates roadmap ordering. Additionally, this candidate requires `hierarchical_test_locks.py` changes, which is the definitive signal of too-wide scope for a maintenance slice. The trigger condition is undefined.

---

### Candidate C — Retrieval Use of `prior_phase_ranked_context` in Phase 1

#### What problem it solves

Phase 1 `execution_loop` builds retrieval from Phase 1 state only. `prior_phase_ranked_context`, `prior_phase_retrieved_symbols`, and `prior_phase_files` are injected into Phase 1's `AgentState.context` via `_build_phase_agent_state` (lines 279–281 in `deterministic_runner.py`) but are **not merged** into ranking or candidate selection by `execution_loop` or `step_dispatcher`. Two-phase runs may under-use Phase 0 docs signal in Phase 1 retrieval, defeating part of the purpose of the two-phase decomposition.

#### Exact files that would change

- **`agent/orchestrator/execution_loop.py`** and/or **`agent/execution/step_dispatcher.py`** and possibly **`agent/orchestrator/replanner.py`** — must read and merge prior-phase context into retrieval or ranking. These are **frozen** for orchestration work per `HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`.
- **`config/agent_config.py`** — caps (e.g., `MAX_CONTEXT_CHARS`) already cited in roadmap §5.5 pruning.

**Explicit:** This is **not** a `plan_resolver`-only change and it **requires touching frozen execution modules**.

#### Blast radius

**High** — affects every phase-local `execution_loop` invocation that sees handoff keys. Risk of double-counting context, lane confusion, or non-deterministic ranking if merge rules are underspecified. The merge design (what gets merged, at what pipeline stage, with what dedup and cap logic) does not yet exist as a written spec.

#### New invariants required

Deterministic merge order; hard cap on injected prior context; no behavior change for compat/single-phase runs; Phase 1 `ranked_context` contract remains valid for `PhaseValidationContract`; no regression to existing vector search or context ranking invariants.

#### Compatibility risks

Low for compat path (no handoff injected). High for the test matrix of two-phase + retrieval — requires a written merge spec before any implementation.

#### Observability impact

Likely new trace fields (`prior_context_used`, merge count) for debugging.

#### Why now / why not now

**Why not now:** Frozen execution path; requires explicit written approval to modify `execution_loop.py`, `step_dispatcher.py`, and/or `replanner.py`. No merge spec exists. Highest blast radius of any candidate. Must not be implemented ad-hoc alongside any other slice.

---

### Candidate D — No-Op / Hold-and-Measure (Based on Stage 7–8 Retry Traces)

#### What problem it solves

Stages 7 and 8 activated non-zero per-phase retry budgets in production. Both shipped with defaults of 1 for both phases. The production retry machinery (`RETRY` decisions, `attempt_history`, `errors_encountered_merged`, `retries_used`) has never been observed under real load. Before implementing REPLAN — which requires structural changes to the highest-risk file in the codebase — the correct engineering step is to **establish a measurement baseline**: define observable criteria for when REPLAN is justified, collect production trace data, and make the REPLAN decision with evidence rather than speculation.

This candidate does not mean "do nothing." It means:

1. **Define** the specific trace-observable signal that justifies REPLAN (e.g., Phase 0 `parent_policy_decision.decision = "RETRY"` fires with the **same `failure_class`** on consecutive attempts for a threshold fraction of two-phase runs → plan is the problem, not execution).
2. **Optionally extend** existing `parent_policy_decision` and `phase_completed` payloads to carry failure-class pattern data that makes this signal detectable without touching `deterministic_runner.py`'s execution logic (trace-only change, if needed).
3. **Write the REPLAN pre-coding decisions document** (invariants, budget accounting, recursion guard, `attempt_history` semantics across plan substitution) as documentation, not code — so implementation can start immediately when measurement justifies it.
4. **Establish and record the Stage 8 proof command baseline** (exact test count) so Stage 9 closeout has a clean reference.

#### Exact files that would change

**None** for production code or execution logic. Optional: minimal additions to trace event payloads in `deterministic_runner.py` (additive-only, no execution semantic changes) if measurement gaps are identified. Any such additions must not touch `hierarchical_test_locks.py`.

#### Blast radius

**Zero** for production execution behavior. Any optional observability additions are additive-only.

#### New invariants required

None. Existing invariants (L1–L6) are unchanged.

#### Compatibility risks

None.

#### Observability impact

**Positive**: establishes a named measurement criteria and trace signal. Any optional payload extensions to existing events (`parent_policy_decision`, `phase_completed`) carry no new event names and require no `hierarchical_test_locks.py` changes.

#### Why now / why not now

**Why now:** This is the only candidate that advances the architecture **without** requiring `deterministic_runner.py` retry-loop surgery before evidence exists. It closes the gap between "retry machinery exists and is now active" and "retry machinery has been observed and proven sufficient or insufficient." REPLAN built on this foundation will be a better-specified, lower-risk implementation.

**Why not as a permanent hold:** This is explicitly a **one-stage gate**, not indefinite deferral. The hold expires when the measurement criteria defined in Stage 9 are satisfied. The pre-coding decisions document written in Stage 9 means Stage 10 (REPLAN) can start immediately once evidence arrives.

---

## 5. Decision Standards

| Constraint | Implication for Stage 9 |
|------------|------------------------|
| Smallest blast radius | **D** has zero; **A** and **C** are high; **B** is very high |
| Preserve compat invariants **L1**, **L6** | **B** is incompatible without `hierarchical_test_locks.py` change; **A** safe if gated |
| Avoid `run_deterministic` change | All four satisfy this |
| Structural edits to retry loop in `deterministic_runner.py` | **A** requires this — **high risk**; **D** explicitly avoids it |
| `hierarchical_test_locks.py` change | **Major scope warning** — **B** requires it; **A**, **C**, **D** do not |
| Frozen retrieval/execution modules | **C** requires touching `execution_loop.py` and/or `step_dispatcher.py` — frozen; **A**, **B**, **D** do not |
| No 3+ phases | Out of scope for all candidates |
| Evidence-based REPLAN | **D** directly enables it; **A** bypasses evidence gate |

---

## 6. Recommendation

### 6.1 Chosen next slice — **Candidate D: Hold-and-Measure**

**Rationale (blunt):**

Stage 7 activated non-zero retry budgets on 2026-03-20. Stage 8 split them per-phase on the same calendar day. **There are no production retry traces to analyze.** REPLAN is designed to address plan-quality failures, but there is no evidence yet that same-plan `RETRY` fails to converge for any observed class of `two_phase_docs_code` runs. Implementing retry-loop surgery on `deterministic_runner.py` — the highest-risk file — without that evidence is the wrong engineering call.

The Stage 7 decision memo stated: *"When to revisit [REPLAN]: after evidence that same-plan retries hit diminishing returns for a measurable class of failures."* That evidence does not yet exist. Stage 8 changed nothing about this precondition; it extended budget configurability, which is a prerequisite for **measuring** whether asymmetric budgets affect failure patterns — not a justification for bypassing the measurement gate.

**Stage 9 = hold-and-measure is not a stall. It is a defined engineering deliverable** with three concrete outputs:

1. **Measurement criteria document** (`Docs/REPLAN_MEASUREMENT_CRITERIA.md` or similar): specifies the exact trace-observable signal that justifies REPLAN implementation (failure class pattern across consecutive same-plan retries, threshold fraction of two-phase runs, observation window).
2. **REPLAN pre-coding decisions document** (`Docs/REPLAN_PRECODING_DECISIONS.md` or similar): answers all unanswered design questions from Stage 8 §4 Candidate A (budget accounting, `attempt_history` across plan substitution, recursion guard, terminal fallback on invalid replan). This document is written now so Stage 10 can begin immediately once measurement criteria are met.
3. **Stage 8 proof command baseline recorded** for Stage 9 closeout reference.

### 6.2 Why the others are deferred

| Candidate | Deferral reason |
|-----------|-----------------|
| **A (REPLAN)** | Requires `deterministic_runner.py` retry-loop surgery; design questions unanswered; no production evidence that same-plan RETRY fails to converge. Ship **D**, define criteria, then **A**. |
| **B (REQUEST_CLARIFICATION)** | Roadmap-ordered after REPLAN; `hierarchical_test_locks.py` change required (**L6**); trigger condition undefined; caller contract breaking. |
| **C (retrieval merge)** | Frozen execution modules (`execution_loop.py`, `step_dispatcher.py`, `replanner.py`); no merge spec; highest blast radius. |

### 6.3 Smallest viable implementation scope (Stage 9)

1. **Measurement criteria document** — defines the trace signal (specific `failure_class` values, frequency threshold, observation window) that would trigger Stage 10 REPLAN approval. References existing `parent_policy_decision` and `phase_completed` event payloads.
2. **REPLAN pre-coding decisions document** — locks all invariant answers from Stage 8 §4.A before any code is written: budget accounting (shared vs separate counter), `attempt_history` entry format across plan substitution, recursion guard contract, terminal STOP fallback on invalid replan, `phase_result["loop_output"]` identity rule. This is documentation only.
3. **Optional trace payload extensions** — if the measurement criteria document identifies gaps in current `parent_policy_decision` payloads (e.g., missing failure-class trend data), extend those payloads additively in `deterministic_runner.py`. Scope rule: additive only, no execution semantic changes, no new event names, `hierarchical_test_locks.py` unchanged.
4. **Proof command baseline** — record the Stage 8 test count against the current checkout as the Stage 9 reference.

### 6.4 Files that may be touched (Stage 9)

| File | Role | Type of change |
|------|------|----------------|
| `Docs/REPLAN_MEASUREMENT_CRITERIA.md` (new) | Defines observable REPLAN justification signal | Documentation only |
| `Docs/REPLAN_PRECODING_DECISIONS.md` (new) | Answers all REPLAN design questions pre-code | Documentation only |
| `agent/orchestrator/deterministic_runner.py` | Optional: additive trace payload extension only | If and only if measurement gap requires it; no execution logic change |

**Explicit:** `tests/hierarchical_test_locks.py`, `execution_loop.py`, `step_dispatcher.py`, `replanner.py`, `parent_plan.py` — **none of these may be changed in Stage 9.**

### 6.5 Hold expiry conditions (Stage 9 → Stage 10 gate)

Stage 9 hold expires when **any one** of the following is satisfied:

1. Production traces show Phase 0 `parent_policy_decision.decision = "RETRY"` with **identical `failure_class`** on ≥2 consecutive attempts across ≥5% of observed two-phase runs over a ≥7-day window — indicating plan-level failure not addressable by RETRY alone.
2. The measurement criteria document identifies a structural gap in the current trace data that prevents observing criterion (1), and a remediation has been deployed for ≥7 days.
3. An explicit architecture re-approval overrides the measurement gate with documented justification.

When the hold expires, **Stage 10 = REPLAN** proceeds directly from the pre-coding decisions document written in Stage 9, with no additional planning phase required.

### 6.6 Rollback (Candidate D)

Candidate D has no production code changes. Rollback is not applicable. If optional trace payload extensions were made, revert those additive additions; the execution path is unaffected.

---

## 7. Do Not Do Yet (Stage 9 Scope Guards)

| Item | Reason |
|------|--------|
| **REPLAN implementation** | `deterministic_runner.py` retry-loop surgery without production evidence; unanswered design questions |
| **REQUEST_CLARIFICATION** | Roadmap-ordered after REPLAN; `hierarchical_test_locks.py` L6 violation; trigger undefined |
| **Retrieval merge (`prior_phase_ranked_context`)** | Frozen execution modules; no written merge spec |
| **≥ 3 phases** | Roadmap gate; `NotImplementedError` guard (L5) |
| **Widen `_is_two_phase_docs_code_intent`** | No Stage 7/8 audit evidence; `two_phase_near_miss` traces must accumulate first |
| **New top-level `loop_output` keys** | Contract expansion without caller + L6 update |
| **Change `hierarchical_test_locks.py`** | Contract-stability signal; no Stage 9 candidate justifies it |
| **Touch `execution_loop.py`, `step_dispatcher.py`, or `replanner.py`** | Frozen for orchestration work; no signed-off spec |

---

## 8. Relation to Prior Memos

- **`STAGE7_DECISION_MEMO.md`** deferred REPLAN to "Stage 8, gated on Stage 7 production observation." Stage 7 production observation has not occurred (same-day specification). Stage 9 formalizes that gate as a measurement deliverable.
- **`STAGE7_DECISION_MEMO.md`** deferred "per-phase independent retry budgets" to post-Stage-7 work — Stage 8 delivered that item. Stage 8's deferral list (REPLAN, REQUEST_CLARIFICATION, retrieval merge) carries forward unchanged into Stage 9.
- **`STAGE8_DECISION_MEMO.md`** stated the REPLAN revisit condition: "After evidence that same-plan retries hit diminishing returns for a measurable class of failures." Stage 9 operationalizes that condition into concrete, trace-observable criteria.
- **`STAGE6_IMPLEMENTATION_PLAN.md` §9** listed REPLAN / REQUEST_CLARIFICATION as non-goals; **Stage 7** shipped activation (retry budget), not policy expansion — this precedent holds. The correct pattern is: activate → measure → policy-expand, not: activate → policy-expand immediately.
- **`HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §5.5** documents retrieval context merge (Candidate C) as future retrieval improvement — consistent with Candidate C deferral until execution-layer approval and a written merge spec exist.
- **`HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`** established the pattern of writing invariant decisions **before** code for high-risk modules. The REPLAN pre-coding decisions document (Stage 9 deliverable) follows this precedent and enables Stage 10 to skip the planning phase entirely.

---

*End of Stage 9 decision memo.*
