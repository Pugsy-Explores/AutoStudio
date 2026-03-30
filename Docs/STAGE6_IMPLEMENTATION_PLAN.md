# Stage 6 — Implementation Plan (Memo)

**Audience:** Principal engineer / release owner
**Branch context:** `next/stage3-from-stage2-v1` line
**Date:** 2026-03-20
**Precondition:** Stage 5 closed out — 160 tests passing on proof command

---

## 1. Where We Are

### 1.1 Repo Stage Map (do not confuse with roadmap stage numbering)

| Repo stage | Status | What shipped |
|------------|--------|--------------|
| **Stage 1** | Complete | `parent_plan.py` schemas; `get_parent_plan`; `run_hierarchical` compat delegation |
| **Stage 2** | Complete | `_is_two_phase_docs_code_intent`; `_build_two_phase_parent_plan`; phase loop; context handoff; `GoalEvaluator.evaluate_with_reason(phase_subgoal=...)` |
| **Stage 3** | Complete | Phase validation enforcement; trace/metadata observability; parent-retry **reporting** |
| **Stage 4** | Complete | Real parent-retry execution (`CONTINUE` / `RETRY` / `STOP`); `errors_encountered_merged`; invariant tests |
| **Stage 5** | Complete | `attempt_history` per phase; top-level `attempts_total` / `retries_used`; compat lock extension |

**Proof command (must still pass on checkout):**
```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
# Expected: 160 passed
```

### 1.2 Roadmap vs. Repo Mapping (required reading before future work)

| Roadmap gate | Repo equivalent | Status |
|---|---|---|
| Roadmap Stage 1 (schemas + compat) | Repo Stage 1 | ✓ Done |
| Roadmap Stage 2 (two-phase mixed lane) | Repo Stage 2 | ✓ Done |
| Roadmap Stage 3 (CONTINUE / RETRY) | Repo Stages 3–4 | ✓ Partially done — `REPLAN` and `REQUEST_CLARIFICATION` not shipped |
| Roadmap Stage 3 (REPLAN + REQUEST_CLARIFICATION) | Not yet | ✗ Open |
| Roadmap Stage 4 (3+ phase decomposition) | Not yet | ✗ Blocked — `len(phases) != 2` guard enforced |

`Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` §2 stage numbering does **not** map 1:1 to repo stage numbering. Do not use roadmap stage numbers to refer to repo stages in code review or planning.

### 1.3 Current Capability Boundaries

**Working:**
- Every instruction → compat path or two-phase `two_phase_docs_code` path
- Two-phase: Phase 0 docs lane → context handoff → Phase 1 code lane
- Parent retry (CONTINUE / RETRY / STOP) with `max_parent_retries` per phase
- Full attempt observability: `attempt_history`, `attempts_total`, `retries_used`
- Compat invariants locked and tested in `tests/hierarchical_test_locks.py`

**Hard gaps identified in audit:**

1. **Detection coverage gap.** `_is_two_phase_docs_code_intent` fires only when `code_markers = ("explain", "flow", "function ", "method ", "class ")` appear in the instruction. Instructions with valid mixed intent that use other code-intent signals (e.g. "trace", "works", "does") fall silently to compat. There is no `two_phase_near_miss` trace event when detection nearly fires.

2. **Connector coverage gap.** `_derive_phase_subgoals` recognizes exactly five connectors: `" and explain "`, `" and describe "`, `" and show how "`, `" and summarize "`, `" and walk through "`. Instructions like `"find the architecture docs, then explain the replanner flow"` fire detection (has "explain" + docs token + discovery verb) but produce `phase_1_subgoal = full_instruction` because `", then explain "` is not in the connector list. The fallback (`phase_1_subgoal = full instruction`) produces a semantically coarser Phase 1 plan.

3. **No near-miss observability.** When `_is_two_phase_docs_code_intent` returns `True` but `_build_two_phase_parent_plan` catches an exception and falls back to compat, the `two_phase_fallback` trace event is emitted. But when detection simply doesn't fire (the instruction is close but misses), nothing is logged. Debugging false-negatives requires re-running the instruction through the heuristic manually.

