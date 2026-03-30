# Stage 8 — Architecture Decision Memo

**Type:** Principal Engineer Decision Memo  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1` line  
**Precondition:** Stage 7 closed — `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE` ships default parent retry budget for `two_phase_docs_code`; proof **184** tests on hierarchical slice (`test_parent_plan_schema` + `test_run_hierarchical_compatibility` + `test_two_phase_execution`).  
**Status:** Decision memo only. No code change implied by this document.

---

## 1. Repo Stage Map (Stages 1–7 Complete)

| Repo stage | Status | What shipped | Primary touchpoints |
|------------|--------|--------------|---------------------|
| **Stage 1** | Complete | `parent_plan.py`; `get_parent_plan`; `run_hierarchical` compat → `run_deterministic` | `parent_plan.py`, `plan_resolver.py`, `deterministic_runner.py` |
| **Stage 2** | Complete | `_is_two_phase_docs_code_intent`; `_build_two_phase_parent_plan`; phase loop; handoff; `GoalEvaluator.evaluate_with_reason(phase_subgoal=...)` | `plan_resolver.py`, `deterministic_runner.py`, `goal_evaluator.py` |
| **Stage 3** | Complete | Phase validation; trace/reporting for parent retry | `deterministic_runner.py` |
| **Stage 4** | Complete | Real parent retry (`CONTINUE` / `RETRY` / `STOP`); `errors_encountered_merged` | `deterministic_runner.py` |
| **Stage 5** | Complete | `attempt_history`; `attempts_total` / `retries_used`; lock extension | `deterministic_runner.py`, `hierarchical_test_locks.py` |
| **Stage 6** | Complete | `_derive_phase_subgoals` connectors; `two_phase_near_miss` trace | `plan_resolver.py` |
| **Stage 7** | Complete | Config `_coerce_max_parent_retries`; single `_budget` for both phases in `_build_two_phase_parent_plan` | `config/agent_config.py`, `plan_resolver.py` |

### 1.1 Roadmap vs Repo (required)

**`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2 “Stage 3 — Parent Policy and Escalation”** lists the **full** parent policy (`CONTINUE` / `RETRY` / `REPLAN` / `REQUEST_CLARIFICATION` / `STOP`) and clarification as structured outcome. **Repo** Stages 3–4 delivered `CONTINUE` / `RETRY` / `STOP` only. **`REPLAN` and `REQUEST_CLARIFICATION` are still not implemented** anywhere in `deterministic_runner.py` (`_parent_policy_decision_after_phase_attempt` returns only `CONTINUE`, `RETRY`, or `STOP`).

Roadmap **§95–101 “Stage 4 — Broader Decomposition Patterns”** (3+ phases) remains a **separate re-approval gate** — unrelated to repo Stage numbers.

Do **not** use roadmap stage numbers as repo stage labels in review.

---

## 2. Locked Invariants (Stage 8 Must Preserve)

Any Stage 8 slice that violates one of these should be rejected unless explicitly re-approved as a broader architecture change.

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **L1** | Compat: `run_hierarchical(..., compatibility_mode=True)` returns **exactly** `run_deterministic`’s `(state, loop_output)` with **no** hierarchical-only top-level keys | `assert_compat_loop_output_has_no_hierarchical_keys` in `tests/hierarchical_test_locks.py` |
| **L2** | `loop_output["phase_count"]` == `len(phase_results)` == **executed** phases only | `_build_hierarchical_loop_output` in `deterministic_runner.py` |
| **L3** | One **final** `phase_result` row per phase; `attempt_count` = attempts for that phase; `attempt_history[-1]` matches final fields | Stage 4/5 tests, `TestStage7CloseoutInvariants` |
| **L4** | Handoff built only from **final successful** phase result (after retries) | `_build_phase_context_handoff` |
| **L5** | `len(phases) != 2` on non-compat path → `NotImplementedError` unless explicitly re-approved | `run_hierarchical` in `deterministic_runner.py` |
| **L6** | `HIERARCHICAL_LOOP_OUTPUT_KEYS` / `_PHASE_RESULT_FIELD_NAMES` are the contract; **new compat-visible top-level keys** = scope smell | `hierarchical_test_locks.py` |

---

## 3. State After Stage 7 (Facts)

