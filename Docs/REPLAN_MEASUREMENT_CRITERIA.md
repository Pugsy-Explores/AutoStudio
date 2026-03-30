# REPLAN Measurement Criteria

**Type:** Stage 9 hold-and-measure gate (documentation only)  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Related:** `Docs/STAGE9_DECISION_MEMO.md`, `Docs/REPLAN_PRECODING_DECISIONS.md`

--- 

## Purpose

Define **observable, trace-based criteria** for when the architecture should **approve Stage 10 (REPLAN implementation)**. This document does **not** implement REPLAN. It is a **measurement and approval gate** so REPLAN is justified by production or staging evidence, not by speculation.

**This is a gate to Stage 10, not REPLAN itself.** No code in this repo is changed by adopting these criteria.

---

## What trace events and fields are used

All signals below are derivable from **existing** hierarchical trace payloads in `agent/orchestrator/deterministic_runner.py` when `trace_id` is non-null (compat path emits no hierarchical per-phase events).

| Event | Role |
|-------|------|
| `run_hierarchical_start` | Binds a run: `parent_plan_id`, planned `phase_count` (length of phases), `compatibility_mode` |
| `phase_started` | Phase scope: `parent_plan_id`, `phase_id`, `phase_index`, `lane`, `step_count`, `subgoal_preview` |
| `phase_completed` | **Per attempt:** `parent_plan_id`, `phase_index`, `attempt_count`, `success`, `goal_met`, `goal_reason`, **`failure_class`**, `phase_validation`, `max_parent_retries`, `parent_retry`, `parent_retry_eligible`, `parent_retry_reason` |
| `parent_policy_decision` | **Per attempt:** `parent_plan_id`, `phase_index`, **`decision`**, **`decision_reason`**, `attempt_count`, `max_parent_retries`, `parent_retry`, `phase_validation` |
| `phase_validation_failed` | Optional extra detail when validation fails (same `phase_index`) |
| `parent_goal_aggregation` | Run outcome summary |

**Correlation key:** `trace_id` (implicit in all events for a run) + `parent_plan_id` + `phase_index` + `attempt_count`.

- Consecutive attempts of the **same phase** share the same `parent_plan_id` and `phase_index`; `attempt_count` is `1`, then `2`, etc.
- **`failure_class`** appears on **`phase_completed`** (not duplicated on `parent_policy_decision`); pair `phase_completed` rows by `(parent_plan_id, phase_index, attempt_count)` and compare `failure_class` across attempts.
- **`parent_policy_decision.decision == "RETRY"`** is logged **after** the corresponding `phase_completed` for that attempt; the next loop iteration increments `attempt_count`.

No additive trace fields are **required** for the hold-expiry criteria below; existing payloads are sufficient.

---

## Exact hold-expiry criteria (Stage 9 → Stage 10 gate)

A **formal go** for Stage 10 REPLAN implementation work is justified when **both** hold:

### Criterion 1 — Repeated failure with same observable class (Phase 0 focus)

For **phase_index == 0** (docs phase), over an agreed observation window:

1. There exists a **pair of consecutive failed attempts** for the same `(parent_plan_id, phase_index)` such that:
   - First attempt: `phase_completed.success == false` (or goal/validation outcome is failed per existing fields), and the immediately following `parent_policy_decision` for that attempt has `decision == "RETRY"`.
   - Second attempt: `phase_completed` for `attempt_count == previous + 1` with the **same `failure_class`** string as the first attempt’s `phase_completed` (both non-null and equal).

2. This pattern occurs at a **material rate** relative to two-phase non-compat runs (exact threshold is operational: e.g. ≥5% of runs or ≥N events per week — set by team before Stage 10 kickoff).

**Interpretation:** Same plan, fresh state per attempt — if `failure_class` is stable across retries, same-plan RETRY may be hitting a **plan-quality** ceiling (wrong seed query, wrong steps), which REPLAN is designed to address.

### Criterion 2 — Observation window and environment

- **Minimum window:** e.g. **7 consecutive calendar days** of collection in the target environment (staging or production), unless a smaller window is explicitly approved with rationale.
- **Trace completeness:** `trace_id` must be present for hierarchical runs under measurement (otherwise events are not emitted).

### Override

Architecture may approve Stage 10 without Criterion 1 if a **written exception** documents why measurement is blocked and what smaller experiment (e.g. forced-failure integration test in a sandbox) substitutes for live traces.

---

## Observation window guidance

| Factor | Guidance |
|--------|----------|
| **Warm-up** | Ignore the first day after enabling non-zero retry budgets if config or traffic shifts abruptly. |
| **Volume** | If weekly two-phase volume is low, extend the window or lower the percentage threshold with explicit approval. |
| **Noise** | Bucket by `failure_class`; do not treat transient infra errors (`timeout`, flaky tool) as REPLAN drivers without separate analysis. |
| **Phase 1** | The same logical criteria **can** be applied to `phase_index == 1` in a follow-on measurement; Stage 9 memo emphasized Phase 0 as the primary signal for docs→code decomposition. |

---

## Examples — patterns that suggest “same-plan retry is insufficient”

These are **indicators**, not automatic triggers without rate/window (see hold-expiry criteria).

1. **Stable `failure_class` across attempt 1 and 2 for Phase 0**, e.g. both `phase_validation_failed` with the same validation failure reasons in `phase_validation.failure_reasons`, and `parent_policy_decision` was `RETRY` after attempt 1.
2. **Repeated `missing_ranked_context` or `min_candidates_not_met`** on consecutive Phase 0 attempts — the docs seed query may be wrong for the instruction class.
3. **`goal_met` false with same `goal_reason` and same `failure_class`** on consecutive attempts (e.g. persistent `goal_not_satisfied` with no drift in `completed_steps`).

---

## Examples — patterns that do **not** justify REPLAN yet

1. **First attempt succeeds** — no retry story.
2. **Failure on attempt 1, success on attempt 2** — RETRY worked; REPLAN not indicated by this signal.
3. **`failure_class` differs** between attempt 1 and 2 (e.g. `timeout` then `goal_not_satisfied`) — environment or nondeterminism; investigate before REPLAN.
4. **Only one attempt** because `max_parent_retries == 0` — measurement must use configs where retries can occur.
5. **Compat-mode runs** — no hierarchical `phase_completed` / `parent_policy_decision`; out of scope for this gate.

---

## Explicit note: gate to Stage 10, not REPLAN

- Satisfying the hold-expiry criteria **authorizes Stage 10 planning and implementation** per `Docs/REPLAN_PRECODING_DECISIONS.md`.
- This document **does not** add REPLAN to the runtime, change parent policy, or modify `loop_output` contracts.

---

*End of REPLAN measurement criteria.*