4. **`REPLAN` and `REQUEST_CLARIFICATION` absent from parent policy.** These are listed in Roadmap Stage 3 but were not in scope for Repo Stages 3–4. They remain open.

5. **`prior_phase_ranked_context` injected but unused.** Phase 1's `state.context["prior_phase_ranked_context"]` is set on every non-compat run. No retrieval component in `execution_loop`, `step_dispatcher`, or `replanner` reads it. The key is dead weight for now (by design; referenced in §5.5 of the execution roadmap as "available as additional signal for future retrieval improvements").

---

## 2. What Is Already Locked and Must Not Change

The following are invariants. Any PR touching these files during Stage 6 must be rejected unless it is fixing a proven bug unrelated to this work.

### 2.1 Compat path invariants
- `run_hierarchical` with `compatibility_mode=True` returns **exactly** `run_deterministic`'s `(state, loop_output)`.
- `loop_output` for compat path must contain **none** of the keys in `HIERARCHICAL_LOOP_OUTPUT_KEYS` and none of `_PHASE_RESULT_FIELD_NAMES` (`errors_encountered_merged`, `attempt_history`).
- **Test assertion:** `assert_compat_loop_output_has_no_hierarchical_keys` in `tests/hierarchical_test_locks.py`.

### 2.2 Hierarchical semantics
- `loop_output["phase_count"]` == `len(phase_results)` == **executed** phases only. Never planned phase total.
- `phase_results`: **one final row per phase**. `attempt_count` = total attempts for that phase.
- `errors_encountered_merged`: concatenation of all attempt loop errors for that phase.
- `attempt_history[-1]` matches final phase result for: `success`, `goal_met`, `goal_reason`, `failure_class`, `phase_validation`, `parent_retry`.
- `len(attempt_history)` == `phase_result["attempt_count"]`.

### 2.3 Handoff
- Built only from the **final successful** phase result (after retries). Never from an intermediate failed attempt.

### 2.4 `len(phases) != 2` guard
- `run_hierarchical` raises `NotImplementedError` when `len(phases) != 2` on the non-compat path. **Do not relax this guard in Stage 6.**

### 2.5 Frozen code
- `run_deterministic()` — no changes.
- `execution_loop.py`, `replanner.py`, `step_dispatcher.py` — no changes.
- `planner/planner_utils.py` (`validate_plan`) — no changes.
- `tests/hierarchical_test_locks.py` — no new keys added (Stage 6 adds nothing to compat or hierarchical key sets).
- All existing tests must pass without modification.

---

## 3. The Exact Recommended Next Slice

### Stage 6: Detection Boundary Hardening + Subgoal Derivation Connector Coverage

**Scope:**

1. **Extend `_derive_phase_subgoals` connector set** in `plan_resolver.py` to cover the most common alternative connectors for the docs-then-code instruction pattern. This makes Phase 1 subgoal derivation correct (instead of falling back to full instruction) for a materially larger set of valid two-phase instructions.

2. **Add `two_phase_near_miss` trace event** in `get_parent_plan` for the case where `_is_two_phase_docs_code_intent` returns `False` but the instruction contains both a discovery verb and a docs token (i.e., it is plausibly a mixed instruction that fell just short of detection). This makes it debuggable from traces without any code change.

3. **Test first**, then runtime changes.

**Exactly one production file changes:** `agent/orchestrator/plan_resolver.py`.

---

## 4. Why This Slice Is Better Than Jumping to REPLAN or N-Phase

### Why not REPLAN first?