- **`TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE`** (default **1**) is read in `_build_two_phase_parent_plan` as `_budget = _coerce_max_parent_retries(...)`.
- **The same `_budget` is written to Phase 0 and Phase 1** `retry_policy.max_parent_retries` (`plan_resolver.py`).
- **`deterministic_runner.py` was not modified in Stage 7.** Per-phase retry execution uses `_get_max_parent_retries(phase_plan)` — already **per-phase** at read time; Stage 7 only made the **constructed** plans carry a non-zero default.
- **Stage 6 implementation plan §337** explicitly noted hardcoded `0` and called “configurable retry budget per decomposition type” a **future** concern — Stage 7 activated a **single** scalar; **splitting budgets by phase** was listed as optional follow-up in `STAGE7_DECISION_MEMO.md` §6 (“Per-phase independent retry budgets”).
- **`prior_phase_ranked_context` / `prior_phase_retrieved_symbols` / `prior_phase_files`** are injected per **roadmap §5.5**; **roadmap §5.5** states `execution_loop`, `step_dispatcher`, and `replanner` **ignore** these keys today — retrieval merge is explicitly **future** work.

---

## 4. Candidate Evaluation

### Candidate A — REPLAN parent-policy outcome

#### What problem it solves

`RETRY` re-runs the **same** `PhasePlan["steps"]` with a fresh `AgentState`. Failures caused by a **bad plan** (wrong seed query, wrong phase-1 steps for the subgoal) will not be fixed by repeating the same steps. **REPLAN** would rebuild one phase’s plan (e.g. `plan(phase_1_subgoal, retry_context=...)`, or a new `_docs_seed_plan`) before spending another parent attempt.

#### Required files to change

- **`agent/orchestrator/deterministic_runner.py`** — **mandatory**: extend `_parent_policy_decision_after_phase_attempt` and/or the **inner** `for attempt_number in range(1, max_attempts + 1)` block so a parent attempt can replace `phase_plan` (or re-call into `plan_resolver`) mid-phase. This is a **structural** change to the retry loop, not a metadata tweak.
- **`agent/orchestrator/plan_resolver.py`** — helpers to rebuild one phase safely; must not recurse into full `get_parent_plan` in a way that re-enters `run_hierarchical`.
- **`tests/test_two_phase_execution.py`** — REPLAN invariants (attempt_history, merged errors, final `loop_output`).

**Explicit:** This candidate **cannot** be completed in `plan_resolver.py` + config alone.

#### Blast radius

**High.** Touches the same surface area as Stage 4/5 (retry loop). Frozen-file policy: treat **`deterministic_runner.py`** edits as **high risk** for hierarchical work unless narrowly scoped bugfixes.

#### New invariants required

- Does REPLAN consume the same `max_parent_retries` budget as RETRY, or a separate `max_parent_replans`?
- `phase_result["loop_output"]`: final attempt only (consistent with today) vs snapshot per replan?
- Trace: new `decision` value `REPLAN` on `parent_policy_decision`; possible `phase_replanned` payload with `plan_id` before/after.

#### Compatibility risks

Compat path: **none** if all REPLAN logic stays under `if not parent_plan["compatibility_mode"]`.

#### Observability impact

New decision enum value; new or extended trace payloads. No change to compat trace parity with `run_deterministic`.

#### Why now / why not now

**Why not now (default):** Stage 7 **just** turned on non-zero default budgets. **Measure** production/trace behavior under `RETRY` before adding plan mutation. REPLAN **requires** retry-loop design decisions that are **not** pre-answered in `STAGE7_CLOSEOUT_REPORT.md`.

**When to revisit:** After evidence that same-plan retries hit diminishing returns for a measurable class of failures.

---

### Candidate B — REQUEST_CLARIFICATION parent-policy outcome

#### What problem it solves

Terminal **STOP** after exhausted retries gives `errors_encountered` and `parent_goal_met: False` but no **first-class** “ask the user” channel. Product may want a structured clarification request instead of silent failure.

#### Required files to change

- **`deterministic_runner.py`** — new terminal branch; clarification payload assembly.
- **Caller contract** — anything that consumes `run_hierarchical`’s `loop_output` must distinguish clarification from failure.
- **`tests/hierarchical_test_locks.py`** — **if** a new top-level hierarchical key is added (e.g. `clarification_requested`), **L6** requires updating the lock list — **major scope warning** per Stage 7 memo.

**Roadmap §1.4 / execution roadmap §85–91:** Clarification is described as a **Stage 3** parent outcome when **retry and replan budgets** are exhausted — **REPLAN is not shipped**, so the roadmap’s ordering is **not** fully satisfiable yet.

#### Blast radius

**Very high** for product integration. **Any** new top-level `loop_output` key visible to hierarchical callers is a **contract expansion**.

#### New invariants required

Clarification is terminal; never emitted on compat; payload schema versioned; `phase_count` / `phase_results` rules unchanged.

#### Compatibility risks

