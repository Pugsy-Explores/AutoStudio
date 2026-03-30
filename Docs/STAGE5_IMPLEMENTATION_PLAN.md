# Stage 5 — Implementation plan (memo)

**Audience:** Principal engineer / release owner  
**Branch context:** `next/stage3-from-stage2-v1` line  
**Date:** 2026-03-20 (updated to reflect shipped stages)

---

## 1. Where we are in the transition

| Repo stage | Status | What shipped |
|------------|--------|----------------|
| **Stage 1** | Complete | Schemas, `get_parent_plan`, `run_hierarchical` compat delegation to `run_deterministic` |
| **Stage 2** | Complete | Two-phase `two_phase_docs_code` parent plan, phase loop, handoff, `GoalEvaluator` `phase_subgoal` |
| **Stage 3** | Complete | Phase validation enforcement, trace/metadata, parent-retry **reporting** (pre-execution) |
| **Stage 4** | Complete | Parent **retry execution** (`RETRY` / `CONTINUE` / `STOP`), `errors_encountered_merged`, invariants — see `Docs/STAGE4_CLOSEOUT_REPORT.md` |
| **Stage 5** | Complete (this repo) | Structured **attempt observability** — see §3 and `Docs/STAGE5_CLOSEOUT_REPORT.md` |

**Proof (hierarchical slice):** run the proof commands in `STAGE4_CLOSEOUT_REPORT.md` / `STAGE5_CLOSEOUT_REPORT.md` and compare counts to the recorded numbers on your checkout (counts rise as invariant tests are added; do not treat a static number as law without running CI).

---

## 2. Doc alignment warning (read before planning further work)