`REPLAN` requires a new decision branch in `_parent_policy_decision_after_phase_attempt` (currently CONTINUE/RETRY/STOP only). It would call `get_plan` or `_build_two_phase_parent_plan` fresh with failure context injected as `retry_context`. This change:
- Touches execution logic in `deterministic_runner.py` (non-compat path)
- Requires a design decision on REPLAN triggers: when does the orchestrator prefer REPLAN over STOP?
- Requires new invariants (what keys does a REPLAN'ed phase result carry? Does `attempt_history` grow? Does `errors_encountered_merged` include the aborted plan?)
- Has a larger blast radius than connector coverage

The connector coverage work is purely additive to `plan_resolver.py`. It cannot break the execution path. REPLAN can.

### Why not N-phase (≥3) first?

`len(phases) != 2` guard is intentional and locked. Relaxing it requires:
- A new decomposition type and detection heuristic (`three_phase_search_edit_test` or similar)
- Explicit re-approval per the roadmap ("Stage 4 — Broader Decomposition Patterns — Not actionable until Stage 3 is stable. Requires explicit re-approval as a separate architecture decision.")
- Roadmap Stage 3 completion is not fully satisfied: `REPLAN` and `REQUEST_CLARIFICATION` are absent.
- Zero existing tests for 3-phase handoff chains or 3-phase retry semantics.

The hardening slices (detection coverage + subgoal derivation) are **not exhausted**. N-phase is the wrong next step.

### Why not retrieval enrichment first?

Feeding `prior_phase_ranked_context` into Phase 1's retrieval pipeline requires touching `execution_loop` or `step_dispatcher`. These are frozen. Stage 6 does not touch them.

---

## 5. File-by-File Change Map

### 5.1 `agent/orchestrator/plan_resolver.py` — EXTEND

**Change 1: `_derive_phase_subgoals` — add connectors**

Current connector list (5 items):
```python
connectors = (" and explain ", " and describe ", " and show how ", " and summarize ", " and walk through ")
```

Add these connectors (ordered by expected frequency; append to the end of the tuple):
```python
", then explain ",
" then explain ",
" and tell me about ",
" and tell me how ",
" and walk me through ",
" and illustrate ",
" before explaining ",
", explain ",
```

**Constraints on new connectors:**
- Must be at least 10 chars (safety against false splits on short fragments).
- Each new connector tested with at least one positive test case in `test_two_phase_execution.py`.
- All existing `_derive_phase_subgoals` tests must still pass.

**Change 2: `get_parent_plan` — add near-miss trace**

After the `if _is_two_phase_docs_code_intent(instruction):` block returns False (i.e., when we fall through to compat), before calling `get_plan`, check if the instruction contains a discovery verb AND a docs token. If so, emit a `two_phase_near_miss` trace event. This has no side effect on execution; it is purely observability.

```python
# Near-miss trace: instruction had docs+discovery markers but did not qualify for two-phase.
if log_event_fn and trace_id and not _is_two_phase_docs_code_intent(instruction):
    il = (instruction or "").strip().lower()
    has_discovery = any(v in il for v in _DOCS_DISCOVERY_VERBS)
    has_docs = any(t in il for t in _DOCS_INTENT_TOKENS)
    if has_discovery and has_docs:
        try:
            log_event_fn(trace_id, "two_phase_near_miss", {
                "reason": "docs_and_discovery_but_no_code_marker",
                "instruction_preview": (instruction or "")[:200],
            })
        except Exception:
            pass
```

**What does NOT change in `plan_resolver.py`:**
- `_is_two_phase_docs_code_intent` detection logic — do not add new code markers; detection precision is more important than recall.
- `_docs_seed_plan`, `_ensure_plan_id`, `new_plan_id`, `get_plan` — untouched.
- `_build_two_phase_parent_plan` — untouched.
- `_DOCS_DISCOVERY_VERBS`, `_DOCS_INTENT_TOKENS`, `_NON_DOCS_TOKENS` constants — untouched.

---

### 5.2 `tests/test_two_phase_execution.py` — EXTEND

Add new test class `TestStage6DetectionConnectors` before the closing of the file.

---

### 5.3 `Docs/STAGE6_CLOSEOUT_REPORT.md` — NEW (written at closeout, not now)

---

### Files NOT touched in Stage 6

| File | Reason |
|------|--------|
| `agent/orchestrator/deterministic_runner.py` | No execution changes |
| `agent/orchestrator/parent_plan.py` | Schema unchanged |
| `agent/orchestrator/goal_evaluator.py` | Evaluator unchanged |
| `agent/orchestrator/execution_loop.py` | Frozen |
| `agent/execution/step_dispatcher.py` | Frozen |
| `agent/orchestrator/replanner.py` | Frozen |
| `planner/planner_utils.py` | Frozen |
| `tests/hierarchical_test_locks.py` | No new compat/hierarchical keys |
| All other test files | No regressions permitted |

---

## 6. Exact Tests to Add First

Implement all tests in `tests/test_two_phase_execution.py` class `TestStage6DetectionConnectors` **before touching `plan_resolver.py`**:

| Test name | What it asserts |
|---|---|
| `test_derive_phase_subgoals_comma_then_explain` | `"find architecture docs, then explain the replanner flow"` → `phase_1_subgoal` is `"Explain the replanner flow"` (not the full string) |
| `test_derive_phase_subgoals_then_explain_no_comma` | `"find the README then explain the dispatch loop"` → phase_1_subgoal is `"Explain the dispatch loop"` |
| `test_derive_phase_subgoals_and_tell_me_about` | `"locate the setup docs and tell me about the configuration"` → phase_1_subgoal is `"The configuration"` |
| `test_derive_phase_subgoals_and_tell_me_how` | `"find the docs and tell me how authentication works"` → splits at connector |
| `test_derive_phase_subgoals_and_walk_me_through` | `"find architecture docs and walk me through the plugin system"` → splits at connector |
| `test_derive_phase_subgoals_before_explaining` | `"find the README before explaining the worker flow"` → splits at `" before explaining "` |
| `test_derive_phase_subgoals_standalone_comma_explain` | `"locate the docs, explain the replanner flow"` → splits at `", explain "` |
| `test_derive_phase_subgoals_fallback_unchanged` | Instruction with no connector → `phase_1_subgoal == instruction.strip()` (existing behavior unchanged) |
| `test_derive_phase_subgoals_short_fragment_not_split` | Connector found but `raw` length < 10 → fallback to full instruction (existing behavior) |
| `test_get_parent_plan_emits_near_miss_when_docs_and_discovery_no_code_marker` | Mock `log_event_fn` + instruction like `"find the README"` (docs+discovery, no code marker) → `two_phase_near_miss` event emitted; `compatibility_mode=True` returned |
| `test_get_parent_plan_no_near_miss_when_two_phase_fires` | Mixed instruction that triggers two-phase → **no** `two_phase_near_miss` event (only `parent_plan_created`) |
| `test_get_parent_plan_no_near_miss_when_pure_code` | Pure code instruction (no docs token) → no near-miss event |
| `test_existing_connectors_still_work_after_extension` | One test per original connector confirming no regression (`" and explain "`, etc.) |

All existing tests must continue to pass. Run full suite before writing any production code.

---

## 7. Exact Runtime Changes After Tests Pass

**PR1 — Extend `_derive_phase_subgoals`:**

In `plan_resolver.py`, extend the `connectors` tuple:
```python
connectors = (
    " and explain ",
    " and describe ",
    " and show how ",
    " and summarize ",
    " and walk through ",
    # Stage 6 additions:
    ", then explain ",
    " then explain ",
    " and tell me about ",
    " and tell me how ",
    " and walk me through ",
    " and illustrate ",
    " before explaining ",
    ", explain ",
)
```

Ordering rule: existing connectors first (no reordering). New connectors appended. The `find()` loop returns the first match in `lower`; ordering within this tuple is not semantically significant for well-formed instructions.

**PR2 — Near-miss trace in `get_parent_plan`:**

In `get_parent_plan`, add the near-miss block immediately before the `get_plan(...)` fallback call. See §5.1 Change 2 for the exact snippet.

**Commit both together** if CI is green on tests. They are independent changes in the same file; one PR is acceptable.

---

## 8. Rollback Plan

Stage 6 is a single-file production change (`plan_resolver.py`). Rollback is:

1. Revert the `_derive_phase_subgoals` connector tuple to the original 5-item tuple.
2. Remove the near-miss trace block from `get_parent_plan`.
3. Remove `TestStage6DetectionConnectors` from `test_two_phase_execution.py` (or leave it as xfail if connector tests still pass the narrower set).
4. Run proof command — must return 160 passed (the Stage 5 count).

No changes to `deterministic_runner.py`, `hierarchical_test_locks.py`, or any other file to roll back.

---

## 9. Explicit Non-Goals

| Non-goal | Reason for deferral |
|---|---|
| Widen `_is_two_phase_docs_code_intent` code markers | Detection precision is more important than recall; false positives send correct single-intent instructions through wasted Phase 0 |
| `REPLAN` parent policy outcome | Requires `deterministic_runner.py` execution logic change; new invariant design; Stage 7 candidate |
| `REQUEST_CLARIFICATION` parent policy outcome | Requires caller contract changes; roadmap Stage 3 remainder; Stage 7+ |
| N-phase (≥3) support | Roadmap gate; `len(phases) != 2` guard is locked; exhaustible hardening slices not yet done |
| Use `prior_phase_ranked_context` in retrieval | Requires `execution_loop` or `step_dispatcher` changes; frozen |
| New compat `loop_output` keys | Compat invariants locked; `hierarchical_test_locks.py` would require update — a signal that the scope is wrong |
| Changes to `parent_plan.py` TypedDicts | Schema unchanged; no new fields required for connector extension |
| Auto-detecting optimal connector from instruction | Heuristic must remain deterministic; no LLM calls in plan construction |
| Parallel phase execution | Not in roadmap until Stage 4+; no evidence of need |
| Docs + edit mixed instructions | Requires a new `two_phase_docs_edit` decomposition type and `validate_plan` for edit-lane steps; Stage 7+ |

---

## 10. Definition of Done (Stage 6)

**Code conditions:**
- `_derive_phase_subgoals` connector tuple has ≥13 items (original 5 + ≥8 new).
- `get_parent_plan` emits `two_phase_near_miss` trace event when instruction has docs+discovery markers but does not trigger two-phase detection.
- No other file in `agent/` or `planner/` is modified.

**Tests:**
- All tests in `TestStage6DetectionConnectors` pass.
- `assert_compat_loop_output_has_no_hierarchical_keys` still passes for all compat tests.
- Full proof command: `python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q` — green with count ≥ 173 (160 + ≥13 new tests).

**No-regression expectations:**
- All 160 existing tests pass unchanged.
- `run_deterministic` output unchanged.
- `run_hierarchical` compat-path output unchanged.
- `run_hierarchical` two-phase execution path semantics unchanged.
- `_is_two_phase_docs_code_intent` detection results unchanged for all existing test cases.

---

## 11. Audit: Tiny Missing Guards (No Production Change Required Now)

During the Stage 6 audit the following observations were made. **None require a production change in Stage 6** but are recorded for the next engineer:

1. **`context_handoff` always non-empty after Phase 0.** `_build_phase_context_handoff` always returns a dict with 3 keys (even if all values are empty lists). Because `if context_handoff:` tests dict truthiness (non-empty dict = truthy), Phase 1 always receives the handoff injection, even when Phase 0 produced nothing useful. This is not harmful — empty lists injected into Phase 1 context are safe. No change needed.

2. **`_derive_phase_subgoals` is called twice when `_build_two_phase_parent_plan` constructs the plan.** Once for subgoal derivation, once implicitly via `plan(phase_1_subgoal)`. These are the same call, just the output is used in two different places. No efficiency concern at current call volume. No change needed.

3. **`max_parent_retries` is hardcoded to 0 in `_build_two_phase_parent_plan`.** Both `two_phase_docs_code` phases always have `max_parent_retries: 0`. The Stage 4 retry machinery is exercised only in tests with manually crafted plans. This is by design for Stage 2 plans; a future configurable retry budget per decomposition type is a Stage 7+ concern.

---

*End of Stage 6 implementation plan.*