Compat: must remain key-clean. Non-compat: **all** callers updated or broken.

#### Observability impact

New events + likely new `loop_output` fields — auditable but high ceremony.

#### Why now / why not now

**Why not now:** Depends on **policy ordering** (replan/clarify) and **caller readiness**. Larger than a config/planning slice.

---

### Candidate C — Per-phase configurable retry budgets (Phase 0 ≠ Phase 1)

#### What problem it solves

Stage 7 applies **one** scalar to **both** phases. Operationally, **docs Phase 0** (sparse retrieval, EXPLAIN variance) may warrant **more** parent attempts than **code Phase 1**, or the reverse. Tuners cannot express that without editing code.

#### Required files to change

- **`config/agent_config.py`** — e.g. `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0` and `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1` (names illustrative), or a pair of ints with the same `_coerce_max_parent_retries` semantics as Stage 7.
- **`agent/orchestrator/plan_resolver.py`** — `_build_two_phase_parent_plan` assigns `phase_0["retry_policy"]["max_parent_retries"]` and `phase_1["retry_policy"]["max_parent_retries"]` from **two** coerced values.

**Explicit:** **`deterministic_runner.py` unchanged** — `_get_max_parent_retries(phase_plan)` already reads **per-phase** `retry_policy`; Stage 4 loop already supports asymmetric budgets **if** the plan carries different numbers (previously only hand-crafted tests did).

#### Blast radius

**Low** — same class as Stage 7 (plan construction + config). **No** new `loop_output` keys. **`hierarchical_test_locks.py` unchanged.**

#### New invariants required

- Both config values pass `_coerce_max_parent_retries` (or equivalent).
- Defaults: if both set equal to current **1**, behavior matches Stage 7 shipped defaults for typical configs.
- Compat path never reads these keys (unchanged).

#### Compatibility risks

**None** on compat — `_build_two_phase_parent_plan` not used for `compatibility_mode=True`.

#### Observability impact

**No new trace event names.** Existing `phase_completed` / `parent_policy_decision` already include **per-phase** `max_parent_retries` in payloads — values would **differ by phase index**, which is **more informative**, not noisier.

#### Why now / why not now

**Why now:** Smallest **incremental** step after Stage 7 — implements the **“per-phase independent retry budgets”** deferral from `STAGE7_DECISION_MEMO.md` §6 without touching the execution engine. Aligns with Stage 6 audit footnote (**§337**) directionally (budget was hardcoded; now tunable per phase).

**Why not now:** If product prioritizes **REPLAN** for plan-quality failures before **tuning attempt counts**, order could swap — at **higher** engineering cost.

---

### Candidate D — Retrieval use of `prior_phase_ranked_context` (and related handoff keys)

#### What problem it solves

Phase 1 `execution_loop` builds retrieval from Phase 1 state only; **`prior_phase_ranked_context`** is injected on `AgentState.context` but **not merged** into ranking / candidate selection. Two-phase runs may **under-use** Phase 0 docs signal in Phase 1 retrieval.

#### Required files to change

- **`agent/orchestrator/execution_loop.py`** and/or **`agent/execution/step_dispatcher.py`** and possibly **`agent/orchestrator/replanner.py`** — must **read** and **merge** prior-phase context into retrieval or ranking in a bounded way.
- **`config/agent_config.py`** — caps (e.g. `MAX_CONTEXT_CHARS`) already cited in roadmap §5.5 pruning.

**Explicit:** This is **not** a `plan_resolver`-only change. **`HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md`** locks “no modifying execution_loop internals” for **Stage 1/2 scope**; hierarchical roadmap still lists these modules as **frozen** for orchestration work except agreed extensions.

#### Blast radius

**High** — affects every phase-local `execution_loop` invocation that sees handoff keys; risk of **double-counting** context, **lane** confusion, or **non-deterministic** ranking if merge rules are vague.

#### New invariants required

Deterministic merge order; hard cap on injected prior context; no behavior change for compat/single-phase; Phase 1 `ranked_context` contract remains valid for `PhaseValidationContract`.

#### Compatibility risks

Low for compat (no handoff). **High** for test matrix of two-phase + retrieval.

#### Observability impact

Likely new trace fields (“prior_context_used”, counts merged) for debugging.

#### Why now / why not now

**Why not now (as default Stage 8):** Largest surface area; **conflicts with “prefer plan_resolver/config”** decision standard. Requires **retrieval design** (what gets merged, where in the pipeline).

**Why later:** Highest **end-user quality** upside for true docs→code tasks **after** retry budgets are tuned (C) and/or policy escalation (A/B) is scoped.

---