`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2 uses **different stage numbers** than this repository:

- Roadmap **“Stage 3 — Parent Policy and Escalation”** / **“Stage 4 — Broader Decomposition (3+ phases)”** are **outline gates**, not 1:1 with **implemented** Stages 3–5 above.
- In particular, roadmap **“Stage 4 — three-phase support”** is **not** repo Stage 4. Repo **Stage 4** is **parent retry execution** on the existing **two-phase** orchestrator.

**Action for future edits:** rename or cross-link roadmap subsections (e.g. “Gate: N-phase decomposition”) to avoid confusing new contributors. This memo does **not** change the roadmap file.

`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_TASK_BREAKDOWN.md` remains **Stage 1–2 actionable** in its header; later stages are driven by closeout reports, not that file.

`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_PRECODING_DECISIONS.md` is **Stage 1–2 locked**; it does not define Stage 4 retry execution or Stage 5 observability — those contracts live in closeout reports + tests.

---

## 3. What Stage 5 is (repo definition) — smallest slice after Stage 4

**Goal:** Make **real** parent retries **inspectable** without changing **when** retries run or **how** policy decides `RETRY` / `CONTINUE` / `STOP`.

**Scope (additive only):**

1. **`phase_results[i]["attempt_history"]`** — `list[dict]`, one row per `execution_loop` attempt for that phase, in order. Each row carries normalized per-attempt **`success`**, **`goal_met`**, **`goal_reason`**, **`failure_class`**, **`errors_encountered`** (that attempt only), **`phase_validation`**, **`parent_retry`**.
2. **Top-level hierarchical `loop_output`:** **`attempts_total`**, **`retries_used`** — derived aggregates (no new execution).

**Non-goals:**

- No changes to `run_deterministic`.
- No changes to `execution_loop`, `replanner`, `step_dispatcher`, `planner`, `validate_plan` (unless a future bug fix is proven elsewhere).
- No new `get_plan` / planner calls.
- No three-phase parent plans; `len(phases) == 2` non-compat remains enforced.
- No change to retry budget math (`1 + max_parent_retries` per phase).
- No new hierarchical-only keys on compat `loop_output` (extend `tests/hierarchical_test_locks.py` instead).

---

## 4. What remained after Stage 4 (before Stage 5)

After Stage 4, consumers could see:

- Final **`phase_result`** row and **`errors_encountered_merged`**, **`attempt_count`**, per-attempt **trace events** (`phase_completed`, `parent_policy_decision`).

They could **not** rely on a **single structured object** inside `loop_output` that listed every attempt’s goal/validation/retry snapshot without re-parsing traces. Stage 5 closes that gap.

---

## 5. Invariants that must stay locked (Stage 4 + compat)

- **Compat:** `run_hierarchical` with `compatibility_mode=True` returns **exactly** `run_deterministic`’s `(state, loop_output)`; `loop_output` has **no** keys in `HIERARCHICAL_LOOP_OUTPUT_KEYS` and no per-phase field names on the top-level dict (`tests/hierarchical_test_locks.py`).
- **`phase_count`:** `loop_output["phase_count"] == len(phase_results)` == **executed** phases (not planned; not attempt count).
- **`phase_results`:** **one row per phase**; final **`loop_output`** / **`context_output`** / outcome fields = **final attempt**; **`errors_encountered_merged`** = concatenation of attempt loop errors for that phase.
- **Handoff:** built only from **final successful** phase result.
- **Policy:** `_parent_policy_decision_after_phase_attempt` outcomes unchanged; Stage 5 only **records** attempts, does not add branches.

---

## 6. File-by-file change map (Stage 5 as implemented)

| File | Role |
|------|------|
| `agent/orchestrator/deterministic_runner.py` | `_snapshot_phase_attempt_for_history`, accumulate `attempt_history`, attach to final `phase_result`; `_build_hierarchical_loop_output` adds `attempts_total`, `retries_used` |
| `tests/hierarchical_test_locks.py` | Add `attempts_total`, `retries_used` to forbidden top-level keys; `attempt_history` to per-phase forbidden names on compat |
| `tests/test_two_phase_execution.py` | `TestStage5AttemptHistory` (+ compat invariant re-use) |
| `Docs/STAGE5_CLOSEOUT_REPORT.md` | Closeout semantics and proof commands |

**Explicitly untouched:** `run_deterministic()`, `execution_loop.py`, `replanner.py`, `step_dispatcher.py`.

---

## 7. Test-first plan (exact tests to add first)

Implement in **`tests/test_two_phase_execution.py`** before or in lockstep with production code:

1. **`attempt_history` present**; `len(attempt_history) == attempt_count` per phase (with and without retries).
2. **`attempt_history[-1]`** matches final **`phase_result`** fields: `success`, `goal_met`, `goal_reason`, `failure_class`, `phase_validation`, `parent_retry`.
3. **`attempts_total`** / **`retries_used`** match sums derived from `phase_results` (e.g. 3 attempts, 1 retry across two phases).
4. **Failed attempt** `errors_encountered` preserved in history when **final** attempt succeeds.
5. **Single-attempt** phases: `len(attempt_history) == 1`.
6. **Compat:** `assert_compat_loop_output_has_no_hierarchical_keys` after patching `run_deterministic` — no `attempts_total`, `retries_used`, or stray per-phase keys on `loop_output`.
7. **Entry shape:** required keys and list/dict types on each history row.

*(Class name in repo: `TestStage5AttemptHistory`.)*

---

## 8. Runtime changes to make after tests (minimal)

1. Inside the **per-phase retry loop**, after building the rolling `phase_result` for an attempt, **append** `_snapshot_phase_attempt_for_history(...)`.
2. On terminal attempt for that phase, set **`phase_result["attempt_history"] = attempt_history`** (full list).
3. In **`_build_hierarchical_loop_output`**, compute **`attempts_total`** = Σ `attempt_count`, **`retries_used`** = Σ `max(0, attempt_count - 1)` over **`phase_results`**.

No change to handoff, merge aggregation, or policy functions beyond reading the same fields already computed per attempt.

---

## 9. Rollback

- Revert **`deterministic_runner.py`** hunks for `attempt_history` and top-level aggregates.
- Revert **`hierarchical_test_locks.py`** key sets.
- Remove **`TestStage5AttemptHistory`** (and any assertions that require the new fields).
- Delete or archive **`Docs/STAGE5_CLOSEOUT_REPORT.md`** if rolling back the feature flag entirely.

---

## 10. Optional follow-ups (not Stage 5)

- Update **`HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md`** stage naming to match repo Stages 3–5 or add a **mapping table**.
- Extend **`parent_plan.py` / TypedDict** documentation for `attempt_history` if static typing is tightened (non-runtime).
- **N-phase decomposition** (roadmap “broader patterns”) remains a **separate** epic; not part of Stage 5.

---

## 11. Audit: `run_deterministic`

**Confirmed:** Stage 4–5 work does not modify **`run_deterministic()`**; hierarchical-only behavior is gated on **`compatibility_mode`** in **`run_hierarchical`**. No further action for this memo.
