# REQUEST_CLARIFICATION Measurement Criteria

**Type:** Stage 11 hold-and-measure gate (documentation only)  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Related:** `Docs/STAGE11_DECISION_MEMO.md`, `Docs/STAGE10_CLOSEOUT_REPORT.md`, `Docs/REPLAN_MEASUREMENT_CRITERIA.md`

---

## Purpose

Define **observable, trace-based criteria** for when the architecture should **approve Stage 12 work to implement `REQUEST_CLARIFICATION`** as a parent-policy outcome (terminal “ask the user” signal on the non-compat hierarchical path). This document does **not** implement clarification. It is a **measurement and approval gate** so clarification is justified by evidence — not by roadmap checkbox pressure.

**This is a gate to Stage 12 (clarification precoding + implementation planning), not REQUEST_CLARIFICATION itself.** No code in this repo is changed by adopting these criteria.

---

## Proof baseline (recorded at Stage 11 doc authoring)

Re-run after checkout; counts may drift if unrelated tests are added.

| Command | Recorded result |
|---------|-----------------|
| `python3 -m pytest tests/test_two_phase_execution.py -q` | **180 passed** |
| `python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q` | **203 passed** |

---

## What trace events and fields are used

Signals assume **Stage 10** is deployed: **RETRY**, **REPLAN**, `phase_replanned`, `phase_replan_failed` may appear. All hierarchical events require non-null **`trace_id`** (compat path does not emit per-phase policy events).

| Event | Role |
|-------|------|
| `run_hierarchical_start` | Binds run: `parent_plan_id`, planned phase count, `compatibility_mode` |
| `phase_started` | Phase scope: `phase_index`, `lane`, `subgoal_preview`, `step_count` |
| `phase_completed` | Per attempt: `phase_index`, `attempt_count`, `success`, `goal_met`, `goal_reason`, **`failure_class`**, `phase_validation` |
| `phase_validation_failed` | Validation detail: `validation_failure_reasons` |
| `parent_policy_decision` | **`decision`**: `CONTINUE` \| `RETRY` \| **`REPLAN`** \| `STOP`; **`decision_reason`**; `attempt_count`; `phase_validation` |
| `phase_replanned` | REPLAN succeeded: `previous_failure_class`, `old_plan_id`, `new_plan_id`, `attempt_count`, `phase_index` |
| `phase_replan_failed` | REPLAN build failed → terminal phase failure path |
| `phase_context_handoff` | Phase 0→1: `ranked_context_items`, `pruned` (handoff size signal) |
| `parent_goal_aggregation` | Run-level outcome |

**Correlation:** `trace_id` + `parent_plan_id` + `phase_index` + `attempt_count`.

**Terminal failure analysis:** For runs with `parent_goal_met: false` (from aggregated `loop_output` or equivalent telemetry), inspect the **last** `parent_policy_decision` per failed phase and whether **budget was exhausted** (`decision == "STOP"` after `attempt_number` reached max for that phase).

---

## Exact hold-expiry criteria (Stage 11 → Stage 12 clarification gate)

A **formal go** for Stage 12 **REQUEST_CLARIFICATION precoding + implementation** is justified only when **all** of the following hold:

### Criterion 1 — Exhausted parent policy without recoverable automation signal

Over an agreed observation window, a **material fraction** of two-phase non-compat runs ends in **terminal failure** where:

1. **Parent attempts for the failing phase reached the configured cap** — i.e. `RETRY` / `REPLAN` (when triggered) did not yield success, and the final `parent_policy_decision` for that phase is **`STOP`** (not a mid-run abort for unrelated reasons).

2. The **dominant failure pattern** is **not** explained by:
   - **infra / limits** alone (`timeout`, `limit_exceeded`, flaky tool — see §Anti-patterns), or  
   - **fixable by more REPLAN iterations** (e.g. `phase_replan_failed` rate dominates and points to **bugs** in `_build_replan_phase`, not user ambiguity).

3. Post-hoc categorization (dashboards or manual sampling) shows a **repeatable class** of terminal outcomes that **product** classifies as **“needs user input”** (ambiguous scope, missing repo-specific pointer, contradictory instruction) — **not** “wrong plan, keep tuning REPLAN.”

**Operational thresholds** (e.g. ≥N terminal failures/week in this class, or ≥X% of failed two-phase runs) are **set by team** before Stage 12 kickoff — this document does not fix global percentages.

### Criterion 2 — Observation window and environment