## 5. Decision Standards (Constraints)

| Constraint | Implication |
|--------------|-------------|
| Smallest blast radius | Prefer **C** over **A**, **B**, **D** |
| Preserve compat invariants **L1**, **L6** | **B** is suspect unless clarification is carried without new top-level keys (difficult) |
| Prefer **no** `run_deterministic` change | All four candidates satisfy if scoped to hierarchical-only (B may still touch return shape) |
| Structural edits to **retry loop** in `deterministic_runner.py` | **A** (and **B**) — **high risk** |
| **`hierarchical_test_locks.py` change** | **Major scope warning** — **B** likely requires it for new keys |
| **No 3+ phases** | Out of scope for Stage 8 slice |
| **Do not widen `_is_two_phase_docs_code_intent`** unless production audit proves miss rate — Stage 6 **near-miss** trace exists for false-negative analysis |

---

## 6. Recommendation

### 6.1 Chosen next slice — **Candidate C: per-phase configurable retry budgets**

**Rationale (blunt):**

- **C** is the only candidate that **extends Stage 7** along the **same files** (`config/agent_config.py`, `plan_resolver.py`) with **zero** changes to `deterministic_runner.py`, **`execution_loop.py`**, **`step_dispatcher.py`**, **`replanner.py`**, or **`hierarchical_test_locks.py`**.
- **A** and **B** require **parent-policy semantics** and **retry-loop** work; **B** likely **breaks caller assumptions** and **lock files**. Ship **C** first, gather traces under **asymmetric** budgets, **then** decide if failures are “wrong plan” (A) vs “need user input” (B).
- **D** is the **right long-term quality fix** for handoff but the **largest** touch; defer until **explicit approval** to modify frozen execution modules and a written merge spec exists.

### 6.2 Why the others are deferred

| Candidate | Deferral reason |
|-----------|-----------------|
| **A (REPLAN)** | Retry-loop surgery; undefined REPLAN vs RETRY accounting; ship **C** and measure first. |
| **B (REQUEST_CLARIFICATION)** | Roadmap ordering vs REPLAN; caller contract; **L6** risk. |
| **D (retrieval)** | Frozen execution path; highest blast radius; not config-only. |

### 6.3 Smallest viable implementation scope (Stage 8 if C is approved)

1. Add **two** config integers (or one struct) with **same coercion** as `_coerce_max_parent_retries`.
2. In `_build_two_phase_parent_plan`, set `phase_0` and `phase_1` `retry_policy` from those two values.
3. Tests: extend `TestStage7RetryBudgetConfiguration` / `TestStage7CloseoutInvariants` — unequal budgets, both phases still respect `_get_max_parent_retries` in execution (mocked `execution_loop`).

### 6.4 Likely files to touch

| File | Role |
|------|------|
| `config/agent_config.py` | Second budget constant or paired settings |
| `agent/orchestrator/plan_resolver.py` | `_build_two_phase_parent_plan` assigns distinct `max_parent_retries` per phase |
| `tests/test_two_phase_execution.py` | Asymmetric budget tests |

### 6.5 Rollback (Candidate C)

1. Revert to **single** `TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PER_PHASE` driving both phases (Stage 7 behavior).
2. Remove second config key(s) and tests that assume asymmetry.
3. Proof command must return prior test count before asymmetry PR.

---

## 7. Do Not Do Yet (Stage 8 Scope Guards)

| Item | Reason |
|------|--------|
| **≥ 3 phases** | Roadmap gate; `NotImplementedError` guard (**L5**) |
| **REPLAN / REQUEST_CLARIFICATION** in same PR as **C** | Policy expansion deserves its own review |
| **Widen `_is_two_phase_docs_code_intent`** | No Stage 7/8 audit evidence; use **`two_phase_near_miss`** traces first |
| **Retrieval merge (D)** without signed-off merge design | Avoid ad-hoc `execution_loop` edits |
| **New top-level `loop_output` keys** without caller + **L6** update | Contract explosion |

---

## 8. Relation to Prior Memos

- **`STAGE7_DECISION_MEMO.md`** deferred “per-phase independent retry budgets” to post–single-scalar work — **Stage 8 Candidate C** is that deferred item.
- **`STAGE6_IMPLEMENTATION_PLAN.md` §9** listed `REPLAN` / `REQUEST_CLARIFICATION` as non-goals; **Stage 7** shipped activation, not policy expansion — **A/B** remain open roadmap items, not automatic Stage 8.
- **`HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §5.5** documents **D** as future retrieval improvement — consistent with deferring **D** until execution-layer approval.

---

*End of Stage 8 decision memo.*
