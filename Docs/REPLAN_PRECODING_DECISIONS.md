# REPLAN Pre-Coding Decisions

**Type:** Architecture lock-in before Stage 10 implementation  
**Date:** 2026-03-20  
**Branch context:** `next/stage3-from-stage2-v1`  
**Related:** `Docs/STAGE9_DECISION_MEMO.md`, `Docs/REPLAN_MEASUREMENT_CRITERIA.md`

This document answers open design questions **in writing** so Stage 10 REPLAN work does not re-litigate semantics in code review. **No code in this file** — implementation must match these decisions unless explicitly revised by a new memo.

---

## 1. Budget: shared with RETRY vs separate `max_parent_replans`

**Decision:** REPLAN consumes the **same** parent-level budget as RETRY: **`retry_policy.max_parent_retries`** (interpreted as a cap on **additional** parent attempts after the first try, whether those additional attempts are labeled RETRY or REPLAN).

- Each **execution** of a phase (whether same plan or replanned plan) consumes **one** unit of “parent attempt” toward the per-phase limit: allowed attempts = `1 + max_parent_retries` total, unchanged from Stage 4 semantics.
- **No separate `max_parent_replans` counter** in Stage 10 initial implementation. A REPLAN is not a “free” extra attempt beyond the existing attempt cap.

**Rationale:** One budget keeps accounting identical to current `attempt_count`, `attempts_total`, and `retries_used` semantics; avoids new config surface and cross-product testing of two independent caps.

---

## 2. Stage 10 start: shared budget only

**Decision:** Stage 10 ships REPLAN under **shared budget only** (see §1). A **separate** replan budget is **out of scope** for Stage 10 unless a follow-on memo re-opens it with production evidence that shared budget blocks valid REPLAN chains.

---

## 3. What `attempt_history` means across plan substitution

**Decision:** `attempt_history` remains a **chronological list of parent attempts for this phase**, one entry per `execution_loop` invocation for that phase.

- Each entry continues to match the Stage 5 shape (`attempt_count`, `success`, `goal_met`, `goal_reason`, `failure_class`, per-attempt `errors_encountered`, `phase_validation`, `parent_retry`).
- After REPLAN, a new attempt uses a **new** `phase_plan` (new `plan_id` / steps). The **entry is still an attempt row**; the implementation **must** record **which plan identity** applied to that attempt (see §9 — add `plan_id` or `phase_plan_snapshot_id` **inside** each attempt row or in trace only; **not** a new top-level `loop_output` key).

**Minimum lock for Stage 10:** each `attempt_history[i]` includes the **`plan_id`** string for the plan executed on that attempt (additive field on the per-attempt dict only). This distinguishes “retry same plan” vs “replan new plan” without ambiguity.

---

## 4. Do replan attempts increment `attempt_count`?

**Decision:** **Yes.** REPLAN leads to a new `execution_loop` call → it is a new parent attempt → `attempt_count` increments exactly as today for RETRY (1-based, monotonic within the phase until success or terminal STOP).

---

## 5. `errors_encountered_merged` across REPLAN

**Decision:** **Unchanged from Stage 4:** `errors_encountered_merged` for the phase is the **concatenation** of `errors_encountered` from **every** attempt’s `loop_output` for that phase, in order, including attempts after REPLAN. No reset on replan.

---

## 6. What `phase_result["loop_output"]` represents after REPLAN

**Decision:** **Unchanged from Stage 4/5:** `phase_result["loop_output"]` is the **`execution_loop` output from the final attempt only** (whether that attempt followed RETRY or REPLAN). Prior attempts’ loop outputs remain visible only via `attempt_history` entries and merged errors, not as multiple `loop_output` fields on `phase_result`.

---

## 7. Recursion / re-entry guard

**Decision:** REPLAN **must not** call `get_parent_plan()` or `run_hierarchical()` from inside the per-phase retry loop.

- Allowed: a **phase-scoped** helper in `plan_resolver.py` (e.g. `_replan_phase(phase_plan, failure_context)`) that calls `plan()`, `_docs_seed_plan`, or other **single-phase** builders, plus `validate_plan`, without constructing a full `ParentPlan`.
- **Forbidden:** any code path that builds a new two-phase parent plan and re-enters `run_hierarchical` recursively.

**Implementation note:** pass `parent_plan_id` and `phase_index` into the helper for logging only; stack depth must stay O(1) relative to today’s loop.

---

## 8. Invalid replan fallback

**Decision:** If replanning **throws** or produces a plan that **fails `validate_plan`**:

1. Log a trace event (name TBD at implement time; e.g. `phase_replan_failed`) with reason string capped for safety.
2. Treat as **terminal failure for that phase** — same as exhausted RETRY: **STOP** with existing `parent_policy_decision` terminal reasons (`goal_not_met` / `phase_failed` per current mapping).
3. **No infinite loop**, no silent skip to success.

---

## 9. Trace event expectations for future REPLAN

**Decision (minimum):**

| Event | When |
|-------|------|
| Existing `parent_policy_decision` | New `decision` value **`"REPLAN"`** (distinct from **`"RETRY"`**); use **`decision_reason`** for sub-reasons (e.g. `replan_scheduled`). Do **not** overload **`"RETRY"`** to mean replan. |
| **`phase_replanned`** (recommended) | Emitted when a new `phase_plan` is committed before the next attempt; payload includes `parent_plan_id`, `phase_index`, `old_plan_id`, `new_plan_id`, `attempt_count` **before** next execution. Optional only if `parent_policy_decision` payloads are proven sufficient for dashboards. |

**Compat:** no new events on compat path. **No new top-level `loop_output` keys** for REPLAN; any new caller-visible fields require a separate contract memo and `hierarchical_test_locks.py` update — **out of scope for Stage 10** unless explicitly approved.

---

## 10. Explicit non-goals for Stage 10

| Non-goal | Notes |
|----------|--------|
| **REQUEST_CLARIFICATION** | Roadmap-ordered after REPLAN policy; not part of Stage 10. |
| **Separate `max_parent_replans`** | Deferred unless a new memo re-opens. |
| **Retrieval merge of `prior_phase_ranked_context`** | Frozen execution modules; separate initiative. |
| **≥ 3 phases** | `NotImplementedError` guard unchanged. |
| **New top-level hierarchical `loop_output` keys** | Forbidden unless separate contract change + lock file update. |
| **Changing `run_deterministic`** | Forbidden. |
| **Changing compat output shape** | Forbidden. |
| **Widening `_is_two_phase_docs_code_intent`** | Not part of REPLAN Stage 10. |

---

## Summary table

| Topic | Decision |
|-------|----------|
| Budget | REPLAN shares `max_parent_retries` with RETRY; same total attempt cap |
| Stage 10 scope | Shared budget only |
| `attempt_history` | Chronological per attempt; add per-row `plan_id` to disambiguate replan |
| `attempt_count` | Increments for each attempt including post-replan |
| `errors_encountered_merged` | Concatenate all attempts; no reset on replan |
| `phase_result["loop_output"]` | Final attempt only |
| Re-entry | No `get_parent_plan` / `run_hierarchical` from replan helper |
| Invalid replan | Terminal STOP; trace failure; no loop |
| Trace | `REPLAN` decision (+ optional `phase_replanned`); compat unchanged |

---

*End of REPLAN pre-coding decisions.*