- **Minimum window:** e.g. **7 consecutive calendar days** in staging or production (or equivalent trace volume), unless a shorter window is **written and approved** with rationale.
- **Trace completeness:** `trace_id` present for measured runs; otherwise events are missing.

### Criterion 3 — Caller readiness (organizational, not code in this repo)

- Product/engineering acknowledges that **REQUEST_CLARIFICATION** will require **caller contract** updates **or** an explicitly approved **non–top-level** encoding — documented in **`Docs/STAGE12_PRECODING_DECISIONS.md`** before implementation.

### Override

Architecture may approve Stage 12 clarification work **without** Criterion 1 only with a **written exception** (e.g. regulatory requirement to surface human escalation) that still mandates **`STAGE12_PRECODING_DECISIONS.md`** before code.

---

## What does **not** count as clarification-worthy

These patterns **must not** alone justify adding `REQUEST_CLARIFICATION`:

| Pattern | Why it is not clarification |
|--------|----------------------------|
| **`timeout` / `limit_exceeded` / `stall_detected`** as dominant `failure_class` | Capacity / infra — fix limits, runtime, or tooling — not “ask the user” by default |
| **First failure** with no retry story | No exhaustion of parent policy |
| **Success after RETRY or after REPLAN** | Automation worked |
| **`phase_replan_failed` due to thrown errors or validate_plan** | Engineering bug or replan helper quality — fix Stage 10 path before blaming the user |
| **Low weekly volume** of two-phase runs | Percentages are meaningless; extend window or wait |
| **Compat-mode runs** | No hierarchical policy events; out of scope |
| **Single occurrence** or **one customer** | No stable class — anecdote, not criteria |
| **“We don’t like STOP UX”** without trace-backed class | Product preference ≠ measurement |

---

## Anti-patterns / false positives

1. **Misclassifying infra as ambiguity** — always bucket `failure_class` and `errors_encountered` before tagging “clarification.”
2. **Conflating REPLAN failure rate with clarification need** — high `phase_replan_failed` suggests **code/validation** work first.
3. **Using clarification to mask bad detection** — widening `_is_two_phase_docs_code_intent` or routing is a **different** decision; use `two_phase_near_miss` traces first.
4. **Demanding clarification because REPLAN exists** — REPLAN must be **measured** first; this gate is **after** that evidence exists.

---

## Observation window guidance

| Factor | Guidance |
|--------|----------|
| **Warm-up** | Ignore the first 24–48h after a release that changes REPLAN if traffic is spiky. |
| **Volume** | If two-phase traffic is low, use **absolute counts** (N events) instead of percentages. |
| **Phase focus** | Start with the phase that drives most terminal failures (often Phase 0 or Phase 1 — measure separately). |
| **REPLAN saturation** | If REPLAN rarely fires, terminal failures may still be “plain STOP” — Criterion 1 still applies to **exhausted budget**, not “must have seen REPLAN.” |

---

## Examples — patterns that **suggest** clarification may be justified (not automatic)

1. Terminal **`STOP`** on a phase whose **`attempt_history`** shows **multiple** distinct plans (`plan_id` changes) and **still** the same **user-interpretable** blocker in `errors_encountered` / synthetic markers (e.g. “instruction references path not in repo” across attempts).
2. Stable **`phase_validation_failed`** with **`missing_explain_success`** after docs context **exists** but evaluator semantics disagree — **only** if product defines this as “user must narrow docs scope.”
3. High rate of **`parent_goal_met: false`** with **empty** `phase_results` success path and **no** `phase_replan_failed` — suggests policy exhaustion without engineering error.

*These require Criterion 1 + team threshold — not automatic approval.*

---

## Explicit non-goals (this document)

| Non-goal | Notes |
|----------|--------|
| **Implement REQUEST_CLARIFICATION** | Stage 12+ |
| **Change `deterministic_runner.py`** | Not triggered by this doc alone |
| **Change `hierarchical_test_locks.py`** | Contract decision belongs in Stage 12 precoding |
| **Define clarification payload schema** | `Docs/STAGE12_PRECODING_DECISIONS.md` |
| **Retrieval merge** | See `Docs/RETRIEVAL_HANDOFF_MERGE_DESIGN_QUESTIONS.md` — separate track |

---

## Explicit note: gate to Stage 12, not implementation

- Satisfying the hold-expiry criteria **authorizes Stage 12 planning** (precoding memo + implementation review).
- This document **does not** add `REQUEST_CLARIFICATION` to the runtime, change parent policy enums in code, or modify `loop_output` contracts.

---

*End of REQUEST_CLARIFICATION measurement criteria.*
